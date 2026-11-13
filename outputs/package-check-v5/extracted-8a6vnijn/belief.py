"""Bounded hidden-board hypotheses with explicit approximate recovery.

Initially boards follow the standard start and supplied observations/moves.
When sampling loses all support, recovery reconciles the previous inventory
with the CURRENT observation. Recovered boards do not certify prior history;
diagnostics retain that distinction permanently. No referee truth is accepted.
"""
import random
import time
import math
from collections import Counter

import chess

from fast_observation import matches_masks, observation_masks, visible_mask
from referee import FowBoard, fow_moves
from uniform_random import OBSERVATION_CHARACTERS, infer_en_passant


def _validate_view(view):
    if len(view) != 64 or not set(view) <= OBSERVATION_CHARACTERS:
        raise ValueError('Expected a 64-square FOW observation')


def forced_blockers(view, side):
    """Infer enemy occupancy where a pawn's missing push proves a blocker.

    The type is unknown. A hidden square is not normally occupied, but an empty
    forward square would be a legal pawn destination and therefore visible.
    """
    _validate_view(view)
    pawn, step, home = ('P', 8, 1) if side else ('p', -8, 6)
    blockers = set()
    for square, symbol in enumerate(view):
        if symbol != pawn:
            continue
        target = square + step
        if not 0 <= target < 64:
            continue
        if view[target] == '?':
            blockers.add(target)
        elif (view[target] == '-' and chess.square_rank(square) == home
              and view[square + 2 * step] == '?'):
            blockers.add(square + 2 * step)
    return blockers


def _view_masks(view, side):
    return observation_masks(view, side)


def _quick_matches(board, masks, side):
    visible, occupied, ours, pieces = masks
    if board.occupied_co[side] != ours or board.occupied & visible != occupied:
        return False
    return all(board.pieces_mask(kind, color) & visible == squares
               for (kind, color), squares in pieces.items())


def repetition_key(board):
    """Fast equivalent of the referee's FOW repetition identity.

    Move counters and promoted markers do not occur in board_fen(); promotion
    still affects clean castling rights. EP matters whenever a pseudo-legal
    FOW capture exists, including an orthodox pinned en-passant capture.
    """
    ep = (board.ep_square if board.ep_square is not None
          and any(board.generate_pseudo_legal_ep()) else None)
    return (board.pawns, board.knights, board.bishops, board.rooks, board.queens,
            board.kings, board.occupied_co[0], board.occupied_co[1], board.turn,
            int(board.clean_castling_rights()), ep)


def _copy_board(board):
    copied = board.copy(stack=False)
    history = getattr(board, 'fow_repetitions', None)
    copied.fow_repetitions = Counter(history if history is not None else
                                    {repetition_key(board): 1})
    return copied


def _continued(board):
    # FOW ends before the next prompt when the player to move can capture the
    # previous mover's king. Orthodox check/pin restrictions do not apply.
    mover_king = board.king(not board.turn)
    return (mover_king is not None and board.king(board.turn) is not None
            and not board.is_attacked_by(board.turn, mover_king)
            and board.halfmove_clock < 100
            and getattr(board, 'fow_repetitions', {}).get(repetition_key(board), 0) < 3
            and any(board.generate_pseudo_legal_moves()))


def _key(board):
    return (board.pawns, board.knights, board.bishops, board.rooks, board.queens,
            board.kings, board.occupied_co[0], board.occupied_co[1],
            board.promoted, board.turn, board.castling_rights, board.ep_square,
            board.halfmove_clock,
            frozenset(getattr(board, 'fow_repetitions', {}).items()))


def _hidden_locations(view, side):
    allowed = {square for square, value in enumerate(view) if value == '?'}
    pawn, step = ('P', 8) if side else ('p', -8)
    for square, symbol in enumerate(view):
        if symbol != pawn:
            continue
        # An enemy on a pawn capture diagonal would be visible, so a '?' there
        # is provably empty, even though it has no '-' character.
        for target in (square + step - 1, square + step + 1):
            if (0 <= target < 64 and
                    abs(chess.square_file(target) - chess.square_file(square)) == 1):
                allowed.discard(target)
    return allowed


