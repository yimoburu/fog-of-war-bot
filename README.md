# Fog of War chess bot

An observation-only Python engine for the [MIT 6.7980 challenge](https://www.mit.edu/~6.7980/fow/).

**V5: 261 wins, 8 draws, 131 losses against released v2 in 400 fresh
games (66.25% score), with zero failures and every replay audited.** It passed
the preregistered comparison gate. Separate regression controls scored 59 wins
and one loss in 60 games. All 188 Python tests, two static package checks and
both complete games using the extracted submission launcher passed.

The candidate uses a 600 ms internal ceiling versus v2's 180 ms, under identical
180+2 game clocks. This is a shared-host local comparison. The multiplicity-adjusted
bounded-pair interval is 55.31%–77.19%, conditional on independent pair outcomes;
shared host load can violate that assumption. The result does not isolate
algorithmic improvement from extra computation.

**MIT arena Elo remains unmeasured.** The 2200–2500 target requires actual rated
arena games. No arena account, submission or activation has been used.

See the [V5 experiment and research rationale](analysis/optimization-v5.md),
[validation report](outputs/validation-v5.md),
[tested submission ZIP](outputs/submission-v5-final.zip),
[frozen plan](outputs/v5-cycle-20261113/wave-001/plan.json) and
[complete final suite](outputs/v5-cycle-20261113/wave-001/final-learned_600/suite.json).
Earlier failures remain preserved: [v3](outputs/validation-v3.md) scored 97W/1D/102L
against v2; [v4](outputs/validation-v4.md) failed its selected-position mechanism
gate. V5 uses a separate, prospectively frozen full-game experiment.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
source .venv/bin/activate
bash run.sh --seed 7980
```

The launcher speaks the arena stdin/stdout protocol: `fow`, color/time-control
header, `ready`, every-ply observations, and `move <uci>` after clock prompts.
Its tested defaults are:

```text
--risk-policy learned --search-ms 600 --particles 256 --worlds 6 --depth 4 --nodes 120000
```

The remaining game clock can reduce the optional-work budget. Additional CLI
arguments override defaults; altered settings require separate strength evaluation.
The raw `bot.py` entrypoint retains historical research defaults; `run.sh`
selects the validated configuration.

## Strategy

- The unchanged official tracker reconstructs exact root moves, castling rights
  and en passant from observations.
- Negative visibility identifies forced pawn blockers and impossible enemy
  placements. Occupancy evidence does not determine piece type.
- Up to 256 observation-consistent hypotheses support immediate-risk estimates;
  six sampled worlds support deeper tactical search. Recovery is explicitly
  approximate, and the bank is not a calibrated complete posterior.
- A compact bundled model supplies a soft immediate-loss signal from observation
  prefixes and known own captures. It was trained on earlier local V3 games;
  true boards supplied offline labels only. Runtime needs no NumPy.
- The utility penalizes estimated immediate king loss heavily relative to bounded
  continuation value. This is a heuristic preference, not a survival guarantee;
  model error, sampling and the prior can still lead to fatal choices. Search
  uses determinization and can suffer strategy fusion; it does not implement
  CFR or equilibrium play.
- Inference, risk scoring and search share one optional deadline. Incomplete
  comparison batches are discarded and a legal fallback remains available.

The bot receives only observations, its own moves and clock. No external engine,
network service or opponent replay truth is available during play. The
[training report](outputs/risk-model-training-v4.json) retains grouped splits,
calibration limitations and numerical warnings.

`bot_v5.py`, `belief_v5.py`, `threat_policy.py` and `fog_pressure.py` are additional
research prototypes. They are excluded from the selected launcher and submission
package; their full-game strength is unmeasured.

## Validate and package

```sh
.venv/bin/python -B -m unittest discover -v
node --test package.test.mjs
.venv/bin/python package_submission.py --output outputs/submission-new.zip
```

Use a fresh ZIP output path. The deterministic archive contains eleven
allowlisted files and stays within the 250,000-byte project payload limit.
Local environments, credentials and SoloCo state are excluded.

The V5 experiment driver freezes candidates and fresh paired seeds before play.
Its prospective guard binds the actual interpreter/dependency sources and
serializes experiment commands. Each final is 200 color-swapped pairs; at most
three attempts share a 5% error budget. At least 244 points out of 400 are needed
for its numerical gate. Failed attempts and historical reviews remain recorded.

After a passing final, separate 20-game controls against v1, the official baseline
and uniform random require 75%, 90% and 95% scores with zero failures. Those scores
are not pooled with v2 strength evidence. The package checker runs the actual
extracted launcher in both colors and audits its replays.

## Runtime and attribution

Local validation uses macOS arm64/Python 3.13.2 with chess 1.11.2 and
python-chess 1.999. The official arena documents Linux amd64/Python 3.14.8 with
4 GiB per bot. Actual arena compatibility and ratings remain unverified.

The [literature review](analysis/research-v4-literature.md) covers Obscuro,
Misty, belief recovery, information-set search and endgames. The
[arena study](analysis/research-v4-arena-evaluation.md) covers current official
rules and six reconstructed public elite replays. Sources, upstream hashes and
AI assistance are documented in [SOURCES.md](SOURCES.md).
