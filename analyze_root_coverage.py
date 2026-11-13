#!/usr/bin/env python3
"""Offline root-filter coverage audit; never run a bot or give truth to policy.

Requires current root-policy source hashes to match each arena_eval input.
Reconstructs the observation tracker and seeded root tie-break draws, not the
belief bank or search. --output must be fresh; otherwise JSON goes to stdout.
"""
import sys
sys.dont_write_bytecode = True

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace

import chess
import bot
from referee import FowBoard, fow_moves, observation, outcome, position_key
from uniform_random import ObservationTracker, candidates

ROOT = Path(__file__).resolve().parent
SOURCES = ('bot.py', 'belief.py', 'search.py', 'referee.py', 'uniform_random.py')
FIELDS = (
    'candidate_turns', 'legal_moves', 'truth_safe_moves', 'visible_safe_moves',
    'turns_hard_filter_active', 'hard_filter_excluded_moves',
    'hard_filter_excluded_truth_safe_moves', 'turns_with_truth_safe_hard_exclusions',
    'turns_all_truth_safe_hard_excluded', 'turns_without_truth_safe_moves',
    'turns_pool_over_64', 'cutoff64_excluded_moves', 'cutoff64_excluded_truth_safe_moves',
    'turns_with_truth_safe_cutoff64_exclusions', 'turns_all_truth_safe_absent_after64',
    'terminal_loss_decisions', 'terminal_losses_with_truth_safe_alternatives',
    'terminal_losses_with_any_truth_safe_hard_exclusion',
    'terminal_losses_with_all_truth_safe_hard_excluded',
    'terminal_losses_with_any_truth_safe_cutoff64_exclusion',
    'terminal_losses_with_all_truth_safe_absent_after64',
    'terminal_losses_with_hidden_slider_blocker_exclusion',
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def root_coverage(truth, tracker, rng):
    """Policy computations receive only the reconstructed observation board."""
    side = tracker.side
    partial, legal = candidates(tracker.current, side, tracker.rights, tracker.previous)
    if set(legal) != set(fow_moves(truth)):
        raise ValueError('Observation-generated legal moves differ from official true-board moves')
    state = SimpleNamespace(side=side, tracker=tracker)
    ranked, actual_safe, explanations = [], set(), {}
    for move in sorted(legal, key=lambda item: item.uci()):
        after = bot.after_move(partial, move)
        visible_safe = bot.visibly_safe(after, side)
        quick = (bot.quick_score(partial, after, move, side) -
                 bot.blocker_risk(state, after, bot.capture_square(partial, move)))
        ranked.append((visible_safe, quick, rng.random(), move))
        # Evaluator-only truth starts here. It never enters the policy scoring.
        actual_after = truth.copy(stack=False)
        actual_after.push(move)
        if not bot.visibly_safe(actual_after, side):
            continue
        actual_safe.add(move)
        if visible_safe:
            continue
        king = after.king(side)
        reasons = []
        for attacker in after.attackers(not side, king):
            piece = after.piece_at(attacker)
            blockers = chess.between(attacker, king) & actual_after.occupied & ~after.occupied
            if piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN) or not blockers:
                raise ValueError('False visible attack is not explained by a hidden slider blocker')
            reasons.append({'visible_attacker': chess.square_name(attacker),
                            'attacker_type': piece.symbol(),
                            'hidden_blockers_evaluator_only': [
                                {'square': chess.square_name(s),
                                 'piece': actual_after.piece_at(s).symbol()}
                                for s in chess.SquareSet(blockers)]})
        explanations[move.uci()] = reasons
    safe_exists = any(row[0] for row in ranked)
    pool = [row for row in ranked if row[0] == safe_exists]
    pool.sort(key=lambda row: (row[1], row[2]), reverse=True)
    kept = {row[3] for row in pool}
    detailed = {row[3] for row in pool[:bot.MAX_DETAIL_MOVES]}
    excluded_hard = actual_safe - kept
    excluded64 = actual_safe & (kept - detailed)
    counts = Counter(candidate_turns=1, legal_moves=len(legal),
                     truth_safe_moves=len(actual_safe),
                     visible_safe_moves=sum(row[0] for row in ranked),
                     turns_hard_filter_active=int(safe_exists),
                     hard_filter_excluded_moves=len(legal) - len(kept),
                     hard_filter_excluded_truth_safe_moves=len(excluded_hard),
                     turns_with_truth_safe_hard_exclusions=int(bool(excluded_hard)),
                     turns_all_truth_safe_hard_excluded=int(bool(actual_safe) and not (actual_safe & kept)),
                     turns_without_truth_safe_moves=int(not actual_safe),
                     turns_pool_over_64=int(len(pool) > bot.MAX_DETAIL_MOVES),
                     cutoff64_excluded_moves=len(pool) - len(detailed),
                     cutoff64_excluded_truth_safe_moves=len(excluded64),
                     turns_with_truth_safe_cutoff64_exclusions=int(bool(excluded64)),
                     turns_all_truth_safe_absent_after64=int(bool(actual_safe) and not (actual_safe & detailed)))
    details = {
        'legal_count': len(legal), 'hard_filter_pool_count': len(pool),
        'truth_safe_moves_evaluator_only': sorted(m.uci() for m in actual_safe),
        'hard_filter_kept_moves': sorted(m.uci() for m in kept),
        'hard_filter_excluded_truth_safe_moves_evaluator_only': sorted(m.uci() for m in excluded_hard),
        'cutoff64_excluded_truth_safe_moves_evaluator_only': sorted(m.uci() for m in excluded64),
        'hidden_slider_blocker_explanations_evaluator_only':
            {m.uci(): explanations[m.uci()] for m in sorted(excluded_hard, key=lambda x: x.uci())},
    }
    return counts, details, actual_safe, kept, detailed


