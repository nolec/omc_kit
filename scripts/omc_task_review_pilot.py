#!/usr/bin/env python3
"""Fail-closed preflight helpers for the task-review product-focus pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlsplit

from omc_output_contract import OutputContractError, parse_envelope

FROZEN_CASE_FIELDS = (
    "case_id",
    "request",
    "base_commit",
    "dod",
    "verification_command",
    "provider",
    "model",
    "reasoning",
    "timeout_sec",
    "repository_id",
    "dependency_condition",
)


def build_execution_capability_matrix(
    *, source_repository: Path, source_commit: str, pilot_contract_sha256: str
) -> dict[str, Any]:
    """Describe which frozen pilot requirements existing execution surfaces prove."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise PilotPreflightError("pilot_source_commit_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", pilot_contract_sha256):
        raise PilotPreflightError("pilot_contract_hash_invalid")
    source_root = Path(
        _git(source_repository, "rev-parse", "--show-toplevel")
    ).resolve()
    execution_root = Path(
        _git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
    ).resolve()
    if source_root != execution_root:
        raise PilotPreflightError("pilot_source_repository_mismatch")
    if not _execution_source_is_clean(execution_root):
        raise PilotPreflightError("pilot_execution_source_dirty")
    if _git(source_root, "rev-parse", "HEAD") != source_commit:
        raise PilotPreflightError("pilot_source_commit_mismatch")
    capabilities = [
        {
            "requirement_id": "R1_ISOLATED_WORKSPACE",
            "status": "SUPPORTED",
            "evidence": "omc_autopilot_workspace.materialize_isolated_clone",
        },
        {
            "requirement_id": "R2_APPROVED_FROZEN_INPUT",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline binds instruction/base commit but not pilot DoD/provider/model/reasoning/timeout",
        },
        {
            "requirement_id": "R3_OMC_TASK_REVIEW",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline runs task/review prompts, not the $omc-task/$omc-review skill contracts",
        },
        {
            "requirement_id": "R4_BASELINE_ARM",
            "status": "ADAPTER_REQUIRED",
            "evidence": "normalize_review_outcome validates native evidence but does not execute a baseline arm",
        },
        {
            "requirement_id": "R5_COUNTERBALANCED_ORDER",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline executes one arm and has no paired arm scheduler",
        },
        {
            "requirement_id": "R6_PAIRED_TERMINAL_RECEIPT",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline returns one candidate result, not a sealed two-arm metric receipt",
        },
        {
            "requirement_id": "R7_SHARED_PROVIDER_CONFIGURATION",
            "status": "ADAPTER_REQUIRED",
            "evidence": "safe pipeline confines the codex executor but does not bind provider/model/reasoning across arms",
        },
        {
            "requirement_id": "R8_BOUNDED_ARM_RETRY",
            "status": "ADAPTER_REQUIRED",
            "evidence": "pilot contract allows one retry per arm but safe pipeline has no paired retry ledger",
        },
    ]
    matrix: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-capability/v1",
        "source_commit": source_commit,
        "pilot_contract_sha256": pilot_contract_sha256,
        "capabilities": capabilities,
    }
    matrix["capability_matrix_sha256"] = _canonical_sha256(matrix)
    return matrix


class PilotPreflightError(ValueError):
    """Raised when pilot evidence cannot support a deterministic decision."""


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise PilotPreflightError("repository_git_evidence_unavailable")
    return result.stdout.strip()


