# V4 arena research and proposed evaluation

Checked November 13, 2026, beginning at 15:37 UTC (07:37 PST). This note records public read-only research, an offline audit of six completed arena games, and a proposed experiment. No bot was submitted, no account was accessed, and no new matches or paid compute were started. The bot's MIT arena rating remains unmeasured.

The clearest new finding is a large time-allocation gap: the selected strong teams averaged **3.30–6.78 seconds per move**, while V3's primary local sample averaged **0.167 seconds**. Our 180 ms optional-work ceiling is self-imposed, not an arena rule. More time is a promising experiment, but cannot by itself repair missing hypotheses, poor weighting or moves excluded from search. A concrete public game also demonstrates a bishop capture trap supported by a hidden queen.

## Current official contract

The [official guide](https://www.mit.edu/~6.7980/fow/assets/starter-guide-content-DTCMv2dG.js), [rules](https://www.mit.edu/~6.7980/fow/?tab=rules) and [starter ZIP](https://www.mit.edu/~6.7980/fow/starter/starter.zip) were refreshed. The downloaded `referee.py`, `uniform_random.py` and `requirements.txt` match our local files byte for byte. The starter ZIP remains SHA256 `b81f04dc5a84b72d20cb194bcaa694452842d54510827a359377abce98bacb4a`.

| Item | Current documented value and implication |
| --- | --- |
| Clock | 180 seconds plus 2 seconds after each move; the entire response must fit the remaining clock. |
| Initialization | Emit and flush `ready` within 15 seconds, outside the game clock. Sandbox setup separately has a 10-second limit. |
| Runtime | Linux amd64, CPython 3.14.8; `python-chess==1.999`, `chess==1.11.2`. Local macOS/Python 3.13 results do not verify this runtime. |
| CPU | Two physical cores/four hardware threads shared by the two bots. The opponent and its children/threads are paused during our turn; other arena matches use separate cores. No pondering during the opponent's turn. |
| Memory | 4 GiB per bot; 64 processes. RAM-backed writable workspace is 768 MiB and `/tmp` is 128 MiB, both counting toward memory usage. |
| Build and dependencies | Five-minute build; no build/play network. C++, Rust and CPU neural libraries are available; no GPU. Import/JIT work must respect initialization limits. |
| Packaging | Root `run.sh`, optional `build.sh`; 128 MiB compressed, 512 MiB expanded, at most 10,000 entries; no symlinks. |

Observations arrive before every ply, in fixed a1-to-h8 order for either color. Only the mover receives a numeric clock prompt. Processing queued observations after resuming therefore belongs inside the same complete-turn budget as inference and search. The process receives no final observation or king-capture prompt after termination.

There is no orthodox check restriction. A move exposing the mover's own king loses immediately; threatening the enemy king gives the opponent its next turn. Castling can cross attacked squares, but finishing with an attacked king still loses. Pawn forward blockers can remain hidden; en-passant visibility must use fog rules. Threefold repetition and 100 plies without a capture/pawn move draw automatically; no-move positions lose, insufficient material is not a draw, and 1,000 plies is a rated draw. These are consequential differences from generic chess engines. [Official guide](https://www.mit.edu/~6.7980/fow/assets/starter-guide-content-DTCMv2dG.js)

Validation plays the baseline in both colors. A normal loss passes; a timeout or confirmed OOM can produce a warning, while other protocol/initialization failures fail validation. Rated timeout/OOM results are losses. Passing upload validation therefore does not establish reliability or strength. [Official guide](https://www.mit.edu/~6.7980/fow/assets/starter-guide-content-DTCMv2dG.js)

## Live ladder and public replay access

The [leaderboard response](https://6s890.lids.mit.edu/api/leaderboard), retrieved at 15:37:16 UTC, reported live Elo with K=8, scheduling open and no configured competition-end timestamp:

| Team | Team Elo at retrieval | Active version | Active-version W/D/L |
| --- | ---: | ---: | ---: |
| Greek Gift | 2277.63 | 28 | 49 / 6 / 6 |
| Farsight | 2051.76 | 31 | 67 / 1 / 26 |
| Strongly Invisible | 2047.68 | 41 | 3312 / 333 / 1450 |
| Knight Vision | 2026.94 | 36 | 8083 / 311 / 3582 |

Ratings carry across versions; active-version records do not. Greek Gift had already changed from the earlier research note's version 27 to version 28. The fixed-1000 reference baseline is not a reliable bridge from a small local match to a 2200 rating. The target was near the top of this snapshot; 2500 exceeded its leader. These observations are dated, not promises about the future ladder. [Rating and record definitions](https://www.mit.edu/~6.7980/fow/api.html)

The [public API](https://www.mit.edu/~6.7980/fow/api.html) explicitly permits completed games, replays, diagnostics, standings and history without a token. It recommends at most one sequential request per second, cached completed data, and polling no faster than every 30 seconds. Its documented public endpoints do **not** provide submission source archives. No account access was needed for this research and no live game's hidden board was requested.

Selection was fixed before fetching the six replays: read the latest 20 completed games for each of the three highest-rated teams' exact active version, using `white=<name> vN`, `colors=any`, `sort=played`, `order=desc`. In that order, take the newest unused game for each color whose opponent's **current snapshot** rating is at least 1900. This is a small qualitative sample, not random sampling or a win-rate comparison. The opponent filter uses current ratings, not ratings at game time. The requests were sequential with at least 1.1 seconds between starts; downloaded completed data were reused.

Every one of the 692 moves, all 698 frames, both observations, unchanged-opponent clock transitions and final results passed reconstruction with the unchanged official referee. All six identify `rulesVersion=fow-v1` and 180+2. Charged move time is calculated as previous mover clock plus 2 minus next mover clock; it includes charged response work and overhead, not a measured internal search duration. Percentiles below use linear interpolation between ordered observations.

| Focus team/color | Opponent | Replay / result | Moves by focus team | Mean seconds | Median | P95 | Maximum | Ending clock |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Greek Gift White | Strongly Invisible v41 | [nnjy0an4](https://6s890.lids.mit.edu/api/games/nnjy0an4), 1–0 | 14 | 6.784 | 6.134 | 8.720 | 8.839 | 113.028 |
| Greek Gift Black | In Pawn Sight v14 | [z5ss8ecx](https://6s890.lids.mit.edu/api/games/z5ss8ecx), 0–1 | 50 | 5.346 | 5.885 | 8.665 | 9.090 | 12.704 |
| Farsight White | duck v11 | [a015fk3y](https://6s890.lids.mit.edu/api/games/a015fk3y), 1–0 | 61 | 3.600 | 3.569 | 6.484 | 6.856 | 82.420 |
| Farsight Black | duck v11 | [0n28sr75](https://6s890.lids.mit.edu/api/games/0n28sr75), 1–0 | 55 | 3.906 | 4.154 | 6.286 | 7.253 | 75.174 |
| Strongly Invisible White | Greek Gift v28 | [5cpqe9fe](https://6s890.lids.mit.edu/api/games/5cpqe9fe), 0–1 | 57 | 3.978 | 3.701 | 5.839 | 8.786 | 67.245 |
| Strongly Invisible Black | Knight Vision v36 | [ssy68mio](https://6s890.lids.mit.edu/api/games/ssy68mio), 1–0 | 109 | 3.301 | 2.924 | 6.080 | 9.062 | 38.221 |

The 346 focus-team moves averaged 3.998 seconds; 343 exceeded 180 ms. Even the long games used substantial clock reserves instead of limiting every turn to a fraction of the increment. This supports testing a larger, adaptive complete-turn budget. It does not identify which computation consumed the time, establish their algorithms, or prove that our bot benefits equally.

## What the moves suggest, and what they do not

1. **Test time use as a separate factor.** Start with roughly 800–1500 ms and a clock-aware reserve, not a blind jump to nine seconds. Log whether time, depth, nodes or world count actually binds; raising milliseconds while depth three or 18,000 nodes binds may add little work. Compare a fixed-180-ms candidate against fixed-180-ms v2 for algorithmic effects, and separately compare larger-budget configurations under a declared compute policy. Budget adaptation must cover observation processing and recovery as well as search. A product improvement over the released 180-ms v2 can be useful, but must not be described as an algorithm-only gain.
2. **Make capture bait an explicit diagnostic.** In [nnjy0an4](https://6s890.lids.mit.edu/api/games/nnjy0an4), White plays `d1c2` at ply 23 and `d3h7` at ply 27. Black's `g8h7` captures the offered bishop and loses immediately to the hidden queen on c2. `g8h8` and `g8f7` would survive that turn. This is an exact test fixture for accounting for hidden defenders and refusing a superficially attractive capture. It does not prove the winner used an explicit trap model or that those alternatives would win the game.
3. **Do not make capture preference the only opponent prior.** Across the 346 focus-team moves, 164 were noncaptures despite at least one permitted capture, and 57 were captures. For example, Greek Gift repeatedly left `Nf6xe4` available while developing and castling in z5ss8ecx. Those are observations, not evidence that every declined capture was bad. An opponent mixture should preserve quiet development, escape, concealed pressure and waiting moves; a capture-only prior can eliminate the true history. Six games are far too little for fitted team-specific likelihoods.
4. **Retain hidden-king and hidden-slider possibilities.** The final attacker was hidden in the loser's immediately preceding observation in all six games: two queens, two rooks and two kings. Four final decisions had immediate safe alternatives; two had none. In ssy68mio, `Kd4-e5` walks next to unseen `Kf4` although five king moves survive. In a015fk3y, `Nh5-f4` leaves `Kg7` exposed to unseen `Qd4`. Hidden threats matter beyond tactical captures. These counts describe the selected six games only.
5. **Use longer prefixes for loss diagnosis.** Farsight had no surviving move at the final turn in 0n28sr75, and Strongly Invisible had none in 5cpqe9fe. A terminal-action classifier cannot fix positions already lost; examine the earlier approach, observation history and support loss. Conversely, a safe alternative at a terminal turn is not evidence of a winning continuation.

The existing [V3 coverage audit](../outputs/root-coverage-v3.json) gives a larger local reason to emphasize actual belief support, shortlist membership and ranking: 82 of 102 terminal losses retained immediately safe choices after the early filters, and no terminal loss lost a safe choice through those filters. All V3 games and these six public games are now development evidence, never a fresh held-out test. Their true boards may label offline diagnostics; production input must remain observations, own moves and clock.

For stronger local comparison, frozen v2 remains the validated primary opponent. A frozen, unpromoted v3 adds policy diversity but is not demonstrably stronger. Public elite replays are useful diagnostic material, not executable opponents. The independent open implementations discussed in the [parallel literature review](research-v4-literature.md) are possible future comparators after protocol, rule, time and offline-build parity is verified; their ratings cannot be assumed. Do not import an engine simply to attach an unsupported “elite” label to local results.

## Proposed V4 pre-registration

This is a proposal to freeze **before** new match results, not an executed plan or a change to the failed V3 criterion. V3 selected the best of three 16-game development samples (8–8, 10–6 and 9–7); the independent 200-game result was 97W/1D/102L, score 48.75%. That failed promotion and remains failed. The lesson is to constrain experiments using mechanisms, enlarge development evidence, and preserve a single untouched final test. [V3 validation](../outputs/validation-v3.md), [raw primary suite](../outputs/v3-holdout-171980/suite.json)

| Stage | Proposed fixed budget | Decision |
| --- | ---: | --- |
| Mechanism diagnostics | Existing local replays and the six public games; no new full games required | Record actual current particles, completed batches, shortlist and chosen-action risk. Use complete game/seed-pair splits for any fitted model. A full-board label must never become a policy feature. |
| Development | At most two predeclared variants, each 32 color-swapped pairs against v2: at most 128 games | Split each fixed panel into two 16-pair blocks. Advance only variants with the stated mechanism improvement, no correctness/resource failure, and score above 50% in both blocks. These are screening thresholds, not significance claims. If both qualify, prefer the predeclared simpler/faster variant; do not choose a winner merely from the highest noisy score. If neither qualifies, stop this cycle. |
| Primary holdout | One frozen candidate, 200 new color-swapped pairs against frozen v2: 400 games | Finish the complete sample. Require the existing 95% bounded-pair lower score bound to exceed 50%, plus the correctness/resource gates. |
| Regression controls | After the primary run, 10 fresh pairs each against v1, MIT baseline and uniform random: 60 games | Proposed minimum scores are 75%, 90% and 95%, respectively, with no protocol/time/resource failures. These small controls detect major regressions; they neither certify those true rates nor override the primary gate. |

The upper bound is **588 full games**, with no paid compute. It can stop before the holdout if development fails. It cannot stop a live holdout because the score looks favorable or unfavorable. Larger time budgets increase cost; establish runtime feasibility on non-held-out inputs before freezing this plan rather than promise a completion time. If the game budget must be reduced, choose the sample size and corresponding weaker sensitivity up front, before seeing held-out results.

For pair i, define `Xi = (score_as_white + score_as_black) / 2`, with win=1, draw=0.5, loss=0. Each pair stays intact and Xi lies in [0,1]. The current harness reports the two-sided 95% Hoeffding interval `mean(X) ± sqrt(log(40)/(2*n))`, clipped to [0,1]. At **200 pairs**, the radius is **0.0960323**; promotion needs score above **59.6032%**, equivalently at least **238.5 points in 400 games**. At 100 pairs the required score exceeds 63.5810%, so the larger sample materially improves sensitivity while retaining the same rule. This is conservative and can leave a real modest gain unconfirmed. [Hoeffding's original paper, 1963](https://www.cs.rpi.edu/academics/courses/spring06/random/hoefding.pdf)

This bound assumes independent bounded pair outcomes. Independently drawn seed streams and stable execution support that assumption; distinct trajectories alone do not establish it. Shared openings reduce coverage, and common host disturbances can induce dependence. Retain per-color W/D/L, every pair's score, failure counts and trajectory diversity alongside the interval. A pair bootstrap can be a predeclared descriptive sensitivity analysis, but must not replace the gate after looking at results. One final candidate is tested; testing several finalists or repeatedly retrying on fresh holdouts requires a new, prospectively allocated error budget, not a succession of nominal 95% claims.

Freeze exact candidate/opponent commands, source and snapshot hashes, package/dependency versions, time policy, max plies, random seed assignments and output paths in an immutable plan. Use fresh, nonoverlapping seed ranges checked against all prior plans. Randomly choose the ranges before play rather than choosing seeds from known successes. Keep both colors of a pair together and namespace pair IDs across workers. Never pool the primary opponent and weaker controls for a rating estimate.

The primary run should be the only experimental workload on the machine. Prefer one worker; if two/four workers are necessary for cost, declare the count in advance, run the entire primary sample that way, and do not overlap controls, builds, profiling or package games. In V3, those overlaps were recorded but could affect time-limited decisions. A compute-matched diagnostic must explicitly set the opponent's budget too; giving only the candidate more milliseconds answers a different question.

All planned attempts remain in the record. A candidate protocol, timeout or resource failure fails the release gate. A source change, corrupt replay or infrastructure interruption invalidates the unfinished suite; preserve its partial evidence and reasons. Any restart uses a newly frozen plan and fresh seeds, without selecting completed wins. Audit every legal move, true board, observation, clock transition and outcome; verify the final package against the frozen allowlist. Profile latency and memory separately, including long queued-observation/recovery paths. Linux/Python 3.14.8 and arena isolation remain separate verification work.

Report the final score as local evidence against the named frozen opponent. A logistic score transform is descriptive relative strength, not MIT Elo. Even a successful V4 promotion cannot establish 2200–2500 without actual authorized rated arena results tied to the played version. No further account request or submission action is part of this research.

## Source provenance

Public sources were cached locally during research; the hashes below bind the inspected bytes, not an assertion that mutable endpoints will remain unchanged. The six replays were fetched between 15:41:51 and 15:42:01 UTC on November 13, 2026. The offline audit did not execute a bot or alter production source.

| Source | SHA256 |
| --- | --- |
| [OpenAPI schema](https://www.mit.edu/~6.7980/fow/openapi.json) | `ade2723fad98e276baea735803c34cd496550a25832916816b248ec123a55166` |
| [Official client](https://www.mit.edu/~6.7980/fow/assets/main-By_mfwSY.js) | `256b016444d69bf70250bec5b382c4c2a289f178ed5469cc3b36220043b39f1f` |
| [Current starter guide](https://www.mit.edu/~6.7980/fow/assets/starter-guide-content-DTCMv2dG.js) | `a3c32c42ae9ea99d3185e735df47ae62348f936b7304aa904539a4744d0320db` |
| [Leaderboard snapshot](https://6s890.lids.mit.edu/api/leaderboard) | `0af8d81b18c0d680ba5abbcd6f1a62fdf9519f9ac0b383a008ac608867e88130` |
| Official `referee.py` inside starter ZIP; identical local file | `2f82ac292bfad29bcb2d701b73c060d1ca8281378b4598f08a620fe5c97255be` |
| nnjy0an4 replay | `18eec129b144ce999a64a5642853d7728578ccc67fd19e5118d52dfd31c73534` |
| z5ss8ecx replay | `efd451a9e54f60cce38cfbc2c80435b08141e378822b12c022a777e7b009d57d` |
| a015fk3y replay | `0b78b0201b2c7a4b7d309c25e995e4ca8bb7e82060a9482e3404ad9cef915120` |
| 0n28sr75 replay | `258998ca76479f3e3e0daf0117ce6f9662ceb1022ecdc9e6a912d3f6a6493638` |
| 5cpqe9fe replay | `05c9e4d67fc4396c24c1d336eb0eef043a81583a6b39e047f4b87aa8d5aaac48` |
| ssy68mio replay | `3cf6da862e570e874cf8b7f345f78a8d601580a02f1dbad7807516d7bea148f5` |
