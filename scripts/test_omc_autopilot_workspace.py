from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import omc_autopilot_workspace as workspace


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "autopilot@example.com")
    _git(root, "config", "user.name", "Autopilot Test")
    (root / "docs").mkdir()
    (root / "docs" / "status.md").write_text("status: pendng\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    return root


def _contract(root: Path, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": workspace.WORK_CONTRACT_SCHEMA_VERSION,
        "run_id": "run-001",
        "instruction_sha256": "a" * 64,
        "base_commit": _git(root, "rev-parse", "HEAD"),
        "source_identity": workspace.source_identity(root),
        "allowed_paths": ["docs/status.md"],
        "allowed_operations": ["modify"],
        "change_class": "document_only",
        "test_policy": "optional",
        "verification_commands": [],
        "pipeline_mode": "lite",
        "executor": "codex",
        "required_capabilities": ["workspace_write_confined"],
        "candidate_branch": "codex/autopilot-safe-test",
        "promotion_policy": "branch_ref_only",
    }
    payload.update(overrides)
    return payload


def test_freeze_work_contract_retries_short_writes(tmp_path: Path, monkeypatch) -> None:
    root = _repository(tmp_path)
    output = tmp_path / "contract.json"
    real_write = os.write
    write_calls = 0

    def short_write(fd: int, data: bytes) -> int:
        nonlocal write_calls
        write_calls += 1
        return real_write(fd, data[: max(1, len(data) // 2)])

    monkeypatch.setattr(workspace.os, "write", short_write)
    frozen = workspace.freeze_work_contract(output, _contract(root))

    assert write_calls > 1
    assert workspace.load_work_contract(output) == frozen


def test_freeze_and_load_work_contract_rejects_tampering(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    path = tmp_path / "contract.json"

    frozen = workspace.freeze_work_contract(path, _contract(root))
    assert workspace.load_work_contract(path)["contract_sha256"] == frozen["contract_sha256"]

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["allowed_paths"].append("scripts/extra.py")
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(workspace.AutopilotWorkspaceError, match="work_contract_digest_mismatch"):
        workspace.load_work_contract(path)


def test_work_contract_rejects_unsafe_run_id(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    payload = _contract(root, run_id="../../outside")

    with pytest.raises(workspace.AutopilotWorkspaceError, match="work_contract_run_id_invalid"):
        workspace.freeze_work_contract(tmp_path / "contract.json", payload)


def test_work_contract_rejects_normalized_git_directory_scope(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    payload = _contract(root, allowed_paths=["./.git/"])

    with pytest.raises(workspace.AutopilotWorkspaceError, match="allowed_path_unsafe"):
        workspace.validate_work_contract(payload)


def test_materialize_clone_does_not_share_objects_or_remote(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.validate_work_contract(_contract(source))
    target = tmp_path / "isolated"

    workspace.materialize_isolated_clone(source, target, contract)

    assert _git(target, "rev-parse", "HEAD") == contract["base_commit"]
    assert _git(target, "remote") == ""
    alternates = target / ".git" / "objects" / "info" / "alternates"
    assert not alternates.exists()
    assert workspace.source_identity(source) == contract["source_identity"]


def test_snapshot_detects_untracked_ignored_deleted_and_symlink(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    before = workspace.snapshot_workspace(root)
    (root / "docs" / "status.md").unlink()
    (root / "extra.txt").write_text("extra\n", encoding="utf-8")
    (root / "ignored.log").write_text("ignored\n", encoding="utf-8")
    (root / "unsafe-link").symlink_to("docs/status.md")

    delta = workspace.compute_workspace_delta(before, workspace.snapshot_workspace(root))

    assert {(item["path"], item["operation"]) for item in delta} >= {
        ("docs/status.md", "delete"),
        ("extra.txt", "create"),
        ("ignored.log", "create"),
        ("unsafe-link", "create"),
    }
    assert next(item for item in delta if item["path"] == "unsafe-link")["kind"] == "symlink"


def test_snapshot_does_not_exempt_runtime_symlink(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    before = workspace.snapshot_workspace(root)
    outside = tmp_path / "outside.txt"
    outside.write_text("sentinel\n", encoding="utf-8")
    runtime = root / ".omc"
    runtime.mkdir()
    (runtime / "cost_log.jsonl").symlink_to(outside)

    delta = workspace.compute_workspace_delta(before, workspace.snapshot_workspace(root))

    runtime_change = next(item for item in delta if item["path"] == ".omc/cost_log.jsonl")
    assert runtime_change["kind"] == "symlink"


def test_snapshot_hashes_files_without_reading_them_whole(tmp_path: Path, monkeypatch) -> None:
    root = _repository(tmp_path)

    def reject_read_bytes(_path: Path) -> bytes:
        raise AssertionError("snapshot must stream file contents")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)

    snapshot = workspace.snapshot_workspace(root)

    assert snapshot["docs/status.md"]["sha256"]


def test_snapshot_ignores_executor_cost_telemetry(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    before = workspace.snapshot_workspace(root)
    cost_log = root / ".omc" / "cost_log.jsonl"
    cost_log.parent.mkdir(exist_ok=True)
    cost_log.write_text('{"tokens": 1}\n', encoding="utf-8")

    assert workspace.compute_workspace_delta(before, workspace.snapshot_workspace(root)) == []


def test_scope_gate_rejects_extra_file_and_symlink(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.validate_work_contract(_contract(source))
    before = workspace.snapshot_workspace(source)
    (source / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
    (source / "scripts").mkdir()
    (source / "scripts" / "extra.py").write_text("pass\n", encoding="utf-8")
    (source / "unsafe-link").symlink_to("docs/status.md")
    delta = workspace.compute_workspace_delta(before, workspace.snapshot_workspace(source))

    violations = workspace.validate_scope(delta, contract)

    assert "path_not_allowed:scripts/extra.py" in violations
    assert "path_not_allowed:unsafe-link" in violations
    assert "unsafe_delta_type:unsafe-link:symlink" in violations


def test_scope_gate_preserves_directory_prefix_contract(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.validate_work_contract(_contract(source, allowed_paths=["docs/"]))
    before = workspace.snapshot_workspace(source)
    (source / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
    delta = workspace.compute_workspace_delta(before, workspace.snapshot_workspace(source))

    assert contract["allowed_paths"] == ["docs/"]
    assert workspace.validate_scope(delta, contract) == []


def test_review_packet_binds_contract_commits_delta_and_tests(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.freeze_work_contract(tmp_path / "contract.json", _contract(source))
    before = workspace.snapshot_workspace(source)
    (source / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
    delta = workspace.compute_workspace_delta(before, workspace.snapshot_workspace(source))
    _git(source, "add", "docs/status.md")
    _git(source, "commit", "-qm", "fix status")

    packet = workspace.build_review_packet(
        contract=contract,
        candidate_commit=_git(source, "rev-parse", "HEAD"),
        delta=delta,
        patch=_git(source, "diff", f"{contract['base_commit']}..HEAD", "--binary"),
        tests=[{"command": "pytest -q", "exit_code": 0, "output_sha256": "b" * 64}],
        executor_receipt={"workspace_write_confined": True},
    )

    assert workspace.validate_review_packet(packet) == []
    packet["candidate_commit"] = "f" * 40
    assert "review_packet_digest_mismatch" in workspace.validate_review_packet(packet)


def test_promote_candidate_branch_does_not_change_source_head_or_worktree(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.validate_work_contract(_contract(source))
    isolated = tmp_path / "isolated"
    workspace.materialize_isolated_clone(source, isolated, contract)
    _git(isolated, "config", "user.email", "autopilot@example.com")
    _git(isolated, "config", "user.name", "Autopilot Test")
    (isolated / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
    _git(isolated, "add", "docs/status.md")
    _git(isolated, "commit", "-qm", "fix status")
    candidate = _git(isolated, "rev-parse", "HEAD")
    source_head = _git(source, "rev-parse", "HEAD")

    promoted = workspace.promote_candidate_branch(
        source=source,
        isolated=isolated,
        contract=contract,
        candidate_commit=candidate,
    )

    assert promoted == "codex/autopilot-safe-test"
    assert _git(source, "rev-parse", "HEAD") == source_head
    assert _git(source, "status", "--porcelain") == ""
    assert _git(source, "rev-parse", promoted) == candidate


def test_promotion_rejects_source_drift(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.validate_work_contract(_contract(source))
    isolated = tmp_path / "isolated"
    workspace.materialize_isolated_clone(source, isolated, contract)
    (source / "unexpected.txt").write_text("drift\n", encoding="utf-8")

    with pytest.raises(workspace.AutopilotWorkspaceError, match="promotion_precondition_changed"):
        workspace.promote_candidate_branch(
            source=source,
            isolated=isolated,
            contract=contract,
            candidate_commit=_git(isolated, "rev-parse", "HEAD"),
        )


def test_candidate_scope_rejects_out_of_contract_commit_path(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.validate_work_contract(_contract(source))
    isolated = tmp_path / "isolated"
    workspace.materialize_isolated_clone(source, isolated, contract)
    _git(isolated, "config", "user.email", "autopilot@example.com")
    _git(isolated, "config", "user.name", "Autopilot Test")
    (isolated / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
    (isolated / "extra.py").write_text("hidden = True\n", encoding="utf-8")
    _git(isolated, "add", "docs/status.md", "extra.py")
    _git(isolated, "commit", "-qm", "candidate")
    candidate = _git(isolated, "rev-parse", "HEAD")
    expected_delta = [
        {
            "path": "docs/status.md",
            "operation": "modify",
            "kind": "file",
            "before": {},
            "after": {},
        }
    ]

    with pytest.raises(workspace.AutopilotWorkspaceError, match="candidate_scope_mismatch"):
        workspace.validate_candidate_scope(
            isolated=isolated,
            contract=contract,
            candidate_commit=candidate,
            expected_delta=expected_delta,
        )


def test_candidate_scope_rejects_content_not_bound_to_snapshot(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    contract = workspace.validate_work_contract(_contract(source))
    isolated = tmp_path / "isolated"
    workspace.materialize_isolated_clone(source, isolated, contract)
    _git(isolated, "config", "user.email", "autopilot@example.com")
    _git(isolated, "config", "user.name", "Autopilot Test")
    before = workspace.snapshot_workspace(isolated)
    (isolated / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
    expected_delta = workspace.compute_workspace_delta(
        before, workspace.snapshot_workspace(isolated)
    )
    expected_delta[0]["after"]["sha256"] = "f" * 64
    _git(isolated, "add", "docs/status.md")
    _git(isolated, "commit", "-qm", "candidate")
    candidate = _git(isolated, "rev-parse", "HEAD")

    with pytest.raises(workspace.AutopilotWorkspaceError, match="candidate_content_mismatch"):
        workspace.validate_candidate_scope(
            isolated=isolated,
            contract=contract,
            candidate_commit=candidate,
            expected_delta=expected_delta,
        )