def _execution_source_is_clean(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", "HEAD"],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise PilotPreflightError("repository_git_evidence_unavailable")


def _canonical_origin(raw: str) -> str:
    value = raw.strip()
    scp_match = re.fullmatch(r"(?:[^@/]+@)?([^:]+):(.+)", value)
    if scp_match and "://" not in value:
        host, path = scp_match.groups()
    else:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        path = parsed.path
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not host or not normalized_path:
        raise PilotPreflightError("repository_origin_invalid")
    return f"{host.casefold()}/{normalized_path}"


def canonical_repository_identity(repo: Path) -> dict[str, str]:
    """Derive a clone-stable identity without trusting caller supplied labels."""
    try:
        origin = _git(repo, "remote", "get-url", "origin")
    except PilotPreflightError as exc:
        raise PilotPreflightError("repository_origin_missing") from exc
    canonical_origin = _canonical_origin(origin)
    roots = _git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()
    if len(roots) != 1 or not re.fullmatch(r"[0-9a-f]{40}", roots[0]):
        raise PilotPreflightError("repository_root_commit_invalid")
    root_commit = roots[0]
    repository_id = hashlib.sha256(
        f"{canonical_origin}\n{root_commit}".encode("utf-8")
    ).hexdigest()
    return {
        "repository_id": repository_id,
        "canonical_origin": canonical_origin,
        "root_commit": root_commit,
    }


def _session_checkpoint(state_root: Path) -> dict[str, str] | None:
    sessions_root = state_root / "sessions"
    sessions: list[tuple[datetime, str, str]] = []
    if not sessions_root.is_dir():
        return None
    for path in sessions_root.glob("*/session.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            session_id = value["session_id"]
            created_at = value["created_at"]
            parsed = datetime.fromisoformat(created_at)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PilotPreflightError("session_checkpoint_invalid") from exc
        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(created_at, str)
            or parsed.tzinfo is None
        ):
            raise PilotPreflightError("session_checkpoint_invalid")
        sessions.append((parsed, session_id, created_at))
    if not sessions:
        return None
    _, session_id, created_at = max(sessions, key=lambda item: (item[0], item[1]))
    return {"created_at": created_at, "session_id": session_id}


def build_pilot_roster(
    repositories: list[Path],
    *,
    pilot_id: str,
    pilot_contract_sha256: str,
    source_commit: str,
) -> dict[str, Any]:
    if not pilot_id.strip():
        raise PilotPreflightError("pilot_id_missing")
    if not re.fullmatch(r"[0-9a-f]{64}", pilot_contract_sha256):
        raise PilotPreflightError("pilot_contract_hash_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise PilotPreflightError("pilot_source_commit_invalid")
    entries: list[dict[str, Any]] = []
    identities: set[str] = set()
    for raw_repo in repositories:
        repo = raw_repo.resolve()
        identity = canonical_repository_identity(repo)
        repository_id = identity["repository_id"]
        if repository_id in identities:
            raise PilotPreflightError("repository_identity_duplicate")
        identities.add(repository_id)
        state_root = repo / ".omc" / "state"
        entries.append(
            {
                **identity,
                "repository_root": str(repo),
                "state_root": str(state_root),
                "checkpoint": _session_checkpoint(state_root),
            }
        )
    if len(entries) < 2:
        raise PilotPreflightError("insufficient_roster_repositories")
    payload: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-roster/v1",
        "pilot_id": pilot_id.strip(),
        "pilot_contract_sha256": pilot_contract_sha256,
        "source_commit": source_commit,
        "repositories": sorted(entries, key=lambda item: item["repository_id"]),
    }
    payload["roster_sha256"] = _canonical_sha256(payload)
    return payload


def validate_pilot_start_receipt(
    receipt: dict[str, Any], *, expected_binding: dict[str, Any]
) -> dict[str, Any]:
    consumed_at = receipt.get("consumed_at")
    try:
        parsed = datetime.fromisoformat(str(consumed_at))
    except ValueError as exc:
        raise PilotPreflightError("pilot_start_receipt_invalid") from exc
    receipt_hash = receipt.get("receipt_sha256")
    hash_payload = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    if receipt_hash != _canonical_sha256(hash_payload):
        raise PilotPreflightError("pilot_start_receipt_hash_mismatch")
    if (
        receipt.get("schema_version") != "omc-task-review-pilot-start/v1"
        or receipt.get("action") != "task_review_pilot_start"
        or receipt.get("status") != "consumed"
        or receipt.get("binding") != expected_binding
        or parsed.tzinfo is None
    ):
        raise PilotPreflightError("pilot_start_receipt_invalid")
    return {"binding": dict(expected_binding), "t0": str(consumed_at)}


def _git_changed_paths(repo: Path, baseline: str, followup: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "-z", baseline, followup],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise PilotPreflightError("completion_commit_evidence_invalid")
    return sorted(
        item.decode("utf-8") for item in result.stdout.split(b"\0") if item
    )


def build_inventory_dry_run(
    roster: dict[str, Any], *, t0: str, observed_at: str | None = None
) -> dict[str, Any]:
    try:
        t0_at = datetime.fromisoformat(t0)
    except (TypeError, ValueError) as exc:
        raise PilotPreflightError("pilot_t0_invalid") from exc
    if t0_at.tzinfo is None:
        raise PilotPreflightError("pilot_t0_invalid")
    try:
        observation_at = (
            datetime.now(t0_at.tzinfo)
            if observed_at is None
            else datetime.fromisoformat(observed_at)
        )
    except (TypeError, ValueError) as exc:
        raise PilotPreflightError("pilot_observed_at_invalid") from exc
    if observation_at.tzinfo is None or observation_at < t0_at:
        raise PilotPreflightError("pilot_observed_at_invalid")
    collection_deadline = t0_at + timedelta(days=7)
    raw_repositories = roster.get("repositories")
    if not isinstance(raw_repositories, list) or len(raw_repositories) < 2:
        raise PilotPreflightError("pilot_roster_invalid")
    expected_hash = roster.get("roster_sha256")
    actual_hash = _canonical_sha256(
        {key: value for key, value in roster.items() if key != "roster_sha256"}
    )
    if expected_hash != actual_hash:
        raise PilotPreflightError("pilot_roster_hash_mismatch")

    inventory: list[dict[str, Any]] = []
    terminal_cursors: dict[str, dict[str, str] | None] = {}
    seen_repository_ids: set[str] = set()
    seen_sessions: set[tuple[str, str]] = set()
    for entry in raw_repositories:
        if not isinstance(entry, dict):
            raise PilotPreflightError("pilot_roster_invalid")
        repo = Path(str(entry.get("repository_root", ""))).resolve()
        identity = canonical_repository_identity(repo)
        if any(identity[key] != entry.get(key) for key in identity):
            raise PilotPreflightError("repository_identity_changed")
        repository_id = identity["repository_id"]
        if repository_id in seen_repository_ids:
            raise PilotPreflightError("repository_identity_duplicate")
        seen_repository_ids.add(repository_id)
        state_root = Path(str(entry.get("state_root", ""))).resolve()
        if state_root != repo / ".omc" / "state":
            raise PilotPreflightError("repository_state_root_changed")
        checkpoint = entry.get("checkpoint")
        checkpoint_key = (
            (datetime.min.replace(tzinfo=t0_at.tzinfo), "")
            if checkpoint is None
            else (datetime.fromisoformat(checkpoint["created_at"]), checkpoint["session_id"])
        )
        observed: list[tuple[datetime, str, Path, dict[str, Any]]] = []
        sessions_root = state_root / "sessions"
        for session_path in sessions_root.glob("*/session.json") if sessions_root.is_dir() else ():
            try:
                session = json.loads(session_path.read_text(encoding="utf-8"))
                session_id = session["session_id"]
                created_at = datetime.fromisoformat(session["created_at"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise PilotPreflightError("session_inventory_invalid") from exc
            if created_at.tzinfo is None or not isinstance(session_id, str):
                raise PilotPreflightError("session_inventory_invalid")
            if (created_at, session_id) <= checkpoint_key or created_at <= t0_at:
                continue
            if session_path.parent.name != session_id:
                raise PilotPreflightError("session_directory_mismatch")
            session_key = (repository_id, session_id)
            if session_key in seen_sessions:
                raise PilotPreflightError("session_identity_duplicate")
            seen_sessions.add(session_key)
            observed.append((created_at, session_id, session_path, session))
        observed.sort(key=lambda item: (item[0], item[1]))
        terminal_cursors[repository_id] = (
            {"created_at": observed[-1][0].isoformat(), "session_id": observed[-1][1]}
            if observed
            else checkpoint
        )
        for created_at, session_id, session_path, session in observed:
            item: dict[str, Any] = {
                "session_id": session_id,
                "created_at": created_at.isoformat(),
                "repository_id": repository_id,
                "eligible": False,
            }
            completion_path = session_path.with_name("completion.json")
            if created_at > observation_at:
                item["disposition"] = "future_session_timestamp"
            elif created_at > collection_deadline:
                item["disposition"] = "collection_window_expired"
            elif session.get("work_class") != "implementation":
                item["disposition"] = "classification_review_required"
            elif not completion_path.is_file():
                item["disposition"] = "completion_receipt_missing"
            else:
                try:
                    completion = json.loads(completion_path.read_text(encoding="utf-8"))
                    baseline = completion["baseline_commit"]
                    followup = completion["followup_commit"]
                    changed_paths = completion["changed_paths"]
                except (OSError, KeyError, TypeError, json.JSONDecodeError):
                    item["disposition"] = "completion_receipt_invalid"
                else:
                    if (
                        completion.get("session_id") != session_id
                        or completion.get("work_class") != "implementation"
                    ):
                        item["disposition"] = "classification_review_required"
                    else:
                        try:
                            actual_paths = _git_changed_paths(repo, baseline, followup)
                        except PilotPreflightError:
                            item["disposition"] = "completion_commit_evidence_invalid"
                        else:
                            if not actual_paths or actual_paths != sorted(changed_paths):
                                item["disposition"] = "completion_changed_paths_mismatch"
                            else:
                                item.update(
                                    {
                                        "eligible": True,
                                        "disposition": "eligible",
                                        "baseline_commit": baseline,
                                        "followup_commit": followup,
                                        "changed_paths": actual_paths,
                                    }
                                )
            inventory.append(item)
    inventory.sort(key=lambda item: (item["created_at"], item["session_id"]))
    eligible = [item for item in inventory if item["eligible"]][:3]
    diverse = len({item["repository_id"] for item in eligible}) >= 2
    status = (
        "PILOT_READY"
        if len(eligible) == 3 and diverse
        else (
            "STOP_ELIGIBILITY_DIVERSITY"
            if len(eligible) == 3
            else (
                "STOP_COLLECTION_WINDOW_EXPIRED"
                if observation_at > collection_deadline
                else "WAITING_FOR_CASES"
            )
        )
    )
    report: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-inventory/v1",
        "roster_sha256": expected_hash,
        "t0": t0,
        "observed_at": observation_at.isoformat(),
        "collection_deadline": collection_deadline.isoformat(),
        "status": status,
        "provider_call_count": 0,
        "inventory": inventory,
        "scanned_session_ids": [item["session_id"] for item in inventory],
        "terminal_cursors": terminal_cursors,
        "selected_cases": eligible if status == "PILOT_READY" else [],
    }
    report["inventory_sha256"] = _canonical_sha256(report)
    return report


def build_readiness_receipt(
    roster: dict[str, Any],
    start: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    roster_hash = roster.get("roster_sha256")
    if roster_hash != _canonical_sha256(
        {key: value for key, value in roster.items() if key != "roster_sha256"}
    ):
        raise PilotPreflightError("readiness_roster_hash_mismatch")
    inventory_hash = inventory.get("inventory_sha256")
    if inventory_hash != _canonical_sha256(
        {key: value for key, value in inventory.items() if key != "inventory_sha256"}
    ):
        raise PilotPreflightError("readiness_inventory_hash_mismatch")
    if inventory.get("provider_call_count") != 0:
        raise PilotPreflightError("readiness_provider_call_detected")
    if inventory.get("t0") != start.get("t0"):
        raise PilotPreflightError("readiness_t0_mismatch")
    try:
        t0_at = datetime.fromisoformat(str(start.get("t0")))
        deadline = datetime.fromisoformat(str(inventory.get("collection_deadline")))
        observed_at = datetime.fromisoformat(str(inventory.get("observed_at")))
    except ValueError as exc:
        raise PilotPreflightError("readiness_time_window_invalid") from exc
    if (
        t0_at.tzinfo is None
        or deadline.tzinfo is None
        or observed_at.tzinfo is None
        or deadline != t0_at + timedelta(days=7)
        or observed_at < t0_at
    ):
        raise PilotPreflightError("readiness_time_window_invalid")
    raw_inventory = inventory.get("inventory")
    selected_cases = inventory.get("selected_cases")
    if not isinstance(raw_inventory, list) or not isinstance(selected_cases, list):
        raise PilotPreflightError("readiness_selected_cases_invalid")
    repository_ids = {
        entry.get("repository_id")
        for entry in roster.get("repositories", [])
        if isinstance(entry, dict)
    }
    eligible: list[dict[str, Any]] = []
    seen_case_ids: set[tuple[object, object]] = set()
    previous_key: tuple[datetime, str] | None = None
    for item in raw_inventory:
        if not isinstance(item, dict):
            raise PilotPreflightError("readiness_selected_case_invalid")
        try:
            created_at = datetime.fromisoformat(str(item.get("created_at")))
        except ValueError as exc:
            raise PilotPreflightError("readiness_selected_case_invalid") from exc
        session_id = item.get("session_id")
        repository_id = item.get("repository_id")
        if (
            created_at.tzinfo is None
            or not isinstance(session_id, str)
            or not session_id
            or repository_id not in repository_ids
        ):
            raise PilotPreflightError("readiness_selected_case_invalid")
        order_key = (created_at, session_id)
        if previous_key is not None and order_key < previous_key:
            raise PilotPreflightError("readiness_inventory_not_chronological")
        previous_key = order_key
        case_id = (repository_id, session_id)
        if case_id in seen_case_ids:
            raise PilotPreflightError("readiness_selected_case_duplicate")
        seen_case_ids.add(case_id)
        if item.get("eligible") is True:
            if not (t0_at < created_at <= min(deadline, observed_at)):
                raise PilotPreflightError("readiness_selected_case_invalid")
            eligible.append(item)
    expected_cases = eligible[:3]
    if len(expected_cases) != 3 or selected_cases != expected_cases:
        raise PilotPreflightError("readiness_selected_cases_invalid")
    if len({item["repository_id"] for item in expected_cases}) < 2:
        raise PilotPreflightError("readiness_repository_diversity_invalid")
    if (
        inventory.get("status") != "PILOT_READY"
        or start.get("binding", {}).get("roster_sha256") != roster_hash
        or inventory.get("roster_sha256") != roster_hash
        or inventory.get("schema_version") != "omc-task-review-pilot-inventory/v1"
        or not re.fullmatch(r"[0-9a-f]{64}", str(inventory_hash or ""))
    ):
        raise PilotPreflightError("readiness_binding_mismatch")
    receipt: dict[str, Any] = {
        "schema_version": "omc-task-review-pilot-readiness/v1",
        "status": "PILOT_READY",
        "roster_sha256": roster_hash,
        "inventory_sha256": inventory["inventory_sha256"],
        "t0": start.get("t0"),
        "provider_call_count": 0,
    }
    receipt["readiness_sha256"] = _canonical_sha256(receipt)
    return receipt


def write_json_no_replace(path: Path, value: dict[str, Any]) -> None:
    """Publish canonical pilot evidence once and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PilotPreflightError("pilot_evidence_already_exists") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotPreflightError("pilot_evidence_invalid") from exc
    if not isinstance(value, dict):
        raise PilotPreflightError("pilot_evidence_invalid")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    roster = sub.add_parser("prepare-roster")
    roster.add_argument("--repository", type=Path, action="append", required=True)
    roster.add_argument("--pilot-id", required=True)
    roster.add_argument("--pilot-contract-sha256", required=True)
    roster.add_argument("--source-commit", required=True)
    roster.add_argument("--output", type=Path, required=True)
    inventory = sub.add_parser("inventory-dry-run")
    inventory.add_argument("--roster", type=Path, required=True)
    inventory.add_argument("--start-receipt", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    readiness = sub.add_parser("readiness")
    readiness.add_argument("--roster", type=Path, required=True)
    readiness.add_argument("--start-receipt", type=Path, required=True)
    readiness.add_argument("--inventory", type=Path, required=True)
    readiness.add_argument("--output", type=Path, required=True)
    capability_matrix = sub.add_parser("capability-matrix")
    capability_matrix.add_argument("--source-repository", type=Path, required=True)
    capability_matrix.add_argument("--source-commit", required=True)
    capability_matrix.add_argument("--pilot-contract-sha256", required=True)
    capability_matrix.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare-roster":
            value = build_pilot_roster(
                args.repository,
                pilot_id=args.pilot_id,
                pilot_contract_sha256=args.pilot_contract_sha256,
                source_commit=args.source_commit,
            )
        elif args.command == "inventory-dry-run":
            roster = _read_json_object(args.roster)
            binding = {
                "session_id": _read_json_object(args.start_receipt).get("binding", {}).get("session_id"),
                "roster_sha256": roster.get("roster_sha256"),
                "pilot_contract_sha256": roster.get("pilot_contract_sha256"),
                "source_commit": roster.get("source_commit"),
            }
            start = validate_pilot_start_receipt(
                _read_json_object(args.start_receipt), expected_binding=binding
            )
            value = build_inventory_dry_run(roster, t0=start["t0"])
        elif args.command == "readiness":
            roster = _read_json_object(args.roster)
            start_receipt = _read_json_object(args.start_receipt)
            binding = {
                "session_id": start_receipt.get("binding", {}).get("session_id"),
                "roster_sha256": roster.get("roster_sha256"),
                "pilot_contract_sha256": roster.get("pilot_contract_sha256"),
                "source_commit": roster.get("source_commit"),
            }
            start = validate_pilot_start_receipt(
                start_receipt, expected_binding=binding
            )
            value = build_readiness_receipt(
                roster, start, _read_json_object(args.inventory)
            )
        else:
            value = build_execution_capability_matrix(
                source_repository=args.source_repository,
                source_commit=args.source_commit,
                pilot_contract_sha256=args.pilot_contract_sha256,
            )
        write_json_no_replace(args.output, value)
    except PilotPreflightError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _present(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def preflight_case(case: dict[str, Any]) -> dict[str, object]:
    """Validate fields that must be frozen before either paired arm runs."""
    missing = [field for field in FROZEN_CASE_FIELDS if not _present(case.get(field))]
    if missing:
        raise PilotPreflightError(f"missing_frozen_fields:{','.join(missing)}")
    if (
        isinstance(case["timeout_sec"], bool)
        or not isinstance(case["timeout_sec"], int)
        or case["timeout_sec"] <= 0
    ):
        raise PilotPreflightError("invalid_timeout_sec")
    if not isinstance(case["dod"], list) or not all(
        isinstance(item, str) and item.strip() for item in case["dod"]
    ):
        raise PilotPreflightError("invalid_dod")
    return {
        "case_id": str(case["case_id"]),
        "ready": True,
        "frozen_field_count": len(FROZEN_CASE_FIELDS),
    }


def select_first_eligible_cases(
    sessions: list[dict[str, Any]],
    *,
    limit: int,
    t0: str,
    minimum_repository_count: int,
) -> list[dict[str, Any]]:
    """Select first-N without sorting, so reordered or incomplete input fails closed."""
    if limit <= 0 or minimum_repository_count <= 0:
        raise PilotPreflightError("invalid_selection_limit")
    try:
        t0_at = datetime.fromisoformat(t0)
    except (TypeError, ValueError) as exc:
        raise PilotPreflightError("pilot_t0_invalid") from exc
    if t0_at.tzinfo is None:
        raise PilotPreflightError("pilot_t0_invalid")
    seen: set[str] = set()
    previous: datetime | None = None
    selected: list[dict[str, Any]] = []
    for session in sessions:
        session_id = session.get("session_id")
        created_at = session.get("created_at")
        if not isinstance(session_id, str) or not session_id or session_id in seen:
            raise PilotPreflightError("session_inventory_identity_invalid")
        if not isinstance(created_at, str):
            raise PilotPreflightError("session_inventory_timestamp_invalid")
        try:
            observed_at = datetime.fromisoformat(created_at)
        except ValueError as exc:
            raise PilotPreflightError("session_inventory_timestamp_invalid") from exc
        if observed_at.tzinfo is None:
            raise PilotPreflightError("session_inventory_timestamp_invalid")
        if previous is not None and observed_at < previous:
            raise PilotPreflightError("session_inventory_not_chronological")
        if not isinstance(session.get("eligible"), bool):
            raise PilotPreflightError("session_eligibility_missing")
        seen.add(session_id)
        previous = observed_at
        if observed_at <= t0_at:
            continue
        if session["eligible"] and len(selected) < limit:
            repository_id = session.get("repository_id")
            if not isinstance(repository_id, str) or not repository_id.strip():
                raise PilotPreflightError("session_repository_identity_missing")
            selected_session = dict(session)
            selected_session["repository_id"] = repository_id.strip()
            selected.append(selected_session)
    if len(selected) != limit:
        raise PilotPreflightError("insufficient_eligible_cases")
    if (
        len({str(item["repository_id"]) for item in selected})
        < minimum_repository_count
    ):
        raise PilotPreflightError("insufficient_repository_diversity")
    return selected


def _native_artifact(result: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    execution = result.get("execution_artifacts")
    descriptor = (
        execution.get("durable_artifact") if isinstance(execution, dict) else None
    )
    if not isinstance(descriptor, dict):
        raise PilotPreflightError("review_artifact_descriptor_missing")
    relative_path = descriptor.get("path")
    expected_sha256 = descriptor.get("sha256")
    path_parts = PurePath(relative_path).parts if isinstance(relative_path, str) else ()
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or PurePath(relative_path).is_absolute()
        or len(path_parts) != 1
        or any(part in {"", ".", ".."} for part in path_parts)
    ):
        raise PilotPreflightError("review_artifact_path_invalid")
    root = artifact_root.resolve()
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise PilotPreflightError("review_artifact_missing") from exc
    try:
        try:
            artifact_fd = os.open(
                relative_path,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except FileNotFoundError as exc:
            raise PilotPreflightError("review_artifact_missing") from exc
        except OSError as exc:
            raise PilotPreflightError("review_artifact_path_invalid") from exc
        try:
            metadata = os.fstat(artifact_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise PilotPreflightError("review_artifact_path_invalid")
            chunks: list[bytes] = []
            while chunk := os.read(artifact_fd, 64 * 1024):
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(artifact_fd)
    finally:
        os.close(root_fd)
    if (
        not isinstance(expected_sha256, str)
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise PilotPreflightError("review_artifact_hash_mismatch")
    try:
        artifact = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotPreflightError("review_artifact_invalid") from exc
    if not isinstance(artifact, dict):
        raise PilotPreflightError("review_artifact_invalid")
    return artifact


def _validate_native_artifact(result: dict[str, Any], *, artifact_root: Path) -> str:
    artifact = _native_artifact(result, artifact_root=artifact_root)
    execution = result["execution_artifacts"]
    verdict = result.get("verdict")
    if (
        artifact.get("artifact_version") != 2
        or artifact.get("runner") != result.get("runner")
        or artifact.get("case_id") != result.get("case_id")
        or artifact.get("diff_sha256") != result.get("diff_id")
        or artifact.get("exit_code") != execution.get("exit_code")
        or artifact.get("adapter_verdict") != verdict
    ):
        raise PilotPreflightError("review_artifact_identity_mismatch")
    for stream in ("stdout", "stderr"):
        retained = artifact.get(stream)
        retained_sha256 = artifact.get(f"retained_{stream}_sha256")
        if (
            not isinstance(retained, str)
            or not isinstance(retained_sha256, str)
            or hashlib.sha256(retained.encode("utf-8")).hexdigest() != retained_sha256
        ):
            raise PilotPreflightError("review_artifact_transcript_mismatch")
    return str(verdict)


def normalize_review_outcome(
    arm: str,
    output: str | dict[str, Any],
    *,
    artifact_root: Path | None = None,
) -> str:
    """Normalize OMC and native review evidence without repairing ambiguity."""
    if arm == "omc":
        if not isinstance(output, str):
            raise PilotPreflightError("review_outcome_inconclusive")
        try:
            parsed = parse_envelope(output)
        except OutputContractError as exc:
            raise PilotPreflightError("review_outcome_inconclusive") from exc
        if parsed["stage"] != "review":
            raise PilotPreflightError("review_outcome_inconclusive")
        return "approved" if parsed["outcome"] == "approved" else "blocked"
    if arm == "baseline":
        if not isinstance(output, dict):
            raise PilotPreflightError("review_outcome_inconclusive")
        execution = output.get("execution_artifacts")
        verdict = output.get("verdict")
        if (
            output.get("status") != "completed"
            or not isinstance(execution, dict)
            or execution.get("exit_code") != 0
            or execution.get("native_review") is not True
            or execution.get("durable_output_retained") is not True
            or verdict not in {"APPROVE", "APPROVE WITH NOTES", "REVISE", "BLOCK"}
        ):
            raise PilotPreflightError("review_outcome_inconclusive")
        if artifact_root is None:
            raise PilotPreflightError("review_artifact_root_required")
        verified_verdict = _validate_native_artifact(
            output, artifact_root=artifact_root
        )
        return (
            "approved"
            if verified_verdict in {"APPROVE", "APPROVE WITH NOTES"}
            else "blocked"
        )
    raise PilotPreflightError("unknown_pilot_arm")
