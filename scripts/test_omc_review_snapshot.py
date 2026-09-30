from __future__ import annotations

import json
import os
import subprocess
import sys
import hashlib
from pathlib import Path

import pytest

import omc_review_snapshot as snapshot
import omc_skill_effectiveness_cohort_v3 as v3
import omc_state


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _snapshot_cli(repo: Path, *args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(Path(snapshot.__file__)), *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "omc@example.test")
    _git(repo, "config", "user.name", "OMC Test")
    (repo / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return repo, _git(repo, "rev-parse", "HEAD")


def _approve(repo: Path, base: str) -> dict[str, object]:
    frozen = snapshot.capture_review_snapshot(repo, base_commit=base)
    evidence = snapshot.seal_review_output(
        repo,
        review_output=b"approved\n",
        snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
    )
    return snapshot.record_review_receipt_from_snapshot(
        repo,
        snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
        review_evidence_path=Path(str(evidence["evidence_path"])),
        review_evidence_sha256=str(evidence["evidence_sha256"]),
        verdict="APPROVE",
    )


def test_opt_in_v3_review_bridge_uses_issued_receipt_and_explicit_followup(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text(".omc/\n")
    v3.config_path(repo).parent.mkdir(exist_ok=True)
    v3.config_path(repo).write_text(json.dumps({
        "generation": "v3", "enabled": True, "status": "DRAFT_SYNTHETIC",
        "activation_id": "synthetic-only", "activation_at": "2026-09-23T00:00:00Z",
    }))
    task = omc_state.record_session(
        repo, mode="autopilot", title="omc-task", request="fixture task",
        role_ids=["senior_coding"], work_class="synthetic", completion_action="start", confirmed=True,
    )
    review = omc_state.record_session(
        repo, mode="autopilot", title="omc-review", request="fixture review",
        role_ids=["code_review"], completion_action="preserve-if-present", confirmed=True,
    )
    assert task["work_id"] == review["work_id"]
    (repo / "app.py").write_text("value = 'reviewed'\n")
    frozen = snapshot.capture_review_snapshot(repo, base_commit=base)
    evidence = snapshot.seal_review_output(
        repo, review_output=b"approved\n", snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
    )
    result = _snapshot_cli(
        repo, "record-review", "--review-snapshot", str(frozen["snapshot_path"]),
        "--review-snapshot-sha256", str(frozen["snapshot_sha256"]),
        "--review-evidence", str(evidence["evidence_path"]),
        "--review-evidence-sha256", str(evidence["evidence_sha256"]),
        "--verdict", "APPROVE", "--cohort-session-id", str(review["session_id"]),
        "--cohort-taxonomy", "verification_gap",
    )
    cohort = result["cohort_capture_v3"]
    assert cohort["status"] == "recorded"
    assert v3.report(repo)["review_count"] == 1
    assert v3.report(repo)["outcome_unobserved"] == 1
    replay = snapshot.record_review_receipt_from_snapshot(
        repo, snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
        review_evidence_path=Path(str(evidence["evidence_path"])),
        review_evidence_sha256=str(evidence["evidence_sha256"]), verdict="APPROVE",
        cohort_session_id=str(review["session_id"]), cohort_taxonomy="verification_gap",
    )
    assert replay["cohort_capture_v3"] == {
        "status": "unobserved", "reason_code": "review_session_already_recorded",
    }
    assert snapshot.load_review_receipt(repo, Path(str(result["receipt_path"])))["receipt_sha256"] == result["receipt_sha256"]
    assert v3.report(repo)["review_count"] == 1
    followup = subprocess.run(
        [sys.executable, str(Path(v3.__file__)), "record-explicit-followup",
         "--target", str(repo), "--choice-id", cohort["choice_id"], "--outcome", "accepted"],
        capture_output=True, text=True,
    )
    assert followup.returncode == 0, followup.stdout + followup.stderr
    assert v3.report(repo)["accepted"] == 1
    assert not (repo / ".omc" / "skill-effectiveness-cohort-v2.jsonl").exists()


def test_v3_choice_io_failure_does_not_hide_valid_review_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base = _repo(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text(".omc/\n")
    v3.config_path(repo).parent.mkdir(exist_ok=True)
    v3.config_path(repo).write_text(json.dumps({
        "generation": "v3", "enabled": True, "status": "DRAFT_SYNTHETIC",
        "activation_id": "synthetic-only", "activation_at": "2026-09-23T00:00:00Z",
    }))
    omc_state.record_session(
        repo, mode="autopilot", title="omc-task", request="fixture task",
        role_ids=["senior_coding"], work_class="synthetic", completion_action="start", confirmed=True,
    )
    review = omc_state.record_session(
        repo, mode="autopilot", title="omc-review", request="fixture review",
        role_ids=["code_review"], completion_action="preserve-if-present", confirmed=True,
    )
    (repo / "app.py").write_text("value = 'reviewed'\n")
    frozen = snapshot.capture_review_snapshot(repo, base_commit=base)
    evidence = snapshot.seal_review_output(
        repo, review_output=b"approved\n", snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
    )
    original_choice = v3.create_choice
    def fail_choice(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise OSError("simulated choice write failure")
    monkeypatch.setattr(v3, "create_choice", fail_choice)
    result = snapshot.record_review_receipt_from_snapshot(
        repo, snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]), verdict="APPROVE",
        review_evidence_path=Path(str(evidence["evidence_path"])),
        review_evidence_sha256=str(evidence["evidence_sha256"]),
        cohort_session_id=str(review["session_id"]), cohort_taxonomy="verification_gap",
    )
    assert result["cohort_capture_v3"] == {
        "status": "unobserved", "reason_code": "capture_io_error",
    }
    assert snapshot.load_review_receipt(repo, Path(str(result["receipt_path"])))["review_verdict"] == "APPROVE"
    assert v3.report(repo)["review_count"] == 1
    monkeypatch.setattr(v3, "create_choice", original_choice)
    recovered = v3.resume_review_choice(repo, session_id=str(review["session_id"]))
    assert recovered["choice_id"]
    assert v3.report(repo)["review_count"] == 1


def test_worktree_and_committed_candidate_have_same_identity(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    (repo / "new.py").write_text("value = 1\n", encoding="utf-8")

    reviewed = snapshot.build_worktree_candidate(repo, base_commit=base)
    _git(repo, "add", "app.py", "new.py")
    _git(repo, "commit", "-qm", "reviewed")
    committed = snapshot.build_commit_candidate(
        repo,
        base_commit=base,
        candidate_commit=_git(repo, "rev-parse", "HEAD"),
    )

    assert committed["candidate_scope"] == reviewed["candidate_scope"]
    assert committed["candidate_scope_sha256"] == reviewed["candidate_scope_sha256"]


def test_committed_candidate_can_be_recorded_then_validated_for_ship(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "reviewed")
    frozen = snapshot.capture_review_snapshot(
        repo,
        base_commit=base,
        candidate_commit=_git(repo, "rev-parse", "HEAD"),
    )
    evidence = snapshot.seal_review_output(
        repo,
        review_output=b"approved\n",
        snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
    )

    snapshot.record_review_receipt_from_snapshot(
        repo,
        snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
        review_evidence_path=Path(str(evidence["evidence_path"])),
        review_evidence_sha256=str(evidence["evidence_sha256"]),
        verdict="APPROVE",
    )

    assert snapshot.validate_ship_candidate(repo)["status"] == "READY"


def test_repository_identity_is_stable_across_a_second_checkout(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(repo), str(clone))

    original = snapshot.build_worktree_candidate(repo, base_commit=base)
    copied = snapshot.build_worktree_candidate(clone, base_commit=base)

    assert original["repository_identity"] == copied["repository_identity"]
    assert original["candidate_scope_sha256"] == copied["candidate_scope_sha256"]


def test_review_receipt_rejects_scope_changed_after_capture(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    frozen = snapshot.capture_review_snapshot(repo, base_commit=base)

    (repo / "app.py").write_text("value = 'changed after review'\n", encoding="utf-8")

    with pytest.raises(snapshot.CandidateScopeError, match="review_stale"):
        snapshot.record_review_receipt_from_snapshot(
            repo,
            snapshot_path=Path(str(frozen["snapshot_path"])),
            snapshot_sha256=str(frozen["snapshot_sha256"]),
            verdict="APPROVE",
            review_evidence_path=Path("/missing/evidence.json"),
            review_evidence_sha256="0" * 64,
        )


def test_review_receipt_rejects_evidence_sealed_for_another_snapshot(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    first = snapshot.capture_review_snapshot(repo, base_commit=base)
    (repo / "app.py").write_text("value = 'different candidate'\n", encoding="utf-8")
    second = snapshot.capture_review_snapshot(repo, base_commit=base)
    evidence = snapshot.seal_review_output(
        repo,
        snapshot_path=Path(str(first["snapshot_path"])),
        snapshot_sha256=str(first["snapshot_sha256"]),
        review_output=b"reviewed only first snapshot\n",
    )

    with pytest.raises(snapshot.CandidateScopeError, match="review_evidence_snapshot_mismatch"):
        snapshot.record_review_receipt_from_snapshot(
            repo,
            snapshot_path=Path(str(second["snapshot_path"])),
            snapshot_sha256=str(second["snapshot_sha256"]),
            review_evidence_path=Path(str(evidence["evidence_path"])),
            review_evidence_sha256=str(evidence["evidence_sha256"]),
            verdict="APPROVE",
        )


def test_post_commit_capture_records_and_ships_the_committed_candidate(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "reviewed")

    frozen = snapshot.capture_review_snapshot(
        repo, base_commit=base, candidate_commit=_git(repo, "rev-parse", "HEAD")
    )
    evidence = snapshot.seal_review_output(
        repo,
        snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
        review_output=b"reviewed committed candidate\n",
    )
    snapshot.record_review_receipt_from_snapshot(
        repo,
        snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
        review_evidence_path=Path(str(evidence["evidence_path"])),
        review_evidence_sha256=str(evidence["evidence_sha256"]),
        verdict="APPROVE",
    )

    assert snapshot.validate_ship_candidate(repo)["status"] == "READY"


def test_cli_supports_post_commit_review_evidence_and_ship_validation(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "reviewed")
    raw = tmp_path / "review.md"
    raw.write_text("reviewed committed candidate\n", encoding="utf-8")

    frozen = _snapshot_cli(
        repo,
        "capture-review",
        "--target",
        ".",
        "--base-commit",
        base,
        "--candidate-commit",
        "HEAD",
    )
    evidence = _snapshot_cli(
        repo,
        "seal-review-output",
        "--target",
        ".",
        "--review-snapshot",
        str(frozen["snapshot_path"]),
        "--review-snapshot-sha256",
        str(frozen["snapshot_sha256"]),
        "--review-output",
        str(raw),
    )
    _snapshot_cli(
        repo,
        "record-review",
        "--target",
        ".",
        "--review-snapshot",
        str(frozen["snapshot_path"]),
        "--review-snapshot-sha256",
        str(frozen["snapshot_sha256"]),
        "--review-evidence",
        str(evidence["evidence_path"]),
        "--review-evidence-sha256",
        str(evidence["evidence_sha256"]),
        "--verdict",
        "APPROVE",
    )

    assert _snapshot_cli(repo, "validate-ship", "--target", ".")["status"] == "READY"


def test_cli_malformed_snapshot_returns_blocked_json_without_traceback(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    malformed = tmp_path / "malformed-snapshot.json"
    malformed.write_text("{\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(snapshot.__file__)),
            "show-review-diff",
            "--review-snapshot",
            str(malformed),
            "--review-snapshot-sha256",
            hashlib.sha256(malformed.read_bytes()).hexdigest(),
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "BLOCKED",
        "reason_code": "peer_snapshot_invalid",
    }
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("path", "contents"),
    [
        ("app.py", "value = 'changed'\n"),
        ("tests/test_app.py", "assert False\n"),
        ("untracked.py", "value = 1\n"),
    ],
)
def test_ship_blocks_code_test_and_untracked_scope_drift(
    tmp_path: Path, path: str, contents: str
) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    _approve(repo, base)

    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")

    result = snapshot.validate_ship_candidate(repo)

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "review_stale"
    assert path in result["changed_paths"]


def test_ship_blocks_untracked_mutation_and_deletion(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "generated.py").write_text("version = 1\n", encoding="utf-8")
    _approve(repo, base)

    (repo / "generated.py").write_text("version = 2\n", encoding="utf-8")
    changed = snapshot.validate_ship_candidate(repo)
    assert changed["reason_code"] == "review_stale"

    (repo / "generated.py").unlink()
    deleted = snapshot.validate_ship_candidate(repo)
    assert deleted["reason_code"] == "review_stale"
    assert "generated.py" in deleted["changed_paths"]


def test_ship_blocks_executable_mode_and_symlink_identity_drift(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (repo / "link").symlink_to("app.py")
    _approve(repo, base)

    os.chmod(repo / "run.sh", 0o755)
    (repo / "link").unlink()
    (repo / "link").symlink_to("run.sh")
    result = snapshot.validate_ship_candidate(repo)

    assert result["reason_code"] == "review_stale"
    assert {"run.sh", "link"}.issubset(result["changed_paths"])


def test_ship_blocks_submodule_identity_drift(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    _git(child, "init", "-q")
    _git(child, "config", "user.email", "omc@example.test")
    _git(child, "config", "user.name", "OMC Test")
    (child / "child.py").write_text("value = 1\n", encoding="utf-8")
    _git(child, "add", "child.py")
    _git(child, "commit", "-qm", "child baseline")

    repo, _ = _repo(tmp_path)
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "deps/child")
    _git(repo, "add", ".gitmodules", "deps/child")
    _git(repo, "commit", "-qm", "add child")
    base = _git(repo, "rev-parse", "HEAD")

    (child / "child.py").write_text("value = 2\n", encoding="utf-8")
    _git(child, "add", "child.py")
    _git(child, "commit", "-qm", "child reviewed")
    reviewed_child = _git(child, "rev-parse", "HEAD")
    _git(repo / "deps" / "child", "fetch", "origin")
    _git(repo / "deps" / "child", "checkout", "-q", reviewed_child)
    _approve(repo, base)

    (child / "child.py").write_text("value = 3\n", encoding="utf-8")
    _git(child, "add", "child.py")
    _git(child, "commit", "-qm", "child changed")
    _git(repo / "deps" / "child", "fetch", "origin")
    _git(repo / "deps" / "child", "checkout", "-q", _git(child, "rev-parse", "HEAD"))

    result = snapshot.validate_ship_candidate(repo)
    assert result["reason_code"] == "review_stale"
    assert result["changed_paths"] == ["deps/child"]


def test_ship_allows_unchanged_worktree_and_unchanged_commit(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    _approve(repo, base)

    assert snapshot.validate_ship_candidate(repo)["status"] == "READY"

    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "reviewed")
    assert snapshot.validate_ship_candidate(repo)["status"] == "READY"


def test_ship_blocks_commit_hook_or_formatter_content_drift(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    _approve(repo, base)

    (repo / "app.py").write_text("value = 'formatted-after-review'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "formatter changed content")

    result = snapshot.validate_ship_candidate(repo)
    assert result["reason_code"] == "review_stale"
    assert result["changed_paths"] == ["app.py"]


def test_snapshot_receipt_and_pointer_tampering_fail_closed(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    receipt = _approve(repo, base)
    pointer_path = Path(str(receipt["pointer_path"]))

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["receipt_sha256"] = "0" * 64
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    result = snapshot.validate_ship_candidate(repo)
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "review_receipt_invalid"


def test_missing_review_pointer_fails_closed(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    receipt = _approve(repo, base)
    Path(str(receipt["pointer_path"])).unlink()

    result = snapshot.validate_ship_candidate(repo)

    assert result == {
        "status": "BLOCKED",
        "reason_code": "review_receipt_invalid",
        "changed_paths": [],
    }


@pytest.mark.parametrize("artifact_key", ["review_snapshot_name", "review_evidence_name"])
def test_ship_blocks_when_bound_review_artifact_is_missing_or_tampered(
    tmp_path: Path, artifact_key: str
) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    receipt = _approve(repo, base)
    stored = json.loads(Path(str(receipt["receipt_path"])).read_text(encoding="utf-8"))
    artifact = Path(str(receipt["receipt_path"])).parent / stored[artifact_key]

    if artifact_key == "review_snapshot_name":
        artifact.unlink()
    else:
        artifact.write_text("{}\n", encoding="utf-8")

    result = snapshot.validate_ship_candidate(repo)

    assert result == {
        "status": "BLOCKED",
        "reason_code": "review_receipt_invalid",
        "changed_paths": [],
    }


def test_rebased_or_unrelated_candidate_fails_closed(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _git(repo, "checkout", "--orphan", "unrelated")
    _git(repo, "rm", "-rf", ".")
    (repo / "other.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-qm", "unrelated")
    unrelated = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(snapshot.CandidateScopeError, match="base_commit_not_ancestor"):
        snapshot.build_commit_candidate(
            repo,
            base_commit=base,
            candidate_commit=unrelated,
        )


def test_async_peer_snapshot_is_hash_bound_and_tamper_detected(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    candidate = snapshot.build_worktree_candidate(repo, base_commit=base)
    frozen = snapshot.create_peer_snapshot(
        repo, candidate=candidate, review_diff=b"reviewed diff\n"
    )

    loaded = snapshot.load_peer_snapshot(
        Path(str(frozen["path"])), expected_sha256=str(frozen["sha256"])
    )
    assert loaded["candidate"]["candidate_scope_sha256"] == candidate["candidate_scope_sha256"]
    assert loaded["review_diff"] == b"reviewed diff\n"

    Path(str(frozen["path"])).write_text("{}\n", encoding="utf-8")
    with pytest.raises(snapshot.PeerSnapshotError, match="peer_snapshot_sha256_mismatch"):
        snapshot.load_peer_snapshot(
            Path(str(frozen["path"])), expected_sha256=str(frozen["sha256"])
        )


def test_async_peer_snapshot_retains_parent_diff_after_worktree_changes(
    tmp_path: Path,
) -> None:
    repo, base = _repo(tmp_path)
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")
    candidate = snapshot.build_worktree_candidate(repo, base_commit=base)
    frozen = snapshot.create_peer_snapshot(
        repo, candidate=candidate, review_diff=b"diff from parent\n"
    )

    (repo / "app.py").write_text("value = 'changed after detach'\n", encoding="utf-8")
    loaded = snapshot.load_peer_snapshot(
        Path(str(frozen["path"])), expected_sha256=str(frozen["sha256"])
    )

    assert loaded["review_diff"] == b"diff from parent\n"
    assert loaded["candidate"]["candidate_scope_sha256"] == candidate["candidate_scope_sha256"]
