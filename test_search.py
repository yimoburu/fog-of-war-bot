"""FOW search semantics, tactical replies, and deterministic bounded fallback."""
import random
from collections import Counter
import unittest
from unittest.mock import patch

import chess

from bot import State, after_move, choose_move, visibly_safe
from belief import repetition_key
from referee import FowBoard, fow_moves, observation
from search import MATE, Search, SearchExpired, search_root


def moves(*ucis):
    return [chess.Move.from_uci(uci) for uci in ucis]


def snapshot(board):
    return board.fen(en_passant='fen'), tuple(board.move_stack)


def state_for(board, seed=7):
    state = State(board.turn, int(board.clean_castling_rights()), seed=seed, search_ms=180)
    state.observe(observation(board, board.turn))
    return state


class SearchTests(unittest.TestCase):
    def test_mover_suicide_is_terminal_but_enemy_pressure_is_not(self):
        engine = Search(1, now=lambda: 0)
        suicide = FowBoard('4r2k/8/8/8/8/8/4N3/4K3 w - - 0 1')
        suicide.push_uci('e2f4')
        self.assertEqual(engine.terminal(suicide, 1), MATE - 1)
        pressure = FowBoard('k7/8/8/8/8/8/1R6/7K w - - 0 1')
        pressure.push_uci('b2a2')
        self.assertTrue(pressure.is_attacked_by(chess.WHITE, chess.A8))
        self.assertIsNone(engine.terminal(pressure, 1))
        # Black receives a reply to the threat and can move its king safely.
        self.assertLess(abs(engine.negamax(pressure, 1)), MATE / 2)

    def test_castling_across_attack_remains_searchable(self):
        world = FowBoard('k4r2/8/8/8/8/B7/8/4K2R w K - 0 1')
        castle = chess.Move.from_uci('e1g1')
        self.assertTrue(world.is_attacked_by(chess.BLACK, chess.F1))
        self.assertIn(castle, fow_moves(world))
        before = snapshot(world)
        chosen, stats = search_root([world], [castle], {castle: 0}, 1,
                                    now=lambda: 0, max_depth=1)
        self.assertEqual(chosen, castle)
        self.assertEqual(stats['depth'], 1)
        self.assertLess(abs(stats['score']), MATE / 2)
        self.assertEqual(snapshot(world), before)
        self.assertTrue(visibly_safe(after_move(world, castle), chess.WHITE))

    def test_search_rejects_queen_capture_with_pawn_recapture(self):
        world = FowBoard('6k1/8/2p5/3p4/8/8/8/3Q2K1 w - - 0 1')
        bait, quiet = moves('d1d5', 'd1e2')
        self.assertTrue(world.is_capture(bait))
        chosen, stats = search_root([world], [bait, quiet], {bait: 50, quiet: 0},
                                    1, now=lambda: 0, max_depth=2, node_limit=10000)
        self.assertEqual(chosen, quiet)
        self.assertEqual(stats['depth'], 2)

    def test_missing_unseen_enemy_is_not_an_invented_mate(self):
        world = FowBoard('8/8/8/8/8/8/8/4K2R w - - 0 1')
        engine = Search(1, now=lambda: 0, complete=False)
        self.assertIsNone(engine.terminal(world, 0))
        self.assertLess(abs(engine.negamax(world, 2)), MATE / 2)
        options = moves('h1h8', 'e1d1')
        _, stats = search_root([world], options, dict.fromkeys(options, 0), 1,
                               now=lambda: 0, complete=False, max_depth=2)
        self.assertEqual(stats['depth'], 2)
        self.assertLess(abs(stats['score']), MATE / 2)

    def test_incomplete_root_action_batch_is_discarded_at_deadline(self):
        world = FowBoard()
        preferred, other = moves('e2e4', 'd2d4')
        ticks = iter((0, 2))
        before = snapshot(world)
        # The first partial score would demote the prior favorite. Since the
        # second action never gets evaluated, none of that batch may be used.
        with patch.object(Search, 'negamax', return_value=100):
            chosen, stats = search_root([world], [preferred, other],
                                        {preferred: 10, other: 0}, 1,
                                        now=lambda: next(ticks), max_depth=1)
        self.assertEqual(chosen, preferred)
        self.assertEqual(stats['depth'], 0)
        self.assertEqual(stats['score'], 10)
        self.assertEqual(snapshot(world), before)

    def test_partial_world_batch_and_recursive_expiry_restore_boards(self):
        worlds = [FowBoard(), FowBoard()]
        options = moves('e2e4', 'd2d4')
        original = [snapshot(world) for world in worlds]
        chosen, stats = search_root(worlds, options, {options[0]: 1, options[1]: 0},
                                    1, now=lambda: 0, node_limit=3)
        self.assertEqual(chosen, options[0])
        self.assertEqual(stats['depth'], 0)
        self.assertEqual([snapshot(world) for world in worlds], original)
        board = FowBoard()
        board.push_uci('e2e4')
        board.push_uci('e7e5')
        before = snapshot(board)
        with self.assertRaises(SearchExpired):
            Search(1, now=lambda: 0, node_limit=4).negamax(board, 3)
        self.assertEqual(snapshot(board), before)

    def test_proven_hidden_blocker_bait_prefers_safe_king_escape(self):
        truth = FowBoard('7k/8/8/8/8/1rq5/1P6/1K6 w - - 0 1')
        state = state_for(truth, seed=2)
        self.assertEqual(state.tracker.current[chess.B3], '?')
        chosen = choose_move(state, 180, random.Random(2), now=lambda: 0)
        self.assertEqual(chosen.uci(), 'b1a1')
        self.assertIn(chosen, fow_moves(truth))
        self.assertTrue(visibly_safe(after_move(truth, chosen), chess.WHITE))
        self.assertGreater(state.last_search['depth'], 0)

    def test_bot_clock_validation_and_legal_node_exhaustion_fallback(self):
        state = state_for(FowBoard())
        for clock in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                choose_move(state, clock, random.Random(7), now=lambda: 0)
        # A tiny clock bypasses optional search while still yielding a move.
        tiny = choose_move(state, .000001, random.Random(7), now=lambda: 0)
        self.assertIn(tiny, state.reconstruct()[1])

        def exhausted(*args, **kwargs):
            kwargs['node_limit'] = 1
            return search_root(*args, **kwargs)

        with patch('bot.search_root', side_effect=exhausted):
            chosen = choose_move(state, 180, random.Random(7), now=lambda: 0)
        self.assertIn(chosen, state.reconstruct()[1])
        self.assertEqual(state.last_search['depth'], 0)
        self.assertEqual(state.last_search['nodes'], 1)

    def test_identical_observation_seed_and_node_budget_are_deterministic(self):
        outputs = []

        def bounded(*args, **kwargs):
            kwargs['node_limit'] = 128
            return search_root(*args, **kwargs)

        for _ in range(2):
            state = state_for(FowBoard(), seed=91)
            with patch('bot.search_root', side_effect=bounded):
                chosen = choose_move(state, 180, random.Random(43), now=lambda: 0)
            self.assertIn(chosen, state.reconstruct()[1])
            outputs.append((chosen, dict(state.last_search)))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0][1]['nodes'], 128)
        self.assertGreater(outputs[0][1]['depth'], 0)

    def test_many_observations_are_deferred_and_tiny_clock_skips_inference(self):
        truth = FowBoard()
        state = State(chess.WHITE, seed=2, search_ms=180)
        with patch.object(state.belief, 'observe') as infer:
            for _ in range(80):
                state.observe(observation(truth, chess.WHITE))
            infer.assert_not_called()
            self.assertEqual(len(state.belief_events), 80)
            chosen = choose_move(state, .000001, random.Random(2), now=lambda: 0)
            infer.assert_not_called()
        self.assertEqual(len(state.belief_events), 80)
        self.assertIn(chosen, fow_moves(truth))

    def test_expired_inference_batch_never_searches_stale_particles(self):
        truth = FowBoard()
        state = state_for(truth)
        state.observe(observation(truth, chess.WHITE))
        # This old full-world hypothesis must be excluded while newer
        # observations remain queued. Visible-only search can still run.
        state.belief.particles = [truth.copy(stack=False)]
        clock = [0.0]

        def expensive_update(view, deadline):
            self.assertIsInstance(deadline, float)
            clock[0] = .07  # Beyond 35% of the 180ms inference allowance.

        def bounded(*args, **kwargs):
            kwargs['node_limit'] = 128
            return search_root(*args, **kwargs)

        with patch.object(state.belief, 'observe', side_effect=expensive_update) as infer, \
                patch.object(state.belief, 'sample') as sample, \
                patch('bot.search_root', side_effect=bounded) as searched:
            chosen = choose_move(state, 180, random.Random(2), now=lambda: clock[0])
        self.assertEqual(infer.call_count, 1)
        self.assertEqual(len(state.belief_events), 1)
        sample.assert_not_called()
        self.assertFalse(searched.call_args.kwargs['complete'])
        self.assertIsNone(searched.call_args.args[0][0].king(chess.BLACK))
        self.assertIn(chosen, fow_moves(truth))

    def test_zero_quiescence_depth_still_detects_all_suicidal_moves(self):
        world = FowBoard('8/8/8/8/8/1qk5/8/K7 w - - 0 1')
        self.assertFalse(world.is_attacked_by(chess.BLACK, chess.A1))
        self.assertTrue(fow_moves(world))
        self.assertTrue(all(not visibly_safe(after_move(world, move), chess.WHITE)
                            for move in fow_moves(world)))
        engine = Search(1, now=lambda: 0)
        self.assertEqual(engine.quiescence(world, -MATE, MATE, 0, 0), -MATE)

    def test_threefold_history_draw_and_search_push_pop_accounting(self):
        world = FowBoard()
        cycle = moves('g1f3', 'g8f6', 'f3g1', 'f6g8')
        for move in cycle:
            world.push(move)
        engine = Search(1, now=lambda: 0)
        engine.bind_history(world)
        self.assertIsNone(engine.terminal(world, 0))
        before_board, before_counts = snapshot(world), engine.repetitions.copy()
        for move in cycle:
            engine.push(world, move)
        self.assertEqual(engine.terminal(world, 4), 0)
        # A fresh engine must also recover the three occurrences from history.
        self.assertEqual(Search(1, now=lambda: 0).terminal(world, 0), 0)
        for _ in cycle:
            engine.pop(world)
        self.assertEqual(snapshot(world), before_board)
        self.assertEqual(engine.repetitions, before_counts)
        self.assertIsNone(engine.terminal(world, 0))

    def test_survival_batch_deadline_restores_particles_and_uses_safe_fallback(self):
        truth = FowBoard('R3r2k/8/8/8/8/8/8/4K3 w - - 0 1')
        state = state_for(truth)
        state.belief_events = []
        state.belief.particles = [truth.copy(stack=False) for _ in range(64)]
        particles = state.belief.particles
        ids = {id(world) for world in particles}
        originals = [snapshot(world) for world in particles]
        clock, visited = [0.0], [0]
        state.last_search = {'depth': 9, 'nodes': 100000, 'status': 'searched'}

        def counted_safety(board, side):
            if id(board) in ids:
                visited[0] += 1
                if visited[0] == 3:
                    clock[0] = 1.0
            return visibly_safe(board, side)

        with patch('bot.visibly_safe', side_effect=counted_safety), \
                patch('bot.search_root') as searched:
            chosen = choose_move(state, 180, random.Random(2), now=lambda: clock[0])
        self.assertEqual(visited[0], 3)
        searched.assert_not_called()
        self.assertEqual([snapshot(world) for world in particles], originals)
        self.assertIn(chosen, fow_moves(truth))
        self.assertTrue(visibly_safe(after_move(truth, chosen), chess.WHITE))
        self.assertEqual(state.last_search, {'depth': 0, 'nodes': 0, 'status': 'fallback'})

    def test_transposition_cache_distinguishes_once_visited_histories(self):
        class KnightCycleBoard(FowBoard):
            """A legal knight-cycle subgame isolates repetition cache semantics."""
            def generate_pseudo_legal_moves(self, from_mask=chess.BB_ALL,
                                           to_mask=chess.BB_ALL):
                if self.turn:
                    uci = 'g1f3' if self.piece_at(chess.G1) else 'f3g1'
                else:
                    uci = 'g8f6' if self.piece_at(chess.G8) else 'f6g8'
                wanted = chess.Move.from_uci(uci)
                for move in super().generate_pseudo_legal_moves(from_mask, to_mask):
                    if move == wanted:
                        yield move

        initial = KnightCycleBoard('k5n1/8/8/8/8/8/1Q6/K5N1 w - - 3 1')
        current = repetition_key(initial)
        rich = Counter({current: 1})
        walked = initial.copy(stack=False)
        for uci in ('g1f3', 'g8f6', 'f3g1'):
            walked.push_uci(uci)
            rich[repetition_key(walked)] += 1
        lean = Counter({current: 1})
        self.assertTrue(all(value == 1 for value in rich.values()))
        board = initial.copy(stack=False)
        engine = Search(1, now=lambda: 0)

        def score(history, searcher):
            board.fow_repetitions = history.copy()
            searcher.bind_history(board)
            return searcher.negamax(board, 5, qleft=0)

        # Both histories contain identical current position/move counters and
        # no repeated entry yet. The richer one reaches a third visit at ply5.
        rich_score = score(rich, engine)
        self.assertEqual(rich_score, 0)
        cached_lean = score(lean, engine)  # Keep the same populated table.
        fresh_lean = score(lean, Search(1, now=lambda: 0))
        self.assertGreater(fresh_lean, 500)
        self.assertEqual(cached_lean, fresh_lean)
        self.assertEqual(snapshot(board), snapshot(initial))


if __name__ == '__main__':
    unittest.main()
