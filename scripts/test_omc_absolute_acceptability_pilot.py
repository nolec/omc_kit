from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
from subprocess import CompletedProcess, run
from unittest.mock import Mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_absolute_acceptability_pilot as pilot
import omc_plan_candidate_universe as candidate_universe
import omc_state


UTC = timezone.utc


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    return private_key, public_key


def _key_file(path: Path, private_key: Ed25519PrivateKey) -> Path:
    path.write_text(
        base64.b64encode(private_key.private_bytes_raw()).decode(), encoding="utf-8"
    )
    path.chmod(0o600)
    return path


def _execution(
    private_key: Ed25519PrivateKey,
    work_id: str,
    executor_start_receipt_sha256: str = "e" * 64,
) -> dict[str, object]:
    return pilot.build_execution_receipt(
        private_key=private_key,
        work_id=work_id,
        request=f"implement {work_id}",
        raw_jsonl='{"type":"turn.completed","usage":{"input_tokens":1}}\n',
        exit_code=0,
        produced_at="2026-09-10T01:00:00+00:00",
        executor_start_receipt_sha256=executor_start_receipt_sha256,
    )


def _study(tmp_path: Path) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]], dict[str, Ed25519PrivateKey]]:
    execution_key, execution_public = _keypair()
    evidence_key, evidence_public = _keypair()
    source_key, source_public = _keypair()
    repo_a_key, repo_a_public = _keypair()
    repo_b_key, repo_b_public = _keypair()
    repository_paths = {
        repo_id: tmp_path / repo_id for repo_id in ("repo-a", "repo-b")
    }
    for path in repository_paths.values():
        path.mkdir()
    registration = {
        "schema_version": pilot.SCHEMA,
        "study_id": "acceptability-v3-test",
        "status": "registered",
        "starts_at": "2026-09-10T00:00:00+00:00",
        "ends_at": "2026-09-24T00:00:00+00:00",
        "selection_count": 10,
        "minimum_repositories": 2,
        "correction_window_hours": 24,
        "executor_start_recording_max_delay_seconds": 60,
        "executor_start_execution_digest_binding_required": True,
        "executor_start_precedes_execution_required": True,
        "incomplete_failure_evidence_allowed": True,
        "incomplete_case_shape_enforced": True,
        "blank_raw_followup_forbidden": True,
        "trusted_execution_public_key": execution_public,
        "trusted_evidence_public_key": evidence_public,
        "trusted_source_public_key": source_public,
        "trusted_start_public_keys": {
            "repo-a": repo_a_public,
            "repo-b": repo_b_public,
        },
        "trusted_repository_roots": {
            repo_id: hashlib.sha256(str(path.resolve()).encode()).hexdigest()
            for repo_id, path in repository_paths.items()
        },
        "registered_repository_paths": {
            repo_id: str(path.resolve()) for repo_id, path in repository_paths.items()
        },
    }
    starts: list[dict[str, object]] = []
    cases: list[dict[str, object]] = []
    base = datetime(2026, 9, 10, tzinfo=UTC)
    for index in range(10):
        work_id = f"w-{index:02d}"
        started = base + timedelta(minutes=index)
        completed = started + timedelta(hours=1)
        request = f"implement {work_id}"
        repo_id = "repo-a" if index < 5 else "repo-b"
        session_id = f"session-{index:02d}"
        session = {
            "session_id": session_id,
            "request": request,
            "work_class": "implementation",
            "created_at": (started - timedelta(days=1)).isoformat(),
            "git": {"head": "a" * 40},
            "work_id": work_id,
            "lineage_root_session_id": session_id,
            "lineage_previous_session_id": None,
            "lineage_index": 0,
        }
        repo_key = repo_a_key if repo_id == "repo-a" else repo_b_key
        lock_draft = candidate_universe.prepare_work_class_lock_receipt(session)
        work_class_lock = candidate_universe.seal_work_class_lock_receipt(
            lock_draft,
            repo_key,
            expected_receipt_sha256=lock_draft["receipt_sha256"],
        )
        source_receipt = omc_state._seal_capture(
            {
                "schema_version": 1,
                "status": "READY",
                "session_id": session_id,
                "work_id": work_id,
                "request_sha256": pilot.canonical_sha256(request),
                "baseline_commit": "a" * 40,
                "repository_root_sha256": (
                    registration["trusted_repository_roots"][repo_id]
                ),
                "verification_plan": {
                    "schema_version": 1,
                    "commands": [{"argv": ["pytest"], "timeout_sec": 60}],
                },
                "verification_plan_sha256": f"{index + 30:064x}",
                "work_class_lock_sha256": work_class_lock["receipt_sha256"],
                "started_at": started.isoformat(),
                "capture_sha256": "",
                "signoff": {},
            },
            private_key=repo_key,
        )
        session_dir = repository_paths[repo_id] / ".omc/state/sessions" / session_id
        (session_dir / "capture").mkdir(parents=True)
        (session_dir / "session.json").write_text(json.dumps(session), encoding="utf-8")
        (session_dir / "work_class_lock.json").write_text(
            json.dumps(work_class_lock), encoding="utf-8"
        )
        (session_dir / "capture/start.json").write_text(
            json.dumps(source_receipt), encoding="utf-8"
        )
        executor_lock = pilot.build_executor_start_receipt(
            private_key=repo_key,
            start_receipt=source_receipt,
            executor_surface="codex_cli_json",
            recorded_at=started.isoformat(),
        )
        (session_dir / "capture/executor-start.json").write_text(
            json.dumps(executor_lock), encoding="utf-8"
        )
        execution = _execution(
            execution_key, work_id, executor_lock["receipt_sha256"]
        )
        starts.append({
            "work_id": work_id,
            "repo_id": repo_id,
            "started_at": started.isoformat(),
            "work_class": "implementation",
            "executor_surface": "codex_cli_json",
            "source_kind": "natural",
            "source_session_receipt_sha256": source_receipt["capture_sha256"],
            "source_session_receipt": source_receipt,
            "executor_start_receipt_sha256": executor_lock["receipt_sha256"],
            "executor_start_receipt": executor_lock,
            "request_sha256": execution["request_sha256"],
        })
        source_case = pilot.build_source_case_receipt(
            private_key=source_key,
            study_id=registration["study_id"],
            work_id=work_id,
            source_kind="natural",
            completed=True,
            completion_at=completed.isoformat(),
            verification_status="passed",
            verification_raw_output="tests passed",
            incomplete_attribution=None,
            correction_observed_through=(completed + timedelta(hours=24)).isoformat(),
            corrections=[],
        )
        stdout = b"tests passed"
        stderr = b""
        (session_dir / "capture/verification-000.stdout").write_bytes(stdout)
        (session_dir / "capture/verification-000.stderr").write_bytes(stderr)
        verification = omc_state._seal_capture(
            {
                "schema_version": 1,
                "status": "VERIFIED",
                "session_id": session_id,
                "work_id": work_id,
                "start_capture_sha256": source_receipt["capture_sha256"],
                "verification_passed": True,
                "verified_tree": "c" * 40,
                "observed_tree": "c" * 40,
                "tree_unchanged": True,
                "results": [{
                    "argv": ["pytest"], "timeout_sec": 60, "exit_code": 0,
                    "timed_out": False, "output_overflow": False,
                    "stdout_path": "verification-000.stdout",
                    "stdout_size": len(stdout),
                    "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                    "stderr_path": "verification-000.stderr",
                    "stderr_size": len(stderr),
                    "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                    "started_at": started.isoformat(),
                    "completed_at": completed.isoformat(),
                }],
                "completed_at": completed.isoformat(),
                "capture_sha256": "",
                "signoff": {},
            },
            private_key=repo_key,
        )
        (session_dir / "capture/verification.json").write_text(
            json.dumps(verification), encoding="utf-8"
        )
        terminal_source = pilot._seal_evidence(
            {
                "schema_version": "omc-absolute-acceptability-case-closure/v1",
                "status": "CASE_CLOSED",
                "session_id": session_id,
                "work_id": work_id,
                "completed": True,
                "completion_at": completed.isoformat(),
                "incomplete_attribution": None,
                "correction_observed_through": (
                    completed + timedelta(hours=24)
                ).isoformat(),
                "corrections": [],
                "closed_at": (completed + timedelta(hours=24)).isoformat(),
                "verification_capture_sha256": verification["capture_sha256"],
            },
            private_key=repo_key,
            signer="acceptability_repository_case_closure",
        )
        (session_dir / "capture/case-terminal.json").write_text(
            json.dumps(terminal_source), encoding="utf-8"
        )
        (session_dir / "capture/source-case.json").write_text(
            json.dumps(source_case), encoding="utf-8"
        )
        cases.append(pilot.build_case_receipt(
            private_key=evidence_key,
            study_id=registration["study_id"],
            work_id=work_id,
            source_kind="natural",
            completed=True,
            completion_at=completed.isoformat(),
            verification_status="passed",
            verification_raw_output="tests passed",
            incomplete_attribution=None,
            correction_observed_through=(completed + timedelta(hours=24)).isoformat(),
            corrections=[],
            execution_receipt_sha256=execution["receipt_sha256"],
            source_case_receipt=source_case,
        ) | {"execution_receipt": execution})
    source_population = pilot.build_source_population_receipt(
        private_key=source_key,
        study_id=registration["study_id"],
        closed_at="2026-09-24T00:00:00+00:00",
        starts=starts,
    )
    inventory = pilot.build_inventory_receipt(
        private_key=evidence_key,
        study_id=registration["study_id"],
        source_kind="natural",
        closed_at="2026-09-24T00:00:00+00:00",
        starts=starts,
        source_population_receipt=source_population,
    )
    return registration, inventory, cases, {
        "evidence": evidence_key,
        "source": source_key,
        "execution": execution_key,
        "repo-a": repo_a_key,
        "repo-b": repo_b_key,
    }


