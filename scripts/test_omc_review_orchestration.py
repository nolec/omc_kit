from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_orchestration import run_review_in_snapshot


def test_run_review_in_snapshot_uses_isolated_cwd_and_returns_metadata(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "diff.txt"
    original.write_text("original", encoding="utf-8")

    def review(snapshot_path: Path) -> dict[str, str]:
        assert snapshot_path != source
        assert (snapshot_path / "diff.txt").read_text(encoding="utf-8") == "original"
        (snapshot_path / "generated.log").write_text("generated", encoding="utf-8")
        return {"verdict": "APPROVE"}

    result = run_review_in_snapshot(source, review)

    assert result["result"] == {"verdict": "APPROVE"}
    assert result["execution_metadata"] == {
        "snapshot_used": True,
        "workspace_mutated": True,
    }
    assert original.read_text(encoding="utf-8") == "original"
    assert not (source / "generated.log").exists()


def test_run_review_in_snapshot_can_return_explicit_execution_envelope(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")

    result = run_review_in_snapshot(
        source,
        lambda _snapshot: {"findings": [{"id": "null-guard"}], "metrics": {}},
        envelope_context={
            "provider": "codex",
            "case_id": "null-guard-1",
            "diff_id": "probe-null-guard",
            "prompt_id": "review-v1",
            "status": "completed",
            "execution_mode": "cli_completed",
            "runner": "codex review",
            "model": "gpt-5.6-luna",
        },
    )

    assert result["provider"] == "codex"
    assert result["case_id"] == "null-guard-1"
    assert result["execution_metadata"]["snapshot_used"] is True


def test_run_review_in_snapshot_rejects_partial_execution_context(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ValueError, match="envelope_context"):
        run_review_in_snapshot(
            source,
            lambda _snapshot: {"findings": [], "metrics": {}},
            envelope_context={"provider": "codex"},
        )


def test_run_review_in_snapshot_reports_clean_workspace(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")

    result = run_review_in_snapshot(source, lambda _: {"verdict": "APPROVE"})

    assert result["execution_metadata"]["workspace_mutated"] is False


def test_run_review_in_snapshot_rethrows_callback_error_and_cleans_up(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")

    def review(snapshot_path: Path) -> dict[str, str]:
        assert snapshot_path.exists()
        raise RuntimeError("review failed")

    with pytest.raises(RuntimeError, match="review failed"):
        run_review_in_snapshot(source, review)


def test_run_review_in_snapshot_preserves_callback_failure_as_failed_envelope(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()

    def review(_snapshot: Path) -> dict[str, object]:
        raise RuntimeError("provider secret should not be copied")

    result = run_review_in_snapshot(
        source,
        review,
        envelope_context={
            "provider": "codex",
            "case_id": "failure-1",
            "diff_id": "probe-failure",
            "prompt_id": "review-v1",
            "status": "completed",
            "execution_mode": "cli_completed",
        },
    )

    assert result["status"] == "failed"
    assert result["execution_mode"] == "cli_failed"
    assert result["error_type"] == "RuntimeError"


def test_run_review_in_snapshot_allows_explicit_not_run_envelope(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()

    result = run_review_in_snapshot(
        source,
        lambda _snapshot: {"findings": [], "metrics": {}},
        envelope_context={
            "provider": "codex",
            "case_id": "not-run-1",
            "diff_id": "probe-not-run",
            "prompt_id": "review-v1",
            "status": "not_run",
            "execution_mode": "not_run",
        },
    )

    assert result["status"] == "not_run"
    assert result["execution_mode"] == "not_run"


def test_run_review_in_snapshot_rejects_oversized_snapshot(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot size limit"):
        run_review_in_snapshot(source, lambda _: {"verdict": "APPROVE"}, max_bytes=1)


def test_run_review_in_snapshot_excludes_generated_directories(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")
    for directory in (".omc", "node_modules", ".venv", "dist", "build", "coverage", "__pycache__", ".next"):
        generated = source / directory
        generated.mkdir()
        (generated / "generated.bin").write_bytes(b"generated")

    def review(snapshot_path: Path) -> dict[str, str]:
        assert (snapshot_path / "diff.txt").exists()
        for directory in (".omc", "node_modules", ".venv", "dist", "build", "coverage", "__pycache__", ".next"):
            assert not (snapshot_path / directory).exists()
        return {"verdict": "APPROVE"}

    result = run_review_in_snapshot(source, review, max_bytes=100)

    assert result["execution_metadata"]["snapshot_used"] is True


def test_run_review_in_snapshot_applies_default_size_limit(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")

    result = run_review_in_snapshot(source, lambda _: {"verdict": "APPROVE"})

    assert result["result"]["verdict"] == "APPROVE"


def test_run_review_in_snapshot_accepts_project_specific_ignore_policy(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")
    (source / "generated-cache").mkdir()
    (source / "generated-cache" / "large.bin").write_bytes(b"generated")
    (source / ".omc").mkdir()
    (source / ".omc" / "state.json").write_text("private", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "dependency.js").write_text("dependency", encoding="utf-8")

    def review(snapshot_path: Path) -> dict[str, str]:
        assert (snapshot_path / "diff.txt").exists()
        assert not (snapshot_path / "generated-cache").exists()
        assert not (snapshot_path / ".omc").exists()
        assert not (snapshot_path / "node_modules").exists()
        return {"verdict": "APPROVE"}

    result = run_review_in_snapshot(
        source,
        review,
        ignored_dirs={".git", "generated-cache"},
        max_bytes=100,
    )

    assert result["execution_metadata"]["workspace_mutated"] is False
