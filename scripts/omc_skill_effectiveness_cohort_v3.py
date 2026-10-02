#!/usr/bin/env python3
"""Opt-in raw-free work-lifecycle observation with separate synthetic/operational enrollment.

Operational enrollment requires a write-once external two-target roster and fresh T0.
The work link is observational, not a human-approval authority claim.
"""
from __future__ import annotations

import argparse
import base64
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
TRANSITION_NAME = "skill-effectiveness-cohort-v3-transition.json"
ACTIVATION_NAME = "skill-effectiveness-cohort-v3-activation.json"
JOURNAL_NAME = "skill-effectiveness-cohort-v3-transition-journal.json"
_TRANSITION_BARRIER = {"generation": "v3", "status": "TRANSITION_BLOCKED"}
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
        marker = _transition(root)
        barrier = (marker is not None and path.exists() and
                   _json_regular(path, reason="transition_invalid") == _TRANSITION_BARRIER)
        if (path.exists() or path.is_symlink()) and not barrier:
            existing = _config(root, check_transition=False)
            if existing.get("roster_sha256") != _hash(roster):
                raise V3Error("activation_conflict")
            return {"status": "unchanged", "activation_id": existing["activation_id"]}
        if _time(roster["activation_at"]) <= _now():
            raise V3Error("fresh_t0_required")
        marker = _transition(root)
        if marker is not None:
            prepared = root / ".omc/cohort-transition-previous/prepared.json"
            if (_json_regular(prepared, reason="transition_not_prepared")
                    != {"transition_sha256": _hash(marker)}):
                raise V3Error("transition_not_prepared")
        if marker is not None and roster["activation_id"] == marker["previous_activation"]:
            raise V3Error("activation_reuse_forbidden")
        if _installation(root) != {k: v for k, v in matching[0].items() if k != "target_identity"}:
            raise V3Error("installation_binding_mismatch")
        if ledger_path(root).exists() or ledger_path(root).is_symlink():
            raise V3Error("v3_ledger_already_exists")
        config = {"generation": "v3", "enabled": True, "status": ACTIVE,
            "activation_id": roster["activation_id"], "activation_at": roster["activation_at"],
            "roster": roster, "roster_sha256": _hash(roster), "target_identity": identity,
            "enrollment_session_ids": legacy._session_ids_at_enrollment(root)}
        if marker is not None:
            work_ids = set(marker["previous_work_ids"])
            for sid in config["enrollment_session_ids"]:
                session = _json_regular(root / ".omc/state/sessions" / sid / "session.json", reason="session_invalid")
                work = session.get("work_id")
                if work is not None:
                    if not isinstance(work, str) or _ID.fullmatch(work) is None:
                        raise V3Error("session_invalid")
                    work_ids.add(work)
            config["excluded_work_ids"] = sorted(work_ids)
        if barrier:
            _replace_transition_config(path, config)
        else:
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


_ARCHIVE_SESSION_FIELDS = {
    "session_id", "work_id", "title", "created_at", "work_class",
    "completion_action", "lineage_root_session_id", "lineage_previous_session_id",
    "lineage_index", "cohort_capture_v3",
}


def archive_closed(root: Path, *, output: Path) -> dict[str, Any]:
    """Preserve a raw-free projection; never move or delete live evidence."""
    root = legacy._root(root)
    if output.resolve().is_relative_to(root) or output.parent.is_symlink():
        raise V3Error("archive_must_be_external")
    with omc_state._omc_lock(root):
        config = _config(root)
        events = _events(root, activation_id=config["activation_id"])
        closure = _closure(root, config, events)
        if closure is None:
            raise V3Error("archive_requires_closed")
        projection = _report_locked(root)
        sessions = []
        directory = root / ".omc/state/sessions"
        if directory.is_symlink():
            raise V3Error("archive_session_invalid")
        for path in sorted(directory.glob("*/session.json")):
            if path.parent.is_symlink():
                raise V3Error("archive_session_invalid")
            session = _json_regular(path, reason="archive_session_invalid")
            if session.get("title") not in _SKILLS:
                continue
            item = {key: session[key] for key in _ARCHIVE_SESSION_FIELDS if key in session}
            item["confirmed"] = session.get("confirmation", {}).get("status") == "confirmed"
            sessions.append(item)
        bundle = {
            "schema": "omc-workflow-archive/v1", "config": config,
            "events": events, "closure": closure, "sessions": sessions,
            "projection": projection,
            "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "claim_boundary": "raw_free_projection_not_full_original_evidence",
        }
        # Pin exactly the declared raw-free fields, not arbitrary nested metadata.
        for item in sessions:
            _validate_archive_session(item)
        bundle["bundle_sha256"] = _hash(bundle)
        if output.exists() or output.is_symlink():
            existing = _json_regular(output, reason="archive_invalid")
            if existing != bundle:
                raise V3Error("archive_conflict")
        else:
            _durable_write_once(output, bundle)
        return verify_archive(output)


