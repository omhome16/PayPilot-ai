# PayPilot.AI 🛩️

**Autopilot for subscription payment recovery — built for Razorpay's AI Builder Buildathon (Track 3: AI Revenue Recovery).**

> Indian subscription businesses lose 8–15% of monthly revenue to failed auto-debit mandates
> (vs 3–6% globally). Naive retry policies recover 20–35%. Full-stack dunning recovers 65–75%.
> PayPilot is the agentic layer that closes that gap — with every money action explainable, bounded, and audited.

## What it does

```
payment.failed webhook ──▶ DIAGNOSE (LLM + rules classify the failure mode)
                              │
                              ▼
                          DECIDE (pick intervention within hard compliance gates)
                              │  gates: retry caps · quiet hours · contact budgets · exposure caps
                              ▼
                          ACT (smart retry · payment link · Hinglish voice nudge · human escalation)
                              │
                              ▼
                          MEASURE (control arm vs agent arm: ₹ recovered across a batch)
```

## Status

**Phase 0 — scaffold & spikes.** See [`../PLAN.md`](../PLAN.md) for the full phased roadmap and
[`../research/`](../research/) for the research that shaped every design decision.

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Repo scaffold + Razorpay test-mode spike | 🚧 in progress |
| 1 | Synthetic corpus + calibrated failure simulator | ⬜ |
| 2 | Baseline (control) dunning arm | ⬜ |
| 3 | Agent core: diagnose → decide loop | ⬜ |
| 4 | Interventions: retries, links, rail logic | ⬜ |
| 5 | Hinglish voice module | ⬜ |
| 6 | Eval harness + measured report | ⬜ |
| 7 | Dashboard | ⬜ |
| 8 | Pitch video + submission | ⬜ |

## Engineering principles

1. **TDD or it didn't happen** — every behavior starts as a failing test
2. **Strict types** (`mypy --strict`) and lint-clean (`ruff`) from day one — enforced in CI
3. **Bounded autonomy** — the agent can never violate a hard compliance gate, by construction
4. **Measured honesty** — synthetic outcomes are calibrated to published rates and documented in
   `SIMULATOR_ASSUMPTIONS.md`; limitations live in `LIMITATIONS.md`

## Dev setup

```bash
cd paypilot
uv sync                      # creates .venv, installs everything
cp .env.example .env         # then paste your TEST-mode keys
uv run pytest                # tests
uv run ruff check .          # lint
uv run mypy src              # types
```

> ⚠️ Test-mode keys only. `.env` is gitignored and must never be committed.
