"""Meaningful boundary tests for observation-only risk features and inference."""
import copy
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

import chess
from referee import FowBoard, observation
from uniform_random import ObservationTracker, candidates
from risk_model import (EPSILON, FEATURE_NAMES, FEATURE_VERSION, RiskHistory,
                        RiskModel, features, predict)


def model_document():
    return {'schema_version': 1, 'feature_version': FEATURE_VERSION,
            'feature_names': list(FEATURE_NAMES), 'mean': [0.0] * len(FEATURE_NAMES),
            'scale': [1.0] * len(FEATURE_NAMES), 'coefficients': [0.1] * len(FEATURE_NAMES),
            'intercept': -2.0, 'normalized_clip': 8.0, 'probability_clip': EPSILON}


def make_state(side, view):
    state = SimpleNamespace(side=side, tracker=ObservationTracker(side),
                            risk_history=RiskHistory(side))
    state.tracker.observe(view)
    state.risk_history.observe(view)
    return state


def input_move(state, uci):
    before, moves = candidates(state.tracker.current, state.side,
                               state.tracker.rights, state.tracker.previous)
    move = chess.Move.from_uci(uci)
    if move not in moves:
        raise AssertionError('Fixture move must be observation-legal')
    after = before.copy(stack=False)
    after.push(move)
    return before, after, move


def prefix_state(moves, side=True):
    truth = FowBoard()
    state = make_state(side, observation(truth, side))
    for uci in moves.split():
        move = chess.Move.from_uci(uci)
        if truth.turn == side:
            before, _, _ = input_move(state, uci)
            state.risk_history.record_move(before, move)
            state.tracker.record_move(move)
        truth.push(move)
        view = observation(truth, side)
        state.tracker.observe(view)
        state.risk_history.observe(view)
    return state, truth