def _evaluate(registration: dict[str, object], inventory: dict[str, object], cases: list[dict[str, object]]) -> dict[str, object]:
    return pilot.evaluate_study(
        registration=registration,
        approved_registration_sha256=pilot.canonical_sha256(registration),
        inventory_receipt=inventory,
        case_receipts=cases,
        as_of="2026-09-25T02:00:00+00:00",
    )


def _resign_case(
    evidence_key: Ed25519PrivateKey,
    source_key: Ed25519PrivateKey,
    registration: dict[str, object],
    case: dict[str, object],
    repository_key: Ed25519PrivateKey | None = None,
    persist_source: bool = True,
    **overrides: object,
) -> dict[str, object]:
    values = {
        "source_kind": "natural",
        "completed": True,
        "completion_at": case["completion_at"],
        "verification_status": "passed",
        "verification_raw_output": "tests passed",
        "incomplete_attribution": None,
        "correction_observed_through": case["correction_observed_through"],
        "corrections": case["corrections"],
        "execution_receipt_sha256": case["execution_receipt"]["receipt_sha256"],
        "closed_at": case["closed_at"],
    }
    values.update(overrides)
    if "closed_at" not in overrides and "correction_observed_through" in overrides:
        values["closed_at"] = values["correction_observed_through"]
    source_case = pilot.build_source_case_receipt(
        private_key=source_key,
        study_id=registration["study_id"],
        work_id=case["work_id"],
        **{key: values[key] for key in (
            "source_kind", "completed", "completion_at", "verification_status",
            "verification_raw_output", "incomplete_attribution",
            "correction_observed_through", "corrections",
            "closed_at",
        )},
    )
    if persist_source:
        matches = [
            path
            for root in registration["registered_repository_paths"].values()
            for path in Path(root).glob(".omc/state/sessions/*/capture/source-case.json")
            if json.loads(path.read_text())["work_id"] == case["work_id"]
        ]
        assert len(matches) == 1
        matches[0].write_text(json.dumps(source_case), encoding="utf-8")
        assert repository_key is not None
        capture_dir = matches[0].parent
        verification = json.loads(
            (capture_dir / "verification.json").read_text(encoding="utf-8")
        )
        terminal_source = pilot._seal_evidence(
            {
                "schema_version": "omc-absolute-acceptability-case-closure/v1",
                "status": "CASE_CLOSED",
                "session_id": capture_dir.parent.name,
                "work_id": case["work_id"],
                "completed": values["completed"],
                "completion_at": values["completion_at"],
                "incomplete_attribution": values["incomplete_attribution"],
                "correction_observed_through": values["correction_observed_through"],
                "corrections": values["corrections"],
                "closed_at": values["closed_at"],
                "verification_capture_sha256": verification["capture_sha256"],
            },
            private_key=repository_key,
            signer="acceptability_repository_case_closure",
        )
        (capture_dir / "case-terminal.json").write_text(
            json.dumps(terminal_source), encoding="utf-8"
        )
    return pilot.build_case_receipt(
        private_key=evidence_key,
        study_id=registration["study_id"],
        work_id=case["work_id"],
        source_case_receipt=source_case,
        **values,
    ) | {"execution_receipt": case["execution_receipt"]}


