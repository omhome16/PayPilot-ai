"""Indian payment calendar: payday crunch windows, festivals, weekends — all deterministic."""

from datetime import date

from paypilot.domain.calendar import IndianPaymentCalendar


def _cal() -> IndianPaymentCalendar:
    return IndianPaymentCalendar.with_default_festivals(year=2026)


def test_payday_crunch_covers_25th_to_3rd() -> None:
    cal = _cal()
    assert cal.in_payday_crunch(date(2026, 9, 28))
    assert cal.in_payday_crunch(date(2026, 9, 1))
    assert not cal.in_payday_crunch(date(2026, 9, 15))


def test_festival_window_detection() -> None:
    cal = _cal()
    diwali = cal.festival_on_or_near(date(2026, 11, 8))
    assert diwali is not None and diwali.name == "Diwali"
    assert cal.festival_on_or_near(date(2026, 7, 15)) is None


def test_weekend_detection() -> None:
    cal = _cal()
    assert cal.is_weekend(date(2026, 9, 5))  # a Saturday
    assert not cal.is_weekend(date(2026, 9, 7))  # a Monday


def test_next_salary_date_from_crunch() -> None:
    cal = _cal()
    assert cal.next_salary_date(date(2026, 9, 28)) == date(2026, 10, 1)
    assert cal.next_salary_date(date(2026, 9, 2)) == date(2026, 10, 1)


def test_calendar_is_pure_and_deterministic() -> None:
    a, b = _cal(), _cal()
    for day in (date(2026, 9, 28), date(2026, 11, 8), date(2026, 12, 25)):
        assert a.in_payday_crunch(day) == b.in_payday_crunch(day)
        assert a.festival_on_or_near(day) == b.festival_on_or_near(day)
