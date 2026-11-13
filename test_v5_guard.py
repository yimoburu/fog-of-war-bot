"""Prospective V5 environment and concurrency boundaries; no bot matches."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import v5_guard as guard


def immutable(path, document):
    raw = (json.dumps(document, sort_keys=True) + '\n').encode()
    path.write_bytes(raw)
    path.with_suffix(path.suffix + '.sha256').write_text(hashlib.sha256(raw).hexdigest() + '\n')


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.experiment = Path(self.folder.name)
        immutable(self.experiment / 'design.json', {
            'python_executable': sys.executable, 'driver_sha256': guard.digest(guard.DRIVER)})
        self.wave = self.experiment / 'wave-001'
        self.wave.mkdir()
        immutable(self.wave / 'plan.json', {'design_file': str(self.experiment / 'design.json')})
        self.result = self.wave / 'result.json'
        self.result.write_text('{"status":"final_ready","sentinel":"must remain unchanged"}\n')
        self.before = self.result.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(guard.main(['bind', '--experiment', str(self.experiment)]), 0)
        self.arguments = ['final', '--wave-plan', str(self.wave / 'plan.json'),
                          '--trusted-local', '--readiness-note', 'synthetic fixture only']

    def rejected_without_delegation(self):
        with patch.object(guard, 'delegate') as delegate, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(self.arguments), 2)
            delegate.assert_not_called()
        self.assertEqual(self.result.read_bytes(), self.before)
        self.assertFalse((self.wave / 'final-started.json').exists())

    def test_matching_identity_delegates_exact_tokens_while_lock_held(self):
        def check(arguments):
            self.assertEqual(arguments, self.arguments)
            with self.assertRaises(guard.GuardRejected):
                with guard.experiment_lock(self.experiment):
                    self.fail('Concurrent lock acquired')
            return 17
        with patch.object(guard, 'delegate', side_effect=check):
            self.assertEqual(guard.main(self.arguments), 17)
        self.assertEqual(self.result.read_bytes(), self.before)

    def test_alternate_interpreter_rejected_before_dependency_import_or_delegate(self):
        with patch.object(sys, 'executable', '/different/python'), patch.object(guard, 'environment_fingerprint') as fingerprint:
            self.rejected_without_delegation()
            fingerprint.assert_not_called()

    def test_dependency_source_mismatch_rejected_before_delegate(self):
        changed = copy.deepcopy(guard.environment_fingerprint())
        key = next(iter(changed['chess_python_source_sha256']))
        changed['chess_python_source_sha256'][key] = '0' * 64
        with patch.object(guard, 'environment_fingerprint', return_value=changed):
            self.rejected_without_delegation()

    def test_dependency_version_mismatch_rejected_before_delegate(self):
        changed = copy.deepcopy(guard.environment_fingerprint())
        changed['chess_distribution_version'] = 'different'
        with patch.object(guard, 'environment_fingerprint', return_value=changed):
            self.rejected_without_delegation()

    def test_concurrent_invocation_cannot_reach_driver_or_modify_result(self):
        with guard.experiment_lock(self.experiment):
            self.rejected_without_delegation()
        with patch.object(guard, 'delegate', return_value=0) as delegate:
            self.assertEqual(guard.main(self.arguments), 0)
            delegate.assert_called_once()

    def test_separate_process_is_rejected_while_experiment_lock_is_held(self):
        with guard.experiment_lock(self.experiment):
            child = subprocess.run([sys.executable, '-B', str(guard.SELF), *self.arguments],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 2)
        self.assertIn('Another guarded command owns this experiment', child.stderr)
        self.assertEqual(self.result.read_bytes(), self.before)
        self.assertFalse((self.wave / 'final-started.json').exists())

    def test_manifest_tampering_and_rebinding_are_rejected(self):
        path = self.experiment / guard.MANIFEST_NAME
        old = path.read_bytes()
        path.chmod(0o644)
        path.write_bytes(old + b' ')
        self.rejected_without_delegation()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(['bind', '--experiment', str(self.experiment)]), 2)


if __name__ == '__main__':
    unittest.main()