def verify_archive(path: Path) -> dict[str, Any]:
    """Verify preserved bytes only; never read a live installation or session."""
    value = _json_regular(path, reason="archive_invalid")
    required = {"schema", "config", "events", "closure", "sessions", "projection",
                "verifier_sha256", "claim_boundary", "bundle_sha256"}
    if (set(value) != required or value["schema"] != "omc-workflow-archive/v1"
            or value["claim_boundary"] != "raw_free_projection_not_full_original_evidence"):
        raise V3Error("archive_invalid")
    body = {key: item for key, item in value.items() if key != "bundle_sha256"}
    if _hash(body) != value["bundle_sha256"]:
        raise V3Error("archive_hash_mismatch")
    events, config, closure = value["events"], value["config"], value["closure"]
    if (not isinstance(events, list) or not isinstance(config, dict)
            or not isinstance(closure, dict) or not isinstance(value["sessions"], list)):
        raise V3Error("archive_invalid")
    roster = _validate_roster(config.get("roster", {}))
    if (config.get("roster_sha256") != _hash(roster)
            or config.get("activation_id") != roster["activation_id"]
            or config.get("activation_at") != roster["activation_at"]
            or config.get("target_identity") not in {t["target_identity"] for t in roster["targets"]}):
        raise V3Error("archive_roster_invalid")
    prior = []
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") not in _EVENT_KEYS:
            raise V3Error("archive_event_invalid")
        if set(event) != _COMMON_EVENT_KEYS | _EVENT_KEYS[event["event_type"]]:
            raise V3Error("archive_event_invalid")
        if (event["schema_version"] != 1 or event["generation"] != "v3"
                or any(not isinstance(event[k], str) or _ID.fullmatch(event[k]) is None
                       for k in ("event_id", "work_id"))):
            raise V3Error("archive_event_invalid")
        if event["event_type"] in {"candidate", "review"}:
            if not isinstance(event["session_id"], str) or _ID.fullmatch(event["session_id"]) is None:
                raise V3Error("archive_event_invalid")
        if event["event_type"] == "candidate" and event["skill_id"] not in _SKILLS:
            raise V3Error("archive_event_invalid")
        if event["event_type"] == "review":
            if (event["verdict"] not in _VERDICTS or event["taxonomy"] not in _TAXONOMIES
                    or (event["taxonomy"] == "review_stale" and event["verdict"] != "BLOCK")
                    or event["work_link_class"] != "asserted_work_link"
                    or ((event["verdict"] in {"APPROVE", "APPROVE_WITH_NOTES"}) != (event["review_receipt_sha256"] is not None))
                    or (event["review_receipt_sha256"] is not None and (
                        not isinstance(event["review_receipt_sha256"], str) or _HEX.fullmatch(event["review_receipt_sha256"]) is None))):
                raise V3Error("archive_event_invalid")
        if event["event_type"] == "followup" and (event["outcome"] not in _OUTCOMES or event["source"] != "operator_reported_unverified"):
            raise V3Error("archive_event_invalid")
        if (event.get("activation_id") != config.get("activation_id")
                or event.get("previous_event_sha256") != (prior[-1]["event_sha256"] if prior else None)
                or event.get("event_sha256") != _hash({k: v for k, v in event.items() if k != "event_sha256"})):
            raise V3Error("archive_event_invalid")
        _validate_event_link(event, prior)
        prior.append(event)
    if (closure.get("activation_id") != config.get("activation_id")
            or closure.get("event_count") != len(events) or closure.get("ledger_sha256") != _hash(events)):
        raise V3Error("archive_closure_invalid")
    for session in value["sessions"]:
        _validate_archive_session(session)
    by_id = {s.get("session_id"): s for s in value["sessions"]}
    if len(by_id) != len(value["sessions"]):
        raise V3Error("archive_session_invalid")
    projection = value["projection"]
    candidates = [e for e in events if e["event_type"] == "candidate"]
    reviews = [e for e in events if e["event_type"] == "review"]
    follows = [e for e in events if e["event_type"] == "followup"]
    works = {e["work_id"] for e in candidates}
    for event in candidates:
        session = by_id.get(event["session_id"])
        if (session is None or not session["confirmed"] or session.get("work_id") != event["work_id"]
                or session.get("title") != event["skill_id"]):
            raise V3Error("archive_session_link_invalid")
    start, end = _time(config.get("activation_at")), _time(closure.get("closed_at"))
    if end < start:
        raise V3Error("archive_closure_invalid")
    if any(_time(e.get("observed_at")) < start or _time(e.get("observed_at")) > end for e in events):
        raise V3Error("archive_event_invalid")
    eligible = {e["session_id"] for e in candidates}
    captures, failures = {}, {}
    for session in value["sessions"]:
        sid = session.get("session_id")
        if (not session["confirmed"] or session.get("title") not in _SKILLS
                or sid in config["enrollment_session_ids"]
                or _time(session.get("created_at")) < start or _time(session.get("created_at")) > end):
            continue
        in_scope = session.get("work_class") == "implementation" or (
            session.get("work_class") is None and session.get("title") == "omc-review"
            and any(e["work_id"] == session.get("work_id") and e["skill_id"] == "omc-task"
                    for e in candidates))
        if not in_scope:
            continue
        eligible.add(sid)
        capture = session.get("cohort_capture_v3")
        if capture is None or capture.get("activation_id") != config["activation_id"]:
            continue
        status = capture.get("status")
        if status == "recorded" and sid in {e["session_id"] for e in candidates}:
            captures[sid] = status
        elif status == "integrity_invalid" and sid not in {e["session_id"] for e in candidates}:
            captures[sid] = status
            reason = capture.get("reason_code")
            failures[reason] = failures.get(reason, 0) + 1
        else:
            raise V3Error("archive_capture_invalid")
    computed = {
        "review_unobserved": len(works - {e["work_id"] for e in reviews}),
        "outcome_unobserved": _outcome_unobserved(works, reviews, follows),
        "review_stale_count": sum(e["taxonomy"] == "review_stale" for e in reviews),
        "review_churn_work_items": sum(sum(e["work_id"] == w for e in reviews) > 1 for w in works),
        "correction_after_approved_review": sum(
            any(r["event_id"] == f["review_event_id"] and r["verdict"] in {"APPROVE", "APPROVE_WITH_NOTES"}
                for r in reviews) for f in follows if f["outcome"] == "correction"),
        **{o: sum(e["outcome"] == o for e in follows) for o in _OUTCOMES},
        "capture_sessions": {"recorded": sum(s == "recorded" for s in captures.values()),
                             "integrity_invalid": sum(s == "integrity_invalid" for s in captures.values()),
                             "status_unobserved": len(eligible - captures.keys()),
                             "failure_reason_counts": dict(sorted(failures.items()))},
    }
    if (not isinstance(projection, dict)
            or projection.get("work_items") != len({e["work_id"] for e in candidates})
            or projection.get("skill_exposures") != len(candidates)
            or projection.get("review_count") != len(reviews)
            or any(projection.get(k) != v for k, v in computed.items())):
        raise V3Error("archive_projection_invalid")
    return {"state": "ARCHIVE_VERIFIED", "bundle_sha256": value["bundle_sha256"],
            "activation_id": config["activation_id"], "event_count": len(events),
            "claim_boundary": value["claim_boundary"]}


