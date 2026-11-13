"""Observation-only belief transitions and exact visibility regressions."""
import unittest
import time
from collections import Counter
from unittest.mock import patch

import chess

from belief import Belief, forced_blockers, repetition_key, _continued
from referee import FowBoard, fow_moves, observation, position_key, outcome


class BeliefTests(unittest.TestCase):
    def follow(self, side, moves, max_particles=64):
        truth = FowBoard()
        belief = Belief(side, seed=31, max_particles=max_particles)
        belief.observe(observation(truth, side))
        for uci in moves:
            move = chess.Move.from_uci(uci)
            self.assertIn(move, fow_moves(truth))
            if truth.turn == side:
                belief.record_move(move)
            truth.push(move)
            view = observation(truth, side)
            belief.observe(view)
            self.assertTrue(belief.particles, (uci, belief.diagnostics))
            self.assertLessEqual(len(belief.particles), max_particles)
            for particle in belief.particles:
                self.assertEqual(observation(particle, side), view)
                self.assertEqual(particle.turn, truth.turn)
                self.assertFalse(particle.is_attacked_by(
                    particle.turn, particle.king(not particle.turn)))
                self.assertLess(particle.fow_repetitions[repetition_key(particle)], 3)
                if belief.diagnostics['repetition_history_exact']:
                    self.assertEqual(sum(particle.fow_repetitions.values()),
                                     belief.diagnostics['observations'])
        return truth, belief

    def test_initial_and_known_own_move_for_both_colors(self):
        for side in (True, False):
            truth, belief = self.follow(side, ['e2e4', 'e7e5', 'g1f3', 'b8c6'])
            self.assertTrue(belief.diagnostics['exact_history'])
            self.assertFalse(belief.diagnostics['complete_posterior'])
            self.assertFalse(belief.diagnostics['rejuvenated'])
            for particle in belief.particles:
                self.assertEqual(particle.occupied_co[side], truth.occupied_co[side])

    def test_opponent_capture_changes_always_visible_own_inventory(self):
        truth, belief = self.follow(True, ['e2e4', 'd7d5', 'e4d5', 'd8d5'])
        self.assertEqual(len(truth.pieces(chess.PAWN, True)), 7)
        for particle in belief.particles:
            self.assertEqual(len(particle.pieces(chess.PAWN, True)), 7)
            self.assertEqual(particle.piece_at(chess.D5), chess.Piece(chess.QUEEN, False))

    def test_en_passant_and_own_castling_rights_survive_history(self):
        _, belief = self.follow(True, ['e2e4', 'a7a6', 'e4e5', 'd7d5'])
        ep = chess.Move.from_uci('e5d6')
        self.assertTrue(all(ep in fow_moves(board) for board in belief.particles))
        _, belief = self.follow(True, ['h2h4', 'g7g6', 'h1h3', 'a7a6', 'h3h1'])
        self.assertTrue(all(not board.castling_rights & chess.BB_H1
                            for board in belief.particles))

    def test_changed_hidden_mask_requires_explicit_approximate_recovery(self):
        truth = FowBoard()
        belief = Belief(True)
        belief.observe(observation(truth, True))
        belief.record_move(chess.Move.from_uci('e2e4'))
        truth.push_uci('e2e4')
        view = list(observation(truth, True))
        # E5 is an empty legal pawn push, so hiding it contradicts the move.
        self.assertEqual(view[chess.E5], '-')
        view[chess.E5] = '?'
        belief.observe(''.join(view))
        self.assertFalse(belief.diagnostics['exact_history'])
        for particle in belief.sample(64):
            self.assertEqual(observation(particle, True), ''.join(view))
            self.assertIsNotNone(particle.piece_at(chess.E5))

    def test_recovery_preserves_inventory_and_never_recovers_exact_history_claim(self):
        truth, belief = self.follow(True, ['e2e4', 'd7d5'])
        capture = chess.Move.from_uci('e4d5')
        belief.record_move(capture)
        truth.push(capture)
        with patch.object(Belief, 'MAX_EXPANSIONS', 0):
            belief.observe(observation(truth, True))
        self.assertTrue(belief.particles)
        self.assertEqual(belief.diagnostics['recovery_count'], 1)
        self.assertFalse(belief.diagnostics['exact_history'])
        self.assertTrue(belief.diagnostics['rejuvenated'])
        self.assertFalse(belief.diagnostics['repetition_history_exact'])
        for particle in belief.particles:
            self.assertEqual(observation(particle, True), observation(truth, True))
            self.assertEqual(len(particle.piece_map()), len(truth.piece_map()))
            self.assertEqual(len(particle.pieces(chess.PAWN, False)), 7)
            self.assertEqual(particle.fow_repetitions,
                             Counter({repetition_key(particle): 1}))
        truth.push_uci('d8d5')
        belief.observe(observation(truth, True))
        self.assertTrue(belief.particles)
        self.assertFalse(belief.diagnostics['exact_history'])
        for particle in belief.particles:
            self.assertEqual(observation(particle, True), observation(truth, True))
            self.assertEqual(particle.occupied_co[False].bit_count(), 15)

    def test_recovery_of_quiet_opponent_move_neither_adds_nor_loses_material(self):
        truth = FowBoard()
        belief = Belief(False, seed=12)
        belief.observe(observation(truth, False))
        truth.push_uci('e2e4')
        with patch.object(Belief, 'MAX_EXPANSIONS', 0):
            belief.observe(observation(truth, False))
        self.assertTrue(belief.particles)
        self.assertFalse(belief.diagnostics['exact_history'])
        self.assertLessEqual(belief.diagnostics['recovery_proposals'], 64)
        for particle in belief.particles:
            self.assertEqual(observation(particle, False), observation(truth, False))
            for kind in chess.PIECE_TYPES:
                self.assertEqual(len(particle.pieces(kind, True)), len(truth.pieces(kind, True)))

    def test_unknown_initial_position_and_depletion_have_no_fake_particles(self):
        truth = FowBoard('7k/8/8/8/8/1rq5/1P6/1K6 w - - 0 1')
        belief = Belief(True)
        belief.observe(observation(truth, True))
        self.assertEqual(belief.sample(64), [])
        self.assertEqual(belief.diagnostics['status'], 'unsupported_initial_observation')
        belief.record_move(chess.Move.from_uci('b1a1'))
        truth.push_uci('b1a1')
        belief.observe(observation(truth, True))
        self.assertEqual(belief.particles, [])
        self.assertFalse(belief.diagnostics['exact_history'])

    def test_samples_are_copies_and_seeded_transition_is_repeatable(self):
        _, left = self.follow(False, ['a2a3', 'a7a6'], max_particles=4)
        _, right = self.follow(False, ['a2a3', 'a7a6'], max_particles=4)
        self.assertEqual([b.fen() for b in left.sample(64)],
                         [b.fen() for b in right.sample(64)])
        original = [b.fen() for b in left.particles]
        copies = left.sample(1)
        histories = [dict(board.fow_repetitions) for board in left.particles]
        copies[0].fow_repetitions[repetition_key(copies[0])] += 10
        copies[0].clear_board()
        self.assertEqual([b.fen() for b in left.particles], original)
        self.assertEqual([dict(b.fow_repetitions) for b in left.particles], histories)

    def test_bounded_work_and_missing_own_move_are_honest(self):
        truth = FowBoard()
        belief = Belief(False, max_particles=2)
        belief.observe(observation(truth, False))
        truth.push_uci('e2e4')
        with patch.object(Belief, 'MAX_EXPANSIONS', 1):
            belief.observe(observation(truth, False))
        self.assertLessEqual(belief.diagnostics['expansions'], 1)
        self.assertTrue(belief.diagnostics['truncated'])
        self.assertLessEqual(len(belief.particles), 2)
        own = Belief(True)
        own.observe(observation(FowBoard(), True))
        own.observe(observation(truth, True))
        self.assertEqual(own.particles, [])
        self.assertEqual(own.diagnostics['status'], 'missing_or_out_of_phase_own_move')

    def test_invalid_input_does_not_change_state(self):
        belief = Belief(True)
        belief.observe(observation(FowBoard(), True))
        prior = dict(belief.diagnostics)
        for invalid in ('?', 'x' * 64):
            with self.assertRaises(ValueError):
                belief.observe(invalid)
        self.assertEqual(belief.diagnostics, prior)
        with self.assertRaises(ValueError):
            Belief(True, max_particles=1025)

    def test_expired_deadline_bounds_transition_and_recovery_without_stale_samples(self):
        truth = FowBoard()
        belief = Belief(False)
        belief.observe(observation(truth, False))
        truth.push_uci('e2e4')
        belief.observe(observation(truth, False), deadline=time.monotonic() - 1)
        self.assertEqual(belief.diagnostics['expansions'], 0)
        self.assertEqual(belief.diagnostics['recovery_proposals'], 0)
        self.assertEqual(belief.sample(64), [])
        self.assertFalse(belief.diagnostics['exact_history'])


