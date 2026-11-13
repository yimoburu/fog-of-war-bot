#!/usr/bin/env python3
"""Audit arena_eval JSON without running bots; stdout or a fresh --output file.

Exit 0 means intact, complete evidence without recorded game failures. Exit 1
means an audit error or recorded failure; the JSON distinguishes those cases.
Use --self-test for tiny in-memory fixtures, with no match subprocesses.
"""
import sys
sys.dont_write_bytecode = True

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import chess
import referee
from referee import FowBoard, fow_moves, observation, outcome, position_key

NORMAL_REASONS = {'King capturable', 'Threefold repetition', '50-move rule',
                  'No legal moves', 'Move limit reached'}
CLOCK_TOLERANCE = 0.000002  # Frames round each clock to six decimal places.


def digest(data):
    return hashlib.sha256(data).hexdigest()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timing_summary(values):
    ordered = sorted(values)
    if not ordered:
        return {'moves': 0, 'total_seconds': 0, 'mean_seconds': None,
                'p50_seconds': None, 'p95_seconds': None, 'p99_seconds': None,
                'max_seconds': None}
    result = {'moves': len(values), 'total_seconds': sum(values),
              'mean_seconds': sum(values) / len(values), 'max_seconds': ordered[-1]}
    result.update({f'p{p}_seconds': ordered[math.ceil(p * len(ordered) / 100) - 1]
                   for p in (50, 95, 99)})
    return result


def candidate_outcome(result, color):
    if result == '1/2-1/2':
        return 'draw'
    if result not in ('1-0', '0-1') or color not in ('white', 'black'):
        return 'unresolved'
    return 'win' if ('white' if result == '1-0' else 'black') == color else 'loss'


