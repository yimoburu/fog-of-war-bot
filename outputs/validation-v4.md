# V4 research and optimization results

November 13, 2026. **V4 is experimental and not promoted. V2 remains released.**
Comprehensive primary-source research, a faster belief matcher, larger banks,
loss-averse search and a locally trained risk model were implemented. Both
predeclared policy variants failed their decision-quality mechanism screen.
There is no new full-game strength estimate and no measured MIT arena Elo.

## Research and implemented changes

The [literature review](../analysis/research-v4-literature.md) examines Obscuro,
two pinned independent implementations, POMCP, ISMCTS and endgame research,
including counterevidence and rule/resource differences. The [arena review](../analysis/research-v4-arena-evaluation.md)
refreshes MIT's official protocol and environment and audits six completed
top-team games: 692 moves and 698 frames matched the unchanged referee.
Those teams averaged roughly four charged seconds per move in this selected
sample; it does not establish their algorithms or an optimal budget for us.
The [cached public evidence](../analysis/public-arena-v4/manifest.json) retains
the leaderboard snapshot, exact selection pages, six replays and a standalone
offline audit script. Its output reproduces all 692 moves and 698 frames.

Implemented in this experimental branch:

- Exact compiled visibility matching, with both-color pawn blockers, castling,
  promotion and en-passant regression coverage. The fixed [microbenchmark](observation-profile-v4.json)
  measured **6.99× faster positive matching** and 6.62× on a mixed corpus.
  These are matching-operation gains, not whole-bot speedups or strength gains.
- Configurable hypothesis banks up to 1,024 slots, a scaled expansion cap and
  a shared absolute recovery deadline. The experiment uses 256 slots.
- Configurable search worlds/depth/nodes and an explicit loss-averse bounded
  continuation formula, with immediate root death counted once.
- A 54-feature observation-only logistic risk model and a reproducible trainer.
  NumPy is training-only. Runtime uses the standard library and pinned chess.
- Source-bound actual-decision diagnostics, fixed-prefix counterfactual probes,
  runtime/model hashing in both match harnesses and an eleven-file ZIP allowlist.

The [training report](risk-model-training-v4.json) records 180,490 eligible
counterfactual actions from 200 previous V3 games. Whole seed pairs were split
70/15/15 before fitting; normalization used training only, regularization used
validation only, and the internal test partition was evaluated once. Its
learned/geometric/constant log losses were **0.10624 / 0.11549 / 0.12667**;
Brier scores were **0.02498 / 0.02609 / 0.02679**. Risk-only top choices were
fatal on **18 / 24 / 38 of 1,069 turns**. These are point estimates from fifteen
test pairs within an already-inspected development corpus, not independent
turn-level trials or fresh playing-strength evidence. High-risk calibration
is imperfect. Training emitted NumPy/Apple Accelerate arithmetic warnings;
the report preserves them, converged finite fits and separate finite/parity
checks rather than silently suppressing the warnings. No weights were retuned
after test inspection.

## Predeclared mechanism result

The [prospective plan](../analysis/experiment-v4.md) and [seed registration](experiment-v4-registration.json)
were frozen before the new probes. A used bank risk; B used the fixed 50/50
bank/model blend. Both used 600 ms optional work, 256 hypotheses, six search
worlds, depth three and 50,000 nodes. Each replayed the same 90 original
observation prefixes, then committed the original move for the next prefix.
Truth was consulted only after each policy returned. This is an off-policy
one-turn diagnostic on two selected games, not 180 new games or a win-rate test.

| Metric | Historical V3 actions | A: bank | B: learned blend |
| --- | ---: | ---: | ---: |
| Immediately fatal choices despite a safe alternative | 2/90 | 3/90 | 3/90 |
| Original terminal traps avoided | — | 0/2 | 0/2 |
| Prefixes with true physical position in bank | 44/90 | 61/90 | 63/90 |
| Prefixes with full true repetition history in bank | 3/90 | 35/90 | 35/90 |
| Prefixes retaining a true-safe shortlisted move | — | 89/90 | 90/90 |
| Response P95 / maximum | Historical 180 ms configuration | 601.44 / 602.12 ms | 601.55 / 602.34 ms |

Both variants passed source, legal-move, observation and diagnostic checks.
Both passed the declared timing screen, but failed the action criteria of at
most one fatal choice and at least one original trap avoided. In-process
response measurements include observation processing, choice, move commitment
and in-memory protocol output; they exclude oracle diagnostic work and do not
measure subprocess IPC or Linux arena isolation.

The three fatal prefixes were `0:70`, `0:74` and `1:103`. At the first, the
hidden queen was absent from the bank. At the second, the bank had collapsed
to one wrong hypothesis and assigned the only truly safe move loss risk one;
A pruned it, while B retained but badly ranked it. At the third, the killing
rook was in only one of 256 bank slots for A and four of 256 for B, and absent
from all six search worlds. Improved average truth coverage therefore did not
repair the decisive threat estimates.

