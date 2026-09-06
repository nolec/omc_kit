import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import omc_task_review_pilot
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from omc_task_review_pilot import (
    PilotPreflightError,
    build_execution_capability_matrix,
    build_inventory_dry_run,
    build_pilot_decision,
    build_paired_dry_run,
    build_persona_paired_dry_run,
    build_pilot_roster,
    build_readiness_receipt,
    build_runner_arm_receipt,
    build_terminal_receipt,
    canonical_repository_identity,
    freeze_case,
    normalize_review_outcome,
    preflight_case,
    prepare_reconciliation_subject,
    record_reconciliation_receipt,
    select_first_eligible_cases,
    validate_pilot_start_receipt,
    write_json_no_replace,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, name: str, remote: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "pilot@example.com")
    _git(repo, "config", "user.name", "Pilot")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "root")
    _git(repo, "remote", "add", "origin", remote)
    return repo


def _execution_source_repo(tmp_path: Path) -> Path:
    repo = _repo(
        tmp_path, "execution-source", "https://example.com/execution-source.git"
    )
    scripts = repo / "scripts"
    scripts.mkdir()
    for name in ("omc_task_review_pilot.py", "omc_output_contract.py"):
        shutil.copy2(Path(__file__).with_name(name), scripts / name)
    _git(repo, "add", "scripts")
    _git(repo, "commit", "-qm", "add pilot helper")
    return repo


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


_EXECUTION_SIGNER = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
_EXECUTION_SIGNER_PUBLIC_KEY = base64.b64encode(
    _EXECUTION_SIGNER.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
).decode("ascii")

_RECONCILIATION_SIGNER = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
_RECONCILIATION_SIGNER_PUBLIC_KEY = base64.b64encode(
    _RECONCILIATION_SIGNER.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
).decode("ascii")

_ADJUDICATION_SIGNER = Ed25519PrivateKey.from_private_bytes(b"\x04" * 32)
_ADJUDICATION_SIGNER_PUBLIC_KEY = base64.b64encode(
    _ADJUDICATION_SIGNER.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
).decode("ascii")

_STUDY_SIGNER = Ed25519PrivateKey.from_private_bytes(b"\x05" * 32)
_STUDY_SIGNER_PUBLIC_KEY = base64.b64encode(
    _STUDY_SIGNER.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
).decode("ascii")


@pytest.fixture(autouse=True)
def _pin_trusted_execution_authority(monkeypatch) -> None:
    monkeypatch.setenv(
        "OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY",
        _EXECUTION_SIGNER_PUBLIC_KEY,
    )
    monkeypatch.setenv(
        "OMC_TASK_REVIEW_PILOT_TRUSTED_RECONCILIATION_PUBLIC_KEY",
        _RECONCILIATION_SIGNER_PUBLIC_KEY,
    )
    monkeypatch.setenv(
        "OMC_TASK_REVIEW_PERSONA_TRUSTED_ADJUDICATION_PUBLIC_KEY",
        _ADJUDICATION_SIGNER_PUBLIC_KEY,
    )
    monkeypatch.setenv(
        "OMC_TASK_REVIEW_PERSONA_TRUSTED_STUDY_PUBLIC_KEY",
        _STUDY_SIGNER_PUBLIC_KEY,
    )


def _reconciliation_authority_receipt(
    subject: dict[str, object], *, signer: Ed25519PrivateKey = _RECONCILIATION_SIGNER,
    public_key: str = _RECONCILIATION_SIGNER_PUBLIC_KEY,
) -> dict[str, str]:
    receipt = {
        "schema_version": "omc-task-review-pilot-reconciliation-authority/v1",
        "signer": "omc-task-review-pilot-reconciliation-v1",
        "signer_public_key": public_key,
        "subject_sha256": subject["reconciliation_subject_sha256"],
        "signature": "",
    }
    receipt["signature"] = base64.b64encode(
        signer.sign(omc_task_review_pilot._canonical_bytes(receipt))
    ).decode("ascii")
    return receipt


def _sign_execution_receipt(receipt: dict[str, object]) -> dict[str, object]:
    receipt["execution_receipt_sha256"] = omc_task_review_pilot._execution_unsigned_digest(receipt)
    receipt["signoff"]["signature"] = base64.b64encode(
        _EXECUTION_SIGNER.sign(omc_task_review_pilot._execution_signed_bytes(receipt))
    ).decode("ascii")
    return receipt


def _frozen_case() -> dict[str, object]:
    return {
        "case_id": "case-01",
        "request": "Fix the checkout total regression.",
        "base_commit": "a" * 40,
        "dod": ["regression test passes"],
        "verification_command": "pytest -q",
        "provider": "codex",
        "model": "gpt-test",
        "reasoning": "medium",
        "timeout_sec": 600,
        "repository_id": "repo-a",
        "dependency_condition": "locked dependencies available",
    }


def _execution_readiness(value: str = "b" * 64) -> dict[str, object]:
    authority = {
        "schema_version": "omc-task-review-pilot-execution-authority/v1",
        "executor_public_key": _EXECUTION_SIGNER_PUBLIC_KEY,
    }
    authority["execution_authority_sha256"] = _sha(authority)
    receipt = {
        "schema_version": "omc-task-review-pilot-readiness/v2",
        "status": "PILOT_READY",
        "roster_sha256": "a" * 64,
        "inventory_sha256": value,
        "t0": "2026-09-03T02:00:00+09:00",
        "provider_call_count": 0,
        "execution_authority": authority,
    }
    receipt["readiness_sha256"] = _sha(receipt)
    return receipt


def _freeze_case(case: dict[str, object] | None = None, *, value: str = "b" * 64) -> dict[str, object]:
    return freeze_case(case or _frozen_case(), readiness_receipt=_execution_readiness(value))


def test_execution_capability_matrix_preserves_paired_boundaries(monkeypatch) -> None:
    source_repository = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        omc_task_review_pilot, "_execution_source_is_clean", lambda _: True
    )
    matrix = build_execution_capability_matrix(
        source_repository=source_repository,
        source_commit=_git(source_repository, "rev-parse", "HEAD"),
        pilot_contract_sha256="b" * 64,
    )

    assert matrix["schema_version"] == "omc-task-review-pilot-capability/v1"
    assert matrix["source_commit"] == _git(source_repository, "rev-parse", "HEAD")
    assert matrix["pilot_contract_sha256"] == "b" * 64
    assert matrix["capability_matrix_sha256"] == _sha(
        {key: value for key, value in matrix.items() if key != "capability_matrix_sha256"}
    )
    capabilities = {item["requirement_id"]: item for item in matrix["capabilities"]}

    assert capabilities["R1_ISOLATED_WORKSPACE"]["status"] == "SUPPORTED"
    assert capabilities["R2_APPROVED_FROZEN_INPUT"]["status"] == "ADAPTER_REQUIRED"
    assert "DoD/provider/model/reasoning/timeout" in capabilities[
        "R2_APPROVED_FROZEN_INPUT"
    ]["evidence"]
    assert capabilities["R3_OMC_TASK_REVIEW"]["status"] == "ADAPTER_REQUIRED"
    assert "$omc-task/$omc-review" in capabilities["R3_OMC_TASK_REVIEW"]["evidence"]

    # A one-arm safe pipeline must not be presented as proof of paired parity.
    assert capabilities["R4_BASELINE_ARM"]["status"] == "ADAPTER_REQUIRED"
    assert capabilities["R5_COUNTERBALANCED_ORDER"]["status"] == "ADAPTER_REQUIRED"
    assert capabilities["R6_PAIRED_TERMINAL_RECEIPT"]["status"] == "ADAPTER_REQUIRED"
    assert capabilities["R7_SHARED_PROVIDER_CONFIGURATION"]["status"] == "ADAPTER_REQUIRED"

    for capability in capabilities.values():
        assert capability["evidence"]
        assert capability["status"] in {
            "SUPPORTED",
            "ADAPTER_REQUIRED",
            "UNSUPPORTED",
        }


def test_execution_capability_matrix_rejects_source_commit_mismatch(monkeypatch) -> None:
    source_repository = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        omc_task_review_pilot, "_execution_source_is_clean", lambda _: True
    )

    with pytest.raises(PilotPreflightError, match="pilot_source_commit_mismatch"):
        build_execution_capability_matrix(
            source_repository=source_repository,
            source_commit="a" * 40,
            pilot_contract_sha256="b" * 64,
        )


def test_execution_capability_matrix_rejects_foreign_repository(tmp_path) -> None:
    foreign_repository = _repo(
        tmp_path, "foreign-source", "https://example.com/foreign-source.git"
    )

    with pytest.raises(PilotPreflightError, match="pilot_source_repository_mismatch"):
        build_execution_capability_matrix(
            source_repository=foreign_repository,
            source_commit=_git(foreign_repository, "rev-parse", "HEAD"),
            pilot_contract_sha256="b" * 64,
        )


def test_capability_matrix_cli_rejects_dirty_execution_source(tmp_path) -> None:
    output = tmp_path / "evidence" / "capability-matrix.json"
    source_repository = _execution_source_repo(tmp_path)
    source_commit = _git(source_repository, "rev-parse", "HEAD")
    (source_repository / "app.py").write_text("value = 2\n", encoding="utf-8")
    command = [
        sys.executable,
        str(source_repository / "scripts" / "omc_task_review_pilot.py"),
        "capability-matrix",
        "--source-repository",
        str(source_repository),
        "--source-commit",
        source_commit,
        "--pilot-contract-sha256",
        "b" * 64,
        "--output",
        str(output),
    ]

    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "pilot_execution_source_dirty"
    assert not output.exists()


def _native_result(tmp_path, *, verdict: str = "APPROVE") -> dict[str, object]:
    artifact = {
        "artifact_version": 2,
        "runner": "codex native review",
        "case_id": "case-01",
        "diff_sha256": "b" * 64,
        "exit_code": 0,
        "adapter_verdict": verdict,
        "stdout": "No actionable findings.",
        "stderr": "",
    }
    artifact["retained_stdout_sha256"] = hashlib.sha256(
        artifact["stdout"].encode()
    ).hexdigest()
    artifact["retained_stderr_sha256"] = hashlib.sha256(
        artifact["stderr"].encode()
    ).hexdigest()
    artifact_path = tmp_path / "case-01.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return {
        "status": "completed",
        "runner": "codex native review",
        "case_id": "case-01",
        "diff_id": "b" * 64,
        "verdict": verdict,
        "execution_artifacts": {
            "exit_code": 0,
            "native_review": True,
            "durable_output_retained": True,
            "durable_artifact": {
                "path": artifact_path.name,
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            },
        },
    }


def test_repository_identity_normalizes_origin_and_rejects_local_only(tmp_path) -> None:
    https_repo = _repo(
        tmp_path,
        "https-repo",
        "https://user:secret@github.com/Example/Project.git/",
    )
    ssh_repo = tmp_path / "ssh-repo"
    subprocess.run(
        ["git", "clone", "-q", str(https_repo), str(ssh_repo)],
        check=True,
    )
    _git(ssh_repo, "remote", "set-url", "origin", "git@github.com:Example/Project.git")
    local_repo = _repo(tmp_path, "local-repo", "https://github.com/other/repo.git")
    _git(local_repo, "remote", "remove", "origin")

    https_identity = canonical_repository_identity(https_repo)
    ssh_identity = canonical_repository_identity(ssh_repo)

    assert https_identity["canonical_origin"] == "github.com/Example/Project"
    assert https_identity["repository_id"] == ssh_identity["repository_id"]
    with pytest.raises(PilotPreflightError, match="repository_origin_missing"):
        canonical_repository_identity(local_repo)


def test_roster_freezes_unique_repository_identity_and_checkpoint(tmp_path) -> None:
    repo_a = _repo(tmp_path, "repo-a", "https://github.com/example/a.git")
    repo_b = _repo(tmp_path, "repo-b", "https://github.com/example/b.git")
    sessions = repo_a / ".omc" / "state" / "sessions" / "s1"
    sessions.mkdir(parents=True)
    (sessions / "session.json").write_text(
        json.dumps({"session_id": "s1", "created_at": "2026-09-03T01:00:00+09:00"}),
        encoding="utf-8",
    )

    roster = build_pilot_roster(
        [repo_a, repo_b],
        pilot_id="pilot-1",
        pilot_contract_sha256="a" * 64,
        source_commit="b" * 40,
    )

    by_origin = {
        item["canonical_origin"]: item for item in roster["repositories"]
    }
    assert by_origin["github.com/example/a"]["checkpoint"]["session_id"] == "s1"
    assert by_origin["github.com/example/b"]["checkpoint"] is None
    assert roster["roster_sha256"] == _sha(
        {key: value for key, value in roster.items() if key != "roster_sha256"}
    )
    with pytest.raises(PilotPreflightError, match="repository_identity_duplicate"):
        build_pilot_roster(
            [repo_a, repo_a],
            pilot_id="pilot-1",
            pilot_contract_sha256="a" * 64,
            source_commit="b" * 40,
        )


