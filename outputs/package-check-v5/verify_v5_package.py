#!/usr/bin/env python3
"""Verify a selected eleven-file ZIP only after passing V5 final and controls.

Preparation does not launch games. An explicit --trusted-local invocation,
passing bound evidence, and a fresh output directory are required. The actual
extracted run.sh plays exactly two serial games against the registered legacy
control, one per color. Extraction, source snapshot, raw replays and failures
are retained. No upload, launcher edit, package rewrite or strength inference.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import shlex
import signal
import stat
import sys
import tempfile
import traceback
import zipfile

import v5_guard as guard

ROOT = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()
ALLOWLIST = ('bot.py', 'belief.py', 'search.py', 'fast_observation.py',
             'risk_model.py', 'risk_model.json', 'referee.py', 'uniform_random.py',
             'run.sh', 'requirements.txt', 'SOURCES.md')
UPSTREAM = {
    'referee.py': '2f82ac292bfad29bcb2d701b73c060d1ca8281378b4598f08a620fe5c97255be',
    'uniform_random.py': 'a9d2d69c1893652dae938c596be59fc16fdf1edba3ccf491d9cf2c92d75b514a',
    'requirements.txt': '391e529739e99dc63461425af4ad7afb10d89aa275922928ceb8e2f5dffa7149',
}
PAYLOAD_LIMIT = 250000


class PackageInterrupted(Exception):
    """Normal Exception so official Bot construction also performs cleanup."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, limit=PAYLOAD_LIMIT):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Expected bounded regular file: ' + str(path))
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Oversized file: ' + str(path))
    return data


def launcher(args):
    """Exact audited launcher shape: selected arguments precede seed forwarding."""
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise ValueError('Selected arguments must be string tokens')
    command = 'exec python3 -u bot.py'
    if args:
        command += ' ' + shlex.join(args)
    return ('#!/usr/bin/env bash\nset -euo pipefail\n'
            'cd -- "$(dirname -- "${BASH_SOURCE[0]}")"\n' + command + ' "$@"\n').encode()


def inspect_archive(path, expected_hashes, selected_args):
    """Read bounded members without extracting or executing archive content."""
    raw = read_regular(path, 2 * 1024 * 1024)
    contents, modes = {}, {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) != 11 or {item.filename for item in infos} != set(ALLOWLIST):
            raise ValueError('ZIP must contain the exact eleven unique allowlisted files')
        if sum(item.file_size for item in infos) > PAYLOAD_LIMIT:
            raise ValueError('ZIP declared payload exceeds 250000 bytes')
        for info in infos:
            mode = info.external_attr >> 16
            if (not stat.S_ISREG(mode) or info.is_dir() or info.flag_bits & 1
                    or info.file_size < 0 or info.file_size > PAYLOAD_LIMIT):
                raise ValueError('ZIP member is not a bounded, unencrypted regular file: ' + info.filename)
            expected_mode = 0o755 if info.filename == 'run.sh' else 0o644
            if stat.S_IMODE(mode) != expected_mode:
                raise ValueError('ZIP member mode differs from the deterministic package contract: ' + info.filename)
            with archive.open(info) as member:
                data = member.read(PAYLOAD_LIMIT + 1)
            if len(data) != info.file_size or len(data) > PAYLOAD_LIMIT:
                raise ValueError('ZIP member actual size differs: ' + info.filename)
            contents[info.filename] = data
            modes[info.filename] = stat.S_IMODE(mode)
    if sum(map(len, contents.values())) > PAYLOAD_LIMIT:
        raise ValueError('ZIP actual payload exceeds 250000 bytes')
    hashes = {name: digest(data) for name, data in contents.items()}
    if hashes != expected_hashes:
        raise ValueError('ZIP bytes differ from current selected runtime or packaging files')
    if any(hashes[name] != value for name, value in UPSTREAM.items()):
        raise ValueError('ZIP changed an official upstream runtime file')
    if contents['run.sh'] != launcher(selected_args) or not modes['run.sh'] & 0o111:
        raise ValueError('run.sh must be executable and embed the exact selected argument tokens')
    return contents, {'zip_sha256': digest(raw), 'payload_bytes': sum(map(len, contents.values())),
                      'payload_limit_bytes': PAYLOAD_LIMIT, 'member_sha256': hashes,
                      'member_modes': modes, 'selected_args': selected_args,
                      'allowlist': list(ALLOWLIST)}


