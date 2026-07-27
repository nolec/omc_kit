from __future__ import annotations

import sys
import subprocess
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_runner import normalize_review_result, run_codex_review


def test_run_codex_review_marks_missing_verdict_as_failed(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "partial exploration only"
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


def test_run_codex_review_accepts_completed_codex_findings_without_omc_verdict(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "Full review comments:\n\n- [P2] Persist failed runs — src/runner.py:10-12\n"
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "completed"
    assert result["verdict"] == "REVISE"
    assert result["findings"] == [
        {
            "severity": "P2",
            "file": "src/runner.py",
            "line": "10",
            "message": "Persist failed runs",
        }
    ]


def test_run_codex_review_accepts_completed_codex_no_findings_output(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "No actionable regressions were identified."
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "completed"
    assert result["verdict"] == "APPROVE"


def test_run_codex_review_extracts_final_message_from_json_events(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = (
            '{"type":"thread.started","thread_id":"thread-1"}\n'
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"- [P2] Persist failed runs — src/runner.py:10"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":12}}\n'
        )
        stderr = "provider trace"

    captured: dict[str, object] = {}

    def run(*args, **kwargs):
        captured["command"] = args[0]
        return Result()

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert captured["command"] == [
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "exec",
        "review",
        "--uncommitted",
        "--ephemeral",
        "--json",
        "-c",
        'sandbox_mode="workspace-write"',
    ]
    assert result["status"] == "completed"
    assert result["verdict"] == "REVISE"
    assert result["stdout"] == "- [P2] Persist failed runs — src/runner.py:10"
    assert result["event_stream"] == Result.stdout
    assert result["execution_artifacts"] == {
        "event_stream_captured": True,
        "final_message_captured": True,
        "exit_code": 0,
        "snapshot_used": True,
        "workspace_mutated": False,
    }


def test_run_codex_review_executes_in_private_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "changed.py").write_text("value = 1\n", encoding="utf-8")
    observed: dict[str, Path] = {}

    class Result:
        returncode = 0
        stdout = (
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"No actionable regressions were identified."}}\n'
        )
        stderr = ""

    def run(*args, **kwargs):
        snapshot = Path(kwargs["cwd"])
        observed["snapshot"] = snapshot
        (snapshot / "provider-cache.txt").write_text("generated", encoding="utf-8")
        return Result()

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)

    result = run_codex_review(source, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert observed["snapshot"] != source
    assert not (source / "provider-cache.txt").exists()
    assert result["execution_artifacts"]["snapshot_used"] is True
    assert result["execution_artifacts"]["workspace_mutated"] is True


def test_run_codex_review_detects_mode_only_snapshot_mutation(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    tracked_file = source / "runner.sh"
    tracked_file.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    tracked_file.chmod(0o644)

    class Result:
        returncode = 0
        stdout = (
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"No actionable regressions were identified."}}\n'
        )
        stderr = ""

    def run(*args, **kwargs):
        (Path(kwargs["cwd"]) / "runner.sh").chmod(0o755)
        return Result()

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)

    result = run_codex_review(source, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert tracked_file.stat().st_mode & 0o777 == 0o644
    assert result["execution_artifacts"]["workspace_mutated"] is True


def test_run_codex_review_rejects_git_worktree_pointer_before_provider_run(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").write_text("gitdir: /private/original/.git/worktrees/review\n", encoding="utf-8")
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not run")

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)

    with pytest.raises(ValueError, match="independent .git directory"):
        run_codex_review(source, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert called is False


def test_run_codex_review_rejects_symlink_before_provider_run(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "private.txt"
    target.write_text("private", encoding="utf-8")
    (source / "linked.txt").symlink_to(target)
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not run")

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)

    with pytest.raises(ValueError, match="cannot contain symlinks"):
        run_codex_review(source, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert called is False


def test_run_codex_review_records_missing_final_json_message_as_failed(monkeypatch, tmp_path):
    class Result:
        returncode = 1
        stdout = '{"type":"thread.started","thread_id":"thread-1"}\n'
        stderr = "review tool failed before final message"

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["stdout"] == ""
    assert result["event_stream"] == Result.stdout
    assert result["execution_artifacts"] == {
        "event_stream_captured": True,
        "final_message_captured": False,
        "exit_code": 1,
        "snapshot_used": True,
        "workspace_mutated": False,
    }


def test_run_codex_review_keeps_positive_summary_without_explicit_approval_unknown(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "The change correctly scopes the cache key and the accompanying tests cover both paths."
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


def test_run_codex_review_saves_timeout_output_as_failed_result(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
            output=b"partial Bearer secret-token",
            stderr=b"still running",
        )

    monkeypatch.setattr("omc_review_runner.subprocess.run", timeout)
    result_path = tmp_path / "result.json"

    result = run_codex_review(
        tmp_path,
        case_id="case-1",
        diff_id="diff-1",
        timeout_sec=1,
        result_path=result_path,
    )

    saved = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"
    assert "secret-token" not in result["stdout"]
    assert saved == result


def test_run_codex_review_preserves_partial_json_events_on_timeout(monkeypatch, tmp_path):
    event_stream = (
        '{"type":"thread.started","thread_id":"thread-1"}\n'
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"- [P2] Persist failed runs — src/runner.py:10"}}\n'
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
            output=event_stream,
            stderr="still running",
        )

    monkeypatch.setattr("omc_review_runner.subprocess.run", timeout)

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["stdout"] == "- [P2] Persist failed runs — src/runner.py:10"
    assert result["event_stream"] == event_stream
    assert result["execution_artifacts"] == {
        "event_stream_captured": True,
        "final_message_captured": True,
        "exit_code": None,
        "snapshot_used": True,
        "workspace_mutated": False,
    }


def test_run_codex_review_saves_launch_failure_without_partial_findings(monkeypatch, tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError("codex not found")

    monkeypatch.setattr("omc_review_runner.subprocess.run", missing)
    result_path = tmp_path / "result.json"

    result = run_codex_review(
        tmp_path,
        case_id="case-1",
        diff_id="diff-1",
        timeout_sec=1,
        result_path=result_path,
    )

    assert result["status"] == "failed"
    assert result["findings"] == []
    assert result["next_action"] is None
    assert "codex not found" in result["stderr"]
    assert json.loads(result_path.read_text(encoding="utf-8")) == result


def test_normalize_failed_result_omits_partial_review_fields():
    result = normalize_review_result(
        provider="codex",
        case_id="case-1",
        diff_id="diff-1",
        status="failed",
        stdout="[중대] — partial finding\nnext_action: $omc-task",
        stderr="timeout",
        duration_ms=10,
    )

    assert result["findings"] == []
    assert result["next_action"] is None


def test_run_codex_review_rejects_invalid_timeout(tmp_path):
    with pytest.raises(ValueError, match="timeout_sec"):
        run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=0)


def test_normalize_review_result_rejects_unknown_verdict_override():
    with pytest.raises(ValueError, match="verdict_override"):
        normalize_review_result(
            provider="codex",
            case_id="case-1",
            diff_id="diff-1",
            status="completed",
            stdout="provider output",
            stderr="",
            duration_ms=10,
            verdict_override="MAYBE",
        )


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


def test_normalize_review_result_redacts_sensitive_structured_findings():
    result = normalize_review_result(
        provider="codex",
        case_id="case-1",
        diff_id="diff-1",
        status="completed",
        stdout=(
            "- [P2] Bearer secret-token contacts owner@example.com "
            "\u2014 /Users/noseunglae/private/runner.py:10\n"
        ),
        stderr="",
        duration_ms=10,
        verdict_override="REVISE",
    )

    finding = result["findings"][0]
    assert "secret-token" not in finding["message"]
    assert "owner@example.com" not in finding["message"]
    assert "/Users/noseunglae" not in finding["file"]


def test_normalize_review_result_redacts_sensitive_next_action():
    result = normalize_review_result(
        provider="omc-review",
        case_id="case-1",
        diff_id="diff-1",
        status="completed",
        stdout=(
            "VERDICT: REVISE\n"
            "next_action: Bearer secret-token owner@example.com /Users/noseunglae/private\n"
        ),
        stderr="",
        duration_ms=10,
    )

    assert "secret-token" not in result["next_action"]
    assert "owner@example.com" not in result["next_action"]
    assert "/Users/noseunglae" not in result["next_action"]


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
