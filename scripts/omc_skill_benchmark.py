#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


NEXT_ACTION_LINE = re.compile(r"다음 액션:\s*(.+)")
SKILL_ACTION = re.compile(r"\$omc-[a-z-]+")


def _count_question_marks(text: str) -> int:
    return text.count("?")


def _extract_next_action_line(text: str) -> str:
    for line in text.splitlines():
        match = NEXT_ACTION_LINE.search(line)
        if match:
            return match.group(1).strip()
    return ""


def _extract_next_action_candidates(text: str) -> list[str]:
    line = _extract_next_action_line(text)
    if not line:
        return []

    actions = SKILL_ACTION.findall(line)
    if "사용자 선택 대기" in line:
        actions.append("사용자 선택 대기")
    return actions


def _missing_markers(text: str, markers: list[str]) -> list[str]:
    return [marker for marker in markers if marker not in text]


def _score_case(metrics: dict[str, object]) -> dict[str, object]:
    percent = 100

    if not metrics["next_action_single"]:
        percent -= 30
    if metrics["expected_next_action_hit"] is False:
        percent -= 25

    percent -= min(int(metrics["question_count"]) * 10, 20)
    percent -= min(int(metrics["missing_markers_count"]) * 15, 45)
    percent = max(percent, 0)

    return {
        "percent": percent,
        "verdict": "good" if percent >= 85 else ("mixed" if percent >= 60 else "weak"),
    }


def evaluate_case(case: dict[str, object]) -> dict[str, object]:
    response = str(case.get("response", ""))
    expected_next_actions = [str(item) for item in case.get("expected_next_actions", [])]
    required_markers = [str(item) for item in case.get("required_markers", [])]
    next_actions = _extract_next_action_candidates(response)
    missing_markers = _missing_markers(response, required_markers)

    expected_hit: bool | None
    if expected_next_actions:
        expected_hit = len(next_actions) == 1 and any(
            action in next_actions for action in expected_next_actions
        )
    else:
        expected_hit = None

    metrics = {
        "output_chars": len(response),
        "next_action_count": len(next_actions),
        "next_action_single": len(next_actions) == 1,
        "next_actions": next_actions,
        "expected_next_action_hit": expected_hit,
        "question_count": _count_question_marks(response),
        "missing_markers_count": len(missing_markers),
        "missing_markers": missing_markers,
    }

    return {
        "skill": str(case.get("skill", "")),
        "request": str(case.get("request", "")),
        "metrics": metrics,
        "score": _score_case(metrics),
    }


def build_report(cases: list[dict[str, object]]) -> dict[str, object]:
    scored_cases = [evaluate_case(case) for case in cases]
    case_count = len(scored_cases)
    if case_count == 0:
        return {
            "cases": [],
            "summary": {
                "case_count": 0,
                "avg_output_chars": 0,
                "next_action_single_rate": 0,
                "expected_next_action_hit_rate": 0,
                "avg_question_count": 0,
                "avg_missing_markers_count": 0,
                "avg_score_percent": 0,
            },
        }

    next_action_single_hits = sum(1 for item in scored_cases if item["metrics"]["next_action_single"])
    expected_hits = [
        item["metrics"]["expected_next_action_hit"]
        for item in scored_cases
        if item["metrics"]["expected_next_action_hit"] is not None
    ]

    summary = {
        "case_count": case_count,
        "avg_output_chars": sum(item["metrics"]["output_chars"] for item in scored_cases) / case_count,
        "next_action_single_rate": next_action_single_hits / case_count,
        "expected_next_action_hit_rate": (
            sum(1 for hit in expected_hits if hit) / len(expected_hits) if expected_hits else 0
        ),
        "avg_question_count": sum(item["metrics"]["question_count"] for item in scored_cases) / case_count,
        "avg_missing_markers_count": (
            sum(item["metrics"]["missing_markers_count"] for item in scored_cases) / case_count
        ),
        "avg_score_percent": sum(item["score"]["percent"] for item in scored_cases) / case_count,
    }
    return {"cases": scored_cases, "summary": summary}


def _load_cases(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data["cases"]
    raise ValueError("input JSON must be a list or an object with a 'cases' list")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score OMC skill outputs with compact benchmark metrics.")
    sub = parser.add_subparsers(dest="command", required=True)

    score = sub.add_parser("score", help="Read cases JSON and output score report.")
    score.add_argument("--input", type=Path, required=True, help="Input JSON path")
    score.add_argument("--format", choices=["json"], default="json", help="Output format")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "score":
        cases = _load_cases(args.input)
        report = build_report(cases)
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