def test_receipt_verification_rejects_raw_tamper_and_wrong_key() -> None:
    private_key, public_key = _keypair()
    receipt = _execution(private_key, "w-00")
    pilot.verify_execution_receipt(receipt, trusted_public_key=public_key)
    for field, value in (("raw_jsonl", "forged"), ("raw_stderr", "hidden")):
        tampered = copy.deepcopy(receipt)
        tampered[field] = value
        with pytest.raises(pilot.PilotEvidenceError, match="execution_receipt_invalid"):
            pilot.verify_execution_receipt(tampered, trusted_public_key=public_key)
    _, wrong_public_key = _keypair()
    with pytest.raises(pilot.PilotEvidenceError, match="execution_receipt_invalid"):
        pilot.verify_execution_receipt(receipt, trusted_public_key=wrong_public_key)


def test_nonzero_or_missing_terminal_success_execution_is_inconclusive() -> None:
    private_key, public_key = _keypair()
    receipts = [
        pilot.build_execution_receipt(
            private_key=private_key, work_id="w-failed", request="fail",
            raw_jsonl='{"type":"turn.completed","usage":{}}\n', exit_code=1,
            produced_at="2026-09-10T01:00:00+00:00",
            executor_start_receipt_sha256="e" * 64,
        ),
        pilot.build_execution_receipt(
            private_key=private_key, work_id="w-missing", request="missing",
            raw_jsonl='{"type":"item.completed"}\n', exit_code=0,
            produced_at="2026-09-10T01:00:00+00:00",
            executor_start_receipt_sha256="e" * 64,
        ),
    ]
    for receipt in receipts:
        with pytest.raises(pilot.PilotEvidenceError, match="execution_receipt_unsuccessful"):
            pilot.verify_execution_receipt(receipt, trusted_public_key=public_key)


def test_public_cli_exposes_all_required_collection_commands() -> None:
    parser = pilot._parser()
    help_text = parser.format_help()
    for command in ("executor-start", "population-close", "case-close"):
        assert command in help_text


def test_public_collectors_write_complete_population_and_case_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    source_key = _key_file(tmp_path / "source.key", keys["source"])
    evidence_key = _key_file(tmp_path / "evidence.key", keys["evidence"])
    repository_key = _key_file(tmp_path / "repo.key", keys["repo-a"])
    inventory_path = tmp_path / "inventory.json"
    inventory = pilot.close_population(
        registration=registration, closed_at="2026-09-24T00:00:00+00:00",
        source_private_key_path=source_key,
        evidence_private_key_path=evidence_key,
        output_path=inventory_path,
    )
    assert len(inventory["starts"]) == 10
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    (capture_dir / "case-terminal.json").unlink()
    (capture_dir / "source-case.json").unlink()
    output = tmp_path / "case.json"
    closure_times = iter([
        datetime(2026, 9, 11, 2, tzinfo=UTC),
        datetime(2026, 9, 11, 3, tzinfo=UTC),
    ])
    monkeypatch.setattr(pilot, "_utc_now", lambda: next(closure_times))
    case = pilot.close_case(
        registration=registration, work_id="w-00",
        completion_at=cases[0]["completion_at"],
        correction_observed_through=cases[0]["correction_observed_through"],
        corrections=[], execution_receipt=cases[0]["execution_receipt"],
        source_private_key_path=source_key,
        evidence_private_key_path=evidence_key,
        repository_private_key_path=repository_key,
        output_path=output,
    )
    assert case["work_id"] == "w-00"
    assert pilot.close_case(
        registration=registration, work_id="w-00",
        completion_at=cases[0]["completion_at"],
        correction_observed_through=cases[0]["correction_observed_through"],
        corrections=[], execution_receipt=cases[0]["execution_receipt"],
        source_private_key_path=source_key,
        evidence_private_key_path=evidence_key,
        repository_private_key_path=repository_key,
        output_path=output,
    ) == case
    with pytest.raises(pilot.PilotEvidenceError, match="receipt_output_exists"):
        pilot.close_population(
            registration=registration, closed_at="2026-09-24T00:00:00+00:00",
            source_private_key_path=source_key,
            evidence_private_key_path=evidence_key,
            output_path=inventory_path,
        )


def test_case_collector_rejects_closure_before_observation_window_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration, _, cases, keys = _study(tmp_path)
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    (capture_dir / "case-terminal.json").unlink()
    (capture_dir / "source-case.json").unlink()
    monkeypatch.setattr(
        pilot, "_utc_now", lambda: datetime(2026, 9, 10, 2, tzinfo=UTC)
    )
    with pytest.raises(pilot.PilotEvidenceError, match="case_closure_too_early"):
        pilot.close_case(
            registration=registration, work_id="w-00",
            completion_at=cases[0]["completion_at"],
            correction_observed_through=cases[0]["correction_observed_through"],
            corrections=[], execution_receipt=cases[0]["execution_receipt"],
            source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
            evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
            repository_private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
            output_path=tmp_path / "case.json",
        )


def test_case_collector_can_seal_omc_attributable_incomplete_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    (capture_dir / "case-terminal.json").unlink()
    (capture_dir / "source-case.json").unlink()
    monkeypatch.setattr(
        pilot, "_utc_now", lambda: datetime(2026, 9, 11, 2, tzinfo=UTC)
    )
    case = pilot.close_case(
        registration=registration, work_id="w-00", completed=False,
        completion_at=None, correction_observed_through=None,
        incomplete_attribution="omc", corrections=[],
        execution_receipt=cases[0]["execution_receipt"],
        source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
        evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
        repository_private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
        output_path=tmp_path / "incomplete-case.json",
    )
    assert case["completed"] is False
    assert case["incomplete_attribution"] == "omc"
    cases[0] = case
    report = _evaluate(registration, inventory, cases)
    assert report["outcome"] == "NOT_ACCEPTABLE"
    assert report["omc_attributable_incomplete_count"] == 1


def test_executor_start_collector_rejects_retrospective_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration, inventory, _, keys = _study(tmp_path)
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    (capture_dir / "executor-start.json").unlink()
    started = datetime.fromisoformat(inventory["starts"][0]["started_at"])
    monkeypatch.setattr(pilot, "_utc_now", lambda: started + timedelta(seconds=61))
    with pytest.raises(
        pilot.PilotEvidenceError, match="executor_start_recording_late"
    ):
        pilot.record_executor_start(
            registration=registration, repo_id="repo-a", session_id="session-00",
            private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
            executor_surface="codex_cli_json",
        )


