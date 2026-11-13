#!/usr/bin/env python3
"""Experimental observation-only belief rejuvenation and hidden-threat insurance.

The threat score and particle frequencies are ranking heuristics, not calibrated
probabilities. Keep released bot.py intact while testing these mechanisms.
"""
import argparse
import math
import random
import sys
import time

import bot as base
from belief_v5 import Belief as RejuvenatedBelief
from belief import repetition_key
from threat_policy import ThreatHistory, risk as threat_exposure
from fog_pressure import PressureExpired, score as pressure_score


class State(base.State):
    def __init__(self, side, rights=None, seed=0, search_ms=900, particles=128,
                 worlds=4, depth=5, nodes=200000, belief_mode='rejuvenated',
                 threat_weight=.35, loss_cost=3200, confidence_prior=8,
                 pressure_weight=0):
        super().__init__(side, rights, seed, search_ms, 'bank', particles,
                         worlds, depth, nodes)
        if belief_mode not in ('original', 'rejuvenated'):
            raise ValueError('Unknown belief mode')
        if not math.isfinite(threat_weight) or not 0 <= threat_weight <= 1:
            raise ValueError('Threat weight must be between0 and1')
        if not math.isfinite(loss_cost) or not 800 <= loss_cost <= 10000:
            raise ValueError('Loss cost must be between800 and10000')
        if not math.isfinite(confidence_prior) or not 0 <= confidence_prior <= 64:
            raise ValueError('Confidence prior must be between0 and64')
        if not math.isfinite(pressure_weight) or not 0 <= pressure_weight <= 2:
            raise ValueError('Pressure weight must be between0 and2')
        if belief_mode == 'rejuvenated':
            self.belief = RejuvenatedBelief(side, seed=seed, max_particles=particles)
        self.threat_history = ThreatHistory(side)
        self.belief_mode = belief_mode
        self.threat_weight = threat_weight
        self.loss_cost = loss_cost
        self.confidence_prior = confidence_prior
        self.pressure_weight = pressure_weight

    def observe(self, view):
        super().observe(view)
        self.threat_history.observe(view)

    def record_move(self, board, move):
        self.threat_history.record_move(board, move)
        super().record_move(board, move)


def physical_count(particles):
    return len({repetition_key(b) for b in particles})


def observed_safe(state, before, after, move):
    """Test observed attackers while preserving proven hidden occupancy.

    Witness placeholders supply blockers only. Their invented piece type must
    never become an attacker in this visible-safety filter.
    """
    witness = state.threat_history.witness(before).copy(stack=False)
    witness.push(move)
    king = after.king(state.side)
    if king is None:
        return False
    for origin in base.chess.scan_forward(after.occupied_co[not state.side]):
        piece = after.piece_at(origin)
        if base.geometric_attack(piece.piece_type, piece.color, origin, king, witness):
            return False
    return True


def mix_risk(bank_risk, exposure, unique_positions, weight, prior):
    """Contaminated-belief heuristic; no finite bank certifies certainty.

    Weight0 is the bank-only ablation, with unanimity below certainty. With insurance enabled, small
    or empty physical banks receive less authority regardless of the number
    of distinct repetition histories retained for the same board.
    """
    if weight == 0:
        return min(.999, bank_risk)
    confidence = (min(.9, unique_positions / (unique_positions + prior))
                  if unique_positions else 0)
    bank_weight = (1 - weight) * confidence
    return bank_weight * bank_risk + (1 - bank_weight) * exposure


