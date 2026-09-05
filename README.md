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

Phases 0–9 complete (see `learning/` journal for the full build narrative).

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Repo scaffold + Razorpay test-mode spike | ✅ |
| 1 | Synthetic corpus + calibrated failure simulator | ✅ |
| 2 | Fair-naive baseline arm + RunEngine referee — **benchmark: 26.7% episodes, ₹8,317** | ✅ |
| 3 | Agent core + head-to-head — **5.6× baseline rupees, zero violations** | ✅ |
| 4 | Agentic graph: LLM brain behind guardrails · record/replay · live Payment Links · 20-world stability (**85% win-rate, mean 2.42×**) | ✅ |
| 5 | Hinglish voice module | ✅ one-way calls **and two-way conversations**: turn loop, LLM dialogue brain, mid-call opt-out, executable outcomes (`voice/conversation.py`); telephony/STT pluggable later |
| 6 | Eval harness + measured report | ✅ `EVAL_REPORT.md` — 20 worlds, 85% win-rate, 2.42× mean, measured zero violations |
| 7 | Dashboard | ✅ **PayPilot Command Center** at `/command` — 8 live panels (Overview, Live Recovery, Agent Graph, Voice Studio, Memory/DB, Ledger, Eval, Webhook Lab) |
| 8 | Pitch video + submission | 🎬 script ready (`PITCH_SCRIPT.md`); honesty doc `LIMITATIONS.md` done |
| 9 | Signed webhook receiver | ✅ `POST /webhooks/razorpay` — HMAC-verified `payment.failed` → agent decision |

## Architecture

```
 payment.failed ──► SENSE (retrieve context from SQLite via tool calls)
                        │  lookup_customer · episode_history · recent_decisions
                        │  consent_status · next_payday   (every call audited)
                        ▼
                    THINK (LLM brain proposes) ──► VALIDATE (hard rails, unbreakable)
                        │                              │ approved / refused
                        ▼                              ▼
                    ACT (smart retry · payment link · voice · escalate)
                        │
                        ▼
                    MEASURE (ledger + eval: control vs agent arm)
   Demo surface: Command Center (/command) — every subsystem live on one screen
```

- **LLM proposes, rails dispose** — consent rules, touch budgets, ₹ thresholds and the
  21-day horizon are unbreakable by construction
- **The agent has real memory** — a SQLite store (`paypilot/store/`) persists customers,
  consent, episodes, decisions, voice calls and an audited `tool_calls` trail; the LLM
  brain retrieves context through `RecoveryTools` instead of being handed a static view
- **Voice is two-way** — a bounded conversational turn loop with per-turn safety
  validation, mid-call opt-out written straight to the store, and outcomes that
  schedule real ledger actions (`voice/conversation.py`)
- **Fail-loud, never silent** — an unavailable or unparseable LLM brain/writer raises;
  the episode escalates to human review with a journaled, counted, dashboard-visible
  `degraded` flag. Deterministic guardrails + the reference template brain stay as
  designed components — determinism is not the enemy, silence is
- **Record-once-replay-forever** — every agent decision is journaled; replays are
  byte-identical, so measured results are AI-driven AND deterministic
- **Multi-seed honest claims** — the agent wins 17/20 seeded worlds (mean 2.42× baseline);
  loss-worlds are documented, not tuned away

## Engineering principles

1. **TDD or it didn't happen** — every behavior starts as a failing test
2. **Strict types** (`mypy --strict`) and lint-clean (`ruff`) from day one — enforced locally before every commit
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

Optional: real TTS audio for approved call scripts (free, keyless edge-tts):

```bash
uv sync --extra tts                       # install edge-tts
uv run paypilot-tts "script text..." out.mp3   # render any script to audio
```

Regenerate the measured artifacts (deterministic, seeded):

```bash
uv run paypilot-eval         # re-runs the 20-world sweep → EVAL_REPORT.md
uv run paypilot-dashboard    # re-runs the sweep → DASHBOARD.html
uv run paypilot-live-eval    # OPTIONAL: real OpenRouter brain vs baseline + doctrine (needs OPENROUTER_API_KEY)
```

Run everything — then open the **PayPilot Command Center** at http://127.0.0.1:8000/command:

```bash
uv run uvicorn paypilot.api.app:create_app --factory --port 8000
```

### Navigating the demo (Command Center → /command)

| Panel | What you'll do there |
|---|---|
| **Overview** | KPI strip (webhooks, decisions, ₹ recovered, escalations, fail-loud alerts) — "run the demo" scenario pack fires a full episode end-to-end |
| **Live Recovery** | One-click failure scenarios (cash-crunch, revoked mandate, big-ticket 3rd failure); click any event card to drill into its decision trace |
| **Agent Graph** | Watch SENSE → THINK → VALIDATE → ACT/ESCALATE light up live; tick "simulate LLM outage" to demo the fail-loud escalation path |
| **Voice Studio** | Pick a seeded customer → start a two-way call → reply via **simulate / type / mic**; watch context get retrieved, replies safety-validated, and outcomes land in the ledger |
| **Memory/DB** | Live SQLite browser — every table, and the `tool_calls` audit streaming in as the agent queries |
| **Ledger** | Append-only audit of every decision, filterable by episode |
| **Eval** | Headline numbers + a "quick sweep" re-run button |
| **Webhook Lab** | Compose any signed `payment.failed` → watch HMAC verify → see the decision |

Other endpoints: `/monitor` (original live feed + spoken call scripts), `/health`,
`POST /webhooks/razorpay` (signed), voice conversation API
(`GET /voice/demo`, `POST /voice/open`, `POST /voice/turn`), store browser
(`GET /store/tables`, `GET /store/rows`).

> 💡 **Demo tip:** live voice (calls + conversations) needs `OPENROUTER_API_KEY`;
> without it the Voice Studio demonstrates the honest fail-loud escalation instead of
> faking a script. Everything else runs keyless.
>
> ⚠️ Test-mode keys only. `.env` is gitignored and must never be committed.
