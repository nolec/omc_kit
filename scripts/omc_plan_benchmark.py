#!/usr/bin/env python3
"""Validate frozen plan cases and score normalized implementation plans."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


_PUBLIC_CASE_FIELDS = {
    "case_id",
    "split",
    "source_type",
    "task_type",
    "request",
    "context_sha256",
}
_GOLD_FIELDS = {
    "case_id",
    "required_items",
    "excluded_scope",
    "dependency_edges",
    "allowed_assumptions",
}
_GOLD_ONLY_KEYS = _GOLD_FIELDS - {"case_id"}
_VALID_SPLITS = {"development", "holdout"}
_VALID_SOURCE_TYPES = {"synthetic_anonymized", "observed_anonymized"}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_PUBLIC_DOCUMENT_FIELDS = {"schema_version", "status", "cases", "corpus_sha256"}
_GOLD_DOCUMENT_FIELDS = {
    "schema_version",
    "status",
    "producer",
    "corpus_sha256",
    "cases",
    "gold_sha256",
    "signoff",
}
_SEMANTIC_LABEL_FIELDS = {
    "case_id",
    "gold_case_sha256",
    "requirement_hits",
    "scope_violations",
    "dependency_hits",
    "unexpected_dependency_edges",
    "task_requirement_links",
    "unsupported_assumptions",
}
_SEMANTIC_ENVELOPE_FIELDS = {"labels", "receipt"}
_SEMANTIC_RECEIPT_FIELDS = {
    "adjudicator",
    "plan_producer",
    "adjudication_execution_id",
    "plan_execution_id",
    "plan_sha256",
    "raw_output_sha256",
    "gold_case_sha256",
    "labels_sha256",
    "adjudication_contract_version",
    "adjudication_prompt_sha256",
    "adjudication_output_schema_sha256",
    "index_catalog_sha256",
    "adjudicator_public_key",
    "signature",
}
_SIGNOFF_EVIDENCE_FIELDS = {
    "reviewer",
    "decision",
    "corpus_sha256",
    "gold_sha256",
}
_PLAN_FIELDS = {
    "requirements_covered",
    "scope_items",
    "dependency_edges",
    "tasks",
    "assumptions",
    "decisions_required",
}
_TASK_FIELDS = {"id", "target", "action", "verify", "supports"}


def canonical_digest(value: Any) -> str:
    """Return a stable digest for JSON-compatible data."""
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_crypto() -> tuple[Any, Any, Any, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "plan benchmark signing requires: "
            "python3 -m pip install -r scripts/requirements-plan-benchmark.txt"
        ) from exc
    return InvalidSignature, serialization, Ed25519PrivateKey, Ed25519PublicKey


def _encode_public_key(public_key: Any) -> str:
    _, serialization, _, _ = _load_crypto()
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _decode_public_key(value: Any, label: str) -> Any:
    _, _, _, Ed25519PublicKey = _load_crypto()
    if not isinstance(value, str):
        raise ValueError(f"{label} must be base64")
    try:
        raw = base64.b64decode(value, validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} is invalid") from exc


def _decode_signature(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be base64")
    try:
        signature = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if len(signature) != 64:
        raise ValueError(f"{label} is invalid")
    return signature


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _contains_gold_key(value: Any) -> bool:
    if isinstance(value, dict):
        if _GOLD_ONLY_KEYS.intersection(value):
            return True
        return any(_contains_gold_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_gold_key(item) for item in value)
    return False


def build_fixture_bundle(
    cases: list[dict[str, Any]],
    gold_cases: list[dict[str, Any]],
    *,
    producer: str = "fixture-producer",
) -> dict[str, Any]:
    """Freeze public cases and private gold labels in one integrity envelope."""
    public_cases = deepcopy(cases)
    private_gold = deepcopy(gold_cases)
    return {
        "schema_version": 1,
        "status": "frozen",
        "cases": public_cases,
        "corpus_sha256": canonical_digest(public_cases),
        "gold": {
            "status": "draft",
            "producer": producer,
            "corpus_sha256": canonical_digest(public_cases),
            "cases": private_gold,
            "gold_sha256": canonical_digest(private_gold),
            "signoff": None,
        },
    }


def build_fixture_documents(
    cases: list[dict[str, Any]],
    gold_cases: list[dict[str, Any]],
    *,
    producer: str = "fixture-producer",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build separately distributable public cases and private gold labels."""
    public_cases = deepcopy(cases)
    private_gold = deepcopy(gold_cases)
    corpus_sha256 = canonical_digest(public_cases)
    return (
        {
            "schema_version": 1,
            "status": "frozen",
            "cases": public_cases,
            "corpus_sha256": corpus_sha256,
        },
        {
            "schema_version": 1,
            "status": "draft",
            "producer": producer,
            "corpus_sha256": corpus_sha256,
            "cases": private_gold,
            "gold_sha256": canonical_digest(private_gold),
            "signoff": None,
        },
    )


