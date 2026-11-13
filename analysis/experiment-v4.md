# V4 prospective experiment

Frozen before V4 diagnostics or full matches on November 13, 2026. The machine
has 14 logical CPUs. No paid compute or arena access is used. Released V2 and
failed V3 retain their original results. This document and the machine-readable
registration define this cycle; runtime plans additionally bind exact sources.

## Candidates and computation

A (`bank`) and B (`learned`) use the same compiled exact observation matching,
256 hypothesis slots, at most 16,384 expansions per observation, six sampled
search worlds, a twelve-move shortlist, depth three and 50,000 search nodes.
Each has a 600 ms optional-work ceiling and the existing remaining-clock cap
of 3.5%, with at least 10 ms of clock reserve. All optional inference, recovery,
risk scoring and search share an absolute deadline; an observation update is
also capped at 80 ms. Deterministic legal/safety checks, observation parsing,
move commitment and output add bounded work outside this optional ceiling.
600 ms is therefore a target allowance, not a hard complete-response guarantee.
Full charged response times, including tails, are recorded by the referee.
The complete-response mechanism ceiling is 650 ms at the 95th percentile and
1,000 ms maximum on the sequential fixed-prefix diagnostic. Any game timeout,
protocol error, OOM or illegal move fails qualification.

A uses the current bank's immediate-loss fraction. B uses an equal convex
blend of that fraction and the frozen learned prediction; if the bank is stale
or absent, B uses the model alone. The bank is heuristic and the model is
imperfectly calibrated. Neither establishes certainty about the true position.
A retains the conservative original fallback when the bank is unavailable.

For each move, deeper root samples that immediately lose are excluded from
continuation averaging, since immediate loss is accounted for by the full-bank
or blended risk `p`. Each surviving continuation value `v` is transformed to
`800*tanh(v/800)`. Let `C` be 80% of their mean plus 20% of their minimum;
if no sampled continuation survives, use `C=-800`. Score is
`(1-p)*C - 3200*p + prior`. The existing heuristic prior is not bounded; this is
an explicit loss-averse heuristic, not calibrated expected score or survival-
first optimization. Immediate death is counted once. Shortlisting uses the
existing detailed score minus `4000*p`. Candidate B's 50/50 blend, utility and
all other parameters are fixed engineering choices, not tuned on test results.

The frozen `risk_model.json` SHA256 is
`fb517196cf82698e6224cbe20ca6e1e02fe63d18d8d490dc0a5f29f813e5028b`.
Its 54-feature model uses only observation prefixes and observed own captures.
All 180,490 eligible counterfactual actions in 7,080 V3 turns were labeled
offline; the same visible-safety filter as production defines eligibility.
Actions are equally weighted. Whole seed pairs were split 70/15/15 before fit;
normalization uses training only and regularization uses validation only.
That inspected test partition is internal development evidence, not the fresh
strength holdout. Geometric baseline means a separately train-fitted logistic
mapping of the fixed exposure heuristic, not the current particle-bank risk.
The source-bound training report contains Brier scores, bins and split hashes.

## Mechanism screen

Replay both variants sequentially on the same 90 original candidate decision
prefixes in `outputs/belief-quality-v3-smoke-638900.json`. At each step commit
the original recorded move, so variants face identical histories. A policy
receives only its observations, original own moves and original clock; true
boards are used only after its decision for diagnostic labels. These are
counterfactual one-turn probes, not new game outcomes.

The historical V3 selected two immediately fatal moves, both with true-safe
alternatives. Each variant must select at most one immediately fatal move on
the 90 prefixes where a true-safe legal action exists, and must avoid at least
one of those two original terminal traps. It must satisfy the response-time
ceilings above and have no legal, observation, source or diagnostic errors.
Record current bank physical and full-history truth coverage separately,
unique physical slots, threat support in bank versus sampled worlds,
true-safe availability in the shortlist, chosen modeled risk, completed depth
and the binding search limit. These companion metrics explain the mechanism;
larger bank size or faster visibility alone cannot satisfy the gate.

## Development and final strength

Both variants get the same 32 fresh seed pairs against byte-exact frozen V2
at an explicitly verified 600 ms allowance. Each 64-game panel runs at exactly
four workers, in four eight-pair batches. The first two batches and last two
batches are the predeclared sixteen-pair blocks. A candidate advances only if
its mechanism screen passes, it has zero correctness/resource failures, and
its score is strictly above 50% in both blocks. Complete both panels; no third
variant, retuning or favorable early stop. If both qualify, select simpler A
regardless of their relative development scores. If neither qualifies, this
cycle ends without a promoted candidate; retain every result and V2 release.

The single selected candidate receives 200 new color-swapped pairs (400 games)
against V2 at 600 ms, using exactly four workers and eight twenty-five-pair
batches. The primary run is the only experimental workload on this host; no
controls, training, profiles, builds or package matches run alongside it.
The target is bundled V4 improvement over V2 at matched allowance. No claim
isolates particles, risk utility, worlds or time as the sole cause.

The promotion criterion remains the two-sided 95% Hoeffding interval for pair
scores `Xi=(white_score+black_score)/2`: lower bound must exceed 0.5 after all
200 pairs. Radius is `sqrt(log(40)/(2*200))=0.0960323`; at least 238.5 of 400
points are needed. Pair independence remains an assumption. Color results,
all pair scores, distinct trajectories and all failures accompany this bound.
No repeated finalist attempts or pooling weaker controls can override failure.

After the primary suite, run ten new pairs each against legacy V1, the official
baseline and uniform random. Minimum scores are 75%, 90% and 95%, respectively,
with zero failures; these small controls do not certify underlying true rates.
All games use 180+2 and 1,000-ply draw. Upper full-game budget: 588 games.
Diagnostic prefixes, existing unit tests and package smoke checks are separate.

Seeds are drawn once before play into `outputs/experiment-v4-registration.json`.
Dev A/B intentionally share a panel; final and controls are disjoint from that
panel and all earlier schedules. Each runtime plan records exact argv, sources,
dependency versions and seeds. Every replay must pass the unchanged official
referee audit. Any source drift, corrupt replay or infrastructure interruption
invalidates the unfinished suite; retain partial evidence and reasons. Any
restart requires a new prospective plan, fresh seeds and no selected wins.

## Independent review and limits

SoloCo Research artifact `artifact-55004532-fba2-4b5e-a5b8-074a54f4dedb`
requested these concrete definitions before freeze. The prior source and
evaluation blocks remain recorded; they were cleared for V3 by exact-source
reviews `artifact-dc7dc956-c3e0-40d3-ba56-e67ca7700d48` and
`artifact-d7bc3564-c004-46f0-8301-e2da70be8b2d`. Those reviews do not approve
V4. New independent V4 source/plan review is required before promotion.
Host tests and match evidence remain distinct from a source-only QA verdict.
Strategy fusion, small search samples, empirical risk calibration, unseen
promotions and local macOS versus Linux/Python 3.14.8 remain limitations.
MIT 2200–2500 Elo requires authorized rated arena results and remains unmeasured.
