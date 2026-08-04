#!/usr/bin/env python3
"""Prepare and verify blind baseline-context selection artifacts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from omc_plan_runtime_pilot import validate_confirmatory_candidate_selection


RETRIEVAL_POLICY = {
    "schema_version": 1,
    "candidate_source": "baseline_tree_only",
    "followup_data_allowed": False,
    "manual_substitution_allowed": False,
    "maximum_selected_files_per_case": 12,
    "maximum_indexed_files_per_case": 20_000,
    "excluded_path_parts": [
        ".git",
        ".next",
        ".nx",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "vendor",
    ],
}
FROZEN_SELECTION_COMMIT = "3ecfd9823620254c80a967190ced6150f2b02a2d"
FROZEN_SELECTION_SHA256 = (
    "7d9305445938ed6f71361c4a99a63e6e1b6a6bedef5ef3404a33c0167864a65b"
)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _without_digest(value: dict[str, Any], field: str) -> dict[str, Any]:
    payload = deepcopy(value)
    payload.pop(field, None)
    return payload


def _validate_packet_digest(
    packet: dict[str, Any], *, trusted_selection_public_keys: set[str]
) -> None:
    if not isinstance(packet, dict) or set(packet) != {
        "schema_version",
        "status",
        "batch_id",
        "selection_sha256",
        "selection_author_session_id",
        "selection_provenance",
        "selection_provenance_sha256",
        "retrieval_policy",
        "retrieval_policy_sha256",
        "cases",
        "packet_sha256",
    }:
        raise ValueError("context selection packet fields are invalid")
    if (
        packet["schema_version"] != 1
        or packet["status"] != "prepared"
        or packet["retrieval_policy"] != RETRIEVAL_POLICY
        or packet["retrieval_policy_sha256"] != canonical_digest(RETRIEVAL_POLICY)
    ):
        raise ValueError("context selection packet must use the frozen policy")
    if packet.get("packet_sha256") != canonical_digest(
        _without_digest(packet, "packet_sha256")
    ):
        raise ValueError("context selection packet hash mismatch")
    provenance = packet["selection_provenance"]
    author_session_id = _verify_selection_provenance(
        packet["selection_sha256"],
        provenance,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if (
        packet["selection_author_session_id"] != author_session_id
        or packet["selection_provenance_sha256"] != canonical_digest(provenance)
    ):
        raise ValueError("selection provenance contract is invalid")


def selection_provenance_payload(provenance: dict[str, Any]) -> bytes:
    payload = deepcopy(provenance)
    signoff = payload.get("signoff")
    if not isinstance(signoff, dict):
        raise ValueError("selection provenance signoff is required")
    signoff["signature"] = ""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_selection_provenance(
    selection_sha256: str,
    provenance: dict[str, Any],
    *,
    trusted_selection_public_keys: set[str],
) -> str:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    expected_fields = {
        "schema_version",
        "status",
        "selection_sha256",
        "selection_commit",
        "author_session_id",
        "signoff",
    }
    if (
        not isinstance(provenance, dict)
        or set(provenance) != expected_fields
        or provenance["schema_version"] != 1
        or provenance["status"] != "attested"
        or selection_sha256 != FROZEN_SELECTION_SHA256
        or provenance["selection_sha256"] != selection_sha256
        or provenance["selection_commit"] != FROZEN_SELECTION_COMMIT
        or not isinstance(provenance["author_session_id"], str)
        or not provenance["author_session_id"].strip()
    ):
        message = (
            "frozen selection digest mismatch"
            if selection_sha256 != FROZEN_SELECTION_SHA256
            else "selection provenance contract is invalid"
        )
        raise ValueError(message)
    signoff = provenance["signoff"]
    if not isinstance(signoff, dict) or set(signoff) != {
        "signer", "signer_public_key", "signature"
    }:
        raise ValueError("selection provenance signoff fields are invalid")
    public_key = signoff["signer_public_key"]
    if (
        not isinstance(signoff["signer"], str)
        or not signoff["signer"].strip()
        or public_key not in trusted_selection_public_keys
    ):
        raise ValueError("selection provenance signer is not trusted")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(
            base64.b64decode(signoff["signature"]),
            selection_provenance_payload(provenance),
        )
    except (ValueError, InvalidSignature) as error:
        raise ValueError("selection provenance signature is invalid") from error
    return provenance["author_session_id"].strip()


def _run_git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() if error.stderr else "git command failed"
        raise ValueError(detail) from error


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("baseline tree path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("baseline tree path is unsafe or non-canonical")
    return path


def _safe_case_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("context selection case id is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or path.as_posix() != value
        or value in {".", ".."}
    ):
        raise ValueError("context selection case id is unsafe or non-canonical")
    return value


def _baseline_tree(repo: Path, commit: str) -> list[dict[str, str]]:
    excluded = set(RETRIEVAL_POLICY["excluded_path_parts"])
    entries: list[dict[str, str]] = []
    for line in _run_git(repo, "ls-tree", "-r", "--full-tree", commit).splitlines():
        metadata, separator, relative = line.partition("\t")
        if not separator:
            raise ValueError("baseline tree entry is malformed")
        parts = metadata.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        path = _safe_relative_path(relative)
        if excluded.intersection(path.parts):
            continue
        entries.append({"path": path.as_posix(), "blob_oid": parts[2]})
    entries.sort(key=lambda item: item["path"])
    if not entries:
        raise ValueError("baseline tree contains no selectable files")
    if len(entries) > RETRIEVAL_POLICY["maximum_indexed_files_per_case"]:
        raise ValueError("baseline tree exceeds indexed file limit")
    return entries


def _verified_packet_tree(
    case: dict[str, Any], *, repo_roots: dict[str, str | Path]
) -> list[dict[str, str]]:
    repo_alias = case.get("repo_alias")
    if not isinstance(repo_alias, str) or repo_alias not in repo_roots:
        raise ValueError("baseline context repo mapping is missing")
    tree = _baseline_tree(
        Path(repo_roots[repo_alias]).resolve(), case.get("baseline_commit")
    )
    if (
        case.get("baseline_file_count") != len(tree)
        or case.get("baseline_tree_sha256") != canonical_digest(tree)
    ):
        raise ValueError("baseline tree fingerprint mismatch")
    return tree


def prepare_context_selection_packet(
    selection: dict[str, Any],
    *,
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    selection_provenance: dict[str, Any],
    trusted_selection_public_keys: set[str],
) -> dict[str, Any]:
    """Build a selector packet without exposing follow-up or local path data."""
    validate_confirmatory_candidate_selection(
        selection,
        trusted_prior_commits=trusted_prior_commits,
    )
    selection_author_session_id = _verify_selection_provenance(
        selection["selection_sha256"],
        selection_provenance,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )

    packet_cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for case in selection["cases"]:
        case_id = _safe_case_id(case.get("case_id"))
        repo_alias = case.get("repo_alias")
        baseline = case.get("baseline_commit")
        followup = case.get("followup_commit")
        request = case.get("request")
        if (
            case_id in case_ids
            or not isinstance(repo_alias, str)
            or repo_alias not in repo_roots
            or not isinstance(baseline, str)
            or not isinstance(followup, str)
            or not isinstance(request, str)
            or not request.strip()
        ):
            raise ValueError("context selection case is invalid")
        case_ids.add(case_id)
        repo = Path(repo_roots[repo_alias]).resolve()
        parents = _run_git(repo, "show", "-s", "--format=%P", followup).split()
        if not parents or parents[0] != baseline:
            raise ValueError("follow-up first parent does not match baseline")
        baseline_tree = _baseline_tree(repo, baseline)
        packet_cases.append({
            "case_id": case_id,
            "repo_alias": repo_alias,
            "baseline_commit": baseline,
            "request": request,
            "baseline_file_count": len(baseline_tree),
            "baseline_tree_sha256": canonical_digest(baseline_tree),
        })

    packet = {
        "schema_version": 1,
        "status": "prepared",
        "batch_id": selection.get("batch_id"),
        "selection_sha256": selection["selection_sha256"],
        "selection_author_session_id": selection_author_session_id,
        "selection_provenance": deepcopy(selection_provenance),
        "selection_provenance_sha256": canonical_digest(selection_provenance),
        "retrieval_policy": deepcopy(RETRIEVAL_POLICY),
        "retrieval_policy_sha256": canonical_digest(RETRIEVAL_POLICY),
        "cases": packet_cases,
    }
    packet["packet_sha256"] = canonical_digest(packet)
    return packet


def _validate_packet_against_selection(
    packet: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selection_public_keys: set[str],
) -> None:
    _validate_packet_digest(
        packet,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    expected = prepare_context_selection_packet(
        selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        selection_provenance=packet["selection_provenance"],
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if packet != expected:
        raise ValueError("context selection packet projection mismatch")


def materialize_baseline_workspaces(
    packet: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    output_root: str | Path,
    trusted_selection_public_keys: set[str],
) -> dict[str, Path]:
    """Create exact baseline-only workspaces without serializing private paths."""
    _validate_packet_against_selection(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    root = Path(output_root).resolve()
    if root.exists():
        raise ValueError("baseline workspace root already exists")
    verified = [
        (case, _verified_packet_tree(case, repo_roots=repo_roots))
        for case in packet.get("cases", [])
    ]
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        for case, tree in verified:
            case_id = _safe_case_id(case["case_id"])
            destination = staging / case_id
            destination.mkdir()
            repo = Path(repo_roots[case["repo_alias"]]).resolve()
            archive = subprocess.check_output(
                [
                    "git", "-C", str(repo), "archive", "--format=tar",
                    case["baseline_commit"],
                ],
                stderr=subprocess.PIPE,
            )
            allowed = {item["path"] for item in tree}
            extracted: set[str] = set()
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
                for member in bundle.getmembers():
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("baseline archive contains unsafe path")
                    if path.as_posix() not in allowed:
                        continue
                    bundle.extract(member, destination, filter="data")
                    extracted.add(path.as_posix())
            if extracted != allowed:
                raise ValueError("baseline archive does not match indexed tree")
        os.replace(staging, root)
    except subprocess.CalledProcessError as error:
        shutil.rmtree(staging, ignore_errors=True)
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(detail or "baseline archive failed") from error
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {case["case_id"]: root / case["case_id"] for case, _ in verified}


def selector_response_payload(response: dict[str, Any]) -> bytes:
    payload = deepcopy(response)
    signoff = payload.get("signoff")
    if not isinstance(signoff, dict):
        raise ValueError("selector response signoff is required")
    signoff["signature"] = ""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_response_signature(
    response: dict[str, Any], *, trusted_selector_public_keys: set[str]
) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signoff = response.get("signoff")
    if not isinstance(signoff, dict) or set(signoff) != {
        "signer", "signer_public_key", "signature"
    }:
        raise ValueError("selector response signoff fields are invalid")
    public_key = signoff["signer_public_key"]
    if (
        not isinstance(signoff["signer"], str)
        or not signoff["signer"].strip()
        or public_key not in trusted_selector_public_keys
    ):
        raise ValueError("selector response signer is not trusted")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(
            base64.b64decode(signoff["signature"]),
            selector_response_payload(response),
        )
    except (ValueError, InvalidSignature) as error:
        raise ValueError("selector response signature is invalid") from error


def _validate_selector_response(
    packet: dict[str, Any],
    response: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selector_public_keys: set[str],
    trusted_selection_public_keys: set[str],
) -> list[dict[str, Any]]:
    _validate_packet_against_selection(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if not isinstance(response, dict) or set(response) != {
        "schema_version", "status", "packet_sha256", "selector", "cases", "signoff"
    }:
        raise ValueError("selector response fields are invalid")
    if response["schema_version"] != 1 or response["status"] != "selected":
        raise ValueError("selector response status is invalid")
    if response["packet_sha256"] != packet["packet_sha256"]:
        raise ValueError("selector response packet hash mismatch")
    selector = response["selector"]
    if not isinstance(selector, dict) or set(selector) != {
        "session_id", "provider_outputs_available"
    }:
        raise ValueError("selector provenance fields are invalid")
    if (
        not isinstance(selector["session_id"], str)
        or not selector["session_id"].strip()
        or selector["session_id"] == packet["selection_author_session_id"]
    ):
        raise ValueError("selector requires an independent session")
    if selector["provider_outputs_available"] is not False:
        raise ValueError("selector provider outputs must be unavailable")
    _verify_response_signature(
        response, trusted_selector_public_keys=trusted_selector_public_keys
    )

    packet_ids = [case["case_id"] for case in packet["cases"]]
    packet_by_id = {case["case_id"]: case for case in packet["cases"]}
    response_cases = response["cases"]
    response_ids = (
        [case.get("case_id") for case in response_cases]
        if isinstance(response_cases, list)
        and all(isinstance(case, dict) for case in response_cases)
        else []
    )
    if (
        not isinstance(response_cases, list)
        or len(response_cases) != len(packet_by_id)
        or any(not isinstance(case, dict) for case in response_cases)
        or any(not isinstance(case_id, str) for case_id in response_ids)
        or len(response_ids) != len(set(response_ids))
        or set(response_ids) != set(packet_by_id)
    ):
        raise ValueError("selector response cases do not match packet")
    if response_ids != packet_ids:
        raise ValueError("selector response case order does not match packet")
    selected_cases: list[dict[str, Any]] = []
    for selected in response_cases:
        if not isinstance(selected, dict) or set(selected) != {
            "case_id", "selected_paths"
        }:
            raise ValueError("selector response case fields are invalid")
        paths = selected["selected_paths"]
        if (
            not isinstance(paths, list)
            or not paths
            or len(paths) > RETRIEVAL_POLICY["maximum_selected_files_per_case"]
            or any(not isinstance(path, str) or not path for path in paths)
            or len(paths) != len(set(paths))
        ):
            raise ValueError("selector selected paths are invalid")
        source = packet_by_id[selected["case_id"]]
        tree = {
            item["path"]: item["blob_oid"]
            for item in _verified_packet_tree(source, repo_roots=repo_roots)
        }
        if any(path not in tree for path in paths):
            raise ValueError("selector path is not present in baseline tree")
        selected_cases.append({
            "case_id": selected["case_id"],
            "repo_alias": source["repo_alias"],
            "baseline_commit": source["baseline_commit"],
            "selected_context": [
                {"path": path, "blob_oid": tree[path]} for path in paths
            ],
        })
    selected_cases.sort(key=lambda item: item["case_id"])
    return selected_cases


def apply_selector_response(
    packet: dict[str, Any],
    response: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selector_public_keys: set[str],
    trusted_selection_public_keys: set[str],
) -> dict[str, Any]:
    selected_cases = _validate_selector_response(
        packet,
        response,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selector_public_keys=trusted_selector_public_keys,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    manifest = {
        "schema_version": 1,
        "status": "preregistered",
        "selection_sha256": packet["selection_sha256"],
        "retrieval_policy_sha256": packet["retrieval_policy_sha256"],
        "packet_sha256": packet["packet_sha256"],
        "selector": deepcopy(response["selector"]),
        "selector_response_sha256": canonical_digest(response),
        "cases": selected_cases,
        "signoff": deepcopy(response["signoff"]),
    }
    manifest["manifest_sha256"] = canonical_digest(manifest)
    return manifest


def validate_baseline_context_manifest(
    manifest: dict[str, Any],
    *,
    packet: dict[str, Any],
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selector_public_keys: set[str],
    trusted_selection_public_keys: set[str],
) -> None:
    if manifest.get("manifest_sha256") != canonical_digest(
        _without_digest(manifest, "manifest_sha256")
    ):
        raise ValueError("baseline context manifest hash mismatch")
    response = {
        "schema_version": 1,
        "status": "selected",
        "packet_sha256": manifest.get("packet_sha256"),
        "selector": deepcopy(manifest.get("selector")),
        "cases": [
            {
                "case_id": case.get("case_id"),
                "selected_paths": [
                    item.get("path") for item in case.get("selected_context", [])
                ],
            }
            for case in manifest.get("cases", [])
        ],
        "signoff": deepcopy(manifest.get("signoff")),
    }
    selected_cases = _validate_selector_response(
        packet,
        response,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selector_public_keys=trusted_selector_public_keys,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if (
        manifest.get("schema_version") != 1
        or manifest.get("status") != "preregistered"
        or manifest.get("selection_sha256") != packet.get("selection_sha256")
        or manifest.get("retrieval_policy_sha256")
        != packet.get("retrieval_policy_sha256")
        or manifest.get("selector_response_sha256") != canonical_digest(response)
        or manifest.get("cases") != selected_cases
    ):
        raise ValueError("baseline context manifest contract mismatch")


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _load_commit_registry(path: str | Path) -> list[str]:
    registry = _load_json(path)
    if (
        set(registry) != {"schema_version", "commits"}
        or registry.get("schema_version") != 1
        or not isinstance(registry.get("commits"), list)
    ):
        raise ValueError("confirmatory prior commit registry is invalid")
    return registry["commits"]


def _write_json_atomic(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("selection")
    prepare.add_argument("repo_map")
    prepare.add_argument("prior_registry")
    prepare.add_argument("selection_provenance")
    prepare.add_argument("--trusted-selection-public-key", action="append", required=True)
    prepare.add_argument("--output", required=True)
    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("packet")
    materialize.add_argument("selection")
    materialize.add_argument("repo_map")
    materialize.add_argument("prior_registry")
    materialize.add_argument("output_root")
    materialize.add_argument(
        "--trusted-selection-public-key", action="append", required=True
    )
    apply = subparsers.add_parser("apply")
    apply.add_argument("packet")
    apply.add_argument("response")
    apply.add_argument("selection")
    apply.add_argument("repo_map")
    apply.add_argument("prior_registry")
    apply.add_argument("--trusted-selector-public-key", action="append", required=True)
    apply.add_argument(
        "--trusted-selection-public-key", action="append", required=True
    )
    apply.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_context_selection_packet(
            _load_json(args.selection),
            repo_roots=_load_json(args.repo_map),
            trusted_prior_commits=_load_commit_registry(args.prior_registry),
            selection_provenance=_load_json(args.selection_provenance),
            trusted_selection_public_keys=set(args.trusted_selection_public_key),
        )
    elif args.command == "apply":
        result = apply_selector_response(
            _load_json(args.packet),
            _load_json(args.response),
            selection=_load_json(args.selection),
            repo_roots=_load_json(args.repo_map),
            trusted_prior_commits=_load_commit_registry(args.prior_registry),
            trusted_selector_public_keys=set(args.trusted_selector_public_key),
            trusted_selection_public_keys=set(args.trusted_selection_public_key),
        )
    else:
        materialize_baseline_workspaces(
            _load_json(args.packet),
            selection=_load_json(args.selection),
            repo_roots=_load_json(args.repo_map),
            trusted_prior_commits=_load_commit_registry(args.prior_registry),
            output_root=args.output_root,
            trusted_selection_public_keys=set(args.trusted_selection_public_key),
        )
        return 0
    _write_json_atomic(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