def _validate_archive_session(session: object) -> None:
    if (not isinstance(session, dict) or set(session) - (_ARCHIVE_SESSION_FIELDS | {"confirmed"})
            or type(session.get("confirmed")) is not bool):
        raise V3Error("archive_session_invalid")
    for key, value in session.items():
        if key == "confirmed":
            continue
        if key == "cohort_capture_v3":
            # Capture can fail before installation binding resolves an activation.
            # Preserve that failure verbatim, without attributing it to a cohort.
            unbound_failure = (
                isinstance(value, dict)
                and set(value) == {"status", "activation_id", "reason_code"}
                and value["status"] == "integrity_invalid"
                and value["activation_id"] is None
                and isinstance(value["reason_code"], str)
                and _ID.fullmatch(value["reason_code"]) is not None
            )
            if unbound_failure:
                continue
            if (not isinstance(value, dict)
                    or set(value) - {"status", "activation_id", "reason_code"}
                    or any(not isinstance(v, str) or _ID.fullmatch(v) is None for v in value.values())):
                raise V3Error("archive_session_invalid")
        elif key == "lineage_index":
            if value is not None and (type(value) is not int or value < 0):
                raise V3Error("archive_session_invalid")
        elif value is not None and not isinstance(value, str):
            raise V3Error("archive_session_invalid")
        elif value is not None:
            allowed = {"title": _SKILLS, "work_class": {"implementation", "synthetic", "document_only", "benchmark_maintenance"},
                       "completion_action": {"start", "continue", "preserve", "preserve-if-present"}}
            if key in allowed and value not in allowed[key]:
                raise V3Error("archive_session_invalid")
            if key in {"session_id", "work_id", "lineage_root_session_id", "lineage_previous_session_id"} and _ID.fullmatch(value) is None:
                raise V3Error("archive_session_invalid")
            if key == "created_at":
                _time(value)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_write_once(path: Path, value: object) -> None:
    _write_once(path, value)
    _fsync_directory(path.parent)


def _replace_transition_config(path: Path, value: object) -> None:
    """Keep the config path continuously present for predecessor consumers."""
    temporary = path.with_name(path.name + ".transition-" + uuid.uuid4().hex)
    try:
        _durable_write_once(temporary, value)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _transition(root: Path) -> dict[str, Any] | None:
    path = root / ".omc" / TRANSITION_NAME
    if not path.exists() and not path.is_symlink():
        return None
    marker = _json_regular(path, reason="transition_invalid")
    fields = {"schema", "target_identity", "previous_activation", "archive_sha256",
              "peer_archive_sha256", "previous_work_ids", "file_sha256"}
    if (set(marker) != fields or marker["schema"] != "omc-cohort-transition/v1"
            or marker["target_identity"] != _target(root)
            or not isinstance(marker["file_sha256"], dict)
            or set(marker["file_sha256"]) - {CONFIG_NAME, LEDGER_NAME, CLOSURE_NAME}
            or not isinstance(marker["previous_work_ids"], list)
            or not isinstance(marker["previous_activation"], str)
            or not isinstance(marker["peer_archive_sha256"], list)
            or len(marker["peer_archive_sha256"]) != 2
            or any(not isinstance(h, str) or _HEX.fullmatch(h) is None
                   for h in [marker["archive_sha256"], *marker["peer_archive_sha256"], *marker["file_sha256"].values()])
            or any(not isinstance(w, str) or _ID.fullmatch(w) is None for w in marker["previous_work_ids"])):
        raise V3Error("transition_invalid")
    return marker


def _external_directory(path: Path, roots: list[Path]) -> Path:
    """Reject symlink ancestors, not just the final custody path."""
    absolute = path.absolute()
    if any(p.is_symlink() for p in [absolute, *absolute.parents]):
        raise V3Error("custody_invalid")
    if any((p / ".git").exists() for p in [absolute, *absolute.parents]):
        raise V3Error("custody_must_be_external")
    if any(absolute.resolve().is_relative_to(r.resolve()) for r in roots):
        raise V3Error("custody_must_be_external")
    absolute.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not absolute.is_dir():
        raise V3Error("custody_invalid")
    _fsync_directory(absolute.parent)
    return absolute


