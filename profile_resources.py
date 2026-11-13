#!/usr/bin/env python3
"""Local in-process turn latency and whole-harness RSS; not arena isolation."""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import random
import resource
import sys
import time

import bot
import legacy_bot
from referee import FowBoard, fow_moves, observation, outcome, position_key

ROOT = Path(__file__).resolve().parent
SOURCE_NAMES = ('profile_resources.py', 'bot.py', 'legacy_bot.py', 'belief.py',
                'search.py', 'referee.py', 'uniform_random.py', 'requirements.txt')


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCE_NAMES}


def rss_measurement():
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == 'darwin':
        unit, multiplier = 'bytes', 1
    elif sys.platform.startswith('linux'):
        unit, multiplier = 'KiB', 1024
    else:
        unit, multiplier = 'platform-dependent', None
    total = raw * multiplier if multiplier is not None else None
    return {'raw_maxrss': raw, 'raw_unit': unit, 'max_rss_bytes': total,
            'max_rss_mib': total / (1024 * 1024) if total is not None else None,
            'scope': 'Entire profiling process: both policies, referee, Python and retained results.'}


def checkpoint(path, document):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True,
                                    allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def latency_summary(samples):
    values = sorted(samples)
    return {'samples': len(values), 'mean_ms': (1000 * sum(values) / len(values) if values else None),
            'p95_ms': (1000 * values[math.ceil(.95 * len(values)) - 1] if values else None),
            'max_ms': (1000 * values[-1] if values else None), 'p95_method': 'nearest rank'}


def diagnostics(state):
    result = {'search': dict(state.last_search),
              'pending_belief_events': len(state.belief_events)}
    keys = ('status', 'exact_history', 'complete_posterior', 'particles', 'expansions',
            'truncated', 'rejuvenated', 'recovery_count', 'recovery_attempts',
            'recovery_proposals', 'recovery_status')
    result['belief'] = {key: state.belief.diagnostics.get(key) for key in keys}
    return result


def candidate_outcome(result, side):
    if result == '1/2-1/2':
        return 'draw'
    if result in ('1-0', '0-1'):
        return 'win' if (result == '1-0') == side else 'loss'
    return 'unresolved'


def run_game(candidate_side, seed, base, increment, max_plies, search_ms):
    """Only the referee owns truth; policies receive strings, clocks and own moves.

    Observations are queued until the corresponding player's next active turn.
    This includes the observation after its own previous move. The measured
    charge spans observation consumption, choose_move and record_move. The
    supplied choose_move clock is conservatively reduced by observation cost.
    Referee rendering, legal-move checks and result serialization are excluded.
    """
    truth = FowBoard()
    states = {candidate_side: bot.State(candidate_side, seed=seed, search_ms=search_ms),
              not candidate_side: legacy_bot.State(not candidate_side)}
    rngs = {candidate_side: random.Random(seed), not candidate_side: random.Random(seed + 1)}
    queues = {True: [], False: []}
    clocks = {True: float(base), False: float(base)}
    repetitions = Counter({position_key(truth): 1})
    result = {'candidate_color': 'white' if candidate_side else 'black',
              'candidate_seed': seed, 'legacy_seed': seed + 1, 'status': 'running',
              'result': '*', 'reason': 'In progress', 'candidate_outcome': 'unresolved',
              'turns': [], 'moves': []}
    for ply in range(max_plies + 1):
        terminal = outcome(truth, repetitions)
        if terminal:
            result.update(result=terminal[0], reason=terminal[1], status='completed')
            break
        if ply == max_plies:
            result.update(result='1/2-1/2', reason='Move limit reached', status='completed')
            break
        for side in (True, False):
            queues[side].append(observation(truth, side))
        side = truth.turn
        state = states[side]
        policy = bot if side == candidate_side else legacy_bot
        before_clock = clocks[side]
        row = {'ply': ply, 'side': 'white' if side else 'black',
               'policy': 'candidate' if side == candidate_side else 'legacy',
               'observations': len(queues[side]), 'clock_before_seconds': before_clock,
               'choose_called': False, 'move': None}
        started = time.perf_counter()
        failure = None
        move = None
        observe_finished = choose_finished = started
        try:
            if before_clock <= 0:
                failure = 'Time forfeit before observation processing'
            else:
                for view in queues[side]:
                    state.observe(view)
                queues[side].clear()
                observe_finished = time.perf_counter()
                remaining = before_clock - (observe_finished - started)
                row['supplied_choose_clock_seconds'] = max(0.0, remaining)
                if remaining <= 0:
                    failure = 'Time forfeit during observation processing'
                else:
                    row['choose_called'] = True
                    move = policy.choose_move(state, remaining, rngs[side])
                    choose_finished = time.perf_counter()
                    if move is None:
                        failure = 'No move returned in nonterminal position'
                    else:
                        # This reconstructed board contains only policy-visible
                        # information. Never pass the referee's board to policy.
                        visible, _ = state.reconstruct()
                        state.record_move(visible, move)
                        row['move'] = move.uci()
        except (Exception, KeyboardInterrupt) as error:
            failure = f'{type(error).__name__}: {error}'
            row['exception_type'] = type(error).__name__
        finished = time.perf_counter()
        elapsed = finished - started
        clocks[side] = before_clock - elapsed
        row.update(elapsed_seconds=elapsed,
                   observation_seconds=max(0.0, observe_finished - started),
                   choose_seconds=max(0.0, choose_finished - observe_finished),
                   clock_after_charge_seconds=clocks[side])
        if side == candidate_side:
            row['diagnostics'] = diagnostics(state)
        if elapsed > before_clock and failure is None:
            failure = 'Time forfeit during turn processing'
        if failure is None and move not in fow_moves(truth):
            failure = 'Illegal move: ' + (move.uci() if move else 'None')
        if failure is not None:
            row['failure'] = failure
            row['clock_after_increment_seconds'] = clocks[side]
            result['turns'].append(row)
            result.update(result='0-1' if side else '1-0', reason=failure,
                          failed_side='white' if side else 'black', status='failure')
            if row.get('exception_type') == 'KeyboardInterrupt':
                result['status'] = 'interrupted'
            break
        truth.push(move)
        repetitions[position_key(truth)] += 1
        clocks[side] += increment
        row['clock_after_increment_seconds'] = clocks[side]
        result['turns'].append(row)
        result['moves'].append(move.uci())
    result['candidate_outcome'] = candidate_outcome(result['result'], candidate_side)
    result['final_fen'] = truth.fen(en_passant='fen')
    result['final_candidate_diagnostics'] = diagnostics(states[candidate_side])
    result['whole_harness_peak_rss'] = rss_measurement()
    return result


