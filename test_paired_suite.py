"""Suite schedule and fail-closed evidence aggregation, without playing games."""
import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arena_eval import schedule
from run_paired_suite import ROOT, V2_SNAPSHOT_FILES, aggregate_children, child_command, main, plan_batches


def evidence_fixture():
    jobs = plan_batches(pairs=4, batch_pairs=2, seed=10, stride=20)
    for job in jobs:
        job['output'] = '/output/' + job['id'] + '.json'
    plan = {'pairs': 4, 'planned_matches': 8, 'jobs': jobs,
            'python_executable': '/python', 'candidate': '/repo/bot.py',
            'opponent': '/repo/v2_bot.py', 'candidate_args': ['--risk-policy', 'robust'],
            'opponent_args': ['--search-ms', '180'], 'base_time': 180, 'increment': 2,
            'max_plies': 1000, 'source_sha256': {'/repo/bot.py': 'abc', '/repo/v2_bot.py': 'def'}}
    children = {}
    for job in jobs:
        rows = []
        for index, scheduled in enumerate(schedule([plan['opponent']], job['pairs'], job['seed'])):
            ours = ['/python', '-u', plan['candidate'], *plan['candidate_args'],
                    '--seed', str(scheduled['bot_seed'])]
            theirs = ['/python', '-u', plan['opponent'], *plan['opponent_args'],
                      '--seed', str(scheduled['opponent_seed'])]
            white = scheduled['color'] == 'white'
            rows.append({**scheduled, 'id': str(index), 'status': 'completed', 'outcome': 'win',
                         'white_argv': ours if white else theirs,
                         'black_argv': theirs if white else ours,
                         'replay': {'result': '1-0' if white else '0-1', 'reason': 'King capturable'}})
        children[job['id']] = {'returncode': 0, 'document': {
            'status': 'completed', 'source_unchanged': True,
            'source_sha256': dict(plan['source_sha256']), 'planned_matches': 2 * job['pairs'],
            'configuration': {'candidate': plan['candidate'], 'opponent': [plan['opponent']],
                              'candidate_arg': plan['candidate_args'], 'opponent_arg': plan['opponent_args'],
                              'games_per_color': job['pairs'], 'seed': job['seed'],
                              'base_time': 180, 'increment': 2, 'max_plies': 1000, 'trusted_local': True},
            'matches': rows}}
    return plan, children


