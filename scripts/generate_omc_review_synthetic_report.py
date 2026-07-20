"""Render the checked-in synthetic Codex/OMC review comparison report."""
from __future__ import annotations

import argparse
from pathlib import Path

from omc_review_compare import build_report, load_cases


def render_report(cases: list[dict]) -> str:
    report = build_report(cases)
    case_count = report["case_count"]
    summaries = report["providers"]

    def row(provider: str, label: str) -> str:
        summary = summaries[provider]
        return (
            f"| {label} | {summary['completed_count']} | {summary['hit_count']} | "
            f"{summary['miss_count']} | {summary['false_positive_count']} | "
            f"{summary['evidence_match_count']} | `{_provenance(provider)}` |"
        )

    return "\n".join(
        [
            "# OMC Review Synthetic Comparison",
            "",
            "## Scope",
            "",
            "- Source: `synthetic`",
            "- Comparison scope: `same_diff`",
            f"- Cases: {case_count}",
            "- Providers: Codex CLI and OMC review",
            "- Purpose: validate the comparison pipeline, not prove production superiority.",
            "- Metrics source: `omc_review_synthetic_runtime_cases.json`",
            "- Codex output: complete CLI output with temporary-repository paths redacted to `src/...`.",
            "- OMC output: complete manually recorded output, with reviewer basis metadata attached.",
            "",
            "## Results",
            "",
            "| Provider | Completed | Critical-or-higher hits | Misses | False positives | Evidence matches | Provenance |",
            "|---|---:|---:|---:|---:|---:|---|",
            row("codex", "Codex"),
            row("omc-review", "OMC review"),
            "",
            "## Interpretation",
            "",
            f"Both providers found the expected defect in all {case_count} controlled cases. The result is a tie on this synthetic set. Codex output is preserved after path redaction; OMC output was recorded by applying the OMC review checklist manually, so it is not an independent OMC model-executor result.",
            "",
            "This report must not be used to claim that OMC review replaces Codex review. That requires at least 10 real anonymized observed diffs with independently preserved outputs and adjudicated gold findings.",
            "",
        ]
    )


def _provenance(provider: str) -> str:
    return "cli_completed" if provider == "codex" else "manual_rule_application"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        render_report(load_cases(args.cases)),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