def test_incomplete_collector_accepts_signed_failed_execution_and_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    (capture_dir / "case-terminal.json").unlink()
    (capture_dir / "source-case.json").unlink()
    verification = json.loads((capture_dir / "verification.json").read_text())
    verification.update({
        "status": "VERIFIED", "verification_passed": False,
        "tree_unchanged": True, "capture_sha256": "", "signoff": {},
    })
    verification["results"][0]["exit_code"] = 1
    failed_verification = omc_state._seal_capture(
        verification, private_key=keys["repo-a"]
    )
    (capture_dir / "verification.json").write_text(
        json.dumps(failed_verification), encoding="utf-8"
    )
    executor_start_digest = inventory["starts"][0][
        "executor_start_receipt_sha256"
    ]
    failed_execution = pilot.build_execution_receipt(
        private_key=keys["execution"], work_id="w-00", request="implement w-00",
        raw_jsonl='{"type":"turn.failed","error":"boom"}\n', exit_code=1,
        produced_at="2026-09-10T01:00:00+00:00",
        executor_start_receipt_sha256=executor_start_digest,
    )
    monkeypatch.setattr(
        pilot, "_utc_now", lambda: datetime(2026, 9, 11, 2, tzinfo=UTC)
    )
    case = pilot.close_case(
        registration=registration, work_id="w-00", completed=False,
        completion_at=None, correction_observed_through=None,
        incomplete_attribution="omc", corrections=[],
        execution_receipt=failed_execution,
        source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
        evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
        repository_private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
        output_path=tmp_path / "failed-case.json",
    )
    cases[0] = case
    report = _evaluate(registration, inventory, cases)
    assert report["outcome"] == "NOT_ACCEPTABLE"
    assert report["omc_attributable_incomplete_count"] == 1


def test_execution_receipt_must_bind_selected_executor_start(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    mismatched = pilot.build_execution_receipt(
        private_key=keys["execution"], work_id="w-00", request="implement w-00",
        raw_jsonl='{"type":"turn.completed","usage":{}}\n', exit_code=0,
        produced_at="2026-09-10T01:00:00+00:00",
        executor_start_receipt_sha256="f" * 64,
    )
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"],
        execution_receipt_sha256=mismatched["receipt_sha256"],
    ) | {"execution_receipt": mismatched}
    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "execution_receipt_binding_mismatch"
    ]


def test_executor_start_must_precede_execution_in_collector_and_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    start = inventory["starts"][0]
    recorded_at = "2026-09-10T00:00:45+00:00"
    executor_start = pilot.build_executor_start_receipt(
        private_key=keys["repo-a"], start_receipt=start["source_session_receipt"],
        executor_surface="codex_cli_json", recorded_at=recorded_at,
    )
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    (capture_dir / "executor-start.json").write_text(
        json.dumps(executor_start), encoding="utf-8"
    )
    execution = pilot.build_execution_receipt(
        private_key=keys["execution"], work_id="w-00", request="implement w-00",
        raw_jsonl='{"type":"turn.completed","usage":{}}\n', exit_code=0,
        produced_at="2026-09-10T00:00:30+00:00",
        executor_start_receipt_sha256=executor_start["receipt_sha256"],
    )
    start["executor_start_receipt"] = executor_start
    start["executor_start_receipt_sha256"] = executor_start["receipt_sha256"]
    source_population = pilot.build_source_population_receipt(
        private_key=keys["source"], study_id=registration["study_id"],
        closed_at=inventory["closed_at"], starts=inventory["starts"],
    )
    inventory = pilot.build_inventory_receipt(
        private_key=keys["evidence"], study_id=registration["study_id"],
        source_kind="natural", closed_at=inventory["closed_at"],
        starts=inventory["starts"], source_population_receipt=source_population,
    )
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"],
        execution_receipt_sha256=execution["receipt_sha256"],
    ) | {"execution_receipt": execution}
    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "case_timeline_invalid"
    ]
    (capture_dir / "case-terminal.json").unlink()
    (capture_dir / "source-case.json").unlink()
    monkeypatch.setattr(
        pilot, "_utc_now", lambda: datetime(2026, 9, 11, 2, tzinfo=UTC)
    )
    key_args = {
        "source_private_key_path": _key_file(tmp_path / "source.key", keys["source"]),
        "evidence_private_key_path": _key_file(tmp_path / "evidence.key", keys["evidence"]),
        "repository_private_key_path": _key_file(tmp_path / "repo.key", keys["repo-a"]),
    }
    with pytest.raises(pilot.PilotEvidenceError, match="case_timeline_invalid"):
        pilot.close_case(
            registration=registration, work_id="w-00",
            completion_at=cases[0]["completion_at"],
            correction_observed_through=cases[0]["correction_observed_through"],
            corrections=[], execution_receipt=execution,
            output_path=tmp_path / "case.json", **key_args,
        )


@pytest.mark.parametrize("bad_field", ["session_id", "work_id", "start_capture_sha256"])
def test_collector_rejects_wrong_verification_binding_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_field: str,
) -> None:
    registration, _, cases, keys = _study(tmp_path)
    capture = Path(registration["registered_repository_paths"]["repo-a"]) / ".omc/state/sessions/session-00/capture"
    verification = json.loads((capture / "verification.json").read_text())
    verification[bad_field] = "f" * 64
    (capture / "verification.json").write_text(json.dumps(
        omc_state._seal_capture(verification, private_key=keys["repo-a"])
    ))
    (capture / "case-terminal.json").unlink()
    (capture / "source-case.json").unlink()
    monkeypatch.setattr(pilot, "_utc_now", lambda: datetime(2026, 9, 12, tzinfo=UTC))
    with pytest.raises(pilot.PilotEvidenceError, match="verification_receipt_invalid"):
        pilot.close_case(
            registration=registration, work_id="w-00",
            completion_at=cases[0]["completion_at"],
            correction_observed_through=cases[0]["correction_observed_through"],
            corrections=[], execution_receipt=cases[0]["execution_receipt"],
            source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
            evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
            repository_private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
            output_path=tmp_path / "case.json",
        )
    assert not (capture / "case-terminal.json").exists()
    assert not (capture / "source-case.json").exists()
    assert not (tmp_path / "case.json").exists()


@pytest.mark.parametrize("taxonomy", [[], {}, None, 1])
def test_invalid_taxonomy_is_structured_error_in_both_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, taxonomy: object,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    corrections = [{"at": "2026-09-10T02:00:00+00:00", "taxonomy": taxonomy, "raw_followup": "fix"}]
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"], corrections=corrections,
    )
    assert _evaluate(registration, inventory, cases)["reason_codes"] == ["correction_evidence_invalid"]
    capture = Path(registration["registered_repository_paths"]["repo-a"]) / ".omc/state/sessions/session-00/capture"
    (capture / "case-terminal.json").unlink()
    (capture / "source-case.json").unlink()
    monkeypatch.setattr(pilot, "_utc_now", lambda: datetime(2026, 9, 12, tzinfo=UTC))
    with pytest.raises(pilot.PilotEvidenceError, match="correction_evidence_invalid"):
        pilot.close_case(
            registration=registration, work_id="w-00",
            completion_at=cases[0]["completion_at"],
            correction_observed_through=cases[0]["correction_observed_through"],
            corrections=corrections, execution_receipt=cases[0]["execution_receipt"],
            source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
            evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
            repository_private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
            output_path=tmp_path / "case.json",
        )
    assert not (capture / "case-terminal.json").exists()
    assert not (tmp_path / "case.json").exists()