def _original_files(root: Path) -> dict[str, Any]:
    omc = root / ".omc"
    names = [CONFIG_NAME, LEDGER_NAME, CLOSURE_NAME, TRANSITION_NAME,
             ACTIVATION_NAME, JOURNAL_NAME]
    previous = omc / "cohort-transition-previous"
    if previous.is_symlink() or not previous.is_dir():
        raise V3Error("transition_destination_invalid")
    descendants = list(previous.rglob("*"))
    if any(p.is_symlink() for p in descendants):
        raise V3Error("transition_source_invalid")
    names.extend(str(p.relative_to(omc)) for p in descendants if not p.is_dir())
    result = {}
    for name in sorted(names):
        path = omc / name
        if path.is_symlink():
            raise V3Error("transition_source_invalid")
        if not path.exists():
            continue
        if not path.is_file():
            raise V3Error("transition_source_invalid")
        data = path.read_bytes()
        result[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                        "bytes_base64": base64.b64encode(data).decode("ascii")}
    return result


def _verified_originals(path: Path, digest: str) -> dict[str, Any]:
    bundle = _json_regular(path, reason="custody_invalid")
    if (_hash(bundle) != digest or set(bundle) != {"schema", "transition_id", "target_identity",
            "peer_archive_sha256", "files", "joint_receipt"}
            or bundle["schema"] != "omc-cohort-originals/v1"
            or not isinstance(bundle["files"], dict)):
        raise V3Error("custody_invalid")
    for name, entry in bundle["files"].items():
        if (not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts
                or not isinstance(entry, dict) or set(entry) != {"sha256", "bytes_base64"}):
            raise V3Error("custody_invalid")
        try:
            data = base64.b64decode(entry["bytes_base64"], validate=True)
        except (ValueError, TypeError) as error:
            raise V3Error("custody_invalid") from error
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise V3Error("custody_invalid")
    joint = bundle["joint_receipt"]
    if not isinstance(joint, dict) or set(joint) != {"path", "sha256", "bytes_base64"}:
        raise V3Error("custody_invalid")
    try:
        data = base64.b64decode(joint["bytes_base64"], validate=True)
    except (ValueError, TypeError) as error:
        raise V3Error("custody_invalid") from error
    if hashlib.sha256(data).hexdigest() != joint["sha256"]:
        raise V3Error("custody_invalid")
    return bundle


def _restore_original_once(path: Path, entry: dict[str, str]) -> None:
    """An existing destination is completion evidence only when bytes match."""
    if path.is_symlink():
        raise V3Error("transition_conflict")
    data = base64.b64decode(entry["bytes_base64"], validate=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise V3Error("transition_conflict")
        return
    # Publish only a fully fsynced file. A killed process can leave an unused temp,
    # never a partial destination that would be mistaken for preserved originals.
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        if temporary.exists():
            temporary.unlink()
    _fsync_directory(path.parent)


def _transition_journal(root: Path) -> dict[str, Any] | None:
    path = root / ".omc" / JOURNAL_NAME
    if not path.exists() and not path.is_symlink():
        return None
    value = _json_regular(path, reason="transition_journal_invalid")
    if (set(value) != {"schema", "transition_id", "target_identity", "peer_archive_sha256",
                       "originals_path", "originals_sha256", "state"}
            or value["schema"] != "omc-cohort-transition-journal/v1"
            or value["target_identity"] != _target(root)
            or not isinstance(value["transition_id"], str) or _ID.fullmatch(value["transition_id"]) is None
            or not isinstance(value["originals_path"], str) or not Path(value["originals_path"]).is_absolute()
            or not isinstance(value["originals_sha256"], str) or _HEX.fullmatch(value["originals_sha256"]) is None
            or not isinstance(value["peer_archive_sha256"], list) or len(value["peer_archive_sha256"]) != 2
            or any(not isinstance(h, str) or _HEX.fullmatch(h) is None for h in value["peer_archive_sha256"])
            or value["state"] not in {"PREPARING", "PREPARED"}):
        raise V3Error("transition_journal_invalid")
    return value


def _verify_transition_history(history: Path, files: dict[str, Any]) -> None:
    """Completed retries must verify preserved originals, not journal state alone."""
    if history.is_symlink() or not history.is_dir():
        raise V3Error("transition_conflict")
    for name, entry in files.items():
        if name.startswith("cohort-transition-previous/"):
            path = history / Path(name).relative_to("cohort-transition-previous")
            if (any(parent.is_symlink() for parent in path.parents)
                    or path.is_symlink() or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]):
                raise V3Error("transition_conflict")


