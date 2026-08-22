# Build Challenges & Technical Obstacles (running log)

> Feeds the Buildathon form field: "What issues did you face while building, and how did you solve them?"
> Log EVERY obstacle — dead ends included. That's the engineering journey they want to see.

| # | Date | Phase | Obstacle | Resolution |
|---|------|-------|----------|------------|
| 1 | 2026-08-21 | 0 | Buildathon page is a JS-heavy SPA; direct fetch + web_extract returned nothing | Recovered full text via curl + HTML stripping; decoded submission format from the Google Form's embedded data |
| 2 | 2026-08-21 | 0 | Web search backend intermittently 403'd during research | Rotated queries/backends; DuckDuckGo HTML fallback; parallel subagents with retry loops |
| 3 | 2026-08-21 | 0 | Razorpay Subscriptions & Plans APIs return 401 on an unactivated (pre-KYC) account — even with valid TEST keys | Spike-verified which APIs DO work unactivated: Orders ✓, Payment Links create/read ✓. Architecture pivots to a domain-owned mandate model + real Payment Links as recovery rail |
| 4 | 2026-08-21 | 0 | Payment Link creation 400'd: "Recurring digits in customer contact are disallowed" | Razorpay rejects phone numbers like 9999999999; use realistic test contacts (e.g. 9876543210). Encoded as a validation rule in our contact fixtures |
| 5 | 2026-08-22 | 2 | Outcome cells were calibrated per-channel but policies STACK attempts — implied an impossible ~80% baseline recovery, which would have made any agent delta meaningless | v2 recalibration: all cells reinterpreted as per-attempt probabilities, untimed-smart-retry discount added, anchor test pins the published band; error + sanity math documented in SIMULATOR_ASSUMPTIONS.md §3-v2 |
| 6 | 2026-08-22 | 3 | Recovery ladder fired one step late: engine counts the opening failure as attempt 1, but the agent computed step = attempts+1 — transient episodes skipped their quick-retry and revoked episodes never sent links | Ladder keys directly on attempts_made; touch budget derived as attempts−1; humans modeled as internal handoff (not a customer touch). Caught only by integration testing — unit tests on each side passed |
| 7 | 2026-08-22 | 3 | Naive-datetime bug resurfaced in NEW agent code (IST→UTC helper subtracted offset before attaching tzinfo) despite Phase-2 fix elsewhere | Fixed at source + audited codebase-wide for the same construct; lesson: regression tests pin old incidents, new call-sites need the same review |