def analyze_match(match, maximum, source):
    if match.get('status') != 'completed':
        raise ValueError('Analysis requires completed, failure-free matches')
    side = {'white': True, 'black': False}[match['color']]
    tracker, rng = ObservationTracker(side), random.Random(match['bot_seed'])
    board, counts, events = FowBoard(), Counter(), []
    repetitions = Counter({position_key(board): 1})
    replay, frames = match['replay'], match['replay']['frames']
    maxima = {'legal_moves': 0, 'hard_filter_pool': 0}
    for ply, frame in enumerate(frames):
        if ply:
            if outcome(board, repetitions) or ply - 1 >= maximum:
                raise ValueError(f'ply {ply}: replay continued after termination')
            move = chess.Move.from_uci(frame['move'])
            if move not in fow_moves(board):
                raise ValueError(f'ply {ply}: illegal recorded move')
            board.push(move)
            repetitions[position_key(board)] += 1
        expected_truth = ''.join(board.piece_at(s).symbol() if board.piece_at(s) else '-'
                                 for s in chess.SQUARES)
        if (frame['ply'] != ply or frame['board'] != expected_truth
                or frame['white'] != observation(board, True)
                or frame['black'] != observation(board, False)):
            raise ValueError(f'ply {ply}: frame differs from official reconstruction')
        if ply == len(frames) - 1:
            break  # Terminal frames are not delivered to either bot.
        tracker.observe(frame[match['color']])
        if board.turn != side:
            continue
        turn, detail, actual_safe, kept, detailed = root_coverage(board, tracker, rng)
        chosen = chess.Move.from_uci(frames[ply + 1]['move'])
        if chosen not in kept:
            raise ValueError(f'ply {ply}: recorded move contradicts reconstructed hard-filter pool')
        terminal_loss = chosen not in actual_safe
        if terminal_loss and (ply != len(frames) - 2 or match['outcome'] != 'loss'
                              or replay['reason'] != 'King capturable'):
            raise ValueError('An immediate candidate king loss must terminate the recorded game')
        if terminal_loss:
            turn.update(terminal_loss_decisions=1,
                        terminal_losses_with_truth_safe_alternatives=int(bool(actual_safe)),
                        terminal_losses_with_any_truth_safe_hard_exclusion=int(bool(actual_safe - kept)),
                        terminal_losses_with_all_truth_safe_hard_excluded=int(bool(actual_safe) and not actual_safe & kept),
                        terminal_losses_with_any_truth_safe_cutoff64_exclusion=int(bool(actual_safe & (kept - detailed))),
                        terminal_losses_with_all_truth_safe_absent_after64=int(bool(actual_safe) and not actual_safe & detailed),
                        terminal_losses_with_hidden_slider_blocker_exclusion=int(bool(actual_safe - kept)))
        counts.update(turn)
        maxima['legal_moves'] = max(maxima['legal_moves'], detail['legal_count'])
        maxima['hard_filter_pool'] = max(maxima['hard_filter_pool'], detail['hard_filter_pool_count'])
        if terminal_loss or actual_safe - kept or actual_safe & (kept - detailed):
            events.append({'ply_before_move': ply, 'chosen_move': chosen.uci(),
                           'terminal_candidate_loss': terminal_loss,
                           'truth_fen_evaluator_only': board.fen(),
                           'candidate_observation': tracker.current, **detail})
        tracker.record_move(chosen)
    terminal = outcome(board, repetitions)
    if terminal is None and len(board.move_stack) >= maximum:
        terminal = ('1/2-1/2', 'Move limit reached')
    if terminal != (replay['result'], replay['reason']):
        raise ValueError('Recorded result differs from reconstructed terminal outcome')
    return {'input': source, 'id': match['id'], 'color': match['color'],
            'opponent': match['opponent'], 'bot_seed': match['bot_seed'],
            'opponent_seed': match['opponent_seed'], 'outcome': match['outcome'],
            'counts': {field: counts[field] for field in FIELDS}, 'maxima': maxima,
            'events': events}


