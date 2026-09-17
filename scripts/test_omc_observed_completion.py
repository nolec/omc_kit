from __future__ import annotations

import base64
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


sys.path.insert(0, str(Path(__file__).parent))

import omc_observed_completion as completion
import omc
import omc_state


def _git_repository(root: Path, remote: str) -> None:
    root.mkdir()
    git = completion.subprocess.run
    for args in (("init",), ("config", "user.email", "omc@example.test"), ("config", "user.name", "OMC")):
        result = git(["git", "-C", str(root), *args], check=False, capture_output=True, text=True)
        assert result.returncode == 0
    result = git(
        ["git", "-C", str(root), "remote", "add", "origin", remote],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def _key_pair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii")
    return private, public


def _terminal(
    root: Path,
    private_key: Ed25519PrivateKey,
    *,
    work_id: str,
    verification_passed: bool = True,
    user_outcome: str = "accepted",
) -> Path:
    receipt = omc_state._seal_capture(
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "capture_validity": "VALID" if verification_passed else "INVALID",
            "claim": "CAPTURE_REHEARSAL",
            "session_id": "session-001",
            "work_id": work_id,
            "start_capture_sha256": "1" * 64,
            "verification_capture_sha256": "2" * 64,
            "completion_sha256": "3" * 64,
            "completion_session_id": "session-001",
            "completion_request_sha256": "4" * 64,
            "completion_lineage_sha256": "5" * 64,
            "root_session_id": "session-001",
            "session_ids": ["session-001"],
            "rework_count": 0,
            "baseline_commit": "a" * 40,
            "followup_commit": "b" * 40,
            "changed_paths": ["app.py"],
            "verification_passed": verification_passed,
            "user_outcome": user_outcome,
            "correction_evidence": None,
            "captured_at": "2026-09-17T00:00:00+00:00",
            "capture_sha256": "",
            "signoff": {},
        },
        private_key=private_key,
    )
    path = root / f"{work_id}.terminal.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _review(
    root: Path,
    private_key: Ed25519PrivateKey,
    terminal: Path,
    *,
    work_id: str,
    verdict: str = "APPROVE",
) -> Path:
    receipt = completion.seal_review(
        work_id=work_id,
        terminal_sha256=completion.hashlib.sha256(terminal.read_bytes()).hexdigest(),
        verdict=verdict,
        private_key=private_key,
        reviewed_at="2026-09-17T00:01:00+00:00",
    )
    path = root / f"{work_id}.review.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _record_complete(
    root: Path,
    terminal_private_key: Ed25519PrivateKey,
    terminal_public_key: str,
    review_private_key: Ed25519PrivateKey,
    review_public_key: str,
    work_id: str = "work-001",
) -> dict[str, object]:
    completion.enable(
        root,
        trusted_terminal_public_key=terminal_public_key,
        trusted_review_public_key=review_public_key,
    )
    terminal = _terminal(root, terminal_private_key, work_id=work_id)
    return completion.record_terminal(
        root,
        terminal=terminal,
        review=_review(root, review_private_key, terminal, work_id=work_id),
    )


