from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import omc_skill_effectiveness_cohort as cohort
import omc_state
import omc_version


def _event_types(root: Path) -> list[str]:
    return [
        json.loads(line)["event_type"]
        for line in cohort.ledger_path(root).read_text(encoding="utf-8").splitlines()
    ]


def test_candidate_review_and_followup_are_raw_free_and_reported(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")

    candidate = cohort.record_candidate(
        root,
        work_id="work-001",
        skill_id="omc-task",
        policy_profile="full",
        source_identity={"version": "0.3.1", "sha256": "a" * 64},
    )
    cohort.record_review(
        root,
        work_id="work-001",
        verdict="REVISE",
        taxonomy="verification_gap",
    )
    cohort.record_followup(root, work_id="work-001", outcome="correction")

    assert candidate["event_type"] == "candidate"
    assert _event_types(root) == ["candidate", "review", "followup"]
    report = cohort.aggregate([root], now="2026-09-18T00:00:00+00:00")
    assert report["aggregate"]["eligible_candidates"] == 1
    assert report["aggregate"]["correction"] == 1
    assert report["aggregate"]["taxonomy_counts"] == {"verification_gap": 1}
    rendered = json.dumps(report)
    assert str(root) not in rendered
    assert "host-a" not in rendered


def test_silence_is_followup_unobserved_not_success(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")
    cohort.record_candidate(
        root,
        work_id="work-001",
        skill_id="omc-plan",
        policy_profile="lite",
        source_identity={"version": "0.3.1", "sha256": "a" * 64},
    )

    report = cohort.aggregate([root], now="2026-09-18T00:00:00+00:00")

    assert report["aggregate"]["followup_unobserved"] == 1
    assert report["aggregate"]["accepted"] == 0


def test_enrollment_ignores_confirmed_sessions_created_before_opt_in(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    omc_state.record_session(
        root, mode="autopilot", title="omc-plan", request="pre-enrollment request",
        role_ids=["analysis"], completion_action="start", confirmed=True,
    )
    cohort.enable(root, host_identity="host-a")

    report = cohort.aggregate([root])

    assert report["aggregate"]["capture_invalid"] == 0


def test_enable_is_idempotent_without_storing_path_or_host_fingerprints(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    assert cohort.enable(root, host_identity="host-a")["status"] == "enabled"
    assert cohort.enable(root, host_identity="host-b") == {"enabled": True, "status": "unchanged"}

    config = json.loads(cohort.config_path(root).read_text(encoding="utf-8"))
    rendered = json.dumps(cohort.aggregate([root]))
    assert "repo_fingerprint" not in config
    assert "host_fingerprint" not in config
    assert str(root) not in rendered
    assert "host-a" not in rendered
    assert "host-b" not in rendered


def test_enable_concurrent_write_conflict_returns_validated_unchanged_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_once = cohort._write_once

    def competing_enable(path: Path, value: object) -> None:
        write_once(path, value)
        raise FileExistsError(path)

    monkeypatch.setattr(cohort, "_write_once", competing_enable)

    assert cohort.enable(root) == {"enabled": True, "status": "unchanged"}
    assert cohort._validate_config(root)["enabled"] is True


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        (
            {
                "work_id": "work-001",
                "skill_id": "omc-task",
                "policy_profile": "full",
                "source_identity": {"version": "0.3.1", "sha256": "a" * 64, "request": "secret"},
            },
            "source_identity_invalid",
        ),
        (
            {
                "work_id": "work-001",
                "skill_id": "omc-ship",
                "policy_profile": "full",
                "source_identity": {"version": "0.3.1", "sha256": "a" * 64},
            },
            "skill_id_invalid",
        ),
    ],
)
def test_record_rejects_raw_or_unapproved_measurement_values(
    tmp_path: Path,
    kwargs: dict[str, object],
    reason: str,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")

    with pytest.raises(cohort.SkillCohortError, match=reason):
        cohort.record_candidate(root, **kwargs)


def test_tampered_source_is_isolated_and_insufficient_samples_block_tuning(tmp_path: Path) -> None:
    valid = tmp_path / "valid"
    tampered = tmp_path / "tampered"
    valid.mkdir()
    tampered.mkdir()
    for root in (valid, tampered):
        cohort.enable(root, host_identity="host-a")
        cohort.record_candidate(
            root,
            work_id="work-001",
            skill_id="omc-review",
            policy_profile="full",
            source_identity={"version": "0.3.1", "sha256": "a" * 64},
        )
    ledger = cohort.ledger_path(tampered)
    ledger.write_text(ledger.read_text(encoding="utf-8").replace("omc-review", "omc-task"), encoding="utf-8")

    report = cohort.aggregate([valid, tampered], now="2026-09-18T00:00:00+00:00")

    assert report["sources"][1] == {"state": "INTEGRITY_INVALID", "reason_code": "event_hash_invalid"}
    assert report["aggregate"]["eligible_candidates"] == 1
    assert report["aggregate"]["tuning_readiness"]["state"] == "INSUFFICIENT_SAMPLE"
    assert report["aggregate"]["tuning_readiness"]["reason_codes"] == [
        "correction_count_below_5", "eligible_count_below_30", "repository_count_below_2"
    ]


def test_confirmed_core_skill_sessions_emit_candidates_without_request_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "cohort@example.test"), ("config", "user.name", "Cohort")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    cohort.enable(root, host_identity="host-a")

    plan = omc_state.record_session(
        root, mode="autopilot", title="omc-plan", request="secret plan request",
        role_ids=["analysis"], completion_action="start", confirmed=True,
    )
    task = omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="secret task request",
        role_ids=["senior_coding"], work_class="benchmark_maintenance",
        completion_action="start", confirmed=True,
    )
    review = omc_state.record_session(
        root, mode="autopilot", title="omc-review", request="secret review request",
        role_ids=["code_review"], completion_action="preserve-if-present", confirmed=True,
    )

    events = [json.loads(line) for line in cohort.ledger_path(root).read_text(encoding="utf-8").splitlines()]
    assert [(event["skill_id"], event["work_id"]) for event in events] == [
        ("omc-plan", plan["work_id"]),
        ("omc-task", task["work_id"]),
        ("omc-review", task["work_id"]),
    ]
    assert all("secret" not in json.dumps(event) for event in events)


def test_root_cli_sync_session_records_candidate_without_locking(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "cohort@example.test"), ("config", "user.name", "Cohort")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    cohort.enable(root)

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc.py"), "state", "sync-session",
            "--target", str(root), "--mode", "autopilot", "--title", "omc-task",
            "--request", "sync candidate", "--roles", "senior_coding",
            "--work-class", "benchmark_maintenance", "--completion-action", "start",
        ],
        cwd=SCRIPTS.parent, text=True, capture_output=True, check=False, timeout=3,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _event_types(root) == ["candidate"]


