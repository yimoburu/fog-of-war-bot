#!/usr/bin/env python3
"""Paired local matches and relative-strength evidence, never absolute arena Elo."""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys

from referee import run_match

ROOT = Path(__file__).resolve().parent
NORMAL_REASONS = {'King capturable', 'Threefold repetition', '50-move rule',
                  'No legal moves', 'Move limit reached'}
SCORES = {'win': 1.0, 'draw': 0.5, 'loss': 0.0}


def schedule(opponents, pairs, seed):
    """A seed pair is a unit: replay the same seeds with the colors reversed."""
    for opponent_index, opponent in enumerate(opponents):
        for pair in range(pairs):
            ours = seed + opponent_index * 20000 + pair * 2
            for color in ('white', 'black'):
                yield dict(opponent=str(opponent), pair=pair, color=color,
                           bot_seed=ours, opponent_seed=ours + 1)


def classify(replay, color):
    result = replay.get('result')
    outcome = 'unresolved'
    if result == '1/2-1/2':
        outcome = 'draw'
    elif result in ('1-0', '0-1'):
        winner = 'white' if result == '1-0' else 'black'
        outcome = 'win' if winner == color else 'loss'
    normal = (outcome != 'unresolved' and replay.get('reason') in NORMAL_REASONS
              and 'failedSide' not in replay and not replay.get('studentReports')
              and not replay.get('warnings'))
    return outcome, 'completed' if normal else 'failure'


def relative_elo(score):
    """Logistic score transform, not a fitted rating or an arena anchor."""
    if score <= 0:
        return '-infinity'
    if score >= 1:
        return '+infinity'
    return 400.0 * math.log10(score / (1.0 - score))


def paired_uncertainty(rows):
    """Conservative bounded-score interval; color games stay in the same pair.

    Hoeffding's inequality bounds independent pair means in [0,1]. This avoids
    bootstrap intervals collapsing at 100% wins. Seed pairs are assumed
    independent; shared openings/policy and host load limit that assumption.
    """
    groups = {}
    for row in rows:
        groups.setdefault(row['pair'], []).append(row)
    if not groups or any(len(pair) != 2 or {r['color'] for r in pair} != {'white', 'black'}
                         or any(r['status'] != 'completed' or r['outcome'] not in SCORES
                                for r in pair) for pair in groups.values()):
        return {'available': False, 'reason': 'Requires complete, failure-free color pairs.'}
    pair_scores = [sum(SCORES[r['outcome']] for r in pair) / 2
                   for pair in groups.values()]
    count = len(pair_scores)
    mean = sum(pair_scores) / count
    radius = math.sqrt(math.log(40.0) / (2.0 * count))
    lower, upper = max(0.0, mean - radius), min(1.0, mean + radius)
    return {
        'available': True, 'independent_units': count, 'unit': 'color-swapped seed pair',
        'method': '95% Hoeffding bounded-pair-mean interval',
        'assumption': 'Independent seed pairs; shared opening/policy and host load may violate this.',
        'score_interval': [lower, upper], 'score_estimate': mean,
        'relative_elo_estimate': relative_elo(mean),
        'relative_elo_interval': [relative_elo(lower), relative_elo(upper)],
        'saturated': mean in (0.0, 1.0),
        'absolute_arena_elo': None,
        'interpretation': 'Relative logistic score transform versus this opponent only; no MIT arena rating. '
                          'Infinity denotes an unbounded estimate, not proven infinite strength.'}


def counts(rows):
    counter = Counter(row['outcome'] for row in rows)
    resolved = sum(counter[k] for k in SCORES)
    return {**{k: counter[k] for k in ('win', 'draw', 'loss', 'unresolved')},
            'resolved': resolved,
            'score': ((counter['win'] + .5 * counter['draw']) / resolved if resolved else None)}


def summarize(matches, opponents):
    aggregate = {}
    for opponent in opponents:
        rows = [r for r in matches if r['opponent'] == str(opponent)]
        clean = [r for r in rows if r['status'] == 'completed']
        aggregate[str(opponent)] = {
            'attempted': len(rows), 'failures': len(rows) - len(clean),
            'raw': counts(rows), 'clean_descriptive_only': counts(clean),
            'by_color': {color: counts([r for r in rows if r['color'] == color])
                         for color in ('white', 'black')},
            'paired_relative_strength': paired_uncertainty(rows)}
    return aggregate