class RepetitionTests(unittest.TestCase):
    def test_no_moves_is_terminal_even_with_both_kings_and_no_draw(self):
        # Deliberately synthetic packed board isolates the referee's no-move
        # rule from orthodox position-validity and check restrictions.
        board = FowBoard('/'.join(['PPPPPPPP'] * 7 + ['KPPPPPPk'])
                         + ' w - - 0 1')
        board.fow_repetitions = Counter({repetition_key(board): 1})
        self.assertIsNotNone(board.king(True))
        self.assertIsNotNone(board.king(False))
        self.assertFalse(board.is_attacked_by(True, board.king(False)))
        self.assertFalse(board.is_attacked_by(False, board.king(True)))
        self.assertEqual(board.halfmove_clock, 0)
        self.assertEqual(fow_moves(board), [])
        self.assertEqual(outcome(board, Counter({position_key(board): 1})),
                         ('0-1', 'No legal moves'))
        self.assertFalse(_continued(board))

    def test_key_matches_official_counter_and_fow_ep_semantics(self):
        board = FowBoard('k3r3/3p4/8/4P3/8/8/8/4K3 b - - 0 1')
        board.push_uci('d7d5')
        self.assertFalse(board.has_legal_en_passant())
        self.assertTrue(any(board.generate_pseudo_legal_ep()))
        absent = board.copy(stack=False)
        absent.ep_square = None
        self.assertNotEqual(position_key(board), position_key(absent))
        self.assertNotEqual(repetition_key(board), repetition_key(absent))
        quiet = FowBoard()
        quiet.push_uci('e2e4')
        other = quiet.copy(stack=False)
        other.ep_square = None
        other.halfmove_clock = 41
        other.fullmove_number = 73
        other.promoted |= chess.BB_B8  # board_fen omits this knight's marker.
        self.assertEqual(position_key(quiet), position_key(other))
        self.assertEqual(repetition_key(quiet), repetition_key(other))

    def test_third_repetition_is_not_a_continuing_particle(self):
        board = FowBoard()
        official = Counter({position_key(board): 1})
        board.fow_repetitions = Counter({repetition_key(board): 1})
        cycle = ['g1f3', 'g8f6', 'f3g1', 'f6g8']
        for ply, move in enumerate(cycle * 2, 1):
            board.push_uci(move)
            official[position_key(board)] += 1
            board.fow_repetitions[repetition_key(board)] += 1
            self.assertEqual(official[position_key(board)],
                             board.fow_repetitions[repetition_key(board)])
            if ply < 8:
                self.assertTrue(_continued(board))
            else:
                self.assertFalse(_continued(board))
                self.assertEqual(outcome(board, official),
                                 ('1/2-1/2', 'Threefold repetition'))


class InferredOccupancyTests(unittest.TestCase):
    def test_hidden_rook_bait_is_a_forced_blocker(self):
        for truth, side, blocker, bait in (
            (FowBoard('7k/8/8/8/8/1rq5/1P6/1K6 w - - 0 1'), True,
             chess.B3, 'b2c3'),
            (FowBoard('1k6/1p6/1RQ5/8/8/8/8/7K b - - 0 1'), False,
             chess.B6, 'b7c6'),
        ):
            view = observation(truth, side)
            self.assertEqual(view[blocker], '?')
            self.assertIn(blocker, forced_blockers(view, side))
            truth.push_uci(bait)
            self.assertTrue(truth.is_attacked_by(not side, truth.king(side)))

    def test_hidden_double_push_blocker_and_ordinary_empty_push(self):
        truth = FowBoard('7k/8/8/8/4r3/8/4P3/K7 w - - 0 1')
        view = observation(truth, True)
        self.assertEqual(view[chess.E3], '-')
        self.assertEqual(view[chess.E4], '?')
        self.assertEqual(forced_blockers(view, True), {chess.E4})
        self.assertEqual(forced_blockers(observation(FowBoard(), True), True), set())


if __name__ == '__main__':
    unittest.main()