@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("damage", [
    "signature", "status_type", "results_shape", "artifact_digest",
    "artifact_missing", "artifact_encoding", "receipt_encoding",
])
def test_case_close_cli_blocks_corrupt_verification_without_writes(
    tmp_path: Path, damage: str, incomplete: bool,
) -> None:
    registration, _, cases, keys = _study(tmp_path)
    capture = Path(registration["registered_repository_paths"]["repo-a"]) / ".omc/state/sessions/session-00/capture"
    receipt_path = capture / "verification.json"
    verification = json.loads(receipt_path.read_text())
    if damage == "signature":
        verification["signoff"]["signature"] = "invalid"
    elif damage == "status_type":
        verification["status"] = []
    elif damage == "results_shape":
        verification["results"] = [None]
    elif damage == "artifact_digest":
        (capture / "verification-000.stdout").write_bytes(b"corrupt")
    elif damage == "artifact_missing":
        (capture / "verification-000.stdout").unlink()
    elif damage == "artifact_encoding":
        raw = b"\xff"
        (capture / "verification-000.stdout").write_bytes(raw)
        verification["results"][0]["stdout_size"] = len(raw)
        verification["results"][0]["stdout_sha256"] = hashlib.sha256(raw).hexdigest()
        verification = omc_state._seal_capture(verification, private_key=keys["repo-a"])
    receipt_path.write_text(json.dumps(verification))
    if damage == "receipt_encoding":
        receipt_path.write_bytes(b"\xff")
    (capture / "case-terminal.json").unlink()
    (capture / "source-case.json").unlink()
    inputs = {
        "registration": registration, "corrections": [],
        "execution": cases[0]["execution_receipt"],
    }
    for name, value in inputs.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    output = tmp_path / "case.json"
    command = [
        sys.executable, str(Path(pilot.__file__).resolve()), "case-close",
        "--registration", str(tmp_path / "registration.json"),
        "--work-id", "w-00", "--corrections", str(tmp_path / "corrections.json"),
        "--execution-receipt", str(tmp_path / "execution.json"),
        "--source-private-key-file", str(_key_file(tmp_path / "source.key", keys["source"])),
        "--evidence-private-key-file", str(_key_file(tmp_path / "evidence.key", keys["evidence"])),
        "--repository-private-key-file", str(_key_file(tmp_path / "repo.key", keys["repo-a"])),
        "--output", str(output),
    ]
    command += (["--incomplete-attribution", "omc"] if incomplete else [
        "--completion-at", cases[0]["completion_at"],
        "--correction-observed-through", cases[0]["correction_observed_through"],
    ])
    result = run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout) == {
        "schema_version": pilot.SCHEMA,
        "outcome": "OBSERVATION_INCONCLUSIVE",
        "reason_codes": ["verification_receipt_invalid"],
    }
    assert result.stderr == ""
    assert not (capture / "case-terminal.json").exists()
    assert not (capture / "source-case.json").exists()
    assert not output.exists()


@pytest.mark.parametrize("consumer", ["collector", "evaluator"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_verification_consumers_use_the_verified_bytes_after_file_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str, stream: str,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    capture = Path(registration["registered_repository_paths"]["repo-a"]) / ".omc/state/sessions/session-00/capture"
    artifact = capture / f"verification-000.{stream}"
    replacement = b"UNVERIFIED REPLACEMENT"
    if consumer == "evaluator":
        cases[0] = _resign_case(
            keys["evidence"], keys["source"], registration, cases[0],
            repository_key=keys["repo-a"],
            verification_raw_output=("" if stream == "stdout" else "tests passed") + replacement.decode(),
        )
    else:
        (capture / "case-terminal.json").unlink()
        (capture / "source-case.json").unlink()
    regular_bytes = omc_state._regular_bytes
    reads = []
    def replace_after_read(path: Path) -> bytes:
        data = regular_bytes(path)
        if path == artifact:
            reads.append(path)
            path.write_bytes(replacement)
        return data
    monkeypatch.setattr(omc_state, "_regular_bytes", replace_after_read)
    monkeypatch.setattr(pilot, "_utc_now", lambda: datetime(2026, 9, 12, tzinfo=UTC))
    if consumer == "evaluator":
        report = _evaluate(registration, inventory, cases)
        assert report["outcome"] == "OBSERVATION_INCONCLUSIVE"
        assert report["reason_codes"] == ["repository_source_case_invalid"]
    else:
        output = tmp_path / "case.json"
        case = pilot.close_case(
            registration=registration, work_id="w-00",
            completion_at=cases[0]["completion_at"],
            correction_observed_through=cases[0]["correction_observed_through"],
            corrections=[], execution_receipt=cases[0]["execution_receipt"],
            source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
            evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
            repository_private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
            output_path=output,
        )
        assert case["verification_raw_output"] == "tests passed"
        assert json.loads(output.read_text())["verification_raw_output"] == "tests passed"
    assert reads == [artifact]


def test_whitespace_only_raw_followup_is_inconclusive(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"],
        corrections=[{
            "at": "2026-09-10T02:00:00+00:00",
            "taxonomy": "ambiguous", "raw_followup": "   ",
        }],
    )
    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "correction_evidence_invalid"
    ]


@pytest.mark.parametrize("damage", ["signature", "status_type", "encoding"])
def test_executor_start_cli_rejects_damaged_input_without_traceback(
    tmp_path: Path, damage: str,
) -> None:
    registration, _, _, keys = _study(tmp_path)
    capture = Path(registration["registered_repository_paths"]["repo-a"]) / ".omc/state/sessions/session-00/capture"
    (capture / "executor-start.json").unlink()
    registration_path = tmp_path / "registration.json"
    registration_path.write_text(json.dumps(registration))
    if damage == "encoding":
        registration_path.write_bytes(b"\xff")
    else:
        start_path = capture / "start.json"
        start = json.loads(start_path.read_text())
        if damage == "signature":
            start["signoff"]["signature"] = "invalid"
        else:
            start["status"] = []
        start_path.write_text(json.dumps(start))
    result = run([
        sys.executable, str(Path(pilot.__file__).resolve()), "executor-start",
        "--registration", str(registration_path), "--repo-id", "repo-a",
        "--session-id", "session-00", "--executor-surface", "codex_cli_json",
        "--private-key-file", str(_key_file(tmp_path / "repo.key", keys["repo-a"])),
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout) == {
        "schema_version": pilot.SCHEMA, "outcome": "OBSERVATION_INCONCLUSIVE",
        "reason_codes": ["json_input_invalid" if damage == "encoding" else "start_receipt_invalid"],
    }
    assert result.stderr == ""
    assert not (capture / "executor-start.json").exists()


@pytest.mark.parametrize("attribution", [[], {}, None, 1])
def test_invalid_attribution_is_blocked_in_evaluator_and_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attribution: object,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"], completed=False,
        completion_at=None, correction_observed_through=None, corrections=[],
        incomplete_attribution=attribution, closed_at="2026-09-12T00:00:00+00:00",
    )
    assert _evaluate(registration, inventory, cases)["reason_codes"] == ["incomplete_attribution_missing"]
    capture = Path(registration["registered_repository_paths"]["repo-a"]) / ".omc/state/sessions/session-00/capture"
    (capture / "case-terminal.json").unlink()
    (capture / "source-case.json").unlink()
    monkeypatch.setattr(pilot, "_utc_now", lambda: datetime(2026, 9, 12, tzinfo=UTC))
    output = tmp_path / "case.json"
    with pytest.raises(pilot.PilotEvidenceError, match="incomplete_case_invalid"):
        pilot.close_case(
            registration=registration, work_id="w-00", completed=False,
            completion_at=None, correction_observed_through=None,
            corrections=[], incomplete_attribution=attribution,
            execution_receipt=cases[0]["execution_receipt"],
            source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
            evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
            repository_private_key_path=_key_file(tmp_path / "repo.key", keys["repo-a"]),
            output_path=output,
        )
    assert not output.exists()
    assert not (capture / "case-terminal.json").exists()
    assert not (capture / "source-case.json").exists()


def test_evaluator_rejects_incomplete_case_with_completion_fields(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"], completed=False,
        incomplete_attribution="omc",
    )
    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "incomplete_case_invalid"
    ]


