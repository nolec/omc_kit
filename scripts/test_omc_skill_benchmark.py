from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "omc_skill_benchmark.py"
FIXTURE_PATH = ROOT / "scripts" / "fixtures" / "omc_skill_benchmark_cases.json"
RESPONSE_MODE_FIXTURE_PATH = ROOT / "scripts" / "fixtures" / "omc_response_mode_cases.json"


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


def test_compare_response_modes_summarizes_candidate_gain():
    mod = _load_module()

    cases = [
        {
            "request": "로그인 버튼 컴포넌트 구현해줘",
            "expected_mode": "execute-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 답변만 제공", "user: 아니 구현해줘"],
            "candidate_trace": ["assistant: 구현 단계로 바로 진입"],
            "baseline_output_chars": 320,
            "candidate_output_chars": 350,
            "baseline_task_start_delay": 2,
            "candidate_task_start_delay": 1,
        },
        {
            "request": "이 변경 diff 리뷰해줘",
            "expected_mode": "review-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 리뷰 시작"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 280,
            "candidate_output_chars": 295,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
        },
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["case_count"] == 2
    assert report["summary"]["baseline_mode_accuracy"] == 0.5
    assert report["summary"]["candidate_mode_accuracy"] == 1.0
    assert report["summary"]["mode_accuracy_delta"] == 0.5
    assert report["summary"]["baseline_reroute_rate"] == 0.5
    assert report["summary"]["candidate_reroute_rate"] == 0.0
    assert report["summary"]["candidate_output_chars_delta"] == 45 / 2
    assert report["summary"]["candidate_task_start_delay_delta"] == -0.5
    assert report["decision"]["verdict"] == "adopt"
    assert report["cases"][0]["baseline"]["mode"] == "answer-first"
    assert report["cases"][0]["candidate"]["mode"] == "execute-first"


def test_compare_response_modes_cli_outputs_decision_json(tmp_path: Path):
    cases = {
        "cases": [
            {
                "request": "리뷰해줘",
                "expected_mode": "review-first",
                "baseline_policy": "baseline",
                "candidate_policy": "candidate",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 315,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
            }
        ]
    }
    input_file = tmp_path / "response-mode.json"
    input_file.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "compare-response-modes", "--input", str(input_file)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["case_count"] == 1
    assert payload["summary"]["candidate_mode_accuracy"] == 1.0
    assert payload["decision"]["verdict"] in {"adopt", "revise", "hold"}


