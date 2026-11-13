# V3 experiment: not promoted

The candidate scored **97 wins, 1 draw and 102 losses against frozen v2** across the complete predeclared 200-game sample. Its score was **48.75%**; the 95% Hoeffding interval over 100 color-swapped seed pairs was **35.17%–62.33%**. The required lower bound above 50% was not met. **V2 remains the released bot.** Neither version has a measured MIT arena rating; 2200–2500 remains an unverified target.

The implementation and evidence are retained to support further improvement. Search profiling measured a speedup under the recorded conditions; the match results did not establish stronger play.

## Experiment and results

Development testing used seed base 121980, eight color-swapped pairs per policy, a 180+2 time control, a 180 ms optional search budget and a 1,000-ply limit. All 48 games completed normally. These are selection data, not held-out evidence.

| Variant | Wins | Draws | Losses |
| --- | ---: | ---: | ---: |
| Search speedup, original risk policy | 8 | 0 | 8 |
| Search speedup, duplicate blocker penalty removed | 10 | 0 | 6 |
| Search speedup, bounded risk utility | 9 | 0 | 7 |

The second variant (`--risk-policy hypothesis`) was selected before fresh validation. Only three default literals changed afterward; [this patch](../analysis/v3-development-defaults.patch) reconstructs the exact earlier bot source (apply with `git apply --unidiff-zero` in a disposable checkout). All three development files retain their original source hashes and replays.

[The final plan](v3-holdout-171980/plan.json), SHA256 `df08cbee1081e92a7f0cef7de550cb176b2e5b9a1d48a603a65ff9d2538975e2`, fixed 100 pairs in four 25-pair batches at seed bases 171980, 181980, 191980 and 201980. Candidate and opponent retained their seeds across reversed colors. Four local workers ran the suite. All batches completed with unchanged declared source hashes, no failures and no selectively excluded games. The lone draw ended by **threefold repetition** in batch 003, local match 39, with the candidate playing Black; the replay audit verified that result.

| Fresh opponent | Wins | Draws | Losses | Role |
| --- | ---: | ---: | ---: | --- |
| Frozen v2 | 97 | 1 | 102 | Primary promotion test: failed |
| Original v1 | 40 | 0 | 0 | Regression control |
| MIT baseline | 39 | 0 | 1 | Regression control |
| Uniform random | 40 | 0 | 0 | Regression control |

Primary scores were 46/100 as White and 51.5/100 as Black, counting a draw as half a point. Controls had separately predeclared thresholds and passed them; their 119/120 result does not override the failed v2 comparison. The populations are never pooled for an Elo claim. Raw results: [primary suite](v3-holdout-171980/suite.json), [v1 control](v3-control-legacy_bot-271980.json), [baseline control](v3-control-baseline-291980.json), [random control](v3-control-uniform_random-311980.json).

## Correctness and package evidence

- All 92 Python tests passed in 36.673 seconds; see the [test log](python-tests-v3.log). Three additional replay-validator corruption/self-tests and two Node static checks passed.
- Independent [core source review](source-review-v3.json) passed for the supplied experimental core. A broader evaluation review was [Blocked](source-review-v3-evaluation-blocked.json) for omitted dependencies; the [supplemental review](source-review-v3-final.json) cleared both evidence gaps. These are source-only verdicts; they do not claim native test execution or playing-strength acceptance.
- [Primary replay audit](replay-audit-v3-primary.json): 200/200 complete replays, 200 distinct trajectories and zero errors or failures. [Control audit](replay-audit-v3-controls.json): 120/120 complete replays, 119 distinct trajectories and zero errors or failures. Every move, board, both observations, clock transition and result was checked with the unchanged official referee. Distinct trajectories do not establish independence.
- The [candidate ZIP](submission-v3-candidate.zip), SHA256 `cb79de4a7e2b467053e7a888405d5aac0c9ff25160c21606ec64a1ed5af63fe0`, contains the eight allowlisted runtime files. Every entry matched the source bytes, regular-file mode and expected launcher permission. Its extracted launcher completed two smoke games against v1, one per color, with two wins and no failures; see the [package evidence](package-check-v3.json). This ZIP is experimental and is not the promoted submission.