def choose_move(state, remaining, rng, now=time.monotonic):
    if not math.isfinite(remaining) or remaining <= 0:
        raise ValueError('Clock must be finite and positive')
    state.last_search = {'depth': 0, 'nodes': 0, 'status': 'fallback'}
    started = now()
    reserve = max(.01, min(.5, remaining * .10))
    budget = min(max(0, state.search_ms) / 1000,
                 max(0, remaining - reserve), remaining * .035)
    deadline = started + budget
    board, moves = state.reconstruct()
    if not moves:
        return None
    ranked = []
    for move in sorted(moves, key=lambda m: m.uci()):
        after = base.after_move(board, move)
        generic = base.blocker_risk(state, after, base.capture_square(board, move))
        quick = base.quick_score(board, after, move, state.side) - generic
        ranked.append((observed_safe(state, board, after, move), quick,
                       rng.random(), move, after, generic))
    safe_exists = any(row[0] for row in ranked)
    pool = [row for row in ranked if row[0] == safe_exists]
    pool.sort(key=lambda row: (row[1], row[2]), reverse=True)
    fallback = pool[0][3]
    if budget <= 0 or now() >= deadline:
        return fallback
    detailed, priors, afters, blockers = [], {}, {}, {}
    for _, quick, tie, move, after, generic in pool[:base.MAX_DETAIL_MOVES]:
        if now() >= deadline:
            return fallback
        detail = base.detail_score(state, board, after, move)
        repeat = 35 * sum(old == move for old in state.move_history[-8:])
        detailed.append((quick + detail - repeat, tie, move))
        priors[move] = .3 * detail + .1 * quick - repeat + tie * .05 - .9 * generic
        afters[move], blockers[move] = after, generic
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
            real_deadline = time.monotonic() + max(0, inference_deadline - now())
            state.belief.observe(value, deadline=real_deadline)
    particles = state.belief.particles if not state.belief_events else []
    unique = physical_count(particles)
    if particles or state.threat_weight:
        detailed = [(score + blockers[move], tie, move)
                    for score, tie, move in detailed]
        for move in priors:
            priors[move] += blockers[move]
    worlds = state.belief.sample(state.worlds) if particles else []
    complete = bool(worlds)
    if not worlds:
        worlds = [board.copy(stack=False)]
    risks, exposures, bank_risks = {}, {}, {}
    for _, _, move in detailed:
        if now() >= deadline:
            return fallback
        losses = 0
        for world in particles:
            if now() >= deadline:
                return fallback
            world.push(move)
            try:
                losses += not base.visibly_safe(world, state.side)
            finally:
                world.pop()
        bank_risks[move] = losses / max(1, len(particles))
        exposures[move] = (threat_exposure(state, board, afters[move], move)
                           if state.threat_weight else 0)
        if now() >= deadline:
            return fallback
        risks[move] = mix_risk(bank_risks[move], exposures[move], unique,
                               state.threat_weight, state.confidence_prior)
    # No unanimous sampled-bank veto. One wrong physical hypothesis can assign
    # risk1 to the only real escape; its risk remains a graded ranking input.
    detailed.sort(key=lambda row: (row[0] - (state.loss_cost + 800) * risks[row[2]],
                                  row[1]), reverse=True)
    selected = [row[2] for row in detailed[:12]]
    fallback = selected[0]
    if now() >= deadline:
        return fallback
    pressure = {}
    if state.pressure_weight and complete:
        pressure_deadline = min(deadline, started + .55 * budget)
        try:
            pressure = {move: pressure_score(worlds, move, state.side,
                                             pressure_deadline, now=now)
                        for move in selected}
        except PressureExpired:
            pressure = {}
        # Compare every shortlisted move on the same completed world batch.
        # Partial optional work never gives a subset of moves a bonus.
        if len(pressure) == len(selected):
            for move in selected:
                priors[move] += state.pressure_weight * pressure[move]
    chosen, stats = base.search_root(worlds, selected, priors, deadline, now=now,
                                    complete=complete, max_depth=state.depth,
                                    node_limit=state.nodes, utility_scale=800,
                                    immediate_risks=risks, loss_cost=state.loss_cost)
    chosen = chosen if stats['depth'] else fallback
    stats.update(status='searched' if stats['depth'] else 'fallback',
                 particles=len(particles), physical_positions=unique,
                 belief_mode=state.belief_mode, threat_weight=state.threat_weight,
                 chosen_risk=risks[chosen], chosen_bank_risk=bank_risks[chosen],
                 chosen_exposure=exposures[chosen],
                 chosen_pressure_bonus=state.pressure_weight * pressure.get(chosen, 0),
                 shortlist_min_risk=min(risks[m] for m in selected))
    state.last_search = stats
    return chosen


def serve(stream_in, stream_out, seed, **options):
    if stream_in.readline().strip().lower() != 'fow':
        raise ValueError('Expected fow handshake')
    header = stream_in.readline().split()
    if len(header) != 3 or header[0].lower() not in ('w', 'b'):
        raise ValueError('Expected color, base time, increment')
    base.positive_float(header[1])
    increment = float(header[2])
    if not math.isfinite(increment) or increment < 0:
        raise ValueError('Invalid increment')
    state = State(header[0].lower() == 'w', seed=seed, **options)
    rng, awaiting = random.Random(seed), False
    stream_out.write('ready\n')
    stream_out.flush()
    for raw in stream_in:
        line = raw.strip()
        if len(line) == 64 and set(line) <= base.OBSERVATION_CHARACTERS:
            state.observe(line)
            awaiting = True
            continue
        remaining = base.positive_float(line)
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
    parser.add_argument('--search-ms', type=float, default=900)
    parser.add_argument('--particles', type=int, default=128)
    parser.add_argument('--worlds', type=int, default=4)
    parser.add_argument('--depth', type=int, default=5)
    parser.add_argument('--nodes', type=int, default=200000)
    parser.add_argument('--belief-mode', choices=('original', 'rejuvenated'), default='rejuvenated')
    parser.add_argument('--threat-weight', type=float, default=.35)
    parser.add_argument('--loss-cost', type=float, default=3200)
    parser.add_argument('--confidence-prior', type=float, default=8)
    parser.add_argument('--pressure-weight', type=float, default=0)
    args = vars(parser.parse_args())
    try:
        if not math.isfinite(args['search_ms']) or not 0 <= args['search_ms'] <= 2000:
            raise ValueError('search-ms must be between0 and2000')
        serve(sys.stdin, sys.stdout, **args)
    except (ValueError, EOFError) as error:
        print('bot_v5: ' + str(error), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
