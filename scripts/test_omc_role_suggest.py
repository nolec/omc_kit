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
