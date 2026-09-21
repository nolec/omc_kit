#!/usr/bin/env python3
"""Immutable review candidates for fail-closed review-to-ship binding."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CANDIDATE_SCHEMA = "omc-candidate-scope/v1"
CANDIDATE_POLICY_VERSION = "omc-candidate-policy/v1"
RECEIPT_SCHEMA = "omc-review-receipt/v1"
POINTER_SCHEMA = "omc-review-current/v1"
PEER_SNAPSHOT_SCHEMA = "omc-peer-snapshot/v1"
_ALLOWED_VERDICTS = {"APPROVE", "APPROVE_WITH_NOTES"}
_RUNTIME_PREFIXES = (".omc/state/", ".omc/context/", ".omc/runs/")
_RUNTIME_FILES = {
    ".omc/.DS_Store",
    ".omc/agent-hook.log",
    ".omc/allow_log.jsonl",
    ".omc/context.md",
    ".omc/cost_log.jsonl",
    ".omc/install-receipt.json",
    ".omc/install-source.json",
    ".omc/notepad.md",
    ".omc/pipeline.log",
    ".omc/pipeline_run_result.json",
    ".omc/pipeline_session.json",
    ".omc/project-memory.json",
    ".omc/summary.md",
}


class CandidateScopeError(ValueError):
    """The candidate cannot be represented without weakening the binding."""


class PeerSnapshotError(ValueError):
    """The peer-review input is not the immutable snapshot requested."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        input=input_bytes,
    )
    if result.returncode != 0:
        raise CandidateScopeError(f"git_command_failed:{args[0]}")
    return result.stdout


def _project_root(project_root: Path) -> Path:
    rendered = _git(project_root, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    if not rendered:
        raise CandidateScopeError("repository_root_unavailable")
    return Path(rendered).resolve()


def _commit(root: Path, revision: str) -> str:
    rendered = _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").decode(
        "ascii"
    ).strip()
    if len(rendered) != 40:
        raise CandidateScopeError("commit_invalid")
    return rendered


