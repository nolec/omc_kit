from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import omc_exec


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "omc_role_suggest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("omc_role_suggest", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_orchestration_hint_routes_review_request():
    mod = _load_module()

    hint = mod.suggest_orchestration("이 변경 diff 리뷰해줘")

    assert hint["response_mode"] == "review-first"
    assert hint["recommended_skill"] == "$omc-review"
    assert hint["primary_role"] == "code_review"


def test_orchestration_hint_prioritizes_critique_over_review_wording():
    mod = _load_module()

    hint = mod.suggest_orchestration("이 계획 냉정하게 리뷰해줘")

    assert hint["response_mode"] == "review-first"
    assert hint["recommended_skill"] == "$omc-critique"
    assert hint["primary_role"] == "code_review"


def test_orchestration_hint_routes_critical_review_wording_to_critique():
    mod = _load_module()

    hint = mod.suggest_orchestration("비판적으로 코드 리뷰해줘")

    assert hint["response_mode"] == "review-first"
    assert hint["recommended_skill"] == "$omc-critique"
    assert hint["primary_role"] == "code_review"


def test_orchestration_hint_routes_plan_request():
    mod = _load_module()

    hint = mod.suggest_orchestration("이 기능 어떻게 구현할지 계획해줘")

    assert hint["response_mode"] == "answer-first"
    assert hint["recommended_skill"] == "$omc-plan"
    assert hint["primary_role"] == "analysis"
    assert hint["task_kind_hint"] == "plan"


def test_orchestration_hint_routes_implementation_request():
    mod = _load_module()

    hint = mod.suggest_orchestration("로그인 버튼 컴포넌트 구현해줘")

    assert hint["response_mode"] == "execute-first"
    assert hint["recommended_skill"] == "$omc-task"
    assert hint["primary_role"] == "senior_coding"
    assert hint["task_kind_hint"] == "task"


def test_orchestration_hint_routes_impact_and_plan_request_to_plan() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("이 변경 영향도 보고 계획까지 잡아줘")

    assert hint["response_mode"] == "answer-first"
    assert hint["recommended_skill"] == "$omc-plan"
    assert hint["primary_role"] == "analysis"


def test_orchestration_hint_routes_root_cause_and_fix_direction_to_investigate() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("버그 원인 찾고 수정 방향 정리해줘")

    assert hint["response_mode"] == "answer-first"
    assert hint["recommended_skill"] == "$omc-investigate"
    assert hint["primary_role"] == "analysis"


def test_orchestration_hint_routes_plan_with_weakness_request_to_critique() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("이 계획 맞는지 약점까지 봐줘")

    assert hint["response_mode"] == "review-first"
    assert hint["recommended_skill"] == "$omc-critique"
    assert hint["primary_role"] == "code_review"


def test_orchestration_hint_routes_explicit_bug_fix_request_to_task() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("버그 수정해줘")

    assert hint["response_mode"] == "execute-first"
    assert hint["recommended_skill"] == "$omc-task"
    assert hint["primary_role"] == "senior_coding"


def test_orchestration_hint_routes_bug_fix_with_test_addition_to_task() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("버그 수정하고 테스트 추가해줘")

    assert hint["response_mode"] == "execute-first"
    assert hint["recommended_skill"] == "$omc-task"
    assert hint["primary_role"] == "senior_coding"


def test_orchestration_hint_routes_work_breakdown_request_to_plan() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("다음 1순위 작업을 더 잘게 쪼개서")

    assert hint["response_mode"] == "answer-first"
    assert hint["recommended_skill"] == "$omc-plan"
    assert hint["primary_role"] == "analysis"
    assert hint["task_kind_hint"] == "plan"


def test_orchestration_hint_routes_progress_summary_request_to_status() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("지금까지 뭐 했는지 정리해줘")

    assert hint["response_mode"] == "answer-first"
    assert hint["recommended_skill"] == "$omc-status"
    assert hint["primary_role"] == "analysis"


def test_orchestration_hint_prioritizes_plan_over_progress_summary_when_both_present() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("지금까지 뭐 했는지 정리하고 다음 작업 계획해줘")

    assert hint["response_mode"] == "answer-first"
    assert hint["recommended_skill"] == "$omc-plan"
    assert hint["primary_role"] == "analysis"
    assert hint["task_kind_hint"] == "plan"


def test_orchestration_hint_prioritizes_review_over_progress_summary_when_both_present() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("지금까지 뭐 했는지 정리하고 현재 변경도 리뷰해줘")

    assert hint["response_mode"] == "review-first"
    assert hint["recommended_skill"] == "$omc-review"
    assert hint["primary_role"] == "code_review"
    assert hint["task_kind_hint"] == "review"


def test_orchestration_hint_prioritizes_task_over_progress_summary_when_both_present() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("지금까지 뭐 했는지 정리하고 버그 수정해줘")

    assert hint["response_mode"] == "execute-first"
    assert hint["recommended_skill"] == "$omc-task"
    assert hint["primary_role"] == "senior_coding"
    assert hint["task_kind_hint"] == "task"


def test_orchestration_hint_prioritizes_ship_over_progress_summary_when_both_present() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("현재 어떤점이 개선됐는지 말해주고 커밋해서 배포해줘")

    assert hint["response_mode"] == "execute-first"
    assert hint["recommended_skill"] == "$omc-ship"
    assert hint["primary_role"] == "directive"
    assert hint["task_kind_hint"] == "ship"


def test_orchestration_hint_prioritizes_investigate_over_progress_summary_when_both_present() -> None:
    mod = _load_module()

    hint = mod.suggest_orchestration("지금까지 뭐 했는지 정리하고 이 버그 원인 먼저 찾아줘")

    assert hint["response_mode"] == "answer-first"
    assert hint["recommended_skill"] == "$omc-investigate"
    assert hint["primary_role"] == "analysis"
    assert hint["task_kind_hint"] == "investigate"


def test_json_output_includes_orchestration_fields():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--text", "리뷰해줘", "--format", "json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "suggestions" in payload
    assert payload["response_mode"] == "review-first"
    assert payload["recommended_skill"] == "$omc-review"
    assert payload["primary_role"] == "code_review"
    assert payload["task_kind_hint"] == "review"
    assert payload["routing_policy"] == "balanced"


def test_plain_output_prints_footer_once_for_multiple_suggestions() -> None:
    mod = _load_module()

    output = mod._fmt_plain(mod.suggest("버그 수정하고 테스트 추가해줘", top=3), "버그 수정하고 테스트 추가해줘")

    assert output.count("🧭 추천 모드:") == 1
    assert output.count("🧩 추천 시작 스킬:") == 1
    assert output.count("🎯 주역할:") == 1


def test_review_hint_stays_consistent_with_quality_first_routing(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setenv("OMC_ROUTING_POLICY", "quality_first")

    hint = mod.suggest_orchestration("이 변경 영향 리뷰해줘")
    routing = omc_exec.resolve_task_routing(
        task_kind=hint["task_kind_hint"],
        request_text="변경 영향 검토",
        retry_count=0,
    )

    assert hint["recommended_skill"] == "$omc-review"
    assert hint["task_kind_hint"] == "review"
    assert hint["routing_policy"] == "quality_first"
    assert routing["model_profile"] == "full_default"
