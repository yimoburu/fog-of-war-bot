"""Observation-only diverse hypotheses with bounded, explicitly approximate repair.

Physical diversity and move-prior weights are heuristics, not posterior masses.
Distinct repetition histories are retained intact, never averaged or merged.
"""
from collections import Counter
import heapq
import math
import time

import chess

from belief import (Belief as BaseBelief, _continued, _copy_board, _hidden_locations,
                    _key, _quick_matches, _validate_view, _view_masks,
                    forced_blockers, repetition_key)
from fast_observation import matches_masks, visible_mask
from referee import FowBoard, fow_moves


def copy_particle(board):
    copied = _copy_board(board)
    copied.fow_history_exact = getattr(board, 'fow_history_exact', True)
    copied.fow_hypothesis_kind = getattr(board, 'fow_hypothesis_kind', 'transition')
    return copied


def opponent_move_weights(board, moves, prior_mix=.65):
    """A capture/development prior with a 35% uniform probability component."""
    if not moves:
        return []
    values = (0, 100, 320, 330, 500, 900, 0)
    raw = []
    side = board.turn
    for move in moves:
        piece = board.piece_type_at(move.from_square)
        victim = board.piece_type_at(move.to_square)
        capture = values[victim] if victim else 100 if board.is_en_passant(move) else 0
        value = 1 + min(4.5, capture / 200) + (3 if move.promotion else 0)
        if piece in (chess.KNIGHT, chess.BISHOP):
            home = 0 if side else 7
            value += .7 if chess.square_rank(move.from_square) == home else .15
        if piece == chess.PAWN:
            value += .25
        if capture and board.is_attacked_by(not side, move.to_square):
            value *= .6  # A cheap prior only; continued-game king safety is exact.
        raw.append(value)
    total = sum(raw)
    return [(1 - prior_mix) + prior_mix * len(moves) * value / total for value in raw]


