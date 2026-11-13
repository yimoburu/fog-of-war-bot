#!/usr/bin/env python3
"""Compare current policies on immutable, identical historical FOW prefixes.

This is an off-policy diagnostic, not a game or strength estimate. Each policy
chooses a counterfactual move, then its state records the ORIGINAL corpus move
so all later observations remain on the same historical trajectory. Truth is
owned by this harness and used for diagnostics only after choose_move returns.

Example: .venv/bin/python profile_policy_replay.py --trusted-local
  --gate outputs/experiment-v4-registration.json --risk-policy bank
  --host-activity 'serial local run' --output outputs/policy-replay-v4-bank.json
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import io
import math
from pathlib import Path
import platform
import random
import sys
import time

import chess

import profile_belief_quality as live
from referee import FowBoard, fow_moves, observation, outcome, position_key


ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / 'outputs/belief-quality-v3-smoke-638900.json'
CORPUS_SHA256 = '92a7d9beb3551862095b221ae63215b741e3a7d4b5e60210d01dd84f4a4fb574'
CONFIGURATION = {'search_ms': 600, 'particles': 256, 'worlds': 6,
                 'depth': 3, 'nodes': 50000}
POLICIES = ('bank', 'learned')
PLAN = ROOT / 'analysis/experiment-v4.md'
PLAN_SHA256 = '886e257c588d2ccec82ae357b098d10975369bab5d2818768ef6fb5a4896efcd'
GATE_CRITERIA = {'decisions': 90, 'original_terminal_traps': 2,
                 'maximum_fatal_actions_with_true_safe_alternative': 1,
                 'minimum_original_terminal_traps_avoided': 1,
                 'maximum_complete_response_p95_ms': 650,
                 'maximum_complete_response_max_ms': 1000,
                 'maximum_source_legal_observation_diagnostic_errors': 0}


class ReplayTrace(live.PolicyTrace):
    """Add observable search bounds without changing search or its inputs."""
    def reset(self):
        super().reset()
        self.search_bounds = None

    def search(self, worlds, moves, priors, deadline, **options):
        result = super().search(worlds, moves, priors, deadline, **options)
        returned_at = time.monotonic()
        stats = result[1]
        self.search_bounds = {
            'requested_depth': options.get('max_depth', 3),
            'node_limit': options.get('node_limit', 18000),
            'completed_depth': stats.get('depth', 0),
            'nodes': stats.get('nodes', 0),
            'completed_requested_depth': stats.get('depth', 0) >= options.get('max_depth', 3),
            'node_limit_reached': stats.get('nodes', 0) >= options.get('node_limit', 18000),
            'deadline_reached_on_search_return': returned_at >= deadline,
            'remaining_search_deadline_ms_on_return': 1000 * (deadline - returned_at),
        }
        return result


def bindings(gate):
    paths = {ROOT / name for name in live.source_hashes('v4')}
    paths.update((Path(__file__).resolve(), CORPUS, gate, PLAN))
    return {str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path):
            live.sha(path.read_bytes()) for path in sorted(paths)}


def load_corpus():
    content = CORPUS.read_bytes()
    if live.sha(content) != CORPUS_SHA256:
        raise ValueError('Immutable replay corpus SHA256 changed')
    corpus = json.loads(content)
    if corpus['status'] != 'completed' or corpus['source_unchanged'] is not True:
        raise ValueError('Corpus is not completed, source-bound evidence')
    if len(corpus['games']) != 2:
        raise ValueError('Expected exactly two original games')
    decisions = 0
    for game in corpus['games']:
        if game['status'] != 'completed' or len(game['moves']) != len(game['turns']):
            raise ValueError('Incomplete corpus trajectory')
        side = game['candidate_color'] == 'white'
        truth = FowBoard()
        repetitions = Counter({position_key(truth): 1})
        for ply, (uci, row) in enumerate(zip(game['moves'], game['turns'])):
            if row['ply'] != ply or row['move'] != uci:
                raise ValueError('Corpus row/move alignment changed')
            if row['side'] != ('white' if truth.turn else 'black'):
                raise ValueError('Corpus turn does not match referee')
            if (row['policy'] == 'candidate') != (truth.turn == side):
                raise ValueError('Corpus candidate color is inconsistent')
            if outcome(truth, repetitions) is not None:
                raise ValueError('Corpus continues after a terminal position')
            move = chess.Move.from_uci(uci)
            if move not in fow_moves(truth):
                raise ValueError('Illegal original corpus move')
            if truth.turn == side:
                decisions += 1
                diagnostic = row['diagnostics']
                true_safe = {move.uci() for move in fow_moves(truth)
                             if not live.immediate_loss(truth, move, side)}
                if (diagnostic['observation'] != observation(truth, side)
                        or diagnostic['truth_fen_after_policy_return'] != truth.fen(en_passant='fen')
                        or diagnostic['chosen_move'] != uci
                        or diagnostic['chosen_true_immediate_loss'] != live.immediate_loss(truth, move, side)
                        or set(diagnostic['true_safe_moves']) != true_safe):
                    raise ValueError('Historical diagnostic does not match its referee prefix')
            truth.push(move)
            repetitions[position_key(truth)] += 1
        if truth.fen(en_passant='fen') != game['final_fen']:
            raise ValueError('Corpus final board differs from replay')
    if decisions != 90:
        raise ValueError('Expected exactly 90 historical candidate decisions')
    return corpus


def validate_gate(registration):
    if (registration.get('plan') != str(PLAN.relative_to(ROOT))
            or registration.get('plan_sha256') != PLAN_SHA256
            or live.sha(PLAN.read_bytes()) != PLAN_SHA256
            or registration.get('diagnostic_input_sha256') != CORPUS_SHA256
            or registration.get('model_sha256') != live.sha((ROOT / 'risk_model.json').read_bytes())
            or registration.get('candidate_policies') != list(POLICIES)):
        raise ValueError('Preregistered plan, model or corpus binding changed')
    flags = registration.get('candidate_args_common', [])
    expected = ['--search-ms', '600', '--particles', '256', '--worlds', '6',
                '--depth', '3', '--nodes', '50000']
    if flags != expected:
        raise ValueError('Replay configuration differs from preregistered candidate flags')


def original_baseline(row):
    """Historical observations retain their identity and original timing label."""
    diagnostic = row['diagnostics']
    return {
        'source': 'immutable recorded v3/hypothesis action; not rerun',
        'move': row['move'],
        'chosen_true_immediate_loss': diagnostic['chosen_true_immediate_loss'],
        'any_true_safe_move_exists': bool(diagnostic['true_safe_moves']),
        'chosen_fatal_with_safe_alternative': (diagnostic['chosen_true_immediate_loss']
                                               and bool(diagnostic['true_safe_moves'])),
        'true_safe_alternative_in_actual_shortlist': bool(diagnostic['true_safe_shortlisted_moves']),
        'truth_physical_position_support_count': diagnostic['truth_physical_position_support_count'],
        'truth_full_repetition_history_support_count': diagnostic['truth_full_repetition_history_support_count'],
        'recorded_original_choose_wall_seconds': row['choose_seconds'],
        'recorded_original_search': diagnostic['search'],
    }


def extend_diagnostic(state, trace, truth, chosen, history, diagnostic):
    """All access to true board support is outside policy and after return."""
    side = state.side
    worlds = trace.root['worlds'] if trace.root else []
    complete = bool(trace.root and trace.root['complete'])
    moves = fow_moves(truth)
    fatal = [move.uci() for move in moves if live.immediate_loss(truth, move, side)]
    sampled_risks = {move.uci(): (sum(live.immediate_loss(board, move, side)
                                    for board in worlds) / len(worlds) if worlds else None)
                     for move in moves}
    bank_risks = diagnostic['bank_immediate_loss_fraction_by_move']
    for attacker in diagnostic['chosen_true_attackers']:
        square, piece = chess.parse_square(attacker['square']), chess.Piece.from_symbol(attacker['piece'])
        attacker['search_world_count_at_same_square'] = sum(board.piece_at(square) == piece for board in worlds)
    diagnostic.update(
        any_true_safe_move_exists=bool(diagnostic['true_safe_moves']),
        chosen_fatal_with_safe_alternative=(diagnostic['chosen_true_immediate_loss']
                                           and bool(diagnostic['true_safe_moves'])),
        true_safe_alternative_in_actual_shortlist=bool(diagnostic['true_safe_shortlisted_moves']),
        true_fatal_moves=fatal,
        sampled_worlds_are_complete_hypotheses=complete,
        search_immediate_loss_fraction_by_move=sampled_risks,
        chosen_search_immediate_loss_fraction=sampled_risks[chosen.uci()],
        sampled_search_full_repetition_history_support_count=sum(
            position_key(board) == position_key(truth)
            and getattr(board, 'fow_repetitions', {}) == history for board in worlds),
        true_fatal_moves_detected_by_current_bank=[move for move in fatal if bank_risks[move] is not None
                                                  and bank_risks[move] > 0],
        true_fatal_moves_detected_by_complete_search_sample=[move for move in fatal if complete
                                                            and sampled_risks[move] > 0],
        search_limits_observed=trace.search_bounds,
    )
    diagnostic['immediate_loss_evidence_axes_nonexclusive'] = live.loss_axes(diagnostic)
    return diagnostic


def replay_game(modules, game, game_index, risk_policy, progress):
    policy = modules['bot']
    side = game['candidate_color'] == 'white'
    seed = game['candidate_seed']
    state = policy.State(side, seed=seed, risk_policy=risk_policy, **CONFIGURATION)
    rng, truth = random.Random(seed), FowBoard()
    exact_history = Counter({live.referee_history_key(truth): 1})
    queued = []
    result = {'game_index': game_index, 'candidate_color': game['candidate_color'],
              'seed': seed, 'risk_policy': risk_policy, 'status': 'running', 'decisions': []}
    trace = ReplayTrace(policy, state)
    try:
        for ply, original in enumerate(game['turns']):
            queued.append(observation(truth, side))
            original_move = chess.Move.from_uci(original['move'])
            if truth.turn == side:
                trace.reset()
                observe_started, observe_cpu_started = time.perf_counter(), time.process_time()
                for view in queued:
                    state.observe(view)
                observation_seconds = time.perf_counter() - observe_started
                observation_cpu_seconds = time.process_time() - observe_cpu_started
                if len(queued) != original['observations']:
                    raise RuntimeError('Replay observation cadence differs from corpus')
                queued.clear()
                # Replay the recorded clock, never a counterfactual game clock.
                clock = original['clock_before_seconds']
                if not math.isfinite(clock) or clock <= 0:
                    raise ValueError('Invalid historical clock')
                wall_started, cpu_started = time.perf_counter(), time.process_time()
                chosen = policy.choose_move(state, clock, rng)
                cpu_seconds = time.process_time() - cpu_started
                wall_seconds = time.perf_counter() - wall_started
                if chosen is None:
                    raise RuntimeError('Policy returned no move in a nonterminal prefix')
                # Candidate has returned. Referee truth is inspected only here.
                diagnostic_started = time.perf_counter()
                diagnostic = live.diagnose_after_choice(state, trace, truth, chosen, exact_history)
                extend_diagnostic(state, trace, truth, chosen, exact_history, diagnostic)
                diagnostic_seconds = time.perf_counter() - diagnostic_started
                if not diagnostic['policy_legal_set_matches_referee']:
                    raise RuntimeError('Policy and referee legal sets disagree')
                if diagnostic['current_particles_all_match_observation'] is False:
                    raise RuntimeError('Current bank contains an observation contradiction')
                # Commit the historical own move; NEVER commit chosen here.
                record_started, record_cpu_started = time.perf_counter(), time.process_time()
                visible, legal = state.reconstruct()
                if original_move not in legal:
                    raise RuntimeError('Historical own move is not observation-legal')
                state.record_move(visible, original_move)
                # Match protocol output formatting/flush without printing 90
                # move commands into the diagnostic report's progress stream.
                sink = io.StringIO()
                sink.write('move ' + chosen.uci() + '\n')
                sink.flush()
                record_seconds = time.perf_counter() - record_started
                record_cpu_seconds = time.process_time() - record_cpu_started
                if state.move_history[-1] != original_move:
                    raise RuntimeError('State did not record the historical own move')
                decision = {
                    'prefix_id': f'{game_index}:{ply}', 'ply': ply,
                    'prefix_moves_sha256': live.sha(json.dumps(game['moves'][:ply], separators=(',', ':')).encode()),
                    'risk_policy': risk_policy, 'chosen_move': chosen.uci(),
                    'recorded_move_for_next_prefix': original_move.uci(),
                    'same_action_as_original': chosen == original_move,
                    'original_clock_seconds_supplied': clock,
                    'choose_cpu_seconds': cpu_seconds, 'choose_wall_seconds': wall_seconds,
                    'complete_response_wall_seconds': observation_seconds + wall_seconds + record_seconds,
                    'complete_response_cpu_seconds': observation_cpu_seconds + cpu_seconds + record_cpu_seconds,
                    'record_and_output_seconds': record_seconds,
                    'observation_seconds': observation_seconds,
                    'inference_seconds': trace.inference_seconds,
                    'inference_updates': trace.inference_updates,
                    'search_seconds': trace.search_seconds,
                    'diagnostic_seconds_excluded': diagnostic_seconds,
                    'diagnostics': diagnostic, 'recorded_original_v3': original_baseline(original),
                }
                result['decisions'].append(decision)
                progress(result)
            truth.push(original_move)
            exact_history[live.referee_history_key(truth)] += 1
        result['status'] = 'completed'
    finally:
        trace.close()
    return result


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {'count': 0, 'mean': None, 'p50': None, 'p95': None, 'max': None}
    return {'count': len(ordered), 'mean': sum(ordered) / len(ordered),
            'p50': ordered[(len(ordered) - 1) // 2],
            'p95': ordered[max(0, math.ceil(.95 * len(ordered)) - 1)], 'max': ordered[-1]}


def summarize(rows):
    diagnostics = [row['diagnostics'] for row in rows]
    fatal = [row for row in rows if row['diagnostics']['chosen_true_immediate_loss']]
    with_safe = [item for item in diagnostics if item['true_safe_moves']]
    bank_slots = sum(item['current_particle_count'] for item in diagnostics)
    duplicate_slots = sum(item['duplicate_physical_slots'] for item in diagnostics)
    fatal_moves = sum(len(item['true_fatal_moves']) for item in diagnostics)
    return {
        'decisions': len(rows),
        'chosen_fatal_count': len(fatal),
        'chosen_fatal_with_safe_alternative_count': sum(item['chosen_fatal_with_safe_alternative'] for item in diagnostics),
        'fatal_prefixes': [row['prefix_id'] for row in fatal],
        'avoidable_fatal_prefixes': [row['prefix_id'] for row in fatal
                                    if row['diagnostics']['any_true_safe_move_exists']],
        'prefixes_with_any_true_safe_move': len(with_safe),
        'prefixes_with_true_safe_move_in_actual_shortlist': sum(item['true_safe_alternative_in_actual_shortlist'] for item in with_safe),
        'prefixes_with_current_bank': sum(bool(item['current_particle_count']) for item in diagnostics),
        'prefixes_with_truth_physical_position_in_bank': sum(bool(item['truth_physical_position_support_count']) for item in diagnostics),
        'prefixes_with_full_truth_history_in_bank': sum(bool(item['truth_full_repetition_history_support_count']) for item in diagnostics),
        'prefixes_with_truth_physical_position_in_search': sum(bool(item['sampled_search_truth_support_count']) for item in diagnostics),
        'prefixes_with_full_truth_history_in_search': sum(bool(item['sampled_search_full_repetition_history_support_count']) for item in diagnostics),
        'observed_bank_slots': bank_slots, 'duplicate_physical_bank_slots': duplicate_slots,
        'duplicate_physical_bank_slot_fraction': duplicate_slots / bank_slots if bank_slots else None,
        'true_fatal_legal_moves': fatal_moves,
        'true_fatal_legal_moves_detected_by_current_bank': sum(len(item['true_fatal_moves_detected_by_current_bank']) for item in diagnostics),
        'true_fatal_legal_moves_detected_by_complete_search_sample': sum(len(item['true_fatal_moves_detected_by_complete_search_sample']) for item in diagnostics),
        'search_depth_counts': dict(Counter(str(item['search'].get('depth', 0)) for item in diagnostics)),
        'search_node_limit_reached_count': sum(bool(item['search_limits_observed'] and item['search_limits_observed']['node_limit_reached']) for item in diagnostics),
        'search_deadline_reached_on_return_count': sum(bool(item['search_limits_observed'] and item['search_limits_observed']['deadline_reached_on_search_return']) for item in diagnostics),
        'same_action_as_original_count': sum(row['same_action_as_original'] for row in rows),
        'original_terminal_traps_seen': sum(row['recorded_original_v3']['chosen_fatal_with_safe_alternative'] for row in rows),
        'original_terminal_traps_avoided': sum(row['recorded_original_v3']['chosen_fatal_with_safe_alternative']
                                             and not row['diagnostics']['chosen_true_immediate_loss'] for row in rows),
        'timing_ms': {key: distribution([1000 * row[key] for row in rows]) for key in
                      ('complete_response_cpu_seconds', 'complete_response_wall_seconds',
                       'choose_cpu_seconds', 'choose_wall_seconds', 'inference_seconds',
                       'search_seconds', 'diagnostic_seconds_excluded')},
        'search_nodes': distribution([item['search'].get('nodes', 0) for item in diagnostics]),
    }


def assess_gate(summary, source_unchanged, complete):
    response = summary['timing_ms']['complete_response_wall_seconds']
    checks = {
        'all_90_prefixes_completed': complete and summary['decisions'] == GATE_CRITERIA['decisions'],
        'both_original_terminal_traps_present': summary['original_terminal_traps_seen'] == GATE_CRITERIA['original_terminal_traps'],
        'at_most_one_fatal_action_with_safe_alternative': summary['chosen_fatal_with_safe_alternative_count'] <= 1,
        'at_least_one_original_terminal_trap_avoided': summary['original_terminal_traps_avoided'] >= 1,
        'complete_response_p95_within_650_ms': response['p95'] is not None and response['p95'] <= 650,
        'complete_response_max_within_1000_ms': response['max'] is not None and response['max'] <= 1000,
        'no_source_legal_observation_diagnostic_errors': complete and source_unchanged,
    }
    return {'passed': all(checks.values()), 'checks': checks,
            'criteria': GATE_CRITERIA, 'scope': 'mechanism screen only; no playing-strength claim'}


def baseline_summary(corpus):
    rows = [original_baseline(row) for game in corpus['games'] for row in game['turns']
            if row['policy'] == 'candidate']
    return {
        'source': 'recorded original v3/hypothesis actions, 180 ms; no rerun',
        'decisions': len(rows),
        'chosen_fatal_count': sum(row['chosen_true_immediate_loss'] for row in rows),
        'chosen_fatal_with_safe_alternative_count': sum(row['chosen_fatal_with_safe_alternative'] for row in rows),
        'prefixes_with_truth_physical_position_in_bank': sum(bool(row['truth_physical_position_support_count']) for row in rows),
        'prefixes_with_full_truth_history_in_bank': sum(bool(row['truth_full_repetition_history_support_count']) for row in rows),
        'recorded_original_choose_wall_ms': distribution([1000 * row['recorded_original_choose_wall_seconds'] for row in rows]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-local', action='store_true', required=True)
    parser.add_argument('--gate', type=Path, required=True,
                        help='Predeclared quantitative gate; content/hash are preserved verbatim.')
    parser.add_argument('--risk-policy', choices=POLICIES, required=True)
    parser.add_argument('--host-activity', required=True,
                        help='Declare concurrent host activity affecting timed choices.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    args.gate, args.output = args.gate.resolve(), args.output.resolve()
    if args.output.exists() or args.output.with_name(args.output.name + '.tmp').exists():
        parser.error('Use a fresh output path; existing evidence is never overwritten')
    gate = json.loads(args.gate.read_text())
    if not isinstance(gate, dict):
        parser.error('Gate must be a JSON object declared before measurement')
    validate_gate(gate)
    live.verify_snapshot()
    corpus = load_corpus()
    source = bindings(args.gate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    document = {
        'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'running', 'source_sha256': source, 'source_unchanged': True,
        'corpus_path': str(CORPUS.relative_to(ROOT)), 'corpus_sha256': CORPUS_SHA256,
        'historical_corpus_source_sha256': corpus['source_sha256'],
        'configuration': {**CONFIGURATION, 'policies': [args.risk_policy], 'clock': 'recorded original per-turn clock'},
        'predeclared_gate': gate, 'predeclared_gate_sha256': live.sha(args.gate.read_bytes()),
        'mechanism_gate_criteria': GATE_CRITERIA,
        'environment': {'python': sys.version, 'platform': platform.platform(),
                        'chess': chess.__version__, 'host_activity': args.host_activity,
                        'execution': 'one serial policy panel; two original games in corpus order'},
        'planned_decisions_per_policy': 90, 'games': [], 'summaries': {},
        'recorded_original_v3_summary': baseline_summary(corpus),
        'limitations': [
            'Off-policy fixed-prefix diagnostics; counterfactual choices do not generate later positions or a game result.',
            'Every state records original own moves; observation queues and original clocks are common across policies.',
            'Candidate receives no full board, true repetition history, original future moves or oracle diagnostics.',
            'Banks and shortlists are observed live after choice; physical support and complete history support are distinct.',
            'Incomplete fallback worlds are not credited as complete sampled threat support.',
            'Both policies use current v4 sources; historical v3 actions/timings are labeled separately and not remeasured.',
            'Timed inference/search can diverge with host scheduling; CPU and wall timings are both preserved.',
            'Post-choice oracle diagnostics/checkpoints are excluded from choice timing and never fed back to policy.',
            'Complete response includes observe, choose, reconstruct, original-move commitment and in-memory protocol write/flush; subprocess IPC is not measured.',
            'Particle and modeled probabilities are uncalibrated; exact full-position absence alone is not threat absence.',
            'Source integrity and complete 90-prefix coverage are required before interpreting summaries.',
        ],
    }
    live.checkpoint(args.output, document)
    try:
        for risk_policy in (args.risk_policy,):
            modules = live.load_candidate('v4')
            for index, game in enumerate(corpus['games']):
                def progress(partial):
                    if len(partial['decisions']) % 10 == 0:
                        if bindings(args.gate) != source:
                            raise RuntimeError('Sources changed during replay')
                        document['active_game'] = partial
                        live.checkpoint(args.output, document)
                        print(f"{risk_policy} game {index + 1}/2: {len(partial['decisions'])} decisions", flush=True)
                result = replay_game(modules, game, index, risk_policy, progress)
                document.pop('active_game', None)
                document['games'].append(result)
                rows = [row for entry in document['games'] if entry['risk_policy'] == risk_policy
                        for row in entry['decisions']]
                document['summaries'][risk_policy] = summarize(rows)
                if bindings(args.gate) != source:
                    raise RuntimeError('Sources changed during replay')
                live.checkpoint(args.output, document)
            if document['summaries'][risk_policy]['decisions'] != 90:
                raise RuntimeError('Policy did not cover every original candidate prefix')
        document['status'] = 'completed'
    except (Exception, KeyboardInterrupt) as error:
        document['status'] = 'aborted'
        document['error'] = type(error).__name__ + ': ' + str(error)
        try:
            document['source_unchanged'] = bindings(args.gate) == source
        except OSError:
            document['source_unchanged'] = False
        live.checkpoint(args.output, document)
        print(document['error'], file=sys.stderr, flush=True)
        return 1
    document['source_unchanged'] = bindings(args.gate) == source
    if not document['source_unchanged']:
        document['status'] = 'aborted_source_changed'
    document['gate_assessment'] = assess_gate(document['summaries'][args.risk_policy],
                                             document['source_unchanged'],
                                             document['status'] == 'completed')
    live.checkpoint(args.output, document)
    print(json.dumps(document['summaries'], sort_keys=True), flush=True)
    print(args.output, flush=True)
    return 0 if document['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
