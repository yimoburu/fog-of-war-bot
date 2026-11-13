# Fog of War chess bot

A lightweight Python bot for the [MIT 6.7980 challenge](https://www.mit.edu/~6.7980/fow/). It uses the official observation-only move generator, remembers enemy sightings, and prioritizes king safety before material, development and information gathering.

Locally verified: 29 Python tests and 40 evaluation games with no protocol failures. Results were **13 wins / 1 draw / 6 losses** against the capture baseline and **20 wins** against uniform random. Both opponents were played 10 times per color. See [validation evidence](outputs/validation.md); this is a small local sample, not an arena ranking.

## Run and test

After cloning, create an isolated Python environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

```sh
.venv/bin/python -m unittest -v test_uniform_random test_bot
source .venv/bin/activate
bash run.sh --seed 7980
```

The launcher speaks the arena's stdin/stdout protocol; it is not an interactive chess UI. It reads `fow`, then a color/time-control header, emits `ready`, processes observations before every ply, and replies only to clock prompts with `move <uci>`.

## Evaluate and package

```sh
.venv/bin/python evaluate.py --trusted-local --games-per-color 10 --seed 7980 --base-time 180 --increment 2 --max-plies 1000 --output outputs/evaluation-new.json
.venv/bin/python package_submission.py --output outputs/submission-new.zip
```

Use fresh output filenames. Evaluation saves actual results, full replays, seeds, source hashes and separate normal-game/failure counts. The referee runs trusted local processes. The ZIP contains only the six allowlisted runtime/attribution files, with `run.sh` at its root. Use the generated `outputs/submission.zip` for eventual manual arena validation; nothing has been uploaded.

## Strategy and limits

The official tracker handles castling rights and en passant using every observation. All candidate moves are simulated before king-safety classification. Optional heuristic scoring considers captures, recapture risk, development, cover, uncertain hidden threats and information gain. Old sightings decay and never become certain pieces. This is a heuristic policy, not a trained model or CFR solver.

Hidden attackers can defeat apparently safe moves, and unseen blockers can make apparent attacks spurious. Scoring is bounded, with a clock-aware fallback, but has no hard real-time guarantee. Seeded choices can vary if host load triggers the deadline fallback.

Tests used macOS ARM and Python 3.13.2 with MIT's pinned chess packages. The advertised Linux/Python 3.14.8 arena and its resource isolation remain unverified. [Sources and AI attribution](SOURCES.md).