def verify_evidence(wave_path, report_path):
    """Recheck final/controls from bound plans and raw replays; never play a game."""
    import arena_eval as arena
    import run_paired_suite as suite
    import v5_release_check as release

    wave = guard.load_bound_json(wave_path)
    experiment = Path(wave['design_file']).resolve().parent
    runtime = guard.verify_manifest(experiment)
    if report_path != experiment / 'release-controls' / 'report.json':
        raise ValueError('Use the canonical completed release-control report')
    report = json.loads(report_path.read_text())
    if report.get('status') != 'controls_passed':
        raise ValueError('Passing final and all three controls are required before package games')
    resolved_path = report_path.parent / 'resolved-plan.json'
    if (Path(report['resolved_plan']).resolve() != resolved_path
            or guard.digest(resolved_path) != report['resolved_plan_sha256']):
        raise ValueError('Control report/resolved-plan binding changed')
    resolved = guard.load_bound_json(resolved_path)
    fresh = release.prepare(wave_path, experiment / 'release-controls-plan.json', runtime)
    if ({key: value for key, value in fresh.items() if key != 'created_utc'} !=
            {key: value for key, value in resolved.items() if key != 'created_utc'}):
        raise ValueError('Control resolution differs from reverified selected final evidence')
    sources = {**resolved['source_sha256'], str(resolved_path): guard.digest(resolved_path)}
    if (report.get('source_sha256') != sources or report.get('candidate') != resolved['candidate_name']
            or report.get('candidate_args') != resolved['candidate_args']
            or report.get('environment') != resolved['environment']
            or set(report.get('controls', {})) != set(release.THRESHOLDS)):
        raise ValueError('Control report source/configuration identity differs')
    if arena.source_hashes([Path(name) for name in sources]) != sources:
        raise ValueError('A source bound to the passing controls changed')
    audit_summaries, evidence = {}, {str(report_path): guard.digest(report_path), **sources}
    for control in resolved['controls']:
        name = control['name']
        recorded = report['controls'][name]
        directory = report_path.parent / name
        result_path, plan_path = directory / 'suite.json', directory / 'plan.json'
        if (not recorded.get('passed') or not recorded.get('valid') or not recorded.get('audit_ok')
                or Path(recorded['suite_file']).resolve() != result_path
                or guard.digest(result_path) != recorded['suite_sha256']):
            raise ValueError('Control suite is not passing or changed: ' + name)
        plan, result = json.loads(plan_path.read_text()), json.loads(result_path.read_text())
        expected_jobs = suite.plan_batches(10, 5, control['seed_base'])
        if (result.get('status') != 'completed' or result.get('errors')
                or result.get('plan_sha256') != guard.digest(plan_path)
                or (directory / 'plan.sha256').read_text().strip() != guard.digest(plan_path)
                or plan.get('candidate') != resolved['candidate']
                or plan.get('candidate_args') != resolved['candidate_args']
                or plan.get('opponent') != control['opponent'] or plan.get('opponent_args') != []
                or plan.get('python_executable') != sys.executable
                or plan.get('pairs') != 10 or plan.get('workers') != 4
                or plan.get('base_time') != 180 or plan.get('increment') != 2 or plan.get('max_plies') != 1000
                or plan.get('source_sha256') != sources
                or [(job['id'], job['seed'], job['pairs']) for job in plan['jobs']] !=
                   [(job['id'], job['seed'], job['pairs']) for job in expected_jobs]):
            raise ValueError('Control executed configuration differs: ' + name)
        children, paths = {}, []
        for job in plan['jobs']:
            path = Path(job['output'])
            child = result['children'][job['id']]
            if path.resolve() != directory / (job['id'] + '.json') or guard.digest(path) != child['output_sha256']:
                raise ValueError('Control raw child evidence changed: ' + name)
            children[job['id']] = {'returncode': child['returncode'], 'document': json.loads(path.read_text())}
            paths.append(path)
            evidence[str(path)] = guard.digest(path)
        checked = suite.aggregate_children(plan, children, arena.source_hashes([Path(p) for p in sources]))
        audit = release.audit_complete(paths, 20)
        summary = checked.get('aggregate', {}).get(control['opponent'])
        verdict = release.control_verdict(summary, 0 if checked['status'] == 'completed' else 1,
                                          audit['ok'] and release.control_audit_agrees(
                                              audit, summary, control['opponent']), control['minimum_score'])
        if (not verdict['passed'] or checked.get('matches') != result.get('matches')
                or any(recorded.get(key) != value for key, value in verdict.items())):
            raise ValueError('Raw control replay/statistics do not reproduce a passing result: ' + name)
        audit_summaries[name] = {'summary': audit['summary'], 'verdict': verdict}
        saved_audit_path = directory / 'replay-audit.json'
        saved_audit = json.loads(saved_audit_path.read_text())
        if (Path(recorded['audit_file']).resolve() != saved_audit_path
                or not saved_audit.get('ok') or saved_audit.get('summary') != audit['summary']):
            raise ValueError('Saved control audit does not reproduce current raw evidence: ' + name)
        evidence.update({str(result_path): guard.digest(result_path), str(plan_path): guard.digest(plan_path),
                         str(saved_audit_path): guard.digest(saved_audit_path)})
    return wave, resolved, {'verified_at': utc_now(), 'controls': audit_summaries,
                            'evidence_sha256': evidence, 'environment': runtime['environment']}