class PairedSuiteTests(unittest.TestCase):
    def test_default_batches_keep_pairs_intact_and_seeds_disjoint(self):
        jobs = plan_batches()
        self.assertEqual([job['pairs'] for job in jobs], [25, 25, 25, 25])
        seeds = [seed for job in jobs for seed in range(job['seed'], job['seed'] + 2 * job['pairs'])]
        self.assertEqual(len(seeds), 200)
        self.assertEqual(len(set(seeds)), 200)
        self.assertEqual([job['pairs'] for job in plan_batches(7, 3)], [3, 3, 1])
        with self.assertRaises(ValueError):
            plan_batches(4, 2, seed_bases=[10, 13])
        with self.assertRaises(ValueError):
            plan_batches(4, 2, seed_bases=[10])

    def test_complete_evidence_namespaces_pair_ids_before_inference(self):
        plan, children = evidence_fixture()
        result = aggregate_children(plan, children, plan['source_sha256'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['matches']), 8)
        self.assertEqual(len({row['pair'] for row in result['matches']}), 4)
        self.assertEqual(len({row['id'] for row in result['matches']}), 8)
        strength = result['aggregate'][plan['opponent']]['paired_relative_strength']
        self.assertEqual(strength['independent_units'], 4)
        self.assertTrue(strength['available'])
        self.assertIsNone(result['absolute_arena_elo'])
        self.assertEqual(children['batch-000']['document']['matches'][0]['pair'], 0)

    def test_no_subset_aggregation_for_missing_failed_or_changed_child(self):
        plan, original = evidence_fixture()
        mutations = (
            lambda data: data.pop('batch-001'),
            lambda data: data['batch-001'].update(returncode=1),
            lambda data: data['batch-001']['document'].update(status='running'),
            lambda data: data['batch-001']['document'].update(source_unchanged=False),
            lambda data: data['batch-001']['document'].update(source_sha256={}),
            lambda data: data['batch-001']['document']['matches'].pop(),
        )
        for mutate in mutations:
            children = copy.deepcopy(original)
            mutate(children)
            result = aggregate_children(plan, children, plan['source_sha256'])
            self.assertEqual(result['status'], 'invalid')
            self.assertEqual(result['aggregate'], {})
            self.assertNotIn('matches', result)
        self.assertEqual(aggregate_children(plan, original, {})['aggregate'], {})

    def test_replay_pair_and_command_inconsistencies_fail_closed(self):
        plan, original = evidence_fixture()
        mutations = (
            lambda row: row.update(pair=99),
            lambda row: row.update(bot_seed=999),
            lambda row: row.update(outcome='loss'),
            lambda row: row.update(status='failure'),
            lambda row: row.update(white_argv=['wrong']),
            lambda row: row['replay'].update(warnings=[{'code': 'stderr_limit'}]),
        )
        for mutate in mutations:
            children = copy.deepcopy(original)
            mutate(children['batch-001']['document']['matches'][0])
            result = aggregate_children(plan, children, plan['source_sha256'])
            self.assertEqual(result['status'], 'invalid')
            self.assertEqual(result['aggregate'], {})

    def test_duplicate_color_and_malformed_match_cannot_complete_pair(self):
        plan, children = evidence_fixture()
        rows = children['batch-000']['document']['matches']
        rows[1] = copy.deepcopy(rows[0])
        self.assertEqual(aggregate_children(plan, children, plan['source_sha256'])['status'], 'invalid')
        rows[1] = None
        self.assertEqual(aggregate_children(plan, children, plan['source_sha256'])['status'], 'invalid')

    def test_child_command_preserves_exact_option_tokens_and_source_list(self):
        plan, _ = evidence_fixture()
        plan['candidate_args'] += ['--search-ms', '180', 'value with spaces']
        command = child_command(plan, plan['jobs'][0])
        forwarded = [value.split('=', 1)[1] for value in command if value.startswith('--candidate-arg=')]
        self.assertEqual(forwarded, plan['candidate_args'])
        sources = [command[index + 1] for index, value in enumerate(command) if value == '--source']
        self.assertEqual(sources, sorted(plan['source_sha256']))

    def test_plan_only_is_readonly_predeclaration_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'suite'
            arguments = ['--trusted-local', '--opponent', str(ROOT / 'v2_bot.py'),
                         '--workers', '4', '--plan-only', '--output-dir', str(output),
                         '--candidate-arg=--risk-policy', '--candidate-arg=robust']
            with patch('run_paired_suite.subprocess.Popen') as launch, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(arguments), 0)
                launch.assert_not_called()
            plan_file = output / 'plan.json'
            original = plan_file.read_bytes()
            plan = json.loads(original)
            self.assertEqual(plan_file.stat().st_mode & 0o222, 0)
            self.assertEqual(plan['planned_matches'], 200)
            self.assertEqual(plan['workers'], 4)
            self.assertEqual(len(plan['jobs']), 4)
            self.assertEqual(plan['candidate_args'], ['--risk-policy', 'robust'])
            self.assertEqual(set(V2_SNAPSHOT_FILES), {'bot.py', 'belief.py', 'search.py',
                             'referee.py', 'uniform_random.py', 'requirements.txt',
                             'SOURCES.md', 'provenance.json'})
            for name in V2_SNAPSHOT_FILES:
                snapshot = ROOT / 'opponents' / 'v2' / name
                self.assertEqual(plan['source_sha256'][str(snapshot)],
                                 hashlib.sha256(snapshot.read_bytes()).hexdigest())
            for job in plan['jobs']:
                argv = job['argv']
                forwarded = {argv[i + 1] for i, value in enumerate(argv) if value == '--source'}
                self.assertTrue({str(ROOT / 'opponents' / 'v2' / name)
                                 for name in V2_SNAPSHOT_FILES} <= forwarded)
            self.assertFalse(any(output.glob('batch-*.json')))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                main(arguments)
            self.assertEqual(failure.exception.code, 2)
            self.assertEqual(plan_file.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
