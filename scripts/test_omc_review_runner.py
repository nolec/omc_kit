from __future__ import annotations

import sys
import subprocess
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_runner import (
    _CODEX_REVIEW_OUTPUT_SCHEMA_PATH,
    _parse_findings,
    normalize_review_result,
    run_codex_review,
    run_native_codex_review,
)


def _contract_output(verdict: str, evidence: str, findings: list[dict[str, object]] | None = None) -> str:
    return json.dumps({"verdict": verdict, "evidence": evidence, "findings": findings or []})


def test_omc_review_skill_empty_result_sections_are_parser_safe():
    skill = (
        Path(__file__).resolve().parents[1]
        / ".agents"
        / "skills"
        / "omc-review"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    for severity in ("치명", "중대", "경미", "제안"):
        assert f"[{severity}]\n" in skill
        assert f"[{severity}] —" not in skill

    empty_output = "\n".join(
        [
            "[치명]",
            "- 없음",
            "[중대]",
            "- 없음",
            "[경미]",
            "- 없음",
            "[제안]",
            "- 없음",
            "[확인 필요]",
            "- 없음",
            "VERDICT: APPROVE",
        ]
    )

    assert _parse_findings(empty_output) == []


def test_run_codex_review_marks_missing_verdict_as_failed(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "partial exploration only"
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"
    assert result["execution_mode"] == "schema_contract_failed"
    assert "output did not satisfy the requested schema" in result["stderr"]


def test_run_codex_review_rejects_findings_without_contract_verdict(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "Full review comments:\n\n- [P2] Persist failed runs — src/runner.py:10-12\n"
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


def test_run_codex_review_rejects_approval_without_contract_verdict(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "No actionable regressions were identified."
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


@pytest.mark.parametrize(
    "stdout",
    [
        "No blocking issues were identified.",
        "The current changes do not introduce a clearly actionable defect based on the available code and repository context.",
    ],
)
def test_run_codex_review_rejects_legacy_no_issue_variants(monkeypatch, tmp_path, stdout):
    class Result:
        returncode = 0
        stderr = ""

    Result.stdout = stdout
    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


def test_run_codex_review_keeps_uncertain_no_issue_phrase_unknown(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = (
            "I cannot establish that the current changes do not introduce "
            "a clearly actionable defect."
        )
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


def test_run_codex_review_extracts_final_message_from_json_events(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = (
            '{"type":"thread.started","thread_id":"thread-1"}\n'
            + json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": _contract_output(
                            "REVISE",
                            "A changed authorization path regresses.",
                            [{"severity": "P2", "summary": "Persist failed runs", "file": "src/runner.py", "line": 10}],
                        ),
                    },
                }
            )
            + "\n"
            '{"type":"turn.completed","usage":{"input_tokens":12}}\n'
        )
        stderr = "provider trace"

    captured: dict[str, object] = {}

    def run(*args, **kwargs):
        captured["command"] = args[0]
        captured["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert captured["command"] == [
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "exec",
        "--ephemeral",
        "--json",
        "--sandbox",
        "workspace-write",
        "--output-schema",
        str(_CODEX_REVIEW_OUTPUT_SCHEMA_PATH),
        "-",
    ]
    assert "Review only the current uncommitted diff" in captured["input"]
    assert result["status"] == "completed"
    assert result["verdict"] == "REVISE"
    assert result["stdout"] == (
        "- [P2] Persist failed runs — src/runner.py:10\n"
        "VERDICT: REVISE\n"
        "EVIDENCE: A changed authorization path regresses."
    )
    assert result["event_stream"] == Result.stdout
    assert result["execution_artifacts"] == {
        "event_stream_captured": True,
        "final_message_captured": True,
        "exit_code": 0,
        "snapshot_used": True,
        "workspace_mutated": False,
    }


def test_run_codex_review_rejects_approval_verdict_without_evidence(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = _contract_output("APPROVE", "")
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


def test_run_codex_review_accepts_contract_approval_with_evidence(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = _contract_output("APPROVE", "No actionable issue remains.")
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "completed"
    assert result["verdict"] == "APPROVE"


def test_run_codex_review_rejects_approval_with_findings(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = _contract_output(
            "APPROVE",
            "No issue remains.",
            [{"severity": "P1", "summary": "Broken authorization", "file": "src/auth.py", "line": 10}],
        )
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_codex_review(tmp_path, case_id="case-1", diff_id="diff-1", timeout_sec=1)

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


def test_run_codex_review_executes_in_private_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "changed.py").write_text("value = 1\n", encoding="utf-8")
    observed: dict[str, Path] = {}

    class Result:
        returncode = 0
        stdout = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": _contract_output("APPROVE", "No actionable issue remains."),
                    },
                }
            )
            + "\n"
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
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": _contract_output("APPROVE", "No actionable issue remains."),
                    },
                }
            )
            + "\n"
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