def checked_runtime(wave, resolved):
    hashes = {name: digest(read_regular(ROOT / name)) for name in ALLOWLIST}
    for name in ALLOWLIST:
        if name in ('run.sh', 'SOURCES.md'):
            continue  # Packaging-only files are checked against current bytes.
        source = Path(resolved['candidate']) if name == 'bot.py' else ROOT / name
        if wave['source_sha256'].get(str(source.resolve())) != hashes[name]:
            raise ValueError('Runtime is not byte-exact selected wave source: ' + name)
    return hashes


def child_rss(usage):
    raw = usage.ru_maxrss
    if sys.platform == 'darwin':
        value, unit = int(raw), 'bytes'
    elif sys.platform.startswith('linux'):
        value, unit = int(raw * 1024), 'KiB'
    else:
        value, unit = None, 'OS-specific; not converted'
    return {'ru_maxrss_raw': raw, 'raw_unit': unit, 'maximum_child_rss_bytes': value,
            'child_user_cpu_seconds': usage.ru_utime, 'child_system_cpu_seconds': usage.ru_stime,
            'scope': 'OS RUSAGE_CHILDREN in a fresh fork worker that launches only two candidate-versus-legacy games. '
                     'Maximum reaped-child high-water RSS, not candidate-only, not concurrent summed memory, '
                     'not an arena container limit measurement; descendant accounting follows the host OS.'}


def cancel_registered_bots(cancellation, number):
    """Stop bot groups without raising asynchronously inside referee cleanup."""
    if cancellation['signal'] is not None:
        return
    cancellation['signal'] = number
    for bot in cancellation['bots']:
        proc = getattr(bot, 'proc', None)
        try:
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as error:
            cancellation['errors'].append(type(error).__name__ + ': ' + str(error))


