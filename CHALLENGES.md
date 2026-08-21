# Build Challenges & Technical Obstacles (running log)

> Feeds the Buildathon form field: "What issues did you face while building, and how did you solve them?"
> Log EVERY obstacle — dead ends included. That's the engineering journey they want to see.

| # | Date | Phase | Obstacle | Resolution |
|---|------|-------|----------|------------|
| 1 | 2026-08-21 | 0 | Buildathon page is a JS-heavy SPA; direct fetch + web_extract returned nothing | Recovered full text via curl + HTML stripping; decoded submission format from the Google Form's embedded data |
| 2 | 2026-08-21 | 0 | Web search backend intermittently 403'd during research | Rotated queries/backends; DuckDuckGo HTML fallback; parallel subagents with retry loops |
