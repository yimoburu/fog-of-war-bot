"""Synthetic verifier regressions. No bot/game/process is launched by these tests.

Prepared during the primary run; execute only after the parent permits host
tests again. Fork, process signaling and referee matches are mocked below.
"""
from contextlib import ExitStack
import json
import signal
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import referee
import verify_v5_package as verify


class PackageVerifierTests(unittest.TestCase):
    def make_archive(self, directory, *, modes=None, extra=None, oversized=False):
        args = ['--risk-policy', 'learned', '--search-ms', '600']
        contents = {name: name.encode() for name in verify.ALLOWLIST}
        contents['run.sh'] = verify.launcher(args)
        if oversized:
            contents['SOURCES.md'] = b'x' * verify.PAYLOAD_LIMIT
        if extra:
            contents.update(extra)
        target = directory / 'submission.zip'
        with zipfile.ZipFile(target, 'w') as archive:
            for name, data in contents.items():
                mode = (modes or {}).get(name, stat.S_IFREG | (0o755 if name == 'run.sh' else 0o644))
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, data)
        return target, {name: verify.digest(data) for name, data in contents.items()}, args

    def test_exact_archive_modes_and_argument_binding(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(verify, 'UPSTREAM', {}):
            target, hashes, args = self.make_archive(Path(temporary))
            contents, metadata = verify.inspect_archive(target, hashes, args)
            self.assertEqual(len(contents), 11)
            self.assertEqual(metadata['member_modes']['run.sh'], 0o755)
            with self.assertRaises(ValueError):
                verify.inspect_archive(target, hashes, ['--risk-policy', 'bank'])
            hashes['bot.py'] = '0' * 64
            with self.assertRaises(ValueError):
                verify.inspect_archive(target, hashes, args)

    def test_reject_odd_modes_symlink_extra_file_and_oversized_payload(self):
        cases = [dict(modes={'run.sh': stat.S_IFREG | 0o641}),
                 dict(modes={'run.sh': stat.S_IFREG | 0o4755}),
                 dict(modes={'bot.py': stat.S_IFREG | 0o755}),
                 dict(modes={'bot.py': stat.S_IFLNK | 0o644}),
                 dict(extra={'extra.py': b'pass'}), dict(oversized=True)]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary, patch.object(verify, 'UPSTREAM', {}):
                target, hashes, args = self.make_archive(Path(temporary), **case)
                with self.assertRaises(ValueError):
                    verify.inspect_archive(target, hashes, args)

    def test_failed_controls_reject_before_any_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'release-controls'
            directory.mkdir()
            target = directory / 'report.json'
            target.write_text(json.dumps({'status': 'controls_failed'}))
            with patch('v5_guard.load_bound_json', return_value={'design_file': str(root / 'design.json')}), \
                    patch('v5_guard.verify_manifest', return_value={}), \
                    patch('referee.run_match') as game:
                with self.assertRaises(ValueError):
                    verify.verify_evidence(root / 'wave-001' / 'plan.json', target)
                game.assert_not_called()

    def worker_mocks(self, stack):
        stack.enter_context(patch('arena_eval.source_hashes', return_value={}))
        stack.enter_context(patch('audit_replays.build_report', return_value={'ok': False}))
        stack.enter_context(patch('resource.getrusage', return_value=SimpleNamespace(
            ru_utime=0, ru_stime=0, ru_maxrss=0)))

    def test_worker_runtime_failure_retains_failed_output(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            directory = Path(temporary)
            self.worker_mocks(stack)
            game = stack.enter_context(patch('referee.run_match', side_effect=RuntimeError('synthetic match failure')))
            result = verify.game_worker(directory, directory, directory / 'legacy.py', 1, {})
            self.assertEqual(result, 1)
            game.assert_called_once()
            document = json.loads((directory / 'smoke-games.json').read_text())
            self.assertEqual(document['status'], 'aborted')
            self.assertIn('synthetic match failure', document['error'])
            self.assertTrue((directory / 'smoke-audit.json').is_file())

    def test_first_cancellation_during_close_does_not_interrupt_other_bot_cleanup(self):
        cancellation = {'signal': None, 'bots': [], 'errors': []}
        created = []

        class FakeBot:
            def __init__(self, *args, **kwargs):
                self.proc = SimpleNamespace(pid=1000 + len(created), returncode=None)
                self.proc.poll = lambda: self.proc.returncode
                self.closed = False
                created.append(self)

            def close(self):
                if cancellation['signal'] is None:
                    verify.cancel_registered_bots(cancellation, signal.SIGINT)
                self.closed = True
                self.proc.returncode = -signal.SIGTERM

        def match(*args, **kwargs):
            bots = [referee.Bot(), referee.Bot()]
            try:
                return {'result': '1/2-1/2', 'reason': 'Move limit reached'}
            finally:
                for bot in bots:
                    bot.close()

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            directory = Path(temporary)
            self.worker_mocks(stack)
            stack.enter_context(patch('referee.Bot', FakeBot))
            game = stack.enter_context(patch('referee.run_match', side_effect=match))
            kill = stack.enter_context(patch('os.killpg'))
            result = verify.game_worker(directory, directory, directory / 'legacy.py', 1, {}, cancellation)
            self.assertEqual(result, 1)
            self.assertEqual(len(created), 2)
            self.assertTrue(all(bot.closed for bot in created))
            self.assertEqual(kill.call_count, 2)
            game.assert_called_once()  # No second game after cancellation.
            self.assertEqual(json.loads((directory / 'smoke-games.json').read_text())['status'], 'aborted')
            self.assertIs(referee.Bot, FakeBot)

    def test_registered_constructor_failure_is_closed(self):
        created = []

        class FailingBot:
            def __init__(self):
                self.proc = SimpleNamespace(pid=1000)
                self.closed = False
                created.append(self)
                raise RuntimeError('synthetic partial constructor')

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            directory = Path(temporary)
            self.worker_mocks(stack)
            stack.enter_context(patch('referee.Bot', FailingBot))
            stack.enter_context(patch('referee.run_match', side_effect=lambda *a, **k: referee.Bot()))
            result = verify.game_worker(directory, directory, directory / 'legacy.py', 1, {})
            self.assertEqual(result, 1)
            self.assertEqual(len(created), 1)
            self.assertTrue(created[0].closed)

    def test_child_setup_error_is_saved_without_real_fork(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            directory = Path(temporary)
            stack.enter_context(patch('os.fork', return_value=0))
            stack.enter_context(patch('os.setsid'))
            stack.enter_context(patch('os.chdir'))
            stack.enter_context(patch.dict('os.environ'))
            stack.enter_context(patch('signal.pthread_sigmask', return_value=set()))
            stack.enter_context(patch('signal.signal'))
            stack.enter_context(patch('os._exit', side_effect=SystemExit))
            stack.enter_context(patch.object(verify, 'game_worker', side_effect=RuntimeError('synthetic worker setup')))
            with self.assertRaises(SystemExit):
                verify.isolated_worker(directory, directory, directory / 'legacy.py', 1, {}, directory)
            failure = json.loads((directory / 'worker-error.json').read_text())
            self.assertEqual(failure['status'], 'failed')
            self.assertIn('synthetic worker setup', failure['error'])


if __name__ == '__main__':
    unittest.main()
