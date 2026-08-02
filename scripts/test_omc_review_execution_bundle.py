from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_review_execution_bundle as execution_bundle
from omc_review_execution_bundle import (
    ExecutionBundleError,
    collect_execution_bundle,
    prepare_execution_bundle,
)
from omc_review_provider_batch import build_omc_execution_batch


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, capture_output=True
    ).stdout


def _workspace_with_diff(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "workspaces" / "case-1"
    workspace.mkdir(parents=True)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    (workspace / "service.py").write_text("value = 1\n", encoding="utf-8")
    _git(workspace, "add", "service.py")
    _git(workspace, "commit", "-qm", "baseline")
    (workspace / "service.py").write_text("value = 2\n", encoding="utf-8")
    return workspace, _git(workspace, "diff", "--binary", "HEAD")


def test_prepares_resumable_case_inputs_and_collects_completed_output(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
    skill = tmp_path / "SKILL.md"
    skill.write_text("review contract\n", encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [{
            "case_id": "case-1",
            "diff_path": "case-1.diff",
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "anonymized": True,
            "anonymization_status": "passed",
        }],
    }
    batch = build_omc_execution_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        skill_path=skill,
        model="gpt-5.4",
        prompt="Review only the current uncommitted diff.",
    )

    bundle = prepare_execution_bundle(batch, output_dir=tmp_path / "bundle")

    assert bundle["status"] == "ready_for_external_execution"
    assert (tmp_path / "bundle" / "prompts" / "case-1.txt").read_text(
        encoding="utf-8"
    ) == "review contract\n\nReview only the current uncommitted diff.\n"
    assert bundle["cases"][0]["status"] == "pending"

    artifact_path = tmp_path / "bundle" / "artifacts" / "case-1.json"
    artifact_path.parent.mkdir()
    artifact_path.write_text(json.dumps({
        "case_id": "case-1",
        "status": "completed",
        "diff_sha256": batch["cases"][0]["diff_sha256"],
        "model": batch["model"],
        "skill_sha256": batch["skill"]["sha256"],
        "prompt_sha256": batch["prompt"]["sha256"],
        "recorded_at": "2026-07-30T00:00:00Z",
        "duration_ms": 100,
        "stdout": "VERDICT: APPROVE\n",
        "stderr": "",
    }), encoding="utf-8")

    collected = collect_execution_bundle(tmp_path / "bundle")

    assert collected["status"] == "completed_omc_runs_pending_adjudication"
    assert collected["cases"][0]["providers"]["omc-review"]["verdict"] == "APPROVE"


def test_rejects_incomplete_artifacts(tmp_path: Path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "execution-batch.json").write_text(json.dumps({
        "status": "ready_for_omc_execution",
        "batch_id": "omc-review-test",
        "model": "gpt-5.4",
        "skill": {"path": str(tmp_path / "SKILL.md"), "sha256": "skill"},
        "prompt": {"sha256": "prompt", "text": "review"},
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff",
            "workspace_diff_sha256": "diff",
            "workspace": str(tmp_path),
        }],
    }), encoding="utf-8")

    with pytest.raises(ExecutionBundleError, match="missing completed artifact: case-1"):
        collect_execution_bundle(bundle_dir)


def test_persists_byte_output_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    bundle_dir = tmp_path / "bundle"
    prompt_dir = bundle_dir / "prompts"
    prompt_dir.mkdir(parents=True)
    binary = tmp_path / "codex"
    binary.write_text("", encoding="utf-8")
    (bundle_dir / "execution-batch.json").write_text(json.dumps({
        "status": "ready_for_omc_execution",
        "model": "gpt-5.4",
        "skill": {"sha256": "skill"},
        "prompt": {"sha256": "prompt"},
        "cases": [{
            "case_id": "case-1",
            "diff_sha256": "diff",
            "workspace": str(tmp_path),
        }],
    }), encoding="utf-8")
    (prompt_dir / "case-1.txt").write_text("review", encoding="utf-8")

    @contextmanager
    def isolated_workspace(*_args, **_kwargs):
        yield SimpleNamespace(path=tmp_path)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd="codex",
            timeout=1,
            output=b"partial output",
            stderr=b"partial error",
        )

    monkeypatch.setattr(execution_bundle, "_validate_batch", lambda _batch: None)
    monkeypatch.setattr(execution_bundle, "_isolated_review_workspace", isolated_workspace)
    monkeypatch.setattr(execution_bundle.subprocess, "run", timeout)

    artifact = execution_bundle.run_execution_case(
        bundle_dir,
        case_id="case-1",
        codex_binary=binary,
        timeout_sec=1,
    )

    assert artifact["status"] == "failed"
    assert artifact["exit_code"] == 124
    assert artifact["stdout"] == "partial output"
    assert artifact["stderr"] == "partial error\nprovider execution timed out"
    assert not (bundle_dir / "artifacts" / "case-1.running.json").exists()
