#!/usr/bin/env python3
"""Offline-only V4 risk training. Truth supplies labels, never runtime features.

Uses NumPy only for fitting. Creates a design/split report before extracting
features or fitting; both --model and --output must be fresh. The fixed final
test partition is scored once after validation selects the regularization.
Example:
  .venv/bin/python -B train_risk_model.py outputs/v3-holdout-171980/batch-*.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
# Avoid multiplying the host's training work across every available CPU.
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')

import chess
import numpy as np

from referee import FowBoard, fow_moves, observation, outcome, position_key
from uniform_random import ObservationTracker, candidates
from risk_model import (EPSILON, FEATURE_NAMES, FEATURE_VERSION, RiskHistory,
                        RiskModel, features, geometric_score)

ROOT = Path(__file__).resolve().parent
SPLIT_SALT = 'ffwb-risk-v4-split-2026-11-13'
REGULARIZATION = (0.001, 0.01, 0.1, 1.0)
NORMALIZED_CLIP = 8.0
DESIGN = {
    'split_salt': SPLIT_SALT,
    'split_method': 'SHA256-sort unique (candidate_seed, opponent_seed) groups; 70/15/15 percent',
    'split_unit': 'entire color-swapped seed pair',
    'feature_version': FEATURE_VERSION,
    'feature_names': list(FEATURE_NAMES),
    'rows': 'all legal actions surviving observation-board visible-safety filter, before 64/12 cutoffs',
    'target': 'official referee immediate king-capturability loss after counterfactual own move',
    'model': 'unweighted regularized logistic regression; no class weighting',
    'regularization_grid': list(REGULARIZATION),
    'selection': 'lowest validation counterfactual action log-loss, tie favors larger regularization',
    'normalization': 'training-only mean and population SD; SD below 1e-8 replaced by 1; clip standardized values to [-8,8]',
    'probability_clip': EPSILON,
    'optimizer': 'Newton with backtracking, max50 iterations, gradient tolerance1e-7, intercept unpenalized',
    'baselines': ['training prevalence', 'fixed geometric score with training-only scalar logistic calibration'],
    'test_policy': 'test labels not used for model selection; evaluate final chosen model once, no refit',
    'limitations': 'V3 vs frozenV2 induced distribution; no universal calibration or strength claim',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def write_json(path, value, exclusive=False):
    with path.open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def split_pairs(pairs):
    """Split full pair keys before looking at labels or fitting anything."""
    unique = sorted(set(pairs))
    if len(unique) < 20:
        raise ValueError('At least20 complete seed pairs are required')
    ranked = sorted(unique, key=lambda pair: sha(f'{SPLIT_SALT}:{pair[0]}:{pair[1]}'.encode()))
    ntrain, nvalid = int(len(ranked) * .70), int(len(ranked) * .15)
    return {pair: ('train' if i < ntrain else 'validation' if i < ntrain + nvalid else 'test')
            for i, pair in enumerate(ranked)}


def read_inputs(paths):
    documents, manifest, pairs = [], [], {}
    referee_hashes = {name: sha((ROOT / name).read_bytes())
                      for name in ('referee.py', 'uniform_random.py')}
    for path in paths:
        raw = path.read_bytes()
        doc = json.loads(raw)
        if (doc.get('status') != 'completed' or doc.get('source_unchanged') is not True
                or len(doc['matches']) != doc['planned_matches']):
            raise ValueError('Input must be a complete unchanged-source arena evaluation')
        for name, digest in referee_hashes.items():
            if doc['source_sha256'].get(str(ROOT / name)) != digest:
                raise ValueError(f'Official replay dependency differs: {name}')
        manifest.append({'path': str(path.resolve()), 'sha256': sha(raw),
                         'matches': len(doc['matches'])})
        for match in doc['matches']:
            if match.get('status') != 'completed':
                raise ValueError('Failure matches are not silently excluded')
            pair = (match['bot_seed'], match['opponent_seed'])
            colors = pairs.setdefault(pair, [])
            colors.append(match['color'])
        documents.append((path, doc))
    if any(sorted(colors) != ['black', 'white'] for colors in pairs.values()):
        raise ValueError('Inputs require exactly both colors for each distinct seed pair')
    return documents, manifest, split_pairs(pairs)


def build_corpus(documents, assignment):
    rows, labels, turns, group_ids, geometric = [], [], [], [], []
    all_legal = all_hard_excluded = verified_frames = 0
    for path, doc in documents:
        for match in doc['matches']:
            side = match['color'] == 'white'
            tracker, history = ObservationTracker(side), RiskHistory(side)
            state = SimpleNamespace(side=side, tracker=tracker, risk_history=history)
            truth = FowBoard()
            repetitions = Counter({position_key(truth): 1})
            frames = match['replay']['frames']
            pair = (match['bot_seed'], match['opponent_seed'])
            partition = assignment[pair]
            maximum = doc['configuration']['max_plies']
            for ply, frame in enumerate(frames):
                if ply:
                    if outcome(truth, repetitions) or ply - 1 >= maximum:
                        raise ValueError('Replay continued after termination')
                    played = chess.Move.from_uci(frame['move'])
                    if played not in fow_moves(truth):
                        raise ValueError('Illegal replay move')
                    truth.push(played)
                    repetitions[position_key(truth)] += 1
                truth_text = ''.join(truth.piece_at(s).symbol() if truth.piece_at(s) else '-'
                                     for s in chess.SQUARES)
                if (frame['ply'] != ply or frame['board'] != truth_text
                        or frame['white'] != observation(truth, True)
                        or frame['black'] != observation(truth, False)):
                    raise ValueError('Replay frame differs from official referee reconstruction')
                verified_frames += 1
                if ply == len(frames) - 1:
                    break
                # Runtime-visible state receives only this side's current view.
                tracker.observe(frame[match['color']])
                history.observe(frame[match['color']])
                if truth.turn != side:
                    continue
                before, legal = candidates(tracker.current, side, tracker.rights, tracker.previous)
                if set(legal) != set(fow_moves(truth)):
                    raise ValueError('Observation-derived legal actions disagree with referee')
                prepared = []
                for move in sorted(legal, key=lambda m: m.uci()):
                    after = before.copy(stack=False)
                    after.push(move)
                    king = after.king(side)
                    prepared.append((king is not None and not after.is_attacked_by(not side, king),
                                     move, after))
                has_visible_safe = any(row[0] for row in prepared)
                eligible = [row for row in prepared if row[0] == has_visible_safe]
                all_legal += len(legal)
                all_hard_excluded += len(legal) - len(eligible)
                start = len(rows)
                for _, move, after in eligible:
                    vector = features(state, before, after, move)
                    # Truth is used exclusively below to construct the target.
                    counterfactual = truth.copy(stack=False)
                    counterfactual.push(move)
                    result = outcome(counterfactual, Counter({position_key(counterfactual): 1}))
                    loss = result is not None and result[1] == 'King capturable'
                    rows.append(vector)
                    geometric.append(geometric_score(vector))
                    labels.append(int(loss))
                    group_ids.append(partition)
                turns.append({'start': start, 'stop': len(rows), 'partition': partition,
                              'pair': list(pair), 'source': path.name, 'id': match['id'],
                              'ply': ply, 'color': match['color']})
                committed = chess.Move.from_uci(frames[ply + 1]['move'])
                history.record_move(before, committed)
                tracker.record_move(committed)
            terminal = outcome(truth, repetitions)
            if terminal is None and len(truth.move_stack) >= maximum:
                terminal = ('1/2-1/2', 'Move limit reached')
            if terminal != (match['replay']['result'], match['replay']['reason']):
                raise ValueError('Replay result differs from referee')
        print(f'Extracted {path.name}: cumulative {len(turns)} turns, {len(rows)} actions', flush=True)
    return (np.asarray(rows, dtype=np.float64), np.asarray(labels, dtype=np.float64),
            np.asarray(geometric, dtype=np.float64), np.asarray(group_ids), turns,
            {'all_legal_actions': all_legal, 'hard_filter_excluded_actions': all_hard_excluded,
             'verified_frames': verified_frames})


def sigmoid(z):
    return np.exp(-np.logaddexp(0, -z))


def fit_logistic(x, y, regularization):
    n, count = x.shape
    design = np.column_stack((np.ones(n), x))
    beta = np.zeros(count + 1)
    prevalence = float(y.mean())
    if not 0 < prevalence < 1:
        raise ValueError('Both training classes are required')
    beta[0] = math.log(prevalence / (1 - prevalence))
    penalty = np.full(count + 1, regularization)
    penalty[0] = 0
    def objective(b):
        z = design @ b
        return float(np.mean(np.logaddexp(0, z) - y * z) + .5 * np.dot(penalty, b * b))
    converged = False
    for iteration in range(50):
        p = sigmoid(design @ beta)
        gradient = design.T @ (p - y) / n + penalty * beta
        if np.max(np.abs(gradient)) < 1e-7:
            converged = True
            break
        curvature = np.maximum(p * (1 - p), 1e-9)
        hessian = (design.T @ (design * curvature[:, None])) / n + np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        old, fraction = objective(beta), 1.0
        while fraction > 2 ** -20:
            trial = beta - fraction * step
            if objective(trial) <= old - 1e-4 * fraction * float(gradient @ step):
                beta = trial
                break
            fraction *= .5
        else:
            break
    return beta, {'iterations': iteration + 1, 'converged': converged,
                  'objective': objective(beta), 'gradient_max_abs': float(np.max(np.abs(gradient)))}


def proper_scores(y, p):
    p = np.clip(p, EPSILON, 1 - EPSILON)
    return {'actions': len(y), 'positive_actions': int(y.sum()), 'prevalence': float(y.mean()),
            'brier': float(np.mean((p - y) ** 2)),
            'log_loss': float(-np.mean(y * np.log(p) + (1 - y) * np.log1p(-p)))}


def evaluate(y, probabilities, turns, partition, selected):
    scores = proper_scores(y[selected], probabilities[selected])
    fatal = safe_turns = retained_turns = kept_safe = all_safe = nturns = 0
    expected_random = expected_random12 = 0.0
    for turn in turns:
        if turn['partition'] != partition:
            continue
        a, b = turn['start'], turn['stop']
        yy, pp = y[a:b], probabilities[a:b]
        order = np.argsort(pp, kind='stable')
        nturns += 1
        fatal += int(yy[order[0]])
        expected_random += float(yy.mean())
        safe = int((yy == 0).sum())
        all_safe += safe
        kept_safe += int((yy[order[:12]] == 0).sum())
        if safe:
            safe_turns += 1
            retained_turns += bool((yy[order[:12]] == 0).any())
            k, unsafe = min(12, len(yy)), len(yy) - safe
            expected_random12 += 1 - (math.comb(unsafe, k) / math.comb(len(yy), k) if unsafe >= k else 0)
    bins = []
    cutoffs = (0, .01, .025, .05, .1, .2, .4, .6, .8, 1)
    subset_y, subset_p = y[selected], probabilities[selected]
    for lo, hi in zip(cutoffs[:-1], cutoffs[1:]):
        mask = (subset_p >= lo) & (subset_p < hi)
        bins.append({'range': [lo, hi], 'actions': int(mask.sum()),
                     'mean_prediction': float(subset_p[mask].mean()) if mask.any() else None,
                     'observed_fraction': float(subset_y[mask].mean()) if mask.any() else None})
    scores.update(turns=nturns, top_choice_fatal_turns=fatal,
                  top_choice_fatal_rate=fatal / nturns,
                  turns_with_safe_alternative=safe_turns,
                  top12_retained_safe_turns=retained_turns,
                  top12_safe_turn_retention=retained_turns / safe_turns if safe_turns else None,
                  top12_safe_action_retention=kept_safe / all_safe if all_safe else None,
                  uniform_random_expected_fatal_rate=expected_random / nturns,
                  uniform_random_top12_expected_safe_turn_retention=expected_random12 / safe_turns if safe_turns else None,
                  calibration_bins=bins,
                  tie_policy='stable UCI lexicographic order, not tactical tie breaking')
    return scores


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('--model', type=Path, default=ROOT / 'risk_model.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/risk-model-training-v4.json')
    args = parser.parse_args(argv)
    if args.model.exists() or args.output.exists():
        raise ValueError('Model and report outputs must both be fresh paths')
    started = time.monotonic()
    documents, manifest, assignment = read_inputs(args.inputs)
    source_hashes = {name: sha((ROOT / name).read_bytes()) for name in
                     ('risk_model.py', 'train_risk_model.py', 'referee.py', 'uniform_random.py')}
    split = [{'candidate_seed': pair[0], 'opponent_seed': pair[1], 'partition': partition}
             for pair, partition in sorted(assignment.items())]
    registered = {'design': DESIGN, 'split': split, 'inputs': manifest, 'source_sha256': source_hashes}
    report = {'schema_version': 1, 'status': 'design_registered_before_extraction_and_fitting',
              **registered, 'design_sha256': sha(canonical(registered)),
              'numpy_version_training_only': np.__version__}
    write_json(args.output, report, exclusive=True)
    print(f'Registered design {report["design_sha256"]}; {dict(Counter(assignment.values()))}', flush=True)
    x, y, geometry, partition, turns, counts = build_corpus(documents, assignment)
    train, valid = partition == 'train', partition == 'validation'
    mean, scale = x[train].mean(axis=0), x[train].std(axis=0)
    scale = np.where(scale < 1e-8, 1.0, scale)
    normalized = np.clip((x - mean) / scale, -NORMALIZED_CLIP, NORMALIZED_CLIP)
    grid, fitted = [], {}
    for penalty in REGULARIZATION:
        beta, diagnostics = fit_logistic(normalized[train], y[train], penalty)
        validation_prediction = sigmoid(beta[0] + normalized[valid] @ beta[1:])
        result = {'regularization': penalty, 'validation': proper_scores(y[valid], validation_prediction),
                  'fit': diagnostics}
        grid.append(result)
        fitted[penalty] = beta
        print(f'L2 {penalty}: validation logloss {result["validation"]["log_loss"]:.6f}', flush=True)
    selected = min(grid, key=lambda r: (r['validation']['log_loss'], -r['regularization']))
    beta = fitted[selected['regularization']]
    # Only after the selection is frozen is the final model applied to test rows.
    predicted = np.clip(sigmoid(beta[0] + normalized @ beta[1:]), EPSILON, 1 - EPSILON)
    geo_mean, geo_scale = float(geometry[train].mean()), float(geometry[train].std())
    if geo_scale < 1e-8:
        geo_scale = 1.0
    geo_x = np.clip((geometry - geo_mean) / geo_scale, -NORMALIZED_CLIP, NORMALIZED_CLIP)[:, None]
    geo_beta, geo_diagnostics = fit_logistic(geo_x[train], y[train], 0.001)
    geo_prediction = np.clip(sigmoid(geo_beta[0] + geo_x[:, 0] * geo_beta[1]), EPSILON, 1 - EPSILON)
    constant = np.full(len(y), y[train].mean())
    model = {'schema_version': 1, 'feature_version': FEATURE_VERSION,
             'feature_names': list(FEATURE_NAMES), 'mean': mean.tolist(), 'scale': scale.tolist(),
             'coefficients': beta[1:].tolist(), 'intercept': float(beta[0]),
             'normalized_clip': NORMALIZED_CLIP, 'probability_clip': EPSILON,
             'regularization': selected['regularization'],
             'training': {'design_sha256': report['design_sha256'], 'inputs': manifest,
                          'source_sha256': source_hashes, 'split': split,
                          'label': DESIGN['target'], 'distribution': DESIGN['limitations'],
                          'class_weighting': False}}
    runtime = RiskModel(model)
    parity = max(abs(runtime.predict_features(x[i]) - predicted[i]) for i in
                 range(0, len(y), max(1, len(y) // 1000)))
    if parity > 1e-12:
        raise ValueError(f'Runtime predictor differs from NumPy: {parity}')
    evaluations = {}
    for name in ('train', 'validation', 'test'):
        mask = partition == name
        evaluations[name] = {method: evaluate(y, prediction, turns, name, mask) for method, prediction in
                             (('learned', predicted), ('geometric', geo_prediction), ('constant', constant))}
    if any(sha((ROOT / name).read_bytes()) != digest for name, digest in source_hashes.items()):
        raise ValueError('A training or referee source changed during this run')
    write_json(args.model, model, exclusive=True)
    report.update(status='completed', elapsed_seconds=time.monotonic() - started,
                  corpus={**counts, 'actions': len(y), 'turns': len(turns), 'matches': 2 * len(assignment),
                          'feature_count': len(FEATURE_NAMES), 'pairs': len(assignment)},
                  validation_grid=grid, selected_regularization=selected['regularization'],
                  final_test_evaluations=1, metrics=evaluations,
                  geometric_baseline={'mean': geo_mean, 'scale': geo_scale,
                                      'intercept': float(geo_beta[0]), 'slope': float(geo_beta[1]),
                                      'regularization': 0.001, 'fit': geo_diagnostics},
                  runtime_numpy_max_absolute_difference=parity,
                  model={'path': str(args.model.resolve()), 'sha256': sha(args.model.read_bytes())},
                  limitations=[
                      'Counterfactual actions and adjacent turns share true states and are correlated.',
                      'Scores are action-weighted; turns with more legal actions have more weight during fitting.',
                      'Pair-group splitting prevents color-pair overlap but does not establish independence of common openings.',
                      'Top choice uses only risk and UCI tie-breaking, not the integrated tactical policy.',
                      'Safe retention concerns immediate survival, not eventual win probability.',
                      'No playing-strength test or arena rating is supplied by this training report.',
                      'The final test is now consumed and cannot select another model or feature change.'])
    write_json(args.output, report)
    print(json.dumps({'status': 'completed', 'test': evaluations['test'],
                      'model_sha256': report['model']['sha256']}, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