class Belief(BaseBelief):
    MAX_ARCHIVE = 8
    MAX_REJUVENATION_PROPOSALS = 128
    MAX_HISTORIES_PER_POSITION = 2

    def __init__(self, side, seed=0, max_particles=64, *, update_seconds=.08):
        super().__init__(side, seed, max_particles)
        if not math.isfinite(update_seconds) or not 0 < update_seconds <= .25:
            raise ValueError('update_seconds must be finite and in (0, .25]')
        self.MAX_UPDATE_SECONDS = update_seconds
        self._archive = []
        self._supported = False
        self._turn = chess.WHITE
        self._ep_after_own_move = None
        self._enemy_turns = 0
        self._last_enemy_sighting = {}
        self._own_rights = chess.BB_RANK_1 if side else chess.BB_RANK_8
        self._own_rights &= chess.BB_CORNERS
        self._repair_deadline = math.inf
        self.known_enemy_captures = Counter()
        self.diagnostics.update(physical_weighting='diversity heuristic; not posterior probability',
                                known_enemy_captures={}, distinct_physical_positions=0,
                                exact_history_particles=0, approximate_history_particles=0,
                                rejuvenation_proposals=0, rejuvenation_added=0,
                                depletion_retries=0, promotion_repairs=0,
                                capture_inventory_type_substitutions=0)

    def _remember_bank(self):
        groups = {}
        for board in self.particles:
            groups.setdefault(repetition_key(board), board)
        boards = list(groups.values())
        if len(boards) > self.MAX_ARCHIVE:
            boards = self._rng.sample(boards, self.MAX_ARCHIVE)
        if boards:
            self._archive = [copy_particle(board) for board in boards]

    def _capture_bookkeeping(self, pending, previous):
        """Persist observed captures even while the current bank is empty."""
        if pending is None or previous is None:
            return
        mover = previous[pending.from_square]
        home = chess.BB_RANK_1 if self.side else chess.BB_RANK_8
        if mover.lower() == 'k':
            self._own_rights &= ~home
        if mover.lower() == 'r':
            self._own_rights &= ~chess.BB_SQUARES[pending.from_square]
        target = pending.to_square
        symbol = previous[target]
        if (mover.lower() == 'p' and symbol == '-'
                and chess.square_file(pending.from_square) != chess.square_file(target)):
            target += -8 if self.side else 8
            symbol = previous[target]
        if symbol in '?-' or symbol.isupper() == self.side:
            return
        victim = chess.Piece.from_symbol(symbol)
        self.known_enemy_captures[victim.symbol()] += 1
        for board in self._archive:
            locations = list(board.pieces(victim.piece_type, not self.side))
            if board.piece_at(target) == victim:
                remove = target
            elif locations:
                remove = min(locations, key=lambda square: chess.square_distance(square, target))
            else:
                # An observed captured promoted piece consumes one latent pawn
                # when that promotion was absent from this inventory hypothesis.
                pawns = list(board.pieces(chess.PAWN, not self.side))
                promoted = list(chess.scan_forward(board.promoted & board.occupied_co[not self.side]))
                uncertain = list(chess.scan_forward(board.occupied_co[not self.side] & ~board.kings))
                alternatives = promoted or pawns or uncertain
                remove = alternatives[0] if alternatives else None
                if remove is not None:
                    # The observed capture proves that one enemy disappeared,
                    # even if this approximate template assigned it the wrong
                    # promotion type. Never silently retain that extra piece.
                    self.diagnostics['capture_inventory_type_substitutions'] += 1
                    board.fow_history_exact = False
            if remove is not None:
                board.remove_piece_at(remove)
                board.castling_rights &= ~chess.BB_SQUARES[remove]

    def _repair_current(self, base, view, previous, perturb, masks):
        if time.monotonic() >= self._repair_deadline:
            return None
        base.castling_rights &= ~(chess.BB_RANK_1 if self.side else chess.BB_RANK_8)
        base.castling_rights |= self._own_rights
        repaired = super()._repair_current(base, view, previous, perturb, masks)
        if repaired is None and time.monotonic() < self._repair_deadline:
            # A promoted piece can first reappear well away from the last rank.
            # Convert existing hidden pawn inventory; never manufacture a piece.
            promoted = copy_particle(base)
            converted = 0
            enemy = not self.side
            for kind in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
                symbol = chess.Piece(kind, enemy).symbol()
                missing = view.count(symbol) - len(promoted.pieces(kind, enemy))
                for _ in range(max(0, missing)):
                    choices = []
                    for square in promoted.pieces(chess.PAWN, enemy):
                        if view[square] == chess.Piece(chess.PAWN, enemy).symbol():
                            continue
                        rank = chess.square_rank(square)
                        distance = (7 - rank) if enemy else rank
                        if rank == (1 if enemy else 6):
                            distance -= 1  # The initial double step is possible.
                        if not any(view[target] == symbol and chess.square_rank(target) ==
                                   (7 if enemy else 0) for target in chess.SQUARES):
                            distance += 1  # A later off-rank sighting needs another move.
                        age = self._enemy_turns - self._last_enemy_sighting.get(
                            (chess.Piece(chess.PAWN, enemy).symbol(), square), 0)
                        if age >= distance:
                            choices.append(square)
                    if not choices:
                        break
                    square = self._rng.choice(choices)
                    promoted.set_piece_at(square, chess.Piece(kind, enemy), promoted=True)
                    converted += 1
            if converted:
                repaired = super()._repair_current(promoted, view, previous, perturb, masks)
                if repaired is not None:
                    self.diagnostics['promotion_repairs'] += converted
        if repaired is not None:
            repaired.fow_history_exact = False
            repaired.fow_hypothesis_kind = 'observation_repair'
        return repaired

    def _archive_recovery(self, view, previous, masks, deadline):
        self.diagnostics['depletion_retries'] += 1
        self.diagnostics['recovery_attempts'] += 1
        recovered, seen = [], set()
        for index in range(self.MAX_RECOVERY_PROPOSALS):
            if not self._archive or time.monotonic() >= deadline:
                break
            base = copy_particle(self._archive[index % len(self._archive)])
            base.turn = self._turn
            base.ep_square = self._ep_after_own_move if self._turn != self.side else None
            base.halfmove_clock = 0
            self.diagnostics['recovery_proposals'] += 1
            board = self._repair_current(base, view, previous, index > 0, masks)
            if board is None or repetition_key(board) in seen:
                continue
            seen.add(repetition_key(board))
            recovered.append(board)
            if len(recovered) >= self.max_particles:
                break
        if recovered:
            self._history_exact = False
            self.diagnostics['recovery_count'] += 1
            self.diagnostics['rejuvenated'] = True
            self.diagnostics['recovery_status'] = 'recovered_current_view'
        else:
            self.diagnostics['recovery_status'] = 'failed'
        return recovered

    def _jitter(self, parent, view, masks, deadline):
        hidden = sum(chess.BB_SQUARES[sq] for sq in _hidden_locations(view, self.side))
        enemy = not self.side
        origins = list(chess.scan_forward(parent.occupied_co[enemy] & hidden))
        self._rng.shuffle(origins)
        for origin in origins[:4]:
            if time.monotonic() >= deadline:
                return None
            proposal = copy_particle(parent)
            proposal.turn, proposal.ep_square = enemy, None
            moves = list(proposal.generate_pseudo_legal_moves(
                from_mask=chess.BB_SQUARES[origin], to_mask=hidden & ~proposal.occupied))
            self._rng.shuffle(moves)
            for move in moves[:8]:
                if time.monotonic() >= deadline:
                    return None
                proposal.push(move)
                saved_turn = proposal.turn
                proposal.turn = parent.turn
                proposal.ep_square = parent.ep_square
                proposal.halfmove_clock = 0
                proposal.fow_repetitions = Counter({repetition_key(proposal): 1})
                valid = (_quick_matches(proposal, masks, self.side) and _continued(proposal)
                         and visible_mask(proposal, self.side) == masks[0])
                if valid:
                    proposal.fow_history_exact = False
                    proposal.fow_hypothesis_kind = 'hidden_mobility_rejuvenation'
                    return proposal
                proposal.turn = saved_turn
                proposal.pop()
        return None

    def observe(self, view, deadline=None):
        _validate_view(view)
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError('Deadline must be finite')
        started = time.monotonic()
        stop_at = min(started + self.MAX_UPDATE_SECONDS,
                      deadline if deadline is not None else math.inf)
        if not self._initialized or not self._supported:
            super().observe(view, deadline=stop_at)
            if self.particles:
                self._supported = True
                for board in self.particles:
                    board.fow_history_exact = True
                    board.fow_hypothesis_kind = 'standard_initial'
                self.diagnostics.update(distinct_physical_positions=1,
                                        exact_history_particles=1,
                                        approximate_history_particles=0)
                self._remember_bank()
            self.diagnostics['elapsed_seconds'] = time.monotonic() - started
            return
        previous = self._last_view
        self._last_view = view
        pending, self._pending = self._pending, None
        phase_error = (pending is None) == (self._turn == self.side)
        if self._turn != self.side:
            self._enemy_turns += 1
        self._turn = not self._turn
        self._ep_after_own_move = ((pending.from_square + pending.to_square) // 2
                                  if pending is not None and previous[pending.from_square].lower() == 'p'
                                  and abs(pending.to_square - pending.from_square) == 16 else None)
        self._capture_bookkeeping(pending, previous)
        home = 0 if self.side else 7
        king_symbol, rook_symbol = ('K', 'R') if self.side else ('k', 'r')
        if view[chess.square(4, home)] != king_symbol:
            self._own_rights = 0
        for file in (0, 7):
            square = chess.square(file, home)
            if view[square] != rook_symbol:
                self._own_rights &= ~chess.BB_SQUARES[square]
        for square, symbol in enumerate(view):
            if symbol not in '?-' and symbol.isupper() != self.side:
                self._last_enemy_sighting[(symbol, square)] = self._enemy_turns
        self.diagnostics['observations'] += 1
        self.diagnostics.update(expansions=0, visibility_checks=0, truncated=False,
                                recovery_proposals=0, recovery_status='not_needed',
                                rejuvenation_proposals=0, rejuvenation_added=0)
        masks = _view_masks(view, self.side)
        self._repair_deadline = stop_at
        groups, heap, serial = {}, [], 0
        parents = list(self.particles)
        self._rng.shuffle(parents)
        # Reserve repair time inside the caller's single absolute limit.
        transition_stop = stop_at - min(.015, max(0, stop_at - started) * .25)
        limit = min(16384, self.MAX_EXPANSIONS * max(64, self.max_particles) // 64)
        if not phase_error:
            for parent in parents:
                if time.monotonic() >= transition_stop:
                    self.diagnostics['truncated'] = True
                    break
                if pending is not None:
                    moves = [pending] if pending in fow_moves(parent) else []
                    weights = [1.0] * len(moves)
                else:
                    moves = fow_moves(parent)
                    weights = opponent_move_weights(parent, moves)
                ordered = sorted(zip(moves, weights),
                                 key=lambda item: self._rng.random() ** (1 / item[1]), reverse=True)
                for move, weight in ordered:
                    if self.diagnostics['expansions'] >= limit or time.monotonic() >= transition_stop:
                        self.diagnostics['truncated'] = True
                        break
                    self.diagnostics['expansions'] += 1
                    child = copy_particle(parent)
                    child.push(move)
                    physical = repetition_key(child)
                    child.fow_repetitions[physical] += 1
                    if not _quick_matches(child, masks, self.side) or not _continued(child):
                        continue
                    self.diagnostics['visibility_checks'] += 1
                    if visible_mask(child, self.side) != masks[0]:
                        continue
                    group = groups.get(physical)
                    if group is not None:
                        if (_key(child) not in group['keys']
                                and len(group['boards']) < self.MAX_HISTORIES_PER_POSITION):
                            group['boards'].append(child)
                            group['keys'].add(_key(child))
                        continue
                    priority = self._rng.random() ** (1 / weight)
                    if len(groups) >= self.max_particles:
                        if priority <= heap[0][0]:
                            continue
                        _, _, removed = heapq.heappop(heap)
                        del groups[removed]
                    serial += 1
                    groups[physical] = {'priority': priority, 'boards': [child], 'keys': {_key(child)}}
                    heapq.heappush(heap, (priority, serial, physical))
                if self.diagnostics['truncated']:
                    break
        ordered_groups = sorted(groups.values(), key=lambda group: group['priority'], reverse=True)
        survivors = [group['boards'][0] for group in ordered_groups]
        for group in ordered_groups:
            if len(survivors) >= self.max_particles:
                break
            survivors.extend(group['boards'][1:self.max_particles - len(survivors) + 1])
        if not survivors and not phase_error:
            survivors = self._archive_recovery(view, previous, masks, stop_at)
        if (survivors and self._turn == self.side and self.diagnostics['observations'] >= 9
                and time.monotonic() < stop_at):
            slots = min(self.max_particles - 1, max(4, self.max_particles // 8))
            seen = {repetition_key(board) for board in survivors}
            additions = []
            for _ in range(self.MAX_REJUVENATION_PROPOSALS):
                if len(additions) >= slots or time.monotonic() >= stop_at:
                    break
                self.diagnostics['rejuvenation_proposals'] += 1
                base = self._rng.choice(survivors + additions)
                candidate = self._jitter(base, view, masks, stop_at)
                if candidate is not None and repetition_key(candidate) not in seen:
                    additions.append(candidate)
                    seen.add(repetition_key(candidate))
            if additions:
                # Reserve only the slots actually filled. A deadline or scarce
                # valid proposals must not discard otherwise useful histories.
                retained = survivors[:self.max_particles - len(additions)]
                survivors = retained + additions
                self._history_exact = False
                self.diagnostics['rejuvenated'] = True
                self.diagnostics['rejuvenation_added'] = len(additions)
        exact_count = sum(getattr(board, 'fow_history_exact', False) for board in survivors)
        if exact_count != len(survivors):
            self._history_exact = False
        status = ('missing_or_out_of_phase_own_move' if phase_error else
                  ('tracking' if self._history_exact else 'tracking_approximate_history')
                  if survivors else 'depleted_retry_available')
        self._finish(survivors, status, started,
                     known_enemy_captures=dict(self.known_enemy_captures),
                     distinct_physical_positions=len({repetition_key(board) for board in survivors}),
                     exact_history_particles=exact_count,
                     approximate_history_particles=len(survivors) - exact_count)
        self._remember_bank()
        self.diagnostics['elapsed_seconds'] = time.monotonic() - started

    def sample(self, count):
        if type(count) is not int or count < 0:
            raise ValueError('Sample count must be a nonnegative integer')
        groups = {}
        for board in self.particles:
            groups.setdefault(repetition_key(board), []).append(board)
        physicals = list(groups)
        self._sample_rng.shuffle(physicals)
        chosen = [self._sample_rng.choice(groups[key]) for key in physicals[:count]]
        if len(chosen) < min(count, len(self.particles)):
            selected = {id(board) for board in chosen}
            remainder = [board for board in self.particles if id(board) not in selected]
            chosen += self._sample_rng.sample(remainder, min(count - len(chosen), len(remainder)))
        return [copy_particle(board) for board in chosen]
