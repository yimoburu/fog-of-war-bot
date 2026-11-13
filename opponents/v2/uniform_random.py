#!/usr/bin/env python3
"""Observation-only, uniformly random Fog of War Chess starter.

Run with ``python3 -u uniform_random.py``. ``--seed INTEGER`` makes a run
repeatable; without it the generator is seeded from operating-system entropy.
The bot uses the documented stdin/stdout protocol, including the observation
sent before *each* player's move. It never receives a full board or an opponent
move. Each legal UCI move, including each promotion choice, has equal weight.
"""
from __future__ import annotations

import argparse
import random
import sys

import chess

from referee import FowBoard, fow_moves

OBSERVATION_CHARACTERS = frozenset("?-PNBRQKpnbrqk")


def infer_en_passant(previous: str | None, current: str, side: chess.Color):
    """Infer the EP target from the observations bracketing an opponent move.

    An eligible enemy pawn must have just arrived from its starting rank. A
    single push would have been visible on our pawn's capture diagonal in the
    previous observation. A capture would change one of our always-visible
    pieces. If a different enemy move merely exposed a stationary pawn, it
    would change an already-visible blocker elsewhere along the revealing ray.
    Comparing squares visible on both observations rules out that alternative.

    Unknown squares are never treated as evidence of occupancy or emptiness.
    The routine is called only on our turn, with the immediately preceding
    observation, so its history spans exactly the opponent's preceding move.
    """
    if previous is None:
        return None
    ours, enemy = ("P", "p") if side else ("p", "P")
    own_symbols = "PNBRQK" if side else "pnbrqk"
    if any(before != after and (before in own_symbols or after in own_symbols)
           for before, after in zip(previous, current)):
        # Own pieces are always observed. Losing one, even when its former
        # square becomes unseen, proves the opponent captured instead.
        return None
    pawn_rank, middle_rank, start_rank = (4, 5, 6) if side else (3, 2, 1)
    possible = []
    for file in range(8):
        target = chess.square(file, pawn_rank)
        middle = chess.square(file, middle_rank)
        start = chess.square(file, start_rank)
        if current[target] != enemy or current[middle] != "-":
            continue
        if not any(0 <= other < 8 and current[chess.square(other, pawn_rank)] == ours
                   for other in (file - 1, file + 1)):
            continue
        if previous[target] not in "?-" or previous[middle] not in "?-":
            continue
        if previous[start] not in ("?", enemy) or current[start] not in "?-":
            continue
        if any(before != "?" and after != "?" and before != after
               for square, (before, after) in enumerate(zip(previous, current))
               if square not in (start, target)):
            continue
        possible.append(middle)
    if len(possible) > 1:
        raise ValueError("Observations are inconsistent with a single opponent move")
    return possible[0] if possible else None


def candidates(current: str, side: chess.Color, rights: int,
               previous: str | None = None):
    """Return a board containing visible pieces only and the exact legal moves.

    Empty unseen squares in the temporary board are placeholders. A move may
    never land on or slide through an unseen square; the explicit mask checks
    below stop those placeholders from creating phantom moves.
    """
    if len(current) != 64 or not set(current) <= OBSERVATION_CHARACTERS:
        raise ValueError("Expected a 64-square FOW observation")
    if previous is not None and (len(previous) != 64 or
                                 not set(previous) <= OBSERVATION_CHARACTERS):
        raise ValueError("Expected a 64-square previous FOW observation")
    board = FowBoard(None)
    board.turn = side
    for square, char in enumerate(current):
        if char not in "?-":
            board.set_piece_at(square, chess.Piece.from_symbol(char))
    board.castling_rights = rights
    board.ep_square = infer_en_passant(previous, current, side)
    moves = []
    for move in fow_moves(board):
        if current[move.to_square] == "?":
            continue
        piece = board.piece_at(move.from_square)
        if piece.piece_type != chess.KNIGHT:
            if any(current[square] == "?" for square in
                   chess.SquareSet(chess.between(move.from_square, move.to_square))):
                continue
        if board.is_castling(move):
            rank = 0 if side else 7
            files = (5, 6) if move.to_square > move.from_square else (1, 2, 3)
            if any(current[chess.square(file, rank)] != "-" for file in files):
                continue
        moves.append(move)
    return board, moves


class ObservationTracker:
    """Remember own castling rights and the last two public-to-us observations."""

    def __init__(self, side: chess.Color, rights: int | None = None):
        self.side = side
        self.rank = 0 if side else 7
        self.king_start = chess.square(4, self.rank)
        self.rook_starts = (chess.square(0, self.rank), chess.square(7, self.rank))
        self.rights = (sum(chess.BB_SQUARES[square] for square in self.rook_starts)
                       if rights is None else rights)
        self.previous = None
        self.current = None

    def observe(self, view: str):
        if len(view) != 64 or not set(view) <= OBSERVATION_CHARACTERS:
            raise ValueError("Expected a 64-square FOW observation")
        self.previous, self.current = self.current, view
        king, rook = ("K", "R") if self.side else ("k", "r")
        if view[self.king_start] != king:
            self.rights = 0
        for square in self.rook_starts:
            if view[square] != rook:
                self.rights &= ~chess.BB_SQUARES[square]

    def legal_moves(self):
        if self.current is None:
            raise ValueError("No observation received")
        return candidates(self.current, self.side, self.rights, self.previous)[1]

    def record_move(self, move: chess.Move):
        if self.current is None:
            raise ValueError("No observation received")
        if self.current[move.from_square].lower() == "k":
            self.rights = 0
        self.rights &= ~chess.BB_SQUARES[move.from_square]


def choose_move(tracker: ObservationTracker, rng: random.Random):
    """Sample exactly once from the complete list, without capture preferences."""
    moves = tracker.legal_moves()
    return rng.choice(moves) if moves else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    if sys.stdin.readline().strip().lower() != "fow":
        return
    header = sys.stdin.readline().split()
    if len(header) != 3 or header[0].lower() not in ("w", "b"):
        raise ValueError("Expected color, base time, and increment")
    tracker = ObservationTracker(header[0].lower() == "w")
    print("ready", flush=True)
    for raw in sys.stdin:
        line = raw.strip()
        if len(line) == 64 and set(line) <= OBSERVATION_CHARACTERS:
            tracker.observe(line)
            continue
        try:
            float(line)
        except ValueError:
            continue
        move = choose_move(tracker, rng)
        if move is None:
            return
        tracker.record_move(move)
        print("move " + move.uci(), flush=True)


if __name__ == "__main__":
    main()
