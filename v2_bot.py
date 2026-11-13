#!/usr/bin/env python3
"""Run the frozen v2 opponent with isolated snapshot-local dependencies."""
import os
from pathlib import Path
import sys

# A fresh isolated interpreter ignores ambient PYTHONPATH/cwd imports and drops
# any modules already loaded by this launcher. Its only application path is v2.
BOOTSTRAP = r'''
import hashlib
import importlib
import json
from pathlib import Path
import runpy
import sys

snapshot = Path(sys.argv.pop(1)).resolve()
manifest = json.loads((snapshot / 'provenance.json').read_text())
if manifest['source_commit'] != '65fde033a2e65394a676e44969a992832ee17470':
    raise RuntimeError('Unexpected v2 snapshot commit')
expected = {'bot.py', 'belief.py', 'search.py', 'referee.py',
            'uniform_random.py', 'requirements.txt', 'SOURCES.md'}
if set(manifest['files']) != expected:
    raise RuntimeError('Unexpected v2 snapshot file manifest')
for name, digest in manifest['files'].items():
    if hashlib.sha256((snapshot / name).read_bytes()).hexdigest() != digest:
        raise RuntimeError('Changed v2 snapshot file: ' + name)
sys.path.insert(0, str(snapshot))
for name in ('belief', 'search', 'referee', 'uniform_random'):
    module = importlib.import_module(name)
    if Path(module.__file__).resolve() != snapshot / (name + '.py'):
        raise RuntimeError('Dependency escaped v2 snapshot: ' + name)
sys.argv[0] = str(snapshot / 'bot.py')
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def snapshot_command(arguments):
    snapshot = Path(__file__).resolve().parent / 'opponents' / 'v2'
    return [sys.executable, '-I', '-B', '-u', '-c', BOOTSTRAP,
            str(snapshot), *arguments]


def main():
    os.execv(sys.executable, snapshot_command(sys.argv[1:]))


if __name__ == '__main__':
    main()
