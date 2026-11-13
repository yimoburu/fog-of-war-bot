"""Bounded FOW tactical search over sampled hypotheses, never a truth oracle.

Future branches use determinization, an approximation with strategy fusion;
the root action is shared across every sampled world. No rating is implied.
"""
import math
import time
from collections import Counter

import chess
from belief import repetition_key

VALUES = (0, 100, 320, 335, 510, 950, 0)
MATE = 100000


class SearchExpired(Exception):
    pass


def piece_square(kind, square, side, endgame):
    file = chess.square_file(square)
    rank = chess.square_rank(square) if side else 7 - chess.square_rank(square)
    center = 7 - abs(2 * file - 7) - abs(2 * rank - 7)
    if kind == chess.PAWN:
        return (rank * rank * 3 + (3 - abs(file - 3.5)) * 5
                - (12 if rank == 1 and file in (3, 4) else 0))
    if kind == chess.KNIGHT:
        return 7 * center - (18 if rank == 0 else 0)
    if kind == chess.BISHOP:
        return 3 * center + (14 if rank > 0 else -10)
    if kind == chess.ROOK:
        return (24 if rank == 6 else 0) + 2 * rank
    if kind == chess.QUEEN:
        return center
    if endgame:
        return 5 * center
    return -8 * rank + (22 if file in (1, 2, 6) and rank == 0 else 0)


def evaluate(board):
    """Centipawn heuristic from side-to-move, on a hypothetical full board."""
    heavy = (board.queens.bit_count() * 950 + board.rooks.bit_count() * 510 +
             (board.knights | board.bishops).bit_count() * 325)
    endgame = heavy < 2600
    score = 0.0
    for side, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        own = board.occupied_co[side]
        for kind in range(1, 7):
            pieces = board.pieces_mask(kind, side)
            for square in chess.scan_forward(pieces):
                score += sign * (VALUES[kind] + piece_square(kind, square, side, endgame))
        if (board.bishops & own).bit_count() >= 2:
            score += sign * 24
        pawns = board.pawns & own
        enemy_pawns = board.pawns & board.occupied_co[not side]
        for file in range(8):
            column = pawns & chess.BB_FILES[file]
            count = column.bit_count()
            if count > 1:
                score -= sign * 14 * (count - 1)
            if not count:
                continue
            neighbors = ((chess.BB_FILES[file - 1] if file else 0) |
                         (chess.BB_FILES[file + 1] if file < 7 else 0))
            if not pawns & neighbors:
                score -= sign * 10 * count
            for square in chess.scan_forward(column):
                rank = chess.square_rank(square)
                ahead = ((chess.BB_ALL << ((rank + 1) * 8)) & chess.BB_ALL
                         if side else (1 << (rank * 8)) - 1)
                if not enemy_pawns & ahead & (neighbors | chess.BB_FILES[file]):
                    progress = rank if side else 7 - rank
                    score += sign * (8 + progress * progress * 3)
        king = board.king(side)
        if king is not None:
            ring = chess.BB_KING_ATTACKS[king]
            shield = (ring & pawns).bit_count()
            score += sign * shield * (4 if endgame else 12)
            if board.is_attacked_by(not side, king):
                # A threat gives the opponent one reply, not an instant victory.
                score -= sign * 65
    return score if board.turn else -score


def order_score(board, move, preferred=None):
    if move == preferred:
        return 1000000
    mover = board.piece_type_at(move.from_square)
    captured = board.piece_type_at(move.to_square)
    score = 0
    if captured:
        score += 10 * VALUES[captured] - VALUES[mover]
    elif board.is_en_passant(move):
        score += 900
    if move.promotion:
        score += 10 * VALUES[move.promotion]
    if board.is_castling(move):
        score += 80
    return score


