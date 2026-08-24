"""Script safety: the voice channel obeys the same rails as everything else.

Required elements: identifies merchant, states the amount, offers an opt-out.
Hard blocklist: threats/legal intimidation, abusive language, spam shouting.
"""

import pytest

from paypilot.voice.safety import validate_script

_GOOD = (
    "Namaste Priya, main FitZone ki taraf se bol rahi hoon. "
    "Aapka ₹1,499 ka payment pending hai, kripya jald clear kar dijiye. "
    "Agar aap ye subscription aage nahi chalana chahte toh humein bata dijiye, "
    "hum ise rok denge ya pause kar denge. Dhanyavaad!"
)


def test_good_script_passes_with_no_violations() -> None:
    report = validate_script(_GOOD, merchant_name="FitZone")
    assert report.ok is True
    assert report.violations == []


def test_missing_optout_fails() -> None:
    bad = (
        "Namaste Priya, FitZone se bol rahi hoon. Aapka ₹1,499 ka payment pending hai. "
        "Kripya turant pay kijiye. Dhanyavaad!"
    )
    report = validate_script(bad, merchant_name="FitZone")
    assert report.ok is False
    assert any("opt-out" in v for v in report.violations)


def test_wrong_merchant_name_fails() -> None:
    report = validate_script(_GOOD.replace("FitZone", "SomeOtherGym"), merchant_name="FitZone")
    assert report.ok is False
    assert any("merchant" in v.lower() for v in report.violations)


@pytest.mark.parametrize(
    "bad_fragment",
    [
        "legal action will be taken",
        "hum police ko report karenge",
        "TERA SARA PAISA DO ABHI ABHI ABHI!!!",
    ],
)
def test_blocklist_and_shouting_fail(bad_fragment: str) -> None:
    base = (
        "Namaste, {m} se baat kar raha hoon. Aapka ₹500 payment pending hai. {frag} "
        "Agar aap subscription nahi chahate toh bata dijiye, hum rok denge."
    )
    report = validate_script(base.format(m="FitZone", frag=bad_fragment), merchant_name="FitZone")
    assert report.ok is False


def test_amount_mention_required() -> None:
    no_amount = (
        "Namaste Priya, FitZone se bol rahi hoon. Aapka payment pending hai. "
        "Agar aap ye subscription nahi chalana chahte toh bata dijiye, hum rok denge."
    )
    report = validate_script(no_amount, merchant_name="FitZone")
    assert report.ok is False
    assert any("amount" in v.lower() for v in report.violations)
