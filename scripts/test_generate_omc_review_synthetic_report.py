from pathlib import Path

from generate_omc_review_synthetic_report import render_report
from omc_review_compare import load_cases


ROOT = Path(__file__).resolve().parents[1]


def test_render_report_uses_fixture_metrics():
    cases = load_cases(ROOT / "scripts/fixtures/omc_review_synthetic_runtime_cases.json")
    rendered = render_report(cases)

    assert "| Codex | 4 | 4 | 0 | 0 | 4 |" in rendered
    assert "| OMC review | 4 | 4 | 0 | 0 | 4 |" in rendered