def test_root_cli_record_confirm_records_candidate_without_locking(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "cohort@example.test"), ("config", "user.name", "Cohort")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    cohort.enable(root)

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc.py"), "state", "record",
            "--target", str(root), "--mode", "autopilot", "--title", "omc-task",
            "--request", "record candidate", "--roles", "senior_coding",
            "--work-class", "benchmark_maintenance", "--completion-action", "start", "--confirm",
        ],
        cwd=SCRIPTS.parent, text=True, capture_output=True, check=False, timeout=3,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _event_types(root) == ["candidate"]


def test_confirm_session_records_candidate_after_pending_session(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root)
    session = omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="confirm candidate",
        role_ids=["senior_coding"], work_class="benchmark_maintenance",
        completion_action="start", confirmed=False,
    )

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc.py"), "state", "confirm",
            "--target", str(root), "--session-id", str(session["session_id"]),
        ],
        cwd=SCRIPTS.parent, text=True, capture_output=True, check=False, timeout=3,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _event_types(root) == ["candidate"]


def test_review_rejects_stale_pending_work_instead_of_reusing_its_work_id(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "cohort@example.test"), ("config", "user.name", "Cohort")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    tracked = root / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    cohort.enable(root, host_identity="host-a")
    omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="current task",
        role_ids=["senior_coding"], work_class="benchmark_maintenance",
        completion_action="start", confirmed=True,
    )
    tracked.write_text("new head\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "new head"], check=True)

    with pytest.raises(ValueError, match="completion preserve does not match pending baseline"):
        omc_state.record_session(
            root, mode="autopilot", title="omc-review", request="review request",
            role_ids=["code_review"], completion_action="preserve-if-present", confirmed=True,
        )


def test_outcomes_are_counted_once_per_work_not_once_per_skill_exposure(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")
    for skill_id in ("omc-task", "omc-review"):
        cohort.record_candidate(
            root, work_id="work-001", skill_id=skill_id, policy_profile="full",
            source_identity={"version": "0.3.1", "sha256": "a" * 64},
        )
    cohort.record_followup(root, work_id="work-001", outcome="correction")

    report = cohort.aggregate([root])

    assert report["aggregate"]["eligible_candidates"] == 2
    assert report["aggregate"]["eligible_work_items"] == 1
    assert report["aggregate"]["correction"] == 1
    assert report["aggregate"]["followup_unobserved"] == 0
    assert report["aggregate"]["unattributed_work_items"] == 1
    assert report["aggregate"]["measurement_scope"] == "workflow_hypothesis_only"


def test_root_cli_exposes_raw_free_cohort_report(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc.py"), "skill-cohort", "report",
            "--source", str(root),
        ],
        cwd=SCRIPTS.parent,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["network_used"] is False
    assert payload["aggregate"]["eligible_candidates"] == 0


def test_report_marks_missing_candidate_for_confirmed_session_invalid(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")
    omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="secret task request",
        role_ids=["senior_coding"], work_class="benchmark_maintenance",
        completion_action="start", confirmed=True,
    )
    cohort.ledger_path(root).unlink()

    report = cohort.aggregate([root])

    assert report["aggregate"]["capture_invalid"] == 1
    assert "candidate_capture_invalid" in report["aggregate"]["tuning_readiness"]["reason_codes"]


def test_session_capture_uses_installed_identity_when_source_kit_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")
    (root / ".omc" / "install-receipt.json").write_text(
        json.dumps({"omc_version": "0.3.1", "source_sha256": "b" * 64}), encoding="utf-8"
    )
    monkeypatch.setattr(omc_version, "capture_source_identity", lambda _path: (_ for _ in ()).throw(ValueError("no kit")))

    omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="secret task request",
        role_ids=["senior_coding"], work_class="benchmark_maintenance",
        completion_action="start", confirmed=True,
    )

    event = json.loads(cohort.ledger_path(root).read_text(encoding="utf-8"))
    assert event["source_version"] == "0.3.1"
    assert event["source_sha256"] == "b" * 64


def test_session_capture_uses_installed_identity_for_consumer_with_version_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    cohort.enable(root)
    (root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (root / ".omc" / "install-receipt.json").write_text(
        json.dumps({"omc_version": "0.3.1", "source_sha256": "b" * 64}),
        encoding="utf-8",
    )
    monkeypatch.setattr(omc_state, "__file__", str(root / "scripts" / "omc_state.py"))

    omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="consumer task",
        role_ids=["senior_coding"], work_class="benchmark_maintenance",
        completion_action="start", confirmed=True,
    )

    event = json.loads(cohort.ledger_path(root).read_text(encoding="utf-8"))
    assert event["source_version"] == "0.3.1"
    assert event["source_sha256"] == "b" * 64


def _current_pending_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "cohort@example.test"), ("config", "user.name", "Cohort")):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    cohort.enable(root, host_identity="host-a")
    omc_state.record_session(
        root, mode="autopilot", title="omc-task", request="current task",
        role_ids=["senior_coding"], work_class="benchmark_maintenance",
        completion_action="start", confirmed=True,
    )
    return root


