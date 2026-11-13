"""Observation boundary, reachability and tactical exposure tests for V5."""
import copy
import random
from types import SimpleNamespace
import unittest

import chess
from referee import FowBoard, fow_moves, observation
from uniform_random import ObservationTracker, candidates
from threat_policy import ThreatHistory, assess, risk


def state_for(board, side):
    state = SimpleNamespace(side=side, tracker=ObservationTracker(side),
                            threat_history=ThreatHistory(side))
    observe(state, board)
    return state


def observe(state, board):
    view = observation(board, state.side)
    state.tracker.observe(view)
    state.threat_history.observe(view)


def action(state, uci):
    before, legal = candidates(state.tracker.current, state.side,
                               state.tracker.rights, state.tracker.previous)
    move = chess.Move.from_uci(uci)
    if move not in legal:
        raise AssertionError('Test move must be observation-legal')
    after = before.copy(stack=False)
    after.push(move)
    return before, after, move


def prefix(moves, side=True):
    board = FowBoard()
    state = state_for(board, side)
    for uci in moves.split():
        move = chess.Move.from_uci(uci)
        if board.turn == side:
            before, _, _ = action(state, uci)
            state.threat_history.record_move(before, move)
            state.tracker.record_move(move)
        board.push(move)
        observe(state, board)
    return state, board


