#!/usr/bin/env python3
"""Reproduce exact-visibility kernel timings on retained live-bank inputs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time

import chess

from fast_observation import matches_masks, observation_masks
from referee import FowBoard, observation


ROOT = Path(__file__).resolve().parent
SMOKE = ROOT / 'outputs/belief-quality-v3-smoke-638900.json'
TURN_INDICES = (0, 5, 10, 20, 30, 38, 45, 55, 65, 75, 85, 89)


def source_hashes():
    names = ('profile_observation.py', 'fast_observation.py', 'referee.py')
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def old_quick_filter(board, side, masks):
    """Frozen v3 prefix check, copied here so later belief changes do not alter it."""
    visible, occupied, ours, pieces = masks
    if board.occupied_co[side] != ours or board.occupied & visible != occupied:
        return False
    return all(board.pieces_mask(kind, color) & visible == squares
               for (kind, color), squares in pieces.items())


def build_cases():
    data = json.loads(SMOKE.read_text())
    turns = [row for game in data['games'] for row in game['turns'] if row['policy'] == 'candidate']
    positive = []

    def append(board, side, view, origin):
        assert observation(board, side) == view
        positive.append({'fen': board.fen(en_passant='fen', promoted=True),
                         'side': 'white' if side else 'black', 'view': view,
                         'expected': True, 'origin': origin})

    for index in TURN_INDICES:
        row = turns[index]
        side = row['side'] == 'white'
        for group_index, group in enumerate(row['diagnostics']['particle_groups'][:2]):
            board = FowBoard(group['fen'])
            origin = f'live_turn_{index}_particle_group_{group_index}'
            append(board, side, row['diagnostics']['observation'], origin)
            after = board.copy(stack=False)
            after.push(next(after.generate_pseudo_legal_moves()))
            append(after, side, observation(after, side), origin + '_after_own_move_off_turn')
    fixtures = ('k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
                'k4r2/8/8/8/8/B7/8/4K2R w K - 0 1')
    for index, fen in enumerate(fixtures):
        for mirrored, board in enumerate((FowBoard(fen), FowBoard(fen).mirror())):
            for side in (True, False):
                append(board, side, observation(board, side), f'fixture_{index}_mirror_{mirrored}')
    negative = []
    for case in positive:
        board, side = FowBoard(case['fen']), case['side'] == 'white'
        square = next(iter(board.pieces(chess.PAWN, side)), None)
        if square is None:
            square = board.king(side)
        board.remove_piece_at(square)
        assert observation(board, side) != case['view']
        negative.append({**case, 'fen': board.fen(en_passant='fen', promoted=True),
                         'expected': False, 'origin': case['origin'] + '_own_piece_removed'})
    return {'positive': positive, 'negative': negative}


def prepare(cases):
    prepared = []
    for case in cases:
        board, side = FowBoard(case['fen']), case['side'] == 'white'
        masks = observation_masks(case['view'], side)
        if matches_masks(board, side, masks) != case['expected']:
            raise RuntimeError('Compiled check disagrees with stored expected result')
        if (observation(board, side) == case['view']) != case['expected']:
            raise RuntimeError('Official oracle disagrees with stored expected result')
        prepared.append((board, side, case['view'], masks, case['expected']))
    return prepared


def measure(cases, loops, repeats):
    checks = {
        'official_string': lambda board, side, view, masks: observation(board, side) == view,
        'old_quick_plus_official_string': lambda board, side, view, masks:
            old_quick_filter(board, side, masks) and observation(board, side) == view,
        'compiled_masks': lambda board, side, view, masks: matches_masks(board, side, masks),
    }
    prepared = prepare(cases)
    expected = loops * sum(row[4] for row in prepared)
    runs = {name: [] for name in checks}
    for repetition in range(repeats):
        order = list(checks)
        if repetition % 2:
            order.reverse()
        for name in order:
            checker, total = checks[name], 0
            cpu_started, wall_started = time.process_time(), time.perf_counter()
            for _ in range(loops):
                for board, side, view, masks, _ in prepared:
                    total += checker(board, side, view, masks)
            wall = time.perf_counter() - wall_started
            cpu = time.process_time() - cpu_started
            if total != expected:
                raise RuntimeError('A timed checker disagreed with the official expected results')
            runs[name].append({'cpu_seconds': cpu, 'wall_seconds': wall,
                               'checks': len(prepared) * loops, 'matching_results': total})
    medians = {name: {kind: statistics.median(run[kind + '_seconds'] for run in values)
                     * 1e6 / (len(prepared) * loops) for kind in ('cpu', 'wall')}
               for name, values in runs.items()}
    return {'cases': len(prepared), 'runs': runs, 'median_microseconds_per_check': medians,
            'cpu_speedup_vs_old_quick_plus_string':
                medians['old_quick_plus_official_string']['cpu'] / medians['compiled_masks']['cpu']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, help='Reuse exact inputs embedded in an earlier output')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/observation-profile-v4.json')
    parser.add_argument('--loops', type=int, default=200)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--host-activity', default='Shared development host; other activity not independently measured')
    args = parser.parse_args()
    if not 1 <= args.loops <= 10000 or not 1 <= args.repeats <= 10:
        parser.error('Use loops1..10000 and repeats1..10')
    if args.output.exists():
        parser.error('Choose a fresh output path')
    before = source_hashes()
    cases = json.loads(args.inputs.read_text())['inputs'] if args.inputs else build_cases()
    results = {'positive': measure(cases['positive'], args.loops, args.repeats),
               'mixed_half_positive': measure(cases['positive'] + cases['negative'], args.loops, args.repeats)}
    compile_runs = []
    for _ in range(args.repeats):
        started = time.process_time()
        for _ in range(args.loops):
            for case in cases['positive']:
                observation_masks(case['view'], case['side'] == 'white')
        compile_runs.append((time.process_time() - started) * 1e6 / (args.loops * len(cases['positive'])))
    after = source_hashes()
    input_path = args.inputs or SMOKE
    output = {
        'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'completed' if before == after else 'invalid_source_changed',
        'source_sha256': before, 'source_sha256_after': after, 'source_unchanged': before == after,
        'input_source': str(input_path), 'input_source_sha256': hashlib.sha256(input_path.read_bytes()).hexdigest(),
        'inputs_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'inputs': cases, 'results': results, 'compile_cpu_us_per_view_runs': compile_runs,
        'compile_cpu_us_per_view_median': statistics.median(compile_runs),
        'configuration': {'loops': args.loops, 'repeats': args.repeats, 'primary_timing': 'process_cpu',
                          'alternating_checker_order': True, 'declared_host_activity': args.host_activity},
        'environment': {'python': sys.version, 'platform': platform.platform(), 'chess': chess.__version__},
        'limitations': ['Exact visibility kernel timing only; not whole-bot speed or playing strength.',
                        'Views are compiled once outside timed matching loops; compile cost is measured separately.',
                        'Physical inputs include observed live particles, off-turn successors and EP/castling fixtures.',
                        'Negative cases remove an own piece; difficult negative cases may have different costs.',
                        'The old quick filter is frozen inline to avoid later belief integration changing the baseline.'],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + '\n')
    print(json.dumps({'output': str(args.output), 'status': output['status'],
                      'positive_cpu_speedup': results['positive']['cpu_speedup_vs_old_quick_plus_string'],
                      'mixed_cpu_speedup': results['mixed_half_positive']['cpu_speedup_vs_old_quick_plus_string'],
                      'compile_cpu_us': output['compile_cpu_us_per_view_median']}, sort_keys=True))
    return int(before != after)


if __name__ == '__main__':
    raise SystemExit(main())
