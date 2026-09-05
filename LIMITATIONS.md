# LIMITATIONS.md — what PayPilot is NOT (read before quoting results)

Honesty is a design principle, so the limits get the same care as the features.

## Evaluation limits
- **Synthetic worlds, not live payments.** Outcomes come from a calibrated simulator
  (`SIMULATOR_ASSUMPTIONS.md`). Probabilities are anchored to published industry rates
  (naive retry 20–35%, full-stack dunning 65–75%, UPI Autopay success 30–50%) but remain
  assumptions. The defensible claim is the **relative** agent-vs-baseline delta on
  identical seeded worlds — not absolute rupee figures.
- **Scripted strategist in batch evaluation.** The 20-world stability runs use a
  scripted doctrine brain (same `Brain` interface the LLM implements) so runs are
  deterministic and reproducible. The OpenRouter brain implements the identical contract;
  its decisions are journaled and replayable, but headline batch numbers were produced
  with the scripted brain.
- **Loss-worlds exist** (2 of 20). Their money sits in far-from-payday crunches and
  late-month downtime windows. We did not tune the doctrine against specific seeds —
  that would be overfitting.
- **Single month simulated** (September 2026). No multi-month retention effects.

## Modeling limits
- **One subscription per customer**; households with multiple mandates are not modeled.
- **Binary outcomes** — no partial payments, installments, or negotiated settlements.
- **Churn side-effects are not modeled.** Annoyance-driven voluntary churn would penalize
  aggressive policies; this favors nobody asymmetrically but simplifies reality.
- **Cohort effects are implicit** (verticals, salary-day clustering, reliability traits)
  but the agent does not yet do explicit peer-group matching (P4.5 profiles are
  individual, not cohort-aggregated).

## Product limits
- **Voice has no real telephony or speech synthesis on a phone line** (Exotel/Twilio
  not wired). The two-way call is real at the intelligence layer — turn loop, intent
  classification, LLM replies validated per turn, mid-call opt-out, executable
  outcomes — but the customer side is driven by the demo UI's three input modes
  (**simulate / type / mic**; mic uses the browser's Web Speech API, so it needs
  Chrome/Edge and a microphone — there is no server-side STT). `audio_path` is real
  only when the optional TTS extra is installed (`uv sync --extra tts` → edge-tts
  renders approved calls to `.mp3`); without it, audio stays `None` by design.
- **Voice is fail-loud by design**: the LIVE voice channel requires an LLM writer /
  dialogue brain (`OPENROUTER_API_KEY`); without one, or on any writer/API failure,
  a call escalates to `human_escalation` with a `degraded` flag — it never silently
  speaks a canned script or a canned reply. The deterministic template writer exists
  only as an explicitly-opted reference (eval, dashboard, engine reference nodes);
  the webhook, live monitor, and conversation engine never fall back to it
  implicitly. Demo voice scenarios therefore need an OpenRouter key to show a real
  call or conversation; without one the studio demonstrates the fail-loud path.
- **Memory is real but SQLite-local.** The agent's store (customers, consent,
  episodes, decisions, voice calls, tool calls) is a real queryable database behind a
  repository interface (the seam for Postgres in production). The live server demo
  seeds an in-memory store per process (deterministic per start); the file-backed
  `Store(path)` mode persists across restarts and is what a deployment would use.
  Consent/do-not-call marks written mid-call are honoured for the lifetime of the
  store. SQLite is the zero-infra default, not a claim of production durability.
- **Live hands are limited to Payment Links** on Razorpay test mode (Phase-0 spike:
  Subscriptions/Plans APIs need KYC activation). Retries stay simulated everywhere.
- **No real PII**: all customers are synthetic; names/contacts are generated. A
  sanitization pass is required before any real customer string reaches an LLM prompt
  (prompt-injection surface, PLAN.md risk #6).
- **Single merchant tenant**; no auth/multi-tenancy on the Command Center (it is a
  demo surface served by FastAPI, not a production service).

## What we'd build next with more time
Cohort intelligence (peer-group strategy matching), churn-penalty sensitivity in the
outcome model, real TTS + telephony, multi-month retention windows, and wiring the
OpenRouter brain into the batch harness with token-metered ROI.
