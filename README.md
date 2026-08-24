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

Phases 0–4 complete (see `learning/` journal for the full build narrative).

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Repo scaffold + Razorpay test-mode spike | ✅ |
| 1 | Synthetic corpus + calibrated failure simulator | ✅ |
| 2 | Fair-naive baseline arm + RunEngine referee — **benchmark: 26.7% episodes, ₹8,317** | ✅ |
| 3 | Agent core + head-to-head — **5.6× baseline rupees, zero violations** | ✅ |
| 4 | Agentic graph: LLM brain behind guardrails · record/replay · live Payment Links · 20-world stability (**90% win-rate, mean 4.12×**) | ✅ |
| 5 | Hinglish voice module | ✅ scripts + safety validator + VoiceNode artifacts (telephony pluggable later) |
| 6 | Eval harness + measured report | ✅ `EVAL_REPORT.md` — 20 worlds, 90% win-rate, 4.12× mean, zero violations |
| 7 | Dashboard | ✅ `DASHBOARD.html` — monochrome glassmorphism, pre-rendered static, real run data |
| 8 | Pitch video + submission | 🎬 script ready (`PITCH_SCRIPT.md`); honesty doc `LIMITATIONS.md` done |

## Architecture (Phase 4)

```
 EpisodeView ──► SENSE ──► THINK (LLM brain) ──► VALIDATE (hard rails)
                                                    │
                              approved ◄────────────┤── refused → safe fallback re-checked
                                  ▼                        │ both refused ▼
                                 ACT ──schedule────────────┴──► ABSTAIN
   ACT: simulated outcome (eval/replay) or REAL Razorpay Payment Link (idempotency-keyed)
```

- **LLM proposes, rails dispose** — consent rules, touch budgets, ₹ thresholds and the
  21-day horizon are unbreakable by construction
- **Record-once-replay-forever** — every agent decision is journaled; replays are
  byte-identical, so measured results are AI-driven AND deterministic
- **Multi-seed honest claims** — the agent wins 18/20 seeded worlds (mean 4.12× baseline);
  loss-worlds are documented, not tuned away

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
