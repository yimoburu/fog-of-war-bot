# V5 local validation — November 13, 2026

**V5 passed the frozen local comparison against released v2: 261 wins, 8 draws and 131 losses in 400 games, a 66.25% score.** All 400 games were retained and independently replay-audited, with zero failures. Separate regression controls passed, and the actual extracted submission launcher passed both smoke games. **Local technical acceptance is complete; public GitHub publication is held for scope clarification.** MIT arena Elo remains unmeasured; the 2200–2500 target is not established by these results.

The selected configuration is `learned_600`, using the repaired experimental runtime already present before the V5 campaign:

```text
--risk-policy learned --search-ms 600 --particles 256 --worlds 6 --depth 4 --nodes 120000
```

Its `bot.py` SHA256 is `0d00f07a120166821b76a04e5ef2eb991c8197701bb4392d082ebbec5d4a85e8`. The opponent is the byte-exact v2 snapshot from commit `65fde033a2e65394a676e44969a992832ee17470`, run through `v2_bot.py --search-ms 180`. Both received 180 seconds plus 2 seconds per move, with a 1,000-ply cap. **The candidate's 600 ms internal allowance versus v2's 180 ms makes this a comparison against the released product, not a compute-matched or algorithm-only result.**

The [global design](v5-cycle-20261113/design.json), [wave plan](v5-cycle-20261113/wave-001/plan.json) and [source archive](v5-cycle-20261113/wave-001/sources.zip) were frozen before play. Four configurations received eight color-swapped seed pairs each. The best two received 32 fresh pairs each; qualification required scores above 50% in both fixed 16-pair halves and no failures. All development records remain intact:

| Configuration | Screen W/D/L, 16 games | Confirmation W/D/L, 64 games | Confirmation score |
| --- | ---: | ---: | ---: |
| hypothesis, 128 particles, 4 worlds, depth 5 | 9/0/7 | Not selected | — |
| robust, 128 particles, 4 worlds, depth 5 | 11/0/5 | Not selected | — |
| bank, 256 particles, 6 worlds, depth 4 | 12/0/4 | 41/3/20 | 66.40625% |
| learned, 256 particles, 6 worlds, depth 4 | 12/1/3 | 53/1/10 | 83.59375% |

All four used 600 ms and 120,000 nodes. Learned's confirmation halves scored 84.375% and 82.8125%; bank's scored 67.1875% and 65.625%. The frozen rule selected learned before the final. These 192 development games are selection evidence, not part of the final strength estimate.

The [complete final](v5-cycle-20261113/wave-001/final-learned_600/suite.json) used 200 new color-swapped pairs in four 100-game batches, with seed bases 1793702664, 1793712664, 1793722664 and 1793732664. All planned games completed; no score-based stopping, selected subset or retry was used.

| Candidate color | Wins | Draws | Losses | Score |
| --- | ---: | ---: | ---: | ---: |
| White | 125 | 4 | 71 | 63.50% |
| Black | 136 | 4 | 60 | 69.00% |
| Total | 261 | 8 | 131 | 66.25% |

The final produced **265 points out of 400**, exceeding the frozen minimum of 244. For each bounded pair score, `Xi = (white points + black points) / 2`, the declared two-sided Hoeffding radius is `sqrt(log(2 / (0.05/3)) / (2*200)) = 0.109401688090`. The resulting interval is **55.3098%–77.1902%**, with its lower endpoint above 50%. The prospective Bonferroni allocation allowed at most three finals; only the first was needed. This interval's coverage is **conditional on independent bounded pair outcomes**. Distinct seeds, color pairing and the allocation itself do not establish independence.

The [independent outcome review](../analysis/outcome-v5-review.md) and [reproducible machine audit](outcome-audit-v5.json) recomputed results and pair scores from raw replays without trusting the stored gate. They verified all 200 pairs, the frozen arguments/interpreter, all 24 final source bindings, archived sources and exact v2 provenance. Official-rule replay reconstruction checked all 400 games and 28,675 plies, including legal moves, both observations, clocks and terminal outcomes, with zero errors or recorded failures. All 400 full move trajectories were distinct, but only five four-ply opening prefixes occurred; trajectory uniqueness is not independence. The outcome-audit SHA256 is `cf235ce1c38660b7faa5b612d45135669a6c4e32eba0eb7b08c14fea79f9912d`.

