"""Integration checks for the separate experimental v5 entrypoint."""
from collections import Counter
import io
import random
import unittest
from unittest.mock import patch

import chess

import bot_v5
from belief import repetition_key
from referee import FowBoard, fow_moves, observation


def state_for(board, **options):
    state = bot_v5.State(board.turn, rights=int(board.clean_castling_rights()),
                         **options)
    state.observe(observation(board, board.turn))
    return state


class BotV5IntegrationTests(unittest.TestCase):
    def test_proven_hidden_blocker_preserves_safe_capture_in_the_root_pool(self):
        truth = FowBoard('r6k/8/8/8/8/pn6/P5B1/K7 w - - 0 1')
        state = state_for(truth, belief_mode='original', search_ms=600)
        before, legal = state.reconstruct()
        move = chess.Move.from_uci('a2b3')
        self.assertEqual(state.tracker.current[chess.A8], 'r')
        self.assertEqual(state.tracker.current[chess.A3], '?')
        self.assertIn(move, legal)
        after = bot_v5.base.after_move(before, move)
        self.assertFalse(bot_v5.base.visibly_safe(after, True))
        self.assertTrue(bot_v5.base.visibly_safe(bot_v5.base.after_move(truth, move), True))
        self.assertTrue(bot_v5.observed_safe(state, before, after, move))
        state.belief_events.clear()
        def search(worlds, moves, priors, deadline, **options):
            self.assertIn(move, moves)
            return move, {'depth': 1, 'nodes': 1, 'worlds': len(worlds), 'score': 0}
        with patch.object(bot_v5, 'threat_exposure', return_value=0), \
                patch.object(bot_v5.base, 'search_root', side_effect=search):
            chosen = bot_v5.choose_move(state, 180, random.Random(3), now=lambda: 0)
        self.assertEqual(chosen, move)

    def test_placeholder_knight_supplies_occupancy_without_becoming_an_attacker(self):
        white = FowBoard('7k/8/8/8/8/p7/P6P/1K6 w - - 0 1')
        for truth, uci, blocker in ((white, 'h2h3', chess.A3),
                                    (white.mirror(), 'h7h6', chess.A6)):
            state = state_for(truth)
            before, legal = state.reconstruct()
            move = chess.Move.from_uci(uci)
            self.assertIn(move, legal)
            witness = state.threat_history.witness(before).copy(stack=False)
            self.assertEqual(witness.piece_at(blocker), chess.Piece(chess.KNIGHT, not state.side))
            witness.push(move)
            # Treating the placeholder's invented type as real would reject a
            # safe quiet move: this knight attacks b1 (or b8 after mirroring).
            self.assertTrue(witness.is_attacked_by(not state.side, witness.king(state.side)))
            after = bot_v5.base.after_move(before, move)
            self.assertTrue(bot_v5.observed_safe(state, before, after, move))
            self.assertTrue(bot_v5.base.visibly_safe(bot_v5.base.after_move(truth, move), state.side))

    def test_physical_count_ignores_inert_ep_and_preserves_effective_ep(self):
        board = FowBoard()
        board.push_uci('e2e4')
        absent = board.copy(stack=False)
        absent.ep_square = None
        self.assertEqual(bot_v5.physical_count([board, absent]), 1)
        board = FowBoard('k3r3/3p4/8/4P3/8/8/8/4K3 b - - 0 1')
        board.push_uci('d7d5')
        absent = board.copy(stack=False)
        absent.ep_square = None
        self.assertFalse(board.has_legal_en_passant())
        self.assertTrue(any(board.generate_pseudo_legal_ep()))
        self.assertEqual(bot_v5.physical_count([board, absent]), 2)

    def test_all_finite_bank_modes_keep_unanimous_risk_below_certainty(self):
        for weight in (0, .1, .35, 1):
            for size in (1, 64, 256):
                value = bot_v5.mix_risk(1, .8, size, weight, 8)
                self.assertGreaterEqual(value, 0)
                self.assertLess(value, 1, (weight, size))
        self.assertEqual(bot_v5.mix_risk(0, .7, 0, .35, 8), .7)
        self.assertLess(bot_v5.mix_risk(0, .7, 256, .35, 8), .7)
        self.assertGreater(bot_v5.mix_risk(0, .7, 256, .35, 8), 0)

    def test_wrong_single_physical_bank_does_not_veto_an_observable_escape(self):
        truth = FowBoard('7k/8/8/8/8/2r5/8/1K6 w - - 0 1')
        wrong = FowBoard('7k/8/8/8/8/1r6/8/1K6 w - - 0 1')
        self.assertEqual(observation(truth, True), observation(wrong, True))
        escape = chess.Move.from_uci('b1b2')
        projected = truth.copy(stack=False)
        projected.push(escape)
        self.assertFalse(projected.is_attacked_by(False, projected.king(True)))
        state = state_for(truth, belief_mode='original', search_ms=600)
        state.belief_events.clear()
        wrong.fow_repetitions = Counter({repetition_key(wrong): 1})
        state.belief.particles = [wrong.copy(stack=False) for _ in range(8)]
        for board in state.belief.particles:
            board.fow_repetitions = Counter({repetition_key(board): 1})
        captured = {}
        def search(worlds, moves, priors, deadline, **options):
            captured.update(options)
            self.assertIn(escape, moves)
            self.assertLess(options['immediate_risks'][escape], 1)
            return escape, {'depth': 1, 'nodes': 1, 'worlds': len(worlds), 'score': 0}
        with patch.object(bot_v5.base, 'search_root', side_effect=search):
            chosen = bot_v5.choose_move(state, 180, random.Random(3), now=lambda: 0)
        self.assertEqual(chosen, escape)
        self.assertEqual(state.last_search['physical_positions'], 1)
        self.assertEqual(state.last_search['chosen_bank_risk'], 1)
        self.assertLess(state.last_search['chosen_risk'], 1)

    def test_exposure_deadline_is_checked_with_empty_and_nonempty_banks(self):
        for populated in (False, True):
            with self.subTest(populated=populated):
                truth = FowBoard()
                state = state_for(truth, belief_mode='original', search_ms=600)
                state.belief_events.clear()
                state.belief.particles = [truth.copy(stack=False)] if populated else []
                saved = [board.fen(en_passant='fen') for board in state.belief.particles]
                clock = [0.0]
                def exposure(*args):
                    clock[0] = 2.0
                    return .2
                with patch.object(bot_v5, 'threat_exposure', side_effect=exposure) as insurance, \
                        patch.object(bot_v5.base, 'search_root') as search:
                    chosen = bot_v5.choose_move(state, 180, random.Random(9), now=lambda: clock[0])
                self.assertEqual(insurance.call_count, 1)
                search.assert_not_called()
                self.assertIn(chosen, fow_moves(truth))
                self.assertEqual([board.fen(en_passant='fen') for board in state.belief.particles], saved)

    def test_expired_search_retains_complete_visible_safety_pass(self):
        truth = FowBoard('R3r2k/8/8/8/5q2/8/4N3/4K3 w - - 0 1')
        state = state_for(truth, search_ms=600)
        clock = iter((0.0, 1.0))
        with patch.object(bot_v5, 'threat_exposure') as insurance, \
                patch.object(bot_v5.base, 'search_root') as search:
            chosen = bot_v5.choose_move(state, 180, random.Random(4), now=lambda: next(clock))
        before, legal = state.reconstruct()
        self.assertIn(chosen, legal)
        self.assertTrue(bot_v5.base.visibly_safe(bot_v5.base.after_move(before, chosen), True))
        insurance.assert_not_called()
        search.assert_not_called()

    def test_empty_bank_uses_current_insurance_and_incomplete_search_world(self):
        truth = FowBoard()
        state = state_for(truth, belief_mode='original', search_ms=600, pressure_weight=1)
        state.belief_events.clear()
        captured = {}
        def search(worlds, moves, priors, deadline, **options):
            captured.update(options)
            return moves[0], {'depth': 1, 'nodes': 1, 'worlds': len(worlds), 'score': 0}
        with patch.object(bot_v5, 'threat_exposure', return_value=.4), \
                patch.object(bot_v5, 'pressure_score') as pressure, \
                patch.object(bot_v5.base, 'search_root', side_effect=search):
            chosen = bot_v5.choose_move(state, 180, random.Random(2), now=lambda: 0)
        pressure.assert_not_called()
        self.assertFalse(captured['complete'])
        self.assertTrue(all(value == .4 for value in captured['immediate_risks'].values()))
        self.assertEqual(state.last_search['particles'], 0)
        self.assertEqual(state.last_search['chosen_risk'], .4)
        self.assertIn(chosen, fow_moves(truth))

    def test_interrupted_pressure_discards_every_partial_root_bonus(self):
        captures = []
        states = []
        def search(worlds, moves, priors, deadline, **options):
            captures.append((list(moves), dict(priors), dict(options['immediate_risks'])))
            return moves[0], {'depth': 1, 'nodes': 1, 'worlds': len(worlds), 'score': 0}
        for weight in (0, 1):
            truth = FowBoard()
            state = state_for(truth, belief_mode='original', search_ms=600,
                              pressure_weight=weight)
            state.belief_events.clear()
            state.belief.particles = [truth.copy(stack=False)]
            states.append(state)
            with patch.object(bot_v5, 'threat_exposure', return_value=0), \
                    patch.object(bot_v5, 'pressure_score', side_effect=(40, bot_v5.PressureExpired())) as pressure, \
                    patch.object(bot_v5.base, 'search_root', side_effect=search):
                bot_v5.choose_move(state, 180, random.Random(5), now=lambda: 0)
            self.assertEqual(pressure.call_count, 2 if weight else 0)
        self.assertEqual(captures[0], captures[1])
        self.assertEqual(states[1].last_search['chosen_pressure_bonus'], 0)

    def test_completed_pressure_batch_changes_all_selected_priors_once(self):
        captures = []
        states = []
        def search(worlds, moves, priors, deadline, **options):
            captures.append((list(moves), dict(priors), dict(options['immediate_risks'])))
            return moves[0], {'depth': 1, 'nodes': 1, 'worlds': len(worlds), 'score': 0}
        for weight in (0, 2):
            truth = FowBoard()
            state = state_for(truth, belief_mode='original', search_ms=600,
                              pressure_weight=weight)
            state.belief_events.clear()
            state.belief.particles = [truth.copy(stack=False)]
            states.append(state)
            with patch.object(bot_v5, 'threat_exposure', return_value=0), \
                    patch.object(bot_v5, 'pressure_score', return_value=10) as pressure, \
                    patch.object(bot_v5.base, 'search_root', side_effect=search):
                bot_v5.choose_move(state, 180, random.Random(5), now=lambda: 0)
            self.assertEqual(pressure.call_count, len(captures[-1][0]) if weight else 0)
        selected, original, risks = captures[0]
        self.assertEqual(selected, captures[1][0])
        self.assertEqual(risks, captures[1][2])
        for move, prior in original.items():
            self.assertAlmostEqual(captures[1][1][move] - prior, 20 if move in selected else 0)
        self.assertEqual(states[1].last_search['chosen_pressure_bonus'], 20)

    def test_every_ply_history_order_and_capture_ledgers_for_both_sides(self):
        truth = FowBoard()
        states = {side: bot_v5.State(side, search_ms=0) for side in (True, False)}
        for state in states.values():
            state.observe(observation(truth, state.side))
        for uci in ('e2e4', 'a7a6', 'e4e5', 'd7d5', 'e5d6', 'c7d6'):
            mover = states[truth.turn]
            before, moves = mover.reconstruct()
            move = chess.Move.from_uci(uci)
            self.assertIn(move, moves)
            mover.record_move(before, move)
            truth.push(move)
            for state in states.values():
                state.observe(observation(truth, state.side))
                self.assertEqual(state.threat_history.current, state.tracker.current)
                self.assertEqual(state.threat_history.ply, state.ply)
                for square in chess.scan_forward(truth.occupied_co[not state.side]):
                    self.assertTrue(state.threat_history.supports[truth.piece_type_at(square)]
                                    & chess.BB_SQUARES[square])
        for state in states.values():
            self.assertEqual(state.threat_history.captures[chess.PAWN], 1)
            self.assertEqual(state.risk_history.captures[chess.PAWN], 1)

    def test_wire_handshake_and_move_output_are_legal_for_both_colors(self):
        for side in (True, False):
            truth = FowBoard()
            views = [observation(truth, side)]
            if not side:
                truth.push_uci('e2e4')
                views.append(observation(truth, side))
            incoming = io.StringIO('fow\n' + ('w' if side else 'b') + ' 180 2\n'
                                   + '\n'.join(views) + '\n180\n')
            outgoing = io.StringIO()
            bot_v5.serve(incoming, outgoing, seed=1, search_ms=0)
            lines = outgoing.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], 'ready')
            self.assertTrue(lines[1].startswith('move '))
            self.assertIn(chess.Move.from_uci(lines[1][5:]), fow_moves(truth))

    def test_wire_rejects_duplicate_clock_without_fresh_observation(self):
        incoming = io.StringIO('fow\nw 180 2\n' + observation(FowBoard(), True)
                               + '\n180\n180\n')
        with self.assertRaisesRegex(ValueError, 'fresh observation'):
            bot_v5.serve(incoming, io.StringIO(), seed=1, search_ms=0)


if __name__ == '__main__':
    unittest.main()
