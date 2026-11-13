"""Rule-level tests for the optional hypothetical fog-pressure prior."""
from collections import Counter
import itertools
import unittest

import chess

from belief import repetition_key
from fog_pressure import MAX_SCORE, PressureExpired, analyze_world, score
from referee import FowBoard, observation, outcome, position_key


def analyze(board, move):
    return analyze_world(board, chess.Move.from_uci(move), board.turn, 10, lambda: 0)


class FogPressureTests(unittest.TestCase):
    def test_exact_public_bishop_bait_and_immediate_terminal_semantics(self):
        # Official completed replay nnjy0an4, immediately before ply27.
        # https://6s890.lids.mit.edu/api/games/nnjy0an4
        board = FowBoard('r2q1rk1/1ppbb1pp/2n1p3/3pP3/p2P1N1n/2PB1N2/PPQ3PP/R1B2RK1 w - - 2 14')
        detail = analyze(board, 'd3h7')
        self.assertEqual(detail['status'], 'hidden_reply_pressure')
        self.assertIn('g8h7', detail['hidden_fatal_replies'])
        self.assertEqual(detail['hidden_attackers']['g8h7'], ['c2'])
        self.assertNotIn('g8h8', detail['hidden_fatal_replies'])
        self.assertNotIn('g8f7', detail['hidden_fatal_replies'])
        self.assertGreater(detail['score'], 0)
        board.push_uci('d3h7')
        self.assertEqual(observation(board, False)[chess.C2], '?')
        board.push_uci('g8h7')
        self.assertEqual(outcome(board, Counter({position_key(board): 1})),
                         ('1-0', 'King capturable'))

    def test_known_attacker_and_no_hidden_bait_score_zero(self):
        hidden = FowBoard('6k1/8/8/8/8/3B4/2Q5/K7 w - - 0 1')
        self.assertGreater(analyze(hidden, 'd3h7')['score'], 0)
        known = hidden.copy(stack=False)
        known.set_piece_at(chess.C8, chess.Piece(chess.ROOK, False))
        self.assertEqual(analyze(known, 'd3h7')['score'], 0)
        self.assertEqual(analyze(FowBoard(), 'e2e4')['score'], 0)

    def test_en_passant_reply_removes_both_blockers_and_exposes_hidden_rook(self):
        board = FowBoard('8/8/8/8/R4p1k/8/6P1/K7 w - - 0 1')
        detail = analyze(board, 'g2g4')
        self.assertNotEqual(detail['status'], 'observation_legal_mismatch')
        self.assertIn('f4g3', detail['hidden_fatal_replies'])
        self.assertEqual(detail['hidden_attackers']['f4g3'], ['a4'])
        self.assertNotIn('f4f3', detail['hidden_fatal_replies'])
        board.push_uci('g2g4')
        board.push_uci('f4g3')
        self.assertIsNone(board.piece_at(chess.G4))
        self.assertEqual(outcome(board, Counter({position_key(board): 1}))[0], '1-0')

    def test_castling_crossing_attack_allowed_but_attacked_destination_loses(self):
        crossing = FowBoard('4k2r/8/8/8/8/8/P7/1K3R2 w k - 0 1')
        crossed = analyze(crossing, 'a2a3')
        self.assertIn('e8g8', crossed['legal_replies'])
        self.assertNotIn('e8g8', crossed['hidden_fatal_replies'])
        attacked = FowBoard('4k2r/8/8/8/8/8/P7/1K4R1 w k - 0 1')
        detail = analyze(attacked, 'a2a3')
        self.assertIn('e8g8', detail['hidden_fatal_replies'])
        self.assertEqual(detail['hidden_attackers']['e8g8'], ['g1'])

    def test_root_en_passant_castling_and_promotion_use_ordinary_board_updates(self):
        cases = [('7k/8/8/3pP3/8/8/8/K7 w - d6 0 1', 'e5d6'),
                 ('k7/8/8/8/8/8/8/4K2R w K - 0 1', 'e1g1'),
                 ('7k/P7/8/8/8/8/8/K7 w - - 0 1', 'a7a8q')]
        for fen, uci in cases:
            with self.subTest(uci=uci):
                board = FowBoard(fen)
                before = board.fen()
                detail = analyze(board, uci)
                self.assertNotIn(detail['status'], ('invalid_root_in_hypothesis',
                                                    'observation_legal_mismatch'))
                self.assertEqual(board.fen(), before)
                self.assertTrue(0 <= detail['score'] < MAX_SCORE)

    def test_no_bonus_for_own_immediate_loss_invalid_root_or_known_draw(self):
        losing = FowBoard('6k1/8/8/8/8/3B4/2Q5/K6r w - - 0 1')
        self.assertEqual(analyze(losing, 'd3h7')['status'], 'root_loses_immediately')
        board = FowBoard('6k1/8/8/8/8/3B4/2Q5/K7 w - - 99 1')
        self.assertEqual(analyze(board, 'd3h7')['status'], 'known_root_draw')
        board.halfmove_clock = 0
        projected = board.copy(stack=False)
        projected.push_uci('d3h7')
        board.fow_repetitions = Counter({repetition_key(projected): 2})
        self.assertEqual(analyze(board, 'd3h7')['status'], 'known_root_draw')
        self.assertEqual(analyze(board, 'a1a8')['score'], 0)

    def test_world_duplicates_cannot_inflate_and_uncertainty_reduces_bonus(self):
        hidden = FowBoard('6k1/8/8/8/8/3B4/2Q5/K7 w - - 0 1')
        known = hidden.copy(stack=False)
        known.set_piece_at(chess.C8, chess.Piece(chess.ROOK, False))
        move = chess.Move.from_uci('d3h7')
        alone = score([hidden], move, True, 10, lambda: 0)
        mixed = score([hidden, known], move, True, 10, lambda: 0)
        repeated = score([hidden] * 20 + [known], move, True, 10, lambda: 0)
        self.assertAlmostEqual(mixed, repeated)
        self.assertLess(mixed, alone)
        self.assertTrue(0 < alone < MAX_SCORE)
        self.assertEqual(score([], move, True, 10, lambda: 0), 0)

    def test_black_symmetry_and_conservative_duplicate_draw_history(self):
        board = FowBoard('6k1/8/8/8/8/3B4/2Q5/K7 w - - 0 1')
        detail = analyze(board, 'd3h7')
        mirrored = analyze(board.mirror(), 'd6h2')
        self.assertAlmostEqual(detail['score'], mirrored['score'])
        self.assertIn('g1h2', mirrored['hidden_fatal_replies'])
        draw = board.copy(stack=False)
        projected = board.copy(stack=False)
        projected.push_uci('d3h7')
        draw.fow_repetitions = Counter({repetition_key(projected): 2})
        move = chess.Move.from_uci('d3h7')
        self.assertEqual(score([board, draw], move, True, 10, lambda: 0), 0)
        self.assertEqual(score([draw, board], move, True, 10, lambda: 0), 0)

    def test_success_leaves_repetition_metadata_and_position_unchanged(self):
        board = FowBoard('6k1/8/8/8/8/3B4/2Q5/K7 w - - 0 1')
        board.fow_repetitions = Counter({repetition_key(board): 2})
        before = (board.fen(), list(board.move_stack), board.fow_repetitions.copy())
        self.assertGreater(analyze(board, 'd3h7')['score'], 0)
        self.assertEqual((board.fen(), list(board.move_stack), board.fow_repetitions), before)

    def test_deadline_discards_partial_work_and_never_mutates_world(self):
        board = FowBoard('6k1/8/8/8/8/3B4/2Q5/K7 w - - 0 1')
        board.fow_repetitions = Counter({repetition_key(board): 1})
        before = (board.fen(), list(board.move_stack), board.fow_repetitions.copy())
        clock = itertools.count()
        with self.assertRaises(PressureExpired):
            score([board], chess.Move.from_uci('d3h7'), True, 5, lambda: next(clock))
        self.assertEqual((board.fen(), list(board.move_stack), board.fow_repetitions), before)
        with self.assertRaises(ValueError):
            score([board], chess.Move.from_uci('d3h7'), True, float('nan'))


if __name__ == '__main__':
    unittest.main()
