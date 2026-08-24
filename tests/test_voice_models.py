"""Voice models: a VoiceCall artifact is compliance-checked BEFORE it can exist."""

import datetime as dt

import pytest

from paypilot.voice.models import VoiceCall


def test_valid_call_constructs_and_estimates_duration() -> None:
    script = "Namaste Priya! " * 40  # 80 words → ~32s at 2.5 wps
    call = VoiceCall(
        episode_key="sub_0001:1",
        merchant_name="FitZone",
        customer_name="Priya",
        script_hinglish=script,
        audio_path=None,
        created_at=dt.datetime(2026, 10, 1, 4, 30, tzinfo=dt.UTC),
    )
    assert call.word_count == 80
    assert call.estimated_duration_seconds == pytest.approx(80 / 2.5)


def test_empty_script_rejected() -> None:
    with pytest.raises(ValueError):
        VoiceCall(
            episode_key="e",
            merchant_name="m",
            customer_name="c",
            script_hinglish="   ",
            audio_path=None,
            created_at=dt.datetime.now(dt.UTC),
        )


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError):
        VoiceCall(
            episode_key="e",
            merchant_name="m",
            customer_name="c",
            script_hinglish="hello there friend",
            audio_path=None,
            created_at=dt.datetime(2026, 10, 1, 4, 30),  # no tzinfo
        )
