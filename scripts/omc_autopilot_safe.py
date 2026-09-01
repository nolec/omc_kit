#!/usr/bin/env python3
"""Isolated, scope-bound execution path for Autopilot candidates."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import omc_autopilot_workspace as workspace
import omc_mission as mission


Runner = Callable[[Path, str], tuple[int, str]]
VERIFICATION_MAX_OUTPUT_BYTES = 1024 * 1024


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _terminal_verdict(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    match = re.fullmatch(r"VERDICT:\s*([A-Z ]+)", lines[-1])
    return match.group(1).strip() if match else ""


def _result(status: str, reason_code: str | None, **values: Any) -> dict[str, Any]:
    return {"status": status, "reason_code": reason_code, **values}


def _task_prompt(
    contract: dict[str, Any], instruction: str, briefing: dict[str, Any] | None = None
) -> str:
    test_rule = (
        "검증 명령에 필요한 테스트를 작성하세요."
        if contract["test_policy"] == "required"
        else "새 테스트 파일은 명시된 허용 경로에 포함된 경우에만 작성하세요."
    )
    return (
        "[OMC SAFE AUTOPILOT]\n"
        + (
            f"MISSION_BRIEFING: {json.dumps(briefing, ensure_ascii=False, sort_keys=True)}\n"
            if briefing is not None
            else ""
        )
        +
        f"지시문: {instruction}\n"
        f"허용 경로: {json.dumps(contract['allowed_paths'], ensure_ascii=False)}\n"
        f"허용 작업: {json.dumps(contract['allowed_operations'])}\n"
        f"변경 등급: {contract['change_class']}\n"
        f"{test_rule}\n"
        "허용 경로 밖 파일을 만들거나 수정하지 마세요.\n"
        "Git commit을 만들지 말고, 검증을 위해 staging했다면 종료 전에 index를 원복하세요.\n"
        "마지막 줄에 VERDICT: PROCEED 또는 VERDICT: BLOCK을 출력하세요."
    )


def _review_prompt(packet: dict[str, Any], briefing: dict[str, Any] | None = None) -> str:
    digest = str(packet["packet_sha256"])
    return (
        "아래 immutable review packet만 검토하세요. workspace를 수정하지 마세요.\n"
        + (
            f"MISSION_BRIEFING: {json.dumps(briefing, ensure_ascii=False, sort_keys=True)}\n"
            if briefing is not None
            else ""
        )
        +
        f"REVIEW_PACKET_SHA256: {digest}\n"
        f"{json.dumps(packet, ensure_ascii=False, sort_keys=True)}\n\n"
        f"마지막 두 줄에 정확히 REVIEW_PACKET_SHA256: {digest} 와 "
        "VERDICT: APPROVE 또는 VERDICT: BLOCK을 출력하세요."
    )


def _confined_verification_command(root: Path, argv: list[str]) -> list[str]:
    root = root.resolve()
    system = platform.system()
    if system == "Darwin":
        sandbox = shutil.which("sandbox-exec")
        if not sandbox:
            raise workspace.AutopilotWorkspaceError(
                "verification_confinement_unavailable"
            )
        root_literal = json.dumps(str(root))
        profile = "\n".join(
            (
                "(version 1)",
                "(allow default)",
                "(deny network*)",
                "(deny file-write*)",
                f"(allow file-write* (subpath {root_literal}) (literal \"/dev/null\"))",
            )
        )
        return [sandbox, "-p", profile, "--", *argv]
    if system == "Linux":
        sandbox = shutil.which("bwrap")
        if not sandbox:
            raise workspace.AutopilotWorkspaceError(
                "verification_confinement_unavailable"
            )
        root_text = str(root)
        return [
            sandbox,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            root_text,
            root_text,
            "--chdir",
            root_text,
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--",
            *argv,
        ]
    raise workspace.AutopilotWorkspaceError("verification_confinement_unavailable")


def _verification_environment(runtime_root: Path) -> dict[str, str]:
    home = runtime_root / "home"
    temporary = runtime_root / "tmp"
    cache = runtime_root / "cache"
    for path in (home, temporary, cache):
        path.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(cache),
        "CI": "true",
    }
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "SYSTEMROOT"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _prepare_verification_runtime(root: Path) -> tuple[Path | None, str | None]:
    runtime_paths = [root / ".omc", root / ".omc" / "runs"]
    runtime_parent = runtime_paths[-1] / "verification-runtime"
    runtime_paths.append(runtime_parent)
    for path in runtime_paths:
        if path.is_symlink():
            return None, "verification_runtime_untrusted"
        try:
            path.mkdir(exist_ok=True)
            metadata = path.lstat()
        except FileExistsError:
            return None, "verification_runtime_untrusted"
        except OSError:
            return None, "verification_runtime_unavailable"
        if not stat.S_ISDIR(metadata.st_mode):
            return None, "verification_runtime_untrusted"
        current_uid = getattr(os, "getuid", lambda: metadata.st_uid)()
        if metadata.st_uid != current_uid:
            return None, "verification_runtime_untrusted"
        if stat.S_IMODE(metadata.st_mode) & 0o700 != 0o700:
            return None, "verification_runtime_untrusted"
    return runtime_parent, None


def _kill_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def _wait_after_termination(process: subprocess.Popen[Any]) -> bool:
    try:
        process.wait(timeout=1)
        return True
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
            return True
        except subprocess.TimeoutExpired:
            return False


def _capture_process_output(
    process: subprocess.Popen[bytes],
    *,
    deadline_monotonic: float,
    max_output_bytes: int,
) -> tuple[bytes, bytes, bool, bool, bool]:
    selector = selectors.DefaultSelector()
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    for name, stream in streams.items():
        if stream is None:
            continue
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, data=name)

    timed_out = False
    output_limit_exceeded = False
    try:
        while selector.get_map() or process.poll() is None:
            remaining_time = deadline_monotonic - time.monotonic()
            if remaining_time <= 0:
                timed_out = True
                _kill_process_group(process)
                break
            events = selector.select(timeout=min(0.05, remaining_time))
            for key, _mask in events:
                remaining_output = max_output_bytes + 1 - sum(
                    len(buffer) for buffer in buffers.values()
                )
                try:
                    chunk = os.read(key.fd, max(1, min(64 * 1024, remaining_output)))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[str(key.data)].extend(chunk)
                if sum(len(buffer) for buffer in buffers.values()) > max_output_bytes:
                    output_limit_exceeded = True
                    _kill_process_group(process)
                    break
            if output_limit_exceeded:
                break

        terminated = True
        if timed_out or output_limit_exceeded:
            terminated = _wait_after_termination(process)
        else:
            process.wait()
        return (
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
            timed_out,
            output_limit_exceeded,
            terminated,
        )
    finally:
        selector.close()
        for stream in streams.values():
            if stream is not None:
                stream.close()


def _run_verification(
    root: Path,
    commands: list[list[str]],
    *,
    deadline_monotonic: float,
    max_output_bytes: int = VERIFICATION_MAX_OUTPUT_BYTES,
) -> tuple[list[dict[str, Any]], str | None]:
    receipts: list[dict[str, Any]] = []
    runtime_parent, runtime_error = _prepare_verification_runtime(root)
    if runtime_error or runtime_parent is None:
        return receipts, runtime_error or "verification_runtime_unavailable"
    for argv in commands:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": None,
                    "output_sha256": _sha256_text(""),
                    "timed_out": True,
                }
            )
            return receipts, "verification_timeout"
        try:
            command = _confined_verification_command(root, argv)
        except workspace.AutopilotWorkspaceError:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": None,
                    "output_sha256": _sha256_text(""),
                    "confinement_unavailable": True,
                }
            )
            return receipts, "verification_confinement_unavailable"
        with tempfile.TemporaryDirectory(
            prefix="command-", dir=runtime_parent
        ) as raw_runtime:
            runtime_root = Path(raw_runtime)
            try:
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=_verification_environment(runtime_root),
                    start_new_session=True,
                )
                (
                    stdout_bytes,
                    stderr_bytes,
                    timed_out,
                    output_limit_exceeded,
                    terminated,
                ) = _capture_process_output(
                    process,
                    deadline_monotonic=time.monotonic() + remaining,
                    max_output_bytes=max_output_bytes,
                )
            except OSError:
                receipts.append(
                    {
                        "argv": argv,
                        "exit_code": None,
                        "output_sha256": _sha256_text(""),
                        "start_failed": True,
                    }
                )
                return receipts, "verification_start_failed"
            if not terminated:
                receipts.append(
                    {
                        "argv": argv,
                        "exit_code": None,
                        "output_sha256": _sha256_text(""),
                        "termination_failed": True,
                    }
                )
                return receipts, "verification_termination_failed"
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            output = stdout + stderr
        if timed_out:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": None,
                    "output_sha256": _sha256_text(stdout + stderr),
                    "timed_out": True,
                }
            )
            return receipts, "verification_timeout"
        if output_limit_exceeded:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": process.returncode,
                    "output_sha256": _sha256_text(output),
                    "output_limit_exceeded": True,
                }
            )
            return receipts, "verification_output_limit_exceeded"
        receipts.append(
            {
                "argv": argv,
                "exit_code": process.returncode,
                "output_sha256": _sha256_text(output),
            }
        )
        if process.returncode != 0:
            return receipts, "verification_failed"
    return receipts, None


def _write_immutable_packet(path: Path, packet: dict[str, Any]) -> None:
    data = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_already_exists") from exc
    except OSError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_unwritable") from exc
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise workspace.AutopilotWorkspaceError("review_packet_write_incomplete")
            offset += written
        os.fsync(fd)
    except OSError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_write_failed") from exc
    finally:
        os.close(fd)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_sync_failed") from exc


def _run_candidate_git(
    root: Path,
    *args: str,
    capture_output: bool = False,
    text: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[Any]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=capture_output,
            text=text,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise workspace.AutopilotWorkspaceError("candidate_git_command_failed") from exc
    if check and result.returncode != 0:
        raise workspace.AutopilotWorkspaceError("candidate_git_command_failed")
    return result


def _commit_candidate(root: Path, delta: list[dict[str, Any]]) -> str:
    _run_candidate_git(root, "config", "user.email", "omc-autopilot@example.invalid")
    _run_candidate_git(root, "config", "user.name", "OMC Autopilot")
    paths = sorted({str(item["path"]) for item in delta})
    if not paths:
        raise workspace.AutopilotWorkspaceError("candidate_has_no_changes")
    _run_candidate_git(root, "add", "-A", "--", *paths)
    staged_output = _run_candidate_git(
        root, "diff", "--cached", "--name-only", "-z", capture_output=True
    ).stdout
    try:
        staged_paths = staged_output.decode("utf-8").rstrip("\0").split("\0")
    except UnicodeDecodeError as exc:
        raise workspace.AutopilotWorkspaceError("candidate_staged_scope_mismatch") from exc
    if sorted(path for path in staged_paths if path) != paths:
        raise workspace.AutopilotWorkspaceError("candidate_staged_scope_mismatch")
    staged = _run_candidate_git(root, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        raise workspace.AutopilotWorkspaceError("candidate_has_no_changes")
    if staged.returncode != 1:
        raise workspace.AutopilotWorkspaceError("candidate_staged_diff_invalid")
    _run_candidate_git(
        root,
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--no-verify",
        "-qm",
        "OMC Autopilot candidate",
    )
    return _run_candidate_git(root, "rev-parse", "HEAD", capture_output=True, text=True).stdout.strip()


def _terminal_packet_verdict(output: str, packet_digest: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    evidence_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith("REVIEW_PACKET_SHA256:") or line.startswith("VERDICT:")
    ]
    if evidence_indexes != [len(lines) - 2, len(lines) - 1]:
        return None
    if lines[-2] != f"REVIEW_PACKET_SHA256: {packet_digest}":
        return None

    verdict_match = re.fullmatch(r"VERDICT:\s*([A-Z][A-Z ]*)", lines[-1])
    return verdict_match.group(1).strip() if verdict_match else None


def _run_readonly_stage(
    *, root: Path, prompt: str, runner: Runner, packet_digest: str, stage: str
) -> tuple[dict[str, Any] | None, str | None]:
    before = workspace.snapshot_workspace(root)
    git_control_before = (
        workspace.snapshot_git_control_plane(root)
        if (root / ".git").is_dir()
        else None
    )
    rc, output = runner(root, prompt)
    after = workspace.snapshot_workspace(root)
    if git_control_before is not None:
        try:
            workspace.validate_git_control_plane(root, git_control_before)
        except workspace.AutopilotWorkspaceError:
            return None, f"{stage}_side_effect_detected"
    if before != after:
        return None, f"{stage}_side_effect_detected"
    if rc != 0:
        return None, f"{stage}_failed"
    verdict = _terminal_packet_verdict(output, packet_digest)
    if verdict is None:
        return None, f"{stage}_evidence_unbound"
    return {"verdict": verdict, "output_sha256": _sha256_text(output)}, None


def run_safe_pipeline(
    *,
    source: Path,
    contract_path: Path,
    instruction: str,
    task_runner: Runner,
    review_runner: Runner,
    workspace_parent: Path,
    critique_runner: Runner | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    contract = workspace.load_work_contract(contract_path)
    completed_stages: list[str] = []

    def finish(status: str, reason_code: str | None, **values: Any) -> dict[str, Any]:
        return _result(
            status,
            reason_code,
            completed_stages=list(completed_stages),
            **values,
        )

    mission_packet: dict[str, Any] | None = None
    mission_receipt: dict[str, Any] | None = None
    if contract["schema_version"] == workspace.WORK_CONTRACT_SCHEMA_VERSION_V2:
        packet_path = contract_path.parent / str(contract["mission_packet_path"])
        approval_path = contract_path.parent / str(contract["mission_approval_path"])
        try:
            mission_packet = mission.load_mission_packet(packet_path)
            if mission_packet["packet_sha256"] != contract["mission_packet_sha256"]:
                return finish("blocked", "mission_packet_contract_mismatch")
            if approval_path.is_symlink():
                return finish("blocked", "mission_approval_symlink")
            approval_bytes = approval_path.read_bytes()
            approval_digest = hashlib.sha256(approval_bytes).hexdigest()
            if approval_digest != contract["mission_approval_sha256"]:
                return finish("blocked", "mission_approval_contract_mismatch")
            mission_receipt = json.loads(approval_bytes.decode("utf-8"))
            mission.validate_mission_approval_receipt(
                mission_receipt,
                packet=mission_packet,
                session_id=str(contract["mission_approval_session_id"]),
            )
        except (OSError, json.JSONDecodeError, mission.MissionError):
            return finish("blocked", "mission_approval_invalid")

    if _sha256_text(instruction) != contract["instruction_sha256"]:
        return finish("blocked", "instruction_identity_mismatch")
    if mission_packet is not None:
        if mission_packet["request_sha256"] != contract["instruction_sha256"]:
            return finish("blocked", "mission_request_identity_mismatch")
        if mission_packet["base_commit"] != contract["base_commit"]:
            return finish("blocked", "mission_base_commit_mismatch")
    if workspace.source_identity(source) != contract["source_identity"]:
        return finish("blocked", "source_identity_drift")
    if contract["pipeline_mode"] == "full" and critique_runner is None:
        return finish("blocked", "full_critique_runner_missing")

    if workspace_parent.is_symlink():
        return finish("blocked", "workspace_parent_symlink")
    try:
        workspace_parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return finish("blocked", "workspace_parent_unwritable")
    if not workspace_parent.is_dir() or workspace_parent.is_symlink():
        return finish("blocked", "workspace_parent_untrusted")
    run_root = workspace_parent / str(contract["run_id"])
    isolated = run_root / "workspace"
    try:
        run_root.mkdir()
    except FileExistsError:
        return finish("blocked", "run_workspace_exists")
    except OSError:
        return finish("blocked", "run_workspace_unwritable")
    try:
        workspace.materialize_isolated_clone(source, isolated, contract)
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc))

    _runtime_parent, runtime_error = _prepare_verification_runtime(isolated)
    if runtime_error:
        return finish(
            "blocked", runtime_error, isolated_workspace=str(isolated)
        )

    before = workspace.snapshot_workspace(isolated)
    git_control_before = workspace.snapshot_git_control_plane(isolated)
    task_briefing = (
        mission.build_stage_briefing(mission_packet, stage="task")
        if mission_packet is not None
        else None
    )
    task_rc, task_output = task_runner(
        isolated, _task_prompt(contract, instruction, task_briefing)
    )
    if task_rc != 0 or _terminal_verdict(task_output) != "PROCEED":
        return finish("blocked", "task_failed", isolated_workspace=str(isolated))

    git_control_after_task = workspace.snapshot_git_control_plane(isolated)
    if git_control_after_task != git_control_before:
        reason_code = (
            "task_git_state_changed"
            if git_control_after_task.get("HEAD") != git_control_before.get("HEAD")
            else "task_git_control_changed"
        )
        return finish(
            "blocked", reason_code, isolated_workspace=str(isolated)
        )

    try:
        workspace.validate_precommit_git_state(isolated, contract)
    except workspace.AutopilotWorkspaceError:
        return finish("blocked", "task_git_state_changed", isolated_workspace=str(isolated))

    after = workspace.snapshot_workspace(isolated)
    delta = workspace.compute_workspace_delta(before, after)
    violations = workspace.validate_scope(delta, contract)
    if violations:
        return finish(
            "blocked",
            "scope_violation",
            violations=violations,
            isolated_workspace=str(isolated),
        )
    completed_stages.append("task")

    deadline = deadline_monotonic if deadline_monotonic is not None else time.monotonic() + 7200
    tests, verification_error = _run_verification(
        isolated,
        contract["verification_commands"],
        deadline_monotonic=deadline,
    )
    if verification_error:
        return finish(
            "blocked",
            verification_error,
            tests=tests,
            isolated_workspace=str(isolated),
        )

    try:
        workspace.validate_git_control_plane(isolated, git_control_before)
    except workspace.AutopilotWorkspaceError:
        return finish(
            "blocked",
            "verification_git_control_changed",
            tests=tests,
            isolated_workspace=str(isolated),
        )

    verified_after = workspace.snapshot_workspace(isolated)
    if verified_after != after:
        return finish(
            "blocked",
            "verification_side_effect_detected",
            tests=tests,
            isolated_workspace=str(isolated),
        )
    try:
        workspace.validate_precommit_git_state(isolated, contract)
    except workspace.AutopilotWorkspaceError:
        return finish(
            "blocked",
            "verification_git_state_changed",
            tests=tests,
            isolated_workspace=str(isolated),
        )
    completed_stages.append("verification")

    try:
        candidate_commit = _commit_candidate(isolated, delta)
        workspace.validate_candidate_scope(
            isolated=isolated,
            contract=contract,
            candidate_commit=candidate_commit,
            expected_delta=delta,
        )
        patch_result = _run_candidate_git(
            isolated,
            "diff",
            "--binary",
            f"{contract['base_commit']}..{candidate_commit}",
            capture_output=True,
            text=True,
        )
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc), isolated_workspace=str(isolated))
    packet = workspace.build_review_packet(
        contract=contract,
        candidate_commit=candidate_commit,
        delta=delta,
        patch=patch_result.stdout,
        tests=tests,
        executor_receipt={"workspace_write_confined": True, "executor": contract["executor"]},
    )
    packet_path = run_root / "review_packet.json"
    try:
        _write_immutable_packet(packet_path, packet)
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc), isolated_workspace=str(isolated))
    packet_digest = str(packet["packet_sha256"])
    critique_prompt = _review_prompt(
        packet,
        mission.build_stage_briefing(mission_packet, stage="critique")
        if mission_packet is not None
        else None,
    )
    review_prompt = _review_prompt(
        packet,
        mission.build_stage_briefing(mission_packet, stage="review")
        if mission_packet is not None
        else None,
    )

    if critique_runner is not None:
        critique_workspace = run_root / "critique-workspace"
        try:
            workspace.materialize_review_clone(
                isolated, critique_workspace, str(contract["base_commit"])
            )
        except workspace.AutopilotWorkspaceError as exc:
            return finish(
                "blocked", str(exc), review_packet_sha256=packet_digest
            )
        critique, error = _run_readonly_stage(
            root=critique_workspace,
            prompt=critique_prompt,
            runner=critique_runner,
            packet_digest=packet_digest,
            stage="critique",
        )
        if error:
            return finish("blocked", error, review_packet_sha256=packet_digest)
        if critique and critique["verdict"] not in {"PROCEED", "APPROVE", "APPROVE WITH NOTES"}:
            return finish("blocked", "critique_rejected", review_packet_sha256=packet_digest)
        completed_stages.append("critique")

    review_workspace = run_root / "review-workspace"
    try:
        workspace.materialize_review_clone(
            isolated, review_workspace, str(contract["base_commit"])
        )
    except workspace.AutopilotWorkspaceError as exc:
        return finish(
            "blocked", str(exc), review_packet_sha256=packet_digest
        )
    review, error = _run_readonly_stage(
        root=review_workspace,
        prompt=review_prompt,
        runner=review_runner,
        packet_digest=packet_digest,
        stage="review",
    )
    if error:
        return finish("blocked", error, review_packet_sha256=packet_digest)
    if review is None or review["verdict"] != "APPROVE":
        return finish("blocked", "review_rejected", review_packet_sha256=packet_digest)
    completed_stages.append("review")

    try:
        candidate_branch = workspace.promote_candidate_branch(
            source=source,
            isolated=isolated,
            contract=contract,
            candidate_commit=candidate_commit,
        )
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc), review_packet_sha256=packet_digest)
    completed_stages.append("promotion")
    return finish(
        "candidate_ready",
        None,
        candidate_branch=candidate_branch,
        candidate_commit=candidate_commit,
        review_packet_sha256=packet_digest,
        mission_packet_sha256=(
            mission_packet["packet_sha256"] if mission_packet is not None else None
        ),
        mission_approval_sha256=(
            contract.get("mission_approval_sha256")
            if mission_receipt is not None
            else None
        ),
        review=review,
        isolated_workspace=str(isolated),
    )
