"""Synthetic fixtures for the dormant v3 work-lifecycle observation contract."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_skill_effectiveness_cohort_v3 as v3
import omc_state


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
        "work_class": "synthetic", "created_at": "2026-09-24T00:00:00Z",
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


def test_capture_failure_is_visible_without_counting_a_prior_candidate_as_success(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    _session(root, "task-002", "work-001", title="omc-task")
    v3.record_candidate(root, session_id="task-001")
    failed_path = root / ".omc" / "state" / "sessions" / "task-002" / "session.json"
    failed = json.loads(failed_path.read_text())
    failed["cohort_capture_v3"] = {"status": "integrity_invalid", "reason_code": "capture_failed", "activation_id": "synthetic-only"}
    failed_path.write_text(json.dumps(failed))
    report = v3.report(root)
    assert report["work_items"] == 1
    assert report["skill_exposures"] == 1
    assert report["capture_sessions"] == {
        "recorded": 0, "integrity_invalid": 1, "status_unobserved": 1,
        "failure_reason_counts": {"capture_failed": 1},
    }


def test_cli_reports_capture_failure_and_work_review_separately(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    _session(root, "review-001", "work-001", title="omc-review")
    _session(root, "task-002", "work-001", title="omc-task")
    v3.record_candidate(root, session_id="task-001")
    v3.record_candidate(root, session_id="review-001")
    v3.record_review(root, session_id="review-001", work_id="work-001", verdict="REVISE", taxonomy="scope_gap")
    failed_path = root / ".omc" / "state" / "sessions" / "task-002" / "session.json"
    failed = json.loads(failed_path.read_text())
    failed["cohort_capture_v3"] = {"status": "integrity_invalid", "reason_code": "capture_failed", "activation_id": "synthetic-only"}
    failed_path.write_text(json.dumps(failed))
    result = subprocess.run(
        [sys.executable, str(Path(v3.__file__)), "report", "--target", str(root)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)
    assert (output["work_items"], output["skill_exposures"], output["review_count"]) == (1, 2, 1)
    assert output["capture_sessions"]["integrity_invalid"] == 1
    assert output["capture_sessions"]["status_unobserved"] == 2
    assert output["outcome_unobserved"] == 1


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


def test_v3_only_confirmed_session_auto_records_candidate(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="isolated task",
        role_ids=["senior_coding"], work_class="synthetic",
        completion_action="start", confirmed=True,
    )
    assert v3.report(root)["work_items"] == 1
    assert json.loads(v3.ledger_path(root).read_text().splitlines()[0])["session_id"] == session["session_id"]
    omc_state.confirm_session(root, session_id=session["session_id"])
    assert v3.report(root)["skill_exposures"] == 1
    assert v3.report(root)["capture_sessions"]["recorded"] == 1


def test_v3_excludes_implementation_task_without_capture_failure(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="real implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )
    assert "cohort_capture_v3" not in session
    assert not v3.ledger_path(root).exists()
    assert v3.report(root)["work_items"] == 0
    assert v3.report(root)["capture_sessions"]["integrity_invalid"] == 0
    with pytest.raises(v3.V3Error, match="candidate_out_of_scope"):
        v3.record_candidate(root, session_id=session["session_id"])


def test_report_rejects_legacy_implementation_candidate_without_rewriting_ledger(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    v3.record_candidate(root, session_id="task-001")
    _session(root, "task-002", "work-002", title="omc-task")
    path = root / ".omc" / "state" / "sessions" / "task-002" / "session.json"
    implementation = json.loads(path.read_text())
    implementation["work_class"] = "implementation"
    path.write_text(json.dumps(implementation))
    # Recreate the valid hash-chained event that an older v3 recorder accepted.
    v3._record(root, event_type="candidate", work_id="work-002",
               payload={"session_id": "task-002", "skill_id": "omc-task"},
               check=lambda _events: None)
    original = v3.ledger_path(root).read_bytes()
    with pytest.raises(v3.V3Error, match="candidate_out_of_scope"):
        v3.report(root)
    assert v3.ledger_path(root).read_bytes() == original


def test_invalid_capture_status_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    path = root / ".omc" / "state" / "sessions" / "task-001" / "session.json"
    session = json.loads(path.read_text())
    session["cohort_capture_v3"] = {"status": "recorded"}
    path.write_text(json.dumps(session))
    with pytest.raises(v3.V3Error, match="v3_session_capture_invalid"):
        v3.report(root)


def test_confirmed_session_without_candidate_or_capture_status_is_unobserved(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    assert v3.report(root)["capture_sessions"]["status_unobserved"] == 1


def test_old_activation_failure_does_not_count_in_current_capture(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    path = root / ".omc" / "state" / "sessions" / "task-001" / "session.json"
    session = json.loads(path.read_text())
    session["cohort_capture_v3"] = {
        "status": "integrity_invalid", "reason_code": "capture_failed", "activation_id": "prior-synthetic",
    }
    path.write_text(json.dumps(session))
    assert v3.report(root)["capture_sessions"] == {
        "recorded": 0, "integrity_invalid": 0, "status_unobserved": 1,
        "failure_reason_counts": {},
    }


def test_v2_and_v3_configs_do_not_dual_record(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / ".omc" / "skill-effectiveness-cohort-v2.json").write_text("{}")
    v2_ledger = root / ".omc" / "skill-effectiveness-cohort-v2.jsonl"
    v2_ledger.write_bytes(b"existing-v2-bytes\n")
    omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="isolated task",
        role_ids=["senior_coding"], work_class="synthetic",
        completion_action="start", confirmed=True,
    )
    assert not v3.ledger_path(root).exists()
    assert v2_ledger.read_bytes() == b"existing-v2-bytes\n"


def test_invalid_opt_in_capture_records_reason_without_v3_event(tmp_path: Path) -> None:
    root = _root(tmp_path)
    v3.config_path(root).write_text("{}")
    session = omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="isolated task",
        role_ids=["senior_coding"], work_class="synthetic",
        completion_action="start", confirmed=True,
    )
    assert session["cohort_capture_v3"] == {
        "status": "integrity_invalid", "reason_code": "v3_config_invalid", "activation_id": None,
    }
    assert not v3.ledger_path(root).exists()


def test_review_approval_requires_explicit_immutable_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    receipt_path = root / ".omc" / "state" / "review-snapshots" / "receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("{}")
    monkeypatch.setattr(v3.omc_review_snapshot, "load_review_receipt", lambda *_args: {
        "receipt_sha256": "a" * 64, "review_verdict": "APPROVE",
    }, raising=False)
    review = v3.record_review(
        root, session_id="review-001", work_id="work-001", verdict="APPROVE",
        taxonomy="verification_gap", review_receipt_sha256="a" * 64,
        review_receipt_path=receipt_path,
    )
    assert review["review_receipt_sha256"] == "a" * 64


def test_nonapproval_cli_records_choice_only_for_current_review_session(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for args in (("init", "-q"), ("config", "user.email", "test@example.test"),
                 ("config", "user.name", "Test")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    (root / ".git" / "info" / "exclude").write_text(".omc/\n")
    (root / "README.md").write_text("base\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    task = omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="fixture task",
        role_ids=["senior_coding"], work_class="synthetic",
        completion_action="start", confirmed=True,
    )
    review = omc_state.record_session(
        root, mode="autopilot", title="omc-review", request="fixture review",
        role_ids=["code_review"], completion_action="preserve-if-present", confirmed=True,
    )
    command = [sys.executable, str(Path(v3.__file__)), "record-explicit-review", "--target", str(root),
               "--session-id", review["session_id"], "--work-id", task["work_id"],
               "--verdict", "BLOCK", "--taxonomy", "review_stale"]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["choice_id"]
    assert v3.report(root)["review_stale_count"] == 1
    assert v3.report(root)["capture_sessions"] == {
        "recorded": 2, "integrity_invalid": 0, "status_unobserved": 0,
        "failure_reason_counts": {},
    }
    again = subprocess.run(command, capture_output=True, text=True)
    assert again.returncode == 2
    assert json.loads(again.stdout)["reason_code"] == "review_session_already_recorded"


def test_retry_after_choice_write_failure_recovers_nonapproval_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    (root / ".omc" / "state" / "latest.json").write_text(json.dumps({
        "latest_confirmed_session_id": "review-001",
    }))
    original_choice = v3.create_choice
    def fail_choice(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise v3.V3Error("choice_write_failed")
    monkeypatch.setattr(v3, "create_choice", fail_choice)
    with pytest.raises(v3.V3Error, match="choice_write_failed"):
        v3.record_explicit_nonapproval_review(
            root, session_id="review-001", work_id="work-001",
            verdict="BLOCK", taxonomy="review_stale",
        )
    assert v3.report(root)["review_count"] == 1
    monkeypatch.setattr(v3, "create_choice", original_choice)
    recovered = v3.record_explicit_nonapproval_review(
        root, session_id="review-001", work_id="work-001",
        verdict="BLOCK", taxonomy="review_stale",
    )
    assert recovered["choice_id"]
    assert v3.report(root)["review_count"] == 1
    assert v3.record_followup(root, choice_id=recovered["choice_id"], outcome="correction")
    with pytest.raises(v3.V3Error, match="review_session_already_recorded"):
        v3.record_explicit_nonapproval_review(
            root, session_id="review-001", work_id="work-001",
            verdict="REVISE", taxonomy="scope_gap",
        )


def test_resume_choice_cli_recovers_partial_review_without_duplicate(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    (root / ".omc" / "state" / "latest.json").write_text(json.dumps({
        "latest_confirmed_session_id": "review-001",
    }))
    v3.record_review(root, session_id="review-001", work_id="work-001",
                     verdict="BLOCK", taxonomy="review_stale")
    command = [sys.executable, str(Path(v3.__file__)), "resume-review-choice",
               "--target", str(root), "--session-id", "review-001"]
    (root / ".omc" / "state" / "latest.json").write_text(json.dumps({
        "latest_confirmed_session_id": "another-review",
    }))
    wrong_session = subprocess.run(command, capture_output=True, text=True)
    assert wrong_session.returncode == 2
    assert json.loads(wrong_session.stdout)["reason_code"] == "review_session_not_current"
    (root / ".omc" / "state" / "latest.json").write_text(json.dumps({
        "latest_confirmed_session_id": "review-001",
    }))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["choice_id"]
    assert v3.report(root)["review_count"] == 1
    repeated = subprocess.run(command, capture_output=True, text=True)
    assert repeated.returncode == 2
    assert json.loads(repeated.stdout)["reason_code"] == "choice_duplicate"


def test_resume_choice_cli_reports_io_error_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    (root / ".omc" / "state" / "latest.json").write_text(json.dumps({
        "latest_confirmed_session_id": "review-001",
    }))
    v3.record_review(root, session_id="review-001", work_id="work-001",
                     verdict="BLOCK", taxonomy="review_stale")
    def fail_choice(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise OSError("simulated choice write failure")
    monkeypatch.setattr(v3, "create_choice", fail_choice)
    code = v3.main(["resume-review-choice", "--target", str(root), "--session-id", "review-001"])
    assert code == 2
    assert json.loads(capsys.readouterr().out) == {
        "generation": "v3", "state": "CAPTURE_FAILED", "reason_code": "capture_io_error",
    }
    assert v3.report(root)["review_count"] == 1


def test_retry_after_choice_write_failure_preserves_approval_receipt_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    _session(root, "review-001", "work-001", title="omc-review")
    v3.record_candidate(root, session_id="review-001")
    (root / ".omc" / "state" / "latest.json").write_text(json.dumps({
        "latest_confirmed_session_id": "review-001",
    }))
    receipt_path = root / ".omc" / "state" / "review-snapshots" / "receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("{}")
    monkeypatch.setattr(v3.omc_review_snapshot, "load_review_receipt", lambda *_args: {
        "receipt_sha256": "a" * 64, "review_verdict": "APPROVE",
    })
    original_choice = v3.create_choice
    monkeypatch.setattr(v3, "create_choice", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        v3.V3Error("choice_write_failed")))
    kwargs = {
        "session_id": "review-001", "taxonomy": "verification_gap", "verdict": "APPROVE",
        "review_receipt_path": receipt_path, "review_receipt_sha256": "a" * 64,
    }
    with pytest.raises(v3.V3Error, match="choice_write_failed"):
        v3.record_completed_review(root, **kwargs)
    monkeypatch.setattr(v3, "create_choice", original_choice)
    with pytest.raises(v3.V3Error, match="review_receipt_invalid"):
        v3.record_completed_review(root, **{**kwargs, "review_receipt_sha256": "b" * 64})
    result = v3.record_completed_review(root, **kwargs)
    assert result["choice_id"]
    assert v3.report(root)["review_count"] == 1
