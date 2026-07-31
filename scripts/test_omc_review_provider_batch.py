from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_review_provider_batch import (
    ProviderBatchError,
    build_codex_provider_batch,
    build_native_codex_review_batch,
    build_omc_execution_batch,
    ingest_omc_execution_results,
)
from omc_review_execution_bundle import (
    ExecutionBundleError,
    collect_execution_bundle,
    prepare_execution_bundle,
)


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


def test_build_codex_provider_batch_includes_staged_new_files(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "case-1"
    workspace.mkdir(parents=True)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "omc@example.com")
    _git(workspace, "config", "user.name", "OMC")
    (workspace / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(workspace, "add", "README.md")
    _git(workspace, "commit", "-qm", "baseline")
    (workspace / "new_service.py").write_text("value = 1\n", encoding="utf-8")
    _git(workspace, "add", "new_service.py")
    diff = _git(workspace, "diff", "--binary", "HEAD")
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


def test_native_codex_provider_batch_requires_durable_per_case_artifact(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    artifact_dir = tmp_path / "durable-artifacts"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [{
            "case_id": "case-1",
            "source_commit": "abc123",
            "diff_path": "case-1.diff",
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "anonymized": True,
            "anonymization_status": "passed",
        }],
    }
    captured: dict[str, object] = {}

    def run_review(_workdir: Path, **kwargs):
        artifact = kwargs["artifact_path"]
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"retained": true}\n', encoding="utf-8")
        captured.update(kwargs)
        return {
            "provider": "codex",
            "case_id": kwargs["case_id"],
            "diff_id": kwargs["diff_id"],
            "status": "completed",
            "runner": "codex native review",
            "execution_artifacts": {"durable_output_retained": True},
        }

    result = build_native_codex_review_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        artifact_dir=artifact_dir,
        timeout_sec=30,
        run_review=run_review,
    )

    assert result["status"] == "completed_native_provider_runs_pending_adjudication"
    assert result["provider_runner"] == "codex native review"
    assert captured["source_commit"] == "abc123"
    assert captured["artifact_path"] == artifact_dir / "case-1.json"


def test_native_codex_provider_batch_rejects_ephemeral_artifact_directory(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
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

    with pytest.raises(ProviderBatchError, match="must not use an ephemeral directory"):
        build_native_codex_review_batch(
            manifest,
            diff_root=diff_root,
            workspace_root=workspace.parent,
            artifact_dir="/private/tmp/omc-review-artifacts",
            timeout_sec=30,
        )


def test_native_codex_provider_batch_recovers_durable_artifact_after_interruption(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    artifact_dir = tmp_path / "durable-artifacts"
    diff_root.mkdir()
    artifact_dir.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "approved_for_provider_execution",
        "candidates": [{
            "case_id": "case-1",
            "source_commit": "abc123",
            "diff_path": "case-1.diff",
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "anonymized": True,
            "anonymization_status": "passed",
        }],
    }
    raw_stdout = "No definite correctness regression was identified.\n"
    artifact = {
        "provider": "codex",
        "runner": "codex native review",
        "case_id": "case-1",
        "diff_sha256": manifest["candidates"][0]["diff_sha256"],
        "source_commit": "abc123",
        "exit_code": 0,
        "duration_ms": 99,
        "stdout": raw_stdout,
        "stderr": "",
        "captured_stdout_sha256": hashlib.sha256(raw_stdout.encode("utf-8")).hexdigest(),
        "retained_stdout_sha256": hashlib.sha256(raw_stdout.encode("utf-8")).hexdigest(),
    }
    (artifact_dir / "case-1.json").write_text(json.dumps(artifact), encoding="utf-8")

    result = build_native_codex_review_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        artifact_dir=artifact_dir,
        timeout_sec=30,
        run_review=lambda *_args, **_kwargs: pytest.fail("must recover instead of rerunning"),
    )

    provider = result["cases"][0]["providers"]["codex"]
    assert result["status"] == "completed_native_provider_runs_pending_adjudication"
    assert provider["verdict"] == "APPROVE"
    assert provider["execution_artifacts"]["verdict_source"] == "recovered_from_durable_artifact"


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


def test_omc_execution_batch_preflights_same_diff_and_hashes_skill(tmp_path: Path):
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

    assert batch["status"] == "ready_for_omc_execution"
    assert batch["cases"][0]["diff_sha256"] == manifest["candidates"][0]["diff_sha256"]
    assert batch["skill"]["sha256"] == hashlib.sha256(skill.read_bytes()).hexdigest()
    assert batch["cases"][0]["workspace"] == str(workspace)