def test_custody_key_must_be_outside_repository_and_mode_0600(tmp_path: Path) -> None:
    private_key, _ = _keypair()
    encoded = base64.b64encode(private_key.private_bytes_raw()).decode()
    external = tmp_path / "executor.key"
    external.write_text(encoded, encoding="utf-8")
    external.chmod(0o600)
    assert pilot.load_custody_private_key(external, repository_root=Path.cwd())
    internal = Path.cwd() / ".omc-test-executor.key"
    internal.write_text(encoded, encoding="utf-8")
    internal.chmod(0o600)
    try:
        with pytest.raises(pilot.PilotEvidenceError, match="custody_key_inside_repository"):
            pilot.load_custody_private_key(internal, repository_root=Path.cwd())
    finally:
        internal.unlink()


def test_sidecar_runs_codex_exec_json_and_writes_a_verifiable_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pilot, "_utc_now", lambda: datetime(2026, 9, 10, 1, tzinfo=UTC))
    registration, inventory, _, keys = _study(tmp_path)
    private_key = keys["execution"]
    public_key = registration["trusted_execution_public_key"]
    key_path = tmp_path / "executor.key"
    key_path.write_text(base64.b64encode(private_key.private_bytes_raw()).decode(), encoding="utf-8")
    key_path.chmod(0o600)
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\nprintf '%s\\n' '{\"type\":\"turn.completed\",\"usage\":{}}'\n", encoding="utf-8")
    fake_codex.chmod(0o700)
    output = tmp_path / "execution-receipt.json"
    receipt = pilot.run_codex_json_sidecar(
        registration=registration,
        private_key_path=key_path,
        repository_root=Path(registration["registered_repository_paths"]["repo-a"]),
        work_id="w-00",
        request="implement w-00",
        output_path=output,
        executor_start_receipt=inventory["starts"][0]["executor_start_receipt"],
        codex_binary=str(fake_codex),
        produced_at="2026-09-10T01:00:00+00:00",
    )
    assert json.loads(output.read_text(encoding="utf-8")) == receipt
    pilot.verify_execution_receipt(receipt, trusted_public_key=public_key)


def test_sidecar_seals_nonzero_execution_without_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pilot, "_utc_now", lambda: datetime(2026, 9, 10, 1, tzinfo=UTC))
    registration, inventory, _, keys = _study(tmp_path)
    private_key = keys["execution"]
    public_key = registration["trusted_execution_public_key"]
    key_path = _key_file(tmp_path / "executor.key", private_key)
    fake_codex = tmp_path / "codex-failed"
    fake_codex.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'launch failed' >&2\nexit 1\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o700)
    receipt = pilot.run_codex_json_sidecar(
        registration=registration,
        private_key_path=key_path,
        repository_root=Path(registration["registered_repository_paths"]["repo-a"]), work_id="w-00",
        request="implement w-00", output_path=tmp_path / "failed.json",
        executor_start_receipt=inventory["starts"][0]["executor_start_receipt"],
        codex_binary=str(fake_codex), produced_at="2026-09-10T01:00:00+00:00",
    )
    assert receipt["exit_code"] == 1
    assert receipt["raw_jsonl"] == ""
    pilot.verify_execution_receipt(
        receipt, trusted_public_key=public_key, require_success=False
    )
    with pytest.raises(pilot.PilotEvidenceError, match="execution_receipt_unsuccessful"):
        pilot.verify_execution_receipt(receipt, trusted_public_key=public_key)


@pytest.mark.parametrize("fault", [
    "bad_digest", "unsigned", "wrong_signature", "wrong_work", "wrong_request",
    "wrong_root", "wrong_execution_key", "wrong_surface", "future_start",
])
def test_sidecar_preflight_blocks_before_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    registration, inventory, _, keys = _study(tmp_path)
    start = inventory["starts"][0]
    receipt = copy.deepcopy(start["executor_start_receipt"])
    root = Path(registration["registered_repository_paths"]["repo-a"])
    request, work_id = "implement w-00", "w-00"
    private_key = keys["execution"]
    now = datetime(2026, 9, 10, 1, tzinfo=UTC)
    if fault == "bad_digest":
        receipt = {"receipt_sha256": "bad"}
    elif fault == "unsigned":
        receipt = {"receipt_sha256": "e" * 64}
    elif fault == "wrong_signature":
        receipt["signoff"]["signature"] = "invalid"
    elif fault == "wrong_work":
        work_id = "w-01"
    elif fault == "wrong_request":
        request = "different request"
    elif fault == "wrong_root":
        root = tmp_path
    elif fault == "wrong_execution_key":
        private_key = keys["source"]
    elif fault == "wrong_surface":
        receipt = pilot.build_executor_start_receipt(
            private_key=keys["repo-a"], start_receipt=start["source_session_receipt"],
            executor_surface="claude_code", recorded_at=start["started_at"],
        )
        (root / ".omc/state/sessions/session-00/capture/executor-start.json").write_text(json.dumps(receipt))
    elif fault == "future_start":
        now = datetime(2026, 9, 9, tzinfo=UTC)
    monkeypatch.setattr(pilot, "_utc_now", lambda: now)
    provider = Mock(return_value=CompletedProcess([], 0, '{"type":"turn.completed"}\n', ''))
    monkeypatch.setattr(pilot.subprocess, "run", provider)
    output = tmp_path / "sidecar.json"
    with pytest.raises(pilot.PilotEvidenceError):
        pilot.run_codex_json_sidecar(
            registration=registration,
            private_key_path=_key_file(tmp_path / "executor.key", private_key),
            repository_root=root, work_id=work_id, request=request,
            output_path=output, executor_start_receipt=receipt,
        )
    provider.assert_not_called()
    assert not output.exists()


