"""Calibrated outcome model — P(success | intervention, failure mode, context).

Calibration sources (full table in SIMULATOR_ASSUMPTIONS.md):
- Naive retry recovers 20–35%; full-stack dunning 65–75%  (Chargebee/Recurly industry data)
- UPI Autopay mandate creation succeeds only 30–50%        (NPCI/PSP reports)
- Insufficient funds dominates failure causes              (RBI/NPCI reporting)
Every number below is either anchored to a published band or explicitly tagged
ASSUMPTION. The eval harness (Phase 6) will run sensitivity analysis over the tagged ones.
"""

import random
from dataclasses import dataclass

from paypilot.domain.enums import FailureMode, Intervention


@dataclass(frozen=True)
class _Cell:
    """One (intervention × mode) success probability with provenance."""

    base: float
    source: str  # "published:<ref>" or "assumption"


# Base cells: P(success) for ONE ATTEMPT under stated context. v2 recalibration
# (2026-08-21, see SIMULATOR_ASSUMPTIONS.md §3-v2): Phase-1 values conflated
# per-channel attribution with per-attempt probability — stacked attempts at those
# levels implied impossible ~80% baseline recovery. Per-attempt values below keep
# the naive arm inside the published 20–35% eventual-recovery band.
_CELLS: dict[tuple[Intervention, FailureMode], _Cell] = {
    # --- SMART_RETRY (timed re-attempt on same mandate) --------------------------
    # "timed well" = near salary date (bonus applied); passing NO timing context
    # applies the untimed discount (see probability()) — blind timing forfeits the edge.
    (Intervention.SMART_RETRY, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.34, "derived-v2: per-attempt, timed-well; untimed ×0.7 ⇒ ≈0.24"
    ),
    (Intervention.SMART_RETRY, FailureMode.AUTH_TIMEOUT): _Cell(
        0.30, "published-anchored-v2: transient infra failures resolve on retry"
    ),
    (Intervention.SMART_RETRY, FailureMode.BANK_DOWNTIME): _Cell(
        0.27, "published-anchored-v2: same class; may still be down at blind retry"
    ),
    # --- RETRY (naive, untimed by definition) — the baseline's honest anchor -----
    (Intervention.RETRY, FailureMode.AUTH_TIMEOUT): _Cell(
        0.22, "published-anchored-v2: blind retry during possible ongoing downtime"
    ),
    (Intervention.RETRY, FailureMode.BANK_DOWNTIME): _Cell(
        0.19, "published-anchored-v2: blind retry may land in same outage"
    ),
    # --- PAYMENT_LINK (customer-initiated, consent-fresh) -------------------------
    (Intervention.PAYMENT_LINK, FailureMode.MANDATE_REVOKED): _Cell(
        0.22, "assumption-v2: win-back after consent withdrawal; no public benchmark"
    ),
    (Intervention.PAYMENT_LINK, FailureMode.LIMIT_EXCEEDED): _Cell(
        0.26, "assumption-v2: one-off over-cap payment converts reasonably"
    ),
    (Intervention.PAYMENT_LINK, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.24, "assumption-v2: customer pays when ready rather than when debited"
    ),
    # --- RAIL_SWITCH (re-authorize on higher-cap rail) ----------------------------
    (Intervention.RAIL_SWITCH, FailureMode.LIMIT_EXCEEDED): _Cell(
        0.24, "assumption-v2: re-auth friction caps conversion; works when cap was the blocker"
    ),
    # --- VOICE_NUDGE (Hinglish call → customer completes payment) -----------------
    (Intervention.VOICE_NUDGE, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.28, "assumption-v2: personal contact lifts per-touch conversion"
    ),
    (Intervention.VOICE_NUDGE, FailureMode.MANDATE_REVOKED): _Cell(
        0.16, "assumption-v2: intrusive channel in win-back context"
    ),
    # --- HUMAN_ESCALATION (ops team takes over) -----------------------------------
    (Intervention.HUMAN_ESCALATION, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.22, "assumption-v2: human negotiation beats automation per-touch, costs opex"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.AUTH_TIMEOUT): _Cell(
        0.24, "assumption-v2: ops completes manual auth flows"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.BANK_DOWNTIME): _Cell(
        0.22, "assumption-v2: ops coordinates alternate rail"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.LIMIT_EXCEEDED): _Cell(
        0.20, "assumption-v2: ops arranges mandate upgrade/installment"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.MANDATE_REVOKED): _Cell(
        0.14, "assumption-v2: high-touch saves some relationships"
    ),
}

# Context modifiers (v2)
_SALARY_PROXIMITY_BONUS = {  # days_to_salary → multiplier on timed retries
    0: 1.50,
    1: 1.50,
    2: 1.45,
    3: 1.30,
}
_UNTIMED_DISCOUNT = 0.70  # SMART_RETRY with no timing context = blind, forfeits edge
_ATTEMPT_DECAY = 0.60  # each subsequent attempt multiplies P by this (fatigue + signal)

_BOUNDS = (0.02, 0.85)


class OutcomeModel:
    """Deterministic given seed; refuses forbidden (intervention, mode) pairs."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)  # noqa: S311 — seeded PRNG is the design

    def probability(
        self,
        intervention: Intervention,
        mode: FailureMode,
        *,
        attempt_no: int = 1,
        days_to_salary: int | None = None,
    ) -> float:
        if intervention not in mode.permitted_interventions:
            raise ValueError(
                f"{intervention.value} not permitted for {mode.value} (compliance boundary)"
            )

        cell = _CELLS[(intervention, mode)]
        p = cell.base

        if intervention in (Intervention.SMART_RETRY, Intervention.RETRY):
            if days_to_salary is not None:
                bonus = _SALARY_PROXIMITY_BONUS.get(days_to_salary)
                if bonus is None and days_to_salary <= 3:
                    bonus = 1.15
                if bonus is not None:
                    p *= bonus
            elif intervention is Intervention.SMART_RETRY:
                # SMART_RETRY without timing context is just a blind retry
                p *= _UNTIMED_DISCOUNT
            if attempt_no > 1:
                p *= _ATTEMPT_DECAY ** (attempt_no - 1)
        elif attempt_no > 1:
            p *= max(_ATTEMPT_DECAY, 0.85) ** (attempt_no - 1)  # gentler decay for non-retries

        return round(min(max(p, _BOUNDS[0]), _BOUNDS[1]), 4)

    def draw(
        self,
        intervention: Intervention,
        mode: FailureMode,
        *,
        attempt_no: int = 1,
        days_to_salary: int | None = None,
    ) -> bool:
        return self._rng.random() < self.probability(
            intervention, mode, attempt_no=attempt_no, days_to_salary=days_to_salary
        )
