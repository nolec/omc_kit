#!/usr/bin/env python3
"""Project-owned, tool-neutral quality gate contract and runner."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIG_SCHEMA = "omc-quality-gates/v1"
PROPOSAL_SCHEMA = "omc-quality-gate-proposal/v1"
RECEIPT_SCHEMA = "omc-quality-gate-approval/v1"
CONFIG_PATH = Path(".omc/quality-gates.json")
RECEIPT_PATH = Path(".omc/state/quality-gate-approval.json")
_PURPOSES = {"preflight", "test", "typecheck", "lint", "build"}
_SCOPES = {"changed", "affected", "full"}
_PLACEHOLDERS = {"{changed_files}", "{base_ref}", "{head_ref}"}
_SHELL_TOKENS = {"|", "||", "&&", ";", ">", ">>", "<", "<<"}
_HOST_BOUND_PATH = re.compile(r"(?:^|[=:])(?:/Users|/home)/[^/:]+(?:/|$)")
_HOST_BOUND_ENV = re.compile(r"^(?:HOME|PATH|USERPROFILE)=")
_HOST_BOUND_REFERENCE = re.compile(
    r"(?:^|[=:])(?:~[^/:=]*(?=/|$)|\$[A-Za-z_][A-Za-z0-9_]*(?=/|\\|$))"
)
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$")
_SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "cmd.exe",
    "csh",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "pwsh",
    "sh",
    "tcsh",
    "zsh",
}


class QualityGateError(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_file_sha256(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualityGateError(f"invalid JSON: {path}") from error
    return _canonical_sha256(value)


def _safe_relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise QualityGateError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise QualityGateError(f"{label} must stay inside the project")
    return path.as_posix()


def _validate_argv(argv: Any) -> list[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v for v in argv):
        raise QualityGateError("gate argv must be a non-empty string array")
    for token in argv:
        if (
            token in _SHELL_TOKENS
            or "$(" in token
            or "`" in token
            or "\n" in token
            or "\r" in token
        ):
            raise QualityGateError(f"unsafe argv token: {token}")
        for marker in _PLACEHOLDERS:
            if marker in token and token != marker:
                raise QualityGateError(f"placeholder must occupy one argv token: {marker}")
        if "{" in token or "}" in token:
            if token not in _PLACEHOLDERS:
                raise QualityGateError(f"unsupported argv placeholder: {token}")
    return list(argv)


def _validate_portable_config(config: dict[str, Any]) -> None:
    for gate in config["gates"]:
        executable = gate["argv"][0]
        executable_path = Path(executable)
        if (
            executable_path.is_absolute()
            or ".." in executable_path.parts
            or executable_path.name == "env"
            or executable_path.name.lower() in _SHELL_EXECUTABLES
        ):
            raise QualityGateError(
                f"config_not_portable: executable must use PATH or a project-relative path in gate {gate['id']}"
            )
        for token in gate["argv"]:
            if (
                _HOST_BOUND_ENV.match(token)
                or _HOST_BOUND_PATH.search(token)
                or _HOST_BOUND_REFERENCE.search(token)
            ):
                raise QualityGateError(
                    f"config_not_portable: host-bound argv token in gate {gate['id']}"
                )


def _validate_config_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != CONFIG_SCHEMA:
        raise QualityGateError("unsupported quality gate schema")
    base_ref = data.get("base_ref")
    if (
        not isinstance(base_ref, str)
        or not base_ref.strip()
        or base_ref.startswith("-")
        or any(char.isspace() for char in base_ref)
    ):
        raise QualityGateError("base_ref must be a safe non-empty git ref")

    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise QualityGateError("at least one evidence entry is required")
    evidence_paths: set[str] = set()
    for entry in evidence:
        if not isinstance(entry, dict):
            raise QualityGateError("evidence entry must be an object")
        path = _safe_relative_path(entry.get("path"), label="evidence path")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise QualityGateError("evidence sha256 must contain 64 characters")
        try:
            int(digest, 16)
        except ValueError as error:
            raise QualityGateError("evidence sha256 must be hexadecimal") from error
        if path in evidence_paths:
            raise QualityGateError(f"duplicate evidence path: {path}")
        evidence_paths.add(path)

    gates = data.get("gates")
    if not isinstance(gates, list) or not gates:
        raise QualityGateError("at least one quality gate is required")
    gate_ids: set[str] = set()
    product_gate_seen = False
    for gate in gates:
        if not isinstance(gate, dict):
            raise QualityGateError("gate must be an object")
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not gate_id or gate_id in gate_ids:
            raise QualityGateError("gate id must be non-empty and unique")
        gate_ids.add(gate_id)
        if gate.get("purpose") not in _PURPOSES:
            raise QualityGateError(f"unsupported gate purpose: {gate.get('purpose')}")
        if gate.get("purpose") == "preflight":
            if product_gate_seen:
                raise QualityGateError("preflight gates must precede product gates")
            if gate.get("scope") != "full" or gate.get("required") is not True:
                raise QualityGateError("preflight gates must be required full-scope gates")
        else:
            product_gate_seen = True
        gate_argv = _validate_argv(gate.get("argv"))
        scope = gate.get("scope")
        if scope not in _SCOPES:
            raise QualityGateError(f"unsupported gate scope: {scope}")
        if scope == "changed" and "{changed_files}" not in gate_argv:
            raise QualityGateError("changed scope requires {changed_files} in argv")
        if scope == "affected" and not {"{base_ref}", "{head_ref}"} <= set(gate_argv):
            raise QualityGateError("affected scope requires {base_ref} and {head_ref} in argv")
        if not isinstance(gate.get("required"), bool):
            raise QualityGateError("gate required must be boolean")
        timeout = gate.get("timeout_sec")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
            raise QualityGateError("gate timeout_sec must be between 1 and 3600")
    return data


def load_config_snapshot(root: Path) -> tuple[dict[str, Any], str]:
    path = root / CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise QualityGateError("quality gate config is missing") from error
    except (OSError, json.JSONDecodeError) as error:
        raise QualityGateError("quality gate config is invalid JSON") from error
    config = _validate_config_data(data)
    return config, _canonical_sha256(config)


def load_config(root: Path) -> dict[str, Any]:
    return load_config_snapshot(root)[0]


def _load_receipt(root: Path) -> dict[str, Any] | None:
    path = root / RECEIPT_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != RECEIPT_SCHEMA:
        return None
    return data


def _stale_evidence(root: Path, config: dict[str, Any]) -> list[str]:
    stale: list[str] = []
    for entry in config["evidence"]:
        path = root / entry["path"]
        if not path.is_file() or file_sha256(path) != entry["sha256"]:
            stale.append(entry["path"])
    return stale


def _status_for_snapshot(root: Path, config: dict[str, Any], config_sha256: str) -> dict[str, Any]:
    stale = _stale_evidence(root, config)
    if stale:
        return {"status": "stale", "config_sha256": config_sha256, "stale_evidence": stale}

    receipt = _load_receipt(root)
    if not receipt:
        return {"status": "approval_required", "config_sha256": config_sha256}
    if receipt.get("config_sha256") != config_sha256:
        return {"status": "approval_stale", "config_sha256": config_sha256}
    if any(gate["scope"] == "full" for gate in config["gates"]) and not receipt.get("allow_full"):
        return {"status": "full_scope_approval_required", "config_sha256": config_sha256}
    return {"status": "ready", "config_sha256": config_sha256}


def status(root: Path) -> dict[str, Any]:
    config_path = root / CONFIG_PATH
    if not config_path.is_file():
        return {"status": "unconfigured", "config_path": CONFIG_PATH.as_posix()}
    try:
        config, config_sha256 = load_config_snapshot(root)
    except QualityGateError as error:
        result = {"status": "invalid", "reason": str(error)}
        if not config_path.is_symlink():
            try:
                result["config_file_sha256"] = file_sha256(config_path)
            except OSError:
                pass
        return result
    return _status_for_snapshot(root, config, config_sha256)


def readiness(root: Path) -> str:
    current = status(root)["status"]
    if current == "unconfigured":
        return "missing"
    if current in {"invalid", "stale"}:
        return "invalid"
    if current == "approval_stale":
        return "approval_stale"
    if current in {"approval_required", "full_scope_approval_required"}:
        return "approval_required"
    return "ready"


def approve(root: Path, *, expected_config_sha256: str, allow_full: bool = False) -> dict[str, Any]:
    config, actual = load_config_snapshot(root)
    if expected_config_sha256 != actual:
        raise QualityGateError("config sha256 does not match the approval request")
    stale = _stale_evidence(root, config)
    if stale:
        raise QualityGateError(f"cannot approve stale evidence: {', '.join(stale)}")
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "config_sha256": actual,
        "allow_full": bool(allow_full),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = root / RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _expand_argv(
    argv: list[str],
    *,
    base_ref: str,
    changed_files: list[str],
) -> list[str]:
    expanded: list[str] = []
    for token in argv:
        if token == "{changed_files}":
            expanded.extend(f"./{path}" if path.startswith("-") else path for path in changed_files)
        elif token == "{base_ref}":
            expanded.append(base_ref)
        elif token == "{head_ref}":
            expanded.append("HEAD")
        else:
            expanded.append(token)
    return expanded


def _text_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _git_changed_files(root: Path, base_ref: str) -> list[str]:
    changed: list[str] = []
    for diff_range in (f"{base_ref}...HEAD", "HEAD"):
        result = subprocess.run(
            ["git", "diff", "--name-only", "-z", diff_range, "--"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise QualityGateError(f"cannot resolve changed files from {base_ref}")
        changed.extend(os.fsdecode(item) for item in result.stdout.split(b"\0") if item)
    return list(dict.fromkeys(changed))


def _executable_available(
    root: Path,
    executable: str,
    *,
    search_path: str | None = None,
    working_directory: Path | None = None,
) -> bool:
    effective_cwd = working_directory or root
    if "/" not in executable:
        inherited_path = os.environ.get("PATH", os.defpath)
        path_value = inherited_path if search_path is None else search_path
        resolved_path = os.pathsep.join(
            str(Path(entry) if Path(entry).is_absolute() else effective_cwd / entry)
            for entry in path_value.split(os.pathsep)
        )
        return shutil.which(executable, path=resolved_path) is not None
    path = Path(executable)
    candidate = path if path.is_absolute() else effective_cwd / path
    return candidate.is_file() and os.access(candidate, os.X_OK)


def _declared_executables(
    root: Path, argv: list[str]
) -> tuple[list[tuple[str, str | None, Path]], list[tuple[str, Path]]]:
    executables = [(argv[0], None, root)]
    working_directories: list[tuple[str, Path]] = []
    if Path(argv[0]).name != "env":
        return executables, working_directories
    environment_path = os.environ.get("PATH", os.defpath)
    utility_path: str | None = None
    effective_cwd = root
    chdir_value: str | None = None
    index = 1
    option_phase = True
    while index < len(argv):
        token = argv[index]
        if option_phase:
            if token == "--":
                option_phase = False
                index += 1
                continue
            if token in {"-i", "--ignore-environment"}:
                environment_path = os.defpath
                index += 1
                continue
            if token in {"-0", "--null", "-v"}:
                index += 1
                continue
            if token in {"-P", "-u", "--unset", "-C", "--chdir"}:
                if index + 1 >= len(argv):
                    raise QualityGateError(
                        f"environment_not_ready: env option requires an argument: {token}"
                    )
                option_value = argv[index + 1]
                if token == "-P":
                    utility_path = option_value
                elif token in {"-u", "--unset"} and option_value == "PATH":
                    environment_path = os.defpath
                elif token in {"-C", "--chdir"}:
                    chdir_value = option_value
                index += 2
                continue
            if token.startswith("--unset=") or token.startswith("--chdir="):
                if token.endswith("="):
                    raise QualityGateError(
                        f"environment_not_ready: env option requires an argument: {token}"
                    )
                if token == "--unset=PATH":
                    environment_path = os.defpath
                elif token.startswith("--chdir="):
                    chdir_value = token.removeprefix("--chdir=")
                index += 1
                continue
            if token in {"-S", "--split-string"}:
                if index + 1 >= len(argv):
                    raise QualityGateError(
                        f"environment_not_ready: env option requires an argument: {token}"
                    )
                try:
                    split = shlex.split(argv[index + 1])
                except ValueError as error:
                    raise QualityGateError(
                        f"environment_not_ready: invalid env split string: {error}"
                    ) from error
                argv = [*argv[:index], *split, *argv[index + 2 :]]
                continue
            if token.startswith("-S") and len(token) > 2:
                try:
                    split = shlex.split(token[2:])
                except ValueError as error:
                    raise QualityGateError(
                        f"environment_not_ready: invalid env split string: {error}"
                    ) from error
                argv = [*argv[:index], *split, *argv[index + 1 :]]
                continue
            if token.startswith("-"):
                raise QualityGateError(
                    f"environment_not_ready: unsupported env wrapper option: {token}"
                )
            option_phase = False
        assignment = _ENV_ASSIGNMENT.fullmatch(token)
        if assignment:
            if token.startswith("PATH="):
                environment_path = assignment.group(1)
            index += 1
            continue
        break
    if chdir_value is not None:
        chdir_path = Path(chdir_value)
        effective_cwd = chdir_path if chdir_path.is_absolute() else root / chdir_path
        working_directories.append((chdir_value, effective_cwd))
    if index < len(argv):
        search_path = utility_path if utility_path is not None else environment_path
        executables.append((argv[index], search_path, effective_cwd))
    return executables, working_directories


def run(root: Path) -> dict[str, Any]:
    config, config_sha256 = load_config_snapshot(root)
    current = _status_for_snapshot(root, config, config_sha256)
    if current["status"] != "ready":
        raise QualityGateError(f"quality gate is not ready: {current['status']}")
    files = _git_changed_files(root, config["base_ref"])
    required_executables: list[tuple[str, str | None, Path]] = []
    required_working_directories: list[tuple[str, Path]] = []
    for gate in config["gates"]:
        if gate["required"]:
            executables, working_directories = _declared_executables(root, gate["argv"])
            required_executables.extend(executables)
            required_working_directories.extend(working_directories)
    missing_executables = sorted(
        {
            executable
            for executable, search_path, working_directory in required_executables
            if not _executable_available(
                root,
                executable,
                search_path=search_path,
                working_directory=working_directory,
            )
        }
    )
    missing_working_directories = sorted(
        {
            declared
            for declared, working_directory in required_working_directories
            if not working_directory.is_dir()
        }
    )
    if missing_executables or missing_working_directories:
        return {
            "status": "blocked",
            "reason": "environment_not_ready",
            "config_sha256": current["config_sha256"],
            "changed_files": files,
            "missing_executables": missing_executables,
            "missing_working_directories": missing_working_directories,
            "gates": [],
        }
    results: list[dict[str, Any]] = []
    blocked = False
    preflight_failed = False
    for gate in config["gates"]:
        argv = _expand_argv(gate["argv"], base_ref=config["base_ref"], changed_files=files)
        if preflight_failed:
            results.append(
                {
                    "id": gate["id"],
                    "purpose": gate["purpose"],
                    "scope": gate["scope"],
                    "argv": argv,
                    "status": "skipped",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "",
                    "reason": "preflight_failed",
                }
            )
            continue
        if gate["scope"] == "changed" and not files:
            results.append(
                {
                    "id": gate["id"],
                    "purpose": gate["purpose"],
                    "scope": gate["scope"],
                    "argv": argv,
                    "status": "skipped",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "",
                    "reason": "no_changed_files",
                }
            )
            continue
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=gate["timeout_sec"],
                check=False,
            )
            gate_status = "passed" if completed.returncode == 0 else "failed"
            result = {
                "id": gate["id"],
                "purpose": gate["purpose"],
                "scope": gate["scope"],
                "argv": argv,
                "status": gate_status,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            # Python's missing entrypoint is an execution problem, not a test
            # failure. Require the interpreter diagnostic as well as absence;
            # exit 2 by itself is also an ordinary test or argument failure.
            if (
                completed.returncode == 2
                and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(argv[0]).name, re.IGNORECASE)
                and len(argv) > 1 and not argv[1].startswith("-")
                and not (root / argv[1]).exists()
                and "can't open file" in completed.stderr
                and "[Errno 2]" in completed.stderr
            ):
                result["execution_status"] = "unavailable"
                result["reason"] = "missing_verification_file"
        except subprocess.TimeoutExpired as error:
            gate_status = "timeout"
            result = {
                "id": gate["id"],
                "purpose": gate["purpose"],
                "scope": gate["scope"],
                "argv": argv,
                "status": gate_status,
                "returncode": None,
                "stdout": _text_output(error.stdout),
                "stderr": _text_output(error.stderr),
            }
        except OSError as error:
            gate_status = "execution_error"
            result = {
                "id": gate["id"],
                "purpose": gate["purpose"],
                "scope": gate["scope"],
                "argv": argv,
                "status": gate_status,
                "returncode": None,
                "stdout": "",
                "stderr": str(error),
            }
        if gate["required"] and gate_status != "passed":
            blocked = True
            if gate["purpose"] == "preflight":
                preflight_failed = True
        results.append(result)
    return {
        "status": "blocked" if blocked else "passed",
        "config_sha256": current["config_sha256"],
        "changed_files": files,
        "gates": results,
    }


def validate_proposal(proposal: Any, root: Path) -> dict[str, Any]:
    if not isinstance(proposal, dict) or proposal.get("schema_version") != PROPOSAL_SCHEMA:
        raise QualityGateError("unsupported quality gate proposal schema")
    config = _validate_config_data(proposal.get("config"))
    _validate_portable_config(config)
    rationale = proposal.get("rationale")
    if not isinstance(rationale, list):
        raise QualityGateError("proposal rationale must be an array")
    evidence_paths = {entry["path"] for entry in config["evidence"]}
    by_gate: dict[str, dict[str, Any]] = {}
    for entry in rationale:
        if not isinstance(entry, dict) or not isinstance(entry.get("gate_id"), str):
            raise QualityGateError("proposal rationale entry is invalid")
        by_gate[entry["gate_id"]] = entry
    for gate in config["gates"]:
        entry = by_gate.get(gate["id"])
        if not entry:
            raise QualityGateError(f"missing rationale for gate: {gate['id']}")
        paths = entry.get("evidence_paths")
        if not isinstance(paths, list) or not paths or not set(paths) <= evidence_paths:
            raise QualityGateError(f"invalid evidence paths for gate: {gate['id']}")
        if not isinstance(entry.get("scope_reason"), str) or not entry["scope_reason"].strip():
            raise QualityGateError(f"missing scope reason for gate: {gate['id']}")
    if any(gate["scope"] == "full" for gate in config["gates"]):
        if proposal.get("full_scope_requested") is not True:
            raise QualityGateError("full scope proposal requires an explicit request")
    for evidence in config["evidence"]:
        path = root / evidence["path"]
        if not path.is_file() or file_sha256(path) != evidence["sha256"]:
            raise QualityGateError(f"proposal evidence is stale: {evidence['path']}")
    return proposal


def _atomic_write_config(root: Path, config: dict[str, Any]) -> None:
    config_path = root / CONFIG_PATH
    if config_path.parent.is_symlink():
        raise QualityGateError("quality gate config must stay inside the project")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.is_symlink() or not config_path.parent.resolve().is_relative_to(root.resolve()):
        raise QualityGateError("quality gate config must stay inside the project")
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=".quality-gates.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            temp_name = temp.name
        os.replace(temp_name, config_path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def apply_proposal(
    root: Path,
    proposal_path: Path,
    *,
    expect_absent: bool = False,
    expected_current_sha256: str | None = None,
    expected_current_file_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualityGateError("quality gate proposal is invalid JSON") from error
    validated = validate_proposal(proposal, root)
    proposed_config = validated["config"]
    proposed_sha256 = _canonical_sha256(proposed_config)
    config_path = root / CONFIG_PATH
    lock_path = root / ".omc" / "state" / "quality-gate-config.lock"
    if (root / ".omc").is_symlink() or lock_path.parent.is_symlink():
        raise QualityGateError("quality gate state must stay inside the project")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not lock_path.parent.resolve().is_relative_to(root.resolve()):
        raise QualityGateError("quality gate state must stay inside the project")

    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if config_path.exists():
            if config_path.is_symlink():
                raise QualityGateError("quality gate config must not be a symlink")
            if expect_absent:
                raise QualityGateError("--expect-absent conflicts with an existing config")
            current_file_sha256 = file_sha256(config_path)
            try:
                _, current_sha256 = load_config_snapshot(root)
            except QualityGateError:
                if expected_current_sha256 is not None:
                    raise QualityGateError(
                        "invalid config requires --expected-current-file-sha256"
                    )
                if expected_current_file_sha256 is None:
                    raise QualityGateError(
                        "invalid config requires --expected-current-file-sha256"
                    )
                if current_file_sha256 != expected_current_file_sha256:
                    raise QualityGateError("current config file sha256 does not match")
            else:
                if expected_current_file_sha256 is not None:
                    raise QualityGateError(
                        "valid config requires --expected-current-sha256"
                    )
                if current_sha256 == proposed_sha256:
                    return {"status": "unchanged", "config_sha256": proposed_sha256}
                if expected_current_sha256 is None:
                    raise QualityGateError("existing config requires --expected-current-sha256")
                if current_sha256 != expected_current_sha256:
                    raise QualityGateError("current config sha256 does not match")
        else:
            if not expect_absent:
                raise QualityGateError("missing config requires --expect-absent")
            if expected_current_sha256 is not None or expected_current_file_sha256 is not None:
                raise QualityGateError("expected current sha256 requires an existing config")
        _atomic_write_config(root, proposed_config)

    return {"status": "applied", "config_sha256": proposed_sha256}



BASELINE_PATH = Path(".omc/state/failure-baseline.json")


def _diagnostic_identity(root: Path) -> dict[str, str]:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    config, config_sha = load_config_snapshot(root)
    executables = []
    for gate in config["gates"]:
        declared, _ = _declared_executables(root, gate["argv"])
        for command, search_path, working_directory in declared:
            effective_cwd = working_directory.resolve()
            path_value = os.environ.get("PATH", os.defpath) if search_path is None else search_path
            resolved_path = os.pathsep.join(
                str(Path(entry) if Path(entry).is_absolute() else effective_cwd / entry)
                for entry in path_value.split(os.pathsep)
            )
            if "/" in command:
                candidate = Path(command)
                if not candidate.is_absolute():
                    candidate = effective_cwd / candidate
                resolved = str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
            else:
                resolved = shutil.which(command, path=resolved_path)
            executables.append({"gate": gate["id"], "command": command,
                                "cwd": str(effective_cwd), "path": resolved_path,
                                "resolved": resolved,
                                "sha256": file_sha256(Path(resolved)) if resolved else "missing"})
    environment = _canonical_sha256({
        "python": sys.version, "platform": sys.platform,
        "path": os.environ.get("PATH", ""), "executables": executables,
    })
    return {"revision": revision, "config": config_sha, "environment": environment}


def _failure_signature(gate: dict[str, Any]) -> str | None:
    if gate.get("execution_status") == "unavailable":
        return None
    if gate.get("status") != "failed" or not isinstance(gate.get("returncode"), int):
        return None
    stdout, stderr = gate.get("stdout"), gate.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return None
    if not (stdout or stderr) or len(stdout) + len(stderr) > 1000000:
        return None
    # Exact output fingerprint: no inference of cause or aggressive normalization.
    return _canonical_sha256({"stdout": stdout, "stderr": stderr, "returncode": gate["returncode"]})


def _baseline_worktree_digest(root: Path) -> str:
    if not (root / ".git").exists():
        return ""
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).split(b"\0")
    fingerprints = {}
    for raw in paths:
        if not raw:
            continue
        name = os.fsdecode(raw)
        if name.startswith(".omc/"):
            continue
        p = root / name
        fingerprints[name] = ("symlink:" + os.readlink(p)) if p.is_symlink() else (file_sha256(p) if p.is_file() else "missing")
    return _canonical_sha256(fingerprints)


def capture_failure_baseline(root: Path) -> dict[str, Any]:
    path = root / BASELINE_PATH
    if any(p.is_symlink() for p in [root / ".omc", path.parent, path]):
        raise QualityGateError("baseline path must not be a symlink")
    if path.exists():
        raise QualityGateError("baseline already exists; no automatic replacement")
    before = _diagnostic_identity(root)
    worktree = _baseline_worktree_digest(root)
    before_diff = subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=root) if (root / ".git").exists() else b""
    report = run(root)
    if before != _diagnostic_identity(root):
        raise QualityGateError("baseline identity changed during execution")
    after_diff = subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=root) if (root / ".git").exists() else b""
    if before_diff != after_diff or worktree != _baseline_worktree_digest(root):
        raise QualityGateError("working tree changed during baseline execution")
    baseline = {"schema": "omc-failure-baseline/v1", "identity": before,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "working_diff_sha256": hashlib.sha256(before_diff).hexdigest(), "working_files_sha256": worktree, "report": report}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lock readers/writers and publish atomically, without replacing an existing record.
    lock_path = path.parent / "failure-baseline.lock"
    if lock_path.is_symlink():
        raise QualityGateError("baseline lock must not be a symlink")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as tmp:
            json.dump(baseline, tmp, ensure_ascii=False)
            temp = Path(tmp.name)
        try:
            os.chmod(temp, 0o600)
            os.link(temp, path)
        except FileExistsError as error:
            raise QualityGateError("baseline already exists") from error
        finally:
            temp.unlink(missing_ok=True)
    return baseline


def diagnose_failures(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    path = root / BASELINE_PATH
    reason = None
    baseline = None
    baseline_revision = None
    try:
        if any(p.is_symlink() for p in [root / ".omc", path.parent, path]):
            raise ValueError("symlink")
        baseline = json.loads(path.read_text())
        if not isinstance(baseline, dict):
            raise ValueError("baseline type")
        identity = _diagnostic_identity(root)
        saved = baseline["identity"]
        if not isinstance(saved, dict) or any(
            not isinstance(saved.get(key), str) or not saved[key]
            for key in ("revision", "config", "environment")
        ):
            raise ValueError("identity type")
        if baseline["schema"] != "omc-failure-baseline/v1":
            raise ValueError("schema")
        if saved["config"] != identity["config"] or saved["environment"] != identity["environment"]:
            raise ValueError("identity mismatch")
        if saved["revision"] != identity["revision"]:
            ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", saved["revision"], identity["revision"]], cwd=root, capture_output=True)
            if ancestor.returncode != 0:
                raise ValueError("revision mismatch")
        saved_report = baseline["report"]
        if not isinstance(saved_report, dict) or not isinstance(saved_report.get("gates"), list):
            raise ValueError("report type")
        saved_gates = saved_report["gates"]
        if any(not isinstance(g, dict) or not isinstance(g.get("id"), str) or not g["id"]
               for g in saved_gates):
            raise ValueError("gate type")
        old_gates = {g["id"]: g for g in saved_gates}
        if len(old_gates) != len(saved_gates):
            raise ValueError("duplicate gates")
        baseline_revision = saved["revision"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, QualityGateError):
        reason = "baseline_missing_or_invalid"
        old_gates = {}
    rows = []
    for gate in report.get("gates", []):
        if gate.get("status") == "passed":
            continue
        signature = _failure_signature(gate)
        old = old_gates.get(gate.get("id"))
        old_signature = _failure_signature(old) if isinstance(old, dict) else None
        classification = "unknown"
        if reason is None and signature is not None and old is not None and gate.get("argv") == old.get("argv"):
            if old_signature is not None:
                classification = "existing" if signature == old_signature else "new"
            elif old.get("status") == "passed":
                classification = "new"
        rows.append({"id": gate.get("id"), "classification": classification,
                     "label": {"existing": "기존 실패", "new": "신규 실패", "unknown": "구분 불가"}[classification],
                     "reason": reason or (gate.get("reason") if gate.get("execution_status") == "unavailable" else None) or ("exact_output_comparison" if classification != "unknown" else "incomplete_or_uncomparable_output"),
                     "raw_output_sha256": signature})
    return {"status": "unknown" if reason else "compared", "reason": reason,
            "baseline_revision": baseline_revision,
            "gates": rows, "completion_policy": "unchanged_required_failure_blocks"}


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OMC tool-neutral quality gate runner")
    parser.add_argument("--target", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--config-sha256", required=True)
    approve_parser.add_argument("--allow-full", action="store_true")
    subparsers.add_parser("run")
    subparsers.add_parser("baseline-capture")
    proposal_parser = subparsers.add_parser("proposal-validate")
    proposal_parser.add_argument("proposal", type=Path)
    apply_parser = subparsers.add_parser("proposal-apply")
    apply_parser.add_argument("proposal", type=Path)
    apply_parser.add_argument("--expect-absent", action="store_true")
    apply_parser.add_argument("--expected-current-sha256")
    apply_parser.add_argument("--expected-current-file-sha256")
    args = parser.parse_args(argv)
    root = args.target.resolve()
    try:
        if args.command == "status":
            result = status(root)
        elif args.command == "approve":
            result = approve(
                root,
                expected_config_sha256=args.config_sha256,
                allow_full=args.allow_full,
            )
        elif args.command == "run":
            result = run(root)
            result["diagnosis"] = diagnose_failures(root, result)
        elif args.command == "baseline-capture":
            result = capture_failure_baseline(root)
        elif args.command == "proposal-validate":
            proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
            result = validate_proposal(proposal, root)
        else:
            result = apply_proposal(
                root,
                args.proposal,
                expect_absent=args.expect_absent,
                expected_current_sha256=args.expected_current_sha256,
                expected_current_file_sha256=args.expected_current_file_sha256,
            )
        _print_json(result)
        if args.command == "status":
            return 0 if result.get("status") == "ready" else 1
        if args.command == "run":
            return 0 if result.get("status") == "passed" else 1
        if args.command == "baseline-capture":
            return 0 if result["report"].get("status") == "passed" else 1
        return 0
    except (OSError, json.JSONDecodeError, QualityGateError) as error:
        _print_json({"status": "blocked", "reason": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
