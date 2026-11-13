"""Frozen opponent provenance, protocol compatibility and import isolation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import chess
from referee import FowBoard, fow_moves, observation

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / 'opponents' / 'v2'
MANIFEST_SHA256 = 'c816c4683a3fa776420b1c1c59dce8fe58af30846329b6c53a9629377707c833'


class SnapshotTests(unittest.TestCase):
    def test_snapshot_matches_pinned_commit_provenance(self):
        data = (SNAPSHOT / 'provenance.json').read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), MANIFEST_SHA256)
        manifest = json.loads(data)
        self.assertEqual(manifest['source_commit'], '65fde033a2e65394a676e44969a992832ee17470')
        self.assertEqual(set(manifest['files']), {'bot.py', 'belief.py', 'search.py',
                         'referee.py', 'uniform_random.py', 'requirements.txt', 'SOURCES.md'})
        for name, digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest(), digest, name)

    def test_launcher_protocol_ignores_poisoned_ambient_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            poisoned = Path(folder)
            for name in ('bot', 'belief', 'search', 'referee', 'uniform_random'):
                (poisoned / (name + '.py')).write_text("raise RuntimeError('AMBIENT IMPORT LEAK')\n")
            environment = dict(os.environ, PYTHONPATH=str(poisoned))
            for side in (chess.WHITE, chess.BLACK):
                truth = FowBoard()
                views = [observation(truth, side)]
                if not side:
                    truth.push_uci('e2e4')
                    views.append(observation(truth, side))
                transcript = 'fow\n' + ('w' if side else 'b') + ' 180 2\n'
                transcript += '\n'.join(views) + '\n180\n'
                run = subprocess.run([sys.executable, '-B', str(ROOT / 'v2_bot.py'),
                                      '--seed', '123', '--search-ms', '5'],
                                     input=transcript, text=True, capture_output=True,
                                     env=environment, cwd=poisoned, timeout=15)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stderr, '')
                lines = run.stdout.splitlines()
                self.assertEqual(lines[0], 'ready')
                self.assertEqual(len(lines), 2)
                self.assertTrue(lines[1].startswith('move '))
                self.assertIn(chess.Move.from_uci(lines[1][5:]), fow_moves(truth))

    def test_wrapper_reports_changed_snapshot_before_starting(self):
        # Exercise the launcher's actual isolated bootstrap against a copied
        # fixture, leaving the canonical frozen snapshot untouched.
        from v2_bot import snapshot_command
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder)
            for source in SNAPSHOT.iterdir():
                if source.is_file():
                    (copied / source.name).write_bytes(source.read_bytes())
            with (copied / 'search.py').open('a') as stream:
                stream.write('\n# accidental change\n')
            command = snapshot_command(['--seed', '123'])
            command[6] = str(copied)
            run = subprocess.run(command, input='fow\nw 180 2\n',
                                 text=True, capture_output=True, timeout=15)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(run.stdout, '')
            self.assertIn('Changed v2 snapshot file: search.py', run.stderr)


if __name__ == '__main__':
    unittest.main()
