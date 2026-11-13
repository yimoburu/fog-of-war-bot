# Independent V5 primary outcome review

Verified **261 wins, 8 draws and 131 losses in all 400 registered games** (200 color-swapped seed pairs). The observed score is **66.25%**. The frozen numerical gate passes: its conditional two-sided Hoeffding interval is **[0.5530983119, 0.7719016881]**, whose lower endpoint exceeds 0.5.

| Candidate color | Wins | Draws | Losses | Score |
| --- | ---: | ---: | ---: | ---: |
| White | 125 | 4 | 71 | 63.5000% |
| Black | 136 | 4 | 60 | 69.0000% |
| All | 261 | 8 | 131 | 66.2500% |

The exact selected policy is `learned_600`: `--risk-policy learned --search-ms 600 --particles 256 --worlds 6 --depth 4 --nodes 120000`. It faced the byte-exact released v2 snapshot at commit `65fde033a2e65394a676e44969a992832ee17470` through `v2_bot.py --search-ms 180`, using 180+2 clocks, a 1000-ply cap and four concurrent workers. Candidate and opponent search allowances were 600 ms and 180 ms, so this was not compute-matched. The selected bot SHA256 is `0d00f07a120166821b76a04e5ef2eb991c8197701bb4392d082ebbec5d4a85e8`.

All four canonical raw batches contain 100 completed games each and match their frozen child hashes, source manifests, seed schedules, interpreter and actual white/black argument vectors. All 200 pairs contain exactly one white and one black game with matching candidate/opponent seeds. Final seeds are distinct across pairs and disjoint from development. All 24 current source bindings match the final manifest, and every archived source matches its corresponding frozen hash. No source or raw-input change was detected during review. Frozen v2 dependencies independently match their recorded snapshot manifest.

Counts and pair scores were recomputed directly from raw replay results, without trusting stored aggregates or calling the experiment driver's gate. For pair score `Xi = (white points + black points)/2` in `[0,1]`, the independent calculation uses `alpha = 0.05/3` and `radius = sqrt(log(2/alpha)/(2*200)) = 0.109401688090`. The mean is 0.6625; both the stored result and interval agree. The three-attempt allocation is a prospective family-error allowance, not evidence that outcomes are independent.

The frozen official-rule replay auditor was rerun on every raw batch. All 400 games and 28,675 plies passed complete board, observation, legal-move, terminal-result and clock reconstruction with zero recorded failures or audit errors. Its input hashes and summary agree with the saved final audit. The referee hash is `2f82ac292bfad29bcb2d701b73c060d1ca8281378b4598f08a620fe5c97255be` and validator hash is `38421d2c45ca61cd2f298f246b7fd14038e6bff680ed347bb98dfea06626f2f8`. The separate outcome/latency/diversity calculations also agree with that auditor.

From all 14,305 completed candidate moves, charged response latency was p50 = 600.898 ms, p95 = 602.756 ms, p99 = 605.278 ms, maximum = 652.933 ms. These clock deltas include local IPC and host contention; they are not CPU search times.

There are 400 distinct full UCI trajectories among 400 games, each occurring once. Opening diversity is narrower: 5 distinct four-ply prefixes, 120 eight-ply prefixes, 276 twelve-ply prefixes and 393 twenty-ply prefixes. All 200 white/black pair scores are retained in the machine summary. Distinct seeds or trajectories do not establish independence: policies, openings and host conditions remain shared.

The readiness amendment at `2026-11-13T17:47:51.551317+00:00` predates final launch at `2026-11-13T17:47:59.444292+00:00` and is hash-bound in the final-start marker. It explicitly permits unrelated user jobs/services on a shared host; only the four registered workers were allowed to run FFWB experiments. This review verifies that prior declaration and its evidence binding, not whole-machine isolation or continuous monitoring. The interval's coverage and inferential interpretation remain conditional on independent bounded pair outcomes. This result supports observed local performance against this v2 configuration; it is not an algorithm-only result, controlled-hardware result or MIT arena/Elo claim.

No controls or package smoke games were evaluated here. Their release gates remain separate. No matches were executed during this audit, and raw data/runtime files were unchanged.

Machine evidence: [outcome-audit-v5.json](../outputs/outcome-audit-v5.json), SHA256 `cf235ce1c38660b7faa5b612d45135669a6c4e32eba0eb7b08c14fea79f9912d`. It contains all input/source hashes, 200 pair W/B scores, per-color/batch records, runtime identity, findings and the complete reproducible audit program. Review created `2026-11-13T18:42:07.861233+00:00`. The prose formatting was cleaned up after the numerical audit; the embedded audit program is the exact executed source.