def audit_match(match, configuration, source, index):
    errors, timings, moves = [], [], []
    row = {'input': source, 'index': index, 'id': match.get('id'),
           'opponent': match.get('opponent'), 'color': match.get('color'),
           'recorded_status': match.get('status'), 'errors': errors}
    replay = match.get('replay')
    if not isinstance(replay, dict):
        errors.append('Missing replay; no board/termination audit is possible')
        row.update(valid=False, game_failure=True, outcome='unresolved',
                   reason=match.get('exception_type', 'Missing replay'))
        return row, []
    result, reason = replay.get('result'), replay.get('reason')
    color = match.get('color')
    row.update(result=result, reason=reason, outcome=candidate_outcome(result, color),
               failed_side=replay.get('failedSide'), warnings=replay.get('warnings', []))
    normal = (result in ('1-0', '0-1', '1/2-1/2') and reason in NORMAL_REASONS
              and 'failedSide' not in replay and not replay.get('studentReports')
              and not replay.get('warnings'))
    row['game_failure'] = not normal or match.get('status') != 'completed'
    if color not in ('white', 'black'):
        errors.append('Invalid candidate color')
    if match.get('outcome') != row['outcome']:
        errors.append('Recorded candidate outcome disagrees with result/color')
    expected_status = 'completed' if normal else 'failure'
    if match.get('status') != expected_status:
        errors.append(f'Recorded status disagrees with replay classification: expected {expected_status}')
    control = replay.get('timeControl', {})
    base, increment = control.get('baseSeconds'), control.get('incrementSeconds')
    maximum = configuration.get('max_plies')
    if (not finite(base) or base <= 0 or not finite(increment) or increment < 0
            or not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1):
        errors.append('Invalid replay clock or configuration max_plies')
        row['valid'] = False
        return row, []
    if base != configuration.get('base_time') or increment != configuration.get('increment'):
        errors.append('Replay time control differs from evaluation configuration')
    frames = replay.get('frames')
    if not isinstance(frames, list) or not frames:
        errors.append('Replay must contain the standard initial frame')
        row['valid'] = False
        return row, []
    board = FowBoard()
    repetitions = Counter({position_key(board): 1})
    previous_clocks = None
    for ply, frame in enumerate(frames):
        if not isinstance(frame, dict):
            errors.append(f'ply {ply}: frame must be an object')
            break
        if frame.get('ply') != ply:
            errors.append(f'ply {ply}: incorrect ply field')
        if ply:
            if outcome(board, repetitions) is not None or ply - 1 >= maximum:
                errors.append(f'ply {ply}: move occurs after rule termination or max_plies')
                break
            try:
                move = chess.Move.from_uci(frame.get('move'))
            except (ValueError, TypeError, AttributeError):
                errors.append(f'ply {ply}: malformed move')
                break
            if move not in fow_moves(board):
                errors.append(f'ply {ply}: illegal FOW move {move.uci()}')
                break
            board.push(move)
            moves.append(move.uci())
            repetitions[position_key(board)] += 1
        elif frame.get('move') is not None:
            errors.append('ply 0: initial frame has a move')
        truth = ''.join(board.piece_at(sq).symbol() if board.piece_at(sq) else '-'
                        for sq in chess.SQUARES)
        for field, expected in (('board', truth), ('white', observation(board, True)),
                                ('black', observation(board, False))):
            if frame.get(field) != expected:
                errors.append(f'ply {ply}: {field} differs from official reconstruction')
        clocks = frame.get('clocks')
        if (not isinstance(clocks, list) or len(clocks) != 2
                or not all(finite(c) and c >= 0 for c in clocks)):
            errors.append(f'ply {ply}: invalid clocks')
            previous_clocks = None
            continue
        if not ply:
            if any(abs(c - base) > CLOCK_TOLERANCE for c in clocks):
                errors.append('ply 0: initial clocks differ from base time')
        elif previous_clocks is not None:
            mover = 0 if ply % 2 else 1
            if abs(clocks[1 - mover] - previous_clocks[1 - mover]) > CLOCK_TOLERANCE:
                errors.append(f'ply {ply}: inactive clock changed')
            elapsed = previous_clocks[mover] + increment - clocks[mover]
            if elapsed < -CLOCK_TOLERANCE or elapsed > previous_clocks[mover] + CLOCK_TOLERANCE:
                errors.append(f'ply {ply}: impossible completed-move duration {elapsed}')
            elif color == ('white' if mover == 0 else 'black'):
                timings.append(max(0.0, elapsed))
        previous_clocks = clocks

    terminal = outcome(board, repetitions)
    if terminal is None and len(board.move_stack) >= maximum:
        terminal = ('1/2-1/2', 'Move limit reached')
    failed_side = replay.get('failedSide')
    if failed_side is not None:
        if failed_side not in ('white', 'black') or result != ('0-1' if failed_side == 'white' else '1-0'):
            errors.append('Failure-side/result mismatch')
        row['termination_verification'] = 'Recorded failure; process cause cannot be reconstructed'
    elif reason in NORMAL_REASONS:
        if terminal != (result, reason):
            errors.append(f'Termination/result mismatch: official outcome is {terminal!r}')
        row['termination_verification'] = 'Official board rules and configured max_plies'
    else:
        # Infrastructure/process failures have no reconstructible board cause.
        if result != '*':
            errors.append('Decisive failure result lacks failedSide')
        row['termination_verification'] = 'Unresolved external failure; not verified from board'
    row.update(valid=not errors, plies=len(board.move_stack), frames=len(frames),
               trajectory_sha256=digest(' '.join(moves).encode()),
               candidate_timing=timing_summary(timings))
    return row, timings if not errors else []


def audit_document(document, source, referee_hash):
    errors, rows, timings = [], [], []
    if not isinstance(document, dict):
        return {'input': source, 'errors': ['Document must be an object'], 'matches': []}, []
    configuration = document.get('configuration', {})
    matches = document.get('matches')
    if not isinstance(configuration, dict) or not isinstance(matches, list):
        return {'input': source, 'errors': ['Missing configuration/matches'], 'matches': []}, []
    if document.get('status') not in ('completed', 'completed_with_failures'):
        errors.append('Evaluation document is not completed')
    if document.get('source_unchanged') is not True:
        errors.append('Evaluation does not certify unchanged sources during its run')
    if document.get('planned_matches') != len(matches):
        errors.append('Recorded match count differs from planned_matches')
    hashes = document.get('source_sha256', {})
    recorded = [v for k, v in hashes.items() if Path(k).name == 'referee.py'] if isinstance(hashes, dict) else []
    if not recorded or any(value != referee_hash for value in recorded):
        errors.append('Recorded referee hash is missing or differs from audit referee')
    for index, match in enumerate(matches):
        if not isinstance(match, dict):
            errors.append(f'Match {index} must be an object')
            continue
        try:
            row, samples = audit_match(match, configuration, source, index)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            row, samples = ({'input': source, 'index': index, 'id': match.get('id'),
                             'valid': False, 'game_failure': True, 'outcome': 'unresolved',
                             'reason': 'Malformed replay', 'errors': [f'{type(error).__name__}: {error}']}, [])
        rows.append(row)
        timings.extend(samples)
    return {'input': source, 'recorded_status': document.get('status'),
            'recorded_source_sha256': hashes, 'errors': errors, 'matches': rows,
            'candidate_timing': timing_summary(timings)}, timings


