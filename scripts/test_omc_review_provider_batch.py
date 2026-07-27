from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_provider_batch import ProviderBatchError, build_codex_provider_batch


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, capture_output=True
    ).stdout


def _workspace_with_diff(tmp_path: Path, case_id: str = "case-1") -> tuple[Path, str]:
    workspace = tmp_path / "workspaces" / case_id
    workspace.mkdir(parents=True)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "omc@example.com")
    _git(workspace, "config", "user.name", "OMC")
    (workspace / "service.py").write_text("value = 1\n", encoding="utf-8")
    _git(workspace, "add", "service.py")
    _git(workspace, "commit", "-qm", "baseline")
    (workspace / "service.py").write_text("value = 2\n", encoding="utf-8")
    return workspace, _git(workspace, "diff", "--binary")


def test_build_codex_provider_batch_runs_only_manifest_matched_workspaces(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [
            {
                "case_id": "case-1",
                "diff_path": "case-1.diff",
                "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            }
        ],
    }
    calls: list[Path] = []

    def run_review(workdir: Path, **kwargs):
        calls.append(workdir)
        return {
            "provider": "codex",
            "case_id": kwargs["case_id"],
            "diff_id": kwargs["diff_id"],
            "status": "completed",
            "verdict": "APPROVE",
        }

    result = build_codex_provider_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        timeout_sec=30,
        run_review=run_review,
    )

    assert calls == [workspace]
    assert result["status"] == "completed_provider_runs_pending_adjudication"
    assert result["cases"][0]["providers"]["codex"]["verdict"] == "APPROVE"
    assert result["cases"][0]["providers"]["omc-review"]["status"] == "not_run"


def test_build_codex_provider_batch_rejects_workspace_diff_mismatch(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff + "extra", encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [
            {
                "case_id": "case-1",
                "diff_path": "case-1.diff",
                "diff_sha256": hashlib.sha256((diff + "extra").encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            }
        ],
    }

    with pytest.raises(ProviderBatchError, match="workspace diff hash mismatch"):
        build_codex_provider_batch(
            manifest,
            diff_root=diff_root,
            workspace_root=workspace.parent,
            timeout_sec=30,
            run_review=lambda *_args, **_kwargs: pytest.fail("must not execute"),
        )


def test_build_codex_provider_batch_preflights_every_workspace_before_execution(tmp_path: Path):
    first_workspace, first_diff = _workspace_with_diff(tmp_path, "case-1")
    _, second_diff = _workspace_with_diff(tmp_path, "case-2")
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(first_diff, encoding="utf-8")
    # The input remains hash-valid, but cannot match the second workspace.
    mismatched_diff = second_diff + "extra"
    (diff_root / "case-2.diff").write_text(mismatched_diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [
            {
                "case_id": "case-1",
                "diff_path": "case-1.diff",
                "diff_sha256": hashlib.sha256(first_diff.encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            },
            {
                "case_id": "case-2",
                "diff_path": "case-2.diff",
                "diff_sha256": hashlib.sha256(mismatched_diff.encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            },
        ],
    }
    calls: list[Path] = []

    with pytest.raises(ProviderBatchError, match="workspace diff hash mismatch: case-2"):
        build_codex_provider_batch(
            manifest,
            diff_root=diff_root,
            workspace_root=first_workspace.parent,
            timeout_sec=30,
            run_review=lambda workdir, **_kwargs: calls.append(workdir) or {},
        )

    assert calls == []


def test_build_codex_provider_batch_allows_git_object_hash_only_difference(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    source_diff = re.sub(
        r"^index [0-9a-f]+\.\.[0-9a-f]+",
        "index 0000000..1111111",
        diff,
        flags=re.MULTILINE,
    )
    (diff_root / "case-1.diff").write_text(source_diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [
            {
                "case_id": "case-1",
                "diff_path": "case-1.diff",
                "diff_sha256": hashlib.sha256(source_diff.encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            }
        ],
    }

    result = build_codex_provider_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        timeout_sec=30,
        run_review=lambda _workdir, **kwargs: {
            "provider": "codex",
            "case_id": kwargs["case_id"],
            "diff_id": kwargs["diff_id"],
            "status": "completed",
            "verdict": "APPROVE",
        },
    )

    assert result["status"] == "completed_provider_runs_pending_adjudication"


def test_build_codex_provider_batch_reuses_completed_case_checkpoint(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    checkpoint_dir = tmp_path / "checkpoints"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [
            {
                "case_id": "case-1",
                "diff_path": "case-1.diff",
                "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            }
        ],
    }
    calls: list[Path] = []

    def run_review(workdir: Path, **kwargs):
        calls.append(workdir)
        return {
            "provider": "codex",
            "case_id": kwargs["case_id"],
            "diff_id": kwargs["diff_id"],
            "status": "completed",
            "verdict": "APPROVE",
        }

    first = build_codex_provider_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        checkpoint_dir=checkpoint_dir,
        timeout_sec=30,
        run_review=run_review,
    )
    second = build_codex_provider_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        checkpoint_dir=checkpoint_dir,
        timeout_sec=30,
        run_review=run_review,
    )

    assert calls == [workspace]
    assert (checkpoint_dir / "case-1.json").is_file()
    assert first["cases"] == second["cases"]


def test_build_codex_provider_batch_resumes_after_later_case_interrupts(tmp_path: Path):
    first_workspace, first_diff = _workspace_with_diff(tmp_path, "case-1")
    _, second_diff = _workspace_with_diff(tmp_path, "case-2")
    diff_root = tmp_path / "diffs"
    checkpoint_dir = tmp_path / "checkpoints"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(first_diff, encoding="utf-8")
    (diff_root / "case-2.diff").write_text(second_diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [
            {
                "case_id": "case-1",
                "diff_path": "case-1.diff",
                "diff_sha256": hashlib.sha256(first_diff.encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            },
            {
                "case_id": "case-2",
                "diff_path": "case-2.diff",
                "diff_sha256": hashlib.sha256(second_diff.encode("utf-8")).hexdigest(),
                "anonymized": True,
                "anonymization_status": "passed",
            },
        ],
    }
    first_run_calls: list[str] = []

    def interrupted_run_review(_workdir: Path, **kwargs):
        case_id = kwargs["case_id"]
        first_run_calls.append(case_id)
        if case_id == "case-2":
            raise RuntimeError("simulated interruption")
        return {
            "provider": "codex",
            "case_id": case_id,
            "diff_id": kwargs["diff_id"],
            "status": "completed",
            "verdict": "APPROVE",
        }

    with pytest.raises(RuntimeError, match="simulated interruption"):
        build_codex_provider_batch(
            manifest,
            diff_root=diff_root,
            workspace_root=first_workspace.parent,
            checkpoint_dir=checkpoint_dir,
            timeout_sec=30,
            run_review=interrupted_run_review,
        )

    resumed_calls: list[str] = []
    result = build_codex_provider_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=first_workspace.parent,
        checkpoint_dir=checkpoint_dir,
        timeout_sec=30,
        run_review=lambda _workdir, **kwargs: resumed_calls.append(kwargs["case_id"]) or {
            "provider": "codex",
            "case_id": kwargs["case_id"],
            "diff_id": kwargs["diff_id"],
            "status": "completed",
            "verdict": "APPROVE",
        },
    )

    assert first_run_calls == ["case-1", "case-2"]
    assert resumed_calls == ["case-2"]
    assert result["status"] == "completed_provider_runs_pending_adjudication"
