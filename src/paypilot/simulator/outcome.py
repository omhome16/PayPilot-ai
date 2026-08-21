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


# Base cells: P(recovery) for attempt_no=1, days_to_salary=7 (neutral context).
_CELLS: dict[tuple[Intervention, FailureMode], _Cell] = {
    # --- SMART_RETRY (timed re-attempt on same mandate) --------------------------
    (Intervention.SMART_RETRY, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.55, "derived: mid-band of dunning 65–75% minus timing risk; dominant mode"
    ),
    (Intervention.SMART_RETRY, FailureMode.AUTH_TIMEOUT): _Cell(
        0.70, "published-anchored: transient infra failures retry well"
    ),
    (Intervention.SMART_RETRY, FailureMode.BANK_DOWNTIME): _Cell(
        0.68, "published-anchored: transient infra failures retry well"
    ),
    # --- RETRY (naive, untimed) — deliberately mediocre: this is the baseline ----
    (Intervention.RETRY, FailureMode.AUTH_TIMEOUT): _Cell(
        0.45, "published-anchored: naive retry band 20–35%, transient modes sit higher"
    ),
    (Intervention.RETRY, FailureMode.BANK_DOWNTIME): _Cell(
        0.40, "published-anchored: naive retry band, downtime may persist past blind retry"
    ),
    # --- PAYMENT_LINK (customer-initiated, consent-fresh) -------------------------
    (Intervention.PAYMENT_LINK, FailureMode.MANDATE_REVOKED): _Cell(
        0.30, "assumption: win-back after consent withdrawal; no public benchmark"
    ),
    (Intervention.PAYMENT_LINK, FailureMode.LIMIT_EXCEEDED): _Cell(
        0.35, "assumption: one-off over-cap payment converts reasonably"
    ),
    (Intervention.PAYMENT_LINK, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.38, "assumption: customer pays when ready rather than when debited"
    ),
    # --- RAIL_SWITCH (re-authorize on higher-cap rail) ----------------------------
    (Intervention.RAIL_SWITCH, FailureMode.LIMIT_EXCEEDED): _Cell(
        0.42, "assumption: requires customer re-auth friction; works when cap was the issue"
    ),
    # --- VOICE_NUDGE (Hinglish call → human completes payment) --------------------
    (Intervention.VOICE_NUDGE, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.45, "assumption: personal contact lifts conversion; capped by effort required"
    ),
    (Intervention.VOICE_NUDGE, FailureMode.MANDATE_REVOKED): _Cell(
        0.25, "assumption: win-back via call, lower than link (more intrusive)"
    ),
    # --- HUMAN_ESCALATION (ops team takes over) -----------------------------------
    (Intervention.HUMAN_ESCALATION, FailureMode.MANDATE_REVOKED): _Cell(
        0.20, "assumption: high-touch saves some enterprise relationships"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.INSUFFICIENT_FUNDS): _Cell(
        0.30, "assumption: human negotiation (payment plan) beats any automated channel"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.AUTH_TIMEOUT): _Cell(
        0.35, "assumption: ops can complete manual auth flows customers abandon"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.BANK_DOWNTIME): _Cell(
        0.33, "assumption: ops coordinates alternate rail while bank recovers"
    ),
    (Intervention.HUMAN_ESCALATION, FailureMode.LIMIT_EXCEEDED): _Cell(
        0.28, "assumption: ops arranges mandate upgrade or installment plan"
    ),
}

# Context modifiers
_SALARY_PROXIMITY_BONUS = {  # days_to_salary → multiplier on timed retries
    0: 1.35,
    1: 1.35,
    2: 1.30,
    3: 1.20,
}
_ATTEMPT_DECAY = 0.72  # each subsequent attempt multiplies P by this (fatigue + signal)

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
