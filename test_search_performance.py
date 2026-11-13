"""Scoring and rule equivalence against the frozen v2 search implementation."""
from collections import Counter
import importlib.util
from pathlib import Path
import random
import unittest
from unittest.mock import patch

import chess

from referee import FowBoard, fow_moves, outcome, position_key
import search


REFERENCE_PATH = Path(__file__).resolve().parent / 'opponents' / 'v2' / 'search.py'
spec = importlib.util.spec_from_file_location('frozen_v2_search_reference', REFERENCE_PATH)
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


def snapshot(board):
    return board.fen(en_passant='fen'), tuple(board.move_stack)


class SearchEquivalenceTests(unittest.TestCase):
    def test_quiet_underpromotions_are_searched_for_both_colors(self):
        white = FowBoard('7k/4P3/8/8/8/8/8/K7 w - - 0 1')
        for board, prefix in ((white, 'e7e8'), (white.mirror(), 'e2e1')):
            wanted = {prefix + suffix for suffix in 'qrbn'}
            official = {move.uci() for move in fow_moves(board) if move.promotion}
            self.assertEqual(official, wanted)
            self.assertTrue(all(not board.is_capture(chess.Move.from_uci(uci))
                                for uci in wanted))
            before, scores = snapshot(board), []
            for implementation in (reference, search):
                engine = implementation.Search(1, now=lambda: 0, node_limit=1000000)
                pushed = []
                original_push = engine.push

                def record_root_promotions(position, move):
                    if not position.move_stack and move.promotion:
                        pushed.append(move.uci())
                    original_push(position, move)

                with patch.object(engine, 'push', side_effect=record_root_promotions):
                    scores.append(engine.quiescence(board, -search.MATE, search.MATE, 0, 1))
                self.assertEqual(Counter(pushed), Counter(wanted))
                self.assertEqual(snapshot(board), before)
            self.assertEqual(scores[0], scores[1])

    def test_complete_no_pseudo_legal_move_position_is_a_loss_at_every_horizon(self):
        # Valid piece counts/ranks, with all eight white pieces and eight pawns
        # immobilized by their own pieces. No mock or orthodox check rule is
        # needed to reach the referee's distinct "No legal moves" outcome.
        white = FowBoard('NRBQKBRN/1PPPPPP1/1P4P1/8/8/8/8/k7 w - - 0 1')
        for board in (white, white.mirror()):
            self.assertTrue(board.is_valid())
            self.assertEqual(fow_moves(board), [])
            self.assertFalse(board.is_attacked_by(board.turn, board.king(not board.turn)))
            counts = Counter({position_key(board): 1})
            self.assertEqual(outcome(board, counts),
                             ('0-1' if board.turn else '1-0', 'No legal moves'))
            before = snapshot(board)
            for implementation in (reference, search):
                for depth in (0, 1, 2):
                    engine = implementation.Search(1, now=lambda: 0)
                    self.assertEqual(engine.negamax(board, depth), -search.MATE)
                    self.assertEqual(snapshot(board), before)
                for left in (0, 1, 3):
                    engine = implementation.Search(1, now=lambda: 0)
                    self.assertEqual(engine.quiescence(board, -search.MATE, search.MATE, 0, left),
                                     -search.MATE)
                    self.assertEqual(snapshot(board), before)

    def test_evaluation_matches_frozen_v2_on_reached_boards(self):
        rng = random.Random(81341)
        checked = 0
        for _ in range(32):
            board = FowBoard()
            counts = Counter({position_key(board): 1})
            for _ in range(60):
                self.assertEqual(search.evaluate(board), reference.evaluate(board))
                checked += 1
                if outcome(board, counts):
                    break
                board.push(rng.choice(fow_moves(board)))
                counts[position_key(board)] += 1
        self.assertGreater(checked, 500)

    def test_exact_shallow_search_and_quiescence_match_frozen_v2(self):
        positions = [
            # Initial play, endgame, promotion choices, pinned EP, castling
            # across an attack, ordinary exchange, and no surviving king move.
            chess.STARTING_FEN,
            '7k/8/8/3p4/4P3/8/8/K7 w - - 0 1',
            'k6r/6P1/8/8/8/8/8/7K w - - 0 1',
            'k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
            'k4r2/8/8/8/8/B7/8/4K2R w K - 0 1',
            '6k1/8/2p5/3p4/8/8/8/3Q2K1 w - - 0 1',
            '8/8/8/8/8/1qk5/8/K7 w - - 0 1',
        ]
        for fen in positions:
            for depth in (0, 1, 2):
                board = FowBoard(fen)
                before = snapshot(board)
                expected = reference.Search(1, now=lambda: 0, node_limit=1000000)
                actual = search.Search(1, now=lambda: 0, node_limit=1000000)
                self.assertEqual(actual.negamax(board, depth, qleft=2),
                                 expected.negamax(board, depth, qleft=2), (fen, depth))
                self.assertEqual(snapshot(board), before)
                self.assertEqual(actual.repetitions, expected.repetitions)
        missing = FowBoard('8/8/8/8/8/8/8/4K2R w - - 0 1')
        for left in (0, 1, 2):
            expected = reference.Search(1, now=lambda: 0, complete=False)
            actual = search.Search(1, now=lambda: 0, complete=False)
            self.assertEqual(actual.quiescence(missing, -search.MATE, search.MATE, 0, left),
                             expected.quiescence(missing, -search.MATE, search.MATE, 0, left))

    def test_complete_multiworld_root_scores_and_boards_match(self):
        worlds = []
        for reply in ('b8c6', 'g8f6'):
            board = FowBoard()
            board.push_uci('e2e4')
            board.push_uci(reply)
            worlds.append(board)
        moves = [chess.Move.from_uci(uci) for uci in ('g1f3', 'd2d4', 'e4e5', 'b1c3')]
        priors = dict(zip(moves, (4.0, 3.0, 2.0, 1.0)))
        before = [snapshot(board) for board in worlds]
        expected, old_stats = reference.search_root(
            worlds, moves, priors, 1, now=lambda: 0, max_depth=2, node_limit=1000000)
        actual, new_stats = search.search_root(
            worlds, moves, priors, 1, now=lambda: 0, max_depth=2, node_limit=1000000)
        self.assertEqual(new_stats['depth'], 2)
        self.assertEqual(new_stats['score'], old_stats['score'])
        self.assertEqual(actual, expected)
        self.assertEqual([snapshot(board) for board in worlds], before)


if __name__ == '__main__':
    unittest.main()