def test_run_native_codex_review_retains_raw_output_and_marks_adapter_verdict(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = "- [P1] Authorization is bypassed — src/auth.py:8\n"
        stderr = ""

    def run(*args, **kwargs):
        captured["command"] = args[0]
        captured["input"] = kwargs.get("input")
        return Result()

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)
    artifact = tmp_path / "durable" / "case-1.json"

    result = run_native_codex_review(
        tmp_path,
        case_id="case-1",
        diff_id="diff-1",
        timeout_sec=1,
        artifact_path=artifact,
        source_commit="abc123",
    )

    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert captured["command"] == [
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "review",
        "--uncommitted",
    ]
    assert captured["input"] is None
    assert result["status"] == "completed"
    assert result["runner"] == "codex native review"
    assert result["verdict"] == "REVISE"
    assert result["execution_artifacts"]["verdict_source"] == "adapter_from_native_output"
    assert result["execution_artifacts"]["durable_output_retained"] is True
    assert saved["source_commit"] == "abc123"
    assert saved["stdout"] == Result.stdout
    assert saved["command"] == captured["command"]
    assert saved["clean_baseline"] is False


def test_run_native_codex_review_clean_baseline_excludes_project_instructions(monkeypatch, tmp_path):
    (tmp_path / "AGENTS.md").write_text("project instructions", encoding="utf-8")
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "SKILL.md").write_text("omc", encoding="utf-8")
    captured: dict[str, Path] = {}

    class Result:
        returncode = 0
        stdout = "No definite correctness regression was identified.\n"
        stderr = ""

    def run(*args, **kwargs):
        captured["cwd"] = Path(kwargs["cwd"])
        return Result()

    monkeypatch.setattr("omc_review_runner.subprocess.run", run)
    artifact = tmp_path / "durable" / "case-1.json"

    result = run_native_codex_review(
        tmp_path,
        case_id="case-1",
        diff_id="diff-1",
        timeout_sec=1,
        artifact_path=artifact,
        clean_baseline=True,
    )

    assert not (captured["cwd"] / "AGENTS.md").exists()
    assert not (captured["cwd"] / ".agents").exists()
    assert result["execution_artifacts"]["clean_baseline"] is True
    assert json.loads(artifact.read_text(encoding="utf-8"))["clean_baseline"] is True


def test_run_native_codex_review_redacts_sensitive_artifact_transcript(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "No actionable issues found.\n"
        stderr = "loaded file:///Users/example/private-plugin\n"

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())
    artifact = tmp_path / "durable" / "case-1.json"

    result = run_native_codex_review(
        tmp_path,
        case_id="case-1",
        diff_id="diff-1",
        timeout_sec=1,
        artifact_path=artifact,
    )

    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert saved["redacted"] is True
    assert "/Users/example" not in saved["stderr"]
    assert saved["captured_stderr_sha256"] != saved["retained_stderr_sha256"]


def test_run_native_codex_review_accepts_native_no_finding_summary(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "No definite correctness regression was identified.\n"
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_native_codex_review(
        tmp_path,
        case_id="case-1",
        diff_id="diff-1",
        timeout_sec=1,
    )

    assert result["status"] == "completed"
    assert result["verdict"] == "APPROVE"
    assert result["findings"] == []


def test_run_native_codex_review_rejects_unparseable_review_comment(monkeypatch, tmp_path):
    class Result:
        returncode = 0
        stdout = "Review comment:\n\n- [P1 malformed finding]\n"
        stderr = ""

    monkeypatch.setattr("omc_review_runner.subprocess.run", lambda *args, **kwargs: Result())

    result = run_native_codex_review(
        tmp_path,
        case_id="case-1",
        diff_id="diff-1",
        timeout_sec=1,
    )

    assert result["status"] == "failed"
    assert result["verdict"] == "unknown"


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
            "  - [src/service.py:12] evidence_class: behavioral_direct | "
            "evidence: null 입력이 예외 경로로 전달됩니다.\n"
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
            "message": (
                "evidence_class: behavioral_direct | "
                "evidence: null 입력이 예외 경로로 전달됩니다."
            ),
            "evidence_class": "behavioral_direct",
            "evidence": "null 입력이 예외 경로로 전달됩니다.",
        }
    ]
    assert result["metrics"]["duration_ms"] == 120


def test_normalize_omc_review_rejects_strong_finding_without_direct_evidence_class():
    with pytest.raises(ValueError, match="cannot be recorded as finding"):
        normalize_review_result(
            provider="omc-review",
            case_id="case-1",
            diff_id="diff-1",
            status="completed",
            stdout=(
                "[중대] — alias가 깨질 수 있습니다\n"
                "  - [src/service.py:12] evidence_class: context_needed | "
                "evidence: tsconfig alias 확인 필요\n"
                "VERDICT: REVISE\n"
            ),
            stderr="",
            duration_ms=10,
        )


