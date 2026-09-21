from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import omc_peer_review as peer
import omc_review_snapshot as snapshot


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_peer_freezes_diff_before_detach(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "omc@example.test")
    _git(repo, "config", "user.name", "OMC Test")
    (repo / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "baseline")
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")

    frozen = peer.freeze_peer_input(repo)
    (repo / "app.py").write_text("value = 'changed after detach'\n", encoding="utf-8")
    loaded = snapshot.load_peer_snapshot(
        Path(str(frozen["snapshot_path"])),
        expected_sha256=str(frozen["snapshot_sha256"]),
    )

    assert b"reviewed" in loaded["review_diff"]
    assert b"changed after detach" not in loaded["review_diff"]


def test_peer_snapshot_cannot_bind_scope_changed_after_detach(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "omc@example.test")
    _git(repo, "config", "user.name", "OMC Test")
    (repo / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "baseline")
    (repo / "app.py").write_text("value = 'reviewed'\n", encoding="utf-8")

    frozen = peer.freeze_peer_input(repo)
    (repo / "app.py").write_text("value = 'changed after detach'\n", encoding="utf-8")
    evidence = snapshot.seal_review_output(
        repo,
        snapshot_path=Path(str(frozen["snapshot_path"])),
        snapshot_sha256=str(frozen["snapshot_sha256"]),
        review_output=b"peer reviewed candidate only\n",
    )

    with pytest.raises(snapshot.CandidateScopeError, match="review_stale"):
        snapshot.record_review_receipt_from_snapshot(
            repo,
            snapshot_path=Path(str(frozen["snapshot_path"])),
            snapshot_sha256=str(frozen["snapshot_sha256"]),
            verdict="APPROVE",
            review_evidence_path=Path(str(evidence["evidence_path"])),
            review_evidence_sha256=str(evidence["evidence_sha256"]),
        )
