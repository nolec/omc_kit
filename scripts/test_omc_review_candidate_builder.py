from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omc_review_candidate_builder import (
    CandidateBuildError,
    approve_candidate_manifest,
    build_private_provenance_audit,
    build_candidate_bundle,
    persist_manifest_after_audit,
)


def _spec() -> dict[str, object]:
    return {
        "source_type": "observed_output",
        "candidates": [
            {
                "case_id": "observed-safe-case",
                "source_commit": "abc123",
                "source_title": "anonymized observed diff",
                "source_repo": "/private/source-project",
            }
        ],
    }


def test_build_candidate_bundle_writes_safe_diff_without_source_repository(tmp_path: Path):
    diff = "diff --git a/src/example.py b/src/example.py\n--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n"

    manifest = build_candidate_bundle(
        _spec(),
        tmp_path,
        read_diff=lambda candidate: diff,
    )

    candidate = manifest["candidates"][0]
    assert manifest["status"] == "pending_anonymization_review"
    assert candidate["anonymized"] is False
    assert candidate["diff_sha256"] == hashlib.sha256(diff.encode()).hexdigest()
    assert "source_repo" not in candidate
    assert (tmp_path / "observed-safe-case.diff").read_text(encoding="utf-8") == diff


def test_build_candidate_bundle_rejects_sensitive_diff_before_writing(tmp_path: Path):
    diff = "diff --git a/src/example.py b/src/example.py\n--- a/src/example.py\n+++ b/src/example.py\n+owner@example.com\n"

    with pytest.raises(CandidateBuildError, match="sensitive value"):
        build_candidate_bundle(_spec(), tmp_path, read_diff=lambda candidate: diff)

    assert list(tmp_path.iterdir()) == []


def test_build_candidate_bundle_applies_explicit_redactions_before_validation(tmp_path: Path):
    spec = _spec()
    spec["candidates"][0]["redactions"] = {"owner@example.com": "[redacted-email]"}
    diff = "diff --git a/src/example.py b/src/example.py\n--- a/src/example.py\n+++ b/src/example.py\n+owner@example.com\n"

    build_candidate_bundle(spec, tmp_path, read_diff=lambda candidate: diff)

    assert "owner@example.com" not in (tmp_path / "observed-safe-case.diff").read_text(encoding="utf-8")


def test_build_candidate_bundle_can_rebuild_from_a_private_saved_diff(tmp_path: Path):
    private_diff = tmp_path / "private.diff"
    private_diff.write_text("diff --git a/src/example.py b/src/example.py\n+safe\n", encoding="utf-8")
    spec = _spec()
    spec["candidates"][0].pop("source_repo")
    spec["candidates"][0]["source_diff_path"] = str(private_diff)

    build_candidate_bundle(spec, tmp_path / "output")

    assert (tmp_path / "output" / "observed-safe-case.diff").is_file()


def test_build_candidate_bundle_allows_git_dev_null_for_deleted_files(tmp_path: Path):
    diff = "diff --git a/src/obsolete.py b/src/obsolete.py\n--- a/src/obsolete.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"

    build_candidate_bundle(_spec(), tmp_path, read_diff=lambda candidate: diff)

    assert (tmp_path / "observed-safe-case.diff").is_file()


def test_build_candidate_bundle_rejects_overwriting_private_source_diff(tmp_path: Path):
    private_diff = tmp_path / "observed-safe-case.diff"
    original = "diff --git a/src/example.py b/src/example.py\n+safe\n"
    private_diff.write_text(original, encoding="utf-8")
    spec = _spec()
    spec["candidates"][0].pop("source_repo")
    spec["candidates"][0]["source_diff_path"] = str(private_diff)

    with pytest.raises(CandidateBuildError, match="overwrite a private source diff"):
        build_candidate_bundle(spec, tmp_path)

    assert private_diff.read_text(encoding="utf-8") == original