def checkpoint(path, document):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True,
                                    allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def source_hashes(paths):
    return {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def run_game(row, candidate, candidate_args, opponent_args, base, increment, max_plies):
    ours = [sys.executable, '-u', str(candidate), *candidate_args, '--seed', str(row['bot_seed'])]
    theirs = [sys.executable, '-u', row['opponent'], *opponent_args,
              '--seed', str(row['opponent_seed'])]
    white, black = (ours, theirs) if row['color'] == 'white' else (theirs, ours)
    result = {**row, 'white_argv': white, 'black_argv': black, 'outcome': 'unresolved'}
    try:
        replay = run_match(white, black, base=base, increment=increment,
                           max_plies=max_plies, match_id=row['id'])
        outcome, status = classify(replay, row['color'])
        result.update(outcome=outcome, status=status, replay=replay)
    except (Exception, KeyboardInterrupt) as error:
        result.update(status='exception', exception_type=type(error).__name__,
                      exception_message=str(error))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-local', required=True, action='store_true',
                        help='Run only trusted local scripts; this harness is not a sandbox.')
    parser.add_argument('--candidate', type=Path, default=ROOT / 'bot.py')
    parser.add_argument('--opponent', type=Path, action='append', required=True)
    parser.add_argument('--candidate-arg', action='append', default=[])
    parser.add_argument('--opponent-arg', action='append', default=[])
    parser.add_argument('--source', type=Path, action='append', default=[],
                        help='Additional implementation dependency to hash.')
    parser.add_argument('--games-per-color', type=int, default=20)
    parser.add_argument('--seed', type=int, default=17980)
    parser.add_argument('--base-time', type=float, default=180)
    parser.add_argument('--increment', type=float, default=2)
    parser.add_argument('--max-plies', type=int, default=1000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not (1 <= args.games_per_color <= 1000 and 0 <= args.seed <= 2140000000
            and 1 <= args.max_plies <= 10000 and math.isfinite(args.base_time)
            and 0 < args.base_time <= 3600 and math.isfinite(args.increment)
            and 0 <= args.increment <= 60):
        parser.error('Invalid bounded match configuration')
    candidate = args.candidate.resolve()
    opponents = [p.resolve() for p in args.opponent]
    if len(opponents) != len(set(opponents)):
        parser.error('Opponent paths must be unique')
    dependencies = [ROOT / name for name in ('arena_eval.py', 'referee.py', 'uniform_random.py',
                    'requirements.txt', 'belief.py', 'search.py') if (ROOT / name).exists()]
    paths = sorted(set([candidate, *opponents, *dependencies, *args.source]))
    if any(not p.is_file() for p in paths):
        parser.error('Every script/source must be an existing regular file')
    if args.output.exists() or args.output.with_name(args.output.name + '.tmp').exists():
        parser.error('Choose a fresh output path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    hashes = source_hashes(paths)
    configuration = {k: v for k, v in vars(args).items() if k != 'output'}
    configuration.update(candidate=str(candidate), opponent=list(map(str, opponents)),
                         source=list(map(str, args.source)))
    document = {'schema_version': 1, 'status': 'running', 'absolute_arena_elo': None,
                'environment': {'python': sys.version, 'platform': platform.platform(),
                                'chess': importlib.metadata.version('chess'),
                                'python_chess': importlib.metadata.version('python-chess')},
                'source_sha256': hashes, 'configuration': configuration,
                'planned_matches': 2 * args.games_per_color * len(opponents),
                'matches': [], 'aggregate': {},
                'limitations': ['NO ABSOLUTE MIT ARENA ELO: opponents have no verified rating anchors.',
                                'Relative logistic score differences are not official ratings.',
                                'Color-swapped seed pairs, not individual games, are uncertainty units.',
                                'Shared openings/policies and host load limit independence.',
                                'Serial trusted local execution does not reproduce arena isolation.',
                                'Raw results retain failures; relative inference requires failure-free pairs.']}
    checkpoint(args.output, document)
    for index, row in enumerate(schedule(opponents, args.games_per_color, args.seed)):
        row['id'] = str(index)
        result = run_game(row, candidate, args.candidate_arg, args.opponent_arg,
                          args.base_time, args.increment, args.max_plies)
        document['matches'].append(result)
        document['aggregate'] = summarize(document['matches'], opponents)
        print(f"{index + 1}/{document['planned_matches']} {Path(row['opponent']).name} "
              f"{row['color']} {result['outcome']} ({result['status']})", flush=True)
        try:
            drift = source_hashes(paths) != hashes
        except OSError:
            drift = True
        if drift:
            document['status'] = 'aborted_source_changed'
            document['source_unchanged'] = False
            document['aggregate'] = {}  # Mixed-code results cannot establish relative strength.
        elif result['status'] == 'exception':
            document['status'] = 'aborted'
        checkpoint(args.output, document)
        if document['status'] != 'running':
            return 1
    failed = any(row['status'] != 'completed' for row in document['matches'])
    document['status'] = 'completed_with_failures' if failed else 'completed'
    document['source_unchanged'] = True
    checkpoint(args.output, document)
    print(args.output, flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
