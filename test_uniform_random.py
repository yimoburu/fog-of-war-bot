import collections
from pathlib import Path
import random
import subprocess
import sys
import unittest

import chess

from referee import FowBoard, fow_moves, observation, outcome, position_key, run_match
from uniform_random import ObservationTracker, candidates, choose_move, infer_en_passant


def uci(moves):
    return {move.uci() for move in moves}


def seen_moves(board, previous=None):
    return candidates(observation(board, board.turn), board.turn,
                      int(board.clean_castling_rights()), previous)[1]


class UniformRandomTests(unittest.TestCase):
    def assert_complete(self, board, previous=None):
        self.assertEqual(uci(seen_moves(board, previous)), uci(fow_moves(board)))

    def test_initial_moves_and_blocked_pawn(self):
        board = FowBoard()
        self.assertEqual(len(seen_moves(board)), 20)
        self.assert_complete(board)
        board = FowBoard("k7/8/8/8/8/4r3/4P3/7K w - - 0 1")
        self.assertEqual(observation(board, True)[chess.E3], "?")
        self.assertNotIn("e2e3", uci(seen_moves(board)))
        self.assertNotIn("e2e4", uci(seen_moves(board)))
        self.assert_complete(board)

    def test_each_promotion_is_a_separate_move_and_captures_not_preferred(self):
        board = FowBoard("k6r/6P1/8/8/8/8/8/7K w - - 0 1")
        tracker = ObservationTracker(True, 0)
        tracker.observe(observation(board, True))
        available = tracker.legal_moves()
        self.assertEqual(uci(available), uci(fow_moves(board)))
        for target in ("g7g8", "g7h8"):
            for piece in "qrbn":
                self.assertIn(target + piece, uci(available))

        class EveryIndex:
            def __init__(self):
                self.index = 0

            def choice(inner_self, sequence):
                self.assertEqual(uci(sequence), uci(available))
                selected = sequence[inner_self.index]
                inner_self.index += 1
                return selected

        # The sampler passes the whole unweighted candidate list to choice.
        # Exhaust its indices: quiet moves, captures and underpromotions all
        # appear exactly once, without a probabilistic/flaky frequency test.
        rng = EveryIndex()
        selected = [choose_move(tracker, rng) for _ in available]
        self.assertEqual(collections.Counter(selected), collections.Counter(available))

    def test_castling_out_of_through_and_into_attack(self):
        for black_rank in ("k3r3", "k4r2", "k5r1"):
            board = FowBoard(f"{black_rank}/8/8/8/8/8/8/4K2R w K - 0 1")
            self.assertIn("e1g1", uci(seen_moves(board)))
            self.assert_complete(board)
        for side in (True, False):
            board = FowBoard("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
            board.turn = side
            self.assert_complete(board)
            rank = "1" if side else "8"
            self.assertIn(f"e{rank}c{rank}", uci(seen_moves(board)))
            self.assertIn(f"e{rank}g{rank}", uci(seen_moves(board)))
            board.set_piece_at(chess.B1 if side else chess.B8,
                               chess.Piece(chess.KNIGHT, side))
            self.assertNotIn(f"e{rank}c{rank}", uci(seen_moves(board)))
            self.assert_complete(board)

    def test_castling_rights_never_return_when_rook_or_king_returns(self):
        for first, second in (("h1h2", "h2h1"), ("e1e2", "e2e1")):
            board = FowBoard("k7/8/8/8/8/8/8/R3K2R w KQ - 0 1")
            tracker = ObservationTracker(True)
            tracker.observe(observation(board, True))
            for move in (first, second):
                board.turn = True
                tracker.record_move(chess.Move.from_uci(move))
                board.push_uci(move)
                tracker.observe(observation(board, True))
            board.turn = True
            self.assertNotIn("e1g1", uci(tracker.legal_moves()))
            self.assertEqual(uci(tracker.legal_moves()), uci(fow_moves(board)))

    def test_pinned_en_passant_is_legal_for_both_colors(self):
        board = FowBoard("k3r3/3p4/8/4P3/8/8/8/4K3 b - - 0 1")
        previous = observation(board, True)
        board.push_uci("d7d5")
        self.assertIn("e5d6", uci(seen_moves(board, previous)))
        self.assertFalse(board.has_legal_en_passant())
        self.assert_complete(board, previous)
        board = FowBoard("4k3/8/8/8/4p3/8/3P4/K3R3 w - - 0 1")
        previous = observation(board, False)
        board.push_uci("d2d4")
        self.assertIn("e4d3", uci(seen_moves(board, previous)))
        self.assertFalse(board.has_legal_en_passant())
        self.assert_complete(board, previous)

    def test_two_pawns_can_capture_the_same_en_passant_target(self):
        board = FowBoard("k7/3p4/8/2P1P3/8/8/8/7K b - - 0 1")
        previous = observation(board, True)
        board.push_uci("d7d5")
        self.assertTrue({"c5d6", "e5d6"} <= uci(seen_moves(board, previous)))
        self.assert_complete(board, previous)

    def test_identical_current_views_need_history_to_disambiguate_en_passant(self):
        double = FowBoard("k7/3p4/8/4PN2/5N2/8/8/7K b - - 0 1")
        single = FowBoard("k7/8/3p4/4PN2/5N2/8/8/7K b - - 0 1")
        previous_double, previous_single = observation(double, True), observation(single, True)
        double.push_uci("d7d5")
        single.push_uci("d6d5")
        self.assertEqual(observation(double, True), observation(single, True))
        self.assertIn("e5d6", uci(seen_moves(double, previous_double)))
        self.assertNotIn("e5d6", uci(seen_moves(single, previous_single)))
        self.assert_complete(double, previous_double)
        self.assert_complete(single, previous_single)

    def test_revealing_a_stationary_pawn_does_not_create_en_passant(self):
        board = FowBoard("k7/8/8/3pPN2/8/3b4/8/3R3K b - - 0 1")
        previous = observation(board, True)
        self.assertEqual(previous[chess.D5], "?")
        self.assertEqual(previous[chess.D6], "-")
        board.push_uci("d3c4")
        current = observation(board, True)
        self.assertEqual(current[chess.D5], "p")
        self.assertEqual(current[chess.D6], "-")
        self.assertIsNone(infer_en_passant(previous, current, True))
        self.assert_complete(board, previous)

    def test_starting_pawn_capture_that_reveals_other_pawn_is_not_en_passant(self):
        board = FowBoard("k2R4/3p4/4N3/3pP3/8/8/8/7K b - - 0 1")
        previous = observation(board, True)
        self.assertEqual(previous[chess.D5], "?")
        board.push_uci("d7e6")
        self.assertEqual(observation(board, True)[chess.D5], "p")
        self.assertNotIn("e5d6", uci(seen_moves(board, previous)))
        self.assert_complete(board, previous)

    def test_candidate_sets_match_many_reachable_games(self):
        rng = random.Random(124)
        checked = ep_count = 0
        for _ in range(100):
            board = FowBoard()
            trackers = {side: ObservationTracker(side) for side in (True, False)}
            repetitions = collections.Counter({position_key(board): 1})
            for _ in range(300):
                if outcome(board, repetitions):
                    break
                for side in trackers:
                    trackers[side].observe(observation(board, side))
                tracker = trackers[board.turn]
                expected = fow_moves(board)
                self.assertEqual(uci(tracker.legal_moves()), uci(expected), board.fen())
                checked += 1
                ep_count += sum(board.is_en_passant(move) for move in expected)
                move = rng.choice(expected)
                tracker.record_move(move)
                board.push(move)
                repetitions[position_key(board)] += 1
        self.assertGreater(checked, 2000)
        self.assertGreater(ep_count, 5)

    def test_ep_inference_under_varied_obstructions_and_captures(self):
        # Synthetic positions complement reachable-play testing: enumerate
        # every opponent move near a potential EP pawn, varying which other
        # pieces independently reveal, block, capture, or expose its squares.
        rng = random.Random(345)
        checked = ep_count = 0
        for _ in range(150):
            board = FowBoard(None)
            side = rng.choice((True, False))
            board.turn = not side
            rank = 4 if side else 3
            board.set_piece_at(chess.square(4, rank), chess.Piece(chess.PAWN, side))
            board.set_piece_at(chess.square(3, rng.choice((rank, 6 if side else 1))),
                               chess.Piece(chess.PAWN, not side))
            squares = [sq for sq in chess.SQUARES if board.piece_at(sq) is None]
            rng.shuffle(squares)
            pieces = [(chess.KING, side), (chess.KING, not side)] + [
                (rng.choice((1, 1, 2, 3, 4, 5)), rng.choice((True, False)))
                for _ in range(rng.randrange(15))]
            for piece, color in pieces:
                board.set_piece_at(squares.pop(), chess.Piece(piece, color))
            previous = observation(board, side)
            for move in fow_moves(board):
                after = board.copy(stack=False)
                after.push(move)
                if after.king(side) is None:
                    continue
                self.assert_complete(after, previous)
                checked += 1
                ep_count += sum(after.is_en_passant(candidate) for candidate in fow_moves(after))
        self.assertGreater(checked, 2000)
        self.assertGreater(ep_count, 20)

    def test_seeded_protocol_is_reproducible(self):
        script = str(Path(__file__).with_name("uniform_random.py"))
        transcript = "fow\nw 180 2\n" + observation(FowBoard(), True) + "\n180\n"
        outputs = [subprocess.run([sys.executable, "-u", script, "--seed", "1234"],
                                  input=transcript, text=True, capture_output=True,
                                  check=True, timeout=5).stdout for _ in range(2)]
        self.assertEqual(outputs[0], outputs[1])
        lines = outputs[0].splitlines()
        self.assertEqual(lines[0], 'ready')
        self.assertIn(lines[1].removeprefix("move "), uci(fow_moves(FowBoard())))
        self.assertEqual(len(lines), 2)

    def test_persistent_bots_complete_games_without_protocol_or_move_forfeits(self):
        script = str(Path(__file__).with_name("uniform_random.py"))
        white = [sys.executable, "-u", script, "--seed", "42"]
        black = [sys.executable, "-u", script, "--seed", "43"]
        for commands in ((white, black), (black, white)):
            replay = run_match(*commands, base=10, increment=.1, max_plies=1000)
            self.assertIn(replay["result"], ("1-0", "0-1", "1/2-1/2"))
            self.assertIn(replay["reason"], ("King capturable", "Threefold repetition",
                                             "50-move rule", "No legal moves"))
            self.assertNotIn("failedSide", replay)


if __name__ == "__main__":
    unittest.main()
