from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


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


def test_orchestration_hint_routes_implementation_request():
    mod = _load_module()

    hint = mod.suggest_orchestration("로그인 버튼 컴포넌트 구현해줘")

    assert hint["response_mode"] == "execute-first"
    assert hint["recommended_skill"] == "$omc-task"
    assert hint["primary_role"] == "senior_coding"


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
