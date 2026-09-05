# PayPilot.AI

**Autopilot for subscription payment recovery — built for the Razorpay AI Builder Buildathon (Track 3: AI Revenue Recovery).**

> Indian subscription businesses lose 8–15% of monthly revenue to failed auto-debit mandates
> (vs 3–6% globally). Naive retry policies recover 20–35%. Full-stack dunning recovers 65–75%.
> PayPilot is the agentic layer that closes that gap — every decision made live by an LLM,
> bounded by unbreakable compliance rails, and fully audited.

## What it does

```
payment.failed webhook ──▶ SENSE   (retrieve customer context from SQLite via audited tool calls)
                               │
                               ▼
                           THINK   (live LLM brain proposes ONE next move as strict JSON)
                               │
                               ▼
                           VALIDATE (hard rails dispose: consent law, touch budgets,
                                     ₹ thresholds, 21-day horizon — unbreakable)
                               │
                               ▼
                           ACT     (smart retry · payment link · Hinglish voice call · human escalation)
                               │
                               ▼
                           PROVE   (every decision on an append-only ledger; every number measured)
```

## Live demo (2 minutes)

Prerequisites: Python 3.12+, `uv`, and a free OpenRouter key ([openrouter.ai/keys](https://openrouter.ai/keys)).

```bash
cd paypilot
uv sync                       # creates .venv, installs everything
cp .env.example .env          # then paste OPENROUTER_API_KEY + TEST-mode Razorpay keys
uv run uvicorn paypilot.api.app:create_app --factory --port 8000
```

Open **http://127.0.0.1:8000/command** — one page, narrated top to bottom:

| Step | What to do | What you'll see |
|---|---|---|
| **Start** | "Fire the demo pack" | Three signed `payment.failed` webhooks enter the real pipeline |
| **1 · Fire** | Watch the cards stream in | Live LLM action + reason per event; `Thinking ↓` jumps to its trace, `▶ Hindi audio` plays the neural Hindi call script |
| **2 · Decide** | Watch SENSE → THINK → VALIDATE → ACT light up | The actual LangGraph state at each node — the LLM's proposal, the rails' verdict |
| **3 · Speak** | Start a call, reply as the customer (simulate / type / mic) | Retrieved history, safety-validated Hinglish replies, executable outcomes (pay link, salary-timed retry, cancel, human) |
| **4 · Prove** | Ledger (auto-filtered to the episode) + Memory tables | The audit trail and the exact context the LLM reasoned over, including every tool call |
| **5 · Scale** | Re-run the sweep; fire your own failure in the Webhook Lab | Measured numbers, then a custom signed webhook through HMAC verification to decision |

> The product is **LLM-only**: the server refuses to start without `OPENROUTER_API_KEY`.
> The default model is a free tier (`nvidia/nemotron-3-super-120b-a12b:free`) verified to emit
> strict JSON. A brain outage never fakes a decision — the episode escalates loudly to human
> review with a `degraded` flag on every surface.

## Architecture

- **LLM proposes, rails dispose** — the OpenRouter brain (SENSE → THINK → VALIDATE → ACT
  via LangGraph) decides; `StandardGuardrails` veto anything unlawful and substitute a safe
  fallback. No scripted decision path exists in the product code (fakes live only in tests
  as injected seams, and in the labeled doctrine-replay benchmark).
- **Real memory** — SQLite holds customers, consent, episodes, decisions, voice calls and an
  audited `tool_calls` trail; the brain retrieves context through `RecoveryTools`, never a
  static view (`src/paypilot/store/`).
- **Two-way Hinglish voice** — a bounded turn loop (`voice/conversation.py`): deterministic
  intent tagging on input, live LLM dialogue brain, per-turn safety gates, mid-call opt-out
  written straight to the store. Playback is an explicit `hi-IN` voice in-browser, plus
  server-rendered neural Hindi audio (`POST /voice/audio`, needs `uv sync --extra tts`).
- **Record-once-replay-forever** — every decision is journaled (`GraphPolicy.journal`);
  replays serve byte-identical proposals without touching the network.

## Measured results (honest labels)

- **Doctrine-replay benchmark** (`EVAL_REPORT.md`): 20 seeded worlds, agent wins **85%**,
  mean **2.42×** baseline rupees, **zero** compliance violations. Deterministic and
  reproducible — this is the reference policy, not the live LLM.
- **Live LLM brain** (`uv run paypilot-live-eval`, needs the key): the real OpenRouter brain
  through the same rails, journaled per world and replayable. Non-deterministic by nature;
  the journals make every claim checkable.

Loss-worlds are shown unedited — nothing was tuned against specific seeds. Assumptions and
limits: `SIMULATOR_ASSUMPTIONS.md`, `LIMITATIONS.md`.

## Configuration (`.env`)

| Key | Required | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | Server refuses to start without it |
| `OPENROUTER_MODEL` | no | Default is a verified free tier; any OpenRouter chat model works |
| `OPENROUTER_TIMEOUT_S` | no | Default 90s — free-tier reasoning models think before answering |
| `RZP_KEY_ID` / `RZP_KEY_SECRET` | yes | **Test-mode only** (`rzp_test_…` enforced at startup) |
| `RZP_WEBHOOK_SECRET` | yes | HMAC verification for `POST /webhooks/razorpay` |
| `MERCHANT_NAME` | no | Spoken in voice scripts (default `PayPilot`) |

`.env` is gitignored and must never be committed.

## Commands

```bash
uv run pytest                # tests (hermetic — dummy keys, injected fake brains, no network)
uv run ruff check .          # lint
uv run mypy src              # strict types
uv run paypilot-eval         # 20-world sweep → EVAL_REPORT.md (deterministic)
uv run paypilot-dashboard    # sweep → DASHBOARD.html
uv run paypilot-live-eval    # live LLM brain vs baseline + doctrine (needs key)
uv run paypilot-tts "text" out.mp3   # Hindi neural TTS render (needs --extra tts)
```

API surface: `/command` (dashboard; `/` and `/monitor` redirect here), `POST /webhooks/razorpay`
(signed), `POST /monitor/simulate`, `POST /monitor/fire`, `POST /agent/trace`,
`GET /voice/demo`, `POST /voice/open`, `POST /voice/turn`, `POST /voice/audio`,
`GET /store/tables`, `GET /store/rows`, `POST /eval/quick`, `GET /health`.

## Repo layout

```
src/paypilot/
  api/          # FastAPI app + Command Center SPA (static/)
  graph/        # LangGraph SENSE→THINK→VALIDATE→ACT, LLM brain, guardrails, replay
  engine/       # baselines, runner/referee, narration layer
  voice/        # two-way conversations, Hinglish writers, safety, TTS
  store/        # SQLite memory + audited recovery tools
  simulator/    # calibrated synthetic population / failures / outcomes
  eval/         # multi-seed sweeps, reports, live-LLM eval
```

## License

MIT — see `LICENSE`.
