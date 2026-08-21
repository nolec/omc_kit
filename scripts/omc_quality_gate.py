#!/usr/bin/env python3
"""Project-owned, tool-neutral quality gate contract and runner."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
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
_PURPOSES = {"test", "typecheck", "lint", "build"}
_SCOPES = {"changed", "affected", "full"}
_PLACEHOLDERS = {"{changed_files}", "{base_ref}", "{head_ref}"}
_SHELL_TOKENS = {"|", "||", "&&", ";", ">", ">>", "<", "<<"}


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
    for gate in gates:
        if not isinstance(gate, dict):
            raise QualityGateError("gate must be an object")
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not gate_id or gate_id in gate_ids:
            raise QualityGateError("gate id must be non-empty and unique")
        gate_ids.add(gate_id)
        if gate.get("purpose") not in _PURPOSES:
            raise QualityGateError(f"unsupported gate purpose: {gate.get('purpose')}")
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


def run(root: Path) -> dict[str, Any]:
    config, config_sha256 = load_config_snapshot(root)
    current = _status_for_snapshot(root, config, config_sha256)
    if current["status"] != "ready":
        raise QualityGateError(f"quality gate is not ready: {current['status']}")
    files = _git_changed_files(root, config["base_ref"])
    results: list[dict[str, Any]] = []
    blocked = False
    for gate in config["gates"]:
        argv = _expand_argv(gate["argv"], base_ref=config["base_ref"], changed_files=files)
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
        return 0
    except (OSError, json.JSONDecodeError, QualityGateError) as error:
        _print_json({"status": "blocked", "reason": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
