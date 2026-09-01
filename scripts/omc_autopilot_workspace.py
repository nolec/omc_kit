#!/usr/bin/env python3
"""Fail-closed workspace boundaries for unattended Autopilot execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


WORK_CONTRACT_SCHEMA_VERSION = "omc-autopilot-work-contract/v1"
REVIEW_PACKET_SCHEMA_VERSION = "omc-autopilot-review-packet/v1"

_SHA256_LENGTH = 64
_CONTRACT_FIELDS = {
    "schema_version",
    "run_id",
    "instruction_sha256",
    "base_commit",
    "source_identity",
    "allowed_paths",
    "allowed_operations",
    "change_class",
    "test_policy",
    "verification_commands",
    "pipeline_mode",
    "executor",
    "required_capabilities",
    "candidate_branch",
    "promotion_policy",
}
_ALLOWED_OPERATIONS = {"create", "modify", "delete"}
_ALLOWED_CHANGE_CLASSES = {"document_only", "implementation", "synthetic", "benchmark_maintenance"}
_ALLOWED_TEST_POLICIES = {"optional", "required"}
_RUNTIME_PATHS = {
    ".omc/cost_log.jsonl",
    ".omc/context.md",
    ".omc/notepad.md",
    ".omc/pipeline_run_result.json",
    ".omc/pipeline_session.json",
    ".omc/summary.md",
}
_RUNTIME_PREFIXES = (".omc/runs/", ".omc/state/", ".omc/wip/")


class AutopilotWorkspaceError(RuntimeError):
    """Raised when an Autopilot workspace boundary cannot be trusted."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise AutopilotWorkspaceError(f"git_command_failed:{args[0]}:{result.stderr.strip()}")
    return result


def _safe_relative_path(value: object, field: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    directory_scope = text.endswith("/")
    path = PurePosixPath(text)
    normalized = path.as_posix()
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or normalized == ".git"
        or normalized.startswith(".git/")
    ):
        raise AutopilotWorkspaceError(f"{field}_unsafe")
    return f"{normalized}/" if directory_scope else normalized


def _require_sha256(value: object, field: str) -> str:
    text = str(value or "")
    if len(text) != _SHA256_LENGTH or any(char not in "0123456789abcdef" for char in text):
        raise AutopilotWorkspaceError(f"{field}_invalid")
    return text


def _require_git_oid(value: object, field: str) -> str:
    text = str(value or "")
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise AutopilotWorkspaceError(f"{field}_invalid")
    return text


def source_identity(root: Path) -> dict[str, str]:
    root = root.resolve()
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    tree = _git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout
    return {
        "schema_version": "omc-autopilot-source-identity/v1",
        "head": head,
        "tree": tree,
        "status_sha256": _sha256(status.encode("utf-8")),
        "clean": str(not bool(status.strip())).lower(),
    }


