"""Indian payment calendar — payday crunches, festivals, weekends.

Deterministic and pure: same inputs always give same answers (tests pin this), because
the eval harness requires identical worlds across runs.

This module is also the single home of IST↔UTC math. Every subsystem (engine,
graph, voice, simulator, store) used to carry its own private ``timedelta(hours=5,
minutes=30)`` and salary-date helper; that duplication is consolidated here so the
whole repo agrees on what "next salary date" means.
"""

import datetime as dt
from dataclasses import dataclass

IST_OFFSET = dt.timedelta(hours=5, minutes=30)  # India Standard Time is UTC+5:30


def ist_date_of(when_utc: dt.datetime) -> dt.date:
    """Calendar day in India for a UTC instant."""
    return (when_utc + IST_OFFSET).date()


def next_salary_date(ist_day: dt.date) -> dt.date:
    """Next 1st-of-month strictly after ``ist_day`` (default-festival calendar)."""
    return IndianPaymentCalendar.with_default_festivals(year=ist_day.year).next_salary_date(ist_day)


def utc_at_ist_hour(ist_day: dt.date, hour_ist: int, minute_ist: int = 30) -> dt.datetime:
    """UTC instant of ``hour_ist:minute_ist`` IST on ``ist_day`` (tz-aware)."""
    naive_ist = dt.datetime(ist_day.year, ist_day.month, ist_day.day, hour_ist, minute_ist)
    return naive_ist.replace(tzinfo=dt.UTC) - IST_OFFSET


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
