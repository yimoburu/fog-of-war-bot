"""Release gate tests only; no control or other bot matches are executed."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import v5_release_check as release


class ReleaseTests(unittest.TestCase):
    def fixture(self):
        wave = {'candidates': [{'name': 'winner', 'args': ['--risk-policy', 'learned', '--search-ms', '600']}]}
        result = {'status': 'final_passed_release_pending', 'final_selection': 'winner',
                  'screens': {'winner': {'valid': True}},
                  'confirmations': {'winner': {'valid': True, 'qualification': {'passed': True}}},
                  'final': {'valid': True, 'audit_ok': True, 'statistical_gate': {'passed': True}}}
        return wave, result

    def test_not_passed_final_and_any_prior_failure_rejected(self):
        wave, good = self.fixture()
        for change in (lambda r: r.update(status='final_ready'),
                       lambda r: r['screens']['winner'].update(valid=False),
                       lambda r: r['confirmations']['winner']['qualification'].update(passed=False),
                       lambda r: r['final'].update(audit_ok=False),
                       lambda r: r['final']['statistical_gate'].update(passed=False)):
            result = copy.deepcopy(good)
            change(result)
            with self.assertRaises(ValueError):
                release.selected_candidate(wave, result)
        self.assertEqual(release.selected_candidate(wave, good), wave['candidates'][0])

    def test_control_thresholds_include_draw_points_and_fail_closed(self):
        summary = {'attempted': 20, 'failures': 0,
                   'raw': {'resolved': 20, 'win': 18, 'draw': 2, 'loss': 0, 'score': .95}}
        self.assertTrue(release.control_verdict(summary, 0, True, .95)['passed'])
        for code, audited in ((1, True), (0, False)):
            self.assertFalse(release.control_verdict(summary, code, audited, .75)['passed'])
        summary['failures'] = 1
        self.assertFalse(release.control_verdict(summary, 0, True, .75)['passed'])

    def test_control_score_must_match_independently_audited_outcomes(self):
        groups = [{'opponent': 'fixture', 'color': color, 'win': 9, 'draw': 1,
                   'loss': 0, 'unresolved': 0} for color in ('white', 'black')]
        audit = {'summary': {'by_opponent_color': groups}}
        summary = {'raw': {'win': 18, 'draw': 2, 'loss': 0, 'unresolved': 0}}
        self.assertTrue(release.control_audit_agrees(audit, summary, 'fixture'))
        summary['raw'].update(win=20, draw=0)
        self.assertFalse(release.control_audit_agrees(audit, summary, 'fixture'))
        self.assertFalse(release.control_audit_agrees(audit, summary, 'other-opponent'))

    def test_registered_controls_have_disjoint_namespaces_and_fixed_thresholds(self):
        root = release.ROOT / 'outputs/v5-cycle-20261113'
        plan = json.loads((root / 'release-controls-plan.json').read_text())
        design = json.loads((root / 'design.json').read_text())
        self.assertEqual(len(release.validate_controls(plan, design)), 3)
        bad = copy.deepcopy(plan)
        bad['controls'][1]['seed_base'] = bad['controls'][0]['seed_base']
        with self.assertRaises(ValueError):
            release.validate_controls(bad, design)
        bad = copy.deepcopy(plan)
        bad['controls'][0]['minimum_score'] = .5
        with self.assertRaises(ValueError):
            release.validate_controls(bad, design)
        bad = copy.deepcopy(plan)
        bad['controls'][0]['opponent'] = str(root / 'weaker.py')
        with self.assertRaises(ValueError):
            release.validate_controls(bad, design)

    def test_alternate_control_plan_cannot_authorize_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wave = root / 'wave-001' / 'plan.json'
            alternate = root / 'alternative-controls.json'
            alternate.write_text((release.ROOT / 'outputs/v5-cycle-20261113/release-controls-plan.json').read_text())
            with patch.object(release.guard, 'load_bound_json', return_value={'design_file': str(root / 'design.json')}):
                with self.assertRaisesRegex(ValueError, 'same experiment'):
                    release.prepare(wave, alternate, {})

    def test_raw_audit_requires_all_files_counts_and_actual_input_hashes(self):
        import audit_replays
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'batch.json'
            path.write_text('{}')
            good = {'ok': True, 'summary': {'inputs': 1, 'matches': 20,
                'verified_completed_trajectories': 20, 'recorded_failures': 0},
                'inputs': [{'input': str(path.resolve()), 'sha256': release.guard.digest(path)}]}
            with patch.object(audit_replays, 'build_report', return_value=good) as build:
                self.assertEqual(release.audit_complete([path], 20), good)
                with self.assertRaises(ValueError):
                    release.audit_complete([path, path.with_name('missing.json')], 20)
                self.assertEqual(build.call_count, 1)
                with self.assertRaises(ValueError):
                    release.audit_complete([path], 400)
                good['inputs'][0]['sha256'] = 'unrelated stale evidence'
                with self.assertRaises(ValueError):
                    release.audit_complete([path], 20)

    def test_unpassed_final_rejected_before_any_launcher_or_output(self):
        import run_paired_suite
        source = release.ROOT / 'outputs/v5-cycle-20261113/wave-001/plan.json'
        wave = json.loads(source.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / 'wave-001'
            directory.mkdir()
            plan = directory / 'plan.json'
            plan.write_text('{}')
            with patch.object(release.guard, 'load_bound_json', return_value={'design_file': str(root / 'design.json')}), \
                 patch.object(release.guard, 'verify_manifest', return_value={}), \
                 patch.object(release, 'prepare', side_effect=ValueError('Final is not passed')), \
                 patch.object(run_paired_suite, 'main') as launch, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(release.main(['--wave-plan', str(plan), '--trusted-local']), 2)
                launch.assert_not_called()
                self.assertFalse((root / 'release-controls').exists())

    def test_guarded_single_attempt_forwards_exact_args_and_keeps_controls_separate(self):
        import audit_replays
        import run_paired_suite
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root = Path(tmp)
            wave = root / 'wave-001'
            wave.mkdir()
            path = wave / 'plan.json'
            path.write_text('{}')
            controls = [{'name': name, 'minimum_score': minimum, 'seed_base': 1950000000 + i * 100000,
                         'opponent': '/fixture/' + name + '.py'} for i, (name, minimum) in enumerate(release.THRESHOLDS.items())]
            selected_args = ['--risk-policy', 'learned', '--search-ms', '600', '--nodes', '120000']
            resolved = {'source_sha256': {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()},
                        'controls': controls, 'candidate': '/fixture/bot.py', 'candidate_name': 'learned',
                        'candidate_args': selected_args, 'environment': {}, 'scope': 'separate controls'}
            calls = []
            def fake_suite(arguments):
                calls.append(arguments)
                with self.assertRaises(release.guard.GuardRejected):
                    with release.guard.experiment_lock(root):
                        self.fail('Guard lock not held throughout execution')
                out = Path(arguments[arguments.index('--output-dir') + 1]);out.mkdir()
                opponent = arguments[arguments.index('--opponent') + 1]
                self.assertEqual([a.split('=', 1)[1] for a in arguments if a.startswith('--candidate-arg=')], selected_args)
                self.assertEqual(arguments[arguments.index('--pairs') + 1], '10')
                self.assertEqual(arguments[arguments.index('--batch-pairs') + 1], '5')
                self.assertEqual(arguments[arguments.index('--workers') + 1], '4')
                children = [out / f'batch-{i:03}.json' for i in range(2)]
                for child in children:
                    child.write_text('{}')
                (out / 'plan.json').write_text(json.dumps({'jobs': [{'output': str(child)} for child in children]}))
                (out / 'suite.json').write_text(json.dumps({'status': 'completed', 'aggregate': {opponent: {
                    'attempted': 20, 'failures': 0, 'raw': {'resolved': 20, 'win': 20, 'draw': 0, 'loss': 0, 'score': 1.0}}}}))
                return 0
            def fake_audit(paths):
                opponent = '/fixture/' + paths[0].parent.name + '.py'
                groups = [{'opponent': opponent, 'color': color, 'win': 10, 'draw': 0,
                           'loss': 0, 'unresolved': 0} for color in ('white', 'black')]
                return {'ok': True, 'summary': {'inputs': len(paths), 'matches': 20,
                        'verified_completed_trajectories': 20, 'recorded_failures': 0,
                        'by_opponent_color': groups},
                        'inputs': [{'input': str(path.resolve()), 'sha256': release.guard.digest(path)} for path in paths]}
            with patch.object(release.guard, 'load_bound_json', return_value={'design_file': str(root / 'design.json')}), \
                 patch.object(release.guard, 'verify_manifest', return_value={}), \
                 patch.object(release, 'prepare', return_value=resolved), \
                 patch.object(run_paired_suite, 'main', side_effect=fake_suite), \
                 patch.object(audit_replays, 'build_report', side_effect=fake_audit):
                code = release.main(['--wave-plan', str(path), '--trusted-local'])
                self.assertEqual(code, 0, (root / 'release-controls/report.json').read_text())
                self.assertEqual(len(calls), 3)
                report = json.loads((root / 'release-controls/report.json').read_text())
                self.assertEqual(report['status'], 'controls_passed')
                self.assertEqual(set(report['controls']), set(release.THRESHOLDS))
                self.assertIsNone(report['absolute_arena_elo'])
                self.assertEqual(release.main(['--wave-plan', str(path), '--trusted-local']), 2)
                self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
