"""P7 contracts: dashboard data pipeline + monochrome-glass HTML renderer."""

import json

from paypilot.dashboard.data import build_dashboard_data
from paypilot.dashboard.render import render_html, save_dashboard


def test_dashboard_data_is_json_serializable_and_complete() -> None:
    data = build_dashboard_data(stability_seeds=[1, 2], stability_size=40, focus_seed=42)
    blob = json.dumps(data)  # must not raise
    assert '"headline"' in blob

    h = data["headline"]
    assert h["win_rate"].endswith("%")
    assert "×" in h["mean_multiplier"]
    assert h["violations"] == "0"

    assert len(data["worlds"]) == 2
    w = data["worlds"][0]
    assert {"seed", "baseline_rupees", "agent_rupees", "multiplier"} <= set(w)

    mix = data["focus"]["action_mix"]
    assert sum(mix.values()) > 0
    assert set(mix) <= {
        "retry",
        "smart_retry",
        "payment_link",
        "rail_switch",
        "voice_nudge",
        "human_escalation",
        "wait_self_heal",
    }

    assert len(data["focus"]["episode_timelines"]) >= 3
    tl = data["focus"]["episode_timelines"][0]
    assert {"title", "subtitle", "entries"} <= set(tl)


def test_render_html_contains_glass_shell_and_data() -> None:
    data = build_dashboard_data(stability_seeds=[1], stability_size=30, focus_seed=42)
    html = render_html(data)
    assert "PayPilot.AI" in html
    assert "backdrop-filter" in html  # glassmorphism present
    assert "rgba(255, 255, 255" in html  # white-on-black glass fill
    assert data["headline"]["win_rate"] in html  # real number baked in


def test_save_dashboard_writes_file(tmp_path) -> None:
    data = build_dashboard_data(stability_seeds=[1], stability_size=30, focus_seed=42)
    out = tmp_path / "dashboard.html"
    save_dashboard(data, out)
    assert out.exists() and "PayPilot" in out.read_text(encoding="utf-8")
