# PayPilot.AI — 5-Minute Pitch Video Script (v1)

> Total runtime target: 4:45–5:00. Speak in your natural voice; Hinglish welcome.
> Every number below is real and reproducible from the repo (EVAL_REPORT.md).

## Shot list (what to record, in order)

| # | Shot | Source | Duration |
|---|---|---|---|
| 1 | Hook — you on camera (or voiceover over black) | webcam | 0:20 |
| 2 | Problem — Reddit pain quotes + failure stats | screen: slides or repo README | 0:40 |
| 3 | The graph — architecture diagram | learning doc 06 diagram | 0:30 |
| 4 | Agent thinking — dashboard episode stories scroll | DASHBOARD.html | 0:35 |
| 5 | Guardrails — show a refusal + fallback in journal | tests/test_graph.py output | 0:25 |
| 6 | Voice — Hinglish script + safety validator | terminal run of VoiceNode | 0:30 |
| 7 | Results — EVAL_REPORT.md headline + per-world table | EVAL_REPORT.md | 0:45 |
| 8 | Engineering — `uv run pytest` (141 passed) + CI badge | terminal + GitHub | 0:25 |
| 9 | Close — the ask | webcam | 0:20 |

---

## Script

### 1 · Hook (0:00–0:20)

> "Every month, Indian subscription businesses silently lose 8 to 15 percent of their
> revenue. Not to competitors — to *failed auto-debits*. UPI Autopay bounces, e-NACH
> gets declined, cards expire. And most merchants recover almost nothing. I built
> PayPilot to fix that."

### 2 · Problem (0:20–1:00)

> "Here's why recovery fails today. Merchants retry blindly — same rail, fixed
> intervals — and published data says that recovers only 20 to 35 percent. Worse,
> it annoys real people. Reddit is full of it: 'trapped in a UPI Autopay loop',
> 'charged 550 rupees just for a NACH failure'. Every dumb retry can literally cost
> the customer money. Razorpay's own blog says it: rigid retries without user
> context lead to unnecessary failures. So the question is — what should a *smart*
> recovery agent do instead?"

### 3 · The graph (1:00–1:30)

> "PayPilot is a LangGraph state machine. SENSE builds the customer's story — failure
> reason, amount, days to salary, and their payment history. THINK is where the LLM
> proposes a move. VALIDATE runs it through hard rails — consent rules, touch
> budgets, rupee thresholds, a 21-day stop. The LLM proposes; the rails dispose.
> Then ACT — timed retry, payment link, Hinglish voice call, or human handoff."

### 4 · Agent thinking (1:30–2:05)

> "Here's the live dashboard. Every episode is a journaled story. This one: reliable
> payer, fails near payday — the agent chooses *strategic patience*. Do nothing
> today, check in after salary day. This one: mandate revoked — consent is gone, so
> no retry will ever work; it sends a one-click win-back link instead."

### 5 · Guardrails (2:05–2:30)

> "And when the brain proposes something forbidden? Say, retrying a revoked mandate —
> the rail refuses it, logs the override, and substitutes a safe fallback
> automatically. Across every world we tested, that counter stayed at zero
> violations. Compliance here isn't a promise — it's a type error."

### 6 · Voice (2:30–3:00)

> "For high-value customers, the agent makes a call — in Hinglish. The LLM writes the
> script: name, exact amount, payment link, and a polite pause-option. Before
> anything is spoken, a validator checks it — no threats, no pressure, no spam.
> Unsafe output never reaches a customer's ear."

### 7 · Results (3:00–3:45)

> "Now the part that matters — measured money. I ran both arms through twenty
> calibrated worlds: same subscribers, same failures, only the brain differed.
> The agent won ninety percent of the worlds. Mean recovery: four-point-one-two
> times the naive baseline. Fifty-five percent of at-risk money recovered, versus
> twenty-seven for blind retries. And I'll show you the two worlds it *lost* too —
> because I didn't tune the strategy against the test. Every decision is recorded
> and replayable, so these numbers are AI-driven *and* deterministic."

### 8 · Engineering (3:45–4:10)

> "Under the hood: 141 tests, test-driven, strict mypy, ruff, CI on every push.
> Payment links hit Razorpay's real test API with idempotency keys. Secrets are
> structurally locked to test mode. Every assumption in the simulator is sourced
> and documented."

### 9 · Close (4:10–4:35)

> "PayPilot is what revenue recovery looks like when an LLM does the thinking and
> compliance does the vetoing. It's measured, it's auditable, and it's built on
> Razorpay's own rails. I'm excited to build more of this with your AI team.
> Thank you."

---

## Recording notes
- Record terminal shots at 16px+ font; zoom to 125% in browser for dashboard shots
- Keep the dashboard scroll slow (judges pause on numbers)
- If under 5:00, cut shot 8 first, then shorten shot 2
- Upload: YouTube (unlisted) → paste link in the form
