from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import omc_plan_candidate_universe as candidate_universe  # noqa: E402
import omc_state  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _private_key_text(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode("ascii")


@pytest.fixture
def capture_repo(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "capture@example.com")
    _git(repo, "config", "user.name", "Capture Test")
    (repo / "app.py").write_text("print('before')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "initial")
    private_key = Ed25519PrivateKey.generate()
    session = omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-task",
        request="change app output",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="start",
        confirmed=True,
    )
    candidate_universe.seal_session_work_class_lock(repo, session, private_key)
    return repo, private_key, candidate_universe.public_key_text(private_key)


def _plan(path: Path, *commands: dict[str, object]) -> Path:
    path.write_text(
        json.dumps({"schema_version": 1, "commands": list(commands)}),
        encoding="utf-8",
    )
    return path


def test_capture_start_freezes_exact_verification_plan(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('verified')"], "timeout_sec": 10},
    )

    receipt = omc_state.start_capture(
        repo,
        plan,
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )

    assert receipt["status"] == "READY"
    assert receipt["verification_plan"]["commands"][0]["argv"][0] == sys.executable
    assert receipt["work_id"]
    assert receipt["signoff"]["signer_public_key"] == public_key
    assert (repo / ".omc/state/sessions" / receipt["session_id"] / "capture/start.json").is_file()
    with pytest.raises(ValueError, match="capture already started"):
        omc_state.start_capture(
            repo,
            plan,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": 1, "commands": []},
        {"schema_version": 1, "commands": [{"argv": "pytest", "timeout_sec": 10}]},
        {"schema_version": 1, "commands": [{"argv": ["pytest"], "timeout_sec": 0}]},
        {"schema_version": 1, "commands": [{"argv": ["pytest"], "timeout_sec": 10}], "extra": True},
    ],
)
def test_capture_start_rejects_invalid_plan(
    capture_repo: tuple[Path, Ed25519PrivateKey, str],
    tmp_path: Path,
    payload: object,
) -> None:
    repo, private_key, public_key = capture_repo
    plan = tmp_path / "verification.json"
    plan.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="verification plan invalid"):
        omc_state.start_capture(
            repo,
            plan,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_capture_start_rejects_symlink_plan(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    target = _plan(
        tmp_path / "target.json",
        {"argv": ["pytest"], "timeout_sec": 10},
    )
    plan = tmp_path / "verification.json"
    plan.symlink_to(target)
    with pytest.raises(ValueError, match="input not regular file"):
        omc_state.start_capture(
            repo,
            plan,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_capture_start_rejects_preexisting_untracked_work(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    (repo / "already-built.py").write_text("print('done')\n", encoding="utf-8")
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": ["pytest"], "timeout_sec": 10},
    )

    with pytest.raises(ValueError, match="retrospective capture forbidden"):
        omc_state.start_capture(
            repo,
            plan,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_capture_start_rejects_tampered_work_class_lock(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    session_id = json.loads((repo / ".omc/state/latest.json").read_text())["latest_session_id"]
    lock_path = repo / ".omc/state/sessions" / session_id / "work_class_lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["signoff"]["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": ["pytest"], "timeout_sec": 10},
    )

    with pytest.raises(ValueError, match="work class lock invalid"):
        omc_state.start_capture(
            repo,
            plan,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_work_class_lock_seals_lineage_in_single_receipt(
    capture_repo: tuple[Path, Ed25519PrivateKey, str]
) -> None:
    repo, private_key, _ = capture_repo
    pending = json.loads((repo / ".omc/state/pending-completion.json").read_text())
    continuation = omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-task",
        request="recover partial lineage write",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="continue",
        work_id=pending["work_id"],
        confirmed=True,
    )
    candidate_universe.seal_session_work_class_lock(repo, continuation, private_key)
    session_dir = repo / ".omc/state/sessions" / continuation["session_id"]
    lock = json.loads((session_dir / "work_class_lock.json").read_text())
    assert lock["schema_version"] == 2
    assert lock["work_id"] == continuation["work_id"]
    assert lock["root_session_id"] == continuation["lineage_root_session_id"]
    assert lock["previous_session_id"] == continuation["lineage_previous_session_id"]
    assert lock["lineage_index"] == continuation["lineage_index"]
    assert not (session_dir / "completion_lineage_link.json").exists()

    continuation["lineage_previous_session_id"] = "tampered"
    with pytest.raises(ValueError, match="work class lock receipt already exists"):
        candidate_universe.seal_session_work_class_lock(repo, continuation, private_key)


def test_capture_verify_preserves_raw_output_and_failure(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
        {"argv": [sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(3)"], "timeout_sec": 10},
    )
    start = omc_state.start_capture(
        repo,
        plan,
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )

    result = omc_state.verify_capture(
        repo,
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )

    assert result["status"] == "VERIFIED"
    assert result["verification_passed"] is False
    assert [item["exit_code"] for item in result["results"]] == [0, 3]
    capture_dir = repo / ".omc/state/sessions" / start["session_id"] / "capture"
    stdout = (capture_dir / result["results"][0]["stdout_path"]).read_bytes()
    stderr = (capture_dir / result["results"][1]["stderr_path"]).read_bytes()
    assert stdout == b"ok\n"
    assert stderr == b"bad\n"
    assert result["results"][0]["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()


def test_capture_finish_rejects_commit_changed_after_successful_verification(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    (repo / "app.py").write_text("print('verified')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    omc_state.verify_capture(
        repo, signer_private_key=private_key, trusted_public_key=public_key
    )
    (repo / "app.py").write_text("print('changed-after-verify')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "change after verify")
    omc_state.record_completion_receipt(repo)

    with pytest.raises(ValueError, match="completion binding invalid"):
        omc_state.finish_capture(
            repo,
            outcome="accepted",
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_capture_verify_records_invalid_tree_mutated_by_verification_command(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    mutate = (
        "from pathlib import Path; import subprocess; "
        "Path('app.py').write_text(\"print('mutated')\\n\"); "
        "subprocess.run(['git', 'add', 'app.py'], check=True)"
    )
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", mutate], "timeout_sec": 10},
    )
    omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    (repo / "app.py").write_text("print('candidate')\n", encoding="utf-8")
    _git(repo, "add", "app.py")

    result = omc_state.verify_capture(
        repo,
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )

    assert result["verification_passed"] is False
    assert result["tree_unchanged"] is False
    assert omc_state.capture_status(repo, trusted_public_key=public_key)["status"] == "VERIFIED"


def test_capture_continues_across_code_review_session(
    capture_repo: tuple[Path, Ed25519PrivateKey, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    start = omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    (repo / "app.py").write_text("print('after')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-review",
        request="review staged change",
        role_ids=["code_review"],
        confirmed=True,
    )
    continuation = omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-task",
        request="apply review correction",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="continue",
        work_id=str(start["work_id"]),
        confirmed=True,
    )
    candidate_universe.seal_session_work_class_lock(repo, continuation, private_key)

    verification = omc_state.verify_capture(
        repo, signer_private_key=private_key, trusted_public_key=public_key
    )
    _git(repo, "commit", "-qm", "reviewed change")
    omc_state.record_completion_receipt(repo)
    envelope_validation_count = 0
    original_validate = candidate_universe._validate_work_class_lock_receipt_envelope

    def count_envelope_validation(receipt: object, *, expected_status: str) -> None:
        nonlocal envelope_validation_count
        envelope_validation_count += 1
        original_validate(receipt, expected_status=expected_status)

    monkeypatch.setattr(
        candidate_universe,
        "_validate_work_class_lock_receipt_envelope",
        count_envelope_validation,
    )
    terminal = omc_state.finish_capture(
        repo,
        outcome="accepted",
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )

    assert verification["session_id"] == start["session_id"]
    assert terminal["session_id"] == start["session_id"]
    assert terminal["completion_session_id"] == continuation["session_id"]
    assert terminal["completion_request_sha256"] == omc_state._canonical_sha256(
        continuation["request"]
    )
    assert terminal["session_ids"] == [start["session_id"], continuation["session_id"]]
    assert terminal["rework_count"] == 1
    assert terminal["capture_validity"] == "VALID"
    assert envelope_validation_count == 3
    lineage_path = (
        repo
        / ".omc/state/sessions"
        / continuation["session_id"]
        / "completion-lineage.json"
    )
    tampered = json.loads(lineage_path.read_text(encoding="utf-8"))
    tampered["rework_count"] = 0
    lineage_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert omc_state.capture_status(repo, trusted_public_key=public_key)["status"] == "BLOCKED"


@pytest.mark.parametrize("attack", ["omit_with_session_tamper", "reorder"])
def test_capture_finish_rejects_tampered_completion_lineage(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path, attack: str
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    start = omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    (repo / "app.py").write_text("print('after')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    intermediate = omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-task",
        request="first correction",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="continue",
        work_id=str(start["work_id"]),
        confirmed=True,
    )
    candidate_universe.seal_session_work_class_lock(repo, intermediate, private_key)
    second_intermediate = omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-task",
        request="second correction",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="continue",
        work_id=str(start["work_id"]),
        confirmed=True,
    )
    candidate_universe.seal_session_work_class_lock(repo, second_intermediate, private_key)
    final = omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-task",
        request="final correction",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="continue",
        work_id=str(start["work_id"]),
        confirmed=True,
    )
    candidate_universe.seal_session_work_class_lock(repo, final, private_key)
    omc_state.verify_capture(
        repo, signer_private_key=private_key, trusted_public_key=public_key
    )
    _git(repo, "commit", "-qm", "corrected change")
    omc_state.record_completion_receipt(repo)
    lineage_path = repo / ".omc/state/sessions" / final["session_id"] / "completion-lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    if attack == "omit_with_session_tamper":
        lineage["session_ids"] = [
            start["session_id"], second_intermediate["session_id"], final["session_id"]
        ]
        lineage["rework_count"] = 2
    else:
        lineage["session_ids"] = [
            start["session_id"],
            second_intermediate["session_id"],
            intermediate["session_id"],
            final["session_id"],
        ]
    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")
    if attack == "omit_with_session_tamper":
        intermediate_session_path = (
            repo / ".omc/state/sessions" / intermediate["session_id"] / "session.json"
        )
        intermediate_session = json.loads(intermediate_session_path.read_text(encoding="utf-8"))
        intermediate_session["work_id"] = "other-work"
        intermediate_session_path.write_text(json.dumps(intermediate_session), encoding="utf-8")

    with pytest.raises(ValueError, match="completion lineage invalid"):
        omc_state.finish_capture(
            repo,
            outcome="accepted",
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_capture_verify_rejects_signed_start_from_another_session(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    first = omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    second_session = omc_state.record_session(
        repo,
        mode="autopilot",
        title="omc-task",
        request="continue different session",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="continue",
        work_id=str(first["work_id"]),
        confirmed=True,
    )
    candidate_universe.seal_session_work_class_lock(repo, second_session, private_key)
    first_path = repo / ".omc/state/sessions" / first["session_id"] / "capture/start.json"
    second_dir = repo / ".omc/state/sessions" / second_session["session_id"] / "capture"
    second_dir.mkdir(parents=True)
    (second_dir / "start.json").write_bytes(first_path.read_bytes())

    with pytest.raises(ValueError, match="capture (context invalid|session ambiguous)"):
        omc_state.verify_capture(
            repo,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_bounded_command_timeout_kills_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "child-survived"
    child_code = f"import time, pathlib; time.sleep(1.2); pathlib.Path({str(marker)!r}).write_text('bad')"
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(5)"
    )

    _, _, _, timed_out, _ = omc_state._run_bounded_command(
        [sys.executable, "-c", parent_code], cwd=tmp_path, timeout_sec=1
    )
    time.sleep(0.5)

    assert timed_out is True
    assert not marker.exists()


def test_capture_finish_keeps_user_outcome_independent_from_verification(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "import sys; sys.exit(1)"], "timeout_sec": 10},
    )
    start = omc_state.start_capture(
        repo,
        plan,
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )
    (repo / "app.py").write_text("print('after')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    omc_state.verify_capture(
        repo,
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )
    _git(repo, "commit", "-qm", "change output")
    omc_state.record_completion_receipt(repo)

    receipt = omc_state.finish_capture(
        repo,
        outcome="accepted",
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )

    assert receipt["user_outcome"] == "accepted"
    assert receipt["verification_passed"] is False
    assert receipt["capture_validity"] == "INVALID"
    assert receipt["claim"] == "CAPTURE_REHEARSAL"
    assert receipt["signoff"]["signer_public_key"] == public_key
    with pytest.raises(ValueError, match="capture already finalized"):
        omc_state.finish_capture(
            repo,
            outcome="accepted",
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_capture_status_is_direct_and_fail_closed(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    start = omc_state.start_capture(
        repo,
        plan,
        signer_private_key=private_key,
        trusted_public_key=public_key,
    )
    assert omc_state.capture_status(repo, trusted_public_key=public_key) == {
        "status": "READY",
        "capture_validity": "INCOMPLETE",
        "session_id": start["session_id"],
        "work_id": start["work_id"],
        "verification_passed": None,
        "user_outcome": None,
        "reason": None,
        "claim": "CAPTURE_REHEARSAL",
    }
    start_path = repo / ".omc/state/sessions" / start["session_id"] / "capture/start.json"
    tampered = json.loads(start_path.read_text(encoding="utf-8"))
    tampered["request_sha256"] = "0" * 64
    start_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert omc_state.capture_status(repo, trusted_public_key=public_key)["status"] == "BLOCKED"


def test_capture_verify_rejects_unknown_signed_start_field(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    start = omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    start_path = repo / ".omc/state/sessions" / start["session_id"] / "capture/start.json"
    start["unexpected"] = True
    start_path.write_text(
        json.dumps(omc_state._seal_capture(start, private_key=private_key)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="capture receipt invalid"):
        omc_state.verify_capture(
            repo,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_capture_status_revalidates_raw_verification_artifact(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    start = omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    result = omc_state.verify_capture(
        repo, signer_private_key=private_key, trusted_public_key=public_key
    )
    capture_dir = repo / ".omc/state/sessions" / start["session_id"] / "capture"
    (capture_dir / result["results"][0]["stdout_path"]).write_bytes(b"tampered\n")

    assert omc_state.capture_status(repo, trusted_public_key=public_key)["status"] == "BLOCKED"


def test_capture_status_blocks_when_staged_tree_changes_after_verification(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    omc_state.start_capture(
        repo, plan, signer_private_key=private_key, trusted_public_key=public_key
    )
    (repo / "app.py").write_text("print('verified')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    omc_state.verify_capture(
        repo, signer_private_key=private_key, trusted_public_key=public_key
    )
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    _git(repo, "add", "app.py")

    assert omc_state.capture_status(repo, trusted_public_key=public_key)["status"] == "BLOCKED"


def test_capture_finish_requires_nonempty_correction_evidence(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    correction = tmp_path / "correction.txt"
    correction.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="correction evidence required"):
        omc_state.finish_capture(
            repo,
            outcome="correction_required",
            correction_file=correction,
            signer_private_key=private_key,
            trusted_public_key=public_key,
        )


def test_omc_cli_exposes_capture_start_and_status(
    capture_repo: tuple[Path, Ed25519PrivateKey, str], tmp_path: Path
) -> None:
    repo, private_key, public_key = capture_repo
    plan = _plan(
        tmp_path / "verification.json",
        {"argv": [sys.executable, "-c", "print('ok')"], "timeout_sec": 10},
    )
    key_path = tmp_path / "capture.key"
    key_path.write_text(_private_key_text(private_key), encoding="utf-8")
    key_path.chmod(0o600)
    env = {
        **os.environ,
        "OMC_REQUIRE_WORK_CLASS_LOCK": "1",
        "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE": str(key_path),
        "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY": public_key,
    }
    started = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc.py"),
            "state",
            "capture-start",
            "--target",
            str(repo),
            "--verification-plan",
            str(plan),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert started.returncode == 0, started.stderr
    assert json.loads(started.stdout)["status"] == "READY"
    status = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc.py"),
            "state",
            "capture-status",
            "--target",
            str(repo),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["status"] == "READY"