def _prepare_repeat(root: Path, *, own: dict[str, Any],
                    verified: list[dict[str, Any]], transition_id: str, custody: Path) -> dict[str, Any]:
    if not isinstance(transition_id, str) or _ID.fullmatch(transition_id) is None:
        raise V3Error("transition_id_invalid")
    peer_hashes = sorted(v["bundle_sha256"] for v in verified)
    omc = root / ".omc"
    journal_path = omc / JOURNAL_NAME
    originals_path = custody.absolute() / transition_id / _target(root) / "originals.json"
    journal = None
    candidate = _transition_journal(root)
    if candidate is not None:
        if candidate.get("transition_id") == transition_id:
            journal = candidate
        elif candidate.get("state") != "PREPARED":
            raise V3Error("transition_conflict")
    if journal is None:
        current = _config(root)
        events = _events(root, activation_id=current["activation_id"])
        closure = _closure(root, current, events)
        if current != own["config"] or events != own["events"] or closure != own["closure"] or closure is None:
            raise V3Error("transition_archive_mismatch")
        _activation_check(root, current)
        old_marker = _transition(root)
        if old_marker is None or old_marker["previous_activation"] == current["activation_id"]:
            raise V3Error("repeat_transition_requires_activated_generation")
        if _json_regular(omc / "cohort-transition-previous/prepared.json", reason="transition_conflict") != {"transition_sha256": _hash(old_marker)}:
            raise V3Error("transition_conflict")
        pointer = _json_regular(omc / ACTIVATION_NAME, reason="activation_invalid")
        joint_path = Path(pointer["path"])
        joint_bytes = joint_path.read_bytes()
        directory = _external_directory(originals_path.parent, [root])
        bundle = {"schema": "omc-cohort-originals/v1", "transition_id": transition_id,
                  "target_identity": _target(root), "peer_archive_sha256": peer_hashes,
                  "files": _original_files(root), "joint_receipt": {
                      "path": str(joint_path), "sha256": hashlib.sha256(joint_bytes).hexdigest(),
                      "bytes_base64": base64.b64encode(joint_bytes).decode("ascii")}}
        if originals_path.exists() or originals_path.is_symlink():
            if _json_regular(originals_path, reason="custody_invalid") != bundle:
                raise V3Error("custody_conflict")
        else:
            _durable_write_once(directory / "originals.json", bundle)
        _verified_originals(originals_path, _hash(bundle))
        journal = {"schema": "omc-cohort-transition-journal/v1", "transition_id": transition_id,
                   "target_identity": _target(root), "peer_archive_sha256": peer_hashes,
                   "originals_path": str(originals_path), "originals_sha256": _hash(bundle),
                   "state": "PREPARING"}
        if journal_path.exists():
            _replace_transition_config(journal_path, journal)
        else:
            _durable_write_once(journal_path, journal)
    if (set(journal) != {"schema", "transition_id", "target_identity", "peer_archive_sha256",
                        "originals_path", "originals_sha256", "state"}
            or journal["schema"] != "omc-cohort-transition-journal/v1"
            or journal["target_identity"] != _target(root)
            or journal["peer_archive_sha256"] != peer_hashes
            or journal["originals_path"] != str(originals_path)
            or journal["state"] not in {"PREPARING", "PREPARED"}):
        raise V3Error("transition_conflict")
    _external_directory(originals_path.parent, [root])
    bundle = _verified_originals(originals_path, journal["originals_sha256"])
    if (bundle["transition_id"] != transition_id or bundle["target_identity"] != _target(root)
            or bundle["peer_archive_sha256"] != peer_hashes):
        raise V3Error("custody_invalid")
    files = bundle["files"]
    current = own["config"]
    marker = {"schema": "omc-cohort-transition/v1", "target_identity": _target(root),
              "previous_activation": current["activation_id"], "archive_sha256": _hash(own),
              "peer_archive_sha256": peer_hashes,
              "previous_work_ids": sorted(set(current.get("excluded_work_ids", [])) |
                  {s["work_id"] for s in own["sessions"] if isinstance(s.get("work_id"), str)}),
              "file_sha256": {n: files[n]["sha256"] for n in (CONFIG_NAME, LEDGER_NAME, CLOSURE_NAME) if n in files}}
    previous = omc / "cohort-transition-previous"
    history = omc / ("cohort-transition-history-" + transition_id)
    result = {"state": "TRANSITION_BLOCKED", "archive_sha256": marker["archive_sha256"]}
    prepared_value = {"transition_sha256": _hash(marker)}
    proof = previous / "prepared.json"
    if previous.is_symlink() or history.is_symlink():
        raise V3Error("transition_destination_invalid")
    if journal["state"] == "PREPARED" and (proof.exists() or proof.is_symlink()):
        _verify_transition_history(history, files)
        if (_transition(root) != marker or _json_regular(previous / "prepared.json", reason="transition_invalid") != prepared_value):
            raise V3Error("transition_conflict")
        for name in marker["file_sha256"]:
            path = previous / name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != files[name]["sha256"]:
                raise V3Error("transition_conflict")
        return result
    # Invalidate predecessor enroll's prepared proof BEFORE publishing the barrier.
    if not history.exists():
        if not previous.is_dir():
            raise V3Error("transition_conflict")
        old_proof = previous / "prepared.json"
        proof_entry = files.get("cohort-transition-previous/prepared.json")
        if proof_entry is None or old_proof.is_symlink() or not old_proof.is_file():
            raise V3Error("transition_conflict")
        old_proof_bytes = old_proof.read_bytes()
        if (hashlib.sha256(old_proof_bytes).hexdigest() != proof_entry["sha256"]
                and _json_regular(old_proof, reason="transition_conflict") != {"state": "TRANSITION_BLOCKED"}):
            raise V3Error("transition_conflict")
        for name, entry in files.items():
            if name.startswith("cohort-transition-previous/") and name != "cohort-transition-previous/prepared.json":
                path = omc / name
                if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise V3Error("transition_conflict")
        _replace_transition_config(previous / "prepared.json", {"state": "TRANSITION_BLOCKED"})
    config = _json_regular(config_path(root), reason="transition_invalid")
    if config != _TRANSITION_BARRIER:
        if config_path(root).read_bytes() != base64.b64decode(files[CONFIG_NAME]["bytes_base64"]):
            raise V3Error("transition_conflict")
        _replace_transition_config(config_path(root), _TRANSITION_BARRIER)
    if not history.exists():
        os.rename(previous, history)
        _fsync_directory(omc)
    elif not history.is_dir():
        raise V3Error("transition_conflict")
    # Preserve the original prepared proof in custody; local history is intentionally blocked.
    for name, entry in files.items():
        if name.startswith("cohort-transition-previous/") and name != "cohort-transition-previous/prepared.json":
            path = history / Path(name).relative_to("cohort-transition-previous")
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise V3Error("transition_conflict")
    history_prepared = history / "prepared.json"
    original_prepared = files.get("cohort-transition-previous/prepared.json")
    if original_prepared is None:
        raise V3Error("custody_invalid")
    original_prepared_value = json.loads(base64.b64decode(original_prepared["bytes_base64"]))
    history_value = _json_regular(history_prepared, reason="transition_conflict")
    if history_value != original_prepared_value:
        if history_value != {"state": "TRANSITION_BLOCKED"}:
            raise V3Error("transition_conflict")
        # Restore exact original bytes, not a reformatted JSON representation.
        temporary = history / (".prepared-" + uuid.uuid4().hex)
        _restore_original_once(temporary, original_prepared)
        os.replace(temporary, history_prepared)
        _fsync_directory(history)
    _verify_transition_history(history, files)
    old_marker_bytes = base64.b64decode(files[TRANSITION_NAME]["bytes_base64"])
    marker_path = omc / TRANSITION_NAME
    existing_marker = _json_regular(marker_path, reason="transition_invalid")
    if existing_marker != marker:
        if marker_path.read_bytes() != old_marker_bytes:
            raise V3Error("transition_conflict")
        _replace_transition_config(marker_path, marker)
    if previous.is_symlink():
        raise V3Error("transition_destination_invalid")
    previous.mkdir(exist_ok=True, mode=0o700)
    _fsync_directory(omc)
    for name in marker["file_sha256"]:
        _restore_original_once(previous / name, files[name])
    for name in (LEDGER_NAME, CLOSURE_NAME, ACTIVATION_NAME):
        path = omc / name
        if path.is_symlink():
            raise V3Error("transition_conflict")
        if path.exists():
            if name not in files or path.read_bytes() != base64.b64decode(files[name]["bytes_base64"]):
                raise V3Error("transition_conflict")
            path.unlink()
            _fsync_directory(omc)
    # Seal readiness before exposing the proof consumed by predecessor enroll.
    # If killed between these writes, retry verifies all bytes before publishing proof.
    _replace_transition_config(journal_path, {**journal, "state": "PREPARED"})
    prepared = previous / "prepared.json"
    if prepared.exists() or prepared.is_symlink():
        if _json_regular(prepared, reason="transition_invalid") != prepared_value:
            raise V3Error("transition_conflict")
    else:
        _durable_write_once(prepared, prepared_value)
    return result