def test_start_receipt_binds_consumed_decision_to_roster() -> None:
    binding = {
        "session_id": "session-1",
        "roster_sha256": "a" * 64,
        "pilot_contract_sha256": "b" * 64,
        "source_commit": "c" * 40,
    }
    receipt = {
        "schema_version": "omc-task-review-pilot-start/v1",
        "decision_id": "pilot-start",
        "action": "task_review_pilot_start",
        "status": "consumed",
        "consumed_at": "2026-09-03T02:00:00+09:00",
        "binding": binding,
    }
    receipt["receipt_sha256"] = _sha(receipt)

    assert validate_pilot_start_receipt(receipt, expected_binding=binding)["t0"]
    receipt["action"] = "mission_accept"
    with pytest.raises(PilotPreflightError, match="pilot_start_receipt_hash_mismatch"):
        validate_pilot_start_receipt(receipt, expected_binding=binding)


def test_start_receipt_rejects_content_changed_after_hashing() -> None:
    binding = {
        "session_id": "session-1",
        "roster_sha256": "a" * 64,
        "pilot_contract_sha256": "b" * 64,
        "source_commit": "c" * 40,
    }
    receipt = {
        "schema_version": "omc-task-review-pilot-start/v1",
        "decision_id": "pilot-start",
        "action": "task_review_pilot_start",
        "status": "consumed",
        "consumed_at": "2026-09-03T02:00:00+09:00",
        "binding": binding,
    }
    receipt["receipt_sha256"] = _sha(receipt)
    receipt["consumed_at"] = "2026-09-03T03:00:00+09:00"

    with pytest.raises(PilotPreflightError, match="pilot_start_receipt_hash_mismatch"):
        validate_pilot_start_receipt(receipt, expected_binding=binding)


def test_inventory_dry_run_rechecks_git_evidence_and_never_calls_provider(tmp_path) -> None:
    repo_a = _repo(tmp_path, "repo-a", "https://github.com/example/a.git")
    repo_b = _repo(tmp_path, "repo-b", "https://github.com/example/b.git")
    roster = build_pilot_roster(
        [repo_a, repo_b],
        pilot_id="pilot-1",
        pilot_contract_sha256="a" * 64,
        source_commit="b" * 40,
    )
    for index, repo in enumerate((repo_a, repo_b, repo_a), start=1):
        baseline = _git(repo, "rev-parse", "HEAD")
        (repo / "app.py").write_text(f"value = {index + 1}\n", encoding="utf-8")
        _git(repo, "add", "app.py")
        _git(repo, "commit", "-qm", f"change-{index}")
        followup = _git(repo, "rev-parse", "HEAD")
        session_id = f"s{index}"
        session_dir = repo / ".omc" / "state" / "sessions" / session_id
        session_dir.mkdir(parents=True)
        created_at = f"2026-09-03T02:0{index}:00+09:00"
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "created_at": created_at,
                    "work_class": "implementation",
                }
            ),
            encoding="utf-8",
        )
        (session_dir / "completion.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "baseline_commit": baseline,
                    "followup_commit": followup,
                    "changed_paths": ["app.py"],
                    "work_class": "implementation",
                }
            ),
            encoding="utf-8",
        )

    report = build_inventory_dry_run(
        roster,
        t0="2026-09-03T02:00:00+09:00",
        observed_at="2026-09-15T02:00:00+09:00",
    )

    assert report["provider_call_count"] == 0
    assert report["status"] == "PILOT_READY"
    assert len(report["selected_cases"]) == 3
    assert len(report["scanned_session_ids"]) == 3

    completion = repo_b / ".omc/state/sessions/s2/completion.json"
    tampered = json.loads(completion.read_text())
    tampered["changed_paths"] = ["forged.py"]
    completion.write_text(json.dumps(tampered), encoding="utf-8")
    blocked = build_inventory_dry_run(roster, t0="2026-09-03T02:00:00+09:00")
    assert "completion_changed_paths_mismatch" in {
        item["disposition"] for item in blocked["inventory"]
    }
    assert blocked["status"] != "PILOT_READY"


def test_inventory_dry_run_excludes_sessions_after_seven_day_window(tmp_path) -> None:
    repo_a = _repo(tmp_path, "repo-a", "https://github.com/example/a.git")
    repo_b = _repo(tmp_path, "repo-b", "https://github.com/example/b.git")
    roster = build_pilot_roster(
        [repo_a, repo_b],
        pilot_id="pilot-1",
        pilot_contract_sha256="a" * 64,
        source_commit="b" * 40,
    )
    for index, repo in enumerate((repo_a, repo_b, repo_a), start=1):
        baseline = _git(repo, "rev-parse", "HEAD")
        (repo / "app.py").write_text(f"late = {index}\n", encoding="utf-8")
        _git(repo, "add", "app.py")
        _git(repo, "commit", "-qm", f"late-{index}")
        session_id = f"late-{index}"
        session_dir = repo / ".omc/state/sessions" / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "created_at": f"2026-09-{11 + index:02d}T02:00:00+09:00",
                    "work_class": "implementation",
                }
            ),
            encoding="utf-8",
        )
        (session_dir / "completion.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "baseline_commit": baseline,
                    "followup_commit": _git(repo, "rev-parse", "HEAD"),
                    "changed_paths": ["app.py"],
                    "work_class": "implementation",
                }
            ),
            encoding="utf-8",
        )

    future_report = build_inventory_dry_run(
        roster,
        t0="2026-09-03T02:00:00+09:00",
        observed_at="2026-09-04T02:00:00+09:00",
    )
    assert future_report["status"] == "WAITING_FOR_CASES"
    assert {item["disposition"] for item in future_report["inventory"]} == {
        "future_session_timestamp"
    }

    report = build_inventory_dry_run(
        roster,
        t0="2026-09-03T02:00:00+09:00",
        observed_at="2026-09-15T02:00:00+09:00",
    )

    assert report["status"] == "STOP_COLLECTION_WINDOW_EXPIRED"
    assert report["selected_cases"] == []
    assert {item["disposition"] for item in report["inventory"]} == {
        "collection_window_expired"
    }


def test_inventory_dry_run_rejects_duplicate_or_misplaced_session_ids(tmp_path) -> None:
    repo_a = _repo(tmp_path, "repo-a", "https://github.com/example/a.git")
    repo_b = _repo(tmp_path, "repo-b", "https://github.com/example/b.git")
    roster = build_pilot_roster(
        [repo_a, repo_b],
        pilot_id="pilot-1",
        pilot_contract_sha256="a" * 64,
        source_commit="b" * 40,
    )
    for directory in ("first", "duplicate"):
        session_dir = repo_a / ".omc/state/sessions" / directory
        session_dir.mkdir(parents=True)
        (session_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_id": "same-session",
                    "created_at": "2026-09-03T02:01:00+09:00",
                    "work_class": "implementation",
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(PilotPreflightError, match="session_directory_mismatch"):
        build_inventory_dry_run(roster, t0="2026-09-03T02:00:00+09:00")


def test_inventory_dry_run_ignores_malformed_session_before_checkpoint(tmp_path) -> None:
    repo_a = _repo(tmp_path, "repo-a", "https://github.com/example/a.git")
    repo_b = _repo(tmp_path, "repo-b", "https://github.com/example/b.git")
    legacy_dir = repo_a / ".omc/state/sessions/legacy-directory"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "legacy-session",
                "created_at": "2026-09-03T01:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    roster = build_pilot_roster(
        [repo_a, repo_b],
        pilot_id="pilot-1",
        pilot_contract_sha256="a" * 64,
        source_commit="b" * 40,
    )

    report = build_inventory_dry_run(
        roster,
        t0="2026-09-03T02:00:00+09:00",
        observed_at="2026-09-03T03:00:00+09:00",
    )

    assert report["status"] == "WAITING_FOR_CASES"
    assert report["inventory"] == []


def test_readiness_requires_bound_roster_start_and_inventory_hashes() -> None:
    roster = {
        "schema_version": "omc-task-review-pilot-roster/v1",
        "repositories": [{"repository_id": "repo-a"}, {"repository_id": "repo-b"}],
    }
    roster["roster_sha256"] = _sha(roster)
    t0 = "2026-09-03T02:00:00+09:00"
    start = {
        "binding": {
            "roster_sha256": roster["roster_sha256"],
            "execution_authority": _execution_readiness()["execution_authority"],
        },
        "t0": t0,
    }
    selected_cases = [
        {
            "session_id": "s1",
            "created_at": "2026-09-03T02:01:00+09:00",
            "repository_id": "repo-a",
            "eligible": True,
        },
        {
            "session_id": "s2",
            "created_at": "2026-09-03T02:02:00+09:00",
            "repository_id": "repo-b",
            "eligible": True,
        },
        {
            "session_id": "s3",
            "created_at": "2026-09-03T02:03:00+09:00",
            "repository_id": "repo-a",
            "eligible": True,
        },
    ]
    inventory = {
        "schema_version": "omc-task-review-pilot-inventory/v1",
        "status": "PILOT_READY",
        "provider_call_count": 0,
        "roster_sha256": roster["roster_sha256"],
        "t0": t0,
        "observed_at": "2026-09-03T03:00:00+09:00",
        "collection_deadline": "2026-09-10T02:00:00+09:00",
        "inventory": selected_cases,
        "selected_cases": selected_cases,
    }
    inventory["inventory_sha256"] = _sha(inventory)

    receipt = build_readiness_receipt(roster, start, inventory)
    assert receipt["status"] == "PILOT_READY"
    start["binding"]["roster_sha256"] = "c" * 64
    with pytest.raises(PilotPreflightError, match="readiness_binding_mismatch"):
        build_readiness_receipt(roster, start, inventory)

    start["binding"]["roster_sha256"] = roster["roster_sha256"]
    inventory["provider_call_count"] = 1
    with pytest.raises(PilotPreflightError, match="readiness_inventory_hash_mismatch"):
        build_readiness_receipt(roster, start, inventory)

    inventory["provider_call_count"] = 1
    inventory.pop("inventory_sha256")
    inventory["inventory_sha256"] = _sha(inventory)
    with pytest.raises(PilotPreflightError, match="readiness_provider_call_detected"):
        build_readiness_receipt(roster, start, inventory)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda value: value.pop("roster_sha256"), "readiness_binding_mismatch"),
        (
            lambda value: value.__setitem__("t0", "2026-09-03T03:00:00+09:00"),
            "readiness_t0_mismatch",
        ),
        (
            lambda value: value.__setitem__(
                "observed_at", "2026-09-03T02:02:00+09:00"
            ),
            "readiness_selected_case_invalid",
        ),
        (
            lambda value: (
                value["inventory"][1].__setitem__("repository_id", "repo-a"),
                value["selected_cases"][1].__setitem__("repository_id", "repo-a"),
            ),
            "readiness_repository_diversity_invalid",
        ),
        (
            lambda value: (
                value["inventory"][2].__setitem__(
                    "created_at", "2026-09-11T02:00:00+09:00"
                ),
                value["selected_cases"][2].__setitem__(
                    "created_at", "2026-09-11T02:00:00+09:00"
                ),
            ),
            "readiness_selected_case_invalid",
        ),
    ],
)
def test_readiness_revalidates_inventory_semantics(mutation, reason) -> None:
    roster = {"repositories": [{"repository_id": "repo-a"}, {"repository_id": "repo-b"}]}
    roster["roster_sha256"] = _sha(roster)
    t0 = "2026-09-03T02:00:00+09:00"
    start = {
        "binding": {
            "roster_sha256": roster["roster_sha256"],
            "execution_authority": _execution_readiness()["execution_authority"],
        },
        "t0": t0,
    }
    inventory = {
        "schema_version": "omc-task-review-pilot-inventory/v1",
        "status": "PILOT_READY",
        "provider_call_count": 0,
        "roster_sha256": roster["roster_sha256"],
        "t0": t0,
        "observed_at": "2026-09-03T03:00:00+09:00",
        "collection_deadline": "2026-09-10T02:00:00+09:00",
        "inventory": [
            {"session_id": "s1", "created_at": "2026-09-03T02:01:00+09:00", "repository_id": "repo-a", "eligible": True},
            {"session_id": "s2", "created_at": "2026-09-03T02:02:00+09:00", "repository_id": "repo-b", "eligible": True},
            {"session_id": "s3", "created_at": "2026-09-03T02:03:00+09:00", "repository_id": "repo-a", "eligible": True},
        ],
    }
    inventory["selected_cases"] = [dict(item) for item in inventory["inventory"]]
    mutation(inventory)
    inventory["inventory_sha256"] = _sha(inventory)

    with pytest.raises(PilotPreflightError, match=reason):
        build_readiness_receipt(roster, start, inventory)


