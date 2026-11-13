# Fog of War chess bot

**Experimental v3 candidate: not promoted.** It scored **97 wins, 1 draw and 102 losses** against frozen v2 in 200 games and failed the predeclared promotion criterion. The released bot remains v2 on `main`. This experimental revision retains the faster search, risk-policy experiments, isolated v2 opponent and reproducible evaluation tools; its `run.sh` runs the unpromoted candidate. See [v3 validation](outputs/validation-v3.md).

An observation-only Python engine for the [MIT 6.7980 challenge](https://www.mit.edu/~6.7980/fow/). Version 2 combines hidden-position hypotheses, inference from missing pawn destinations, tactical search and a deadline-aware fallback.

**Target: 2200–2500 MIT arena Elo. Current arena rating: unmeasured.** Local matches against starter opponents cannot certify that target. The [research note](analysis/research-strength.md) explains the rating pool and stronger-engine research. The original bot and its evidence remain available as `legacy_bot.py` and [v1 validation](outputs/validation.md).

**Released v2 evidence:** 118 wins / 2 losses in 120 held-out games, including **39/40 wins against the previous bot**. All **68 Python tests** and the independent source review passed. See [v2 validation](outputs/validation-v2.md) and the [tested submission ZIP](outputs/submission-v2-final.zip). These are local results, not an arena rating.

## Run and test

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest -v test_uniform_random test_bot test_belief test_search test_arena_eval
node --test package.test.mjs
source .venv/bin/activate
bash run.sh --seed 7980
```

The launcher implements the arena stdin/stdout protocol. It reads `fow`, then the color/time-control header, emits `ready`, processes every-ply observations, and answers clock prompts with `move <uci>`. This is a bot process, not an interactive chess UI. `--search-ms 180` is the default optional-work ceiling; the remaining clock can reduce it. Values from 0 through 2000 are accepted.

## Strategy

- The unmodified official tracker reconstructs exact legal root moves, castling rights and en passant from observations.
- Negative information matters: a hidden square directly ahead of a pawn implies a blocker, and a hidden capture diagonal cannot contain an enemy. Inferred occupancy does not establish piece identity.
- Up to 64 full-board hypotheses track every observed ply and known own move. Surviving transitions match the complete observation mask. If sampling loses support, bounded recovery reconciles the current view and retained enemy inventory. Recovered boards are explicitly approximate; they do not claim consistency with the entire history.
- Up to twelve root actions share a sample of four hypothetical worlds. Iterative depth-three tactical search, capture extensions, positional evaluation and adverse-tail scoring supplement the king-safety checks. Future branches use determinization and can suffer strategy fusion; this is not CFR or an equilibrium solver.
- Exact hypothesis paths retain FOW repetition counts. Recovery resets uncertain repetition history. Search applies the mover-loss rule before draw checks, including positions where every move is suicidal.
- Optional belief processing is deferred until the clock arrives. Inference, survival scoring and search share that turn budget; incomplete comparison batches are discarded. A legal fallback remains available, but no hard real-time guarantee or certainty of king safety is claimed.

No downloaded engine or trained weights are needed. The bot never receives a full referee board, opponent move list, network service, or privileged replay data during play.

## Evaluate and package

Use fresh output paths:

```sh
.venv/bin/python arena_eval.py --trusted-local --candidate bot.py \
  --opponent legacy_bot.py --opponent baseline.py --opponent uniform_random.py \
  --games-per-color 20 --seed 71980 --base-time 180 --increment 2 \
  --max-plies 1000 --output outputs/strength-new.json
.venv/bin/python package_submission.py --output outputs/submission-new.zip
```

Evaluation records paired seeds and swapped colors, source hashes, full replays, failures, per-color results and conservative pair-level uncertainty. Any relative Elo transform is descriptive of that opponent sample; it is never an absolute MIT rating. The referee runs trusted local processes without the arena sandbox.

The ZIP contains eight allowlisted files: `bot.py`, `belief.py`, `search.py`, `referee.py`, `uniform_random.py`, `run.sh`, `requirements.txt`, and `SOURCES.md`. Credentials, local environments and SoloCo state are excluded. The packager uses a 250,000-byte ceiling; historical v1 scopes keep their original limits.

## Arena validation

Local validation uses macOS ARM/Python 3.13.2 and the pinned MIT chess packages. The arena advertises Linux amd64/Python 3.14.8 with 4 GiB per bot. Actual sandbox execution and the requested rating require an authorized team submission and rated games. No submission or activation has been performed.

Use the official [Submit interface](https://www.mit.edu/~6.7980/fow/?tab=submit) with an instructor-provided team account. A rating claim must identify the date, team, active version and that version’s game record: team Elo carries across submitted versions. See [sources and AI attribution](SOURCES.md).