def test_signed_first_ten_acceptability_passes_and_inventory_tamper_fails(tmp_path: Path) -> None:
    registration, inventory, cases, _ = _study(tmp_path)
    assert _evaluate(registration, inventory, cases)["outcome"] == "PRELIMINARY_ACCEPTABLE"
    inventory["starts"].reverse()
    report = _evaluate(registration, inventory, cases)
    assert report["reason_codes"] == ["inventory_receipt_invalid"]


def test_unsigned_start_valid_boolean_cannot_replace_inventory_receipt(tmp_path: Path) -> None:
    registration, inventory, cases, _ = _study(tmp_path)
    forged = {"starts": inventory["starts"], "inventory_complete": True}
    assert _evaluate(registration, forged, cases)["reason_codes"] == ["inventory_receipt_invalid"]


def test_resigned_inventory_cannot_hide_tampered_source_start_receipt(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    starts = copy.deepcopy(inventory["starts"])
    starts[0]["source_session_receipt"]["started_at"] = (
        "2026-09-10T00:30:00+00:00"
    )
    inventory = pilot.build_inventory_receipt(
        private_key=keys["evidence"],
        study_id=registration["study_id"],
        source_kind="natural",
        closed_at="2026-09-24T00:00:00+00:00",
        starts=starts,
        source_population_receipt=inventory["source_population_receipt"],
    )

    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "source_population_mismatch"
    ]


def test_unsigned_completion_and_correction_mutation_is_inconclusive(tmp_path: Path) -> None:
    registration, inventory, cases, _ = _study(tmp_path)
    cases[0]["verified"] = True
    cases[0]["corrections"] = []
    assert "case_receipt_invalid" in _evaluate(registration, inventory, cases)["reason_codes"]


def test_evidence_resign_cannot_hide_source_case_correction(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    original = cases[0]
    cases[0] = pilot.build_case_receipt(
        private_key=keys["evidence"],
        study_id=registration["study_id"],
        work_id=original["work_id"],
        source_kind="natural",
        completed=True,
        completion_at=original["completion_at"],
        verification_status="passed",
        verification_raw_output="tests passed",
        incomplete_attribution=None,
        correction_observed_through=original["correction_observed_through"],
        corrections=[{
            "at": "2026-09-10T02:00:00+00:00",
            "taxonomy": "ambiguous",
            "raw_followup": "숨기려는 수정",
        }],
        execution_receipt_sha256=original["execution_receipt"]["receipt_sha256"],
        source_case_receipt=original["source_case_receipt"],
    ) | {"execution_receipt": original["execution_receipt"]}

    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "source_case_mismatch"
    ]


def test_resigned_source_and_evidence_cannot_hide_repository_case_stream(
    tmp_path: Path,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    original = cases[0]
    correction = {
        "at": "2026-09-10T02:00:00+00:00",
        "taxonomy": "ambiguous",
        "raw_followup": "실제 수정 지시",
    }
    persisted = pilot.build_source_case_receipt(
        private_key=keys["source"], study_id=registration["study_id"],
        work_id=original["work_id"], source_kind="natural", completed=True,
        completion_at=original["completion_at"], verification_status="passed",
        verification_raw_output="tests passed", incomplete_attribution=None,
        correction_observed_through=original["correction_observed_through"],
        corrections=[correction],
    )
    repo_path = Path(registration["registered_repository_paths"]["repo-a"])
    source_path = repo_path / ".omc/state/sessions/session-00/capture/source-case.json"
    source_path.write_text(json.dumps(persisted), encoding="utf-8")
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, original,
        persist_source=False, corrections=[]
    )

    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "repository_source_case_invalid"
    ]


def test_repository_verification_receipt_failure_cannot_be_reported_as_passed(
    tmp_path: Path,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    verification = json.loads(
        (capture_dir / "verification.json").read_text(encoding="utf-8")
    )
    verification["verification_passed"] = False
    verification["capture_sha256"] = ""
    verification["signoff"] = {}
    verification = omc_state._seal_capture(verification, private_key=keys["repo-a"])
    (capture_dir / "verification.json").write_text(
        json.dumps(verification), encoding="utf-8"
    )

    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "repository_source_case_invalid"
    ]


def test_synthetic_provenance_is_inconclusive_even_when_resigned(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    inventory = pilot.build_inventory_receipt(
        private_key=keys["evidence"],
        study_id=registration["study_id"],
        source_kind="synthetic",
        closed_at="2026-09-24T00:00:00+00:00",
        starts=inventory["starts"],
        source_population_receipt=inventory["source_population_receipt"],
    )
    assert _evaluate(registration, inventory, cases)["reason_codes"] == ["synthetic_evidence_forbidden"]


def test_future_observation_timestamp_is_inconclusive(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"],
        correction_observed_through="2099-01-01T00:00:00+00:00",
    )
    assert "observation_timestamp_in_future" in _evaluate(registration, inventory, cases)["reason_codes"]


def test_correction_outside_fixed_window_is_inconclusive(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"],
        corrections=[{"at": "2026-09-12T02:00:00+00:00", "taxonomy": "ambiguous", "raw_followup": "더 고쳐줘"}],
    )
    assert "correction_timestamp_outside_window" in _evaluate(registration, inventory, cases)["reason_codes"]


def test_execution_and_completion_must_follow_start_and_precede_closure(
    tmp_path: Path,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    cases[0] = _resign_case(
        keys["evidence"], keys["source"], registration, cases[0],
        repository_key=keys["repo-a"],
        completion_at="2026-09-09T23:00:00+00:00",
        correction_observed_through="2026-09-10T23:00:00+00:00",
    )
    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "case_timeline_invalid"
    ]

    future_root = tmp_path / "future"
    future_root.mkdir()
    registration, inventory, cases, keys = _study(future_root)
    future_execution = pilot.build_execution_receipt(
        private_key=keys["execution"], work_id="w-00", request="implement w-00",
        raw_jsonl='{"type":"turn.completed","usage":{}}\n', exit_code=0,
        produced_at="2026-09-26T00:00:00+00:00",
        executor_start_receipt_sha256=inventory["starts"][0][
            "executor_start_receipt_sha256"
        ],
    )
    original = cases[0]
    cases[0] = pilot.build_case_receipt(
        private_key=keys["evidence"], study_id=registration["study_id"],
        work_id="w-00", source_kind="natural", completed=True,
        completion_at=original["completion_at"], verification_status="passed",
        verification_raw_output="tests passed", incomplete_attribution=None,
        correction_observed_through=original["correction_observed_through"],
        corrections=[], execution_receipt_sha256=future_execution["receipt_sha256"],
        source_case_receipt=original["source_case_receipt"],
    ) | {"execution_receipt": future_execution}
    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "case_timeline_invalid"
    ]