def test_pilot_evidence_is_published_without_replacement(tmp_path) -> None:
    output = tmp_path / "pilot" / "roster.json"
    write_json_no_replace(output, {"status": "frozen"})

    with pytest.raises(PilotPreflightError, match="pilot_evidence_already_exists"):
        write_json_no_replace(output, {"status": "changed"})

    assert json.loads(output.read_text()) == {"status": "frozen"}


def test_prepare_roster_cli_publishes_machine_readable_evidence(tmp_path) -> None:
    repo_a = _repo(tmp_path, "repo-a", "https://github.com/example/a.git")
    repo_b = _repo(tmp_path, "repo-b", "https://github.com/example/b.git")
    output = tmp_path / "evidence" / "roster.json"

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("omc_task_review_pilot.py")),
            "prepare-roster",
            "--repository",
            str(repo_a),
            "--repository",
            str(repo_b),
            "--pilot-id",
            "pilot-1",
            "--pilot-contract-sha256",
            "a" * 64,
            "--source-commit",
            "b" * 40,
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema_version"] == "omc-task-review-pilot-roster/v1"
    assert json.loads(output.read_text())["roster_sha256"]


def test_capability_matrix_cli_publishes_no_replace_artifact(tmp_path) -> None:
    output = tmp_path / "evidence" / "capability-matrix.json"
    source_repository = _execution_source_repo(tmp_path)
    source_commit = _git(source_repository, "rev-parse", "HEAD")
    command = [
        sys.executable,
        str(source_repository / "scripts" / "omc_task_review_pilot.py"),
        "capability-matrix",
        "--source-repository",
        str(source_repository),
        "--source-commit",
        source_commit,
        "--pilot-contract-sha256",
        "b" * 64,
        "--output",
        str(output),
    ]

    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema_version"] == "omc-task-review-pilot-capability/v1"
    assert json.loads(result.stdout)["source_commit"] == source_commit
    assert json.loads(result.stdout)["pilot_contract_sha256"] == "b" * 64
    assert json.loads(result.stdout)["capability_matrix_sha256"]
    assert json.loads(output.read_text()) == json.loads(result.stdout)

    repeated = subprocess.run(command, check=False, capture_output=True, text=True)
    assert repeated.returncode == 2
    assert json.loads(repeated.stdout)["reason"] == "pilot_evidence_already_exists"


def test_capability_matrix_cli_rejects_foreign_repository(tmp_path) -> None:
    output = tmp_path / "evidence" / "capability-matrix.json"
    execution_source = _execution_source_repo(tmp_path)
    foreign_repository = _repo(
        tmp_path, "foreign-source", "https://example.com/foreign-source.git"
    )
    command = [
        sys.executable,
        str(execution_source / "scripts" / "omc_task_review_pilot.py"),
        "capability-matrix",
        "--source-repository",
        str(foreign_repository),
        "--source-commit",
        _git(foreign_repository, "rev-parse", "HEAD"),
        "--pilot-contract-sha256",
        "b" * 64,
        "--output",
        str(output),
    ]

    result = subprocess.run(command, check=False, capture_output=True, text=True)

    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "pilot_source_repository_mismatch"
    assert not output.exists()


def test_freeze_case_and_paired_dry_run_cli_publish_no_provider_call_artifacts(tmp_path) -> None:
    case_path = tmp_path / "case.json"
    readiness_path = tmp_path / "readiness.json"
    frozen_path = tmp_path / "evidence" / "case.json"
    dry_run_path = tmp_path / "evidence" / "dry-run.json"
    case_path.write_text(json.dumps(_frozen_case()), encoding="utf-8")
    readiness_path.write_text(json.dumps(_execution_readiness()), encoding="utf-8")
    script = str(Path(__file__).with_name("omc_task_review_pilot.py"))

    freeze = subprocess.run(
        [
            sys.executable,
            script,
            "freeze-case",
            "--case",
            str(case_path),
            "--readiness",
            str(readiness_path),
            "--output",
            str(frozen_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert freeze.returncode == 0, freeze.stderr

    dry_run = subprocess.run(
        [
            sys.executable,
            script,
            "paired-dry-run",
            "--case-receipt",
            str(frozen_path),
            "--case-position",
            "2",
            "--output",
            str(dry_run_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert json.loads(dry_run.stdout)["provider_call_count"] == 0
    assert json.loads(dry_run_path.read_text())["arm_order"] == ["baseline", "omc"]


def test_reconciliation_receipt_binds_a_signed_declared_root_snapshot(tmp_path) -> None:
    root = tmp_path / "v2-state"
    root.mkdir()
    (root / "roster.json").write_text('{"schema_version":"roster/v1"}\n')

    subject = prepare_reconciliation_subject(
        pilot_id="task-review-product-focus-20260903-v2",
        declared_roots=[{"root_id": "local-v2-state", "path": root}],
        observed_at="2026-09-04T15:10:00+09:00",
    )

    assert subject["status"] == "NO_EXECUTION_EVIDENCE_IN_DECLARED_ROOTS"
    assert subject["declared_roots"][0]["root_id"] == "local-v2-state"
    assert subject["declared_roots"][0]["files"][0]["path"] == "roster.json"
    assert subject["missing_execution_evidence"] == [
        "readiness",
        "terminal",
        "decision",
    ]

    receipt = record_reconciliation_receipt(
        subject, _reconciliation_authority_receipt(subject)
    )

    assert receipt["status"] == "NO_EXECUTION_EVIDENCE_IN_DECLARED_ROOTS"
    assert receipt["authority"]["signer_public_key"] == _RECONCILIATION_SIGNER_PUBLIC_KEY
    assert receipt["reconciliation_sha256"] == _sha(
        {key: value for key, value in receipt.items() if key != "reconciliation_sha256"}
    )


def test_reconciliation_rejects_missing_roots_and_executor_key_reuse(tmp_path) -> None:
    with pytest.raises(PilotPreflightError, match="reconciliation_root_missing"):
        prepare_reconciliation_subject(
            pilot_id="task-review-product-focus-20260903-v2",
            declared_roots=[{"root_id": "missing", "path": tmp_path / "missing"}],
            observed_at="2026-09-04T15:10:00+09:00",
        )

    root = tmp_path / "v2-state"
    root.mkdir()
    subject = prepare_reconciliation_subject(
        pilot_id="task-review-product-focus-20260903-v2",
        declared_roots=[{"root_id": "local-v2-state", "path": root}],
        observed_at="2026-09-04T15:10:00+09:00",
    )
    reused_executor_key = _reconciliation_authority_receipt(
        subject,
        signer=_EXECUTION_SIGNER,
        public_key=_EXECUTION_SIGNER_PUBLIC_KEY,
    )

    with pytest.raises(PilotPreflightError, match="reconciliation_authority_mismatch"):
        record_reconciliation_receipt(subject, reused_executor_key)


def test_reconciliation_rejects_malformed_public_function_inputs(tmp_path) -> None:
    with pytest.raises(PilotPreflightError, match="reconciliation_root_descriptor_invalid"):
        prepare_reconciliation_subject(
            pilot_id="task-review-product-focus-20260903-v2",
            declared_roots=[{"root_id": "local-v2-state", "path": 7}],
            observed_at="2026-09-04T15:10:00+09:00",
        )

    with pytest.raises(PilotPreflightError, match="reconciliation_subject_invalid"):
        record_reconciliation_receipt([], {})  # type: ignore[arg-type]

    malformed = {
        "schema_version": "omc-task-review-pilot-reconciliation-subject/v1",
        "status": "NO_EXECUTION_EVIDENCE_IN_DECLARED_ROOTS",
    }
    malformed["reconciliation_subject_sha256"] = _sha(malformed)
    with pytest.raises(PilotPreflightError, match="reconciliation_subject_invalid"):
        record_reconciliation_receipt(malformed, {})


def test_reconciliation_rejects_a_trusted_signature_for_the_wrong_subject_schema(tmp_path) -> None:
    root = tmp_path / "v2-state"
    root.mkdir()
    subject = prepare_reconciliation_subject(
        pilot_id="task-review-product-focus-20260903-v2",
        declared_roots=[{"root_id": "local-v2-state", "path": root}],
        observed_at="2026-09-04T15:10:00+09:00",
    )
    subject["schema_version"] = "omc-task-review-pilot-reconciliation-subject/v0"
    subject["reconciliation_subject_sha256"] = _sha(
        {key: value for key, value in subject.items() if key != "reconciliation_subject_sha256"}
    )

    with pytest.raises(PilotPreflightError, match="reconciliation_subject_invalid"):
        record_reconciliation_receipt(
            subject, _reconciliation_authority_receipt(subject)
        )


def test_reconciliation_rejects_unhashable_execution_evidence(tmp_path) -> None:
    root = tmp_path / "v2-state"
    root.mkdir()
    subject = prepare_reconciliation_subject(
        pilot_id="task-review-product-focus-20260903-v2",
        declared_roots=[{"root_id": "local-v2-state", "path": root}],
        observed_at="2026-09-04T15:10:00+09:00",
    )
    subject["declared_roots"][0]["execution_evidence"] = [{}]
    subject["reconciliation_subject_sha256"] = _sha(
        {key: value for key, value in subject.items() if key != "reconciliation_subject_sha256"}
    )

    with pytest.raises(PilotPreflightError, match="reconciliation_subject_invalid"):
        record_reconciliation_receipt(subject, {})


def test_reconciliation_cli_requires_a_valid_declared_root_descriptor(tmp_path) -> None:
    output = tmp_path / "subject.json"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("omc_task_review_pilot.py")),
            "prepare-reconciliation",
            "--pilot-id",
            "task-review-product-focus-20260903-v2",
            "--artifact-root",
            "missing-separator",
            "--observed-at",
            "2026-09-04T15:10:00+09:00",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "blocked",
        "reason": "reconciliation_root_descriptor_invalid",
    }
    assert not output.exists()


def test_preflight_rejects_missing_frozen_field() -> None:
    case = _frozen_case()
    del case["verification_command"]

    with pytest.raises(PilotPreflightError, match="missing_frozen_fields"):
        preflight_case(case)


def test_preflight_rejects_boolean_timeout() -> None:
    case = _frozen_case()
    case["timeout_sec"] = True

    with pytest.raises(PilotPreflightError, match="invalid_timeout_sec"):
        preflight_case(case)


def test_freeze_case_binds_all_execution_inputs_without_mutation() -> None:
    case = _frozen_case()

    receipt = _freeze_case(case)

    assert receipt["schema_version"] == "omc-task-review-pilot-case/v2"
    assert receipt["readiness_sha256"] == _execution_readiness()["readiness_sha256"]
    assert receipt["case"] == case
    assert receipt["case_sha256"] == _sha(
        {key: value for key, value in receipt.items() if key != "case_sha256"}
    )
    case["model"] = "changed-after-freeze"
    assert receipt["case"]["model"] == "gpt-test"


def test_paired_dry_run_requires_a_valid_frozen_case_and_shared_configuration() -> None:
    receipt = _freeze_case()

    dry_run = build_paired_dry_run(receipt, case_position=2)

    assert dry_run["provider_call_count"] == 0
    assert dry_run["arm_order"] == ["baseline", "omc"]
    assert dry_run["arms"][0]["configuration"] == dry_run["arms"][1]["configuration"]
    assert dry_run["arms"][0]["configuration"]["model"] == "gpt-test"

    receipt["case"]["model"] = "forged"
    with pytest.raises(PilotPreflightError, match="frozen_case_hash_mismatch"):
        build_paired_dry_run(receipt, case_position=2)


def test_paired_dry_run_rejects_invalid_counterbalance_position() -> None:
    receipt = _freeze_case()

    with pytest.raises(PilotPreflightError, match="invalid_case_position"):
        build_paired_dry_run(receipt, case_position=4)


def test_persona_counterbalance_accepts_ten_positions_and_rejects_eleven() -> None:
    receipt = _freeze_case()
    study_binding_sha256 = "b" * 64

    dry_run = build_persona_paired_dry_run(
        receipt,
        case_position=10,
        study_binding_sha256=study_binding_sha256,
    )

    assert dry_run["arm_order"] == ["baseline", "omc"]
    assert dry_run["study_binding_sha256"] == study_binding_sha256
    with pytest.raises(PilotPreflightError, match="invalid_case_position"):
        build_persona_paired_dry_run(
            receipt,
            case_position=11,
            study_binding_sha256=study_binding_sha256,
        )


def test_persona_paired_dry_run_cli_blocks_invalid_source_before_execution(
    tmp_path: Path,
) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    enrollments = _persona_enrollments(registration, tmp_path)
    frozen = omc_task_review_pilot.freeze_persona_case(
        _frozen_case(),
        registration_receipt=registration,
        arm_mapping_receipt=mapping,
    )
    frozen["case"]["base_commit"] = "main"
    frozen["case_sha256"] = _sha(
        {key: value for key, value in frozen.items() if key != "case_sha256"}
    )
    frozen_path = tmp_path / "frozen.json"
    registration_path = tmp_path / "registration.json"
    mapping_path = tmp_path / "mapping.json"
    enrollment_path = tmp_path / "enrollment.json"
    output_path = tmp_path / "dry-run.json"
    frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
    registration_path.write_text(json.dumps(registration), encoding="utf-8")
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    enrollment_path.write_text(json.dumps(enrollments[0]), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("omc_task_review_pilot.py")),
            "persona-paired-dry-run",
            "--case-receipt",
            str(frozen_path),
            "--case-position",
            "1",
            "--registration",
            str(registration_path),
            "--enrollment-receipt",
            str(enrollment_path),
            "--arm-mapping",
            str(mapping_path),
            "--artifact-root",
            str(tmp_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "blocked",
        "reason": "persona_enrollment_case_binding_mismatch",
    }
    assert not output_path.exists()


def _terminal_arm(arm: str, *, elapsed: float, intervention: int = 0) -> dict[str, object]:
    return {
        "arm": arm,
        "verification_passed": True,
        "review_outcome": "approved",
        "elapsed_seconds": elapsed,
        "user_intervention": intervention,
        "rework_count": 0,
        "fatal_violation": False,
        "provider_call_count": 1,
        "raw_output_sha256": "a" * 64,
    }


def _runner_arm_receipt(
    dry_run: dict[str, object], tmp_path: Path, arm: str, *, elapsed: float,
    intervention: int = 0, provider_calls: int = 1, model: str = "gpt-test",
    review_outcome: str = "approved",
) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    output = tmp_path / f"{arm}.txt"
    output.write_text(f"{arm} output\n", encoding="utf-8")
    result = _terminal_arm(arm, elapsed=elapsed, intervention=intervention)
    result["provider_call_count"] = provider_calls
    result["review_outcome"] = review_outcome
    result["raw_output_path"] = output.name
    result["raw_output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    configuration = {
        "provider": "codex",
        "model": model,
        "reasoning": "medium",
        "timeout_sec": 600,
        "verification_command": "pytest -q",
    }
    execution = {
        "schema_version": "omc-task-review-pilot-execution/v2",
        "signed_at": "2026-09-03T02:02:00+09:00",
        "dry_run_sha256": dry_run["dry_run_sha256"],
        "case_sha256": dry_run["case_sha256"],
        "arm": arm,
        "configuration": configuration,
        "result": result,
        "signoff": {
            "signer": "omc-task-review-pilot-executor-v1",
            "signer_public_key": _EXECUTION_SIGNER_PUBLIC_KEY,
            "signature": "",
        },
    }
    _sign_execution_receipt(execution)
    execution_path = tmp_path / f"{arm}.execution.json"
    execution_path.write_text(json.dumps(execution), encoding="utf-8")
    return build_runner_arm_receipt(
        dry_run, execution_path, artifact_root=tmp_path
    )


def test_terminal_receipt_requires_runner_arm_receipts_and_calculates_completion(tmp_path) -> None:
    dry_run = build_paired_dry_run(
        _freeze_case(), case_position=1
    )
    terminal = build_terminal_receipt(
        dry_run,
        [
            _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80),
            _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100),
        ],
    )

    assert terminal["completion"]["omc"] is True
    assert terminal["terminal_sha256"] == _sha(
        {key: value for key, value in terminal.items() if key != "terminal_sha256"}
    )
    with pytest.raises(PilotPreflightError, match="terminal_arm_set_invalid"):
        build_terminal_receipt(
            dry_run, [_runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80)]
        )
    with pytest.raises(PilotPreflightError, match="terminal_arm_receipt_invalid"):
        build_terminal_receipt(
            dry_run, [_terminal_arm("omc", elapsed=80), _terminal_arm("baseline", elapsed=100)]
        )


def test_terminal_receipt_preserves_blocked_review_and_actual_provider_calls(tmp_path) -> None:
    dry_run = build_paired_dry_run(
        _freeze_case(), case_position=1
    )
    blocked = _runner_arm_receipt(
        dry_run,
        tmp_path,
        "baseline",
        elapsed=120,
        provider_calls=2,
        review_outcome="blocked",
    )

    terminal = build_terminal_receipt(
        dry_run, [_runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80), blocked]
    )

    assert terminal["completion"]["baseline"] is False
    assert terminal["provider_call_count"] == 3
    assert terminal["arms"]["baseline"]["raw_output_sha256"] == hashlib.sha256(
        (tmp_path / "baseline.txt").read_bytes()
    ).hexdigest()


