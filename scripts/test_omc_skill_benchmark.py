from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "omc_skill_benchmark.py"
FIXTURE_PATH = ROOT / "scripts" / "fixtures" / "omc_skill_benchmark_cases.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("omc_skill_benchmark", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_evaluate_case_scores_core_metrics():
    mod = _load_module()

    case = {
        "skill": "omc-plan",
        "request": "스킬 품질 추가 개선 가치의 토큰 효율 벤치마크 설계",
        "response": (
            "CONTRACT\n"
            "권장 옵션: B\n"
            "다음 액션: $omc-task\n"
            "사용자 확인: 진행할까요?\n"
        ),
        "expected_next_actions": ["$omc-task"],
        "required_markers": ["CONTRACT", "다음 액션", "DoD"],
    }

    scored = mod.evaluate_case(case)

    assert scored["skill"] == "omc-plan"
    assert scored["metrics"]["next_action_count"] == 1
    assert scored["metrics"]["next_action_single"] is True
    assert scored["metrics"]["expected_next_action_hit"] is True
    assert scored["metrics"]["question_count"] == 1
    assert scored["metrics"]["missing_markers_count"] == 1
    assert scored["metrics"]["output_chars"] == len(case["response"])
    assert scored["score"]["percent"] < 100


def test_build_report_aggregates_case_scores():
    mod = _load_module()

    cases = [
        {
            "skill": "omc-plan",
            "request": "계획해줘",
            "response": "CONTRACT\n다음 액션: $omc-task\n",
            "expected_next_actions": ["$omc-task"],
            "required_markers": ["CONTRACT", "다음 액션"],
        },
        {
            "skill": "omc-review",
            "request": "리뷰해줘",
            "response": "판정: APPROVE\n다음 액션: $omc-ship 또는 $omc-task\n",
            "expected_next_actions": ["$omc-ship"],
            "required_markers": ["판정", "다음 액션"],
        },
    ]

    report = mod.build_report(cases)

    assert report["summary"]["case_count"] == 2
    assert report["summary"]["next_action_single_rate"] == 0.5
    assert report["summary"]["expected_next_action_hit_rate"] == 0.5
    assert report["summary"]["avg_missing_markers_count"] == 0
    assert report["summary"]["avg_output_chars"] > 0


def test_build_report_keeps_summary_schema_for_empty_cases():
    mod = _load_module()

    report = mod.build_report([])

    assert report == {
        "cases": [],
        "summary": {
            "case_count": 0,
            "avg_output_chars": 0,
            "next_action_single_rate": 0,
            "expected_next_action_hit_rate": 0,
            "avg_question_count": 0,
            "avg_missing_markers_count": 0,
            "avg_score_percent": 0,
            "source_type_counts": {},
        },
    }


def test_next_action_parsing_ignores_skill_mentions_outside_next_action_line():
    mod = _load_module()

    case = {
        "skill": "omc-review",
        "request": "리뷰해줘",
        "response": (
            "설명: 필요하면 $omc-task 로 다시 갈 수 있음\n"
            "판정: APPROVE\n"
            "다음 액션: $omc-ship\n"
        ),
        "expected_next_actions": ["$omc-ship"],
        "required_markers": ["판정", "다음 액션"],
    }

    scored = mod.evaluate_case(case)

    assert scored["metrics"]["next_action_count"] == 1
    assert scored["metrics"]["next_action_single"] is True
    assert scored["metrics"]["expected_next_action_hit"] is True


def test_score_cli_outputs_json(tmp_path: Path):
    cases = [
        {
            "skill": "omc-ship",
            "request": "ship 준비",
            "response": "결론: SHIP READY\n다음 액션: 사용자 선택 대기\n",
            "expected_next_actions": ["사용자 선택 대기"],
            "required_markers": ["결론", "다음 액션"],
        }
    ]
    input_file = tmp_path / "cases.json"
    input_file.write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "score", "--input", str(input_file), "--format", "json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["case_count"] == 1
    assert payload["cases"][0]["skill"] == "omc-ship"
    assert payload["cases"][0]["metrics"]["expected_next_action_hit"] is True


