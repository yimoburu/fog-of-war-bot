#!/usr/bin/env python3
"""Live observation-only belief diagnostics, with truth inspected after choice.

Example: .venv/bin/python profile_belief_quality.py --trusted-local --pairs 1
  --candidate v4 --risk-policy bank --search-ms 600 --particles 128
  --worlds 4 --depth 4 --nodes 60000 --output outputs/belief-smoke.json

The candidate runs in its own imported module tree and receives observations,
its clock and its own moves. The opponent is the isolated frozen-v2 subprocess.
Post-choice oracle analysis never feeds results back into either policy.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import random
import sys
import time

import chess

from referee import Bot, FowBoard, fow_moves, observation, outcome, position_key


ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / 'opponents/v2'
FROZEN_MODULES = ('referee', 'uniform_random', 'belief', 'search', 'bot')
MODULES = ('referee', 'uniform_random', 'fast_observation', 'risk_model',
           'belief', 'search', 'bot')
CURRENT_CANDIDATES = ('v3', 'v4')
SEARCH_DEFAULTS = {'particles': 64, 'worlds': 4, 'depth': 3, 'nodes': 18000}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checkpoint(path, document):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temporary.replace(path)


def verify_snapshot():
    manifest = json.loads((SNAPSHOT / 'provenance.json').read_text())
    if manifest['source_commit'] != '65fde033a2e65394a676e44969a992832ee17470':
        raise ValueError('Unexpected frozen-v2 source commit')
    expected = {'bot.py', 'belief.py', 'search.py', 'referee.py',
                'uniform_random.py', 'requirements.txt', 'SOURCES.md'}
    if set(manifest['files']) != expected:
        raise ValueError('Unexpected frozen-v2 file manifest')
    for name, expected_hash in manifest['files'].items():
        if sha((SNAPSHOT / name).read_bytes()) != expected_hash:
            raise ValueError('Frozen-v2 snapshot changed: ' + name)


def source_hashes(candidate):
    paths = {ROOT / 'profile_belief_quality.py', ROOT / 'referee.py', ROOT / 'v2_bot.py',
             SNAPSHOT / 'provenance.json', *(SNAPSHOT / (name + '.py') for name in FROZEN_MODULES),
             SNAPSHOT / 'requirements.txt', SNAPSHOT / 'SOURCES.md'}
    if candidate in CURRENT_CANDIDATES:
        paths.update(ROOT / (name + '.py') for name in MODULES)
        paths.add(ROOT / 'requirements.txt')
        paths.add(ROOT / 'risk_model.json')
    return {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in sorted(paths)}


def load_candidate(candidate):
    """Resolve imports within one tree, then restore all ambient module names."""
    if candidate not in ('v2', *CURRENT_CANDIDATES):
        raise ValueError('Unknown candidate')
    directory = SNAPSHOT if candidate == 'v2' else ROOT
    modules = FROZEN_MODULES if candidate == 'v2' else MODULES
    previous = {name: sys.modules.get(name) for name in MODULES}
    loaded = {}
    try:
        for name in modules:
            unique = '_belief_profile_' + candidate + '_' + name
            spec = importlib.util.spec_from_file_location(unique, directory / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[unique] = module
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
        if any(Path(module.__file__).resolve() != directory / (name + '.py')
               for name, module in loaded.items()):
            raise RuntimeError('Candidate dependency escaped its selected tree')
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return loaded


class PolicyTrace:
    """Observe real policy calls without supplying any referee information."""
    def __init__(self, policy, state):
        self.policy, self.state = policy, state
        self.original_search = policy.search_root
        self.original_observe = state.belief.observe
        policy.search_root = self.search
        state.belief.observe = self.observe
        self.reset()

    def reset(self):
        self.root = None
        self.search_seconds = 0.0
        self.inference_seconds = 0.0
        self.inference_updates = 0

    def observe(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return self.original_observe(*args, **kwargs)
        finally:
            self.inference_seconds += time.perf_counter() - started
            self.inference_updates += 1

    def search(self, worlds, moves, priors, deadline, **options):
        # Keep references to these actual sampled worlds. Search restores them;
        # expensive snapshots and all oracle comparisons occur after choice.
        self.root = {'worlds': worlds, 'moves': list(moves),
                     'priors': {move.uci(): priors[move] for move in moves},
                     'complete': options.get('complete', True),
                     'utility_scale': options.get('utility_scale'),
                     'immediate_risks': {move.uci(): options['immediate_risks'][move]
                                         for move in moves}
                     if options.get('immediate_risks') is not None else None}
        started = time.perf_counter()
        try:
            return self.original_search(worlds, moves, priors, deadline, **options)
        finally:
            self.search_seconds += time.perf_counter() - started

    def close(self):
        self.policy.search_root = self.original_search
        self.state.belief.observe = self.original_observe


def immediate_loss(board, move, side):
    board.push(move)
    try:
        king = board.king(side)
        return king is None or board.is_attacked_by(not side, king)
    finally:
        board.pop()


def history_fingerprint(board):
    history = getattr(board, 'fow_repetitions', {})
    entries = sorted(((list(key), count) for key, count in history.items()), key=repr)
    return sha(json.dumps(entries, separators=(',', ':')).encode())


def referee_history_key(board):
    """Oracle-side key, deliberately independent of candidate module calls."""
    ep = (board.ep_square if board.ep_square is not None
          and any(board.generate_pseudo_legal_ep()) else None)
    return (board.pawns, board.knights, board.bishops, board.rooks, board.queens,
            board.kings, board.occupied_co[0], board.occupied_co[1], board.turn,
            int(board.clean_castling_rights()), ep)


def loss_axes(diagnostic):
    """Non-exclusive evidence: absent killing pieces and ranking can coexist."""
    if not diagnostic['chosen_true_immediate_loss']:
        return {}
    chosen_risk = diagnostic['chosen_bank_immediate_loss_fraction']
    safe_risks = [diagnostic['bank_immediate_loss_fraction_by_move'][move]
                  for move in diagnostic['true_safe_moves']
                  if diagnostic['bank_immediate_loss_fraction_by_move'][move] is not None]
    attackers = diagnostic['chosen_true_attackers']
    return {
        'avoidable_at_this_turn': bool(diagnostic['true_safe_moves']),
        'all_actual_killing_pieces_absent_from_bank': bool(attackers) and all(
            attacker['bank_count_at_same_square'] == 0 for attacker in attackers),
        'true_safe_alternative_in_actual_shortlist': bool(diagnostic['true_safe_shortlisted_moves']),
        'true_safe_alternative_has_lower_bank_risk': chosen_risk is not None and any(
            risk < chosen_risk for risk in safe_risks),
        'chosen_looks_safer_than_all_true_safe_alternatives': chosen_risk is not None and bool(safe_risks)
        and all(chosen_risk < risk for risk in safe_risks),
    }


def diagnose_after_choice(state, trace, truth, chosen, actual_history):
    """Referee-only diagnostics; called after choose_move has returned."""
    side, view = state.side, state.tracker.current
    visible, policy_moves = state.reconstruct()
    truth_moves = fow_moves(truth)
    if chosen not in truth_moves:
        raise ValueError('Cannot diagnose an illegal candidate move')
    pending = len(state.belief_events)
    raw = list(state.belief.particles)
    particles = raw if not pending else []
    consistent = [observation(board, side) == view and board.turn == side for board in particles]
    true_key = position_key(truth)
    groups = {}
    history_keys = set()
    exact_history_support = 0
    for index, board in enumerate(particles):
        key, fingerprint = position_key(board), history_fingerprint(board)
        history_keys.add((key, fingerprint))
        group = groups.setdefault(key, {'representative': board, 'indices': [], 'histories': Counter()})
        group['indices'].append(index)
        group['histories'][fingerprint] += 1
        exact_history_support += (key == true_key and
                                  getattr(board, 'fow_repetitions', {}) == actual_history)

    true_safe = [move for move in truth_moves if not immediate_loss(truth, move, side)]
    visible_safe = [move for move in policy_moves if not immediate_loss(visible, move, side)]
    bank_risks = {}
    for move in truth_moves:
        losses = sum(len(group['indices']) for group in groups.values()
                     if immediate_loss(group['representative'], move, side))
        bank_risks[move.uci()] = losses / len(particles) if particles else None
    chosen_loss = immediate_loss(truth, chosen, side)
    shortlist = trace.root['moves'] if trace.root is not None else None
    shortlisted_safe = ([move for move in shortlist if move in true_safe]
                        if shortlist is not None else None)
    chosen_risk = bank_risks[chosen.uci()]
    if not chosen_loss:
        attribution = 'survived_immediate_reply'
    elif not true_safe:
        attribution = 'no_true_safe_move_at_this_turn'
    elif not particles:
        attribution = 'avoidable_loss_without_current_particles'
    elif chosen_risk == 0:
        attribution = 'avoidable_loss_missing_hidden_threat_in_bank'
    elif shortlist is None:
        attribution = 'avoidable_loss_during_fallback_no_observed_shortlist'
    elif not shortlisted_safe:
        attribution = 'avoidable_loss_no_true_safe_move_in_shortlist'
    else:
        attribution = 'avoidable_loss_ranking_despite_shortlisted_safe_move'

    after = truth.copy(stack=False)
    after.push(chosen)
    king = after.king(side)
    attackers = []
    if king is not None:
        for square in after.attackers(not side, king):
            piece = after.piece_at(square)
            attackers.append({'square': chess.square_name(square), 'piece': piece.symbol(),
                              'hidden_before_choice': view[square] == '?',
                              'bank_count_at_same_square': sum(board.piece_at(square) == piece
                                                               for board in particles)})
    sampled = trace.root['worlds'] if trace.root is not None else []
    multiplicities = [len(group['indices']) for group in groups.values()]
    count = len(particles)
    retained = []
    for key, group in groups.items():
        representative = group['representative']
        retained.append({'fen': representative.fen(en_passant='fen'),
                         'physical_key_sha256': sha(repr(key).encode()),
                         'particle_indices': group['indices'],
                         'history_hash_counts': dict(group['histories']),
                         'matches_true_physical_position': key == true_key,
                         'chosen_move_immediately_loses': immediate_loss(representative, chosen, side)})
    return {
        'observation': view, 'truth_fen_after_policy_return': truth.fen(en_passant='fen'),
        'policy_legal_set_matches_referee': set(policy_moves) == set(truth_moves),
        'belief_diagnostics': dict(state.belief.diagnostics), 'search': dict(state.last_search),
        'pending_events_before_recording_own_move': pending,
        'raw_particle_count': len(raw), 'current_particle_count': count,
        'current_particles_all_match_observation': all(consistent) if particles else None,
        'truth_placement_support_count': sum(board.board_fen() == truth.board_fen() for board in particles),
        'truth_physical_position_support_count': sum(position_key(board) == true_key for board in particles),
        'truth_full_repetition_history_support_count': exact_history_support,
        'unique_physical_positions': len(groups), 'unique_position_history_pairs': len(history_keys),
        'duplicate_physical_slots': count - len(groups),
        'physical_diversity_effective_count': count * count / sum(n * n for n in multiplicities)
        if count else 0,
        'particle_groups': retained,
        'sampled_search_worlds': len(sampled),
        'sampled_search_unique_physical_positions': len({position_key(board) for board in sampled}),
        'sampled_search_truth_support_count': sum(position_key(board) == true_key for board in sampled),
        'legal_moves': [move.uci() for move in truth_moves],
        'true_safe_moves': [move.uci() for move in true_safe],
        'visible_safe_moves': [move.uci() for move in visible_safe],
        'actual_shortlist': [move.uci() for move in shortlist] if shortlist is not None else None,
        'true_safe_shortlisted_moves': [move.uci() for move in shortlisted_safe]
        if shortlisted_safe is not None else None,
        'actual_root_priors': trace.root['priors'] if trace.root else None,
        'root_utility_scale': trace.root['utility_scale'] if trace.root else None,
        'root_immediate_risks': trace.root['immediate_risks'] if trace.root else None,
        'bank_immediate_loss_fraction_by_move': bank_risks,
        'chosen_move': chosen.uci(), 'chosen_true_immediate_loss': chosen_loss,
        'chosen_bank_immediate_loss_fraction': chosen_risk,
        'chosen_true_attackers': attackers, 'immediate_loss_attribution': attribution,
    }


def run_game(modules, side, seed, args):
    policy = modules['bot']
    options = {'seed': seed, 'search_ms': args.search_ms}
    if args.candidate in CURRENT_CANDIDATES:
        options.update({name: getattr(args, name) for name in SEARCH_DEFAULTS})
        if args.risk_policy is not None:
            options['risk_policy'] = args.risk_policy
    state = policy.State(side, **options)
    rng, truth = random.Random(seed), FowBoard()
    queues = {True: [], False: []}
    clocks = {True: float(args.base_time), False: float(args.base_time)}
    repetitions = Counter({position_key(truth): 1})
    exact_history = Counter({referee_history_key(truth): 1})
    argv = [sys.executable, '-B', '-u', str(ROOT / 'v2_bot.py'), '--seed', str(seed + 1),
            '--search-ms', str(args.opponent_search_ms)]
    result = {'candidate_color': 'white' if side else 'black', 'candidate_seed': seed,
              'opponent_seed': seed + 1, 'opponent_argv': argv,
              'candidate_policy': args.candidate, 'risk_policy': getattr(state, 'risk_policy', 'frozen_v2'),
              'status': 'running', 'result': '*', 'reason': 'In progress', 'turns': [], 'moves': []}
    opponent, trace = None, PolicyTrace(policy, state)
    try:
        opponent = Bot(argv, not side, args.base_time, args.increment)
        for ply in range(args.max_plies + 1):
            if opponent.failure:
                raise RuntimeError('Opponent protocol failure: ' + opponent.failure)
            terminal = outcome(truth, repetitions)
            if terminal is not None:
                result.update(status='completed', result=terminal[0], reason=terminal[1])
                break
            if ply == args.max_plies:
                result.update(status='completed', result='1/2-1/2', reason='Move limit reached')
                break
            for color in (True, False):
                queues[color].append((observation(truth, color), ply))
            mover = truth.turn
            row = {'ply': ply, 'side': 'white' if mover else 'black',
                   'policy': 'candidate' if mover == side else 'frozen_v2',
                   'clock_before_seconds': clocks[mover], 'observations': len(queues[mover])}
            started = time.perf_counter()
            if mover == side:
                trace.reset()
                for view, _ in queues[mover]:
                    state.observe(view)
                queues[mover].clear()
                choose_started = time.perf_counter()
                remaining = clocks[mover] - (choose_started - started)
                if remaining <= 0:
                    raise TimeoutError('Candidate time forfeit before choosing')
                chosen = policy.choose_move(state, remaining, rng)
                choose_finished = time.perf_counter()
                # First oracle access for diagnostics: the policy has returned.
                diagnostic_started = time.perf_counter()
                diagnostic = diagnose_after_choice(state, trace, truth, chosen, exact_history)
                diagnostic_seconds = time.perf_counter() - diagnostic_started
                record_started = time.perf_counter()
                visible, _ = state.reconstruct()
                state.record_move(visible, chosen)
                record_seconds = time.perf_counter() - record_started
                charged = choose_finished - started + record_seconds
                row.update(diagnostics=diagnostic, diagnostic_seconds_excluded=diagnostic_seconds,
                           choose_seconds=choose_finished - choose_started,
                           inference_seconds=trace.inference_seconds,
                           inference_updates=trace.inference_updates,
                           search_seconds=trace.search_seconds,
                           other_choose_seconds=max(0.0, choose_finished - choose_started -
                                                    trace.inference_seconds - trace.search_seconds))
            else:
                for view, observed_ply in queues[mover]:
                    opponent.observe(view, observed_ply)
                queues[mover].clear()
                remaining = clocks[mover] - (time.perf_counter() - started)
                uci, _ = opponent.move(remaining)
                chosen = chess.Move.from_uci(uci)
                charged = time.perf_counter() - started
            clocks[mover] -= charged
            row.update(move=chosen.uci(), charged_seconds=charged, clock_after_charge_seconds=clocks[mover])
            result['turns'].append(row)
            if clocks[mover] < 0:
                result.update(status='failure', result='0-1' if mover else '1-0', reason='Time forfeit')
                break
            if chosen not in fow_moves(truth):
                result.update(status='failure', result='0-1' if mover else '1-0', reason='Illegal move')
                break
            truth.push(chosen)
            repetitions[position_key(truth)] += 1
            exact_history[referee_history_key(truth)] += 1
            clocks[mover] += args.increment
            result['moves'].append(chosen.uci())
    except (Exception, KeyboardInterrupt) as error:
        result.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failure',
                      reason=f'{type(error).__name__}: {error}')
    finally:
        trace.close()
        if opponent is not None:
            opponent.close()
            if opponent.failure and result['status'] == 'completed':
                result.update(status='failure', reason='Opponent protocol failure: ' + opponent.failure)
    result['candidate_outcome'] = ('draw' if result['result'] == '1/2-1/2' else
                                   'win' if result['result'] == ('1-0' if side else '0-1') else
                                   'loss' if result['result'] in ('1-0', '0-1') else 'unresolved')
    result['final_fen'] = truth.fen(en_passant='fen')
    candidate_turns = [row for row in result['turns'] if row['policy'] == 'candidate']
    result['candidate_loss_attribution'] = (
        candidate_turns[-1]['diagnostics']['immediate_loss_attribution']
        if result['candidate_outcome'] == 'loss' and result['reason'] == 'King capturable'
        and candidate_turns else None)
    return result


def summarize(games):
    turns = [row for game in games for row in game['turns'] if row['policy'] == 'candidate']
    current = [row for row in turns if row['diagnostics']['current_particle_count']]
    slots = sum(row['diagnostics']['current_particle_count'] for row in current)
    duplicates = sum(row['diagnostics']['duplicate_physical_slots'] for row in current)
    axes = [loss_axes(row['diagnostics']) for row in turns
            if row['diagnostics']['chosen_true_immediate_loss']]
    return {
        'games': len(games), 'failures': sum(game['status'] != 'completed' for game in games),
        'raw_candidate_wdl': dict(Counter(game['candidate_outcome'] for game in games)),
        'candidate_turns': len(turns), 'turns_with_current_particles': len(current),
        'turns_with_true_placement_in_bank': sum(bool(row['diagnostics']['truth_placement_support_count'])
                                                 for row in current),
        'turns_with_true_physical_position_in_bank': sum(bool(row['diagnostics']['truth_physical_position_support_count'])
                                                         for row in current),
        'turns_with_full_true_repetition_history_in_bank': sum(bool(row['diagnostics']['truth_full_repetition_history_support_count'])
                                                               for row in current),
        'particle_slots_observed': slots, 'duplicate_physical_slots_observed': duplicates,
        'duplicate_physical_slot_fraction': duplicates / slots if slots else None,
        'search_world_slots': sum(row['diagnostics']['sampled_search_worlds'] for row in turns),
        'unique_search_world_slots': sum(row['diagnostics']['sampled_search_unique_physical_positions'] for row in turns),
        'candidate_immediate_loss_attributions': dict(Counter(row['diagnostics']['immediate_loss_attribution']
                                                             for row in turns if row['diagnostics']['chosen_true_immediate_loss'])),
        'game_loss_attributions': dict(Counter(game['candidate_loss_attribution'] for game in games
                                              if game['candidate_loss_attribution'] is not None)),
        'immediate_loss_evidence_axes_nonexclusive': dict(Counter(
            key for row in axes for key, value in row.items() if value)),
        'search_depth_counts': dict(Counter(str(row['diagnostics']['search'].get('depth')) for row in turns)),
        'mean_phase_ms': {key: 1000 * sum(row[key] for row in turns) / len(turns) if turns else None
                          for key in ('choose_seconds', 'inference_seconds', 'search_seconds',
                                      'other_choose_seconds', 'diagnostic_seconds_excluded')},
        'all_observed_current_banks_match_views': all(row['diagnostics']['current_particles_all_match_observation']
                                                     for row in current),
        'all_candidate_legal_sets_match_referee': all(row['diagnostics']['policy_legal_set_matches_referee'] for row in turns),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-local', action='store_true', required=True)
    parser.add_argument('--candidate', choices=('v2', 'v3', 'v4'), default='v4',
                        help='v2 is frozen; v4 is the current worktree; v3 remains an alias for the current worktree')
    parser.add_argument('--risk-policy', choices=('legacy', 'hypothesis', 'robust', 'bank', 'learned'))
    parser.add_argument('--pairs', type=int, default=1)
    parser.add_argument('--seed', type=int, default=638900)
    parser.add_argument('--base-time', type=float, default=180)
    parser.add_argument('--increment', type=float, default=2)
    parser.add_argument('--search-ms', type=float, default=180)
    parser.add_argument('--opponent-search-ms', type=float, default=180)
    for name, default in SEARCH_DEFAULTS.items():
        parser.add_argument('--' + name, type=int, default=default)
    parser.add_argument('--max-plies', type=int, default=1000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not (1 <= args.pairs <= 10 and 0 <= args.seed <= 2140000000
            and 1 <= args.max_plies <= 10000 and math.isfinite(args.base_time)
            and 0 < args.base_time <= 3600 and math.isfinite(args.increment)
            and 0 <= args.increment <= 60 and all(math.isfinite(value) and 0 <= value <= 2000
                                                for value in (args.search_ms, args.opponent_search_ms))
            and 1 <= args.particles <= 1024 and 1 <= args.worlds <= 32
            and 1 <= args.depth <= 6 and 1 <= args.nodes <= 1000000):
        parser.error('Invalid bounded profiling configuration')
    if args.candidate == 'v2' and args.risk_policy is not None:
        parser.error('Frozen v2 has no selectable risk policy')
    if args.candidate == 'v2' and any(getattr(args, name) != default
                                      for name, default in SEARCH_DEFAULTS.items()):
        parser.error('Frozen v2 cannot change particles, worlds, depth or nodes')
    if args.output.exists() or args.output.with_name(args.output.name + '.tmp').exists():
        parser.error('Choose a fresh output path; existing evidence is never overwritten')
    verify_snapshot()
    hashes = source_hashes(args.candidate)
    modules = load_candidate(args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    document = {
        'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'running', 'planned_games': 2 * args.pairs, 'games': [],
        'configuration': {key: value for key, value in vars(args).items() if key != 'output'},
        'source_sha256': hashes,
        'candidate_module_paths': {name: str(module.__file__) for name, module in modules.items()},
        'environment': {'python': sys.version, 'platform': platform.platform(), 'chess': chess.__version__},
        'limitations': [
            'Live local diagnostics, not a strength estimate or arena qualification.',
            'Candidate v3 is a compatibility alias for the current v4 worktree, not an immutable historical v3 snapshot.',
            'The candidate receives only observations, clocks and its own moves; oracle diagnostics run after choice.',
            'Actual current banks/shortlists are observed live; no past particle banks are reconstructed.',
            'Candidate module dependencies are isolated by imported module identity; opponent runs isolated v2 subprocess.',
            'Candidate clocks exclude post-choice diagnostics; opponent clocks include local subprocess IPC.',
            'Small tracing overhead is included in candidate decisions; host scheduling can still alter timed choices.',
            'Physical identity uses official repetition position semantics, excluding clocks and repetition histories.',
            'Duplicate physical particles can represent distinct valid draw histories; duplication alone does not prove wasted probability mass.',
            'Particle frequencies and recovery hypotheses are uncalibrated; missing true full position does not alone imply missed threats.',
            'Attribution concerns immediate loss at the chosen turn, not the entire causal history of a game.',
        ],
    }
    checkpoint(args.output, document)
    for pair in range(args.pairs):
        for side in (True, False):
            game = run_game(modules, side, args.seed + 2 * pair, args)
            game['pair'] = pair
            document['games'].append(game)
            try:
                unchanged = source_hashes(args.candidate) == hashes
            except OSError:
                unchanged = False
            document['source_unchanged'] = unchanged
            document['aggregate'] = summarize(document['games'])
            if not unchanged:
                document.update(status='aborted_source_changed', aggregate={})
            elif game['status'] != 'completed':
                document['status'] = 'interrupted' if game['status'] == 'interrupted' else 'aborted_failure'
            checkpoint(args.output, document)
            print(f"{len(document['games'])}/{document['planned_games']} "
                  f"{game['candidate_color']} {game['candidate_outcome']} {game['reason']} "
                  f"({len(game['moves'])} plies)", flush=True)
            if document['status'] != 'running':
                return 1
    document['status'] = 'completed'
    checkpoint(args.output, document)
    print(json.dumps(document['aggregate'], sort_keys=True), flush=True)
    print(args.output, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
