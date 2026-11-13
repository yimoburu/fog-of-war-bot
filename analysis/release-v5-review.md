# Independent V5 release review

The frozen `learned_600` configuration passes all three release controls and the actual packaged-launcher checks. All 60 control games and both package smoke games were reconstructed under the official rules. This audit executed no new games and changed no runtime or raw evidence.

| Control | Wins | Draws | Losses | Score | Required |
| --- | ---: | ---: | ---: | ---: | ---: |
| legacy_v1 | 19 | 0 | 1 | 95% | 75% |
| official_baseline | 20 | 0 | 0 | 100% | 90% |
| uniform_random | 20 | 0 | 0 | 100% | 95% |

The control inventory is 59 wins, zero draws and one loss, with no process/rule failures. These three opponent scores remain separate regression checks. They are not pooled with the primary 261W/8D/131L v2 result. Every raw child was reaggregated; registered thresholds, fresh paired seeds, both colors, candidate/opponent argv, frozen source hashes, saved audit summaries and the final-to-controls evidence chain match.

The final archive is [submission-v5-final.zip](../outputs/submission-v5-final.zip), SHA256 `5b190981af47c62f9cd22834eb0bffc4be06611ac43cf6d106b6db910083d99c`. It contains exactly 11 allowlisted regular files, 136,506 uncompressed bytes (137,624 ZIP bytes), under the 250,000-byte payload ceiling. Member bytes match both the selected runtime and retained extraction. Modes are exactly 0755 for `run.sh` and 0644 for the remaining files; no symlinks, extra members or substituted upstream files were accepted. The selected arguments are `--risk-policy learned --search-ms 600 --particles 256 --worlds 6 --depth 4 --nodes 120000`.

The completed [package verification](../outputs/package-check-v5/verification.json) records worker exit 0, unchanged sources and two normal wins against legacy v1, one per color. Recorded argv uses the actual extracted `run.sh`; the pinned virtualenv supplies `python3`. The checker source and extraction are hash-bound. Both full replays and their saved audit agree with independent reconstruction. These games establish local launcher operability only.

Across 44 completed package candidate moves, p95 charged response latency is 601.699 ms and maximum is 602.644 ms. Recorded child maximum RSS is 52,215,808 bytes (49.80 MiB), scoped to both candidate and legacy subprocess roles in a fresh worker; it is not candidate-only memory, concurrent summed memory or a container-limit measurement. Construction/cleanup registration wraps the unchanged referee; rules and protocol remain delegated to it.

Primary confidence coverage remains conditional on independent bounded pair outcomes. Shared host effects and the 600 ms versus 180 ms comparison remain disclosed. No arena Elo, algorithm-only improvement, Linux container execution or independent-pair proof is claimed.

Documentation review: README and packaged SOURCES agree with the verified outcomes and retain the relevant limitations. Packaged SOURCES bytes were unchanged. No publication, upload or activation was performed by this reviewer.

The [machine receipt](../outputs/release-audit-v5.json) contains per-control W/B pair scores, complete input and package source hashes, replay summaries, archive manifest, recorded resource scope and the exact audit program. SHA256 `3f3b737b4e00ca6357e85bff254c6bc0f8606d645b1952a5591924a6bcbe97b0`. Created `2026-11-13T18:53:19.836880+00:00`.
