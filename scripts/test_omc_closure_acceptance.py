from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import omc_state
import pytest


def _latest_path(root: Path) -> Path:
    path = root / ".omc" / "state" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _confirmed_state(root: Path) -> None:
    _latest_path(root).write_text(
        json.dumps(
            {
                "latest_session_id": "session-1",
                "latest_confirmed_session_id": "session-1",
                "latest_confirmation": {"status": "confirmed"},
            }
        ),
        encoding="utf-8",
    )


def _binding() -> dict:
    return {
        "session_id": "session-1",
        "task_id": "task-1",
        "request_digest": "a" * 64,
        "contract_sha256": "b" * 64,
        "scope_sha256": "d" * 64,
        "verification_receipt_sha256": "c" * 64,
        "accepted_residual_issue_ids": ["issue-1"],
        "accepted_residual_issues_sha256": "e" * 64,
    }


def _mission_binding() -> dict:
    return {
        "session_id": "session-1",
        "request_sha256": "a" * 64,
        "base_commit": "b" * 40,
        "mission_packet_sha256": "c" * 64,
    }


def test_mission_acceptance_is_exact_and_single_use(tmp_path: Path):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="mission-1",
        action="mission_accept",
        options=[
            {"id": "accept", "aliases": ["확인"], "value": _mission_binding()}
        ],
    )
    assert omc_state.resolve_pending_decision(tmp_path, response="확인")["resolved"]

    receipt_path = tmp_path / "mission-approval.json"
    consumed = omc_state.consume_pending_decision(
        tmp_path, decision_id="mission-1", receipt_output=receipt_path
    )
    replay = omc_state.consume_pending_decision(tmp_path, decision_id="mission-1")

    receipt = consumed["acceptance_receipt"]
    assert consumed["consumed"] is True
    assert consumed["action"] == "mission_accept"
    assert receipt["binding"] == _mission_binding()
    assert receipt["receipt_sha256"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert replay == {"consumed": False, "reason": "decision_not_acknowledged"}


def test_mission_acceptance_does_not_replace_existing_receipt(tmp_path: Path):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="mission-existing",
        action="mission_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _mission_binding()}],
    )
    assert omc_state.resolve_pending_decision(tmp_path, response="확인")["resolved"]
    receipt_path = tmp_path / "mission-approval.json"
    receipt_path.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="mission_receipt_already_exists"):
        omc_state.consume_pending_decision(
            tmp_path,
            decision_id="mission-existing",
            receipt_output=receipt_path,
        )

    assert receipt_path.read_text(encoding="utf-8") == "preserve"
    latest = json.loads(_latest_path(tmp_path).read_text(encoding="utf-8"))
    assert latest["pending_decision"]["status"] == "acknowledged"


def test_mission_acceptance_requires_receipt_output(tmp_path: Path):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="mission-no-output",
        action="mission_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _mission_binding()}],
    )
    assert omc_state.resolve_pending_decision(tmp_path, response="확인")["resolved"]

    consumed = omc_state.consume_pending_decision(
        tmp_path, decision_id="mission-no-output"
    )

    assert consumed == {
        "consumed": False,
        "reason": "mission_receipt_output_required",
    }
    latest = json.loads(_latest_path(tmp_path).read_text(encoding="utf-8"))
    assert latest["pending_decision"]["status"] == "acknowledged"


def test_mission_acceptance_recovers_after_state_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="mission-recovery",
        action="mission_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _mission_binding()}],
    )
    assert omc_state.resolve_pending_decision(tmp_path, response="확인")["resolved"]
    receipt_path = tmp_path / "mission-approval.json"
    original_write_json = omc_state._write_json

    def fail_state_write(path: Path, payload: dict) -> None:
        raise OSError("simulated state write failure")

    monkeypatch.setattr(omc_state, "_write_json", fail_state_write)
    with pytest.raises(OSError, match="simulated state write failure"):
        omc_state.consume_pending_decision(
            tmp_path,
            decision_id="mission-recovery",
            receipt_output=receipt_path,
        )
    persisted_receipt = receipt_path.read_bytes()

    monkeypatch.setattr(omc_state, "_write_json", original_write_json)
    recovered = omc_state.consume_pending_decision(
        tmp_path,
        decision_id="mission-recovery",
        receipt_output=receipt_path,
    )

    assert recovered["consumed"] is True
    assert receipt_path.read_bytes() == persisted_receipt
    latest = json.loads(_latest_path(tmp_path).read_text(encoding="utf-8"))
    assert latest["pending_decision"]["status"] == "consumed"