def prepare_transition(root: Path, *, archives: list[Path], archive: Path,
                       transition_id: str | None = None, custody: Path | None = None) -> dict[str, Any]:
    """Block capture before moving old artifacts; retry resumes exact moves."""
    root = legacy._root(root)
    bundles = [_json_regular(p, reason="archive_invalid") for p in archives]
    verified = [verify_archive(p) for p in archives]
    if (len(bundles) != 2 or len({b["config"]["target_identity"] for b in bundles}) != 2
            or len({b["config"]["roster_sha256"] for b in bundles}) != 1):
        raise V3Error("transition_pair_invalid")
    own = _json_regular(archive, reason="archive_invalid")
    own_check = verify_archive(archive)
    if own["config"]["target_identity"] != _target(root) or own_check not in verified:
        raise V3Error("transition_archive_mismatch")
    with omc_state._omc_lock(root):
        if transition_id is not None or custody is not None:
            if transition_id is None or custody is None:
                raise V3Error("repeat_transition_arguments_required")
            return _prepare_repeat(root, own=own, verified=verified,
                                   transition_id=transition_id, custody=custody)
        marker = _transition(root)
        previous = root / ".omc/cohort-transition-previous"
        if previous.is_symlink():
            raise V3Error("transition_destination_invalid")
        if marker is None:
            current = _config(root)
            events = _events(root, activation_id=current["activation_id"])
            if (current != own["config"] or events != own["events"]
                    or _closure(root, current, events) != own["closure"]):
                raise V3Error("transition_archive_mismatch")
            hashes = {}
            for name in (CONFIG_NAME, LEDGER_NAME, CLOSURE_NAME):
                path = root / ".omc" / name
                if path.is_symlink():
                    raise V3Error("transition_source_invalid")
                if path.exists():
                    hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            marker = {"schema": "omc-cohort-transition/v1", "target_identity": _target(root),
                      "previous_activation": current["activation_id"],
                      "archive_sha256": own_check["bundle_sha256"],
                      "peer_archive_sha256": sorted(v["bundle_sha256"] for v in verified),
                      "previous_work_ids": sorted({s["work_id"] for s in own["sessions"]
                                                    if isinstance(s.get("work_id"), str)}),
                      "file_sha256": hashes}
            _durable_write_once(root / ".omc" / TRANSITION_NAME, marker)
        if (marker["archive_sha256"] != own_check["bundle_sha256"]
                or marker["peer_archive_sha256"] != sorted(v["bundle_sha256"] for v in verified)):
            raise V3Error("transition_conflict")
        previous.mkdir(exist_ok=True, mode=0o700)
        prepared = previous / "prepared.json"
        if prepared.exists() or prepared.is_symlink():
            if _json_regular(prepared, reason="transition_invalid") != {"transition_sha256": _hash(marker)}:
                raise V3Error("transition_conflict")
            return {"state": "TRANSITION_BLOCKED", "archive_sha256": marker["archive_sha256"]}
        for name, digest in marker["file_sha256"].items():
            source, dest = root / ".omc" / name, previous / name
            if dest.is_symlink() or source.is_symlink():
                raise V3Error("transition_source_invalid")
            if dest.exists():
                if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                    raise V3Error("transition_conflict")
                if source.exists():
                    if name == CONFIG_NAME and _json_regular(source, reason="transition_invalid") == _TRANSITION_BARRIER:
                        continue
                    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                        raise V3Error("transition_conflict")
                    if name == CONFIG_NAME:
                        _replace_transition_config(source, _TRANSITION_BARRIER)
                    else:
                        source.unlink()
                    _fsync_directory(source.parent)
                continue
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise V3Error("transition_source_invalid")
            # No capture path may write while the marker has no joint activation.
            os.link(source, dest, follow_symlinks=False)
            _fsync_directory(previous)
            if name == CONFIG_NAME:
                _replace_transition_config(source, _TRANSITION_BARRIER)
            else:
                source.unlink()
            _fsync_directory(source.parent)
        _durable_write_once(prepared, {"transition_sha256": _hash(marker)})
        return {"state": "TRANSITION_BLOCKED", "archive_sha256": marker["archive_sha256"]}


