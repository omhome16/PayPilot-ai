# Build Challenges & Technical Obstacles (running log)

> Feeds the Buildathon form field: "What issues did you face while building, and how did you solve them?"
> Log EVERY obstacle — dead ends included. That's the engineering journey they want to see.

| # | Date | Phase | Obstacle | Resolution |
|---|------|-------|----------|------------|
| 1 | 2026-08-21 | 0 | Buildathon page is a JS-heavy SPA; direct fetch + web_extract returned nothing | Recovered full text via curl + HTML stripping; decoded submission format from the Google Form's embedded data |
| 2 | 2026-08-21 | 0 | Web search backend intermittently 403'd during research | Rotated queries/backends; DuckDuckGo HTML fallback; parallel subagents with retry loops |
| 3 | 2026-08-21 | 0 | Razorpay Subscriptions & Plans APIs return 401 on an unactivated (pre-KYC) account — even with valid TEST keys | Spike-verified which APIs DO work unactivated: Orders ✓, Payment Links create/read ✓. Architecture pivots to a domain-owned mandate model + real Payment Links as recovery rail |
| 4 | 2026-08-21 | 0 | Payment Link creation 400'd: "Recurring digits in customer contact are disallowed" | Razorpay rejects phone numbers like 9999999999; use realistic test contacts (e.g. 9876543210). Encoded as a validation rule in our contact fixtures |
