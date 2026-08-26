#!/usr/bin/env python3
"""Create a new Product Value corpus without mutating the approved v1 input."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping


SCHEMA_VERSION = "omc-product-value-selection/v2"
LOCK_FILENAME = "benchmark-dependencies.lock"
LOCK_ALIASES = {"source-e", "source-f"}
EXPECTED_ALIASES = {f"source-{letter}" for letter in "abcdef"}
EXPECTED_WORKLOAD_IDS = {f"pv-{index:02d}" for index in range(1, 7)}
EXACT_LOCK_ENTRY = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)=="
    r"(?P<version>[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?)$"
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_parent_receipt(value: dict[str, Any]) -> None:
    if (
        set(value) != {
            "schema_version",
            "workload_count",
            "public_payload_sha256",
        }
        or value.get("schema_version") != "omc-product-value-selection/v1"
        or value.get("workload_count") != 6
        or not _is_sha256(value.get("public_payload_sha256"))
    ):
        raise ValueError("corpus_v2_parent_receipt_invalid")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("corpus_v2_input_invalid") from error


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("corpus_v2_git_failed") from error
    if result.returncode != 0:
        raise ValueError("corpus_v2_git_failed")
    return result.stdout.strip()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _inputs(
    root: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, str]],
    dict[str, Any],
]:
    workloads = _load_json(root / "workloads.json")
    private_selection = _load_json(root / "private-selection.json")
    source_roots = _load_json(root / "source-roots.json")
    parent_receipt = _load_json(root / "selection-receipt.json")
    if (
        not isinstance(workloads, list)
        or len(workloads) != 6
        or not isinstance(private_selection, list)
        or len(private_selection) != 6
        or not isinstance(source_roots, dict)
        or len(source_roots) != 6
        or not isinstance(parent_receipt, dict)
    ):
        raise ValueError("corpus_v2_input_invalid")
    workload_ids: set[str] = set()
    aliases: set[str] = set()
    packets: dict[str, dict[str, Any]] = {}
    for workload in workloads:
        if not isinstance(workload, dict):
            raise ValueError("corpus_v2_input_invalid")
        workload_id = workload.get("workload_id")
        alias = workload.get("repo_alias")
        if (
            not isinstance(workload_id, str)
            or not workload_id.strip()
            or workload_id in workload_ids
            or not isinstance(alias, str)
            or not alias.strip()
            or alias in aliases
        ):
            raise ValueError("corpus_v2_input_invalid")
        workload_ids.add(workload_id)
        aliases.add(alias)
        packet = _load_json(root / "packets" / f"{workload_id}.json")
        if (
            not isinstance(packet, dict)
            or packet.get("workload_id") != workload_id
            or packet.get("repo_alias") != alias
            or packet.get("source_commit") != workload.get("source_commit")
            or canonical_sha256(packet) != workload.get("execution_packet_sha256")
            or canonical_sha256(packet.get("request"))
            != workload.get("request_sha256")
            or canonical_sha256(packet.get("dod")) != workload.get("dod_sha256")
            or canonical_sha256(packet.get("verification"))
            != workload.get("verification_sha256")
        ):
            raise ValueError("corpus_v2_packet_binding_invalid")
        packets[workload_id] = packet
    if aliases != EXPECTED_ALIASES or workload_ids != EXPECTED_WORKLOAD_IDS:
        raise ValueError("corpus_v2_input_invalid")
    selection_by_id = {
        item.get("workload_id"): item
        for item in private_selection
        if isinstance(item, dict) and isinstance(item.get("workload_id"), str)
    }
    if (
        len(selection_by_id) != 6
        or set(selection_by_id) != workload_ids
        or set(source_roots) != aliases
    ):
        raise ValueError("corpus_v2_input_invalid")
    workload_by_id = {item["workload_id"]: item for item in workloads}
    for workload_id, selection in selection_by_id.items():
        workload = workload_by_id[workload_id]
        if (
            selection.get("repo_alias") != workload["repo_alias"]
            or selection.get("snapshot_commit") != workload["source_commit"]
            or selection.get("repository_identity_sha256")
            != workload.get("repository_identity_sha256")
        ):
            raise ValueError("corpus_v2_input_invalid")
    return workloads, packets, private_selection, source_roots, parent_receipt


def _corpus_source_digest_from_inputs(
    root: Path,
    workloads: list[dict[str, Any]],
    packets: dict[str, dict[str, Any]],
    private_selection: list[dict[str, Any]],
    source_roots: dict[str, dict[str, str]],
    parent_receipt: dict[str, Any],
) -> str:
    """Hash one validated input snapshot plus its Git repository identities."""
    repositories: list[dict[str, str]] = []
    for workload in sorted(workloads, key=lambda item: item["workload_id"]):
        alias = workload.get("repo_alias")
        entry = source_roots.get(alias)
        if (
            not isinstance(alias, str)
            or not isinstance(entry, dict)
            or set(entry) != {"path", "identity_sha256"}
            or entry.get("identity_sha256") != workload.get("repository_identity_sha256")
        ):
            raise ValueError("corpus_v2_input_invalid")
        repository = Path(str(entry["path"])).expanduser().resolve(strict=False)
        if _git(repository, "status", "--porcelain"):
            raise ValueError("corpus_v2_source_dirty")
        head = _git(repository, "rev-parse", "HEAD")
        tree = _git(repository, "rev-parse", "HEAD^{tree}")
        if head != workload.get("source_commit"):
            raise ValueError("corpus_v2_source_commit_mismatch")
        repositories.append({"repo_alias": alias, "head": head, "tree": tree})
    return canonical_sha256({
        "workloads": workloads,
        "packets": packets,
        "private_selection": private_selection,
        "parent_receipt": parent_receipt,
        "repositories": repositories,
    })


def corpus_source_digest(source_root: str | Path) -> str:
    """Hash public metadata plus clean Git HEAD/tree identities for all sources."""
    root = Path(source_root).expanduser().resolve(strict=False)
    inputs = _inputs(root)
    return _corpus_source_digest_from_inputs(root, *inputs)


def _assert_source_unchanged(source: Path, expected_digest: str) -> None:
    try:
        current_digest = corpus_source_digest(source)
    except Exception as error:
        raise ValueError("corpus_v2_source_mutated") from error
    if not hmac.compare_digest(current_digest, expected_digest):
        raise ValueError("corpus_v2_source_mutated")


def validate_dependency_lock(content: str) -> str:
    """Return a deterministic exact-pin lock or reject ambiguous sources."""
    if not isinstance(content, str):
        raise ValueError("corpus_v2_dependency_lock_invalid")
    entries: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        match = EXACT_LOCK_ENTRY.fullmatch(line)
        if match is None:
            raise ValueError("corpus_v2_dependency_lock_invalid")
        normalized_name = re.sub(r"[-_.]+", "-", match.group("name")).lower()
        if normalized_name in entries:
            raise ValueError("corpus_v2_dependency_lock_invalid")
        entries[normalized_name] = f"{normalized_name}=={match.group('version')}"
    if not entries:
        raise ValueError("corpus_v2_dependency_lock_invalid")
    return "\n".join(entries[name] for name in sorted(entries)) + "\n"


def _validate_locks(value: Mapping[str, str]) -> dict[str, str]:
    if (
        not isinstance(value, Mapping)
        or set(value) != LOCK_ALIASES
        or any(not isinstance(value[alias], str) or not value[alias].strip() for alias in LOCK_ALIASES)
    ):
        raise ValueError("corpus_v2_dependency_locks_invalid")
    return {alias: validate_dependency_lock(value[alias]) for alias in sorted(LOCK_ALIASES)}


def _clone_at_commit(source: Path, destination: Path, commit: str) -> None:
    try:
        clone = subprocess.run(
            ["git", "clone", "--no-local", "--quiet", str(source), str(destination)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("corpus_v2_clone_failed") from error
    if clone.returncode != 0:
        raise ValueError("corpus_v2_clone_failed")
    _git(destination, "checkout", "--quiet", "--detach", commit)


def _commit_lock(repository: Path, content: str) -> str:
    (repository / LOCK_FILENAME).write_text(content, encoding="utf-8")
    _git(repository, "add", LOCK_FILENAME)
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "OMC Corpus Builder",
        "GIT_AUTHOR_EMAIL": "omc-corpus@example.invalid",
        "GIT_COMMITTER_NAME": "OMC Corpus Builder",
        "GIT_COMMITTER_EMAIL": "omc-corpus@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    }
    _git(repository, "commit", "-qm", "chore: add benchmark dependency lock", env=commit_env)
    return _git(repository, "rev-parse", "HEAD")


def build_corpus_v2(
    source_root: str | Path,
    output_root: str | Path,
    *,
    batch_id: str,
    expected_parent_source_digest: str,
    dependency_locks: Mapping[str, str],
) -> dict[str, Any]:
    locks = _validate_locks(dependency_locks)
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("corpus_v2_batch_id_invalid")
    if not _is_sha256(expected_parent_source_digest):
        raise ValueError("corpus_v2_parent_digest_invalid")
    source = Path(source_root).expanduser().resolve(strict=False)
    destination = Path(output_root).expanduser().resolve(strict=False)
    if destination.exists():
        raise ValueError("corpus_v2_output_exists")
    inputs = _inputs(source)
    before_digest = _corpus_source_digest_from_inputs(source, *inputs)
    if not hmac.compare_digest(before_digest, expected_parent_source_digest):
        raise ValueError("corpus_v2_parent_digest_mismatch")
    workloads, packets, private_selection, source_roots, parent_receipt = inputs
    _validate_parent_receipt(parent_receipt)
    selection_by_id = {
        item["workload_id"]: deepcopy(item)
        for item in private_selection
        if isinstance(item, dict) and isinstance(item.get("workload_id"), str)
    }
    if len(selection_by_id) != 6:
        raise ValueError("corpus_v2_input_invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    destination_reserved = False
    try:
        frozen_workloads: list[dict[str, Any]] = []
        frozen_packets: dict[str, dict[str, Any]] = {}
        frozen_selection: list[dict[str, Any]] = []
        frozen_roots: dict[str, dict[str, str]] = {}
        for original in sorted(workloads, key=lambda item: item["workload_id"]):
            workload = deepcopy(original)
            workload_id = workload["workload_id"]
            alias = workload["repo_alias"]
            source_entry = source_roots.get(alias)
            if not isinstance(source_entry, dict):
                raise ValueError("corpus_v2_input_invalid")
            repository = staging / "sources" / alias
            repository.parent.mkdir(parents=True, exist_ok=True)
            _clone_at_commit(
                Path(source_entry["path"]).expanduser().resolve(strict=False),
                repository,
                workload["source_commit"],
            )
            if alias in locks:
                previous_identity = workload["repository_identity_sha256"]
                new_commit = _commit_lock(repository, locks[alias])
                workload["source_commit"] = new_commit
                workload["repository_identity_sha256"] = canonical_sha256({
                    "repo_alias": alias,
                    "parent_identity_sha256": previous_identity,
                    "source_commit": new_commit,
                    "dependency_lock_sha256": file_sha256(repository / LOCK_FILENAME),
                })
            packet = deepcopy(packets[workload_id])
            packet["source_commit"] = workload["source_commit"]
            workload["execution_packet_sha256"] = canonical_sha256(packet)
            selection = selection_by_id[workload_id]
            selection["snapshot_commit"] = workload["source_commit"]
            selection["repository_identity_sha256"] = workload[
                "repository_identity_sha256"
            ]
            frozen_workloads.append(workload)
            frozen_packets[workload_id] = packet
            frozen_selection.append(selection)
            frozen_roots[alias] = {
                "path": str(destination / "sources" / alias),
                "identity_sha256": workload["repository_identity_sha256"],
            }
        public_payload = {
            "schema_version": SCHEMA_VERSION,
            "batch_id": batch_id,
            "workloads": frozen_workloads,
            "packet_sha256s": {
                workload_id: canonical_sha256(packet)
                for workload_id, packet in sorted(frozen_packets.items())
            },
            "source_identities": {
                alias: entry["identity_sha256"]
                for alias, entry in sorted(frozen_roots.items())
            },
        }
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "batch_id": batch_id,
            "workload_count": 6,
            "parent_public_payload_sha256": parent_receipt.get(
                "public_payload_sha256"
            ),
            "parent_source_digest": before_digest,
            "public_payload_sha256": canonical_sha256(public_payload),
            "approval_status": "pending",
        }
        _write_json(staging / "workloads.json", frozen_workloads)
        _write_json(staging / "private-selection.json", frozen_selection)
        _write_json(staging / "source-roots.json", frozen_roots)
        _write_json(staging / "selection-public-payload.json", public_payload)
        _write_json(staging / "selection-receipt.json", receipt)
        for workload_id, packet in sorted(frozen_packets.items()):
            _write_json(staging / "packets" / f"{workload_id}.json", packet)
        _assert_source_unchanged(source, before_digest)
        try:
            destination.mkdir()
        except FileExistsError as error:
            raise ValueError("corpus_v2_output_exists") from error
        destination_reserved = True
        children = sorted(
            staging.iterdir(),
            key=lambda path: (path.name == "selection-receipt.json", path.name),
        )
        for child in children:
            os.replace(child, destination / child.name)
        staging.rmdir()
        return receipt
    except Exception as error:
        shutil.rmtree(staging, ignore_errors=True)
        if destination_reserved:
            shutil.rmtree(destination, ignore_errors=True)
        if isinstance(error, ValueError) and str(error) == "corpus_v2_source_mutated":
            raise
        _assert_source_unchanged(source, before_digest)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--expected-parent-source-digest", required=True)
    parser.add_argument("--source-e-lock", required=True, type=Path)
    parser.add_argument("--source-f-lock", required=True, type=Path)
    args = parser.parse_args(argv)
    receipt = build_corpus_v2(
        args.source,
        args.out,
        batch_id=args.batch_id,
        expected_parent_source_digest=args.expected_parent_source_digest,
        dependency_locks={
            "source-e": args.source_e_lock.read_text(encoding="utf-8"),
            "source-f": args.source_f_lock.read_text(encoding="utf-8"),
        },
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