def test_omc_execution_batch_persists_absolute_skill_path_for_later_ingestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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

    monkeypatch.chdir(tmp_path)
    batch = build_omc_execution_batch(
        manifest,
        diff_root=diff_root,
        workspace_root=workspace.parent,
        skill_path=Path("SKILL.md"),
        model="gpt-5.4",
        prompt="Review only the current uncommitted diff.",
    )

    assert batch["skill"]["path"] == str(skill.resolve())


def test_execution_bundle_prepares_resumable_case_inputs_and_collects_completed_output(
    tmp_path: Path,
):
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
    assert (tmp_path / "bundle" / "prompts" / "case-1.txt").read_text(encoding="utf-8") == (
        "review contract\n\nReview only the current uncommitted diff.\n"
    )
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


def test_execution_bundle_rejects_incomplete_or_mismatched_artifacts(tmp_path: Path):
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


def test_omc_execution_result_ingestion_requires_matching_packet_metadata(tmp_path: Path):
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
    raw_output = (
        "- [경미] Header is dropped — service.py:1\n"
        "evidence_class: behavioral_direct\n"
        "evidence: The changed value is never forwarded.\n"
        "VERDICT: REVISE\n"
    )

    result = ingest_omc_execution_results(batch, [{
        "case_id": "case-1",
        "diff_sha256": batch["cases"][0]["diff_sha256"],
        "model": "gpt-5.4",
        "skill_sha256": batch["skill"]["sha256"],
        "prompt_sha256": batch["prompt"]["sha256"],
        "recorded_at": "2026-07-29T00:00:00Z",
        "duration_ms": 100,
        "stdout": raw_output,
    }])

    provider = result["cases"][0]["providers"]["omc-review"]
    assert result["status"] == "completed_omc_runs_pending_adjudication"
    assert provider["verdict"] == "REVISE"
    assert provider["raw_output_sha256"] == hashlib.sha256(raw_output.encode("utf-8")).hexdigest()

    with pytest.raises(ProviderBatchError, match="prompt hash mismatch"):
        ingest_omc_execution_results(batch, [{
            "case_id": "case-1",
            "diff_sha256": batch["cases"][0]["diff_sha256"],
            "model": "gpt-5.4",
            "skill_sha256": batch["skill"]["sha256"],
            "prompt_sha256": "wrong",
            "recorded_at": "2026-07-29T00:00:00Z",
            "duration_ms": 100,
            "stdout": raw_output,
        }])


def test_omc_execution_result_ingestion_rejects_workspace_diff_drift(tmp_path: Path):
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
    (workspace / "service.py").write_text("value = 3\n", encoding="utf-8")

    with pytest.raises(ProviderBatchError, match="workspace diff hash mismatch: case-1"):
        ingest_omc_execution_results(batch, [{
            "case_id": "case-1",
            "diff_sha256": batch["cases"][0]["diff_sha256"],
            "model": "gpt-5.4",
            "skill_sha256": batch["skill"]["sha256"],
            "prompt_sha256": batch["prompt"]["sha256"],
            "recorded_at": "2026-07-29T00:00:00Z",
            "duration_ms": 100,
            "stdout": "VERDICT: APPROVE\n",
        }])


def test_omc_execution_result_ingestion_rejects_review_skill_drift(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
    skill = tmp_path / "SKILL.md"
    skill.write_text("review contract v1\n", encoding="utf-8")
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
    skill.write_text("review contract v2\n", encoding="utf-8")

    with pytest.raises(ProviderBatchError, match="review skill hash mismatch"):
        ingest_omc_execution_results(batch, [{
            "case_id": "case-1",
            "diff_sha256": batch["cases"][0]["diff_sha256"],
            "model": "gpt-5.4",
            "skill_sha256": batch["skill"]["sha256"],
            "prompt_sha256": batch["prompt"]["sha256"],
            "recorded_at": "2026-07-29T00:00:00Z",
            "duration_ms": 100,
            "stdout": "VERDICT: APPROVE\n",
        }])


def test_provider_batch_cli_prepares_omc_execution_packet(tmp_path: Path):
    workspace, diff = _workspace_with_diff(tmp_path)
    diff_root = tmp_path / "diffs"
    diff_root.mkdir()
    (diff_root / "case-1.diff").write_text(diff, encoding="utf-8")
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
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "omc-batch.json"
    skill = tmp_path / "SKILL.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    skill.write_text("review contract\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "omc_review_provider_batch.py"),
            "--prepare-omc",
            "--manifest", str(manifest_path),
            "--diff-root", str(diff_root),
            "--workspace-root", str(workspace.parent),
            "--skill-path", str(skill),
            "--model", "gpt-5.4",
            "--prompt", "Review only the current uncommitted diff.",
            "--output", str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "ready_for_omc_execution"
