#!/usr/bin/env python3
"""Read-only reproduction of the six public MIT replay analyses.

Run from the FFWB repository, or pass --referee-dir. Requires its pinned chess
packages. No bot execution or networking. --output creates a fresh JSON file.
"""
import argparse
import collections
import hashlib
import importlib
import json
from pathlib import Path
import statistics
import sys


def quantile(xs, pct):
    ordered = sorted(xs)
    index = (len(ordered) - 1) * pct
    low = int(index)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (index - low)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, nargs='?', default=Path(__file__).resolve().parent)
    parser.add_argument('--referee-dir', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.referee_dir.resolve()))
    import chess
    referee = importlib.import_module('referee')
    referee_file = Path(referee.__file__).resolve()
    assert referee_file == args.referee_dir.resolve() / 'referee.py'
    assert hashlib.sha256(referee_file.read_bytes()).hexdigest() == '2f82ac292bfad29bcb2d701b73c060d1ca8281378b4598f08a620fe5c97255be'
    directory = args.directory
    manifest = json.loads((directory / 'sources.json').read_text())
    selected = json.loads((directory / 'selected.json').read_text())
    leaderboard_raw = (directory / 'leaderboard.json').read_bytes()
    assert hashlib.sha256(leaderboard_raw).hexdigest() == manifest['leaderboard.json']['sha256']
    records = []
    for selection in selected:
        name = 'replay-' + selection['game']['id'] + '.json'
        raw = (directory / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == manifest[name]['sha256']
        replay = json.loads(raw)
        board = referee.FowBoard()
        repetitions = collections.Counter({referee.position_key(board): 1})
        frames = replay['frames']
        times, moves, declined, captures, castles = [[], []], [[], []], [[], []], [0, 0], []
        for i, frame in enumerate(frames):
            assert frame['ply'] == i
            if i:
                previous, side = frames[i - 1], board.turn
                index = 0 if side else 1
                move = chess.Move.from_uci(frame['move'])
                assert move in referee.fow_moves(board)
                elapsed = previous['clocks'][index] + replay['timeControl']['incrementSeconds'] - frame['clocks'][index]
                assert 0 <= elapsed <= previous['clocks'][index]
                assert previous['clocks'][1 - index] == frame['clocks'][1 - index]
                times[index].append(elapsed)
                moves[index].append(move.uci())
                capture_options = [m.uci() for m in referee.fow_moves(board) if board.is_capture(m)]
                if capture_options and not board.is_capture(move):
                    declined[index].append({'ply': i, 'move': move.uci(), 'available_captures': capture_options})
                captures[index] += int(board.is_capture(move))
                if board.is_castling(move):
                    castles.append({'ply': i, 'move': move.uci()})
                if i == len(frames) - 1:
                    final_before, final_move, final_previous, final_side = board.copy(), move, previous, side
                board.push(move)
                repetitions[referee.position_key(board)] += 1
            assert frame['board'] == ''.join(board.piece_at(s).symbol() if board.piece_at(s) else '-' for s in chess.SQUARES)
            assert frame['white'] == referee.observation(board, chess.WHITE)
            assert frame['black'] == referee.observation(board, chess.BLACK)
            result = referee.outcome(board, repetitions)
            if i < len(frames) - 1:
                assert result is None
        assert result == (replay['result'], replay['reason'])
        color_index = 0 if selection['focus_color'] == 'white' else 1
        elapsed = times[color_index]
        king = board.king(final_side)
        view = final_previous['white' if final_side else 'black']
        safe = []
        for alternative in referee.fow_moves(final_before):
            final_before.push(alternative)
            own_king = final_before.king(final_side)
            if own_king is not None and not final_before.is_attacked_by(not final_side, own_king):
                safe.append(alternative.uci())
            final_before.pop()
        records.append({
            'id': replay['id'], 'white': replay['white'], 'black': replay['black'],
            'focus': selection['focus_team'], 'color': selection['focus_color'],
            'versions': [replay['whiteVersion'], replay['blackVersion']],
            'result': replay['result'], 'reason': replay['reason'], 'rules': replay['rulesVersion'],
            'plies': len(frames) - 1, 'focus_moves': len(elapsed),
            'time_s': {'mean': statistics.mean(elapsed), 'median': statistics.median(elapsed),
                       'p95_linear': quantile(elapsed, .95), 'max': max(elapsed), 'total': sum(elapsed),
                       'over_180ms': sum(t > .18 for t in elapsed), 'ending_clock': frames[-1]['clocks'][color_index]},
            'focus_first_12_moves': moves[color_index][:12], 'focus_captures': captures[color_index],
            'noncapturing_moves_when_capture_available': len(declined[color_index]),
            'declines_examples': declined[color_index][:6], 'castles': castles,
            'terminal': {'losing_side': 'white' if final_side else 'black', 'move': final_move.uci(),
                         'is_capture': final_before.is_capture(final_move), 'king_after': chess.square_name(king),
                         'attackers': [{'square': chess.square_name(s), 'piece': board.piece_at(s).symbol(),
                                        'pre_move_observation': view[s]} for s in board.attackers(not final_side, king)],
                         'safe_alternatives': safe, 'board_before_fen': final_before.fen()}})
    existing = directory / 'analysis.json'
    if existing.exists():
        assert records == json.loads(existing.read_text()), 'Recorded research analysis differs'
    if args.output:
        with args.output.open('x') as stream:
            json.dump(records, stream, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps({'status': 'passed', 'games': len(records),
                      'moves': sum(r['plies'] for r in records),
                      'frames': sum(r['plies'] + 1 for r in records),
                      'focus_moves': sum(r['focus_moves'] for r in records),
                      'recorded_analysis_exact_match': existing.exists()}, sort_keys=True))


if __name__ == '__main__':
    main()
