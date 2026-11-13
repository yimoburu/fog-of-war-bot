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


# These tables retain exactly the public piece_square formula and pawn masks;
# evaluation no longer rebuilds board-independent geometry at every leaf.
PST = tuple(tuple(tuple(tuple(piece_square(kind, square, side, endgame)
                              for square in chess.SQUARES)
                        for kind in range(7))
                  for side in (False, True))
            for endgame in (False, True))
FILE_NEIGHBORS = tuple((chess.BB_FILES[file - 1] if file else 0) |
                       (chess.BB_FILES[file + 1] if file < 7 else 0)
                       for file in range(8))
PASSED_MASKS = tuple(tuple(
    (((chess.BB_ALL << ((chess.square_rank(square) + 1) * 8)) & chess.BB_ALL)
     if side else (1 << (chess.square_rank(square) * 8)) - 1) &
    (FILE_NEIGHBORS[chess.square_file(square)] | chess.BB_FILES[chess.square_file(square)])
    for square in chess.SQUARES) for side in (False, True))
PASSED_BONUS = tuple(tuple(8 + (chess.square_rank(square) if side else
                               7 - chess.square_rank(square)) ** 2 * 3
                          for square in chess.SQUARES) for side in (False, True))


def evaluate(board):
    """Centipawn heuristic from side-to-move, on a hypothetical full board."""
    heavy = (board.queens.bit_count() * 950 + board.rooks.bit_count() * 510 +
             (board.knights | board.bishops).bit_count() * 325)
    endgame = heavy < 2600
    score = 0.0
    for side, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        own = board.occupied_co[side]
        tables = PST[endgame][side]
        for kind in range(1, 7):
            pieces = board.pieces_mask(kind, side)
            table, material = tables[kind], VALUES[kind]
            for square in chess.scan_forward(pieces):
                score += sign * (material + table[square])
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
            neighbors = FILE_NEIGHBORS[file]
            if not pawns & neighbors:
                score -= sign * 10 * count
            for square in chess.scan_forward(column):
                if not enemy_pawns & PASSED_MASKS[side][square]:
                    score += sign * PASSED_BONUS[side][square]
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
        # Quiescence performs its own deadline and terminal check. Dispatch
        # leaves directly rather than doing both checks twice at every leaf.
        if depth <= 0:
            return self.quiescence(board, alpha, beta, ply, qleft)
        self.check()
        terminal = self.terminal(board, ply)
        if terminal is not None:
            return terminal
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
        if self.complete:
            # Stand-pat is invalid if every move loses, even without check.
            # This matters in FOW: there is no stalemate draw.
            has_surviving_move = False
            mover = board.turn
            for move in board.generate_pseudo_legal_moves():
                self.check()
                # This probe checks only the mover's king, then restores the
                # board. It neither searches nor consults repetition counts.
                board.push(move)
                try:
                    own_king = board.king(mover)
                    has_surviving_move = (own_king is not None and
                                          not board.is_attacked_by(board.turn, own_king))
                finally:
                    board.pop()
                if has_surviving_move:
                    break
            if not has_surviving_move:
                return -MATE + ply
        stand = evaluate(board)
        if left <= 0:
            return stand
        if not in_danger:
            if stand >= beta:
                return stand
            alpha = max(alpha, stand)
        value = -MATE + ply if in_danger else stand
        if in_danger:
            moves = list(board.generate_pseudo_legal_moves())
            if not moves:
                return -MATE + ply if self.complete else stand
        else:
            moves = list(board.generate_pseudo_legal_captures())
            # Capture promotions are already included. Generate only pawn
            # destinations on an empty promotion rank for the quiet choices.
            promotion_rank = chess.BB_RANK_8 if board.turn else chess.BB_RANK_1
            penultimate = chess.BB_RANK_7 if board.turn else chess.BB_RANK_2
            pawns = board.pawns & board.occupied_co[board.turn] & penultimate
            if pawns:
                moves.extend(board.generate_pseudo_legal_moves(
                    from_mask=pawns, to_mask=promotion_rank & ~board.occupied))
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
                complete=True, max_depth=3, node_limit=18000,
                utility_scale=None, immediate_risks=None):
    """Only publish a completed common-depth/common-world batch.

    Default priors contain root safety/uncertainty penalties. With bounded
    utility, immediate loss is counted once using the larger belief bank.
    An unfinished common-depth batch never replaces a completed result.
    """
    if not moves or not worlds:
        return None, {'depth': 0, 'nodes': 0}
    if utility_scale is not None and (not math.isfinite(utility_scale) or utility_scale <= 0):
        raise ValueError('Utility scale must be finite and positive')
    if immediate_risks is not None and (utility_scale is None or any(
            move not in immediate_risks or not math.isfinite(immediate_risks[move])
            or not 0 <= immediate_risks[move] <= 1 for move in moves)):
        raise ValueError('Immediate risks require bounded utility and fractions in [0,1]')
    engine = Search(deadline, now, node_limit, complete)
    scores = dict(priors)
    def rank(move):
        # Heuristic priors cannot prefer certain modeled death over a move
        # with surviving hypotheses, even when continuation values saturate.
        survives = immediate_risks is None or immediate_risks[move] < 1
        return survives, scores[move]
    best = max(moves, key=rank)
    completed = 0
    order = sorted(moves, key=rank, reverse=True)
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
                        mover = not world.turn
                        king = world.king(mover)
                        immediate_loss = (king is None or world.is_attacked_by(world.turn, king))
                        if immediate_risks is not None and immediate_loss:
                            # Root death was measured on the larger common bank.
                            # Continue only surviving worlds, so it is counted
                            # once rather than again through a tiny search sample.
                            continue
                        value = -engine.negamax(world, depth - 1, ply=1)
                        values.append(value if utility_scale is None else
                                      utility_scale * math.tanh(value / utility_scale))
                    finally:
                        engine.pop(world)
                if not values:
                    # No sampled continuation supports survival. Do not invent
                    # its value even when other bank members survived.
                    values = [-utility_scale]
                mean = sum(values) / len(values)
                # Conservative tail term softens optimistic determinization.
                tail = min(values)
                continuation = .8 * mean + .2 * tail
                if immediate_risks is not None:
                    risk = immediate_risks[move]
                    continuation = (1 - risk) * continuation - risk * utility_scale
                batch[move] = continuation + priors[move]
            scores = batch
            best = max(order, key=rank)
            completed = depth
            order.sort(key=rank, reverse=True)
    except SearchExpired:
        pass
    return best, {'depth': completed, 'nodes': engine.nodes,
                  'worlds': len(worlds), 'score': scores[best]}