def game_worker(output, extracted, legacy, seed, sources, cancellation=None):
    """Called only by the post-gate fork; no public ungated worker CLI exists."""
    import arena_eval as arena
    import audit_replays
    import referee

    cancellation = cancellation if cancellation is not None else {'signal': None, 'bots': [], 'errors': []}
    original_bot = referee.Bot

    class RegisteredBot(original_bot):
        def __init__(self, *args, **kwargs):
            self._verification_closed = False
            if cancellation['signal'] is not None:
                raise PackageInterrupted('Cancelled before bot construction')
            cancellation['bots'].append(self)
            super().__init__(*args, **kwargs)
            # A signal can arrive just before Popen assigns self.proc. A late
            # cancellation is checked immediately after bounded initialization.
            if cancellation['signal'] is not None:
                self.close()
                raise PackageInterrupted('Cancelled during bot construction')

        def close(self):
            if not self._verification_closed:
                super().close()
                self._verification_closed = True

    target = output / 'smoke-games.json'
    document = {'schema_version': 1, 'status': 'running', 'planned_matches': 2,
                'configuration': {'base_time': 180, 'increment': 2, 'max_plies': 1000},
                'source_sha256': sources, 'matches': [], 'source_unchanged': True,
                'absolute_arena_elo': None, 'scope': 'Packaged launcher compatibility only; two games are not strength evidence.'}
    arena.checkpoint(target, document)
    referee.Bot = RegisteredBot
    try:
        initial_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        if initial_usage.ru_utime or initial_usage.ru_stime or initial_usage.ru_maxrss:
            raise ValueError('RSS worker unexpectedly has prior child accounting')
        for index, color in enumerate(('white', 'black')):
            if cancellation['signal'] is not None:
                raise PackageInterrupted('Cancelled before next package game')
            if arena.source_hashes([Path(path) for path in sources]) != sources:
                raise ValueError('Bound package-verification input changed before game')
            ours = [str(extracted / 'run.sh'), '--seed', str(seed)]
            theirs = [sys.executable, '-u', str(legacy), '--seed', str(seed + 1)]
            white, black = (ours, theirs) if color == 'white' else (theirs, ours)
            row = {'id': str(index), 'pair': 0, 'color': color, 'opponent': str(legacy),
                   'bot_seed': seed, 'opponent_seed': seed + 1,
                   'white_argv': white, 'black_argv': black}
            replay = referee.run_match(white, black, base=180, increment=2, max_plies=1000,
                                       match_id='package-' + color)
            outcome, status = arena.classify(replay, color)
            row.update(replay=replay, outcome=outcome, status=status)
            document['matches'].append(row)
            document['source_unchanged'] = arena.source_hashes([Path(path) for path in sources]) == sources
            arena.checkpoint(target, document)
            if cancellation['signal'] is not None:
                raise PackageInterrupted('Package game cancelled')
            if not document['source_unchanged']:
                raise ValueError('Bound package-verification input changed during game')
        failed = any(row['status'] != 'completed' for row in document['matches'])
        document['status'] = 'completed_with_failures' if failed else 'completed'
    except BaseException as error:
        document.update(status='aborted', error=type(error).__name__ + ': ' + str(error),
                        traceback=traceback.format_exc())
    finally:
        # Signals never raise from the handler, including while close() waits.
        # Close every registered process even if construction/run_match failed.
        for bot in cancellation['bots']:
            if getattr(bot, 'proc', None) is not None:
                try:
                    bot.close()
                except Exception as error:
                    cancellation['errors'].append(type(error).__name__ + ': ' + str(error))
        referee.Bot = original_bot
    if cancellation['signal'] is not None or cancellation['errors']:
        document.update(status='aborted', cancellation_signal=cancellation['signal'],
                        cleanup_errors=cancellation['errors'])
    document['child_resources'] = child_rss(resource.getrusage(resource.RUSAGE_CHILDREN))
    arena.checkpoint(target, document)
    audit = audit_replays.build_report([target])
    arena.checkpoint(output / 'smoke-audit.json', audit)
    return 0 if document['status'] == 'completed' and audit['ok'] else 1