def _is_ancestor(root: Path, *, base_commit: str, candidate_commit: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", base_commit, candidate_commit],
        check=False,
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise CandidateScopeError("base_commit_ancestry_unavailable")
    return result.returncode == 0


def _decode_paths(raw: bytes) -> list[str]:
    paths: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            path = item.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CandidateScopeError("candidate_path_invalid") from error
        if Path(path).is_absolute() or ".." in Path(path).parts or not path:
            raise CandidateScopeError("candidate_path_invalid")
        paths.append(path)
    return paths


def is_runtime_artifact(path: str) -> bool:
    return (
        path in _RUNTIME_FILES
        or path.startswith(_RUNTIME_PREFIXES)
        or (path.startswith(".omc/native-review-") and path.endswith(".json"))
    )


def _tree_entry(root: Path, *, revision: str, path: str) -> dict[str, str]:
    raw = _git(root, "ls-tree", "-z", revision, "--", path)
    if not raw:
        return {"mode": "missing", "type": "missing", "oid": "missing"}
    metadata = raw.split(b"\t", 1)[0].split()
    if len(metadata) != 3:
        raise CandidateScopeError("tree_entry_invalid")
    return {
        "mode": metadata[0].decode("ascii"),
        "type": metadata[1].decode("ascii"),
        "oid": metadata[2].decode("ascii"),
    }


def _hash_blob(root: Path, *, path: str, content: bytes) -> str:
    raw = _git(root, "hash-object", f"--path={path}", "--stdin", input_bytes=content)
    digest = raw.decode("ascii").strip()
    if len(digest) != 40:
        raise CandidateScopeError("blob_oid_invalid")
    return digest


def _working_entry(root: Path, *, path: str, base_entry: dict[str, str]) -> dict[str, str]:
    candidate = root / path
    if candidate.is_symlink():
        content = os.fsencode(os.readlink(candidate))
        return {"mode": "120000", "type": "blob", "oid": _hash_blob(root, path=path, content=content)}
    if not candidate.exists():
        return {"mode": "missing", "type": "missing", "oid": "missing"}
    if candidate.is_dir():
        try:
            child_head = _commit(candidate, "HEAD")
        except CandidateScopeError as error:
            raise CandidateScopeError("candidate_directory_unsupported") from error
        return {"mode": "160000", "type": "commit", "oid": child_head}
    if not candidate.is_file():
        raise CandidateScopeError("candidate_file_unsupported")
    filemode = _git(root, "config", "--bool", "core.filemode").decode("ascii").strip()
    if filemode == "false" and base_entry["mode"] not in {"missing", "120000", "160000"}:
        mode = base_entry["mode"]
    else:
        mode = "100755" if candidate.stat().st_mode & stat.S_IXUSR else "100644"
    return {
        "mode": mode,
        "type": "blob",
        "oid": _hash_blob(root, path=path, content=candidate.read_bytes()),
    }


def _status(base: dict[str, str], final: dict[str, str]) -> str:
    if base["type"] == "missing" and final["type"] != "missing":
        return "added"
    if base["type"] != "missing" and final["type"] == "missing":
        return "deleted"
    if base != final:
        return "modified"
    raise CandidateScopeError("candidate_path_unchanged")


def _candidate(
    root: Path,
    *,
    base_commit: str,
    paths: list[str],
    entry_for_path: Any,
) -> dict[str, object]:
    entries: list[dict[str, str]] = []
    for path in sorted(set(paths)):
        if is_runtime_artifact(path):
            continue
        base_entry = _tree_entry(root, revision=base_commit, path=path)
        final_entry = entry_for_path(path, base_entry)
        try:
            status = _status(base_entry, final_entry)
        except CandidateScopeError as error:
            if str(error) == "candidate_path_unchanged":
                continue
            raise
        entries.append({"path": path, "status": status, **final_entry})
    scope_payload = {
        "schema_version": CANDIDATE_SCHEMA,
        "policy_version": CANDIDATE_POLICY_VERSION,
        "base_commit": base_commit,
        "entries": entries,
    }
    return {
        "schema_version": CANDIDATE_SCHEMA,
        "repository_root_sha256": _sha256(str(root).encode("utf-8")),
        "base_commit": base_commit,
        "candidate_scope": entries,
        "candidate_scope_sha256": _sha256(_canonical_bytes(scope_payload)),
        "changed_paths": [entry["path"] for entry in entries],
    }


def build_worktree_candidate(project_root: Path, *, base_commit: str) -> dict[str, object]:
    root = _project_root(project_root)
    base = _commit(root, base_commit)
    head = _commit(root, "HEAD")
    if not _is_ancestor(root, base_commit=base, candidate_commit=head):
        raise CandidateScopeError("base_commit_not_ancestor")
    tracked = _decode_paths(_git(root, "diff", "--name-only", "-z", base, "--"))
    untracked = _decode_paths(
        _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--")
    )
    return _candidate(
        root,
        base_commit=base,
        paths=[*tracked, *untracked],
        entry_for_path=lambda path, base_entry: _working_entry(
            root, path=path, base_entry=base_entry
        ),
    )


def build_commit_candidate(
    project_root: Path, *, base_commit: str, candidate_commit: str
) -> dict[str, object]:
    root = _project_root(project_root)
    base = _commit(root, base_commit)
    candidate = _commit(root, candidate_commit)
    if not _is_ancestor(root, base_commit=base, candidate_commit=candidate):
        raise CandidateScopeError("base_commit_not_ancestor")
    paths = _decode_paths(_git(root, "diff", "--name-only", "-z", base, candidate, "--"))
    return _candidate(
        root,
        base_commit=base,
        paths=paths,
        entry_for_path=lambda path, _base_entry: _tree_entry(
            root, revision=candidate, path=path
        ),
    )


def _snapshot_dir(root: Path) -> Path:
    destination = root / ".omc" / "state" / "review-snapshots"
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or not destination.is_dir():
        raise CandidateScopeError("review_snapshot_directory_invalid")
    return destination


def _regular_bytes(path: Path, *, error_type: type[Exception], error_code: str) -> bytes:
    try:
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise error_type(error_code)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            return handle.read()
    except FileNotFoundError as error:
        raise error_type(error_code) from error
    except OSError as error:
        raise error_type(error_code) from error


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise CandidateScopeError("review_snapshot_already_exists") from error


def _atomic_replace(path: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _validate_candidate(candidate: Any) -> dict[str, object]:
    required = {
        "schema_version",
        "repository_root_sha256",
        "base_commit",
        "candidate_scope",
        "candidate_scope_sha256",
        "changed_paths",
    }
    if not isinstance(candidate, dict) or set(candidate) != required:
        raise CandidateScopeError("candidate_invalid")
    if candidate["schema_version"] != CANDIDATE_SCHEMA:
        raise CandidateScopeError("candidate_schema_invalid")
    if not isinstance(candidate["base_commit"], str) or len(candidate["base_commit"]) != 40:
        raise CandidateScopeError("candidate_base_invalid")
    entries = candidate["candidate_scope"]
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict)
        or set(entry) != {"path", "status", "mode", "type", "oid"}
        or entry.get("status") not in {"added", "modified", "deleted"}
        for entry in entries
    ):
        raise CandidateScopeError("candidate_entries_invalid")
    payload = {
        "schema_version": CANDIDATE_SCHEMA,
        "policy_version": CANDIDATE_POLICY_VERSION,
        "base_commit": candidate["base_commit"],
        "entries": entries,
    }
    if candidate["candidate_scope_sha256"] != _sha256(_canonical_bytes(payload)):
        raise CandidateScopeError("candidate_scope_sha256_invalid")
    if candidate["changed_paths"] != [entry["path"] for entry in entries]:
        raise CandidateScopeError("candidate_paths_invalid")
    return candidate


