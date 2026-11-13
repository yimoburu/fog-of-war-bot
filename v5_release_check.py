#!/usr/bin/env python3
"""Run the three registered release controls only after a verified V5 final pass.

The prospective guard lock/environment checks cover this whole command. One
attempt is retained, including failures. This does not package, publish, alter
the launcher, pool opponent scores or assign an arena rating.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

import v5_guard as guard

ROOT = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()
THRESHOLDS = {'legacy_v1': .75, 'official_baseline': .90, 'uniform_random': .95}
CONTROL_FILES = {'legacy_v1': 'legacy_bot.py', 'official_baseline': 'baseline.py',
                 'uniform_random': 'uniform_random.py'}
REGISTERED_CONTROLS_SHA256 = '5d83c6c2717e781dad3934ddb9dbea02cc6480046c96896a5ae005c96fc83571'


def now():
    return datetime.now(timezone.utc).isoformat()


def selected_candidate(wave, result):
    """Check the whole qualification chain before any control can be launched."""
    if result.get('status') != 'final_passed_release_pending':
        raise ValueError('A completed passing final is required before release controls')
    name = result.get('final_selection')
    candidates = [item for item in wave['candidates'] if item['name'] == name]
    if len(candidates) != 1:
        raise ValueError('Final selection is not a unique frozen candidate')
    if (not result['screens'][name].get('valid')
            or not result['confirmations'][name].get('valid')
            or not result['confirmations'][name]['qualification'].get('passed')
            or not result.get('final', {}).get('valid')
            or not result['final'].get('audit_ok')
            or not result['final'].get('statistical_gate', {}).get('passed')):
        raise ValueError('Candidate lacks failure-free qualification and final audit')
    return candidates[0]


def validate_controls(controls, design):
    if (controls.get('schema_version') != 1 or controls.get('attempts') != 1
            or controls.get('pairs_per_opponent') != 10 or controls.get('batch_pairs') != 5
            or controls.get('workers') != 4 or controls.get('failures_allowed') != 0
            or controls.get('clock') != {'base_seconds': 180, 'increment_seconds': 2, 'max_plies': 1000}):
        raise ValueError('Control schedule or clock differs from the prospective contract')
    opponents = controls.get('controls', [])
    if len(opponents) != 3 or {row['name'] for row in opponents} != set(THRESHOLDS):
        raise ValueError('Exactly the three registered control opponents are required')
    strength = {seed for wave in design['seed_layout'] for stage in wave.values()
                for base in stage['seed_bases'] for seed in range(base, base + 2 * stage['batch_pairs'])}
    used = set()
    for row in opponents:
        if Path(row['opponent']).resolve() != ROOT / CONTROL_FILES[row['name']]:
            raise ValueError('Control opponent differs from its registered implementation')
        base = row['seed_base']
        if type(base) is not int or not 0 <= base <= 2_139_000_000:
            raise ValueError('Invalid control seed base')
        seeds = set(range(base, base + 10)) | set(range(base + 10000, base + 10010))
        if row['minimum_score'] != THRESHOLDS[row['name']] or used & seeds or strength & seeds:
            raise ValueError('Control thresholds changed or seeds overlap')
        used.update(seeds)
    return opponents


def audit_complete(paths, count, output=None):
    """Require every raw input and every planned trajectory, including failures."""
    import audit_replays
    if not paths or len(set(paths)) != len(paths) or not all(path.is_file() for path in paths):
        raise ValueError('Missing or duplicate raw replay input')
    audited = audit_replays.build_report(paths)
    if output is not None:
        import arena_eval
        arena_eval.checkpoint(output, audited)
    summary = audited.get('summary', {})
    if (not audited.get('ok') or summary.get('inputs') != len(paths)
            or summary.get('matches') != count
            or summary.get('verified_completed_trajectories') != count
            or summary.get('recorded_failures') != 0):
        raise ValueError('Not every planned replay passed independent reconstruction')
    if {row['input']: row['sha256'] for row in audited['inputs']} != {
            str(path.resolve()): guard.digest(path) for path in paths}:
        raise ValueError('Replay audit inputs differ from current raw evidence')
    return audited


def control_verdict(summary, returncode, audit_ok, minimum_score):
    valid = (returncode == 0 and audit_ok and summary is not None
             and summary.get('attempted') == 20 and summary.get('failures') == 0
             and summary.get('raw', {}).get('resolved') == 20)
    score = summary['raw']['score'] if valid else None
    if valid:
        raw = summary['raw']
        counts = [raw.get(name) for name in ('win', 'draw', 'loss')]
        valid = (all(type(count) is int and count >= 0 for count in counts)
                 and sum(counts) == 20 and raw.get('unresolved', 0) == 0
                 and type(score) in (int, float) and math.isfinite(score)
                 and abs(score - (counts[0] + .5 * counts[1]) / 20) <= 1e-12)
        if not valid:
            score = None
    return {'valid': valid, 'passed': valid and score is not None and score >= minimum_score,
            'score': score, 'minimum_score': minimum_score, 'zero_failures_required': True,
            'raw': summary.get('raw') if summary else None}


def control_audit_agrees(audit, summary, opponent):
    """The independently reconstructed outcomes must reproduce the score."""
    groups = audit.get('summary', {}).get('by_opponent_color', [])
    if (not summary or len(groups) != 2
            or {row.get('color') for row in groups} != {'white', 'black'}
            or any(row.get('opponent') != opponent for row in groups)):
        return False
    names = ('win', 'draw', 'loss', 'unresolved')
    if any(any(type(row.get(name)) is not int or row[name] < 0 for name in names)
           or sum(row[name] for name in names) != 10 for row in groups):
        return False
    return all(sum(row[name] for row in groups) == summary.get('raw', {}).get(name, 0)
               for name in names)


def prepare(wave_path, controls_path, runtime):
    """Resolve and verify immutable candidate/strength evidence; no writes/games."""
    import arena_eval as arena
    import run_paired_suite as suite
    import v5_experiments as experiment

    wave = guard.load_bound_json(wave_path)
    design_path = Path(wave['design_file']).resolve()
    directory = design_path.parent
    if (wave_path.parent.parent != directory
            or controls_path != directory / 'release-controls-plan.json'
            or guard.digest(controls_path) != REGISTERED_CONTROLS_SHA256):
        raise ValueError('Wave and control plan must belong to the same experiment')
    design = guard.load_bound_json(design_path)
    controls = guard.load_bound_json(controls_path)
    result_path = wave_path.parent / 'result.json'
    result = json.loads(result_path.read_text())
    candidate = selected_candidate(wave, result)
    if not experiment.confirmation_gate(result['confirmations'][candidate['name']]['matches'])['passed']:
        raise ValueError('Selected confirmation no longer reproduces its qualification')
    if result.get('plan_sha256') != guard.digest(wave_path) or wave['design_sha256'] != guard.digest(design_path):
        raise ValueError('Final result/plan binding changed')
    experiment.validate_sources(wave, wave_path, guard.digest(wave_path))
    opponents = validate_controls(controls, design)
    for row in opponents:
        if guard.digest(row['opponent']) != row['sha256']:
            raise ValueError('Control opponent source changed: ' + row['name'])
    final_path = wave_path.parent / ('final-' + candidate['name']) / 'suite.json'
    record = result['final']
    if Path(record['suite_file']).resolve() != final_path or guard.digest(final_path) != record['suite_sha256']:
        raise ValueError('Passing final suite changed')
    final = json.loads(final_path.read_text())
    child_plan_path = final_path.parent / 'plan.json'
    child_plan = json.loads(child_plan_path.read_text())
    if (final.get('status') != 'completed' or final.get('errors')
            or final.get('plan_sha256') != guard.digest(child_plan_path)
            or child_plan['candidate'] != wave['candidate'] or child_plan['candidate_args'] != candidate['args']
            or child_plan['opponent'] != design['opponent'] or child_plan['opponent_args'] != design['opponent_args']
            or child_plan['python_executable'] != sys.executable
            or child_plan['base_time'] != 180 or child_plan['increment'] != 2
            or child_plan['max_plies'] != 1000 or child_plan['workers'] != design['workers']
            or [job['seed'] for job in child_plan['jobs']] != wave['stages']['final']['seed_bases']
            or any(job['pairs'] != wave['stages']['final']['batch_pairs'] for job in child_plan['jobs'])
            or child_plan['pairs'] != 200 or not experiment.final_gate(final.get('matches', []))['passed']):
        raise ValueError('Final statistics or executed configuration do not verify')
    expected_sources = {**wave['source_sha256'], str(wave_path): guard.digest(wave_path)}
    if child_plan['source_sha256'] != expected_sources:
        raise ValueError('Final source manifest differs from its frozen wave')
    children = {}
    for job in child_plan['jobs']:
        path = Path(job['output'])
        child = final['children'][job['id']]
        if guard.digest(path) != child['output_sha256']:
            raise ValueError('Final raw child evidence changed')
        children[job['id']] = {'returncode': child['returncode'], 'document': json.loads(path.read_text())}
    checked = suite.aggregate_children(child_plan, children, arena.source_hashes([Path(p) for p in expected_sources]))
    if checked['status'] != 'completed' or checked['matches'] != final['matches']:
        raise ValueError('Final raw matches do not reproduce their aggregate')
    final_audit_path = final_path.parent / 'replay-audit.json'
    if (Path(record['audit_file']).resolve() != final_audit_path
            or not json.loads(final_audit_path.read_text()).get('ok')):
        raise ValueError('Final replay audit is not passing')
    final_inputs = [Path(job['output']) for job in child_plan['jobs']]
    final_audit = audit_complete(final_inputs, 400)
    sources = {Path(name) for name in wave['source_sha256']}
    sources.update((SELF, Path(guard.__file__).resolve(), directory / guard.MANIFEST_NAME,
                    wave_path, result_path, controls_path, final_path, child_plan_path, final_audit_path))
    sources.update(final_inputs)
    sources.update(Path(row['opponent']) for row in opponents)
    sources.update(Path(name) for name in runtime['environment']['chess_python_source_sha256'])
    sources.add(Path(runtime['environment']['python_executable_resolved']))
    hashes = arena.source_hashes(sorted(sources))
    return {'schema_version': 1, 'created_utc': now(), 'wave_plan': str(wave_path),
            'wave_plan_sha256': guard.digest(wave_path), 'final_result': str(result_path),
            'final_result_sha256': guard.digest(result_path), 'control_plan': str(controls_path),
            'control_plan_sha256': guard.digest(controls_path), 'candidate': wave['candidate'],
            'candidate_name': candidate['name'], 'candidate_args': candidate['args'],
            'controls': opponents, 'source_sha256': hashes, 'environment': runtime['environment'],
            'guard_manifest_sha256': guard.digest(directory / guard.MANIFEST_NAME),
            'independent_final_reaudit': {key: final_audit[key] for key in
                ('ok', 'summary', 'referee_sha256', 'validator_sha256')},
            'workers': 4, 'batch_pairs': 5, 'pairs_per_opponent': 10,
            'concurrency_note': 'Two five-pair jobs per control; four is the worker ceiling.',
            'base_time': 180, 'increment': 2, 'max_plies': 1000,
            'scope': 'Separate regression controls; no pooled strength score or arena Elo.',
            'absolute_arena_elo': None}


def run(args):
    wave_path = args.wave_plan.resolve()
    wave = guard.load_bound_json(wave_path)
    directory = Path(wave['design_file']).resolve().parent
    controls_path = (args.control_plan or directory / 'release-controls-plan.json').resolve()
    # A rejected contender cannot touch the report, launch a game or reset a slot.
    with guard.experiment_lock(directory):
        runtime = guard.verify_manifest(directory)
        resolved = prepare(wave_path, controls_path, runtime)
        import arena_eval as arena
        import run_paired_suite as suite
        import v5_experiments as experiment
        output = directory / 'release-controls'
        output.mkdir(exist_ok=False)  # Consumes the single attempt before any game.
        plan_path = output / 'resolved-plan.json'
        experiment.immutable(plan_path, resolved)
        sources = {**resolved['source_sha256'], str(plan_path): guard.digest(plan_path)}
        report = {'status': 'running', 'started_utc': now(), 'resolved_plan': str(plan_path),
                  'resolved_plan_sha256': guard.digest(plan_path), 'controls': {},
                  'source_sha256': sources, 'environment': resolved['environment'],
                  'candidate': resolved['candidate_name'], 'candidate_args': resolved['candidate_args'],
                  'absolute_arena_elo': None, 'scope': resolved['scope']}
        target = output / 'report.json'
        arena.checkpoint(target, report)
        try:
            for control in resolved['controls']:
                if arena.source_hashes([Path(p) for p in sources]) != sources:
                    raise ValueError('A frozen release-control source/input changed')
                guard.verify_manifest(directory)
                report['active_control'] = control['name']
                arena.checkpoint(target, report)
                child_directory = output / control['name']
                flags = ['--trusted-local', '--candidate', resolved['candidate'], '--opponent', control['opponent'],
                         '--pairs', '10', '--batch-pairs', '5', '--workers', '4',
                         '--seed', str(control['seed_base']), '--base-time', '180', '--increment', '2',
                         '--max-plies', '1000', '--output-dir', str(child_directory)]
                flags += ['--candidate-arg=' + value for value in resolved['candidate_args']]
                for name in sorted(sources):
                    flags += ['--source', name]
                print('Starting release control:', control['name'], flush=True)
                code = suite.main(flags)
                child = json.loads((child_directory / 'suite.json').read_text())
                child_plan = json.loads((child_directory / 'plan.json').read_text())
                inputs = [Path(job['output']) for job in child_plan['jobs']]
                audit = audit_complete(inputs, 20, child_directory / 'replay-audit.json')
                summary = child.get('aggregate', {}).get(control['opponent'])
                verdict = control_verdict(summary, code,
                    audit['ok'] and child.get('status') == 'completed'
                    and control_audit_agrees(audit, summary, control['opponent']),
                    control['minimum_score'])
                verdict.update(suite_file=str(child_directory / 'suite.json'),
                               suite_sha256=guard.digest(child_directory / 'suite.json'),
                               audit_file=str(child_directory / 'replay-audit.json'), audit_ok=audit['ok'])
                report['controls'][control['name']] = verdict
                arena.checkpoint(target, report)
                if child.get('status') == 'aborted':
                    raise ValueError('Control suite aborted; preserving attempt without launching further controls')
                if arena.source_hashes([Path(p) for p in sources]) != sources:
                    raise ValueError('A frozen release-control source/input changed during play')
                guard.verify_manifest(directory)
            report['status'] = 'controls_passed' if all(row['passed'] for row in report['controls'].values()) else 'controls_failed'
        except (Exception, KeyboardInterrupt, SystemExit) as error:
            report.update(status='aborted', error=type(error).__name__ + ': ' + str(error),
                          aborted_control=report.get('active_control'))
        report.pop('active_control', None)
        report['finished_utc'] = now()
        arena.checkpoint(target, report)
        print('Release control result:', report['status'], target, flush=True)
        return int(report['status'] != 'controls_passed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wave-plan', type=Path, required=True)
    parser.add_argument('--control-plan', type=Path)
    parser.add_argument('--trusted-local', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Release controls rejected before launch: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
