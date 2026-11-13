"""V4 loss utility and observation-only model integration boundaries."""
import math
import random
import unittest
from unittest.mock import patch

import chess
import bot
from referee import FowBoard, observation
from search import Search, search_root


class V4PolicyTests(unittest.TestCase):
    def test_increased_loss_cost_prevents_risky_material_override(self):
        world = FowBoard('7k/8/8/8/8/8/4N3/4K3 w - - 0 1')
        risky, safe = map(chess.Move.from_uci, ('e2f4', 'e1d1'))
        risks = {risky: .5, safe: .05}
        priors = {risky: 0, safe: 0}
        before = world.fen(), list(world.move_stack)
        # Even an overwhelmingly good continuation cannot compensate for the
        # larger modeled immediate-death chance at the declared loss aversion.
        def value(board, *_args, **_kwargs):
            return -100000 if board.peek() == risky else 0
        with patch.object(Search, 'negamax', side_effect=value):
            old, _ = search_root([world], [risky, safe], priors, 1, now=lambda: 0,
                                 max_depth=1, utility_scale=800, immediate_risks=risks)
            new, stats = search_root([world], [risky, safe], priors, 1, now=lambda: 0,
                                     max_depth=1, utility_scale=800,
                                     immediate_risks=risks, loss_cost=3200)
        self.assertEqual(old, risky)
        self.assertEqual(new, safe)
        self.assertAlmostEqual(stats['score'], -160)
        self.assertEqual((world.fen(), list(world.move_stack)), before)

    def test_loss_cost_rejects_unbounded_or_nonfinite_values(self):
        world = FowBoard()
        move = chess.Move.from_uci('e2e4')
        for scale, cost in ((None, 3200), (800, 0), (800, -1),
                            (800, math.inf), (800, math.nan)):
            with self.subTest(scale=scale, cost=cost), self.assertRaises(ValueError):
                search_root([world], [move], {move: 0}, 1, now=lambda: 0,
                            utility_scale=scale, loss_cost=cost)

    def test_model_is_blended_once_and_history_tracks_observed_capture(self):
        truth = FowBoard('7k/8/8/8/8/1bq5/1P6/1K6 w - - 0 1')
        state = bot.State(chess.WHITE, 0, risk_policy='learned')
        state.observe(observation(truth, chess.WHITE))
        state.belief_events.clear()
        state.belief.particles = [truth.copy(stack=False)]
        captured = {}
        def root(worlds, moves, priors, deadline, **kwargs):
            captured.update(kwargs)
            return moves[0], {'depth': 1, 'nodes': 0}
        with patch('bot.predict_risk', return_value=.2), patch('bot.search_root', side_effect=root):
            bot.choose_move(state, 180, random.Random(7), now=lambda: 0)
        self.assertEqual(captured['loss_cost'], 3200)
        risks = captured['immediate_risks']
        self.assertAlmostEqual(risks[chess.Move.from_uci('b2c3')], .1)
        self.assertAlmostEqual(risks[chess.Move.from_uci('b1a2')], .6)
        before, _ = state.reconstruct()
        state.record_move(before, chess.Move.from_uci('b2c3'))
        self.assertEqual(state.risk_history.captures[chess.QUEEN], 1)

    def test_stale_bank_is_not_blended_into_model_risk(self):
        state = bot.State(chess.WHITE, risk_policy='learned')
        state.observe(observation(FowBoard(), chess.WHITE))
        # Two fake stale worlds must be ignored while inference is skipped.
        state.belief.particles = [FowBoard(), FowBoard()]
        clock, captured = [0.0], {}
        original = bot.detail_score
        def detail(*args):
            clock[0] = .07
            return original(*args)
        def root(worlds, moves, priors, deadline, **kwargs):
            captured.update(kwargs)
            return moves[0], {'depth': 1, 'nodes': 0}
        with patch('bot.detail_score', side_effect=detail), patch('bot.predict_risk', return_value=.2), \
                patch('bot.search_root', side_effect=root):
            bot.choose_move(state, 180, random.Random(7), now=lambda: clock[0])
        self.assertFalse(captured['complete'])
        self.assertTrue(state.belief_events)
        self.assertEqual(set(captured['immediate_risks'].values()), {.2})

    def test_model_expiry_without_current_particles_keeps_conservative_fallback(self):
        truth = FowBoard('7k/8/8/8/8/1rq5/1P6/1K6 w - - 0 1')
        for stale in (False, True):
            with self.subTest(stale=stale):
                state = bot.State(chess.WHITE, 0, risk_policy='learned')
                state.observe(observation(truth, chess.WHITE))
                if stale:
                    state.belief.particles = [truth.copy(stack=False)]
                else:
                    state.belief_events.clear()
                clock = [.0]
                original = bot.detail_score
                def detail(*args):
                    clock[0] = .07  # Deliberately leave the observation queued.
                    return original(*args)
                def expensive_prediction(*args, **kwargs):
                    clock[0] = .2  # A single cooperative operation may overrun.
                    return .999
                with patch('bot.detail_score', side_effect=detail), \
                        patch('bot.predict_risk', side_effect=expensive_prediction) as predicted, \
                        patch('bot.search_root') as searched:
                    chosen = bot.choose_move(state, 180, random.Random(2), now=lambda: clock[0])
                self.assertEqual(predicted.call_count, 1)
                searched.assert_not_called()
                self.assertEqual(chosen.uci(), 'b1a1')
                self.assertEqual(state.last_search['status'], 'fallback')


if __name__ == '__main__':
    unittest.main()
