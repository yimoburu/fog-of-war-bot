#!/usr/bin/env python3
"""Predeclare paired local matches, retain child evidence, aggregate fail-closed."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import signal
import subprocess
import sys
import time

from arena_eval import (ROOT_RUNTIME_DEPENDENCIES, checkpoint, classify, schedule,
                        source_hashes, summarize)

ROOT = Path(__file__).resolve().parent
V2_SNAPSHOT_FILES = ('bot.py', 'belief.py', 'search.py', 'referee.py',
                     'uniform_random.py', 'requirements.txt', 'SOURCES.md',
                     'provenance.json')
LIMITATIONS = [
    'Local trusted execution, not an arena sandbox or an official MIT rating.',
    'Concurrent workers share CPU and memory; contention can change time-limited decisions.',
    'Both colors of each seed pair stay in one worker; seed ranges do not overlap.',
    'Pair independence remains an assumption despite distinct seeds; policies/openings are shared.',
    'Aggregate inference requires every predeclared job, unchanged sources and zero failures.',
    'Aborted or failed child evidence is retained without a selected-subset strength estimate.',
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def plan_batches(pairs=100, batch_pairs=25, seed=171980, stride=10000, seed_bases=None):
    if not (1 <= pairs <= 4000 and 1 <= batch_pairs <= 1000 and stride > 0):
        raise ValueError('Require 1..4000 pairs, 1..1000 pairs per batch and positive seed stride')
    sizes = [min(batch_pairs, pairs - start) for start in range(0, pairs, batch_pairs)]
    bases = list(seed_bases) if seed_bases is not None else [seed + stride * i for i in range(len(sizes))]
    if len(bases) != len(sizes):
        raise ValueError('Provide exactly one seed base per batch')
    used = set()
    result = []
    for index, (count, base) in enumerate(zip(sizes, bases)):
        if not 0 <= base <= 2140000000 or base + 2 * count - 1 > 2147483647:
            raise ValueError('Seed range is out of bounds')
        seeds = set(range(base, base + 2 * count))
        if used & seeds:
            raise ValueError('Batch seed ranges overlap')
        used.update(seeds)
        result.append({'id': f'batch-{index:03d}', 'pairs': count, 'seed': base})
    return result


def aggregate_children(plan, children, current_hashes):
    """Never publish strength from a successful subset of a predeclared suite."""
    errors, combined = [], []
    if current_hashes != plan['source_sha256']:
        errors.append('Current sources differ from the immutable plan')
    expected_ids = {job['id'] for job in plan['jobs']}
    if set(children) != expected_ids:
        errors.append('Missing or unexpected child jobs')
    if (sum(job['pairs'] for job in plan['jobs']) != plan['pairs']
            or plan['planned_matches'] != 2 * plan['pairs']):
        errors.append('Plan batch totals are inconsistent')
    for job in plan['jobs']:
        prefix = job['id']
        record = children.get(prefix, {})
        document = record.get('document')
        if record.get('returncode') != 0 or not isinstance(document, dict):
            errors.append(prefix + ': unsuccessful process or missing evidence')
            continue
        if document.get('status') != 'completed' or document.get('source_unchanged') is not True:
            errors.append(prefix + ': child did not complete with unchanged sources')
        if document.get('source_sha256') != plan['source_sha256']:
            errors.append(prefix + ': source provenance differs from plan')
        expected_config = {
            'candidate': plan['candidate'], 'opponent': [plan['opponent']],
            'candidate_arg': plan['candidate_args'], 'opponent_arg': plan['opponent_args'],
            'games_per_color': job['pairs'], 'seed': job['seed'],
            'base_time': plan['base_time'], 'increment': plan['increment'],
            'max_plies': plan['max_plies'], 'trusted_local': True,
        }
        configuration = document.get('configuration', {})
        if (not isinstance(configuration, dict)
                or any(configuration.get(key) != value for key, value in expected_config.items())):
            errors.append(prefix + ': child configuration differs from plan')
        rows = document.get('matches', [])
        if (not isinstance(rows, list) or document.get('planned_matches') != job['pairs'] * 2
                or len(rows) != job['pairs'] * 2):
            errors.append(prefix + ': incomplete planned match count')
            continue
        expected = list(schedule([plan['opponent']], job['pairs'], job['seed']))
        seen = set()
        for index, (row, scheduled) in enumerate(zip(rows, expected)):
            if not isinstance(row, dict) or not isinstance(row.get('replay'), dict):
                errors.append(prefix + ': malformed match evidence')
                continue
            identity = (row.get('pair'), row.get('color'))
            if identity in seen or any(row.get(key) != value for key, value in scheduled.items()):
                errors.append(prefix + ': duplicate or incorrect seed/color pair')
            seen.add(identity)
            if row.get('id') != str(index):
                errors.append(prefix + ': incorrect local match identity')
            replay = row.get('replay', {})
            actual_outcome, actual_status = classify(replay, row.get('color'))
            if (row.get('status') != 'completed' or actual_status != 'completed'
                    or row.get('outcome') != actual_outcome):
                errors.append(prefix + ': failed game or inconsistent replay outcome')
            ours = [plan['python_executable'], '-u', plan['candidate'], *plan['candidate_args'],
                    '--seed', str(scheduled['bot_seed'])]
            theirs = [plan['python_executable'], '-u', plan['opponent'], *plan['opponent_args'],
                      '--seed', str(scheduled['opponent_seed'])]
            white, black = (ours, theirs) if scheduled['color'] == 'white' else (theirs, ours)
            if row.get('white_argv') != white or row.get('black_argv') != black:
                errors.append(prefix + ': executed command differs from plan')
            combined.append({**scheduled, 'pair': prefix + ':' + str(scheduled['pair']),
                             'id': prefix + ':' + str(index), 'batch': prefix,
                             'local_pair': scheduled['pair'], 'local_match_id': str(index),
                             'child_output': job['output'], 'outcome': row.get('outcome'),
                             'status': row.get('status')})
    if errors:
        return {'status': 'invalid', 'errors': sorted(set(errors)), 'aggregate': {},
                'absolute_arena_elo': None}
    return {'status': 'completed', 'errors': [], 'matches': combined,
            'aggregate': summarize(combined, [plan['opponent']]), 'absolute_arena_elo': None}


def child_command(plan, job):
    command = [plan['python_executable'], '-B', str(ROOT / 'arena_eval.py'), '--trusted-local',
               '--candidate', plan['candidate'], '--opponent', plan['opponent'],
               '--games-per-color', str(job['pairs']), '--seed', str(job['seed']),
               '--base-time', str(plan['base_time']), '--increment', str(plan['increment']),
               '--max-plies', str(plan['max_plies']), '--output', job['output']]
    command.extend('--candidate-arg=' + arg for arg in plan['candidate_args'])
    command.extend('--opponent-arg=' + arg for arg in plan['opponent_args'])
    for source in sorted(plan['source_sha256']):
        command.extend(['--source', source])
    return command


def execute(plan, plan_path, plan_digest, output_dir):
    pending = list(plan['jobs'])
    active, records = {}, {}
    reason = None
    next_source_check = 0

    def save_progress():
        checkpoint(output_dir / 'progress.json', {
            'status': 'aborting' if reason else 'running', 'plan_sha256': plan_digest,
            'reason': reason, 'jobs': records, 'pending': [job['id'] for job in pending]})

    def abort(message):
        nonlocal reason
        if reason is None:
            reason = message
            print('Aborting suite: ' + message, flush=True)
        for job_id, item in active.items():
            if item.get('cancel_at') is None and item['process'].poll() is None:
                # SIGINT lets arena_eval/referee unwind and close their bots.
                try:
                    item['process'].send_signal(signal.SIGINT)
                except ProcessLookupError:
                    continue
                item['cancel_at'] = time.monotonic()
                records[job_id]['cancellation'] = 'SIGINT requested; awaiting closure'
        save_progress()

    while pending or active:
        try:
            now = time.monotonic()
            if now >= next_source_check:
                next_source_check = now + 2
                try:
                    unchanged = (source_hashes([Path(p) for p in plan['source_sha256']])
                                 == plan['source_sha256']
                                 and hashlib.sha256(plan_path.read_bytes()).hexdigest() == plan_digest)
                except OSError:
                    unchanged = False
                if not unchanged:
                    abort('Source or immutable plan changed')
            while pending and len(active) < plan['workers'] and reason is None:
                job = pending.pop(0)
                record = {'status': 'starting', 'started_at': utc_now(),
                          'output': job['output'], 'log': job['log'], 'returncode': None}
                records[job['id']] = record
                log = Path(job['log']).open('xb')
                try:
                    process = subprocess.Popen(job['argv'], cwd=ROOT, stdout=log,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                except OSError as error:
                    log.close()
                    record.update(status='launch_failed', error=str(error))
                    abort(job['id'] + ': launch failed')
                    break
                record.update(status='running', pid=process.pid)
                active[job['id']] = {'process': process, 'log': log, 'cancel_at': None}
                print(f"Started {job['id']}: {job['pairs']} pairs, seed {job['seed']}", flush=True)
                save_progress()
            for job_id, item in list(active.items()):
                process = item['process']
                if item['cancel_at'] is not None and process.poll() is None:
                    elapsed = time.monotonic() - item['cancel_at']
                    if elapsed > 20:
                        process.kill()
                        records[job_id]['cancellation'] = 'Forced child kill; descendant closure unverified'
                    elif elapsed > 15 and not item.get('terminated'):
                        process.terminate()
                        item['terminated'] = True
                        records[job_id]['cancellation'] = 'Child SIGTERM; descendant closure unverified'
                code = process.poll()
                if code is None:
                    continue
                item['log'].close()
                records[job_id].update(returncode=code, finished_at=utc_now(),
                                       status='completed' if code == 0 else 'failed')
                del active[job_id]
                print(f'Finished {job_id}: exit {code}', flush=True)
                if code != 0:
                    abort(job_id + ': nonzero process exit')
                save_progress()
            if reason and not active:
                break
            time.sleep(.2)
        except KeyboardInterrupt:
            abort('Interrupted by operator')
        except Exception as error:
            abort(type(error).__name__ + ': ' + str(error))
    for job in pending:
        records[job['id']] = {'status': 'not_started', 'returncode': None,
                              'output': job['output'], 'log': job['log']}
    return records, reason


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-local', action='store_true', required=True)
    parser.add_argument('--candidate', type=Path, default=ROOT / 'bot.py')
    parser.add_argument('--opponent', type=Path, required=True)
    parser.add_argument('--candidate-arg', action='append', default=[])
    parser.add_argument('--opponent-arg', action='append', default=[])
    parser.add_argument('--source', type=Path, action='append', default=[])
    parser.add_argument('--pairs', type=int, default=100)
    parser.add_argument('--batch-pairs', type=int, default=25)
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--seed', type=int, default=171980)
    parser.add_argument('--seed-stride', type=int, default=10000)
    parser.add_argument('--seed-bases', type=int, nargs='+')
    parser.add_argument('--base-time', type=float, default=180)
    parser.add_argument('--increment', type=float, default=2)
    parser.add_argument('--max-plies', type=int, default=1000)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--plan-only', action='store_true', help='Write the immutable plan without running games')
    args = parser.parse_args(argv)
    try:
        jobs = plan_batches(args.pairs, args.batch_pairs, args.seed, args.seed_stride, args.seed_bases)
    except ValueError as error:
        parser.error(str(error))
    if not (math.isfinite(args.base_time) and 0 < args.base_time <= 3600
            and math.isfinite(args.increment) and 0 <= args.increment <= 60
            and 1 <= args.max_plies <= 10000):
        parser.error('Invalid bounded match configuration')
    dependencies = [ROOT / name for name in ('run_paired_suite.py', 'arena_eval.py', 'referee.py',
                    'uniform_random.py', 'requirements.txt', 'belief.py', 'search.py')
                    if (ROOT / name).exists()]
    if any(path.resolve() == ROOT / 'bot.py' for path in (args.candidate, args.opponent)):
        dependencies.extend(ROOT / name for name in ROOT_RUNTIME_DEPENDENCIES)
    if any(path.resolve() == ROOT / 'v2_bot.py' for path in (args.candidate, args.opponent)):
        dependencies.extend(ROOT / 'opponents' / 'v2' / name for name in V2_SNAPSHOT_FILES)
    sources = sorted({path.resolve() for path in [args.candidate, args.opponent, *dependencies, *args.source]})
    if any(not path.is_file() for path in sources):
        parser.error('Every script/source must be an existing file')
    output_dir = args.output_dir.resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error('Choose a fresh output directory; existing evidence is never overwritten')
    plan = {'schema_version': 1, 'created_at': utc_now(), 'python_executable': sys.executable,
            'candidate': str(args.candidate.resolve()), 'opponent': str(args.opponent.resolve()),
            'candidate_args': args.candidate_arg, 'opponent_args': args.opponent_arg,
            'pairs': args.pairs, 'planned_matches': args.pairs * 2, 'workers': args.workers,
            'base_time': args.base_time, 'increment': args.increment, 'max_plies': args.max_plies,
            'source_sha256': source_hashes(sources), 'jobs': jobs, 'limitations': LIMITATIONS,
            'absolute_arena_elo': None}
    for job in jobs:
        job.update(output=str(output_dir / (job['id'] + '.json')),
                   log=str(output_dir / (job['id'] + '.log')))
        job['argv'] = child_command(plan, job)
    plan_path = output_dir / 'plan.json'
    data = (json.dumps(plan, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    with plan_path.open('xb') as stream:
        stream.write(data)
    plan_path.chmod(0o444)
    digest = hashlib.sha256(data).hexdigest()
    with (output_dir / 'plan.sha256').open('x') as stream:
        stream.write(digest + '\n')
    (output_dir / 'plan.sha256').chmod(0o444)
    print(f'Frozen plan: {plan_path} ({digest})', flush=True)
    if args.plan_only:
        return 0
    records, abort_reason = execute(plan, plan_path, digest, output_dir)
    children = {}
    for job in jobs:
        record = records[job['id']]
        path = Path(job['output'])
        document = None
        try:
            raw = path.read_bytes()
            record['output_sha256'] = hashlib.sha256(raw).hexdigest()
            document = json.loads(raw)
            record['child_status'] = document.get('status')
            record['retained_matches'] = len(document.get('matches', []))
        except (OSError, ValueError, AttributeError) as error:
            record['evidence_error'] = str(error)
        children[job['id']] = {'returncode': record.get('returncode'), 'document': document}
    try:
        current = source_hashes(sources)
    except OSError:
        current = {}
    try:
        plan_unchanged = hashlib.sha256(plan_path.read_bytes()).hexdigest() == digest
    except OSError:
        plan_unchanged = False
    if not plan_unchanged:
        abort_reason = abort_reason or 'Immutable plan changed before final aggregation'
    try:
        result = aggregate_children(plan, children, current)
    except (KeyError, TypeError, ValueError) as error:
        result = {'status': 'invalid', 'aggregate': {}, 'absolute_arena_elo': None,
                  'errors': ['Malformed child evidence: ' + str(error)]}
    if abort_reason:
        result.update(status='aborted', aggregate={})
        result['errors'].append(abort_reason)
        result.pop('matches', None)
    result.update(plan_sha256=digest, plan_file=str(plan_path), finished_at=utc_now(),
                  planned_pairs=args.pairs, planned_matches=args.pairs * 2,
                  children=records, limitations=LIMITATIONS)
    checkpoint(output_dir / 'suite.json', result)
    print(f"Suite {result['status']}: {output_dir / 'suite.json'}", flush=True)
    return int(result['status'] != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
