#!/usr/bin/env python3
"""Fog of War chess rules and a local runner for your own bots.

This runner executes programs on your computer without a sandbox.
Use it only for code you trust. Setup and bot protocol instructions:
https://www.mit.edu/~6.7980/fow/starter/README.md
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import json
import os
from pathlib import Path
import queue
import re
import select
import shlex
import signal
import subprocess
import threading
import time
import uuid

import chess

MAX_LINE_BYTES = 4096
MAX_OUTPUT_BYTES = 1024 * 1024
INITIALIZATION_SECONDS = 15
MAX_PROTOCOL_EVENTS = 10000
MAX_PROTOCOL_BYTES = 768 * 1024


class ProtocolTimeline:
    """Bounded protocol-only timeline; timestamps never contain host wall time."""

    def __init__(self):
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.events = []
        self.bytes = 32
        self.ply = 0
        self.truncated = False
        self.closed = False

    def now_ms(self, when=None):
        return max(0, int(((time.monotonic() if when is None else when) - self.started) * 1000))

    def record(self, side, direction, text, when, ply=None):
        with self.lock:
            at = self.now_ms(when)
            if self.closed:
                return at
            event = {'atMs': at, 'ply': self.ply if ply is None else ply, 'side': 'white' if side else 'black',
                     'direction': direction, 'text': text[:256]}
            size = len(json.dumps(event).encode()) + 1
            self.truncated |= len(text) > 256
            if len(self.events) >= MAX_PROTOCOL_EVENTS or self.bytes + size > MAX_PROTOCOL_BYTES:
                self.truncated = True
            else:
                self.events.append((when, event))
                self.bytes += size
            return at

    def set_ply(self, ply):
        with self.lock:
            self.ply = ply

    def finish(self):
        with self.lock:
            self.closed = True
            return {'events': [event for _, event in sorted(self.events, key=lambda pair: pair[0])],
                    'truncated': self.truncated}


class FowBoard(chess.Board):
    """Standard chess movement with no check restrictions, including castling."""

    def parse_uci(self, uci):
        move = chess.Move.from_uci(uci)
        if move not in self.generate_pseudo_legal_moves():
            raise chess.IllegalMoveError(f'Illegal FOW move: {uci}')
        return move

    def generate_castling_moves(self, from_mask=chess.BB_ALL, to_mask=chess.BB_ALL):
        rank = 0 if self.turn == chess.WHITE else 7
        king = chess.square(4, rank)
        if not (from_mask & chess.BB_SQUARES[king]):
            return
        if self.piece_at(king) != chess.Piece(chess.KING, self.turn):
            return
        if self.promoted & chess.BB_SQUARES[king]:
            return
        rights = self.clean_castling_rights()
        for rook_file, target_file, empty_files in [(7, 6, (5, 6)), (0, 2, (1, 2, 3))]:
            rook = chess.square(rook_file, rank)
            target = chess.square(target_file, rank)
            if not (rights & chess.BB_SQUARES[rook] and to_mask & chess.BB_SQUARES[target]):
                continue
            if self.piece_at(rook) != chess.Piece(chess.ROOK, self.turn):
                continue
            if any(self.piece_at(chess.square(f, rank)) for f in empty_files):
                continue
            # Intentionally no attacks/check test on e/f/g or e/d/c.
            yield chess.Move(king, target)


def fow_moves(board: FowBoard):
    return list(board.generate_pseudo_legal_moves())


def observation(board: FowBoard, side: chess.Color) -> str:
    view_board = board.copy(stack=False)
    view_board.turn = side
    if side != board.turn:
        view_board.ep_square = None
    moves = fow_moves(view_board)
    visible = chess.SquareSet(view_board.occupied_co[side])
    for move in moves:
        visible.add(move.to_square)
        if view_board.is_en_passant(move):
            visible.add(move.to_square - 8 if side else move.to_square + 8)
    return ''.join((board.piece_at(sq).symbol() if board.piece_at(sq) else '-') if sq in visible else '?' for sq in range(64))


def position_key(board: FowBoard):
    # EP distinguishes repetition positions only when FOW permits a capture.
    # python-chess's default key asks for *orthodox legal* EP and is unsuitable.
    ep = board.ep_square if any(board.generate_pseudo_legal_ep()) else None
    return board.board_fen(), board.turn, int(board.clean_castling_rights()), ep


def outcome(board: FowBoard, repetitions: Counter):
    opponent_king = board.king(not board.turn)
    if opponent_king is None or board.is_attacked_by(board.turn, opponent_king):
        return ('1-0' if board.turn else '0-1', 'King capturable')
    if repetitions[position_key(board)] >= 3:
        return ('1/2-1/2', 'Threefold repetition')
    if board.halfmove_clock >= 100:
        return ('1/2-1/2', '50-move rule')
    if not fow_moves(board):
        return ('0-1' if board.turn else '1-0', 'No legal moves')
    return None


def make_frame(board, clocks, move=None):
    return {
        'ply': len(board.move_stack),
        'board': ''.join(board.piece_at(sq).symbol() if board.piece_at(sq) else '-' for sq in range(64)),
        'white': observation(board, chess.WHITE),
        'black': observation(board, chess.BLACK),
        'move': move,
        'clocks': [round(clocks[0], 6), round(clocks[1], 6)],
    }


class StderrCapture:
    """Keep a bounded stderr tail, count excess output, and drain without forfeiting."""
    LIMIT = 1024 * 1024
    LINE_LIMIT = 4096
    RETAIN = 16384

    def __init__(self):
        self.excerpt = bytearray()
        self.total = 0
        self.line_bytes = 0
        self.exceeded = False

    def feed(self, chunk):
        kept = chunk[:max(0, self.RETAIN - len(self.excerpt))]
        self.excerpt.extend(chunk[-self.RETAIN:])
        del self.excerpt[:-self.RETAIN]
        if not self.exceeded:
            self.total = min(self.LIMIT + 1, self.total + len(chunk))
            parts = chunk.split(b'\n')
            longest = self.line_bytes + len(parts[0]) + (len(parts) > 1)
            if len(parts) > 1:
                longest = max(longest, *(len(part) + 1 for part in parts[1:-1]), len(parts[-1]))
                self.line_bytes = len(parts[-1])
            else:
                self.line_bytes += len(chunk)
            self.exceeded = self.total > self.LIMIT or longest > self.LINE_LIMIT
        # Forward at most RETAIN bytes through the adapter's diagnostic pipe;
        # the separate student capture file receives the rolling tail.
        return kept


class BotFailure(Exception):
    def __init__(self, message, *, received=None):
        super().__init__(message)
        self.received = received
        self.protocol = None


def failure_code(message):
    # Only fixed, course-protocol categories are sent to students. Exception
    # text, process argv, runtime stderr and host paths remain staff diagnostics.
    for prefix, code in [
        ('Illegal move', 'illegal_move'), ('Malformed move', 'malformed_move'),
        ('Move sent before', 'early_move'), ('Bot startup timed out', 'initialization_timeout'),
        ('Model initialization timed out', 'initialization_timeout'),
        ('Time forfeit', 'time_limit'), ('Output queue limit', 'output_limit'),
        ('Output limit', 'output_limit'), ('Invalid output', 'invalid_output'),
        ('Bot did not read', 'input_not_read'), ('Bot input pipe', 'exited'),
        ('Bot exited', 'exited'),
    ]:
        if message.startswith(prefix):
            return code
    return 'protocol_failure'


def failure_report(error, side, board, clocks, bot=None, phase='initialization', timeline=None):
    protocol = error.protocol if isinstance(error, BotFailure) else None
    if protocol is None and bot is not None:
        protocol = bot.protocol_snapshot()
    protocol = protocol or {'transcript': [], 'truncated': False}
    return {'code': failure_code(str(error)), 'side': 'white' if side else 'black',
            'phase': phase, 'ply': len(board.move_stack),
            'clockSeconds': 0 if failure_code(str(error)) == 'time_limit' else max(0, clocks[0 if side else 1]),
            'observation': observation(board, side) if phase != 'initialization' else None,
            'received': getattr(error, 'received', None),
            'atMs': timeline.now_ms() if timeline else None, **protocol}


class SandboxStartupFailure(BotFailure):
    """The trusted adapter failed before the sandbox reported readiness."""


class SandboxControlFailure(BotFailure):
    """A trusted freezer failed; abort without assigning a student loss."""


def confirmed_clean_startup_exit(ready_path):
    """Read host-only evidence after the adapter exited; never inspect bot text."""
    import stat
    # Runtime evidence wins over a previously published clean-exit marker.
    # Any malformed/unreadable runtime marker also leaves ownership uncertain.
    try:
        ready_path.with_suffix('.runtime').lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    else:
        return False
    expected = b'runtime-confirmed-clean-startup-exit\n'
    try:
        descriptor = os.open(ready_path.with_suffix('.startup-exit'),
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as marker:
            if not stat.S_ISREG(os.fstat(marker.fileno()).st_mode):
                return False
            return marker.read(len(expected) + 1) == expected
    except OSError:
        return False


class Bot:
    def __init__(self, argv, side, base, increment, ready_file=None, startup_timeout=10,
                 initialization_timeout=INITIALIZATION_SECONDS, timeline=None, turn_control_factory=None):
        self.argv, self.side = argv, side
        self.control = None
        self.observations = []
        self.timeline = timeline or ProtocolTimeline()
        self.messages = queue.Queue(maxsize=256)
        self.failure = None
        self.failure_received = None
        self.stdout_expected = 'ready'
        self.stdout_lock = threading.Lock()
        self.stderr_capture = StderrCapture()
        self.stderr_excerpt = self.stderr_capture.excerpt
        self.readers = []
        self.protocol_trace = deque(maxlen=12)
        self.protocol_lock = threading.Lock()
        self.protocol_truncated = False
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        os.set_blocking(self.proc.stdin.fileno(), False)
        for stream, is_stdout in [(self.proc.stdout, True), (self.proc.stderr, False)]:
            reader = threading.Thread(target=self._read, args=(stream, is_stdout), daemon=True)
            reader.start()
            self.readers.append(reader)
        try:
            if ready_file is not None:
                deadline = time.monotonic() + startup_timeout
                ready_path = Path(ready_file)
                while not ready_path.is_file():
                    # Student stdout and trusted readiness travel on separate
                    # pipes. A recorded output failure can precede marker delivery;
                    # send() will turn it into a forfeit after readiness is known.
                    adapter_exit = self.proc.poll()
                    if adapter_exit is not None:
                        # The adapter writes its marker before exiting. Recheck
                        # after observing exit to avoid using the earlier snapshot.
                        if ready_path.is_file():
                            break
                        if adapter_exit == 0 and confirmed_clean_startup_exit(ready_path):
                            raise BotFailure('Bot exited before completing startup')
                        raise SandboxStartupFailure('Sandbox failed before startup readiness')
                    if time.monotonic() >= deadline:
                        if ready_path.is_file():
                            break
                        raise SandboxStartupFailure('Sandbox startup readiness timed out')
                    time.sleep(.01)
            if turn_control_factory is not None:
                try:
                    self.control = turn_control_factory(ready_file)
                except BotFailure:
                    raise
                except Exception as error:
                    raise SandboxControlFailure('Could not attach sandbox freezer') from error
            self.send('fow')
            self.send(f'{"w" if side else "b"} {base:g} {increment:g}')
            self.initialize(initialization_timeout)
            self._control("freeze")
        except Exception as error:
            self.close()
            # Construction can fail before this Bot enters run_match's list.
            # Preserve its drained stderr on the exception for staff diagnostics;
            # failure_report deliberately does not expose this field to students.
            error.private_diagnostics = self.stderr_excerpt.decode('utf-8', 'replace')
            if isinstance(error, BotFailure):
                if (not isinstance(error, (SandboxStartupFailure, SandboxControlFailure))
                        and failure_code(str(error)) == 'exited'):
                    error.args = ('Bot exited before completing startup',)
                error.protocol = self.protocol_snapshot()
            raise

    def trace(self, direction, text, when=None, *, ply=None):
        when = time.monotonic() if when is None else when
        at = self.timeline.record(self.side, direction, text, when, ply)
        with self.protocol_lock:
            self.protocol_truncated |= len(self.protocol_trace) == self.protocol_trace.maxlen or len(text) > 256
            self.protocol_trace.append({'direction': direction, 'text': text[:256], 'atMs': at})

    def protocol_snapshot(self):
        with self.protocol_lock:
            return {'transcript': [
                {'direction': line['direction'], 'text': line['text']}
                for line in self.protocol_trace
            ], 'truncated': self.protocol_truncated}

    def initialize(self, timeout):
        # No observations or clock prompts are sent until readiness. This is a
        # bounded, mandatory initialization phase, outside the game clock.
        deadline = time.monotonic() + timeout
        while True:
            if self.failure:
                raise BotFailure(self.failure, received=self.failure_received)
            budget = deadline - time.monotonic()
            try:
                arrived, line = self.messages.get(timeout=max(0, min(budget, .1)))
            except queue.Empty:
                if budget <= 0:
                    raise BotFailure('Bot startup timed out')
                continue
            if line is None:
                raise BotFailure('Bot exited before completing startup')
            if arrived > deadline:
                raise BotFailure('Bot startup timed out')
            if line.lower() == 'ready':
                return
            raise BotFailure('Invalid output: expected ready', received='[non-protocol output omitted]')

    def _read(self, stream, stdout):
        if not stdout:
            try:
                while chunk := stream.read(65536):
                    self.stderr_capture.feed(chunk)
            except (OSError, ValueError):
                pass  # Shutdown can close the pipe; stderr never decides a result.
            return
        total = 0
        pending = b''
        try:
            while True:
                chunk = stream.read(MAX_LINE_BYTES + 1)
                arrived = time.monotonic()
                if not chunk:
                    if pending:
                        self.failure_received = pending.decode('utf-8', errors='replace')[:256]
                        self.failure = 'Invalid output: protocol lines must end with a newline'
                    self.messages.put_nowait((arrived, None))
                    return
                total += len(chunk)
                pending += chunk
                responses = []
                # Validate the whole available chunk before releasing a response:
                # a valid move followed by debug text must not win a race with
                # the main thread accepting the move or completing the game.
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    self.trace('output', line.decode('utf-8', errors='replace').rstrip('\r'), arrived)
                    if len(line) + 1 > MAX_LINE_BYTES or total > MAX_OUTPUT_BYTES:
                        self.failure = 'Output limit exceeded'
                        return
                    text = line.decode('utf-8', errors='strict').removesuffix('\r')
                    normalized = text.lower()
                    with self.stdout_lock:
                        if normalized == 'ready' and self.stdout_expected == 'ready':
                            self.stdout_expected = None
                        elif re.fullmatch(r'move [a-h][1-8][a-h][1-8][qrbn]?', normalized):
                            if self.stdout_expected != 'move':
                                self.failure_received = text[:256]
                                self.failure = 'Move sent before clock prompt'
                                return
                            self.stdout_expected = None
                        else:
                            self.failure_received = text[:256]
                            self.failure = ('Malformed move response' if normalized.startswith('move ')
                                            else 'Invalid output: unexpected stdout; send diagnostics to stderr')
                            return
                    responses.append((arrived, text))
                if len(pending) >= MAX_LINE_BYTES or total > MAX_OUTPUT_BYTES:
                    self.trace('output', pending.decode('utf-8', errors='replace'), arrived)
                    self.failure = 'Output limit exceeded'
                    return
                for response in responses:
                    self.messages.put_nowait(response)
        except queue.Full:
            self.failure = 'Output queue limit exceeded'
        except (UnicodeError, OSError, ValueError):
            self.failure = 'Invalid output or closed pipe'

    def send(self, line, timeout=1.0, *, ply=None):
        data = (line + '\n').encode()
        sent = time.monotonic()
        deadline = sent + timeout
        while data:
            if self.failure:
                raise BotFailure(self.failure, received=self.failure_received)
            if self.proc.poll() is not None:
                raise BotFailure('Bot exited')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BotFailure('Bot did not read input')
            _, ready, _ = select.select([], [self.proc.stdin], [], remaining)
            if ready:
                try:
                    data = data[os.write(self.proc.stdin.fileno(), data):]
                except BlockingIOError:
                    continue
                except (BrokenPipeError, OSError):
                    raise BotFailure('Bot input pipe closed') from None
        self.trace('input', line, sent, ply=ply)

    def _control(self, operation):
        if self.control is not None:
            try:
                getattr(self.control, operation)()
            except BotFailure:
                raise
            except Exception as error:
                raise SandboxControlFailure('Sandbox turn isolation failed') from error

    def observe(self, value, ply):
        if self.failure:
            raise BotFailure(self.failure, received=self.failure_received)
        if self.control is None:
            self.send(value, ply=ply)
        else:
            # At most two observations between turns; never fill a frozen stdin pipe.
            # Preserve the originating position even when delivery is delayed.
            self.observations.append((value, ply))
            if len(self.observations) > 2:
                raise SandboxControlFailure('Unexpected pending observation count')

    def move(self, remaining):
        self._control('resume')
        try:
            return self._move(remaining)
        finally:
            # Confirm all descendants are frozen before another side can run.
            self._control('freeze')
            if self.failure:
                raise BotFailure(self.failure, received=self.failure_received)

    def _move(self, remaining):
        if remaining <= 0:
            raise BotFailure('Time forfeit')
        started = time.monotonic()
        deadline = started + remaining
        buffered = bool(self.observations)
        for value, ply in self.observations:
            self.send(value, timeout=min(max(0, deadline-time.monotonic()), 1.0), ply=ply)
        self.observations.clear()
        prompted = time.monotonic() if buffered else started
        with self.stdout_lock:
            self.stdout_expected = 'move'
        self.send(f'{remaining:.9f}', timeout=min(remaining, 1.0))
        while True:
            if self.failure:
                raise BotFailure(self.failure, received=self.failure_received)
            try:
                # A response may already have arrived on time while the referee
                # was descheduled. Inspect its reader timestamp before forfeiting.
                arrived, line = self.messages.get_nowait()
            except queue.Empty:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    raise BotFailure('Time forfeit')
                try:
                    arrived, line = self.messages.get(timeout=min(budget, .1))
                except queue.Empty:
                    continue
            if line is None:
                raise BotFailure('Bot exited')
            if arrived > deadline:
                raise BotFailure('Time forfeit')
            normalized = line.lower()
            if normalized.startswith('move '):
                if arrived < prompted:
                    raise BotFailure('Move sent before clock prompt', received=line[:256])
                if not re.fullmatch(r'move [a-h][1-8][a-h][1-8][qrbn]?', normalized):
                    raise BotFailure('Malformed move response', received=line[:256])
                return normalized[5:], arrived - started
            raise BotFailure('Invalid output: expected a move response', received='[non-protocol output omitted]')

    def close(self):
        if self.control is not None:
            try:
                self.control.resume()
            except Exception:
                pass  # The adapter also thaws before removal; cleanup must still run.
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS may report EPERM for an already-exited process group.
            # Reap before deciding; a live process still makes cleanup fail.
            if self.proc.poll() is None:
                raise
        try:
            # Permit trusted container adapters to remove their container.
            self.proc.wait(timeout=5.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=1)
        # Kill surviving grandchildren even when their direct parent exited.
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if self.proc.poll() is None:
                raise
        for reader in self.readers:
            reader.join(timeout=1)
        for stream in [self.proc.stdin, self.proc.stdout, self.proc.stderr]:
            stream.close()


def run_match(white_argv, black_argv, base=180, increment=2, match_id=None,
              white_name='White', black_name='Black', max_plies=10000,
              ready_files=None, startup_timeout=10, initialization_timeout=INITIALIZATION_SECONDS,
              turn_control_factory=None):
    if base <= 0 or increment < 0 or not all(map(lambda x: x < float('inf'), (base, increment))):
        raise ValueError('Time control must be finite, with positive base and nonnegative increment')
    board = FowBoard()
    clocks = [float(base), float(base)]
    replay = {'id': match_id or str(uuid.uuid4()), 'white': white_name, 'black': black_name,
              'result': '*', 'reason': 'In progress',
              'timeControl': {'baseSeconds': base, 'incrementSeconds': increment},
              'frames': [make_frame(board, clocks)]}
    bots = []
    timeline = ProtocolTimeline()
    repetitions = Counter({position_key(board): 1})
    try:
        for side, argv in [(chess.WHITE, white_argv), (chess.BLACK, black_argv)]:
            try:
                ready = ready_files[0 if side else 1] if ready_files else None
                bots.append(Bot(argv, side, base, increment, ready, startup_timeout,
                                initialization_timeout, timeline, turn_control_factory))
            except (OSError, BotFailure) as error:
                if getattr(error, 'private_diagnostics', ''):
                    replay.setdefault('privateDiagnostics', {})['white' if side else 'black'] = error.private_diagnostics
                replay.update(result='0-1' if side else '1-0', reason=f'Bot startup failed: {error}')
                if isinstance(error, SandboxControlFailure) or (ready_files and isinstance(error, (OSError, SandboxStartupFailure))):
                    replay.update(result='*', reason=f'Infrastructure startup failure: {error}')
                else:
                    replay['failedSide'] = 'white' if side else 'black'
                if 'failedSide' in replay:
                    replay['studentReports'] = [failure_report(error, side, board, clocks, timeline=timeline)]
                return replay
        while True:
            # Check both output readers before accepting a terminal result or
            # scheduling another turn, including extra text after a valid move.
            for index, bot in enumerate(bots):
                if bot.failure:
                    error = BotFailure(bot.failure, received=bot.failure_received)
                    replay.update(result='0-1' if index == 0 else '1-0', reason=str(error),
                                  failedSide='white' if index == 0 else 'black')
                    replay['studentReports'] = [failure_report(error, index == 0, board, clocks,
                                                               bot, 'move', timeline)]
                    return replay
            terminal = outcome(board, repetitions)
            if terminal:
                replay.update(zip(('result', 'reason'), terminal))
                return replay
            if len(board.move_stack) >= max_plies:
                replay.update(result='1/2-1/2', reason='Move limit reached')
                return replay
            for index, side in enumerate([chess.WHITE, chess.BLACK]):
                try:
                    bots[index].observe(observation(board, side), len(board.move_stack))
                except SandboxControlFailure:
                    replay.update(result='*', reason='Infrastructure turn isolation failure')
                    return replay
                except BotFailure as error:
                    replay.update(result='0-1' if side else '1-0', reason=str(error))
                    replay['failedSide'] = 'white' if side else 'black'
                    replay['studentReports'] = [failure_report(error, side, board, clocks, bots[index], 'observation', timeline)]
                    return replay
            index = 0 if board.turn else 1
            try:
                uci, elapsed = bots[index].move(clocks[index])
                clocks[index] -= elapsed
                if clocks[index] < 0:
                    raise BotFailure('Time forfeit')
                move = chess.Move.from_uci(uci)
                if move not in fow_moves(board):
                    raise BotFailure(f'Illegal move: {uci}', received='move '+uci)
                board.push(move)
                clocks[index] += increment
                repetitions[position_key(board)] += 1
                replay['frames'].append(make_frame(board, clocks, uci))
                timeline.set_ply(len(board.move_stack))
            except SandboxControlFailure:
                replay.update(result='*', reason='Infrastructure turn isolation failure')
                return replay
            except BotFailure as error:
                replay.update(result='0-1' if index == 0 else '1-0', reason=str(error))
                replay['failedSide'] = 'white' if index == 0 else 'black'
                replay['studentReports'] = [failure_report(error, index == 0, board, clocks, bots[index], 'move', timeline)]
                return replay
    finally:
        for bot in bots:
            bot.close()
        # close() drains and joins the stdout reader. Do not publish a normal
        # result if trailing invalid output was still in flight at the last move.
        if replay['result'] != '*' and 'failedSide' not in replay:
            for index, bot in enumerate(bots):
                if bot.failure:
                    error = BotFailure(bot.failure, received=bot.failure_received)
                    replay.update(result='0-1' if index == 0 else '1-0', reason=str(error),
                                  failedSide='white' if index == 0 else 'black')
                    replay['studentReports'] = [failure_report(error, index == 0, board, clocks,
                                                               bot, 'move', timeline)]
                    break
        replay['protocol'] = timeline.finish()
        replay['warnings'] = [{'side': 'white' if index == 0 else 'black', 'code': 'stderr_limit'}
                              for index, bot in enumerate(bots) if bot.stderr_capture.exceeded]
        replay.setdefault('privateDiagnostics', {}).update({
            'white' if index == 0 else 'black': bot.stderr_excerpt.decode('utf-8', 'replace')
            for index, bot in enumerate(bots) if bot.stderr_excerpt
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--white', required=True, help='Trusted argv command, parsed without a shell')
    parser.add_argument('--black', required=True, help='Trusted argv command, parsed without a shell')
    parser.add_argument('--trusted-local', action='store_true', required=True,
                        help='Acknowledge these local executables are trusted; this is not a sandbox')
    parser.add_argument('--base-time', type=float, default=180)
    parser.add_argument('--increment', type=float, default=2)
    parser.add_argument('--id')
    parser.add_argument('--white-name', default='White')
    parser.add_argument('--black-name', default='Black')
    parser.add_argument('--max-plies', type=int, default=10000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    replay = run_match(shlex.split(args.white), shlex.split(args.black), args.base_time,
                       args.increment, args.id, args.white_name, args.black_name, args.max_plies)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + '.tmp')
    temp.write_text(json.dumps(replay, indent=2) + '\n')
    temp.replace(args.output)
    print(f'{replay["result"]}: {replay["reason"]}; {len(replay["frames"])-1} plies; {args.output}')


if __name__ == '__main__':
    main()
