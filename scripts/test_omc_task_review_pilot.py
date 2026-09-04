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
