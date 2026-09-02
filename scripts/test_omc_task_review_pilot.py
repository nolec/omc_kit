import hashlib
import json
import os

import pytest

from omc_task_review_pilot import (
    PilotPreflightError,
    normalize_review_outcome,
    preflight_case,
    select_first_eligible_cases,
)


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
