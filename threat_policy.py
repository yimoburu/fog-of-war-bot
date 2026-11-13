"""Experimental factored hidden-threat insurance, independent of joint particles.

Attach ``state.threat_history = ThreatHistory(side)``; observe every delivered
view and record each committed own move on its observation-only before board.
``risk(state, before, after, move)`` returns a bounded exposure score. ``assess``
also returns the supported attack origins and inventory bounds. Neither API
reads a particle bank, true position, future observation or learned model.

Supports are deliberately an over-approximation: different pieces may each
have moved in mutually incompatible histories. An occupancy witness respects
the current view, but is not a complete, history-consistent world. The score is
an insurance heuristic, NOT a calibrated probability or a proof of safety.
Caller decides how to combine it with other evidence; this module neither
certifies bank risk1 nor prunes moves. No replay-specific positions are used.
"""
from __future__ import annotations

import math

import chess
from fast_observation import visible_mask

INITIAL = (0, 8, 2, 2, 2, 1, 1)
TAIL_MIX = 0.10
MAX_EXPOSURE = 0.95
ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAGONAL = ((1, 1), (1, -1), (-1, 1), (-1, -1))


def _signature(board):
    return (board.pawns, board.knights, board.bishops, board.rooks,
            board.queens, board.kings, board.occupied_co[0], board.occupied_co[1])


def _view_parts(view, side):
    if not isinstance(view, str) or len(view) != 64 or not set(view) <= set('?-PNBRQKpnbrqk'):
        raise ValueError('Expected a 64-square FOW observation')
    board = chess.Board(None)
    enemy = [0] * 7
    unknown = visible = 0
    for square, symbol in enumerate(view):
        bit = chess.BB_SQUARES[square]
        if symbol == '?':
            unknown |= bit
        else:
            visible |= bit
            if symbol != '-':
                piece = chess.Piece.from_symbol(symbol)
                board.set_piece_at(square, piece)
                if piece.color != side:
                    enemy[piece.piece_type] |= bit
    pawn_attacks = 0
    for square in chess.scan_forward(board.pawns & board.occupied_co[side]):
        pawn_attacks |= chess.BB_PAWN_ATTACKS[side][square]
    # An enemy on one of our pawn's capture destinations would be visible.
    # Unknown does not mean an enemy could occupy every such square.
    return board, enemy, unknown & ~pawn_attacks, visible


def _blockers(view, side):
    pawn, step, start = ('P', 8, 1) if side else ('p', -8, 6)
    result = 0
    for square, symbol in enumerate(view):
        if symbol != pawn:
            continue
        front = square + step
        if 0 <= front < 64 and view[front] == '?':
            result |= chess.BB_SQUARES[front]
        elif (0 <= front < 64 and view[front] == '-'
              and chess.square_rank(square) == start and view[square + 2 * step] == '?'):
            result |= chess.BB_SQUARES[square + 2 * step]
    return result


def _victim_square(before, move):
    return (move.to_square + (-8 if before.turn else 8)
            if before.is_en_passant(move) else move.to_square)


def _sliding_destinations(origin, occupied, forbidden, directions):
    result = 0
    f, r = chess.square_file(origin), chess.square_rank(origin)
    for df, dr in directions:
        ff, rr = f + df, r + dr
        while 0 <= ff < 8 and 0 <= rr < 8:
            bit = chess.BB_SQUARES[chess.square(ff, rr)]
            if not (forbidden & bit):
                result |= bit
            if occupied & bit:
                break
            ff, rr = ff + df, rr + dr
    return result


def _attacks(kind, color, origin, target, occupied):
    if origin == target:
        return False
    target_bit = chess.BB_SQUARES[target]
    if kind == chess.PAWN:
        return bool(chess.BB_PAWN_ATTACKS[color][origin] & target_bit)
    if kind == chess.KNIGHT:
        return bool(chess.BB_KNIGHT_ATTACKS[origin] & target_bit)
    if kind == chess.KING:
        return bool(chess.BB_KING_ATTACKS[origin] & target_bit)
    df = abs(chess.square_file(origin) - chess.square_file(target))
    dr = abs(chess.square_rank(origin) - chess.square_rank(target))
    aligned = ((kind in (chess.BISHOP, chess.QUEEN) and df == dr)
               or (kind in (chess.ROOK, chess.QUEEN) and (df == 0 or dr == 0)))
    return bool(aligned and not (chess.between(origin, target) & occupied))