Raw results: [A](policy-replay-v4-bank.json), [B](policy-replay-v4-learned.json).
The [exact 23-file measured source archive](policy-replay-v4-source.zip) has
SHA256 `50b2863765547ca19f42b5dc5d4119022c25a6dad6186ee25af13b71191e04ff`.
It preserves the pre-repair bot bytes, observation corpus, plan, dependencies
and diagnostic harness. The two earlier live diagnostic games and their
[source archive](belief-quality-v3-smoke-638900-source.zip) also remain intact.

## Correctness review and repair

The complete Python suite passed 116 tests. Independent SoloCo QA then found
one missing deadline checkpoint when learned scoring had no current particles.
That [Fail](source-review-v4-core-failed.json) is retained. The repair checks the
deadline before every candidate's risk computation and immediately after a
model prediction. A new absent/stale-bank regression verifies that an expired
prediction stops the batch and preserves the conservative fallback.
All 29 targeted search/risk tests passed after the repair, including that new
case: **117 distinct Python tests** passed across the full and targeted runs.
The two Node package/static checks also passed.

Independent [supplemental QA](source-review-v4-core-repair.json) returned a
source-only **Pass for that exact B1 repair**. The repaired `bot.py` SHA256 is
`0d00f07a120166821b76a04e5ef2eb991c8197701bb4392d082ebbec5d4a85e8`.
The probe results above belong to the archived pre-repair source; the repair
does not replace their failed verdict or establish stronger play. Timing is
cooperative, not a hard real-time guarantee.

The first separate model QA assignment was cancelled with
`ASSIGNMENT_INELIGIBLE`; its process closure was confirmed and no verdict
returned. Notice `notice-98f1b24d-ae51-4cda-a1c7-4e1fa8d1ab40` records:
“Specialist qa could not finish: Role session cancelled; held for inspection,
not automatically retried”. A replacement review was requested only after
inspection and B1 closure. An overlarge review context was rejected at 209,900
characters against a separate 200,000-character context ceiling; removing
unrelated historical search/evaluation records retained the complete model
and training evidence. The 250,000-byte development/package limit remains
unchanged; these are different limits. Historical notices and failed reviews
have not been erased.

## Delivery and limits

The [experimental ZIP](submission-v4-experimental.zip) has SHA256
`87e50d29be61d203df65438cbacc3bf80a3584c59de0d887affcf31fb2064792`:
136,627 bytes, eleven regular allowlisted entries, 135,509 uncompressed bytes.
It is an inspection/reproduction artifact, not a promoted arena submission.
Its launcher retains the historical `hypothesis`/180-ms defaults; the README
gives the explicit flags for A and B. The released [V2 ZIP](submission-v2-final.zip)
remains the recommended release.

The [extracted-package verification](package-check-v4.json) completed four
serial smoke games against legacy V1: each experimental policy in both colors.
Every game ended normally and passed the unchanged referee's full replay audit.
The maximum charged response was 601.718 ms. Per-game child-process peak memory
was 34.59–46.55 MiB, covering candidate plus legacy opponent rather than the
candidate alone. These four checks establish operability only; their outcomes
are not pooled with the mechanism screen or used as strength evidence.

**Stopped before the strength stage.** Neither policy can qualify under the
frozen conjunctive criteria because both failed the mechanism prerequisite.
The 128 development games were not run. This is an explicit procedural
deviation from the plan's wording “Complete both panels”, not a claim that all
planned work completed. It preserves both failures and cannot turn either into
a pass. The conditional 400-game holdout and 60 regression controls had no
eligible entrant and were not run. Their seeds remain unused. No threshold,
candidate ranking or sample size was changed after observing results.
An [independent host outcome review](../analysis/outcome-v4-review.md) reconstructed
all 180 probe decisions and serialized particle groups, verified the archived
source bindings, and confirmed both failed gates and this conservative stop.
It is a source/data audit, distinct from the native SoloCo source reviews.

The replacement independent [model QA](source-review-v4-model.json) returned
a scoped **Pass for the exact experimental model implementation and training
method**. It did not reproduce training, verify empirical calibration, approve
the match harness or establish playing strength. The cancelled assignment and
its notice remain historical evidence; the replacement result completes that
model-review task without erasing the interruption.

All work used local macOS ARM/Python 3.13.2 and MIT's pinned chess packages.
Linux amd64/Python 3.14.8 and the official arena sandbox remain unverified.
There was no arena submission, paid compute, account access or background
automation. The 2200–2500 MIT arena Elo target remains unmeasured.
