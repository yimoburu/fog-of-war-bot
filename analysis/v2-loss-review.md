# V2 loss review and risk-policy hypotheses

This is an offline forensic review of the two losses in [strength-v2-final-71980.json](../outputs/strength-v2-final-71980.json). The findings motivate experiments; they do not demonstrate that a proposed change improves overall strength or establishes an arena rating.

Evaluation SHA256: `3f1ea54b64cbdb504d902cc63ecf51ae99144cfa5311f812a47091de50993920`.

The evaluated V2 source is preserved under [opponents/v2](../opponents/v2). Its hashes match the evaluation manifest:

| File | SHA256 |
| --- | --- |
| bot.py | `99be8f7028bed556459bedab5b0163799bac4970686e33f33adea6b86c891232` |
| belief.py | `7c9f57397d5552c90eb70c425aac5cec80363d707ac71c632480bae93e504d63` |
| search.py | `a2cb6000bf92c026edba87d790d0b49f47ef759e77d7ed27a1ae4a5a6791992e` |

Both games were reconstructed from the standard start with the unmodified official referee. All 130 frames matched the recorded true boards and both players' regenerated observations. Both results were `0-1`, reason `King capturable`, with no protocol failure. The decisive moves were legal under the rules; legality does not require king safety.

Evidence levels matter here. Recorded moves, observations and results are direct replay evidence. FENs, actual attackers and safe alternatives below use evaluator-only truth. Additional particle diagnostics came from a separate reconstruction that fed the bot only its own observations and known own moves, using the recorded seed. Original per-turn particles were not logged, and deadlines can change sampling. Those reconstructed diagnostics explain a mechanism but are not a claim to have recovered the original internal state exactly. No replay truth was supplied to the policy.

**Match 32: a generic blocker penalty conflicts with explicit hypotheses.**

Identity: match ID `32`, pair `16`, White candidate seed `72012`, Black `legacy_bot.py` seed `72013`. There are 76 frames, with the terminal position at ply 75. Immediately before White chose `g2g1`:

```text
True FEN: 3k4/7p/8/6p1/6P1/7r/6K1/4q3 w - - 0 38
White observation, a1 through h8:
?????---?????-K-?????--r??????P?????????????????????????????????
```

White observes its king on g2 and pawn on g4, and the enemy rook on h3. The queen on e1 is hidden. The exact legal moves are `g2h3,g2g3,g2f3,g2h2,g2f2,g2h1,g2g1,g2f1`. The partial-board safety screen retains `g2h3,g2f2,g2g1,g2f1`. Evaluator truth shows that **only `g2h3` survives this turn**; the chosen move exposes the king to Qe1 along the first rank. This does not establish that capturing Rh3 would eventually win the game.

Full replay move history, included for reproducible reconstruction:

```text
g1f3 g8f6 b1c3 b8c6 d2d4 c6d4 f3d4 f6e4 d4e6 f7e6
c3e4 e6e5 e4c3 e5e4 c3e4 e7e5 c2c4 f8e7 c1d2 c7c5
g2g4 b7b5 c4b5 c8b7 e4c5 b7h1 e2e4 e7c5 d2e3 c5e3
f2e3 h1e4 a2a4 d8e7 d1d2 e8g8 h2h3 f8f1 e1f1 e4d5
d2d5 e7f7 d5f7 g8f7 a1d1 f7e8 f1e2 e5e4 b5b6 a7b6
a4a5 b6a5 d1d4 g7g5 d4d1 a5a4 d1d7 e8d7 e2d1 d7d8
d1e1 a4a3 b2a3 a8a3 e1e2 a3e3 e2f1 e3h3 f1g2 e4e3
g2g1 e3e2 g1g2 e2e1q g2g1
```

Observation-only evidence proves enemy occupancy on g5: White's g4 pawn would reveal g5 if the forward destination were empty, but g5 is `?`. The forced blocker first appears there at ply 54 and remains present. Its type is not directly observed.

V2's generic `blocker_risk` assigns `g2h3` an **8000-point penalty** because a hypothetical knight on g5 attacks h3. In the reconstruction, all 64 current particles instead had enemy inventory `k,p,p,p,r`, and the rook was observed on h3. Capturing h3 lost in 0/64 particles; `g2f2` lost in 17/64, and `g2g1,g2f1` in 0/64. The generic penalty therefore penalizes a threat absent from every retained explicit hypothesis. The final reconstructed choice was `g2f1`, rather than the recorded `g2g1`; both lose on the true board.

White's observed captures removed both original knights on plies 7 and 11, bishops on 31 and 41, one rook on 39, and the queen on 43. This is useful inventory evidence, but **does not categorically exclude hidden promotions**. It must not become a hard claim that no knight or queen can remain. The current-view particle support is also incomplete.

A second weakness is support loss. In the reconstruction, the true position was retained at ply 68 but absent at 70, 72 and 74. The final particles contained no promoted queen. The actual hidden pawn sequence `e4e3,e3e2,e2e1q` is visible to the evaluator, not White. Recovery only when the entire bank empties cannot repair a missing true branch while other hypotheses remain observation-consistent.