**Shared-host limitation.** Before the final, whole-machine isolation was found unavailable because unrelated jobs and services continued. The [prospective readiness amendment](v5-cycle-20261113/final-readiness.json), created at 17:47:51 UTC and bound into the 17:47:59 UTC final-start marker, permitted unrelated host work while restricting FFWB experiments to the four registered workers. Independent [SoloCo methodology review](source-review-v5-shared-host.json) accepted that narrower condition. The original stronger condition remains recorded as unsatisfied. Before/after process observations do not prove continuous isolation or pair independence; shared load can affect the implementations differently. Reported performance therefore belongs to this local shared-host setup.

Across 14,305 completed candidate responses, charged latency was p50 600.898 ms, p95 602.756 ms, p99 605.278 ms and maximum 652.933 ms. These clock deltas include IPC and scheduling, not just CPU search. The internal deadline is cooperative, not a hard real-time guarantee.

The separate [release-control plan](v5-cycle-20261113/release-controls-plan.json) and [completed report](v5-cycle-20261113/release-controls/report.json) retain all 60 games. Each opponent received ten complete color pairs with the same selected candidate, 180+2 clocks and distinct registered seeds. Each suite used two five-pair jobs under a four-worker ceiling. Every replay was reconstructed independently; all controls had zero failures.

| Regression opponent | W/D/L | Score | Required score | Result |
| --- | ---: | ---: | ---: | --- |
| Legacy v1 | 19/0/1 | 95% | 75% | Pass |
| Official baseline | 20/0/0 | 100% | 90% | Pass |
| Uniform random | 20/0/0 | 100% | 95% | Pass |

These are separate regression checks. Their 59 wins and one loss are not pooled with the v2 comparison or used for an Elo estimate.

**Correctness and review evidence.** The [host test receipt](tests-v5.json) records all 188 Python tests passing in 60.075 seconds, including selected-runtime, harness, guard and package-verifier tests, plus tests of unselected prototypes. An initial Node check failed by applying the payload bound to a mixed development-file set of 250,562 bytes. That failure remains in the receipt; its `all_passed: false` is not rewritten. The [corrected static receipt](package-static-tests-v5.json) records both checks passing after applying the unchanged 250,000-byte limit to the exact eleven-file runtime allowlist. The [final static receipt](package-static-tests-v5-final.json) records both checks passing again after the README update. The separate generated-development bound remains 200,000 bytes. No selected algorithm, model, harness or match input changed for this correction.

The original [V5 evaluator QA Fail](source-review-v5-evaluation-failed.json) remains preserved. It identified unenforced interpreter identity, missing exact wave/opponent evidence and a concurrent-invocation race. The new prospective guard checks the exact interpreter/dependency fingerprint and holds an experiment lock through delegation; seven executed guard regressions passed. The [supplemental source-only Pass](source-review-v5-evaluation-repair.json), artifact `artifact-4665fbff-f1bb-4c7a-97c9-630c29623fb1`, closed that narrow remedy. Development's earlier direct invocation was not retrospectively guarded. Guard checks are trusted-local entry checks, not continuous attestation or exclusion of arbitrary host work. Source review and host execution are distinct evidence; neither is native company-development acceptance.

**Verified delivery.** [submission-v5-final.zip](submission-v5-final.zip) is **137,624 bytes**, with **136,506 uncompressed payload bytes** across exactly eleven regular allowlisted entries. Its SHA256 is `5b190981af47c62f9cd22834eb0bffc4be06611ac43cf6d106b6db910083d99c`. The executable `run.sh` embeds the selected arguments; runtime bytes match the frozen candidate, and official upstream files retain their expected hashes. Environments, credentials and SoloCo state are excluded.