class ThreatHistory:
    """Compact per-type reachability envelopes with known-capture inventory."""

    def __init__(self, side):
        self.side = bool(side)
        self.current = None
        self.ply = -1
        self.supports = [0] * 7
        self.captures = [0] * 7
        self.promotion_possible = False
        self.relaxations = 0
        self.standard_start = False
        self._pending = False
        self._last_double = None
        self._board = None
        self._visible_enemy = [0] * 7
        self._hidden = self._visible = 0
        self._cache = None

    def inventory_upper(self, kind):
        extra = (max(0, INITIAL[chess.PAWN] - self.captures[chess.PAWN])
                 if self.promotion_possible and kind in
                 (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN) else 0)
        return max(chess.popcount(self._visible_enemy[kind]), INITIAL[kind] + extra - self.captures[kind], 0)

    def _certain_enemy(self):
        certain = 0
        for kind in range(1, 7):
            certain |= self._visible_enemy[kind]
            lower = max(0, INITIAL[kind] - self.captures[kind])
            if self.promotion_possible and kind == chess.PAWN:
                lower = 0
            if lower and chess.popcount(self.supports[kind]) == lower:
                certain |= self.supports[kind]
        return certain

    def _expand_opponent(self, next_board):
        enemy = not self.side
        own_before = self._board.occupied_co[self.side]
        own_after = next_board.occupied_co[self.side]
        lost_own = own_before & ~own_after
        certain = self._certain_enemy() | _blockers(self.current, self.side)
        occupied = own_before | certain
        forbidden = certain | own_after
        updated = list(self.supports)  # Another enemy piece may have moved.
        promotions = 0
        for kind in range(1, 7):
            for origin in chess.scan_forward(self.supports[kind]):
                destinations = 0
                if kind == chess.PAWN:
                    step, home, back = (8, 1, 7) if enemy else (-8, 6, 0)
                    front = origin + step
                    if 0 <= front < 64 and not occupied & chess.BB_SQUARES[front]:
                        destinations |= chess.BB_SQUARES[front]
                        double = origin + 2 * step
                        if chess.square_rank(origin) == home and not occupied & chess.BB_SQUARES[double]:
                            destinations |= chess.BB_SQUARES[double]
                    destinations |= chess.BB_PAWN_ATTACKS[enemy][origin] & lost_own
                    if self._last_double is not None:
                        own_to, ep = self._last_double
                        if (lost_own & chess.BB_SQUARES[own_to]
                                and chess.square_rank(origin) == chess.square_rank(own_to)
                                and abs(chess.square_file(origin) - chess.square_file(own_to)) == 1):
                            destinations |= chess.BB_SQUARES[ep]
                    promotions |= destinations & chess.BB_RANKS[back]
                    destinations &= ~(chess.BB_RANK_1 | chess.BB_RANK_8)
                elif kind == chess.KNIGHT:
                    destinations = chess.BB_KNIGHT_ATTACKS[origin] & ~forbidden
                elif kind == chess.KING:
                    destinations = chess.BB_KING_ATTACKS[origin] & ~forbidden
                    rank = 0 if enemy else 7
                    if origin == chess.square(4, rank):
                        for target, files in ((6, (5, 6)), (2, (1, 2, 3))):
                            # Enemy castling rights are hidden; retain possible
                            # castling if no known occupant rules out its path.
                            if not any(occupied & chess.BB_SQUARES[chess.square(f, rank)] for f in files):
                                destinations |= chess.BB_SQUARES[chess.square(target, rank)]
                else:
                    directions = (DIAGONAL if kind == chess.BISHOP else ORTHOGONAL
                                  if kind == chess.ROOK else ORTHOGONAL + DIAGONAL)
                    destinations = _sliding_destinations(origin, occupied, forbidden, directions)
                updated[kind] |= destinations
        if promotions:
            self.promotion_possible = True
            for kind in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
                updated[kind] |= promotions
        self.supports = updated
        self._last_double = None

    def observe(self, view):
        board, visible_enemy, hidden, visible = _view_parts(view, self.side)
        if self.current is None:
            initial = chess.Board()
            mask = visible_mask(initial, self.side)
            initial_view = ''.join((initial.piece_at(s).symbol() if initial.piece_at(s) else '-')
                                   if mask & chess.BB_SQUARES[s] else '?' for s in chess.SQUARES)
            self.standard_start = view == initial_view
            self.supports = ([0] + [int(initial.pieces(kind, not self.side)) for kind in range(1, 7)]
                             if self.standard_start else [0] + [hidden | visible_enemy[kind] for kind in range(1, 7)])
            if not self.standard_start:
                self.relaxations += 1
        elif not self._pending:
            self._expand_opponent(board)
        self.ply += 1
        self.current = view
        self._board = board
        self._visible_enemy = visible_enemy
        self._hidden, self._visible = hidden, visible
        self._pending = False
        self._cache = None
        for kind in range(1, 7):
            if (kind in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
                    and chess.popcount(visible_enemy[kind]) + self.captures[kind] > INITIAL[kind]):
                self.promotion_possible = True
            newly_seen = visible_enemy[kind] & ~self.supports[kind]
            if newly_seen:
                self.relaxations += 1
            self.supports[kind] = (self.supports[kind] & hidden) | visible_enemy[kind]
            if self.inventory_upper(kind) == 0:
                self.supports[kind] = 0
            elif not self.supports[kind]:
                # An empty independent envelope is an approximation alarm, not
                # proof a remaining type vanished. Relax only current hidden
                # squares; never reinsert something contradicting the view.
                self.supports[kind] = hidden | visible_enemy[kind]
                self.relaxations += 1
            if kind == chess.PAWN:
                self.supports[kind] &= ~(chess.BB_RANK_1 | chess.BB_RANK_8)

    def record_move(self, before, move):
        self._validate(before)
        if self._pending:
            raise ValueError('Own move already recorded; observe its result first')
        piece = before.piece_at(move.from_square)
        if piece is None or piece.color != self.side:
            raise ValueError('Expected an observed own mover')
        victim_square = _victim_square(before, move)
        victim = before.piece_at(victim_square)
        if victim is not None and victim.color != self.side:
            self.captures[victim.piece_type] += 1
            for kind in range(1, 7):
                self.supports[kind] &= ~chess.BB_SQUARES[victim_square]
        self._last_double = ((move.to_square, (move.from_square + move.to_square) // 2)
                             if piece.piece_type == chess.PAWN
                             and abs(move.to_square - move.from_square) == 16 else None)
        self._pending = True
        self._cache = None

    def _validate(self, before):
        if (self.current is None or before.turn != self.side
                or _signature(before) != _signature(self._board)):
            raise ValueError('Threat input must be our current observation-only board')

    def witness(self, before):
        self._validate(before)
        if self._pending:
            raise ValueError('Threat queries must precede own move commitment')
        key = (int(before.castling_rights), before.ep_square)
        if self._cache is None or self._cache[0] != key:
            witness = before.copy(stack=False)
            for square in chess.scan_forward(_blockers(self.current, self.side)):
                # Only occupancy is asserted. Placeholder types are NEVER
                # counted as attackers; each tested origin supplies its type.
                witness.set_piece_at(square, chess.Piece(chess.KNIGHT, not self.side))
            if visible_mask(witness, self.side) != self._visible:
                raise ValueError('Current-view occupancy witness failed visibility parity')
            self._cache = key, witness
        return self._cache[1]


def assess(state, before, after, move):
    """Bounded, bank-independent exposure and its non-certifying evidence."""
    history = state.threat_history
    if history.side != bool(state.side) or state.tracker.current != history.current:
        raise ValueError('Missing or stale threat history')
    witness = history.witness(before).copy(stack=False)
    projected = before.copy(stack=False)
    projected.push(move)
    if _signature(projected) != _signature(after) or projected.turn != after.turn:
        raise ValueError('Threat after board must be the observation-only move projection')
    witness.push(move)
    king = witness.king(state.side)
    if king is None:
        raise ValueError('Own king must be observed')
    victim = _victim_square(before, move)
    hidden = history._hidden & ~after.occupied & ~chess.BB_SQUARES[victim]
    details = {}
    pressure = 0.0
    visible_threats = []
    for kind in range(1, 7):
        visible = after.pieces_mask(kind, not state.side)
        for origin in chess.scan_forward(visible):
            if _attacks(kind, not state.side, origin, king, witness.occupied):
                visible_threats.append(chess.square_name(origin))
        supported = history.supports[kind] & hidden
        count = chess.popcount(supported)
        # A known counterfactual capture reduces the remaining inventory for
        # this move without mutating the history shared by other candidates.
        taken = before.piece_at(victim)
        upper = history.inventory_upper(kind) - int(taken is not None and taken.color != state.side
                                                   and taken.piece_type == kind)
        hidden_units = min(count, max(0, upper - chess.popcount(visible)), 4 if kind == chess.PAWN else 2)
        dangerous = [origin for origin in chess.scan_forward(supported)
                     if _attacks(kind, not state.side, origin, king, witness.occupied)] if hidden_units else []
        fraction = len(dangerous) / count if count else 0.0
        component = hidden_units * ((1 - TAIL_MIX) * fraction + TAIL_MIX * bool(dangerous))
        pressure += component
        details[chess.piece_symbol(kind)] = {
            'supported_hidden_squares': count, 'inventory_upper_after_move': max(0, upper),
            'effective_hidden_units': hidden_units,
            'attacking_hidden_origins': [chess.square_name(s) for s in dangerous],
            'support_attack_fraction': fraction, 'exposure_component': component,
        }
    # Compress smoothly: a hard cap erased tactical differences whenever the
    # independent type envelopes contributed more than one unit in total.
    # Visible origins add at most one pressure unit regardless of their count;
    # even a visible slider can have hidden blockers. This is a monotone score,
    # not calibrated probability or multiplication of independent events.
    visible_pressure = float(bool(visible_threats))
    total_pressure = pressure + visible_pressure
    exposure = MAX_EXPOSURE * total_pressure / (1 + total_pressure)
    if not math.isfinite(exposure):
        raise ValueError('Non-finite threat exposure')
    return {'exposure': exposure, 'hidden_pressure': pressure,
            'visible_pressure': visible_pressure,
            'visible_attacking_origins': visible_threats,
            'types': details, 'promotion_possible': history.promotion_possible,
            'history_relaxations': history.relaxations,
            'standard_start': history.standard_start,
            'semantics': 'factored support insurance; not a posterior or proof of safety'}


def risk(state, before, after, move):
    """Return only the bounded exposure score; callers choose its influence."""
    return assess(state, before, after, move)['exposure']
