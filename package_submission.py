#!/usr/bin/env python3
"""Create an allowlisted deterministic local ZIP; never upload it."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parent
RUNTIME_FILES = ('bot.py', 'belief.py', 'search.py', 'referee.py', 'uniform_random.py', 'run.sh',
                 'requirements.txt', 'SOURCES.md')
UPSTREAM = {
    'referee.py': '2f82ac292bfad29bcb2d701b73c060d1ca8281378b4598f08a620fe5c97255be',
    'uniform_random.py': 'a9d2d69c1893652dae938c596be59fc16fdf1edba3ccf491d9cf2c92d75b514a',
    'requirements.txt': '391e529739e99dc63461425af4ad7afb10d89aa275922928ceb8e2f5dffa7149',
}
LIMIT = 250000


def read_regular(path):
    if not hasattr(os, 'O_NOFOLLOW'):
        raise RuntimeError('O_NOFOLLOW support required')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError('Expected bounded regular file: ' + path.name)
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError('Oversized input: ' + path.name)
    return data


def build(output):
    contents = {name: read_regular(ROOT / name) for name in RUNTIME_FILES}
    for name, digest in UPSTREAM.items():
        if hashlib.sha256(contents[name]).hexdigest() != digest:
            raise ValueError('Changed upstream source: ' + name)
    if sum(map(len, contents.values())) > LIMIT:
        raise ValueError('Archive input exceeds size limit')
    if output.suffix.lower() != '.zip':
        raise ValueError('Output must have .zip suffix')
    with output.open('xb') as stream:
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, data in contents.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                mode = 0o755 if name == 'run.sh' else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, data)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('submission.zip'))
    args = parser.parse_args()
    print(build(args.output), args.output)


if __name__ == '__main__':
    main()
