#!/usr/bin/env python3
"""Fail-close availability preflight for the six Product Value workloads."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any

import omc_product_value_acceptance as acceptance
import omc_product_value_corpus_v2 as corpus_v2


SCHEMA_VERSION = "omc-product-value-corpus-preflight/v1"
EXPECTED_WORKLOADS = tuple(f"pv-{index:02d}" for index in range(1, 7))
EXPECTED_ALIASES = {
    workload_id: f"source-{chr(96 + index)}"
    for index, workload_id in enumerate(EXPECTED_WORKLOADS, start=1)
}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def report_sha256(report: dict[str, Any]) -> str:
    """Return the digest of a report without its self-referential hash field."""
    payload = dict(report)
    payload.pop("report_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _git(root: Path, *args: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return result.returncode == 0, result.stdout.strip()


def _committed_file_sha256(root: Path, commit: Any, relative: Any) -> str | None:
    if not isinstance(commit, str) or not isinstance(relative, str):
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{relative}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def _nonempty_argv(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _packet_reasons(packet: Any, workload: dict[str, Any]) -> list[str]:
    if not isinstance(packet, dict):
        return ["execution_packet_missing"]
    reasons: list[str] = []
    if not isinstance(packet.get("request"), str) or not packet["request"].strip():
        reasons.append("request_missing")
    if not isinstance(packet.get("dod"), str) or not packet["dod"].strip():
        reasons.append("dod_missing")
    verification = packet.get("verification")
    if not isinstance(verification, dict) or not _nonempty_argv(
        verification.get("argv")
    ):
        reasons.append("verification_command_missing")
    bindings_match = (
        packet.get("workload_id") == workload.get("workload_id")
        and packet.get("repo_alias") == workload.get("repo_alias")
        and packet.get("source_commit") == workload.get("source_commit")
        and corpus_v2.canonical_sha256(packet)
        == workload.get("execution_packet_sha256")
        and corpus_v2.canonical_sha256(packet.get("request"))
        == workload.get("request_sha256")
        and corpus_v2.canonical_sha256(packet.get("dod"))
        == workload.get("dod_sha256")
        and corpus_v2.canonical_sha256(packet.get("verification"))
        == workload.get("verification_sha256")
    )
    if not bindings_match:
        reasons.append("execution_packet_binding_mismatch")
    return reasons


def _readiness_options(argv: Any) -> tuple[str, dict[str, str]] | None:
    if not _nonempty_argv(argv) or len(argv) % 2 == 0:
        return None
    options: dict[str, str] = {}
    for index in range(1, len(argv), 2):
        name = argv[index]
        value = argv[index + 1]
        if not name.startswith("--") or name in options:
            return None
        options[name] = value
    return argv[0], options


def _safe_source_file(source: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        return None
    candidate = source.joinpath(*path.parts).resolve(strict=False)
    if source != candidate and source not in candidate.parents:
        return None
    return candidate


def _environment_reasons(
    environment: Any,
    workload: dict[str, Any],
    source_entry: Any,
    cache_digests: dict[str, str | None],
) -> list[str]:
    if not isinstance(environment, dict):
        return ["environment_spec_missing"]
    required_paths = (
        "dependency_lock_path",
        "direct_surface_verification_path",
        "runtime_identity_path",
    )
    if any(
        not isinstance(environment.get(key), str) or not environment[key].strip()
        for key in required_paths
    ):
        return ["environment_spec_incomplete"]
    if not _is_sha256(environment.get("direct_surface_verification_sha256")):
        return ["environment_spec_incomplete"]
    readiness = environment.get("readiness")
    parsed = (
        _readiness_options(readiness.get("argv"))
        if isinstance(readiness, dict)
        else None
    )
    if parsed is None:
        return ["environment_spec_incomplete"]
    command, options = parsed
    required_options = {
        "--source-commit",
        "--dependency-lock-path",
        "--dependency-lock-sha256",
        "--cache-path",
        "--cache-sha256",
        "--runtime-identity-path",
        "--runtime-identity-sha256",
    }
    if set(options) != required_options or any(
        not _is_sha256(options[name])
        for name in (
            "--dependency-lock-sha256",
            "--cache-sha256",
            "--runtime-identity-sha256",
        )
    ):
        return ["environment_spec_incomplete"]
    if not isinstance(source_entry, dict) or not isinstance(
        source_entry.get("path"), str
    ):
        return ["environment_artifact_mismatch"]
    source = Path(source_entry["path"]).expanduser().resolve(strict=False)
    lock = _safe_source_file(source, environment["dependency_lock_path"])
    surface = _safe_source_file(
        source, environment["direct_surface_verification_path"]
    )
    cache_path = environment.get("cache_path")
    runtime_path = environment["runtime_identity_path"]
    if not isinstance(cache_path, str) or not cache_path.strip():
        return ["environment_spec_incomplete"]
    cache = Path(cache_path).expanduser().resolve(strict=False)
    runtime = Path(runtime_path).expanduser().resolve(strict=False)
    cache_key = str(cache)
    try:
        if cache_key not in cache_digests:
            cache_digests[cache_key] = acceptance.canonical_cache_inventory_sha256(
                cache,
                require_readonly=True,
            )
        lock_sha256 = (
            _file_sha256(lock) if lock is not None and lock.is_file() else None
        )
        surface_sha256 = (
            _file_sha256(surface)
            if surface is not None and surface.is_file()
            else None
        )
        artifacts_match = (
            options["--source-commit"] == workload.get("source_commit")
            and options["--dependency-lock-path"]
            == environment["dependency_lock_path"]
            and options["--cache-path"] == cache_path
            and options["--runtime-identity-path"] == runtime_path
            and command == runtime_path
            and lock_sha256 == options["--dependency-lock-sha256"]
            and surface_sha256
            == environment["direct_surface_verification_sha256"]
            and runtime.is_file()
            and _file_sha256(runtime) == options["--runtime-identity-sha256"]
            and cache_digests[cache_key] == options["--cache-sha256"]
        )
    except (OSError, ValueError):
        artifacts_match = False
        cache_digests[cache_key] = None
    if not artifacts_match:
        return ["environment_artifact_mismatch"]
    commit = workload.get("source_commit")
    if (
        _committed_file_sha256(
            source, commit, environment["dependency_lock_path"]
        )
        != lock_sha256
        or _committed_file_sha256(
            source, commit, environment["direct_surface_verification_path"]
        )
        != surface_sha256
    ):
        return ["environment_artifact_not_commit_bound"]
    return []


def _source_reasons(
    source_entry: Any,
    workload: dict[str, Any],
) -> list[str]:
    if not isinstance(source_entry, dict):
        return ["source_repository_missing"]
    source_identity = source_entry.get("identity_sha256")
    workload_identity = workload.get("repository_identity_sha256")
    if not _is_sha256(source_identity) or not _is_sha256(workload_identity):
        return ["source_identity_invalid"]
    if source_identity != workload_identity:
        return ["source_identity_mismatch"]
    path_value = source_entry.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        return ["source_repository_missing"]
    source = Path(path_value).expanduser().resolve(strict=False)
    is_repo, _ = _git(source, "rev-parse", "--git-dir")
    if not is_repo:
        return ["source_repository_missing"]
    top_level_ok, top_level = _git(source, "rev-parse", "--show-toplevel")
    if (
        not top_level_ok
        or Path(top_level).expanduser().resolve(strict=False) != source
    ):
        return ["source_repository_root_mismatch"]
    commit = workload.get("source_commit")
    if not isinstance(commit, str) or not commit:
        return ["source_commit_missing"]
    commit_exists, _ = _git(source, "cat-file", "-e", f"{commit}^{{commit}}")
    if not commit_exists:
        return ["source_commit_missing"]
    head_ok, head = _git(source, "rev-parse", "HEAD")
    if not head_ok or head != commit:
        return ["source_commit_mismatch"]
    clean_ok, status = _git(source, "status", "--porcelain")
    if not clean_ok:
        return ["source_repository_unreadable"]
    if status:
        return ["source_dirty"]
    return []


def _index_workloads(
    value: Any,
) -> tuple[dict[str, dict[str, Any]], set[str], bool]:
    if not isinstance(value, list):
        return {}, set(EXPECTED_WORKLOADS), True
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    malformed = len(value) != len(EXPECTED_WORKLOADS)
    for item in value:
        if not isinstance(item, dict):
            malformed = True
            continue
        workload_id = item.get("workload_id")
        if not isinstance(workload_id, str) or not workload_id:
            malformed = True
            continue
        if workload_id in indexed:
            duplicates.add(workload_id)
        else:
            indexed[workload_id] = item
    return indexed, duplicates, malformed


def preflight_corpus(
    source_root: str | Path,
    environment_specs_path: str | Path,
) -> dict[str, Any]:
    """Inspect corpus availability without mutating inputs or calling a provider."""
    root = Path(source_root).expanduser().resolve(strict=False)
    workloads_input = _load_json(root / "workloads.json")
    workload_by_id, duplicate_ids, workload_collection_invalid = _index_workloads(
        workloads_input
    )
    source_roots = _load_json(root / "source-roots.json")
    if not isinstance(source_roots, dict):
        source_roots = {}
    environments = _load_json(Path(environment_specs_path).expanduser())
    if not isinstance(environments, dict):
        environments = {}

    expected_ids = set(EXPECTED_WORKLOADS)
    expected_aliases = set(EXPECTED_ALIASES.values())
    report_reasons: list[str] = []
    if workload_collection_invalid:
        report_reasons.append("workload_collection_invalid")
    if set(workload_by_id) - expected_ids:
        report_reasons.append("unexpected_workload_ids")
    if set(source_roots) - expected_aliases:
        report_reasons.append("unexpected_source_aliases")
    if set(environments) - expected_ids:
        report_reasons.append("unexpected_environment_ids")

    results: list[dict[str, Any]] = []
    packets: dict[str, Any] = {}
    cache_digests: dict[str, str | None] = {}
    for workload_id in EXPECTED_WORKLOADS:
        reasons: list[str] = []
        workload = workload_by_id.get(workload_id)
        if not isinstance(workload, dict):
            reasons.append("workload_metadata_missing")
            workload = {"workload_id": workload_id}
        elif workload_id in duplicate_ids:
            reasons.append("workload_metadata_duplicate")
        alias = workload.get("repo_alias")
        if alias != EXPECTED_ALIASES[workload_id]:
            reasons.append("repo_alias_mismatch")
        packet = _load_json(root / "packets" / f"{workload_id}.json")
        packets[workload_id] = packet
        reasons.extend(_packet_reasons(packet, workload))
        source_reasons = _source_reasons(source_roots.get(alias), workload)
        reasons.extend(source_reasons)
        if not source_reasons:
            reasons.extend(
                _environment_reasons(
                    environments.get(workload_id),
                    workload,
                    source_roots.get(alias),
                    cache_digests,
                )
            )
        unique_reasons = list(dict.fromkeys(reasons))
        results.append(
            {
                "workload_id": workload_id,
                "status": "ready" if not unique_reasons else "blocked",
                "reason_codes": unique_reasons,
            }
        )

    ready_count = sum(item["status"] == "ready" for item in results)
    input_binding_sha256 = corpus_v2.canonical_sha256(
        {
            "workloads": workloads_input,
            "packets": packets,
            "environments": environments,
            "source_identities": {
                alias: (
                    entry.get("identity_sha256")
                    if isinstance(entry, dict)
                    else None
                )
                for alias, entry in sorted(source_roots.items())
            },
        }
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": (
            "ready"
            if ready_count == len(EXPECTED_WORKLOADS) and not report_reasons
            else "blocked"
        ),
        "workload_count": len(EXPECTED_WORKLOADS),
        "ready_count": ready_count,
        "provider_call_count": 0,
        "input_binding_sha256": input_binding_sha256,
        "reason_codes": report_reasons,
        "workloads": results,
    }
    report["report_sha256"] = report_sha256(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--environment-specs", required=True, type=Path)
    args = parser.parse_args(argv)
    report = preflight_corpus(args.source, args.environment_specs)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
