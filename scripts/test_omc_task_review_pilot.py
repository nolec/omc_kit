import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omc_task_review_pilot import (
    PilotPreflightError,
    build_inventory_dry_run,
    build_pilot_roster,
    build_readiness_receipt,
    canonical_repository_identity,
    normalize_review_outcome,
    preflight_case,
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


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
    start = {"binding": {"roster_sha256": roster["roster_sha256"]}, "t0": t0}
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
    start = {"binding": {"roster_sha256": roster["roster_sha256"]}, "t0": t0}
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
