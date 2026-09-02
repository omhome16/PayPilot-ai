"""Live LLM-brain eval harness: three arms per world, journaled LLM decisions.

The harness must refuse to fake LLM numbers (no key → error) and must work with
any Brain — a FakeBrain stands in for the network in tests.
"""

import json

import pytest

from paypilot.domain.enums import Intervention
from paypilot.eval.live_eval import (
    LiveWorldOutcome,
    build_live_brain,
    render_live_markdown,
    run_live_brain_eval,
)
from paypilot.graph.brain import FakeBrain
from paypilot.settings import Settings


def _settings() -> Settings:
    return Settings(
        rzp_key_id="rzp_test_key",
        rzp_key_secret="rzp_test_secret",  # noqa: S106 — fixture value
        rzp_webhook_secret="whsec_test",  # noqa: S106 — fixture value
    )


def test_build_live_brain_returns_none_without_key() -> None:
    assert build_live_brain(_settings()) is None


def test_live_eval_refuses_to_fake_llm_numbers() -> None:
    with pytest.raises(RuntimeError, match="refuses to fake"):
        run_live_brain_eval(seeds=[1], brain=None, journal_dir=None, settings=_settings())


def test_live_eval_runs_three_arms_and_journals(tmp_path) -> None:
    """Baseline + scripted doctrine + (here) a scripted stand-in for the LLM brain."""
    brain = FakeBrain(default_action=Intervention.SMART_RETRY, on_salary_day=True)
    outcomes = run_live_brain_eval(seeds=[1, 2], size=60, brain=brain, journal_dir=tmp_path)
    assert len(outcomes) == 2
    o = outcomes[0]
    assert o.baseline_paise >= 0 and o.scripted_paise >= 0 and o.llm_paise >= 0
    assert o.llm_consults >= 1
    journal = json.loads((tmp_path / "live-seed-1.json").read_text(encoding="utf-8"))
    assert journal["version"] == 1 and journal["decisions"]


def test_render_live_markdown_reports_headlines() -> None:
    outcomes = [
        LiveWorldOutcome(
            seed=1,
            baseline_paise=10_000,
            scripted_paise=20_000,
            llm_paise=30_000,
            llm_consults=12,
            llm_overrides=1,
        )
    ]
    text = render_live_markdown(outcomes, tokens_used=4_321, model="z-ai/glm-4.6")
    assert "1/1 worlds won" in text
    assert "3.00×" in text
    assert "4,321 tokens" in text
    assert "| 1 | 100 | 200 | 300 | 12 | 1 |" in text
