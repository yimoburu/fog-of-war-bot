# Strength validation — version 2

November 12, 2026, America/Los_Angeles.

The strengthened local bot passed correctness, independent source review, paired matches and extracted-package checks. **2200–2500 MIT arena Elo is an unverified target, not an achieved rating.** No arena submission or activation was performed.

## Frozen held-out evaluation

[Full results and replays](strength-v2-final-71980.json): seed 71980, 180+2 time control, maximum 1,000 plies, and the default 180 ms optional search budget. Twenty seed pairs per opponent were played in both colors. Candidate and opponent seeds are recorded for every game. Runtime and evaluator hashes remained unchanged throughout.

| Opponent | White W/D/L | Black W/D/L | Total W/D/L | Score | Conservative 95% paired score interval |
| --- | ---: | ---: | ---: | ---: | --- |
| Frozen previous bot |19/0/1|20/0/0|39/0/1|97.5%|67.1%–100%|
| Official MIT capture baseline |19/0/1|20/0/0|39/0/1|97.5%|67.1%–100%|
| Official uniform random |20/0/0|20/0/0|40/0/0|100%|69.6%–100%|

All 120 games completed normally: 118 wins, 2 losses and 0 draws, with zero forfeits, protocol reports or warnings. A separate host audit replayed every move through the unmodified referee and verified legal moves, board frames, outcomes and all source hashes. All 120 terminal reasons were King capturable; all 120 full move trajectories were distinct. The frozen previous opponent exactly matches bot.py from repository commit 123a73f.

Uncertainty uses color-pair means as sampling units and a conservative 95% Hoeffding bound. Its independent-pair assumption is limited by shared openings/policies and timing. The relative logistic score transform is about +636 against the previous bot and capture baseline, with a lower bound about +124 and an unbounded upper end. These are descriptive local relative values, not absolute arena ratings. The random-opponent point transform is unbounded because the sample has no losses. No arbitrary rating anchor is assigned.

## Correctness and independent review

- [68 Python tests passed](python-tests-v2-final.log) in 20.903 seconds, including the unchanged official move oracle, every-ply protocol, inferred blockers, recovery, EP/castling, FOW terminal ordering, repetition, clock fallback and interrupted-search restoration.
- [Two Node integrity checks passed](static-tests-v2-final.log), including unchanged official sources and the explicit package manifest.
- SoloCo Research supplied design review artifact-eb6fa2ee-e239-4e27-9716-7afd584a9ecf.
- The [first independent QA review](source-review-v2-first.md), artifact-9194a3ff-904f-468b-b987-b62a3ddf18d2, returned Fail. It found unsafe cache reuse across repetition histories and missing no-move terminal filtering.
- Both defects were repaired. A regression distinguishes equal boards with different once-visited histories, compares cached and fresh scores, and fails under the old cache key. A separate official-referee fixture checks no-move loss.
- [Final independent QA](source-review-v2-final.md), artifact-c5f7bc89-184d-4fa5-9d40-056db855711a, returned **Pass for source-level readiness**, covering corrected subject artifact-6d002b7f-4b08-416e-ac85-8fec2256f43e. Native session: 01a1d3a7-813c-76b0-a004-079366cd29b9. All six supplied source/test files match the reviewed hashes. Execution evidence comes separately from the authorized host workflow; no native company_develop acceptance is claimed.

The original working-intent phrase official JSON protocol was a preliminary CoS mistake. The official referee and tested implementation use the line-oriented fow/ready/observation/clock/move protocol. This was corrected in company artifact-35ab6883-85e2-46b9-b5ae-66ac59a16cd6.

## Runtime and uncertainty measurements

[Replay-clock summary](strength-v2-summary.json): 2,085 candidate responses, mean 174.70 ms, p95 180.92 ms and maximum 191.95 ms. These derive from the official referee's rounded clock changes and include locally measured response handling. They are not Linux sandbox measurements.

The [four-game resource smoke test](resource-profile-v2.json) queues observations until the player's active turn. All four games completed normally. Across 99 candidate turns, mean latency was 171.96 ms, p95 was 180.87 ms and maximum was 180.97 ms. Whole-harness peak RSS was 34.42 MiB, including both policies, referee, Python and retained results. This is not a per-bot isolation measurement and excludes subprocess startup/IPC.

The profile observed 21 turns with exact-history tracking and 78 with explicitly approximate recovered history, with 16 recoveries. Search completed depth 1 on 31 turns, depth 2 on 53 and depth 3 on 15. Sampled hypotheses are not a calibrated posterior; most profiled turns depended on approximate recovery. The recorded pending event after each completed turn is the normal newly queued own move, not evidence that stale particles were searched.

Testing used macOS ARM/Python 3.13.2, python-chess 1.999 and chess 1.11.2. Docker was installed but its daemon was unavailable. Actual Linux amd64/Python 3.14.8 execution, CPU isolation and MIT's 4 GiB sandbox remain unverified. Deadlines are cooperative and do not guarantee hard real-time completion. Search shares root actions but determinized future branches still have strategy fusion. Visible-only safety filtering can reject moves that would be safe behind unknown blockers. No claim of optimality, CFR equivalence, or target Elo is made.

## Submission archive

[submission-v2-final.zip](submission-v2-final.zip) contains exactly eight files: bot.py, belief.py, search.py, referee.py, uniform_random.py, run.sh, requirements.txt and SOURCES.md. Every entry matches the current source byte-for-byte; no symlinks, credentials or environment files. [Package check](package-check-v2.json) extracted the archive and ran its real launcher in two full games against the previous bot, one per color. Both were normal wins.

Size: 88,364 bytes. SHA256: `bd2d088c6af77aa92ea103744ea25511cd38a8e15e19212ae7124eafb237b81b`.

Evaluation SHA256: `3f1ea54b64cbdb504d902cc63ecf51ae99144cfa5311f812a47091de50993920`.

## Retained history and next gate

[Development seed 21980](development-search-21980.json) produced 12 wins before the final corrections. [Seed 61980](strength-v2-61980.json) stopped at 76 games when source changes were detected during the QA repairs; status is aborted_source_changed. Its results are not pooled with the final 120 games. The failed review and old test log remain available. Historical v1 artifacts/manifests describe their original commit, not the current checkout. SoloCo's earlier two notices remain resolved; their failures and old attempt limits remain historical.

The next necessary gate for the requested 2200–2500 target is an authorized MIT team submission, successful arena build/runtime checks, and enough rated games against strong opponents. Team Elo carries across versions, so any claim must identify the observation date, team, active version and its record. See the [research and calibration note](../analysis/research-strength.md) and [official Submit interface](https://www.mit.edu/~6.7980/fow/?tab=submit).
