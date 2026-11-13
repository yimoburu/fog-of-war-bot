"""Small optional offensive prior on hypothetical boards, never referee truth.

``score(worlds, rootmove, side, deadline, now)`` returns a bounded centipawn
bonus. The caller supplies its own hypotheses, applies an optional weight
(recommended default zero), and discards an entire root batch if
``PressureExpired`` occurs. No incomplete per-world result is returned.

A reply is a fog trap only if it appears king-safe on the opponent's current
observation but loses immediately to a currently hidden attacker in that
hypothesis. Current invisibility does NOT establish opponent ignorance: old
sightings are unknown here, so an explicit discount is applied. Reply weights
and world aggregation are heuristics, not a fitted opponent policy or a
probability of winning. This bonus must not replace own-king risk assessment.
"""
from __future__ import annotations

import math
import time

import chess

from referee import fow_moves, observation, position_key
from uniform_random import candidates

MAX_SCORE = 80.0
UNKNOWN_MEMORY_DISCOUNT = .5
CAPTURE_MIX = .25
VALUES = (0, 100, 320, 335, 510, 950, 0)


class PressureExpired(Exception):
    """The caller must discard this incomplete common evaluation batch."""


def _check(deadline, now):
    if now() >= deadline:
        raise PressureExpired


def analyze_world(world, rootmove, side, deadline, now=time.monotonic):
    """Explain one hypothetical root move without mutating the input board.

    Invalid root moves contribute zero; they never improve an aggregate by
    silently dropping an inconvenient hypothesis. A wrong side-to-move is an
    input error. Exact FOW move/observation semantics include EP and castling.
    """
    if not math.isfinite(deadline):
        raise ValueError('Pressure deadline must be finite')
    if world.turn != side:
        raise ValueError('Hypothesis must be on the root player\'s turn')
    _check(deadline, now)
    result = {'score': 0.0, 'status': 'no_hidden_trap', 'legal_replies': [],
              'apparently_safe_replies': [], 'hidden_fatal_replies': [],
              'hidden_attackers': {}, 'uniform_rate': 0.0,
              'capture_weighted_rate': 0.0}
    if rootmove not in fow_moves(world):
        result['status'] = 'invalid_root_in_hypothesis'
        return result
    enemy = not side
    previous_view = observation(world, enemy)
    board = world.copy(stack=False)
    board.push(rootmove)
    king = board.king(side)
    if king is None or board.is_attacked_by(enemy, king):
        result['status'] = 'root_loses_immediately'
        return result
    if board.king(enemy) is None:
        result['status'] = 'root_already_wins'
        return result
    # Histories belong to each input hypothesis. We only read its counter;
    # approximate histories remain approximate, never a claim about truth.
    history = getattr(world, 'fow_repetitions', {})
    # Runtime hypotheses use belief.repetition_key, not referee.position_key.
    # Derive that equivalent tuple locally without importing a belief policy.
    ep = board.ep_square if any(board.generate_pseudo_legal_ep()) else None
    draw_key = (board.pawns, board.knights, board.bishops, board.rooks,
                board.queens, board.kings, board.occupied_co[0],
                board.occupied_co[1], board.turn,
                int(board.clean_castling_rights()), ep)
    if board.halfmove_clock >= 100 or history.get(draw_key, 0) >= 2:
        result['status'] = 'known_root_draw'
        return result
    _check(deadline, now)
    view = observation(board, enemy)
    observed, perceived_moves = candidates(view, enemy,
                                          board.clean_castling_rights(), previous_view)
    replies = fow_moves(board)
    # A mismatched reconstruction should disable this optional prior, not
    # score an imagined reply. Correct current-view reconstruction is exact.
    if set(replies) != set(perceived_moves):
        result['status'] = 'observation_legal_mismatch'
        return result
    visible = sum(chess.BB_SQUARES[square] for square, symbol in enumerate(view)
                  if symbol != '?')
    safe_count = bad_count = 0
    total_weight = bad_weight = 0.0
    for reply in replies:
        _check(deadline, now)
        result['legal_replies'].append(reply.uci())
        victim = observed.piece_type_at(reply.to_square)
        material = VALUES[victim] if victim else 100 if observed.is_en_passant(reply) else 0
        # All apparent-safe quiet replies retain weight. Captures receive a
        # modest capped preference; capture greed is not assumed.
        weight = 1 + min(2.0, material / 300)
        observed.push(reply)
        board.push(reply)
        try:
            enemy_king = observed.king(enemy)
            if enemy_king is None or observed.is_attacked_by(side, enemy_king):
                continue
            safe_count += 1
            total_weight += weight
            result['apparently_safe_replies'].append(reply.uci())
            attackers = board.attackers_mask(side, enemy_king) & ~visible
            if attackers:
                # In the MIT variant this king is already capturable: the
                # opponent loses now, before we receive another move prompt.
                bad_count += 1
                bad_weight += weight
                result['hidden_fatal_replies'].append(reply.uci())
                result['hidden_attackers'][reply.uci()] = [chess.square_name(square)
                                                           for square in chess.scan_forward(attackers)]
        finally:
            observed.pop()
            board.pop()
    _check(deadline, now)
    if safe_count:
        result['uniform_rate'] = bad_count / safe_count
        result['capture_weighted_rate'] = bad_weight / total_weight
        rate = ((1 - CAPTURE_MIX) * result['uniform_rate'] +
                CAPTURE_MIX * result['capture_weighted_rate'])
        result['score'] = MAX_SCORE * UNKNOWN_MEMORY_DISCOUNT * rate
        if bad_count:
            result['status'] = 'hidden_reply_pressure'
    return result


def score(worlds, rootmove, side, deadline, now=time.monotonic):
    """Half mean plus half worst-world bonus across distinct physical boards.

    Duplicate physical boards cannot amplify support. If their repetition
    histories differ, the smallest bonus represents that physical group.
    Empty inputs return zero. An expired call raises instead of publishing a
    partial estimate; the caller controls its optional common root batch.
    """
    if not math.isfinite(deadline):
        raise ValueError('Pressure deadline must be finite')
    _check(deadline, now)
    groups = {}
    for world in worlds:
        _check(deadline, now)
        key = position_key(world)
        value = analyze_world(world, rootmove, side, deadline, now)['score']
        groups[key] = min(groups.get(key, value), value)
    _check(deadline, now)
    if not groups:
        return 0.0
    values = list(groups.values())
    return .5 * sum(values) / len(values) + .5 * min(values)
