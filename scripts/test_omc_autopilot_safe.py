from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import omc_autopilot_safe as safe
import omc_autopilot_workspace as workspace
import omc_autopilot as autopilot
import omc_mission as mission


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "safe@example.com")
    _git(root, "config", "user.name", "Safe Pipeline")
    (root / "docs").mkdir()
    (root / "docs" / "status.md").write_text("status: pendng\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    return root


def _freeze_contract(
    source: Path,
    tmp_path: Path,
    *,
    verification_commands: list[list[str]] | None = None,
    pipeline_mode: str = "lite",
    allowed_paths: list[str] | None = None,
    instruction: str | None = None,
) -> Path:
    instruction = (
        instruction
        or "docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다"
    )
    payload = {
        "schema_version": workspace.WORK_CONTRACT_SCHEMA_VERSION,
        "run_id": "safe-run-001",
        "instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
        "base_commit": _git(source, "rev-parse", "HEAD"),
        "source_identity": workspace.source_identity(source),
        "allowed_paths": allowed_paths or ["docs/status.md"],
        "allowed_operations": ["modify"],
        "change_class": "document_only",
        "test_policy": "optional",
        "verification_commands": verification_commands or [],
        "pipeline_mode": pipeline_mode,
        "executor": "codex",
        "required_capabilities": ["workspace_write_confined"],
        "candidate_branch": "codex/safe-candidate",
        "promotion_policy": "branch_ref_only",
    }
    path = tmp_path / "contract.json"
    workspace.freeze_work_contract(path, payload)
    return path


def _freeze_mission_contract(
    source: Path,
    tmp_path: Path,
    instruction: str,
    *,
    approval_session_id: str = "session-1",
) -> Path:
    packet_path = tmp_path / "mission.json"
    packet = mission.freeze_mission_packet(
        packet_path,
        {
            "schema_version": mission.MISSION_SCHEMA_VERSION,
            "request_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
            "base_commit": _git(source, "rev-parse", "HEAD"),
            "outcome": "문서 오타를 안전하게 수정한다.",
            "deliverables": ["수정된 문서"],
            "definition_of_done": ["오타 수정", "검증 통과"],
            "non_goals": ["배포"],
            "validation": {"max_total_rounds": 2, "max_revisions_per_issue": 1},
        },
    )
    receipt = mission.build_mission_approval_receipt(
        decision_id="mission-1", session_id="session-1", packet=packet
    )
    approval_path = tmp_path / "mission-approval.json"
    approval_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    payload = {
        "schema_version": workspace.WORK_CONTRACT_SCHEMA_VERSION_V2,
        "run_id": "safe-mission-run-001",
        "instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
        "base_commit": _git(source, "rev-parse", "HEAD"),
        "source_identity": workspace.source_identity(source),
        "allowed_paths": ["docs/status.md"],
        "allowed_operations": ["modify"],
        "change_class": "document_only",
        "test_policy": "optional",
        "verification_commands": [],
        "pipeline_mode": "lite",
        "executor": "codex",
        "required_capabilities": ["workspace_write_confined"],
        "candidate_branch": "codex/safe-mission-candidate",
        "promotion_policy": "branch_ref_only",
        "mission_packet_path": packet_path.name,
        "mission_packet_sha256": packet["packet_sha256"],
        "mission_approval_path": approval_path.name,
        "mission_approval_sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest(),
        "mission_approval_session_id": approval_session_id,
    }
    contract_path = tmp_path / "mission-contract.json"
    workspace.freeze_work_contract(contract_path, payload)
    return contract_path


def test_safe_pipeline_binds_same_approved_mission_to_task_and_review(tmp_path: Path) -> None:
    source = _source(tmp_path)
    instruction = "docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다"
    contract = _freeze_mission_contract(source, tmp_path, instruction)
    observed: dict[str, str] = {}

    def task(root: Path, prompt: str) -> tuple[int, str]:
        observed["task"] = prompt
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    def review(root: Path, prompt: str) -> tuple[int, str]:
        observed["review"] = prompt
        return _approve_review(root, prompt)

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction=instruction,
        task_runner=task,
        review_runner=review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "candidate_ready"
    digest = result["mission_packet_sha256"]
    assert digest in observed["task"]
    assert digest in observed["review"]


def test_safe_pipeline_blocks_invalid_mission_before_provider(tmp_path: Path) -> None:
    source = _source(tmp_path)
    instruction = "docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다"
    contract = _freeze_mission_contract(source, tmp_path, instruction)
    (tmp_path / "mission-approval.json").write_text("{}", encoding="utf-8")
    calls = {"task": 0}

    def task(_root: Path, _prompt: str) -> tuple[int, str]:
        calls["task"] += 1
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction=instruction,
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "mission_approval_contract_mismatch"
    assert calls["task"] == 0


def test_safe_pipeline_blocks_wrong_mission_approval_session_before_provider(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    instruction = "docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다"
    contract = _freeze_mission_contract(
        source, tmp_path, instruction, approval_session_id="session-2"
    )
    calls = {"task": 0}

    def task(_root: Path, _prompt: str) -> tuple[int, str]:
        calls["task"] += 1
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction=instruction,
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "mission_approval_invalid"
    assert calls["task"] == 0


@pytest.mark.parametrize(
    ("mission_request", "mission_base", "reason"),
    [
        ("f" * 64, None, "mission_request_identity_mismatch"),
        (None, "f" * 40, "mission_base_commit_mismatch"),
    ],
)
def test_safe_pipeline_blocks_mission_identity_drift_before_provider(
    tmp_path: Path,
    mission_request: str | None,
    mission_base: str | None,
    reason: str,
) -> None:
    source = _source(tmp_path)
    instruction = "docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다"
    contract = _freeze_mission_contract(source, tmp_path, instruction)
    packet_path = tmp_path / "mission.json"
    payload = json.loads(packet_path.read_text(encoding="utf-8"))
    payload.pop("packet_sha256")
    if mission_request is not None:
        payload["request_sha256"] = mission_request
    if mission_base is not None:
        payload["base_commit"] = mission_base
    packet_path.unlink()
    packet = mission.freeze_mission_packet(packet_path, payload)
    receipt_path = tmp_path / "mission-approval.json"
    receipt = mission.build_mission_approval_receipt(
        decision_id="mission-2", session_id="session-1", packet=packet
    )
    receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    contract_payload = json.loads(contract.read_text(encoding="utf-8"))
    contract_payload.pop("contract_sha256")
    contract_payload["mission_packet_sha256"] = packet["packet_sha256"]
    contract_payload["mission_approval_sha256"] = hashlib.sha256(
        receipt_path.read_bytes()
    ).hexdigest()
    contract.unlink()
    workspace.freeze_work_contract(contract, contract_payload)
    calls = {"task": 0}

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction=instruction,
        task_runner=lambda _root, _prompt: (
            calls.__setitem__("task", calls["task"] + 1) or 0,
            "VERDICT: PROCEED",
        ),
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["reason_code"] == reason
    assert calls["task"] == 0


def test_safe_pipeline_reads_approval_bytes_once(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path)
    instruction = "docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다"
    contract = _freeze_mission_contract(source, tmp_path, instruction)
    real_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path.name == "mission-approval.json":
            raise AssertionError("approval must be decoded from the hashed bytes")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction=instruction,
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "candidate_ready"


def _approve_review(_root: Path, prompt: str) -> tuple[int, str]:
    digest = re.search(r"REVIEW_PACKET_SHA256: ([0-9a-f]{64})", prompt)
    assert digest
    return 0, f"REVIEW_PACKET_SHA256: {digest.group(1)}\nVERDICT: APPROVE"


def test_verification_command_uses_workspace_confined_macos_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(safe.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        safe.shutil,
        "which",
        lambda command: "/usr/bin/sandbox-exec" if command == "sandbox-exec" else None,
    )

    command = safe._confined_verification_command(tmp_path, ["pytest", "-q"])

    assert command[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert "(deny network*)" in command[2]
    assert "(deny file-write*)" in command[2]
    assert f'(subpath "{tmp_path}")' in command[2]
    assert command[-3:] == ["--", "pytest", "-q"]


def test_verification_fails_closed_without_confinement(
    tmp_path: Path, monkeypatch
) -> None:
    marker = tmp_path / "must-not-run"
    monkeypatch.setattr(safe.platform, "system", lambda: "unsupported")

    receipts, error = safe._run_verification(
        tmp_path,
        [[sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]],
        deadline_monotonic=time.monotonic() + 10,
    )

    assert error == "verification_confinement_unavailable"
    assert receipts[0]["confinement_unavailable"] is True
    assert not marker.exists()


def test_verification_output_limit_terminates_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        safe,
        "_confined_verification_command",
        lambda _root, argv: argv,
        raising=False,
    )
    command = [sys.executable, "-c", "print('x' * 10000)"]

    receipts, error = safe._run_verification(
        tmp_path,
        [command],
        deadline_monotonic=time.monotonic() + 10,
        max_output_bytes=128,
    )

    assert error == "verification_output_limit_exceeded"
    assert receipts[0]["output_limit_exceeded"] is True


def test_verification_output_limit_never_uses_unbounded_file_read(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        safe,
        "_confined_verification_command",
        lambda _root, argv: argv,
        raising=False,
    )
    monkeypatch.setattr(
        safe.Path,
        "read_bytes",
        lambda _path: pytest.fail("verification output must use bounded reads"),
    )

    receipts, error = safe._run_verification(
        tmp_path,
        [[sys.executable, "-c", "print('x' * 10000)"]],
        deadline_monotonic=time.monotonic() + 10,
        max_output_bytes=128,
    )

    assert error == "verification_output_limit_exceeded"
    assert receipts[0]["output_limit_exceeded"] is True


def test_verification_output_capture_is_hard_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        safe,
        "_confined_verification_command",
        lambda _root, argv: argv,
        raising=False,
    )
    preserved: list[Path] = []

    class PreservedDirectory:
        def __init__(self, *, prefix: str, dir: Path) -> None:
            self.path = Path(dir) / f"{prefix}preserved"

        def __enter__(self) -> str:
            self.path.mkdir(parents=True)
            preserved.append(self.path)
            return str(self.path)

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(safe.tempfile, "TemporaryDirectory", PreservedDirectory)

    receipts, error = safe._run_verification(
        tmp_path,
        [[sys.executable, "-c", "import os; os.write(1, b'x' * (16 * 1024 * 1024))"]],
        deadline_monotonic=time.monotonic() + 10,
        max_output_bytes=128,
    )

    assert error == "verification_output_limit_exceeded"
    assert receipts[0]["output_limit_exceeded"] is True
    captured_files = [path for root in preserved for path in root.glob("std*")]
    assert sum(path.stat().st_size for path in captured_files) <= 129


def test_verification_receives_remaining_deadline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(safe, "_confined_verification_command", lambda _root, argv: argv)
    receipts, error = safe._run_verification(
        tmp_path,
        [[sys.executable, "-c", "print('ok')"]],
        deadline_monotonic=time.monotonic() + 30,
    )

    assert error is None
    assert receipts[0]["exit_code"] == 0


def test_verification_timeout_is_recorded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(safe, "_confined_verification_command", lambda _root, argv: argv)
    receipts, error = safe._run_verification(
        tmp_path,
        [[sys.executable, "-c", "import time; time.sleep(5)"]],
        deadline_monotonic=time.monotonic() + 0.05,
    )

    assert error == "verification_timeout"
    assert receipts == [
        {
            "argv": [sys.executable, "-c", "import time; time.sleep(5)"],
            "exit_code": None,
            "output_sha256": hashlib.sha256(b"").hexdigest(),
            "timed_out": True,
        }
    ]


def test_safe_pipeline_blocks_scope_violation_before_review(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    calls = {"review": 0}

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        (root / "extra.py").write_text("pass\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    def review(root: Path, prompt: str) -> tuple[int, str]:
        calls["review"] += 1
        return _approve_review(root, prompt)

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "scope_violation"
    assert calls["review"] == 0
    assert _git(source, "status", "--porcelain") == ""
    branch = subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/codex/safe-candidate"],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )
    assert branch.returncode != 0


def test_safe_pipeline_uses_terminal_task_verdict(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED\nREASON_CODE: test_failure\nVERDICT: BLOCK"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "task_failed"


def test_safe_pipeline_rejects_task_commit_even_when_worktree_hides_extra_file(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "extra.py").write_text("hidden = True\n", encoding="utf-8")
        _git(root, "add", "extra.py")
        _git(root, "commit", "-qm", "hide extra file in history")
        (root / "extra.py").unlink()
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "task_git_state_changed"


def test_safe_pipeline_rejects_task_git_control_plane_change(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / ".gitattributes").write_text(
        "docs/status.md filter=escape\n", encoding="utf-8"
    )
    _git(source, "add", ".gitattributes")
    _git(source, "commit", "-qm", "add attributes")
    contract = _freeze_contract(source, tmp_path)
    marker = tmp_path / "outside-marker"

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        _git(
            root,
            "config",
            "filter.escape.clean",
            f"sh -c 'touch {marker}; cat'",
        )
        _git(root, "config", "filter.escape.required", "true")
        (root / "docs" / "status.md").write_text(
            "status: pending\n", encoding="utf-8"
        )
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "task_git_control_changed"
    assert not marker.exists()


def test_safe_pipeline_rejects_verification_workspace_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(safe, "_confined_verification_command", lambda _root, argv: argv)
    source = _source(tmp_path)
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('docs/status.md').write_text('status: verifier-mutated\\n')",
    ]
    contract = _freeze_contract(source, tmp_path, verification_commands=[command])

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "verification_side_effect_detected"


def test_safe_pipeline_blocks_task_owned_verification_runtime_file(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text(
            "status: pending\n", encoding="utf-8"
        )
        runtime_path = root / ".omc" / "runs" / "verification-runtime"
        runtime_path.rmdir()
        runtime_path.write_text("provider-owned\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "verification_runtime_untrusted"


def test_safe_pipeline_blocks_unusable_verification_runtime_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(safe, "_confined_verification_command", lambda _root, argv: argv)
    source = _source(tmp_path)
    contract = _freeze_contract(
        source,
        tmp_path,
        verification_commands=[[sys.executable, "-c", "print('ok')"]],
    )

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text(
            "status: pending\n", encoding="utf-8"
        )
        (root / ".omc" / "runs" / "verification-runtime").chmod(0)
        return 0, "VERDICT: PROCEED"

    runtime_path = (
        tmp_path
        / "runs"
        / "safe-run-001"
        / "workspace"
        / ".omc"
        / "runs"
        / "verification-runtime"
    )
    try:
        result = safe.run_safe_pipeline(
            source=source,
            contract_path=contract,
            instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
            task_runner=task,
            review_runner=_approve_review,
            workspace_parent=tmp_path / "runs",
        )
    finally:
        if runtime_path.exists():
            runtime_path.chmod(0o700)

    assert result["status"] == "blocked"
    assert result["reason_code"] == "verification_runtime_untrusted"


def test_safe_pipeline_rejects_verification_index_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(safe, "_confined_verification_command", lambda _root, argv: argv)
    source = _source(tmp_path)
    contract = _freeze_contract(
        source,
        tmp_path,
        verification_commands=[["git", "add", "docs/status.md"]],
    )

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "verification_git_state_changed"


def test_safe_pipeline_converts_candidate_commit_failure_to_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    original_run = subprocess.run

    def fail_candidate_commit(args, *positional, **kwargs):
        if args[0] == "git" and "commit" in args and "OMC Autopilot candidate" in args:
            raise subprocess.CalledProcessError(1, args, stderr="signing failed")
        return original_run(args, *positional, **kwargs)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(safe.subprocess, "run", fail_candidate_commit)
    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "candidate_git_command_failed"


def test_safe_pipeline_converts_patch_generation_failure_to_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    original_run = subprocess.run

    def fail_patch_generation(args, *positional, **kwargs):
        if list(args[:3]) == ["git", "diff", "--binary"]:
            raise subprocess.CalledProcessError(1, args, stderr="diff failed")
        return original_run(args, *positional, **kwargs)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    monkeypatch.setattr(safe.subprocess, "run", fail_patch_generation)
    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "candidate_git_command_failed"


def test_safe_pipeline_rejects_non_git_file_mode(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        path = root / "docs" / "status.md"
        path.write_text("status: pending\n", encoding="utf-8")
        path.chmod(0o600)
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "scope_violation"
    assert result["violations"] == [
        "unsupported_file_mode_change:docs/status.md:0644->0600"
    ]


def test_safe_pipeline_accepts_restrictive_umask_without_mode_change(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        path = root / "docs" / "status.md"
        assert path.stat().st_mode & 0o777 == 0o600
        path.write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    previous_umask = os.umask(0o077)
    try:
        result = safe.run_safe_pipeline(
            source=source,
            contract_path=contract,
            instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
            task_runner=task,
            review_runner=_approve_review,
            workspace_parent=tmp_path / "runs",
        )
    finally:
        os.umask(previous_umask)

    assert result["status"] == "candidate_ready"


def test_safe_pipeline_rejects_existing_run_root(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    run_root = tmp_path / "runs" / "safe-run-001"
    run_root.mkdir(parents=True)

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=lambda _root, _prompt: (0, "VERDICT: PROCEED"),
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "run_workspace_exists"


def test_safe_pipeline_promotes_only_approved_candidate_branch(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    original_head = _git(source, "rev-parse", "HEAD")

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "candidate_ready"
    assert result["candidate_branch"] == "codex/safe-candidate"
    assert result["review_packet_sha256"]
    assert _git(source, "rev-parse", "HEAD") == original_head
    assert _git(source, "status", "--porcelain") == ""
    candidate_text = _git(source, "show", "codex/safe-candidate:docs/status.md")
    assert candidate_text == "status: pending"


def test_safe_pipeline_rejects_preexisting_review_packet(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    runs = tmp_path / "runs"

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        packet = runs / "safe-run-001" / "review_packet.json"
        packet.write_text("preexisting\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=runs,
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "review_packet_already_exists"
    assert (runs / "safe-run-001" / "review_packet.json").read_text(encoding="utf-8") == "preexisting\n"


def test_safe_pipeline_does_not_commit_runtime_telemetry(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        cost_log = root / ".omc" / "cost_log.jsonl"
        cost_log.parent.mkdir(exist_ok=True)
        cost_log.write_text('{"tokens": 1}\n', encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=_approve_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "candidate_ready"
    candidate_files = _git(source, "ls-tree", "-r", "--name-only", "codex/safe-candidate")
    assert ".omc/cost_log.jsonl" not in candidate_files.splitlines()


def test_safe_pipeline_runs_review_without_task_runtime_state(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    observed: dict[str, object] = {}

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text(
            "status: pending\n", encoding="utf-8"
        )
        runtime_state = root / ".omc" / "state" / "provider.json"
        runtime_state.parent.mkdir(parents=True, exist_ok=True)
        runtime_state.write_text('{"instruction":"provider-owned"}\n', encoding="utf-8")
        (root / ".omc" / "summary.md").write_text(
            "provider-owned summary\n", encoding="utf-8"
        )
        return 0, "VERDICT: PROCEED"

    def review(root: Path, prompt: str) -> tuple[int, str]:
        observed["root"] = root
        observed["runtime_state_exists"] = (
            root / ".omc" / "state" / "provider.json"
        ).exists()
        observed["runtime_summary_exists"] = (root / ".omc" / "summary.md").exists()
        return _approve_review(root, prompt)

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "candidate_ready"
    assert observed["root"] != Path(result["isolated_workspace"])
    assert observed["runtime_state_exists"] is False
    assert observed["runtime_summary_exists"] is False


def test_safe_pipeline_isolates_review_from_critique_runtime_state(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path, pipeline_mode="full")
    observed: dict[str, object] = {}

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text(
            "status: pending\n", encoding="utf-8"
        )
        return 0, "VERDICT: PROCEED"

    def critique(root: Path, prompt: str) -> tuple[int, str]:
        runtime_state = root / ".omc" / "state" / "critique.json"
        runtime_state.parent.mkdir(parents=True, exist_ok=True)
        runtime_state.write_text('{"instruction":"critique-owned"}\n', encoding="utf-8")
        observed["critique_root"] = root
        return _approve_review(root, prompt)

    def review(root: Path, prompt: str) -> tuple[int, str]:
        observed["review_root"] = root
        observed["critique_state_exists"] = (
            root / ".omc" / "state" / "critique.json"
        ).exists()
        return _approve_review(root, prompt)

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        critique_runner=critique,
        review_runner=review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "candidate_ready"
    assert observed["critique_root"] != observed["review_root"]
    assert observed["critique_state_exists"] is False


def test_safe_pipeline_review_uses_trusted_base_control_files(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "AGENTS.md").write_text("trusted base instructions\n", encoding="utf-8")
    _git(source, "add", "AGENTS.md")
    _git(source, "commit", "-qm", "add trusted instructions")
    instruction = "AGENTS.md의 에이전트 지침을 새 계약으로 갱신한다"
    contract = _freeze_contract(
        source,
        tmp_path,
        allowed_paths=["AGENTS.md"],
        instruction=instruction,
    )
    observed: dict[str, str] = {}

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "AGENTS.md").write_text(
            "candidate-controlled instructions\n", encoding="utf-8"
        )
        return 0, "VERDICT: PROCEED"

    def review(root: Path, prompt: str) -> tuple[int, str]:
        observed["agents"] = (root / "AGENTS.md").read_text(encoding="utf-8")
        return _approve_review(root, prompt)

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction=instruction,
        task_runner=task,
        review_runner=review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "candidate_ready"
    assert observed["agents"] == "trusted base instructions\n"
    assert (
        _git(source, "show", "codex/safe-candidate:AGENTS.md")
        == "candidate-controlled instructions"
    )


def test_safe_pipeline_rejects_review_side_effect(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    def mutating_review(root: Path, prompt: str) -> tuple[int, str]:
        (root / "review-side-effect.txt").write_text("unexpected\n", encoding="utf-8")
        return _approve_review(root, prompt)

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=mutating_review,
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "review_side_effect_detected"
    assert _git(source, "status", "--porcelain") == ""


def test_safe_pipeline_rejects_unbound_review_verdict(tmp_path: Path) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)

    def task(root: Path, _prompt: str) -> tuple[int, str]:
        (root / "docs" / "status.md").write_text("status: pending\n", encoding="utf-8")
        return 0, "VERDICT: PROCEED"

    result = safe.run_safe_pipeline(
        source=source,
        contract_path=contract,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        task_runner=task,
        review_runner=lambda _root, _prompt: (0, "VERDICT: APPROVE"),
        workspace_parent=tmp_path / "runs",
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "review_evidence_unbound"


@pytest.mark.parametrize("stage", ["review", "critique"])
@pytest.mark.parametrize(
    "output_template",
    [
        "REVIEW_PACKET_SHA256: {digest}\nVERDICT: APPROVE\n[ERROR] output truncated",
        "VERDICT: APPROVE\nREVIEW_PACKET_SHA256: {digest}",
        "VERDICT: BLOCK\nREVIEW_PACKET_SHA256: {digest}\nVERDICT: APPROVE",
    ],
)
def test_readonly_stage_rejects_nonterminal_or_ambiguous_evidence(
    tmp_path: Path, stage: str, output_template: str
) -> None:
    digest = "a" * 64

    result, error = safe._run_readonly_stage(
        root=tmp_path,
        prompt="ignored",
        runner=lambda _root, _prompt: (
            0,
            output_template.format(digest=digest),
        ),
        packet_digest=digest,
        stage=stage,
    )

    assert result is None
    assert error == f"{stage}_evidence_unbound"


def test_safe_cli_uses_workspace_write_for_task_and_read_only_for_review(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    calls: list[tuple[str, str, bool]] = []

    def pipeline_step(
        root: Path,
        step_name: str,
        prompt: str,
        executor: str,
        timeout_sec: int,
        **kwargs,
    ) -> tuple[int, str]:
        del root, prompt, executor, timeout_sec
        calls.append(
            (step_name, kwargs["sandbox_mode"], kwargs["allow_fallback"])
        )
        if step_name == "safe_review":
            return 0, "REVIEW_PACKET_SHA256: " + ("a" * 64) + "\nVERDICT: APPROVE"
        return 0, "VERDICT: PROCEED"

    def safe_pipeline(**kwargs):
        kwargs["task_runner"](source, "task")
        kwargs["review_runner"](source, "review")
        return {
            "status": "candidate_ready",
            "reason_code": None,
            "candidate_branch": "codex/safe-candidate",
            "candidate_commit": "a" * 40,
            "review_packet_sha256": "b" * 64,
        }

    monkeypatch.setattr(autopilot, "_run_pipeline_step", pipeline_step)
    monkeypatch.setattr(safe, "run_safe_pipeline", safe_pipeline)
    monkeypatch.setattr(autopilot, "_save_pipeline_result", lambda root, data: None)

    rc = autopilot.cmd_safe_pipeline(
        source,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        contract_path=contract,
        executor_pref="codex",
        max_time=120,
    )

    assert rc == 0
    assert calls == [
        ("safe_task", "workspace-write", False),
        ("safe_review", "read-only", False),
    ]


def test_safe_cli_preserves_completed_stages_when_review_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    persisted: dict[str, object] = {}

    monkeypatch.setattr(
        safe,
        "run_safe_pipeline",
        lambda **_kwargs: {
            "status": "blocked",
            "reason_code": "review_rejected",
            "completed_stages": ["task", "verification"],
        },
    )
    monkeypatch.setattr(
        autopilot,
        "_save_pipeline_result",
        lambda _root, data: persisted.update(data),
    )

    rc = autopilot.cmd_safe_pipeline(
        source,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        contract_path=contract,
        executor_pref="codex",
        max_time=120,
    )

    assert rc == 1
    assert persisted["steps"] == {
        "task": {"status": "completed"},
        "verification": {"status": "completed"},
        "review": {"status": "blocked"},
        "promotion": {"status": "not_completed"},
    }


def test_safe_cli_marks_promotion_failure_as_blocked_stage(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source(tmp_path)
    contract = _freeze_contract(source, tmp_path)
    persisted: dict[str, object] = {}

    monkeypatch.setattr(
        safe,
        "run_safe_pipeline",
        lambda **_kwargs: {
            "status": "blocked",
            "reason_code": "candidate_branch_conflict",
            "completed_stages": ["task", "verification", "review"],
        },
    )
    monkeypatch.setattr(
        autopilot,
        "_save_pipeline_result",
        lambda _root, data: persisted.update(data),
    )

    rc = autopilot.cmd_safe_pipeline(
        source,
        instruction="docs/status.md의 pendng를 pending으로 수정하고 다른 파일은 변경하지 않는다",
        contract_path=contract,
        executor_pref="codex",
        max_time=120,
    )

    assert rc == 1
    assert persisted["steps"] == {
        "task": {"status": "completed"},
        "verification": {"status": "completed"},
        "review": {"status": "completed"},
        "promotion": {"status": "blocked"},
    }


def test_single_approval_has_separate_bounded_entrypoint():
    assert callable(getattr(safe, '_run_single_approval', None))


@pytest.mark.parametrize('revisions', [True, -1, 3])
def test_single_approval_rejects_unapproved_revision_budget(revisions):
    policy = {
        'allowed_paths': ['app.py', 'test_app.py'], 'test_paths': ['test_app.py'],
        'verification_commands': [['python3', '-m', 'pytest', 'test_app.py', '-q']],
        'red_case_id': 'test_app.py::test_value', 'max_time_sec': 120,
        'max_revisions': revisions, 'approval_expires_epoch': 4000000000,
        'verifier_path': '/private/tmp/approved-verifier.py', 'verifier_sha256': 'c' * 64,
    }
    with pytest.raises(mission.MissionError):
        mission.validate_single_execution(policy)


def _single_fixture(tmp_path, monkeypatch, instruction='fix value'):
    source = tmp_path / 'single-source'
    source.mkdir()
    _git(source, 'init', '-q')
    _git(source, 'config', 'user.name', 'Fixture')
    _git(source, 'config', 'user.email', 'fixture@localhost')
    (source / 'app.py').write_text('value = 1\n')
    (source / 'test_app.py').write_text('def test_baseline():\n    assert True\n')
    _git(source, 'add', '.')
    _git(source, 'commit', '-qm', 'baseline')
    verifier = tmp_path / 'oracle.py'
    verifier.write_text("import pathlib,sys\nns={}\nexec((pathlib.Path(sys.argv[1])/'app.py').read_text(),ns)\nassert ns['value']==2\n")
    commands = [[sys.executable, '-B', '-m', 'pytest', '-p', 'no:cacheprovider', 'test_app.py', '-q']]
    policy = {'allowed_paths': ['app.py', 'test_app.py'], 'test_paths': ['test_app.py'],
              'verification_commands': commands, 'red_case_id': 'test_app.py::test_value',
              'max_time_sec': 60, 'max_revisions': 2, 'approval_expires_epoch': time.time()+600,
              'verifier_path': str(verifier), 'verifier_sha256': hashlib.sha256(verifier.read_bytes()).hexdigest()}
    packet = mission.freeze_mission_packet(tmp_path/'mission-single.json', {
        'schema_version': 'omc-autopilot-mission/v2', 'request_sha256': hashlib.sha256(instruction.encode()).hexdigest(),
        'base_commit': _git(source, 'rev-parse', 'HEAD'), 'outcome': 'value is 2',
        'deliverables': ['fixed value'], 'definition_of_done': ['value is 2'], 'non_goals': ['publish'],
        'validation': {'max_total_rounds': 3, 'max_revisions_per_issue': 2}, 'execution': policy})
    receipt = mission.build_mission_approval_receipt(decision_id='explicit-user-approval',session_id='authorizing-session',packet=packet)
    approval_path=tmp_path/'approval-single.json';approval_path.write_text(json.dumps(receipt))
    contract_path=_freeze_contract(source,tmp_path,allowed_paths=policy['allowed_paths'],verification_commands=commands,instruction=instruction)
    doc=json.loads(contract_path.read_text());doc.pop('contract_sha256')
    doc.update(schema_version=workspace.WORK_CONTRACT_SCHEMA_VERSION_V2,change_class='implementation',test_policy='required',promotion_policy='isolated_candidate_only',
        mission_packet_path='mission-single.json',mission_packet_sha256=packet['packet_sha256'],
        mission_approval_path=approval_path.name,mission_approval_sha256=hashlib.sha256(approval_path.read_bytes()).hexdigest(),
        mission_approval_session_id='authorizing-session')
    final=tmp_path/'single-contract.json';workspace.freeze_work_contract(final,doc)
    # Unit tests exercise real child checks; OS confinement has separate tests.
    monkeypatch.setattr(safe,'_confined_verification_command',lambda root,argv:argv)
    return source,final


@pytest.mark.parametrize('mode', ['normal','test_repair','review_repair','exhausted','review_block','oracle_repair'])
def test_single_approval_runs_real_checks_and_bounded_repairs(tmp_path,monkeypatch,mode):
    source,contract=_single_fixture(tmp_path,monkeypatch)
    task_calls=[];review_calls=[]
    def task(root,prompt):
        task_calls.append(root)
        with (root/'.omc/cost_log.jsonl').open('a') as telemetry:
            telemetry.write('{"executor":"codex"}\n')
        if '[OMC RED ONLY]' in prompt:
            (root/'test_app.py').write_text("from pathlib import Path\ndef test_baseline():\n    assert True\ndef test_value():\n    ns={}\n    exec(Path('app.py').read_text(),ns)\n    assert ns['value']==2\n")
        else:
            number=len(task_calls)-1
            value=1 if mode=='exhausted' or (mode in ['test_repair','oracle_repair'] and number==1) else 2
            (root/'app.py').write_text(f'value = {value}\n')
            if mode=='oracle_repair':
                # A weak test must not defeat the parent-owned independent oracle.
                (root/'test_app.py').write_text('def test_baseline():\n    assert True\ndef test_value():\n    assert True\n')
            if number>1:
                assert 'candidate_snapshot_sha256' in prompt
        return 0,'VERDICT: PROCEED'
    def review(root,prompt):
        review_calls.append(root)
        digest=re.search(r'REVIEW_PACKET_SHA256: ([0-9a-f]{64})',prompt).group(1)
        verdict='BLOCK' if mode=='review_block' else 'REVISE' if mode=='review_repair' and len(review_calls)==1 else 'APPROVE'
        return 0,f'Please fix the value if needed.\nREVIEW_PACKET_SHA256: {digest}\nVERDICT: {verdict}'
    before=workspace.source_identity(source)
    result=safe.run_safe_pipeline(source=source,contract_path=contract,instruction='fix value',task_runner=task,review_runner=review,workspace_parent=tmp_path/'runs')
    if mode in ['exhausted','review_block']:
        assert result['status']=='blocked',result
        assert result['reason_code']==('revision_budget_exhausted' if mode=='exhausted' else 'review_rejected'),result
    else:
        assert result['status']=='candidate_ready',result
        assert result['source_modified'] is False
        assert _git(Path(result['isolated_workspace']),'rev-parse','HEAD')==result['candidate_commit']
    assert len(set(task_calls))==1
    assert len(task_calls)<=4  # one RED + initial implementation + at most two repairs
    assert workspace.source_identity(source)==before
    assert not _git(source,'branch','--list','codex/safe-candidate')


@pytest.mark.parametrize('failure', ['expired','scope','oracle','registration','gate','commit','verifier_write','governance'])
def test_single_approval_failures_never_return_success(tmp_path,monkeypatch,failure):
    source,path=_single_fixture(tmp_path,monkeypatch)
    calls=[]
    if failure in ['expired','scope']:
        original=mission.load_mission_packet
        def changed(p):
            packet=original(p)
            if failure=='expired':packet['execution']['approval_expires_epoch']=time.time()-1
            else:packet['execution']['allowed_paths']=['other.py']
            return packet
        # Internal branch receives a validated packet from the existing loader;
        # exercise runtime checks directly, not a forged approval in production.
        packet=original(tmp_path/'mission-single.json')
        if failure=='expired':packet['execution']['approval_expires_epoch']=time.time()-1
        else:packet['execution']['allowed_paths']=['other.py']
        args=dict(source=source,contract=workspace.load_work_contract(path),packet=packet,
            approval=json.loads((tmp_path/'approval-single.json').read_text()),instruction='fix value',
            task_runner=lambda *a:calls.append(a),review_runner=lambda *a:calls.append(a),workspace_parent=tmp_path/'runs',deadline_monotonic=None)
        result=safe._run_single_approval(**args)
        assert result['status']=='blocked' and not calls
        return
    if failure=='oracle':(tmp_path/'oracle.py').write_text('raise SystemExit(0)')
    if failure=='registration':monkeypatch.setattr(safe.workspace,'materialize_isolated_clone',lambda *a:(_ for _ in ()).throw(workspace.AutopilotWorkspaceError('fixture registration failure')))
    if failure=='gate':
        import omc_pipeline_guard as gate
        monkeypatch.setattr(gate,'_evaluate_implementation_completion',lambda *a:{'outcome':'blocked'})
    if failure=='commit':monkeypatch.setattr(safe,'_commit_candidate',lambda *a:(_ for _ in ()).throw(workspace.AutopilotWorkspaceError('candidate_git_command_failed')))
    def task(root,prompt):
        calls.append(root)
        if '[OMC RED ONLY]' in prompt:
            (root/'test_app.py').write_text("from pathlib import Path\ndef test_baseline():\n    assert True\ndef test_value():\n    ns={}\n    exec(Path('app.py').read_text(),ns)\n    assert ns['value']==2\n")
        else:
            (root/'app.py').write_text('value = 2\n')
            if failure=='governance':(root/'.omc/pipeline_session.json').write_text('{}')
        return 0,'VERDICT: PROCEED'
    def review(root,prompt):
        digest=re.search(r'REVIEW_PACKET_SHA256: ([0-9a-f]{64})',prompt).group(1)
        if failure=='verifier_write':
            (root.parent/'verifier.py').chmod(0o600)
            (root.parent/'verifier.py').write_text('pass')
        return 0,f'REVIEW_PACKET_SHA256: {digest}\nVERDICT: APPROVE'
    before=workspace.source_identity(source)
    result=safe.run_safe_pipeline(source=source,contract_path=path,instruction='fix value',task_runner=task,review_runner=review,workspace_parent=tmp_path/'runs')
    assert result['status']=='blocked',result
    assert workspace.source_identity(source)==before


def test_single_approval_same_run_is_not_replayed(tmp_path,monkeypatch):
    source,path=_single_fixture(tmp_path,monkeypatch)
    runs=tmp_path/'runs';runs.mkdir();(runs/'safe-run-001').mkdir()
    calls=[]
    result=safe.run_safe_pipeline(source=source,contract_path=path,instruction='fix value',
        task_runner=lambda *a:calls.append(a),review_runner=lambda *a:calls.append(a),workspace_parent=runs)
    assert result['reason_code']=='single_approval_already_started' and not calls


def test_single_approval_cli_freezes_isolated_candidate_policy(tmp_path,monkeypatch):
    source,path=_single_fixture(tmp_path,monkeypatch)
    packet=mission.load_mission_packet(tmp_path/'mission-single.json')
    code=autopilot.cmd_freeze_work_contract(source,instruction='fix value',run_id='cli-run',
        allowed_paths=packet['execution']['allowed_paths'],allowed_operations=['modify'],
        change_class='implementation',test_policy='required',verification_commands=packet['execution']['verification_commands'],
        mode='lite',branch='codex/unused-isolated',output=tmp_path/'cli-contract.json',
        mission_packet_path=tmp_path/'mission-single.json',mission_approval_path=tmp_path/'approval-single.json',
        mission_approval_session_id='authorizing-session')
    assert code==0
    assert workspace.load_work_contract(tmp_path/'cli-contract.json')['promotion_policy']=='isolated_candidate_only'


def test_single_approval_rejects_deleting_baseline_test(tmp_path,monkeypatch):
    source,path=_single_fixture(tmp_path,monkeypatch)
    def task(root,prompt):
        (root/'test_app.py').write_text('def test_value():\n    assert False\n')
        return 0,'VERDICT: PROCEED'
    result=safe.run_safe_pipeline(source=source,contract_path=path,instruction='fix value',
        task_runner=task,review_runner=lambda *a:pytest.fail('review must not run'),workspace_parent=tmp_path/'runs')
    assert result['reason_code']=='baseline_tests_changed'


def test_single_approval_rejects_changed_contract_before_provider(tmp_path,monkeypatch):
    source,path=_single_fixture(tmp_path,monkeypatch)
    calls=[]
    result=safe.run_safe_pipeline(source=source,contract_path=path,instruction='fix value',
        task_runner=lambda *a:calls.append(a),review_runner=lambda *a:calls.append(a),
        workspace_parent=tmp_path/'runs',expected_contract_sha256='0'*64)
    assert result['reason_code']=='work_contract_changed_before_execution'
    assert not calls


def _review_fix_runner(mode, calls):
    def task(root, prompt):
        red = '[OMC RED ONLY]' in prompt
        calls.append('red' if red else 'implementation')
        test = root / 'test_app.py'
        if red:
            if mode == 'red_mutation':
                body = "\nfrom pathlib import Path\ndef test_value():\n    Path('app.py').write_text('value = 2\\n')\n    assert False\n"
            else:
                body = "\nfrom pathlib import Path\ndef test_value():\n    ns={}\n    exec(Path('app.py').read_text(),ns)\n    assert ns['value']==2\n"
            test.write_text(test.read_text() + body)
        else:
            (root / 'app.py').write_text('value = 2\n')
            if mode == 'skip':
                test.write_text("import pytest\npytestmark=pytest.mark.skip(reason='disabled')\n" + test.read_text())
        return 0, 'VERDICT: PROCEED'
    return task


def _review_fix_approve(root, prompt):
    digest = re.search(r'REVIEW_PACKET_SHA256: ([0-9a-f]{64})', prompt).group(1)
    return 0, f'REVIEW_PACKET_SHA256: {digest}\nVERDICT: APPROVE'


def test_review_fix_all_skipped_is_not_success(tmp_path, monkeypatch):
    source, path = _single_fixture(tmp_path, monkeypatch)
    calls = []
    result = safe.run_safe_pipeline(source=source, contract_path=path, instruction='fix value',
        task_runner=_review_fix_runner('skip', calls), review_runner=_review_fix_approve,
        workspace_parent=tmp_path/'runs')
    assert result['status'] == 'blocked', result
    assert result['reason_code'] == 'revision_budget_exhausted', result
    assert all(a['error'] == 'required_tests_not_passed' for a in result['attempts'])


def test_review_fix_red_command_cannot_modify_implementation(tmp_path, monkeypatch):
    source, path = _single_fixture(tmp_path, monkeypatch)
    calls = []
    result = safe.run_safe_pipeline(source=source, contract_path=path, instruction='fix value',
        task_runner=_review_fix_runner('red_mutation', calls), review_runner=_review_fix_approve,
        workspace_parent=tmp_path/'runs')
    assert result['reason_code'] == 'red_verification_side_effect_detected', result
    assert calls == ['red']


def test_review_fix_same_approval_cannot_restart_with_new_run_id(tmp_path, monkeypatch):
    source, path = _single_fixture(tmp_path, monkeypatch)
    calls = []
    args = dict(source=source, instruction='fix value', task_runner=_review_fix_runner('normal', calls),
                review_runner=_review_fix_approve, workspace_parent=tmp_path/'runs')
    first = safe.run_safe_pipeline(contract_path=path, **args)
    assert first['status'] == 'candidate_ready', first
    doc = json.loads(path.read_text())
    doc.pop('contract_sha256')
    doc['run_id'] = 'new-run-same-approval'
    second_path = tmp_path / 'second-contract.json'
    workspace.freeze_work_contract(second_path, doc)
    args['workspace_parent'] = tmp_path/'different-runs'
    second = safe.run_safe_pipeline(contract_path=second_path, **args)
    assert second['reason_code'] == 'single_approval_already_consumed', second
    assert calls == ['red', 'implementation']


def test_review_fix_qualified_test_names_remain_distinct(tmp_path, monkeypatch):
    original_git = _git
    def class_baseline(root, *args):
        if args == ('commit', '-qm', 'baseline'):
            (root/'test_app.py').write_text('class TestOne:\n    def test_valid(self):\n        assert True\nclass TestTwo:\n    def test_valid(self):\n        assert True\n')
            original_git(root, 'add', 'test_app.py')
        return original_git(root, *args)
    monkeypatch.setattr(sys.modules[__name__], '_git', class_baseline)
    source, path = _single_fixture(tmp_path, monkeypatch)
    calls = []
    result = safe.run_safe_pipeline(source=source, contract_path=path, instruction='fix value',
        task_runner=_review_fix_runner('normal', calls), review_runner=_review_fix_approve,
        workspace_parent=tmp_path/'runs')
    assert result['status'] == 'candidate_ready', result
    assert calls == ['red', 'implementation']


def _freeze_single_through_cli(tmp_path, monkeypatch, instruction):
    source, _ = _single_fixture(tmp_path, monkeypatch, instruction=instruction)
    packet = mission.load_mission_packet(tmp_path / 'mission-single.json')
    contract = tmp_path / 'public-cli-contract.json'
    argv = ['omc_autopilot.py', '--target', str(source), 'freeze-work-contract',
            '--instruction', instruction, '--run-id', 'public-cli-raw-request',
            '--allowed-path', 'app.py', '--allowed-path', 'test_app.py',
            '--allowed-operation', 'modify', '--change-class', 'implementation',
            '--test-policy', 'required', '--mode', 'lite',
            '--branch', 'codex/public-cli-request', '--output', str(contract),
            '--mission-packet', str(tmp_path / 'mission-single.json'),
            '--mission-approval', str(tmp_path / 'approval-single.json'),
            '--mission-approval-session-id', 'authorizing-session',
            '--verify-command-json', json.dumps(packet['execution']['verification_commands'][0])]
    monkeypatch.setattr(sys, 'argv', argv)
    assert autopilot.main() == 0
    assert workspace.load_work_contract(contract)['instruction_sha256'] == hashlib.sha256(instruction.encode()).hexdigest()
    return source, contract


def _public_pipeline_argv(source, contract, instruction):
    return ['omc_autopilot.py', '--target', str(source), 'pipeline',
            '--instruction', instruction, '--work-contract', str(contract),
            '--branch', 'codex/public-cli-request', '--executor', 'codex',
            '--max-time', '60']


@pytest.mark.parametrize('instruction', [
    '한글 value 수정 요청입니다\n',
    '한글 value 수정 요청입니다\r\n',
    '  한글 value 수정 요청입니다  ',
    '\n한글 value 수정 요청입니다\n\n',
], ids=['lf', 'crlf', 'spaces', 'multiline'])
def test_public_cli_contract_preserves_approved_request_bytes(tmp_path, monkeypatch, instruction):
    source, contract = _freeze_single_through_cli(tmp_path, monkeypatch, instruction)
    before = workspace.source_identity(source)
    index_before = (source / '.git/index').read_bytes()
    files_before = {name: (source / name).read_bytes() for name in ('app.py', 'test_app.py')}
    pins = {p: p.read_bytes() for p in (contract, tmp_path / 'mission-single.json', tmp_path / 'approval-single.json')}
    calls = []
    def provider(root, step, prompt, *args, **kwargs):
        calls.append(step)
        if step == 'safe_review':
            digest = re.search(r'REVIEW_PACKET_SHA256: ([0-9a-f]{64})', prompt).group(1)
            return 0, f'REVIEW_PACKET_SHA256: {digest}\nVERDICT: APPROVE'
        if '[OMC RED ONLY]' in prompt:
            (root / 'test_app.py').write_text("from pathlib import Path\ndef test_baseline():\n    assert True\ndef test_value():\n    ns={}\n    exec(Path('app.py').read_text(),ns)\n    assert ns['value']==2\n")
        else:
            (root / 'app.py').write_text('value = 2\n')
        return 0, 'VERDICT: PROCEED'
    monkeypatch.setattr(autopilot, '_run_pipeline_step', provider)
    monkeypatch.setenv('OMC_AUTOPILOT_SAFE_RUNS', str(tmp_path / 'public-runs'))
    monkeypatch.setattr(sys, 'argv', _public_pipeline_argv(source, contract, instruction))
    code = autopilot.main()
    result = json.loads((source / '.omc/pipeline_run_result.json').read_text())
    assert code == 0 and result['status'] == 'candidate_ready', result
    assert result['instruction'] == instruction
    assert calls == ['safe_task', 'safe_task', 'safe_review']
    # The public CLI appends .omc reports; business files, HEAD and index stay fixed.
    assert _git(source, 'rev-parse', 'HEAD') == before['head']
    assert (source / '.git/index').read_bytes() == index_before
    assert all((source / name).read_bytes() == raw for name, raw in files_before.items())
    assert not _git(source, 'status', '--porcelain', '--untracked-files=no')
    assert all(name.startswith('.omc/') for name in _git(source, 'ls-files', '--others', '--exclude-standard').splitlines())
    assert all(p.read_bytes() == raw for p, raw in pins.items())


@pytest.mark.parametrize('change', ['body', 'trailing-newline', 'leading-space', 'receipt', 'mission'])
def test_public_cli_contract_rejects_changed_request_before_provider(tmp_path, monkeypatch, change):
    instruction = '한글 value 수정 요청입니다'
    source, contract = _freeze_single_through_cli(tmp_path, monkeypatch, instruction)
    submitted = instruction
    if change == 'body':
        submitted += ' 다른 작업'
    elif change == 'trailing-newline':
        submitted += '\n'
    elif change == 'leading-space':
        submitted = ' ' + submitted
    else:
        path = tmp_path / ('approval-single.json' if change == 'receipt' else 'mission-single.json')
        payload = json.loads(path.read_text())
        if change == 'receipt':
            payload['binding']['session_id'] = 'different-session'
        else:
            payload['request_sha256'] = 'f' * 64
        path.write_text(json.dumps(payload))
    calls = []
    monkeypatch.setattr(autopilot, '_run_pipeline_step', lambda *a, **k: calls.append(a))
    monkeypatch.setenv('OMC_AUTOPILOT_SAFE_RUNS', str(tmp_path / 'public-runs'))
    monkeypatch.setattr(sys, 'argv', _public_pipeline_argv(source, contract, submitted))
    assert autopilot.main() == 1
    assert not calls
    if change not in {'receipt', 'mission'}:
        result = json.loads((source / '.omc/pipeline_run_result.json').read_text())
        assert result['reason_code'] == 'instruction_identity_mismatch', result


@pytest.mark.parametrize('instruction', ['', ' \t\r\n', ' 짧음 '])
@pytest.mark.parametrize('contract_mode', [False, True])
def test_public_cli_empty_or_short_request_never_calls_pipeline(tmp_path, monkeypatch, instruction, contract_mode):
    calls = []
    monkeypatch.setattr(autopilot, 'cmd_safe_pipeline', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(autopilot, 'cmd_pipeline', lambda *a, **k: calls.append(a))
    argv = ['omc_autopilot.py', '--target', str(tmp_path), 'pipeline', '--instruction', instruction]
    if contract_mode:
        argv += ['--work-contract', str(tmp_path / 'not-read.json')]
    monkeypatch.setattr(sys, 'argv', argv)
    assert autopilot.main() == 1
    assert not calls


def test_public_cli_without_contract_retains_legacy_strip(tmp_path, monkeypatch):
    observed = []
    def normal(root, **kwargs):
        observed.append(kwargs['instruction'])
        return 0
    monkeypatch.setattr(autopilot, 'cmd_pipeline', normal)
    monkeypatch.setattr(sys, 'argv', ['omc_autopilot.py', '--target', str(tmp_path),
        'pipeline', '--instruction', ' \n한글 value 수정 요청입니다\r\n '])
    assert autopilot.main() == 0
    assert observed == ['한글 value 수정 요청입니다']