def test_enable_rejects_a_shared_terminal_and_review_trust_anchor(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _git_repository(source, "git@example.test:team/source.git")
    _private_key, public_key = _key_pair()

    with pytest.raises(completion.CompletionObservationError, match="review_trust_anchor_not_independent"):
        completion.enable(
            source,
            trusted_terminal_public_key=public_key,
            trusted_review_public_key=public_key,
        )


def test_aggregate_reads_a_pre_independence_v1_ledger_without_allowing_new_writes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _git_repository(source, "git@example.test:team/source.git")
    terminal_private_key, terminal_public_key = _key_pair()
    review_private_key, review_public_key = _key_pair()
    _record_complete(
        source, terminal_private_key, terminal_public_key, review_private_key, review_public_key
    )
    config = json.loads(completion.config_path(source).read_text(encoding="utf-8"))
    config["trusted_review_public_key"] = terminal_public_key
    completion.config_path(source).write_text(json.dumps(config), encoding="utf-8")
    event = json.loads(completion.ledger_path(source).read_text(encoding="utf-8"))
    event["user_outcome"] = event.pop("terminal_reported_outcome")
    event["event_sha256"] = completion._sha256(
        {key: value for key, value in event.items() if key != "event_sha256"}
    )
    completion.ledger_path(source).write_text(json.dumps(event) + "\n", encoding="utf-8")

    assert completion.aggregate([source])["sources"] == [{
        "state": "OBSERVED_COMPLETE",
        "repo_fingerprint": completion.repository_fingerprint(source),
        "event_count": 1,
        "incomplete_reasons": [],
    }]
    with pytest.raises(completion.CompletionObservationError, match="review_trust_anchor_not_independent"):
        completion.record_terminal(
            source,
            terminal=_terminal(source, terminal_private_key, work_id="work-legacy-write"),
            review=_review(
                source,
                review_private_key,
                _terminal(source, terminal_private_key, work_id="work-legacy-review"),
                work_id="work-legacy-write",
            ),
        )


def test_aggregate_is_local_raw_free_and_keeps_other_valid_sources_when_one_is_invalid(
    tmp_path: Path,
) -> None:
    complete = tmp_path / "complete"
    incomplete = tmp_path / "incomplete"
    invalid = tmp_path / "invalid"
    _git_repository(complete, "git@example.test:team/complete.git")
    _git_repository(incomplete, "git@example.test:team/incomplete.git")
    _git_repository(invalid, "git@example.test:team/invalid.git")
    terminal_private_key, terminal_public_key = _key_pair()
    review_private_key, review_public_key = _key_pair()

    complete_event = _record_complete(
        complete, terminal_private_key, terminal_public_key, review_private_key, review_public_key
    )
    completion.enable(
        incomplete,
        trusted_terminal_public_key=terminal_public_key,
        trusted_review_public_key=review_public_key,
    )
    incomplete_terminal = _terminal(
        incomplete, terminal_private_key, work_id="work-002", verification_passed=False
    )
    incomplete_event = completion.record_terminal(
        incomplete,
        terminal=incomplete_terminal,
        review=_review(
            incomplete, review_private_key, incomplete_terminal, work_id="work-002", verdict="NOT_RUN"
        ),
    )
    completion.enable(
        invalid,
        trusted_terminal_public_key=terminal_public_key,
        trusted_review_public_key=review_public_key,
    )
    ledger = completion.ledger_path(invalid)
    ledger.write_text('{"not":"a valid event"}\n', encoding="utf-8")

    report = completion.aggregate([complete, incomplete, invalid])

    assert report["schema_version"] == 1
    assert report["network_used"] is False
    assert report["aggregate"] == {
        "observed_complete": 1,
        "observed_incomplete": 1,
        "unobserved": 0,
        "integrity_invalid": 1,
        "incomplete_reasons": {"verification_failed": 1},
    }
    assert [source["state"] for source in report["sources"]] == [
        "OBSERVED_COMPLETE",
        "OBSERVED_INCOMPLETE",
        "INTEGRITY_INVALID",
    ]
    assert complete_event["event_sha256"] != incomplete_event["event_sha256"]
    assert complete_event["terminal_reported_outcome"] == "accepted"
    assert "user_outcome" not in complete_event

    raw_bytes = completion.ledger_path(complete).read_bytes()
    assert b'"terminal_reported_outcome":"accepted"' in raw_bytes
    assert b'"user_outcome"' not in raw_bytes
    assert b"git@example.test" not in raw_bytes
    assert b"session-001" not in raw_bytes
    assert b"command" not in raw_bytes
    assert b"output" not in raw_bytes
    assert b"path" not in raw_bytes


def test_aggregate_rejects_a_config_tampered_to_share_review_and_terminal_keys(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _git_repository(source, "git@example.test:team/source.git")
    terminal_private_key, terminal_public_key = _key_pair()
    review_private_key, review_public_key = _key_pair()
    _record_complete(
        source, terminal_private_key, terminal_public_key, review_private_key, review_public_key
    )
    config = json.loads(completion.config_path(source).read_text(encoding="utf-8"))
    config["trusted_review_public_key"] = terminal_public_key
    completion.config_path(source).write_text(json.dumps(config), encoding="utf-8")

    report = completion.aggregate([source])

    assert report["sources"] == [{
        "state": "INTEGRITY_INVALID",
        "reason_code": "review_trust_anchor_not_independent",
    }]


def test_terminal_record_rejects_unenabled_repo_and_tampered_or_symlinked_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _git_repository(source, "git@example.test:team/source.git")
    terminal_private_key, terminal_public_key = _key_pair()
    review_private_key, review_public_key = _key_pair()

    with pytest.raises(completion.CompletionObservationError, match="repository_not_enabled"):
        completion.record_terminal(
            source,
            terminal=_terminal(source, terminal_private_key, work_id="work-001"),
            review=_review(
                source,
                review_private_key,
                _terminal(source, terminal_private_key, work_id="work-001-review"),
                work_id="work-001",
            ),
        )

    _record_complete(source, terminal_private_key, terminal_public_key, review_private_key, review_public_key)
    ledger = completion.ledger_path(source)
    event = json.loads(ledger.read_text(encoding="utf-8"))
    event["review"]["verdict"] = "REJECT"
    ledger.write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert completion.aggregate([source])["sources"][0]["state"] == "INTEGRITY_INVALID"

    symlink = tmp_path / "source-link"
    symlink.symlink_to(source, target_is_directory=True)
    assert completion.aggregate([symlink])["sources"][0]["state"] == "INTEGRITY_INVALID"


def test_terminal_record_requires_the_configured_signature_and_serializes_concurrent_appends(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _git_repository(source, "git@example.test:team/source.git")
    terminal_private_key, terminal_public_key = _key_pair()
    review_private_key, review_public_key = _key_pair()
    foreign_key, _foreign_public = _key_pair()
    completion.enable(
        source,
        trusted_terminal_public_key=terminal_public_key,
        trusted_review_public_key=review_public_key,
    )

    with pytest.raises(completion.CompletionObservationError, match="terminal_receipt_invalid"):
        foreign_terminal = _terminal(source, foreign_key, work_id="work-foreign")
        completion.record_terminal(
            source,
            terminal=foreign_terminal,
            review=_review(source, review_private_key, foreign_terminal, work_id="work-foreign"),
        )

    terminals = [
        _terminal(source, terminal_private_key, work_id="work-003"),
        _terminal(source, terminal_private_key, work_id="work-004"),
    ]
    reviews = [
        _review(source, review_private_key, terminal, work_id=work_id)
        for terminal, work_id in zip(terminals, ("work-003", "work-004"))
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(completion.record_terminal, source, terminal=terminal, review=review)
            for terminal, review in zip(terminals, reviews)
        ]
        [future.result() for future in futures]

    report = completion.aggregate([source])
    assert report["sources"][0]["state"] == "OBSERVED_COMPLETE"
    assert report["sources"][0]["event_count"] == 2


def test_terminal_record_rejects_missing_or_mismatched_review_receipt(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _git_repository(source, "git@example.test:team/source.git")
    terminal_private_key, terminal_public_key = _key_pair()
    review_private_key, review_public_key = _key_pair()
    completion.enable(
        source,
        trusted_terminal_public_key=terminal_public_key,
        trusted_review_public_key=review_public_key,
    )
    terminal = _terminal(source, terminal_private_key, work_id="work-005")

    with pytest.raises(completion.CompletionObservationError, match="review_receipt_required"):
        completion.record_terminal(source, terminal=terminal, review=None)

    mismatched = _review(source, review_private_key, terminal, work_id="work-other")
    with pytest.raises(completion.CompletionObservationError, match="review_receipt_invalid"):
        completion.record_terminal(source, terminal=terminal, review=mismatched)


def test_seal_review_cli_uses_external_private_key_file_and_write_once_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_key, public_key = _key_pair()
    terminal = _terminal(tmp_path, private_key, work_id="work-006")
    private_key_file = tmp_path / "review-private-key.txt"
    private_key_file.write_text(
        base64.b64encode(private_key.private_bytes_raw()).decode("ascii") + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "review.json"

    assert completion.main([
        "seal-review",
        "--work-id", "work-006",
        "--terminal-sha256", completion.hashlib.sha256(terminal.read_bytes()).hexdigest(),
        "--verdict", "APPROVE",
        "--reviewed-at", "2026-09-17T00:01:00+00:00",
        "--signer-private-key-file", str(private_key_file),
        "--out", str(output),
    ]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert completion._verify_review_receipt(receipt, public_key)["verdict"] == "APPROVE"

    assert completion.main([
        "seal-review",
        "--work-id", "work-006",
        "--terminal-sha256", completion.hashlib.sha256(terminal.read_bytes()).hexdigest(),
        "--verdict", "APPROVE",
        "--reviewed-at", "2026-09-17T00:01:00+00:00",
        "--signer-private-key-file", str(private_key_file),
        "--out", str(output),
    ]) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reason_code"] == "write_once_conflict"


def test_root_completion_report_is_a_direct_local_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    _git_repository(source, "git@example.test:team/source.git")
    terminal_private_key, terminal_public_key = _key_pair()
    review_private_key, review_public_key = _key_pair()
    _record_complete(source, terminal_private_key, terminal_public_key, review_private_key, review_public_key)

    monkeypatch.setattr(sys, "argv", ["omc.py", "completion-report", "--source", str(source)])
    assert "completion-report" in omc.direct_commands()
    assert omc.main() == 0

    report = json.loads(capsys.readouterr().out)
    assert report["aggregate"]["observed_complete"] == 1