def test_root_cli_records_review_against_only_the_current_pending_work(tmp_path: Path) -> None:
    root = _current_pending_root(tmp_path)

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc.py"), "skill-cohort", "record-review",
            "--target", str(root), "--verdict", "REVISE", "--taxonomy", "scope_gap",
        ], cwd=SCRIPTS.parent, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert _event_types(root)[-1] == "review"


def test_root_cli_records_followup_against_only_the_current_pending_work(tmp_path: Path) -> None:
    root = _current_pending_root(tmp_path)

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc.py"), "skill-cohort", "record-followup",
            "--target", str(root), "--outcome", "correction",
        ], cwd=SCRIPTS.parent, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert _event_types(root)[-1] == "followup"


@pytest.mark.parametrize("command", ["record-review", "record-followup"])
def test_root_cli_rejects_minimal_or_stale_pending_receipts(tmp_path: Path, command: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root, host_identity="host-a")
    pending = root / ".omc" / "state"
    pending.mkdir(exist_ok=True)
    (pending / "pending-completion.json").write_text(json.dumps({"work_id": "work-001"}), encoding="utf-8")

    command_args = [
        sys.executable, str(SCRIPTS / "omc.py"), "skill-cohort", command,
        "--target", str(root),
    ]
    if command == "record-review":
        command_args.extend(["--verdict", "REVISE", "--taxonomy", "scope_gap"])
    else:
        command_args.extend(["--outcome", "correction"])
    result = subprocess.run(
        command_args, cwd=SCRIPTS.parent, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["reason_code"] == "pending_work_invalid"


def test_direct_cohort_cli_allows_opt_in_without_host_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc_skill_effectiveness_cohort.py"), "enable",
            "--target", str(root),
        ], cwd=SCRIPTS.parent, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["status"] == "enabled"


