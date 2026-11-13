# SoloCo independent source review

Reviewer: QA & Eval, native session `01a1d332-6daa-7cf1-8239-f652b643453e`.
Review: `artifact-b37ae7f7-efb1-4942-ae6c-859ea3fe01da`, source subject `artifact-9713bee9-eb5e-4fac-a62a-0886f4a74142` revision1.

Independent source verdict: Pass for source-level readiness only.

Reviewed artifact-9713bee9-eb5e-4fac-a62a-0886f4a74142 revision 1: its six host files supersede earlier same-path files; the five supplied upstream dependencies provide supporting context. No tools, commands, file changes, or tests were performed. Supplied hashes identify the review subject; I did not independently recompute them.

Findings:
1. Protocol and information boundaries: serve processes every observation, preserves the preceding observation for EP inference, emits one flushed ready, and produces a move only after a finite positive clock prompt. Duplicate clock prompts without a fresh observation are rejected. The policy reconstructs from observations; evaluator truth is not passed into it.
2. Legality and safety: candidate generation retains the supplied FOW pseudo-legal rules rather than orthodox check restrictions. Each candidate is pushed before king-safety classification, accounting for EP removal, promotion, and castling rook relocation. Every candidate receives classification before optional scoring. If apparently safe candidates exist, fallback remains within that pool; otherwise a legal candidate is still returned.
3. Corrected all-unsafe fixture: 7k/8/8/8/8/8/rr6/Kr6 w - - 0 1 exposes all three opposing rooks to Ka1. Each possible king capture remains attacked by another rook. The strong assertion is retained. This resolves the previously identified fixture defect in the reviewed host source, without changing the earlier candidate's history.
4. Horizontal EP fixture: after f7f5, g5f6 removes both f5 and g5, exposing the a5 rook's line to h5. The test verifies both removals and the resulting unsafe classification. Castling tests correctly distinguish crossing f1 under attack from finishing g1 under attack.
5. History: contradictory visible evidence removes stale sightings; hidden sightings influence heuristic risk without becoming reconstructed pieces. The identical-current-view EP test exercises both observations and verifies silence before a clock prompt.
6. Evaluation: schedules pair identical seeds across colors for each opponent. Reports preserve failures, unresolved outcomes, attempted counts, and separate raw/clean denominators. Exceptions abort with recorded evidence. Seeded scheduling does not imply identical timed gameplay; that limitation is explicitly recorded. No strength threshold or measured superiority is established.
7. Packaging: the fixed allowlist, upstream hash checks, exclusive output creation, fixed ZIP metadata, and rejection of symlink/nonregular inputs are coherent. Tests actually construct two archives, compare bytes and entries, and exercise changed-upstream, existing-output, and symlink rejection cases.

Limitations: the complete safety pass and an individual detail calculation are not preempted by the optional deadline. The tiny-clock test proves selection behavior, not timely completion. Visible-board safety can misclassify threats because hidden blockers and attackers are unknown. These are disclosed limitations, not newly discovered source blockers. The reported Node 2/2 result is supplied evidence, not Python execution evidence.

Blockers for this source-only review: none. Minimum required source patch: none identified.

Handoff to runner: combine this review with separately authorized runtime evidence bound to the exact host files: Python suites, completed paired evaluation with retained failures, and ZIP construction plus extracted-launch validation. Python behavior, timing, gameplay strength, and Python 3.14.8 compatibility remain unverified here. Prior oversized and cancelled attempts retain their recorded dispositions. This review grants no execution, integration, or submission authority.
