# Sources and AI attribution

Public sources inspected November 12, 2026 (America/Los_Angeles):

- [MIT 6.7980 course](https://www.mit.edu/~6.7980/)
- [Challenge rules](https://www.mit.edu/~6.7980/fow/?tab=rules)
- [Official starter ZIP](https://www.mit.edu/~6.7980/fow/starter/starter.zip), including its README, referee, Python tests, and neural/C++ guides
- [Current guide asset](https://www.mit.edu/~6.7980/fow/assets/starter-guide-content-DTCMv2dG.js)
- [Information sets and perfect recall](https://www.mit.edu/~6.7980/efg_intro.html)
- [Learning in extensive-form games](https://www.mit.edu/~6.7980/learning_efg.html)
- [Bandit feedback](https://www.mit.edu/~6.7980/bandit.html)
- [Syllabus and AI disclosure policy](https://www.mit.edu/~6.7980/syllabus.pdf)

Starter ZIP SHA256:
`b81f04dc5a84b72d20cb194bcaa694452842d54510827a359377abce98bacb4a`

Unmodified MIT source SHA256:

| File | SHA256 |
| --- | --- |
| baseline.py | b2b9bfcde789a7c8bd86badd2c0c34d7cc243a8df1a1d75276acabd569498c3d |
| referee.py | 2f82ac292bfad29bcb2d701b73c060d1ca8281378b4598f08a620fe5c97255be |
| requirements.txt | 391e529739e99dc63461425af4ad7afb10d89aa275922928ceb8e2f5dffa7149 |
| test_uniform_random.py | 60cd5e79fd17b2c24feaa29d7461a08406f5a3401693149b7b8acda5497f842a |
| uniform_random.py | a9d2d69c1893652dae938c596be59fc16fdf1edba3ccf491d9cf2c92d75b514a |

Codex via SoloCo assisted research, generated the bot and supporting tools, and supplied an independent QA source review. Codex then repaired two test issues and executed local validation with the user's authorization. The original MIT move generator, referee and tests remain unchanged. See the local validation report for measured results and review limitations.

The second version adds independently reviewed observation-consistent hypotheses,
explicitly approximate recovery, negative-information reasoning, tactical search,
and a paired evaluation harness. Host development assistants implemented and
tested these changes; SoloCo Research reviewed the design and SoloCo QA reviewed
the frozen engine source. `legacy_bot.py` preserves the first bot for comparison.
The new implementation does not incorporate Misty, Stockfish, Obscuro code, or
third-party trained weights. Its search is a determinization approximation, not
an implementation of CFR or a claim of reproducing Obscuro's results.

Additional primary references inspected for the strength work:

- [MIT arena API and rating definitions](https://www.mit.edu/~6.7980/fow/api.html)
- [Obscuro paper](https://arxiv.org/html/2506.01242v2)
- [Misty implementation](https://github.com/brianhliou/misty)
- [LiteEFG MIT-compatible DarkChess environment](https://liumy2010.github.io/LiteEFG/guide/deep-learning/dark-chess.html)

These sources informed design and evaluation planning. They do not establish an
Elo rating for this bot. The requested 2200–2500 MIT arena Elo remains a target
until supported by actual rated arena games.

No chess artwork is included. The archive's GPL notice concerns artwork; the inspected Python files have no license header. This document does not assign a new license to upstream code.

V4 research refreshed the official guide, starter and public completed-game
API on November 13, 2026. Six completed elite games were reconstructed against
the unchanged MIT referee for qualitative timing and hidden-threat analysis.
Detailed dated findings and source hashes are in
`analysis/research-v4-literature.md` and `analysis/research-v4-arena-evaluation.md`.
Additional primary references include:

- [Obscuro-chess implementation, pinned source](https://github.com/opowell/obscuro-chess/tree/008841f327a6a96c683652eca22586cca525946f)
- [POMCP paper](https://proceedings.neurips.cc/paper_files/paper/2010/file/edfbe1afcf9246bb0d40eb4d8027d90f-Paper.pdf)
- [Information Set Monte Carlo Tree Search](https://eprints.whiterose.ac.uk/id/eprint/75048/1/CowlingPowleyWhitehouse2012.pdf)
- [Fog of War endgames](https://arxiv.org/html/2601.18813v1)

The bundled `risk_model.json` was trained from our own 200 recorded V3
local games, grouped by complete seed pair into 70/15/15 training, validation
and test pairs. Features use only observation prefixes and known own captures;
full boards supply offline labels only. NumPy 2.2.6 was used for training, and
is not a runtime dependency. No external implementation or trained weights
were copied. Prediction quality on this development distribution is separate
from full-game strength; the model is a soft risk signal, not a safety proof.
V4 failed its own mechanism gate and was not promoted. V5 separately tested
the frozen learned-risk configuration with a 600 ms optional budget, 256 particles,
six search worlds, depth four and a 120,000-node ceiling. It scored 261 wins,
eight draws and 131 losses in 400 fresh games against byte-exact released v2
using 180 ms, under equal 180+2 clocks. This shared-host local result passed the
preregistered numerical gate; confidence coverage remains conditional on pair
independence. It is not an algorithm-only or MIT arena Elo claim.

Additional V5 belief-recovery, threat-envelope and fog-pressure prototypes are
retained separately from the selected launcher. Their unit tests and source
reviews do not establish full-game strength. SoloCo independently reviewed
evaluation-source repairs and the prospective shared-host methodology; host
tools executed the games and reconstructed their replays. See V5 validation
for the complete release evidence and retained historical failures.
