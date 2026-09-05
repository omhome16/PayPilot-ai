"""Core domain models. Money is ALWAYS integer paise internally — the #1 Indian-fintech
bug source is rupee/paise confusion, so the boundary is typed and one-directional."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from paypilot.domain.contacts import ContactError, normalize_indian_contact
from paypilot.domain.enums import FailureMode, MandateRail, MandateStatus


class _Strict(BaseModel):
    """All domain models are frozen and reject unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Subscription(_Strict):
    id: str
    customer_id: str
    plan_name: str
    amount_paise: int  # ₹199.00 == 19_900
    cycle: str = "monthly"
    billing_day: int = Field(default=1, ge=1, le=28)  # day of month the debit fires

    @field_validator("amount_paise")
    @classmethod
    def _amount_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("amount must be positive paise (₹1 = 100 paise)")
        return v

    @property
    def amount_rupees(self) -> Decimal:
        return Decimal(self.amount_paise) / 100


class Customer(_Strict):
    id: str
    name: str
    contact: str  # normalized +91XXXXXXXXXX
    vertical: str  # ott | gym | saas

    @field_validator("contact")
    @classmethod
    def _valid_contact(cls, v: str) -> str:
        try:
            return normalize_indian_contact(v)
        except ContactError as e:
            raise ValueError(f"invalid contact: {e}") from e


class Mandate(_Strict):
    id: str
    customer_id: str
    rail: MandateRail
    status: MandateStatus = MandateStatus.ACTIVE
    max_amount_paise: int = Field(gt=0)
    created_on: date

    @property
    def can_be_charged(self) -> bool:
        return self.status is MandateStatus.ACTIVE

    def revoke(self) -> "Mandate":
        if self.status in (MandateStatus.REVOKED, MandateStatus.EXPIRED):
            raise ValueError(f"cannot revoke a mandate already {self.status.value.upper()}")
        return self.model_copy(update={"status": MandateStatus.REVOKED})

    def pause(self) -> "Mandate":
        if self.status is not MandateStatus.ACTIVE:
            raise ValueError("only ACTIVE mandates can pause")
        return self.model_copy(update={"status": MandateStatus.PAUSED})

    def resume(self) -> "Mandate":
        if self.status is not MandateStatus.PAUSED:
            raise ValueError("only PAUSED mandates can resume")
        return self.model_copy(update={"status": MandateStatus.ACTIVE})


class FailureEvent(_Strict):
    """Webhook-shaped record of one failed collection attempt."""

    id: str
    subscription_id: str
    mode: FailureMode
    occurred_at: datetime
    attempt_no: int = Field(ge=1)
    amount_paise: int = Field(gt=0)
    episode_no: int = Field(default=1, ge=1)  # logical incident grouping (simulator-assigned)


class CustomerProfile(_Strict):
    """P4.5 memory: past-behavior summary a recovery brain may consult.

    Generated FROM hidden traits at population-build time, so history is
    informative about the customer but can never leak future outcomes.
    """

    customer_id: str
    tenure_cycles: int = Field(ge=1, le=36)  # months as a subscriber
    paid_on_time: int = Field(ge=0)
    missed_cycles: int = Field(ge=0)  # prior failed months (before this window)
    reliability: float = Field(ge=0.0, le=1.0)  # hidden trait the history was drawn from
    link_affinity: float = Field(ge=0.0, le=1.0)  # responds to payment links (vs calls)

    @property
    def on_time_ratio(self) -> float:
        return self.paid_on_time / self.tenure_cycles

    @property
    def history_tone(self) -> str:
        """Bucket label for the on-time ratio (shared by every SENSE layer)."""
        ratio = round(self.on_time_ratio, 2)
        if ratio >= 0.8:
            return "reliable payer"
        if ratio >= 0.5:
            return "mixed record"
        return "history of misses"

    def summary(self) -> tuple[str, dict[str, int | float]]:
        """(history_note, history dict) for a customer's profile — one-line
        narrative plus the JSON-safe numbers every SENSE node attaches to its
        story. Single source of truth so LLM states never drift between layers."""
        ratio = round(self.on_time_ratio, 2)
        note = (
            f"history: {self.history_tone} ({int(self.paid_on_time)}/{self.tenure_cycles} on time)"
        )
        history = {
            "tenure_cycles": self.tenure_cycles,
            "on_time_ratio": ratio,
            "missed_cycles": self.missed_cycles,
            "link_affinity": round(self.link_affinity, 2),
        }
        return note, history
