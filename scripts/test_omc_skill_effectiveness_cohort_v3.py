"""Synthetic fixtures for the dormant v3 work-lifecycle observation contract."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_skill_effectiveness_cohort_v3 as v3


def _root(tmp_path: Path, *, enabled: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".omc").mkdir()
    v3.config_path(root).write_text(json.dumps({
        "generation": "v3", "enabled": enabled, "status": "DRAFT_SYNTHETIC",
        "activation_id": "synthetic-only", "activation_at": "2026-09-23T00:00:00Z",
    }))
    return root


def _session(root: Path, session_id: str, work_id: str, *, title: str) -> None:
    path = root / ".omc" / "state" / "sessions" / session_id
    path.mkdir(parents=True)
    (path / "session.json").write_text(json.dumps({
        "session_id": session_id, "work_id": work_id, "title": title,
        "confirmation": {"status": "confirmed"},
    }))


def test_default_disabled_no_v2_mutation(tmp_path: Path) -> None:
    root = _root(tmp_path, enabled=False)
    legacy = root / ".omc" / "skill-effectiveness-cohort-v2.jsonl"
    legacy.write_text("sentinel\n")
    _session(root, "task-001", "work-001", title="omc-task")
    with pytest.raises(v3.V3Error, match="v3_disabled"):
        v3.record_candidate(root, session_id="task-001")
    assert legacy.read_text() == "sentinel\n"
    assert not v3.ledger_path(root).exists()


def test_absent_v3_config_reports_disabled(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert v3.report(root) == {"generation": "v3", "state": "DISABLED"}


def test_task_review_exposures_are_one_work_and_missing_outcome_is_not_success(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="task-001")
    v3.record_candidate(root, session_id="review-001")
    report = v3.report(root)
    assert (report["work_items"], report["skill_exposures"]) == (1, 2)
    assert (report["review_unobserved"], report["outcome_unobserved"]) == (1, 1)
    assert report["accepted"] == 0


def test_review_link_is_asserted_not_approval_authority(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="task-001")
    v3.record_candidate(root, session_id="review-001")
    v3.record_review(root, session_id="review-001", work_id="work-001", verdict="BLOCK", taxonomy="review_stale")
    report = v3.report(root)
    assert report["review_count"] == 1
    assert report["review_stale_count"] == 1
    assert report["work_link_class"] == "asserted_work_link"
    assert "approval_authority_count" not in report


def test_cross_work_review_is_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    _session(root, "review-001", "work-002", title="omc-review")
    v3.record_candidate(root, session_id="task-001")
    with pytest.raises(v3.V3Error, match="work_link_mismatch"):
        v3.record_review(root, session_id="review-001", work_id="work-001", verdict="REVISE", taxonomy="scope_gap")


def test_approve_cannot_be_recorded_with_a_fabricated_receipt_hash(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    with pytest.raises(v3.V3Error, match="review_receipt_invalid"):
        v3.record_review(root, session_id="review-001", work_id="work-001", verdict="APPROVE", taxonomy="scope_gap", review_receipt_sha256="a" * 64)


def test_choice_is_single_use_and_tied_to_latest_review(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    review = v3.record_review(root, session_id="review-001", work_id="work-001", verdict="REVISE", taxonomy="scope_gap")
    choice = v3.create_choice(root, work_id="work-001", review_event_id=review["event_id"])
    v3.record_followup(root, choice_id=choice["choice_id"], outcome="correction")
    assert json.loads(v3.ledger_path(root).read_text().splitlines()[-1])["source"] == "operator_reported_unverified"
    with pytest.raises(v3.V3Error, match="choice_consumed"):
        v3.record_followup(root, choice_id=choice["choice_id"], outcome="accepted")
    assert v3.report(root)["correction"] == 1


def test_tampered_ledger_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    v3.record_candidate(root, session_id="task-001")
    path = v3.ledger_path(root)
    path.write_text(path.read_text().replace("omc-task", "omc-plan"))
    with pytest.raises(v3.V3Error, match="v3_ledger_invalid"):
        v3.report(root)


def test_self_hashed_malformed_event_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    event = {
        "generation": "v3", "activation_id": "synthetic-only",
        "event_type": "candidate", "previous_event_sha256": None,
        "work_id": "work-001", "event_id": "forged-001",
    }
    event["event_sha256"] = v3._hash(event)
    v3.ledger_path(root).write_text(json.dumps(event) + "\n")
    with pytest.raises(v3.V3Error, match="v3_ledger_invalid"):
        v3.report(root)


def test_self_hashed_orphan_followup_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    event = {
        "schema_version": 1, "generation": "v3", "activation_id": "synthetic-only",
        "event_id": "followup-001", "event_type": "followup", "work_id": "work-001",
        "observed_at": "2026-09-23T00:00:00Z", "previous_event_sha256": None,
        "choice_id": "choice-001", "review_event_id": "review-001",
        "outcome": "accepted", "source": "operator_reported_unverified",
    }
    event["event_sha256"] = v3._hash(event)
    v3.ledger_path(root).write_text(json.dumps(event) + "\n")
    with pytest.raises(v3.V3Error, match="v3_ledger_invalid"):
        v3.report(root)


def test_self_hashed_review_cannot_link_to_task_candidate(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    candidate = v3.record_candidate(root, session_id="task-001")
    event = {
        "schema_version": 1, "generation": "v3", "activation_id": "synthetic-only",
        "event_id": "review-001", "event_type": "review", "work_id": "work-001",
        "observed_at": "2026-09-23T00:00:01Z",
        "previous_event_sha256": candidate["event_sha256"],
        "session_id": "task-001", "verdict": "REVISE", "taxonomy": "scope_gap",
        "review_receipt_sha256": None, "work_link_class": "asserted_work_link",
    }
    event["event_sha256"] = v3._hash(event)
    with v3.ledger_path(root).open("a") as ledger:
        ledger.write(json.dumps(event) + "\n")
    with pytest.raises(v3.V3Error, match="v3_ledger_invalid"):
        v3.report(root)


def test_review_stale_requires_block_verdict(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    with pytest.raises(v3.V3Error, match="review_stale_verdict_invalid"):
        v3.record_review(root, session_id="review-001", work_id="work-001", verdict="REVISE", taxonomy="review_stale")
    assert v3.report(root)["review_stale_count"] == 0


def test_cli_reports_disabled_without_creating_ledger(tmp_path: Path) -> None:
    root = _root(tmp_path, enabled=False)
    result = subprocess.run(
        [sys.executable, str(Path(v3.__file__)), "report", "--target", str(root)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["state"] == "DISABLED"
    assert not v3.ledger_path(root).exists()


def test_v3_rejects_enabled_config_without_synthetic_marker(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = json.loads(v3.config_path(root).read_text())
    config.pop("status")
    v3.config_path(root).write_text(json.dumps(config))
    with pytest.raises(v3.V3Error, match="v3_config_invalid"):
        v3.report(root)


def test_cli_fixture_flow_shows_observation_not_authority(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    _session(root, "review-001", "work-001", title="omc-review")
    script = str(Path(v3.__file__))
    for args in (
        ("fixture-candidate", "--session-id", "task-001"),
        ("fixture-candidate", "--session-id", "review-001"),
        ("fixture-review", "--session-id", "review-001", "--work-id", "work-001", "--verdict", "REVISE", "--taxonomy", "scope_gap"),
    ):
        run = subprocess.run([sys.executable, script, *args, "--target", str(root)], capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
    review_events = [json.loads(line) for line in v3.ledger_path(root).read_text().splitlines()]
    review_id = review_events[-1]["event_id"]
    choice_run = subprocess.run(
        [sys.executable, script, "fixture-choice", "--target", str(root), "--work-id", "work-001", "--review-event-id", review_id],
        capture_output=True, text=True,
    )
    assert choice_run.returncode == 0, choice_run.stdout + choice_run.stderr
    choice_id = json.loads(choice_run.stdout)["choice_id"]
    followup_run = subprocess.run(
        [sys.executable, script, "fixture-followup", "--target", str(root), "--choice-id", choice_id, "--outcome", "correction"],
        capture_output=True, text=True,
    )
    assert followup_run.returncode == 0, followup_run.stdout + followup_run.stderr
    report = subprocess.run([sys.executable, script, "report", "--target", str(root)], capture_output=True, text=True)
    assert report.returncode == 0
    output = json.loads(report.stdout)
    assert output["state"] == "DRAFT_SYNTHETIC"
    assert output["review_count"] == 1
    assert output["correction"] == 1
    assert "approval_authority_count" not in output
