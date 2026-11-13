#!/usr/bin/env python3
"""Seeded local evaluation with actual outcomes and preserved failure evidence."""
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


def schedule(count, seed):
    for index, opponent in enumerate(('baseline.py', 'uniform_random.py')):
        for pair in range(count):
            ours = seed + index * 20000 + pair * 2
            for color in ('white', 'black'):
                yield opponent, pair, color, ours, ours + 1


def summarize(matches):
    result = {}
    for opponent in ('baseline.py', 'uniform_random.py'):
        rows = [row for row in matches if row['opponent'] == opponent]
        entry = {'attempted': len(rows),
                 'failures': sum(row['status'] != 'completed' for row in rows)}
        for label, subset in (('raw', rows),
                              ('clean', [r for r in rows if r['status'] == 'completed'])):
            counts = Counter(row['outcome'] for row in subset)
            denominator = sum(counts[key] for key in ('win', 'draw', 'loss'))
            entry[label] = {key: counts[key] for key in ('win', 'draw', 'loss', 'unresolved')}
            entry[label].update(denominator=denominator, score_fraction=(
                (counts['win'] + 0.5 * counts['draw']) / denominator if denominator else None))
        result[opponent] = entry
    return result


def checkpoint(path, document):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True,
                                    allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trusted-local', action='store_true', required=True)
    parser.add_argument('--games-per-color', type=int, default=10)
    parser.add_argument('--seed', type=int, default=7980)
    parser.add_argument('--base-time', type=float, default=180)
    parser.add_argument('--increment', type=float, default=2)
    parser.add_argument('--max-plies', type=int, default=1000)
    parser.add_argument('--output', type=Path, default=Path('evaluation.json'))
    args = parser.parse_args()
    if not (1 <= args.games_per_color <= 100 and 0 <= args.seed <= 2147400000 and
            1 <= args.max_plies <= 1000 and math.isfinite(args.base_time) and
            0.1 <= args.base_time <= 180 and math.isfinite(args.increment) and
            0 <= args.increment <= 2):
        parser.error('Invalid bounded evaluation configuration')
    if args.output.exists() or args.output.with_name(args.output.name + '.tmp').exists():
        parser.error('Choose a fresh output path')
    names = ('bot.py', 'baseline.py', 'uniform_random.py', 'referee.py',
             'requirements.txt', 'evaluate.py')
    document = {
        'schema_version': 1, 'status': 'running',
        'environment': {'python': sys.version, 'platform': platform.platform(),
                        'chess': importlib.metadata.version('chess'),
                        'python_chess': importlib.metadata.version('python-chess')},
        'source_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in names},
        'configuration': {k: v for k, v in vars(args).items() if k != 'output'},
        'planned_matches': 4 * args.games_per_color, 'matches': [], 'aggregate': {},
        'limitations': ['Timing depends on host load; seeds alone do not fix outcomes.',
                       'Color pairs are not independent samples.',
                       'Raw WDL includes forfeits; clean WDL excludes failures.']}
    checkpoint(args.output, document)
    failed = False
    for index, (opponent, pair, color, ours, theirs) in enumerate(
            schedule(args.games_per_color, args.seed)):
        bot = [sys.executable, '-u', str(ROOT / 'bot.py'), '--seed', str(ours)]
        enemy = [sys.executable, '-u', str(ROOT / opponent), '--seed', str(theirs)]
        white, black = (bot, enemy) if color == 'white' else (enemy, bot)
        row = dict(id=str(index), opponent=opponent, pair=pair, color=color,
                   bot_seed=ours, opponent_seed=theirs, white_argv=white,
                   black_argv=black, outcome='unresolved')
        fatal = False
        try:
            replay = run_match(white, black, base=args.base_time, increment=args.increment,
                               max_plies=args.max_plies, match_id=str(index))
            result = replay['result']
            if result == '1/2-1/2':
                row['outcome'] = 'draw'
            elif result in ('1-0', '0-1'):
                winner = 'white' if result == '1-0' else 'black'
                row['outcome'] = 'win' if winner == color else 'loss'
            valid = (result in ('1-0', '0-1', '1/2-1/2') and
                     replay.get('reason') in NORMAL_REASONS and
                     'failedSide' not in replay and not replay.get('studentReports'))
            row.update(status='completed' if valid else 'failure', replay=replay)
            failed |= not valid
        except (Exception, KeyboardInterrupt) as error:
            row.update(status='exception', exception_type=type(error).__name__,
                       exception_message=str(error))
            failed = fatal = True
        document['matches'].append(row)
        document['aggregate'] = summarize(document['matches'])
        if fatal:
            document['status'] = 'aborted'
        checkpoint(args.output, document)
        if fatal:
            return 1
    document['status'] = 'completed_with_failures' if failed else 'completed'
    checkpoint(args.output, document)
    print(args.output)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
