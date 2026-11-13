"""Visibility parity against unmodified MIT rules, including negative evidence."""
import random
import unittest

import chess
from fast_observation import matches_masks, observation_masks, visible_mask
from referee import FowBoard, fow_moves, observation


class FastObservationTests(unittest.TestCase):
    def check_board(self, board):
        before = (board.fen(en_passant='fen'), tuple(board.move_stack))
        for side in (True, False):
            view = observation(board, side)
            expected = sum(chess.BB_SQUARES[sq] for sq, char in enumerate(view) if char != '?')
            self.assertEqual(visible_mask(board, side), expected, (board.fen(), side))
            self.assertTrue(matches_masks(board, side, observation_masks(view, side)))
            square = next(i for i, char in enumerate(view) if char != '?')
            altered = view[:square] + '?' + view[square + 1:]
            self.assertFalse(matches_masks(board, side, observation_masks(altered, side)))
        self.assertEqual((board.fen(en_passant='fen'), tuple(board.move_stack)), before)

    def test_castling_ep_promotions_and_blocked_pawns(self):
        positions = [
            chess.STARTING_FEN,
            'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',
            'k4r2/8/8/8/8/B7/8/4K2R w K - 0 1',
            'k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
            '7k/8/8/8/3Pp3/8/8/K7 b - d3 0 1',
            'k6r/6P1/8/8/8/8/8/7K w - - 0 1',
            '7k/8/8/8/8/1rq5/1P6/1K6 w - - 0 1',
            '7k/8/8/8/8/p7/P7/K7 w - - 0 1',
        ]
        for fen in positions:
            board = FowBoard(fen)
            for candidate in (board, FowBoard(board.mirror().fen(en_passant='fen'))):
                self.check_board(candidate)
                for move in fow_moves(candidate):
                    candidate.push(move)
                    self.check_board(candidate)
                    candidate.pop()

    def test_reached_positions_both_turns(self):
        rng = random.Random(907981)
        checked = 0
        for _ in range(80):
            board = FowBoard()
            for _ in range(120):
                self.check_board(board)
                checked += 1
                safe = []
                for move in fow_moves(board):
                    side = board.turn
                    board.push(move)
                    king = board.king(side)
                    if king is not None and board.king(not side) is not None and not board.is_attacked_by(not side, king):
                        safe.append(move)
                    board.pop()
                if not safe:
                    break
                board.push(rng.choice(safe))
        self.assertGreater(checked, 1000)

    def test_piece_identity_and_extra_visibility_are_constraints(self):
        board = FowBoard()
        view = observation(board, True)
        wrong = view.replace('N', 'B', 1)
        self.assertFalse(matches_masks(board, True, observation_masks(wrong, True)))
        square = view.index('?')
        wrong = view[:square] + '-' + view[square + 1:]
        self.assertFalse(matches_masks(board, True, observation_masks(wrong, True)))
        for view in ('?', 'x' * 64):
            with self.assertRaises(ValueError):
                observation_masks(view, True)

    def test_stale_en_passant_view_is_rejected_after_turn_changes(self):
        board = FowBoard('k7/8/8/3pP3/8/8/8/7K w - d6 0 1')
        for position, side in ((board, True), (board.mirror(), False)):
            previous = observation(position, side)
            previous_masks = observation_masks(previous, side)
            self.assertTrue(matches_masks(position, side, previous_masks))
            # Identical physical pieces and retained EP square, but an
            # off-turn observer cannot see through an expired EP opportunity.
            position.turn = not side
            current = observation(position, side)
            self.assertNotEqual(previous, current)
            self.assertFalse(matches_masks(position, side, previous_masks))
            self.assertTrue(matches_masks(position, side, observation_masks(current, side)))


if __name__ == '__main__':
    unittest.main()