def test_load_cases_normalizes_optional_fields_and_missing_text_fields(tmp_path: Path):
    mod = _load_module()

    input_file = tmp_path / "cases.json"
    input_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "response": "다음 액션: $omc-plan\n",
                        "expected_next_actions": None,
                        "required_markers": None,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    cases = mod._load_cases(input_file)

    assert cases == [
        {
            "response": "다음 액션: $omc-plan\n",
            "expected_next_actions": [],
            "required_markers": [],
        }
    ]


def test_load_cases_rejects_invalid_case_shapes(tmp_path: Path):
    mod = _load_module()

    invalid_cases = [
        [{"skill": "omc-plan"}],
        [{"response": "", "expected_next_actions": [], "required_markers": []}],
        [{"response": "ok", "expected_next_actions": "bad", "required_markers": []}],
        [{"response": "ok", "expected_next_actions": [], "required_markers": [1]}],
        [{"response": "ok", "expected_next_actions": [], "required_markers": [], "source_type": "unknown"}],
        [{"response": "ok", "expected_next_actions": [], "required_markers": [], "source_type": "observed_output"}],
        [{"response": "ok", "expected_next_actions": [], "required_markers": [], "source_type": "observed_output", "evidence": 1}],
    ]

    for index, payload in enumerate(invalid_cases):
        input_file = tmp_path / f"invalid-{index}.json"
        input_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            mod._load_cases(input_file)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid payload should fail: {payload}")


def test_fixture_cases_include_reentry_scenario():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload

    reentry_cases = [case for case in cases if case.get("skill") == "omc-reentry"]
    assert len(reentry_cases) == 3, "fixture must include exactly three omc-reentry cases"
    observed_cases = [case for case in cases if case.get("source_type") == "observed_output"]
    assert observed_cases, "fixture must include at least one observed_output case"

    case_map = {case["request"]: case for case in reentry_cases}
    good_case = case_map["이 프로젝트 뭐였지"]
    multi_action_case = case_map["복귀 요약 후 다음에 뭘 해야 할지 추천해줘"]
    missing_marker_case = case_map["구조만 빠르게 알려줘"]

    assert good_case["expected_next_actions"] == ["$omc-plan"]
    assert good_case["source_type"] == "synthetic"
    assert "프로젝트 한 줄 요약" in good_case["required_markers"]
    assert "다음 읽을 파일 3개" in good_case["required_markers"]
    assert multi_action_case["expected_next_actions"] == ["$omc-plan"]
    assert "추천 다음 스킬" in multi_action_case["required_markers"]
    assert "다음 읽을 파일 3개" in missing_marker_case["required_markers"]

    observed_case = observed_cases[0]
    assert observed_case["skill"] == "omc-plan"
    assert observed_case["expected_next_actions"] == ["$omc-task"]
    assert observed_case.get("evidence"), "observed_output case must explain evidence source"


def test_score_cli_outputs_summary_for_fixture_cases():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "score", "--input", str(FIXTURE_PATH), "--format", "json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["case_count"] == 4
    assert payload["summary"]["next_action_single_rate"] == 3 / 4
    assert payload["summary"]["expected_next_action_hit_rate"] == 2 / 4
    assert payload["summary"]["avg_missing_markers_count"] == 1 / 4
    assert payload["summary"]["source_type_counts"] == {
        "observed_output": 1,
        "synthetic": 3,
    }
    reentry_cases = [case for case in payload["cases"] if case["skill"] == "omc-reentry"]
    assert len(reentry_cases) == 3, "score output must include all omc-reentry fixture cases"

    case_map = {case["request"]: case for case in reentry_cases}
    assert case_map["이 프로젝트 뭐였지"]["score"]["verdict"] == "good"
    assert case_map["복귀 요약 후 다음에 뭘 해야 할지 추천해줘"]["metrics"]["next_action_single"] is False
    assert case_map["구조만 빠르게 알려줘"]["metrics"]["missing_markers_count"] == 1
    observed_case = next(case for case in payload["cases"] if case.get("source_type") == "observed_output")
    assert observed_case["skill"] == "omc-plan"
    assert observed_case["metrics"]["expected_next_action_hit"] is True
