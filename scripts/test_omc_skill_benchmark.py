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


def _write_observed_run(runs_root: Path, run_id: str, payload: dict[str, object]) -> None:
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def test_build_report_adds_pairwise_comparison_summary_when_cases_share_comparison_id():
    mod = _load_module()

    cases = [
        {
            "skill": "omc-task",
            "request": "로그인 버튼 컴포넌트 구현해줘",
            "comparison_id": "task-compression",
            "variant": "baseline",
            "source_type": "synthetic",
            "response": "CONTRACT\nRED\nTDD GATE\nHandoff\n다음 액션: $omc-review\n설명 한 줄 추가\n",
            "expected_next_actions": ["$omc-review"],
            "required_markers": ["CONTRACT", "RED", "TDD GATE", "Handoff", "다음 액션"],
        },
        {
            "skill": "omc-task",
            "request": "로그인 버튼 컴포넌트 구현해줘",
            "comparison_id": "task-compression",
            "variant": "candidate",
            "source_type": "current_contract_sample",
            "evidence": "current skill snapshot",
            "response": "CONTRACT / RED / TDD GATE / Handoff\n다음 액션: $omc-review\n",
            "expected_next_actions": ["$omc-review"],
            "required_markers": ["CONTRACT", "RED", "TDD GATE", "Handoff", "다음 액션"],
        },
    ]

    report = mod.build_report(cases)

    assert report["comparison_summary"]["pair_count"] == 1
    assert report["comparison_summary"]["avg_output_chars_delta"] < 0
    assert report["comparison_summary"]["avg_output_reduction_rate"] > 0
    assert report["comparison_summary"]["next_action_preserved_rate"] == 1.0
    assert report["comparison_summary"]["evidence_level_counts"] == {"synthetic_pair": 1}
    assert report["comparisons"][0]["comparison_id"] == "task-compression"
    assert report["comparisons"][0]["baseline_source_type"] == "synthetic"
    assert report["comparisons"][0]["candidate_source_type"] == "current_contract_sample"
    assert report["comparisons"][0]["evidence_level"] == "synthetic_pair"
    assert report["comparisons"][0]["candidate_output_chars"] < report["comparisons"][0]["baseline_output_chars"]


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


def test_next_action_parsing_accepts_common_recommendation_labels():
    mod = _load_module()

    case = {
        "skill": "omc-plan",
        "request": "계획해줘",
        "response": (
            "설명: 계획 흐름 정리\n"
            "추천 다음 스킬: $omc-task\n"
        ),
        "expected_next_actions": ["$omc-task"],
        "required_markers": ["설명", "추천 다음 스킬"],
    }

    scored = mod.evaluate_case(case)

    assert scored["metrics"]["next_action_count"] == 1
    assert scored["metrics"]["next_action_single"] is True
    assert scored["metrics"]["expected_next_action_hit"] is True


def test_next_action_parsing_accepts_non_colon_recommendation_labels():
    mod = _load_module()

    case = {
        "skill": "omc-status",
        "request": "상태 알려줘",
        "response": (
            "현재 상태 요약\n"
            "다음 단계 - $omc-reentry\n"
        ),
        "expected_next_actions": ["$omc-reentry"],
        "required_markers": ["현재 상태 요약", "다음 단계"],
    }

    scored = mod.evaluate_case(case)

    assert scored["metrics"]["next_action_count"] == 1
    assert scored["metrics"]["next_action_single"] is True
    assert scored["metrics"]["expected_next_action_hit"] is True


def test_next_action_parsing_accepts_english_recommendation_labels():
    mod = _load_module()

    case = {
        "skill": "omc-review",
        "request": "리뷰해줘",
        "response": (
            "요약: 변경 검토\n"
            "Next action: $omc-ship\n"
        ),
        "expected_next_actions": ["$omc-ship"],
        "required_markers": ["요약", "Next action"],
    }

    scored = mod.evaluate_case(case)

    assert scored["metrics"]["next_action_count"] == 1
    assert scored["metrics"]["next_action_single"] is True
    assert scored["metrics"]["expected_next_action_hit"] is True


def test_next_action_parsing_accepts_label_and_separator_variants():
    mod = _load_module()

    case = {
        "skill": "omc-status",
        "request": "상태 알려줘",
        "response": (
            "현재 상태 요약\n"
            "Next step → $omc-reentry\n"
        ),
        "expected_next_actions": ["$omc-reentry"],
        "required_markers": ["현재 상태 요약", "Next step"],
    }

    scored = mod.evaluate_case(case)

    assert scored["metrics"]["next_action_count"] == 1
    assert scored["metrics"]["next_action_single"] is True
    assert scored["metrics"]["expected_next_action_hit"] is True


def test_next_action_parsing_accepts_arrow_separator_variant():
    mod = _load_module()

    case = {
        "skill": "omc-status",
        "request": "상태 알려줘",
        "response": (
            "현재 상태 요약\n"
            "Recommended next skill => $omc-reentry\n"
        ),
        "expected_next_actions": ["$omc-reentry"],
        "required_markers": ["현재 상태 요약", "Recommended next skill"],
    }

    scored = mod.evaluate_case(case)

    assert scored["metrics"]["next_action_count"] == 1
    assert scored["metrics"]["next_action_single"] is True
    assert scored["metrics"]["expected_next_action_hit"] is True


def test_split_next_action_spec_separates_label_and_payload():
    mod = _load_module()

    label, separator, payload = mod._split_next_action_spec("Recommended next skill => $omc-ship")

    assert label == "recommended next skill"
    assert separator == "=>"
    assert payload == "$omc-ship"


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
        [{"response": "ok", "expected_next_actions": [], "required_markers": [], "source_type": "current_contract_sample"}],
        [{"response": "ok", "expected_next_actions": [], "required_markers": [], "comparison_id": "pair-only"}],
        [{"response": "ok", "expected_next_actions": [], "required_markers": [], "variant": "baseline"}],
        [{"response": "ok", "expected_next_actions": [], "required_markers": [], "comparison_id": "pair", "variant": "wrong"}],
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
    task_cases = [case for case in cases if case.get("skill") == "omc-task"]
    task_comparison_cases = [case for case in task_cases if case.get("comparison_id")]
    task_observed_cases = [case for case in task_cases if case.get("source_type") == "observed_output"]
    assert len(reentry_cases) == 3, "fixture must include exactly three omc-reentry cases"
    assert len(task_cases) == 3, "fixture must include two comparison cases and one observed omc-task case"
    assert len(task_comparison_cases) == 2
    assert len(task_observed_cases) == 1
    observed_cases = [case for case in cases if case.get("source_type") == "observed_output"]
    assert len(observed_cases) >= 2, "fixture must include at least two observed_output cases"
    contract_sample_cases = [case for case in cases if case.get("source_type") == "current_contract_sample"]
    assert len(contract_sample_cases) == 1

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

    task_case_map = {case["variant"]: case for case in task_comparison_cases}
    assert set(task_case_map) == {"baseline", "candidate"}
    assert task_case_map["baseline"]["comparison_id"] == "omc-task-compression-login-button"
    assert task_case_map["candidate"]["comparison_id"] == "omc-task-compression-login-button"
    assert task_case_map["baseline"]["source_type"] == "synthetic"
    assert task_case_map["candidate"]["source_type"] == "current_contract_sample"
    assert len(task_case_map["candidate"]["response"]) < len(task_case_map["baseline"]["response"])

    observed_task_requests = {case["request"] for case in task_observed_cases}
    assert "대시보드 운영형 고도화 작업 계속 진행 (현재 staged 변경 포함, 테스트/리뷰/PR 준비까지)" in observed_task_requests
    assert all(case.get("evidence") for case in task_observed_cases)

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
    assert payload["summary"]["case_count"] == 7
    assert payload["summary"]["next_action_single_rate"] == 5 / 7
    assert payload["summary"]["expected_next_action_hit_rate"] == 4 / 6
    assert payload["summary"]["avg_missing_markers_count"] == 4 / 7
    assert payload["summary"]["source_type_counts"] == {
        "current_contract_sample": 1,
        "observed_output": 2,
        "synthetic": 4,
    }
    assert payload["comparison_summary"]["pair_count"] == 1
    assert payload["comparison_summary"]["avg_output_chars_delta"] < 0
    assert payload["comparison_summary"]["avg_output_reduction_rate"] > 0
    assert payload["comparison_summary"]["evidence_level_counts"] == {"synthetic_pair": 1}
    reentry_cases = [case for case in payload["cases"] if case["skill"] == "omc-reentry"]
    assert len(reentry_cases) == 3, "score output must include all omc-reentry fixture cases"

    case_map = {case["request"]: case for case in reentry_cases}
    assert case_map["이 프로젝트 뭐였지"]["score"]["verdict"] == "good"
    assert case_map["복귀 요약 후 다음에 뭘 해야 할지 추천해줘"]["metrics"]["next_action_single"] is False
    assert case_map["구조만 빠르게 알려줘"]["metrics"]["missing_markers_count"] == 1
    observed_case = next(case for case in payload["cases"] if case.get("source_type") == "observed_output")
    assert observed_case["skill"] == "omc-plan"
    assert observed_case["metrics"]["expected_next_action_hit"] is True
    observed_task_case = next(
        case
        for case in payload["cases"]
        if case["skill"] == "omc-task" and case.get("source_type") == "observed_output"
    )
    assert observed_task_case["metrics"]["missing_markers_count"] == 3
    assert observed_task_case["score"]["verdict"] == "weak"
    task_comparison = payload["comparisons"][0]
    assert task_comparison["skill"] == "omc-task"
    assert task_comparison["request"] == "로그인 버튼 컴포넌트 구현해줘"
    assert task_comparison["candidate_output_chars"] < task_comparison["baseline_output_chars"]
    assert task_comparison["next_action_preserved"] is True
    assert task_comparison["baseline_source_type"] == "synthetic"
    assert task_comparison["candidate_source_type"] == "current_contract_sample"


