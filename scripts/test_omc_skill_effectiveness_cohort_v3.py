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


def test_archive_preserves_unbound_capture_failure(tmp_path, monkeypatch):
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    root = roots[0]
    path = root / ".omc/state/sessions/unbound-session/session.json"
    path.parent.mkdir(parents=True)
    session = {"session_id": "unbound-session", "title": "omc-task",
               "created_at": "2026-09-30T00:00:00Z",
               "confirmation": {"status": "confirmed"},
               "cohort_capture_v3": {"status": "integrity_invalid",
                                     "reason_code": "installation_audit_failed",
                                     "activation_id": None}}
    path.write_text(json.dumps(session))
    before = path.read_bytes()
    output = tmp_path / "unbound-archive.json"
    assert v3.archive_closed(root, output=output)["state"] == "ARCHIVE_VERIFIED"
    assert v3.verify_archive(output)["state"] == "ARCHIVE_VERIFIED"
    archived = json.loads(output.read_text())
    assert archived["sessions"][0]["cohort_capture_v3"] == session["cohort_capture_v3"]
    assert archived["projection"] == json.loads(outputs[0].read_text())["projection"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("capture", [
    {"status": "recorded", "activation_id": None},
    {"status": "other", "reason_code": "capture_failed", "activation_id": None},
    {"status": "integrity_invalid", "activation_id": None},
    {"status": "integrity_invalid", "reason_code": None, "activation_id": None},
    {"status": "integrity_invalid", "reason_code": "capture_failed", "activation_id": None, "raw": "secret"},
    {"status": "integrity_invalid", "reason_code": "capture_failed", "activation_id": 1},
])
def test_archive_rejects_invalid_unbound_capture(capture):
    with pytest.raises(v3.V3Error, match="archive_session_invalid"):
        v3._validate_archive_session({"confirmed": True, "cohort_capture_v3": capture})


def test_transition_archive_is_independent_and_raw_free(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    for root in roots:
        v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    for root in roots:
        v3.close(root)
    output = tmp_path / "archive.json"
    result = v3.archive_closed(roots[0], output=output)
    assert result["state"] == "ARCHIVE_VERIFIED"
    assert v3.verify_archive(output)["state"] == "ARCHIVE_VERIFIED"
    assert v3.archive_closed(roots[0], output=output) == result
    (roots[0] / ".omc/install-receipt.json").write_text("{}")
    assert v3.verify_archive(output)["state"] == "ARCHIVE_VERIFIED"
    data = json.loads(output.read_text())
    data["projection"]["review_count"] = 999
    output.write_text(json.dumps(data))
    with pytest.raises(v3.V3Error):
        v3.verify_archive(output)


def test_archive_refuses_active_observation_and_preserves_bytes(tmp_path, monkeypatch):
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    v3.enroll(roots[0], roster_path=roster)
    before = v3.config_path(roots[0]).read_bytes()
    with pytest.raises(v3.V3Error, match="archive_requires_closed"):
        v3.archive_closed(roots[0], output=tmp_path / "archive.json")
    assert v3.config_path(roots[0]).read_bytes() == before


def test_transition_blocks_until_joint_activation_and_fresh_work(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    for root in roots:
        v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    for root in roots:
        v3.close(root)
    archives = [tmp_path / (r.name + "-archive.json") for r in roots]
    for root, output in zip(roots, archives):
        v3.archive_closed(root, output=output)
    for root, archive in zip(roots, archives):
        v3.prepare_transition(root, archives=archives, archive=archive)
        assert v3.prefers_v3(root)
        assert v3.report(root)["state"] == "TRANSITION_BLOCKED"
        v3.prepare_transition(root, archives=archives, archive=archive)
    new_roster = tmp_path / "new-roster.json"
    v3.create_roster(targets=roots, output=new_roster, activation_id="operational-002",
                     activation_at="2026-10-03T00:00:00Z")
    _session(roots[0], "before-registration", "existing-work", title="omc-task")
    for root in roots:
        v3.enroll(root, roster_path=new_roster)
        assert v3.report(root)["state"] == "TRANSITION_BLOCKED"
    joint = tmp_path / "joint.json"
    v3.activate_pair(roots, output=joint)
    assert v3.report(roots[0])["state"] == "REGISTERED_NOT_STARTED"
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 4, tzinfo=timezone.utc))
    _session(roots[0], "fresh-001", "fresh-work", title="omc-task")
    path = roots[0] / ".omc/state/sessions/fresh-001/session.json"
    data = json.loads(path.read_text())
    data.update(work_class="implementation", created_at="2026-10-04T00:00:00Z",
                completion_action="start", lineage_root_session_id="fresh-001", lineage_index=0)
    path.write_text(json.dumps(data))
    v3.record_candidate(roots[0], session_id="fresh-001")
    assert v3.report(roots[0])["work_items"] == 1
    _session(roots[0], "old-continued", "existing-work", title="omc-task")
    old_path = roots[0] / ".omc/state/sessions/old-continued/session.json"
    old_data = json.loads(old_path.read_text())
    old_data.update(work_class="implementation", created_at="2026-10-04T00:00:00Z",
                    completion_action="start", lineage_root_session_id="old-continued", lineage_index=0)
    old_path.write_text(json.dumps(old_data))
    with pytest.raises(v3.V3Error, match="candidate_out_of_scope"):
        v3.record_candidate(roots[0], session_id="old-continued")
    _session(roots[0], "fresh-review", "fresh-work", title="omc-review")
    rp = roots[0] / ".omc/state/sessions/fresh-review/session.json"
    rd = json.loads(rp.read_text())
    rd.update(created_at="2026-10-04T00:00:00Z", work_class=None)
    rp.write_text(json.dumps(rd))
    v3.record_candidate(roots[0], session_id="fresh-review")
    assert v3.report(roots[0])["skill_exposures"] == 2
    joint.write_text("{}")
    with pytest.raises(v3.V3Error, match="activation_invalid"):
        v3.report(roots[0])


def _closed_archives(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    for root in roots:
        v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    outputs = [tmp_path / (r.name + "-archive.json") for r in roots]
    for root, output in zip(roots, outputs):
        v3.close(root)
        v3.archive_closed(root, output=output)
    return roots, outputs


@pytest.mark.parametrize("interrupt", [False, True])
def test_transition_keeps_previous_version_on_v3_fail_closed(tmp_path, monkeypatch, interrupt):
    import types
    old = types.ModuleType("previous_v3_bootstrap")
    old.__file__ = v3.__file__
    source = subprocess.check_output(
        ["git", "show", "700f541:scripts/omc_skill_effectiveness_cohort_v3.py"], text=True)
    exec(compile(source, old.__file__, "exec"), old.__dict__)
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    root = roots[0]
    replace = v3.os.replace
    if interrupt:
        def stop_after_barrier(source, destination):
            replace(source, destination)
            if Path(destination) == v3.config_path(root):
                raise OSError("interrupted after atomic barrier")
        monkeypatch.setattr(v3.os, "replace", stop_after_barrier)
        with pytest.raises(OSError, match="atomic barrier"):
            v3.prepare_transition(root, archives=outputs, archive=outputs[0])
        assert old.prefers_v3(root)
        with pytest.raises(old.V3Error, match="v3_config_invalid"):
            old.report(root)
        monkeypatch.setattr(v3.os, "replace", replace)
    v3.prepare_transition(root, archives=outputs, archive=outputs[0])
    assert old.prefers_v3(root), "previous consumer must not select v2"
    with pytest.raises(old.V3Error, match="v3_config_invalid"):
        old.report(root)
    assert v3.report(root)["state"] == "TRANSITION_BLOCKED"


@pytest.mark.parametrize("crash_after", [1, 2])
def test_transition_interrupted_move_resumes_without_fallback(tmp_path, monkeypatch, crash_after):
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    root = roots[0]
    link = v3.os.link
    calls = []
    def interrupted(source, dest, **kwargs):
        result = link(source, dest, **kwargs)
        if Path(dest).parent.name == "cohort-transition-previous":
            calls.append(dest)
            if len(calls) == crash_after:
                raise OSError("injected process interruption")
        return result
    monkeypatch.setattr(v3.os, "link", interrupted)
    with pytest.raises(OSError):
        v3.prepare_transition(root, archives=outputs, archive=outputs[0])
    assert v3.prefers_v3(root)
    assert v3.report(root)["state"] == "TRANSITION_BLOCKED"
    monkeypatch.setattr(v3.os, "link", link)
    v3.prepare_transition(root, archives=outputs, archive=outputs[0])
    assert json.loads(v3.config_path(root).read_text()) == v3._TRANSITION_BARRIER
    assert v3.verify_archive(outputs[0])["state"] == "ARCHIVE_VERIFIED"


def test_transition_cli_and_session_capture_surface(tmp_path, monkeypatch, capsys):
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    root = roots[0]
    assert v3.main(["prepare-transition", "--target", str(root), "--archive", str(outputs[0]),
                    "--pair-archive", str(outputs[0]), "--pair-archive", str(outputs[1])]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "TRANSITION_BLOCKED"
    assert v3.main(["report", "--target", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "TRANSITION_BLOCKED"
    (root / ".omc/skill-effectiveness-cohort-v2.json").write_text("{}")
    result = omc_state._record_skill_effectiveness_candidate(root, {
        "title": "omc-review", "work_id": "work-001", "session_id": "review-001",
    })
    assert result["status"] == "integrity_invalid"
    assert not (root / ".omc/skill-effectiveness-cohort-v2.jsonl").exists()


def test_archive_failed_save_leaves_live_state_and_symlink_refused(tmp_path, monkeypatch):
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    root = roots[0]
    before = v3.config_path(root).read_bytes()
    def fail(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(v3, "_durable_write_once", fail)
    with pytest.raises(OSError):
        v3.archive_closed(root, output=tmp_path / "failed.json")
    assert v3.config_path(root).read_bytes() == before
    symlink = tmp_path / "link.json"
    symlink.symlink_to(outputs[0])
    with pytest.raises(v3.V3Error):
        v3.verify_archive(symlink)


def test_transition_process_exit_resumes_exact_preserved_files(tmp_path, monkeypatch):
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    code = """
import sys, os
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
import omc_skill_effectiveness_cohort_v3 as v
import omc_install_audit
omc_install_audit.audit_target = lambda *a, **k: {'installed_integrity_status':'ok'}
v._now = lambda: datetime(2026,10,2,tzinfo=timezone.utc)
link = os.link
def interrupted(source, dest, **kwargs):
    link(source,dest,**kwargs)
    if Path(dest).parent.name == 'cohort-transition-previous':
        os._exit(90)
os.link = interrupted
v.prepare_transition(Path(sys.argv[2]), archives=[Path(sys.argv[3]),Path(sys.argv[4])],archive=Path(sys.argv[3]))
"""
    result = subprocess.run([sys.executable, "-c", code, str(Path(v3.__file__).parent),
                             str(roots[0]), str(outputs[0]), str(outputs[1])])
    assert result.returncode == 90
    assert v3.report(roots[0])["state"] == "TRANSITION_BLOCKED"
    v3.prepare_transition(roots[0], archives=outputs, archive=outputs[0])
    assert v3.verify_archive(outputs[0])["state"] == "ARCHIVE_VERIFIED"


def test_joint_activation_rejects_partial_enrollment_and_late_t0(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    for root, output in zip(roots, outputs):
        v3.prepare_transition(root, archives=outputs, archive=output)
    roster = tmp_path / "next-roster.json"
    v3.create_roster(targets=roots, output=roster, activation_id="next-activation",
                     activation_at="2026-10-03T00:00:00Z")
    v3.enroll(roots[0], roster_path=roster)
    with pytest.raises(v3.V3Error):
        v3.activate_pair(roots, output=tmp_path / "joint.json")
    assert not (tmp_path / "joint.json").exists()
    v3.enroll(roots[1], roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 4, tzinfo=timezone.utc))
    with pytest.raises(v3.V3Error, match="fresh_t0_required"):
        v3.activate_pair(roots, output=tmp_path / "joint.json")
    assert v3.report(roots[0])["state"] == "TRANSITION_BLOCKED"


def test_transition_rejects_unknown_work_start(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    for root, output in zip(roots, outputs):
        v3.prepare_transition(root, archives=outputs, archive=output)
    roster = tmp_path / "next-roster.json"
    v3.create_roster(targets=roots, output=roster, activation_id="next-activation",
                     activation_at="2026-10-03T00:00:00Z")
    for root in roots:
        v3.enroll(root, roster_path=roster)
    v3.activate_pair(roots, output=tmp_path / "joint.json")
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 4, tzinfo=timezone.utc))
    _session(roots[0], "unknown-001", "unknown-work", title="omc-task")
    path = roots[0] / ".omc/state/sessions/unknown-001/session.json"
    data = json.loads(path.read_text())
    data.update(work_class="implementation", created_at="2026-10-04T00:00:00Z")
    path.write_text(json.dumps(data))
    with pytest.raises(v3.V3Error, match="candidate_out_of_scope"):
        v3.record_candidate(roots[0], session_id="unknown-001")


def test_actual_guard_session_start_does_not_fall_back_during_transition(tmp_path, monkeypatch):
    roots, outputs = _closed_archives(tmp_path, monkeypatch)
    root = roots[0]
    v3.prepare_transition(root, archives=outputs, archive=outputs[0])
    (root / ".omc/skill-effectiveness-cohort-v2.json").write_text("{}")
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.com", "commit", "--allow-empty", "-qm", "base"], check=True)
    command = [sys.executable, str(Path(v3.__file__).parent / "omc_guard.py"),
               "sync-require", "--target", str(root), "--mode", "autopilot", "--title", "omc-task",
               "--request", "synthetic transition check", "--roles", "senior_coding",
               "--work-class", "synthetic", "--completion-action", "start", "--for", "task"]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    hook = subprocess.run([sys.executable, str(Path(v3.__file__).parent / "omc.py"),
                           "hook", "session_start", "--target", str(root)], capture_output=True, text=True)
    assert hook.returncode == 0, hook.stdout + hook.stderr
    sessions = list((root / ".omc/state/sessions").glob("*/session.json"))
    capture = json.loads(sessions[-1].read_text())["cohort_capture_v3"]
    assert capture["status"] == "integrity_invalid"
    assert not (root / ".omc/skill-effectiveness-cohort-v2.jsonl").exists()


def _operational_pair(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import omc_install_audit
    roots = [tmp_path / name for name in ("alpha", "beta")]
    for root in roots:
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                        "https://example.com/" + root.name + ".git"], check=True)
        (root / ".omc").mkdir()
        (root / ".omc/install-receipt.json").write_text(json.dumps({
            "omc_version": "0.3.4", "source_sha256": "a" * 64,
        }))
    monkeypatch.setattr(omc_install_audit, "audit_target", lambda *a, **k: {
        "installed_integrity_status": "ok", "verification_status": "ok",
    })
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 9, 30, tzinfo=timezone.utc), raising=False)
    roster = tmp_path / "roster.json"
    v3.create_roster(targets=roots, output=roster, activation_id="operational-001",
                     activation_at="2026-10-01T00:00:00Z")
    return roots, roster


def test_operational_enrollment_is_write_once_and_preserves_v2(tmp_path, monkeypatch):
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    legacy = root / ".omc/skill-effectiveness-cohort-v2.jsonl"
    legacy.write_bytes(b"historical-v2\n")
    v3.enroll(root, roster_path=roster)
    original = v3.config_path(root).read_bytes()
    assert v3.enroll(root, roster_path=roster)["status"] == "unchanged"
    assert v3.config_path(root).read_bytes() == original
    assert legacy.read_bytes() == b"historical-v2\n"
    assert v3.report(root)["state"] == "REGISTERED_NOT_STARTED"


def test_operational_work_lifecycle_counts_task_review_together(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    for sid, title in [("task-001", "omc-task"), ("review-001", "omc-review"),
                       ("task-002", "omc-task"), ("review-002", "omc-review")]:
        _session(root, sid, "work-001", title=title)
        path = root / ".omc/state/sessions" / sid / "session.json"
        data = json.loads(path.read_text())
        data.update(work_class="implementation" if title == "omc-task" else None,
                    created_at="2026-10-02T00:00:00Z")
        path.write_text(json.dumps(data))
        v3.record_candidate(root, session_id=sid)
    v3.record_review(root, session_id="review-001", work_id="work-001",
                     verdict="REVISE", taxonomy="requirement_gap")
    receipt = root / "receipt.json"
    receipt.write_text("{}")
    monkeypatch.setattr(v3.omc_review_snapshot, "load_review_receipt", lambda *a: {
        "receipt_sha256": "b" * 64, "review_verdict": "APPROVE",
    })
    review = v3.record_review(root, session_id="review-002", work_id="work-001",
                              verdict="APPROVE", taxonomy="verification_gap",
                              review_receipt_path=receipt, review_receipt_sha256="b" * 64)
    choice = v3.create_choice(root, work_id="work-001", review_event_id=review["event_id"])
    v3.record_followup(root, choice_id=choice["choice_id"], outcome="accepted")
    report = v3.report(root)
    assert report["state"] == "ACTIVE_NATURAL_OBSERVATION"
    assert (report["work_items"], report["review_count"], report["accepted"]) == (1, 2, 1)
    assert report["review_churn_work_items"] == 1
    assert report["outcome_unobserved"] == 0


@pytest.mark.parametrize("mutation", ["receipt", "origin", "roster_digest"])
def test_operational_binding_changes_fail_closed(tmp_path, monkeypatch, mutation):
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    if mutation == "receipt":
        (root / ".omc/install-receipt.json").write_text("{}")
    elif mutation == "origin":
        subprocess.run(["git", "-C", str(root), "remote", "set-url", "origin",
                        "https://example.com/other.git"], check=True)
    else:
        config = json.loads(v3.config_path(root).read_text())
        config["roster_sha256"] = "f" * 64
        v3.config_path(root).write_text(json.dumps(config))
    with pytest.raises(v3.V3Error):
        v3.report(root)
    assert not v3.ledger_path(root).exists()


def test_operational_enrollment_rejects_third_target_and_late_t0(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    subprocess.run(["git", "-C", str(roots[1]), "remote", "set-url", "origin",
                    "https://example.com/third.git"], check=True)
    with pytest.raises(v3.V3Error, match="roster_target_not_allowed"):
        v3.enroll(roots[1], roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    with pytest.raises(v3.V3Error, match="fresh_t0_required"):
        v3.enroll(roots[0], roster_path=roster)


def test_operational_capture_takes_precedence_over_preserved_v2(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    (root / ".omc/skill-effectiveness-cohort-v2.json").write_bytes(b"{}")
    legacy = root / ".omc/skill-effectiveness-cohort-v2.jsonl"
    legacy.write_bytes(b"historical-v2\n")
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    # record_session uses the actual current time; set T0 just before it instead.
    monkeypatch.setattr(omc_state, "_iso_now", lambda: "2026-10-02T00:00:00+00:00")
    session = omc_state.record_session(root, mode="autopilot", title="omc-task",
        request="natural implementation", role_ids=["senior_coding"],
        work_class="implementation", completion_action="start", confirmed=True)
    assert session["cohort_capture_v3"]["status"] == "recorded"
    assert v3.report(root)["work_items"] == 1
    assert legacy.read_bytes() == b"historical-v2\n"


def test_operational_close_blocks_append_without_touching_v2(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    v3.close(root)
    assert v3.report(root)["state"] == "CLOSED"
    with pytest.raises(v3.V3Error, match="v3_closed"):
        v3._record(root, event_type="candidate", work_id="work-001",
                   payload={"session_id": "task-001", "skill_id": "omc-task"}, check=lambda e: None)


@pytest.mark.parametrize("closed", [False, True])
def test_operational_report_uses_one_locked_ledger_snapshot(tmp_path, monkeypatch, closed):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    _session(root, "task-001", "work-001", title="omc-task")
    path = root / ".omc/state/sessions/task-001/session.json"
    session = json.loads(path.read_text())
    session.update(work_class="implementation", created_at="2026-10-02T00:00:00Z")
    path.write_text(json.dumps(session))
    v3.record_candidate(root, session_id="task-001")
    if closed:
        v3.close(root)
    original = v3._events
    reads = []
    def read_locked(*args, **kwargs):
        key = str(omc_state._lock_path(root).resolve())
        assert key in omc_state._LOCK_REGISTRY, "report reads ledger without the writer lock"
        events = original(*args, **kwargs)
        reads.append(events)
        return events
    monkeypatch.setattr(v3, "_events", read_locked)
    result = v3.report(root)
    assert len(reads) == 1, "closure validation must reuse the counted ledger snapshot"
    assert result["work_items"] == result["skill_exposures"] == 1
    assert result["state"] == ("CLOSED" if closed else v3.ACTIVE)


def test_operational_aggregate_requires_complete_roster(tmp_path, monkeypatch):
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    for root in roots:
        v3.enroll(root, roster_path=roster)
    assert v3.aggregate(roots, roster_path=roster)["aggregate"]["work_items"] == 0
    with pytest.raises(v3.V3Error, match="pilot_roster_incomplete"):
        v3.aggregate(roots[:1], roster_path=roster)


def test_operational_legacy_recording_is_blocked(tmp_path, monkeypatch):
    import omc_skill_effectiveness_cohort as legacy
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    ledger = root / ".omc/skill-effectiveness-cohort-v2.jsonl"
    ledger.write_bytes(b"preserved\n")
    with pytest.raises(legacy.SkillCohortError, match="v3_generation_required"):
        legacy._record_v2(root, event_type="review", work_id="a" * 32,
                          payload={"verdict": "APPROVE", "taxonomy": "scope_gap"})
    assert ledger.read_bytes() == b"preserved\n"


def test_operational_old_work_cannot_be_backfilled(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    _session(root, "task-old", "work-old", title="omc-task")
    v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    with pytest.raises(v3.V3Error, match="candidate_out_of_scope"):
        v3.record_candidate(root, session_id="task-old")
    assert not v3.ledger_path(root).exists()


def test_operational_roster_rejects_failed_install_audit(tmp_path, monkeypatch):
    import omc_install_audit
    roots, _roster = _operational_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(omc_install_audit, "audit_target", lambda *a, **k: {
        "installed_integrity_status": "failed",
    })
    with pytest.raises(v3.V3Error, match="installation_audit_failed"):
        v3.create_roster(targets=roots, output=tmp_path / "other.json",
            activation_id="other-001", activation_at="2026-10-01T00:00:00Z")
    assert not (tmp_path / "other.json").exists()


def test_operational_cli_real_install_register_enroll_report(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("OMC_INSTALLATION_REGISTRY_DIR", str(tmp_path / "registry"))
    roots = [tmp_path / name for name in ("alpha", "beta")]
    for root in roots:
        root.mkdir()
        # These project-preserved prompt files are deliberately ignored by this
        # fixture, as required by the existing installation visibility contract.
        (root / ".gitignore").write_text("PROMPT_COMMON.md\nPROMPT_COMMON_LEAN.md\n")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                        "https://example.com/" + root.name + ".git"], check=True)
        result = subprocess.run([sys.executable, str(Path(v3.__file__).with_name("omc.py")),
                                 "setup", "--target", str(root)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    roster = tmp_path / "roster.json"
    def cli(*args):
        result = subprocess.run([sys.executable, str(Path(v3.__file__)), *args],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    t0 = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    cli("create-roster", "--target", str(roots[0]), "--target", str(roots[1]),
        "--output", str(roster), "--activation-id", "cli-operational", "--activation-at", t0)
    for root in roots:
        cli("enroll", "--target", str(root), "--roster", str(roster))
        assert cli("report", "--target", str(root))["state"] == "REGISTERED_NOT_STARTED"
    result = cli("aggregate", "--source", str(roots[0]), "--source", str(roots[1]), "--roster", str(roster))
    assert result["aggregate"]["work_items"] == 0
    assert result["aggregate"]["product_effect"] == "NOT_PROVEN"


def test_operational_before_t0_rejects_append(tmp_path, monkeypatch):
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    with pytest.raises(v3.V3Error, match="v3_not_started"):
        v3._record(root, event_type="candidate", work_id="work-001",
            payload={"session_id": "task-001", "skill_id": "omc-task"}, check=lambda e: None)
    assert not v3.ledger_path(root).exists()


def test_operational_closure_tampering_is_visible(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    roots, roster = _operational_pair(tmp_path, monkeypatch)
    root = roots[0]
    v3.enroll(root, roster_path=roster)
    monkeypatch.setattr(v3, "_now", lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))
    v3.close(root)
    path = root / ".omc" / v3.CLOSURE_NAME
    closure = json.loads(path.read_text())
    closure["event_count"] = 1
    path.write_text(json.dumps(closure))
    with pytest.raises(v3.V3Error, match="v3_closure_invalid"):
        v3.report(root)


def test_malformed_config_status_is_structured_failure(tmp_path):
    root = _root(tmp_path)
    config = json.loads(v3.config_path(root).read_text())
    config["status"] = []
    v3.config_path(root).write_text(json.dumps(config))
    with pytest.raises(v3.V3Error, match="v3_config_invalid"):
        v3.report(root)


@pytest.mark.parametrize("last_verdict,expected", [("APPROVE", 1), ("REVISE", 0)])
def test_correction_metric_binds_to_the_selected_latest_review(tmp_path, monkeypatch, last_verdict, expected):
    root = _root(tmp_path)
    _session(root, "task-001", "work-001", title="omc-task")
    v3.record_candidate(root, session_id="task-001")
    monkeypatch.setattr(v3.omc_review_snapshot, "load_review_receipt", lambda *a: {
        "receipt_sha256": "b" * 64, "review_verdict": "APPROVE",
    })
    receipt = root / "receipt.json"
    receipt.write_text("{}")
    for index, verdict in enumerate(["APPROVE", last_verdict]):
        sid = "review-00" + str(index)
        _session(root, sid, "work-001", title="omc-review")
        v3.record_candidate(root, session_id=sid)
        review = v3.record_review(root, session_id=sid, work_id="work-001", verdict=verdict,
            taxonomy="verification_gap", review_receipt_path=receipt if verdict == "APPROVE" else None,
            review_receipt_sha256="b" * 64 if verdict == "APPROVE" else None)
    choice = v3.create_choice(root, work_id="work-001", review_event_id=review["event_id"])
    v3.record_followup(root, choice_id=choice["choice_id"], outcome="correction")
    assert v3.report(root)["correction_after_approved_review"] == expected


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