def test_signed_inventory_omission_is_caught_by_repository_rescan(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    source_population = pilot.build_source_population_receipt(
        private_key=keys["source"],
        study_id=registration["study_id"],
        closed_at="2026-09-24T00:00:00+00:00",
        starts=inventory["starts"][:9],
    )
    inventory = pilot.build_inventory_receipt(
        private_key=keys["evidence"],
        study_id=registration["study_id"],
        source_kind="natural",
        closed_at="2026-09-24T00:00:00+00:00",
        starts=inventory["starts"][:9],
        source_population_receipt=source_population,
    )
    assert _evaluate(registration, inventory, cases[:9])["reason_codes"] == [
        "repository_population_mismatch"
    ]


def test_complete_rescanned_population_with_too_few_starts_is_low_demand(
    tmp_path: Path,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    repo_b = Path(registration["registered_repository_paths"]["repo-b"])
    (repo_b / ".omc/state/sessions/session-09").rename(tmp_path / "outside-study-session")
    starts = inventory["starts"][:9]
    source_population = pilot.build_source_population_receipt(
        private_key=keys["source"], study_id=registration["study_id"],
        closed_at="2026-09-24T00:00:00+00:00", starts=starts,
    )
    inventory = pilot.build_inventory_receipt(
        private_key=keys["evidence"], study_id=registration["study_id"],
        source_kind="natural", closed_at="2026-09-24T00:00:00+00:00",
        starts=starts, source_population_receipt=source_population,
    )

    assert _evaluate(registration, inventory, cases[:9])["outcome"] == (
        "LOW_NATURAL_DEMAND"
    )


@pytest.mark.parametrize("missing", ["start.json", "capture"])
def test_missing_start_capture_is_not_low_natural_demand(
    tmp_path: Path, missing: str,
) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    session = Path(registration["registered_repository_paths"]["repo-b"]) / ".omc/state/sessions/session-09"
    target = session / "capture" if missing == "capture" else session / "capture/start.json"
    target.rename(tmp_path / "missing-capture")
    source = pilot.build_source_population_receipt(
        private_key=keys["source"], study_id=registration["study_id"],
        closed_at=inventory["closed_at"], starts=inventory["starts"][:9],
    )
    incomplete = pilot.build_inventory_receipt(
        private_key=keys["evidence"], study_id=registration["study_id"],
        source_kind="natural", closed_at=inventory["closed_at"],
        starts=inventory["starts"][:9], source_population_receipt=source,
    )
    assert _evaluate(registration, incomplete, cases[:9])["reason_codes"] == ["repository_stream_invalid"]
    output = tmp_path / "population.json"
    with pytest.raises(pilot.PilotEvidenceError, match="repository_stream_invalid"):
        pilot.close_population(
            registration=registration, closed_at=inventory["closed_at"],
            source_private_key_path=_key_file(tmp_path / "source.key", keys["source"]),
            evidence_private_key_path=_key_file(tmp_path / "evidence.key", keys["evidence"]),
            output_path=output,
        )
    assert not output.exists()


def test_registration_tamper_is_inconclusive_against_approved_digest(tmp_path: Path) -> None:
    registration, inventory, cases, _ = _study(tmp_path)
    approved = pilot.canonical_sha256(registration)
    registration["ends_at"] = "2026-09-25T00:00:00+00:00"
    report = pilot.evaluate_study(
        registration=registration,
        approved_registration_sha256=approved,
        inventory_receipt=inventory,
        case_receipts=cases,
        as_of="2026-09-25T02:00:00+00:00",
    )
    assert report["reason_codes"] == ["registration_digest_mismatch"]


def test_registered_repository_root_must_match_source_capture(tmp_path: Path) -> None:
    registration, inventory, cases, _ = _study(tmp_path)
    registration["trusted_repository_roots"]["repo-a"] = "f" * 64

    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "repository_stream_invalid"
    ]


def test_repository_executor_lock_prevents_executor_relabeling(tmp_path: Path) -> None:
    registration, inventory, cases, keys = _study(tmp_path)
    capture_dir = (
        Path(registration["registered_repository_paths"]["repo-a"])
        / ".omc/state/sessions/session-00/capture"
    )
    lock = pilot.build_executor_start_receipt(
        private_key=keys["repo-a"],
        start_receipt=inventory["starts"][0]["source_session_receipt"],
        executor_surface="claude_code",
        recorded_at=inventory["starts"][0]["started_at"],
    )
    (capture_dir / "executor-start.json").write_text(json.dumps(lock), encoding="utf-8")

    assert _evaluate(registration, inventory, cases)["reason_codes"] == [
        "repository_population_mismatch"
    ]


def test_draft_preregistration_activates_into_the_evaluator_schema() -> None:
    preregistration = json.loads(
        Path("docs/real_use_product_observation_preregistration_v3.json").read_text()
    )
    _, execution_public = _keypair()
    _, evidence_public = _keypair()
    _, source_public = _keypair()
    _, repo_a_public = _keypair()
    _, repo_b_public = _keypair()

    registration = pilot.activate_preregistration(
        preregistration=preregistration,
        starts_at="2026-09-10T00:00:00+00:00",
        trusted_execution_public_key=execution_public,
        trusted_evidence_public_key=evidence_public,
        trusted_source_public_key=source_public,
        trusted_start_public_keys={"repo-a": repo_a_public, "repo-b": repo_b_public},
        trusted_repository_roots={"repo-a": "a" * 64, "repo-b": "b" * 64},
        registered_repository_paths={
            "repo-a": "/registered/repo-a",
            "repo-b": "/registered/repo-b",
        },
    )

    assert registration["status"] == "registered"
    assert registration["ends_at"] == "2026-09-24T00:00:00+00:00"
    assert registration["preregistration_sha256"] == pilot.canonical_sha256(
        preregistration
    )