def test_approve_candidate_manifest_promotes_only_hash_verified_candidates(tmp_path: Path):
    diff = "diff --git a/src/example.py b/src/example.py\n+safe\n"
    path = tmp_path / "case.diff"
    path.write_text(diff, encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "pending_anonymization_review",
        "candidates": [
            {
                "case_id": "observed-safe-case",
                "source_commit": "abc123",
                "source_title": "anonymized observed diff",
                "diff_path": "case.diff",
                "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
                "anonymized": False,
                "anonymization_status": "pending_review",
            }
        ],
    }

    approved, audit = approve_candidate_manifest(manifest, tmp_path, approved_by="reviewer-a")

    assert approved["status"] == "approved_for_provider_execution"
    assert approved["candidates"][0]["anonymized"] is True
    assert approved["candidates"][0]["anonymization_status"] == "passed"
    assert audit["approved_by"] == "reviewer-a"
    assert audit["cases"] == [{"case_id": "observed-safe-case", "diff_sha256": manifest["candidates"][0]["diff_sha256"]}]


def test_approve_candidate_manifest_rejects_hash_mismatch_without_mutation(tmp_path: Path):
    path = tmp_path / "case.diff"
    path.write_text("changed\n", encoding="utf-8")
    manifest = {
        "source_type": "observed_output",
        "status": "pending_anonymization_review",
        "candidates": [
            {
                "case_id": "observed-safe-case",
                "source_commit": "abc123",
                "source_title": "anonymized observed diff",
                "diff_path": "case.diff",
                "diff_sha256": "0" * 64,
                "anonymized": False,
                "anonymization_status": "pending_review",
            }
        ],
    }

    with pytest.raises(CandidateBuildError, match="hash verification failed"):
        approve_candidate_manifest(manifest, tmp_path, approved_by="reviewer-a")

    assert manifest["status"] == "pending_anonymization_review"
    assert manifest["candidates"][0]["anonymized"] is False


def test_build_private_provenance_audit_keeps_source_locator_out_of_manifest(tmp_path: Path):
    source_diff = tmp_path / "private.diff"
    source_diff.write_text("diff --git a/src/example.py b/src/example.py\n+safe\n", encoding="utf-8")
    spec = _spec()
    spec["candidates"][0].pop("source_repo")
    spec["candidates"][0]["source_diff_path"] = str(source_diff)
    manifest = build_candidate_bundle(spec, tmp_path / "output")

    audit = build_private_provenance_audit(spec, manifest)

    assert audit["event"] == "candidate_bundle_built"
    assert audit["cases"] == [
        {
            "case_id": "observed-safe-case",
            "source_commit": "abc123",
            "source_locator": str(source_diff),
        }
    ]
    assert "source_locator" not in manifest["candidates"][0]


def test_persist_manifest_after_audit_keeps_old_manifest_when_atomic_write_fails(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"status":"pending"}\n', encoding="utf-8")
    audit_path = tmp_path / "audit.jsonl"

    with pytest.raises(OSError, match="write failed"):
        persist_manifest_after_audit(
            manifest_path,
            {"status": "approved"},
            audit_path,
            {"event": "approved"},
            write_manifest=lambda path, payload: (_ for _ in ()).throw(OSError("write failed")),
        )

    assert manifest_path.read_text(encoding="utf-8") == '{"status":"pending"}\n'
    assert audit_path.read_text(encoding="utf-8") == '{"event": "approved"}\n'


def test_persist_manifest_after_audit_skips_manifest_when_audit_append_fails(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"status":"pending"}\n', encoding="utf-8")
    writer_called = False

    def write_manifest(path, payload):
        nonlocal writer_called
        writer_called = True

    with pytest.raises(OSError, match="audit failed"):
        persist_manifest_after_audit(
            manifest_path,
            {"status": "approved"},
            tmp_path / "audit.jsonl",
            {"event": "approved"},
            append_audit=lambda path, record: (_ for _ in ()).throw(OSError("audit failed")),
            write_manifest=write_manifest,
        )

    assert writer_called is False
    assert manifest_path.read_text(encoding="utf-8") == '{"status":"pending"}\n'
