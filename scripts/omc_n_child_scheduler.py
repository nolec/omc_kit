from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import signal
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable

if __package__:
    from .omc_executor_shadow import (
        build_n_child_dag_proposal,
        execute_reserved_single_child_grant_file,
        finalize_single_child_execution_reservation_file,
        reserve_single_child_execution_grant_file,
    )
else:
    from omc_executor_shadow import (
        build_n_child_dag_proposal,
        execute_reserved_single_child_grant_file,
        finalize_single_child_execution_reservation_file,
        reserve_single_child_execution_grant_file,
    )


def _blocked(reason_code: str) -> dict[str, Any]:
    return {"status": "blocked", "reason_code": reason_code}


def _parse_expiry(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _current_time(now: Callable[[], datetime]) -> datetime | None:
    try:
        current = now()
    except Exception:
        return None
    if (
        not isinstance(current, datetime)
        or current.tzinfo is None
        or current.utcoffset() is None
    ):
        return None
    return current


def _kill_process_group(proc: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def _run_bounded_adapter_command(
    command: list[str],
    *,
    cwd: str | Path | None = None,
    input_text: str = "",
    timeout_sec: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="omc-provider-io-") as raw_temp:
        temp_root = Path(raw_temp)
        input_path = temp_root / "input.json"
        stdout_path = temp_root / "stdout"
        stderr_path = temp_root / "stderr"
        input_path.write_text(input_text, encoding="utf-8")
        with (
            input_path.open("rb") as input_file,
            stdout_path.open("wb") as stdout_file,
            stderr_path.open("wb") as stderr_file,
        ):
            proc = subprocess.Popen(
                command,
                cwd=str(cwd) if cwd is not None else None,
                stdin=input_file,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            deadline = time.monotonic() + timeout_sec
            limit_exceeded = False
            timed_out = False
            while proc.poll() is None:
                response_size = stdout_path.stat().st_size + stderr_path.stat().st_size
                if response_size > max_response_bytes:
                    limit_exceeded = True
                    _kill_process_group(proc)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    _kill_process_group(proc)
                    break
                time.sleep(0.01)
            proc.wait()
        response_size = stdout_path.stat().st_size + stderr_path.stat().st_size
        limit_exceeded = limit_exceeded or response_size > max_response_bytes
        stdout = stdout_path.read_bytes()[: max_response_bytes + 1].decode(
            "utf-8", errors="replace"
        )
        stderr = stderr_path.read_bytes()[: max_response_bytes + 1].decode(
            "utf-8", errors="replace"
        )
        return {
            "returncode": int(proc.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "limit_exceeded": limit_exceeded,
            "timed_out": timed_out,
        }


def build_process_provider_runner(
    adapter_path: str | Path,
    *,
    capability_timeout_sec: float = 10.0,
    capability_profile: str = "provider_enforced",
) -> Callable[..., dict[str, Any]]:
    """Create a provider runner after an explicit fail-closed capability handshake."""
    source_path = Path(adapter_path).expanduser().resolve(strict=False)
    if not source_path.is_file() or not os.access(source_path, os.X_OK):
        raise ValueError("provider_adapter_unavailable")
    runtime = tempfile.TemporaryDirectory(prefix="omc-provider-adapter-")
    path = Path(runtime.name) / "provider-adapter"
    try:
        shutil.copy2(source_path, path)
        path.chmod(0o500)
        capability_proc = _run_bounded_adapter_command(
            [str(path), "capabilities"],
            timeout_sec=capability_timeout_sec,
            max_response_bytes=64 * 1024,
        )
    except OSError as exc:
        runtime.cleanup()
        raise ValueError("provider_adapter_capability_unavailable") from exc
    try:
        capabilities = json.loads(capability_proc["stdout"])
    except (TypeError, json.JSONDecodeError) as exc:
        runtime.cleanup()
        raise ValueError("provider_adapter_capability_invalid") from exc
    hard_token_profile = isinstance(capabilities, dict) and (
        capabilities.get("hard_total_token_limit") is True
        and capabilities.get("hard_output_limit") is True
        and capabilities.get("token_enforcement")
        == {
            "mode": "provider_enforced_total",
            "request_field": "max_total_tokens",
            "over_limit_behavior": "reject_before_or_during_generation",
        }
    )
    subscription_profile = isinstance(capabilities, dict) and (
        capabilities.get("execution_profile") == "subscription_bounded"
        and capabilities.get("authentication") == "chatgpt_subscription"
        and capabilities.get("hard_total_token_limit") is False
        and capabilities.get("hard_output_limit") is True
        and capabilities.get("token_usage_mode") == "observed_post_call"
        and capabilities.get("hard_bounds")
        == ["elapsed_time", "output_chars", "process_group"]
    )
    profile_supported = (
        capability_profile == "provider_enforced" and hard_token_profile
    ) or (
        capability_profile == "subscription_bounded" and subscription_profile
    )
    if (
        capability_proc["returncode"] != 0
        or capability_proc["timed_out"]
        or capability_proc["limit_exceeded"]
        or not isinstance(capabilities, dict)
        or capabilities.get("protocol") != "omc-provider/v1"
        or not profile_supported
    ):
        runtime.cleanup()
        raise ValueError("provider_token_limit_unsupported")

    def run(**kwargs: Any) -> dict[str, Any]:
        path = Path(runtime.name) / "provider-adapter"
        request = {
            "protocol": "omc-provider/v1",
            "executor": kwargs["executor"],
            "prompt": kwargs["prompt"],
            "project_root": str(Path(kwargs["project_root"]).resolve()),
            "timeout_sec": kwargs["timeout_sec"],
            "max_total_tokens": kwargs["max_total_tokens"],
            "max_output_chars": kwargs["max_output_chars"],
        }
        max_response_bytes = max(4096, int(request["max_output_chars"]) * 6 + 4096)
        try:
            proc = _run_bounded_adapter_command(
                [str(path), "execute"],
                cwd=request["project_root"],
                input_text=json.dumps(request, ensure_ascii=False),
                # Subscription adapters own the semantic deadline and need a
                # bounded window to kill their process group and emit a receipt.
                timeout_sec=float(request["timeout_sec"])
                + (2.0 if capability_profile == "subscription_bounded" else 0.0),
                max_response_bytes=max_response_bytes,
            )
        except OSError as exc:
            return {"returncode": 70, "output": f"provider adapter error: {exc}"}
        if proc["limit_exceeded"]:
            return {
                "returncode": 65,
                "output": "provider adapter output limit exceeded",
            }
        if proc["timed_out"]:
            return {"returncode": 124, "output": "provider adapter timeout"}
        if proc["returncode"] != 0:
            return {
                "returncode": proc["returncode"],
                "output": "\n".join(
                    part for part in (proc["stdout"], proc["stderr"]) if part
                ),
            }
        try:
            result = json.loads(proc["stdout"])
        except json.JSONDecodeError:
            return {"returncode": 65, "output": "provider adapter result invalid"}
        if not isinstance(result, dict):
            return {"returncode": 65, "output": "provider adapter result invalid"}
        output = result.get("output")
        if not isinstance(output, str) or len(output) > request["max_output_chars"]:
            return {
                "returncode": 65,
                "output": "provider adapter output limit exceeded",
            }
        return result

    return run


def _proposal_from_grant(
    grant: dict[str, Any], trusted_target: str | Path
) -> dict[str, Any]:
    request = {
        "schema_version": grant.get("schema_version"),
        "dag_id": grant.get("dag_id"),
        "execution_mode": grant.get("execution_mode"),
        "execution_requested": grant.get("execution_requested"),
        "children": deepcopy(grant.get("children")),
        "child_grants": deepcopy(grant.get("child_grants")),
        "child_prompts": deepcopy(grant.get("child_prompts")),
        "aggregate_budget": deepcopy(grant.get("aggregate_budget")),
    }
    if "target_binding" in grant:
        request["target_binding"] = deepcopy(grant["target_binding"])
    return build_n_child_dag_proposal(trusted_target, request)


def _validate_grant(
    grant: Any,
    *,
    trusted_target: str | Path,
    prompts: Any,
    now: Callable[[], datetime],
) -> tuple[dict[str, Any] | None, datetime | None, dict[str, Any] | None]:
    if (
        not isinstance(grant, dict)
        or grant.get("schema_version") != "omc-n-child-dag/v2"
        or grant.get("mode") != "n_child_dag_grant"
        or grant.get("status") != "ready"
        or grant.get("execution_allowed") is not True
        or grant.get("scheduler_eligible") is not True
    ):
        return None, None, _blocked("dag_grant_not_scheduler_eligible")
    if (
        grant.get("replay_check_required") is not True
        or any(
            grant.get(flag) is not False
            for flag in (
                "automatic_retry_allowed",
                "automatic_redistribution_allowed",
                "automatic_fallback_allowed",
                "automatic_resume_allowed",
            )
        )
    ):
        return None, None, _blocked("dag_grant_binding_mismatch")

    proposal = _proposal_from_grant(grant, trusted_target)
    if proposal.get("status") != "ready":
        return None, None, _blocked("dag_grant_binding_mismatch")
    proposal_fields = (
        "schema_version",
        "dag_id",
        "execution_mode",
        "execution_requested",
        "children",
        "child_grants",
        "child_prompts",
        "aggregate_budget",
        "scope_policy_version",
        "target_identity_sha256",
        "graph_sha256",
        "child_grant_sha256s",
        "prompt_sha256s",
        "aggregate_budget_sha256",
        "proposal_sha256",
    )
    if "target_binding" in grant or "target_binding" in proposal:
        proposal_fields += ("target_binding",)
    if any(grant.get(field) != proposal.get(field) for field in proposal_fields):
        return None, None, _blocked("dag_grant_binding_mismatch")

    child_ids = [child["child_id"] for child in proposal["children"]]
    ready_child_ids = [
        child["child_id"] for child in proposal["children"] if not child["depends_on"]
    ]
    approval_id = grant.get("approval_id")
    if (
        grant.get("child_ids") != child_ids
        or grant.get("ready_child_ids") != ready_child_ids
        or grant.get("fallback_action") != "parent_review"
        or not isinstance(approval_id, str)
        or not approval_id.strip()
        or approval_id != approval_id.strip()
        or approval_id
        in {child_grant["approval_id"] for child_grant in proposal["child_grants"]}
    ):
        return None, None, _blocked("dag_grant_binding_mismatch")
    if (
        not isinstance(prompts, dict)
        or set(prompts) != set(child_ids)
        or any(
            not isinstance(prompts[child_id], str)
            or not prompts[child_id].strip()
            or prompts[child_id] != proposal["child_prompts"][child_id]
            for child_id in child_ids
        )
    ):
        return None, None, _blocked("dag_execution_input_invalid")
    budget = proposal["aggregate_budget"]
    try:
        flattened_elapsed = float(grant.get("max_total_elapsed_sec"))
    except (TypeError, ValueError, OverflowError):
        flattened_elapsed = float("nan")
    if any(
        grant.get(flattened) != budget.get(source)
        for flattened, source in (
            ("max_external_calls", "max_external_calls"),
            ("max_parallelism", "max_parallelism"),
            ("max_output_chars", "max_output_chars"),
            ("max_total_tokens", "max_total_tokens"),
        )
    ) or not math.isfinite(flattened_elapsed) or flattened_elapsed != float(
        budget["max_total_elapsed_sec"]
    ):
        return None, None, _blocked("dag_grant_binding_mismatch")

    current = _current_time(now)
    expires_at = _parse_expiry(grant.get("approval_expires_at"))
    if current is None:
        return None, None, _blocked("dag_time_invalid")
    if expires_at is None or expires_at <= current:
        return None, None, _blocked("dag_grant_expired")
    child_expiries = [
        _parse_expiry(child_grant.get("approval_expires_at"))
        for child_grant in proposal["child_grants"]
    ]
    if any(expiry is None for expiry in child_expiries) or expires_at > min(
        expiry for expiry in child_expiries if expiry is not None
    ):
        return None, None, _blocked("dag_grant_binding_mismatch")
    return proposal, expires_at, None


def _persist_dag_state(
    ledger_path: str | Path, state: dict[str, Any], *, create: bool
) -> dict[str, Any]:
    path = Path(ledger_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"status": "indeterminate", "reason_code": "dag_ledger_write_failed"}
    lock_path = path.with_name(f"{path.name}.lock")
    if path.is_symlink() or lock_path.is_symlink():
        return _blocked("dag_ledger_path_invalid")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError:
        return _blocked("dag_ledger_lock_failed")

    temp_path: Path | None = None
    try:
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if path.is_symlink():
                return _blocked("dag_ledger_path_invalid")
            if create and path.exists():
                return _blocked("dag_already_started")
            revision = 1
            if not create:
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    return {
                        "status": "indeterminate",
                        "reason_code": "dag_ledger_read_failed",
                    }
                current_dag = current.get("dag") if isinstance(current, dict) else None
                if (
                    not isinstance(current, dict)
                    or current.get("schema_version") != 1
                    or not isinstance(current.get("revision"), int)
                    or isinstance(current.get("revision"), bool)
                    or not isinstance(current_dag, dict)
                    or current_dag.get("dag_id") != state.get("dag_id")
                ):
                    return {
                        "status": "indeterminate",
                        "reason_code": "dag_ledger_invalid",
                    }
                revision = current["revision"] + 1
            ledger = {
                "schema_version": 1,
                "revision": revision,
                "dag": deepcopy(state),
            }
            try:
                fd, raw_temp = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
                )
                temp_path = Path(raw_temp)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                    json.dump(
                        ledger,
                        temp_file,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    temp_file.write("\n")
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_path, path)
                temp_path = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                return {
                    "status": "indeterminate",
                    "reason_code": "dag_ledger_write_failed",
                }
            return {
                "status": "persisted",
                "reason_code": "dag_state_persisted",
                "ledger": ledger,
            }
    except OSError:
        return {"status": "indeterminate", "reason_code": "dag_ledger_lock_failed"}
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _result(state: dict[str, Any]) -> dict[str, Any]:
    children = state["children"]
    result = {
        "status": state["status"],
        "reason_code": state["reason_code"],
        "dag_id": state["dag_id"],
        "children": deepcopy(children),
        "completed_child_ids": [
            child["child_id"] for child in children if child["status"] == "succeeded"
        ],
        "pending_child_ids": [
            child["child_id"] for child in children if child["status"] == "not_started"
        ],
        "failed_child_ids": [
            child["child_id"]
            for child in children
            if child["status"] in {"failed", "timeout", "blocked", "indeterminate"}
        ],
        "external_call_count": state["external_call_count"],
        "total_elapsed_sec": state["total_elapsed_sec"],
        "total_output_chars": state["total_output_chars"],
        "input_tokens": state["input_tokens"],
        "output_tokens": state["output_tokens"],
        "total_tokens": state["total_tokens"],
    }
    if state.get("parent_review") is not None:
        result["parent_review"] = deepcopy(state["parent_review"])
    return result


def _review(state: dict[str, Any], reason_code: str) -> None:
    failed = [
        child["child_id"]
        for child in state["children"]
        if child["status"] in {"failed", "timeout", "blocked", "indeterminate"}
    ]
    state["status"] = (
        "indeterminate"
        if any(child["status"] == "indeterminate" for child in state["children"])
        else "review_required"
    )
    state["reason_code"] = reason_code
    state["parent_review"] = {
        "status": "review_required",
        "reason_code": "dag_child_requires_review",
        "failed_child_ids": failed,
    }


def _persist_or_review(
    ledger_path: str | Path, state: dict[str, Any]
) -> dict[str, Any] | None:
    persisted = _persist_dag_state(ledger_path, state, create=False)
    if persisted["status"] == "persisted":
        return None
    return {
        **persisted,
        "parent_review": {
            "status": "review_required",
            "reason_code": "dag_state_requires_review",
            "dag_reason_code": persisted["reason_code"],
        },
    }


def _run_git(
    args: list[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        check=False,
    )


def _path_in_scope(path: str, scope_paths: list[str]) -> bool:
    return any(path == scope or path.startswith(f"{scope}/") for scope in scope_paths)


def _path_crosses_symlink(project_root: Path, path: str) -> bool:
    current = project_root
    for part in PurePosixPath(path).parts:
        current /= part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False


def _provider_result_allows_patch(
    result: Any, *, max_total_tokens: Any
) -> bool:
    if not isinstance(result, dict):
        return False
    returncode = result.get("returncode")
    output = result.get("output")
    usage = result.get("token_usage")
    if (
        not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or returncode != 0
        or not isinstance(output, str)
        or not isinstance(max_total_tokens, int)
        or isinstance(max_total_tokens, bool)
        or max_total_tokens <= 0
        or not isinstance(usage, dict)
    ):
        return False
    if any(
        not isinstance(usage.get(field), int)
        or isinstance(usage.get(field), bool)
        or usage[field] < 0
        for field in ("input_tokens", "output_tokens", "total_tokens")
    ):
        return False
    return (
        usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
        and usage["total_tokens"] <= max_total_tokens
    )


def _scoped_provider_runner(
    runner: Callable[..., dict[str, Any]],
    *,
    project_root: Path,
    scope_paths: list[str],
    max_elapsed_sec: float,
    excluded_paths: set[Path],
) -> Callable[..., dict[str, Any]]:
    excluded_by_parent: dict[Path, set[str]] = {}
    for path in excluded_paths:
        excluded_by_parent.setdefault(path.parent, set()).add(path.name)
    project_identity = project_root.resolve(strict=False)
    excluded_relative_paths = []
    for path in excluded_paths:
        try:
            excluded_relative_paths.append(path.relative_to(project_identity))
        except ValueError:
            continue

    def run(**kwargs: Any) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="omc-n-child-") as raw_temp:
            workspace = Path(raw_temp) / "workspace"

            def ignore_runtime_files(directory: str, names: list[str]) -> list[str]:
                source = Path(directory).resolve(strict=False)
                excluded_names = excluded_by_parent.get(source, set())
                return [
                    name
                    for name in names
                    if name == ".git"
                    or (Path(directory) / name).is_symlink()
                    or name in excluded_names
                    or any(
                        name.startswith(f".{excluded_name}.")
                        and name.endswith(".tmp")
                        for excluded_name in excluded_names
                    )
                ]

            shutil.copytree(
                project_root,
                workspace,
                symlinks=False,
                ignore_dangling_symlinks=True,
                ignore=ignore_runtime_files,
            )
            commands = (
                ["init", "-q"],
                ["config", "user.email", "omc-scheduler@example.invalid"],
                ["config", "user.name", "OMC Scheduler"],
                ["add", "-A"],
                ["commit", "--allow-empty", "-qm", "baseline"],
            )
            for command in commands:
                completed = _run_git(command, cwd=workspace)
                if completed.returncode != 0:
                    return {
                        "returncode": 70,
                        "output": "scope workspace initialization failed",
                    }
            baseline_result = _run_git(["rev-parse", "HEAD"], cwd=workspace)
            if baseline_result.returncode != 0:
                return {
                    "returncode": 70,
                    "output": "scope workspace baseline failed",
                }
            baseline = baseline_result.stdout.decode("ascii").strip()
            bounded_timeout = min(float(kwargs["timeout_sec"]), max_elapsed_sec)
            runner_result = runner(
                **{
                    **kwargs,
                    "project_root": workspace,
                    "timeout_sec": bounded_timeout,
                }
            )
            if (
                not isinstance(runner_result, dict)
                or runner_result.get("returncode") != 0
            ):
                return runner_result
            if not _provider_result_allows_patch(
                runner_result,
                max_total_tokens=kwargs.get("max_total_tokens"),
            ):
                return runner_result

            for relative_path in excluded_relative_paths:
                runtime_path = workspace / relative_path
                if runtime_path.is_dir() and not runtime_path.is_symlink():
                    shutil.rmtree(runtime_path)
                else:
                    runtime_path.unlink(missing_ok=True)

            staged = _run_git(["add", "-A"], cwd=workspace)
            if staged.returncode != 0:
                return {
                    "returncode": 70,
                    "output": "scope workspace collection failed",
                }
            names = _run_git(
                ["diff", "--cached", "--name-only", "-z", baseline],
                cwd=workspace,
            )
            if names.returncode != 0:
                return {
                    "returncode": 70,
                    "output": "scope change inventory failed",
                }
            changed_paths = [
                value.decode("utf-8", errors="surrogateescape")
                for value in names.stdout.split(b"\0")
                if value
            ]
            violations = [
                path
                for path in changed_paths
                if not _path_in_scope(path, scope_paths)
                or _path_crosses_symlink(project_root, path)
                or (workspace / path).is_symlink()
            ]
            if violations:
                return {
                    "returncode": 65,
                    "output": "scope violation: " + ", ".join(sorted(violations)),
                    "reason_code": "scope_policy_violation",
                    "patch_applied": False,
                    "scope_violation_detected": True,
                    "token_usage": runner_result.get("token_usage"),
                }
            if not changed_paths:
                return {
                    **runner_result,
                    "patch_applied": False,
                    "scope_violation_detected": False,
                }

            patch_result = _run_git(
                ["diff", "--cached", "--binary", baseline], cwd=workspace
            )
            if patch_result.returncode != 0:
                return {
                    "returncode": 70,
                    "output": "scope patch generation failed",
                }
            check = _run_git(
                ["apply", "--check", "--whitespace=nowarn", "-"],
                cwd=project_root,
                input_bytes=patch_result.stdout,
            )
            if check.returncode != 0:
                return {
                    "returncode": 66,
                    "output": "scope patch conflict",
                    "reason_code": "scope_patch_conflict",
                    "patch_applied": False,
                    "scope_violation_detected": False,
                    "token_usage": runner_result.get("token_usage"),
                }
            applied = _run_git(
                ["apply", "--whitespace=nowarn", "-"],
                cwd=project_root,
                input_bytes=patch_result.stdout,
            )
            if applied.returncode != 0:
                return {
                    "returncode": 66,
                    "output": "scope patch apply failed",
                    "reason_code": "scope_patch_apply_failed",
                    "patch_applied": False,
                    "scope_violation_detected": False,
                    "token_usage": runner_result.get("token_usage"),
                }
            return {
                **runner_result,
                "patch_applied": True,
                "scope_violation_detected": False,
            }

    return run


def execute_n_child_provider_adapter(
    child_grant: dict[str, Any],
    child_ledger_path: str | Path,
    *,
    prompt: str,
    project_root: str | Path,
    runner: Callable[..., dict[str, Any]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    dag_expires_at: datetime | None = None,
    dag_ledger_path: str | Path,
    scope_paths: list[str],
    max_elapsed_sec: float,
) -> dict[str, Any]:
    """Execute one pre-reserved child through a hard-token-limited runner.

    An injected runner is the trusted provider boundary and must enforce the
    signed ``max_total_tokens`` value passed to it.
    """
    if runner is None:
        completed_at = _current_time(now)
        if completed_at is None:
            return {
                "status": "indeterminate",
                "reason_code": "dag_time_invalid",
                "external_call_performed": False,
            }
        finalized = finalize_single_child_execution_reservation_file(
            child_ledger_path,
            idempotency_key=child_grant["idempotency_key"],
            outcome={
                "status": "failed",
                "reason_code": "provider_token_limit_unsupported",
                "elapsed_sec": 0.0,
                "output_chars": 0,
            },
            now=completed_at,
        )
        if finalized.get("status") != "finalized":
            return {
                **finalized,
                "external_call_performed": False,
                "parent_review": {
                    "status": "review_required",
                    "reason_code": "provider_reservation_requires_review",
                },
            }
        return {
            "status": "failed",
            "reason_code": "provider_token_limit_unsupported",
            "external_call_performed": False,
            "recorded_elapsed_sec": 0.0,
            "output_chars": 0,
            "token_usage": None,
            "ledger_status": finalized["status"],
            "entry": finalized["entry"],
        }
    if (
        not callable(runner)
        or not isinstance(scope_paths, list)
        or not scope_paths
        or not math.isfinite(max_elapsed_sec)
        or max_elapsed_sec <= 0
    ):
        return {
            "status": "blocked",
            "reason_code": "provider_adapter_input_invalid",
            "external_call_performed": False,
        }
    scoped_runner = _scoped_provider_runner(
        runner,
        project_root=Path(project_root),
        scope_paths=scope_paths,
        max_elapsed_sec=max_elapsed_sec,
        excluded_paths={
            Path(dag_ledger_path).resolve(strict=False),
            Path(dag_ledger_path)
            .with_name(f"{Path(dag_ledger_path).name}.lock")
            .resolve(strict=False),
            Path(child_ledger_path).resolve(strict=False),
            Path(child_ledger_path)
            .with_name(f"{Path(child_ledger_path).name}.lock")
            .resolve(strict=False),
            Path(project_root).resolve(strict=False) / ".omc" / "cost_log.jsonl",
        },
    )
    return execute_reserved_single_child_grant_file(
        child_grant,
        child_ledger_path,
        prompt=prompt,
        project_root=project_root,
        runner=scoped_runner,
        monotonic=monotonic,
        now=now,
        sequence_expires_at=dag_expires_at,
        require_token_usage=True,
    )


def execute_n_child_dag_grant_file(
    grant: dict[str, Any],
    dag_ledger_path: str | Path,
    child_ledger_path: str | Path,
    *,
    trusted_target: str | Path,
    prompts: dict[str, str],
    project_root: str | Path,
    runner: Callable[..., dict[str, Any]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    scheduler_monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Consume one v2 grant as bounded ready waves with no automatic resume."""
    proposal, expires_at, error = _validate_grant(
        grant, trusted_target=trusted_target, prompts=prompts, now=now
    )
    if error is not None:
        return error
    assert proposal is not None and expires_at is not None
    if Path(dag_ledger_path).resolve(strict=False) == Path(child_ledger_path).resolve(
        strict=False
    ):
        return _blocked("separate_dag_ledger_required")
    if Path(project_root).resolve(strict=False) != Path(trusted_target).resolve(
        strict=False
    ):
        return _blocked("dag_target_mismatch")

    child_grants = {
        child_grant["child_id"]: child_grant
        for child_grant in proposal["child_grants"]
    }
    proposal_children = {
        child["child_id"]: child for child in proposal["children"]
    }
    state = {
        "dag_id": proposal["dag_id"],
        "proposal_sha256": proposal["proposal_sha256"],
        "approval_id": grant["approval_id"],
        "status": "reserved",
        "reason_code": "dag_reserved",
        "external_call_count": 0,
        "total_elapsed_sec": 0.0,
        "total_output_chars": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "parent_review": None,
        "children": [
            {
                "child_id": child["child_id"],
                "depends_on": deepcopy(child["depends_on"]),
                "status": "not_started",
                "reason_code": "ready" if not child["depends_on"] else "awaiting_dependency",
            }
            for child in proposal["children"]
        ],
    }
    if runner is None:
        for child in state["children"]:
            if not child["depends_on"]:
                child["status"] = "blocked"
                child["reason_code"] = "provider_token_limit_unsupported"
        _review(state, "child_not_succeeded")
        persisted = _persist_dag_state(dag_ledger_path, state, create=True)
        if persisted["status"] != "persisted":
            return {
                **persisted,
                "parent_review": {
                    "status": "review_required",
                    "reason_code": "dag_state_requires_review",
                    "dag_reason_code": persisted["reason_code"],
                },
            }
        return _result(state)
    persisted = _persist_dag_state(dag_ledger_path, state, create=True)
    if persisted["status"] != "persisted":
        return {
            **persisted,
            "parent_review": {
                "status": "review_required",
                "reason_code": "dag_state_requires_review",
                "dag_reason_code": persisted["reason_code"],
            },
        }

    try:
        started = float(scheduler_monotonic())
    except (TypeError, ValueError, OverflowError, StopIteration):
        _review(state, "dag_clock_invalid")
        maybe_error = _persist_or_review(dag_ledger_path, state)
        return maybe_error or _result(state)
    if not math.isfinite(started):
        _review(state, "dag_clock_invalid")
        maybe_error = _persist_or_review(dag_ledger_path, state)
        return maybe_error or _result(state)
    child_by_id = {child["child_id"]: child for child in state["children"]}
    while any(child["status"] == "not_started" for child in state["children"]):
        current = _current_time(now)
        if current is None or expires_at <= current:
            _review(state, "dag_time_invalid" if current is None else "dag_grant_expired")
            maybe_error = _persist_or_review(dag_ledger_path, state)
            return maybe_error or _result(state)
        remaining_elapsed = (
            float(grant["max_total_elapsed_sec"]) - state["total_elapsed_sec"]
        )
        if remaining_elapsed <= 0:
            _review(state, "dag_budget_exceeded")
            maybe_error = _persist_or_review(dag_ledger_path, state)
            return maybe_error or _result(state)

        ready = [
            child
            for child in state["children"]
            if child["status"] == "not_started"
            and all(child_by_id[dependency]["status"] == "succeeded" for dependency in child["depends_on"])
        ][: grant["max_parallelism"]]
        if not ready:
            _review(state, "dag_dependency_stalled")
            maybe_error = _persist_or_review(dag_ledger_path, state)
            return maybe_error or _result(state)
        if state["external_call_count"] + len(ready) > grant["max_external_calls"]:
            _review(state, "dag_budget_exceeded")
            maybe_error = _persist_or_review(dag_ledger_path, state)
            return maybe_error or _result(state)

        reservations: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for child in ready:
            child["status"] = "running"
            child["reason_code"] = "execution_claim_pending"
        state["status"] = "running"
        state["reason_code"] = "children_running"
        maybe_error = _persist_or_review(dag_ledger_path, state)
        if maybe_error is not None:
            return maybe_error

        for child in ready:
            child_grant = child_grants[child["child_id"]]
            reservation = reserve_single_child_execution_grant_file(
                child_grant,
                child_ledger_path,
                expected_scope_hash=child_grant["scope_hash"],
                now=current,
            )
            if reservation.get("status") != "reserved":
                child["status"] = (
                    "indeterminate"
                    if reservation.get("status") == "indeterminate"
                    else "blocked"
                )
                child["reason_code"] = reservation.get(
                    "reason_code", "execution_reservation_failed"
                )
                for other, reserved_grant in reservations:
                    finalized = finalize_single_child_execution_reservation_file(
                        child_ledger_path,
                        idempotency_key=reserved_grant["idempotency_key"],
                        outcome={
                            "status": "failed",
                            "reason_code": "reservation_batch_aborted",
                            "elapsed_sec": 0.0,
                            "output_chars": 0,
                        },
                        now=current,
                    )
                    if finalized.get("status") == "finalized":
                        other["status"] = "blocked"
                        other["reason_code"] = "reservation_batch_aborted_after_reserve"
                    else:
                        other["status"] = "indeterminate"
                        other["reason_code"] = "reservation_abort_terminalization_failed"
                for other in ready:
                    if other["status"] != "running":
                        continue
                    other["status"] = "not_started"
                    other["reason_code"] = "reservation_batch_aborted"
                _review(state, "child_reservation_failed")
                maybe_error = _persist_or_review(dag_ledger_path, state)
                return maybe_error or _result(state)
            reservations.append((child, child_grant))

        with ThreadPoolExecutor(max_workers=len(reservations)) as pool:
            futures = [
                pool.submit(
                    execute_n_child_provider_adapter,
                    child_grant,
                    child_ledger_path,
                    prompt=prompts[child["child_id"]],
                    project_root=project_root,
                    runner=runner,
                    monotonic=monotonic,
                    now=now,
                    dag_expires_at=expires_at,
                    dag_ledger_path=dag_ledger_path,
                    scope_paths=proposal_children[child["child_id"]]["scope_paths"],
                    max_elapsed_sec=remaining_elapsed,
                )
                for child, child_grant in reservations
            ]
            outcomes = []
            for future, (_, child_grant) in zip(futures, reservations):
                try:
                    outcomes.append(future.result())
                except Exception:
                    completed_at = _current_time(now)
                    try:
                        finalized = finalize_single_child_execution_reservation_file(
                            child_ledger_path,
                            idempotency_key=child_grant["idempotency_key"],
                            outcome={
                                "status": "failed",
                                "reason_code": "provider_adapter_exception",
                                "elapsed_sec": 0.0,
                                "output_chars": 0,
                            },
                            now=completed_at,
                        )
                    except Exception:
                        finalized = {
                            "status": "indeterminate",
                            "reason_code": "provider_exception_terminalization_failed",
                            "entry": None,
                        }
                    outcomes.append(
                        {
                            "status": "indeterminate",
                            "reason_code": "provider_adapter_exception",
                            "external_call_performed": True,
                            "ledger_status": finalized.get("status"),
                            "entry": finalized.get("entry"),
                        }
                    )

        for (child, _), outcome in zip(reservations, outcomes):
            if outcome.get("external_call_performed") is True:
                state["external_call_count"] += 1
            entry = outcome.get("entry") if isinstance(outcome, dict) else None
            durable = entry.get("outcome") if isinstance(entry, dict) else None
            elapsed = (
                durable.get("elapsed_sec", 0.0)
                if isinstance(durable, dict)
                else outcome.get("recorded_elapsed_sec", 0.0)
            )
            output_chars = (
                durable.get("output_chars", 0)
                if isinstance(durable, dict)
                else outcome.get("output_chars", 0)
            )
            state["total_output_chars"] += (
                output_chars
                if isinstance(output_chars, int) and not isinstance(output_chars, bool)
                else 0
            )
            token_usage = (
                durable.get("token_usage") if isinstance(durable, dict) else None
            )
            if isinstance(token_usage, dict):
                state["input_tokens"] += token_usage["input_tokens"]
                state["output_tokens"] += token_usage["output_tokens"]
                state["total_tokens"] += token_usage["total_tokens"]
            child.update(
                {
                    "status": outcome.get("status", "indeterminate"),
                    "reason_code": outcome.get(
                        "reason_code", "execution_result_invalid"
                    ),
                    "elapsed_sec": float(elapsed)
                    if isinstance(elapsed, (int, float))
                    and not isinstance(elapsed, bool)
                    else 0.0,
                    "output_chars": output_chars,
                    "token_usage": deepcopy(token_usage),
                    "usage_durability": "durable"
                    if outcome.get("ledger_status") == "finalized"
                    else "observed_only",
                }
            )
        try:
            finished = float(scheduler_monotonic())
        except (TypeError, ValueError, OverflowError, StopIteration):
            finished = float("nan")
        if not math.isfinite(finished) or finished < started:
            _review(state, "dag_clock_invalid")
            maybe_error = _persist_or_review(dag_ledger_path, state)
            return maybe_error or _result(state)
        state["total_elapsed_sec"] = finished - started

        if any(child["status"] != "succeeded" for child in ready):
            for child in state["children"]:
                if child["status"] == "not_started" and any(
                    child_by_id[dependency]["status"] != "succeeded"
                    for dependency in child["depends_on"]
                ):
                    child["reason_code"] = "dependency_not_succeeded"
            _review(state, "child_not_succeeded")
            maybe_error = _persist_or_review(dag_ledger_path, state)
            return maybe_error or _result(state)
        if (
            state["total_elapsed_sec"] > grant["max_total_elapsed_sec"]
            or state["total_output_chars"] > grant["max_output_chars"]
            or state["total_tokens"] > grant["max_total_tokens"]
        ):
            _review(state, "dag_budget_exceeded")
            maybe_error = _persist_or_review(dag_ledger_path, state)
            return maybe_error or _result(state)
        maybe_error = _persist_or_review(dag_ledger_path, state)
        if maybe_error is not None:
            return maybe_error

    state["status"] = "completed"
    state["reason_code"] = "dag_completed"
    maybe_error = _persist_or_review(dag_ledger_path, state)
    return maybe_error or _result(state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute an approved bounded N-child DAG through a provider adapter."
    )
    parser.add_argument("--grant-file", type=Path, required=True)
    parser.add_argument("--prompts-file", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--dag-ledger", type=Path, required=True)
    parser.add_argument("--child-ledger", type=Path, required=True)
    parser.add_argument("--provider-adapter", type=Path, required=True)
    parser.add_argument(
        "--provider-profile",
        choices=("provider_enforced", "subscription_bounded"),
        default="provider_enforced",
        help="Capability contract required from the provider adapter.",
    )
    args = parser.parse_args(argv)
    try:
        grant = json.loads(args.grant_file.read_text(encoding="utf-8"))
        prompts = json.loads(args.prompts_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result = _blocked("dag_input_unavailable")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    if not isinstance(grant, dict) or not isinstance(prompts, dict):
        result = _blocked("dag_input_invalid")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    try:
        runner = build_process_provider_runner(
            args.provider_adapter,
            capability_profile=args.provider_profile,
        )
    except ValueError as exc:
        result = _blocked(str(exc))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    result = execute_n_child_dag_grant_file(
        grant,
        args.dag_ledger,
        args.child_ledger,
        trusted_target=args.target.resolve(),
        prompts=prompts,
        project_root=args.target.resolve(),
        runner=runner,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