class Belief:
    """Track a finite set of boards through EVERY-PLY observations.

    Call record_move after selecting our move, then observe both the resulting
    position and the next opponent position. Only an exact standard initial
    observation enables tracking; arbitrary-position clients get an empty set.
    """

    MAX_UPDATE_SECONDS = 0.08
    MAX_EXPANSIONS = 4096
    MAX_RECOVERY_SECONDS = 0.04
    MAX_RECOVERY_PROPOSALS = 64

    def __init__(self, side, seed=0, max_particles=64):
        if type(max_particles) is not int or not 1 <= max_particles <= 1024:
            raise ValueError('max_particles must be an integer between 1 and 1024')
        self.side = bool(side)
        self.max_particles = max_particles
        self.particles = []
        self.diagnostics = {
            'status': 'uninitialized', 'exact_history': False,
            'complete_posterior': False, 'observations': 0,
            'particles': 0, 'expansions': 0, 'visibility_checks': 0,
            'truncated': False, 'rejuvenated': False,
            'recovery_count': 0, 'recovery_attempts': 0,
            'recovery_proposals': 0, 'recovery_status': 'not_needed',
            'repetition_history_exact': False,
        }
        self._rng = random.Random(seed)
        self._sample_rng = random.Random(seed)
        self._pending = None
        self._initialized = False
        self._history_exact = False
        self._last_view = None

    def _finish(self, boards, status, started, **details):
        self.particles = boards
        self.diagnostics.update(
            status=status, exact_history=bool(boards) and self._history_exact,
            repetition_history_exact=bool(boards) and self._history_exact,
            particles=len(boards),
            elapsed_seconds=time.monotonic() - started, **details)

    def _repair_current(self, base, view, previous, perturb, masks):
        """Reconcile a prior inventory; never claim the rearrangement occurred."""
        enemy = not self.side
        pool = [(square, base.piece_at(square), bool(base.promoted & chess.BB_SQUARES[square]))
                for square in chess.SquareSet(base.occupied_co[enemy])]
        rebuilt = FowBoard(None)
        rebuilt.turn = base.turn
        rebuilt.halfmove_clock = base.halfmove_clock
        rebuilt.fullmove_number = base.fullmove_number
        rebuilt.ep_square = base.ep_square
        if rebuilt.turn == self.side:
            try:
                rebuilt.ep_square = infer_en_passant(previous, view, self.side)
            except ValueError:
                return None
        relocated = set()
        for square, symbol in enumerate(view):
            if symbol not in '?-' and symbol.isupper() == self.side:
                rebuilt.set_piece_at(square, chess.Piece.from_symbol(symbol))
        required = [(square, chess.Piece.from_symbol(symbol))
                    for square, symbol in enumerate(view)
                    if symbol not in '?-' and symbol.isupper() == enemy]
        # Preserve already matching sightings before assigning moved pieces.
        required.sort(key=lambda item: base.piece_at(item[0]) != item[1])
        for square, piece in required:
            matching = [i for i, (_, old, _) in enumerate(pool) if old == piece]
            if not matching:
                # A visible promotion may consume one existing near-promotion
                # pawn; this changes its type but never adds an extra piece.
                if (piece.piece_type in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT)
                        and chess.square_rank(square) == (7 if enemy else 0)):
                    matching = [i for i, (old_square, old, _) in enumerate(pool)
                                if old.piece_type == chess.PAWN
                                and chess.square_rank(old_square) == (6 if enemy else 1)]
                if not matching:
                    return None
            index = min(matching, key=lambda i: (
                chess.square_distance(pool[i][0], square), self._rng.random()))
            origin, old, promoted = pool.pop(index)
            if origin != square or old != piece:
                relocated.add(origin)
            rebuilt.set_piece_at(square, piece, promoted=promoted or old != piece)

        allowed = _hidden_locations(view, self.side)
        forced = forced_blockers(view, self.side)

        def can_place(entry, square):
            return (square in allowed and rebuilt.piece_at(square) is None
                    and (entry[1].piece_type != chess.PAWN
                         or chess.square_rank(square) not in (0, 7)))

        def place(entry, square):
            origin, piece, promoted = entry
            rebuilt.set_piece_at(square, piece, promoted=promoted)
            if origin != square:
                relocated.add(origin)

        for square in sorted(forced):
            possible = [i for i, entry in enumerate(pool) if can_place(entry, square)]
            if not possible:
                return None
            index = min(possible, key=lambda i: (
                chess.square_distance(pool[i][0], square), self._rng.random()))
            place(pool.pop(index), square)
        self._rng.shuffle(pool)
        for entry in pool:
            origin = entry[0]
            options = [square for square in allowed if can_place(entry, square)]
            if not options:
                return None
            if origin in options and (not perturb or self._rng.random() > 0.20):
                target = origin
            else:
                # Favor nearby repairs while retaining several alternatives.
                options.sort(key=lambda square: (
                    chess.square_distance(origin, square), self._rng.random()))
                target = self._rng.choice(options[:min(8, len(options))])
            place(entry, target)
        rights = base.castling_rights
        for square in relocated:
            rights &= ~chess.BB_SQUARES[square]
            piece = base.piece_at(square)
            if piece and piece.piece_type == chess.KING:
                rights &= ~chess.BB_RANKS[0 if piece.color else 7]
        rebuilt.castling_rights = rights
        rebuilt.castling_rights = rebuilt.clean_castling_rights()
        # The rearrangement certifies no earlier repetition history.
        rebuilt.fow_repetitions = Counter({repetition_key(rebuilt): 1})
        if not _continued(rebuilt) or not matches_masks(rebuilt, self.side, masks):
            return None
        return rebuilt

    def _recover(self, parents, pending, view, previous, masks, deadline=None):
        started = time.monotonic()
        stop_at = started + self.MAX_RECOVERY_SECONDS
        if deadline is not None:
            stop_at = min(stop_at, deadline)
        self.diagnostics['recovery_attempts'] += 1
        recovered, seen = [], set()
        proposals = 0
        for index in range(self.MAX_RECOVERY_PROPOSALS):
            if time.monotonic() >= stop_at:
                break
            parent = parents[index % len(parents)]
            base = _copy_board(parent)
            if pending is not None:
                if base.turn != self.side or pending not in fow_moves(base):
                    continue
                # This applies the observed capture or EP removal before any
                # repair, preserving the reduced enemy inventory exactly.
                base.push(pending)
            else:
                if base.turn == self.side:
                    continue
                base.turn = self.side
                base.ep_square = None
                base.halfmove_clock = 0  # Unknown opponent move may reset it.
            proposals += 1
            board = self._repair_current(base, view, previous, perturb=index > 0,
                                         masks=masks)
            if board is None or _key(board) in seen:
                continue
            seen.add(_key(board))
            recovered.append(board)
            if len(recovered) >= self.max_particles:
                break
        self.diagnostics.update(recovery_proposals=proposals,
                                recovery_status='recovered_current_view' if recovered else 'failed')
        if recovered:
            self._history_exact = False
            self.diagnostics['recovery_count'] += 1
            self.diagnostics['rejuvenated'] = True
        return recovered

    def observe(self, view, deadline=None):
        """Consume one observation, optionally sharing an absolute time limit.

        An expired limit can deplete the belief. Old boards are never retained
        as if they represented the new observation. The same caller deadline
        bounds transition and recovery work together.
        """
        _validate_view(view)
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError('Deadline must be finite')
        started = time.monotonic()
        stop_at = started + self.MAX_UPDATE_SECONDS
        if deadline is not None:
            stop_at = min(stop_at, deadline)
        self.diagnostics['observations'] += 1
        self.diagnostics.update(expansions=0, visibility_checks=0,
                                truncated=False, recovery_proposals=0,
                                recovery_status='not_needed')
        previous, self._last_view = self._last_view, view
        masks = _view_masks(view, self.side)
        if not self._initialized:
            self._initialized = True
            initial = FowBoard()
            if matches_masks(initial, self.side, masks):
                self._history_exact = True
                initial.fow_repetitions = Counter({repetition_key(initial): 1})
                self._finish([initial], 'tracking', started)
            else:
                self._finish([], 'unsupported_initial_observation', started)
            return
        pending, self._pending = self._pending, None
        if not self.particles:
            self._finish([], 'depleted', started)
            return

        parents = list(self.particles)
        self._rng.shuffle(parents)
        survivors, seen = [], set()
        expansions = visibility_checks = survivor_count = 0
        truncated = False
        phase_error = False
        # Grow transition coverage with the bank, retaining a hard work cap.
        # MAX_EXPANSIONS remains adjustable for bounded-work regression tests.
        expansion_limit = min(16384, self.MAX_EXPANSIONS *
                              max(64, self.max_particles) // 64)
        for parent in parents:
            if pending is not None:
                if parent.turn != self.side:
                    phase_error = True
                    continue
                moves = [pending] if pending in fow_moves(parent) else []
            else:
                if parent.turn == self.side:
                    phase_error = True
                    continue
                moves = fow_moves(parent)
                self._rng.shuffle(moves)
            for move in moves:
                if (expansions >= expansion_limit
                        or time.monotonic() >= stop_at):
                    truncated = True
                    break
                expansions += 1
                child = _copy_board(parent)
                child.push(move)
                child.fow_repetitions[repetition_key(child)] += 1
                if not _quick_matches(child, masks, self.side) or not _continued(child):
                    continue
                visibility_checks += 1
                if visible_mask(child, self.side) != masks[0]:
                    continue
                key = _key(child)
                if key in seen:
                    continue
                seen.add(key)
                survivor_count += 1
                if len(survivors) < self.max_particles:
                    survivors.append(child)
                else:
                    index = self._rng.randrange(survivor_count)
                    if index < self.max_particles:
                        survivors[index] = child
            if truncated:
                break
        if not survivors and not phase_error:
            survivors = self._recover(parents, pending, view, previous, masks,
                                      stop_at)
        status = (('tracking' if self._history_exact else 'tracking_approximate_history')
                  if survivors else 'missing_or_out_of_phase_own_move'
                  if phase_error else 'depleted_by_work_limit' if truncated
                  else 'depleted_by_observation')
        self._finish(survivors, status, started, expansions=expansions,
                     visibility_checks=visibility_checks, truncated=truncated,
                     consistent_successors=survivor_count,
                     sampled=survivor_count > self.max_particles)

    def record_move(self, move):
        if self._pending is not None:
            raise ValueError('Own move already recorded; an observation is required')
        if not isinstance(move, chess.Move) or not move:
            raise ValueError('Expected a non-null chess move')
        self._pending = move

    def sample(self, count):
        if not isinstance(count, int) or count < 0:
            raise ValueError('Sample count must be a nonnegative integer')
        chosen = self._sample_rng.sample(self.particles, min(count, len(self.particles)))
        return [_copy_board(board) for board in chosen]
