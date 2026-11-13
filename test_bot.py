"""Authored behavioral tests; execution requires a separately authorized host."""
import collections
import io
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import chess
import package_submission
from bot import (MEMORY_PLIES, State, after_move, choose_move, hidden_risk,
                 serve, visibly_safe)
from evaluate import NORMAL_REASONS, schedule, summarize
from referee import FowBoard, fow_moves, observation, outcome, position_key, run_match

ROOT = Path(__file__).resolve().parent


def ucis(moves):
    return {move.uci() for move in moves}


def state_for(board, side=None):
    side = board.turn if side is None else side
    # These oracle tests exercise root legality/safety; search has its own suite.
    state = State(side, int(board.clean_castling_rights()), search_ms=0)
    state.observe(observation(board, side))
    return state


def pick(state, seed=7):
    return choose_move(state, 180, random.Random(seed), now=lambda: 0)


class StrategyTests(unittest.TestCase):
    def test_known_attack_and_moving_blocker_bait(self):
        for fen, bait in (
            ('R3r2k/8/8/8/8/8/8/4K3 w - - 0 1', None),
            ('R3r2k/8/8/8/5q2/8/4N3/4K3 w - - 0 1', 'e2f4'),
        ):
            truth = FowBoard(fen)
            state = state_for(truth)
            self.assertEqual(state.tracker.current[chess.E8], 'r')
            board, moves = state.reconstruct()
            if bait:
                bad = chess.Move.from_uci(bait)
                self.assertIn(bad, moves)
                self.assertFalse(visibly_safe(after_move(board, bad), True))
            chosen = pick(state)
            self.assertIn(chosen, fow_moves(truth))
            self.assertTrue(visibly_safe(after_move(board, chosen), True))
            self.assertTrue(visibly_safe(after_move(truth, chosen), True))
            # Expired optional work must still honor the complete safety pass.
            ticks = iter((0, 1))
            fallback = choose_move(state, 180, random.Random(7), now=lambda: next(ticks))
            self.assertTrue(visibly_safe(after_move(board, fallback), True))

    def test_horizontal_ep_removes_both_blockers(self):
        truth = FowBoard('7k/5p2/8/r5PK/8/8/8/R7 b - - 0 1')
        state = state_for(truth, True)
        truth.push_uci('f7f5')
        state.observe(observation(truth, True))
        board, moves = state.reconstruct()
        self.assertEqual(state.tracker.current[chess.A5], 'r')
        self.assertEqual(ucis(moves), ucis(fow_moves(truth)))
        ep = chess.Move.from_uci('g5f6')
        self.assertIn(ep, moves)
        after = after_move(board, ep)
        self.assertIsNone(after.piece_at(chess.F5))
        self.assertIsNone(after.piece_at(chess.G5))
        self.assertFalse(visibly_safe(after, True))
        self.assertTrue(visibly_safe(after_move(board, pick(state)), True))
        state.record_move(board, ep)
        self.assertNotIn(chess.F5, state.sightings)

    def test_castling_crossing_vs_finishing_attack(self):
        for fen, safe in (
            ('k4r2/8/8/8/8/B7/8/4K2R w K - 0 1', True),
            ('k5r1/8/8/8/8/1B6/8/4K2R w K - 0 1', False),
        ):
            truth = FowBoard(fen)
            state = state_for(truth)
            board, moves = state.reconstruct()
            move = chess.Move.from_uci('e1g1')
            self.assertIn(move, moves)
            after = after_move(board, move)
            self.assertEqual(after.piece_at(chess.F1), chess.Piece(chess.ROOK, True))
            self.assertEqual(visibly_safe(after, True), safe)
            full = after_move(truth, move)
            result = outcome(full, collections.Counter({position_key(full): 1}))
            if safe:
                self.assertIsNone(result)
            else:
                self.assertEqual(result, ('0-1', 'King capturable'))
                self.assertNotEqual(pick(state), move)

    def test_hidden_blocker_and_promotions_match_oracle(self):
        truth = FowBoard('k7/8/8/8/8/4r3/4P3/7K w - - 0 1')
        state = state_for(truth)
        self.assertEqual(state.tracker.current[chess.E3], '?')
        self.assertEqual(ucis(state.reconstruct()[1]), ucis(fow_moves(truth)))
        self.assertNotIn('e2e3', ucis(state.reconstruct()[1]))
        self.assertNotIn('e2e4', ucis(state.reconstruct()[1]))
        truth = FowBoard('k6r/6P1/8/8/8/8/8/7K w - - 0 1')
        state = state_for(truth)
        board, moves = state.reconstruct()
        self.assertEqual(ucis(moves), ucis(fow_moves(truth)))
        for prefix in ('g7g8', 'g7h8'):
            for suffix in 'qrbn':
                move = chess.Move.from_uci(prefix + suffix)
                self.assertIn(move, moves)
                self.assertEqual(after_move(board, move).piece_at(move.to_square).piece_type,
                                 move.promotion)

    def test_attacking_enemy_king_is_not_terminal(self):
        truth = FowBoard('k7/8/8/8/8/8/1R6/7K w - - 0 1')
        after = after_move(truth, chess.Move.from_uci('b2a2'))
        self.assertTrue(after.is_attacked_by(True, chess.A8))
        self.assertIsNone(outcome(after, collections.Counter({position_key(after): 1})))

    def test_seed_clock_and_all_unsafe_fallback(self):
        state = state_for(FowBoard())
        self.assertEqual(pick(state), pick(state))
        for remaining in (0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                choose_move(state, remaining, random.Random(1))
        self.assertIn(choose_move(state, 0.000001, random.Random(1), now=lambda: 0),
                      state.reconstruct()[1])
        # Actual board where every king destination is attacked by visible rooks.
        truth = FowBoard('7k/8/8/8/8/8/rr6/Kr6 w - - 0 1')
        state = state_for(truth)
        board, moves = state.reconstruct()
        self.assertTrue(moves)
        self.assertTrue(all(not visibly_safe(after_move(board, m), True) for m in moves))
        self.assertIn(pick(state), moves)

    def test_reached_games_candidate_and_safety_invariants(self):
        rng = random.Random(912)
        checked = 0
        for _ in range(20):
            truth = FowBoard()
            states = {side: State(side, search_ms=0) for side in (True, False)}
            repetitions = collections.Counter({position_key(truth): 1})
            for _ in range(100):
                if outcome(truth, repetitions):
                    break
                for side, state in states.items():
                    state.observe(observation(truth, side))
                state = states[truth.turn]
                board, moves = state.reconstruct()
                self.assertEqual(ucis(moves), ucis(fow_moves(truth)))
                chosen = pick(state, checked)
                self.assertIn(chosen, fow_moves(truth))
                if any(visibly_safe(after_move(board, m), truth.turn) for m in moves):
                    self.assertTrue(visibly_safe(after_move(board, chosen), truth.turn))
                actual = chosen if checked % 2 == 0 else rng.choice(moves)
                state.record_move(board, actual)
                truth.push(actual)
                repetitions[position_key(truth)] += 1
                checked += 1
        self.assertGreater(checked, 100)


class HistoryTests(unittest.TestCase):
    def test_real_sighting_contradiction(self):
        truth = FowBoard('r6k/8/8/8/8/8/8/R6K w - - 0 1')
        state = state_for(truth)
        self.assertIn(chess.A8, state.sightings)
        for uci in ('a1b1', 'a8a7', 'b1b8'):
            move = chess.Move.from_uci(uci)
            if truth.turn:
                state.record_move(state.reconstruct()[0], move)
            truth.push(move)
            state.observe(observation(truth, True))
        self.assertEqual(state.tracker.current[chess.A8], '-')
        self.assertNotIn(chess.A8, state.sightings)
        self.assertIsNone(state.reconstruct()[0].piece_at(chess.A7))

    def test_sighting_decay_expiry_and_invalid_observation(self):
        cells = ['?'] * 64
        cells[chess.E1] = 'K'
        cells[chess.E8] = 'r'
        state = State(True, 0)
        state.observe(''.join(cells))
        cells[chess.E8] = '?'
        state.observe(''.join(cells))
        board, _ = state.reconstruct()
        first = hidden_risk(state, board, chess.A1)
        self.assertIsNone(board.piece_at(chess.E8))
        for _ in range(4):
            state.observe(''.join(cells))
        self.assertLess(hidden_risk(state, board, chess.A1), first)
        for _ in range(MEMORY_PLIES):
            state.observe(''.join(cells))
        self.assertNotIn(chess.E8, state.sightings)
        self.assertLessEqual(len(state.sightings), 64)
        before = (state.ply, dict(state.sightings), state.tracker.current,
                  state.tracker.previous, state.tracker.rights)
        for view in ('?', 'x' * 64, '-' * 65):
            with self.assertRaises(ValueError):
                state.observe(view)
            self.assertEqual(before, (state.ply, dict(state.sightings), state.tracker.current,
                                      state.tracker.previous, state.tracker.rights))
        with self.assertRaises(ValueError):
            State(True).reconstruct()

    def test_same_current_view_different_ep_history_and_protocol(self):
        views = []
        for fen, uci, ep_expected in (
            ('k7/3p4/8/4PN2/5N2/8/8/7K b - - 0 1', 'd7d5', True),
            ('k7/8/3p4/4PN2/5N2/8/8/7K b - - 0 1', 'd6d5', False),
        ):
            truth = FowBoard(fen)
            previous = observation(truth, True)
            truth.push_uci(uci)
            current = observation(truth, True)
            views.append(current)
            state = State(True, 0)
            state.observe(previous)
            state.observe(current)
            self.assertEqual('e5d6' in ucis(state.reconstruct()[1]), ep_expected)
            self.assertEqual(ucis(state.reconstruct()[1]), ucis(fow_moves(truth)))
            prefix = 'fow\nw 180 2\n' + previous + '\n' + current + '\n'
            output = io.StringIO()
            serve(io.StringIO(prefix), output, 1)
            self.assertEqual(output.getvalue(), 'ready\n')
            output = io.StringIO()
            serve(io.StringIO(prefix + '180\n'), output, 1)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], 'ready')
            self.assertIn(lines[1].removeprefix('move '), ucis(fow_moves(truth)))
        self.assertEqual(*views)


