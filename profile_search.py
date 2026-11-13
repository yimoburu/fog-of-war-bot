#!/usr/bin/env python3
"""Compare exact completed searches with frozen v2, using saved common inputs.

Run with .venv/bin/python profile_search.py. To repeat the identical sampled
worlds, add --inputs outputs/search-profile-v3.json. This measures local search
cost and semantic equivalence, not playing strength or arena performance.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import time
from unittest.mock import patch

import chess

from belief import repetition_key
from referee import FowBoard, observation
import search


ROOT = Path(__file__).resolve().parent
CASES = ((0, 2), (1, 15), (2, 32), (1, 55))
REPLAY = ROOT / 'outputs/resource-profile-v2.json'
FROZEN = ROOT / 'opponents/v2'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_hashes():
    paths = ('profile_search.py', 'search.py', 'belief.py', 'uniform_random.py',
             'referee.py', 'opponents/v2/search.py', 'opponents/v2/bot.py',
             'outputs/resource-profile-v2.json')
    return {path: digest((ROOT / path).read_bytes()) for path in paths}


def serialize_world(board):
    history = getattr(board, 'fow_repetitions', None)
    if history is None:
        history = Counter({repetition_key(board): 1})
    return {'fen': board.fen(en_passant='fen'),
            'repetitions': [[list(key), count] for key, count in sorted(
                history.items(), key=lambda row: repr(row[0]))]}


def deserialize_world(saved):
    board = FowBoard(saved['fen'])
    board.fow_repetitions = Counter({tuple(key): count for key, count in saved['repetitions']})
    return board


def capture_inputs():
    """Reproduce v2 preparation, supplying the policy only its observations."""
    frozen_bot = load_module('profile_frozen_v2_bot', FROZEN / 'bot.py')
    games = json.loads(REPLAY.read_text())['games']
    inputs = []
    for game_index, target_ply in CASES:
        game = games[game_index]
        side = game['candidate_color'] == 'white'
        state = frozen_bot.State(side, seed=game['candidate_seed'])
        truth = FowBoard()
        rng = random.Random(game['candidate_seed'])
        captured = {}

        def capture(worlds, moves, priors, deadline, **options):
            captured.clear()
            captured.update(worlds=[serialize_world(board) for board in worlds],
                            moves=[move.uci() for move in moves],
                            priors={move.uci(): priors[move] for move in moves},
                            complete=options['complete'])
            return moves[0], {'depth': 0, 'nodes': 0}

        # Only input construction uses a fixed clock. Finite successor and
        # recovery-proposal limits remain active, making sampled inputs stable
        # under host load. Timed searches below use the real monotonic clock.
        with patch.object(frozen_bot, 'search_root', capture), \
                patch('time.monotonic', return_value=0.0):
            for ply in range(target_ply + 1):
                state.observe(observation(truth, side))
                if truth.turn == side:
                    frozen_bot.choose_move(state, 180, rng, now=lambda: 0.0)
                if ply == target_ply:
                    break
                move = chess.Move.from_uci(game['moves'][ply])
                if truth.turn == side:
                    state.record_move(state.reconstruct()[0], move)
                truth.push(move)
        if truth.turn != side or not captured:
            raise RuntimeError('Selected replay position is not a captured candidate turn')
        inputs.append({'case': f'game{game_index}-ply{target_ply}',
                       'game_index': game_index, 'ply': target_ply,
                       'candidate_color': game['candidate_color'],
                       'candidate_seed': game['candidate_seed'],
                       'belief_status': state.belief.diagnostics['status'], **captured})
    return inputs


def benchmark(inputs, repeats, depth, node_limit):
    frozen = load_module('profile_frozen_v2_search', FROZEN / 'search.py')
    modules = {'frozen_v2': frozen, 'optimized': search}
    rows = []
    for case in inputs:
        moves = [chess.Move.from_uci(uci) for uci in case['moves']]
        priors = {move: case['priors'][move.uci()] for move in moves}
        samples = {name: [] for name in modules}
        expected = None
        for repetition in range(repeats):
            order = ('frozen_v2', 'optimized') if repetition % 2 == 0 else ('optimized', 'frozen_v2')
            for name in order:
                worlds = [deserialize_world(saved) for saved in case['worlds']]
                before = [serialize_world(board) for board in worlds]
                wall_started, cpu_started = time.perf_counter(), time.process_time()
                move, stats = modules[name].search_root(
                    worlds, moves, priors, time.monotonic() + 60.0,
                    complete=case['complete'], max_depth=depth, node_limit=node_limit)
                cpu_seconds = time.process_time() - cpu_started
                wall_seconds = time.perf_counter() - wall_started
                result = (move.uci(), stats['score'], stats['depth'])
                if stats['depth'] != depth:
                    raise RuntimeError(f'{case["case"]} {name}: did not finish requested depth')
                if expected is None:
                    expected = result
                if result != expected:
                    raise RuntimeError(f'{case["case"]}: exact result mismatch {result} != {expected}')
                if [serialize_world(board) for board in worlds] != before:
                    raise RuntimeError('Search did not restore a root world or history')
                samples[name].append({'wall_seconds': wall_seconds, 'cpu_seconds': cpu_seconds,
                                      'move': result[0], 'score': result[1],
                                      'depth': stats['depth'], 'nodes': stats['nodes']})
        row = {'case': case['case'], 'worlds': len(case['worlds']), 'moves': len(moves),
               'exact_results_equal': True, 'root_worlds_restored': True}
        for name, runs in samples.items():
            row[name] = {'runs': runs,
                         'median_wall_seconds': statistics.median(run['wall_seconds'] for run in runs),
                         'median_cpu_seconds': statistics.median(run['cpu_seconds'] for run in runs)}
        row['wall_speedup'] = row['frozen_v2']['median_wall_seconds'] / row['optimized']['median_wall_seconds']
        row['cpu_speedup'] = row['frozen_v2']['median_cpu_seconds'] / row['optimized']['median_cpu_seconds']
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, help='Reuse exact inputs embedded in an earlier profile JSON')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/search-profile-v3.json')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--depth', type=int, default=3)
    parser.add_argument('--node-limit', type=int, default=1000000)
    parser.add_argument('--host-activity', default='Not independently measured',
                        help='Declared concurrent host work during this measurement')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or not 1 <= args.depth <= 4 or args.node_limit < 1:
        parser.error('Use repeats1..10, depth1..4, and a positive node limit')
    before = source_hashes()
    inputs = json.loads(args.inputs.read_text())['inputs'] if args.inputs else capture_inputs()
    encoded_inputs = json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()
    rows = benchmark(inputs, args.repeats, args.depth, args.node_limit)
    after = source_hashes()
    totals = {name: {kind: sum(row[name][f'median_{kind}_seconds'] for row in rows)
                     for kind in ('wall', 'cpu')} for name in ('frozen_v2', 'optimized')}
    result = {
        'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'completed' if before == after else 'invalid_source_changed',
        'source_sha256': before, 'source_sha256_after': after, 'source_unchanged': before == after,
        'inputs_sha256': digest(encoded_inputs), 'inputs': inputs,
        'configuration': {'repeats': args.repeats, 'completed_depth': args.depth,
                          'node_limit': args.node_limit, 'per_search_deadline_seconds': 60,
                          'utility_scale': None, 'immediate_risks': None,
                          'alternating_implementation_order': True,
                          'primary_timing': 'process_cpu_seconds',
                          'declared_host_activity': args.host_activity,
                          'input_source': str(args.inputs) if args.inputs else str(REPLAY)},
        'environment': {'python': sys.version, 'platform': platform.platform(),
                        'chess': chess.__version__},
        'measurement_conditions': [
            'Exact common root worlds, repetition counters, moves and priors are embedded for replay.',
            'Input construction uses frozen v2 bot logic, current hashed helper modules and fixed clock/work bounds.',
            'Both search modules use the same hashed referee and repetition-key helpers.',
            'Timings cover search only; input construction and world deserialization are excluded.',
            'Process CPU timing is primary; wall timing is secondary and includes effects of concurrent host work.',
            'Node counts differ because redundant leaf checks were removed; compare completed depth and exact results.',
            'This is a local microbenchmark, not a 180ms playing-strength test or arena qualification.',
        ],
        'cases': rows,
        'aggregate': {'exact_results_equal': all(row['exact_results_equal'] for row in rows),
                      'sum_median_seconds': totals,
                      'wall_speedup': totals['frozen_v2']['wall'] / totals['optimized']['wall'],
                      'cpu_speedup': totals['frozen_v2']['cpu'] / totals['optimized']['cpu']},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(args.output), 'status': result['status'],
                      'aggregate': result['aggregate']}, sort_keys=True))
    return 0 if before == after else 1


if __name__ == '__main__':
    raise SystemExit(main())