def summarize(games):
    turns = [turn for game in games for turn in game['turns']]
    candidate = [turn for turn in turns if turn['policy'] == 'candidate']
    particles = [turn['diagnostics']['belief']['particles'] for turn in candidate]
    particles = [value for value in particles if isinstance(value, int)]
    return {
        'attempted_games': len(games),
        'failures': sum(game['status'] != 'completed' for game in games),
        'candidate_wdl_raw_includes_failures': dict(Counter(game['candidate_outcome'] for game in games)),
        'turn_latency': {policy: latency_summary([row['elapsed_seconds'] for row in turns
                                                   if row['policy'] == policy])
                         for policy in ('candidate', 'legacy')},
        'candidate_search_depth_counts': dict(Counter(str(row['diagnostics']['search'].get('depth'))
                                                     for row in candidate)),
        'candidate_belief_status_counts': dict(Counter(str(row['diagnostics']['belief'].get('status'))
                                                      for row in candidate)),
        'candidate_particle_counts': {'samples': len(particles),
                                     'mean': sum(particles) / len(particles) if particles else None,
                                     'min': min(particles) if particles else None,
                                     'max': max(particles) if particles else None},
        'candidate_recoveries_across_games': sum(game['final_candidate_diagnostics']['belief']
                                                .get('recovery_count', 0) or 0 for game in games),
        'candidate_recovery_attempts_across_games': sum(game['final_candidate_diagnostics']['belief']
                                                      .get('recovery_attempts', 0) or 0 for game in games),
        'candidate_turns_with_pending_events': sum(row['diagnostics']['pending_belief_events'] > 0
                                                  for row in candidate),
        'whole_harness_peak_rss': rss_measurement()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-local', action='store_true', required=True)
    parser.add_argument('--seed', type=int, default=47980)
    parser.add_argument('--base-time', type=float, default=180)
    parser.add_argument('--increment', type=float, default=2)
    parser.add_argument('--max-plies', type=int, default=1000)
    parser.add_argument('--search-ms', type=float, default=180)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not (0 <= args.seed <= 2140000000 and math.isfinite(args.base_time)
            and 0 < args.base_time <= 3600 and math.isfinite(args.increment)
            and 0 <= args.increment <= 60 and 1 <= args.max_plies <= 10000
            and math.isfinite(args.search_ms) and 0 <= args.search_ms <= 2000):
        parser.error('Invalid bounded profile configuration')
    if args.output.exists() or args.output.with_name(args.output.name + '.tmp').exists():
        parser.error('Choose a fresh output path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    hashes = source_hashes()
    document = {'schema_version': 1, 'status': 'running', 'planned_games': 4, 'games': [],
                'configuration': {key: value for key, value in vars(args).items() if key != 'output'},
                'source_sha256': hashes,
                'environment': {'platform': platform.platform(), 'python': sys.version,
                                'chess': importlib.metadata.version('chess')},
                'initial_whole_harness_peak_rss': rss_measurement(),
                'limitations': [
                    'In-process profiling only: no arena rating or Linux sandbox verification.',
                    'RSS is the entire local harness, both bots, referee and retained results; not per-bot memory.',
                    'Turn latency includes queued observations, choose_move, reconstruction and record_move.',
                    'Choose clock is conservatively reduced by elapsed observation handling.',
                    'Excludes subprocess startup, IPC, freezing and referee work; not end-to-end arena latency.',
                    'All counters are observed diagnostics, not calibrated belief probabilities.',
                    'Two seed pairs are a resource smoke sample, not a strength benchmark.']}
    checkpoint(args.output, document)
    for seed in (args.seed, args.seed + 2):
        for side in (True, False):
            game = run_game(side, seed, args.base_time, args.increment, args.max_plies, args.search_ms)
            document['games'].append(game)
            document['aggregate'] = summarize(document['games'])
            try:
                unchanged = source_hashes() == hashes
            except OSError:
                unchanged = False
            document['source_unchanged'] = unchanged
            if not unchanged:
                document['status'] = 'aborted_source_changed'
            elif game['status'] == 'interrupted':
                document['status'] = 'interrupted'
            checkpoint(args.output, document)
            print(f"{len(document['games'])}/4 seed={seed} {game['candidate_color']} "
                  f"{game['candidate_outcome']} ({game['status']}); "
                  f"{len(game['turns'])} plies", flush=True)
            if document['status'] != 'running':
                return 1
    failed = any(game['status'] != 'completed' for game in document['games'])
    document['status'] = 'completed_with_failures' if failed else 'completed'
    checkpoint(args.output, document)
    print(json.dumps(document['aggregate'], sort_keys=True), flush=True)
    print(args.output, flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