def test_compare_response_modes_summarizes_candidate_gain():
    mod = _load_module()

    cases = [
        {
            "request": "로그인 버튼 컴포넌트 구현해줘",
            "expected_mode": "execute-first",
            "expected_next_action": "$omc-task",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 답변만 제공", "user: 아니 구현해줘"],
            "candidate_trace": ["assistant: 구현 단계로 바로 진입"],
            "baseline_output_chars": 320,
            "candidate_output_chars": 350,
            "baseline_task_start_delay": 2,
            "candidate_task_start_delay": 1,
            "baseline_next_action": "사용자 선택 대기",
            "candidate_next_action": "$omc-task",
        },
        {
            "request": "이 변경 diff 리뷰해줘",
            "expected_mode": "review-first",
            "expected_next_action": "$omc-ship",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 리뷰 시작"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 280,
            "candidate_output_chars": 295,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
            "baseline_next_action": "$omc-task",
            "candidate_next_action": "$omc-ship",
        },
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["case_count"] == 2
    assert report["summary"]["baseline_mode_accuracy"] == 0.5
    assert report["summary"]["candidate_mode_accuracy"] == 1.0
    assert report["summary"]["mode_accuracy_delta"] == 0.5
    assert report["summary"]["baseline_reroute_rate"] == 0.5
    assert report["summary"]["candidate_reroute_rate"] == 0.0
    assert report["summary"]["baseline_wrong_first_skill_rate"] == 0.5
    assert report["summary"]["candidate_wrong_first_skill_rate"] == 0.0
    assert report["summary"]["wrong_first_skill_rate_delta"] == -0.5
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["summary"]["candidate_output_chars_delta"] == 45 / 2
    assert report["summary"]["candidate_task_start_delay_delta"] == -0.5
    assert report["decision"]["verdict"] == "adopt"
    assert report["cases"][0]["baseline"]["mode"] == "answer-first"
    assert report["cases"][0]["candidate"]["mode"] == "execute-first"


def test_compare_response_modes_caps_decision_when_wrong_next_step_stays_high():
    mod = _load_module()

    cases = [
        {
            "request": "계획해줘",
            "expected_mode": "answer-first",
            "expected_next_action": "사용자 선택 대기",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 계획 정리"],
            "candidate_trace": ["assistant: 계획 정리"],
            "baseline_output_chars": 260,
            "candidate_output_chars": 265,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
            "baseline_next_action": "$omc-task",
            "candidate_next_action": "$omc-task",
        }
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["candidate_wrong_next_step_rate"] == 1.0
    assert report["decision"]["checks"]["next_step_accuracy_not_worse"] is False
    assert report["decision"]["verdict"] in {"revise", "hold"}


def test_decision_from_summary_blocks_adopt_when_next_step_check_fails():
    mod = _load_module()

    summary = {
        "mode_accuracy_delta": 0.2,
        "reroute_rate_delta": -0.4,
        "candidate_task_start_delay_delta": 0,
        "baseline_output_chars_avg": 300,
        "candidate_output_chars_delta": 15,
        "observed_output_count": 0,
        "observed_same_surface_count": 0,
        "next_action_case_count": 1,
        "candidate_wrong_next_step_rate": 1.0,
        "wrong_next_step_rate_delta": 0,
    }

    decision = mod._decision_from_summary(summary)

    assert decision["checks"]["next_step_accuracy_not_worse"] is False
    assert decision["passed_checks"] == 4
    assert decision["verdict"] == "revise"


def test_decision_from_summary_does_not_report_ready_when_baseline_flag_is_false():
    mod = _load_module()

    summary = {
        "mode_accuracy_delta": 0.1,
        "reroute_rate_delta": -0.1,
        "candidate_task_start_delay_delta": 0,
        "baseline_output_chars_avg": 300,
        "candidate_output_chars_delta": -20,
        "observed_output_count": 20,
        "observed_same_surface_count": 2,
        "readiness_observed_sample_count": 20,
        "readiness_same_surface_case_count": 2,
        "readiness_distinct_policy_pair_count": 2,
        "baseline_comparison_ready": False,
        "next_action_case_count": 0,
        "candidate_wrong_next_step_rate": 0,
        "wrong_next_step_rate_delta": 0,
    }

    decision = mod._decision_from_summary(summary)

    assert decision["baseline_comparison_status"] == "deferred"
    assert decision["next_kpi_blocker"] == "baseline_comparison_not_ready"
    assert decision["readiness_status_line"] == "not ready: baseline comparison input is not ready"
    assert decision["readiness_blocker_line"] == "pending: baseline comparison input is not ready"
    assert decision["policy_comparison_summary"] == (
        "policy comparison pending: baseline comparison input is not ready"
    )


def test_decision_from_summary_keeps_rejection_count_without_reason_map():
    mod = _load_module()

    summary = {
        "mode_accuracy_delta": 0.0,
        "reroute_rate_delta": 0.0,
        "candidate_task_start_delay_delta": 0,
        "baseline_output_chars_avg": 300,
        "candidate_output_chars_delta": 0,
        "observed_output_count": 20,
        "observed_same_surface_count": 0,
        "readiness_observed_sample_count": 20,
        "readiness_same_surface_case_count": 0,
        "readiness_distinct_policy_pair_count": 2,
        "baseline_comparison_ready": False,
        "rejected_observed_output_case_count": 2,
        "rejected_observed_output_reasons": {},
        "next_action_case_count": 0,
        "candidate_wrong_next_step_rate": 0,
        "wrong_next_step_rate_delta": 0,
    }

    decision = mod._decision_from_summary(summary)

    assert decision["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert decision["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: need more same-surface evidence; rejected observed_output=2"
    )


def test_decision_from_summary_keeps_ready_rejection_count_without_reason_map():
    mod = _load_module()

    summary = {
        "mode_accuracy_delta": 0.0,
        "reroute_rate_delta": 0.0,
        "candidate_task_start_delay_delta": 0,
        "baseline_output_chars_avg": 300,
        "candidate_output_chars_delta": 0,
        "observed_output_count": 20,
        "observed_same_surface_count": 2,
        "readiness_observed_sample_count": 20,
        "readiness_same_surface_case_count": 2,
        "readiness_distinct_policy_pair_count": 2,
        "baseline_comparison_ready": True,
        "rejected_observed_output_case_count": 2,
        "rejected_observed_output_reasons": {},
        "next_action_case_count": 0,
        "candidate_wrong_next_step_rate": 0,
        "wrong_next_step_rate_delta": 0,
    }

    decision = mod._decision_from_summary(summary)

    assert decision["baseline_comparison_status"] == "ready"
    assert decision["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=2"
    )


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


def test_top_expensive_flows_cli_outputs_json(tmp_path: Path):
    cases = {
        "cases": [
            {
                "request": "다음 1순위 작업을 더 잘게 쪼개서",
                "expected_mode": "answer-first",
                "expected_next_action": "$omc-critique",
                "baseline_policy": "baseline",
                "candidate_policy": "candidate",
                "baseline_trace": ["assistant: 바로 구현 전제", "user: 아니 더 잘게 계획해줘"],
                "candidate_trace": ["assistant: 재분해", "assistant: critique 추천"],
                "baseline_output_chars": 268,
                "candidate_output_chars": 302,
                "baseline_task_start_delay": 1,
                "candidate_task_start_delay": 0,
                "baseline_next_action": "$omc-task",
                "candidate_next_action": "$omc-critique",
                "source_type": "observed_request",
                "evidence": "real request",
            }
        ]
    }
    input_file = tmp_path / "expensive-flows.json"
    input_file.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "top-expensive-flows", "--input", str(input_file)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["case_count"] == 1
    assert payload["summary"]["top_flow_count"] == 1
    assert payload["flows"][0]["flow_kind"] == "wrong_next_step"


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
                "expected_next_action": "$omc-ship",
                "baseline_next_action": "$omc-task",
                "candidate_next_action": "$omc-ship",
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
    assert cases[0]["expected_next_action"] == "$omc-ship"
    assert cases[0]["baseline_next_action"] == "$omc-task"
    assert cases[0]["candidate_next_action"] == "$omc-ship"
    assert cases[0]["baseline_response_sample"] == "변경 요약만 제공하고 리뷰 구조는 생략함"
    assert cases[0]["candidate_response_sample"] == "리뷰 범위, 이슈, 판정까지 구조화해 제공함"

    report = mod.compare_response_modes(cases)
    assert report["summary"]["source_type_counts"] == {"observed_output": 1}
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["cases"][0]["source_type"] == "observed_output"
    assert report["cases"][0]["baseline"]["response_sample"] == "변경 요약만 제공하고 리뷰 구조는 생략함"
    assert report["cases"][0]["candidate"]["response_sample"] == "리뷰 범위, 이슈, 판정까지 구조화해 제공함"


def test_collect_observed_response_mode_cases_builds_neutral_seed_cases_from_runs(tmp_path: Path):
    mod = _load_module()

    runs_dir = tmp_path / ".omc" / "runs"
    (runs_dir / "20260630T101010-abcd1234").mkdir(parents=True, exist_ok=True)
    (runs_dir / "20260630T111111-bcde2345").mkdir(parents=True, exist_ok=True)
    (runs_dir / "20260630T121212-cdef3456").mkdir(parents=True, exist_ok=True)

    (runs_dir / "20260630T101010-abcd1234" / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "현재 로드맵 최신화하고 다음 작업 체크",
                "benchmark_source_type": "observed_request",
                "policy_pair": "baseline->candidate",
                "status": "completed",
                "last_completed_step": "review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (runs_dir / "20260630T111111-bcde2345" / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "이 변경 위험한지 먼저 리뷰해주고, 괜찮으면 그다음 커밋까지 해줘",
                "benchmark_source_type": "observed_request",
                "policy_pair": "candidate->baseline",
                "status": "completed",
                "last_completed_step": "review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (runs_dir / "20260630T121212-cdef3456" / "result.json").write_text(
        json.dumps(
            {
                "task_id": "ignore-me",
                "instruction": "메타데이터 없는 실행",
                "status": "completed",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = mod.collect_observed_response_mode_cases(runs_dir)

    assert payload["summary"]["case_count"] == 2
    assert payload["summary"]["observed_sample_case_count"] == 0
    assert payload["summary"]["neutral_seed_case_count"] == 2
    assert payload["summary"]["policy_pair_counts"] == {
        "baseline->candidate": 1,
        "candidate->baseline": 1,
    }
    assert payload["summary"]["distinct_policy_pair_count"] == 2

    first = payload["cases"][0]
    second = payload["cases"][1]

    assert first["request"] == "현재 로드맵 최신화하고 다음 작업 체크"
    assert first["expected_mode"] == "answer-first"
    assert first["baseline_policy"] == "baseline"
    assert first["candidate_policy"] == "candidate"
    assert first["source_type"] == "observed_request"
    assert "run=20260630T101010-abcd1234" in first["evidence"]
    assert "task=observed-collect" in first["evidence"]
    assert first["baseline_output_chars"] == first["candidate_output_chars"]
    assert first["baseline_task_start_delay"] == first["candidate_task_start_delay"] == 0

    assert second["expected_mode"] == "review-first"
    assert second["baseline_policy"] == "candidate"
    assert second["candidate_policy"] == "baseline"
    assert second["source_type"] == "observed_request"


def test_collect_observed_response_modes_cli_outputs_seed_case_json(tmp_path: Path):
    runs_dir = tmp_path / ".omc" / "runs" / "20260630T131313-feed6789"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "버그 원인 먼저 보고 바로 고칠 수 있으면 수정해줘",
                "benchmark_source_type": "observed_request",
                "policy_pair": "baseline->candidate",
                "status": "completed",
                "last_completed_step": "task",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "collect-observed-response-modes",
            "--runs-dir",
            str(tmp_path / ".omc" / "runs"),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["case_count"] == 1
    assert payload["cases"][0]["expected_mode"] == "execute-first"
    assert payload["cases"][0]["source_type"] == "observed_request"


def test_neutral_observed_seed_cases_do_not_count_toward_kpi_readiness(tmp_path: Path):
    mod = _load_module()

    runs_dir = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_dir / f"20260630T13{index:02d}00-seed{index:02d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"현재 로드맵 최신화하고 다음 작업 체크 {index}",
                    "benchmark_source_type": "observed_request",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    collected = mod.collect_observed_response_mode_cases(runs_dir)
    report = mod.compare_response_modes(collected["cases"])

    assert report["summary"]["sample_case_count"] == 20
    assert report["summary"]["observed_sample_case_count"] == 0
    assert report["summary"]["sample_requirement_met"] is False
    assert report["summary"]["distinct_policy_pair_count"] == 2
    assert report["summary"]["readiness_distinct_policy_pair_count"] == 0
    assert report["summary"]["policy_requirement_met"] is False
    assert report["decision"]["kpi_readiness"] == "incomplete"


def test_collect_observed_response_mode_cases_builds_observed_output_cases_from_runs(tmp_path: Path):
    mod = _load_module()

    runs_dir = tmp_path / ".omc" / "runs" / "20260630T141414-out12345"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "이거 클로드코드로 실행한건데 이거 제대로 진행된 거 맞아? plan",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "구현 방식부터 길게 제시하고 다음 스킬 추천이 없음",
                "candidate_response_sample": "판정과 다음 액션을 분리해 멈춤",
                "status": "completed",
                "last_completed_step": "review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = mod.collect_observed_response_mode_cases(tmp_path / ".omc" / "runs")

    assert payload["summary"]["case_count"] == 1
    assert payload["summary"]["observed_output_case_count"] == 1
    assert payload["summary"]["same_surface_case_count"] == 1
    case = payload["cases"][0]
    assert case["source_type"] == "observed_output"
    assert case["comparison_scope"] == "same_surface"
    assert case["baseline_response_sample"] == "구현 방식부터 길게 제시하고 다음 스킬 추천이 없음"
    assert case["candidate_response_sample"] == "판정과 다음 액션을 분리해 멈춤"


def test_collect_observed_response_mode_cases_summarizes_readiness_observed_counts(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"

    same_surface = runs_root / "20260630T151515-same1111"
    same_surface.mkdir(parents=True, exist_ok=True)
    (same_surface / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "plan 출력이 멈춰야 하는지 확인해줘",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "바로 task로 진행",
                "candidate_response_sample": "판정 후 멈춤",
                "status": "completed",
                "last_completed_step": "review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    cross_surface = runs_root / "20260630T161616-cross2222"
    cross_surface.mkdir(parents=True, exist_ok=True)
    (cross_surface / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "status 응답 품질 비교",
                "benchmark_source_type": "observed_output",
                "policy_pair": "candidate->baseline",
                "comparison_scope": "cross_surface",
                "baseline_response_sample": "긴 상태 설명",
                "candidate_response_sample": "핵심만 정리",
                "status": "completed",
                "last_completed_step": "review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    neutral_seed = runs_root / "20260630T171717-seed3333"
    neutral_seed.mkdir(parents=True, exist_ok=True)
    (neutral_seed / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "현재 로드맵 최신화하고 다음 작업 체크",
                "benchmark_source_type": "observed_request",
                "policy_pair": "baseline->candidate",
                "status": "completed",
                "last_completed_step": "review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = mod.collect_observed_response_mode_cases(runs_root)

    assert payload["summary"]["case_count"] == 3
    assert payload["summary"]["observed_sample_case_count"] == 2
    assert payload["summary"]["same_surface_case_count"] == 1
    assert payload["summary"]["cross_surface_case_count"] == 1
    assert payload["summary"]["neutral_seed_case_count"] == 1
    assert payload["summary"]["readiness_observed_sample_count"] == 2
    assert payload["summary"]["readiness_same_surface_case_count"] == 1
    assert payload["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more observed samples"
    )


def test_collect_observed_response_mode_cases_reports_rejected_observed_output_metadata(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    invalid_output = runs_root / "20260630T181818-bad4444"
    invalid_output.mkdir(parents=True, exist_ok=True)
    (invalid_output / "result.json").write_text(
        json.dumps(
            {
                "task_id": "observed-collect",
                "instruction": "status 응답 품질 비교",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "긴 상태 설명",
                "status": "completed",
                "last_completed_step": "review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = mod.collect_observed_response_mode_cases(runs_root)

    assert payload["summary"]["case_count"] == 0
    assert payload["summary"]["observed_output_case_count"] == 0
    assert payload["summary"]["rejected_observed_output_case_count"] == 1
    assert payload["summary"]["rejected_observed_output_reasons"] == {
        "missing_candidate_response_sample": 1
    }
    assert payload["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more observed samples; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )


def test_collect_observed_response_mode_cases_marks_bottleneck_ready_when_thresholds_are_met(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T010{index:02d}-run{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed review request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    payload = mod.collect_observed_response_mode_cases(runs_root)

    assert payload["summary"]["observed_sample_case_count"] == 20
    assert payload["summary"]["readiness_same_surface_case_count"] == 1
    assert payload["summary"]["distinct_policy_pair_count"] == 2
    assert payload["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready"
    )


def test_collected_observed_ready_summary_matches_comparison_ready_summary(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T020{index:02d}-bridge{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed bridge request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready"
    )
    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled"
    )


def test_collected_observed_ready_summary_preserves_rejection_context_in_policy_summary(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T021{index:02d}-ready{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed ready request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "same_surface" if index < 2 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    _write_observed_run(
        runs_root,
        "20260701T02199-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed ready invalid request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["readiness_status_line"] == (
        "ready: baseline comparison wording can be enabled"
    )
    assert report["decision"]["baseline_comparison_line"] == (
        "baseline comparison ready: candidate improves mode accuracy by 0.0pp, "
        "improves reroute rate by 0.0pp, and improves task start delay by 0.0"
    )
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert report["decision"]["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert collected["summary"]["fixture_taxonomy_counts"] == {
        "ready_expected": 1,
        "pending_expected": 0,
        "ambiguous": 1,
    }


def test_ready_mixed_fixture_ignores_observed_request_for_readiness_counts(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T022{index:02d}-mixed{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed mixed request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "same_surface" if index < 2 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    _write_observed_run(
        runs_root,
        "20260701T02290-neutral0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed neutral request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "candidate->baseline",
            "status": "completed",
            "last_completed_step": "plan",
        },
    )
    _write_observed_run(
        runs_root,
        "20260701T02299-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed mixed invalid request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_sample_case_count"] == 20
    assert collected["summary"]["readiness_observed_sample_count"] == 20
    assert collected["summary"]["distinct_policy_pair_count"] == 2
    assert collected["summary"]["readiness_distinct_policy_pair_count"] == 2
    assert collected["summary"]["readiness_sample_gap"] == 0
    assert collected["summary"]["readiness_same_surface_gap"] == 0
    assert collected["summary"]["baseline_comparison_ready"] is True
    assert collected["summary"]["readiness_blocker_line"] == (
        "ready: baseline comparison wording can be enabled"
    )
    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["readiness_status_line"] == (
        "ready: baseline comparison wording can be enabled"
    )


def test_accumulated_observed_dataset_fixture_keeps_collected_and_report_ready_in_sync(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T023{index:02d}-accum{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed accumulated request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "same_surface" if index < 2 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    _write_observed_run(
        runs_root,
        "20260701T02390-neutral0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed accumulated neutral request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "candidate->baseline",
            "status": "completed",
            "last_completed_step": "plan",
        },
    )
    _write_observed_run(
        runs_root,
        "20260701T02399-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed accumulated invalid request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["readiness_sample_gap"] == 0
    assert collected["summary"]["readiness_same_surface_gap"] == 0
    assert collected["summary"]["baseline_comparison_ready"] is True
    assert collected["summary"]["readiness_blocker_line"] == (
        "ready: baseline comparison wording can be enabled"
    )
    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["readiness_status_line"] == (
        "ready: baseline comparison wording can be enabled"
    )
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert report["summary"]["readiness_distinct_policy_pair_count"] == 2
    assert report["summary"]["observed_sample_case_count"] == 20


def test_pending_mixed_observed_dataset_keeps_collected_and_report_deferred_in_sync(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T024{index:02d}-pending{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed pending request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    _write_observed_run(
        runs_root,
        "20260701T02490-neutral0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed pending neutral request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "candidate->baseline",
            "status": "completed",
            "last_completed_step": "plan",
        },
    )
    _write_observed_run(
        runs_root,
        "20260701T02499-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed pending invalid request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "baseline->candidate",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["readiness_observed_sample_count"] == 20
    assert collected["summary"]["readiness_distinct_policy_pair_count"] == 2
    assert collected["summary"]["readiness_sample_gap"] == 0
    assert collected["summary"]["readiness_same_surface_gap"] == 1
    assert collected["summary"]["baseline_comparison_ready"] is False
    assert collected["summary"]["readiness_blocker_line"] == (
        "pending: need more same-surface evidence"
    )
    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more same-surface evidence; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison pending: need more same-surface evidence; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )


def test_same_surface_threshold_transition_ignores_invalid_same_surface_noise(
    tmp_path: Path,
):
    mod = _load_module()

    def _collect_cases(
        *,
        valid_same_surface_count: int,
        run_prefix: str,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, int]]:
        runs_root = tmp_path / ".omc" / "runs" / run_prefix
        for index in range(20):
            run_dir = runs_root / f"20260701T024{index:02d}-threshold{index:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "result.json").write_text(
                json.dumps(
                    {
                        "task_id": "observed-collect",
                        "instruction": f"실제 observed threshold request {index}",
                        "benchmark_source_type": "observed_output",
                        "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                        "comparison_scope": (
                            "same_surface" if index < valid_same_surface_count else "cross_surface"
                        ),
                        "baseline_response_sample": "기존 응답 샘플",
                        "candidate_response_sample": "개선 응답 샘플",
                        "status": "completed",
                        "last_completed_step": "review",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        _write_observed_run(
            runs_root,
            f"20260701T0249{valid_same_surface_count}-invalid0001",
            {
                "task_id": "observed-collect",
                "instruction": "실제 observed threshold invalid request",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

        collected = mod.collect_observed_response_mode_cases(runs_root)
        report = mod.compare_response_modes(collected["cases"])
        taxonomy = mod._fixture_taxonomy_counts_from_readiness(collected["cases"])
        return collected, report, taxonomy

    zero_collected, zero_report, zero_taxonomy = _collect_cases(
        valid_same_surface_count=0,
        run_prefix="threshold-zero",
    )
    one_collected, one_report, one_taxonomy = _collect_cases(
        valid_same_surface_count=1,
        run_prefix="threshold-one",
    )

    assert zero_collected["summary"]["readiness_same_surface_gap"] == 1
    assert zero_collected["summary"]["baseline_comparison_ready"] is False
    assert zero_collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more same-surface evidence; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert zero_report["decision"]["baseline_comparison_status"] == "deferred"
    assert zero_report["decision"]["policy_comparison_summary"] == (
        "policy comparison pending: need more same-surface evidence; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert zero_taxonomy == {
        "ready_expected": 0,
        "pending_expected": 1,
        "ambiguous": 0,
    }

    assert one_collected["summary"]["readiness_same_surface_gap"] == 0
    assert one_collected["summary"]["baseline_comparison_ready"] is True
    assert one_collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert one_report["decision"]["baseline_comparison_status"] == "ready"
    assert one_report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert one_taxonomy == {
        "ready_expected": 1,
        "pending_expected": 1,
        "ambiguous": 0,
    }


def test_policy_pair_threshold_transition_ignores_invalid_noise_after_same_surface_is_ready(
    tmp_path: Path,
):
    mod = _load_module()

    def _collect_cases(
        *,
        valid_candidate_policy_pair: bool,
        run_prefix: str,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, int]]:
        runs_root = tmp_path / ".omc" / "runs" / run_prefix
        for index in range(20):
            run_dir = runs_root / f"20260701T025{index:02d}-pair{index:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "result.json").write_text(
                json.dumps(
                    {
                        "task_id": "observed-collect",
                        "instruction": f"실제 observed policy threshold request {index}",
                        "benchmark_source_type": "observed_output",
                        "policy_pair": (
                            "candidate->baseline"
                            if valid_candidate_policy_pair and index == 1
                            else "baseline->candidate"
                        ),
                        "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                        "baseline_response_sample": "기존 응답 샘플",
                        "candidate_response_sample": "개선 응답 샘플",
                        "status": "completed",
                        "last_completed_step": "review",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        _write_observed_run(
            runs_root,
            f"20260701T0259{'1' if valid_candidate_policy_pair else '0'}-invalid0001",
            {
                "task_id": "observed-collect",
                "instruction": "실제 observed policy threshold invalid request",
                "benchmark_source_type": "observed_output",
                "policy_pair": "candidate->baseline",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

        collected = mod.collect_observed_response_mode_cases(runs_root)
        report = mod.compare_response_modes(collected["cases"])
        taxonomy = mod._fixture_taxonomy_counts_from_readiness(collected["cases"])
        return collected, report, taxonomy

    pending_collected, pending_report, pending_taxonomy = _collect_cases(
        valid_candidate_policy_pair=False,
        run_prefix="policy-pair-pending",
    )
    ready_collected, ready_report, ready_taxonomy = _collect_cases(
        valid_candidate_policy_pair=True,
        run_prefix="policy-pair-ready",
    )

    assert pending_collected["summary"]["readiness_same_surface_gap"] == 0
    assert pending_collected["summary"]["readiness_distinct_policy_pair_count"] == 1
    assert pending_collected["summary"]["baseline_comparison_ready"] is False
    assert pending_collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more policy pair coverage; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert pending_collected["summary"]["policy_comparison_summary"] == (
        "policy comparison pending: need more policy pair coverage; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert pending_collected["summary"]["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: need more policy pair coverage; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert pending_collected["summary"]["next_priority_recommendation"] == "expand_policy_pair_coverage"
    assert pending_collected["summary"]["next_priority_reason"] == "need more policy pair coverage"
    assert pending_report["decision"]["baseline_comparison_status"] == "deferred"
    assert pending_report["decision"]["next_kpi_blocker"] == "insufficient_policy_pairs"
    assert pending_report["decision"]["policy_comparison_summary"] == (
        "policy comparison pending: need more policy pair coverage; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert pending_report["decision"]["next_priority_recommendation"] == "expand_policy_pair_coverage"
    assert pending_report["decision"]["next_priority_reason"] == "need more policy pair coverage"
    assert pending_taxonomy == {
        "ready_expected": 0,
        "pending_expected": 1,
        "ambiguous": 0,
    }

    assert ready_collected["summary"]["readiness_same_surface_gap"] == 0
    assert ready_collected["summary"]["readiness_distinct_policy_pair_count"] == 2
    assert ready_collected["summary"]["baseline_comparison_ready"] is True
    assert ready_collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert ready_collected["summary"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert ready_collected["summary"]["next_priority_recommendation"] == "maintain_policy_comparison_confidence"
    assert ready_collected["summary"]["next_priority_reason"] == "readiness requirements are currently satisfied"
    assert ready_report["decision"]["baseline_comparison_status"] == "ready"
    assert ready_report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert ready_report["decision"]["next_priority_recommendation"] == "maintain_policy_comparison_confidence"
    assert ready_report["decision"]["next_priority_reason"] == "readiness requirements are currently satisfied"
    assert ready_taxonomy == {
        "ready_expected": 1,
        "pending_expected": 1,
        "ambiguous": 0,
    }


def test_observed_run_accumulation_progression_keeps_deferred_and_ready_states_stable(
    tmp_path: Path,
):
    mod = _load_module()

    def _collect_stage(
        *,
        valid_sample_count: int,
        valid_same_surface_count: int,
        include_second_policy_pair: bool,
        run_prefix: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        runs_root = tmp_path / ".omc" / "runs" / run_prefix
        for index in range(valid_sample_count):
            run_dir = runs_root / f"20260701T026{index:02d}-progress{index:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "result.json").write_text(
                json.dumps(
                    {
                        "task_id": "observed-collect",
                        "instruction": f"실제 observed progression request {index}",
                        "benchmark_source_type": "observed_output",
                        "policy_pair": (
                            "candidate->baseline"
                            if include_second_policy_pair and index == 1
                            else "baseline->candidate"
                        ),
                        "comparison_scope": (
                            "same_surface" if index < valid_same_surface_count else "cross_surface"
                        ),
                        "baseline_response_sample": "기존 응답 샘플",
                        "candidate_response_sample": "개선 응답 샘플",
                        "status": "completed",
                        "last_completed_step": "review",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        _write_observed_run(
            runs_root,
            "20260701T02690-neutral0001",
            {
                "task_id": "observed-collect",
                "instruction": "실제 observed progression neutral request",
                "benchmark_source_type": "observed_request",
                "policy_pair": "candidate->baseline",
                "status": "completed",
                "last_completed_step": "plan",
            },
        )
        _write_observed_run(
            runs_root,
            "20260701T02699-invalid0001",
            {
                "task_id": "observed-collect",
                "instruction": "실제 observed progression invalid request",
                "benchmark_source_type": "observed_output",
                "policy_pair": "candidate->baseline",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

        collected = mod.collect_observed_response_mode_cases(runs_root)
        report = mod.compare_response_modes(collected["cases"])
        return collected, report

    sample_collected, sample_report = _collect_stage(
        valid_sample_count=1,
        valid_same_surface_count=0,
        include_second_policy_pair=False,
        run_prefix="progress-sample",
    )
    same_surface_collected, same_surface_report = _collect_stage(
        valid_sample_count=20,
        valid_same_surface_count=0,
        include_second_policy_pair=True,
        run_prefix="progress-same-surface",
    )
    policy_pair_collected, policy_pair_report = _collect_stage(
        valid_sample_count=20,
        valid_same_surface_count=1,
        include_second_policy_pair=False,
        run_prefix="progress-policy-pair",
    )
    ready_collected, ready_report = _collect_stage(
        valid_sample_count=20,
        valid_same_surface_count=1,
        include_second_policy_pair=True,
        run_prefix="progress-ready",
    )

    assert sample_collected["summary"]["readiness_sample_gap"] == 19
    assert sample_collected["summary"]["baseline_comparison_ready"] is False
    assert sample_collected["summary"]["readiness_blocker_line"] == "pending: need more observed samples"
    assert sample_collected["summary"]["next_priority_recommendation"] == "collect_more_observed_runs"
    assert sample_collected["summary"]["next_priority_reason"] == "need more observed samples"
    assert sample_report["decision"]["baseline_comparison_status"] == "deferred"
    assert sample_report["decision"]["next_kpi_blocker"] == "insufficient_observed_samples"
    assert sample_report["decision"]["next_priority_recommendation"] == "collect_more_observed_runs"
    assert sample_report["decision"]["next_priority_reason"] == "need more observed samples"

    assert same_surface_collected["summary"]["readiness_sample_gap"] == 0
    assert same_surface_collected["summary"]["readiness_same_surface_gap"] == 1
    assert same_surface_collected["summary"]["baseline_comparison_ready"] is False
    assert same_surface_collected["summary"]["readiness_blocker_line"] == (
        "pending: need more same-surface evidence"
    )
    assert same_surface_collected["summary"]["next_priority_recommendation"] == "add_same_surface_observed_evidence"
    assert same_surface_collected["summary"]["next_priority_reason"] == "need more same-surface evidence"
    assert same_surface_report["decision"]["baseline_comparison_status"] == "deferred"
    assert same_surface_report["decision"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert same_surface_report["decision"]["next_priority_recommendation"] == "add_same_surface_observed_evidence"
    assert same_surface_report["decision"]["next_priority_reason"] == "need more same-surface evidence"

    assert policy_pair_collected["summary"]["readiness_same_surface_gap"] == 0
    assert policy_pair_collected["summary"]["readiness_distinct_policy_pair_count"] == 1
    assert policy_pair_collected["summary"]["baseline_comparison_ready"] is False
    assert policy_pair_collected["summary"]["readiness_blocker_line"] == (
        "pending: need more policy pair coverage"
    )
    assert policy_pair_collected["summary"]["next_priority_recommendation"] == "expand_policy_pair_coverage"
    assert policy_pair_collected["summary"]["next_priority_reason"] == "need more policy pair coverage"
    assert policy_pair_report["decision"]["baseline_comparison_status"] == "deferred"
    assert policy_pair_report["decision"]["next_kpi_blocker"] == "insufficient_policy_pairs"
    assert policy_pair_report["decision"]["next_priority_recommendation"] == "expand_policy_pair_coverage"
    assert policy_pair_report["decision"]["next_priority_reason"] == "need more policy pair coverage"

    assert ready_collected["summary"]["readiness_sample_gap"] == 0
    assert ready_collected["summary"]["readiness_same_surface_gap"] == 0
    assert ready_collected["summary"]["readiness_distinct_policy_pair_count"] == 2
    assert ready_collected["summary"]["baseline_comparison_ready"] is True
    assert ready_collected["summary"]["readiness_blocker_line"] == (
        "ready: baseline comparison wording can be enabled"
    )
    assert ready_collected["summary"]["next_priority_recommendation"] == "maintain_policy_comparison_confidence"
    assert ready_collected["summary"]["next_priority_reason"] == "readiness requirements are currently satisfied"
    assert ready_report["decision"]["baseline_comparison_status"] == "ready"
    assert ready_report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert ready_report["decision"]["next_priority_recommendation"] == "maintain_policy_comparison_confidence"
    assert ready_report["decision"]["next_priority_reason"] == "readiness requirements are currently satisfied"


def test_collected_observed_summary_exposes_multi_run_kpi_triplet(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T025{index:02d}-kpi{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_status = "completed" if index < 15 and not (5 <= index < 8) else "failed"
        steps = {
            "review": {
                "status": "completed" if run_status == "completed" else "failed",
                "cost_estimate": 0.01,
            }
        }
        if index < 8:
            steps["task_retry"] = {
                "status": "completed" if index < 5 else "failed",
                "reroute_target": "task_retry",
            }
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed kpi request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": run_status,
                    "last_completed_step": "review",
                    "steps": steps,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    summary = collected["summary"]

    assert summary["total_run_count"] == 20
    assert summary["reroute_rate"] == 0.4
    assert summary["retry_to_success_rate"] == 0.625
    assert summary["cost_per_successful_task"] == 0.01


def test_collected_observed_multi_run_kpis_are_carried_into_comparison_summary(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T026{index:02d}-carry{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        run_status = "completed" if index < 12 and not (3 <= index < 6) else "failed"
        steps = {
            "review": {
                "status": "completed" if run_status == "completed" else "failed",
                "cost_estimate": 0.02,
            }
        }
        if index < 6:
            steps["plan_retry"] = {
                "status": "completed" if index < 3 else "failed",
                "decision": "reroute",
                "reroute_target": "plan_retry",
            }
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed kpi carry request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": run_status,
                    "last_completed_step": "review",
                    "steps": steps,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert report["summary"]["total_run_count"] == 20
    assert report["summary"]["reroute_rate"] == 0.3
    assert report["summary"]["retry_to_success_rate"] == 0.5
    assert report["summary"]["cost_per_successful_task"] == 0.02


def test_neutral_observed_request_policy_pair_does_not_unlock_readiness(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T027{index:02d}-ready{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"실제 observed readiness request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T02800-neutral0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed neutral seed request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "candidate->baseline",
            "status": "completed",
            "last_completed_step": "plan",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["distinct_policy_pair_count"] == 2
    assert collected["summary"]["readiness_distinct_policy_pair_count"] == 1
    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more policy pair coverage"
    )
    assert collected["summary"]["next_priority_recommendation"] == "expand_policy_pair_coverage"
    assert collected["summary"]["next_priority_reason"] == "need more policy pair coverage"
    assert report["summary"]["readiness_blocker_line"] == "pending: need more policy pair coverage"
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_policy_pairs"


def test_ready_collected_summary_prefers_operator_validation_when_reason_signals_exist(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T028{index:02d}-ready{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"실제 observed ready request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T02900-signal0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed operator bottleneck request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "baseline->candidate",
            "status": "completed",
            "last_completed_step": "plan",
            "baseline_trace": ["assistant: task로 바로 진행", "user: 아니 plan만 봐달라"],
            "candidate_trace": ["assistant: plan 검토로 판단", "assistant: 사용자 선택 대기"],
            "baseline_output_chars": 320,
            "candidate_output_chars": 240,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_reason_signals_present"] is True
    assert collected["summary"]["baseline_comparison_ready"] is True
    assert collected["summary"]["next_priority_recommendation"] == (
        "validate_operator_bottlenecks_from_observed_runs"
    )
    assert collected["summary"]["next_priority_reason"] == (
        "reason signals observed in ready dataset"
    )
    assert report["decision"]["next_priority_recommendation"] == (
        "validate_operator_bottlenecks_from_observed_runs"
    )


def test_accumulated_observed_ready_reason_signal_keeps_summary_and_decision_aligned(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T028{index:02d}-accum{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"실제 accumulated observed ready request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T02900-accum-signal0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 accumulated observed operator bottleneck request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "baseline->candidate",
            "status": "completed",
            "last_completed_step": "plan",
            "baseline_trace": ["assistant: task로 바로 진행", "user: 아니 plan만 봐달라"],
            "candidate_trace": ["assistant: plan 검토로 판단", "assistant: 사용자 선택 대기"],
            "baseline_output_chars": 320,
            "candidate_output_chars": 240,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_reason_signals_present"] is True
    assert collected["summary"]["reason_signal_summary_line"] == (
        "reason signals present: operator validation evidence observed"
    )
    assert collected["summary"]["next_priority_recommendation"] == (
        "validate_operator_bottlenecks_from_observed_runs"
    )
    assert collected["summary"]["next_priority_reason"] == (
        "reason signals observed in ready dataset"
    )
    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; reason signals observed"
    )
    assert report["decision"]["next_priority_recommendation"] == (
        "validate_operator_bottlenecks_from_observed_runs"
    )
    assert report["decision"]["next_priority_reason"] == (
        "reason signals observed in ready dataset"
    )


def test_observed_request_without_reason_signal_does_not_flip_ready_next_priority(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T0291{index:02d}-no-signal{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"실제 observed ready request without signal {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T02999-no-signal0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed request지만 reroute/과출력 신호는 없는 케이스",
            "benchmark_source_type": "observed_request",
            "policy_pair": "baseline->candidate",
            "status": "completed",
            "last_completed_step": "plan",
            "baseline_trace": ["assistant: plan 검토를 제안", "user: 확인"],
            "candidate_trace": ["assistant: plan 검토를 제안", "assistant: 사용자 선택 대기"],
            "baseline_output_chars": 240,
            "candidate_output_chars": 240,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_reason_signals_present"] is False
    assert collected["summary"]["reason_signal_summary_line"] == "reason signals present: none"
    assert collected["summary"]["next_priority_recommendation"] == (
        "maintain_policy_comparison_confidence"
    )
    assert collected["summary"]["next_priority_reason"] == (
        "readiness requirements are currently satisfied"
    )
    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["next_priority_recommendation"] == (
        "maintain_policy_comparison_confidence"
    )
    assert report["decision"]["next_priority_reason"] == (
        "readiness requirements are currently satisfied"
    )


def test_reason_signal_does_not_override_baseline_not_ready_priority(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T0292{index:02d}-baseline-gap{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"실제 observed baseline gap request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T02929-baseline-drift0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed request인데 baseline comparison은 아직 not ready",
            "benchmark_source_type": "observed_request",
            "policy_pair": "baseline->candidate",
            "status": "completed",
            "last_completed_step": "plan",
            "baseline_trace": ["assistant: task로 바로 진행", "user: 아니 plan만 봐달라"],
            "candidate_trace": ["assistant: plan 검토로 판단", "assistant: 사용자 선택 대기"],
            "baseline_output_chars": 320,
            "candidate_output_chars": 240,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])
    report["summary"]["baseline_comparison_ready"] = False
    decision = mod._decision_from_summary(report["summary"])

    assert collected["summary"]["observed_reason_signals_present"] is True
    assert decision["baseline_comparison_status"] == "deferred"
    assert decision["next_priority_recommendation"] == (
        "stabilize_baseline_comparison_inputs"
    )
    assert decision["next_priority_reason"] == (
        "baseline comparison input is not ready"
    )


def test_collected_cases_preserve_rejection_bottleneck_for_policy_pair_shortage(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T029{index:02d}-policy{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"실제 observed policy shortage request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T02999-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "실제 observed invalid request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "candidate->baseline",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more policy pair coverage; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert report["decision"]["next_kpi_blocker"] == "insufficient_policy_pairs"
    assert report["decision"]["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: need more policy pair coverage; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )


def test_collected_observed_pending_summary_matches_comparison_pending_summary(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T030{index:02d}-bridge{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed pending request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more same-surface evidence"
    )
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert report["decision"]["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: need more same-surface evidence"
    )


def test_collected_observed_pending_summary_matches_policy_pair_pending_summary(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T031{index:02d}-bridge{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"실제 observed pair pending request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate",
                    "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more policy pair coverage"
    )
    assert report["summary"]["readiness_blocker_line"] == "pending: need more policy pair coverage"
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_policy_pairs"
    assert report["decision"]["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: need more policy pair coverage"
    )


def test_collect_observed_response_mode_cases_reports_fixture_taxonomy_counts(tmp_path: Path):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T040{index:02d}-taxonomy{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        comparison_scope = "same_surface" if index < 2 else "cross_surface"
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"taxonomy request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                    "comparison_scope": comparison_scope,
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    payload = mod.collect_observed_response_mode_cases(runs_root)

    assert payload["summary"]["fixture_taxonomy_counts"] == {
        "ready_expected": 1,
        "pending_expected": 0,
        "ambiguous": 1,
    }


def test_same_surface_transition_keeps_summary_report_and_taxonomy_aligned(tmp_path: Path) -> None:
    mod = _load_module()

    def _collect_cases(*, same_surface_count: int, run_prefix: str) -> tuple[dict[str, object], list[dict[str, object]]]:
        runs_root = tmp_path / ".omc" / "runs" / run_prefix
        for index in range(20):
            run_dir = runs_root / f"20260701T05{index:02d}-transition{index:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "result.json").write_text(
                json.dumps(
                    {
                        "task_id": "observed-collect",
                        "instruction": f"same-surface transition request {index}",
                        "benchmark_source_type": "observed_output",
                        "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                        "comparison_scope": "same_surface" if index < same_surface_count else "cross_surface",
                        "baseline_response_sample": "기존 응답 샘플",
                        "candidate_response_sample": "개선 응답 샘플",
                        "status": "completed",
                        "last_completed_step": "review",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        collected = mod.collect_observed_response_mode_cases(runs_root)
        return collected, collected["cases"]

    zero_collected, zero_cases = _collect_cases(same_surface_count=0, run_prefix="zero")
    zero_report = mod.compare_response_modes(zero_cases)
    one_collected, one_cases = _collect_cases(same_surface_count=1, run_prefix="one")
    one_report = mod.compare_response_modes(one_cases)
    one_taxonomy = mod._fixture_taxonomy_counts_from_readiness(one_cases)
    two_collected, two_cases = _collect_cases(same_surface_count=2, run_prefix="two")
    two_report = mod.compare_response_modes(two_cases)
    two_taxonomy = mod._fixture_taxonomy_counts_from_readiness(two_cases)

    assert zero_collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: need more same-surface evidence"
    )
    assert zero_collected["summary"]["readiness_blocker_line"] == (
        "pending: need more same-surface evidence"
    )
    assert zero_collected["summary"]["next_priority_recommendation"] == "add_same_surface_observed_evidence"
    assert zero_collected["summary"]["next_priority_reason"] == "need more same-surface evidence"
    assert zero_report["decision"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert zero_report["decision"]["baseline_comparison_status"] == "deferred"

    assert one_collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready"
    )
    assert one_collected["summary"]["readiness_blocker_line"] == (
        "ready: baseline comparison wording can be enabled"
    )
    assert one_collected["summary"]["next_priority_recommendation"] == "maintain_policy_comparison_confidence"
    assert one_collected["summary"]["next_priority_reason"] == "readiness requirements are currently satisfied"
    assert one_report["decision"]["baseline_comparison_status"] == "ready"
    assert one_report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled"
    )
    assert one_taxonomy == {
        "ready_expected": 1,
        "pending_expected": 1,
        "ambiguous": 0,
    }

    assert two_collected["summary"]["observed_data_bottleneck_summary"] == (
        "observed data bottleneck: baseline comparison input is ready"
    )
    assert two_collected["summary"]["readiness_blocker_line"] == (
        "ready: baseline comparison wording can be enabled"
    )
    assert two_report["decision"]["baseline_comparison_status"] == "ready"
    assert two_taxonomy == {
        "ready_expected": 1,
        "pending_expected": 0,
        "ambiguous": 1,
    }


def test_valid_same_surface_evidence_stays_ready_even_with_additional_invalid_same_surface_noise(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T0293{index:02d}-stable-same-surface{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"stable same-surface request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T02939-invalid-same-surface0001",
        {
            "task_id": "observed-collect",
            "instruction": "invalid same-surface noise request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "candidate->baseline",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["readiness_same_surface_gap"] == 0
    assert collected["summary"]["baseline_comparison_ready"] is True
    assert collected["summary"]["baseline_comparison_status"] == "ready"
    assert collected["summary"]["next_kpi_blocker"] == "none"
    assert collected["summary"]["rejected_observed_output_case_count"] == 1
    assert collected["summary"]["rejected_observed_output_reasons"] == {
        "missing_candidate_response_sample": 1
    }
    assert collected["summary"]["next_priority_recommendation"] == (
        "maintain_policy_comparison_confidence"
    )
    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["next_priority_recommendation"] == (
        "maintain_policy_comparison_confidence"
    )
    assert report["summary"]["rejected_observed_output_case_count"] == 1
    assert report["summary"]["rejected_observed_output_reasons"] == {
        "missing_candidate_response_sample": 1
    }
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )


def test_invalid_same_surface_noise_does_not_hide_same_surface_gap(tmp_path: Path) -> None:
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T0393{index:02d}-cross-surface-only{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"cross surface only request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T039399-invalid-same-surface-noise0001",
        {
            "task_id": "observed-collect",
            "instruction": "invalid same-surface only noise request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "candidate->baseline",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["same_surface_case_count"] == 0
    assert collected["summary"]["readiness_same_surface_case_count"] == 0
    assert collected["summary"]["readiness_same_surface_gap"] == 1
    assert collected["summary"]["baseline_comparison_status"] == "deferred"
    assert collected["summary"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert collected["summary"]["next_priority_recommendation"] == "add_same_surface_observed_evidence"
    assert collected["summary"]["rejected_observed_output_case_count"] == 1
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["next_priority_recommendation"] == "add_same_surface_observed_evidence"


def test_collected_summary_exposes_baseline_comparison_line_for_same_surface_gap(
    tmp_path: Path,
) -> None:
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T0493{index:02d}-collected-gap{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"collected baseline line request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    collected = mod.collect_observed_response_mode_cases(runs_root)

    assert collected["summary"]["baseline_comparison_status"] == "deferred"
    assert collected["summary"]["baseline_comparison_line"] == (
        "baseline comparison deferred: need more same-surface evidence"
    )


def test_collect_observed_response_mode_cases_preserves_explicit_multi_reason_rejection_metadata(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T031{index:02d}-explicit-ready{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"explicit rejection ready request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T03199-explicit-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "explicit rejection invalid observed output",
            "dataset_rejected_observed_output_case_count": 1,
            "dataset_rejected_observed_output_reasons": {
                "missing_comparison_scope": 1,
                "missing_baseline_response_sample": 1,
                "missing_candidate_response_sample": 1,
            },
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["rejected_observed_output_case_count"] == 1
    assert collected["summary"]["rejected_observed_output_reasons"] == {
        "missing_comparison_scope": 1,
        "missing_baseline_response_sample": 1,
        "missing_candidate_response_sample": 1,
    }
    assert report["summary"]["rejected_observed_output_case_count"] == 1
    assert report["summary"]["rejected_observed_output_reasons"] == {
        "missing_comparison_scope": 1,
        "missing_baseline_response_sample": 1,
        "missing_candidate_response_sample": 1,
    }


def test_collected_summary_surfaces_pending_blocker_and_status_for_operational_observed_checks(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        _write_observed_run(
            runs_root,
            f"20260701T0295{index:02d}-policy-pair-pending{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"policy pair pending request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    _write_observed_run(
        runs_root,
        "20260701T029599-reason-signal0001",
        {
            "task_id": "observed-collect",
            "instruction": "policy pair pending reason signal request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "candidate->baseline",
            "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 500,
            "candidate_output_chars": 200,
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["baseline_comparison_ready"] is False
    assert collected["summary"]["baseline_comparison_status"] == "deferred"
    assert collected["summary"]["next_kpi_blocker"] == "insufficient_policy_pairs"
    assert collected["summary"]["next_priority_recommendation"] == "expand_policy_pair_coverage"
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_policy_pairs"


def test_collected_summary_surfaces_sample_gap_blocker_for_operational_observed_checks(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(19):
        _write_observed_run(
            runs_root,
            f"20260701T0296{index:02d}-sample-gap{index:04d}",
            {
                "task_id": "observed-collect",
                "instruction": f"sample gap request {index}",
                "benchmark_source_type": "observed_output",
                "policy_pair": "baseline->candidate" if index < 10 else "candidate->baseline",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
                "status": "completed",
                "last_completed_step": "review",
            },
        )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_modes(collected["cases"])

    assert collected["summary"]["readiness_sample_gap"] == 1
    assert collected["summary"]["baseline_comparison_ready"] is False
    assert collected["summary"]["baseline_comparison_status"] == "deferred"
    assert collected["summary"]["readiness_blocker_line"] == "pending: need more observed samples"
    assert collected["summary"]["next_kpi_blocker"] == "insufficient_observed_samples"
    assert collected["summary"]["next_priority_recommendation"] == "collect_more_observed_runs"
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_observed_samples"
    assert report["decision"]["next_priority_recommendation"] == "collect_more_observed_runs"


def test_compare_response_mode_threshold_candidates_reports_false_ready_and_pending_counts():
    mod = _load_module()

    cases = []
    for index in range(20):
        cases.append(
            {
                "request": f"리뷰해줘 observed {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline" if index < 10 else "candidate",
                "candidate_policy": "candidate" if index < 10 else "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 280,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
            }
        )

    report = mod.compare_response_mode_threshold_candidates(
        cases,
        thresholds=[
            {"label": "current", "min_samples": 20, "min_same_surface": 1, "min_policy_pairs": 2},
            {"label": "stricter_same_surface", "min_samples": 20, "min_same_surface": 2, "min_policy_pairs": 2},
            {"label": "looser_samples", "min_samples": 10, "min_same_surface": 1, "min_policy_pairs": 2},
        ],
        fixture_taxonomy={"ready_expected": 1, "pending_expected": 0, "ambiguous": 0},
    )

    assert report["candidates"][0]["label"] == "current"
    assert report["candidates"][0]["baseline_comparison_ready"] is True
    assert report["candidates"][0]["false_ready_count"] == 0
    assert report["candidates"][0]["false_pending_count"] == 0
    assert report["candidates"][1]["baseline_comparison_ready"] is False
    assert report["candidates"][1]["false_pending_count"] == 1
    assert report["candidates"][2]["baseline_comparison_ready"] is True
    assert report["candidates"][2]["false_ready_count"] == 0


def test_compare_response_mode_threshold_candidates_preserves_taxonomy_counts():
    mod = _load_module()

    cases = []
    for index in range(10):
        cases.append(
            {
                "request": f"리뷰해줘 observed {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline" if index < 5 else "candidate",
                "candidate_policy": "candidate" if index < 5 else "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 280,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                "baseline_response_sample": "기존 응답 샘플",
                "candidate_response_sample": "개선 응답 샘플",
            }
        )

    report = mod.compare_response_mode_threshold_candidates(
        cases,
        thresholds=[
            {"label": "current", "min_samples": 20, "min_same_surface": 1, "min_policy_pairs": 2},
            {"label": "looser_samples", "min_samples": 10, "min_same_surface": 1, "min_policy_pairs": 2},
        ],
        fixture_taxonomy={"ready_expected": 0, "pending_expected": 3, "ambiguous": 0},
    )

    assert report["candidates"][0]["baseline_comparison_ready"] is False
    assert report["candidates"][0]["false_pending_count"] == 0
    assert report["candidates"][1]["baseline_comparison_ready"] is True
    assert report["candidates"][1]["false_ready_count"] == 3


def test_observed_progression_policy_pair_pending_fixture_flags_loose_threshold_as_false_ready(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T027{index:02d}-candidate{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"observed candidate progression request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate",
                    "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    _write_observed_run(
        runs_root,
        "20260701T02790-neutral0001",
        {
            "task_id": "observed-collect",
            "instruction": "observed candidate progression neutral request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "candidate->baseline",
            "status": "completed",
            "last_completed_step": "plan",
        },
    )
    _write_observed_run(
        runs_root,
        "20260701T02799-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "observed candidate progression invalid request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "candidate->baseline",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    report = mod.compare_response_mode_threshold_candidates(
        collected["cases"],
        thresholds=[
            {"label": "current", "min_samples": 20, "min_same_surface": 1, "min_policy_pairs": 2},
            {"label": "loose_policy_pairs", "min_samples": 20, "min_same_surface": 1, "min_policy_pairs": 1},
        ],
        fixture_taxonomy={"ready_expected": 0, "pending_expected": 1, "ambiguous": 0},
    )

    assert collected["summary"]["readiness_same_surface_gap"] == 0
    assert collected["summary"]["readiness_distinct_policy_pair_count"] == 1
    assert report["candidates"][0]["label"] == "current"
    assert report["candidates"][0]["baseline_comparison_ready"] is False
    assert report["candidates"][0]["false_ready_count"] == 0
    assert report["candidates"][1]["label"] == "loose_policy_pairs"
    assert report["candidates"][1]["baseline_comparison_ready"] is True
    assert report["candidates"][1]["false_ready_count"] == 1


def test_observed_progression_report_surface_aligns_with_threshold_candidate_summary(
    tmp_path: Path,
):
    mod = _load_module()

    runs_root = tmp_path / ".omc" / "runs"
    for index in range(20):
        run_dir = runs_root / f"20260701T028{index:02d}-surface{index:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_id": "observed-collect",
                    "instruction": f"observed report surface request {index}",
                    "benchmark_source_type": "observed_output",
                    "policy_pair": "baseline->candidate",
                    "comparison_scope": "same_surface" if index == 0 else "cross_surface",
                    "baseline_response_sample": "기존 응답 샘플",
                    "candidate_response_sample": "개선 응답 샘플",
                    "status": "completed",
                    "last_completed_step": "review",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    _write_observed_run(
        runs_root,
        "20260701T02890-neutral0001",
        {
            "task_id": "observed-collect",
            "instruction": "observed report surface neutral request",
            "benchmark_source_type": "observed_request",
            "policy_pair": "candidate->baseline",
            "status": "completed",
            "last_completed_step": "plan",
        },
    )
    _write_observed_run(
        runs_root,
        "20260701T02899-invalid0001",
        {
            "task_id": "observed-collect",
            "instruction": "observed report surface invalid request",
            "benchmark_source_type": "observed_output",
            "policy_pair": "candidate->baseline",
            "comparison_scope": "same_surface",
            "baseline_response_sample": "기존 응답 샘플",
            "status": "completed",
            "last_completed_step": "review",
        },
    )

    collected = mod.collect_observed_response_mode_cases(runs_root)
    compared = mod.compare_response_modes(collected["cases"])
    candidates = mod.compare_response_mode_threshold_candidates(
        collected["cases"],
        thresholds=[
            {"label": "current", "min_samples": 20, "min_same_surface": 1, "min_policy_pairs": 2},
            {"label": "loose_policy_pairs", "min_samples": 20, "min_same_surface": 1, "min_policy_pairs": 1},
        ],
        fixture_taxonomy={"ready_expected": 0, "pending_expected": 1, "ambiguous": 0},
    )

    assert compared["summary"]["readiness_blocker_line"] == "pending: need more policy pair coverage"
    assert compared["decision"]["baseline_comparison_status"] == "deferred"
    assert compared["decision"]["baseline_comparison_line"] == (
        "baseline comparison deferred: need more policy pair coverage"
    )
    assert compared["decision"]["policy_comparison_summary"] == (
        "policy comparison pending: need more policy pair coverage; rejected observed_output=1 "
        "(missing_candidate_response_sample:1)"
    )
    assert candidates["candidates"][0]["label"] == "current"
    assert candidates["candidates"][0]["baseline_comparison_ready"] is False
    assert candidates["candidates"][1]["label"] == "loose_policy_pairs"
    assert candidates["candidates"][1]["baseline_comparison_ready"] is True
    assert candidates["candidates"][1]["false_ready_count"] == 1


def test_compare_response_modes_reports_incomplete_next_action_cases():
    mod = _load_module()

    cases = [
        {
            "request": "리뷰해줘",
            "expected_mode": "review-first",
            "expected_next_action": "$omc-ship",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 리뷰 시작"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 280,
            "candidate_output_chars": 285,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
            "baseline_next_action": "$omc-task",
        }
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["next_action_case_count"] == 0
    assert report["summary"]["next_action_incomplete_case_count"] == 1


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

    assert len(cases) >= 28, "response mode fixture should include at least 28 cases"

    expected_modes = {case["expected_mode"] for case in cases}
    assert expected_modes == {"answer-first", "execute-first", "review-first"}

    mode_counts: dict[str, int] = {}
    requests = {case["request"] for case in cases}
    observed_request_cases = []
    observed_output_cases = []
    cases_with_next_action = []
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
        if "expected_next_action" in case:
            cases_with_next_action.append(case)
            assert isinstance(case["expected_next_action"], str) and case["expected_next_action"].strip()
            assert isinstance(case.get("baseline_next_action"), str) and case["baseline_next_action"].strip()
            assert isinstance(case.get("candidate_next_action"), str) and case["candidate_next_action"].strip()

    assert all(count >= 3 for count in mode_counts.values())
    assert len(observed_request_cases) >= 15
    assert len(observed_output_cases) >= 2
    assert len(cases_with_next_action) >= 16
    assert "이 버그 원인 먼저 보고 바로 고칠 수 있으면 수정해줘" in requests
    assert "이 변경 위험한지 먼저 리뷰해주고, 괜찮으면 그다음 커밋까지 해줘" in requests
    assert "이 기능 해야 할지 판단하고 진행 순서만 정리해줘" in requests
    assert "OMC orchestration layer 1단계와 response_mode benchmark 변경 리뷰" in requests
    assert "OMC orchestration 다음 개선 계획 수립" in requests
    assert "현재 로드맵 최신화하고 다음 작업 체크" in requests
    assert "현재 어떤 작업 진행됐는지 상태만 빠르게 알려줘" in requests
    assert "오랜만에 해당 프로젝트에 돌아와서 어떤 프로젝트인지 파악하고 분석하는 느낌인데" in requests
    assert "복귀용 프로젝트 reentry 스킬 재설계 정보원 우선순위와 출력 계약 고정" in requests
    assert "현재 어떤점이 개선된거야" in requests
    assert "현재 git changes 변경 상태 리뷰 보고 정말 괜찮은 변경인지 체크하려고 하는데 무슨 스킬 써야해" in requests
    assert "fugu 문서 2개 먼저 커밋 태스크 2부터 $omc-task" in requests
    assert "다음 1순위 작업을 더 잘게 쪼개서" in requests


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


def test_observed_output_cases_do_not_affect_mode_or_task_delay_checks():
    mod = _load_module()

    cases = [
        {
            "request": "이거 클로드코드로 실행한건데 이거 제대로 진행된 거 맞아? plan",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 구현 방식 제시"],
            "candidate_trace": ["assistant: 판정과 다음 액션을 분리해 멈춤"],
            "baseline_output_chars": 438,
            "candidate_output_chars": 214,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
            "source_type": "observed_output",
            "comparison_scope": "same_surface",
            "evidence": "real observed output",
            "baseline_response_sample": "구현 방식부터 길게 제시하고 다음 스킬 추천이 없음",
            "candidate_response_sample": "판정과 다음 액션을 분리해 멈춤",
        },
        {
            "request": "로그인 버튼 컴포넌트 구현해줘",
            "expected_mode": "execute-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 구현 시작"],
            "candidate_trace": ["assistant: 답변만 제공"],
            "baseline_output_chars": 100,
            "candidate_output_chars": 100,
            "baseline_task_start_delay": 2,
            "candidate_task_start_delay": 3,
            "source_type": "synthetic",
        },
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["mode_accuracy_delta"] == 1.0
    assert report["summary"]["candidate_task_start_delay_delta"] == 1
    assert report["decision"]["checks"]["mode_accuracy_up"] is True
    assert report["decision"]["checks"]["task_start_delay_not_worse"] is False


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


def test_compare_response_modes_reports_kpi_readiness_and_policy_pairs():
    mod = _load_module()

    cases = [
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

    report = mod.compare_response_modes(cases)

    assert report["summary"]["sample_case_count"] == 1
    assert report["summary"]["observed_sample_case_count"] == 0
    assert report["summary"]["sample_requirement_met"] is False
    assert report["summary"]["distinct_policy_count"] == 2
    assert report["summary"]["distinct_policy_pair_count"] == 1
    assert report["summary"]["policy_requirement_met"] is False
    assert report["summary"]["policy_pair_counts"] == {"baseline->candidate": 1}
    assert report["summary"]["primary_policy_pair"] == "baseline->candidate"
    assert report["decision"]["kpi_readiness"] == "incomplete"


def test_compare_response_modes_reports_readiness_status_line_and_blocker():
    mod = _load_module()

    case = {
        "request": "리뷰해줘",
        "expected_mode": "review-first",
        "baseline_policy": "baseline",
        "candidate_policy": "candidate",
        "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
        "candidate_trace": ["assistant: 리뷰 시작"],
        "baseline_output_chars": 300,
        "candidate_output_chars": 220,
        "baseline_task_start_delay": 2,
        "candidate_task_start_delay": 1,
        "source_type": "observed_output",
        "comparison_scope": "cross_surface",
        "baseline_response_sample": "요약만 제공",
        "candidate_response_sample": "리뷰 구조로 응답",
    }

    report = mod.compare_response_modes([case])

    assert report["summary"]["readiness_sample_gap"] == 19
    assert report["summary"]["readiness_same_surface_gap"] == 1
    assert report["summary"]["baseline_comparison_ready"] is False
    assert report["summary"]["readiness_blocker_line"] == "pending: need more observed samples"
    assert report["decision"]["readiness_status_line"] == "not ready: samples 1/20, same-surface 0/1, policy pairs 1/2"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_observed_samples"
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["baseline_comparison_line"] == "baseline comparison deferred: need more observed samples"
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison pending: need more observed samples"
    )
    assert report["decision"]["next_priority_recommendation"] == "collect_more_observed_runs"
    assert report["decision"]["next_priority_reason"] == "need more observed samples"


def test_compare_response_modes_marks_kpi_ready_at_twenty_samples():
    mod = _load_module()

    cases = [
        {
            "request": f"리뷰해줘 {index}",
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
        for index in range(20)
    ]

    report = mod.compare_response_modes(cases)

    assert report["summary"]["sample_case_count"] == 20
    assert report["summary"]["observed_sample_case_count"] == 0
    assert report["summary"]["sample_requirement_met"] is False
    assert report["summary"]["primary_policy_pair"] == "baseline->candidate"
    assert report["decision"]["kpi_readiness"] == "incomplete"


def test_compare_response_modes_defers_baseline_comparison_when_only_observed_requests_exist():
    mod = _load_module()

    cases = []
    for index in range(10):
        cases.append(
            {
                "request": f"리뷰해줘 observed {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline",
                "candidate_policy": "candidate",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 315,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_request",
                "evidence": f"session-{index}",
            }
        )
        cases.append(
            {
                "request": f"구현해줘 observed {index}",
                "expected_mode": "execute-first",
                "baseline_policy": "candidate",
                "candidate_policy": "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 구현해줘"],
                "candidate_trace": ["assistant: 구현 시작"],
                "baseline_output_chars": 280,
                "candidate_output_chars": 260,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_request",
                "evidence": f"session-exec-{index}",
            }
        )

    report = mod.compare_response_modes(cases)

    assert report["summary"]["sample_case_count"] == 20
    assert report["summary"]["observed_sample_case_count"] == 20
    assert report["summary"]["sample_requirement_met"] is True
    assert report["summary"]["distinct_policy_pair_count"] == 2
    assert report["summary"]["policy_requirement_met"] is True
    assert report["summary"]["baseline_comparison_ready"] is False
    assert report["summary"]["readiness_blocker_line"] == "pending: need more same-surface evidence"
    assert report["decision"]["kpi_readiness"] == "incomplete"
    assert report["decision"]["readiness_status_line"] == "not ready: samples 20/20, same-surface 0/1, policy pairs 2/2"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert report["decision"]["baseline_comparison_status"] == "deferred"
    assert report["decision"]["baseline_comparison_line"] == "baseline comparison deferred: need more same-surface evidence"


def test_compare_response_modes_marks_kpi_incomplete_when_same_surface_evidence_is_missing():
    mod = _load_module()

    cases = []
    for index in range(20):
        cases.append(
            {
                "request": f"리뷰해줘 {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline" if index < 10 else "candidate",
                "candidate_policy": "candidate" if index < 10 else "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 220,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "cross_surface",
                "baseline_response_sample": "요약만 제공",
                "candidate_response_sample": "리뷰 구조로 응답",
            }
        )

    report = mod.compare_response_modes(cases)

    assert report["summary"]["sample_requirement_met"] is True
    assert report["summary"]["policy_requirement_met"] is True
    assert report["summary"]["readiness_same_surface_case_count"] == 0
    assert report["summary"]["readiness_same_surface_gap"] == 1
    assert report["summary"]["readiness_blocker_line"] == "pending: need more same-surface evidence"
    assert report["decision"]["kpi_readiness"] == "incomplete"
    assert report["decision"]["readiness_status_line"] == "not ready: samples 20/20, same-surface 0/1, policy pairs 2/2"
    assert report["decision"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert report["decision"]["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: need more same-surface evidence"
    )


def test_compare_response_modes_reports_rejection_reason_in_bottleneck_summary():
    mod = _load_module()

    cases = []
    for index in range(20):
        cases.append(
            {
                "request": f"리뷰해줘 {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline" if index < 10 else "candidate",
                "candidate_policy": "candidate" if index < 10 else "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 220,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "cross_surface",
                "baseline_response_sample": "요약만 제공",
                "candidate_response_sample": "리뷰 구조로 응답",
                "dataset_rejected_observed_output_case_count": 2,
                "dataset_rejected_observed_output_reasons": {
                    "missing_candidate_response_sample": 2,
                },
            }
        )

    report = mod.compare_response_modes(cases)

    assert report["decision"]["policy_comparison_bottleneck_summary"] == (
        "policy comparison bottleneck: need more same-surface evidence; rejected observed_output=2 "
        "(missing_candidate_response_sample:2)"
    )


def test_compare_response_modes_reports_policy_comparison_summary_when_ready():
    mod = _load_module()

    cases = []
    for index in range(20):
        cases.append(
            {
                "request": f"리뷰해줘 same-surface {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline" if index < 10 else "candidate",
                "candidate_policy": "candidate" if index < 10 else "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 220,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "요약만 제공",
                "candidate_response_sample": "리뷰 구조로 응답",
            }
        )

    report = mod.compare_response_modes(cases)

    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison ready: baseline comparison wording can be enabled"
    )


def test_compare_response_modes_policy_comparison_summary_mentions_reason_signal_presence():
    mod = _load_module()

    cases = [
        {
            "request": "이거 plan 검토만 하려던 건데 왜 바로 task로 가",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": [
                "assistant: task로 바로 진행하자고 응답",
                "user: 아니 plan 검토만 하려던 거야",
            ],
            "candidate_trace": [
                "assistant: 설명 요청으로 판단",
                "assistant: 사용자 선택 대기로 멈춤",
            ],
            "baseline_output_chars": 320,
            "candidate_output_chars": 260,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
        },
        {
            "request": "task, critique는 안고쳐도 된다고?",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 장문 설명"],
            "candidate_trace": ["assistant: 압축된 설명"],
            "baseline_output_chars": 610,
            "candidate_output_chars": 320,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
        },
    ]

    report = mod.compare_response_modes(cases)

    assert report["decision"]["policy_comparison_summary"] == (
        "policy comparison pending: need more observed samples; reason signals observed"
    )
    assert report["decision"]["next_priority_recommendation"] == "collect_more_observed_runs"
    assert report["decision"]["next_priority_reason"] == "need more observed samples"


def test_compare_response_modes_recommends_same_surface_validation_when_only_same_surface_is_missing():
    mod = _load_module()

    cases = []
    for index in range(20):
        cases.append(
            {
                "request": f"리뷰해줘 cross-surface {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline" if index < 10 else "candidate",
                "candidate_policy": "candidate" if index < 10 else "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 220,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "cross_surface",
                "baseline_response_sample": "요약만 제공",
                "candidate_response_sample": "리뷰 구조로 응답",
            }
        )

    report = mod.compare_response_modes(cases)

    assert report["decision"]["next_kpi_blocker"] == "insufficient_same_surface_evidence"
    assert report["decision"]["next_priority_recommendation"] == "add_same_surface_observed_evidence"
    assert report["decision"]["next_priority_reason"] == "need more same-surface evidence"


def test_compare_response_modes_recommends_operator_bottleneck_validation_when_ready_with_reason_signals():
    mod = _load_module()

    cases = []
    for index in range(20):
        cases.append(
            {
                "request": f"리뷰해줘 same-surface {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline" if index < 10 else "candidate",
                "candidate_policy": "candidate" if index < 10 else "baseline",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 220,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "요약만 제공",
                "candidate_response_sample": "리뷰 구조로 응답",
            }
        )
    cases.append(
        {
            "request": "이거 plan 검토만 하려던 건데 왜 바로 task로 가",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": [
                "assistant: task로 바로 진행하자고 응답",
                "user: 아니 plan 검토만 하려던 거야",
            ],
            "candidate_trace": [
                "assistant: 설명 요청으로 판단",
                "assistant: 사용자 선택 대기로 멈춤",
            ],
            "baseline_output_chars": 320,
            "candidate_output_chars": 260,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
        }
    )

    report = mod.compare_response_modes(cases)

    assert report["decision"]["baseline_comparison_status"] == "ready"
    assert report["decision"]["next_priority_recommendation"] == "validate_operator_bottlenecks_from_observed_runs"
    assert report["decision"]["next_priority_reason"] == "reason signals observed in ready dataset"


def test_compare_response_modes_recommends_policy_pair_expansion_when_policy_pairs_are_missing():
    mod = _load_module()

    cases = []
    for index in range(20):
        cases.append(
            {
                "request": f"리뷰해줘 same-surface single-pair {index}",
                "expected_mode": "review-first",
                "baseline_policy": "baseline",
                "candidate_policy": "candidate",
                "baseline_trace": ["assistant: 설명만 제공", "user: 아니 리뷰해줘"],
                "candidate_trace": ["assistant: 리뷰 시작"],
                "baseline_output_chars": 300,
                "candidate_output_chars": 220,
                "baseline_task_start_delay": 2,
                "candidate_task_start_delay": 1,
                "source_type": "observed_output",
                "comparison_scope": "same_surface",
                "baseline_response_sample": "요약만 제공",
                "candidate_response_sample": "리뷰 구조로 응답",
            }
        )

    report = mod.compare_response_modes(cases)

    assert report["decision"]["next_kpi_blocker"] == "insufficient_policy_pairs"
    assert report["decision"]["next_priority_recommendation"] == "expand_policy_pair_coverage"
    assert report["decision"]["next_priority_reason"] == "need more policy pair coverage"


def test_compare_response_modes_recommends_input_stabilization_when_baseline_flag_drifts_after_thresholds():
    mod = _load_module()

    summary = {
        "mode_accuracy_delta": 0.2,
        "reroute_rate_delta": -0.4,
        "candidate_task_start_delay_delta": 0,
        "candidate_output_chars_avg": 220,
        "baseline_output_chars_avg": 300,
        "candidate_output_chars_delta": -80,
        "next_action_case_count": 0,
        "observed_output_count": 20,
        "observed_same_surface_count": 1,
        "readiness_observed_sample_count": 20,
        "readiness_same_surface_case_count": 1,
        "readiness_distinct_policy_pair_count": 2,
        "baseline_comparison_ready": False,
        "distinct_policy_pair_count": 2,
        "rejected_observed_output_case_count": 0,
        "rejected_observed_output_reasons": {},
        "observed_reason_signals_present": False,
    }

    decision = mod._decision_from_summary(summary)

    assert decision["next_kpi_blocker"] == "baseline_comparison_not_ready"
    assert decision["next_priority_recommendation"] == "stabilize_baseline_comparison_inputs"
    assert decision["next_priority_reason"] == "baseline comparison input is not ready"


def test_collected_summary_recommends_input_stabilization_when_baseline_ready_flag_drifts():
    mod = _load_module()

    summary = {
        "readiness_observed_sample_count": 20,
        "readiness_same_surface_case_count": 1,
        "readiness_distinct_policy_pair_count": 2,
        "baseline_comparison_ready": False,
        "observed_reason_signals_present": True,
        "rejected_observed_output_case_count": 0,
        "rejected_observed_output_reasons": {},
    }

    blocker, blocker_line = mod._resolve_readiness_blocker(
        sample_gap=0,
        same_surface_gap=0,
        policy_pair_count=2,
        baseline_comparison_ready=False,
    )
    next_priority_recommendation, next_priority_reason = mod._resolve_next_priority(
        blocker=blocker,
        observed_reason_signals_present=True,
        baseline_comparison_status="deferred",
    )

    assert blocker == "baseline_comparison_not_ready"
    assert blocker_line == "pending: baseline comparison input is not ready"
    assert next_priority_recommendation == "stabilize_baseline_comparison_inputs"
    assert next_priority_reason == "baseline comparison input is not ready"


def test_response_mode_fixture_observed_request_case_affects_next_action_accuracy():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(case for case in cases if case["request"] == "다음 1순위 작업을 더 잘게 쪼개서")

    report = mod.compare_response_modes([target_case])

    assert report["summary"]["next_action_case_count"] == 1
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["expected_next_action"] == "$omc-critique"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-critique"


def test_response_mode_fixture_covers_plan_status_and_reentry_next_actions():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    case_map = {case["request"]: case for case in cases}

    plan_case = case_map["현재 로드맵 최신화하고 다음 작업 체크"]
    status_case = case_map["현재 어떤 작업 진행됐는지 상태만 빠르게 알려줘"]
    reentry_case = case_map["오랜만에 해당 프로젝트에 돌아와서 어떤 프로젝트인지 파악하고 분석하는 느낌인데"]

    report = mod.compare_response_modes([plan_case, status_case, reentry_case])

    assert report["summary"]["next_action_case_count"] == 3
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["expected_next_action"] == "$omc-plan"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-plan"
    assert report["cases"][1]["expected_next_action"] == "$omc-status"
    assert report["cases"][1]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][1]["candidate"]["next_action"] == "$omc-status"
    assert report["cases"][2]["expected_next_action"] == "$omc-reentry"
    assert report["cases"][2]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][2]["candidate"]["next_action"] == "$omc-reentry"


def test_response_mode_fixture_covers_roadmap_sync_next_action():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(
        case for case in cases if case["request"] == "로드맵과 현재 진행된 부분 싱크부터 맞추자"
    )

    report = mod.compare_response_modes([target_case])

    assert report["summary"]["next_action_case_count"] == 1
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["expected_next_action"] == "$omc-plan"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-plan"


def test_response_mode_fixture_prefers_plan_for_roadmap_sync_plus_progress_check():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(
        case for case in cases if case["request"] == "현재 로드맵 최신화하고 어디까지 진행된건지 체크해"
    )

    report = mod.compare_response_modes([target_case])

    assert report["summary"]["next_action_case_count"] == 1
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["source_type"] == "observed_request"
    assert report["cases"][0]["expected_next_action"] == "$omc-plan"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-plan"


def test_response_mode_fixture_distinguishes_plan_wording_from_plan_review_intent():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    roadmap_plan_case = next(
        case for case in cases if case["request"] == "현재 로드맵 최신화하고 다음 작업 체크"
    )
    plan_review_case = next(
        case
        for case in cases
        if case["request"] == "이거 클로드코드로 실행한건데 이거 제대로 진행된 거 맞아? plan"
        and case["source_type"] == "observed_request"
    )

    report = mod.compare_response_modes([roadmap_plan_case, plan_review_case])

    assert report["summary"]["next_action_case_count"] == 2
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert plan_review_case["source_type"] == "observed_request"
    assert report["cases"][0]["expected_next_action"] == "$omc-plan"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-plan"
    assert report["cases"][1]["expected_next_action"] == "사용자 선택 대기"
    assert report["cases"][1]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][1]["candidate"]["next_action"] == "사용자 선택 대기"


def test_response_mode_fixture_contains_observed_request_for_plan_review_intent():
    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload

    matched_cases = [
        case
        for case in cases
        if case["source_type"] == "observed_request"
        and "plan" in case["request"]
        and case.get("expected_next_action") == "사용자 선택 대기"
    ]

    assert matched_cases, "fixture should include observed_request evidence for plan wording review intent"


def test_response_mode_fixture_covers_benchmark_status_and_comparison_requests():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    case_map = {case["request"]: case for case in cases}

    benchmark_case = case_map["우리 omc 관련 벤치마킹해야지"]
    compare_case = case_map["현재 omc 상태랑 okx-maker-grid-bot-wind 에서 사용중인 omc 상태랑 비교해보자"]
    status_case = case_map["현재 우리 omc 시스템 어느정도야"]

    report = mod.compare_response_modes([benchmark_case, compare_case, status_case])

    assert report["summary"]["next_action_case_count"] == 3
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["expected_next_action"] == "$omc-benchmark"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-plan"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-benchmark"
    assert report["cases"][1]["expected_next_action"] == "$omc-benchmark"
    assert report["cases"][1]["baseline"]["next_action"] == "$omc-status"
    assert report["cases"][1]["candidate"]["next_action"] == "$omc-benchmark"
    assert report["cases"][2]["expected_next_action"] == "$omc-status"
    assert report["cases"][2]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][2]["candidate"]["next_action"] == "$omc-status"


def test_response_mode_fixture_observed_review_request_prefers_review_next_action():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(
        case
        for case in cases
        if case["request"] == "현재 git changes 변경 상태 리뷰 보고 정말 괜찮은 변경인지 체크하려고 하는데 무슨 스킬 써야해"
    )

    report = mod.compare_response_modes([target_case])

    assert report["summary"]["next_action_case_count"] == 1
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["expected_next_action"] == "$omc-review"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-review"


def test_response_mode_fixture_review_request_infers_review_first_mode():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(
        case
        for case in cases
        if case["request"] == "현재 git changes 변경 상태 리뷰 보고 정말 괜찮은 변경인지 체크하려고 하는데 무슨 스킬 써야해"
    )

    report = mod.compare_response_modes([target_case])

    assert report["cases"][0]["baseline"]["mode"] == "review-first"
    assert report["cases"][0]["candidate"]["mode"] == "review-first"
    assert report["summary"]["next_action_case_count"] == 1
    assert report["cases"][0]["source_type"] == "observed_request"
    assert report["cases"][0]["expected_next_action"] == "$omc-review"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-review"


def test_response_mode_fixture_distinguishes_status_request_from_explanation_request():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    case_map = {case["request"]: case for case in cases}

    status_case = case_map["현재 어떤 작업 진행됐는지 상태만 빠르게 알려줘"]
    explanation_case = case_map["현재 어떤점이 개선된거야"]

    report = mod.compare_response_modes([status_case, explanation_case])

    assert report["summary"]["next_action_case_count"] == 2
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["expected_next_action"] == "$omc-status"
    assert report["cases"][0]["candidate"]["next_action"] == "$omc-status"
    assert report["cases"][1]["expected_next_action"] == "사용자 선택 대기"
    assert report["cases"][1]["candidate"]["next_action"] == "사용자 선택 대기"


def test_response_mode_fixture_distinguishes_plan_gate_explanation_from_task_progression():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(
        case
        for case in cases
        if case["request"] == "plan으로 계획 세우고 task 했는데 왜 작업을 선언하라는거지"
    )

    report = mod.compare_response_modes([target_case])

    assert report["summary"]["next_action_case_count"] == 1
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["source_type"] == "observed_request"
    assert report["cases"][0]["expected_next_action"] == "사용자 선택 대기"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "사용자 선택 대기"


def test_response_mode_fixture_distinguishes_option_recommendation_from_immediate_task_progression():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(case for case in cases if case["request"] == "2,3 중에 뭘 추천해")

    report = mod.compare_response_modes([target_case])

    assert report["summary"]["next_action_case_count"] == 1
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["source_type"] == "observed_request"
    assert report["cases"][0]["expected_next_action"] == "사용자 선택 대기"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "사용자 선택 대기"


def test_response_mode_fixture_distinguishes_roadmap_assessment_bundle_from_task_progression():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    target_case = next(
        case
        for case in cases
        if case["request"]
        == "로드맵 완료율을 퍼센트 느낌으로\n지금 종료해도 되는 범위 / 더 해야 하는 범위\n다음 우선순위 3개만 딱 잘라서"
    )

    report = mod.compare_response_modes([target_case])

    assert report["summary"]["next_action_case_count"] == 1
    assert report["summary"]["baseline_wrong_next_step_rate"] == 1.0
    assert report["summary"]["candidate_wrong_next_step_rate"] == 0.0
    assert report["summary"]["wrong_next_step_rate_delta"] == -1.0
    assert report["cases"][0]["source_type"] == "observed_request"
    assert report["cases"][0]["expected_next_action"] == "사용자 선택 대기"
    assert report["cases"][0]["baseline"]["next_action"] == "$omc-task"
    assert report["cases"][0]["candidate"]["next_action"] == "사용자 선택 대기"


def test_build_expensive_flow_report_ranks_top5_flows_with_categories():
    mod = _load_module()

    cases = [
        {
            "request": "다음 1순위 작업을 더 잘게 쪼개서",
            "expected_mode": "answer-first",
            "expected_next_action": "$omc-critique",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 바로 구현 전제", "user: 아니 더 잘게 계획해줘"],
            "candidate_trace": ["assistant: 재분해", "assistant: critique 추천"],
            "baseline_output_chars": 268,
            "candidate_output_chars": 302,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "baseline_next_action": "$omc-task",
            "candidate_next_action": "$omc-critique",
            "source_type": "observed_request",
            "evidence": "real request",
        },
        {
            "request": "지금까지 뭐 했는지 정리해줘",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 실행 전제로 답함", "user: 아니 정리해줘"],
            "candidate_trace": ["assistant: 요약 제공"],
            "baseline_output_chars": 280,
            "candidate_output_chars": 294,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "source_type": "synthetic",
        },
        {
            "request": "이거 클로드코드로 실행한건데 이거 제대로 진행된 거 맞아? plan",
            "expected_mode": "answer-first",
            "expected_next_action": "사용자 선택 대기",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: PHASE 3 제시"],
            "candidate_trace": ["assistant: 과진행 판단 후 멈춤"],
            "baseline_output_chars": 438,
            "candidate_output_chars": 214,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "baseline_next_action": "$omc-task",
            "candidate_next_action": "사용자 선택 대기",
            "source_type": "observed_output",
            "comparison_scope": "cross_surface",
            "evidence": "sample",
            "baseline_response_sample": "PHASE 3",
            "candidate_response_sample": "멈춤",
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
        {
            "request": "리스크 중심으로 냉정하게 리뷰해줘",
            "expected_mode": "review-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 일반 설명", "user: 아니 냉정하게 리뷰해줘"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 295,
            "candidate_output_chars": 321,
            "baseline_task_start_delay": 2,
            "candidate_task_start_delay": 1,
            "source_type": "synthetic",
        },
        {
            "request": "현재 git changes 괜찮은지 코드 봐줘",
            "expected_mode": "review-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 리뷰 시작"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 288,
            "candidate_output_chars": 302,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
            "source_type": "synthetic",
        },
    ]

    report = mod.build_expensive_flow_report(cases)

    assert report["summary"]["case_count"] == 6
    assert report["summary"]["top_flow_count"] == 5
    assert report["summary"]["flow_kind_counts"]["wrong_next_step"] >= 1
    assert report["summary"]["flow_kind_counts"]["reroute_loop"] >= 1
    assert report["summary"]["flow_kind_counts"]["over_stage_entry"] >= 1
    assert report["flows"][0]["waste_score"] >= report["flows"][-1]["waste_score"]
    requests = {item["request"] for item in report["flows"]}
    assert "다음 1순위 작업을 더 잘게 쪼개서" in requests
    assert "이거 클로드코드로 실행한건데 이거 제대로 진행된 거 맞아? plan" in requests


def test_response_mode_fixture_exposes_top5_expensive_flows():
    mod = _load_module()

    payload = json.loads(RESPONSE_MODE_FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload

    report = mod.build_expensive_flow_report(cases)

    assert report["summary"]["case_count"] >= 21
    assert report["summary"]["top_flow_count"] == 5
    assert report["summary"]["observed_case_count"] >= 5
    assert any(item["source_type"] == "observed_request" for item in report["flows"])
    assert any(item["flow_kind"] == "wrong_next_step" for item in report["flows"])
    wrong_next_step_flow = next(item for item in report["flows"] if item["flow_kind"] == "wrong_next_step")
    assert "expected_next_action" in wrong_next_step_flow
    assert "baseline_next_action_correct" in wrong_next_step_flow
    assert "candidate_next_action_correct" in wrong_next_step_flow


def test_build_expensive_flow_report_exposes_reroute_loop_reason_fields():
    mod = _load_module()

    cases = [
        {
            "request": "이거 plan 검토만 하려던 건데 왜 바로 task로 가",
            "expected_mode": "answer-first",
            "expected_next_action": "사용자 선택 대기",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": [
                "assistant: task로 바로 진행하자고 응답",
                "user: 아니 plan 검토만 하려던 거야",
            ],
            "candidate_trace": [
                "assistant: 설명 요청으로 판단",
                "assistant: 사용자 선택 대기로 멈춤",
            ],
            "baseline_output_chars": 320,
            "candidate_output_chars": 260,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "baseline_next_action": "$omc-task",
            "candidate_next_action": "사용자 선택 대기",
            "source_type": "observed_request",
            "evidence": "real reroute request",
        }
    ]

    report = mod.build_expensive_flow_report(cases)

    reroute_flow = next(item for item in report["flows"] if item["baseline_reroute"] is True)
    assert reroute_flow["baseline_reroute"] is True
    assert reroute_flow["reroute_reason"] == "user_correction_after_baseline"
    assert reroute_flow["reroute_signal"] == "user_requested_path_change"


def test_build_expensive_flow_report_exposes_output_bloat_reason_fields():
    mod = _load_module()

    cases = [
        {
            "request": "task, critique는 안고쳐도 된다고?",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 장문 설명"],
            "candidate_trace": ["assistant: 압축된 설명"],
            "baseline_output_chars": 610,
            "candidate_output_chars": 320,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
            "evidence": "real compression follow-up",
        }
    ]

    report = mod.build_expensive_flow_report(cases)

    output_bloat_flow = next(item for item in report["flows"] if item["flow_kind"] == "output_bloat")
    assert output_bloat_flow["output_chars_saved"] == 290
    assert output_bloat_flow["output_bloat_reason"] == "baseline_output_exceeds_candidate"
    assert output_bloat_flow["compression_signal"] == "char_reduction_confirmed"


def test_build_expensive_flow_report_summarizes_observed_reason_signals():
    mod = _load_module()

    cases = [
        {
            "request": "이거 plan 검토만 하려던 건데 왜 바로 task로 가",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": [
                "assistant: task로 바로 진행하자고 응답",
                "user: 아니 plan 검토만 하려던 거야",
            ],
            "candidate_trace": [
                "assistant: 설명 요청으로 판단",
                "assistant: 사용자 선택 대기로 멈춤",
            ],
            "baseline_output_chars": 320,
            "candidate_output_chars": 260,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
            "evidence": "real reroute request",
        },
        {
            "request": "task, critique는 안고쳐도 된다고?",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 장문 설명"],
            "candidate_trace": ["assistant: 압축된 설명"],
            "baseline_output_chars": 610,
            "candidate_output_chars": 320,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
            "evidence": "real compression follow-up",
        },
    ]

    report = mod.build_expensive_flow_report(cases)

    assert report["summary"]["observed_reason_signal_counts"] == {
        "reroute_reason": 1,
        "output_bloat_reason": 1,
        "compression_signal": 1,
    }


def test_build_expensive_flow_report_marks_missing_next_actions_as_gap():
    mod = _load_module()

    cases = [
        {
            "request": "현재 git changes 변경 상태 리뷰 보고 정말 괜찮은 변경인지 체크하려고 하는데 무슨 스킬 써야해",
            "expected_mode": "review-first",
            "expected_next_action": "$omc-review",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 리뷰 시작"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 288,
            "candidate_output_chars": 302,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 1,
            "baseline_next_action": "$omc-review",
            "source_type": "observed_request",
            "evidence": "real request",
        }
    ]

    report = mod.build_expensive_flow_report(cases)

    flow = report["flows"][0]
    assert flow["flow_kind"] == "general_overhead"
    assert flow["expected_next_action"] == "$omc-review"
    assert flow["baseline_next_action"] == "$omc-review"
    assert "candidate_next_action" not in flow
    assert flow["next_action_incomplete"] is True
    assert flow["next_action_gap"] is True


def test_build_expensive_flow_report_diversifies_top_flows_and_surfaces_operator_priority():
    mod = _load_module()

    cases = [
        {
            "request": "wrong next step dominant",
            "expected_mode": "answer-first",
            "expected_next_action": "$omc-plan",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: task로 바로 진행"],
            "candidate_trace": ["assistant: plan으로 정렬"],
            "baseline_output_chars": 420,
            "candidate_output_chars": 260,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "baseline_next_action": "$omc-task",
            "candidate_next_action": "$omc-plan",
            "source_type": "observed_request",
            "evidence": "real wrong next step",
        },
        {
            "request": "reroute loop visible",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: task로 바로 진행", "user: 아니 plan 검토만 하려던 거야"],
            "candidate_trace": ["assistant: 사용자 선택 대기"],
            "baseline_output_chars": 350,
            "candidate_output_chars": 250,
            "baseline_task_start_delay": 1,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
            "evidence": "real reroute",
        },
        {
            "request": "output bloat visible",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 장문 설명"],
            "candidate_trace": ["assistant: 압축 설명"],
            "baseline_output_chars": 610,
            "candidate_output_chars": 280,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
            "source_type": "observed_request",
            "evidence": "real compression",
        },
        {
            "request": "over stage entry visible",
            "expected_mode": "review-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 설명만 제공"],
            "candidate_trace": ["assistant: 리뷰 시작"],
            "baseline_output_chars": 280,
            "candidate_output_chars": 260,
            "baseline_task_start_delay": 3,
            "candidate_task_start_delay": 1,
            "source_type": "synthetic",
        },
        {
            "request": "general overhead only",
            "expected_mode": "answer-first",
            "baseline_policy": "baseline",
            "candidate_policy": "candidate",
            "baseline_trace": ["assistant: 요약"],
            "candidate_trace": ["assistant: 요약"],
            "baseline_output_chars": 220,
            "candidate_output_chars": 210,
            "baseline_task_start_delay": 0,
            "candidate_task_start_delay": 0,
            "source_type": "synthetic",
        },
    ]

    report = mod.build_expensive_flow_report(cases)

    flow_kinds = {item["flow_kind"] for item in report["flows"]}
    assert "wrong_next_step" in flow_kinds
    assert "reroute_loop" in flow_kinds
    assert "output_bloat" in flow_kinds
    assert "over_stage_entry" in flow_kinds
    assert report["summary"]["dominant_flow_kind"] in flow_kinds
    assert report["summary"]["operator_next_priority"] == "tighten_next_action_routing"
    assert report["summary"]["operator_next_priority_reason"] == (
        "wrong next step remains the dominant expensive flow"
    )


def test_response_mode_fixture_covers_operator_experience_finish_request():
    mod = _load_module()

    report = mod.compare_response_modes(
        mod._load_response_mode_cases(RESPONSE_MODE_FIXTURE_PATH)
    )

    case = next(
        item
        for item in report["cases"]
        if item["request"]
        == "plan/task/review 흐름을 더 똑똑하게 next-action 품질과 reroute/output bloat/과다 단계 진입 사용감 개선 마무리하자"
    )

    assert case["expected_next_action"] == "$omc-plan"
    assert case["baseline"]["next_action"] == "$omc-task"
    assert case["candidate"]["next_action"] == "$omc-plan"


def test_response_mode_fixture_surfaces_observed_output_bloat_signal_in_full_flow_report():
    mod = _load_module()

    cases = mod._load_response_mode_cases(RESPONSE_MODE_FIXTURE_PATH)
    report = mod.build_expensive_flow_report(cases)

    assert report["summary"]["flow_kind_counts"].get("output_bloat", 0) >= 1
    assert report["summary"]["observed_reason_signal_counts"].get("output_bloat_reason", 0) >= 1
    assert report["summary"]["observed_reason_signal_counts"].get("compression_signal", 0) >= 1


def test_response_mode_fixture_reports_output_bloat_as_secondary_validation_signal():
    mod = _load_module()

    cases = mod._load_response_mode_cases(RESPONSE_MODE_FIXTURE_PATH)
    report = mod.build_expensive_flow_report(cases)

    assert report["summary"]["operator_validation_status"] == "ready_to_close"
    assert report["summary"]["output_bloat_followup_needed"] is False
    assert report["summary"]["output_bloat_status_line"] == (
        "output_bloat observed but not dominant; keep focus on wrong_next_step"
    )
