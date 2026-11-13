#!/usr/bin/env python3
"""Bounded V5 development waves and prospectively corrected fresh final tests.

No matches run during init/register/self-test. Example:
  python v5_experiments.py init --output-dir outputs/v5-cycle
  python v5_experiments.py register --experiment outputs/v5-cycle
  python v5_experiments.py run --wave-plan outputs/v5-cycle/wave-001/plan.json --trusted-local
  python v5_experiments.py final --wave-plan outputs/v5-cycle/wave-001/plan.json --trusted-local --readiness-note 'Native QA checked; host exclusive'

Register later waves only after the prior wave finishes. Optional --configs is
JSON containing a candidates list of exactly four {name, args} objects. Sources
may change between waves; each registered wave freezes its own exact sources.
The candidate may use up to1500 ms versus released v2 at180 ms: NOT compute-matched.
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import arena_eval as arena
import audit_replays
import run_paired_suite as suite

ROOT = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()
MAX_WAVES = 3
FINAL_PAIRS = 200
FAMILY_ALPHA = .05
PER_FINAL_ALPHA = FAMILY_ALPHA / MAX_WAVES
RULES = {
    'maximum_waves': MAX_WAVES, 'candidates_per_wave': 4,
    'screen_pairs_per_candidate': 8, 'confirmation_candidates_per_wave': 2,
    'confirmation_pairs_per_candidate': 32, 'confirmation_block_pairs': 16,
    'final_pairs': FINAL_PAIRS, 'maximum_final_attempts': MAX_WAVES,
    'family_alpha': FAMILY_ALPHA, 'per_final_two_sided_alpha': PER_FINAL_ALPHA,
    'selection': 'highest completed screen score, ties by frozen candidate order; top two confirm',
    'confirmation_gate': 'zero failures and score strictly above .5 in each 16-pair block and overall',
    'final_selection': 'highest confirmation score among qualifiers, ties by frozen candidate order',
    'final_statistical_gate': 'complete audited final; mean pair score minus sqrt(log(2/alpha)/(2*n)) > .5',
    'retry_policy': 'at most one final per wave; later frozen waves may adapt to prior results; fresh final seeds',
    'stop_policy': 'finish every started successful stage; stop cycle at first final statistical pass; no score-based early stopping',
    'final_readiness': 'development stops at final_ready; explicit final command records native QA and exclusive-host readiness',
    'zero_failures': 'qualifying candidate screen, confirmation and final must all be complete and failure-free',
    'failed_candidate_policy': 'retain failed stage evidence; candidate ineligible; source drift aborts whole wave',
    'maximum_full_games': 1776,
}
DEPENDENCIES = ('v5_experiments.py', 'run_paired_suite.py', 'arena_eval.py',
                'audit_replays.py', 'bot.py', 'belief.py', 'search.py',
                'fast_observation.py', 'risk_model.py', 'risk_model.json',
                'referee.py', 'uniform_random.py', 'requirements.txt', 'v2_bot.py')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def immutable(path, document):
    data = (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    with path.open('xb') as stream:
        stream.write(data)
    path.chmod(0o444)
    checksum = path.with_suffix(path.suffix + '.sha256')
    with checksum.open('x') as stream:
        stream.write(hashlib.sha256(data).hexdigest() + '\n')
    checksum.chmod(0o444)


def read_immutable(path):
    if digest(path) != path.with_suffix(path.suffix + '.sha256').read_text().strip():
        raise ValueError('Immutable plan checksum changed: ' + str(path))
    return json.loads(path.read_text())


def seed_layout(base):
    if type(base) is not int or not 0 <= base <= 2_130_000_000:
        raise ValueError('Seed base must be between0 and2130000000')
    layout = []
    used = set()
    for wave in range(MAX_WAVES):
        stages = {}
        for stage, offset, pairs, batch_pairs in (
                ('screen', 0, 8, 2), ('confirmation', 100000, 32, 8),
                ('final', 200000, FINAL_PAIRS, 50)):
            jobs = suite.plan_batches(pairs, batch_pairs, base + wave * 1000000 + offset)
            values = {seed for job in jobs for seed in range(job['seed'], job['seed'] + 2 * job['pairs'])}
            if used & values:
                raise ValueError('Internal stage seed overlap')
            used.update(values)
            stages[stage] = {'pairs': pairs, 'batch_pairs': batch_pairs,
                             'seed_bases': [job['seed'] for job in jobs]}
        layout.append(stages)
    return layout


def default_candidates():
    candidates = []
    for policy in ('hypothesis', 'robust', 'bank', 'learned'):
        smaller = policy in ('hypothesis', 'robust')
        candidates.append({'name': policy + '_600', 'args': [
            '--risk-policy', policy, '--search-ms', '600',
            '--particles', '128' if smaller else '256',
            '--worlds', '4' if smaller else '6',
            '--depth', '5' if smaller else '4', '--nodes', '120000']})
    return candidates


def historical_seed_values(directory):
    """Inspect recorded scalar seeds and seed-base arrays without loading replays."""
    seeds, files = set(), {}
    scalar = re.compile(r'"(?:bot_seed|opponent_seed|candidate_seed|seed|seed_base)"\s*:\s*(\d+)')
    arrays = re.compile(r'"[a-z_]*seed_bases"\s*:\s*\[([\d,\s]*)\]')
    mappings = re.compile(r'"[a-z_]*seed_bases"\s*:\s*\{([^{}]*)\}')
    for path in sorted(directory.rglob('*.json')):
        raw = path.read_bytes()
        text = raw.decode('utf-8')
        found = {int(value) for value in scalar.findall(text)}
        for group in arrays.findall(text):
            found.update(int(value) for value in re.findall(r'\d+', group))
        for group in mappings.findall(text):
            found.update(int(value) for value in re.findall(r':\s*(\d+)', group))
        if found:
            seeds.update(found)
            files[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
    return seeds, files


def validate_candidates(candidates):
    if not isinstance(candidates, list) or len(candidates) != 4:
        raise ValueError('Register exactly four configurations per wave')
    names = set()
    for item in candidates:
        if not isinstance(item, dict) or set(item) != {'name', 'args'}:
            raise ValueError('Each configuration requires exactly name and args')
        name, args = item['name'], item['args']
        if not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9_-]{0,47}', name) or name in names:
            raise ValueError('Configuration names must be unique safe path components')
        names.add(name)
        if not isinstance(args, list) or any(not isinstance(arg, str) or '\0' in arg for arg in args):
            raise ValueError('Configuration args must be exact string tokens')
        if any(arg == '--seed' or arg.startswith('--seed=') for arg in args):
            raise ValueError('Seeds belong to the registered schedule')
        times = []
        for index, arg in enumerate(args):
            if arg == '--search-ms':
                if index + 1 == len(args):
                    raise ValueError('Missing --search-ms value')
                times.append(float(args[index + 1]))
            elif arg.startswith('--search-ms='):
                times.append(float(arg.split('=', 1)[1]))
        if len(times) != 1 or not math.isfinite(times[0]) or not 0 < times[0] <= 1500:
            raise ValueError('Each candidate must explicitly set one search-ms in(0,1500]')
    return candidates


def initialize(args):
    historical, history_files = historical_seed_values(ROOT / 'outputs')
    for _ in range(100):
        base = args.seed_base if args.seed_base is not None else 1_000_000_001 + secrets.randbelow(999_999_999)
        layout = seed_layout(base)
        allocated = {seed for stages in layout for stage in stages.values()
                     for start in stage['seed_bases'] for seed in range(start, start + 2 * stage['batch_pairs'])}
        # A preceding recorded seed can be the start of a not-yet-played batch;
        # reserve the harness maximum1000pairs around every historical value.
        if not any(start <= value < start + 2000 for start in historical for value in allocated):
            break
        if args.seed_base is not None:
            raise ValueError('Requested seeds overlap prior recorded or reserved batch seeds')
    else:
        raise ValueError('Could not select a disjoint seed namespace')
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    plan = {'schema_version': 1, 'created_utc': utc_now(), 'rules': RULES,
            'driver_sha256': digest(SELF), 'python_executable': sys.executable,
            'workers': args.workers, 'base_time': 180, 'increment': 2, 'max_plies': 1000,
            'opponent': str(ROOT / 'v2_bot.py'), 'opponent_args': ['--search-ms', '180'],
            'seed_base': base, 'seed_layout': layout,
            'seed_note': 'Stage/wave ranges are disjoint; candidates intentionally share within-stage panels. Historical scalar seeds and seed-base arrays were checked with 2000-seed batch reservations.',
            'historical_seed_count': len(historical),
            'historical_seed_max': max(historical) if historical else None,
            'historical_schedule_file_sha256': history_files,
            'comparison': 'Candidate up to 1500 ms vs byte-exact released v2 default 180 ms, same 180+2; NOT compute-matched.',
            'limitations': ['Local shared host, not arena isolation; no paid compute or arena rating.',
                            'Later waves may inspect failed-final diagnostics for development; every subsequent final uses fresh seeds and its allocated alpha.',
                            'Bonferroni allocation covers at most 3 final attempts; pair independence remains an assumption.',
                            'Package checks and compute-matched follow-ups are separate from this experiment.'],
            'release_controls': {'owner': 'parent-run after statistical pass; not supplied by this driver',
                                 'pairs_per_opponent': 10,
                                 'minimum_scores': {'legacy_v1': .75, 'official_baseline': .90, 'uniform_random': .95},
                                 'maximum_failures': 0, 'maximum_extra_games_per_qualifying_candidate': 60,
                                 'separate_from_maximum_1776_strength_games': True,
                                 'pooled_strength_claim': False, 'statistical_pass_is_not_release': True},
            'absolute_arena_elo': None}
    immutable(directory / 'design.json', plan)
    print('Registered design:', directory / 'design.json', flush=True)
    return 0


def check_design(path):
    design = read_immutable(path)
    if design['rules'] != RULES or design['driver_sha256'] != digest(SELF):
        raise ValueError('Driver or prospective rules changed; start a separately declared cycle')
    if design['seed_layout'] != seed_layout(design['seed_base']):
        raise ValueError('Invalid seed layout')
    return design


def register(args):
    experiment = args.experiment.resolve()
    design_path = experiment / 'design.json'
    design = check_design(design_path)
    existing = sorted(experiment.glob('wave-*/plan.json'))
    if len(existing) >= MAX_WAVES:
        raise ValueError('All three wave slots are already registered')
    for previous in existing:
        result_path = previous.parent / 'result.json'
        if not result_path.exists():
            raise ValueError('Finish the registered wave before registering another')
        result = json.loads(result_path.read_text())
        if result.get('status') in ('running', 'final_ready', 'final_passed_release_pending'):
            raise ValueError('A wave is active or the cycle already passed promotion')
    candidates = default_candidates() if args.configs is None else json.loads(args.configs.read_text())['candidates']
    validate_candidates(candidates)
    candidate = args.candidate.resolve()
    sources = {ROOT / name for name in DEPENDENCIES}
    sources.update(ROOT / 'opponents' / 'v2' / name for name in suite.V2_SNAPSHOT_FILES)
    sources.update((candidate, design_path, *[p.resolve() for p in args.source]))
    if args.configs is not None:
        sources.add(args.configs.resolve())
    if any(not path.is_file() for path in sources):
        raise ValueError('All frozen implementation sources must exist')
    wave = len(existing) + 1
    directory = experiment / f'wave-{wave:03d}'
    directory.mkdir(exist_ok=False)
    contents = {str(path): path.read_bytes() for path in sorted(sources)}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    archive_path = directory / 'sources.zip'
    entries = {}
    with archive_path.open('xb') as stream, zipfile.ZipFile(stream, 'w') as archive:
        for name, data in contents.items():
            path = Path(name)
            entry = (str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else
                     'external/' + hashlib.sha256(name.encode()).hexdigest()[:16] + '-' + path.name)
            entries[name] = entry
            archive.writestr(zipfile.ZipInfo(entry, (1980, 1, 1, 0, 0, 0)), data)
    archive_path.chmod(0o444)
    plan = {'schema_version': 1, 'created_utc': utc_now(), 'wave': wave,
            'design_file': str(design_path), 'design_sha256': digest(design_path),
            'candidate': str(candidate), 'candidates': candidates,
            'source_sha256': hashes,
            'source_archive': {'path': str(archive_path), 'sha256': digest(archive_path), 'entries': entries},
            'stages': design['seed_layout'][wave - 1], 'rules': RULES,
            'host_activity': args.host_activity}
    immutable(directory / 'plan.json', plan)
    print('Registered wave without matches:', directory / 'plan.json', flush=True)
    return 0


def validate_sources(plan, plan_path, plan_hash):
    if digest(plan_path) != plan_hash or arena.source_hashes([Path(p) for p in plan['source_sha256']]) != plan['source_sha256']:
        raise ValueError('Frozen wave source or plan changed; retain evidence and abort')
    if digest(plan['source_archive']['path']) != plan['source_archive']['sha256']:
        raise ValueError('Frozen source archive changed')


def rank_candidates(candidates, results, limit):
    order = {candidate['name']: i for i, candidate in enumerate(candidates)}
    eligible = [name for name, result in results.items() if result.get('valid')]
    return sorted(eligible, key=lambda name: (-results[name]['score'], order[name]))[:limit]


def confirmation_gate(matches):
    if len(matches) != 64 or any(r['status'] != 'completed' or r['outcome'] not in arena.SCORES for r in matches):
        return {'passed': False, 'reason': 'Requires 32 complete failure-free pairs'}
    halves = [sum(arena.SCORES[r['outcome']] for r in matches[start:start + 32]) / 32 for start in (0, 32)]
    overall = sum(halves) / 2
    return {'passed': overall > .5 and all(score > .5 for score in halves),
            'overall_score': overall, 'fixed_block_scores': halves}


def final_gate(matches):
    grouped = {}
    for row in matches:
        grouped.setdefault(row['pair'], []).append(row)
    if (len(grouped) != FINAL_PAIRS or len(matches) != 2 * FINAL_PAIRS
            or any(len(rows) != 2 or {r['color'] for r in rows} != {'white', 'black'}
                   or any(r['status'] != 'completed' or r['outcome'] not in arena.SCORES for r in rows)
                   for rows in grouped.values())):
        return {'passed': False, 'reason': 'Requires 200 complete failure-free color pairs'}
    means = [sum(arena.SCORES[r['outcome']] for r in rows) / 2 for rows in grouped.values()]
    mean = sum(means) / len(means)
    radius = math.sqrt(math.log(2 / PER_FINAL_ALPHA) / (2 * len(means)))
    return {'passed': mean - radius > .5, 'pairs': len(means), 'score': mean,
            'two_sided_alpha': PER_FINAL_ALPHA, 'family_alpha': FAMILY_ALPHA,
            'maximum_final_attempts': MAX_WAVES, 'radius': radius,
            'score_interval': [max(0, mean - radius), min(1, mean + radius)],
            'method': 'Bonferroni allocated two-sided Hoeffding bounded-pair interval',
            'absolute_arena_elo': None}


def run_stage(plan, design, plan_path, plan_hash, candidate, stage):
    validate_sources(plan, plan_path, plan_hash)
    specification = plan['stages'][stage]
    directory = plan_path.parent / (stage + '-' + candidate['name'])
    arguments = ['--trusted-local', '--candidate', plan['candidate'], '--opponent', design['opponent'],
                 '--pairs', str(specification['pairs']), '--batch-pairs', str(specification['batch_pairs']),
                 '--workers', str(design['workers']), '--seed-bases', *map(str, specification['seed_bases']),
                 '--base-time', str(design['base_time']), '--increment', str(design['increment']),
                 '--max-plies', str(design['max_plies']), '--output-dir', str(directory)]
    arguments += ['--candidate-arg=' + value for value in candidate['args']]
    arguments += ['--opponent-arg=' + value for value in design['opponent_args']]
    for source in sorted({*plan['source_sha256'], str(plan_path)}):
        arguments += ['--source', source]
    print('Starting', stage, candidate['name'], 'pairs', specification['pairs'], flush=True)
    code = suite.main(arguments)
    validate_sources(plan, plan_path, plan_hash)
    result = json.loads((directory / 'suite.json').read_text())
    child_plan = json.loads((directory / 'plan.json').read_text())
    paths = [Path(job['output']) for job in child_plan['jobs'] if Path(job['output']).exists()]
    audit = audit_replays.build_report(paths) if paths else {'ok': False, 'error': 'No child evidence'}
    arena.checkpoint(directory / 'replay-audit.json', audit)
    valid = code == 0 and result['status'] == 'completed' and audit['ok']
    summary = result.get('aggregate', {}).get(design['opponent'])
    record = {'valid': valid, 'suite_file': str(directory / 'suite.json'),
              'suite_sha256': digest(directory / 'suite.json'), 'returncode': code,
              'audit_file': str(directory / 'replay-audit.json'), 'audit_ok': audit['ok'],
              'score': summary['raw']['score'] if valid else None,
              'raw_record': summary['raw'] if summary else None,
              'matches': result.get('matches', []) if valid else [],
              'reason': None if valid else 'Incomplete, failed, or unaudited stage; all child files retained'}
    if valid and len(record['matches']) != specification['pairs'] * 2:
        raise ValueError('Stage result size differs from fixed schedule')
    validate_sources(plan, plan_path, plan_hash)
    return record


def run_wave(args):
    plan_path = args.wave_plan.resolve()
    plan = read_immutable(plan_path)
    plan_hash = digest(plan_path)
    design = check_design(Path(plan['design_file']))
    if digest(plan['design_file']) != plan['design_sha256'] or plan['rules'] != RULES:
        raise ValueError('Wave differs from global design')
    validate_sources(plan, plan_path, plan_hash)
    output = plan_path.parent / 'result.json'
    if output.exists():
        raise ValueError('Wave already attempted; never overwrite or retry a consumed wave')
    result = {'status': 'running', 'started_utc': utc_now(), 'wave': plan['wave'],
              'plan_sha256': plan_hash, 'screens': {}, 'confirmations': {}, 'final': None,
              'comparison': design['comparison'], 'absolute_arena_elo': None}
    arena.checkpoint(output, result)
    try:
        for candidate in plan['candidates']:
            result['active_stage'] = ['screen', candidate['name']]
            arena.checkpoint(output, result)
            result['screens'][candidate['name']] = run_stage(plan, design, plan_path, plan_hash, candidate, 'screen')
            arena.checkpoint(output, result)
        selected = rank_candidates(plan['candidates'], result['screens'], 2)
        result['confirmation_selection'] = selected
        candidates = {candidate['name']: candidate for candidate in plan['candidates']}
        for name in selected:
            result['active_stage'] = ['confirmation', name]
            arena.checkpoint(output, result)
            record = run_stage(plan, design, plan_path, plan_hash, candidates[name], 'confirmation')
            record['qualification'] = confirmation_gate(record['matches']) if record['valid'] else {'passed': False}
            result['confirmations'][name] = record
            arena.checkpoint(output, result)
        qualified = {name: record for name, record in result['confirmations'].items() if record['qualification']['passed']}
        finalists = rank_candidates(plan['candidates'], qualified, 1)
        if not finalists:
            result['status'] = 'no_qualifier'
        else:
            name = finalists[0]
            result['final_selection'] = name
            result['status'] = 'final_ready'
            result['final_readiness_required'] = 'Run final only after native QA readiness and exclusive host availability'
        validate_sources(plan, plan_path, plan_hash)
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        result.update(status='aborted', error=type(error).__name__ + ': ' + str(error))
    if result['status'] == 'aborted':
        result['aborted_stage'] = result.get('active_stage')
    result.pop('active_stage', None)
    result['finished_utc'] = utc_now()
    arena.checkpoint(output, result)
    print('Wave result:', result['status'], output, flush=True)
    return int(result['status'] == 'aborted')


def run_final(args):
    """Resume only an unstarted final, never retry a consumed final attempt."""
    plan_path = args.wave_plan.resolve()
    plan = read_immutable(plan_path)
    plan_hash = digest(plan_path)
    design = check_design(Path(plan['design_file']))
    output = plan_path.parent / 'result.json'
    result = json.loads(output.read_text())
    name = result.get('final_selection')
    marker = plan_path.parent / 'final-started.json'
    if (result.get('status') != 'final_ready' or result.get('plan_sha256') != plan_hash
            or marker.exists() or (plan_path.parent / ('final-' + str(name))).exists()):
        raise ValueError('Final must be qualified and never previously started')
    if not args.readiness_note.strip():
        raise ValueError('Record native QA and exclusive-host readiness before starting final')
    try:
        validate_sources(plan, plan_path, plan_hash)
        candidate = next(item for item in plan['candidates'] if item['name'] == name)
        if not result['screens'][name]['valid'] or not result['confirmations'][name]['qualification']['passed']:
            raise ValueError('Finalist lacks complete prior-stage qualification')
        immutable(marker, {'started_utc': utc_now(), 'plan_sha256': plan_hash,
                           'candidate': name, 'readiness_note': args.readiness_note,
                           'two_sided_alpha': PER_FINAL_ALPHA})
        result.update(status='running', active_stage=['final', name], final_readiness_note=args.readiness_note)
        arena.checkpoint(output, result)
        record = run_stage(plan, design, plan_path, plan_hash, candidate, 'final')
        record['statistical_gate'] = final_gate(record['matches']) if record['valid'] else {'passed': False}
        result['final'] = record
        result['status'] = 'final_passed_release_pending' if record['statistical_gate']['passed'] else 'final_not_passed'
        result['release_controls'] = {'status': 'not_run_by_driver', 'requirements': design['release_controls']}
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        result.update(status='aborted', aborted_stage=['final', name], error=type(error).__name__ + ': ' + str(error))
    result.pop('active_stage', None)
    result['finished_utc'] = utc_now()
    arena.checkpoint(output, result)
    print('Final result:', result['status'], output, flush=True)
    return int(result['status'] == 'aborted')


def self_test():
    class Tests(unittest.TestCase):
        def test_seed_namespaces_and_default_configs(self):
            layout = seed_layout(600000000)
            self.assertEqual(len(layout), 3)
            self.assertEqual(validate_candidates(default_candidates()), default_candidates())
            self.assertEqual(sum(4 * stage['screen']['pairs'] * 2 + 2 * stage['confirmation']['pairs'] * 2
                                 + stage['final']['pairs'] * 2 for stage in layout), 1776)
            with self.assertRaises(ValueError):
                validate_candidates(default_candidates()[:3])
        def test_selection_and_confirmation_blocks(self):
            candidates = default_candidates()
            records = {item['name']: {'valid': True, 'score': .625} for item in candidates}
            self.assertEqual(rank_candidates(candidates, records, 2), [x['name'] for x in candidates[:2]])
            records[candidates[0]['name']]['valid'] = False
            self.assertEqual(rank_candidates(candidates, records, 2)[0], candidates[1]['name'])
            rows = [{'outcome': 'win' if i < 40 else 'loss', 'status': 'completed'} for i in range(64)]
            self.assertFalse(confirmation_gate(rows)['passed'])
            rows = [{'outcome': 'win' if i % 32 < 20 else 'loss', 'status': 'completed'} for i in range(64)]
            self.assertTrue(confirmation_gate(rows)['passed'])
        def test_final_family_correction_and_pair_failure(self):
            def rows(wins):
                return [{'pair': i // 2, 'color': 'white' if i % 2 == 0 else 'black',
                         'outcome': 'win' if i < wins else 'loss', 'status': 'completed'} for i in range(400)]
            self.assertFalse(final_gate(rows(243))['passed'])
            self.assertTrue(final_gate(rows(244))['passed'])
            changed = rows(400)
            changed[-1]['color'] = 'white'
            self.assertFalse(final_gate(changed)['passed'])
        def test_registration_and_tamper_without_matches(self):
            with tempfile.TemporaryDirectory() as folder:
                directory = Path(folder) / 'cycle'
                args = argparse.Namespace(seed_base=600000000, output_dir=directory, workers=4)
                initialize(args)
                with patch.object(suite, 'main', side_effect=AssertionError('No match execution')):
                    register(argparse.Namespace(experiment=directory, configs=None, candidate=ROOT / 'bot.py',
                                                source=[], host_activity='unit fixture'))
                plan = read_immutable(directory / 'wave-001/plan.json')
                self.assertEqual(len(plan['candidates']), 4)
                self.assertIn(str(ROOT / 'risk_model.json'), plan['source_sha256'])
                self.assertIn(str(ROOT / 'opponents/v2/provenance.json'), plan['source_sha256'])
                with self.assertRaises(ValueError):
                    register(argparse.Namespace(experiment=directory, configs=None, candidate=ROOT / 'bot.py',
                                                source=[], host_activity='unit fixture'))
        def test_wave_finishes_four_screens_two_confirmations_one_final(self):
            with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
                directory = Path(folder) / 'cycle'
                initialize(argparse.Namespace(seed_base=600000000, output_dir=directory, workers=4))
                register(argparse.Namespace(experiment=directory, configs=None, candidate=ROOT / 'bot.py',
                                            source=[], host_activity='unit fixture'))
                called = []
                def fake_stage(plan, design, path, checksum, candidate, stage):
                    called.append((stage, candidate['name']))
                    if stage == 'screen':
                        score = [.75, .625, .5, .25][[c['name'] for c in plan['candidates']].index(candidate['name'])]
                        return {'valid': True, 'score': score, 'matches': []}
                    size = 64 if stage == 'confirmation' else 400
                    rows = [{'pair': i // 2, 'color': 'white' if i % 2 == 0 else 'black',
                             'status': 'completed', 'outcome': 'win' if (i % 32 < 20 if size == 64 else i < 244) else 'loss'}
                            for i in range(size)]
                    return {'valid': True, 'score': sum(arena.SCORES[r['outcome']] for r in rows) / size,
                            'matches': rows}
                with patch.dict(globals(), run_stage=fake_stage):
                    self.assertEqual(run_wave(argparse.Namespace(wave_plan=directory / 'wave-001/plan.json')), 0)
                    pending = json.loads((directory / 'wave-001/result.json').read_text())
                    self.assertEqual(pending['status'], 'final_ready')
                    self.assertEqual(len(called), 6)
                    self.assertEqual(run_final(argparse.Namespace(wave_plan=directory / 'wave-001/plan.json',
                                                               readiness_note='synthetic readiness fixture')), 0)
                result = json.loads((directory / 'wave-001/result.json').read_text())
                self.assertEqual(Counter(stage for stage, name in called), {'screen': 4, 'confirmation': 2, 'final': 1})
                self.assertEqual(result['status'], 'final_passed_release_pending')
                self.assertEqual(result['final_selection'], 'hypothesis_600')
                with self.assertRaises(ValueError):
                    register(argparse.Namespace(experiment=directory, configs=None, candidate=ROOT / 'bot.py',
                                                source=[], host_activity='unit fixture'))
                with self.assertRaises(ValueError):
                    run_final(argparse.Namespace(wave_plan=directory / 'wave-001/plan.json', readiness_note='retry forbidden'))
        def test_no_qualifier_keeps_final_untouched_and_allows_next_wave(self):
            with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
                directory = Path(folder) / 'cycle'
                initialize(argparse.Namespace(seed_base=600000000, output_dir=directory, workers=4))
                register(argparse.Namespace(experiment=directory, configs=None, candidate=ROOT / 'bot.py',
                                            source=[], host_activity='unit fixture'))
                stages = []
                def fake_stage(plan, design, path, checksum, candidate, stage):
                    stages.append(stage)
                    self.assertNotEqual(stage, 'final')
                    rows = [{'status': 'completed', 'outcome': 'win' if i % 2 else 'loss'} for i in range(64)]
                    return {'valid': True, 'score': .5, 'matches': rows}
                with patch.dict(globals(), run_stage=fake_stage):
                    self.assertEqual(run_wave(argparse.Namespace(wave_plan=directory / 'wave-001/plan.json')), 0)
                result = json.loads((directory / 'wave-001/result.json').read_text())
                self.assertEqual(result['status'], 'no_qualifier')
                self.assertIsNone(result['final'])
                self.assertEqual(register(argparse.Namespace(experiment=directory, configs=None,
                                 candidate=ROOT / 'bot.py', source=[], host_activity='unit fixture')), 0)
    return int(not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests)).wasSuccessful())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    initialize_parser = commands.add_parser('init', help='Freeze global limits, alpha and seed namespaces; no games')
    initialize_parser.add_argument('--output-dir', type=Path, required=True)
    initialize_parser.add_argument('--seed-base', type=int)
    initialize_parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    registration = commands.add_parser('register', help='Freeze the next wave, with no games')
    registration.add_argument('--experiment', type=Path, required=True)
    registration.add_argument('--configs', type=Path)
    registration.add_argument('--candidate', type=Path, default=ROOT / 'bot.py')
    registration.add_argument('--source', type=Path, action='append', default=[])
    registration.add_argument('--host-activity', default='Development may overlap small unit checks and native cloud reviews; final primary workload is exclusive')
    run = commands.add_parser('run', help='Execute screens/confirmations only; stop before any final')
    run.add_argument('--wave-plan', type=Path, required=True)
    run.add_argument('--trusted-local', action='store_true', required=True)
    final = commands.add_parser('final', help='Start an unattempted qualified final after explicit readiness')
    final.add_argument('--wave-plan', type=Path, required=True)
    final.add_argument('--trusted-local', action='store_true', required=True)
    final.add_argument('--readiness-note', required=True)
    commands.add_parser('self-test', help='Small synthetic tests only; never plays matches')
    args = parser.parse_args(argv)
    try:
        return {'init': initialize, 'register': register, 'run': run_wave, 'final': run_final,
                'self-test': lambda _: self_test()}[args.command](args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