def test_root_cli_allows_opt_in_without_host_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc.py"), "skill-cohort", "enable",
            "--target", str(root),
        ], cwd=SCRIPTS.parent, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["status"] == "enabled"


def test_setup_enrollment_is_distinct_from_manual_opt_in_and_is_idempotent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    first = cohort.enable_from_setup(root)
    second = cohort.enable_from_setup(root)

    assert first == {"enabled": True, "status": "enabled", "enrollment_source": "setup"}
    assert second == {"enabled": True, "status": "unchanged", "enrollment_source": "setup"}
    config = json.loads(cohort.config_path(root).read_text(encoding="utf-8"))
    assert config["enrollment_source"] == "setup"


def test_setup_enrollment_never_overwrites_an_invalid_existing_config(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    config_path = root / ".omc" / cohort.CONFIG_NAME
    config_path.parent.mkdir()
    original = b'{"enabled": false}\n'
    config_path.write_bytes(original)

    with pytest.raises(cohort.SkillCohortError, match="config_invalid"):
        cohort.enable_from_setup(root)

    assert config_path.read_bytes() == original


def test_setup_enrollment_reports_the_published_manual_source_after_a_write_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    original_write = cohort._write_once

    def manual_writer(path: Path, value: object) -> None:
        manual = dict(value)
        manual.pop("enrollment_source", None)
        original_write(path, manual)
        raise FileExistsError(path)

    monkeypatch.setattr(cohort, "_write_once", manual_writer)

    assert cohort.enable_from_setup(root) == {
        "enabled": True,
        "status": "unchanged",
        "enrollment_source": "manual",
    }


def test_local_status_reports_setup_enrollment_and_invalid_config_without_reading_raw_work(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable_from_setup(root)

    assert cohort.local_status(root) == {
        "eligible_candidates": 0,
        "enrollment_source": "setup",
        "state": "ENABLED",
    }

    cohort.config_path(root).write_text("{broken", encoding="utf-8")
    assert cohort.local_status(root) == {
        "reason_code": "config_invalid",
        "state": "INTEGRITY_INVALID",
    }


def test_local_status_marks_symlinked_config_as_integrity_invalid(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (root / ".omc").mkdir()
    cohort.config_path(root).symlink_to(outside)

    assert cohort.local_status(root) == {
        "reason_code": "config_not_regular_file",
        "state": "INTEGRITY_INVALID",
    }


def test_direct_cohort_cli_cannot_inject_candidate(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cohort.enable(root)

    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "omc_skill_effectiveness_cohort.py"),
            "record-candidate", "--target", str(root), "--work-id", "forged-work-id",
            "--skill-id", "omc-task", "--policy-profile", "full",
            "--source-version", "0.3.1", "--source-sha256", "a" * 64,
        ], cwd=SCRIPTS.parent, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert not cohort.ledger_path(root).exists()


@pytest.mark.parametrize("command", ["record-review", "record-followup"])
def test_direct_cohort_cli_cannot_bypass_pending_work_binding(
    tmp_path: Path, command: str
) -> None:
    root = _current_pending_root(tmp_path)
    command_args = [
        sys.executable, str(SCRIPTS / "omc_skill_effectiveness_cohort.py"), command,
        "--target", str(root), "--work-id", "forged-work-id",
    ]
    if command == "record-review":
        command_args.extend(["--verdict", "REVISE", "--taxonomy", "scope_gap"])
    else:
        command_args.extend(["--outcome", "correction"])

    result = subprocess.run(
        command_args, cwd=SCRIPTS.parent, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert _event_types(root) == ["candidate"]
