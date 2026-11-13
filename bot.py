#!/usr/bin/env python3
"""Observation-only FOW belief search; strength must be measured in the arena."""
import argparse
import math
import random
import sys
import time

import chess
from belief import Belief, forced_blockers
from search import search_root
from uniform_random import OBSERVATION_CHARACTERS, ObservationTracker, candidates

VALUES = {chess.PAWN: 100, chess.KNIGHT: 310, chess.BISHOP: 325,
          chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 20000}
MEMORY_PLIES = 12
MAX_DETAIL_MOVES = 64
MAX_DETAIL_SECONDS = 0.12


class State:
    def __init__(self, side, rights=None, seed=0, search_ms=180, risk_policy='hypothesis'):
        self.side = side
        self.tracker = ObservationTracker(side, rights)
        self.ply = -1
        self.sightings = {}
        self.belief = Belief(side, seed=seed)
        self.belief_events = []
        self.search_ms = search_ms
        if risk_policy not in ('legacy', 'hypothesis', 'robust'):
            raise ValueError('Unknown risk policy')
        self.risk_policy = risk_policy
        self.last_search = {}
        self.move_history = []

    def observe(self, view):
        if len(view) != 64 or not set(view) <= OBSERVATION_CHARACTERS:
            raise ValueError('Expected a 64-square FOW observation')
        self.tracker.observe(view)
        # Arena observations may be queued while the process is frozen. Defer
        # optional inference until the following clock establishes its budget.
        self.belief_events.append(('observe', view))
        self.ply += 1
        for square, (_, seen) in list(self.sightings.items()):
            if self.ply - seen > MEMORY_PLIES or view[square] != '?':
                del self.sightings[square]
        for square, symbol in enumerate(view):
            if symbol not in '?-' and symbol.isupper() != self.side:
                self.sightings[square] = (symbol, self.ply)

    def reconstruct(self):
        tracker = self.tracker
        if tracker.current is None:
            raise ValueError('No observation received')
        return candidates(tracker.current, self.side, tracker.rights,
                          tracker.previous)

    def record_move(self, board, move):
        self.sightings.pop(capture_square(board, move), None)
        self.sightings.pop(move.to_square, None)
        self.tracker.record_move(move)
        self.belief_events.append(('move', move))
        self.move_history.append(move)
        self.move_history = self.move_history[-12:]


def capture_square(board, move):
    if board.is_en_passant(move):
        return move.to_square + (-8 if board.turn else 8)
    return move.to_square


def after_move(board, move):
    result = board.copy(stack=False)
    result.push(move)  # Includes EP removal, rook relocation and promotion.
    return result


def visibly_safe(board, side):
    # Unknown blockers can make a visible slider's apparent attack spurious.
    king = board.king(side)
    return king is not None and not board.is_attacked_by(not side, king)


def geometric_attack(piece_type, color, origin, target, board):
    dx = chess.square_file(target) - chess.square_file(origin)
    dy = chess.square_rank(target) - chess.square_rank(origin)
    if origin == target:
        return False
    if piece_type == chess.PAWN:
        return abs(dx) == 1 and dy == (1 if color else -1)
    if piece_type == chess.KNIGHT:
        return (abs(dx), abs(dy)) in ((1, 2), (2, 1))
    if piece_type == chess.KING:
        return max(abs(dx), abs(dy)) == 1
    diagonal, straight = abs(dx) == abs(dy), dx == 0 or dy == 0
    aligned = ((piece_type == chess.BISHOP and diagonal) or
               (piece_type == chess.ROOK and straight) or
               (piece_type == chess.QUEEN and (diagonal or straight)))
    return bool(aligned and not (chess.between(origin, target) & board.occupied))


def hidden_risk(state, after, captured_square):
    view, side = state.tracker.current, state.side
    king = after.king(side)
    if king is None:
        return 10000.0
    risk = 0.0
    kf, kr = chess.square_file(king), chess.square_rank(king)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (1, -1), (-1, 1), (-1, -1)):
        f, r, distance = kf + dx, kr + dy, 1
        while 0 <= f < 8 and 0 <= r < 8:
            square = chess.square(f, r)
            if after.piece_at(square):
                break
            if view[square] == '?' and square != captured_square:
                risk += 1.8 / distance
            f, r, distance = f + dx, r + dy, distance + 1
    for square in chess.SquareSet(chess.BB_KNIGHT_ATTACKS[king]):
        if view[square] == '?' and after.piece_at(square) is None:
            risk += 0.9
    pawn_rank = kr + (1 if side else -1)
    if 0 <= pawn_rank < 8:
        for f in (kf - 1, kf + 1):
            if 0 <= f < 8:
                square = chess.square(f, pawn_rank)
                if view[square] == '?' and after.piece_at(square) is None:
                    risk += 1.2
    for square, (symbol, seen) in state.sightings.items():
        if (view[square] != '?' or square == captured_square or
                after.piece_at(square) is not None):
            continue
        age = state.ply - seen
        if 0 < age <= MEMORY_PLIES:
            piece = chess.Piece.from_symbol(symbol)
            if geometric_attack(piece.piece_type, piece.color, square, king, after):
                risk += 7 * (1 - age / (MEMORY_PLIES + 1))
    return risk