def test_normalize_omc_review_rejects_strong_finding_without_evidence_detail():
    with pytest.raises(ValueError, match="evidence:"):
        normalize_review_result(
            provider="omc-review",
            case_id="case-1",
            diff_id="diff-1",
            status="completed",
            stdout=(
                "[P1] — 실행 경로가 누락됩니다 — src/service.py:12\n"
                "evidence_class: behavioral_direct\n"
                "VERDICT: REVISE\n"
            ),
            stderr="",
            duration_ms=10,
        )


@pytest.mark.parametrize("evidence_class", ["context_needed", "unresolved", "non_behavioral"])
def test_normalize_omc_review_rejects_non_finding_evidence_classes_at_any_severity(
    evidence_class: str,
):
    with pytest.raises(ValueError, match="cannot be recorded as finding"):
        normalize_review_result(
            provider="omc-review",
            case_id="case-1",
            diff_id="diff-1",
            status="completed",
            stdout=(
                "[P3] — 확인이 필요한 가설 — src/service.py:12\n"
                f"evidence_class: {evidence_class}\n"
                "VERDICT: APPROVE WITH NOTES\n"
            ),
            stderr="",
            duration_ms=10,
        )


def test_normalize_omc_review_keeps_confirmation_section_out_of_previous_finding():
    result = normalize_review_result(
        provider="omc-review",
        case_id="case-1",
        diff_id="diff-1",
        status="completed",
        stdout=(
            "[제안] — 테스트 강도 보강\n"
            "  - [tests/service_test.py:12] evidence_class: test_quality_only | 테스트 보강 제안\n"
            "[확인 필요] — 현재 diff만으로 finding 확정 불가\n"
            "  - [src/service.py:7] evidence_class: context_needed | 호출 경로 확인 필요\n"
            "VERDICT: APPROVE WITH NOTES\n"
        ),
        stderr="",
        duration_ms=10,
    )

    assert result["findings"] == []
    assert result["suggestions"] == [
        {
            "severity": "제안",
            "file": "tests/service_test.py",
            "line": "12",
            "message": "evidence_class: test_quality_only | 테스트 보강 제안",
            "evidence_class": "test_quality_only",
        }
    ]


def test_normalize_omc_review_rejects_backticked_evidence_class():
    """Reject non-canonical evidence tokens so reports have one stable grammar."""
    with pytest.raises(ValueError, match="requires evidence_class"):
        normalize_review_result(
            provider="omc-review",
            case_id="case-1",
            diff_id="diff-1",
            status="completed",
            stdout=(
                "[제안]\n"
                "- [scripts/service.py:12] evidence_class: `test_quality_only` | "
                "회귀 테스트를 추가하세요.\n"
                "VERDICT: APPROVE WITH NOTES\n"
            ),
            stderr="",
            duration_ms=10,
        )


def test_omc_review_skill_has_a_read_only_evaluation_exception():
    skill = (
        Path(__file__).resolve().parents[1]
        / ".agents"
        / "skills"
        / "omc-review"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "blind/read-only" in skill
    assert "state/session 명령과 변경 가능한 검증은 실행하지 않는다" in skill


def test_omc_review_skill_has_diff_local_p1_detection_patterns():
    root = Path(__file__).resolve().parents[1]
    installed_skill = (root / ".agents" / "skills" / "omc-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    template_skill = (root / "templates" / ".agents" / "skills" / "omc-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    for skill in (installed_skill, template_skill):
        assert "동적 값 이중 읽기" in skill
        assert "식별자→순서 매핑" in skill
        assert "측정값 없는 정책 성공" in skill
        assert "필수 메타데이터 조건부 검증" in skill
        assert "외부 계약 확인만을 이유로 `context_needed`로 내리지 않는다" in skill


def test_normalize_omc_review_preserves_finding_line_ranges():
    result = normalize_review_result(
        provider="omc-review",
        case_id="case-1",
        diff_id="diff-1",
        status="completed",
        stdout=(
            "[경미]\n"
            "- [scripts/service.py:80-83] evidence_class: behavioral_direct | "
            "evidence: 변경된 호출이 저장소를 변경합니다.\n"
            "VERDICT: APPROVE WITH NOTES\n"
        ),
        stderr="",
        duration_ms=10,
    )

    assert result["findings"][0]["line"] == "80-83"


def test_normalize_omc_review_preserves_multi_line_location():
    result = normalize_review_result(
        provider="omc-review",
        case_id="case-1",
        diff_id="diff-1",
        status="completed",
        stdout=(
            "[중대]\n"
            "- [scripts/service.py:17,33] evidence_class: behavioral_direct | "
            "evidence: 두 소비처가 서로 다른 값을 사용합니다.\n"
            "VERDICT: REVISE\n"
        ),
        stderr="",
        duration_ms=10,
    )

    assert result["findings"][0]["line"] == "17,33"


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
