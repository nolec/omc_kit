#!/usr/bin/env python3
"""Local-only, raw-free cohort ledger for OMC skill-effectiveness hypotheses."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import omc_state


SCHEMA_VERSION = 1
CONFIG_NAME = "skill-effectiveness-cohort-v1.json"
LEDGER_NAME = "skill-effectiveness-cohort-v1.jsonl"
_WORK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,127}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SKILLS = frozenset({"omc-plan", "omc-task", "omc-review"})
_PROFILES = frozenset({"lite", "full", "unknown"})
_TAXONOMIES = frozenset({
    "requirement_gap", "verification_gap", "scope_gap", "output_confusion",
    "gate_friction", "environment_blocker", "external_dependency",
})
_VERDICTS = frozenset({"APPROVE", "REVISE", "BLOCK", "NOT_RUN"})
_OUTCOMES = frozenset({"accepted", "correction", "deferred"})


class SkillCohortError(ValueError):
    """The local cohort contract was not satisfied."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _now() -> str:
    # Session state is persisted at whole-second precision; keep enrollment on
    # the same clock so a session created later in the enrollment second is in scope.
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _timestamp(value: object, *, reason: str) -> datetime:
    if not isinstance(value, str):
        raise SkillCohortError(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SkillCohortError(reason) from error
    if parsed.tzinfo is None:
        raise SkillCohortError(reason)
    return parsed.astimezone(UTC)


def _regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise SkillCohortError("not_regular_file")


def _root(project_root: Path) -> Path:
    if project_root.is_symlink() or not project_root.is_dir():
        raise SkillCohortError("source_not_regular_directory")
    return project_root.resolve(strict=True)


def _omc_dir(project_root: Path, *, create: bool) -> Path:
    path = project_root / ".omc"
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise SkillCohortError("omc_directory_invalid")
    if create:
        path.mkdir(parents=False, exist_ok=True)
    return path


def config_path(project_root: Path) -> Path:
    return project_root / ".omc" / CONFIG_NAME


def ledger_path(project_root: Path) -> Path:
    return project_root / ".omc" / LEDGER_NAME


def _session_ids_at_enrollment(project_root: Path) -> list[str]:
    session_root = project_root / ".omc" / "state" / "sessions"
    if not session_root.exists() and not session_root.is_symlink():
        return []
    if session_root.is_symlink() or not session_root.is_dir():
        raise SkillCohortError("session_state_invalid")
    session_ids: list[str] = []
    for session_path in session_root.glob("*/session.json"):
        _regular_file(session_path)
        session_id = session_path.parent.name
        if _WORK_ID.fullmatch(session_id) is None:
            raise SkillCohortError("session_state_invalid")
        session_ids.append(session_id)
    return sorted(set(session_ids))


def _write_once(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise SkillCohortError("write_once_conflict")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(_canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    _regular_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkillCohortError("config_invalid") from error
    if not isinstance(value, dict):
        raise SkillCohortError("config_invalid")
    return value


def _validate_config(project_root: Path) -> dict[str, Any]:
    _omc_dir(project_root, create=False)
    config = _read_json(config_path(project_root))
    expected = {"schema_version", "enabled", "enrollment_id", "enrolled_at", "enrollment_session_ids"}
    try:
        enrollment_uuid = uuid.UUID(config.get("enrollment_id", ""))
    except (TypeError, ValueError, AttributeError) as error:
        raise SkillCohortError("config_invalid") from error
    if (
        set(config) != expected
        or config.get("schema_version") != SCHEMA_VERSION
        or config.get("enabled") is not True
        or not isinstance(config.get("enrollment_id"), str)
        or enrollment_uuid.version != 4
    ):
        raise SkillCohortError("config_invalid")
    _timestamp(config["enrolled_at"], reason="config_invalid")
    session_ids = config["enrollment_session_ids"]
    if (
        not isinstance(session_ids, list)
        or session_ids != sorted(set(session_ids))
        or any(not isinstance(session_id, str) or _WORK_ID.fullmatch(session_id) is None for session_id in session_ids)
    ):
        raise SkillCohortError("config_invalid")
    return config


def enable(project_root: Path, *, host_identity: str | None = None) -> dict[str, Any]:
    root = _root(project_root)
    # Kept as an ignored keyword-only compatibility parameter for callers from
    # older installations. Host identifiers are neither needed nor retained.
    del host_identity
    _omc_dir(root, create=True)
    with omc_state._omc_lock(root):
        path = config_path(root)
        if path.exists() or path.is_symlink():
            _validate_config(root)
            return {"enabled": True, "status": "unchanged"}
        config = {
            "schema_version": SCHEMA_VERSION,
            "enabled": True,
            "enrollment_id": str(uuid.uuid4()),
            "enrolled_at": _now(),
            "enrollment_session_ids": _session_ids_at_enrollment(root),
        }
        try:
            _write_once(path, config)
        except FileExistsError:
            # A non-cohort writer may win between the check and the atomic link.
            # Only accept that race after the published config passes validation.
            _validate_config(root)
            return {"enabled": True, "status": "unchanged"}
        return {"enabled": True, "status": "enabled"}


def _validate_source_identity(value: object) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"version", "sha256"}:
        raise SkillCohortError("source_identity_invalid")
    version = value.get("version")
    digest = value.get("sha256")
    if not isinstance(version, str) or not version or not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
        raise SkillCohortError("source_identity_invalid")
    return version, digest


def _validate_event(event: object, *, enrollment_id: str, previous_hash: str | None) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise SkillCohortError("event_not_object")
    common = {"schema_version", "event_id", "event_type", "observed_at", "enrollment_id", "work_id", "previous_event_sha256", "event_sha256"}
    event_type = event.get("event_type")
    type_fields = {
        "candidate": {"skill_id", "policy_profile", "source_version", "source_sha256"},
        "review": {"verdict", "taxonomy"},
        "followup": {"outcome"},
    }
    if event_type not in type_fields or set(event) != common | type_fields[event_type]:
        raise SkillCohortError("event_schema_invalid")
    if (
        event.get("schema_version") != SCHEMA_VERSION
        or not isinstance(event.get("event_id"), str)
        or not isinstance(event.get("observed_at"), str)
        or event.get("enrollment_id") != enrollment_id
        or not isinstance(event.get("work_id"), str)
        or _WORK_ID.fullmatch(event["work_id"]) is None
        or event.get("previous_event_sha256") != previous_hash
        or not isinstance(event.get("event_sha256"), str)
        or _HEX64.fullmatch(event["event_sha256"]) is None
    ):
        raise SkillCohortError("event_contract_invalid")
    if event_type == "candidate" and (
        event.get("skill_id") not in _SKILLS
        or event.get("policy_profile") not in _PROFILES
        or not isinstance(event.get("source_version"), str)
        or not event["source_version"]
        or not isinstance(event.get("source_sha256"), str)
        or _HEX64.fullmatch(event["source_sha256"]) is None
    ):
        raise SkillCohortError("event_contract_invalid")
    if event_type == "review" and (event.get("verdict") not in _VERDICTS or event.get("taxonomy") not in _TAXONOMIES):
        raise SkillCohortError("event_contract_invalid")
    if event_type == "followup" and event.get("outcome") not in _OUTCOMES:
        raise SkillCohortError("event_contract_invalid")
    unsigned = dict(event)
    unsigned.pop("event_sha256")
    if _sha256(unsigned) != event["event_sha256"]:
        raise SkillCohortError("event_hash_invalid")
    return event


def _load_events(project_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = _validate_config(project_root)
    path = ledger_path(project_root)
    if not path.exists() and not path.is_symlink():
        return config, []
    _regular_file(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise SkillCohortError("ledger_unreadable") from error
    previous_hash: str | None = None
    events: list[dict[str, Any]] = []
    seen: dict[str, set[object]] = {"candidate": set(), "review": set(), "followup": set()}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise SkillCohortError("ledger_json_invalid") from error
        checked = _validate_event(event, enrollment_id=config["enrollment_id"], previous_hash=previous_hash)
        event_key: object = (
            (checked["work_id"], checked["skill_id"])
            if checked["event_type"] == "candidate"
            else checked["work_id"]
        )
        if event_key in seen[checked["event_type"]]:
            raise SkillCohortError("duplicate_event")
        if checked["event_type"] != "candidate" and not any(
            candidate_work_id == checked["work_id"]
            for candidate_work_id, _skill_id in seen["candidate"]
        ):
            raise SkillCohortError("candidate_required")
        seen[checked["event_type"]].add(event_key)
        previous_hash = checked["event_sha256"]
        events.append(checked)
    return config, events


def _append(path: Path, event: dict[str, Any]) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir() or path.is_symlink():
        raise SkillCohortError("ledger_not_regular_file")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(descriptor, _canonical_bytes(event) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record(project_root: Path, *, event_type: str, work_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    root = _root(project_root)
    if not isinstance(work_id, str) or _WORK_ID.fullmatch(work_id) is None:
        raise SkillCohortError("work_id_invalid")
    with omc_state._omc_lock(root):
        config, events = _load_events(root)
        if any(
            event["event_type"] == event_type
            and event["work_id"] == work_id
            and (event_type != "candidate" or event.get("skill_id") == payload.get("skill_id"))
            for event in events
        ):
            raise SkillCohortError("duplicate_event")
        if event_type != "candidate" and not any(event["event_type"] == "candidate" and event["work_id"] == work_id for event in events):
            raise SkillCohortError("candidate_required")
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "observed_at": _now(),
            "enrollment_id": config["enrollment_id"],
            "work_id": work_id,
            "previous_event_sha256": events[-1]["event_sha256"] if events else None,
            **payload,
        }
        event["event_sha256"] = _sha256(event)
        _validate_event(event, enrollment_id=config["enrollment_id"], previous_hash=event["previous_event_sha256"])
        _append(ledger_path(root), event)
        return event


def record_candidate(project_root: Path, *, work_id: str, skill_id: str, policy_profile: str, source_identity: object) -> dict[str, Any]:
    if skill_id not in _SKILLS:
        raise SkillCohortError("skill_id_invalid")
    if policy_profile not in _PROFILES:
        raise SkillCohortError("policy_profile_invalid")
    version, digest = _validate_source_identity(source_identity)
    return _record(project_root, event_type="candidate", work_id=work_id, payload={
        "skill_id": skill_id,
        "policy_profile": policy_profile,
        "source_version": version,
        "source_sha256": digest,
    })


def record_review(project_root: Path, *, work_id: str, verdict: str, taxonomy: str) -> dict[str, Any]:
    if verdict not in _VERDICTS:
        raise SkillCohortError("review_verdict_invalid")
    if taxonomy not in _TAXONOMIES:
        raise SkillCohortError("taxonomy_invalid")
    return _record(project_root, event_type="review", work_id=work_id, payload={"verdict": verdict, "taxonomy": taxonomy})


def record_followup(project_root: Path, *, work_id: str, outcome: str) -> dict[str, Any]:
    if outcome not in _OUTCOMES:
        raise SkillCohortError("followup_outcome_invalid")
    return _record(project_root, event_type="followup", work_id=work_id, payload={"outcome": outcome})


def pending_work_id(project_root: Path) -> str:
    root = _root(project_root)
    path = root / ".omc" / "state" / "pending-completion.json"
    _regular_file(path)
    try:
        pending = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkillCohortError("pending_work_invalid") from error
    current_head = omc_state._git_info(root).get("head")
    if not isinstance(pending, dict) or not omc_state._pending_completion_matches_session(
        root, pending, current_head=current_head
    ):
        raise SkillCohortError("pending_work_invalid")
    work_id = pending.get("work_id")
    if not isinstance(work_id, str) or _WORK_ID.fullmatch(work_id) is None:
        raise SkillCohortError("pending_work_invalid")
    return work_id


def record_pending_review(project_root: Path, *, verdict: str, taxonomy: str) -> dict[str, Any]:
    return record_review(project_root, work_id=pending_work_id(project_root), verdict=verdict, taxonomy=taxonomy)


def record_pending_followup(project_root: Path, *, outcome: str) -> dict[str, Any]:
    return record_followup(project_root, work_id=pending_work_id(project_root), outcome=outcome)


def _source_report(source: Path) -> dict[str, Any]:
    try:
        if not source.is_absolute() or source.is_symlink() or not source.is_dir():
            raise SkillCohortError("source_not_regular_directory")
        config, events = _load_events(source.resolve(strict=True))
        candidates = [event for event in events if event["event_type"] == "candidate"]
        candidate_work_ids = {event["work_id"] for event in candidates}
        candidate_keys = {(event["work_id"], event["skill_id"]) for event in candidates}
        expected_keys: set[tuple[str, str]] = set()
        enrolled_at = _timestamp(config["enrolled_at"], reason="config_invalid")
        enrollment_session_ids = set(config["enrollment_session_ids"])
        session_root = source / ".omc" / "state" / "sessions"
        if session_root.exists():
            if session_root.is_symlink() or not session_root.is_dir():
                raise SkillCohortError("session_state_invalid")
            for session_path in session_root.glob("*/session.json"):
                _regular_file(session_path)
                try:
                    session = json.loads(session_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise SkillCohortError("session_state_invalid") from error
                if (
                    isinstance(session, dict)
                    and session.get("title") in _SKILLS
                    and isinstance(session.get("work_id"), str)
                    and _WORK_ID.fullmatch(session["work_id"]) is not None
                    and isinstance(session.get("confirmation"), dict)
                    and session["confirmation"].get("status") == "confirmed"
                ):
                    if (
                        session_path.parent.name not in enrollment_session_ids
                        and _timestamp(session.get("created_at"), reason="session_state_invalid") >= enrolled_at
                    ):
                        expected_keys.add((session["work_id"], session["title"]))
        reviews = {event["work_id"]: event for event in events if event["event_type"] == "review"}
        followups = {event["work_id"]: event for event in events if event["event_type"] == "followup"}
        outcomes = {"accepted": 0, "correction": 0, "deferred": 0}
        taxonomy_counts: dict[str, int] = {}
        skill_counts: dict[str, int] = {}
        for candidate in candidates:
            skill_counts[candidate["skill_id"]] = skill_counts.get(candidate["skill_id"], 0) + 1
        for review in reviews.values():
            taxonomy = review["taxonomy"]
            taxonomy_counts[taxonomy] = taxonomy_counts.get(taxonomy, 0) + 1
        for followup in followups.values():
            outcomes[followup["outcome"]] += 1
        return {
            "state": "OBSERVED",
            "enrollment_id": config["enrollment_id"],
            "eligible_candidates": len(candidates),
            "eligible_work_items": len(candidate_work_ids),
            "unattributed_work_items": sum(
                sum(candidate["work_id"] == work_id for candidate in candidates) != 1
                for work_id in candidate_work_ids
            ),
            "capture_invalid": len(expected_keys - candidate_keys),
            "accepted": outcomes["accepted"],
            "correction": outcomes["correction"],
            "deferred": outcomes["deferred"],
            "followup_unobserved": len(candidate_work_ids - set(followups)),
            "taxonomy_counts": dict(sorted(taxonomy_counts.items())),
            "skill_counts": dict(sorted(skill_counts.items())),
        }
    except SkillCohortError as error:
        return {"state": "INTEGRITY_INVALID", "reason_code": str(error)}


def aggregate(sources: list[Path], *, now: str | None = None) -> dict[str, Any]:
    del now  # A missing follow-up remains unobserved; elapsed time never promotes it to success.
    if not sources:
        raise SkillCohortError("source_required")
    reports = [_source_report(source) for source in sources]
    observed = [report for report in reports if report["state"] == "OBSERVED"]
    aggregate_counts = {key: sum(int(report[key]) for report in observed) for key in ("eligible_candidates", "eligible_work_items", "unattributed_work_items", "capture_invalid", "accepted", "correction", "deferred", "followup_unobserved")}
    taxonomy_counts: dict[str, int] = {}
    skill_counts: dict[str, int] = {}
    for report in observed:
        for key, value in report["taxonomy_counts"].items():
            taxonomy_counts[key] = taxonomy_counts.get(key, 0) + int(value)
        for key, value in report["skill_counts"].items():
            skill_counts[key] = skill_counts.get(key, 0) + int(value)
    reason_codes = []
    if aggregate_counts["eligible_candidates"] < 30:
        reason_codes.append("eligible_count_below_30")
    if aggregate_counts["correction"] < 5:
        reason_codes.append("correction_count_below_5")
    if aggregate_counts["capture_invalid"]:
        reason_codes.append("candidate_capture_invalid")
    if len({report["enrollment_id"] for report in observed}) < 2:
        reason_codes.append("repository_count_below_2")
    readiness = {"state": "READY" if not reason_codes else "INSUFFICIENT_SAMPLE", "reason_codes": sorted(reason_codes)}
    return {
        "schema_version": SCHEMA_VERSION,
        "network_used": False,
        "sources": reports,
        "aggregate": {
            **aggregate_counts,
            "integrity_invalid": sum(report["state"] == "INTEGRITY_INVALID" for report in reports),
            "taxonomy_counts": dict(sorted(taxonomy_counts.items())),
            "skill_counts": dict(sorted(skill_counts.items())),
            "measurement_scope": "workflow_hypothesis_only",
            "tuning_readiness": readiness,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enable_cmd = sub.add_parser("enable")
    enable_cmd.add_argument("--target", type=Path, required=True)
    pending_review = sub.add_parser("record-pending-review")
    pending_review.add_argument("--target", type=Path, required=True)
    pending_review.add_argument("--verdict", required=True)
    pending_review.add_argument("--taxonomy", required=True)
    pending_followup = sub.add_parser("record-pending-followup")
    pending_followup.add_argument("--target", type=Path, required=True)
    pending_followup.add_argument("--outcome", required=True)
    report = sub.add_parser("report")
    report.add_argument("--source", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "enable":
            result = enable(args.target)
        elif args.command == "record-pending-review":
            result = record_pending_review(args.target, verdict=args.verdict, taxonomy=args.taxonomy)
        elif args.command == "record-pending-followup":
            result = record_pending_followup(args.target, outcome=args.outcome)
        else:
            result = aggregate(args.source)
    except SkillCohortError as error:
        print(json.dumps({"status": "blocked", "reason_code": str(error)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