def build_report(paths):
    referee_hash = digest(Path(referee.__file__).read_bytes())
    inputs, all_timings = [], []
    for path in paths:
        source = str(path.resolve())
        raw = None
        try:
            raw = path.read_bytes()
            document = json.loads(raw)
            entry, timings = audit_document(document, source, referee_hash)
            entry['sha256'] = digest(raw)
            inputs.append(entry)
            if not entry['errors']:
                all_timings.extend(timings)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            inputs.append({'input': source, 'errors': [f'{type(error).__name__}: {error}'],
                           'matches': [], **({'sha256': digest(raw)} if raw is not None else {})})
    rows = [row for entry in inputs for row in entry['matches']]
    trusted = [row for entry in inputs if not entry['errors'] for row in entry['matches'] if row['valid']]
    trajectories, groups = defaultdict(list), defaultdict(Counter)
    for row in trusted:
        if row['recorded_status'] == 'completed':
            trajectories[row['trajectory_sha256']].append(
                {k: row[k] for k in ('input', 'id', 'color', 'opponent')})
        key = (str(row.get('opponent')), str(row.get('color')))
        groups[key][row['outcome']] += 1
    audit_errors = sum(len(e['errors']) for e in inputs) + sum(len(r['errors']) for r in rows)
    failures = [r for r in rows if r['game_failure']]
    return {
        'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'integrity_ok': audit_errors == 0, 'ok': audit_errors == 0 and not failures,
        'referee_path': str(Path(referee.__file__).resolve()), 'referee_sha256': referee_hash,
        'validator_sha256': digest(Path(__file__).read_bytes()),
        'summary': {'inputs': len(inputs), 'matches': len(rows), 'audit_errors': audit_errors,
                    'invalid_matches': sum(not r['valid'] for r in rows),
                    'recorded_failures': len(failures),
                    'failure_reasons': dict(Counter(str(r.get('reason')) for r in failures)),
                    'verified_completed_trajectories': sum(map(len, trajectories.values())),
                    'distinct_trajectories': len(trajectories),
                    'candidate_timing': timing_summary(all_timings),
                    'by_opponent_color': [{'opponent': opponent, 'color': color,
                                           **{name: count[name] for name in ('win', 'draw', 'loss', 'unresolved')}}
                                          for (opponent, color), count in sorted(groups.items())]},
        'duplicate_trajectories': [{'sha256': key, 'games': games}
                                   for key, games in trajectories.items() if len(games) > 1],
        'inputs': inputs,
        'limitations': [
            'No bot is executed and no true board is supplied to a policy.',
            'Process failure causes cannot be proved from board frames; they remain recorded failures.',
            'Timing is previous mover clock + increment - current mover clock, with 2us rounding tolerance.',
            'Timing includes recorded completed candidate moves only; failed attempts without a frame are absent.',
            'Timing excludes invalid matches/files; percentiles use nearest rank. It is elapsed response time, not CPU time.',
            'Trajectories are standard-start UCI sequences, ignoring clocks, seeds and player identities; uniqueness is not independence.',
            'Recorded aggregates and Elo estimates are not trusted or reproduced; no arena rating is inferred.'],
    }


