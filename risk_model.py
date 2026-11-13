"""Experimental immediate king-loss probability from a player's observations.

Runtime dependencies: Python standard library and chess only. Attach
``state.risk_history = RiskHistory(state.side)`` when a State is created. Call
``risk_history.observe(view)`` exactly once for every observation, including
after our own move; call ``risk_history.record_move(before, move)`` once when
our chosen move is committed, using the observation-only before board.

``features(state, before, after, move)`` returns FEATURE_NAMES-ordered floats.
``predict(state, before, after, move, model=None)`` returns a soft probability.
Boards must contain only observed pieces; added hidden pieces are rejected.
The state interface reads only side, tracker.current and risk_history. Belief
particles, truth boards, future observations and search scores are never read.
The model is empirical on its documented training distribution, not a proof.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path

import chess

FEATURE_VERSION = "v4-immediate-risk-1"
SCHEMA_VERSION = 1
MEMORY_PLIES = 12
EPSILON = 1e-6
INITIAL_COUNTS = (0, 8, 2, 2, 2, 1, 1)
FEATURE_NAMES = (
    'phase', 'unknown_fraction', 'own_pieces', 'own_pawns', 'visible_enemies',
    'king_move', 'pawn_move', 'capture', 'en_passant', 'castle', 'promotion',
    'king_advance', 'king_file_center', 'king_rank_center',
    'adjacent_own', 'adjacent_pawns', 'ray_own_shields', 'shield_change',
    'rook_hidden_ray', 'bishop_hidden_ray', 'rook_near_hidden', 'bishop_near_hidden',
    'hidden_knight_origins', 'hidden_pawn_origins', 'hidden_king_origins',
    'ray_exposure_change', 'pawn_potential', 'knight_potential',
    'bishop_potential', 'rook_potential', 'queen_potential', 'promotion_potential',
    'rook_ray_inventory', 'bishop_ray_inventory', 'knight_inventory', 'pawn_inventory',
    'kingmove_rook_ray', 'kingmove_bishop_ray', 'kingmove_knight', 'kingmove_pawn',
    'seen_rook_threat', 'seen_bishop_threat', 'seen_knight_threat',
    'seen_pawn_threat', 'seen_king_threat', 'recent_hidden_sightings',
    'forced_blocker_threat', 'captures_pawn', 'captures_knight', 'captures_bishop',
    'captures_rook', 'captures_queen', 'victim_value', 'visible_attack',
)


def _signature(board):
    return (board.pawns, board.knights, board.bishops, board.rooks,
            board.queens, board.kings, board.occupied_co[0], board.occupied_co[1])


def capture_square(before, move):
    """The observed victim square, including en-passant's displaced victim."""
    if before.is_en_passant(move):
        return move.to_square + (-8 if before.turn else 8)
    return move.to_square


class RiskHistory:
    """Independent prefix state; inventory counts only known own captures."""

    def __init__(self, side):
        self.side = bool(side)
        self.ply = -1
        self.current = None
        self.captures = [0] * 7
        self.sightings = {}
        self._signature = None
        self._pending = False
        self._blockers = ()

    def observe(self, view):
        if (not isinstance(view, str) or len(view) != 64
                or not set(view) <= set('?-PNBRQKpnbrqk')):
            raise ValueError('RiskHistory requires a 64-square observation')
        self.ply += 1
        self.current = view
        self._pending = False
        for square, (_, seen) in list(self.sightings.items()):
            if self.ply - seen > MEMORY_PLIES or view[square] != '?':
                del self.sightings[square]
        observed = chess.Board(None)
        blockers = []
        ours = 'P' if self.side else 'p'
        for square, symbol in enumerate(view):
            if symbol not in '?-':
                piece = chess.Piece.from_symbol(symbol)
                observed.set_piece_at(square, piece)
                if piece.color != self.side:
                    self.sightings[square] = (symbol, self.ply)
            if symbol == ours:
                front = square + (8 if self.side else -8)
                if 0 <= front < 64 and view[front] == '?':
                    blockers.append(front)
        self._signature = _signature(observed)
        self._blockers = tuple(blockers)

    def validate_before(self, before):
        if self.current is None or self._signature != _signature(before):
            raise ValueError('Risk input board differs from observed pieces')
        if before.turn != self.side:
            raise ValueError('Risk prediction must be for our own turn')

    def record_move(self, before, move):
        self.validate_before(before)
        if self._pending:
            raise ValueError('Observe after committing a move before recording another')
        mover = before.piece_at(move.from_square)
        if mover is None or mover.color != self.side:
            raise ValueError('Expected a known own mover')
        victim_square = capture_square(before, move)
        victim = before.piece_at(victim_square)
        if victim is not None and victim.color != self.side:
            self.captures[victim.piece_type] += 1
        self.sightings.pop(victim_square, None)
        self.sightings.pop(move.to_square, None)
        self._pending = True


