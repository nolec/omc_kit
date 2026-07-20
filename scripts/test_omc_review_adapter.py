from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_adapter import isolated_snapshot, build_review_execution_envelope


def test_isolated_snapshot_copies_source_and_cleans_up(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "index").write_text("ignored", encoding="utf-8")

    snapshot_path = None
    with isolated_snapshot(source) as snapshot:
        snapshot_path = snapshot.path
        assert snapshot.path.exists()
        assert (snapshot.path / "diff.txt").read_text(encoding="utf-8") == "original"
        assert not (snapshot.path / ".git").exists()
        assert snapshot.mutated is False

    assert snapshot_path is not None
    assert not snapshot_path.exists()
    assert snapshot.final_hash == snapshot.initial_hash


def test_isolated_snapshot_reports_workspace_mutation(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")

    with isolated_snapshot(source) as snapshot:
        (snapshot.path / "diff.txt").write_text("changed", encoding="utf-8")
        (snapshot.path / "generated.log").write_text("created", encoding="utf-8")

    assert snapshot.mutated is True
    assert snapshot.final_hash != snapshot.initial_hash


def test_isolated_snapshot_reports_file_mode_mutation(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    executable = source / "run.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o644)

    with isolated_snapshot(source) as snapshot:
        (snapshot.path / "run.sh").chmod(0o755)

    assert snapshot.mutated is True


def test_isolated_snapshot_rejects_source_symlink(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")
    (source / "linked.txt").symlink_to(target)

    with pytest.raises(ValueError, match="symlinks"):
        with isolated_snapshot(source):
            pass


def test_isolated_snapshot_cleans_up_after_consumer_error(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "diff.txt").write_text("original", encoding="utf-8")

    snapshot_path = None
    with pytest.raises(RuntimeError, match="consumer failed"):
        with isolated_snapshot(source) as snapshot:
            snapshot_path = snapshot.path
            raise RuntimeError("consumer failed")

    assert snapshot_path is not None
    assert not snapshot_path.exists()


def test_build_review_execution_envelope_requires_explicit_context_and_preserves_metadata():
    metadata = {"snapshot_used": True, "workspace_mutated": False}

    envelope = build_review_execution_envelope(
        provider="codex",
        case_id="null-guard-1",
        diff_id="probe-null-guard",
        prompt_id="review-v1",
        status="completed",
        execution_mode="cli_completed",
        runner="codex review",
        model="gpt-5.6-luna",
        result={"findings": [{"id": "null-guard"}], "metrics": {}},
        execution_metadata=metadata,
    )

    assert envelope["provider"] == "codex"
    assert envelope["case_id"] == "null-guard-1"
    assert envelope["execution_metadata"] == metadata
    assert envelope["findings"] == [{"id": "null-guard"}]


def test_build_review_execution_envelope_does_not_infer_missing_identity():
    with pytest.raises(ValueError, match="case_id"):
        build_review_execution_envelope(
            provider="codex",
            case_id="",
            diff_id="probe-null-guard",
            prompt_id="review-v1",
            status="completed",
            execution_mode="cli_completed",
            result={"findings": [], "metrics": {}},
            execution_metadata={"snapshot_used": True, "workspace_mutated": False},
        )


def test_isolated_snapshot_rejects_missing_source(tmp_path: Path):
    with pytest.raises(ValueError, match="source directory"):
        with isolated_snapshot(tmp_path / "missing"):
            pass