def self_test():
    import copy
    import unittest

    class Fixtures(unittest.TestCase):
        def test_small_replay_and_corruption(self):
            board = FowBoard()
            frames = [referee.make_frame(board, [180, 180])]
            for uci, clocks in (('e2e4', [181.9, 180]), ('e7e5', [181.9, 181.8])):
                board.push_uci(uci)
                frames.append(referee.make_frame(board, clocks, uci))
            match = {'id': 'fixture', 'color': 'white', 'opponent': 'fixture',
                     'status': 'completed', 'outcome': 'draw',
                     'replay': {'result': '1/2-1/2', 'reason': 'Move limit reached',
                                'frames': frames, 'timeControl': {'baseSeconds': 180, 'incrementSeconds': 2}}}
            config = {'base_time': 180, 'increment': 2, 'max_plies': 2}
            row, samples = audit_match(match, config, 'memory', 0)
            self.assertTrue(row['valid'], row['errors'])
            self.assertAlmostEqual(samples[0], .1)
            for field, value in (('board', '?' * 64), ('white', '-' * 64), ('black', '-' * 64),
                                 ('move', 'e7e4'), ('clocks', [181.9, 999])):
                broken = copy.deepcopy(match)
                broken['replay']['frames'][-1][field] = value
                self.assertFalse(audit_match(broken, config, 'memory', 0)[0]['valid'], field)
            broken = copy.deepcopy(match)
            broken['replay']['result'] = '1-0'
            self.assertFalse(audit_match(broken, config, 'memory', 0)[0]['valid'])

        def test_terminal_priority_and_recorded_failure(self):
            board, clocks = FowBoard(), [180, 180]
            frames = [referee.make_frame(board, clocks)]
            for ply, uci in enumerate(('f2f3', 'e7e5', 'g2g4', 'd8h4', 'a2a3'), 1):
                board.push_uci(uci)
                clocks[0 if ply % 2 else 1] += 1.9
                frames.append(referee.make_frame(board, clocks, uci))
            match = {'id': 'terminal', 'color': 'white', 'opponent': 'fixture',
                     'status': 'completed', 'outcome': 'loss',
                     'replay': {'result': '0-1', 'reason': 'King capturable',
                                'frames': frames, 'timeControl': {'baseSeconds': 180, 'incrementSeconds': 2}}}
            config = {'base_time': 180, 'increment': 2, 'max_plies': 5}
            self.assertTrue(audit_match(match, config, 'memory', 0)[0]['valid'])
            broken = copy.deepcopy(match)
            broken['replay'].update(result='1/2-1/2', reason='Move limit reached')
            broken['outcome'] = 'draw'
            self.assertFalse(audit_match(broken, config, 'memory', 0)[0]['valid'])
            failure = copy.deepcopy(match)
            failure['status'] = 'failure'
            failure['replay'].update(frames=[frames[0]], failedSide='white',
                                     reason='Bot startup failed: fixture')
            row, samples = audit_match(failure, config, 'memory', 0)
            self.assertTrue(row['valid'], row['errors'])
            self.assertTrue(row['game_failure'])
            self.assertEqual(samples, [])

        def test_fresh_output_and_multiple_inputs(self):
            import contextlib
            import io
            import tempfile
            ref_hash = digest(Path(referee.__file__).read_bytes())
            document = {'status': 'completed', 'source_unchanged': True,
                        'source_sha256': {str(Path(referee.__file__).resolve()): ref_hash,
                                          '/fixture/opponent/referee.py': ref_hash},
                        'configuration': {}, 'matches': [], 'planned_matches': 0}
            with tempfile.TemporaryDirectory() as folder:
                paths = [Path(folder) / name for name in ('one.json', 'two.json')]
                for path in paths:
                    path.write_text(json.dumps(document))
                target = Path(folder) / 'audit.json'
                args = [*map(str, paths), '--output', str(target)]
                self.assertEqual(main(args), 0)
                self.assertEqual(json.loads(target.read_text())['summary']['inputs'], 2)
                before = target.read_bytes()
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    main(args)
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(target.read_bytes(), before)

    return unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Fixtures)).wasSuccessful()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='*', help='One or more arena_eval JSON child files')
    parser.add_argument('--output', type=Path, help='Fresh JSON output path; default stdout')
    parser.add_argument('--self-test', action='store_true', help='Run tiny in-memory corruption fixtures')
    args = parser.parse_args(argv)
    if args.self_test:
        if args.inputs or args.output:
            parser.error('--self-test does not accept inputs or output')
        return int(not self_test())
    if not args.inputs:
        parser.error('Provide at least one evaluation JSON file')
    if len({p.resolve() for p in args.inputs}) != len(args.inputs):
        parser.error('Input paths must be distinct')
    if args.output and (args.output.exists() or args.output.resolve() in {p.resolve() for p in args.inputs}):
        parser.error('Choose a fresh output path; inputs are never overwritten')
    report = build_report(args.inputs)
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(payload)
        except OSError as error:
            parser.error(str(error))
    else:
        sys.stdout.write(payload)
    return int(not report['ok'])


if __name__ == '__main__':
    raise SystemExit(main())
