from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from omc_plan_benchmark import build_fixture_bundle, gold_signoff_payload
from omc_plan_gold_signoff import apply_gold_signoff, prepare_gold_signoff


def _gold_document():
    bundle = build_fixture_bundle(
        [{
            "case_id": "plan-dev-case",
            "split": "development",
            "source_type": "synthetic_anonymized",
            "task_type": "bugfix",
            "request": "Fix the development case",
            "context_sha256": "a" * 64,
        }],
        [{
            "case_id": "plan-dev-case",
            "required_items": [{"id": "REQ-1", "weight": 1, "critical": True}],
            "excluded_scope": [],
            "dependency_edges": [],
            "allowed_assumptions": [],
        }],
    )
    return bundle["gold"]


def _public_key(private_key):
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def test_prepare_and_apply_gold_signoff_without_private_key_access():
    gold = _gold_document()
    private_key = Ed25519PrivateKey.generate()
    public_key = _public_key(private_key)
    evidence = {
        "reviewer": "independent-human-reviewer",
        "decision": "approved",
        "corpus_sha256": gold["corpus_sha256"],
        "gold_sha256": gold["gold_sha256"],
    }

    prepared = prepare_gold_signoff(
        gold,
        signer=evidence["reviewer"],
        approved_at="2026-08-02T09:00:00Z",
        evidence=evidence,
        signer_public_key=public_key,
    )
    expected_payload = gold_signoff_payload(
        gold,
        signer=evidence["reviewer"],
        approved_at="2026-08-02T09:00:00Z",
        evidence=evidence,
        signer_public_key=public_key,
    )
    assert base64.b64decode(prepared["payload_base64"]) == expected_payload

    signed = apply_gold_signoff(
        gold,
        prepared=prepared,
        signature=base64.b64encode(private_key.sign(expected_payload)).decode("ascii"),
        trusted_signer_public_keys={public_key},
    )

    assert signed["status"] == "signed_off"
    assert signed["signoff"]["evidence"] == evidence


def test_apply_gold_signoff_rejects_stale_prepared_payload():
    gold = _gold_document()
    private_key = Ed25519PrivateKey.generate()
    public_key = _public_key(private_key)
    evidence = {
        "reviewer": "independent-human-reviewer",
        "decision": "approved",
        "corpus_sha256": gold["corpus_sha256"],
        "gold_sha256": gold["gold_sha256"],
    }
    prepared = prepare_gold_signoff(
        gold,
        signer=evidence["reviewer"],
        approved_at="2026-08-02T09:00:00Z",
        evidence=evidence,
        signer_public_key=public_key,
    )
    prepared["gold_sha256"] = "f" * 64

    with pytest.raises(ValueError, match="prepared gold hash mismatch"):
        apply_gold_signoff(
            gold,
            prepared=prepared,
            signature=base64.b64encode(private_key.sign(b"stale")).decode("ascii"),
            trusted_signer_public_keys={public_key},
        )
