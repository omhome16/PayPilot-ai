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
  deterministic and CI-safe. The OpenRouter brain implements the identical contract;
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
- **Voice is simulated**: LLM/template script + safety validation + duration estimate.
  No real telephony (Exotel/Twilio) is wired; `audio_path` stays `None` without a TTS
  backend.
- **Live hands are limited to Payment Links** on Razorpay test mode (Phase-0 spike:
  Subscriptions/Plans APIs need KYC activation). Retries stay simulated everywhere.
- **No real PII**: all customers are synthetic; names/contacts are generated. A
  sanitization pass is required before any real customer string reaches an LLM prompt
  (prompt-injection surface, PLAN.md risk #6).
- **Single merchant tenant**; no auth/multi-tenancy on the dashboard (it is a static
  artifact, not a service).

## What we'd build next with more time
Cohort intelligence (peer-group strategy matching), churn-penalty sensitivity in the
outcome model, real TTS + telephony, multi-month retention windows, and wiring the
OpenRouter brain into the batch harness with token-metered ROI.
