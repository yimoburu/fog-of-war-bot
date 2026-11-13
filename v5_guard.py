#!/usr/bin/env python3
"""Prospective environment/serialization guard for the unchanged V5 driver.

Bind once with the recorded interpreter:
  .venv/bin/python -B v5_guard.py bind --experiment outputs/v5-cycle-20261113
Then replace v5_experiments.py with v5_guard.py for register/run/final commands.
The lock remains held throughout delegation. No experiment or attempt is reset.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()
DRIVER = ROOT / 'v5_experiments.py'
MANIFEST_NAME = 'guard-manifest.json'


class GuardRejected(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_bound_json(path):
    path = Path(path)
    if digest(path) != path.with_suffix(path.suffix + '.sha256').read_text().strip():
        raise GuardRejected('Immutable evidence checksum changed: ' + str(path))
    return json.loads(path.read_text())


@contextmanager
def experiment_lock(experiment):
    """A separate open file description must not enter while another owns it."""
    path = Path(experiment) / '.v5-guard.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise GuardRejected('Another guarded command owns this experiment; no action taken') from error
        yield
    finally:
        # Never unlink the lock file: replacing its inode would split the lock.
        os.close(descriptor)


def verify_design(experiment):
    design_path = Path(experiment) / 'design.json'
    design = load_bound_json(design_path)
    if sys.executable != design.get('python_executable'):
        raise GuardRejected('Interpreter differs from the frozen design: expected ' + str(design.get('python_executable')))
    if digest(DRIVER) != design.get('driver_sha256'):
        raise GuardRejected('Frozen V5 driver source changed')
    return design


def environment_fingerprint():
    """Bind the actual interpreter and imported chess sources, not version alone."""
    import chess
    source = Path(chess.__file__).resolve()
    package = source.parent
    sources = sorted(package.rglob('*.py'))
    if source not in sources or not sources:
        raise GuardRejected('Cannot identify actual chess package sources')
    distribution = importlib.metadata.distribution('chess')
    expected = Path(distribution.locate_file('chess/__init__.py')).resolve()
    if source != expected:
        raise GuardRejected('Imported chess does not belong to the installed chess distribution')
    return {
        'python_executable': sys.executable,
        'python_executable_resolved': str(Path(sys.executable).resolve()),
        'python_executable_sha256': digest(sys.executable),
        'python_version': sys.version,
        'python_implementation': platform.python_implementation(),
        'python_cache_tag': sys.implementation.cache_tag,
        'platform': platform.platform(),
        'machine': platform.machine(),
        'chess_import_path': str(source),
        'chess_module_version': chess.__version__,
        'chess_distribution_version': distribution.version,
        'python_chess_distribution_version': importlib.metadata.version('python-chess'),
        'chess_python_source_sha256': {str(path): digest(path) for path in sources},
    }


def bind(experiment):
    verify_design(experiment)
    target = experiment / MANIFEST_NAME
    checksum = target.with_suffix(target.suffix + '.sha256')
    if target.exists() or checksum.exists():
        raise GuardRejected('Guard manifest already exists; never replace its identity')
    document = {
        'schema_version': 1,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'experiment': str(experiment),
        'design_file': str(experiment / 'design.json'),
        'design_sha256': digest(experiment / 'design.json'),
        'driver_file': str(DRIVER), 'driver_sha256': digest(DRIVER),
        'guard_file': str(SELF), 'guard_sha256': digest(SELF),
        'environment': environment_fingerprint(),
        'scope': 'Prospective guarded register/run/final commands; prior direct execution was not guarded.',
        'lock': 'Nonblocking flock per experiment, held for the entire delegated command.',
        'attempt_policy': 'Unchanged driver retains all existing attempts, final-start markers, seeds and alpha allocation.',
        'limitations': 'Trusted local integrity checks; no hostile runtime attestation, arena isolation or retroactive enforcement.',
    }
    data = (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    with target.open('xb') as stream:
        stream.write(data)
    target.chmod(0o444)
    with checksum.open('x') as stream:
        stream.write(hashlib.sha256(data).hexdigest() + '\n')
    checksum.chmod(0o444)
    print('Bound prospective guard:', target, hashlib.sha256(data).hexdigest(), flush=True)
    return 0


def verify_manifest(experiment):
    verify_design(experiment)  # Reject alternate interpreters before importing chess.
    manifest = load_bound_json(experiment / MANIFEST_NAME)
    expected = {'experiment': str(experiment), 'design_file': str(experiment / 'design.json'),
                'design_sha256': digest(experiment / 'design.json'),
                'driver_file': str(DRIVER), 'driver_sha256': digest(DRIVER),
                'guard_file': str(SELF), 'guard_sha256': digest(SELF)}
    if manifest.get('schema_version') != 1 or any(manifest.get(k) != v for k, v in expected.items()):
        raise GuardRejected('Guard, design or driver binding changed')
    if environment_fingerprint() != manifest.get('environment'):
        raise GuardRejected('Interpreter or chess dependency fingerprint differs from guard manifest')
    return manifest


def route(arguments):
    """Read only enough arguments to find the experiment; delegate exact tokens."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('bind', 'register', 'run', 'final'))
    parser.add_argument('--experiment', type=Path)
    parser.add_argument('--wave-plan', type=Path)
    parsed, _ = parser.parse_known_args(arguments)
    if parsed.command in ('bind', 'register'):
        if parsed.experiment is None:
            raise GuardRejected('This command requires --experiment')
        experiment = parsed.experiment.resolve()
    else:
        if parsed.wave_plan is None:
            raise GuardRejected('This command requires --wave-plan')
        path = parsed.wave_plan.resolve()
        plan = load_bound_json(path)
        experiment = Path(plan['design_file']).resolve().parent
        if path.parent.parent != experiment:
            raise GuardRejected('Wave plan is outside its bound experiment directory')
    if not experiment.is_dir():
        raise GuardRejected('Experiment directory does not exist')
    return parsed.command, experiment


def delegate(arguments):
    # Import only after environment validation and acquiring the experiment lock.
    specification = importlib.util.spec_from_file_location('_guarded_v5_driver', DRIVER)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.main(arguments)


def guarded(arguments):
    command, experiment = route(arguments)
    with experiment_lock(experiment):
        if command == 'bind':
            return bind(experiment)
        verify_manifest(experiment)
        return delegate(arguments)


def main(argv=None):
    try:
        return guarded(list(sys.argv[1:] if argv is None else argv))
    except (GuardRejected, OSError, KeyError, ValueError) as error:
        print('V5 guard rejected: ' + str(error), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