def isolated_worker(output, extracted, legacy, seed, sources, python_bin):
    """Forward cancellation once; repeated signals must not interrupt cleanup."""
    handled = {signal.SIGINT, signal.SIGTERM}
    original_handlers = {number: signal.getsignal(number) for number in handled}
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handled)
    child, cancelled = None, []
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        child = os.fork()
        if child == 0:
            code = 1
            try:
                os.setsid()  # Terminal Ctrl-C goes only to the parent verifier.
                cancellation = {'signal': None, 'bots': [], 'errors': []}

                def cancel_worker(number, frame):
                    cancel_registered_bots(cancellation, number)

                for number in handled:
                    signal.signal(number, cancel_worker)
                signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
                os.environ['PATH'] = str(python_bin) + os.pathsep + os.environ.get('PATH', '')
                os.environ.pop('PYTHONPATH', None)
                os.environ.pop('PYTHONHOME', None)
                os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
                os.chdir(extracted)
                code = game_worker(output, extracted, legacy, seed, sources, cancellation)
            except BaseException as error:
                failure = {'status': 'failed', 'error': type(error).__name__ + ': ' + str(error),
                           'traceback': traceback.format_exc(), 'finished_utc': utc_now()}
                try:
                    (output / 'worker-error.json').write_text(json.dumps(failure, indent=2) + '\n')
                except BaseException:
                    pass  # Parent still records a missing or failed worker result.
            finally:
                os._exit(code)

        def forward_once(number, frame):
            if not cancelled:
                cancelled.append(number)
                try:
                    os.kill(child, signal.SIGINT)
                except ProcessLookupError:
                    pass

        for number in handled:
            signal.signal(number, forward_once)
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        while True:
            try:
                _, status = os.waitpid(child, 0)
                return os.waitstatus_to_exitcode(status), cancelled
            except InterruptedError:
                continue
    except BaseException:
        if child is not None and child > 0:
            for number in handled:
                signal.signal(number, signal.SIG_IGN)
            try:
                os.kill(child, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(child, 0)
            except ChildProcessError:
                pass
        raise
    finally:
        if child != 0:
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
            for number, handler in original_handlers.items():
                signal.signal(number, handler)


def run(args):
    wave_path, report_path = args.wave_plan.resolve(), args.controls_report.resolve()
    wave = guard.load_bound_json(wave_path)
    experiment = Path(wave['design_file']).resolve().parent
    with guard.experiment_lock(experiment):
        wave, resolved, evidence = verify_evidence(wave_path, report_path)
        runtime_hashes = checked_runtime(wave, resolved)
        package_path = args.zip.absolute()
        contents, package = inspect_archive(package_path, runtime_hashes, resolved['candidate_args'])
        if not 0 <= args.seed < 2_147_483_646:
            raise ValueError('Seed must be in [0,2147483646)')
        if not hasattr(os, 'fork'):
            raise ValueError('A POSIX fork worker is required for scoped child RSS')
        python_bin = Path(sys.executable).absolute().parent
        if not (python_bin / 'python3').is_file() or (python_bin / 'python3').resolve() != Path(sys.executable).resolve():
            raise ValueError('The bound interpreter directory lacks matching python3 for run.sh')
        legacy = next(Path(item['opponent']) for item in resolved['controls'] if item['name'] == 'legacy_v1')
        output = args.output_dir.absolute()
        output.mkdir(parents=True, exist_ok=False)
        extracted = Path(tempfile.mkdtemp(prefix='extracted-', dir=output))
        for name, data in contents.items():
            path = extracted / name
            with path.open('xb') as stream:
                stream.write(data)
            path.chmod(0o755 if name == 'run.sh' else 0o644)
        verifier_bytes = read_regular(SELF, 1024 * 1024)
        (output / SELF.name).write_bytes(verifier_bytes)
        import arena_eval as arena
        sources = {**evidence['evidence_sha256'], str(SELF): digest(verifier_bytes),
                   str(package_path): package['zip_sha256'],
                   **{str(ROOT / name): value for name, value in runtime_hashes.items()},
                   **{str(extracted / name): value for name, value in package['member_sha256'].items()}}
        package.update(zip_path=str(package_path), extraction_directory=str(extracted),
                       verifier_sha256=digest(verifier_bytes), candidate_name=resolved['candidate_name'],
                       source_sha256=sources, evidence=evidence,
                       launcher_python3=str(python_bin / 'python3'), seed=args.seed)
        arena.checkpoint(output / 'package-check.json', package)
        report = {'status': 'running', 'started_utc': utc_now(), 'package': package,
                  'absolute_arena_elo': None,
                  'process_instrumentation': 'The worker subclasses official Bot construction/cleanup to register '
                                             'processes, check cancellation and avoid duplicate closes; protocol, timing, moves '
                                             'and rules remain delegated to the unchanged referee source.',
                  'limitations': ['Trusted local checks, not a hostile-package sandbox or MIT arena execution.',
                                  'Smoke wins/losses are compatibility observations, not strength estimates.',
                                  'Charged latency is clock-delta response time including local IPC; not CPU search time.',
                                  'RSS covers both bot roles in the fresh worker, not candidate-only or peak simultaneous memory.']}
        arena.checkpoint(output / 'verification.json', report)
        errors, exitcode, cancelled = [], None, []
        try:
            exitcode, cancelled = isolated_worker(output, extracted, legacy, args.seed, sources, python_bin)
        except BaseException as error:
            errors.append(type(error).__name__ + ': ' + str(error))
        result_path, audit_path = output / 'smoke-games.json', output / 'smoke-audit.json'
        def retained_json(path):
            try:
                raw = path.read_bytes()
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError('Expected an object')
                return value, digest(raw)
            except (OSError, ValueError, UnicodeError) as error:
                errors.append(str(path) + ': ' + str(error))
                return {}, None
        results, result_digest = retained_json(result_path)
        audit, audit_digest = retained_json(audit_path)
        matches = results.get('matches', [])
        if not isinstance(matches, list) or any(not isinstance(row, dict) for row in matches):
            errors.append('Malformed worker match list')
            matches = []
        audit_summary = audit.get('summary', {})
        if not isinstance(audit_summary, dict):
            errors.append('Malformed worker audit summary')
            audit_summary = {}
        try:
            unchanged = arena.source_hashes([Path(path) for path in sources]) == sources
        except OSError as error:
            unchanged = False
            errors.append('Post-run source verification: ' + str(error))
        passed = (exitcode == 0 and not cancelled and not errors and unchanged
                  and results.get('status') == 'completed' and len(matches) == 2
                  and audit.get('ok') and audit_summary.get('matches') == 2)
        report.update(status='passed' if passed else 'failed', finished_utc=utc_now(),
                      worker_exitcode=exitcode, cancelled_by_signals=cancelled, errors=errors,
                      source_unchanged=unchanged,
                      smoke_file=str(result_path), smoke_sha256=result_digest,
                      audit_file=str(audit_path), audit_sha256=audit_digest,
                      charged_latency=audit_summary.get('candidate_timing'),
                      child_resources=results.get('child_resources'),
                      outcomes=[{key: row.get(key) for key in ('color', 'status', 'outcome')} for row in matches])
        arena.checkpoint(output / 'verification.json', report)
        print('Package verification:', report['status'], output / 'verification.json', flush=True)
        return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--wave-plan', type=Path, required=True)
    parser.add_argument('--controls-report', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=1960031861)
    parser.add_argument('--trusted-local', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print('Package verification rejected: ' + str(error), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