def test_load_response_mode_cases_preserves_source_metadata_and_samples(tmp_path: Path):
    mod = _load_module()

    payload = {
        "cases": [
            {
                "request": "실제 리뷰 요청",
                "expected_mode": "review-first",
                "baseline_policy": "baseline",
                "candidate_policy": "candidate",
                "baseline_trace": ["assistant: 요약만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 280,
                "candidate_output_chars": 310,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "same_surface",
                "evidence": "2026-06-25 실제 세션 출력 샘플 정리",
                "baseline_response_sample": "변경 요약만 제공하고 리뷰 구조는 생략함",
                "candidate_response_sample": "리뷰 범위, 이슈, 판정까지 구조화해 제공함",
            }
        ]
    }
    input_file = tmp_path / "response-mode-observed.json"
    input_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    cases = mod._load_response_mode_cases(input_file)

    assert cases[0]["source_type"] == "observed_output"
    assert cases[0]["comparison_scope"] == "same_surface"
    assert cases[0]["evidence"] == "2026-06-25 실제 세션 출력 샘플 정리"
    assert cases[0]["baseline_response_sample"] == "변경 요약만 제공하고 리뷰 구조는 생략함"
    assert cases[0]["candidate_response_sample"] == "리뷰 범위, 이슈, 판정까지 구조화해 제공함"

    report = mod.compare_response_modes(cases)
    assert report["summary"]["source_type_counts"] == {"observed_output": 1}
    assert report["cases"][0]["source_type"] == "observed_output"
    assert report["cases"][0]["baseline"]["response_sample"] == "변경 요약만 제공하고 리뷰 구조는 생략함"
    assert report["cases"][0]["candidate"]["response_sample"] == "리뷰 범위, 이슈, 판정까지 구조화해 제공함"


def test_load_response_mode_cases_rejects_observed_output_without_response_samples(tmp_path: Path):
    mod = _load_module()

    payload = {
        "cases": [
            {
                "request": "실제 리뷰 요청",
                "expected_mode": "review-first",
                "baseline_policy": "baseline",
                "candidate_policy": "candidate",
                "baseline_trace": ["assistant: 요약만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 280,
                "candidate_output_chars": 310,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "same_surface",
                "evidence": "2026-06-25 실제 세션 출력 샘플 정리",
            }
        ]
    }
    input_file = tmp_path / "response-mode-invalid.json"
    input_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        mod._load_response_mode_cases(input_file)
    except ValueError:
        pass
    else:
        raise AssertionError("observed_output response mode case should require response samples")


def test_response_mode_fixture_covers_three_policy_modes_and_mixed_intent_examples():
    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload

    assert len(cases) >= 18, "response mode fixture should include at least 18 cases"

    expected_modes = {case["expected_mode"] for case in cases}
    assert expected_modes == {"answer-first", "execute-first", "review-first"}

    mode_counts: dict[str, int] = {}
    requests = {case["request"] for case in cases}
    observed_request_cases = []
    observed_output_cases = []
    for case in cases:
        mode = case["expected_mode"]
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        assert isinstance(case["request"], str) and case["request"].strip()
        assert "baseline_mode" not in case
        assert "candidate_mode" not in case
        assert "baseline_reroute" not in case
        assert "candidate_reroute" not in case
        assert case["baseline_policy"] == "baseline"
        assert case["candidate_policy"] == "candidate"
        assert isinstance(case["baseline_trace"], list) and case["baseline_trace"]
        assert isinstance(case["candidate_trace"], list) and case["candidate_trace"]
        assert case["source_type"] in {"synthetic", "observed_request", "observed_output"}
        if case["source_type"] != "synthetic":
            assert isinstance(case.get("evidence"), str) and case["evidence"].strip()
        if case["source_type"] == "observed_request":
            observed_request_cases.append(case)
        if case["source_type"] == "observed_output":
            observed_output_cases.append(case)
            assert case["comparison_scope"] in {"same_surface", "cross_surface"}
            assert isinstance(case.get("baseline_response_sample"), str) and case["baseline_response_sample"].strip()
            assert isinstance(case.get("candidate_response_sample"), str) and case["candidate_response_sample"].strip()

    assert all(count >= 3 for count in mode_counts.values())
    assert len(observed_request_cases) >= 3
    assert len(observed_output_cases) >= 1
    assert "이 버그 원인 먼저 보고 바로 고칠 수 있으면 수정해줘" in requests
    assert "이 변경 위험한지 먼저 리뷰해주고, 괜찮으면 그다음 커밋까지 해줘" in requests
    assert "이 기능 해야 할지 판단하고 진행 순서만 정리해줘" in requests
    assert "OMC orchestration layer 1단계와 response_mode benchmark 변경 리뷰" in requests
    assert "OMC orchestration 다음 개선 계획 수립" in requests
    assert "복귀용 프로젝트 reentry 스킬 재설계 정보원 우선순위와 출력 계약 고정" in requests


def test_load_response_mode_cases_rejects_observed_output_without_comparison_scope(tmp_path: Path):
    mod = _load_module()

    payload = {
        "cases": [
            {
                "request": "실제 리뷰 요청",
                "expected_mode": "review-first",
                "baseline_policy": "baseline",
                "candidate_policy": "candidate",
                "baseline_trace": ["assistant: 요약만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 280,
                "candidate_output_chars": 310,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "evidence": "2026-06-25 실제 세션 출력 샘플 정리",
                "baseline_response_sample": "변경 요약만 제공하고 리뷰 구조는 생략함",
                "candidate_response_sample": "리뷰 범위, 이슈, 판정까지 구조화해 제공함"
            }
        ]
    }
    input_file = tmp_path / "response-mode-missing-scope.json"
    input_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        mod._load_response_mode_cases(input_file)
    except ValueError:
        pass
    else:
        raise AssertionError("observed_output response mode case should require comparison_scope")


def test_compare_response_modes_caps_verdict_when_only_cross_surface_observed_output_exists():
    mod = _load_module()

    cases = [
        {
            "request": "이거 클로드코드로 실행한건데 이거 제대로 진행된 거 맞아? plan",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": [
                "assistant: 구현 방식 제시",
                "assistant: PHASE 3 ▸ TDD 태스크 분해",
                "assistant: 준비되었습니다. /task 로 진행하시겠습니까?"
            ],
            "candidate_trace": [
                "assistant: plan 단계에서는 과진행이라고 먼저 판단",
                "assistant: 왜 맞지 않는지 설명하고 다음 스킬 없이 멈춤"
            ],
            "baseline_output_chars": 438,
            "candidate_output_chars": 214,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "source_type": "observed_output",
            "comparison_scope": "cross_surface",
            "evidence": "baseline: Claude Code, candidate: Codex",
            "baseline_response_sample": "PHASE 3 ▸ TDD 태스크 분해",
            "candidate_response_sample": "plan 단계에서는 과진행이라고 먼저 판단",
        },
        {
            "request": "로그인 버튼 컴포넌트 구현해줘",
            "expected_mode": "execute-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 답변만 제공", "user: 아니 구현해줘"],
            "candidate_trace": ["assistant: 구현 단계로 바로 진입"],
            "baseline_output_chars": 320,
            "candidate_output_chars": 348,
            "baseline_task_start_delay": 2,
            "candidate_task_start_delay": 1,
            "source_type": "synthetic",
        },
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["comparison_scope_counts"] == {"cross_surface": 1}
    assert report["decision"]["observed_evidence_guard"] == "insufficient_same_surface"
    assert report["decision"]["verdict"] == "revise"


def test_compare_response_modes_adopts_when_three_of_four_checks_pass():
    mod = _load_module()

    cases = [
        {
            "request": "로그인 버튼 컴포넌트 구현해줘",
            "expected_mode": "execute-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 설명만 제공", "user: 아니 구현해줘"],
            "candidate_trace": ["assistant: 구현 시작"],
            "baseline_output_chars": 100,
            "candidate_output_chars": 130,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
        },
        {
            "request": "지금까지 뭐 했는지 정리해줘",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 요약 제공"],
            "candidate_trace": ["assistant: 요약 제공"],
            "baseline_output_chars": 100,
            "candidate_output_chars": 100,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
        },
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["mode_accuracy_delta"] >= 0.15
    assert report["summary"]["reroute_rate_delta"] <= -0.30
    assert report["summary"]["candidate_task_start_delay_delta"] <= 0
    assert report["decision"]["checks"]["output_growth_within_budget"] is False
    assert report["decision"]["passed_checks"] == 3
    assert report["decision"]["verdict"] == "adopt"


def test_compare_response_modes_derives_reroute_from_trace():
    mod = _load_module()

    cases = [
        {
            "request": "리뷰해줘",
            "expected_mode": "review-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 150,
            "candidate_output_chars": 140,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
        }
    ]

    report = mod.compare_response_modes(cases)

    assert report["cases"][0]["baseline"]["reroute"] is True
    assert report["cases"][0]["candidate"]["reroute"] is False
