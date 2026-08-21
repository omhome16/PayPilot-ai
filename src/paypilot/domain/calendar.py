"""Indian payment calendar — payday crunches, festivals, weekends.

Deterministic and pure: same inputs always give same answers (tests pin this), because
the eval harness requires identical worlds across runs.
"""

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Festival:
    name: str
    month: int
    day: int
    window_days: int = 5  # spending surge spans roughly this many days around the date


# Fixed-date major festivals for the demo year. (Lunar-calendar drift noted in
# SIMULATOR_ASSUMPTIONS.md — exact dates don't matter, the *pattern* does.)
_DEFAULT_FESTIVALS_2026: tuple[Festival, ...] = (
    Festival("Lohri", 1, 13),
    Festival("Holi", 3, 4),
    Festival("Raksha Bandhan", 8, 28),
    Festival("Diwali", 11, 8, window_days=7),
    Festival("Christmas", 12, 25),
)


class IndianPaymentCalendar:
    def __init__(self, year: int, festivals: tuple[Festival, ...]) -> None:
        self.year = year
        self._festivals = festivals

    @classmethod
    def with_default_festivals(cls, year: int) -> "IndianPaymentCalendar":
        return cls(year=year, festivals=_DEFAULT_FESTIVALS_2026)

    def in_payday_crunch(self, day: dt.date) -> bool:
        """Cash-tight window: 25th of month through 3rd of next (salaries land ~1st)."""
        return day.day >= 25 or day.day <= 3

    def is_weekend(self, day: dt.date) -> bool:
        return day.weekday() >= 5

    def festival_on_or_near(self, day: dt.date) -> Festival | None:
        for f in self._festivals:
            center = dt.date(self.year, f.month, f.day)
            delta = abs((day - center).days)
            if delta <= f.window_days // 2 + f.window_days % 2:
                return f
        return None

    def next_salary_date(self, day: dt.date) -> dt.date:
        """Next 1st-of-month strictly after ``day``."""
        if day.month == 12:
            return dt.date(day.year + 1, 1, 1)
        return dt.date(day.year, day.month + 1, 1)
