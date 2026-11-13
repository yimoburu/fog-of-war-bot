"""Statistical and failure-accounting checks; no bot subprocesses needed."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import arena_eval as evaluation


def row(pair, color, outcome='win', status='completed'):
    return dict(pair=pair, color=color, outcome=outcome, status=status, opponent='old.py')


class EvaluationTests(unittest.TestCase):
    def test_schedule_swaps_colors_preserving_seeds(self):
        games = list(evaluation.schedule(['old.py', 'baseline.py'], 2, 10))
        self.assertEqual(len(games), 8)
        self.assertEqual([r['color'] for r in games[:2]], ['white', 'black'])
        self.assertEqual(games[0]['bot_seed'], games[1]['bot_seed'])
        self.assertEqual(games[0]['opponent_seed'], games[1]['opponent_seed'])
        self.assertNotEqual(games[0]['bot_seed'], games[2]['bot_seed'])
        self.assertNotEqual(games[0]['bot_seed'], games[4]['bot_seed'])

    def test_failure_preserves_scored_loss(self):
        replay = dict(result='0-1', reason='Time forfeit', failedSide='white')
        self.assertEqual(evaluation.classify(replay, 'white'), ('loss', 'failure'))
        replay.update(result='*', reason='Infrastructure failure')
        self.assertEqual(evaluation.classify(replay, 'white'), ('unresolved', 'failure'))

    def test_warning_is_not_clean_success(self):
        replay = dict(result='1-0', reason='King capturable', warnings=[{'code': 'stderr_limit'}])
        self.assertEqual(evaluation.classify(replay, 'white'), ('win', 'failure'))
        replay['warnings'] = []
        self.assertEqual(evaluation.classify(replay, 'black'), ('loss', 'completed'))

    def test_pair_mean_and_non_degenerate_saturated_interval(self):
        rows = [row(i, color) for i in range(40) for color in ('white', 'black')]
        estimate = evaluation.paired_uncertainty(rows)
        self.assertEqual(estimate['independent_units'], 40)
        self.assertEqual(estimate['relative_elo_estimate'], '+infinity')
        self.assertLess(estimate['score_interval'][0], 1)
        self.assertGreater(estimate['score_interval'][0], .5)
        self.assertTrue(estimate['saturated'])
        self.assertIsNone(estimate['absolute_arena_elo'])
        json.dumps(estimate, allow_nan=False)

    def test_partial_or_failed_pair_has_no_inference(self):
        for rows in ([row(0, 'white')],
                     [row(0, 'white'), row(0, 'black', status='failure')],
                     [row(0, 'white'), row(0, 'white')]):
            self.assertFalse(evaluation.paired_uncertainty(rows)['available'])

    def test_raw_failure_counts_and_balanced_score(self):
        rows = [row(0, 'white'), row(0, 'black', 'loss', 'failure')]
        summary = evaluation.summarize(rows, ['old.py'])['old.py']
        self.assertEqual(summary['raw']['score'], .5)
        self.assertEqual(summary['clean_descriptive_only']['score'], 1)
        self.assertEqual(summary['failures'], 1)
        self.assertFalse(summary['paired_relative_strength']['available'])
        self.assertEqual(evaluation.relative_elo(.5), 0)
        self.assertAlmostEqual(evaluation.relative_elo(.75), 190.84850188786498)

    def test_runtime_exception_keeps_schedule_and_cause(self):
        scheduled = dict(next(evaluation.schedule(['old.py'], 1, 42)), id='0')
        with patch.object(evaluation, 'run_match', side_effect=RuntimeError('test failure')):
            result = evaluation.run_game(scheduled, Path('bot.py'), [], [], 180, 2, 1000)
        self.assertEqual(result['status'], 'exception')
        self.assertEqual(result['exception_message'], 'test failure')
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['bot_seed'], 42)

    def test_atomic_checkpoint_and_source_hash_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'evidence.json'
            evaluation.checkpoint(path, {'matches': [1]})
            self.assertEqual(json.loads(path.read_text()), {'matches': [1]})
            self.assertFalse(path.with_name(path.name + '.tmp').exists())
            before = evaluation.source_hashes([path])
            evaluation.checkpoint(path, {'matches': [1, 2]})
            self.assertNotEqual(before, evaluation.source_hashes([path]))


if __name__ == '__main__':
    unittest.main()
