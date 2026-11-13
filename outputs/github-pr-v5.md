The released v2 bot scored below the new learned-risk configuration in a frozen 400-game local comparison: V5 recorded 261 wins, eight draws and 131 losses (66.25%). The launcher now selects the tested 600 ms, 256-particle, six-world, depth-four configuration with a 120,000-node ceiling.

Validation: all 400 primary replays audited; separate controls scored 59 wins and one loss across 60 games; 188 Python tests and two static checks passed; the exact eleven-file ZIP completed both smoke games. Independent host audits and standalone SoloCo evidence/claim reviews are retained in `outputs/validation-v5.md`, including the failed claim review and its narrow correction. Historical V3/V4 failures and unused research prototypes remain explicit.

The comparison uses equal 180+2 clocks, but V5's internal budget is 600 ms versus v2's 180 ms on a shared host. The registered interval of 55.31%–77.19% is conditional on independent pair outcomes. This is observed local release performance, not a compute-matched result or MIT arena Elo measurement.

Tested ZIP SHA256: `5b190981af47c62f9cd22834eb0bffc4be06611ac43cf6d106b6db910083d99c`.

Local technical acceptance: `decision-efb31c3b-1d39-4be8-92b6-159df37e507d`. Public publication is currently held pending reconciliation of earlier private-update scope with the repository's observed public visibility. This file is a prepared PR description; no PR has been created.
