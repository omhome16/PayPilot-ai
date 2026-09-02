"""Brain interface: anything that can propose a recovery move for one decision point.

The LLM (OpenRouter via LangGraph node) implements this in LIVE mode; FakeBrain
implements it for tests and record/replay. The graph never knows which is which.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from paypilot.domain.enums import Intervention


@dataclass(frozen=True)
class BrainProposal:
    """What the brain wants to do. The graph validates before anything executes."""

    action: Intervention
    days_ahead: int = 0  # schedule offset from 'now' (ignored if on_salary_day)
    on_salary_day: bool = False  # time the action to the customer's next salary date
    reason: str = ""  # free-text justification (LLM narration lives here)
    raw: dict[str, Any] = field(default_factory=dict)  # untouched model output (audit)
    abstain: bool = False  # replayed 'do nothing' consult (replay.py); never from a live brain

    @property
    def intervention(self) -> Intervention:
        """Alias so guardrail/engine code reads naturally."""
        return self.action

    @property
    def timing(self) -> str:
        return "salary_day" if self.on_salary_day else f"+{self.days_ahead}d"


@dataclass(frozen=True)
class BrainState:
    """Compact, serializable context handed to the brain at each THINK call."""

    mode: str
    amount_rupees: float
    attempts: int  # failed attempts so far (opening failure = 1)
    touches_used: int  # recovery contacts made so far
    wait_already_used: bool
    rail: str
    vertical: str
    days_to_salary: int | None
    history_note: str = ""  # P4.5 fills this with customer-profile summary


class Brain(Protocol):
    def propose(self, state: dict[str, Any]) -> BrainProposal: ...


class FakeBrain:
    """Scripted brain: fixed default, an explicit sequence, or a state→proposal fn.

    Deterministic, zero-network — the CI workhorse. Also used in replay mode.
    """

    def __init__(
        self,
        default_action: Intervention = Intervention.HUMAN_ESCALATION,
        on_salary_day: bool = False,
        sequence: list[BrainProposal] | None = None,
        fn: Callable[[dict[str, Any]], BrainProposal] | None = None,
    ) -> None:
        self._default_action = default_action
        self._on_salary_day = on_salary_day
        self._sequence = list(sequence) if sequence else []
        self._fn = fn
        self.calls = 0

    @classmethod
    def sequence(cls, steps: list[BrainProposal]) -> "FakeBrain":
        """Scripted brain: returns proposals in order, holding the last one forever."""
        return cls(sequence=steps)

    def propose(self, state: dict[str, Any]) -> BrainProposal:
        self.calls += 1
        if self._fn is not None:
            return self._fn(state)
        if self._sequence:
            if self.calls <= len(self._sequence):
                return self._sequence[self.calls - 1]
            # exhausted → safe conservative default
            return BrainProposal(action=self._default_action, reason="scripted sequence exhausted")
        return BrainProposal(action=self._default_action, on_salary_day=self._on_salary_day)