General hypothesis: suppress the duplicated generic all-piece-type blocker penalty when a fully processed, current particle bank already represents blocker contents and immediate survival. Retain the generic fallback when the bank is absent or stale. Separately test sampling that preserves promotion and king-threat diversity; a small uniform bank can discard low-frequency but decisive continuations.

**Match 58: a one-particle difference dominates the decision.**

Identity: match ID `58`, pair `9`, White candidate seed `91998`, Black `baseline.py` seed `91999`. There are 54 frames, with the terminal position at ply 53. Immediately before White chose `g1h2`:

```text
True FEN: 7k/p1p4p/8/8/R5PP/1P6/4r3/6K1 w - - 1 27
White observation, a1 through h8:
-????-K--????----P??????R-----PP-?????---???????p???????????????
```

White sees its own Kg1, Ra4, Pb3, Pg4 and Ph4; the only visible enemy is Pa7. There is no forced blocker. The hidden rook on e2 attacks h2, g2 and f2. Evaluator truth shows that all legal non-king moves, plus `g1h1,g1f1`, survive; `g1h2,g1g2,g1f2` lose immediately.

Full replay history:

```text
g1f3 g8f6 b1c3 e7e6 c3d5 f6d5 e2e3 d5e3 d2e3 g7g6
f1d3 g6g5 e1g1 b7b5 c1d2 b8a6 f3d4 b5b4 d3e2 a6c5
d2b4 f7f5 e2f3 d7d5 b4c5 f8c5 d4e6 c8e6 a2a4 c5e3
f2e3 e8g8 b2b3 f5f4 f3d5 d8d5 d1d5 e6d5 e3f4 g5f4
f1f4 f8f4 g2g3 f4a4 a1a4 d5b3 c2b3 a8e8 h2h4 g8h8
g3g4 e8e2 g1h2
```

The reconstruction reproduced `g1h2` at depth two. All 64 current particles had enemy inventory `k,p,p,p,r`, but the true position had fallen out of the retained bank by reconstructed ply 48. The final immediate-loss fractions were:

| Moves | Reconstructed losses |
| --- | ---: |
| g1h2, g1g2 | 1/64 |
| g1h1 and every legal non-king move | 2/64 |
| g1f2 | 8/64 |
| g1f1 | 9/64 |

V2 subtracts `30000 * loss_fraction`. A one-particle difference therefore contributes **468.75 points**, outweighing the geometric hidden-risk preference for h1: risk score 5.777 for h1 versus 9.12 for h2. These are correlated, approximate hypotheses rather than independent posterior samples. Treating 1/64 versus 2/64 as a finely calibrated risk distinction is unsupported. The observations do not uniquely reveal Re2, so this is not a proven missed deduction of the actual rook square.

General hypotheses: bound outcome utilities; avoid counting the same immediate death in both the large-bank risk term and the small search sample; examine stability under resampling; and retain geometric caution when modeled probability differences are weak. Threat-stratified sampling and bounded rejuvenation while support is nonempty may help, but need independent tests. Hardcoding a preference for h1 or these exact positions would not address the underlying problem.

**Read-only review of the subsequent prototype.**

The inspected implementation places `risk_policy` in `bot.py`, with `legacy`, `hypothesis` and `robust` options; there is no separate `risk_policy.py`. The robust prototype removes the generic blocker penalty for current particles, bounds searched continuation values with `800*tanh(value/800)`, and combines immediate death from the larger bank with conditional surviving continuations. The 800 scale and 1600 shortlist weight are heuristic parameters, not estimated win probabilities or validated strength settings.

A concern raised during review was that adding unbounded priors outside bounded utility could rank certain modeled death above a surviving action. The latest inspected code adds a survival-first ranking in `search_root` and removes certain-death moves from the robust shortlist when alternatives survive. A small read-only probe confirmed that a surviving move with a negative prior beats a 100%-modeled-death alternative despite utility saturation. This addresses that specific concern, not calibration in general.

Remaining limitations to test:

- Generic penalties influence the initial `pool[:64]` selection before their later removal. In a high-mobility position, a useful move can be excluded before the correction reaches it.
- When all four searched worlds die but the larger bank contains surviving worlds, the empty-continuation fallback produces -800 regardless of the larger bank's survival fraction. This is a conservative bound, but its selection depends on the small sample. Conditional continuation sampling or representative surviving worlds would avoid confusing missing continuation evidence with certain loss.
- The surviving continuation sample size differs by action, and its minimum contributes to the score. That can introduce additional sample-size sensitivity; bounded utilities alone do not remove it.
- The prototype retains the same belief mechanism. Missing promotion/rook branches and approximate recovery remain possible even if aggregation improves.

The two historical positions are now development fixtures. Any parameters chosen using them must be evaluated on fresh held-out games, with color-swapped pairs and multiple opponent styles. Include mirrored/color-swapped blocker cases, promoted-piece cases, hidden sliding attacks, depleted or stale banks, and deadline interruption. Report failure rates, source hashes and timing along with wins. No proposed change here is yet proven to convert either historical loss into an eventual win or to improve arena Elo.
