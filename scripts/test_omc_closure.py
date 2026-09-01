from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

import omc_closure


def _envelope(
    *,
    session_id: str = "session-1",
    task_id: str = "task-1",
    request_digest: str = "a" * 64,
) -> dict:
    return {
        "schema_version": "omc-closure/v1",
        "work_unit": {
            "session_id": session_id,
            "task_id": task_id,
            "request_digest": request_digest,
        },
        "scope": {
            "deliverables": ["closure envelope"],
            "definition_of_done": ["identity-bound round trip"],
            "non_goals": ["automatic skill transition"],
        },
        "validation": {
            "max_total_rounds": 3,
            "max_revisions_per_issue": 1,
        },
        "acceptance": {
            "authority": "user",
            "rule_id": None,
        },
    }


def test_store_and_load_identity_bound_envelope(tmp_path: Path):
    stored = omc_closure.store_envelope(tmp_path, _envelope())

    assert stored["contract_sha256"] == omc_closure.envelope_sha256(stored)
    loaded = omc_closure.load_envelope(
        tmp_path,
        session_id="session-1",
        task_id="task-1",
        request_digest="a" * 64,
    )
    assert loaded == {
        "status": "valid",
        "mode": "closure",
        "envelope": stored,
    }


def test_absent_envelope_uses_legacy_mode(tmp_path: Path):
    assert omc_closure.load_envelope(
        tmp_path,
        session_id="session-1",
        task_id="task-1",
        request_digest="a" * 64,
    ) == {
        "status": "absent",
        "mode": "legacy",
        "envelope": None,
    }


def test_deleted_frozen_envelope_does_not_downgrade_to_legacy(tmp_path: Path):
    omc_closure.store_envelope(tmp_path, _envelope())
    path = omc_closure.envelope_path(tmp_path, "session-1", "task-1")
    path.unlink()

    with pytest.raises(
        omc_closure.ClosureContractError,
        match="closure_contract_missing",
    ):
        omc_closure.load_envelope(
            tmp_path,
            session_id="session-1",
            task_id="task-1",
            request_digest="a" * 64,
        )


def test_tampered_enrollment_marker_blocks_valid_envelope(tmp_path: Path):
    omc_closure.store_envelope(tmp_path, _envelope())
    marker_path = omc_closure.enrollment_path(tmp_path, "session-1", "task-1")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["contract_sha256"] = "b" * 64
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(
        omc_closure.ClosureContractError,
        match="closure_enrollment_contract_mismatch",
    ):
        omc_closure.load_envelope(
            tmp_path,
            session_id="session-1",
            task_id="task-1",
            request_digest="a" * 64,
        )


def test_absent_envelope_still_rejects_invalid_request_digest(tmp_path: Path):
    with pytest.raises(omc_closure.ClosureContractError, match="request_digest_invalid"):
        omc_closure.load_envelope(
            tmp_path,
            session_id="session-1",
            task_id="task-1",
            request_digest="not-a-digest",
        )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("session_id", "session-2", "work_unit_identity_mismatch"),
        ("task_id", "task-2", "work_unit_identity_mismatch"),
        ("request_digest", "b" * 64, "work_unit_identity_mismatch"),
    ],
)
def test_load_rejects_other_work_unit(
    tmp_path: Path,
    field: str,
    value: str,
    reason: str,
):
    omc_closure.store_envelope(tmp_path, _envelope())
    identity = {
        "session_id": "session-1",
        "task_id": "task-1",
        "request_digest": "a" * 64,
    }
    identity[field] = value

    if field in {"session_id", "task_id"}:
        source = omc_closure.envelope_path(tmp_path, "session-1", "task-1")
        target = omc_closure.envelope_path(
            tmp_path,
            identity["session_id"],
            identity["task_id"],
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    with pytest.raises(omc_closure.ClosureContractError, match=reason):
        omc_closure.load_envelope(tmp_path, **identity)


def test_load_rejects_tampered_envelope(tmp_path: Path):
    omc_closure.store_envelope(tmp_path, _envelope())
    path = omc_closure.envelope_path(tmp_path, "session-1", "task-1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["validation"]["max_total_rounds"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(omc_closure.ClosureContractError, match="contract_digest_mismatch"):
        omc_closure.load_envelope(
            tmp_path,
            session_id="session-1",
            task_id="task-1",
            request_digest="a" * 64,
        )


def test_existing_malformed_envelope_blocks_instead_of_falling_back(tmp_path: Path):
    path = omc_closure.envelope_path(tmp_path, "session-1", "task-1")
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(omc_closure.ClosureContractError, match="closure_contract_invalid_json"):
        omc_closure.load_envelope(
            tmp_path,
            session_id="session-1",
            task_id="task-1",
            request_digest="a" * 64,
        )


@pytest.mark.parametrize("value", ["../escape", "a/b", "", ".", ".."])
def test_identity_rejects_unsafe_path_segments(tmp_path: Path, value: str):
    with pytest.raises(omc_closure.ClosureContractError, match="identity_segment_invalid"):
        omc_closure.envelope_path(tmp_path, value, "task-1")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema_version="v0"),
        lambda value: value["scope"].update(deliverables=[]),
        lambda value: value["scope"].update(definition_of_done=[]),
        lambda value: value["validation"].update(max_total_rounds=0),
        lambda value: value["validation"].update(max_revisions_per_issue=-1),
        lambda value: value["acceptance"].update(authority="machine"),
        lambda value: value["acceptance"].update(
            authority="preauthorized_rule", rule_id="rule-1"
        ),
        lambda value: value["acceptance"].update(authority="user", rule_id="rule-1"),
    ],
)
def test_store_rejects_invalid_contract(tmp_path: Path, mutate):
    payload = _envelope()
    mutate(payload)

    with pytest.raises(omc_closure.ClosureContractError):
        omc_closure.store_envelope(tmp_path, payload)


def test_envelope_has_no_automatic_transition_directive(tmp_path: Path):
    stored = omc_closure.store_envelope(tmp_path, _envelope())

    assert "next_skill" not in stored
    assert "auto_transition" not in stored


def test_store_does_not_replace_frozen_contract(tmp_path: Path):
    omc_closure.store_envelope(tmp_path, _envelope())
    changed = _envelope()
    changed["scope"]["deliverables"] = ["different deliverable"]

    with pytest.raises(
        omc_closure.ClosureContractError,
        match="closure_contract_already_frozen",
    ):
        omc_closure.store_envelope(tmp_path, changed)


def test_load_rejects_symlinked_contract(tmp_path: Path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({}), encoding="utf-8")
    path = omc_closure.envelope_path(tmp_path, "session-1", "task-1")
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(omc_closure.ClosureContractError, match="closure_contract_symlink"):
        omc_closure.load_envelope(
            tmp_path,
            session_id="session-1",
            task_id="task-1",
            request_digest="a" * 64,
        )


def test_store_rejects_symlinked_session_directory(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sessions = tmp_path / ".omc" / "state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "session-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(omc_closure.ClosureContractError, match="closure_storage_symlink"):
        omc_closure.store_envelope(tmp_path, _envelope())
