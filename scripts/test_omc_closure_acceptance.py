from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import omc_state


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
