from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "omc_skill_benchmark.py"


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