def quick_score(board, after, move, side):
    victim = board.piece_at(capture_square(board, move))
    score = VALUES[victim.piece_type] if victim else 0
    if move.promotion:
        score += VALUES[move.promotion] - VALUES[chess.PAWN]
    moved = after.piece_at(move.to_square)
    if (moved and moved.piece_type != chess.KING and
            after.is_attacked_by(not side, move.to_square)):
        defended = after.is_attacked_by(side, move.to_square)
        score -= VALUES[moved.piece_type] * (0.70 if defended else 0.95)
    return score + (24 if board.is_castling(move) else 0)


def detail_score(state, before, after, move):
    side = state.side
    score = -32 * hidden_risk(state, after, capture_square(before, move))
    piece = before.piece_at(move.from_square)
    home = 0 if side else 7
    rank = chess.square_rank(move.to_square)
    progress = rank if side else 7 - rank
    center = 3.5 - abs(chess.square_file(move.to_square) - 3.5)
    if piece.piece_type in (chess.KNIGHT, chess.BISHOP):
        score += 7 * center
        if chess.square_rank(move.from_square) == home:
            score += 18
    elif piece.piece_type == chess.PAWN:
        score += 4 * center + 3 * min(progress, 5)
    enemy_king = after.king(not side)
    if enemy_king is not None and after.is_attacked_by(side, enemy_king):
        score += 18  # Pressure, not an immediate win.
    covered = 0
    for square in chess.SquareSet(after.occupied_co[side]):
        own = after.piece_at(square)
        covered |= int(after.attacks(square))
        if own.piece_type != chess.KING and square != move.to_square:
            if after.is_attacked_by(not side, square):
                defended = after.is_attacked_by(side, square)
                score -= VALUES[own.piece_type] * (0.05 if defended else 0.12)
    king = after.king(side)
    if king is not None:
        for square in chess.SquareSet(chess.BB_KING_ATTACKS[king]):
            cover = after.piece_at(square)
            if cover and cover.color == side:
                score += 6 if cover.piece_type == chess.PAWN else 2
    # Approximate information gain; attack coverage is not exact visibility.
    score += 0.8 * sum(state.tracker.current[square] == '?'
                       for square in chess.SquareSet(covered))
    return score


def blocker_risk(state, after, captured):
    """Penalty for a proven enemy blocker whose unknown type can kill us."""
    king = after.king(state.side)
    if king is None:
        return 10000
    risks = 0
    for square in forced_blockers(state.tracker.current, state.side):
        if square == captured or after.piece_at(square):
            continue
        for kind, weight in ((chess.PAWN, 8), (chess.KNIGHT, 2),
                             (chess.BISHOP, 2), (chess.ROOK, 2),
                             (chess.QUEEN, 1), (chess.KING, 1)):
            if geometric_attack(kind, not state.side, square, king, after):
                risks += weight
    return risks * 4000


