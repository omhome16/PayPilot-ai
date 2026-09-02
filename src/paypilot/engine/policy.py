"""Policy contract: the plug-in point where a brain (naive or agentic) meets the world.

A Policy sees ONLY an EpisodeView — a read-only summary of one failure episode with the
context a merchant's system would realistically have. It proposes ONE next action (or
gives up). The engine owns everything else: gates, timing, outcomes, audit.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from paypilot.domain.enums import FailureMode, Intervention, MandateRail

if TYPE_CHECKING:
    from paypilot.domain.models import CustomerProfile


@dataclass(frozen=True)
class EpisodeView:
    """What any policy may know about one episode. No peeking at the outcome model."""

    subscription_id: str
    episode_no: int
    mode: FailureMode
    amount_paise: int
    first_failed_at: datetime  # UTC
    attempts_made: int  # failed attempts so far (the opening failure = 1)
    rail: MandateRail
    billing_day: int  # day-of-month the debit normally fires
    vertical: str
    profile: "CustomerProfile | None" = None  # P4.5: past-behavior memory (both arms see it)


@dataclass(frozen=True)
class ProposedAction:
    """A policy's wish. The engine gates it before it touches reality."""

    intervention: Intervention
    run_at: datetime  # UTC; engine clamps into legal debit hours


class Policy(Protocol):
    """Any recovery strategy implements this. That's the whole interface."""

    name: str

    def next_action(self, episode: EpisodeView) -> ProposedAction | None:
        """Next move for this episode, or None to give up (permanently)."""
        ...
