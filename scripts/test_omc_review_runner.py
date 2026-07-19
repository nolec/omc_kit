from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_runner import normalize_review_result


def test_normalize_review_result_extracts_verdict_next_action_and_findings():
    result = normalize_review_result(
        provider="omc-review",
        case_id="case-1",
        diff_id="diff-1",
        status="completed",
        stdout=(
            "[중대] — null 처리 누락\n"
            "  - [src/service.py:12] 입력이 null이면 예외가 발생합니다.\n"
            "VERDICT: REVISE\n"
            "next_action: $omc-task\n"
        ),
        stderr="",
        duration_ms=120,
    )

    assert result["execution_mode"] == "cli_completed"
    assert result["verdict"] == "REVISE"
    assert result["next_action"] == "$omc-task"
    assert result["findings"] == [
        {
            "severity": "중대",
            "file": "src/service.py",
            "line": "12",
            "message": "입력이 null이면 예외가 발생합니다.",
        }
    ]
    assert result["metrics"]["duration_ms"] == 120


def test_normalize_review_result_rejects_unparseable_completed_output():
    with pytest.raises(ValueError, match="verdict"):
        normalize_review_result(
            provider="codex",
            case_id="case-1",
            diff_id="diff-1",
            status="completed",
            stdout="review output without a machine-readable verdict",
            stderr="",
            duration_ms=10,
        )


def test_normalize_review_result_rejects_invalid_metrics():
    with pytest.raises(ValueError, match="duration_ms"):
        normalize_review_result(
            provider="codex",
            case_id="case-1",
            diff_id="diff-1",
            status="completed",
            stdout="VERDICT: APPROVE",
            stderr="",
            duration_ms=-1,
        )


def test_normalize_review_result_redacts_sensitive_output_and_keeps_full_next_action():
    result = normalize_review_result(
        provider="omc-review",
        case_id="case-1",
        diff_id="diff-1",
        batch_id="batch-1",
        status="completed",
        stdout=(
            "VERDICT: APPROVE WITH NOTES\n"
            "next_action: 사용자 선택 대기\n"
            "token=ghp_abcdefghijklmnopqrstuvwxyz\n"
        ),
        stderr="email test@example.com",
        duration_ms=10,
    )

    assert result["prompt_id"] == "omc-review:batch-1:case-1"
    assert result["next_action"] == "사용자 선택 대기"
    assert "ghp_" not in result["stdout"]
    assert "test@example.com" not in result["stderr"]


def test_normalize_review_result_redacts_bearer_and_aws_tokens():
    result = normalize_review_result(
        provider="omc-review",
        case_id="case-1",
        diff_id="diff-1",
        status="completed",
        stdout="VERDICT: APPROVE\nBearer secret-token AKIAIOSFODNN7EXAMPLE",
        stderr="",
        duration_ms=10,
    )

    assert "Bearer" not in result["stdout"]
    assert "AKIAIOSFODNN7EXAMPLE" not in result["stdout"]


def test_normalize_review_result_rejects_non_anonymized_batch_id():
    with pytest.raises(ValueError, match="batch_id"):
        normalize_review_result(
            provider="omc-review",
            case_id="case-1",
            diff_id="diff-1",
            batch_id="/Users/private-run",
            status="completed",
            stdout="VERDICT: APPROVE",
            stderr="",
            duration_ms=10,
        )


def test_normalize_review_result_marks_failed_cli_runs_separately():
    result = normalize_review_result(
        provider="codex",
        case_id="case-1",
        diff_id="diff-1",
        status="failed",
        stdout="",
        stderr="timeout",
        duration_ms=10,
    )

    assert result["execution_mode"] == "cli_failed"
    assert result["verdict"] == "unknown"