The [extracted-package verification](package-check-v5/verification.json) passed with unchanged sources, worker exit 0 and no errors. Its actual launcher completed two normal wins against legacy v1, one per color; both full replays passed auditing. These are compatibility checks, not strength evidence. Across 44 candidate responses, p95 was 601.699 ms and maximum 602.644 ms. Maximum reaped-child RSS was **52,215,808 bytes** in the fresh local worker. That high-water measurement covers both bot roles under OS descendant accounting; it is not candidate-only memory, summed concurrent memory or an arena container measurement.

**Final review and local acceptance.** Standalone [final QA](source-review-v5-release-failed.json) independently reconciled every supplied pair and found the result/package evidence coherent, but failed an overstated README king-safety claim. That original Fail remains. The README now states the heuristic preference and its fatal-choice limitations. [Supplemental QA Pass](source-review-v5-release-claim-repair.json), artifact `artifact-6289d952-f4e0-4255-ba30-b595078a3cf9`, closed that sole documentation blocker without changing runtime, ZIP or results. The [post-correction static checks](package-static-tests-v5-claim-repair.json) also passed. Chief of Staff decision `decision-efb31c3b-1d39-4be8-92b6-159df37e507d` accepts the exact local release. Native reviews assessed supplied evidence; host tools executed tests and audits.

**GitHub publication: HELD.** Live GitHub metadata reports the existing `yimoburu/fog-of-war-bot` repository as public, while earlier coordinator-authored delivery records specified private updates. [Scope QA](source-review-v5-publication-scope.json), artifact `artifact-14723338-0bf4-4d47-9769-2658c97d32d3`, retains the technical Pass but requires an attributable disposition of public delivery before publication. It does not establish that the founder personally demanded privacy. No visibility change or new V5 push has occurred. The bot, ZIP and complete local evidence are ready; only public publication depends on that clarification.

**Preserved failures and notices.** [V3](validation-v3.md) remains a failed promotion test: 97 wins, one draw and 102 losses against v2. [V4](validation-v4.md) remains a failed selected-prefix mechanism test: both variants chose three immediately fatal actions in 90 prefixes and avoided neither original trap. Its full-game stages were not run; the documented stop deviation and unused seeds remain preserved. V5's separately frozen full-game result does not relabel those earlier tests.

The [original validation record](validation.md) documents resolution of both original SoloCo notices on November 12, without erasing their failed/cancelled jobs:

- `notice-1f3bf4fd-fe7b-426b-87c6-4a76d59f926a`: “Specialist engineer could not finish: Candidate exceeds byte limit: actual 114050, permitted 100000, excess 14050”. The supported project ceiling was raised to 250,000 bytes; the original immutable scope and spent attempt were retained.
- `notice-a6d47547-eafc-4b33-a79a-30c3fb8296b6`: “Specialist engineer could not finish: Role session cancelled; held for inspection, not automatically retried”. Process closure and resource release were confirmed; the correction was completed through the separately authorized host workflow, without claiming native acceptance of the cancelled job.
- The later `notice-98f1b24d-ae51-4cda-a1c7-4e1fa8d1ab40` records: “Specialist qa could not finish: Role session cancelled; held for inspection, not automatically retried”. This V4 model-QA assignment's closure was inspected and a replacement [scoped model review](source-review-v4-model.json) passed. The cancellation and notice remain historical evidence; this report does not claim a new notice-ledger resolution. The separate 200,000-character specialist-context limit is not the 250,000-byte project/package limit.

`bot_v5.py`, `belief_v5.py`, `threat_policy.py` and `fog_pressure.py` are additional research prototypes, excluded from the selected launcher and ZIP. Their unit tests do not establish playing strength, and none supplies the reported final result. The bundled learned model was trained on earlier V3 data, with truth used only for offline labels; the [training report](risk-model-training-v4.json) retains grouped splits, calibration limitations and numerical warnings. Runtime policy inputs remain observations, known own moves/captures and clocks.

All execution was local macOS arm64/Python 3.13.2 with chess 1.11.2 and python-chess 1.999. Official Linux amd64/Python 3.14.8 sandbox behavior and actual arena ratings remain unverified. No arena account, submission, paid compute or background automation was used. The evidence supports the observed local result against this exact v2 configuration; it does not establish 2200–2500 MIT Elo or general strongest-bot status.
