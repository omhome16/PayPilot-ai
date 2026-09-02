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
| 4 | Agentic graph: LLM brain behind guardrails · record/replay · live Payment Links · 20-world stability (**85% win-rate, mean 2.42×**) | ✅ |
| 5 | Hinglish voice module | ✅ wired: `VOICE_NUDGE` decisions execute as safety-validated `VoiceCall` artifacts (engine + webhook API); telephony pluggable later |
| 6 | Eval harness + measured report | ✅ `EVAL_REPORT.md` — 20 worlds, 85% win-rate, 2.42× mean, measured zero violations |
| 7 | Dashboard | ✅ `DASHBOARD.html` — monochrome glassmorphism, pre-rendered static, real run data |
| 8 | Pitch video + submission | 🎬 script ready (`PITCH_SCRIPT.md`); honesty doc `LIMITATIONS.md` done |
| 9 | Signed webhook receiver | ✅ `POST /webhooks/razorpay` — HMAC-verified `payment.failed` → agent decision |

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
- **Voice executes for real** — an approved `VOICE_NUDGE` becomes a strictly-validated
  Hinglish `VoiceCall` artifact (template or LLM script, opt-out enforced), both in the
  engine and at the webhook; the scripted doctrine reserves calls (low-EV per attempt),
  so the dashboard shows 0 by discipline, not by absence
- **Record-once-replay-forever** — every agent decision is journaled; replays are
  byte-identical, so measured results are AI-driven AND deterministic
- **Multi-seed honest claims** — the agent wins 17/20 seeded worlds (mean 2.42× baseline);
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

Regenerate the measured artifacts (deterministic, seeded):

```bash
uv run paypilot-eval         # re-runs the 20-world sweep → EVAL_REPORT.md
uv run paypilot-dashboard    # re-runs the sweep → DASHBOARD.html
uv run paypilot-live-eval    # OPTIONAL: real OpenRouter brain vs baseline + doctrine (needs OPENROUTER_API_KEY)
```

Run the webhook receiver — signed `payment.failed` in → recovery decision out — and open the
live monitor at http://127.0.0.1:8000/monitor (one-click failure scenarios, strategy briefs,
call scripts spoken aloud via browser speech synthesis):

```bash
uv run uvicorn paypilot.api.app:create_app --factory --port 8000
```

> ⚠️ Test-mode keys only. `.env` is gitignored and must never be committed.
