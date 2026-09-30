#!/usr/bin/env python3
"""Opt-in raw-free work-lifecycle observation with separate synthetic/operational enrollment.

Operational enrollment requires a write-once external two-target roster and fresh T0.
The work link is observational, not a human-approval authority claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import omc_review_snapshot
import omc_state
import omc_skill_effectiveness_cohort as legacy


CONFIG_NAME = "skill-effectiveness-cohort-v3.json"
LEDGER_NAME = "skill-effectiveness-cohort-v3.jsonl"
ACTIVE = "ACTIVE_NATURAL_OBSERVATION"
CLOSURE_NAME = "skill-effectiveness-cohort-v3-closure.json"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,127}")
_HEX = re.compile(r"[0-9a-f]{64}")
_SKILLS = {"omc-plan", "omc-task", "omc-review"}
_VERDICTS = {"APPROVE", "APPROVE_WITH_NOTES", "REVISE", "BLOCK", "NOT_RUN"}
_TAXONOMIES = {
    "requirement_gap", "verification_gap", "scope_gap", "output_confusion",
    "gate_friction", "environment_blocker", "external_dependency", "review_stale",
}
_OUTCOMES = {"accepted", "correction", "deferred"}
_COMMON_EVENT_KEYS = {
    "schema_version", "generation", "activation_id", "event_id", "event_type",
    "work_id", "observed_at", "previous_event_sha256", "event_sha256",
}
_EVENT_KEYS = {
    "candidate": {"session_id", "skill_id"},
    "review": {"session_id", "verdict", "taxonomy", "review_receipt_sha256", "work_link_class"},
    "choice": {"choice_id", "review_event_id"},
    "followup": {"choice_id", "review_event_id", "outcome", "source"},
}


class V3Error(ValueError):
    """The observation contract could not be established."""


def config_path(root: Path) -> Path:
    return root / ".omc" / CONFIG_NAME


def ledger_path(root: Path) -> Path:
    return root / ".omc" / LEDGER_NAME


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise V3Error("activation_at_invalid") from error
    if result.tzinfo is None:
        raise V3Error("activation_at_invalid")
    return result


def _write_once(path: Path, value: object) -> None:
    try:
        legacy._write_once(path, value)
    except legacy.SkillCohortError as error:
        raise V3Error(str(error)) from error


def _target(root: Path) -> str:
    try:
        return legacy._target_identity(root)
    except legacy.SkillCohortError as error:
        raise V3Error(str(error)) from error


def _installation(root: Path) -> dict[str, str]:
    import omc_install_audit
    path = root / ".omc/install-receipt.json"
    if path.is_symlink() or not path.is_file():
        raise V3Error("installation_invalid")
    receipt_bytes = path.read_bytes()
    try:
        receipt = json.loads(receipt_bytes)
    except (ValueError, UnicodeError) as error:
        raise V3Error("installation_invalid") from error
    if not isinstance(receipt, dict):
        raise V3Error("installation_invalid")
    digest = receipt.get("source_sha256")
    version = receipt.get("omc_version")
    if (not isinstance(digest, str) or _HEX.fullmatch(digest) is None
            or not isinstance(version, str) or not version):
        raise V3Error("installation_invalid")
    audit = omc_install_audit.audit_target(root, install_receipt_bytes=receipt_bytes)
    if audit.get("installed_integrity_status") != "ok":
        raise V3Error("installation_audit_failed")
    return {"source_sha256": digest, "source_version": version,
            "install_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest()}


def _validate_roster(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"generation", "activation_id", "activation_at", "targets"}:
        raise V3Error("roster_invalid")
    if (value["generation"] != "v3" or not isinstance(value["activation_id"], str)
            or _ID.fullmatch(value["activation_id"]) is None):
        raise V3Error("roster_invalid")
    _time(value["activation_at"])
    targets = value["targets"]
    if not isinstance(targets, list) or len(targets) != 2:
        raise V3Error("roster_requires_exactly_two_targets")
    identities = []
    for target in targets:
        if (not isinstance(target, dict) or set(target) != {
            "target_identity", "source_sha256", "source_version", "install_receipt_sha256"}
                or any(not isinstance(target.get(key), str) or _HEX.fullmatch(target[key]) is None
                       for key in ("target_identity", "source_sha256", "install_receipt_sha256"))
                or not isinstance(target.get("source_version"), str) or not target["source_version"]):
            raise V3Error("roster_invalid")
        identities.append(target["target_identity"])
    if identities != sorted(set(identities)):
        raise V3Error("roster_target_duplicate")
    return value


def create_roster(*, targets: list[Path], output: Path, activation_id: str,
                  activation_at: str) -> dict[str, Any]:
    if len(targets) != 2:
        raise V3Error("roster_requires_exactly_two_targets")
    roots = [legacy._root(root) for root in targets]
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise V3Error("roster_parent_invalid")
    if any(output.resolve().is_relative_to(root) for root in roots):
        raise V3Error("roster_must_be_external")
    if _time(activation_at) <= _now():
        raise V3Error("fresh_t0_required")
    roster = _validate_roster({"generation": "v3", "activation_id": activation_id,
        "activation_at": activation_at, "targets": sorted([
            {"target_identity": _target(root), **_installation(root)} for root in roots
        ], key=lambda item: item["target_identity"])})
    _write_once(output, roster)
    return {"status": "registered", "roster_sha256": _hash(roster), **roster}


def enroll(root: Path, *, roster_path: Path) -> dict[str, Any]:
    root = legacy._root(root)
    if roster_path.resolve().is_relative_to(root):
        raise V3Error("roster_must_be_external")
    roster = _validate_roster(_json_regular(roster_path, reason="roster_invalid"))
    identity = _target(root)
    matching = [item for item in roster["targets"] if item["target_identity"] == identity]
    if len(matching) != 1:
        raise V3Error("roster_target_not_allowed")
    with omc_state._omc_lock(root):
        path = config_path(root)
        if path.exists() or path.is_symlink():
            existing = _config(root)
            if existing.get("roster_sha256") != _hash(roster):
                raise V3Error("activation_conflict")
            return {"status": "unchanged", "activation_id": existing["activation_id"]}
        if _time(roster["activation_at"]) <= _now():
            raise V3Error("fresh_t0_required")
        if _installation(root) != {k: v for k, v in matching[0].items() if k != "target_identity"}:
            raise V3Error("installation_binding_mismatch")
        if ledger_path(root).exists() or ledger_path(root).is_symlink():
            raise V3Error("v3_ledger_already_exists")
        config = {"generation": "v3", "enabled": True, "status": ACTIVE,
            "activation_id": roster["activation_id"], "activation_at": roster["activation_at"],
            "roster": roster, "roster_sha256": _hash(roster), "target_identity": identity,
            "enrollment_session_ids": legacy._session_ids_at_enrollment(root)}
        _write_once(path, config)
        return {"status": "enrolled", "activation_id": config["activation_id"],
                "roster_sha256": config["roster_sha256"]}


def _closure(root: Path, config: dict[str, Any],
             events: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    path = root / ".omc" / CLOSURE_NAME
    if not path.exists() and not path.is_symlink():
        return None
    value = _json_regular(path, reason="v3_closure_invalid")
    if (set(value) != {"activation_id", "closed_at", "ledger_sha256", "event_count"}
            or value["activation_id"] != config["activation_id"]
            or not isinstance(value["ledger_sha256"], str)
            or _HEX.fullmatch(value["ledger_sha256"]) is None
            or type(value["event_count"]) is not int or value["event_count"] < 0
            or _time(value["closed_at"]) < _time(config["activation_at"])
            or _time(value["closed_at"]) > _now()):
        raise V3Error("v3_closure_invalid")
    if events is None:
        events = _events(root, activation_id=config["activation_id"])
    if value["ledger_sha256"] != _hash(events) or value["event_count"] != len(events):
        raise V3Error("v3_closure_invalid")
    return value


def close(root: Path) -> dict[str, Any]:
    with omc_state._omc_lock(root):
        config = _config(root)
        if config["status"] != ACTIVE or _now() < _time(config["activation_at"]):
            raise V3Error("v3_not_active")
        existing = _closure(root, config)
        if existing is not None:
            return {"status": "unchanged", **existing}
        events = _events(root, activation_id=config["activation_id"])
        receipt = {"activation_id": config["activation_id"], "closed_at": _now().isoformat(),
                   "ledger_sha256": _hash(events), "event_count": len(events)}
        _write_once(root / ".omc" / CLOSURE_NAME, receipt)
        return {"status": "closed", **receipt}


def _json_regular(path: Path, *, reason: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise V3Error(reason)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise V3Error(reason) from error
    if not isinstance(value, dict):
        raise V3Error(reason)
    return value


def _config(root: Path) -> dict[str, Any]:
    try:
        legacy._omc_dir(root, create=False)
    except legacy.SkillCohortError as error:
        raise V3Error(str(error)) from error
    value = _json_regular(config_path(root), reason="v3_config_invalid")
    basic = {"generation", "enabled", "status", "activation_id", "activation_at"}
    operational = basic | {"roster", "roster_sha256", "target_identity", "enrollment_session_ids"}
    if set(value) != (operational if value.get("status") == ACTIVE else basic):
        raise V3Error("v3_config_invalid")
    if value["generation"] != "v3" or value["status"] not in ("DRAFT_SYNTHETIC", ACTIVE) or type(value["enabled"]) is not bool:
        raise V3Error("v3_config_invalid")
    if not isinstance(value["activation_id"], str) or not value["activation_id"]:
        raise V3Error("v3_config_invalid")
    try:
        timestamp = datetime.fromisoformat(value["activation_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise V3Error("v3_config_invalid") from error
    if timestamp.tzinfo is None:
        raise V3Error("v3_config_invalid")
    if value["status"] == ACTIVE:
        roster = _validate_roster(value["roster"])
        if (value["roster_sha256"] != _hash(roster)
                or value["activation_id"] != roster["activation_id"]
                or value["activation_at"] != roster["activation_at"]
                or value["target_identity"] != _target(root)):
            raise V3Error("roster_binding_conflict")
        targets = [target for target in roster["targets"]
                   if target["target_identity"] == value["target_identity"]]
        if len(targets) != 1 or _installation(root) != {
            key: item for key, item in targets[0].items() if key != "target_identity"}:
            raise V3Error("installation_binding_mismatch")
        sessions = value["enrollment_session_ids"]
        if (not isinstance(sessions, list) or any(not isinstance(s, str) or _ID.fullmatch(s) is None for s in sessions)
                or sessions != sorted(set(sessions))):
            raise V3Error("v3_config_invalid")
    return value


def _session(root: Path, session_id: str) -> dict[str, Any]:
    if not isinstance(session_id, str) or _ID.fullmatch(session_id) is None:
        raise V3Error("session_id_invalid")
    path = root / ".omc" / "state" / "sessions" / session_id / "session.json"
    session = _json_regular(path, reason="session_invalid")
    confirmation = session.get("confirmation")
    if (
        session.get("session_id") != session_id
        or not isinstance(session.get("work_id"), str)
        or _ID.fullmatch(session["work_id"]) is None
        or session.get("title") not in _SKILLS
        or not isinstance(confirmation, dict)
        or confirmation.get("status") != "confirmed"
    ):
        raise V3Error("session_invalid")
    return session


def _validate_event_link(event: dict[str, Any], prior: list[dict[str, Any]]) -> None:
    """Replay the same lifecycle constraints used when appending events."""
    event_type = event["event_type"]
    work_id = event["work_id"]
    if any(old["event_id"] == event["event_id"] for old in prior):
        raise V3Error("v3_ledger_invalid")
    if event_type == "candidate":
        if any(old["event_type"] == "candidate" and old["session_id"] == event["session_id"] for old in prior):
            raise V3Error("v3_ledger_invalid")
    elif event_type == "review":
        if not any(old["event_type"] == "candidate" and old["session_id"] == event["session_id"]
                   and old["work_id"] == work_id and old["skill_id"] == "omc-review" for old in prior):
            raise V3Error("v3_ledger_invalid")
        if any(old["event_type"] == "followup" and old["work_id"] == work_id for old in prior):
            raise V3Error("v3_ledger_invalid")
    elif event_type == "choice":
        reviews = [old for old in prior if old["event_type"] == "review" and old["work_id"] == work_id]
        if not reviews or reviews[-1]["event_id"] != event["review_event_id"]:
            raise V3Error("v3_ledger_invalid")
        if any(old["event_type"] == "followup" and old["work_id"] == work_id for old in prior):
            raise V3Error("v3_ledger_invalid")
        if any(old["event_type"] == "choice" and (
            old["choice_id"] == event["choice_id"] or
            (old["work_id"] == work_id and old["review_event_id"] == event["review_event_id"])
        ) for old in prior):
            raise V3Error("v3_ledger_invalid")
    else:
        choices = [old for old in prior if old["event_type"] == "choice"
                   and old["choice_id"] == event["choice_id"]]
        reviews = [old for old in prior if old["event_type"] == "review" and old["work_id"] == work_id]
        if (len(choices) != 1 or choices[0]["work_id"] != work_id
                or choices[0]["review_event_id"] != event["review_event_id"]
                or not reviews or reviews[-1]["event_id"] != event["review_event_id"]
                or any(old["event_type"] == "followup" and old["choice_id"] == event["choice_id"] for old in prior)):
            raise V3Error("v3_ledger_invalid")


def _events(root: Path, *, activation_id: str) -> list[dict[str, Any]]:
    path = ledger_path(root)
    if not path.exists() and not path.is_symlink():
        return []
    if path.is_symlink() or not path.is_file():
        raise V3Error("v3_ledger_invalid")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise V3Error("v3_ledger_invalid") from error
    previous: str | None = None
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise V3Error("v3_ledger_invalid") from error
        if not isinstance(event, dict) or event.get("previous_event_sha256") != previous:
            raise V3Error("v3_ledger_invalid")
        digest = event.get("event_sha256")
        if (
            not isinstance(digest, str)
            or digest != _hash({key: item for key, item in event.items() if key != "event_sha256"})
            or event.get("generation") != "v3"
            or event.get("activation_id") != activation_id
            or not isinstance(event.get("event_type"), str)
            or event["event_type"] not in _EVENT_KEYS
        ):
            raise V3Error("v3_ledger_invalid")
        event_type = event["event_type"]
        if (
            set(event) != _COMMON_EVENT_KEYS | _EVENT_KEYS[event_type]
            or event.get("schema_version") != 1
            or not isinstance(event.get("event_id"), str)
            or _ID.fullmatch(event["event_id"]) is None
            or not isinstance(event.get("work_id"), str)
            or _ID.fullmatch(event["work_id"]) is None
            or not isinstance(event.get("observed_at"), str)
        ):
            raise V3Error("v3_ledger_invalid")
        try:
            observed = datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
        except ValueError as error:
            raise V3Error("v3_ledger_invalid") from error
        if observed.tzinfo is None:
            raise V3Error("v3_ledger_invalid")
        if event_type == "candidate":
            if (not isinstance(event["session_id"], str) or _ID.fullmatch(event["session_id"]) is None
                    or not isinstance(event["skill_id"], str) or event["skill_id"] not in _SKILLS):
                raise V3Error("v3_ledger_invalid")
        elif event_type == "review":
            if (
                not isinstance(event["session_id"], str)
                or _ID.fullmatch(event["session_id"]) is None
                or not isinstance(event["verdict"], str)
                or event["verdict"] not in _VERDICTS
                or not isinstance(event["taxonomy"], str)
                or event["taxonomy"] not in _TAXONOMIES
                or (event["taxonomy"] == "review_stale" and event["verdict"] != "BLOCK")
                or (event["verdict"] in {"APPROVE", "APPROVE_WITH_NOTES"} and event["review_receipt_sha256"] is None)
                or (event["verdict"] not in {"APPROVE", "APPROVE_WITH_NOTES"} and event["review_receipt_sha256"] is not None)
                or event["work_link_class"] != "asserted_work_link"
                or (event["review_receipt_sha256"] is not None and (
                    not isinstance(event["review_receipt_sha256"], str)
                    or _HEX.fullmatch(event["review_receipt_sha256"]) is None
                ))
            ):
                raise V3Error("v3_ledger_invalid")
        elif event_type == "followup":
            if not isinstance(event["outcome"], str) or event["outcome"] not in _OUTCOMES or event["source"] != "operator_reported_unverified":
                raise V3Error("v3_ledger_invalid")
        if event_type in {"choice", "followup"} and (
            not isinstance(event.get("choice_id"), str)
            or _ID.fullmatch(event["choice_id"]) is None
            or not isinstance(event.get("review_event_id"), str)
            or _ID.fullmatch(event["review_event_id"]) is None
        ):
            raise V3Error("v3_ledger_invalid")
        _validate_event_link(event, events)
        previous = digest
        events.append(event)
    return events


def _record(
    root: Path, *, event_type: str, work_id: str, payload: dict[str, Any],
    check: Callable[[list[dict[str, Any]]], None],
) -> dict[str, Any]:
    if not isinstance(work_id, str) or _ID.fullmatch(work_id) is None:
        raise V3Error("work_id_invalid")
    with omc_state._omc_lock(root):
        config = _config(root)
        if not config["enabled"]:
            raise V3Error("v3_disabled")
        if config["status"] == ACTIVE:
            if _closure(root, config) is not None:
                raise V3Error("v3_closed")
            if _now() < _time(config["activation_at"]):
                raise V3Error("v3_not_started")
        events = _events(root, activation_id=config["activation_id"])
        check(events)
        event = {
            "schema_version": 1, "generation": "v3",
            "activation_id": config["activation_id"], "event_id": uuid.uuid4().hex,
            "event_type": event_type, "work_id": work_id,
            "observed_at": _now().isoformat(),
            "previous_event_sha256": events[-1]["event_sha256"] if events else None,
            **payload,
        }
        event["event_sha256"] = _hash(event)
        path = ledger_path(root)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(_canonical(event) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


def _candidate_in_scope(root: Path, session: dict[str, Any], prior: list[dict[str, Any]],
                        config: dict[str, Any] | None = None) -> bool:
    config = config or _config(root)
    work_class = "implementation" if config["status"] == ACTIVE else "synthetic"
    if config["status"] == ACTIVE and (
        session["session_id"] in config["enrollment_session_ids"]
        or _time(session.get("created_at")) < _time(config["activation_at"])
        or _time(session.get("created_at")) > _now()
    ):
        return False
    if session.get("work_class") == work_class:
        return True
    return session.get("work_class") is None and session["title"] == "omc-review" and any(
        e["event_type"] == "candidate"
        and e["work_id"] == session["work_id"]
        and e["skill_id"] == "omc-task"
        and _session(root, e["session_id"]).get("work_class") == work_class
        for e in prior
    )


def record_candidate(root: Path, *, session_id: str) -> dict[str, Any]:
    session = _session(root, session_id)
    def check(events: list[dict[str, Any]]) -> None:
        if any(e["event_type"] == "candidate" and e.get("session_id") == session_id for e in events):
            raise V3Error("candidate_duplicate")
        if not _candidate_in_scope(root, session, events):
            raise V3Error("candidate_out_of_scope")
    return _record(root, event_type="candidate", work_id=session["work_id"],
                   payload={"session_id": session_id, "skill_id": session["title"]}, check=check)


def record_review(
    root: Path, *, session_id: str, work_id: str, verdict: str,
    taxonomy: str, review_receipt_sha256: str | None = None,
    review_receipt_path: Path | None = None,
) -> dict[str, Any]:
    session = _session(root, session_id)
    if session["title"] != "omc-review" or session["work_id"] != work_id:
        raise V3Error("work_link_mismatch")
    if verdict not in _VERDICTS or taxonomy not in _TAXONOMIES:
        raise V3Error("review_input_invalid")
    if taxonomy == "review_stale" and verdict != "BLOCK":
        raise V3Error("review_stale_verdict_invalid")
    if verdict in {"APPROVE", "APPROVE_WITH_NOTES"}:
        if (review_receipt_sha256 is None or _HEX.fullmatch(review_receipt_sha256) is None
                or review_receipt_path is None):
            raise V3Error("review_receipt_invalid")
        try:
            receipt = omc_review_snapshot.load_review_receipt(root, review_receipt_path)
        except (omc_review_snapshot.CandidateScopeError, OSError) as error:
            raise V3Error("review_receipt_invalid") from error
        if receipt["receipt_sha256"] != review_receipt_sha256 or receipt["review_verdict"] != verdict:
            raise V3Error("review_receipt_invalid")
    elif review_receipt_sha256 is not None or review_receipt_path is not None:
        raise V3Error("review_receipt_unexpected")
    def check(events: list[dict[str, Any]]) -> None:
        if not any(e["event_type"] == "candidate" and e["session_id"] == session_id for e in events):
            raise V3Error("review_candidate_required")
        if any(e["event_type"] == "review" and e["session_id"] == session_id for e in events):
            raise V3Error("review_session_already_recorded")
        if any(e["event_type"] == "followup" and e["work_id"] == work_id for e in events):
            raise V3Error("followup_finalized")
    return _record(root, event_type="review", work_id=work_id,
                   payload={"session_id": session_id, "verdict": verdict, "taxonomy": taxonomy,
                            "review_receipt_sha256": review_receipt_sha256,
                            "work_link_class": "asserted_work_link"}, check=check)


def record_completed_review(
    root: Path, *, session_id: str, taxonomy: str, verdict: str,
    review_receipt_path: Path, review_receipt_sha256: str,
) -> dict[str, Any]:
    """Connect the receipt just issued for this explicit review session."""
    session = _session(root, session_id)
    if session["title"] != "omc-review":
        raise V3Error("work_link_mismatch")
    latest = omc_state._read_json(root / ".omc" / "state" / "latest.json", {})
    if latest.get("latest_confirmed_session_id") != session_id:
        raise V3Error("review_session_not_current")
    return _record_review_with_choice(
        root, session_id=session_id, work_id=session["work_id"], verdict=verdict,
        taxonomy=taxonomy, review_receipt_path=review_receipt_path,
        review_receipt_sha256=review_receipt_sha256,
    )


def record_explicit_nonapproval_review(
    root: Path, *, session_id: str, work_id: str, verdict: str, taxonomy: str,
) -> dict[str, Any]:
    if verdict not in {"REVISE", "BLOCK"}:
        raise V3Error("review_input_invalid")
    session = _session(root, session_id)
    latest = omc_state._read_json(root / ".omc" / "state" / "latest.json", {})
    if latest.get("latest_confirmed_session_id") != session_id or session["work_id"] != work_id:
        raise V3Error("review_session_not_current")
    return _record_review_with_choice(
        root, session_id=session_id, work_id=work_id, verdict=verdict, taxonomy=taxonomy,
    )


def _record_review_with_choice(
    root: Path, *, session_id: str, work_id: str, verdict: str, taxonomy: str,
    review_receipt_path: Path | None = None, review_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        review = record_review(
            root, session_id=session_id, work_id=work_id, verdict=verdict,
            taxonomy=taxonomy, review_receipt_path=review_receipt_path,
            review_receipt_sha256=review_receipt_sha256,
        )
    except V3Error as error:
        if str(error) != "review_session_already_recorded":
            raise
        events = _events(root, activation_id=_config(root)["activation_id"])
        matching = [event for event in events if event["event_type"] == "review"
                    and event["session_id"] == session_id]
        if (len(matching) != 1 or matching[0]["work_id"] != work_id
                or matching[0]["verdict"] != verdict
                or matching[0]["taxonomy"] != taxonomy
                or matching[0]["review_receipt_sha256"] != review_receipt_sha256
                or any(event["event_type"] == "choice"
                       and event["review_event_id"] == matching[0]["event_id"]
                       for event in events)):
            raise error
        review = matching[0]
    choice = create_choice(root, work_id=work_id, review_event_id=review["event_id"])
    return {"review_event_id": review["event_id"], **choice}


def resume_review_choice(root: Path, *, session_id: str) -> dict[str, Any]:
    """Recover a missing choice for a recorded review without adding another review."""
    session = _session(root, session_id)
    if session["title"] != "omc-review":
        raise V3Error("work_link_mismatch")
    latest = omc_state._read_json(root / ".omc" / "state" / "latest.json", {})
    if latest.get("latest_confirmed_session_id") != session_id:
        raise V3Error("review_session_not_current")
    events = _events(root, activation_id=_config(root)["activation_id"])
    matching = [event for event in events if event["event_type"] == "review"
                and event["session_id"] == session_id and event["work_id"] == session["work_id"]]
    if len(matching) != 1:
        raise V3Error("review_not_recorded")
    review = matching[0]
    if any(event["event_type"] == "choice" and event["review_event_id"] == review["event_id"]
           for event in events):
        raise V3Error("choice_duplicate")
    choice = create_choice(root, work_id=session["work_id"], review_event_id=review["event_id"])
    return {"review_event_id": review["event_id"], **choice}


def create_choice(root: Path, *, work_id: str, review_event_id: str) -> dict[str, Any]:
    choice_id = uuid.uuid4().hex
    def check(events: list[dict[str, Any]]) -> None:
        reviews = [e for e in events if e["event_type"] == "review" and e["work_id"] == work_id]
        if not reviews or reviews[-1]["event_id"] != review_event_id:
            raise V3Error("review_not_latest")
        if any(e["event_type"] == "followup" and e["work_id"] == work_id for e in events):
            raise V3Error("followup_finalized")
        if any(e["event_type"] == "choice" and e["work_id"] == work_id and e["review_event_id"] == review_event_id for e in events):
            raise V3Error("choice_duplicate")
    _record(root, event_type="choice", work_id=work_id,
            payload={"choice_id": choice_id, "review_event_id": review_event_id}, check=check)
    return {"choice_id": choice_id, "work_id": work_id, "review_event_id": review_event_id}


def record_followup(root: Path, *, choice_id: str, outcome: str) -> dict[str, Any]:
    if outcome not in _OUTCOMES or not isinstance(choice_id, str) or _ID.fullmatch(choice_id) is None:
        raise V3Error("followup_input_invalid")
    config = _config(root)
    events = _events(root, activation_id=config["activation_id"])
    choices = [e for e in events if e["event_type"] == "choice" and e["choice_id"] == choice_id]
    if len(choices) != 1:
        raise V3Error("choice_invalid")
    choice = choices[0]
    work_id = choice["work_id"]
    def check(current: list[dict[str, Any]]) -> None:
        if any(e["event_type"] == "followup" and e["choice_id"] == choice_id for e in current):
            raise V3Error("choice_consumed")
        reviews = [e for e in current if e["event_type"] == "review" and e["work_id"] == work_id]
        if not reviews or reviews[-1]["event_id"] != choice["review_event_id"]:
            raise V3Error("review_not_latest")
    return _record(root, event_type="followup", work_id=work_id,
                   payload={"choice_id": choice_id, "review_event_id": choice["review_event_id"],
                            "outcome": outcome, "source": "operator_reported_unverified"}, check=check)


def report(root: Path) -> dict[str, Any]:
    if not config_path(root).exists() and not config_path(root).is_symlink():
        return {"generation": "v3", "state": "DISABLED"}
    with omc_state._omc_lock(root):
        return _report_locked(root)


def _report_locked(root: Path) -> dict[str, Any]:
    """Count and validate one ledger snapshot while append/close are excluded."""
    config = _config(root)
    if not config["enabled"]:
        return {"generation": "v3", "state": "DISABLED"}
    events = _events(root, activation_id=config["activation_id"])
    closure = _closure(root, config, events) if config["status"] == ACTIVE else None
    if config["status"] == ACTIVE and any(
        _time(event["observed_at"]) < _time(config["activation_at"])
        or _time(event["observed_at"]) > _now()
        or (closure is not None and _time(event["observed_at"]) > _time(closure["closed_at"]))
        for event in events
    ):
        raise V3Error("event_out_of_window")
    prior_candidates: list[dict[str, Any]] = []
    for event in events:
        if event["event_type"] != "candidate":
            continue
        session = _session(root, event["session_id"])
        if not _candidate_in_scope(root, session, prior_candidates, config):
            raise V3Error("candidate_out_of_scope")
        prior_candidates.append(event)
    candidates = [e for e in events if e["event_type"] == "candidate"]
    reviews = [e for e in events if e["event_type"] == "review"]
    followups = [e for e in events if e["event_type"] == "followup"]
    works = {e["work_id"] for e in candidates}
    candidate_session_ids = {e["session_id"] for e in candidates}
    capture_statuses: dict[str, str] = {}
    failure_reason_counts: dict[str, int] = {}
    eligible_session_ids = set(candidate_session_ids)
    activation_at = datetime.fromisoformat(config["activation_at"].replace("Z", "+00:00"))
    sessions_dir = root / ".omc" / "state" / "sessions"
    if sessions_dir.is_symlink():
        raise V3Error("v3_session_capture_invalid")
    if sessions_dir.exists():
        for path in sessions_dir.glob("*/session.json"):
            if path.parent.is_symlink():
                raise V3Error("v3_session_capture_invalid")
            session = _json_regular(path, reason="v3_session_capture_invalid")
            session_id = path.parent.name
            if _ID.fullmatch(session_id) is None or session.get("session_id") != session_id:
                raise V3Error("v3_session_capture_invalid")
            if session.get("title") not in _SKILLS:
                continue
            confirmation = session.get("confirmation")
            if not isinstance(confirmation, dict) or confirmation.get("status") != "confirmed":
                continue
            try:
                created_at = datetime.fromisoformat(session["created_at"].replace("Z", "+00:00"))
            except (KeyError, AttributeError, ValueError) as error:
                raise V3Error("v3_session_capture_invalid") from error
            if created_at.tzinfo is None:
                raise V3Error("v3_session_capture_invalid")
            if created_at < activation_at:
                continue
            if not _candidate_in_scope(root, session, candidates, config):
                continue
            if closure is not None and created_at > _time(closure["closed_at"]):
                continue
            eligible_session_ids.add(session_id)
            capture = session.get("cohort_capture_v3")
            if capture is None:
                continue
            if not isinstance(capture, dict) or "activation_id" not in capture:
                raise V3Error("v3_session_capture_invalid")
            if capture.get("activation_id") != config["activation_id"]:
                continue
            status = capture.get("status")
            if status == "recorded" and set(capture) == {"status", "activation_id"} and session_id in candidate_session_ids:
                capture_statuses[session_id] = status
            elif (status == "integrity_invalid" and set(capture) == {"status", "reason_code", "activation_id"}
                  and isinstance(capture["reason_code"], str)
                  and _ID.fullmatch(capture["reason_code"]) is not None
                  and session_id not in candidate_session_ids):
                capture_statuses[session_id] = status
                reason = capture["reason_code"]
                failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + 1
            else:
                raise V3Error("v3_session_capture_invalid")
    return {
        "generation": "v3", "state": (
            "CLOSED" if closure is not None else
            "REGISTERED_NOT_STARTED" if config["status"] == ACTIVE and _now() < activation_at else config["status"]),
        "activation_id": config["activation_id"],
        **({"roster_sha256": config["roster_sha256"], "target_identity": config["target_identity"]}
           if config["status"] == ACTIVE else {}),
        "measurement_scope": "descriptive_work_lifecycle_only",
        "work_link_class": "asserted_work_link",
        "work_items": len(works), "skill_exposures": len(candidates),
        "review_count": len(reviews),
        "review_churn_work_items": sum(sum(e["work_id"] == work for e in reviews) > 1 for work in works),
        "correction_after_approved_review": sum(
            any(review["event_id"] == followup["review_event_id"]
                and review["verdict"] in {"APPROVE", "APPROVE_WITH_NOTES"} for review in reviews)
            for followup in followups if followup["outcome"] == "correction"
        ),
        "review_unobserved": len(works - {e["work_id"] for e in reviews}),
        "review_stale_count": sum(e["taxonomy"] == "review_stale" for e in reviews),
        "outcome_unobserved": len(works - {e["work_id"] for e in followups}),
        "capture_sessions": {
            "recorded": sum(status == "recorded" for status in capture_statuses.values()),
            "integrity_invalid": sum(status == "integrity_invalid" for status in capture_statuses.values()),
            "status_unobserved": len(eligible_session_ids - capture_statuses.keys()),
            "failure_reason_counts": dict(sorted(failure_reason_counts.items())),
        },
        **{outcome: sum(e["outcome"] == outcome for e in followups) for outcome in sorted(_OUTCOMES)},
    }


def prefers_v3(root: Path) -> bool:
    """Operational config takes precedence even when invalid or closed; never fall back."""
    path = config_path(root)
    if not path.exists() and not path.is_symlink():
        return False
    value = _json_regular(path, reason="v3_config_invalid")
    return value.get("status") != "DRAFT_SYNTHETIC"


def aggregate(roots: list[Path], *, roster_path: Path) -> dict[str, Any]:
    roster = _validate_roster(_json_regular(roster_path, reason="roster_invalid"))
    if len(roots) != 2 or len({root.resolve() for root in roots}) != 2:
        raise V3Error("pilot_roster_incomplete")
    reports = [report(root) for root in roots]
    if (any(item.get("roster_sha256") != _hash(roster) for item in reports)
            or sorted(item.get("target_identity", "") for item in reports) != [t["target_identity"] for t in roster["targets"]]):
        raise V3Error("roster_binding_conflict")
    keys = ("work_items", "skill_exposures", "review_count", "review_unobserved",
            "review_churn_work_items", "review_stale_count", "correction_after_approved_review",
            "outcome_unobserved", "accepted", "correction", "deferred")
    return {"generation": "v3", "sources": reports,
            "aggregate": {**{key: sum(item[key] for item in reports) for key in keys},
                          "measurement_scope": "descriptive_work_lifecycle_only",
                          "product_effect": "NOT_PROVEN"}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("report")
    cmd.add_argument("--target", required=True, type=Path)
    roster_cmd = sub.add_parser("create-roster")
    roster_cmd.add_argument("--target", required=True, type=Path, action="append")
    roster_cmd.add_argument("--output", required=True, type=Path)
    roster_cmd.add_argument("--activation-id", required=True)
    roster_cmd.add_argument("--activation-at", required=True)
    enroll_cmd = sub.add_parser("enroll")
    enroll_cmd.add_argument("--target", required=True, type=Path)
    enroll_cmd.add_argument("--roster", required=True, type=Path)
    close_cmd = sub.add_parser("close")
    close_cmd.add_argument("--target", required=True, type=Path)
    aggregate_cmd = sub.add_parser("aggregate")
    aggregate_cmd.add_argument("--source", required=True, type=Path, action="append")
    aggregate_cmd.add_argument("--roster", required=True, type=Path)
    candidate_cmd = sub.add_parser("fixture-candidate")
    candidate_cmd.add_argument("--target", required=True, type=Path)
    candidate_cmd.add_argument("--session-id", required=True)
    review_cmd = sub.add_parser("fixture-review")
    review_cmd.add_argument("--target", required=True, type=Path)
    review_cmd.add_argument("--session-id", required=True)
    review_cmd.add_argument("--work-id", required=True)
    review_cmd.add_argument("--verdict", required=True)
    review_cmd.add_argument("--taxonomy", required=True)
    review_cmd.add_argument("--review-receipt-sha256")
    review_cmd.add_argument("--review-receipt-path", type=Path)
    explicit_review = sub.add_parser("record-explicit-review")
    explicit_review.add_argument("--target", required=True, type=Path)
    explicit_review.add_argument("--session-id", required=True)
    explicit_review.add_argument("--work-id", required=True)
    explicit_review.add_argument("--verdict", required=True, choices=["REVISE", "BLOCK"])
    explicit_review.add_argument("--taxonomy", required=True, choices=sorted(_TAXONOMIES))
    resume_choice = sub.add_parser("resume-review-choice")
    resume_choice.add_argument("--target", required=True, type=Path)
    resume_choice.add_argument("--session-id", required=True)
    choice_cmd = sub.add_parser("fixture-choice")
    choice_cmd.add_argument("--target", required=True, type=Path)
    choice_cmd.add_argument("--work-id", required=True)
    choice_cmd.add_argument("--review-event-id", required=True)
    followup_cmd = sub.add_parser("fixture-followup")
    followup_cmd.add_argument("--target", required=True, type=Path)
    followup_cmd.add_argument("--choice-id", required=True)
    followup_cmd.add_argument("--outcome", required=True)
    explicit_followup = sub.add_parser("record-explicit-followup")
    explicit_followup.add_argument("--target", required=True, type=Path)
    explicit_followup.add_argument("--choice-id", required=True)
    explicit_followup.add_argument("--outcome", required=True, choices=sorted(_OUTCOMES))
    args = parser.parse_args(argv)
    try:
        if args.command == "create-roster":
            result = create_roster(targets=args.target, output=args.output,
                activation_id=args.activation_id, activation_at=args.activation_at)
        elif args.command == "enroll":
            result = enroll(args.target, roster_path=args.roster)
        elif args.command == "close":
            result = close(args.target)
        elif args.command == "aggregate":
            result = aggregate(args.source, roster_path=args.roster)
        elif args.command == "report":
            result = report(args.target)
        elif args.command == "fixture-candidate":
            result = record_candidate(args.target, session_id=args.session_id)
        elif args.command == "fixture-choice":
            result = create_choice(args.target, work_id=args.work_id, review_event_id=args.review_event_id)
        elif args.command == "record-explicit-review":
            result = record_explicit_nonapproval_review(
                args.target, session_id=args.session_id, work_id=args.work_id,
                verdict=args.verdict, taxonomy=args.taxonomy,
            )
        elif args.command == "resume-review-choice":
            result = resume_review_choice(args.target, session_id=args.session_id)
        elif args.command in {"fixture-followup", "record-explicit-followup"}:
            result = record_followup(args.target, choice_id=args.choice_id, outcome=args.outcome)
        else:
            result = record_review(
                args.target, session_id=args.session_id, work_id=args.work_id,
                verdict=args.verdict, taxonomy=args.taxonomy,
                review_receipt_sha256=getattr(args, "review_receipt_sha256", None),
                review_receipt_path=getattr(args, "review_receipt_path", None),
            )
    except (V3Error, legacy.SkillCohortError) as error:
        result = {"generation": "v3", "state": "INTEGRITY_INVALID", "reason_code": str(error)}
    except OSError:
        result = {"generation": "v3", "state": "CAPTURE_FAILED", "reason_code": "capture_io_error"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("state") in {"INTEGRITY_INVALID", "CAPTURE_FAILED"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
