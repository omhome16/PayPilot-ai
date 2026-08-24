"""Domain enums. The failure taxonomy CARRIES BEHAVIOR: each mode declares which
interventions are permissible. This is compliance encoded at the type level — the agent
later cannot select an intervention a failure mode forbids, by construction."""

from enum import StrEnum


class MandateRail(StrEnum):
    """How the recurring payment is authorized. India-specific mix."""

    UPI_AUTOPAY = "upi_autopay"
    ENACH = "enach"
    CARD = "card"


class MandateStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Intervention(StrEnum):
    """Every recovery action PayPilot can take. All money-touching actions are gated."""

    RETRY = "retry"  # plain re-attempt, same rail
    SMART_RETRY = "smart_retry"  # re-attempt at a chosen better time
    PAYMENT_LINK = "payment_link"  # one-click link for a lapsed mandate
    RAIL_SWITCH = "rail_switch"  # move mandate to another rail
    VOICE_NUDGE = "voice_nudge"  # Hinglish voice call
    HUMAN_ESCALATION = "human_escalation"  # hand off to merchant ops
    WAIT_SELF_HEAL = "wait_self_heal"  # strategic patience: schedule ONE later check-in (DR9)


class FailureMode(StrEnum):
    """Why an auto-debit failed. Taxonomy aligned with RBI/NPCI failure reporting.

    ``permitted_interventions`` is the compliance boundary: e.g. a revoked mandate must
    NEVER be retried (the customer withdrew consent) — only consent-based channels apply.
    """

    INSUFFICIENT_FUNDS = "insufficient_funds"
    AUTH_TIMEOUT = "auth_timeout"
    MANDATE_REVOKED = "mandate_revoked"
    BANK_DOWNTIME = "bank_downtime"
    LIMIT_EXCEEDED = "limit_exceeded"

    @property
    def permitted_interventions(self) -> frozenset[Intervention]:
        match self:
            case FailureMode.INSUFFICIENT_FUNDS:
                return frozenset(
                    {
                        Intervention.SMART_RETRY,
                        Intervention.PAYMENT_LINK,
                        Intervention.VOICE_NUDGE,
                        Intervention.HUMAN_ESCALATION,
                        Intervention.WAIT_SELF_HEAL,  # DR9: patience for cash-crunch mode
                    }
                )
            case FailureMode.AUTH_TIMEOUT | FailureMode.BANK_DOWNTIME:
                # transient infrastructure issues — plain retries are fine
                return frozenset(
                    {
                        Intervention.RETRY,
                        Intervention.SMART_RETRY,
                        Intervention.HUMAN_ESCALATION,
                    }
                )
            case FailureMode.MANDATE_REVOKED:
                # consent withdrawn: retrying would violate it. Win-back channels only.
                return frozenset(
                    {
                        Intervention.PAYMENT_LINK,
                        Intervention.VOICE_NUDGE,
                        Intervention.HUMAN_ESCALATION,
                    }
                )
            case FailureMode.LIMIT_EXCEEDED:
                # amount exceeds mandate/e-mandate cap — same-amount retry cannot help;
                # re-authorize on a higher-cap rail or use non-mandate channels
                return frozenset(
                    {
                        Intervention.PAYMENT_LINK,
                        Intervention.RAIL_SWITCH,
                        Intervention.HUMAN_ESCALATION,
                    }
                )