def _geometric_attack(kind, side, origin, target, occupied):
    if origin == target:
        return False
    if kind == chess.PAWN:
        return bool(chess.BB_PAWN_ATTACKS[side][origin] & chess.BB_SQUARES[target])
    if kind == chess.KNIGHT:
        return bool(chess.BB_KNIGHT_ATTACKS[origin] & chess.BB_SQUARES[target])
    if kind == chess.KING:
        return bool(chess.BB_KING_ATTACKS[origin] & chess.BB_SQUARES[target])
    df = abs(chess.square_file(origin) - chess.square_file(target))
    dr = abs(chess.square_rank(origin) - chess.square_rank(target))
    aligned = ((kind in (chess.BISHOP, chess.QUEEN) and df == dr)
               or (kind in (chess.ROOK, chess.QUEEN) and (df == 0 or dr == 0)))
    return bool(aligned and not (chess.between(origin, target) & occupied))


def _rays(board, king, hidden, side):
    rook = bishop = rook_near = bishop_near = shields = 0.0
    kf, kr = chess.square_file(king), chess.square_rank(king)
    for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (1, -1), (-1, 1), (-1, -1)):
        f, r, distance = kf + df, kr + dr, 1
        while 0 <= f < 8 and 0 <= r < 8:
            square = chess.square(f, r)
            bit = chess.BB_SQUARES[square]
            if board.occupied & bit:
                shields += bool(board.occupied_co[side] & bit)
                break
            if hidden & bit:
                if df and dr:
                    bishop += 1.0 / distance
                    bishop_near += distance <= 2
                else:
                    rook += 1.0 / distance
                    rook_near += distance <= 2
            f, r, distance = f + df, r + dr, distance + 1
    return rook, bishop, rook_near, bishop_near, shields


