"""Observation-only v5 belief regressions, including failure modes from v4."""
from collections import Counter
import itertools
import unittest
from unittest.mock import patch

import chess

from belief import _continued, _view_masks, repetition_key
from belief_v5 import Belief, copy_particle, opponent_move_weights
from referee import FowBoard, fow_moves, observation
from uniform_random import candidates


class BeliefV5Tests(unittest.TestCase):
    def follow(self, side, moves, count=64):
        truth, tracker = FowBoard(), Belief(side, seed=31, max_particles=count)
        tracker.observe(observation(truth, side))
        for uci in moves:
            move = chess.Move.from_uci(uci)
            self.assertIn(move, fow_moves(truth))
            if truth.turn == side:
                tracker.record_move(move)
            truth.push(move)
            view = observation(truth, side)
            tracker.observe(view)
            self.assertTrue(tracker.particles, (uci, tracker.diagnostics))
            self.assertLessEqual(len(tracker.particles), count)
            for board in tracker.particles:
                self.assertEqual(observation(board, side), view)
                self.assertEqual(board.turn, truth.turn)
                self.assertTrue(_continued(board))
        return truth, tracker

    def test_standard_tracking_both_colors_preserves_views_and_known_moves(self):
        line = ('e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'g8f6',
                'd2d3', 'f8c5', 'c2c3', 'd7d6')
        with patch('time.monotonic', return_value=0):
            for side in (True, False):
                truth, tracker = self.follow(side, line)
                self.assertEqual(tracker.diagnostics['particles'], len(tracker.particles))
                for board in tracker.particles:
                    if board.fow_history_exact:
                        self.assertEqual(sum(board.fow_repetitions.values()), len(line) + 1)
                    else:
                        self.assertFalse(tracker.diagnostics['exact_history'])

    def test_nonempty_bank_gets_distinct_current_view_rejuvenation(self):
        tracker = Belief(True, seed=8, max_particles=64)
        tracker.observe(observation(FowBoard(), True))
        prior = FowBoard('7k/8/8/7r/8/8/1P6/1K6 b - - 0 10')
        prior.fow_repetitions = Counter({repetition_key(prior): 1})
        prior.fow_history_exact = True
        tracker.particles = [prior]
        tracker._remember_bank()
        tracker._turn = False
        tracker._last_view = observation(prior, True)
        tracker.diagnostics['observations'] = 12
        current = prior.copy(stack=False)
        current.push_uci('h5h4')
        view = observation(current, True)
        with patch('time.monotonic', return_value=0):
            tracker.observe(view)
        self.assertGreater(tracker.diagnostics['rejuvenation_added'], 0)
        self.assertGreater(tracker.diagnostics['exact_history_particles'], 0)
        self.assertGreater(tracker.diagnostics['approximate_history_particles'], 0)
        self.assertFalse(tracker.diagnostics['exact_history'])
        self.assertFalse(tracker.diagnostics['repetition_history_exact'])
        for board in tracker.particles:
            self.assertEqual(observation(board, True), view)
            self.assertEqual(set(fow_moves(board)), set(fow_moves(current)))
            self.assertEqual(board.occupied_co[False].bit_count(), 2)
            if not board.fow_history_exact:
                self.assertEqual(board.fow_repetitions, Counter({repetition_key(board): 1}))

    def test_diversity_retains_complete_counters_without_merging_histories(self):
        tracker = Belief(True, seed=3, max_particles=16)
        truth = FowBoard()
        tracker.observe(observation(truth, True))
        parents = []
        for index in range(5):
            board = FowBoard()
            historical = FowBoard()
            historical.push(list(historical.generate_pseudo_legal_moves())[index])
            board.fow_repetitions = Counter({repetition_key(board): 1,
                                            repetition_key(historical): index + 1})
            board.fow_history_exact = True
            parents.append(board)
        tracker.particles = parents
        move = chess.Move.from_uci('e2e4')
        tracker.record_move(move)
        truth.push(move)
        expected = []
        for parent in parents:
            child = copy_particle(parent)
            child.push(move)
            child.fow_repetitions[repetition_key(child)] += 1
            expected.append(child.fow_repetitions)
        with patch('time.monotonic', return_value=0):
            tracker.observe(observation(truth, True))
        self.assertEqual(len(tracker.particles), 2)
        self.assertEqual(tracker.diagnostics['distinct_physical_positions'], 1)
        self.assertNotEqual(tracker.particles[0].fow_repetitions,
                            tracker.particles[1].fow_repetitions)
        for board in tracker.particles:
            self.assertIn(board.fow_repetitions, expected)

    def test_sampling_prefers_unique_physical_positions_and_preserves_metadata(self):
        tracker = Belief(True, seed=4)
        a, b = FowBoard(), FowBoard()
        b.push_uci('g1f3')
        for board in (a, b):
            board.fow_repetitions = Counter({repetition_key(board): 1})
            board.fow_history_exact = False
            board.fow_hypothesis_kind = 'fixture_approximate'
        tracker.particles = [copy_particle(a) for _ in range(8)] + [b]
        samples = tracker.sample(2)
        self.assertEqual(len({repetition_key(board) for board in samples}), 2)
        self.assertTrue(all(not board.fow_history_exact for board in samples))
        samples[0].fow_repetitions.clear()
        samples[0].clear_board()
        self.assertTrue(all(board.fow_repetitions for board in tracker.particles))
        self.assertTrue(all(board.king(True) is not None for board in tracker.particles))

    def test_partial_rejuvenation_keeps_unfilled_slots_and_original_histories(self):
        with patch('time.monotonic', return_value=0):
            truth, tracker = self.follow(True, ('e2e4', 'e7e5', 'g1f3', 'b8c6',
                                                'f1c4', 'g8f6', 'd2d3'))
            truth.push_uci('f8c5')
            candidate = truth.copy(stack=False)
            candidate.turn = False
            candidate.push_uci('a7a5')
            candidate.fow_history_exact = False
            candidate.fow_hypothesis_kind = 'fixture_rejuvenation'
            candidate.fow_repetitions = Counter({repetition_key(candidate): 1})
            self.assertEqual(observation(candidate, True), observation(truth, True))
            with patch.object(tracker, '_jitter', side_effect=itertools.chain(
                    (candidate,), itertools.repeat(None))):
                tracker.observe(observation(truth, True))
        self.assertEqual(tracker.diagnostics['rejuvenation_added'], 1)
        self.assertEqual(len(tracker.particles), 64)
        originals = [board for board in tracker.particles if board.fow_history_exact]
        self.assertEqual(len(originals), 63)
        self.assertTrue(all(sum(board.fow_repetitions.values()) == 9 for board in originals))
        self.assertEqual(sum(repetition_key(board) == repetition_key(candidate)
                             for board in tracker.particles), 1)

    def test_depleted_bank_retries_and_keeps_captures_including_en_passant(self):
        cases = [(('e2e4', 'd7d5'), 'e4d5', 'd8d5'),
                 (('e2e4', 'a7a6', 'e4e5', 'd7d5'), 'e5d6', 'c7d6')]
        with patch('time.monotonic', return_value=0):
            for opening, capture, reply in cases:
                with self.subTest(capture=capture):
                    truth, tracker = self.follow(True, opening)
                    tracker.particles = []
                    tracker.record_move(chess.Move.from_uci(capture))
                    truth.push_uci(capture)
                    tracker.observe(observation(truth, True), deadline=-1)
                    self.assertEqual(tracker.sample(64), [])
                    self.assertEqual(tracker.known_enemy_captures['p'], 1)
                    truth.push_uci(reply)
                    tracker.observe(observation(truth, True))
                    self.assertTrue(tracker.particles)
                    self.assertFalse(tracker.diagnostics['exact_history'])
                    for board in tracker.particles:
                        self.assertEqual(board.occupied_co[False].bit_count(), 15)
                        self.assertEqual(len(board.pieces(chess.PAWN, False)), 7)
                        self.assertEqual(observation(board, True), observation(truth, True))

    def test_hidden_promotion_can_first_appear_away_from_last_rank_without_extra_material(self):
        for side, kind in itertools.product((True, False),
                                            (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT)):
            with self.subTest(side=side, kind=kind):
                base = FowBoard('7k/1p6/8/8/8/8/1P6/1K6 w - - 0 1')
                current = FowBoard('7k/8/8/8/8/8/1P6/1K6 w - - 0 1')
                current.set_piece_at(chess.C3, chess.Piece(kind, False))
                if not side:
                    base, current = base.mirror(), current.mirror()
                tracker = Belief(side, seed=2)
                view = observation(current, side)
                masks = _view_masks(view, side)
                tracker._enemy_turns = 0
                self.assertIsNone(tracker._repair_current(base.copy(stack=False), view, None, False, masks))
                tracker._enemy_turns = 10
                repaired = tracker._repair_current(base, view, None, False, masks)
                self.assertIsNotNone(repaired)
                self.assertEqual(observation(repaired, side), view)
                self.assertEqual(repaired.occupied.bit_count(), base.occupied.bit_count())
                self.assertEqual(len(repaired.pieces(chess.PAWN, not side)), 0)
                self.assertEqual(len(repaired.pieces(kind, not side)), 1)
                self.assertFalse(repaired.fow_history_exact)

    def test_recovery_preserves_known_own_double_push_ep_rights(self):
        tracker, truth = Belief(True, seed=10), FowBoard()
        tracker.observe(observation(truth, True))
        tracker.particles = []
        tracker.record_move(chess.Move.from_uci('e2e4'))
        truth.push_uci('e2e4')
        with patch('time.monotonic', return_value=0):
            tracker.observe(observation(truth, True))
        self.assertTrue(tracker.particles)
        self.assertTrue(all(board.ep_square == chess.E3 for board in tracker.particles))

    def test_observed_capture_reduces_inventory_with_wrong_latent_promotion_type(self):
        tracker = Belief(True)
        prior = FowBoard('7k/8/8/8/8/2r5/1P6/1K6 w - - 0 1')
        prior.promoted |= chess.BB_C3
        actual_view_board = prior.copy(stack=False)
        actual_view_board.set_piece_at(chess.C3, chess.Piece(chess.QUEEN, False))
        tracker._archive = [prior]
        tracker._capture_bookkeeping(chess.Move.from_uci('b2c3'),
                                    observation(actual_view_board, True))
        self.assertEqual(prior.occupied_co[False].bit_count(), 1)
        self.assertEqual(tracker.known_enemy_captures['q'], 1)
        self.assertEqual(tracker.diagnostics['capture_inventory_type_substitutions'], 1)

    def test_opponent_prior_has_uniform_floor_and_prefers_profitable_capture(self):
        board = FowBoard('7k/8/8/8/8/q7/R7/K7 w - - 0 1')
        moves = fow_moves(board)
        weights = dict(zip(moves, opponent_move_weights(board, moves)))
        self.assertAlmostEqual(sum(weights.values()), len(moves))
        self.assertTrue(all(value >= .35 for value in weights.values()))
        self.assertGreater(weights[chess.Move.from_uci('a2a3')],
                           weights[chess.Move.from_uci('a2b2')])

    def test_deadline_stops_transition_and_repair_without_stale_samples(self):
        tracker, truth = Belief(False, max_particles=256), FowBoard()
        tracker.observe(observation(truth, False))
        truth.push_uci('e2e4')
        clock = itertools.chain((10.0,), itertools.repeat(10.1))
        with patch('belief_v5.time.monotonic', side_effect=clock):
            tracker.observe(observation(truth, False))
        self.assertEqual(tracker.diagnostics['expansions'], 0)
        self.assertEqual(tracker.diagnostics['recovery_proposals'], 0)
        self.assertEqual(tracker.sample(256), [])
        self.assertTrue(tracker._archive)

    def test_elapsed_diagnostic_includes_archive_bookkeeping(self):
        tracker, truth, clock = Belief(True), FowBoard(), [0.0]
        original = tracker._remember_bank
        def remember():
            original()
            clock[0] += .01
        with patch('belief_v5.time.monotonic', side_effect=lambda: clock[0]), \
                patch.object(tracker, '_remember_bank', side_effect=remember):
            tracker.observe(observation(truth, True))
            self.assertAlmostEqual(tracker.diagnostics['elapsed_seconds'], .01)
            tracker.record_move(chess.Move.from_uci('e2e4'))
            truth.push_uci('e2e4')
            tracker.observe(observation(truth, True))
            self.assertAlmostEqual(tracker.diagnostics['elapsed_seconds'], .01)

    def test_unsupported_start_never_seeds_a_fabricated_game(self):
        tracker = Belief(True)
        truth = FowBoard('7k/8/8/8/8/8/1P6/1K6 w - - 0 1')
        tracker.observe(observation(truth, True))
        self.assertEqual(tracker.particles, [])
        tracker.record_move(chess.Move.from_uci('b2b3'))
        truth.push_uci('b2b3')
        tracker.observe(observation(truth, True))
        self.assertEqual(tracker.particles, [])
        self.assertEqual(tracker._archive, [])

    def test_budget_and_count_validation_and_single_particle_bound(self):
        for seconds in (0, -.1, .251, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                Belief(True, update_seconds=seconds)
        with patch('time.monotonic', return_value=0):
            _, tracker = self.follow(True, ('e2e4', 'e7e5', 'g1f3', 'b8c6',
                                            'f1c4', 'g8f6', 'd2d3', 'f8c5'), count=1)
        self.assertEqual(len(tracker.particles), 1)
        self.assertEqual(tracker.diagnostics['rejuvenation_added'], 0)


if __name__ == '__main__':
    unittest.main()