def analyze(paths):
    source_hashes = {name: sha((ROOT / name).read_bytes()) for name in SOURCES}
    inputs, matches, errors, total = [], [], [], Counter()
    for path in paths:
        raw = path.read_bytes()
        document = json.loads(raw)
        source = str(path.resolve())
        inputs.append({'path': source, 'sha256': sha(raw), 'status': document.get('status')})
        if (document.get('status') != 'completed' or document.get('source_unchanged') is not True
                or len(document['matches']) != document['planned_matches']):
            raise ValueError(f'{path}: requires complete evidence with unchanged sources')
        for name, expected in source_hashes.items():
            if document['source_sha256'].get(str(ROOT / name)) != expected:
                raise ValueError(f'{path}: current {name} differs from evaluated source')
        for match in document['matches']:
            try:
                row = analyze_match(match, document['configuration']['max_plies'], source)
                matches.append(row)
                total.update(row['counts'])
            except (ValueError, KeyError, TypeError) as error:
                errors.append({'input': source, 'id': match.get('id'), 'error': str(error)})
    by_color = {}
    for color in ('white', 'black'):
        group = [row for row in matches if row['color'] == color]
        summed = sum((Counter(row['counts']) for row in group), Counter())
        by_color[color] = {'games': len(group), 'outcomes': dict(Counter(r['outcome'] for r in group)),
                           'counts': {field: summed[field] for field in FIELDS}}
    if source_hashes != {name: sha((ROOT / name).read_bytes()) for name in SOURCES}:
        errors.append({'error': 'Policy sources changed during analysis'})
    return {'schema_version': 1, 'ok': not errors, 'inputs': inputs,
            'policy_source_sha256': source_hashes, 'analyzer_sha256': sha(Path(__file__).read_bytes()),
            'summary': {'games': len(matches), 'outcomes': dict(Counter(r['outcome'] for r in matches)),
                        'counts': {field: total[field] for field in FIELDS},
                        'maxima': {key: max((r['maxima'][key] for r in matches), default=0)
                                   for key in ('legal_moves', 'hard_filter_pool')},
                        'by_color': by_color},
            'errors': errors, 'matches': matches,
            'limitations': [
                'True boards and true safety are evaluator-only; policy scores receive only observation-derived state.',
                'No bot, search or belief update is executed. Root ties follow the recorded bot seed and one draw per sorted legal move.',
                'The 64-move analysis measures structural eligibility for detail scoring; optional deadlines may return earlier.',
                'The later 12-move search shortlist depends on unlogged particles/deadlines and is not reconstructed.',
                'An excluded safe move proves next-ply survival on this replay truth, not eventual victory or a policy-observable certainty.',
                'All analyzed games become development evidence; any change needs a fresh holdout.']}


def self_test():
    board = FowBoard('R3r2k/8/8/8/4p3/8/8/4K3 w - - 0 1')
    tracker = ObservationTracker(True, 0)
    tracker.observe(observation(board, True))
    counts, details, safe, kept, _ = root_coverage(board, tracker, random.Random(1))
    move = chess.Move.from_uci('a8b8')
    assert move in safe and move not in kept
    assert counts['turns_with_truth_safe_hard_exclusions'] == 1
    reasons = details['hidden_slider_blocker_explanations_evaluator_only'][move.uci()]
    assert reasons[0]['hidden_blockers_evaluator_only'] == [{'square': 'e4', 'piece': 'p'}]
    assert counts['turns_pool_over_64'] == 0
    print('Hidden-slider-blocker fixture passed', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='*')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(argv)
    if args.self_test:
        if args.inputs or args.output:
            parser.error('--self-test takes no inputs/output')
        self_test()
        return 0
    if not args.inputs or len({p.resolve() for p in args.inputs}) != len(args.inputs):
        parser.error('Provide distinct arena_eval child JSON files')
    if args.output and (args.output.exists() or args.output.resolve() in {p.resolve() for p in args.inputs}):
        parser.error('Choose a fresh output path')
    try:
        report = analyze(args.inputs)
        payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(payload)
        else:
            sys.stdout.write(payload)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    return int(not report['ok'])


if __name__ == '__main__':
    raise SystemExit(main())