def test_runner_arm_receipt_binds_durable_output_and_frozen_configuration(tmp_path) -> None:
    dry_run = build_paired_dry_run(
        _freeze_case(), case_position=1
    )
    receipt = _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80)
    assert receipt["result"]["raw_output_sha256"] == hashlib.sha256(
        (tmp_path / "omc.txt").read_bytes()
    ).hexdigest()

    with pytest.raises(PilotPreflightError, match="execution_receipt_schema_invalid"):
        invalid_path = tmp_path / "invalid.execution.json"
        invalid_path.write_text(json.dumps(_terminal_arm("baseline", elapsed=100)), encoding="utf-8")
        build_runner_arm_receipt(
            dry_run, invalid_path, artifact_root=tmp_path
        )

    # A self-hash proves only integrity after creation, not who executed the arm.
    unsigned = {
        "schema_version": "omc-task-review-pilot-execution/v2",
        "dry_run_sha256": dry_run["dry_run_sha256"],
        "case_sha256": dry_run["case_sha256"],
        "arm": "omc",
        "configuration": dry_run["arms"][1]["configuration"],
        "result": receipt["result"],
    }
    unsigned["execution_receipt_sha256"] = _sha(unsigned)
    unsigned_path = tmp_path / "unsigned.execution.json"
    unsigned_path.write_text(json.dumps(unsigned), encoding="utf-8")
    with pytest.raises(PilotPreflightError, match="execution_receipt_signoff_invalid"):
        build_runner_arm_receipt(dry_run, unsigned_path, artifact_root=tmp_path)

    with pytest.raises(PilotPreflightError, match="execution_receipt_binding_mismatch"):
        _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100, model="other")

    symlink = tmp_path / "baseline.txt"
    symlink.unlink()
    symlink.symlink_to(tmp_path / "omc.txt")
    linked = {
        "schema_version": "omc-task-review-pilot-execution/v2",
        "dry_run_sha256": dry_run["dry_run_sha256"],
        "case_sha256": dry_run["case_sha256"],
        "arm": "baseline",
        "configuration": dry_run["arms"][0]["configuration"],
        "result": {
            **_terminal_arm("baseline", elapsed=100),
            "raw_output_path": symlink.name,
            "raw_output_sha256": hashlib.sha256((tmp_path / "omc.txt").read_bytes()).hexdigest(),
        },
        "signoff": {
            "signer": "omc-task-review-pilot-executor-v1",
            "signer_public_key": _EXECUTION_SIGNER_PUBLIC_KEY,
            "signature": "",
        },
    }
    _sign_execution_receipt(linked)
    linked_path = tmp_path / "linked.execution.json"
    linked_path.write_text(json.dumps(linked), encoding="utf-8")
    with pytest.raises(PilotPreflightError, match="runner_output_path_invalid"):
        build_runner_arm_receipt(dry_run, linked_path, artifact_root=tmp_path)


def test_terminal_receipt_rechecks_runner_output_durability(tmp_path) -> None:
    dry_run = build_paired_dry_run(
        _freeze_case(), case_position=1
    )
    omc = _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80)
    baseline = _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100)
    (tmp_path / "omc.txt").unlink()

    with pytest.raises(PilotPreflightError, match="runner_output_missing"):
        build_terminal_receipt(dry_run, [omc, baseline])


def test_execution_authority_is_not_accepted_from_the_case() -> None:
    """The executor key must originate in approved pilot evidence, not the case."""
    authority = {
        "schema_version": "omc-task-review-pilot-execution-authority/v1",
        "executor_public_key": _EXECUTION_SIGNER_PUBLIC_KEY,
    }
    authority["execution_authority_sha256"] = _sha(authority)
    readiness = {
        "schema_version": "omc-task-review-pilot-readiness/v2",
        "status": "PILOT_READY",
        "roster_sha256": "a" * 64,
        "inventory_sha256": "b" * 64,
        "t0": "2026-09-03T02:00:00+09:00",
        "provider_call_count": 0,
        "execution_authority": authority,
    }
    readiness["readiness_sha256"] = _sha(readiness)

    case = _frozen_case()
    case["execution_signer_public_key"] = "A" * 44
    frozen = freeze_case(case, readiness_receipt=readiness)
    dry_run = build_paired_dry_run(frozen, case_position=1)

    assert dry_run["execution_signer_public_key"] == _EXECUTION_SIGNER_PUBLIC_KEY
    assert "execution_signer_public_key" not in frozen["case"]


def test_freeze_case_rejects_self_issued_execution_authority(monkeypatch) -> None:
    attacker = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
    attacker_public_key = base64.b64encode(
        attacker.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode("ascii")
    readiness = _execution_readiness()
    authority = readiness["execution_authority"]
    authority["executor_public_key"] = attacker_public_key
    authority["execution_authority_sha256"] = _sha(
        {
            "schema_version": authority["schema_version"],
            "executor_public_key": attacker_public_key,
        }
    )
    readiness["readiness_sha256"] = _sha(
        {key: value for key, value in readiness.items() if key != "readiness_sha256"}
    )

    with pytest.raises(PilotPreflightError, match="trusted_execution_authority_mismatch"):
        freeze_case(_frozen_case(), readiness_receipt=readiness)

    monkeypatch.delenv("OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY")
    with pytest.raises(PilotPreflightError, match="trusted_execution_authority_missing"):
        freeze_case(_frozen_case(), readiness_receipt=_execution_readiness())


def test_terminal_reopens_and_revalidates_execution_receipt(tmp_path) -> None:
    """An arm hash alone cannot stand in for the original signed execution receipt."""
    dry_run = build_paired_dry_run(
        _freeze_case(), case_position=1
    )
    omc = _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80)
    baseline = _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100)

    execution_path = tmp_path / omc["execution_receipt"]["path"]
    execution_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PilotPreflightError, match="execution_receipt"):
        build_terminal_receipt(dry_run, [omc, baseline])


def test_pilot_decision_rejects_self_hashed_terminal_metric_forgery(tmp_path) -> None:
    dry_run = build_paired_dry_run(_freeze_case(), case_position=1)
    terminal = build_terminal_receipt(
        dry_run,
        [
            _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80),
            _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100),
        ],
    )
    terminal["arms"]["omc"]["elapsed_seconds"] = 1
    terminal["terminal_sha256"] = _sha(
        {key: value for key, value in terminal.items() if key != "terminal_sha256"}
    )

    with pytest.raises(PilotPreflightError, match="terminal_arm_bundle_mismatch"):
        build_pilot_decision(
            [terminal, terminal, terminal], readiness_receipt=_execution_readiness()
        )


def test_pilot_decision_blocks_a_self_hashed_malformed_terminal_arm_bundle(tmp_path) -> None:
    dry_run = build_paired_dry_run(_freeze_case(), case_position=1)
    terminal = build_terminal_receipt(
        dry_run,
        [
            _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80),
            _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100),
        ],
    )
    terminal["arm_receipts"][0] = {}
    terminal["terminal_sha256"] = _sha(
        {key: value for key, value in terminal.items() if key != "terminal_sha256"}
    )

    with pytest.raises(PilotPreflightError, match="terminal_arm_bundle_invalid"):
        build_pilot_decision(
            [terminal, terminal, terminal], readiness_receipt=_execution_readiness()
        )


def test_pilot_decision_blocks_a_self_hashed_non_object_dry_run(tmp_path) -> None:
    dry_run = build_paired_dry_run(_freeze_case(), case_position=1)
    terminal = build_terminal_receipt(
        dry_run,
        [
            _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80),
            _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100),
        ],
    )
    terminal["dry_run"] = []
    terminal["terminal_sha256"] = _sha(
        {key: value for key, value in terminal.items() if key != "terminal_sha256"}
    )

    with pytest.raises(PilotPreflightError, match="paired_dry_run_schema_invalid"):
        build_pilot_decision(
            [terminal, terminal, terminal], readiness_receipt=_execution_readiness()
        )


def test_pilot_decision_rejects_terminal_from_other_readiness(tmp_path) -> None:
    readiness = _execution_readiness()
    dry_run = build_paired_dry_run(
        freeze_case(_frozen_case(), readiness_receipt=readiness), case_position=1
    )
    terminal = build_terminal_receipt(
        dry_run,
        [
            _runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80),
            _runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100),
        ],
    )

    with pytest.raises(PilotPreflightError, match="terminal_pilot_binding_mismatch"):
        build_pilot_decision(
            [terminal, terminal, terminal],
            readiness_receipt=_execution_readiness("c" * 64),
        )


