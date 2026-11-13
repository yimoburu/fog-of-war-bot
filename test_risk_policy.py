"""Bounded root utility, common-bank survival accounting and blocker policy."""
import math
import random
import unittest
from unittest.mock import patch

import chess
import bot
from referee import FowBoard, fow_moves, observation
from search import Search, SearchExpired, search_root


def snapshot(board):
    return board.fen(en_passant='fen'), tuple(board.move_stack)


def world_pair():
    # The same knight move exposes a hidden rook in one hypothesis only.
    dead = FowBoard('4r2k/8/8/8/8/8/4N3/4K3 w - - 0 1')
    alive = FowBoard('7k/8/8/8/8/8/4N3/4K3 w - - 0 1')
    return dead, alive, chess.Move.from_uci('e2f4')


def make_state(truth, policy, current=True):
    state = bot.State(truth.turn, int(truth.clean_castling_rights()),
                      seed=7, search_ms=180, risk_policy=policy)
    state.observe(observation(truth, truth.turn))
    state.belief.particles = [truth.copy(stack=False)]
    if current:
        state.belief_events.clear()
    return state


class RiskPolicyTests(unittest.TestCase):
    def test_invalid_scale_risk_fraction_and_policy_are_rejected(self):
        _, world, move = world_pair()
        for scale in (0, -1, float('inf'), float('nan')):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                search_root([world], [move], {move: 0}, 1,
                            now=lambda: 0, utility_scale=scale)
        for risks in ({}, {move: -.1}, {move: 1.1}, {move: float('nan')}, {move: float('inf')}):
            with self.subTest(risks=risks), self.assertRaises(ValueError):
                search_root([world], [move], {move: 0}, 1,
                            now=lambda: 0, utility_scale=800, immediate_risks=risks)
        with self.assertRaises(ValueError):
            search_root([world], [move], {move: 0}, 1,
                        now=lambda: 0, immediate_risks={move: .5})
        with self.assertRaises(ValueError):
            bot.State(True, risk_policy='unknown')

    def test_tactical_values_are_bounded_before_common_world_aggregation(self):
        _, world, move = world_pair()
        other = world.copy(stack=False)
        worlds = [world, other]
        before = [snapshot(item) for item in worlds]
        with patch.object(Search, 'negamax', side_effect=(-100000, 100000)):
            chosen, stats = search_root(worlds, [move], {move: 17}, 1,
                                        now=lambda: 0, max_depth=1, utility_scale=800)
        # Saturated +/-800 values: mean0 plus20% pessimistic tail, plusprior17.
        self.assertEqual(chosen, move)
        self.assertAlmostEqual(stats['score'], -143)
        self.assertEqual(stats['depth'], 1)
        self.assertEqual([snapshot(item) for item in worlds], before)

    def test_common_bank_immediate_loss_is_counted_once(self):
        dead, alive, move = world_pair()
        worlds = [dead, alive]
        before = [snapshot(item) for item in worlds]
        with patch.object(Search, 'negamax', return_value=-400) as searched:
            _, stats = search_root(worlds, [move], {move: 17}, 1,
                                   now=lambda: 0, max_depth=1, utility_scale=800,
                                   immediate_risks={move: .25})
        # The larger bank supplies25% root loss. The dead half of this small
        # search sample must not impose a second death penalty or dilute value.
        self.assertEqual(searched.call_count, 1)
        expected = .75 * (800 * math.tanh(.5)) - .25 * 800 + 17
        self.assertAlmostEqual(stats['score'], expected)
        self.assertEqual([snapshot(item) for item in worlds], before)

    def test_no_surviving_sample_stays_conservative_and_restores_world(self):
        dead, _, move = world_pair()
        before = snapshot(dead)
        with patch.object(Search, 'negamax') as searched:
            _, stats = search_root([dead], [move], {move: 0}, 1,
                                   now=lambda: 0, max_depth=1, utility_scale=800,
                                   immediate_risks={move: .25})
        searched.assert_not_called()
        self.assertEqual(stats['score'], -800)
        self.assertEqual(snapshot(dead), before)

    def test_expired_surviving_continuation_restores_all_worlds(self):
        dead, alive, move = world_pair()
        worlds = [dead, alive]
        before = [snapshot(item) for item in worlds]
        with patch.object(Search, 'negamax', side_effect=SearchExpired):
            chosen, stats = search_root(worlds, [move], {move: 17}, 1,
                                        now=lambda: 0, max_depth=1, utility_scale=800,
                                        immediate_risks={move: .25})
        self.assertEqual(chosen, move)
        self.assertEqual(stats['depth'], 0)
        self.assertEqual(stats['score'], 17)
        self.assertEqual([snapshot(item) for item in worlds], before)

    def test_certain_modeled_death_cannot_win_with_an_adversarial_prior(self):
        dead, _, suicide = world_pair()
        escape = chess.Move.from_uci('e1d1')
        self.assertIn(escape, fow_moves(dead))
        self.assertTrue(bot.visibly_safe(bot.after_move(dead, escape), chess.WHITE))
        before = snapshot(dead)
        for expired in (False, True):
            with self.subTest(expired=expired), patch.object(Search, 'negamax', return_value=0):
                chosen, stats = search_root(
                    [dead], [suicide, escape], {suicide: 1e12, escape: -1e12}, 1,
                    now=lambda: 2 if expired else 0, max_depth=1, utility_scale=800,
                    immediate_risks={suicide: 1, escape: .99})
            self.assertEqual(chosen, escape)
            self.assertEqual(stats['depth'], 0 if expired else 1)
            self.assertEqual(snapshot(dead), before)

    def _capture_root_inputs(self, truth, policy, current=True):
        state = make_state(truth, policy, current)
        captured = {}
        clock = [0.0]
        original_detail = bot.detail_score

        def detail(*args):
            value = original_detail(*args)
            if not current:
                clock[0] = .07  # Skip inference while leaving root time.
            return value

        def capture(worlds, moves, priors, deadline, **options):
            captured.update(worlds=worlds, moves=list(moves), priors=dict(priors),
                            options=dict(options))
            return moves[0], {'depth': 1, 'nodes': 0, 'score': priors[moves[0]]}

        with patch('bot.detail_score', side_effect=detail), \
                patch('bot.search_root', side_effect=capture):
            chosen = bot.choose_move(state, 180, random.Random(43), now=lambda: clock[0])
        self.assertIn(chosen, fow_moves(truth))
        return state, captured

    def test_current_hypotheses_replace_generic_blocker_and_root_death_penalties(self):
        # The hidden b3 piece is a bishop, not every possible blocker type.
        truth = FowBoard('7k/8/8/8/8/1bq5/1P6/1K6 w - - 0 1')
        state, legacy = self._capture_root_inputs(truth, 'legacy')
        _, hypothesis = self._capture_root_inputs(truth, 'hypothesis')
        _, robust = self._capture_root_inputs(truth, 'robust')
        move = chess.Move.from_uci('b2c3')
        visible, _ = state.reconstruct()
        generic = bot.blocker_risk(state, bot.after_move(visible, move), chess.C3)
        self.assertGreater(generic, 0)
        self.assertTrue(bot.visibly_safe(bot.after_move(truth, move), chess.WHITE))
        self.assertAlmostEqual(hypothesis['priors'][move] - legacy['priors'][move], generic)
        self.assertEqual(robust['options']['utility_scale'], 800)
        risks = robust['options']['immediate_risks']
        self.assertEqual(risks[move], 0)
        self.assertNotIn('immediate_risks', hypothesis['options'])
        risky = chess.Move.from_uci('b1a2')
        self.assertEqual(risks[risky], 1)
        self.assertNotIn(risky, robust['moves'])
        # Robust death risk travels through bounded utility, not a second -30000
        # prior. Hypothesis mode deliberately retains the legacy death prior.
        self.assertAlmostEqual(robust['priors'][risky] - hypothesis['priors'][risky], 30000)

    def test_stale_hypotheses_do_not_remove_conservative_blocker_prior(self):
        truth = FowBoard('7k/8/8/8/8/1bq5/1P6/1K6 w - - 0 1')
        _, legacy = self._capture_root_inputs(truth, 'legacy', current=False)
        for policy in ('hypothesis', 'robust'):
            state, inputs = self._capture_root_inputs(truth, policy, current=False)
            self.assertTrue(state.belief_events)
            self.assertEqual(inputs['priors'], legacy['priors'])
            self.assertFalse(inputs['options']['complete'])
            self.assertNotIn('utility_scale', inputs['options'])
            self.assertNotIn('immediate_risks', inputs['options'])

    def test_interrupted_survival_keeps_pre_correction_conservative_fallback(self):
        truth = FowBoard('7k/8/8/8/8/1rq5/1P6/1K6 w - - 0 1')
        for policy in ('hypothesis', 'robust'):
            with self.subTest(policy=policy):
                state = make_state(truth, policy)
                state.belief.particles = [truth.copy(stack=False) for _ in range(64)]
                particles = state.belief.particles
                particle_ids = {id(world) for world in particles}
                before = [snapshot(world) for world in particles]
                clock, checked = [0.0], [0]

                def safety(board, side):
                    if id(board) in particle_ids:
                        checked[0] += 1
                        if checked[0] == 3:
                            clock[0] = 1.0
                    return bot_safety(board, side)

                bot_safety = bot.visibly_safe
                with patch('bot.visibly_safe', side_effect=safety), \
                        patch('bot.search_root') as searched:
                    chosen = bot.choose_move(state, 180, random.Random(2), now=lambda: clock[0])
                self.assertEqual(checked[0], 3)
                searched.assert_not_called()
                self.assertEqual(chosen.uci(), 'b1a1')
                self.assertTrue(bot.visibly_safe(bot.after_move(truth, chosen), chess.WHITE))
                self.assertEqual([snapshot(world) for world in particles], before)


if __name__ == '__main__':
    unittest.main()
