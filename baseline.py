#!/usr/bin/env python3
"""Small observation-only baseline. No access to truth, clocks of the opponent,
or the opponent's moves. Intentionally omits en passant to keep the starter small.
"""
import argparse
import random
import sys

import chess
from referee import FowBoard, fow_moves


def candidates(observation, side, rights):
    board = FowBoard(None)
    board.turn = side
    for square, char in enumerate(observation):
        if char not in '?-':
            board.set_piece_at(square, chess.Piece.from_symbol(char))
    board.castling_rights = rights
    result = []
    for move in fow_moves(board):
        if observation[move.to_square] == '?':
            continue
        piece = board.piece_at(move.from_square)
        if piece.piece_type != chess.KNIGHT:
            if any(observation[sq] == '?' for sq in chess.SquareSet(chess.between(move.from_square, move.to_square))):
                continue
        result.append(move)
    return board, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    if sys.stdin.readline().strip() != 'fow':
        return
    color, _, _ = sys.stdin.readline().split()
    print('ready', flush=True)
    side = color == 'w'
    rank = 0 if side else 7
    king_start = chess.square(4, rank)
    rights = chess.BB_SQUARES[chess.square(0, rank)] | chess.BB_SQUARES[chess.square(7, rank)]
    current = None
    for raw in sys.stdin:
        line = raw.strip()
        if len(line) == 64:
            current = line
            if current[king_start] != ('K' if side else 'k'):
                rights = 0
            for rook in [chess.square(0, rank), chess.square(7, rank)]:
                if current[rook] != ('R' if side else 'r'):
                    rights &= ~chess.BB_SQUARES[rook]
            continue
        try:
            float(line)
        except ValueError:
            continue
        if current is None:
            return
        board, moves = candidates(current, side, rights)
        if not moves:
            return
        values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
        def score(move):
            target = board.piece_at(move.to_square)
            value = values[target.piece_type] * 10 if target else 0
            if move.promotion:
                value += values[move.promotion] * 5
            return value + rng.random()
        move = max(moves, key=score)
        piece = board.piece_at(move.from_square)
        if piece.piece_type == chess.KING:
            rights = 0
        rights &= ~chess.BB_SQUARES[move.from_square]
        print('move ' + move.uci(), flush=True)


if __name__ == '__main__':
    main()