def features(state, before, after, move):
    """Observation-only feature tuple; reject truth-expanded or stale inputs."""
    history = state.risk_history
    side = bool(state.side)
    if side != history.side or state.tracker.current != history.current:
        raise ValueError('Risk history is missing, stale or belongs to the other side')
    history.validate_before(before)
    if history._pending:
        raise ValueError('Features must be computed before committing our move')
    mover = before.piece_at(move.from_square)
    if mover is None or mover.color != side:
        raise ValueError('Expected an observed own mover')
    projected = before.copy(stack=False)
    projected.push(move)
    if _signature(after) != _signature(projected) or after.turn != projected.turn:
        raise ValueError('Risk after board must be the observation-only move projection')
    king, old_king = after.king(side), before.king(side)
    if king is None or old_king is None:
        raise ValueError('Risk features require an observed own king')
    view = history.current
    victim_square = capture_square(before, move)
    victim = before.piece_at(victim_square)
    captured = victim is not None and victim.color != side
    hidden = sum(chess.BB_SQUARES[s] for s, symbol in enumerate(view) if symbol == '?')
    hidden &= ~after.occupied & ~chess.BB_SQUARES[victim_square]
    rr, br, rn, bn, shields = _rays(after, king, hidden, side)
    old_rr, old_br, _, _, old_shields = _rays(before, old_king, hidden, side)
    knight = chess.popcount(chess.BB_KNIGHT_ATTACKS[king] & hidden)
    pawn = chess.popcount(chess.BB_PAWN_ATTACKS[side][king] & hidden)
    near_king = chess.popcount(chess.BB_KING_ATTACKS[king] & hidden)
    visible = [0] * 7
    for square in chess.scan_forward(after.occupied_co[not side]):
        visible[after.piece_type_at(square)] += 1
    # This is a soft feature, not a proof of hidden inventory. Captured promoted
    # pieces are not distinguishable from originals; promotion allowance remains.
    caps = list(history.captures)
    if captured:
        caps[victim.piece_type] += 1
    pawn_budget = max(0, 8 - caps[chess.PAWN]) / 8
    promotion = min(1.0, max(0.0, (history.ply - 10) / 50)) * pawn_budget
    potential = [0.0] * 7
    for kind in range(chess.PAWN, chess.KING + 1):
        initial = INITIAL_COUNTS[kind]
        potential[kind] = max(0, initial - caps[kind] - visible[kind]) / initial
        if kind in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            potential[kind] += 0.25 * promotion
    seen = [0.0] * 7
    remembered = 0.0
    for square, (symbol, seen_ply) in history.sightings.items():
        age = history.ply - seen_ply
        if not (0 < age <= MEMORY_PLIES) or not (hidden & chess.BB_SQUARES[square]):
            continue
        decay = (MEMORY_PLIES + 1 - age) / (MEMORY_PLIES + 1)
        remembered += decay
        piece = chess.Piece.from_symbol(symbol)
        if _geometric_attack(piece.piece_type, piece.color, square, king, after.occupied):
            seen[piece.piece_type] += decay
    blocker_threat = 0.0
    for square in history._blockers:
        if square == victim_square or after.occupied & chess.BB_SQUARES[square]:
            continue
        for kind in range(chess.PAWN, chess.KING + 1):
            if _geometric_attack(kind, not side, square, king, after.occupied):
                blocker_threat += potential[kind]
    kingmove = float(mover.piece_type == chess.KING)
    ring = chess.BB_KING_ATTACKS[king]
    own = after.occupied_co[side]
    kf, kr = chess.square_file(king), chess.square_rank(king)
    data = {
        'phase': min(history.ply / 80, 2.0),
        'unknown_fraction': view.count('?') / 64,
        'own_pieces': chess.popcount(own) / 16,
        'own_pawns': chess.popcount(own & after.pawns) / 8,
        'visible_enemies': sum(visible) / 16,
        'king_move': kingmove, 'pawn_move': float(mover.piece_type == chess.PAWN),
        'capture': float(captured), 'en_passant': float(before.is_en_passant(move)),
        'castle': float(before.is_castling(move)), 'promotion': float(bool(move.promotion)),
        'king_advance': (kr if side else 7 - kr) / 7,
        'king_file_center': (3.5 - abs(kf - 3.5)) / 3.5,
        'king_rank_center': (3.5 - abs(kr - 3.5)) / 3.5,
        'adjacent_own': chess.popcount(ring & own) / 8,
        'adjacent_pawns': chess.popcount(ring & own & after.pawns) / 8,
        'ray_own_shields': shields / 8, 'shield_change': (shields - old_shields) / 8,
        'rook_hidden_ray': rr / 8, 'bishop_hidden_ray': br / 8,
        'rook_near_hidden': rn / 8, 'bishop_near_hidden': bn / 8,
        'hidden_knight_origins': knight / 8, 'hidden_pawn_origins': pawn / 2,
        'hidden_king_origins': near_king / 8,
        'ray_exposure_change': (rr + br - old_rr - old_br) / 16,
        'pawn_potential': potential[chess.PAWN], 'knight_potential': potential[chess.KNIGHT],
        'bishop_potential': potential[chess.BISHOP], 'rook_potential': potential[chess.ROOK],
        'queen_potential': potential[chess.QUEEN], 'promotion_potential': promotion,
        'rook_ray_inventory': rr * (potential[chess.ROOK] + potential[chess.QUEEN]) / 16,
        'bishop_ray_inventory': br * (potential[chess.BISHOP] + potential[chess.QUEEN]) / 16,
        'knight_inventory': knight * potential[chess.KNIGHT] / 8,
        'pawn_inventory': pawn * potential[chess.PAWN] / 2,
        'kingmove_rook_ray': kingmove * rr / 8, 'kingmove_bishop_ray': kingmove * br / 8,
        'kingmove_knight': kingmove * knight / 8, 'kingmove_pawn': kingmove * pawn / 2,
        'seen_rook_threat': (seen[chess.ROOK] + seen[chess.QUEEN]) / 4,
        'seen_bishop_threat': (seen[chess.BISHOP] + seen[chess.QUEEN]) / 4,
        'seen_knight_threat': seen[chess.KNIGHT] / 2, 'seen_pawn_threat': seen[chess.PAWN] / 2,
        'seen_king_threat': seen[chess.KING], 'recent_hidden_sightings': remembered / 16,
        'forced_blocker_threat': blocker_threat / 8,
        'captures_pawn': caps[chess.PAWN] / 8, 'captures_knight': caps[chess.KNIGHT] / 2,
        'captures_bishop': caps[chess.BISHOP] / 2, 'captures_rook': caps[chess.ROOK] / 2,
        'captures_queen': caps[chess.QUEEN],
        'victim_value': (0, 1, 3, 3.25, 5, 9, 20)[victim.piece_type] / 9 if captured else 0.0,
        'visible_attack': float(after.is_attacked_by(not side, king)),
    }
    return tuple(float(data[name]) for name in FEATURE_NAMES)


