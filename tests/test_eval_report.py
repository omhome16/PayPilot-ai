"""P6 contracts: the eval report turns raw seed outcomes into an honest document."""

import statistics

from paypilot.eval.multiseed import SeedOutcome
from paypilot.eval.report import build_report, render_markdown, save_report


def _worlds() -> list[SeedOutcome]:
    return [
        SeedOutcome(1, 40, 100_000, 10_000, 30_000, 5, 12, 0, 0),
        SeedOutcome(2, 38, 95_000, 8_000, 25_000, 4, 11, 0, 0),
        SeedOutcome(3, 44, 120_000, 15_000, 45_000, 7, 15, 0, 0),
        SeedOutcome(4, 41, 110_000, 9_000, 27_000, 6, 13, 0, 0),
    ]


def test_build_report_computes_correct_statistics() -> None:
    rep = build_report(_worlds(), llm_tokens_used=200_000)
    assert rep.worlds == 4
    assert rep.win_rate == 1.0
    assert rep.mean_agent_share == pytest_approx(
        statistics.mean([0.30, 25_000 / 95_000, 0.375, 27_000 / 110_000])
    )
    assert rep.total_violations == 0
    # multiplier per world: 3.0, 3.125, 3.0, 3.0 → mean 3.03…
    assert 3.0 <= rep.mean_multiplier <= 3.13


def test_roi_line_converts_intelligence_cost_honestly() -> None:
    rep = build_report(
        _worlds(),
        llm_tokens_used=200_000,
        usd_per_m_tokens=0.20,
        usd_inr=83.0,
    )
    # 0.2M tokens × $0.20/M = $0.04 → ₹3.32
    assert rep.intelligence_cost_inr == pytest_approx(0.04 * 83.0)
    assert "intelligence" in rep.roi_line.lower()
    assert "₹" in rep.roi_line


def test_render_markdown_contains_the_story() -> None:
    md = render_markdown(build_report(_worlds(), llm_tokens_used=100_000))
    assert "# PayPilot Evaluation Report" in md
    assert "win-rate" in md.lower()
    assert "zero compliance violations" in md.lower()
    assert "| world |" in md  # a results table exists
    assert "caveat" in md.lower()  # honesty section present


def test_save_report_writes_file(tmp_path) -> None:
    out = tmp_path / "EVAL_REPORT.md"
    save_report(build_report(_worlds()), out)
    assert out.exists() and out.read_text(encoding="utf-8").startswith("# PayPilot")


def pytest_approx(x: float, tol: float = 1e-6):
    class _A:
        def __eq__(self, other: object) -> bool:
            return abs(float(other) - x) <= tol  # type: ignore[arg-type]

    return _A()


def test_end_to_end_small_run_produces_report(tmp_path) -> None:
    from paypilot.eval.report import generate_report

    out = tmp_path / "report.md"
    rep = generate_report(seeds=[21, 22], size=60, out_path=out)
    assert rep.worlds == 2
    assert out.exists()
