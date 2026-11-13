# V5: fresh full-game comparison against released v2

Started November 13, 2026. Objective: beat the exact released v2 opponent in
complete local games. The user has no MIT arena account; no absolute arena Elo
or 2200–2500 claim can be established here.

Completed outcome: the selected `learned_600` policy scored **261 wins, eight
draws and 131 losses in all 400 fresh final games**, passing the registered
conditional numerical gate. The cycle stopped after wave 1; waves 2 and 3
were not used. Separate controls scored 59 wins and one loss; both actual-ZIP
smoke games passed. See [validation](../outputs/validation-v5.md) and the
[independent outcome review](outcome-v5-review.md) for evidence and limits.

## Research translated into experiments

The [literature review](research-v4-literature.md) and
[official-arena/public-replay research](research-v4-arena-evaluation.md) remain
the source-backed research basis. In particular:

- The official clock is 180+2. The previous 180 ms internal ceiling was ours.
  The selected public strong-team replays used substantially more time, but
  those observations do not prove the same benefit for this engine.
- Larger particle banks can lose physical diversity and omit hidden threats
  while remaining nonempty. Exact current-view consistency does not establish
  a correct posterior or full-history consistency.
- Full-information minimax cannot represent all benefits of hidden threats or
  opponent mistakes. A bounded offensive heuristic is a testable hypothesis,
  not a substitute for defensive safety or information-set search.
- The V4 action probes failed their mechanism gate. They remain failed and are
  development diagnostics only. V3's independent 200 games also remain failed:
  97 wins, 1 draw, 102 losses. Neither result is relabeled as an improvement.

## Frozen experiment

The immutable [design](../outputs/v5-cycle-20261113/design.json) and
[wave 1 plan](../outputs/v5-cycle-20261113/wave-001/plan.json) were created before
the first match. They bind the driver, source archive, commands and seeds.
The opponent is byte-exact commit `65fde033a2e65394a676e44969a992832ee17470`.

| Wave 1 policy | Internal cap | Particles | Search worlds | Maximum depth | Node cap |
| --- | ---: | ---: | ---: | ---: | ---: |
| hypothesis | 600 ms | 128 | 4 | 5 | 120,000 |
| robust | 600 ms | 128 | 4 | 5 | 120,000 |
| bank | 600 ms | 256 | 6 | 4 | 120,000 |
| learned | 600 ms | 256 | 6 | 4 | 120,000 |

Released v2 uses its default 180 ms cap. Both receive identical 180+2 game
clocks and the official 1,000-ply maximum. This is a product comparison against
the release, **not a compute-matched algorithm comparison**.

Four workers run complete color-swapped pairs. Each wave screens all four
configurations on eight pairs each. The best two receive 32 fresh pairs each;
ties use the frozen configuration order. A qualifier must score above 50% in
each fixed 16-pair confirmation half, and have no failures in either stage.
The best qualifying confirmation score selects at most one final per wave.

The final is 200 new pairs / 400 games. At most three finals are allowed across
three waves, with a two-sided alpha of .05/3 for each. The gate is a bounded-pair
Hoeffding lower score bound above .5: at least **244 points out of 400**. It
assumes independent bounded pair outcomes; shared openings and host conditions
limit that assumption. The fixed sample is completed regardless of its interim
score. All attempts remain recorded. A failed final may inform a later wave,
but that wave uses fresh final seeds and its already allocated alpha.

The maximum is 1,776 strength games; stop at the first final statistical pass.
Separate post-final controls are ten pairs each against legacy v1, the official
baseline and uniform random, with score thresholds 75%, 90% and 95%, and zero
failures. These controls are not pooled with v2 strength evidence.

## Validation repair before any final

The initial independent SoloCo evaluator review
(`artifact-76e945a0-ff81-4430-8d2f-4dc6a88da9ad`) failed because the recorded
interpreter was not enforced and the review packet lacked exact wave/opponent
bindings. It also identified a concurrent invocation race. The original
review, sources, design and consumed games are retained.

The prospective [guard](../v5_guard.py) leaves the frozen driver unchanged.
It validates the exact Python executable, binary/version and installed chess
sources, and holds a nonblocking experiment lock through delegation. Its
[manifest](../outputs/v5-cycle-20261113/guard-manifest.json) binds the unchanged
design. All future register/run/final commands in this campaign must use this
guard. This is trusted local entry checking, not continuous attestation or
host-wide exclusion of arbitrary processes. The coordinator separately keeps
the primary final free of other experimental workloads.

The [seven executed guard regressions](../outputs/v5-cycle-20261113/guard-tests.json)
passed, including alternate-interpreter rejection and a concurrent subprocess
that cannot enter the driver or change the wave result. The
[source/archive checks](../outputs/v5-cycle-20261113/wave-001-binding.json) and
[v2 Git-blob comparison](../outputs/v5-cycle-20261113/v2-environment-binding.json)
bind all completed screen commands and all seven released opponent files.
Earlier development recorded interpreter/version evidence but did not capture
installed package byte hashes at game start; prospective enforcement is not
claimed retroactively. Independent SoloCo closure review
`artifact-4665fbff-f1bb-4c7a-97c9-630c29623fb1` passed the prospective guard,
binding and serialization remedy. Final entry still requires completed candidate
qualification, closure of development processes, and exclusive experimental
workload readiness; this source Pass is not a strength or release verdict.

## Additional mechanisms prepared separately

These modules are **not imported by wave 1**, and their full-game strength is
unmeasured. Unit tests and code review do not establish a match improvement.

- `belief_v5.py`: physical-position diversity with intact distinct repetition
  histories; a move prior with a uniform floor; bounded nonempty-bank
  rejuvenation; depletion retries; persistent capture accounting; delayed
  promotion repair. Approximate hypotheses are explicitly marked.
- `threat_policy.py`: observation-prefix-only per-type movement envelopes and
  known-capture inventory, independent of sampled boards. Smooth bounded
  exposure preserves ranking differences without treating it as probability.
- `fog_pressure.py`: small hypothetical hidden-reply pressure bonus, discounted
  for unknown opponent memory. No true-board policy input and no hard pruning.
- `bot_v5.py`: optional integration, graded finite-bank unanimity, physical
  diversity diagnostics, proven hidden blockers in the visible-safety pass,
  and all-or-nothing optional pressure scoring. It is an experimental entrypoint.

No running-wave source is edited. New mechanisms require their own frozen wave
and exact dependency hashes before full-game testing. The released default is
unchanged until final evidence, regression controls, package validation and
required review are complete.

## Runtime limits

Before any final game, a host check found unrelated Python jobs and security
services using CPU. Whole-machine isolation was therefore not established.
The original exclusivity condition remains recorded as unsatisfied; the
prospective amendment restricts all FFWB-owned experimental activity to the
four registered match workers while unrelated host work continues. Independent
SoloCo methodology review `artifact-72371b3b-154a-48c0-8b4d-5bd22e53c49e` passed
this narrower condition. The comparison and inferential coverage remain
conditional on the stated assumptions; process CPU observations and color
pairing do not prove independence. See the [host observation](../outputs/v5-cycle-20261113/host-readiness-observation.json).

Execution here is trusted local macOS arm64 / Python 3.13.2, with chess 1.11.2
and python-chess 1.999. It does not reproduce the official Linux amd64 / Python
3.14.8 sandbox. All observations and legal moves are checked using the retained
official referee. Independent replay auditing shares that referee; it checks
the games rather than independently proving the referee's rules.