class ProtocolTests(unittest.TestCase):
    def launch(self, transcript):
        return subprocess.run([sys.executable, '-u', str(ROOT / 'bot.py'), '--seed', '123'],
                              input=transcript, text=True, capture_output=True, timeout=15)

    def test_both_colors_and_seeded_inprocess_output(self):
        for side in (True, False):
            truth = FowBoard()
            views = [observation(truth, side)]
            if not side:
                truth.push_uci('e2e4')
                views.append(observation(truth, side))
            prefix = 'fow\n' + ('w' if side else 'b') + ' 180 2\n'
            prefix += '\n'.join(views) + '\n'
            quiet = self.launch(prefix)
            self.assertEqual(quiet.returncode, 0, quiet.stderr)
            self.assertEqual(quiet.stdout, 'ready\n')
            run = self.launch(prefix + '180\n')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stderr, '')
            lines = run.stdout.splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], 'ready')
            self.assertIn(lines[1].removeprefix('move '), ucis(fow_moves(truth)))
            outputs = []
            with patch('bot.choose_move', side_effect=lambda s, r, g:
                       choose_move(s, r, g, now=lambda: 0)):
                for _ in range(2):
                    output = io.StringIO()
                    serve(io.StringIO(prefix + '180\n'), output, 123)
                    outputs.append(output.getvalue())
            self.assertEqual(*outputs)

    def test_negative_protocol_cases(self):
        prefix = 'fow\nw 180 2\n' + observation(FowBoard(), True) + '\n'
        for clock in ('nan', 'inf', '-1', '0', 'debug', ''):
            run = self.launch(prefix + clock + '\n')
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, 'ready\n')
            self.assertIn('bot:', run.stderr)
        run = self.launch(prefix + '180\n179\n')
        self.assertEqual(run.returncode, 2)
        self.assertEqual(len(run.stdout.splitlines()), 2)
        run = self.launch('fow\nw 180 2\n180\n')
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, 'ready\n')
        for header in ('bad\nw 180 2\n', 'fow\nx 180 2\n', 'fow\nw 180 nan\n'):
            run = self.launch(header)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, '')

    def test_imports_have_no_output(self):
        run = subprocess.run([sys.executable, '-c', 'import bot, evaluate, package_submission'],
                             cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout + run.stderr, '')

    def test_complete_matches_both_colors_both_opponents(self):
        ours = [sys.executable, '-u', str(ROOT / 'bot.py'), '--seed', '452']
        for opponent in ('baseline.py', 'uniform_random.py'):
            theirs = [sys.executable, '-u', str(ROOT / opponent), '--seed', '453']
            for commands in ((ours, theirs), (theirs, ours)):
                replay = run_match(*commands, base=180, increment=2, max_plies=1000)
                self.assertIn(replay['result'], ('1-0', '0-1', '1/2-1/2'))
                self.assertIn(replay['reason'], NORMAL_REASONS)
                self.assertNotIn('failedSide', replay, replay.get('studentReports'))
                self.assertFalse(replay.get('studentReports'))