def activate_pair(roots: list[Path], *, output: Path) -> dict[str, Any]:
    """Publish a joint readiness fact, not a cross-repository transaction."""
    from contextlib import ExitStack
    roots = sorted((legacy._root(r) for r in roots), key=str)
    if len(roots) != 2 or len(set(roots)) != 2 or any(output.resolve().is_relative_to(r) for r in roots):
        raise V3Error("activation_pair_invalid")
    with ExitStack() as locks:
        for root in roots:
            locks.enter_context(omc_state._omc_lock(root))
        configs = [_config(r, check_transition=False) for r in roots]
        markers = [_transition(r) for r in roots]
        journals = [_transition_journal(r) for r in roots]
        if any(j is not None for j in journals):
            if (any(j is None or j.get("state") != "PREPARED" for j in journals)
                    or len({j["transition_id"] for j in journals if j is not None}) != 1
                    or any(j["peer_archive_sha256"] != m["peer_archive_sha256"]
                           for j, m in zip(journals, markers) if j is not None and m is not None)):
                raise V3Error("activation_pair_invalid")
            for journal in journals:
                _verified_originals(Path(journal["originals_path"]), journal["originals_sha256"])
        if (any(m is None for m in markers)
                or len({c["roster_sha256"] for c in configs}) != 1
                or len({_hash(m["peer_archive_sha256"]) for m in markers if m is not None}) != 1
                or any(c["activation_id"] == m["previous_activation"] for c, m in zip(configs, markers))):
            raise V3Error("activation_pair_invalid")
        receipt = {"schema": "omc-cohort-joint-activation/v1", "roster_sha256": configs[0]["roster_sha256"],
                   "activation_id": configs[0]["activation_id"], "activation_at": configs[0]["activation_at"],
                   "targets": sorted([{"target_identity": _target(r), "config_sha256": _hash(c),
                                        "transition_sha256": _hash(m)}
                                       for r, c, m in zip(roots, configs, markers)], key=lambda t: t["target_identity"])}
        if output.exists() or output.is_symlink():
            if _json_regular(output, reason="activation_invalid") != receipt:
                raise V3Error("activation_conflict")
        else:
            if _time(receipt["activation_at"]) <= _now():
                raise V3Error("fresh_t0_required")
            _durable_write_once(output, receipt)
        pointer = {"path": str(output.resolve()), "sha256": _hash(receipt)}
        for root in roots:
            path = root / ".omc" / ACTIVATION_NAME
            if path.exists() or path.is_symlink():
                if _json_regular(path, reason="activation_invalid") != pointer:
                    raise V3Error("activation_conflict")
            else:
                if _time(receipt["activation_at"]) <= _now():
                    raise V3Error("fresh_t0_required")
                _durable_write_once(path, pointer)
        return {"state": "REGISTERED_NOT_STARTED", "activation_sha256": _hash(receipt)}


def _activation_check(root: Path, config: dict[str, Any]) -> None:
    marker = _transition(root)
    if marker is None:
        return
    pointer_path = root / ".omc" / ACTIVATION_NAME
    if not pointer_path.exists() and not pointer_path.is_symlink():
        raise V3Error("transition_blocked")
    pointer = _json_regular(pointer_path, reason="activation_invalid")
    if set(pointer) != {"path", "sha256"} or not isinstance(pointer["path"], str):
        raise V3Error("activation_invalid")
    receipt = _json_regular(Path(pointer["path"]), reason="activation_invalid")
    targets = receipt.get("targets")
    expected = {"target_identity": _target(root), "config_sha256": _hash(config),
                "transition_sha256": _hash(marker)}
    if (set(receipt) != {"schema", "roster_sha256", "activation_id", "activation_at", "targets"}
            or receipt.get("schema") != "omc-cohort-joint-activation/v1"
            or _hash(receipt) != pointer["sha256"]
            or receipt.get("roster_sha256") != config["roster_sha256"]
            or receipt.get("activation_id") != config["activation_id"]
            or receipt.get("activation_at") != config["activation_at"]
            or not isinstance(targets, list) or len(targets) != 2
            or any(not isinstance(t, dict) or set(t) != set(expected) for t in targets)
            or expected not in targets
            or len({t.get("target_identity") for t in targets}) != 2):
        raise V3Error("activation_invalid")


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