def gold_signoff_payload(
    gold_document: dict[str, Any],
    *,
    signer: str,
    approved_at: str,
    evidence: dict[str, Any],
    signer_public_key: str,
) -> bytes:
    """Return the canonical claim an independent gold reviewer must sign."""
    gold_document = _require_object(gold_document, "gold document")
    if not isinstance(signer, str) or not signer.strip():
        raise ValueError("gold sign-off signer is required")
    producer = gold_document.get("producer")
    if not isinstance(producer, str) or not producer.strip():
        raise ValueError("gold producer is required")
    if signer.strip() == producer.strip():
        raise ValueError("gold sign-off requires an independent signer")
    _decode_public_key(signer_public_key, "gold signer public key")
    try:
        datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("gold sign-off approved_at must be ISO-8601") from exc
    if not approved_at.endswith("Z"):
        raise ValueError("gold sign-off approved_at must use UTC Z")
    evidence = deepcopy(_require_object(evidence, "gold sign-off evidence"))
    if set(evidence) != _SIGNOFF_EVIDENCE_FIELDS:
        raise ValueError("gold sign-off evidence fields are invalid")
    if evidence["reviewer"] != signer.strip():
        raise ValueError("gold sign-off evidence reviewer mismatch")
    if evidence["decision"] != "approved":
        raise ValueError("gold sign-off evidence must record approval")
    if evidence["corpus_sha256"] != gold_document.get("corpus_sha256"):
        raise ValueError("gold sign-off evidence corpus hash mismatch")
    if evidence["gold_sha256"] != gold_document.get("gold_sha256"):
        raise ValueError("gold sign-off evidence gold hash mismatch")
    return _canonical_bytes({
        "kind": "omc-gold-signoff-v1",
        "producer": producer.strip(),
        "signer": signer.strip(),
        "signer_public_key": signer_public_key,
        "approved_at": approved_at,
        "corpus_sha256": gold_document.get("corpus_sha256"),
        "gold_sha256": gold_document.get("gold_sha256"),
        "evidence": evidence,
    })


def sign_off_gold_document(
    gold_document: dict[str, Any],
    *,
    signer: str,
    approved_at: str,
    evidence: dict[str, Any],
    signer_public_key: str,
    signature: str,
    trusted_signer_public_keys: set[str],
) -> dict[str, Any]:
    """Promote draft gold only after a trusted external signature verifies."""
    signed = deepcopy(_require_object(gold_document, "gold document"))
    if signed.get("status") != "draft":
        raise ValueError("only draft gold can be signed off")
    payload = gold_signoff_payload(
        signed,
        signer=signer,
        approved_at=approved_at,
        evidence=evidence,
        signer_public_key=signer_public_key,
    )
    if (
        not trusted_signer_public_keys
        or signer_public_key not in trusted_signer_public_keys
    ):
        raise ValueError("untrusted gold signer public key")
    public_key = _decode_public_key(signer_public_key, "gold signer public key")
    signature_bytes = _decode_signature(signature, "gold sign-off signature")
    InvalidSignature, _, _, _ = _load_crypto()
    try:
        public_key.verify(signature_bytes, payload)
    except InvalidSignature as exc:
        raise ValueError("gold sign-off signature mismatch") from exc
    signed["status"] = "signed_off"
    signed["signoff"] = {
        "signer": signer.strip(),
        "signer_public_key": signer_public_key,
        "approved_at": approved_at,
        "evidence": evidence,
        "evidence_sha256": canonical_digest(evidence),
        "signature": signature,
    }
    return signed


def _validate_required_fields(
    item: dict[str, Any],
    required_fields: set[str],
    label: str,
) -> None:
    missing = sorted(required_fields - set(item))
    if missing:
        raise ValueError(f"{label} missing fields: {', '.join(missing)}")


def _validate_case(case: Any, index: int) -> dict[str, Any]:
    case = _require_object(case, f"cases[{index}]")
    _validate_required_fields(case, _PUBLIC_CASE_FIELDS, f"cases[{index}]")
    if _contains_gold_key(case):
        raise ValueError(f"public case contains gold data: {case.get('case_id')}")
    extra_fields = sorted(set(case) - _PUBLIC_CASE_FIELDS)
    if extra_fields:
        raise ValueError(
            "unsupported public case fields: " + ", ".join(extra_fields)
        )
    if case["split"] not in _VALID_SPLITS:
        raise ValueError(f"invalid case split: {case['split']}")
    if case["source_type"] not in _VALID_SOURCE_TYPES:
        raise ValueError(f"invalid case source type: {case['source_type']}")
    if not isinstance(case["request"], str) or not case["request"].strip():
        raise ValueError(f"case request is required: {case.get('case_id')}")
    context_hash = case["context_sha256"]
    if (
        not isinstance(context_hash, str)
        or _SHA256_PATTERN.fullmatch(context_hash) is None
    ):
        raise ValueError(f"invalid context hash: {case.get('case_id')}")
    return case


