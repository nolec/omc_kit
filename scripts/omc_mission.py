#!/usr/bin/env python3
"""Frozen, user-approved mission contracts for safe Autopilot runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


MISSION_SCHEMA_VERSION = "omc-autopilot-mission/v1"
MISSION_APPROVAL_SCHEMA_VERSION = "omc-autopilot-mission-approval/v1"
_PACKET_FIELDS = {
    "schema_version",
    "request_sha256",
    "base_commit",
    "outcome",
    "deliverables",
    "definition_of_done",
    "non_goals",
    "validation",
}
_STAGES = {"task", "critique", "review"}


class MissionError(RuntimeError):
    """Raised when a mission or its approval cannot be trusted."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_digest(value: object, field: str, length: int = 64) -> str:
    text = str(value or "")
    if len(text) != length or not re.fullmatch(r"[0-9a-f]+", text):
        raise MissionError(f"{field}_invalid")
    return text


def _require_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MissionError(f"{field}_invalid")
    return text


def _require_text_list(value: object, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise MissionError(f"{field}_invalid")
    normalized = [_require_text(item, field) for item in value]
    if len(normalized) != len(set(normalized)):
        raise MissionError(f"{field}_duplicate")
    return normalized


def validate_mission_packet(
    payload: object, *, require_digest: bool = False
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MissionError("mission_packet_not_object")
    expected = set(_PACKET_FIELDS)
    if require_digest:
        expected.add("packet_sha256")
    if set(payload) != expected:
        raise MissionError("mission_packet_fields_invalid")
    if payload.get("schema_version") != MISSION_SCHEMA_VERSION:
        raise MissionError("mission_packet_schema_invalid")

    normalized = {
        "schema_version": MISSION_SCHEMA_VERSION,
        "request_sha256": _require_digest(payload.get("request_sha256"), "request_sha256"),
        "base_commit": _require_digest(payload.get("base_commit"), "base_commit", 40),
        "outcome": _require_text(payload.get("outcome"), "outcome"),
        "deliverables": _require_text_list(payload.get("deliverables"), "deliverables"),
        "definition_of_done": _require_text_list(
            payload.get("definition_of_done"), "definition_of_done"
        ),
        "non_goals": _require_text_list(
            payload.get("non_goals"), "non_goals", allow_empty=True
        ),
    }
    validation = payload.get("validation")
    if not isinstance(validation, dict) or set(validation) != {
        "max_total_rounds",
        "max_revisions_per_issue",
    }:
        raise MissionError("validation_invalid")
    rounds = validation.get("max_total_rounds")
    revisions = validation.get("max_revisions_per_issue")
    if (
        not isinstance(rounds, int)
        or isinstance(rounds, bool)
        or not 1 <= rounds <= 10
        or not isinstance(revisions, int)
        or isinstance(revisions, bool)
        or not 0 <= revisions <= rounds
    ):
        raise MissionError("validation_invalid")
    normalized["validation"] = {
        "max_total_rounds": rounds,
        "max_revisions_per_issue": revisions,
    }
    if require_digest:
        expected_digest = _sha256(_canonical_bytes(normalized))
        if payload.get("packet_sha256") != expected_digest:
            raise MissionError("mission_packet_digest_mismatch")
        normalized["packet_sha256"] = expected_digest
    else:
        normalized["packet_sha256"] = _sha256(_canonical_bytes(normalized))
    return normalized


def freeze_mission_packet(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    frozen = validate_mission_packet(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as exc:
        raise MissionError("mission_packet_already_frozen") from exc
    except OSError as exc:
        raise MissionError("mission_packet_unwritable") from exc
    try:
        data = json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise MissionError("mission_packet_write_incomplete")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    return frozen


def load_mission_packet(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise MissionError("mission_packet_symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MissionError("mission_packet_unreadable") from exc
    return validate_mission_packet(payload, require_digest=True)


def mission_approval_binding(packet: dict[str, Any], *, session_id: str) -> dict[str, str]:
    packet = validate_mission_packet(packet, require_digest="packet_sha256" in packet)
    return {
        "session_id": _require_text(session_id, "session_id"),
        "request_sha256": packet["request_sha256"],
        "base_commit": packet["base_commit"],
        "mission_packet_sha256": packet["packet_sha256"],
    }


def build_mission_approval_receipt(
    *, decision_id: str, session_id: str, packet: dict[str, Any]
) -> dict[str, Any]:
    binding = mission_approval_binding(packet, session_id=session_id)
    receipt = {
        "schema_version": MISSION_APPROVAL_SCHEMA_VERSION,
        "decision_id": _require_text(decision_id, "decision_id"),
        "action": "mission_accept",
        "status": "consumed",
        "binding": binding,
    }
    receipt["receipt_sha256"] = _sha256(_canonical_bytes(receipt))
    return receipt


def validate_mission_approval_receipt(
    receipt: object, *, packet: dict[str, Any], session_id: str
) -> dict[str, Any]:
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version",
        "decision_id",
        "action",
        "status",
        "binding",
        "receipt_sha256",
    }:
        raise MissionError("mission_approval_receipt_invalid")
    unsigned = {key: receipt[key] for key in receipt if key != "receipt_sha256"}
    if (
        receipt.get("schema_version") != MISSION_APPROVAL_SCHEMA_VERSION
        or receipt.get("action") != "mission_accept"
        or receipt.get("status") != "consumed"
        or receipt.get("receipt_sha256") != _sha256(_canonical_bytes(unsigned))
    ):
        raise MissionError("mission_approval_receipt_invalid")
    expected = mission_approval_binding(packet, session_id=session_id)
    if receipt.get("binding") != expected:
        raise MissionError("mission_approval_binding_mismatch")
    return dict(receipt)


def build_stage_briefing(packet: dict[str, Any], *, stage: str) -> dict[str, Any]:
    if stage not in _STAGES:
        raise MissionError("mission_stage_invalid")
    packet = validate_mission_packet(packet, require_digest="packet_sha256" in packet)
    return {
        "schema_version": "omc-autopilot-mission-briefing/v1",
        "stage": stage,
        "mission_packet_sha256": packet["packet_sha256"],
        "mission": {
            "outcome": packet["outcome"],
            "deliverables": packet["deliverables"],
            "definition_of_done": packet["definition_of_done"],
            "non_goals": packet["non_goals"],
            "validation": packet["validation"],
        },
    }