class RiskModelTests(unittest.TestCase):
    def test_prefix_only_invariance_and_no_belief_channel(self):
        state, _ = prefix_state('g1f3 g8f6 b1c3 b8c6')
        before, after, move = input_move(state, 'e2e4')
        expected = features(state, before, after, move)
        clone = copy.deepcopy(state)
        class Forbidden:
            def __getattribute__(self, name):
                raise AssertionError('Privileged or incidental channel read')
        clone.truth = Forbidden()
        clone.belief = Forbidden()
        clone.future_observations = Forbidden()
        clone.sightings = Forbidden()  # Module owns an independent prefix record.
        self.assertEqual(expected, features(clone, before, after, move))
        self.assertEqual(predict(state, before, after, move, RiskModel(model_document())),
                         predict(clone, before, after, move, RiskModel(model_document())))

    def test_same_prefix_different_hidden_truth_gives_identical_features(self):
        one = FowBoard('7k/p1p4p/8/8/R5PP/1P6/4r3/6K1 w - - 1 27')
        two = one.copy(stack=False)
        two.remove_piece_at(chess.E2)
        two.set_piece_at(chess.C3, chess.Piece(chess.ROOK, False))
        self.assertEqual(observation(one, True), observation(two, True))
        left, right = make_state(True, observation(one, True)), make_state(True, observation(two, True))
        a, b, m = input_move(left, 'g1h2')
        aa, bb, mm = input_move(right, 'g1h2')
        self.assertEqual(features(left, a, b, m), features(right, aa, bb, mm))
        one.push(m)
        two.push(mm)
        self.assertTrue(one.is_attacked_by(False, one.king(True)))
        self.assertFalse(two.is_attacked_by(False, two.king(True)))

    def test_truth_expanded_or_stale_inputs_rejected(self):
        state, truth = prefix_state('e2e4 a7a6')
        before, after, move = input_move(state, 'e4e5')
        with self.assertRaises(ValueError):
            features(state, truth, after, move)
        poisoned = after.copy(stack=False)
        poisoned.set_piece_at(chess.E8, chess.Piece(chess.QUEEN, False))
        with self.assertRaises(ValueError):
            features(state, before, poisoned, move)
        state.tracker.current = '?' * 64
        with self.assertRaises(ValueError):
            features(state, before, after, move)

    def test_en_passant_inventory_and_displaced_sighting(self):
        state, truth = prefix_state('e2e4 a7a6 e4e5 d7d5')
        before, after, move = input_move(state, 'e5d6')
        self.assertTrue(before.is_en_passant(move))
        self.assertEqual(state.risk_history.captures[chess.PAWN], 0)
        vector = dict(zip(FEATURE_NAMES, features(state, before, after, move)))
        self.assertEqual(vector['en_passant'], 1)
        self.assertEqual(vector['captures_pawn'], 1 / 8)
        state.risk_history.record_move(before, move)
        self.assertEqual(state.risk_history.captures[chess.PAWN], 1)
        self.assertNotIn(chess.D5, state.risk_history.sightings)
        with self.assertRaises(ValueError):
            state.risk_history.record_move(before, move)
        truth.push(move)
        state.risk_history.observe(observation(truth, True))
        self.assertEqual(state.risk_history.captures[chess.PAWN], 1)

    def test_opponent_capture_does_not_count_as_our_capture(self):
        state, _ = prefix_state('e2e4 d7d5 e4d5 d8d5')
        self.assertEqual(state.risk_history.captures[chess.PAWN], 1)
        self.assertEqual(sum(state.risk_history.captures), 1)

    def test_black_en_passant_inventory(self):
        state, _ = prefix_state('a2a3 e7e5 a3a4 e5e4 d2d4', side=False)
        before, after, move = input_move(state, 'e4d3')
        self.assertTrue(before.is_en_passant(move))
        self.assertEqual(dict(zip(FEATURE_NAMES, features(state, before, after, move)))['captures_pawn'], 1 / 8)
        state.risk_history.record_move(before, move)
        self.assertNotIn(chess.D4, state.risk_history.sightings)
        self.assertEqual(state.risk_history.captures[chess.PAWN], 1)

    def test_castling_projection_includes_rook_move(self):
        state, _ = prefix_state('e2e4 a7a6 g1f3 a6a5 f1e2 b7b6')
        before, after, move = input_move(state, 'e1g1')
        vector = dict(zip(FEATURE_NAMES, features(state, before, after, move)))
        self.assertEqual(vector['castle'], 1)
        self.assertEqual(after.piece_at(chess.F1), chess.Piece(chess.ROOK, True))
        broken = after.copy(stack=False)
        broken.remove_piece_at(chess.F1)
        with self.assertRaises(ValueError):
            features(state, before, broken, move)

    def test_hidden_promotion_potential_survives_original_queen_capture(self):
        state, _ = prefix_state('e2e4 a7a6')
        state.risk_history.captures[chess.QUEEN] = 1
        state.risk_history.ply = 70
        before, after, move = input_move(state, 'e4e5')
        vector = dict(zip(FEATURE_NAMES, features(state, before, after, move)))
        self.assertGreater(vector['queen_potential'], 0)

    def test_schema_fails_closed_and_soft_probabilities_are_finite(self):
        model = RiskModel(model_document())
        for fill in (-1e100, 0, 1e100):
            p = model.predict_features([fill] * len(FEATURE_NAMES))
            self.assertTrue(math.isfinite(p) and 0 < p < 1)
        for key, value in (('feature_version', 'future'), ('scale', [0] * len(FEATURE_NAMES)),
                           ('intercept', float('nan')), ('normalized_clip', 9),
                           ('coefficients', [float('inf')] * len(FEATURE_NAMES)),
                           ('coefficients', [True] * len(FEATURE_NAMES)),
                           ('coefficients', [1e308] * len(FEATURE_NAMES))):
            document = model_document()
            document[key] = value
            with self.assertRaises(ValueError, msg=key):
                RiskModel(document)
        with self.assertRaises(ValueError):
            model.predict_features([float('nan')] * len(FEATURE_NAMES))

    def test_artifact_when_present(self):
        artifact = Path(__file__).with_name('risk_model.json')
        if not artifact.exists():
            self.skipTest('Fitted artifact is produced after pre-fit boundary tests')
        model = RiskModel.load(artifact)
        state, _ = prefix_state('e2e4 e7e5')
        before, after, move = input_move(state, 'g1f3')
        p = predict(state, before, after, move, model)
        self.assertTrue(math.isfinite(p) and EPSILON <= p <= 1 - EPSILON)

    def test_registered_pair_split_is_order_invariant(self):
        from train_risk_model import split_pairs
        from collections import Counter
        pairs = [(171980 + 2 * i, 171981 + 2 * i) for i in range(100)]
        assigned = split_pairs(pairs)
        self.assertEqual(assigned, split_pairs(list(reversed(pairs)) + pairs))
        self.assertEqual(Counter(assigned.values()), {'train': 70, 'validation': 15, 'test': 15})


if __name__ == '__main__':
    unittest.main()