The frozen v2 snapshot is bound to Git commit `65fde033a2e65394a676e44969a992832ee17470`. Its isolated launcher checks exact manifest membership and dependency origin, while the suite hashes all seven snapshot files plus `provenance.json`. Host tests checked the byte-exact commit snapshot, import poisoning and tamper rejection.

## Performance and limits

[Search profiling](search-profile-v3.json) used four fixed root inputs, three runs per engine and completed depth-three searches. Scores, moves and restored world states matched v2. The sum of median CPU times decreased from 3.217836 seconds to 1.939896 seconds: **1.66× faster**. This is a search-only comparison, not a whole-game speed or Elo estimate. Inputs and timing conditions are embedded for reproduction.

The primary replay clocks measured 7,080 candidate responses: mean 166.73 ms, 95th percentile 180.825 ms and maximum 202.79 ms. Controls measured 2,127 responses: mean 170.84 ms, 95th percentile 180.87 ms and maximum 215.18 ms. The optional 180 ms work budget is cooperative, not a hard real-time guarantee.

[Resource profiling](resource-profile-v3.json) completed four games normally. Whole-harness peak RSS was 32.97 MiB; 61 candidate responses had mean latency 170.37 ms, 95th percentile 180.82 ms and maximum 181.09 ms. Diagnostics reported 17 turns with exact-history hypotheses and 44 with approximate-history hypotheses; 11 recovery events occurred. An exact current observation match is not an exact posterior or a full-history guarantee.

Validation used macOS ARM/Python 3.13.2 with MIT's pinned chess packages. During portions of the primary suite, three serial regression-control workers and package/resource checks also ran on the host with 14 logical CPUs. Shared load can change time-limited decisions. Seeds and shared openings/policies limit the independence assumption behind the confidence interval. Hash checks detect persistent changes in trusted local execution; they are not hostile tamper-proof snapshots or interpreter/package attestation. Linux/Python 3.14.8 arena isolation and actual rated submission remain unverified. No arena submission occurred.

## Reproduce

On this experimental version, use fresh output paths:

```sh
.venv/bin/python -m unittest discover -v
.venv/bin/python -B audit_replays.py --self-test
node --test package.test.mjs
.venv/bin/python run_paired_suite.py --trusted-local --opponent v2_bot.py \
  --candidate-arg=--risk-policy --candidate-arg=hypothesis \
  --pairs 100 --batch-pairs 25 --workers 4 --seed 171980 \
  --base-time 180 --increment 2 --max-plies 1000 --output-dir outputs/new-suite
.venv/bin/python profile_search.py --inputs outputs/search-profile-v3.json \
  --host-activity 'Describe actual concurrent activity'
```

Reruns are new measurements: wall-clock inference means seeds do not ensure identical trajectories across machines or host loads. [Selection](../analysis/selection-v3.json), [control plan](../analysis/controls-v3.json), [v2 loss analysis](../analysis/v2-loss-review.md) and all unsuccessful development variants remain preserved. Future changes require fresh validation; the failed result cannot be reclassified as a promotion.

## Next improvement, based on measured failures

The [root-coverage audit](root-coverage-v3.json) checked all 7,080 candidate decisions in the 200-game primary suite. Hidden slider blockers caused 34 truly safe moves to be filtered on 23 turns (0.325%), but never removed every safe move and affected none of the 102 terminal losing decisions. The 64-move detail cutoff applied once, also outside a terminal losing decision.

At 82 of the 102 terminal losses, immediate safe alternatives remained eligible after those filters; the other 20 positions had no immediate safe move. This evidence does not support prioritizing a filter rewrite. The next diagnostic should distinguish missing hidden-threat hypotheses, the twelve-move shortlist, and ranking among retained moves. It must record the actual current particle bank; reconstructing old particles from seeds and wall-clock runs does not recover their exact original state. Safe alternatives at one turn do not establish an eventual win.

The analyzer uses referee truth only after the fact to assess choices. No privileged board information enters the production policy. These games are now development evidence for future changes; a future candidate needs a new held-out sample.