def record_review_receipt(
    project_root: Path,
    *,
    candidate: dict[str, object],
    verdict: str,
    review_output: bytes,
    verification_receipt_sha256: str | None = None,
) -> dict[str, object]:
    root = _project_root(project_root)
    candidate = _validate_candidate(candidate)
    if candidate["repository_root_sha256"] != _sha256(str(root).encode("utf-8")):
        raise CandidateScopeError("candidate_repository_mismatch")
    if verdict not in _ALLOWED_VERDICTS or not review_output:
        raise CandidateScopeError("review_receipt_input_invalid")
    if verification_receipt_sha256 is not None and (
        len(verification_receipt_sha256) != 64
        or any(char not in "0123456789abcdef" for char in verification_receipt_sha256)
    ):
        raise CandidateScopeError("verification_receipt_sha256_invalid")
    receipt_id = uuid.uuid4().hex
    body = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "repository_root_sha256": candidate["repository_root_sha256"],
        "base_commit": candidate["base_commit"],
        "candidate_scope": candidate["candidate_scope"],
        "candidate_scope_sha256": candidate["candidate_scope_sha256"],
        "verification_receipt_sha256": verification_receipt_sha256,
        "review_verdict": verdict,
        "review_output_sha256": _sha256(review_output),
        "reviewed_at": _now(),
    }
    receipt = {**body, "receipt_sha256": _sha256(_canonical_bytes(body))}
    directory = _snapshot_dir(root)
    receipt_path = directory / f"{receipt_id}.json"
    _write_exclusive(receipt_path, _canonical_bytes(receipt))
    pointer_path = directory / "current.json"
    pointer = {
        "schema_version": POINTER_SCHEMA,
        "repository_root_sha256": candidate["repository_root_sha256"],
        "receipt_name": receipt_path.name,
        "receipt_sha256": receipt["receipt_sha256"],
    }
    _atomic_replace(pointer_path, _canonical_bytes(pointer))
    return {
        "receipt_path": str(receipt_path),
        "pointer_path": str(pointer_path),
        "receipt_sha256": receipt["receipt_sha256"],
        "candidate_scope_sha256": candidate["candidate_scope_sha256"],
    }


def capture_review_snapshot(project_root: Path, *, base_commit: str) -> dict[str, object]:
    """Freeze the candidate before review output exists.

    The empty diff is deliberate: this artifact establishes the reviewed
    candidate identity, while the caller preserves the raw review body
    separately. Peer review uses the same envelope with its immutable diff.
    """
    root = _project_root(project_root)
    candidate = build_worktree_candidate(root, base_commit=base_commit)
    frozen = create_peer_snapshot(root, candidate=candidate, review_diff=b"")
    return {
        "snapshot_path": frozen["path"],
        "snapshot_sha256": frozen["sha256"],
        "candidate_scope_sha256": candidate["candidate_scope_sha256"],
    }


