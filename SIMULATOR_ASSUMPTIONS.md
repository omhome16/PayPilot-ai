# SIMULATOR_ASSUMPTIONS.md — every number, sourced or confessed

> PayPilot's evaluation runs in a calibrated synthetic world. This file is the complete
> inventory of that world's parameters. **Rule: any number not traceable to this file
> must not exist in the simulator.** Sensitivity analysis over `assumption` entries is
> planned for Phase 6.

## 1. Population parameters

| Parameter | Value | Type | Source / rationale |
|---|---|---|---|
| Population size | 300 | design choice | batch size the track bar implies; large enough for stable rates, small enough to demo |
| Vertical mix | OTT 50% · gym 30% · SaaS 20% | assumption | mirrors Indian subscription-economy composition in D2C reports; exact split immaterial to relative claims |
| Amount bands (₹) | OTT 99–299 · gym 999–1999 · SaaS 4999–14999 | assumption | typical monthly price points per vertical |
| Mandate rail mix | UPI Autopay 60% · eNACH 30% · card 10% | published-anchored | UPI Autopay is now the dominant new-mandate rail per NPCI monthly data |
| Billing day | uniform 1–28 | assumption | simplification: real books skew to salary-adjacent days; noted as Phase 6 sensitivity item |
| Mandate headroom | 1.2×–2.0× of plan amount | assumption | mandates are approved above plan price so normal hikes don't fail |
| Contact numbers | +91, first digit 6–9, no ≥5 recurring digits | published-anchored behavior | Razorpay rejects "recurring digits" (observed live in Phase 0 spike, CHALLENGES.md #4); threshold undocumented, we conservatively use ≥5 |

## 2. Failure generation

| Parameter | Value | Type | Source / rationale |
|---|---|---|---|
| Base monthly fail rate | 8% | published-anchored | Razorpay-platform subscriptions fail 8–15%/mo vs Stripe 3–6% (research/05 §B); base sits at band floor before multipliers |
| Payday-crunch multiplier | ×3.2 (25th–3rd) | assumption | cash-tightness effect direction is certain, magnitude tuned so blended rate lands ~15%; sensitivity item |
| Festival multiplier | ×1.5, ±3d window around fixed dates | assumption | festival spending surges strain balances; lunar drift ignored (pattern > date) |
| Weekend multiplier | ×1.2 | assumption | payroll/banking friction on weekends |
| Failure-mode prior | ISF 52% · auth-timeout 14% · bank-downtime 9% · limit-exceeded 12% · revoked 13% | published-anchored shape | insufficient funds dominates RBI/NPCI failure reporting; others assumed ordering |
| Transient auto-retry | P = 0.30, gap 3 days | assumption | banks/PSPs retry transient failures once; models attempt_no=2 events |
| Debit hours | 08:00–21:59 IST only | compliance mirror | NPCI guardrail on autopay presentation windows |

## 3. Outcome cells — P(recovery)

Context modifiers applied on top of base cells:
salary-proximity bonus ×1.20–1.35 for timed retries within 3 days of salary date;
attempt decay ×0.72 per additional attempt (**ASSUMPTION**, fatigue/signal intuition).

| Intervention × Mode | Base P | Type | Rationale |
|---|---|---|---|
| SMART_RETRY × insufficient_funds | 0.55 | derived | mid-band of full-stack dunning (65–75%) discounted for timing risk on the dominant mode |
| SMART_RETRY × auth_timeout | 0.70 | published-anchored | transient failures retry well once infra recovers |
| SMART_RETRY × bank_downtime | 0.68 | published-anchored | same class as above |
| RETRY × auth_timeout | 0.45 | published-anchored | naive-retry band 20–35%, but transients sit higher; **this cell anchors the baseline arm** |
| RETRY × bank_downtime | 0.40 | published-anchored | blind retry may land inside the same downtime |
| PAYMENT_LINK × mandate_revoked | 0.30 | assumption | win-back after consent withdrawal; no public benchmark exists |
| PAYMENT_LINK × limit_exceeded | 0.35 | assumption | one-off over-cap payment converts reasonably |
| PAYMENT_LINK × insufficient_funds | 0.38 | assumption | customer pays when ready rather than when debited |
| RAIL_SWITCH × limit_exceeded | 0.42 | assumption | works when cap was genuinely the blocker; re-auth friction caps it |
| VOICE_NUDGE × insufficient_funds | 0.45 | assumption | personal contact lifts conversion; effort required caps it |
| VOICE_NUDGE × mandate_revoked | 0.25 | assumption | intrusive channel, win-back context |
| HUMAN_ESCALATION × {isf, timeout, downtime, limit, revoked} | 0.30 / 0.35 / 0.33 / 0.28 / 0.20 | assumption | high-touch beats automation per-contact but costs opex; costs tracked separately in Phase 6 |
| Global bounds | clamp [0.02, 0.85] | design choice | nothing is impossible; nothing is certain |

## 3-v2. RECALIBRATION (2026-08-21, during Phase 2 design) — per-attempt probabilities

**What was wrong:** §3's values were calibrated as *per-channel* success attribution
("of episodes this channel touched, it recovered X%"). A policy STACKS attempts, and
cumulative probability at those levels implied ~80% eventual recovery for plain retries —
far above the published naive band (20–35%). The baseline would have been superhuman,
making any agent delta meaningless.

**The correction:** all cells reinterpreted as **P(success of ONE attempt)** under the
stated context. New table:

| Intervention × Mode | Per-attempt P | Type | Note |
|---|---|---|---|
| SMART_RETRY × insufficient_funds | 0.34 | derived-v2 | timed-well; untimed ×0.7 ⇒ ≈0.24 |
| SMART_RETRY × auth_timeout / bank_downtime | 0.30 / 0.27 | published-anchored-v2 | transients resolve on retry |
| RETRY × auth_timeout / bank_downtime | 0.22 / 0.19 | published-anchored-v2 | blind retry, outage may persist |
| PAYMENT_LINK × {revoked, limit, isf} | 0.22 / 0.26 / 0.24 | assumption-v2 | |
| RAIL_SWITCH × limit_exceeded | 0.24 | assumption-v2 | |
| VOICE_NUDGE × {isf, revoked} | 0.28 / 0.16 | assumption-v2 | |
| HUMAN_ESCALATION × all five | 0.14–0.24 | assumption-v2 | |

Modifiers v2: salary-proximity ×1.50/1.50/1.45/1.30 (0–3 days); **untimed SMART_RETRY
×0.70** (blind timing forfeits the edge); attempt decay **×0.60** per extra attempt.

**Sanity check (why this is right):** blind retry on ISF ≈ 0.24/attempt → 2 attempts ≈ 42%
episode-cumulative on that mode; blended across ALL modes (naive merchants earn ₹0 on
revoked/limit episodes) lands ≈ 25–30% overall — inside the published 20–35% band. ✅

---

## 4. What this world deliberately does NOT model

- Customer cashflow *memory* across months (each episode independent)
- Partial payments, negotiated amounts, installments
- Multi-subscription households sharing one bank balance
- Seasonal churn beyond festival windows
- Retry-cost economics (Phase 6 adds ₹ cost per contact/attempt for net-value claims)

## 5. Why relative claims are safe even with assumption-heavy cells

Both arms (baseline & agent) run against **the identical seeded world** and face identical
cells. If an assumption is wrong by Δ, both arms shift together; the *delta between arms* —
the thing we claim — is far more robust than any absolute number. Absolute figures are
reported with their calibration caveats attached.
