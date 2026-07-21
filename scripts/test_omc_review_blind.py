import hashlib
import json

import pytest

from omc_review_blind import build_blind_pack


def _manifest(case_id, filename, digest="digest"):
    return {
        "source_type": "observed_output",
        "candidates": [
            {
                "case_id": case_id,
                "diff_path": filename,
                "diff_sha256": digest,
                "source_commit": "abc123",
                "provider_status": {"codex": "completed", "omc-review": "completed"},
            }
        ],
    }


def test_build_blind_pack_contains_only_provider_neutral_case_fields(tmp_path):
    diff = "diff --git a/app.py b/app.py\n+return False\n"
    path = tmp_path / "case.diff"
    path.write_text(diff, encoding="utf-8")
    digest = hashlib.sha256(diff.encode()).hexdigest()

    pack = build_blind_pack(_manifest("case-1", "case.diff", digest), tmp_path, ["case-1"])

    assert pack["status"] == "ready_for_independent_adjudication"
    assert pack["case_count"] == 1
    assert set(pack) == {"status", "source_type", "case_count", "cases"}
    assert set(pack["cases"][0]) == {"case_id", "source_commit", "diff_sha256", "diff"}
    assert "provider_status" not in json.dumps(pack)


def test_build_blind_pack_rejects_hash_mismatch(tmp_path):
    path = tmp_path / "case.diff"
    path.write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sha256"):
        build_blind_pack(_manifest("case-1", "case.diff", "wrong"), tmp_path, ["case-1"])


def test_build_blind_pack_rejects_duplicate_or_unknown_requested_ids(tmp_path):
    diff = "changed\n"
    path = tmp_path / "case.diff"
    path.write_text(diff, encoding="utf-8")
    digest = hashlib.sha256(diff.encode()).hexdigest()
    manifest = _manifest("case-1", "case.diff", digest)

    with pytest.raises(ValueError, match="duplicate"):
        build_blind_pack(manifest, tmp_path, ["case-1", "case-1"])
    with pytest.raises(ValueError, match="not found"):
        build_blind_pack(manifest, tmp_path, ["case-2"])


def test_build_blind_pack_rejects_provider_result_fields_in_generated_metadata(tmp_path):
    diff = "changed\n"
    path = tmp_path / "case.diff"
    path.write_text(diff, encoding="utf-8")
    digest = hashlib.sha256(diff.encode()).hexdigest()
    manifest = _manifest("case-1", "case.diff", digest)
    manifest["candidates"][0]["findings"] = []

    with pytest.raises(ValueError, match="provider result"):
        build_blind_pack(manifest, tmp_path, ["case-1"])