def validate_work_contract(payload: object, *, require_digest: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AutopilotWorkspaceError("work_contract_not_object")
    expected = set(_CONTRACT_FIELDS)
    if require_digest:
        expected.add("contract_sha256")
    if set(payload) != expected:
        raise AutopilotWorkspaceError("work_contract_fields_invalid")
    if payload.get("schema_version") != WORK_CONTRACT_SCHEMA_VERSION:
        raise AutopilotWorkspaceError("work_contract_schema_invalid")
    run_id = str(payload.get("run_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise AutopilotWorkspaceError("work_contract_run_id_invalid")
    _require_sha256(payload.get("instruction_sha256"), "instruction_sha256")
    base_commit = _require_git_oid(payload.get("base_commit"), "base_commit")
    identity = payload.get("source_identity")
    if (
        not isinstance(identity, dict)
        or set(identity) != {"schema_version", "head", "tree", "status_sha256", "clean"}
        or identity.get("schema_version") != "omc-autopilot-source-identity/v1"
        or identity.get("head") != base_commit
        or identity.get("clean") != "true"
    ):
        raise AutopilotWorkspaceError("source_identity_invalid")
    _require_git_oid(identity.get("tree"), "source_identity_tree")
    _require_sha256(identity.get("status_sha256"), "source_identity_status_sha256")
    paths = payload.get("allowed_paths")
    if not isinstance(paths, list) or not paths:
        raise AutopilotWorkspaceError("allowed_paths_invalid")
    normalized_paths = [_safe_relative_path(item, "allowed_path") for item in paths]
    if len(normalized_paths) != len(set(normalized_paths)):
        raise AutopilotWorkspaceError("allowed_paths_duplicate")
    operations = payload.get("allowed_operations")
    if not isinstance(operations, list) or not operations or not set(operations) <= _ALLOWED_OPERATIONS:
        raise AutopilotWorkspaceError("allowed_operations_invalid")
    if payload.get("change_class") not in _ALLOWED_CHANGE_CLASSES:
        raise AutopilotWorkspaceError("change_class_invalid")
    if payload.get("test_policy") not in _ALLOWED_TEST_POLICIES:
        raise AutopilotWorkspaceError("test_policy_invalid")
    commands = payload.get("verification_commands")
    if not isinstance(commands, list) or any(
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
        for command in commands
    ):
        raise AutopilotWorkspaceError("verification_commands_invalid")
    if payload.get("test_policy") == "required" and not commands:
        raise AutopilotWorkspaceError("verification_commands_required")
    if payload.get("pipeline_mode") not in {"lite", "full"}:
        raise AutopilotWorkspaceError("pipeline_mode_invalid")
    if payload.get("executor") != "codex":
        raise AutopilotWorkspaceError("executor_confinement_unsupported")
    capabilities = payload.get("required_capabilities")
    if capabilities != ["workspace_write_confined"]:
        raise AutopilotWorkspaceError("required_capabilities_invalid")
    branch = str(payload.get("candidate_branch") or "")
    if not branch.startswith("codex/") or ".." in branch or branch.endswith("/"):
        raise AutopilotWorkspaceError("candidate_branch_invalid")
    if payload.get("promotion_policy") != "branch_ref_only":
        raise AutopilotWorkspaceError("promotion_policy_invalid")
    normalized = dict(payload)
    normalized["allowed_paths"] = normalized_paths
    if require_digest:
        expected_digest = _sha256(_canonical_bytes({key: normalized[key] for key in _CONTRACT_FIELDS}))
        if normalized.get("contract_sha256") != expected_digest:
            raise AutopilotWorkspaceError("work_contract_digest_mismatch")
    return normalized


def freeze_work_contract(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_work_contract(payload)
    frozen = dict(normalized)
    frozen["contract_sha256"] = _sha256(_canonical_bytes(normalized))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as exc:
        raise AutopilotWorkspaceError("work_contract_already_frozen") from exc
    except OSError as exc:
        raise AutopilotWorkspaceError("work_contract_unwritable") from exc
    try:
        data = json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise AutopilotWorkspaceError("work_contract_write_incomplete")
            offset += written
        os.fsync(fd)
    except OSError as exc:
        raise AutopilotWorkspaceError("work_contract_write_failed") from exc
    finally:
        os.close(fd)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise AutopilotWorkspaceError("work_contract_sync_failed") from exc
    return frozen


def load_work_contract(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise AutopilotWorkspaceError("work_contract_symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutopilotWorkspaceError("work_contract_unreadable") from exc
    return validate_work_contract(payload, require_digest=True)


def materialize_isolated_clone(source: Path, destination: Path, contract: dict[str, Any]) -> Path:
    contract = validate_work_contract(contract, require_digest="contract_sha256" in contract)
    if source_identity(source) != contract["source_identity"]:
        raise AutopilotWorkspaceError("source_identity_drift")
    if destination.exists():
        raise AutopilotWorkspaceError("isolated_destination_exists")
    result = subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(source.resolve()), str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AutopilotWorkspaceError(f"isolated_clone_failed:{result.stderr.strip()}")
    _git(destination, "checkout", "--quiet", "--detach", str(contract["base_commit"]))
    _git(destination, "remote", "remove", "origin")
    if _git(destination, "rev-parse", "HEAD").stdout.strip() != contract["base_commit"]:
        raise AutopilotWorkspaceError("isolated_base_commit_mismatch")
    return destination


def materialize_review_clone(
    source: Path, destination: Path, trusted_commit: str
) -> Path:
    """Create a clean reviewer checkout at a trusted, pre-candidate commit."""
    trusted_commit = _require_git_oid(trusted_commit, "trusted_commit")
    if destination.exists() or destination.is_symlink():
        raise AutopilotWorkspaceError("review_destination_exists")
    result = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--no-checkout",
            str(source.resolve()),
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AutopilotWorkspaceError(
            f"review_clone_failed:{result.stderr.strip()}"
        )
    _git(destination, "checkout", "--quiet", "--detach", trusted_commit)
    _git(destination, "remote", "remove", "origin")
    if _git(destination, "rev-parse", "HEAD").stdout.strip() != trusted_commit:
        raise AutopilotWorkspaceError("review_trusted_commit_mismatch")
    return destination


def _runtime_path(path: str) -> bool:
    return path in _RUNTIME_PATHS or any(path.startswith(prefix) for prefix in _RUNTIME_PREFIXES)


def _git_file_mode(filesystem_mode: int) -> int:
    return 0o755 if filesystem_mode & 0o111 else 0o644


def snapshot_workspace(root: Path) -> dict[str, dict[str, Any]]:
    root = root.resolve()
    snapshot: dict[str, dict[str, Any]] = {}
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root).as_posix()
        directories[:] = sorted(name for name in directories if not (relative_dir == "." and name == ".git"))
        names = sorted(set(files) | {name for name in directories if (current_path / name).is_symlink()})
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                continue
            metadata = path.lstat()
            if _runtime_path(relative) and stat.S_ISREG(metadata.st_mode):
                continue
            filesystem_mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                kind = "symlink"
                mode = filesystem_mode
                digest = _sha256(os.readlink(path).encode("utf-8"))
            elif stat.S_ISREG(metadata.st_mode):
                kind = "file"
                mode = _git_file_mode(filesystem_mode)
                digest = _sha256_file(path)
            else:
                kind = "special"
                mode = filesystem_mode
                digest = _sha256(f"{metadata.st_mode}:{metadata.st_rdev}".encode("ascii"))
            snapshot[relative] = {
                "kind": kind,
                "mode": mode,
                "filesystem_mode": filesystem_mode,
                "sha256": digest,
            }
        directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
    return snapshot


def snapshot_git_control_plane(root: Path) -> dict[str, dict[str, Any]]:
    """Snapshot executable Git metadata without invoking Git or hashing objects."""
    git_dir = root.resolve() / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise AutopilotWorkspaceError("git_control_plane_untrusted")
    snapshot: dict[str, dict[str, Any]] = {}
    excluded_roots = {"index", "logs", "objects"}
    for current, directories, files in os.walk(git_dir, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(git_dir).as_posix()
        if relative_dir == ".":
            directories[:] = sorted(
                name for name in directories if name not in excluded_roots
            )
            files = [name for name in files if name not in excluded_roots]
        names = sorted(
            set(files)
            | {name for name in directories if (current_path / name).is_symlink()}
        )
        for name in names:
            path = current_path / name
            relative = path.relative_to(git_dir).as_posix()
            metadata = path.lstat()
            filesystem_mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                kind = "symlink"
                digest = _sha256(os.readlink(path).encode("utf-8"))
            elif stat.S_ISREG(metadata.st_mode):
                kind = "file"
                digest = _sha256_file(path)
            else:
                kind = "special"
                digest = _sha256(
                    f"{metadata.st_mode}:{metadata.st_rdev}".encode("ascii")
                )
            snapshot[relative] = {
                "kind": kind,
                "filesystem_mode": filesystem_mode,
                "sha256": digest,
            }
        directories[:] = [
            name for name in directories if not (current_path / name).is_symlink()
        ]

    objects_info = git_dir / "objects" / "info"
    for name in ("alternates", "http-alternates"):
        path = objects_info / name
        if not path.exists() and not path.is_symlink():
            continue
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise AutopilotWorkspaceError("git_control_plane_untrusted")
        snapshot[f"objects/info/{name}"] = {
            "kind": "file",
            "filesystem_mode": stat.S_IMODE(metadata.st_mode),
            "sha256": _sha256_file(path),
        }
    return snapshot


def validate_git_control_plane(
    root: Path, expected: dict[str, dict[str, Any]]
) -> None:
    if snapshot_git_control_plane(root) != expected:
        raise AutopilotWorkspaceError("git_control_plane_changed")


def compute_workspace_delta(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    delta: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        previous = before.get(path)
        current = after.get(path)
        if previous == current:
            continue
        operation = "create" if previous is None else "delete" if current is None else "modify"
        value = current or previous or {}
        delta.append(
            {
                "path": path,
                "operation": operation,
                "kind": value.get("kind"),
                "before": previous,
                "after": current,
            }
        )
    return delta


def _path_allowed(path: str, allowed_paths: list[str]) -> bool:
    return any(path == allowed or (allowed.endswith("/") and path.startswith(allowed)) for allowed in allowed_paths)


def validate_scope(delta: list[dict[str, Any]], contract: dict[str, Any]) -> list[str]:
    contract = validate_work_contract(contract, require_digest="contract_sha256" in contract)
    failures: list[str] = []
    for item in delta:
        path = str(item.get("path") or "")
        operation = str(item.get("operation") or "")
        kind = str(item.get("kind") or "")
        if not _path_allowed(path, contract["allowed_paths"]):
            failures.append(f"path_not_allowed:{path}")
        if operation not in contract["allowed_operations"]:
            failures.append(f"operation_not_allowed:{path}:{operation}")
        if kind != "file":
            failures.append(f"unsafe_delta_type:{path}:{kind}")
        before = item.get("before")
        after = item.get("after")
        if operation == "modify" and isinstance(before, dict) and isinstance(after, dict):
            before_mode = int(before.get("mode") or 0)
            after_mode = int(after.get("mode") or 0)
            before_filesystem_mode = int(before.get("filesystem_mode") or 0)
            after_filesystem_mode = int(after.get("filesystem_mode") or 0)
            if before_mode == after_mode and before_filesystem_mode != after_filesystem_mode:
                failures.append(
                    "unsupported_file_mode_change:"
                    f"{path}:{before_filesystem_mode:04o}->{after_filesystem_mode:04o}"
                )
    return sorted(set(failures))


def validate_precommit_git_state(isolated: Path, contract: dict[str, Any]) -> None:
    """Require providers and verifiers to leave Git history and the index untouched."""
    contract = validate_work_contract(contract, require_digest="contract_sha256" in contract)
    if _git(isolated, "rev-parse", "HEAD").stdout.strip() != contract["base_commit"]:
        raise AutopilotWorkspaceError("precommit_git_state_changed")
    staged = _git(isolated, "diff", "--cached", "--quiet", check=False)
    if staged.returncode != 0:
        raise AutopilotWorkspaceError("precommit_git_state_changed")


def validate_candidate_scope(
    *,
    isolated: Path,
    contract: dict[str, Any],
    candidate_commit: str,
    expected_delta: list[dict[str, Any]],
) -> None:
    """Bind the candidate commit topology and changed paths to the validated delta."""
    contract = validate_work_contract(contract, require_digest="contract_sha256" in contract)
    candidate_commit = _require_git_oid(candidate_commit, "candidate_commit")
    parents = _git(isolated, "rev-list", "--parents", "-n", "1", candidate_commit).stdout.split()
    if parents != [candidate_commit, contract["base_commit"]]:
        raise AutopilotWorkspaceError("candidate_parent_mismatch")

    changed = _git(
        isolated,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        str(contract["base_commit"]),
        candidate_commit,
    ).stdout
    fields = changed.rstrip("\0").split("\0") if changed else []
    if len(fields) % 2:
        raise AutopilotWorkspaceError("candidate_scope_mismatch")
    operation_by_status = {"A": "create", "M": "modify", "D": "delete"}
    actual: list[tuple[str, str]] = []
    for index in range(0, len(fields), 2):
        status, path = fields[index], fields[index + 1]
        operation = operation_by_status.get(status)
        if operation is None:
            raise AutopilotWorkspaceError("candidate_scope_mismatch")
        actual.append((operation, path))
    expected = sorted(
        (str(item.get("operation") or ""), str(item.get("path") or ""))
        for item in expected_delta
    )
    if sorted(actual) != expected:
        raise AutopilotWorkspaceError("candidate_scope_mismatch")

    for item in expected_delta:
        path = str(item.get("path") or "")
        operation = str(item.get("operation") or "")
        tree_entry = subprocess.run(
            ["git", "ls-tree", "-z", candidate_commit, "--", path],
            cwd=isolated,
            capture_output=True,
            check=False,
        )
        if tree_entry.returncode != 0:
            raise AutopilotWorkspaceError("candidate_content_mismatch")
        records = [record for record in tree_entry.stdout.rstrip(b"\0").split(b"\0") if record]
        if operation == "delete":
            if records:
                raise AutopilotWorkspaceError("candidate_content_mismatch")
            continue
        if len(records) != 1 or b"\t" not in records[0]:
            raise AutopilotWorkspaceError("candidate_content_mismatch")
        header, recorded_path = records[0].split(b"\t", 1)
        try:
            mode, object_type, object_id = header.decode("ascii").split()
            decoded_path = recorded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise AutopilotWorkspaceError("candidate_content_mismatch") from exc
        if object_type != "blob" or decoded_path != path:
            raise AutopilotWorkspaceError("candidate_content_mismatch")
        after = item.get("after")
        if not isinstance(after, dict) or after.get("kind") != "file":
            raise AutopilotWorkspaceError("candidate_content_mismatch")
        snapshot_mode = int(after.get("mode") or 0)
        expected_modes = {0o644: "100644", 0o755: "100755"}
        expected_mode = expected_modes.get(snapshot_mode)
        if expected_mode is None:
            raise AutopilotWorkspaceError("candidate_content_mismatch")
        blob = subprocess.run(
            ["git", "cat-file", "blob", object_id],
            cwd=isolated,
            capture_output=True,
            check=False,
        )
        if (
            blob.returncode != 0
            or mode != expected_mode
            or _sha256(blob.stdout) != after.get("sha256")
        ):
            raise AutopilotWorkspaceError("candidate_content_mismatch")


def build_review_packet(
    *,
    contract: dict[str, Any],
    candidate_commit: str,
    delta: list[dict[str, Any]],
    patch: str,
    tests: list[dict[str, Any]],
    executor_receipt: dict[str, Any],
) -> dict[str, Any]:
    contract = validate_work_contract(contract, require_digest="contract_sha256" in contract)
    packet: dict[str, Any] = {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "contract_sha256": contract.get("contract_sha256") or _sha256(_canonical_bytes(contract)),
        "base_commit": contract["base_commit"],
        "candidate_commit": candidate_commit,
        "delta": delta,
        "delta_sha256": _sha256(_canonical_bytes(delta)),
        "patch": patch,
        "patch_sha256": _sha256(patch.encode("utf-8")),
        "tests": tests,
        "tests_sha256": _sha256(_canonical_bytes(tests)),
        "executor_receipt": executor_receipt,
        "executor_receipt_sha256": _sha256(_canonical_bytes(executor_receipt)),
    }
    packet["packet_sha256"] = _sha256(_canonical_bytes(packet))
    return packet


def validate_review_packet(packet: object) -> list[str]:
    if not isinstance(packet, dict):
        return ["review_packet_not_object"]
    if packet.get("schema_version") != REVIEW_PACKET_SCHEMA_VERSION:
        return ["review_packet_schema_invalid"]
    failures: list[str] = []
    expected = _sha256(_canonical_bytes({key: value for key, value in packet.items() if key != "packet_sha256"}))
    if packet.get("packet_sha256") != expected:
        failures.append("review_packet_digest_mismatch")
    for field, value in (
        ("delta", packet.get("delta")),
        ("tests", packet.get("tests")),
        ("executor_receipt", packet.get("executor_receipt")),
    ):
        digest = packet.get(f"{field}_sha256")
        if digest != _sha256(_canonical_bytes(value)):
            failures.append(f"review_packet_{field}_digest_mismatch")
    patch = packet.get("patch")
    if not isinstance(patch, str) or packet.get("patch_sha256") != _sha256(str(patch or "").encode("utf-8")):
        failures.append("review_packet_patch_digest_mismatch")
    return failures


def promote_candidate_branch(
    *, source: Path, isolated: Path, contract: dict[str, Any], candidate_commit: str
) -> str:
    contract = validate_work_contract(contract, require_digest="contract_sha256" in contract)
    if source_identity(source) != contract["source_identity"]:
        raise AutopilotWorkspaceError("promotion_precondition_changed")
    ancestry = _git(isolated, "merge-base", "--is-ancestor", str(contract["base_commit"]), candidate_commit, check=False)
    if ancestry.returncode != 0:
        raise AutopilotWorkspaceError("candidate_not_descendant")
    branch = str(contract["candidate_branch"])
    if _git(source, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        raise AutopilotWorkspaceError("candidate_branch_conflict")
    _git(isolated, "update-ref", "refs/omc/candidate", candidate_commit)
    with tempfile.TemporaryDirectory(prefix="omc-autopilot-bundle-") as temporary:
        bundle = Path(temporary) / "candidate.bundle"
        _git(isolated, "bundle", "create", str(bundle), "refs/omc/candidate")
        _git(source, "bundle", "verify", str(bundle))
        fetched = _git(
            source,
            "fetch",
            str(bundle),
            f"refs/omc/candidate:refs/heads/{branch}",
            check=False,
        )
        if fetched.returncode != 0:
            raise AutopilotWorkspaceError(f"candidate_promotion_failed:{fetched.stderr.strip()}")
    if _git(source, "rev-parse", branch).stdout.strip() != candidate_commit:
        raise AutopilotWorkspaceError("candidate_branch_commit_mismatch")
    return branch