def test_pilot_decision_requires_three_sealed_receipts_and_uses_fixed_thresholds(tmp_path) -> None:
    readiness = _execution_readiness()
    terminals = []
    for position in (1, 2, 3):
        case = _frozen_case()
        case["case_id"] = f"case-{position}"
        dry_run = build_paired_dry_run(
            freeze_case(case, readiness_receipt=readiness),
            case_position=position,
        )
        terminals.append(
            build_terminal_receipt(
                    dry_run,
                    [
                        _runner_arm_receipt(dry_run, tmp_path / str(position), "omc", elapsed=80),
                        _runner_arm_receipt(dry_run, tmp_path / str(position), "baseline", elapsed=100),
                ],
            )
        )

    assert build_pilot_decision(
        terminals, readiness_receipt=readiness
    )["status"] == "CONTINUE"
    assert build_pilot_decision(
        terminals[:2], readiness_receipt=readiness
    )["status"] == "INCONCLUSIVE"

    terminals[0]["arms"]["omc"]["fatal_violation"] = True
    with pytest.raises(PilotPreflightError, match="terminal_hash_mismatch"):
        build_pilot_decision(terminals, readiness_receipt=readiness)


def test_pilot_decision_rejects_terminal_receipts_without_provider_calls(tmp_path) -> None:
    readiness = _execution_readiness()
    terminals = []
    for position in (1, 2, 3):
        case = _frozen_case()
        case["case_id"] = f"case-{position}"
        dry_run = build_paired_dry_run(
            freeze_case(case, readiness_receipt=readiness),
            case_position=position,
        )
        artifact_root = tmp_path / str(position)
        artifact_root.mkdir()
        omc = _runner_arm_receipt(dry_run, artifact_root, "omc", elapsed=80, provider_calls=0)
        baseline = _runner_arm_receipt(dry_run, artifact_root, "baseline", elapsed=100, provider_calls=0)
        terminals.append(build_terminal_receipt(dry_run, [omc, baseline]))

    assert build_pilot_decision(
        terminals, readiness_receipt=readiness
    )["status"] == "INCONCLUSIVE"
    assert build_pilot_decision(
        terminals, readiness_receipt=readiness
    )["reason"] == "provider_execution_absent"


def _persona_terminal(index: int, study_binding_sha256: str) -> dict[str, object]:
    repository_id = "repo-a" if index < 7 else "repo-b"
    return {
        "terminal_sha256": f"{index + 1:064x}",
        "case_sha256": f"{index + 101:064x}",
        "dry_run": {
            "schema_version": "omc-task-review-persona-paired-dry-run/v1",
            "case_position": index + 1,
            "study_binding_sha256": study_binding_sha256,
            "case_source": {
                "repository_id": repository_id,
                "base_commit": "a" * 40,
            },
        },
        "completion": {"omc": True, "baseline": True},
        "arms": {
            "omc": {
                "verification_passed": True,
                "fatal_violation": False,
                "provider_call_count": 1,
                "elapsed_seconds": 80,
                "user_intervention": 0,
            },
            "baseline": {
                "verification_passed": True,
                "fatal_violation": False,
                "provider_call_count": 1,
                "elapsed_seconds": 100,
                "user_intervention": 1,
            },
        },
    }


def _persona_mapping() -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "omc-task-review-persona-arm-mapping/v1",
        "study_id": "task-review-persona-effectiveness-20260904-v1",
        "arm_a": "baseline",
        "arm_b": "omc",
        "registered_at": "2026-09-03T02:01:00+09:00",
        "signer_public_key": _STUDY_SIGNER_PUBLIC_KEY,
        "signature": "",
    }
    receipt["signature"] = base64.b64encode(
        _STUDY_SIGNER.sign(
            omc_task_review_pilot._persona_arm_mapping_signed_bytes(receipt)
        )
    ).decode("ascii")
    return receipt