def choose_move(state, remaining, rng, now=time.monotonic):
    if not math.isfinite(remaining) or remaining <= 0:
        raise ValueError('Clock must be finite and positive')
    state.last_search = {'depth': 0, 'nodes': 0, 'status': 'fallback'}
    started = now()
    reserve = max(0.01, min(0.5, remaining * 0.10))
    budget = min(max(0, state.search_ms) / 1000,
                 max(0, remaining - reserve), remaining * 0.035)
    deadline = started + budget
    board, moves = state.reconstruct()
    if not moves:
        return None
    ranked = []
    # Complete safety pass before any optional deadline checks.
    for move in sorted(moves, key=lambda item: item.uci()):
        after = after_move(board, move)
        ranked.append((visibly_safe(after, state.side),
                       quick_score(board, after, move, state.side) -
                       blocker_risk(state, after, capture_square(board, move)),
                       rng.random(), move, after))
    safe_exists = any(row[0] for row in ranked)
    pool = [row for row in ranked if row[0] == safe_exists]
    pool.sort(key=lambda row: (row[1], row[2]), reverse=True)
    fallback = pool[0][3]
    if budget <= 0 or now() >= deadline:
        return fallback
    detailed = []
    priors = {}
    for _, quick, tie, move, after in pool[:MAX_DETAIL_MOVES]:
        if now() >= deadline:
            return fallback
        detail = detail_score(state, board, after, move)
        # Penalize oscillation without inventing a full-board repetition count.
        repeat = 35 * sum(old == move for old in state.move_history[-8:])
        detailed.append((quick + detail - repeat, tie, move))
        priors[move] = (.3 * detail + .1 * quick - repeat + tie * .05
                        - .9 * blocker_risk(state, after, capture_square(board, move)))
    # Discard incomplete batches; never compare quick and detailed scores.
    if now() >= deadline:
        return fallback
    detailed.sort(key=lambda row: (row[0], row[1]), reverse=True)
    fallback = detailed[0][2]
    inference_deadline = min(deadline, started + budget * .35)
    while state.belief_events and now() < inference_deadline:
        kind, value = state.belief_events.pop(0)
        if kind == 'move':
            state.belief.record_move(value)
        else:
            # The production clock is monotonic; an injected test clock still
            # gets a real finite bound for the hypothesis tracker.
            real_deadline = time.monotonic() + max(0, inference_deadline - now())
            state.belief.observe(value, deadline=real_deadline)
    current_particles = state.belief.particles if not state.belief_events else []
    if current_particles and state.risk_policy != 'legacy':
        # A forced blocker is already represented in every current hypothesis.
        # Do not ALSO pretend it can be every starting piece type. The fallback
        # retains the generic penalty when the bank is absent or stale.
        revised = []
        for score, tie, move in detailed:
            if now() >= deadline:
                return fallback
            generic = blocker_risk(state, after_move(board, move),
                                   capture_square(board, move))
            revised.append((score + generic, tie, move))
            priors[move] += generic
        detailed = sorted(revised, key=lambda row: (row[0], row[1]), reverse=True)
        # Retain the conservative fallback until the whole survival batch is
        # complete; removing a generic penalty alone does not establish safety.
    worlds = state.belief.sample(4) if current_particles else []
    complete = bool(worlds)
    if not worlds:
        worlds = [board.copy(stack=False)]
    # Use every retained hypothesis for immediate survival classification, then
    # a common small subset for deeper search. The weights are heuristic.
    survival = {}
    for _, _, move in detailed:
        losses = 0
        for world in current_particles:
            if now() >= deadline:
                return fallback
            world.push(move)
            try:
                losses += not visibly_safe(world, state.side)
            finally:
                world.pop()
        survival[move] = losses / max(1, len(current_particles))
        if state.risk_policy != 'robust' or not current_particles:
            priors[move] -= 30000 * survival[move]
    # Keep twelve candidates for depth search, including low-risk quiet moves.
    risk_weight = 1600 if state.risk_policy == 'robust' and current_particles else 30000
    detailed.sort(key=lambda row: (row[0] - risk_weight * survival[row[2]], row[1]), reverse=True)
    if state.risk_policy == 'robust' and current_particles:
        survivors = [row for row in detailed if survival[row[2]] < 1]
        if survivors:
            detailed = survivors
    selected = [row[2] for row in detailed[:12]]
    fallback = selected[0]
    if now() >= deadline:
        return fallback
    options = ({'utility_scale': 800, 'immediate_risks': survival}
               if state.risk_policy == 'robust' and current_particles else {})
    chosen, stats = search_root(worlds, selected, priors, deadline, now=now,
                                complete=complete, max_depth=3, **options)
    state.last_search = stats
    state.last_search['status'] = 'searched' if stats['depth'] else 'fallback'
    return chosen if stats['depth'] else fallback


def positive_float(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Expected a finite positive number')
    return value


def serve(stream_in, stream_out, seed, search_ms=180, risk_policy='hypothesis'):
    if stream_in.readline().strip().lower() != 'fow':
        raise ValueError('Expected fow handshake')
    header = stream_in.readline().split()
    if len(header) != 3 or header[0].lower() not in ('w', 'b'):
        raise ValueError('Expected color, base time, increment')
    positive_float(header[1])
    increment = float(header[2])
    if not math.isfinite(increment) or increment < 0:
        raise ValueError('Invalid increment')
    state = State(header[0].lower() == 'w', seed=seed, search_ms=search_ms,
                  risk_policy=risk_policy)
    rng = random.Random(seed)
    stream_out.write('ready\n')
    stream_out.flush()
    awaiting = False
    for raw in stream_in:
        line = raw.strip()
        if len(line) == 64 and set(line) <= OBSERVATION_CHARACTERS:
            state.observe(line)
            awaiting = True
            continue
        remaining = positive_float(line)
        if not awaiting:
            raise ValueError('Clock requires a fresh observation')
        move = choose_move(state, remaining, rng)
        if move is None:
            return
        board, _ = state.reconstruct()
        state.record_move(board, move)
        awaiting = False
        stream_out.write('move ' + move.uci() + '\n')
        stream_out.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--search-ms', type=float, default=180,
                        help='Maximum optional search milliseconds per move (default180).')
    parser.add_argument('--risk-policy', choices=('legacy', 'hypothesis', 'robust'),
                        default='hypothesis', help='Risk aggregation policy for reproducible comparisons.')
    args = parser.parse_args()
    try:
        if not math.isfinite(args.search_ms) or not 0 <= args.search_ms <= 2000:
            raise ValueError('search-ms must be between0 and2000')
        serve(sys.stdin, sys.stdout, args.seed, args.search_ms, args.risk_policy)
    except (ValueError, EOFError) as error:
        print('bot: ' + str(error), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