def test_mission_acceptance_recovers_after_partial_receipt_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="mission-partial-write",
        action="mission_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _mission_binding()}],
    )
    assert omc_state.resolve_pending_decision(tmp_path, response="확인")["resolved"]
    receipt_path = tmp_path / "mission-approval.json"
    original_write = omc_state.os.write
    write_calls = 0

    def fail_after_partial_write(fd: int, data: bytes) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            return original_write(fd, data[:4])
        raise OSError("simulated receipt write failure")

    monkeypatch.setattr(omc_state.os, "write", fail_after_partial_write)
    with pytest.raises(OSError, match="simulated receipt write failure"):
        omc_state.consume_pending_decision(
            tmp_path,
            decision_id="mission-partial-write",
            receipt_output=receipt_path,
        )

    assert not receipt_path.exists()
    monkeypatch.setattr(omc_state.os, "write", original_write)
    recovered = omc_state.consume_pending_decision(
        tmp_path,
        decision_id="mission-partial-write",
        receipt_output=receipt_path,
    )

    assert recovered["consumed"] is True
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == recovered[
        "acceptance_receipt"
    ]


def test_closure_acceptance_is_session_bound_and_single_use(tmp_path: Path):
    _confirmed_state(tmp_path)
    opened = omc_state.open_pending_decision(
        tmp_path,
        decision_id="accept-1",
        action="closure_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _binding()}],
        ttl_seconds=600,
    )
    assert opened["action"] == "closure_accept"

    resolved = omc_state.resolve_pending_decision(tmp_path, response="확인")
    assert resolved["resolved"] is True

    consumed = omc_state.consume_pending_decision(
        tmp_path, decision_id="accept-1"
    )
    replay = omc_state.consume_pending_decision(tmp_path, decision_id="accept-1")

    assert consumed["consumed"] is True
    assert consumed["acceptance_receipt"]["status"] == "consumed"
    assert consumed["acceptance_receipt"]["binding"] == _binding()
    assert replay == {"consumed": False, "reason": "decision_not_acknowledged"}


def test_closure_acceptance_rejects_changed_session(tmp_path: Path):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="accept-1",
        action="closure_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _binding()}],
    )
    latest = json.loads(_latest_path(tmp_path).read_text(encoding="utf-8"))
    latest["latest_session_id"] = "session-2"
    latest["latest_confirmed_session_id"] = "session-2"
    _latest_path(tmp_path).write_text(json.dumps(latest), encoding="utf-8")

    result = omc_state.resolve_pending_decision(tmp_path, response="확인")

    assert result == {"resolved": False, "reason": "session_changed"}


def test_closure_acceptance_rejects_expired_receipt(tmp_path: Path):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="accept-1",
        action="closure_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _binding()}],
    )
    latest = json.loads(_latest_path(tmp_path).read_text(encoding="utf-8"))
    latest["pending_decision"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    _latest_path(tmp_path).write_text(json.dumps(latest), encoding="utf-8")

    result = omc_state.resolve_pending_decision(tmp_path, response="확인")

    assert result == {"resolved": False, "reason": "expired"}


def test_closure_acceptance_rejects_binding_changed_after_acknowledgement(
    tmp_path: Path,
):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="accept-1",
        action="closure_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _binding()}],
    )
    assert omc_state.resolve_pending_decision(tmp_path, response="확인")["resolved"]
    latest = json.loads(_latest_path(tmp_path).read_text(encoding="utf-8"))
    latest["pending_decision"]["selected_value"]["task_id"] = "task-2"
    _latest_path(tmp_path).write_text(json.dumps(latest), encoding="utf-8")

    result = omc_state.consume_pending_decision(tmp_path, decision_id="accept-1")

    assert result == {"consumed": False, "reason": "decision_binding_changed"}


def test_closure_acceptance_rejects_options_changed_after_acknowledgement(
    tmp_path: Path,
):
    _confirmed_state(tmp_path)
    omc_state.open_pending_decision(
        tmp_path,
        decision_id="accept-1",
        action="closure_accept",
        options=[{"id": "accept", "aliases": ["확인"], "value": _binding()}],
    )
    assert omc_state.resolve_pending_decision(tmp_path, response="확인")["resolved"]
    latest = json.loads(_latest_path(tmp_path).read_text(encoding="utf-8"))
    latest["pending_decision"]["options"][0]["value"]["task_id"] = "task-2"
    _latest_path(tmp_path).write_text(json.dumps(latest), encoding="utf-8")

    result = omc_state.consume_pending_decision(tmp_path, decision_id="accept-1")

    assert result == {"consumed": False, "reason": "decision_options_changed"}
