"""Exact MIT FOW visibility using bitboards; no new observation information.

The unchanged official referee remains the test oracle. Pawn pushes, capture
visibility, off-turn en passant, and FOW castling follow that implementation.
"""
import chess


def visible_mask(board, side):
    own = board.occupied_co[side]
    enemy = board.occupied_co[not side]
    pawns = board.pawns & own
    visible = own
    for square in chess.scan_forward(own & ~pawns):
        visible |= board.attacks_mask(square)
    empty = ~board.occupied & chess.BB_ALL
    if side:
        single = (pawns << 8) & empty & chess.BB_ALL
        double = ((single & chess.BB_RANK_3) << 8) & empty
        captures = (((pawns & ~chess.BB_FILE_A) << 7) |
                    ((pawns & ~chess.BB_FILE_H) << 9)) & enemy
    else:
        single = (pawns >> 8) & empty
        double = ((single & chess.BB_RANK_6) >> 8) & empty
        captures = (((pawns & ~chess.BB_FILE_A) >> 9) |
                    ((pawns & ~chess.BB_FILE_H) >> 7)) & enemy
    visible |= single | double | captures
    if side == board.turn and board.ep_square is not None:
        for move in board.generate_pseudo_legal_ep():
            captured = move.to_square + (-8 if side else 8)
            visible |= chess.BB_SQUARES[move.to_square] | chess.BB_SQUARES[captured]
    rank = 0 if side else 7
    king = chess.square(4, rank)
    king_bit = chess.BB_SQUARES[king]
    if board.kings & own & king_bit and not board.promoted & king_bit:
        rights = board.clean_castling_rights()
        for rook_file, target_file, empty_files in ((7, 6, (5, 6)), (0, 2, (1, 2, 3))):
            rook = chess.BB_SQUARES[chess.square(rook_file, rank)]
            path = sum(chess.BB_SQUARES[chess.square(f, rank)] for f in empty_files)
            if rights & rook and board.rooks & own & rook and not board.occupied & path:
                visible |= chess.BB_SQUARES[chess.square(target_file, rank)]
    return visible & chess.BB_ALL


def observation_masks(view, side):
    """Compile a validated 64-character observation once per update."""
    if len(view) != 64 or any(c not in '?-PNBRQKpnbrqk' for c in view):
        raise ValueError('Expected a 64-square FOW observation')
    visible = occupied = ours = 0
    pieces = {}
    for square, symbol in enumerate(view):
        if symbol == '?':
            continue
        bit = chess.BB_SQUARES[square]
        visible |= bit
        if symbol == '-':
            continue
        occupied |= bit
        if symbol.isupper() == side:
            ours |= bit
        piece = chess.Piece.from_symbol(symbol)
        key = piece.piece_type, piece.color
        pieces[key] = pieces.get(key, 0) | bit
    return visible, occupied, ours, pieces


def matches_masks(board, side, masks):
    visible, occupied, ours, pieces = masks
    return (board.occupied_co[side] == ours
            and board.occupied & visible == occupied
            and all(board.pieces_mask(kind, color) & visible == squares
                    for (kind, color), squares in pieces.items())
            and visible_mask(board, side) == visible)
