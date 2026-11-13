# Independent host review of the V4 mechanism outcome

Reviewed November 13, 2026. Scope: the frozen experiment plan, registration,
fixed-prefix diagnostic harness and its tracing helper, two completed diagnostic
JSON files, and their exact source archive. This is a host-side source/data
review, not a SoloCo QA verdict. No policy profiles, full games or training were
rerun; production files and criteria were not changed.

## Disposition

**End this cycle without promotion and without starting the strength stage.**
Both variants failed the frozen mechanism prerequisite. No possible development
match score can make either qualify under the unchanged conjunction of gates.
There is therefore no eligible entrant for the conditional 400-game holdout or
60 controls. V2 remains the release; no V4 playing-strength or arena-rating
conclusion follows.

There is a procedural qualification: [the frozen plan](experiment-v4.md)
explicitly says “Both variants get” development panels and “Complete both
panels”. Its wording does not expressly exempt variants that fail the earlier
mechanism screen. Thus skipping the unstarted 128 development games should be
recorded honestly as a **stop/deviation from that completion instruction**, not
as completion of every planned procedure. This is a conservative termination:
it preserves both failures and cannot create a favorable selection. It does
not justify changing criteria, rescuing either variant or discarding results.

Recommended status text: **“Mechanism screen failed for both candidates;
stopped before strength testing. The planned 128 development games were not
run, a documented procedural deviation because neither candidate remained
eligible. The conditional 400-game holdout and 60 controls were not started.”**
Preserve the immutable registration and unused seeds, all diagnostic evidence
and the pre-repair source archive. Any future candidate needs a new prospective
plan; do not retroactively relabel this one. There is no partly completed
strength suite from which successful games are being selected.

## Independently checked outcome

I reconstructed both original trajectories with the unchanged official referee
and matched all 90 unique candidate prefix identities in each result. For every
prefix I checked the chosen move's legality, observation, truth-board FEN,
complete set of immediately safe legal actions, fatal-action label, original
move committed for the next prefix, original clock and prefix-move hash.
I recomputed the two original trap identities and the response-time arithmetic
and nearest-rank P95 used by the harness.

| Frozen screen metric | A: bank | B: learned blend | Required |
| --- | ---: | ---: | --- |
| Complete unique prefixes | 90 | 90 | 90 |
| Prefixes with a true-safe legal action | 90 | 90 | Eligibility condition for fatal count |
| Fatal choices despite a safe alternative | 3 | 3 | At most 1 |
| Original terminal traps present / avoided | 2 / 0 | 2 / 0 | Both present; at least 1 avoided |
| Complete-response P95 | 601.441 ms | 601.545 ms | At most 650 ms |
| Complete-response maximum | 602.120 ms | 602.343 ms | At most 1,000 ms |
| True physical position present in current bank | 61/90 | 63/90 | Companion metric only |
| Full true repetition history present in current bank | 35/90 | 35/90 | Companion metric only |

Both variants' avoidable fatal choices occur at `0:70`, `0:74` and `1:103`.
The original V3 terminal traps are `0:74` and `1:103`; neither variant avoids
either. The additional fatal choice is an earlier counterfactual probe, not a
third actual game loss. The gate aggregation implements the frozen thresholds
and correctly returns `passed=false` for both. `status=completed` means the
measurement finished; it does not mean the candidate passed qualification.

I also rebuilt the physical positions in every serialized particle group,
checked their observation consistency and unique slot indices, and recomputed
physical support and full-history support using the stored history hashes.
The summary counts above agree. A contains 10,117 observed slots with 3,065
duplicate physical slots; B contains 9,869 with 3,363. Improved average coverage
is real on these prefixes but is explicitly insufficient to pass the screen.
The historical physical-coverage comparison is 44/90, from the recorded V3 run.

## Truth boundary and diagnostic limits

The harness gives the policy observations derived by the official referee,
its color/seed/configuration, original own-move history and recorded clock.
`choose_move` receives no truth board, oracle labels, true repetition counter
or future move. The tracing wrapper records actual search inputs and timings
without introducing oracle values. Truth-based labels are calculated only
after the decision returns. The helper's counterfactual board changes are
paired `push`/`pop` operations; no oracle-derived results are written back into
policy state. I found no direct truth or future-data channel in this path.

After the probe, the policy records the **original** own move, not its proposed
counterfactual move. Later observations therefore remain consistent with the
same historical trajectory. This is a valid conditional one-turn diagnostic,
but it does not simulate what would happen after choosing the candidate move.
After an earlier counterfactual fatal choice, later probes still occur because
the original trajectory continues. Those later rows are not additional games.

The 90 prefixes come from only two selected, previously inspected losing games.
They are strongly dependent and selected for diagnosis, not representative
independent draws. Original V3 actions used a 180 ms configuration, whereas
these variants used 600 ms and several changed mechanisms. A success would
have supported only this bundled conditional screen; the observed failure
does not prove that every V4 component or its overall game strength is worse.
It does establish that neither frozen variant met the chosen prerequisite.

Response timing includes observation processing, choice, reconstruction,
commitment of the original move and an in-memory output write/flush. It excludes
oracle diagnostic work and subprocess IPC, and does not verify arena isolation.
Timed scheduling can change sampled worlds and decisions. Full-history support
was independently recomputed from the stored bank group/history hashes; sampled
world membership itself was checked through tracing source and recorded
summaries, not by reexecuting the search or recovering unrecorded world objects.
This bounded review is sufficient for the failed action gate, not a general
proof of the profiler or runtime.

## Source binding and separate repair

The measured archive has exactly the 23 paths declared in each diagnostic's
`source_sha256`. Every archived file hash matches both declarations. The only
current source differing from those measured manifests is `bot.py`, reflecting
the later B1 deadline repair. The failed screen belongs to the archived bytes;
a subsequent source-only pass or package smoke test cannot replace that result.
This review does not independently revalidate that repair or those smoke games.

| Reviewed artifact | SHA256 |
| --- | --- |
| [Frozen plan](experiment-v4.md) | `886e257c588d2ccec82ae357b098d10975369bab5d2818768ef6fb5a4896efcd` |
| [Registration](../outputs/experiment-v4-registration.json) | `9dc20d465384b9c3f3fb39e31e8ce5ef36391dd1d9f861d928f2fcd118e3e2be` |
| [A result](../outputs/policy-replay-v4-bank.json) | `b4bf61bd957b0f001e19a766b869fcf226c2146eac42c5f2d4618151b8cc1d2e` |
| [B result](../outputs/policy-replay-v4-learned.json) | `eca69565e0972b2c0e44dc78adefc7fd23b3af4a5b0e12f4f2a3181f0529bd87` |
| [Exact measured source archive](../outputs/policy-replay-v4-source.zip) | `50b2863765547ca19f42b5dc5d4119022c25a6dad6186ee25af13b71191e04ff` |
| [Replay harness](../profile_policy_replay.py) | `01e322024935844af7e61a1e48e68d20b0f2b756ab45fa68cdf94eccd77b7c71` |
| [Tracing/diagnostic helper](../profile_belief_quality.py) | `60d940e08b7287fc165c96cabe7e04da9f2986aa9d8f1500d3d7664d68be9703` |
| Measured `bot.py`, inside archive | `ce6353897f810625b3cf217e65f57ac569ccc0dae5f0129092658250a1ce60f9` |

No criteria or source files were changed to reach this disposition. Only this
review document was added.
