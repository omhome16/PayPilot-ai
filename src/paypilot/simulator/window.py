"""Simulation time windows."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class WindowSpec:
    """Inclusive date range the simulation covers."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("window end before start")