def record_review_receipt_from_snapshot(
    project_root: Path,
    *,
    snapshot_path: Path,
    snapshot_sha256: str,
    verdict: str,
    review_output: bytes,
    verification_receipt_sha256: str | None = None,
) -> dict[str, object]:
    """Persist only the exact candidate that existed when review began."""
    root = _project_root(project_root)
    loaded = load_peer_snapshot(snapshot_path, expected_sha256=snapshot_sha256)
    candidate = loaded["candidate"]
    if candidate["repository_root_sha256"] != _sha256(str(root).encode("utf-8")):
        raise CandidateScopeError("review_snapshot_repository_mismatch")
    current = build_worktree_candidate(root, base_commit=str(candidate["base_commit"]))
    if current["candidate_scope_sha256"] != candidate["candidate_scope_sha256"]:
        raise CandidateScopeError("review_stale")
    return record_review_receipt(
        root,
        candidate=candidate,
        verdict=verdict,
        review_output=review_output,
        verification_receipt_sha256=verification_receipt_sha256,
    )


def _current_receipt(root: Path) -> dict[str, object]:
    directory = _snapshot_dir(root)
    pointer_path = directory / "current.json"
    try:
        pointer = json.loads(
            _regular_bytes(
                pointer_path,
                error_type=CandidateScopeError,
                error_code="review_pointer_invalid",
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidateScopeError("review_pointer_invalid") from error
    expected_pointer = {
        "schema_version",
        "repository_root_sha256",
        "receipt_name",
        "receipt_sha256",
    }
    if not isinstance(pointer, dict) or set(pointer) != expected_pointer:
        raise CandidateScopeError("review_pointer_invalid")
    if (
        pointer["schema_version"] != POINTER_SCHEMA
        or pointer["repository_root_sha256"] != _sha256(str(root).encode("utf-8"))
        or not isinstance(pointer["receipt_name"], str)
        or Path(pointer["receipt_name"]).name != pointer["receipt_name"]
    ):
        raise CandidateScopeError("review_pointer_invalid")
    try:
        receipt = json.loads(
            _regular_bytes(
                directory / pointer["receipt_name"],
                error_type=CandidateScopeError,
                error_code="review_receipt_invalid",
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidateScopeError("review_receipt_invalid") from error
    expected_receipt = {
        "schema_version",
        "receipt_id",
        "repository_root_sha256",
        "base_commit",
        "candidate_scope",
        "candidate_scope_sha256",
        "verification_receipt_sha256",
        "review_verdict",
        "review_output_sha256",
        "reviewed_at",
        "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_receipt:
        raise CandidateScopeError("review_receipt_invalid")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if (
        receipt["schema_version"] != RECEIPT_SCHEMA
        or receipt["review_verdict"] not in _ALLOWED_VERDICTS
        or receipt["repository_root_sha256"] != _sha256(str(root).encode("utf-8"))
        or receipt["receipt_sha256"] != _sha256(_canonical_bytes(body))
        or receipt["receipt_sha256"] != pointer["receipt_sha256"]
    ):
        raise CandidateScopeError("review_receipt_invalid")
    _validate_candidate(
        {
            "schema_version": CANDIDATE_SCHEMA,
            "repository_root_sha256": receipt["repository_root_sha256"],
            "base_commit": receipt["base_commit"],
            "candidate_scope": receipt["candidate_scope"],
            "candidate_scope_sha256": receipt["candidate_scope_sha256"],
            "changed_paths": [entry["path"] for entry in receipt["candidate_scope"]],
        }
    )
    return receipt


def _changed_paths(expected: list[dict[str, str]], actual: list[dict[str, str]]) -> list[str]:
    expected_by_path = {entry["path"]: entry for entry in expected}
    actual_by_path = {entry["path"]: entry for entry in actual}
    return sorted(
        path
        for path in set(expected_by_path) | set(actual_by_path)
        if expected_by_path.get(path) != actual_by_path.get(path)
    )


def validate_ship_candidate(project_root: Path) -> dict[str, object]:
    root = _project_root(project_root)
    try:
        receipt = _current_receipt(root)
    except CandidateScopeError:
        return {"status": "BLOCKED", "reason_code": "review_receipt_invalid", "changed_paths": []}
    base = str(receipt["base_commit"])
    try:
        current = build_worktree_candidate(root, base_commit=base)
    except CandidateScopeError as error:
        return {
            "status": "BLOCKED",
            "reason_code": "review_stale",
            "detail_code": str(error),
            "changed_paths": [],
        }
    expected_scope = receipt["candidate_scope"]
    if current["candidate_scope_sha256"] != receipt["candidate_scope_sha256"]:
        return {
            "status": "BLOCKED",
            "reason_code": "review_stale",
            "changed_paths": _changed_paths(expected_scope, current["candidate_scope"]),
        }
    return {
        "status": "READY",
        "reason_code": None,
        "changed_paths": [],
        "receipt_sha256": receipt["receipt_sha256"],
    }


def create_peer_snapshot(
    project_root: Path, *, candidate: dict[str, object], review_diff: bytes
) -> dict[str, object]:
    root = _project_root(project_root)
    candidate = _validate_candidate(candidate)
    payload = {
        "schema_version": PEER_SNAPSHOT_SCHEMA,
        "candidate": candidate,
        "candidate_scope_sha256": candidate["candidate_scope_sha256"],
        "review_diff_base64": base64.b64encode(review_diff).decode("ascii"),
        "review_diff_sha256": _sha256(review_diff),
    }
    encoded = _canonical_bytes(payload)
    directory = _snapshot_dir(root) / "peer-inputs"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise PeerSnapshotError("peer_snapshot_directory_invalid")
    path = directory / f"{uuid.uuid4().hex}.json"
    try:
        _write_exclusive(path, encoded)
    except CandidateScopeError as error:
        raise PeerSnapshotError(str(error)) from error
    return {"path": str(path), "sha256": _sha256(encoded)}


def load_peer_snapshot(path: Path, *, expected_sha256: str) -> dict[str, object]:
    try:
        raw = _regular_bytes(
            path,
            error_type=PeerSnapshotError,
            error_code="peer_snapshot_invalid",
        )
    except PeerSnapshotError:
        raise
    if _sha256(raw) != expected_sha256:
        raise PeerSnapshotError("peer_snapshot_sha256_mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PeerSnapshotError("peer_snapshot_invalid") from error
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema_version",
            "candidate",
            "candidate_scope_sha256",
            "review_diff_base64",
            "review_diff_sha256",
        }
        or payload["schema_version"] != PEER_SNAPSHOT_SCHEMA
    ):
        raise PeerSnapshotError("peer_snapshot_invalid")
    try:
        candidate = _validate_candidate(payload["candidate"])
    except CandidateScopeError as error:
        raise PeerSnapshotError("peer_snapshot_invalid") from error
    if payload["candidate_scope_sha256"] != candidate["candidate_scope_sha256"]:
        raise PeerSnapshotError("peer_snapshot_invalid")
    try:
        review_diff = base64.b64decode(payload["review_diff_base64"], validate=True)
    except (TypeError, ValueError) as error:
        raise PeerSnapshotError("peer_snapshot_invalid") from error
    if _sha256(review_diff) != payload["review_diff_sha256"]:
        raise PeerSnapshotError("peer_snapshot_invalid")
    return {"candidate": candidate, "review_diff": review_diff}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record-review")
    record.add_argument("--target", type=Path, default=Path.cwd())
    record.add_argument("--review-snapshot", type=Path, required=True)
    record.add_argument("--review-snapshot-sha256", required=True)
    record.add_argument("--verdict", required=True, choices=sorted(_ALLOWED_VERDICTS))
    record.add_argument("--review-output", type=Path, required=True)
    record.add_argument("--verification-receipt-sha256")
    capture = sub.add_parser("capture-review")
    capture.add_argument("--target", type=Path, default=Path.cwd())
    capture.add_argument("--base-commit", required=True)
    validate = sub.add_parser("validate-ship")
    validate.add_argument("--target", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        if args.command == "record-review":
            result = record_review_receipt_from_snapshot(
                args.target,
                snapshot_path=args.review_snapshot,
                snapshot_sha256=args.review_snapshot_sha256,
                verdict=args.verdict,
                review_output=args.review_output.read_bytes(),
                verification_receipt_sha256=args.verification_receipt_sha256,
            )
        elif args.command == "capture-review":
            result = capture_review_snapshot(args.target, base_commit=args.base_commit)
        else:
            result = validate_ship_candidate(args.target)
    except (CandidateScopeError, OSError) as error:
        print(json.dumps({"status": "BLOCKED", "reason_code": str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if args.command in {"capture-review", "record-review"} or result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