def _config(root: Path, *, check_transition: bool = True) -> dict[str, Any]:
    try:
        legacy._omc_dir(root, create=False)
    except legacy.SkillCohortError as error:
        raise V3Error(str(error)) from error
    if check_transition and _transition(root) is not None and not config_path(root).exists():
        raise V3Error("transition_blocked")
    value = _json_regular(config_path(root), reason="v3_config_invalid")
    if _transition(root) is not None and value == _TRANSITION_BARRIER:
        raise V3Error("transition_blocked")
    basic = {"generation", "enabled", "status", "activation_id", "activation_at"}
    operational = basic | {"roster", "roster_sha256", "target_identity", "enrollment_session_ids"}
    marker = _transition(root)
    if marker is not None and value.get("activation_id") != marker["previous_activation"]:
        operational |= {"excluded_work_ids"}
        excluded = value.get("excluded_work_ids")
        if (not isinstance(excluded, list) or any(not isinstance(w, str) or _ID.fullmatch(w) is None for w in excluded)
                or excluded != sorted(set(excluded))):
            raise V3Error("v3_config_invalid")
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
    if check_transition:
        _activation_check(root, value)
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


def _work_followup_finalized(events: list[dict[str, Any]], work_id: str) -> bool:
    """Correction consumes one review choice; acceptance/deferral closes the work."""
    return any(event["event_type"] == "followup" and event["work_id"] == work_id
               and event["outcome"] != "correction" for event in events)


def _outcome_unobserved(
    works: set[str], reviews: list[dict[str, Any]], followups: list[dict[str, Any]],
) -> int:
    """An earlier correction is not an outcome for the next review round."""
    latest_reviews = {review["work_id"]: review["event_id"] for review in reviews}
    observed = {followup["review_event_id"] for followup in followups}
    return sum(latest_reviews.get(work) not in observed for work in works)


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
        if _work_followup_finalized(prior, work_id):
            raise V3Error("v3_ledger_invalid")
    elif event_type == "choice":
        reviews = [old for old in prior if old["event_type"] == "review" and old["work_id"] == work_id]
        if not reviews or reviews[-1]["event_id"] != event["review_event_id"]:
            raise V3Error("v3_ledger_invalid")
        if _work_followup_finalized(prior, work_id):
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
    marker = _transition(root)
    if marker is not None:
        excluded = set(config["excluded_work_ids"])
        if session["work_id"] in excluded:
            return False
        root_id = session.get("lineage_root_session_id")
        if not isinstance(root_id, str):
            origins = []
            directory = root / ".omc/state/sessions"
            if directory.is_symlink():
                raise V3Error("session_invalid")
            for path in directory.glob("*/session.json"):
                if path.parent.is_symlink():
                    raise V3Error("session_invalid")
                candidate = _json_regular(path, reason="session_invalid")
                if candidate.get("work_id") == session["work_id"] and candidate.get("completion_action") == "start":
                    origins.append(candidate.get("session_id"))
            if len(origins) != 1 or not isinstance(origins[0], str):
                return False
            root_id = origins[0]
        origin = _session(root, root_id)
        if (origin["work_id"] != session["work_id"] or origin.get("completion_action") != "start"
                or origin.get("lineage_root_session_id") != root_id or origin.get("lineage_index") != 0
                or _time(origin.get("created_at")) < _time(config["activation_at"])
                or _time(origin.get("created_at")) > _now()):
            return False
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
        if _work_followup_finalized(events, work_id):
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
        if _work_followup_finalized(events, work_id):
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
    if _transition(root) is not None:
        try:
            _config(root)
        except V3Error as error:
            if str(error) == "transition_blocked":
                return {"generation": "v3", "state": "TRANSITION_BLOCKED"}
            raise
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
        "outcome_unobserved": _outcome_unobserved(works, reviews, followups),
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
    if _transition(root) is not None:
        return True
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
    archive_cmd = sub.add_parser("archive-closed")
    archive_cmd.add_argument("--target", required=True, type=Path)
    archive_cmd.add_argument("--output", required=True, type=Path)
    verify_cmd = sub.add_parser("verify-archive")
    verify_cmd.add_argument("--archive", required=True, type=Path)
    prepare_cmd = sub.add_parser("prepare-transition")
    prepare_cmd.add_argument("--target", required=True, type=Path)
    prepare_cmd.add_argument("--archive", required=True, type=Path)
    prepare_cmd.add_argument("--pair-archive", required=True, type=Path, action="append")
    prepare_cmd.add_argument("--transition-id")
    prepare_cmd.add_argument("--custody", type=Path)
    activate_cmd = sub.add_parser("activate-pair")
    activate_cmd.add_argument("--target", required=True, type=Path, action="append")
    activate_cmd.add_argument("--output", required=True, type=Path)
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
        if args.command == "archive-closed":
            result = archive_closed(args.target, output=args.output)
        elif args.command == "verify-archive":
            result = verify_archive(args.archive)
        elif args.command == "prepare-transition":
            result = prepare_transition(args.target, archives=args.pair_archive, archive=args.archive,
                                        transition_id=args.transition_id, custody=args.custody)
        elif args.command == "activate-pair":
            result = activate_pair(args.target, output=args.output)
        elif args.command == "create-roster":
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