class ThreatPolicyTests(unittest.TestCase):
    def test_common_knowledge_start_is_not_uniform_hidden_placement(self):
        state, _ = prefix('')
        h = state.threat_history
        self.assertTrue(h.standard_start)
        self.assertEqual(h.supports[chess.ROOK], chess.BB_A8 | chess.BB_H8)
        self.assertEqual(h.supports[chess.KNIGHT], chess.BB_B8 | chess.BB_G8)
        before, after, move = action(state, 'e2e4')
        self.assertEqual(risk(state, before, after, move), 0)

    def test_enemy_one_move_reach_and_known_start_blockers(self):
        state, _ = prefix('e2e4 g8f6')
        h = state.threat_history
        self.assertTrue(h.supports[chess.KNIGHT] & chess.BB_F6)
        self.assertFalse(h.supports[chess.KNIGHT] & chess.BB_E4)
        self.assertEqual(h.supports[chess.ROOK], chess.BB_A8 | chess.BB_H8)

    def test_both_color_own_en_passant_capture_inventory(self):
        for side, moves, ep, victim in ((True, 'e2e4 a7a6 e4e5 d7d5', 'e5d6', chess.D5),
                                       (False, 'a2a3 e7e5 a3a4 e5e4 d2d4', 'e4d3', chess.D4)):
            state, _ = prefix(moves, side)
            before, after, move = action(state, ep)
            self.assertTrue(before.is_en_passant(move))
            state.threat_history.record_move(before, move)
            self.assertEqual(state.threat_history.captures[chess.PAWN], 1)
            self.assertFalse(state.threat_history.supports[chess.PAWN] & chess.BB_SQUARES[victim])
            with self.assertRaises(ValueError):
                state.threat_history.record_move(before, move)

    def test_opponent_en_passant_reaches_landing_not_victim_square(self):
        state, board = prefix('e2e4 a7a6 e4e5 d7d5 e5d6', side=False)
        self.assertEqual(board.piece_at(chess.D6), chess.Piece(chess.PAWN, True))
        self.assertTrue(state.threat_history.supports[chess.PAWN] & chess.BB_D6)
        self.assertEqual(sum(state.threat_history.captures), 0)

    def test_negative_pawn_visibility_excludes_hidden_enemy_origin(self):
        board = FowBoard('7k/8/8/8/8/8/P7/2K5 w - - 0 1')
        state = state_for(board, True)
        self.assertEqual(state.tracker.current[chess.B3], '?')
        for kind in range(1, 7):
            self.assertFalse(state.threat_history.supports[kind] & chess.BB_B3)

    def test_vacating_own_bishop_shield_increases_rook_exposure(self):
        board = FowBoard('k5r1/8/8/8/8/8/P5B1/6K1 w - - 0 1')
        state = state_for(board, True)
        quiet = assess(state, *action(state, 'a2a3'))
        opened = assess(state, *action(state, 'g2h3'))
        self.assertIn('g8', opened['types']['r']['attacking_hidden_origins'])
        self.assertNotIn('g8', quiet['types']['r']['attacking_hidden_origins'])
        self.assertGreater(opened['types']['r']['exposure_component'], quiet['types']['r']['exposure_component'])
        self.assertGreater(opened['exposure'], quiet['exposure'])
        self.assertGreater(risk(state, *action(state, 'g2h3')),
                           risk(state, *action(state, 'a2a3')))

    def test_sparse_positions_keep_distinct_total_risks_without_saturation(self):
        for fen in ('k5r1/8/8/8/8/8/P5B1/6K1 w - - 0 1',
                    '7k/8/8/8/8/8/P7/2K5 w - - 0 1'):
            with self.subTest(fen=fen):
                state = state_for(FowBoard(fen), True)
                before, legal = candidates(state.tracker.current, state.side,
                                           state.tracker.rights, state.tracker.previous)
                values = []
                for move in legal:
                    after = before.copy(stack=False)
                    after.push(move)
                    values.append(risk(state, before, after, move))
                self.assertTrue(all(0 <= value < .95 for value in values))
                self.assertGreater(max(values) - min(values), .01)

    def test_forced_blocker_remains_in_attack_witness_after_shield_moves(self):
        board = FowBoard('r6k/8/8/8/8/pn6/P7/K7 w - - 0 1')
        state = state_for(board, True)
        self.assertEqual(state.tracker.current[chess.A3], '?')
        detail = assess(state, *action(state, 'a2b3'))
        self.assertNotIn('a8', detail['types']['r']['attacking_hidden_origins'])

    def test_hidden_promotion_expands_all_promotion_types(self):
        board = FowBoard('7k/8/8/8/8/8/1p6/6K1 b - - 0 1')
        state = state_for(board, True)
        state.threat_history.captures[chess.QUEEN] = 1
        board.push(chess.Move.from_uci('b2b1q'))
        observe(state, board)
        h = state.threat_history
        self.assertTrue(h.promotion_possible)
        self.assertGreater(h.inventory_upper(chess.QUEEN), 0)
        for kind in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            self.assertTrue(h.supports[kind] & chess.BB_B1)
        self.assertFalse(h.supports[chess.PAWN] & (chess.BB_RANK_1 | chess.BB_RANK_8))

    def test_observed_captures_can_remove_original_type_without_promotion(self):
        state, _ = prefix('')
        state.threat_history.captures[chess.QUEEN] = 1
        self.assertEqual(state.threat_history.inventory_upper(chess.QUEEN), 0)

    def test_no_truth_or_particle_dependency_and_rejects_expanded_board(self):
        state, truth = prefix('e2e4 a7a6')
        before, after, move = action(state, 'e4e5')
        expected = assess(state, before, after, move)
        clone = copy.deepcopy(state)
        class Forbidden:
            def __getattribute__(self, name):
                raise AssertionError('Unapproved information channel accessed')
        clone.belief = clone.risk_model = clone.truth = clone.future = Forbidden()
        self.assertEqual(expected, assess(clone, before, after, move))
        with self.assertRaises(ValueError):
            assess(state, truth, after, move)
        poisoned = after.copy(stack=False)
        poisoned.set_piece_at(chess.E8, chess.Piece(chess.QUEEN, False))
        with self.assertRaises(ValueError):
            assess(state, before, poisoned, move)

    def test_prefix_envelopes_contain_true_enemy_locations_on_fixed_board_sequences(self):
        for seed in (17, 81, 203):
            rng, board = random.Random(seed), FowBoard()
            states = {side: state_for(board, side) for side in (True, False)}
            for _ in range(48):
                safe = []
                for move in fow_moves(board):
                    side = board.turn
                    board.push(move)
                    king = board.king(side)
                    if king is not None and not board.is_attacked_by(not side, king):
                        safe.append(move)
                    board.pop()
                if not safe:
                    break
                selected = rng.choice(safe)
                current = states[board.turn]
                before, after, _ = action(current, selected.uci())
                value = risk(current, before, after, selected)
                self.assertTrue(0 <= value < 1)
                current.threat_history.record_move(before, selected)
                current.tracker.record_move(selected)
                board.push(selected)
                for side, state in states.items():
                    observe(state, board)
                    for square in chess.scan_forward(board.occupied_co[not side]):
                        kind = board.piece_type_at(square)
                        self.assertTrue(state.threat_history.supports[kind] & chess.BB_SQUARES[square],
                                        (seed, board.ply(), side, chess.square_name(square), kind))


if __name__ == '__main__':
    unittest.main()