def geometric_score(vector):
    """Fixed, bank-independent baseline; higher means more exposed geometry."""
    f = dict(zip(FEATURE_NAMES, vector))
    return (1.8 * 8 * (f['rook_hidden_ray'] + f['bishop_hidden_ray'])
            + 0.9 * 8 * f['hidden_knight_origins'] + 1.2 * 2 * f['hidden_pawn_origins']
            + 7 * (4 * f['seen_rook_threat'] + 4 * f['seen_bishop_threat']
                   + 2 * f['seen_knight_threat'] + 2 * f['seen_pawn_threat']
                   + f['seen_king_threat']))


class RiskModel:
    def __init__(self, document):
        if (document.get('schema_version') != SCHEMA_VERSION
                or document.get('feature_version') != FEATURE_VERSION
                or document.get('feature_names') != list(FEATURE_NAMES)):
            raise ValueError('Unsupported risk model schema or features')
        self.mean = self._numbers(document.get('mean'), 'mean')
        self.scale = self._numbers(document.get('scale'), 'scale')
        self.coefficients = self._numbers(document.get('coefficients'), 'coefficients')
        self.intercept = document.get('intercept')
        self.clip = document.get('normalized_clip')
        if (type(self.intercept) not in (float, int) or not math.isfinite(self.intercept)
                or type(self.clip) not in (float, int) or not math.isfinite(self.clip)
                or abs(self.intercept) > 1e6 or self.clip != 8.0
                or any(not 1e-12 <= x <= 1e6 for x in self.scale)
                or any(abs(x) > 1e6 for x in self.mean + self.coefficients)
                or document.get('probability_clip') != EPSILON):
            raise ValueError('Invalid risk model normalization or intercept')

    @staticmethod
    def _numbers(values, name):
        if (not isinstance(values, list) or len(values) != len(FEATURE_NAMES)
                or any(type(x) not in (float, int) or not math.isfinite(x) for x in values)):
            raise ValueError('Invalid risk model ' + name)
        return tuple(float(x) for x in values)

    @classmethod
    def load(cls, path=None):
        source = Path(path) if path is not None else Path(__file__).with_name('risk_model.json')
        return cls(json.loads(source.read_text(encoding='utf-8')))

    def predict_features(self, vector):
        if len(vector) != len(FEATURE_NAMES) or any(not math.isfinite(x) for x in vector):
            raise ValueError('Invalid risk feature vector')
        score = self.intercept + sum(
            coefficient * max(-self.clip, min(self.clip, (value - mean) / scale))
            for value, mean, scale, coefficient in zip(vector, self.mean, self.scale, self.coefficients))
        probability = (1 / (1 + math.exp(-min(score, 700))) if score >= 0
                       else math.exp(max(score, -700)) / (1 + math.exp(max(score, -700))))
        return min(1 - EPSILON, max(EPSILON, probability))


@lru_cache(maxsize=1)
def default_model():
    return RiskModel.load()


def predict(state, before, after, move, model=None):
    """Return empirical immediate-loss probability, strictly between zero and one."""
    return (default_model() if model is None else model).predict_features(
        features(state, before, after, move))
