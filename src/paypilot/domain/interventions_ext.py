"""WAIT_SELF_HEAL sanity checks (DR9 — Chargebee-backed, user idea).

WAIT_SELF_HEAL lives directly on the Intervention enum (enums.py); this module
documents intent and provides a runtime consistency check for graph startup.
"""

from paypilot.domain.enums import FailureMode, Intervention


def verify_wait_self_heal_wiring() -> None:
    """Raise loudly if the enum and its compliance mapping drift out of sync."""
    if not hasattr(Intervention, "WAIT_SELF_HEAL"):
        raise RuntimeError("Intervention.WAIT_SELF_HEAL missing — enums.py out of sync")
    if Intervention.WAIT_SELF_HEAL not in FailureMode.INSUFFICIENT_FUNDS.permitted_interventions:
        raise RuntimeError(
            "WAIT_SELF_HEAL must be permitted for INSUFFICIENT_FUNDS — check enums.py"
        )
