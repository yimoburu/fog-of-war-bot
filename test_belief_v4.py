"""Compiled observation tracking and expanded bounded particle-bank regressions."""
import importlib.util
import itertools
from pathlib import Path
import unittest
from collections import Counter
from unittest.mock import patch

import chess

import belief
from belief import Belief, repetition_key
from referee import FowBoard, fow_moves, observation


def frozen_belief():
    path = Path(__file__).with_name('opponents') / 'v2' / 'belief.py'
    spec = importlib.util.spec_from_file_location('_v2_belief_differential', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BeliefV4Tests(unittest.TestCase):
    OPENING = ('e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'g8f6',
               'd2d3', 'f8c5', 'c2c3', 'd7d6')

    @classmethod
    def setUpClass(cls):
        cls.v2 = frozen_belief()

    def assert_same_particles(self, current, old):
        # Keys include the complete repetition Counter, not only board layout.
        self.assertEqual([belief._key(board) for board in current.particles],
                         [self.v2._key(board) for board in old.particles])
        self.assertEqual({key: value for key, value in current.diagnostics.items()
                          if key != 'elapsed_seconds'},
                         {key: value for key, value in old.diagnostics.items()
                          if key != 'elapsed_seconds'})

    def test_tracking_matches_frozen_v2_without_time_truncation(self):
        lines = (self.OPENING,
                 ('e2e4', 'd7d5', 'e4d5', 'd8d5'),
                 ('e2e4', 'a7a6', 'e4e5', 'd7d5', 'e5d6'),
                 ('h2h4', 'g7g6', 'h1h3', 'a7a6', 'h3h1'))
        # A fixed clock isolates matching semantics from host scheduling. The
        # normal finite expansion bound still applies to both implementations.
        with patch('time.monotonic', return_value=0):
            for side, moves in itertools.product((True, False), lines):
                with self.subTest(side=side, moves=moves):
                    current = Belief(side, seed=31)
                    old = self.v2.Belief(side, seed=31)
                    truth = FowBoard()
                    view = observation(truth, side)
                    current.observe(view)
                    old.observe(view)
                    self.assert_same_particles(current, old)
                    for uci in moves:
                        move = chess.Move.from_uci(uci)
                        self.assertIn(move, fow_moves(truth))
                        if truth.turn == side:
                            current.record_move(move)
                            old.record_move(move)
                        truth.push(move)
                        view = observation(truth, side)
                        current.observe(view)
                        old.observe(view)
                        self.assertFalse(current.diagnostics['truncated'])
                        self.assertTrue(current.diagnostics['exact_history'])
                        self.assert_same_particles(current, old)

    def test_recovery_compiles_once_and_preserves_frozen_inventory_and_history(self):
        current, old = Belief(True, seed=31), self.v2.Belief(True, seed=31)
        truth = FowBoard()
        with patch('time.monotonic', return_value=0):
            for tracker in (current, old):
                tracker.observe(observation(truth, True))
            for uci in ('e2e4', 'd7d5', 'e4d5'):
                move = chess.Move.from_uci(uci)
                if truth.turn:
                    current.record_move(move)
                    old.record_move(move)
                truth.push(move)
                view = observation(truth, True)
                if uci == 'e4d5':
                    # Force the same repair path for a known own capture.
                    with patch.object(Belief, 'MAX_EXPANSIONS', 0), \
                            patch.object(self.v2.Belief, 'MAX_EXPANSIONS', 0), \
                            patch('belief.observation_masks',
                                  wraps=belief.observation_masks) as compile_view:
                        current.observe(view)
                        old.observe(view)
                    self.assertEqual(compile_view.call_count, 1)
                else:
                    current.observe(view)
                    old.observe(view)
                self.assert_same_particles(current, old)
        self.assertTrue(current.particles)
        self.assertFalse(current.diagnostics['exact_history'])
        self.assertFalse(current.diagnostics['repetition_history_exact'])
        for particle in current.particles:
            self.assertEqual(observation(particle, True), view)
            self.assertEqual(particle.occupied_co[False].bit_count(), 15)
            self.assertEqual(particle.fow_repetitions,
                             Counter({repetition_key(particle): 1}))

    def test_256_bank_grows_beyond_old_cap_with_exact_views_and_copied_histories(self):
        with patch('time.monotonic', return_value=0):
            for side in (True, False):
                with self.subTest(side=side):
                    current = Belief(side, seed=31, max_particles=256)
                    truth = FowBoard()
                    current.observe(observation(truth, side))
                    peak_size = peak_expansions = 0
                    for uci in self.OPENING:
                        move = chess.Move.from_uci(uci)
                        if truth.turn == side:
                            current.record_move(move)
                        truth.push(move)
                        view = observation(truth, side)
                        current.observe(view)
                        self.assertTrue(current.particles)
                        self.assertLessEqual(len(current.particles), 256)
                        self.assertLessEqual(current.diagnostics['expansions'], 16384)
                        self.assertTrue(current.diagnostics['exact_history'])
                        peak_size = max(peak_size, len(current.particles))
                        peak_expansions = max(peak_expansions,
                                              current.diagnostics['expansions'])
                        for particle in current.particles:
                            self.assertEqual(observation(particle, side), view)
                            self.assertEqual(sum(particle.fow_repetitions.values()),
                                             current.diagnostics['observations'])
                    self.assertEqual(peak_size, 256)
                    self.assertGreater(peak_expansions, 4096)
                    before = [dict(board.fow_repetitions) for board in current.particles]
                    samples = current.sample(256)
                    self.assertEqual(len(samples), len(current.particles))
                    samples[0].fow_repetitions.clear()
                    samples[0].clear_board()
                    self.assertEqual([dict(board.fow_repetitions)
                                      for board in current.particles], before)

    def test_recovery_shares_default_absolute_update_limit(self):
        current = Belief(False, max_particles=256)
        truth = FowBoard()
        current.observe(observation(truth, False))
        truth.push_uci('e2e4')
        # Simulate host preemption after observe starts. Recovery must not gain
        # another 40 ms once the original 80 ms update limit has expired.
        clock = itertools.chain((10.0,), itertools.repeat(10.1))
        with patch('belief.time.monotonic', side_effect=clock):
            current.observe(observation(truth, False))
        self.assertEqual(current.diagnostics['expansions'], 0)
        self.assertEqual(current.diagnostics['recovery_proposals'], 0)
        self.assertEqual(current.sample(256), [])

    def test_particle_count_validation_and_unchanged_default(self):
        self.assertEqual(Belief(True).max_particles, 64)
        for count in (1, 64, 65, 128, 256, 1024):
            self.assertEqual(Belief(True, max_particles=count).max_particles, count)
        for count in (0, -1, 1025, 1.0, '256', None, True, False):
            with self.subTest(count=count), self.assertRaises(ValueError):
                Belief(True, max_particles=count)


if __name__ == '__main__':
    unittest.main()