def _validate_gold_case(gold_case: Any, index: int) -> dict[str, Any]:
    gold_case = _require_object(gold_case, f"gold.cases[{index}]")
    _validate_required_fields(gold_case, _GOLD_FIELDS, f"gold.cases[{index}]")
    required_items = _require_list(
        gold_case["required_items"],
        f"gold.cases[{index}].required_items",
    )
    required_ids: set[str] = set()
    for item_index, required_item in enumerate(required_items):
        required_item = _require_object(
            required_item,
            f"gold.cases[{index}].required_items[{item_index}]",
        )
        _validate_required_fields(
            required_item,
            {"id", "weight", "critical"},
            f"gold.cases[{index}].required_items[{item_index}]",
        )
        item_id = required_item["id"]
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("gold required item id is required")
        if item_id in required_ids:
            raise ValueError(f"duplicate gold required item id: {item_id}")
        required_ids.add(item_id)
        weight = required_item["weight"]
        if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
            raise ValueError(f"gold required item weight must be positive: {item_id}")
        if not isinstance(required_item["critical"], bool):
            raise ValueError(f"gold critical flag must be boolean: {item_id}")
    _string_set(gold_case["excluded_scope"], f"gold.cases[{index}].excluded_scope")
    for before, after in _edge_set(
        gold_case["dependency_edges"],
        f"gold.cases[{index}].dependency_edges",
    ):
        if before not in required_ids or after not in required_ids:
            raise ValueError("gold dependency edge must reference a requirement id")
        if before == after:
            raise ValueError("gold dependency edge cannot reference itself")
    _string_set(
        gold_case["allowed_assumptions"],
        f"gold.cases[{index}].allowed_assumptions",
    )
    return gold_case


def _validate_gold_signoff(
    gold: dict[str, Any],
    *,
    require_signed_off: bool,
    trusted_signer_public_keys: set[str] | None,
) -> None:
    status = gold.get("status")
    signoff = gold.get("signoff")
    producer = gold.get("producer")
    if not isinstance(producer, str) or not producer.strip():
        raise ValueError("gold producer is required")
    if status == "draft":
        if signoff is not None:
            raise ValueError("draft gold cannot contain sign-off provenance")
        if require_signed_off:
            raise ValueError("gold labels require independent sign-off")
        return
    if status != "signed_off":
        raise ValueError("gold status must be draft or signed_off")
    signoff = _require_object(signoff, "gold.signoff")
    expected_fields = {
        "signer",
        "signer_public_key",
        "approved_at",
        "evidence",
        "evidence_sha256",
        "signature",
    }
    if set(signoff) != expected_fields:
        raise ValueError("gold sign-off provenance fields are invalid")
    signer = signoff["signer"]
    signer_public_key = signoff["signer_public_key"]
    approved_at = signoff["approved_at"]
    evidence_sha256 = signoff["evidence_sha256"]
    evidence = _require_object(signoff["evidence"], "gold.signoff.evidence")
    if set(evidence) != _SIGNOFF_EVIDENCE_FIELDS:
        raise ValueError("gold sign-off evidence fields are invalid")
    payload = gold_signoff_payload(
        gold,
        signer=signer,
        approved_at=approved_at,
        evidence=evidence,
        signer_public_key=signer_public_key,
    )
    if (
        not isinstance(evidence_sha256, str)
        or _SHA256_PATTERN.fullmatch(evidence_sha256) is None
    ):
        raise ValueError("gold sign-off evidence hash is invalid")
    if evidence_sha256 != canonical_digest(evidence):
        raise ValueError("gold sign-off evidence hash mismatch")
    if (
        not trusted_signer_public_keys
        or signer_public_key not in trusted_signer_public_keys
    ):
        raise ValueError("untrusted gold signer public key")
    public_key = _decode_public_key(signer_public_key, "gold signer public key")
    signature = _decode_signature(signoff["signature"], "gold sign-off signature")
    InvalidSignature, _, _, _ = _load_crypto()
    try:
        public_key.verify(signature, payload)
    except InvalidSignature as exc:
        raise ValueError("gold sign-off signature mismatch") from exc


