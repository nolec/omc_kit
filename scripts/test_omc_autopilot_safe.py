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
