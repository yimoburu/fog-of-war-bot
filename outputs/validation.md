# Local validation — November 12, 2026

The fog-of-war Python bot is built, independently source-reviewed, locally tested, and packaged. It has not been submitted to the arena.

## Delivered files

- [Submission ZIP](submission.zip) — six runtime/attribution files, run.sh at root, no environment or credentials.
- [Run instructions](../README.md), [source attribution](../SOURCES.md).
- [Python test log](python-tests.log), [final static test log](static-tests-final.log).
- [40-game results and full replays](evaluation-7980.json).
- [Extracted-package check](package-check.json).
- [Independent SoloCo source review](source-review.md).

## Executed checks

- 29 Python tests passed in 11.051 seconds. They include the unchanged official move-set oracle tests, castling and en passant, every-ply history, known attack and blocker cases, protocol errors, reproducibility, archive construction/rejections, and full games against both opponents.
- Two Node static checks passed after the final documentation update: original source hashes, file bounds, launcher and archive manifest.
- All 40 planned evaluation games completed at 180 seconds + 2 seconds/move, capped at 1000 plies. No forfeits, protocol failures, warnings, missing games or unresolved results.
- The final ZIP was extracted into a temporary folder, each entry compared byte-for-byte with its source, and its launcher played two full games against the baseline, once per color. Both completed normally (candidate wins); these are packaging checks, separate from the 40-game sample.

## Evaluation

Seed 7980; 10 paired seeds per opponent, each played in both colors. W/D/L is from this bot's perspective. Score = (wins + half draws) / games.

| Opponent | White W/D/L | Black W/D/L | Total W/D/L | Score |
| --- | ---: | ---: | ---: | ---: |
| baseline.py | 5/1/4 | 8/0/2 | 13/1/6 | 67.5% |
| uniform_random.py | 10/0/0 | 10/0/0 | 20/0/0 | 100.0% |

Raw replay outcomes were independently reconciled against the summary and seed schedule. Endings: {'King capturable': 39, 'Threefold repetition': 1}.

Across 620 bot moves, elapsed time derived from replay clocks was 0.95 ms mean, 1.59 ms p95 and 3.14 ms maximum. These rounded local measurements include handling overhead and are not an arena resource benchmark.

## Review and scope

SoloCo Software Engineer generated the implementation. QA & Eval independently returned **Pass for source-level readiness**, with no material source blockers, in review artifact-b37ae7f7-efb1-4942-ae6c-859ea3fe01da (native session 01a1d332-6daa-7cf1-8239-f652b643453e). The Chief of Staff performed the separately authorized host repairs, runtime validation and packaging, and accepts this local deliverable on that combined evidence. All six reviewed code/test/launcher files still match their reviewed hashes.

The successful Python runs are host evidence, not SoloCo Node-executor receipts. No native company_develop acceptance is claimed. The original retained candidates and failed receipts remain unchanged.

## Limits

Testing used macOS ARM/Python 3.13.2, python-chess 1.999 and chess 1.11.2. Exact Linux/Python 3.14.8 arena execution, CPU/memory isolation, live upload and tournament ranking remain unverified. This small paired sample is correlated and establishes neither Elo nor broad superiority. The bot uses heuristic observation history, not trained RL or a solved game. Hidden pieces can still cause losses; the deadline-aware fallback provides no hard timing guarantee.

## Resolved SoloCo notices and current development limit

Both historical notices were resolved in SoloCo's notice ledger on November 12, 2026, following the founder's instruction to raise the limit and fix both. Inspection after resolution reports zero unresolved notices. Their original failed/canceled jobs and spent attempts remain recorded; resolution does not turn them into passing native results.

- **notice-1f3bf4fd-fe7b-426b-87c6-4a76d59f926a — must_know, resolved**, reference job-a1e7e4d6-02c2-449b-9711-858e7dd352ff: “Specialist engineer could not finish: Candidate exceeds byte limit: actual 114050, permitted 100000, excess 14050”. The project ceiling was raised from 100,000 to **250,000 bytes** using the supported `.company/development.json` setting `{"max_bytes_ceiling": 250000}`. `company_develop inspect` verified effective total and per-file limits of 250,000 bytes with development supported and eligible. New scopes can select the larger budget; the original scope retains its immutable 100,000-byte limit. A smaller implementation had already been completed and validated locally.
- **notice-a6d47547-eafc-4b33-a79a-30c3fb8296b6 — must_know, resolved**, reference job-45b8bb52-3147-4d88-aca7-a210f05393b9: “Specialist engineer could not finish: Role session cancelled; held for inspection, not automatically retried”. Exact job evidence confirmed original process closure and resource release (`termination_confirmed: true`), with `ROLE_ABORTED` / `ASSIGNMENT_INELIGIBLE` following a governing authorization change. Its requested static-threshold correction was completed in the separately authorized host workflow, source-reviewed, and covered by the passing checks above. The canceled assignment is superseded; no retry or native acceptance is claimed. Future governing changes should be recorded before dispatch, and canceled work inspected before replacement.

The change and disposition are recorded in decision-5bb19ad8-645e-4255-ae18-2e0e2893f792 and the notice resolutions. Completed host validation is recorded in artifact-f0f11c76-585b-4f21-bbcb-31a8401d1851. Local `.company` state is excluded from Git; another SoloCo checkout can apply the same JSON configuration after company initialization. Raising this ceiling does not reset attempts or guarantee model context capacity.

A separate static test failed because an internal drafting target of 39 KB was enforced as a hard requirement. The host correction restored the intended 44 KB generated-file bound while preserving the 100 KB total bound. Source review also found and repaired one incorrect all-unsafe test position; its strong assertion was retained. The resulting Python suite passed.

## Reproduction

Run from the project folder, using fresh output paths:

```sh
.venv/bin/python -m unittest -v test_uniform_random test_bot
node --test package.test.mjs
.venv/bin/python evaluate.py --trusted-local --games-per-color 10 --seed 7980 --base-time 180 --increment 2 --max-plies 1000 --output outputs/evaluation-repeat.json
.venv/bin/python package_submission.py --output outputs/submission-repeat.zip
```

Identical seeds stabilize the schedule and tie breaks; host scheduling can still alter deadline fallback and timing.

ZIP size: 52654 bytes.

ZIP SHA256: `8aaa8158eef152e4788ba234d8885aa034e1d8e573cebfc9ba980652af15814c`.

Evaluation JSON SHA256: `dbe7a5ec58e160ce0beaecdacaca2719f8eebf0e09145c6ab7050ae9aec2c10c`.