def validate_fixture_bundle(
    bundle: dict[str, Any],
    *,
    require_signed_off: bool = True,
    trusted_signer_public_keys: set[str] | None = None,
) -> None:
    """Fail closed when the frozen corpus or private labels are inconsistent."""
    bundle = _require_object(bundle, "fixture bundle")
    if bundle.get("schema_version") != 1 or bundle.get("status") != "frozen":
        raise ValueError("fixture bundle must use frozen schema version 1")

    cases = _require_list(bundle.get("cases"), "cases")
    if bundle.get("corpus_sha256") != canonical_digest(cases):
        raise ValueError("corpus hash mismatch")
    validated_cases = [_validate_case(case, index) for index, case in enumerate(cases)]
    case_ids = [case["case_id"] for case in validated_cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate case_id")
    development_count = sum(case["split"] == "development" for case in validated_cases)
    holdout_count = sum(case["split"] == "holdout" for case in validated_cases)
    if (development_count, holdout_count) != (4, 10):
        raise ValueError("fixture bundle must contain 4 development and 10 holdout cases")

    gold = _require_object(bundle.get("gold"), "gold")
    if gold.get("corpus_sha256") != bundle.get("corpus_sha256"):
        raise ValueError("public and gold corpus anchor mismatch")
    _validate_gold_signoff(
        gold,
        require_signed_off=require_signed_off,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )
    gold_cases = _require_list(gold.get("cases"), "gold.cases")
    if gold.get("gold_sha256") != canonical_digest(gold_cases):
        raise ValueError("gold hash mismatch")
    validated_gold = [
        _validate_gold_case(gold_case, index)
        for index, gold_case in enumerate(gold_cases)
    ]
    gold_ids = [gold_case["case_id"] for gold_case in validated_gold]
    if len(gold_ids) != len(set(gold_ids)):
        raise ValueError("duplicate gold case_id")
    if set(case_ids) != set(gold_ids):
        raise ValueError("public case ids and gold case ids must match")


def validate_fixture_documents(
    public_document: dict[str, Any],
    gold_document: dict[str, Any],
    *,
    require_signed_off: bool = True,
    trusted_signer_public_keys: set[str] | None = None,
) -> None:
    """Validate separate documents without weakening the bundle contract."""
    public_document = _require_object(public_document, "public fixture")
    gold_document = _require_object(gold_document, "gold fixture")
    public_extra = sorted(set(public_document) - _PUBLIC_DOCUMENT_FIELDS)
    if public_extra:
        raise ValueError(
            "unsupported public fixture fields: " + ", ".join(public_extra)
        )
    gold_extra = sorted(set(gold_document) - _GOLD_DOCUMENT_FIELDS)
    if gold_extra:
        raise ValueError(
            "unsupported gold fixture fields: " + ", ".join(gold_extra)
        )
    if public_document.get("schema_version") != gold_document.get("schema_version"):
        raise ValueError("public and gold schema version mismatch")
    if public_document.get("schema_version") != 1:
        raise ValueError("fixture documents must use schema version 1")
    if gold_document.get("corpus_sha256") != public_document.get("corpus_sha256"):
        raise ValueError("public and gold corpus anchor mismatch")
    validate_fixture_bundle(
        {
            "schema_version": public_document.get("schema_version"),
            "status": public_document.get("status"),
            "cases": public_document.get("cases"),
            "corpus_sha256": public_document.get("corpus_sha256"),
            "gold": {
                "status": gold_document.get("status"),
                "producer": gold_document.get("producer"),
                "corpus_sha256": gold_document.get("corpus_sha256"),
                "cases": gold_document.get("cases"),
                "gold_sha256": gold_document.get("gold_sha256"),
                "signoff": gold_document.get("signoff"),
            },
        },
        require_signed_off=require_signed_off,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )


def _string_set(value: Any, label: str) -> set[str]:
    items = _require_list(value, label)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise ValueError(f"{label} must contain non-empty strings")
    normalized = set(items)
    if len(normalized) != len(items):
        raise ValueError(f"{label} must not contain duplicates")
    return normalized


def _edge_set(value: Any, label: str) -> set[tuple[str, str]]:
    edges = _require_list(value, label)
    normalized: set[tuple[str, str]] = set()
    for index, edge in enumerate(edges):
        edge = _require_object(edge, f"{label}[{index}]")
        if set(edge) != {"before", "after"}:
            raise ValueError(f"{label}[{index}] fields are invalid")
        before = edge.get("before")
        after = edge.get("after")
        if not all(isinstance(item, str) and item.strip() for item in (before, after)):
            raise ValueError(f"{label}[{index}] requires before and after")
        normalized.add((before, after))
    return normalized


def _semantic_receipt_payload(receipt: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in receipt.items() if key != "signature"}
    return _canonical_bytes(unsigned)


def seal_semantic_adjudication(
    labels: dict[str, Any],
    *,
    plan: dict[str, Any],
    gold: dict[str, Any],
    adjudicator: str,
    plan_producer: str,
    adjudication_execution_id: str,
    plan_execution_id: str,
    private_key: Any,
    raw_output: str = "",
    adjudication_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal labels produced by an independent adjudication execution."""
    _, _, Ed25519PrivateKey, _ = _load_crypto()
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("semantic adjudication private key is required")
    identities = {
        "adjudicator": adjudicator,
        "plan_producer": plan_producer,
        "adjudication_execution_id": adjudication_execution_id,
        "plan_execution_id": plan_execution_id,
    }
    if not all(isinstance(value, str) and value.strip() for value in identities.values()):
        raise ValueError("semantic adjudication identities are required")
    if adjudicator.strip() == plan_producer.strip():
        raise ValueError("semantic adjudication requires an independent adjudicator")
    if adjudication_execution_id.strip() == plan_execution_id.strip():
        raise ValueError("semantic adjudication requires an independent execution")
    if not isinstance(raw_output, str):
        raise ValueError("semantic adjudication raw output must be a string")
    if adjudication_provenance is None:
        adjudication_provenance = {
            "adjudication_contract_version": 1,
            "adjudication_prompt_sha256": canonical_digest("legacy-prompt"),
            "adjudication_output_schema_sha256": canonical_digest("legacy-schema"),
            "index_catalog_sha256": canonical_digest("legacy-catalog"),
        }
    adjudication_provenance = _require_object(
        adjudication_provenance, "adjudication provenance"
    )
    provenance_fields = {
        "adjudication_contract_version",
        "adjudication_prompt_sha256",
        "adjudication_output_schema_sha256",
        "index_catalog_sha256",
    }
    if set(adjudication_provenance) != provenance_fields:
        raise ValueError("adjudication provenance fields are invalid")
    if (
        not isinstance(adjudication_provenance["adjudication_contract_version"], int)
        or adjudication_provenance["adjudication_contract_version"] < 1
        or any(
            not isinstance(adjudication_provenance[field], str)
            or _SHA256_PATTERN.fullmatch(adjudication_provenance[field]) is None
            for field in provenance_fields - {"adjudication_contract_version"}
        )
    ):
        raise ValueError("adjudication provenance is invalid")
    labels = deepcopy(_require_object(labels, "semantic adjudication labels"))
    receipt = {
        "adjudicator": adjudicator.strip(),
        "plan_producer": plan_producer.strip(),
        "adjudication_execution_id": adjudication_execution_id.strip(),
        "plan_execution_id": plan_execution_id.strip(),
        "plan_sha256": canonical_digest(plan),
        "raw_output_sha256": canonical_digest(raw_output),
        "gold_case_sha256": canonical_digest(gold),
        "labels_sha256": canonical_digest(labels),
        **adjudication_provenance,
        "adjudicator_public_key": _encode_public_key(private_key.public_key()),
    }
    receipt["signature"] = base64.b64encode(
        private_key.sign(_semantic_receipt_payload(receipt))
    ).decode("ascii")
    return {"labels": labels, "receipt": receipt}


def _open_semantic_adjudication(
    envelope: Any,
    *,
    plan: dict[str, Any],
    gold: dict[str, Any],
    expected_plan_producer: str,
    expected_plan_execution_id: str,
    trusted_adjudicator_public_keys: set[str],
    raw_output: str,
) -> dict[str, Any]:
    envelope = _require_object(envelope, "sealed semantic adjudication")
    if set(envelope) != _SEMANTIC_ENVELOPE_FIELDS:
        raise ValueError("sealed semantic adjudication fields are invalid")
    labels = _require_object(envelope["labels"], "semantic adjudication labels")
    receipt = _require_object(envelope["receipt"], "semantic adjudication receipt")
    if set(receipt) != _SEMANTIC_RECEIPT_FIELDS:
        raise ValueError("semantic adjudication receipt fields are invalid")
    identity_fields = (
        "adjudicator",
        "plan_producer",
        "adjudication_execution_id",
        "plan_execution_id",
    )
    if not all(
        isinstance(receipt.get(field), str) and receipt[field].strip()
        for field in identity_fields
    ):
        raise ValueError("semantic adjudication receipt identities are invalid")
    if not all(
        isinstance(receipt.get(field), str)
        and _SHA256_PATTERN.fullmatch(receipt[field]) is not None
        for field in (
            "plan_sha256",
            "raw_output_sha256",
            "gold_case_sha256",
            "labels_sha256",
            "adjudication_prompt_sha256",
            "adjudication_output_schema_sha256",
            "index_catalog_sha256",
        )
    ):
        raise ValueError("semantic adjudication receipt hashes are invalid")
    if (
        not isinstance(receipt["adjudication_contract_version"], int)
        or receipt["adjudication_contract_version"] < 1
    ):
        raise ValueError("semantic adjudication contract version is invalid")
    adjudicator_public_key = receipt["adjudicator_public_key"]
    public_key = _decode_public_key(
        adjudicator_public_key,
        "semantic adjudicator public key",
    )
    signature = _decode_signature(
        receipt["signature"],
        "semantic adjudication signature",
    )
    if (
        not trusted_adjudicator_public_keys
        or adjudicator_public_key not in trusted_adjudicator_public_keys
    ):
        raise ValueError("untrusted adjudicator public key")
    if receipt["plan_producer"] != expected_plan_producer:
        raise ValueError("semantic adjudication plan producer mismatch")
    if receipt["plan_execution_id"] != expected_plan_execution_id:
        raise ValueError("semantic adjudication plan execution mismatch")
    if receipt["adjudicator"] == receipt["plan_producer"]:
        raise ValueError("semantic adjudication requires an independent adjudicator")
    if receipt["adjudication_execution_id"] == receipt["plan_execution_id"]:
        raise ValueError("semantic adjudication requires an independent execution")
    if receipt["plan_sha256"] != canonical_digest(plan):
        raise ValueError("semantic adjudication plan hash mismatch")
    if receipt["raw_output_sha256"] != canonical_digest(raw_output):
        raise ValueError("semantic adjudication raw output hash mismatch")
    if receipt["gold_case_sha256"] != canonical_digest(gold):
        raise ValueError("semantic adjudication gold hash mismatch")
    if receipt["labels_sha256"] != canonical_digest(labels):
        raise ValueError("semantic adjudication labels hash mismatch")
    InvalidSignature, _, _, _ = _load_crypto()
    try:
        public_key.verify(signature, _semantic_receipt_payload(receipt))
    except InvalidSignature as exc:
        raise ValueError("semantic adjudication signature mismatch") from exc
    return labels


def _validate_semantic_labels(
    labels: Any,
    *,
    plan: dict[str, Any],
    gold: dict[str, Any],
    task_ids: set[str],
) -> dict[str, Any]:
    if labels is None:
        raise ValueError("semantic adjudication is required")
    labels = _require_object(labels, "semantic adjudication")
    if set(labels) != _SEMANTIC_LABEL_FIELDS:
        raise ValueError("semantic adjudication fields are invalid")
    if labels["case_id"] != gold["case_id"]:
        raise ValueError("semantic adjudication case mismatch")
    if labels["gold_case_sha256"] != canonical_digest(gold):
        raise ValueError("semantic adjudication gold hash mismatch")

    required_ids = {item["id"] for item in gold["required_items"]}
    requirement_hits = _string_set(
        labels["requirement_hits"],
        "semantic.requirement_hits",
    )
    if not requirement_hits.issubset(required_ids):
        raise ValueError("semantic requirement hit references unknown gold id")

    scope_violations = _string_set(
        labels["scope_violations"],
        "semantic.scope_violations",
    )
    if not scope_violations.issubset(set(gold["excluded_scope"])):
        raise ValueError("semantic scope violation is not in gold exclusions")

    dependency_hits = _edge_set(
        labels["dependency_hits"],
        "semantic.dependency_hits",
    )
    expected_edges = _edge_set(gold["dependency_edges"], "gold.dependency_edges")
    if not dependency_hits.issubset(expected_edges):
        raise ValueError("semantic dependency hit is not in gold edges")
    unexpected_edges = _edge_set(
        labels["unexpected_dependency_edges"],
        "semantic.unexpected_dependency_edges",
    )
    if unexpected_edges.intersection(expected_edges):
        raise ValueError("semantic unexpected dependency overlaps gold edge")

    task_links = _require_list(
        labels["task_requirement_links"],
        "semantic.task_requirement_links",
    )
    linked_task_ids: set[str] = set()
    normalized_task_links: dict[str, set[str]] = {}
    for index, link in enumerate(task_links):
        link = _require_object(link, f"semantic.task_requirement_links[{index}]")
        if set(link) != {"task_id", "requirement_ids"}:
            raise ValueError("semantic task link fields are invalid")
        task_id = link["task_id"]
        if task_id not in task_ids:
            raise ValueError("semantic task link references unknown task")
        if task_id in linked_task_ids:
            raise ValueError("semantic task link duplicates task")
        linked_task_ids.add(task_id)
        link_requirements = _string_set(
            link["requirement_ids"],
            f"semantic.task_requirement_links[{index}].requirement_ids",
        )
        if not link_requirements.issubset(required_ids):
            raise ValueError("semantic task link references unknown gold id")
        normalized_task_links[task_id] = link_requirements

    assumptions = _string_set(plan["assumptions"], "assumptions")
    unsupported_assumptions = _string_set(
        labels["unsupported_assumptions"],
        "semantic.unsupported_assumptions",
    )
    if not unsupported_assumptions.issubset(assumptions):
        raise ValueError("semantic unsupported assumption is not in plan")

    return {
        "requirement_hits": requirement_hits,
        "scope_violations": scope_violations,
        "dependency_hits": dependency_hits,
        "unexpected_dependency_edges": unexpected_edges,
        "task_requirement_links": normalized_task_links,
        "unsupported_assumptions": unsupported_assumptions,
    }


def score_plan(
    plan: dict[str, Any],
    gold: dict[str, Any],
    semantic_labels: dict[str, Any] | None,
    *,
    trusted_adjudicator_public_keys: set[str],
    expected_plan_producer: str,
    expected_plan_execution_id: str,
    raw_output: str = "",
) -> dict[str, Any]:
    """Score a normalized plan against one private gold contract."""
    plan = _require_object(plan, "plan")
    _validate_required_fields(plan, _PLAN_FIELDS, "plan")
    if set(plan) != _PLAN_FIELDS:
        raise ValueError("plan fields are invalid")
    gold = _validate_gold_case(gold, 0)
    required_items = gold["required_items"]
    required_ids = {item["id"] for item in required_items}

    _string_set(plan.get("requirements_covered"), "requirements_covered")
    _string_set(plan.get("scope_items"), "scope_items")
    _edge_set(plan.get("dependency_edges"), "dependency_edges")
    _string_set(plan.get("assumptions"), "assumptions")
    decisions_required = _string_set(
        plan.get("decisions_required"),
        "decisions_required",
    )
    tasks = _require_list(plan.get("tasks"), "tasks")
    task_ids: set[str] = set()
    executable_steps = 0
    for index, task in enumerate(tasks):
        task = _require_object(task, f"tasks[{index}]")
        _validate_required_fields(task, _TASK_FIELDS, f"tasks[{index}]")
        if set(task) != _TASK_FIELDS:
            raise ValueError(f"tasks[{index}] fields are invalid")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"tasks[{index}].id is required")
        if task_id in task_ids:
            raise ValueError(f"duplicate task id: {task_id}")
        task_ids.add(task_id)
        _string_set(
            task.get("supports"),
            f"tasks[{index}].supports",
        )
        execution_fields = ("target", "action", "verify")
        for field in execution_fields:
            if not isinstance(task[field], str):
                raise ValueError(f"tasks[{index}].{field} must be a string")
        if all(task[field].strip() for field in execution_fields):
            executable_steps += 1
    labels = _open_semantic_adjudication(
        semantic_labels,
        plan=plan,
        gold=gold,
        expected_plan_producer=expected_plan_producer,
        expected_plan_execution_id=expected_plan_execution_id,
        trusted_adjudicator_public_keys=trusted_adjudicator_public_keys,
        raw_output=raw_output,
    )
    semantic = _validate_semantic_labels(
        labels,
        plan=plan,
        gold=gold,
        task_ids=task_ids,
    )
    covered_ids = semantic["requirement_hits"]

    total_weight = sum(item["weight"] for item in required_items)
    covered_weight = sum(
        item["weight"] for item in required_items if item["id"] in covered_ids
    )
    critical_omissions = [
        item["id"]
        for item in required_items
        if item["critical"] and item["id"] not in covered_ids
    ]

    expected_edges = _edge_set(gold["dependency_edges"], "gold.dependency_edges")
    dependency_hits = semantic["dependency_hits"]
    unexpected_edges = semantic["unexpected_dependency_edges"]
    dependency_denominator = len(expected_edges) + len(unexpected_edges)
    dependency_accuracy = (
        len(dependency_hits) / dependency_denominator
        if expected_edges and dependency_denominator
        else None
    )
    scope_violations = (
        sorted(semantic["scope_violations"])
        if gold["excluded_scope"]
        else None
    )

    task_links = semantic["task_requirement_links"]
    bloat_steps = sum(
        not task_links.get(task_id)
        for task_id in task_ids
    )

    return {
        "case_id": gold["case_id"],
        "weighted_coverage": covered_weight / total_weight if total_weight else 1.0,
        "critical_omissions": critical_omissions,
        "scope_violations": scope_violations,
        "dependency_accuracy": dependency_accuracy,
        "unexpected_dependency_edges": [
            {"before": before, "after": after}
            for before, after in sorted(unexpected_edges)
        ],
        "executable_step_rate": executable_steps / len(tasks) if tasks else 1.0,
        "unsupported_assumptions": sorted(semantic["unsupported_assumptions"]),
        "decision_proxy": len(decisions_required),
        "bloat_ratio": bloat_steps / len(tasks) if tasks else 0.0,
        "output_size_chars": len(raw_output),
    }


def score_plan_batch(
    public_document: dict[str, Any],
    gold_document: dict[str, Any],
    result_document: dict[str, Any],
    *,
    trusted_gold_signer_public_keys: set[str],
    trusted_adjudicator_public_keys: set[str],
    allow_draft_gold: bool = False,
) -> dict[str, Any]:
    """Score complete provider result batches for one frozen corpus split."""
    validate_fixture_documents(
        public_document,
        gold_document,
        require_signed_off=not allow_draft_gold,
        trusted_signer_public_keys=trusted_gold_signer_public_keys,
    )
    result_document = _require_object(result_document, "result document")
    if set(result_document) != {"schema_version", "split", "providers"}:
        raise ValueError("result document fields are invalid")
    if result_document["schema_version"] != 1:
        raise ValueError("result document must use schema version 1")
    split = result_document["split"]
    if split not in _VALID_SPLITS:
        raise ValueError("result document split is invalid")

    selected_cases = [
        case for case in public_document["cases"] if case["split"] == split
    ]
    selected_case_ids = {case["case_id"] for case in selected_cases}
    gold_by_id = {case["case_id"]: case for case in gold_document["cases"]}
    providers = _require_list(result_document["providers"], "providers")
    if not providers:
        raise ValueError("result document requires at least one provider")

    provider_ids: set[str] = set()
    provider_reports: list[dict[str, Any]] = []
    for provider_index, provider in enumerate(providers):
        provider = _require_object(provider, f"providers[{provider_index}]")
        if set(provider) != {"provider_id", "plan_producer", "executions"}:
            raise ValueError(f"providers[{provider_index}] fields are invalid")
        provider_id = provider["provider_id"]
        plan_producer = provider["plan_producer"]
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError(f"providers[{provider_index}].provider_id is required")
        if provider_id in provider_ids:
            raise ValueError(f"duplicate provider_id: {provider_id}")
        provider_ids.add(provider_id)
        if not isinstance(plan_producer, str) or not plan_producer.strip():
            raise ValueError(f"providers[{provider_index}].plan_producer is required")

        executions = _require_list(
            provider["executions"],
            f"providers[{provider_index}].executions",
        )
        execution_by_case: dict[str, dict[str, Any]] = {}
        for execution_index, execution in enumerate(executions):
            label = f"providers[{provider_index}].executions[{execution_index}]"
            execution = _require_object(execution, label)
            expected_fields = {
                "case_id",
                "plan_execution_id",
                "plan",
                "raw_output",
                "semantic_adjudication",
            }
            if set(execution) != expected_fields:
                raise ValueError(f"{label} fields are invalid")
            case_id = execution["case_id"]
            if case_id not in selected_case_ids:
                raise ValueError(f"{label} references a case outside {split}")
            if case_id in execution_by_case:
                raise ValueError(f"duplicate execution case_id: {case_id}")
            execution_id = execution["plan_execution_id"]
            if not isinstance(execution_id, str) or not execution_id.strip():
                raise ValueError(f"{label}.plan_execution_id is required")
            if not isinstance(execution["raw_output"], str):
                raise ValueError(f"{label}.raw_output must be a string")
            execution_by_case[case_id] = execution

        if set(execution_by_case) != selected_case_ids:
            raise ValueError(f"provider {provider_id} must cover every {split} case")

        case_scores = []
        for case in selected_cases:
            execution = execution_by_case[case["case_id"]]
            case_scores.append(score_plan(
                execution["plan"],
                gold_by_id[case["case_id"]],
                execution["semantic_adjudication"],
                trusted_adjudicator_public_keys=trusted_adjudicator_public_keys,
                expected_plan_producer=plan_producer,
                expected_plan_execution_id=execution["plan_execution_id"],
                raw_output=execution["raw_output"],
            ))

        def metric_mean(field: str) -> float | None:
            measured = [
                score[field] for score in case_scores if score[field] is not None
            ]
            return sum(measured) / len(measured) if measured else None

        scope_measurements = [
            score["scope_violations"]
            for score in case_scores
            if score["scope_violations"] is not None
        ]

        provider_reports.append({
            "provider_id": provider_id,
            "plan_producer": plan_producer,
            "summary": {
                "weighted_coverage_mean": metric_mean("weighted_coverage"),
                "critical_omission_count": sum(
                    len(score["critical_omissions"]) for score in case_scores
                ),
                "scope_violation_count": (
                    sum(len(violations) for violations in scope_measurements)
                    if scope_measurements
                    else None
                ),
                "dependency_accuracy_mean": metric_mean("dependency_accuracy"),
                "executable_step_rate_mean": metric_mean("executable_step_rate"),
                "unsupported_assumption_count": sum(
                    len(score["unsupported_assumptions"]) for score in case_scores
                ),
                "decision_proxy_mean": metric_mean("decision_proxy"),
                "bloat_ratio_mean": metric_mean("bloat_ratio"),
                "output_size_chars_total": sum(
                    score["output_size_chars"] for score in case_scores
                ),
            },
            "case_scores": case_scores,
        })

    return {
        "schema_version": 1,
        "split": split,
        "gold_status": gold_document["status"],
        "evaluation_status": (
            "verified_signed_off"
            if gold_document["status"] == "signed_off"
            else "draft_not_for_comparison"
        ),
        "corpus_sha256": public_document["corpus_sha256"],
        "gold_sha256": gold_document["gold_sha256"],
        "result_sha256": canonical_digest(result_document),
        "case_count": len(selected_cases),
        "providers": provider_reports,
    }


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _require_object(payload, str(path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", help="Frozen public benchmark cases JSON")
    parser.add_argument("gold", help="Private signed-off gold labels JSON")
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Validate structure without requiring independent sign-off",
    )
    parser.add_argument(
        "--trusted-signer-public-key",
        action="append",
        default=[],
        help="Trusted base64 Ed25519 gold signer public key (repeatable)",
    )
    parser.add_argument(
        "--results",
        help="Provider plan result batch JSON to score",
    )
    parser.add_argument(
        "--output",
        help="Write the scored batch report to this JSON path",
    )
    parser.add_argument(
        "--trusted-adjudicator-public-key",
        action="append",
        default=[],
        help="Trusted base64 Ed25519 adjudicator public key (repeatable)",
    )
    args = parser.parse_args()
    public_document = _load(args.cases)
    gold_document = _load(args.gold)
    if args.results:
        report = score_plan_batch(
            public_document,
            gold_document,
            _load(args.results),
            trusted_gold_signer_public_keys=set(
                args.trusted_signer_public_key
            ),
            trusted_adjudicator_public_keys=set(
                args.trusted_adjudicator_public_key
            ),
            allow_draft_gold=args.allow_draft,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
            print(f"plan benchmark report written: {args.output}")
        else:
            print(rendered)
        return 0
    if args.output:
        parser.error("--output requires --results")
    if args.trusted_adjudicator_public_key:
        parser.error("--trusted-adjudicator-public-key requires --results")
    validate_fixture_documents(
        public_document,
        gold_document,
        require_signed_off=not args.allow_draft,
        trusted_signer_public_keys=set(args.trusted_signer_public_key),
    )
    print("plan benchmark fixtures: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