class Search:
    def __init__(self, deadline, now=time.monotonic, node_limit=30000, complete=True):
        self.deadline = deadline
        self.now = now
        self.node_limit = node_limit
        self.complete = complete
        self.nodes = 0
        self.table = {}
        self.repetitions = None

    def bind_history(self, board):
        saved = getattr(board, 'fow_repetitions', None)
        if saved is not None:
            self.repetitions = Counter(saved)
            return
        self.repetitions = Counter({repetition_key(board): 1})
        if board.move_stack:
            previous = board.copy(stack=True)
            while previous.move_stack:
                previous.pop()
                self.repetitions[repetition_key(previous)] += 1

    def push(self, board, move):
        board.push(move)
        self.repetitions[repetition_key(board)] += 1

    def pop(self, board):
        key = repetition_key(board)
        self.repetitions[key] -= 1
        if not self.repetitions[key]:
            del self.repetitions[key]
        board.pop()

    def check(self):
        self.nodes += 1
        if self.nodes >= self.node_limit or self.nodes % 32 == 0 and self.now() >= self.deadline:
            raise SearchExpired

    def terminal(self, board, ply):
        if self.repetitions is None:
            self.bind_history(board)
        previous_king = board.king(not board.turn)
        if previous_king is None:
            if self.complete:
                return MATE - ply
        elif board.is_attacked_by(board.turn, previous_king):
            return MATE - ply
        if board.king(board.turn) is None and self.complete:
            return -MATE + ply
        if self.repetitions[repetition_key(board)] >= 3 or board.halfmove_clock >= 100:
            return 0
        return None

    def negamax(self, board, depth, alpha=-MATE, beta=MATE, ply=0, qleft=3):
        self.check()
        terminal = self.terminal(board, ply)
        if terminal is not None:
            return terminal
        if depth <= 0:
            return self.quiescence(board, alpha, beta, ply, qleft)
        original_alpha = alpha
        # Include halfmove clock; FOW EP and castling differ from orthodox keys.
        key = (board.pawns, board.knights, board.bishops, board.rooks,
               board.queens, board.kings, board.occupied_co[0],
               board.occupied_co[1], board.turn, board.castling_rights,
               board.ep_square, board.halfmove_clock, depth, ply,
               frozenset(self.repetitions.items()))
        # Even once-visited positions can affect descendant draw outcomes.
        # Bind the complete counter, including across sampled worlds.
        saved = self.table.get(key)
        if saved:
            score, flag, best = saved
            if flag == 'exact' or flag == 'lower' and score >= beta or flag == 'upper' and score <= alpha:
                return score
        else:
            best = None
        moves = sorted(board.generate_pseudo_legal_moves(),
                       key=lambda m: order_score(board, m, best), reverse=True)
        best_move = None
        value = -MATE + ply
        legal = False
        mover = board.turn
        for move in moves:
            self.push(board, move)
            try:
                king = board.king(mover)
                if king is None and self.complete or king is not None and board.is_attacked_by(board.turn, king):
                    continue
                legal = True
                score = -self.negamax(board, depth - 1, -beta, -alpha, ply + 1, qleft)
            finally:
                self.pop(board)
            if score > value:
                value, best_move = score, move
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        if not legal:
            return -MATE + ply if self.complete else evaluate(board) - 150
        flag = 'upper' if value <= original_alpha else 'lower' if value >= beta else 'exact'
        if len(self.table) < 15000:
            self.table[key] = (value, flag, best_move)
        return value

    def quiescence(self, board, alpha, beta, ply, left):
        self.check()
        terminal = self.terminal(board, ply)
        if terminal is not None:
            return terminal
        king = board.king(board.turn)
        in_danger = king is not None and board.is_attacked_by(not board.turn, king)
        stand = evaluate(board)
        all_moves = list(board.generate_pseudo_legal_moves())
        if self.complete:
            # Stand-pat is invalid if every move loses, even without check.
            # This matters in FOW: there is no stalemate draw.
            has_surviving_move = False
            mover = board.turn
            for move in all_moves:
                self.check()
                self.push(board, move)
                try:
                    own_king = board.king(mover)
                    has_surviving_move = (own_king is not None and
                                          not board.is_attacked_by(board.turn, own_king))
                finally:
                    self.pop(board)
                if has_surviving_move:
                    break
            if not has_surviving_move:
                return -MATE + ply
        if left <= 0:
            return stand
        if not in_danger:
            if stand >= beta:
                return stand
            alpha = max(alpha, stand)
        value = -MATE + ply if in_danger else stand
        if not all_moves:
            return -MATE + ply if self.complete else stand
        moves = all_moves if in_danger else [m for m in all_moves if board.is_capture(m) or m.promotion]
        moves.sort(key=lambda m: order_score(board, m), reverse=True)
        mover, safe = board.turn, False
        for move in moves:
            self.push(board, move)
            try:
                king = board.king(mover)
                if king is None and self.complete or king is not None and board.is_attacked_by(board.turn, king):
                    continue
                safe = True
                score = -self.quiescence(board, -beta, -alpha, ply + 1, left - 1)
            finally:
                self.pop(board)
            value = max(value, score)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        if in_danger and not safe:
            return -MATE + ply if self.complete else stand - 150
        return value


def search_root(worlds, moves, priors, deadline, now=time.monotonic,
                complete=True, max_depth=3, node_limit=18000):
    """Only publish a completed common-depth/common-world batch.

    Priors already contain root safety/uncertainty penalties. All actions are
    compared on the same hypotheses; an unfinished batch never biases choice
    toward actions earlier in the move ordering.
    """
    if not moves or not worlds:
        return None, {'depth': 0, 'nodes': 0}
    engine = Search(deadline, now, node_limit, complete)
    scores = dict(priors)
    best = max(moves, key=lambda m: scores[m])
    completed = 0
    order = sorted(moves, key=lambda m: scores[m], reverse=True)
    try:
        for depth in range(1, max_depth + 1):
            batch = {}
            for move in order:
                if now() >= deadline:
                    raise SearchExpired
                values = []
                for world in worlds:
                    engine.bind_history(world)
                    engine.push(world, move)
                    try:
                        values.append(-engine.negamax(world, depth - 1, ply=1))
                    finally:
                        engine.pop(world)
                mean = sum(values) / len(values)
                # Conservative tail term softens optimistic determinization.
                tail = min(values)
                batch[move] = .8 * mean + .2 * tail + priors[move]
            scores = batch
            best = max(order, key=lambda m: scores[m])
            completed = depth
            order.sort(key=lambda m: scores[m], reverse=True)
    except SearchExpired:
        pass
    return best, {'depth': completed, 'nodes': engine.nodes,
                  'worlds': len(worlds), 'score': scores[best]}