def _persona_study_binding(
    readiness: dict[str, object], mapping: dict[str, object], artifact_root: Path,
) -> dict[str, object]:
    source_snapshot_path = artifact_root / "source-snapshot.json"
    source_snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "omc-task-review-persona-source-snapshot/v1",
                "study_id": "task-review-persona-effectiveness-20260904-v1",
                "cases": [
                    {
                        "case_position": index + 1,
                        "repository_id": "repo-a" if index < 7 else "repo-b",
                        "base_commit": "a" * 40,
                    }
                    for index in range(10)
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    source_snapshot_sha256 = hashlib.sha256(
        source_snapshot_path.read_bytes()
    ).hexdigest()
    receipt: dict[str, object] = {
        "schema_version": "omc-task-review-persona-study-binding/v1",
        "study_id": "task-review-persona-effectiveness-20260904-v1",
        "readiness_sha256": readiness["readiness_sha256"],
        "source_snapshot_sha256": source_snapshot_sha256,
        "source_snapshot": {
            "path": source_snapshot_path.name,
            "sha256": source_snapshot_sha256,
        },
        "adjudication_public_key": _ADJUDICATION_SIGNER_PUBLIC_KEY,
        "arm_mapping_sha256": _sha(mapping),
        "registered_at": "2026-09-03T02:01:00+09:00",
        "signer_public_key": _STUDY_SIGNER_PUBLIC_KEY,
        "reconciliation_public_key": _RECONCILIATION_SIGNER_PUBLIC_KEY,
        "reconciliation_signature": "",
        "signature": "",
    }
    signed_bytes = omc_task_review_pilot._persona_study_binding_signed_bytes(receipt)
    receipt["signature"] = base64.b64encode(
        _STUDY_SIGNER.sign(signed_bytes)
    ).decode("ascii")
    receipt["reconciliation_signature"] = base64.b64encode(
        _RECONCILIATION_SIGNER.sign(signed_bytes)
    ).decode("ascii")
    return receipt


def _blind_persona_adjudication(
    terminals: list[dict[str, object]], artifact_root: Path,
    *, arm_a_events: int, arm_b_events: int,
) -> dict[str, object]:
    cases = []
    for index, terminal in enumerate(terminals):
        arm_a_path = artifact_root / f"case-{index}-a.txt"
        arm_b_path = artifact_root / f"case-{index}-b.txt"
        arm_a_path.write_text(f"anonymous correction evidence a {index}\n", encoding="utf-8")
        arm_b_path.write_text(f"anonymous correction evidence b {index}\n", encoding="utf-8")
        cases.append(
            {
                "terminal_sha256": terminal["terminal_sha256"],
                "case_sha256": terminal["case_sha256"],
                "arm_a_additional_correction_required": index < arm_a_events,
                "arm_b_additional_correction_required": index < arm_b_events,
                "arm_a_correction_evidence": {
                    "path": arm_a_path.name,
                    "sha256": hashlib.sha256(arm_a_path.read_bytes()).hexdigest(),
                },
                "arm_b_correction_evidence": {
                    "path": arm_b_path.name,
                    "sha256": hashlib.sha256(arm_b_path.read_bytes()).hexdigest(),
                },
            }
        )
    receipt: dict[str, object] = {
        "schema_version": "omc-task-review-persona-adjudication/v2",
        "signer": "omc-task-review-persona-blind-adjudicator-v1",
        "signer_public_key": _ADJUDICATION_SIGNER_PUBLIC_KEY,
        "study_id": "task-review-persona-effectiveness-20260904-v1",
        "cases": cases,
        "signature": "",
    }
    receipt["signature"] = base64.b64encode(
        _ADJUDICATION_SIGNER.sign(
            omc_task_review_pilot._persona_adjudication_signed_bytes(receipt)
        )
    ).decode("ascii")
    return receipt


def test_persona_decision_requires_fresh_binding_blind_mapping_and_real_evidence(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    adjudication = _blind_persona_adjudication(
        terminals, tmp_path, arm_a_events=5, arm_b_events=3
    )
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, **kwargs: receipt,
    )

    decision = omc_task_review_pilot.build_persona_study_decision(
        terminals,
        adjudication_receipt=adjudication,
        readiness_receipt=readiness,
        study_binding_receipt=binding,
        arm_mapping_receipt=mapping,
        artifact_root=tmp_path,
    )
    assert decision["status"] == "CONTINUE"

    (tmp_path / "case-0-a.txt").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(PilotPreflightError, match="persona_correction_evidence_hash_mismatch"):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt=adjudication,
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_decision_revalidates_ten_real_sealed_terminals(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    adjudication_root = tmp_path / "adjudication"
    adjudication_root.mkdir()
    binding = _persona_study_binding(readiness, mapping, adjudication_root)
    study_binding_sha256 = _sha(binding)
    terminals = []
    for index in range(10):
        case = _frozen_case()
        case["case_id"] = f"persona-case-{index + 1}"
        case["repository_id"] = "repo-a" if index < 7 else "repo-b"
        dry_run = build_persona_paired_dry_run(
            freeze_case(case, readiness_receipt=readiness),
            case_position=index + 1,
            study_binding_sha256=study_binding_sha256,
        )
        case_root = tmp_path / f"terminal-{index + 1}"
        terminals.append(
            build_terminal_receipt(
                dry_run,
                [
                    _runner_arm_receipt(dry_run, case_root, "omc", elapsed=80),
                    _runner_arm_receipt(dry_run, case_root, "baseline", elapsed=100),
                ],
            )
        )
    original_read_execution_receipt_file = (
        omc_task_review_pilot._read_execution_receipt_file
    )
    execution_receipt_read_count = 0

    def counted_read_execution_receipt_file(*args, **kwargs):
        nonlocal execution_receipt_read_count
        execution_receipt_read_count += 1
        return original_read_execution_receipt_file(*args, **kwargs)

    monkeypatch.setattr(
        omc_task_review_pilot,
        "_read_execution_receipt_file",
        counted_read_execution_receipt_file,
    )

    decision = omc_task_review_pilot.build_persona_study_decision(
        terminals,
        adjudication_receipt=_blind_persona_adjudication(
            terminals, adjudication_root, arm_a_events=5, arm_b_events=3
        ),
        readiness_receipt=readiness,
        study_binding_receipt=binding,
        arm_mapping_receipt=mapping,
        artifact_root=adjudication_root,
    )

    assert decision["status"] == "CONTINUE"
    assert decision["terminal_receipt_count"] == 10
    assert execution_receipt_read_count == 20


def test_persona_decision_rejects_posthoc_mapping_rebound_after_execution(
    tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    evidence_root = tmp_path / "late-adjudication"
    evidence_root.mkdir()
    original_mapping = _persona_mapping()
    original_binding = _persona_study_binding(
        readiness, original_mapping, evidence_root
    )
    original_binding_sha256 = _sha(original_binding)
    terminals = []
    for index in range(10):
        case = _frozen_case()
        case["case_id"] = f"late-map-case-{index + 1}"
        dry_run = build_persona_paired_dry_run(
            freeze_case(case, readiness_receipt=readiness),
            case_position=index + 1,
            study_binding_sha256=original_binding_sha256,
        )
        case_root = tmp_path / f"late-terminal-{index + 1}"
        terminals.append(
            build_terminal_receipt(
                dry_run,
                [
                    _runner_arm_receipt(dry_run, case_root, "omc", elapsed=80),
                    _runner_arm_receipt(dry_run, case_root, "baseline", elapsed=100),
                ],
            )
        )

    posthoc_mapping = _persona_mapping()
    posthoc_mapping["arm_a"] = "omc"
    posthoc_mapping["arm_b"] = "baseline"
    posthoc_mapping["signature"] = base64.b64encode(
        _STUDY_SIGNER.sign(
            omc_task_review_pilot._persona_arm_mapping_signed_bytes(posthoc_mapping)
        )
    ).decode("ascii")
    posthoc_binding = _persona_study_binding(
        readiness, posthoc_mapping, evidence_root
    )

    with pytest.raises(
        PilotPreflightError, match="persona_terminal_study_binding_mismatch"
    ):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt=_blind_persona_adjudication(
                terminals, evidence_root, arm_a_events=5, arm_b_events=3
            ),
            readiness_receipt=readiness,
            study_binding_receipt=posthoc_binding,
            arm_mapping_receipt=posthoc_mapping,
            artifact_root=evidence_root,
        )


def test_persona_decision_rejects_tampered_source_snapshot(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, *, expected_pilot_binding: receipt,
    )
    (tmp_path / "source-snapshot.json").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(
        PilotPreflightError, match="persona_source_snapshot_hash_mismatch"
    ):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt=_blind_persona_adjudication(
                terminals, tmp_path, arm_a_events=5, arm_b_events=3
            ),
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_decision_requires_reconciliation_trust_anchor(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    monkeypatch.delenv(
        "OMC_TASK_REVIEW_PILOT_TRUSTED_RECONCILIATION_PUBLIC_KEY"
    )

    with pytest.raises(PilotPreflightError, match="reconciliation_authority_missing"):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt={},
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_decision_rejects_invalid_reconciliation_signature(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    binding["reconciliation_signature"] = base64.b64encode(b"invalid").decode(
        "ascii"
    )
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, *, expected_pilot_binding: receipt,
    )

    with pytest.raises(
        PilotPreflightError,
        match="persona_study_reconciliation_signature_invalid",
    ):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt={},
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_decision_rejects_v2_dry_run_mixed_into_study(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    terminals[0]["dry_run"]["schema_version"] = (
        "omc-task-review-pilot-paired-dry-run/v1"
    )
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, *, expected_pilot_binding: receipt,
    )

    with pytest.raises(
        PilotPreflightError, match="persona_terminal_study_binding_mismatch"
    ):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt={},
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_decision_is_inconclusive_without_three_baseline_events(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, *, expected_pilot_binding: receipt,
    )

    decision = omc_task_review_pilot.build_persona_study_decision(
        terminals,
        adjudication_receipt=_blind_persona_adjudication(
            terminals, tmp_path, arm_a_events=2, arm_b_events=0
        ),
        readiness_receipt=readiness,
        study_binding_receipt=binding,
        arm_mapping_receipt=mapping,
        artifact_root=tmp_path,
    )

    assert decision["status"] == "INCONCLUSIVE"
    assert decision["reason"] == "insufficient_baseline_correction_events"


def test_persona_decision_rejects_tampered_or_duplicate_adjudication(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, *, expected_pilot_binding: receipt,
    )
    adjudication = _blind_persona_adjudication(
        terminals, tmp_path, arm_a_events=5, arm_b_events=3
    )
    adjudication["cases"][0]["arm_b_additional_correction_required"] = False

    with pytest.raises(
        PilotPreflightError, match="persona_adjudication_signature_invalid"
    ):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt=adjudication,
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )

    with pytest.raises(PilotPreflightError, match="persona_terminal_case_duplicate"):
        omc_task_review_pilot.build_persona_study_decision(
            terminals[:-1] + [terminals[0]],
            adjudication_receipt=_blind_persona_adjudication(
                terminals[:-1] + [terminals[0]], tmp_path,
                arm_a_events=5, arm_b_events=3
            ),
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_decision_requires_independent_adjudicator(
    monkeypatch, tmp_path: Path,
) -> None:
    readiness = _execution_readiness()
    mapping = _persona_mapping()
    binding = _persona_study_binding(readiness, mapping, tmp_path)
    terminals = [_persona_terminal(index, _sha(binding)) for index in range(10)]
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, *, expected_pilot_binding: receipt,
    )
    monkeypatch.setenv(
        "OMC_TASK_REVIEW_PERSONA_TRUSTED_ADJUDICATION_PUBLIC_KEY",
        _EXECUTION_SIGNER_PUBLIC_KEY,
    )
    with pytest.raises(
        PilotPreflightError, match="persona_adjudication_authority_not_independent"
    ):
        omc_task_review_pilot.build_persona_study_decision(
            terminals,
            adjudication_receipt=_blind_persona_adjudication(
                terminals, tmp_path, arm_a_events=5, arm_b_events=3
            ),
            readiness_receipt=readiness,
            study_binding_receipt=binding,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_terminal_and_decision_cli_publish_evidence(tmp_path) -> None:
    script = str(Path(__file__).with_name("omc_task_review_pilot.py"))
    dry_run_path = tmp_path / "dry-run.json"
    omc_path = tmp_path / "omc.json"
    baseline_path = tmp_path / "baseline.json"
    terminal_path = tmp_path / "terminal.json"
    decision_path = tmp_path / "decision.json"
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(json.dumps(_execution_readiness()), encoding="utf-8")
    dry_run_path.write_text(
        json.dumps(
            build_paired_dry_run(
                _freeze_case(),
                case_position=1,
            )
        ),
        encoding="utf-8",
    )
    dry_run = json.loads(dry_run_path.read_text(encoding="utf-8"))
    omc_path.write_text(json.dumps(_runner_arm_receipt(dry_run, tmp_path, "omc", elapsed=80)), encoding="utf-8")
    baseline_path.write_text(
        json.dumps(_runner_arm_receipt(dry_run, tmp_path, "baseline", elapsed=100)), encoding="utf-8"
    )

    terminal = subprocess.run(
        [
            sys.executable,
            script,
            "terminal-receipt",
            "--dry-run",
            str(dry_run_path),
            "--arm-receipt",
            str(omc_path),
            "--arm-receipt",
            str(baseline_path),
            "--output",
            str(terminal_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert terminal.returncode == 0, terminal.stderr

    decision = subprocess.run(
        [
            sys.executable,
            script,
            "decide",
            "--terminal-receipt",
            str(terminal_path),
            "--terminal-receipt",
            str(terminal_path),
                "--terminal-receipt",
                str(terminal_path),
                "--readiness",
                str(readiness_path),
                "--output",
            str(decision_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert decision.returncode == 2
    assert json.loads(decision.stdout)["reason"] == "terminal_case_duplicate"


def test_selection_uses_first_three_eligible_sessions_without_replacement() -> None:
    sessions = [
        {
            "session_id": "s0",
            "created_at": "2026-09-03T00:00:00+09:00",
            "eligible": False,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s1",
            "created_at": "2026-09-03T00:01:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s2",
            "created_at": "2026-09-03T00:02:00+09:00",
            "eligible": True,
            "repository_id": "repo-b",
        },
        {
            "session_id": "s3",
            "created_at": "2026-09-03T00:03:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s4",
            "created_at": "2026-09-03T00:04:00+09:00",
            "eligible": True,
            "repository_id": "repo-b",
        },
    ]

    selected = select_first_eligible_cases(
        sessions,
        limit=3,
        t0="2026-09-03T00:00:00+09:00",
        minimum_repository_count=2,
    )

    assert [item["session_id"] for item in selected] == ["s1", "s2", "s3"]


def _persona_registration(mapping: dict[str, object]) -> dict[str, object]:
    repositories = []
    for name, root_commit in (("repo-a", "a" * 40), ("repo-b", "b" * 40)):
        canonical_origin = f"example.com/{name}"
        repositories.append(
            {
                "repository_id": hashlib.sha256(
                    f"{canonical_origin}\n{root_commit}".encode("utf-8")
                ).hexdigest(),
                "canonical_origin": canonical_origin,
                "root_commit": root_commit,
            }
        )
    repositories.sort(key=lambda item: item["repository_id"])
    receipt: dict[str, object] = {
        "schema_version": "omc-task-review-persona-study-registration/v1",
        "study_id": "task-review-persona-effectiveness-20260904-v1",
        "t0": "2026-09-03T02:01:00+09:00",
        "collection_deadline": "2026-09-24T02:01:00+09:00",
        "case_count": 10,
        "minimum_repository_count": 2,
        "maximum_cases_per_repository": 7,
        "wall_clock_noninferiority_ratio": 1.15,
        "preregistration_sha256": (
            "0dc5f8d1270162bfc97841525e9191d863c7e6809fbd6d5d8a24e33d5da0ceab"
        ),
        "contract_revision": 2,
        "minimum_relative_reduction": 0.30,
        "minimum_baseline_correction_events": 3,
        "selection_policy": "chronological_first_eligible_implementation_no_replacement",
        "arm_order_policy": "odd_omc_first_even_direct_first",
        "arm_mapping_sha256": _sha(mapping),
        "repositories": repositories,
        "execution_public_key": _EXECUTION_SIGNER_PUBLIC_KEY,
        "study_public_key": _STUDY_SIGNER_PUBLIC_KEY,
        "reconciliation_public_key": _RECONCILIATION_SIGNER_PUBLIC_KEY,
        "adjudication_public_key": _ADJUDICATION_SIGNER_PUBLIC_KEY,
        "initial_previous_enrollment_sha256": "0" * 64,
        "user_approved": True,
        "registered_at": "2026-09-03T02:01:00+09:00",
        "signer_public_key": _STUDY_SIGNER_PUBLIC_KEY,
        "reconciliation_signature": "",
        "signature": "",
    }
    signed = omc_task_review_pilot._persona_registration_signed_bytes(receipt)
    receipt["signature"] = base64.b64encode(_STUDY_SIGNER.sign(signed)).decode("ascii")
    receipt["reconciliation_signature"] = base64.b64encode(
        _RECONCILIATION_SIGNER.sign(signed)
    ).decode("ascii")
    return receipt


def _persona_enrollments(
    registration: dict[str, object], artifact_root: Path, *, repo_b_start: int = 7,
    cases: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    registration_sha256 = _sha(registration)
    previous = "0" * 64
    enrollments: list[dict[str, object]] = []
    for index in range(10):
        case = cases[index] if cases is not None else None
        repository_ids = [
            item["repository_id"] for item in registration["repositories"]
        ]
        repository_id = (
            str(case["repository_id"])
            if case is not None
            else (
                repository_ids[0] if index < repo_b_start else repository_ids[1]
            )
        )
        created_at = f"2026-09-{4 + index:02d}T02:01:00+09:00"
        session = {
            "session_id": f"persona-session-{index + 1}",
            "created_at": created_at,
            "repository_id": repository_id,
            "base_commit": case["base_commit"] if case is not None else "a" * 40,
            "work_class": "implementation",
            "eligible": True,
            "request_sha256": _sha(case["request"]) if case is not None else f"{index + 201:064x}",
            "dod_sha256": _sha(case["dod"]) if case is not None else f"{index + 301:064x}",
            "verification_sha256": _sha(case["verification_command"]) if case is not None else f"{index + 401:064x}",
        }
        evidence_path = artifact_root / f"enrollment-{index + 1}-state.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "omc-task-review-persona-state-evidence/v1",
                    "study_id": registration["study_id"],
                    "previous_terminal_cursor": None if index == 0 else f"cursor-{index}",
                    "terminal_cursor": f"cursor-{index + 1}",
                    "sessions": [session],
                },
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        receipt: dict[str, object] = {
            "schema_version": "omc-task-review-persona-enrollment/v1",
            "study_id": registration["study_id"],
            "registration_sha256": registration_sha256,
            "previous_enrollment_sha256": previous,
            "sequence": index + 1,
            "session_id": session["session_id"],
            "session_created_at": created_at,
            "enrolled_at": created_at,
            "repository_id": repository_id,
            "base_commit": session["base_commit"],
            "work_class": "implementation",
            "request_sha256": session["request_sha256"],
            "dod_sha256": session["dod_sha256"],
            "verification_sha256": session["verification_sha256"],
            "state_evidence": {
                "path": evidence_path.name,
                "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            },
            "signer_public_key": _RECONCILIATION_SIGNER_PUBLIC_KEY,
            "signature": "",
        }
        receipt["signature"] = base64.b64encode(
            _RECONCILIATION_SIGNER.sign(
                omc_task_review_pilot._persona_enrollment_signed_bytes(receipt)
            )
        ).decode("ascii")
        previous = _sha(receipt)
        enrollments.append(receipt)
    return enrollments


def test_persona_enrollment_chain_proves_first_ten_and_repository_diversity(
    tmp_path: Path,
) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    enrollments = _persona_enrollments(registration, tmp_path)

    validated = omc_task_review_pilot.validate_persona_enrollment_chain(
        registration,
        enrollments,
        arm_mapping_receipt=mapping,
        artifact_root=tmp_path,
    )

    assert [item["sequence"] for item in validated] == list(range(1, 11))
    enrollments = _persona_enrollments(registration, tmp_path, repo_b_start=10)
    with pytest.raises(PilotPreflightError, match="persona_repository_distribution_invalid"):
        omc_task_review_pilot.validate_persona_enrollment_chain(
            registration,
            enrollments,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_enrollment_rejects_skipped_eligible_session(tmp_path: Path) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    enrollments = _persona_enrollments(registration, tmp_path)
    evidence_path = tmp_path / "enrollment-1-state.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    skipped = dict(evidence["sessions"][0])
    skipped["session_id"] = "skipped-first-eligible"
    skipped["created_at"] = "2026-09-03T03:01:00+09:00"
    evidence["sessions"].insert(0, skipped)
    evidence_path.write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    enrollments[0]["state_evidence"]["sha256"] = hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()
    enrollments[0]["signature"] = base64.b64encode(
        _RECONCILIATION_SIGNER.sign(
            omc_task_review_pilot._persona_enrollment_signed_bytes(enrollments[0])
        )
    ).decode("ascii")

    with pytest.raises(PilotPreflightError, match="persona_enrollment_not_first_eligible"):
        omc_task_review_pilot.validate_persona_enrollment_chain(
            registration,
            enrollments,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_registration_rejects_malformed_arm_mapping() -> None:
    mapping = _persona_mapping()
    del mapping["arm_b"]
    mapping["signature"] = base64.b64encode(
        _STUDY_SIGNER.sign(
            omc_task_review_pilot._persona_arm_mapping_signed_bytes(mapping)
        )
    ).decode("ascii")
    registration = _persona_registration(mapping)

    with pytest.raises(PilotPreflightError, match="persona_arm_mapping_invalid"):
        omc_task_review_pilot.freeze_persona_case(
            _frozen_case(),
            registration_receipt=registration,
            arm_mapping_receipt=mapping,
        )

    mapping = _persona_mapping()
    mapping["arm_a"] = {}
    mapping["signature"] = base64.b64encode(
        _STUDY_SIGNER.sign(
            omc_task_review_pilot._persona_arm_mapping_signed_bytes(mapping)
        )
    ).decode("ascii")
    registration = _persona_registration(mapping)
    with pytest.raises(PilotPreflightError, match="persona_arm_mapping_invalid"):
        omc_task_review_pilot.freeze_persona_case(
            _frozen_case(),
            registration_receipt=registration,
            arm_mapping_receipt=mapping,
        )


def test_persona_enrollment_rejects_non_boolean_eligibility(tmp_path: Path) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    enrollments = _persona_enrollments(registration, tmp_path)
    evidence_path = tmp_path / "enrollment-1-state.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["sessions"][-1]["eligible"] = 1
    evidence_path.write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    enrollments[0]["state_evidence"]["sha256"] = hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()
    enrollments[0]["signature"] = base64.b64encode(
        _RECONCILIATION_SIGNER.sign(
            omc_task_review_pilot._persona_enrollment_signed_bytes(enrollments[0])
        )
    ).decode("ascii")

    with pytest.raises(PilotPreflightError, match="persona_enrollment_state_invalid"):
        omc_task_review_pilot.validate_persona_enrollment_chain(
            registration,
            enrollments,
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )


def test_persona_registration_rejects_noncanonical_repository_roster() -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    registration["repositories"][0]["repository_id"] = "f" * 64
    registration["signature"] = ""
    registration["reconciliation_signature"] = ""
    signed = omc_task_review_pilot._persona_registration_signed_bytes(registration)
    registration["signature"] = base64.b64encode(
        _STUDY_SIGNER.sign(signed)
    ).decode("ascii")
    registration["reconciliation_signature"] = base64.b64encode(
        _RECONCILIATION_SIGNER.sign(signed)
    ).decode("ascii")

    with pytest.raises(PilotPreflightError, match="persona_repository_roster_invalid"):
        omc_task_review_pilot.freeze_persona_case(
            _frozen_case(),
            registration_receipt=registration,
            arm_mapping_receipt=mapping,
        )


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("preregistration_sha256", "f" * 64),
        ("contract_revision", 3),
        ("minimum_relative_reduction", 0.29),
        ("minimum_baseline_correction_events", 2),
    ],
)
def test_persona_registration_rejects_resigned_decision_contract_tampering(
    field: str, tampered_value: object,
) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    omc_task_review_pilot.freeze_persona_case(
        _frozen_case(),
        registration_receipt=registration,
        arm_mapping_receipt=mapping,
    )
    registration[field] = tampered_value
    registration["signature"] = ""
    registration["reconciliation_signature"] = ""
    signed = omc_task_review_pilot._persona_registration_signed_bytes(registration)
    registration["signature"] = base64.b64encode(
        _STUDY_SIGNER.sign(signed)
    ).decode("ascii")
    registration["reconciliation_signature"] = base64.b64encode(
        _RECONCILIATION_SIGNER.sign(signed)
    ).decode("ascii")

    with pytest.raises(PilotPreflightError, match="persona_study_registration_invalid"):
        omc_task_review_pilot.freeze_persona_case(
            _frozen_case(),
            registration_receipt=registration,
            arm_mapping_receipt=mapping,
        )


def test_persona_decision_metric_gate_includes_total_interventions_and_wall_clock() -> None:
    terminals = [_persona_terminal(index, "b" * 64) for index in range(10)]
    for terminal in terminals:
        terminal["arms"]["baseline"].update({"elapsed_seconds": 100, "user_intervention": 1})
        terminal["arms"]["omc"].update({"elapsed_seconds": 116, "user_intervention": 0})

    result = omc_task_review_pilot._persona_metric_decision(
        terminals,
        correction_events={"direct_codex": 5, "omc_persona": 3},
        wall_clock_noninferiority_ratio=1.15,
        minimum_relative_reduction=0.30,
        minimum_baseline_correction_events=3,
    )

    assert result["status"] == "STOP"
    assert result["reason"] == "wall_clock_noninferiority_failed"


def test_persona_fatal_violation_precedes_low_baseline_event_inconclusive() -> None:
    terminals = [_persona_terminal(index, "b" * 64) for index in range(10)]
    terminals[0]["arms"]["omc"]["fatal_violation"] = True

    result = omc_task_review_pilot._persona_metric_decision(
        terminals,
        correction_events={"direct_codex": 2, "omc_persona": 0},
        wall_clock_noninferiority_ratio=1.15,
        minimum_relative_reduction=0.30,
        minimum_baseline_correction_events=3,
    )

    assert result["status"] == "STOP"
    assert result["reason"] == "fatal_violation"


def test_persona_decision_policy_is_ordered_and_fail_closed(monkeypatch) -> None:
    rules = omc_task_review_pilot.PERSONA_DECISION_RULES
    assert [rule["condition"] for rule in rules] == [
        "fatal_violation",
        "provider_execution_absent",
        "completion_noninferiority_failed",
        "verification_noninferiority_failed",
        "total_intervention_noninferiority_failed",
        "wall_clock_noninferiority_failed",
        "insufficient_baseline_correction_events",
        "correction_reduction_target_missed",
        "all_gates_passed",
    ]
    signals = {rule["condition"]: False for rule in rules[:-1]}
    signals["provider_execution_absent"] = True
    signals["completion_noninferiority_failed"] = True
    assert omc_task_review_pilot._evaluate_persona_decision(signals) == {
        "status": "INCONCLUSIVE",
        "reason": "provider_execution_absent",
    }
    with pytest.raises(PilotPreflightError, match="persona_decision_signals_invalid"):
        omc_task_review_pilot._evaluate_persona_decision({})
    with pytest.raises(PilotPreflightError, match="persona_decision_signals_invalid"):
        omc_task_review_pilot._evaluate_persona_decision({**signals, "unknown": False})
    changed_default = (*rules[:-1], {**rules[-1], "outcome": "STOP"})
    monkeypatch.setattr(
        omc_task_review_pilot, "PERSONA_DECISION_RULES", changed_default
    )
    no_failures = {condition: False for condition in signals}
    assert omc_task_review_pilot._evaluate_persona_decision(no_failures) == {
        "status": "STOP"
    }


def test_persona_decision_policy_rejects_invalid_rule_schema(monkeypatch) -> None:
    rules = omc_task_review_pilot.PERSONA_DECISION_RULES
    signals = {rule["condition"]: False for rule in rules[:-1]}
    invalid_outcome = ({**rules[0], "outcome": "UNKNOWN"}, *rules[1:])
    monkeypatch.setattr(
        omc_task_review_pilot, "PERSONA_DECISION_RULES", invalid_outcome
    )
    with pytest.raises(PilotPreflightError, match="persona_decision_policy_invalid"):
        omc_task_review_pilot._evaluate_persona_decision(signals)

    unhashable_outcome = ({**rules[0], "outcome": []}, *rules[1:])
    monkeypatch.setattr(
        omc_task_review_pilot, "PERSONA_DECISION_RULES", unhashable_outcome
    )
    with pytest.raises(PilotPreflightError, match="persona_decision_policy_invalid"):
        omc_task_review_pilot._evaluate_persona_decision(signals)

    malformed_rule = ("invalid", *rules[1:])
    monkeypatch.setattr(
        omc_task_review_pilot, "PERSONA_DECISION_RULES", malformed_rule
    )
    with pytest.raises(PilotPreflightError, match="persona_decision_policy_invalid"):
        omc_task_review_pilot._persona_pre_metric_decision([])


def test_persona_collection_close_returns_signed_deadline_shortfall(
    tmp_path: Path,
) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    enrollments = _persona_enrollments(registration, tmp_path)[:9]
    close_receipt: dict[str, object] = {
        "schema_version": "omc-task-review-persona-collection-close/v1",
        "study_id": registration["study_id"],
        "registration_sha256": _sha(registration),
        "enrollment_count": 9,
        "final_enrollment_sha256": _sha(enrollments[-1]),
        "observed_at": registration["collection_deadline"],
        "signer_public_key": _RECONCILIATION_SIGNER_PUBLIC_KEY,
        "signature": "",
    }
    close_receipt["signature"] = base64.b64encode(
        _RECONCILIATION_SIGNER.sign(
            omc_task_review_pilot._persona_collection_close_signed_bytes(close_receipt)
        )
    ).decode("ascii")

    result = omc_task_review_pilot.build_persona_collection_close_decision(
        registration,
        enrollments,
        arm_mapping_receipt=mapping,
        collection_close_receipt=close_receipt,
        artifact_root=tmp_path,
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["reason"] == "deadline_or_sample_shortfall"
    assert result["enrollment_count"] == 9
    assert result["collection_close_receipt"] == close_receipt
    assert result["collection_close_sha256"] == _sha(close_receipt)
    close_receipt["observed_at"] = "tampered"
    assert result["collection_close_receipt"]["observed_at"] == registration[
        "collection_deadline"
    ]


def test_enrolled_persona_fatal_violation_precedes_provider_absence(
    monkeypatch, tmp_path: Path,
) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    enrollments = _persona_enrollments(registration, tmp_path)
    terminals = [_persona_terminal(index, _sha(registration)) for index in range(10)]
    for terminal in terminals:
        terminal["persona_registration_sha256"] = _sha(registration)
        terminal["dry_run"]["registration_sha256"] = _sha(registration)
        terminal["dry_run"]["enrollment_sha256"] = _sha(
            enrollments[terminal["dry_run"]["case_position"] - 1]
        )
        enrollment = enrollments[terminal["dry_run"]["case_position"] - 1]
        terminal["dry_run"]["enrollment_session_id"] = enrollment["session_id"]
        terminal["dry_run"]["case_source"] = {
            "repository_id": enrollment["repository_id"],
            "base_commit": enrollment["base_commit"],
        }
        terminal["arms"]["omc"]["provider_call_count"] = 0
        terminal["arms"]["baseline"]["provider_call_count"] = 0
    terminals[0]["arms"]["omc"]["fatal_violation"] = True
    monkeypatch.setattr(
        omc_task_review_pilot,
        "_validated_terminal_receipt",
        lambda receipt, **kwargs: receipt,
    )

    result = omc_task_review_pilot.build_enrolled_persona_study_decision(
        terminals,
        adjudication_receipt=_blind_persona_adjudication(
            terminals, tmp_path, arm_a_events=0, arm_b_events=0
        ),
        arm_mapping_receipt=mapping,
        registration_receipt=registration,
        enrollment_receipts=enrollments,
        artifact_root=tmp_path,
    )

    assert result["status"] == "STOP"
    assert result["reason"] == "fatal_violation"
    assert result["decision_sha256"] == _sha(
        {key: value for key, value in result.items() if key != "decision_sha256"}
    )
    assert result["evidence_bundle"]["registration"] == registration
    terminals[0]["arms"]["omc"]["fatal_violation"] = False
    absent_result = omc_task_review_pilot.build_enrolled_persona_study_decision(
        terminals,
        adjudication_receipt=_blind_persona_adjudication(
            terminals, tmp_path, arm_a_events=0, arm_b_events=0
        ),
        arm_mapping_receipt=mapping,
        registration_receipt=registration,
        enrollment_receipts=enrollments,
        artifact_root=tmp_path,
    )
    assert absent_result["status"] == "INCONCLUSIVE"
    assert absent_result["reason"] == "provider_execution_absent"
    assert absent_result["decision_sha256"] == _sha(
        {
            key: value
            for key, value in absent_result.items()
            if key != "decision_sha256"
        }
    )


def test_enrolled_persona_decision_binds_all_terminals_to_enrollment_chain(
    monkeypatch, tmp_path: Path,
) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    repository_ids = [
        item["repository_id"] for item in registration["repositories"]
    ]
    cases = []
    for index in range(10):
        case = _frozen_case()
        case["case_id"] = f"enrolled-persona-{index + 1}"
        case["repository_id"] = repository_ids[0] if index < 7 else repository_ids[1]
        cases.append(case)
    enrollments = _persona_enrollments(registration, tmp_path, cases=cases)
    registration_sha256 = _sha(registration)
    terminals = []
    for index, case in enumerate(cases):
        frozen = omc_task_review_pilot.freeze_persona_case(
            case,
            registration_receipt=registration,
            arm_mapping_receipt=mapping,
        )
        dry_run = omc_task_review_pilot.build_enrolled_persona_paired_dry_run(
            frozen,
            case_position=index + 1,
            registration_receipt=registration,
            enrollment_receipts=enrollments[: index + 1],
            arm_mapping_receipt=mapping,
            artifact_root=tmp_path,
        )
        case_root = tmp_path / f"enrolled-terminal-{index + 1}"
        terminals.append(
            build_terminal_receipt(
                dry_run,
                [
                    _runner_arm_receipt(dry_run, case_root, "omc", elapsed=80),
                    _runner_arm_receipt(
                        dry_run, case_root, "baseline", elapsed=100, intervention=1
                    ),
                ],
            )
        )

    decision = omc_task_review_pilot.build_enrolled_persona_study_decision(
        terminals,
        adjudication_receipt=_blind_persona_adjudication(
            terminals, tmp_path, arm_a_events=5, arm_b_events=3
        ),
        arm_mapping_receipt=mapping,
        registration_receipt=registration,
        enrollment_receipts=enrollments,
        artifact_root=tmp_path,
    )

    assert decision["status"] == "CONTINUE"
    assert decision["enrollment_count"] == 10
    assert omc_task_review_pilot.verify_enrolled_persona_study_decision(
        decision, artifact_root=tmp_path
    ) == decision
    tampered_decision = json.loads(json.dumps(decision))
    tampered_decision["evidence_bundle"]["registration"][
        "minimum_relative_reduction"
    ] = 0.29
    tampered_decision["decision_sha256"] = _sha(
        {
            key: value
            for key, value in tampered_decision.items()
            if key != "decision_sha256"
        }
    )
    with pytest.raises(PilotPreflightError, match="persona_decision_bundle_invalid"):
        omc_task_review_pilot.verify_enrolled_persona_study_decision(
            tampered_decision, artifact_root=tmp_path
        )
    terminals[0]["persona_registration_sha256"] = "f" * 64
    terminals[0]["terminal_sha256"] = _sha(
        {key: value for key, value in terminals[0].items() if key != "terminal_sha256"}
    )
    with pytest.raises(
        PilotPreflightError, match="terminal_pilot_binding_mismatch"
    ):
        omc_task_review_pilot.build_enrolled_persona_study_decision(
            terminals,
            adjudication_receipt={},
            arm_mapping_receipt=mapping,
            registration_receipt=registration,
            enrollment_receipts=enrollments,
            artifact_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("kind", "builder"),
    [
        ("registration", lambda: _persona_registration(_persona_mapping())),
        ("arm_mapping", _persona_mapping),
    ],
)
def test_persona_signing_payload_exposes_exact_canonical_bytes(
    kind: str, builder,
) -> None:
    receipt = builder()
    if kind == "registration":
        receipt["signature"] = ""
        receipt["reconciliation_signature"] = ""
        expected = omc_task_review_pilot._persona_registration_signed_bytes(receipt)
    else:
        receipt["signature"] = ""
        expected = omc_task_review_pilot._persona_arm_mapping_signed_bytes(receipt)

    payload = omc_task_review_pilot.build_persona_signing_payload(receipt, kind=kind)

    assert base64.b64decode(payload["payload_base64"], validate=True) == expected
    assert payload["payload_sha256"] == hashlib.sha256(expected).hexdigest()
    with pytest.raises(PilotPreflightError, match="persona_signing_payload_kind_invalid"):
        omc_task_review_pilot.build_persona_signing_payload(receipt, kind="unknown")
    with pytest.raises(PilotPreflightError, match="persona_signing_payload_invalid"):
        omc_task_review_pilot.build_persona_signing_payload(
            {"signature": ""}, kind=kind
        )
    malformed = json.loads(json.dumps(receipt))
    malformed["schema_version"] = "invalid"
    with pytest.raises(PilotPreflightError, match="persona_signing_payload_invalid"):
        omc_task_review_pilot.build_persona_signing_payload(malformed, kind=kind)
    if kind == "arm_mapping":
        for invalid_arm in ([], {}, None, True):
            malformed = json.loads(json.dumps(receipt))
            malformed["arm_a"] = invalid_arm
            with pytest.raises(
                PilotPreflightError, match="persona_signing_payload_invalid"
            ):
                omc_task_review_pilot.build_persona_signing_payload(
                    malformed, kind=kind
                )


def test_persona_signing_payload_supports_every_documented_kind(
    tmp_path: Path,
) -> None:
    mapping = _persona_mapping()
    registration = _persona_registration(mapping)
    enrollment = _persona_enrollments(registration, tmp_path)[0]
    terminals = [_persona_terminal(index, "b" * 64) for index in range(10)]
    adjudication = _blind_persona_adjudication(
        terminals, tmp_path, arm_a_events=5, arm_b_events=3
    )
    collection_close = {
        "schema_version": "omc-task-review-persona-collection-close/v1",
        "study_id": registration["study_id"],
        "registration_sha256": _sha(registration),
        "enrollment_count": 1,
        "final_enrollment_sha256": _sha(enrollment),
        "observed_at": registration["collection_deadline"],
        "signer_public_key": _RECONCILIATION_SIGNER_PUBLIC_KEY,
        "signature": "",
    }
    documents = [
        ("enrollment", enrollment, omc_task_review_pilot._persona_enrollment_signed_bytes),
        (
            "adjudication",
            adjudication,
            omc_task_review_pilot._persona_adjudication_signed_bytes,
        ),
        (
            "collection_close",
            collection_close,
            omc_task_review_pilot._persona_collection_close_signed_bytes,
        ),
    ]
    for kind, receipt, signed_bytes in documents:
        receipt["signature"] = ""
        expected = signed_bytes(receipt)
        payload = omc_task_review_pilot.build_persona_signing_payload(
            receipt, kind=kind
        )
        assert base64.b64decode(payload["payload_base64"], validate=True) == expected
        assert payload["payload_sha256"] == hashlib.sha256(expected).hexdigest()


def test_selection_rejects_non_chronological_inventory() -> None:
    sessions = [
        {
            "session_id": "s2",
            "created_at": "2026-09-03T00:02:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s1",
            "created_at": "2026-09-03T00:01:00+09:00",
            "eligible": True,
            "repository_id": "repo-b",
        },
    ]

    with pytest.raises(
        PilotPreflightError, match="session_inventory_not_chronological"
    ):
        select_first_eligible_cases(
            sessions,
            limit=3,
            t0="2026-09-03T00:00:00+09:00",
            minimum_repository_count=2,
        )


def test_selection_rejects_insufficient_cases_and_repository_diversity() -> None:
    sessions = [
        {
            "session_id": "s1",
            "created_at": "2026-09-03T00:01:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s2",
            "created_at": "2026-09-03T00:02:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
    ]

    with pytest.raises(PilotPreflightError, match="insufficient_eligible_cases"):
        select_first_eligible_cases(
            sessions,
            limit=3,
            t0="2026-09-03T00:00:00+09:00",
            minimum_repository_count=2,
        )

    sessions.append(
        {
            "session_id": "s3",
            "created_at": "2026-09-03T00:03:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        }
    )
    with pytest.raises(PilotPreflightError, match="insufficient_repository_diversity"):
        select_first_eligible_cases(
            sessions,
            limit=3,
            t0="2026-09-03T00:00:00+09:00",
            minimum_repository_count=2,
        )


def test_selection_excludes_eligible_session_before_t0() -> None:
    sessions = [
        {
            "session_id": "s0",
            "created_at": "2026-09-02T23:59:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s1",
            "created_at": "2026-09-03T00:01:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s2",
            "created_at": "2026-09-03T00:02:00+09:00",
            "eligible": True,
            "repository_id": "repo-b",
        },
        {
            "session_id": "s3",
            "created_at": "2026-09-03T00:03:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
    ]

    selected = select_first_eligible_cases(
        sessions,
        limit=3,
        t0="2026-09-03T00:00:00+09:00",
        minimum_repository_count=2,
    )

    assert [item["session_id"] for item in selected] == ["s1", "s2", "s3"]


def test_selection_excludes_session_at_t0_and_normalizes_repository_identity() -> None:
    sessions = [
        {
            "session_id": "s0",
            "created_at": "2026-09-03T00:00:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s1",
            "created_at": "2026-09-03T00:01:00+09:00",
            "eligible": True,
            "repository_id": " repo-a ",
        },
        {
            "session_id": "s2",
            "created_at": "2026-09-03T00:02:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
        {
            "session_id": "s3",
            "created_at": "2026-09-03T00:03:00+09:00",
            "eligible": True,
            "repository_id": "repo-a",
        },
    ]

    with pytest.raises(PilotPreflightError, match="insufficient_repository_diversity"):
        select_first_eligible_cases(
            sessions,
            limit=3,
            t0="2026-09-03T00:00:00+09:00",
            minimum_repository_count=2,
        )


def test_review_normalization_accepts_only_explicit_supported_evidence(
    tmp_path,
) -> None:
    omc_output = (
        '<!-- OMC_OUTPUT: {"next_skill":null,"outcome":"approved",'
        '"reason_code":null,"risk":"low","schema_version":"omc-output/v1",'
        '"stage":"review","user_selection_needed":true} -->\n'
        "VERDICT: APPROVE"
    )

    assert normalize_review_outcome("omc", omc_output) == "approved"
    native_approved = _native_result(tmp_path)
    assert (
        normalize_review_outcome("baseline", native_approved, artifact_root=tmp_path)
        == "approved"
    )
    native_revise = _native_result(tmp_path, verdict="REVISE")
    assert (
        normalize_review_outcome(
            "baseline",
            native_revise,
            artifact_root=tmp_path,
        )
        == "blocked"
    )

    with pytest.raises(PilotPreflightError, match="review_outcome_inconclusive"):
        normalize_review_outcome(
            "baseline",
            {
                **native_approved,
                "status": "failed",
                "verdict": "APPROVE",
                "execution_artifacts": {
                    **native_approved["execution_artifacts"],
                    "exit_code": None,
                },
            },
            artifact_root=tmp_path,
        )


def test_review_normalization_rejects_tampered_or_unbound_artifact(tmp_path) -> None:
    result = _native_result(tmp_path)
    artifact_path = tmp_path / "case-01.json"
    artifact_path.write_text("{}", encoding="utf-8")
    with pytest.raises(PilotPreflightError, match="review_artifact_hash_mismatch"):
        normalize_review_outcome("baseline", result, artifact_root=tmp_path)

    result = _native_result(tmp_path)
    result["case_id"] = "case-02"
    with pytest.raises(PilotPreflightError, match="review_artifact_identity_mismatch"):
        normalize_review_outcome("baseline", result, artifact_root=tmp_path)


def test_review_normalization_rejects_artifact_path_escape(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    result = _native_result(tmp_path)
    result["execution_artifacts"]["durable_artifact"]["path"] = "../case-01.json"

    with pytest.raises(PilotPreflightError, match="review_artifact_path_invalid"):
        normalize_review_outcome("baseline", result, artifact_root=artifact_root)


def test_review_normalization_rejects_intermediate_symlink_escape(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    result = _native_result(tmp_path)
    (artifact_root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    result["execution_artifacts"]["durable_artifact"]["path"] = (
        "linked-dir/case-01.json"
    )

    with pytest.raises(PilotPreflightError, match="review_artifact_path_invalid"):
        normalize_review_outcome("baseline", result, artifact_root=artifact_root)


def test_review_normalization_rejects_artifact_swapped_after_path_check(
    tmp_path, monkeypatch
) -> None:
    result = _native_result(tmp_path)
    artifact_path = tmp_path / "case-01.json"
    external_path = tmp_path.parent / "external-case-01.json"
    external_path.write_bytes(artifact_path.read_bytes())
    original_resolve = type(artifact_path).resolve
    original_open = os.open

    def swap_artifact() -> None:
        if artifact_path.is_symlink():
            return
        artifact_path.unlink()
        artifact_path.symlink_to(external_path)

    def swap_after_resolve(path, *args, **kwargs):
        resolved = original_resolve(path, *args, **kwargs)
        if path == artifact_path:
            swap_artifact()
        return resolved

    def swap_before_open(path, flags, *args, **kwargs):
        if path == artifact_path.name and kwargs.get("dir_fd") is not None:
            swap_artifact()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(type(artifact_path), "resolve", swap_after_resolve)
    monkeypatch.setattr(os, "open", swap_before_open)

    with pytest.raises(PilotPreflightError, match="review_artifact_path_invalid"):
        normalize_review_outcome("baseline", result, artifact_root=tmp_path)


def test_preflight_accepts_complete_case_without_creating_a_new_receipt_schema() -> (
    None
):
    result = preflight_case(_frozen_case())

    assert result == {
        "case_id": "case-01",
        "ready": True,
        "frozen_field_count": 11,
    }