class EvidenceTests(unittest.TestCase):
    def test_schedule_and_denominators(self):
        rows = list(schedule(3, 10))
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows, list(schedule(3, 10)))
        for first, second in zip(rows[::2], rows[1::2]):
            self.assertEqual(first[:2], second[:2])
            self.assertEqual((first[2], second[2]), ('white', 'black'))
            self.assertEqual(first[3:], second[3:])
        matches = [dict(opponent='baseline.py', outcome=result, status=status)
                   for result, status in (('win', 'completed'), ('loss', 'failure'),
                                          ('draw', 'completed'), ('unresolved', 'exception'))]
        report = summarize(matches)['baseline.py']
        self.assertEqual(report['attempted'], 4)
        self.assertEqual(report['failures'], 2)
        self.assertEqual(report['raw']['denominator'], 3)
        self.assertEqual(report['clean']['denominator'], 2)
        self.assertEqual(report['raw']['score_fraction'], 0.5)
        self.assertEqual(report['raw']['unresolved'], 1)

    def test_archive_contents_reproducibility_and_rejections(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in package_submission.RUNTIME_FILES:
                (root / name).write_bytes((ROOT / name).read_bytes())
            (root / 'unrelated.txt').write_text('must not be packaged')
            with patch.object(package_submission, 'ROOT', root):
                first, second = root / 'first.zip', root / 'second.zip'
                self.assertEqual(package_submission.build(first), package_submission.build(second))
                self.assertEqual(first.read_bytes(), second.read_bytes())
                with zipfile.ZipFile(first) as archive:
                    self.assertEqual(archive.namelist(), list(package_submission.RUNTIME_FILES))
                    for name in archive.namelist():
                        self.assertEqual(archive.read(name), (root / name).read_bytes())
                with self.assertRaises(FileExistsError):
                    package_submission.build(first)
                source = root / 'requirements.txt'
                original = source.read_bytes()
                source.write_bytes(original + b'\n')
                with self.assertRaises(ValueError):
                    package_submission.build(root / 'changed.zip')
                source.write_bytes(original)
                bot = root / 'bot.py'
                bot.rename(root / 'real-bot.py')
                bot.symlink_to(root / 'real-bot.py')
                with self.assertRaises(OSError):
                    package_submission.build(root / 'symlink.zip')


if __name__ == '__main__':
    unittest.main()
