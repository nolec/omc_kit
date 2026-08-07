import json
import base64
import math
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_plan_runtime_pilot as runtime
from omc_plan_pilot import restore_blind_session_plan_labels


def _protocol():
    return {
        "schema_version": 1,
        "benchmark_scope": "repository_grounded_skill_runtime",
        "providers": ["baseline-plan", "omc-plan"],
        "execution": {
            "sandbox": "read-only",
            "ignore_user_config": True,
            "ephemeral": True,
            "require_same_model_config": True,
            "allowed_workspace_delta": ".agents/skills/omc-plan/SKILL.md",
            "timeout_sec": 180,
        },
        "activation": {
            "required_for_omc": True,
            "forbidden_for_baseline": True,
            "proof_method": "output_nonce",
            "output_field": "runtime_activation_receipt",
            "baseline_sentinel": "unavailable",
            "max_attempts": 2,
        },
        "variability": {
            "development_case_count": 4,
            "runs_per_provider": 2,
            "max_metric_delta": 0.10,
        },
        "confirmatory": {
            "manifest_required": True,
            "claim_scope": "single_confirmatory_corpus",
            "observed_total_token_stop_threshold": 1_200_000,
            "maximum_external_calls": 30,
        },
        "acceptance": {
            "case_count": 10,
            "minimum_executable_task_rate": 0.80,
            "maximum_output_token_ratio": 1.25,
            "maximum_total_token_increase_ratio": 0.05,
            "minimum_quality_gain_for_token_increase": 0.05,
        },
        "superiority": {
            "primary_metric": "weighted_requirement_recall",
            "minimum_primary_gain": 0.05,
            "confidence_level": 0.95,
            "bootstrap_iterations": 10000,
            "bootstrap_seed": 20260803,
            "required_confirmation_batches": 2,
        },
    }


def _case(index=1):
    case = {
        "case_id": f"observed-{index:02d}",
        "split": "holdout",
        "source_type": "observed_anonymized",
        "request": "Add bounded retry handling without changing the public API.",
        "provenance": {
            "source_sha256": runtime._sha256_text(f"source-{index}"),
            "anonymization_reviewed": True,
            "approved": True,
        },
        "context_files": {
            "src/service.py": f"CASE_ID = {index}\n\ndef run():\n    return None\n",
            "tests/test_service.py": "def test_run():\n    assert True\n",
        },
    }
    case["context_sha256"] = runtime.canonical_digest(case["context_files"])
    return case


def _gold(index=1):
    return {
        "case_id": f"observed-{index:02d}",
        "required_items": [{
            "id": "REQ-1",
            "description": "Required behavior",
            "critical": True,
            "weight": 1,
        }],
        "excluded_scope": [],
        "allowed_assumptions": [],
        "dependency_edges": [],
    }


@pytest.mark.parametrize("weight", [math.nan, math.inf, -math.inf])
def test_runtime_gold_rejects_non_finite_requirement_weight(weight):
    gold = _gold()
    gold["required_items"][0]["weight"] = weight

    with pytest.raises(ValueError, match="weight must be positive"):
        runtime._validate_runtime_gold_case(gold)


def test_runtime_gold_accepts_arbitrary_precision_positive_integer_weight():
    gold = _gold()
    gold["required_items"][0]["weight"] = 10**1000

    runtime._validate_runtime_gold_case(gold)


def _reviewed_gold_output(author_output_sha256):
    return {
        "cases": [_gold(index) for index in range(1, 11)],
        "decision": "approve",
        "reviewed_author_output_sha256": author_output_sha256,
    }


def _signed_gold(cases, gold_items):
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).decode("ascii")
    document = {
        "schema_version": 1,
        "status": "signed_off",
        "producer": "fixture-author",
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": runtime.canonical_digest(gold_items),
        "cases": gold_items,
        "signoff": {
            "signer": "independent-reviewer",
            "signer_public_key": public_key,
        },
    }
    document["signoff"]["signature"] = base64.b64encode(
        private_key.sign(runtime.gold_signoff_payload(document))
    ).decode("ascii")
    return document, {public_key}


def _signer():
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).decode("ascii")
    return private_key, public_key


def _signed_confirmatory_manifest(
    cases, gold, *, prior_cases=None, skill_sha256="a" * 64
):
    private_key, public_key = _signer()
    prior_cases = [_case(99)] if prior_cases is None else prior_cases
    prior_fingerprints = [
        {
            "case_id": case["case_id"],
            "source_sha256": case["provenance"]["source_sha256"],
            "context_sha256": case["context_sha256"],
        }
        for case in prior_cases
    ]
    surfaces = (
        "ui_state",
        "api_payload",
        "data_indexing",
        "backend_rules",
        "multi_file_legacy",
    )
    ambiguities = (
        "low", "low", "low",
        "medium", "medium", "medium", "medium",
        "high", "high", "high",
    )
    manifest = {
        "schema_version": 4,
        "status": "signed_off",
        "producer": "fixture-curator",
        "source_corpus_sha256": gold["corpus_sha256"],
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": gold["gold_sha256"],
        "runtime_runner_sha256": runtime._runtime_runner_sha256(),
        "sampling": {
            "source_window": "2026-08-01/2026-08-03",
            "eligibility_rule": "observed requests not used by prior evaluations",
            "ordering_rule": "source timestamp ascending, then source hash",
            "sampling_frame_sha256": runtime._sha256_text("sampling-frame"),
            "semantic_contract": {
                "required_surface_counts": {
                    surface: 2 for surface in surfaces
                },
                "required_ambiguity_counts": {
                    "low": 3,
                    "medium": 4,
                    "high": 3,
                },
                "maximum_selected_object_cases": 2,
                "case_labels": [
                    {
                        "case_id": case["case_id"],
                        "surface": surfaces[index % len(surfaces)],
                        "ambiguity": ambiguities[index],
                        "selected_object": index < 2,
                    }
                    for index, case in enumerate(cases)
                ],
            },
        },
        "prior_registry_sha256": runtime.canonical_digest(prior_fingerprints),
        "prior_fingerprints": prior_fingerprints,
        "selected_fingerprints": [
            {
                "case_id": case["case_id"],
                "source_sha256": case["provenance"]["source_sha256"],
                "context_sha256": case["context_sha256"],
            }
            for case in cases
        ],
        "gold_independence": {
            "author_session_id": "gold-author-session",
            "reviewer_session_id": "gold-reviewer-session",
            "provider_outputs_available": False,
        },
        "budget": {
            "observed_total_token_stop_threshold": 1_200_000,
            "maximum_external_calls": 30,
        },
        "transmission": {
            "payload_sha256": runtime.confirmatory_external_payload_digest(
                cases, gold, skill_sha256
            ),
            "approved": True,
        },
        "claim_scope": "single_confirmatory_corpus",
        "signoff": {
            "signer": "independent-confirmatory-reviewer",
            "signer_public_key": public_key,
        },
    }
    manifest["signoff"]["signature"] = base64.b64encode(
        private_key.sign(runtime.confirmatory_manifest_signoff_payload(manifest))
    ).decode("ascii")
    return manifest, public_key


def _local_transfer_readiness(cases):
    bundle_cases = []
    manifest_cases = []
    for case in cases:
        files = [
            {"relative_path": path, "content_utf8": content}
            for path, content in case["context_files"].items()
        ]
        bundle_cases.append({
            "case_id": case["case_id"],
            "request": case["request"],
            "files": files,
        })
        manifest_cases.append({
            "case_id": case["case_id"],
            "request_sha256": runtime._sha256_text(case["request"]),
            "source_byte_size": sum(
                len(content.encode("utf-8"))
                for content in case["context_files"].values()
            ),
            "transfer_byte_size": sum(
                len(content.encode("utf-8"))
                for content in case["context_files"].values()
            ) + len(case["request"].encode("utf-8")),
            "estimated_input_tokens": 10,
            "files": [
                {
                    "relative_path": path,
                    "source_blob_oid": runtime._sha256_text(path)[:40],
                    "blob_sha256": runtime._sha256_text(content),
                    "byte_size": len(content.encode("utf-8")),
                    "transfer_disposition": "included_text",
                    "privacy_classification": "pattern_clear",
                    "privacy_checks": ["regular_file", "utf8"],
                }
                for path, content in case["context_files"].items()
            ],
        })
    bundle = {
        "schema_version": 1,
        "status": "local_only",
        "batch_id": "fresh-batch-a",
        "selection_sha256": runtime._sha256_text("selection"),
        "cases": bundle_cases,
    }
    bundle["bundle_sha256"] = runtime.canonical_digest(bundle)
    manifest = {
        "schema_version": 1,
        "status": "frozen",
        "batch_id": "fresh-batch-a",
        "selection_sha256": bundle["selection_sha256"],
        "packet_sha256": runtime._sha256_text("packet"),
        "transfer_bundle_sha256": bundle["bundle_sha256"],
        "case_count": len(cases),
        "source_byte_size": 1,
        "transfer_byte_size": 1,
        "transferred_file_count": sum(
            len(case["context_files"]) for case in cases
        ),
        "omitted_file_count": 0,
        "omitted_binary_count": 0,
        "omitted_sensitive_count": 0,
        "cases": manifest_cases,
    }
    manifest["manifest_sha256"] = runtime.canonical_digest(manifest)
    audit = {
        "schema_version": 1,
        "status": "pattern_clear",
        "scanner_version": 1,
        "transfer_manifest_sha256": manifest["manifest_sha256"],
        "finding_count": 0,
        "omitted_binary_count": 0,
        "omitted_sensitive_count": 0,
        "findings": [],
    }
    audit["audit_sha256"] = runtime.canonical_digest(audit)
    readiness = {
        "schema_version": 1,
        "status": "approval_required",
        "transfer_bundle": bundle,
        "transfer_manifest": manifest,
        "privacy_audit": audit,
        "execution_contract": {"status": "frozen"},
        "external_transfer_approved": False,
        "provider_execution_allowed": False,
        "replacement_claim_eligible": False,
        "preregistration_manifest_sha256": runtime._sha256_text("prereg"),
    }
    readiness["readiness_sha256"] = runtime.canonical_digest(readiness)
    return readiness


def _confirmatory_selection(cases):
    surfaces = (
        "ui_state", "ui_state", "api_payload", "api_payload",
        "data_indexing", "data_indexing", "backend_rules", "backend_rules",
        "multi_file_legacy", "multi_file_legacy",
    )
    ambiguities = (
        "low", "low", "low", "medium", "medium",
        "medium", "medium", "high", "high", "high",
    )
    return {
        "selection_policy": {
            "required_surface_counts": {
                surface: 2 for surface in set(surfaces)
            },
            "required_ambiguity_counts": {"low": 3, "medium": 4, "high": 3},
            "maximum_selected_object_cases": 2,
        },
        "cases": [
            {
                "case_id": case["case_id"],
                "surface": surfaces[index],
                "ambiguity": ambiguities[index],
                "selected_object": index < 2,
            }
            for index, case in enumerate(cases)
        ],
    }


def _resign_confirmatory_manifest(manifest):
    private_key, public_key = _signer()
    manifest["signoff"]["signer_public_key"] = public_key
    manifest["signoff"]["signature"] = base64.b64encode(
        private_key.sign(runtime.confirmatory_manifest_signoff_payload(manifest))
    ).decode("ascii")
    return public_key


def _adjudications(sessions):
    from omc_plan_pilot import build_adjudication_provenance

    return [
        {
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh:{session['session_id']}",
            "items": [
                {
                    "item_index": index,
                    "requirement_hit_indexes": [0],
                    "scope_violation_indexes": [],
                    "task_requirement_links": [
                        {"task_index": 0, "requirement_indexes": [0]}
                    ],
                    "edge_requirement_links": [],
                    "unsupported_assumption_indexes": [],
                }
                for index, _ in enumerate(session["items"])
            ],
            "_adjudication_provenance": build_adjudication_provenance(session),
        }
        for session in sessions
    ]


def _metrics():
    return {
        "evaluation_scope": "confirmatory",
        "valid_case_count": 10,
        "provenance_complete_count": 10,
        "token_measurement_status": "observed",
        "baseline-plan": {
            "weighted_requirement_recall": 0.90,
            "critical_omission_count": 0,
            "executable_task_rate": 0.85,
            "unsupported_assumption_count": 1,
            "task_evidence_accuracy": 0.90,
            "output_tokens": 1000,
            "total_tokens": 10000,
        },
        "omc-plan": {
            "weighted_requirement_recall": 0.95,
            "critical_omission_count": 0,
            "executable_task_rate": 0.90,
            "unsupported_assumption_count": 1,
            "task_evidence_accuracy": 0.92,
            "output_tokens": 1200,
            "total_tokens": 10400,
        },
    }


def test_protocol_rejects_unfrozen_thresholds():
    protocol = _protocol()
    del protocol["acceptance"]["maximum_output_token_ratio"]
    with pytest.raises(ValueError, match="acceptance"):
        runtime.validate_runtime_protocol(protocol)

    protocol = _protocol()
    protocol["acceptance"]["maximum_output_token_ratio"] = 1.20
    with pytest.raises(ValueError, match="frozen"):
        runtime.validate_runtime_protocol(protocol)

    protocol = _protocol()
    protocol["superiority"]["minimum_primary_gain"] = 0.04
    with pytest.raises(ValueError, match="superiority"):
        runtime.validate_runtime_protocol(protocol)

    protocol = _protocol()
    protocol["confirmatory"]["claim_scope"] = "global_replacement"
    with pytest.raises(ValueError, match="confirmatory"):
        runtime.validate_runtime_protocol(protocol)


def test_corpus_requires_ten_observed_anonymized_cases_and_approved_gold():
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    runtime.validate_runtime_corpus(
        cases, gold, expected_count=10, trusted_signer_public_keys=trusted
    )

    cases[0]["source_type"] = "synthetic_anonymized"
    with pytest.raises(ValueError, match="observed_anonymized"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


@pytest.mark.parametrize(
    "unsafe_path",
    ("../secret", "src//service.py", "src/./service.py", "src/service.py/"),
)
def test_corpus_rejects_unsafe_context_paths(unsafe_path):
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    cases[0]["context_files"] = {unsafe_path: "no"}
    with pytest.raises(ValueError, match="context path"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


@pytest.mark.parametrize(
    ("context_files", "message"),
    [
        (
            {f"src/file-{index}.py": "x" for index in range(21)},
            "context file count",
        ),
        (
            {"src/service.py": "x" * (128 * 1024 + 1)},
            "context file size",
        ),
        (
            {
                "src/first.py": "x" * (128 * 1024),
                "src/second.py": "x" * (128 * 1024),
                "src/third.py": "x" * (128 * 1024),
                "src/fourth.py": "x" * (128 * 1024),
                "src/fifth.py": "x",
            },
            "context total size",
        ),
    ],
)
def test_corpus_rejects_context_resource_limit_overflow(context_files, message):
    cases = [_case(index) for index in range(1, 11)]
    cases[0]["context_files"] = context_files
    cases[0]["context_sha256"] = runtime.canonical_digest(context_files)
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])

    with pytest.raises(ValueError, match=message):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_corpus_rejects_context_or_gold_hash_tampering():
    cases = [_case(index) for index in range(1, 11)]
    gold_items = [_gold(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, gold_items)
    cases[0]["context_files"]["src/service.py"] = "tampered"
    with pytest.raises(ValueError, match="context hash"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )

    cases = [_case(index) for index in range(1, 11)]
    gold["cases"][0]["required_items"].append({"id": "REQ-2", "critical": False, "weight": 1})
    with pytest.raises(ValueError, match="gold hash"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_corpus_rejects_untrusted_or_unsigned_gold():
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    with pytest.raises(ValueError, match="trusted"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=set()
        )
    gold["signoff"]["signature"] = base64.b64encode(b"invalid" * 10).decode("ascii")
    with pytest.raises(ValueError, match="signature"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_confirmatory_manifest_binds_sampling_gold_and_disjoint_fingerprints():
    cases = [_case(index) for index in range(1, 11)]
    gold, _ = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    manifest, trusted = _signed_confirmatory_manifest(cases, gold)

    runtime.validate_confirmatory_manifest(
        manifest,
        cases=cases,
        gold_document=gold,
        trusted_prior_fingerprints=manifest["prior_fingerprints"],
        trusted_signer_public_keys={trusted},
    )

    prior = [_case(99)]
    manifest, trusted = _signed_confirmatory_manifest(cases, gold, prior_cases=prior)
    manifest["prior_fingerprints"][0]["source_sha256"] = cases[0]["provenance"]["source_sha256"]
    manifest["prior_registry_sha256"] = runtime.canonical_digest(
        manifest["prior_fingerprints"]
    )
    signer, trusted = _signer()
    manifest["signoff"]["signer_public_key"] = trusted
    manifest["signoff"]["signature"] = base64.b64encode(
        signer.sign(runtime.confirmatory_manifest_signoff_payload(manifest))
    ).decode("ascii")
    with pytest.raises(ValueError, match="source fingerprint overlap"):
        runtime.validate_confirmatory_manifest(
            manifest,
            cases=cases,
            gold_document=gold,
            trusted_prior_fingerprints=manifest["prior_fingerprints"],
            trusted_signer_public_keys={trusted},
        )

    manifest, trusted = _signed_confirmatory_manifest(cases, gold)
    trusted_prior = [
        *manifest["prior_fingerprints"],
        runtime._case_fingerprint(cases[0]),
    ]
    with pytest.raises(ValueError, match="prior registry"):
        runtime.validate_confirmatory_manifest(
            manifest,
            cases=cases,
            gold_document=gold,
            trusted_prior_fingerprints=trusted_prior,
            trusted_signer_public_keys={trusted},
        )


def test_prepare_confirmatory_runtime_inputs_requires_exact_payload_approval(monkeypatch):
    source_cases = [_case(index) for index in range(1, 11)]
    readiness = _local_transfer_readiness(source_cases)
    public_corpus = {
        "schema_version": 1,
        "status": "frozen",
        "cases": [
            {
                "case_id": case["case_id"],
                "split": "holdout",
                "source_type": "observed_anonymized",
                "task_type": "development",
                "request": case["request"],
                "context_sha256": case["context_sha256"],
            }
            for case in source_cases
        ],
    }
    public_corpus["corpus_sha256"] = runtime.canonical_digest(
        public_corpus["cases"]
    )
    gold, trusted_gold = _signed_gold(
        public_corpus["cases"], [_gold(index) for index in range(1, 11)]
    )
    signer, signer_public_key = _signer()
    prior = [runtime._case_fingerprint(_case(99))]

    approval = runtime.prepare_confirmatory_runtime_inputs(
        readiness=readiness,
        public_corpus=public_corpus,
        selection=_confirmatory_selection(source_cases),
        gold_document=gold,
        trusted_prior_fingerprints=prior,
        skill_sha256="a" * 64,
        producer="fresh-batch-curator",
        author_session_id="gold-author",
        reviewer_session_id="gold-reviewer",
        signer="independent-confirmatory-reviewer",
        signer_public_key=signer_public_key,
        trusted_gold_signer_public_keys=trusted_gold,
    )

    assert approval["status"] == "approval_required"
    assert approval["provider_execution_allowed"] is False
    assert approval["confirmatory_manifest"] is None
    assert len(approval["runtime_corpus"]["cases"]) == 10

    with pytest.raises(ValueError, match="approved payload hash"):
        runtime.prepare_confirmatory_runtime_inputs(
            readiness=readiness,
            public_corpus=public_corpus,
            selection=_confirmatory_selection(source_cases),
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            producer="fresh-batch-curator",
            author_session_id="gold-author",
            reviewer_session_id="gold-reviewer",
            signer="independent-confirmatory-reviewer",
            signer_public_key=signer_public_key,
            trusted_gold_signer_public_keys=trusted_gold,
            approved_payload_sha256="b" * 64,
        )

    prepared = runtime.prepare_confirmatory_runtime_inputs(
        readiness=readiness,
        public_corpus=public_corpus,
        selection=_confirmatory_selection(source_cases),
        gold_document=gold,
        trusted_prior_fingerprints=prior,
        skill_sha256="a" * 64,
        producer="fresh-batch-curator",
        author_session_id="gold-author",
        reviewer_session_id="gold-reviewer",
        signer="independent-confirmatory-reviewer",
        signer_public_key=signer_public_key,
        trusted_gold_signer_public_keys=trusted_gold,
        approved_payload_sha256=approval["external_payload_sha256"],
    )
    assert prepared["status"] == "signature_required"
    assert prepared["confirmatory_manifest"]["transmission"]["approved"] is True
    assert prepared["confirmatory_manifest"]["runtime_runner_sha256"] == (
        runtime._runtime_runner_sha256()
    )

    signature = base64.b64encode(signer.sign(
        runtime.confirmatory_manifest_signoff_payload(
            prepared["confirmatory_manifest"]
        )
    )).decode("ascii")
    receipt = runtime.seal_confirmatory_runtime_inputs(
        prepared,
        signature=signature,
        gold_document=gold,
        trusted_prior_fingerprints=prior,
        skill_sha256="a" * 64,
        trusted_gold_signer_public_keys=trusted_gold,
        trusted_confirmatory_signer_public_keys={signer_public_key},
    )
    assert receipt["status"] == "execution_ready"
    assert receipt["provider_execution_allowed"] is True
    tampered_payload = deepcopy(prepared)
    tampered_payload["external_payload_sha256"] = "b" * 64
    tampered_payload["preparation_sha256"] = runtime.canonical_digest(
        runtime._without_digest(tampered_payload, "preparation_sha256")
    )
    with pytest.raises(ValueError, match="preparation envelope"):
        runtime.seal_confirmatory_runtime_inputs(
            tampered_payload,
            signature=signature,
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            trusted_gold_signer_public_keys=trusted_gold,
            trusted_confirmatory_signer_public_keys={signer_public_key},
        )

    tampered_corpus = deepcopy(prepared)
    tampered_corpus["runtime_corpus"]["corpus_sha256"] = "b" * 64
    tampered_corpus["preparation_sha256"] = runtime.canonical_digest(
        runtime._without_digest(tampered_corpus, "preparation_sha256")
    )
    with pytest.raises(ValueError, match="preparation envelope"):
        runtime.seal_confirmatory_runtime_inputs(
            tampered_corpus,
            signature=signature,
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            trusted_gold_signer_public_keys=trusted_gold,
            trusted_confirmatory_signer_public_keys={signer_public_key},
        )

    tampered_source = deepcopy(prepared)
    tampered_source["runtime_corpus"]["source_corpus_sha256"] = "b" * 64
    tampered_source["preparation_sha256"] = runtime.canonical_digest(
        runtime._without_digest(tampered_source, "preparation_sha256")
    )
    with pytest.raises(ValueError, match="preparation envelope"):
        runtime.seal_confirmatory_runtime_inputs(
            tampered_source,
            signature=signature,
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            trusted_gold_signer_public_keys=trusted_gold,
            trusted_confirmatory_signer_public_keys={signer_public_key},
        )

    with pytest.raises(ValueError, match="signer is not trusted"):
        runtime.seal_confirmatory_runtime_inputs(
            prepared,
            signature=signature,
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            trusted_gold_signer_public_keys=trusted_gold,
            trusted_confirmatory_signer_public_keys={"untrusted"},
        )

    monkeypatch.setattr(runtime, "_runtime_runner_sha256", lambda: "f" * 64)
    with pytest.raises(ValueError, match="runtime runner hash mismatch"):
        runtime.seal_confirmatory_runtime_inputs(
            prepared,
            signature=signature,
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            trusted_gold_signer_public_keys=trusted_gold,
            trusted_confirmatory_signer_public_keys={signer_public_key},
        )


def test_confirmatory_prepare_and_seal_cli_round_trip(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    readiness = _local_transfer_readiness(source_cases)
    public_corpus = {
        "schema_version": 1,
        "status": "frozen",
        "cases": [
            {
                "case_id": case["case_id"],
                "split": "holdout",
                "source_type": "observed_anonymized",
                "task_type": "development",
                "request": case["request"],
                "context_sha256": case["context_sha256"],
            }
            for case in source_cases
        ],
    }
    public_corpus["corpus_sha256"] = runtime.canonical_digest(
        public_corpus["cases"]
    )
    gold, trusted_gold = _signed_gold(
        public_corpus["cases"], [_gold(index) for index in range(1, 11)]
    )
    signer, signer_public_key = _signer()
    prior = [runtime._case_fingerprint(_case(99))]

    inputs = {
        "readiness": readiness,
        "public-corpus": public_corpus,
        "selection": _confirmatory_selection(source_cases),
        "gold": gold,
        "prior": {"schema_version": 1, "fingerprints": prior},
    }
    paths = {}
    for name, payload in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("confirmatory plan skill\n", encoding="utf-8")
    approval_path = tmp_path / "approval.json"
    prepared_path = tmp_path / "prepared.json"
    receipt_path = tmp_path / "receipt.json"

    prepare_command = [
        sys.executable,
        str(Path(runtime.__file__)),
        "prepare-confirmatory",
        str(paths["readiness"]),
        str(paths["public-corpus"]),
        str(paths["selection"]),
        str(paths["gold"]),
        str(paths["prior"]),
        "--skill-file",
        str(skill_path),
        "--trusted-gold-signer-public-key",
        next(iter(trusted_gold)),
        "--producer",
        "fresh-batch-curator",
        "--author-session-id",
        "gold-author",
        "--reviewer-session-id",
        "gold-reviewer",
        "--signer",
        "independent-confirmatory-reviewer",
        "--signer-public-key",
        signer_public_key,
    ]
    subprocess.run(
        [*prepare_command, "--output", str(approval_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    subprocess.run(
        [
            *prepare_command,
            "--approved-payload-sha256",
            approval["external_payload_sha256"],
            "--output",
            str(prepared_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    signature_path = tmp_path / "signature.txt"
    signature_path.write_text(
        base64.b64encode(
            signer.sign(
                runtime.confirmatory_manifest_signoff_payload(
                    prepared["confirmatory_manifest"]
                )
            )
        ).decode("ascii"),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(Path(runtime.__file__)),
            "seal-confirmatory",
            str(prepared_path),
            str(paths["gold"]),
            str(paths["prior"]),
            "--signature-file",
            str(signature_path),
            "--skill-file",
            str(skill_path),
            "--trusted-gold-signer-public-key",
            next(iter(trusted_gold)),
            "--trusted-confirmatory-signer-public-key",
            signer_public_key,
            "--output",
            str(receipt_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "execution_ready"
    assert receipt["provider_execution_allowed"] is True


def test_confirmatory_gold_author_payload_uses_exact_anonymized_baseline_context():
    source_cases = [_case(index) for index in range(1, 11)]
    source_cases[0]["request"] = (
        "Update Sixshop behavior; contact owner@example.com and see "
        "https://internal.example.com/spec."
    )
    source_cases[0]["context_files"] = {
        "service.py": "TARGET = 'libs/sixshop/service.py'\n",
        "libs/sixshop/service.py": (
            "OWNER = 'owner@example.com'\n"
            "SPEC = 'https://internal.example.com/spec'\n"
            "LOCAL = '/Users/example/private/source.py'\n"
            "PRODUCT = 'Sixshop'\n"
        ),
        "tests/test_service.py": "def test_sixshop():\n    assert True\n",
    }
    source_cases[0]["context_sha256"] = runtime.canonical_digest(
        source_cases[0]["context_files"]
    )
    readiness = _local_transfer_readiness(source_cases)
    selection = _confirmatory_selection(source_cases)

    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=readiness,
        selection=selection,
    )

    assert payload["status"] == "approval_required"
    assert payload["provider_execution_allowed"] is False
    assert payload["public_corpus"]["corpus_sha256"] == runtime.canonical_digest(
        payload["public_corpus"]["cases"]
    )
    runtime_by_id = {
        case["case_id"]: case for case in payload["runtime_corpus"]["cases"]
    }
    packet_by_id = {
        case["case_id"]: case
        for case in payload["gold_author_packet"]["cases"]
    }
    public_by_id = {
        case["case_id"]: case for case in payload["public_corpus"]["cases"]
    }
    for case_id, runtime_case in runtime_by_id.items():
        assert packet_by_id[case_id]["context_files"] == runtime_case["context_files"]
        assert public_by_id[case_id]["context_sha256"] == runtime.canonical_digest(
            runtime_case["context_files"]
        )

    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "Sixshop",
        "sixshop",
        "owner@example.com",
        "https://internal.example.com",
        "/Users/example",
        "baseline_commit",
        "followup_commit",
    ):
        assert forbidden not in serialized
    assert sorted(runtime_by_id[source_cases[0]["case_id"]]["context_files"]) == [
        "context/file-01.py",
        "context/file-02.py",
        "context/file-03.py",
    ]
    assert "libs/" not in serialized


def test_confirmatory_runtime_bridge_accepts_generated_anonymized_public_corpus():
    source_cases = [_case(index) for index in range(1, 11)]
    readiness = _local_transfer_readiness(source_cases)
    selection = _confirmatory_selection(source_cases)
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=readiness,
        selection=selection,
    )
    gold, trusted_gold = _signed_gold(
        payload["public_corpus"]["cases"],
        [_gold(index) for index in range(1, 11)],
    )
    _, signer_public_key = _signer()

    preparation = runtime.prepare_confirmatory_runtime_inputs(
        readiness=readiness,
        public_corpus=payload["public_corpus"],
        selection=selection,
        gold_document=gold,
        trusted_prior_fingerprints=[runtime._case_fingerprint(_case(99))],
        skill_sha256="a" * 64,
        producer="fresh-batch-curator",
        author_session_id="gold-author",
        reviewer_session_id="gold-reviewer",
        signer="independent-confirmatory-reviewer",
        signer_public_key=signer_public_key,
        trusted_gold_signer_public_keys=trusted_gold,
    )

    assert preparation["status"] == "approval_required"
    assert preparation["runtime_corpus"] == payload["runtime_corpus"]


def test_confirmatory_runtime_bridge_rejects_anonymization_policy_drift():
    source_cases = [_case(index) for index in range(1, 11)]
    readiness = _local_transfer_readiness(source_cases)
    selection = _confirmatory_selection(source_cases)
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=readiness,
        selection=selection,
    )
    payload["public_corpus"]["anonymization_policy_sha256"] = "f" * 64

    with pytest.raises(ValueError, match="anonymization policy mismatch"):
        runtime._runtime_cases_from_transfer_readiness(
            readiness,
            payload["public_corpus"],
        )


def test_confirmatory_gold_author_payload_cli(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    readiness_path = tmp_path / "readiness.json"
    selection_path = tmp_path / "selection.json"
    output_path = tmp_path / "gold-author-payload.json"
    readiness_path.write_text(
        json.dumps(_local_transfer_readiness(source_cases)),
        encoding="utf-8",
    )
    selection_path.write_text(
        json.dumps(_confirmatory_selection(source_cases)),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(Path(runtime.__file__)),
            "prepare-gold-author",
            str(readiness_path),
            str(selection_path),
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "approval_required"
    assert payload["payload_bundle_sha256"] == runtime.canonical_digest(
        runtime._without_digest(payload, "payload_bundle_sha256")
    )


def test_gold_author_evidence_writes_durable_payload_and_redacted_manifest(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"

    manifest = runtime.prepare_gold_author_evidence(
        payload=payload,
        artifact_root=artifact_root,
        repo_root=tmp_path / "repository",
        source_commit="a" * 40,
        _system_temp_root=tmp_path / "system-temp",
    )

    private_payload = artifact_root / "gold-author-payload.private.json"
    public_manifest = artifact_root / "gold-author-evidence-manifest.json"
    assert private_payload.is_file()
    assert public_manifest.is_file()
    assert manifest == json.loads(public_manifest.read_text(encoding="utf-8"))
    assert manifest["approval_tuple"] == {
        "external_payload_sha256": payload["external_payload_sha256"],
        "payload_bundle_sha256": payload["payload_bundle_sha256"],
        "source_readiness_sha256": payload["source_readiness_sha256"],
        "public_corpus_sha256": payload["public_corpus"]["corpus_sha256"],
        "runtime_corpus_sha256": payload["runtime_corpus"]["corpus_sha256"],
        "anonymization_policy_sha256": payload["anonymization_policy_sha256"],
    }
    assert manifest["privacy_report"]["status"] == "passed"
    assert manifest["case_count"] == 10
    assert manifest["context_file_count"] == 20
    assert "context_files" not in json.dumps(manifest)
    assert manifest["manifest_sha256"] == runtime.canonical_digest(
        runtime._without_digest(manifest, "manifest_sha256")
    )


def test_gold_author_evidence_rejects_unsafe_root_payload_drift_and_sensitive_text(
    tmp_path,
):
    source_cases = [_case(index) for index in range(1, 11)]
    readiness = _local_transfer_readiness(source_cases)
    selection = _confirmatory_selection(source_cases)
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=readiness,
        selection=selection,
    )
    repo_root = tmp_path / "repository"
    repo_root.mkdir()

    with pytest.raises(ValueError, match="temporary"):
        runtime.prepare_gold_author_evidence(
            payload=payload,
            artifact_root=tmp_path / "system-temp" / "evidence",
            repo_root=repo_root,
            source_commit="a" * 40,
            _system_temp_root=tmp_path / "system-temp",
        )
    with pytest.raises(ValueError, match="outside the repository"):
        runtime.prepare_gold_author_evidence(
            payload=payload,
            artifact_root=repo_root / "evidence",
            repo_root=repo_root,
            source_commit="a" * 40,
            _system_temp_root=tmp_path / "system-temp",
        )

    drifted = deepcopy(payload)
    drifted["external_payload_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="payload hash mismatch"):
        runtime.prepare_gold_author_evidence(
            payload=drifted,
            artifact_root=tmp_path / "durable" / "drifted",
            repo_root=repo_root,
            source_commit="a" * 40,
            _system_temp_root=tmp_path / "system-temp",
        )

    sensitive_cases = [_case(index) for index in range(1, 11)]
    sensitive_cases[0]["context_files"]["src/service.py"] += (
        "API_KEY = 'abcdefgh12345678'\n"
    )
    sensitive_cases[0]["context_sha256"] = runtime.canonical_digest(
        sensitive_cases[0]["context_files"]
    )
    sensitive_payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(sensitive_cases),
        selection=_confirmatory_selection(sensitive_cases),
    )
    with pytest.raises(ValueError, match="privacy scan failed"):
        runtime.prepare_gold_author_evidence(
            payload=sensitive_payload,
            artifact_root=tmp_path / "durable" / "sensitive",
            repo_root=repo_root,
            source_commit="a" * 40,
            _system_temp_root=tmp_path / "system-temp",
        )

    sensitive_path_packet = deepcopy(payload["gold_author_packet"])
    content = sensitive_path_packet["cases"][0]["context_files"].pop(
        "context/file-01.py"
    )
    sensitive_path_packet["cases"][0]["context_files"][
        "context/sixshop/file-01.py"
    ] = content
    privacy_report, findings = runtime._gold_evidence_privacy_report(
        sensitive_path_packet
    )
    assert privacy_report["status"] == "failed"
    assert any(item["code"] == "product_identifier" for item in findings)


def test_gold_author_evidence_privacy_rejects_unquoted_and_bearer_credentials(
    tmp_path,
):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    author_packet = deepcopy(payload["gold_author_packet"])
    paths = list(author_packet["cases"][0]["context_files"])
    author_packet["cases"][0]["context_files"][paths[0]] += (
        "\nAPI_KEY=abcdefgh12345678\n"
    )
    author_packet["cases"][0]["context_files"][paths[1]] += (
        "\nAuthorization: Bearer abcdefgh.12345678.signature\n"
    )

    report, findings = runtime._gold_evidence_privacy_report(author_packet)

    assert report["status"] == "failed"
    assert {item["code"] for item in findings} >= {
        "credential_assignment",
        "bearer_token",
    }


def test_gold_author_evidence_privacy_rejects_prefixed_credential_names(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    author_packet = deepcopy(payload["gold_author_packet"])
    paths = list(author_packet["cases"][0]["context_files"])
    author_packet["cases"][0]["context_files"][paths[0]] += (
        "\nGITHUB_TOKEN=ghp_1234567890abcdef\n"
    )
    author_packet["cases"][0]["context_files"][paths[1]] += (
        "\nCLIENT_SECRET=client1234567890\n"
    )

    report, findings = runtime._gold_evidence_privacy_report(author_packet)

    assert report["status"] == "failed"
    assert sum(
        item["code"] == "credential_assignment" for item in findings
    ) >= 2


def test_gold_author_evidence_privacy_rejects_unquoted_alphabetic_credentials(
    tmp_path,
):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    author_packet = deepcopy(payload["gold_author_packet"])
    paths = list(author_packet["cases"][0]["context_files"])
    author_packet["cases"][0]["context_files"][paths[0]] += (
        "\nCLIENT_SECRET=abcdefghijklmnop\n"
    )
    author_packet["cases"][0]["context_files"][paths[1]] += (
        "\nPASSWORD: huntertwo\n"
    )

    report, findings = runtime._gold_evidence_privacy_report(author_packet)

    assert report["status"] == "failed"
    assert sum(
        item["code"] == "credential_assignment" for item in findings
    ) >= 2


@pytest.mark.parametrize(
    "credential",
    [
        "PASSWORD=hunter!two",
        "CLIENT_SECRET=abc@defgh123",
        "GITHUB_TOKEN=ghp-abc:def123",
    ],
)
def test_gold_author_evidence_privacy_rejects_unquoted_special_credentials(
    tmp_path,
    credential,
):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    author_packet = deepcopy(payload["gold_author_packet"])
    path = next(iter(author_packet["cases"][0]["context_files"]))
    author_packet["cases"][0]["context_files"][path] += f"\n{credential}\n"

    report, findings = runtime._gold_evidence_privacy_report(author_packet)

    assert report["status"] == "failed"
    assert any(item["code"] == "credential_assignment" for item in findings)


def test_gold_author_evidence_receipt_rejects_empty_gold_output(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"
    manifest = runtime.prepare_gold_author_evidence(
        payload=payload,
        artifact_root=artifact_root,
        repo_root=tmp_path / "repository",
        source_commit="a" * 40,
        _system_temp_root=tmp_path / "system-temp",
    )

    with pytest.raises(ValueError, match="author raw output"):
        runtime.record_gold_evidence_receipt(
            artifact_root=artifact_root,
            phase="author",
            provider="external-codex",
            session_id="author-session",
            session_nonce="author-nonce",
            approved_manifest_sha256=manifest["manifest_sha256"],
            input_sha256=payload["external_payload_sha256"],
            raw_output="{}",
        )


def test_gold_author_evidence_receipt_requires_approved_manifest_digest(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"
    manifest = runtime.prepare_gold_author_evidence(
        payload=payload,
        artifact_root=artifact_root,
        repo_root=tmp_path / "repository",
        source_commit="a" * 40,
        _system_temp_root=tmp_path / "system-temp",
    )

    with pytest.raises(ValueError, match="approved manifest"):
        runtime.record_gold_evidence_receipt(
            artifact_root=artifact_root,
            phase="author",
            provider="external-codex",
            session_id="author-session",
            session_nonce="author-nonce",
            approved_manifest_sha256="f" * 64,
            input_sha256=payload["external_payload_sha256"],
            raw_output=json.dumps(
                {"cases": [_gold(index) for index in range(1, 11)]}
            ),
        )

    runtime.record_gold_evidence_receipt(
        artifact_root=artifact_root,
        phase="author",
        provider="external-codex",
        session_id="author-session",
        session_nonce="author-nonce",
        approved_manifest_sha256=manifest["manifest_sha256"],
        input_sha256=payload["external_payload_sha256"],
        raw_output=json.dumps(
            {"cases": [_gold(index) for index in range(1, 11)]}
        ),
    )
    ledger = json.loads(
        (artifact_root / "gold-evidence-receipt-ledger.json").read_text(
            encoding="utf-8"
        )
    )
    ledger["receipts"][0]["approval_manifest_sha256"] = "f" * 64
    ledger["receipts"][0]["receipt_sha256"] = runtime.canonical_digest(
        runtime._without_digest(ledger["receipts"][0], "receipt_sha256")
    )
    ledger["ledger_sha256"] = runtime.canonical_digest(
        runtime._without_digest(ledger, "ledger_sha256")
    )

    with pytest.raises(ValueError, match="approved manifest"):
        runtime.validate_gold_evidence_receipt_ledger(
            ledger,
            manifest=manifest,
            artifact_root=artifact_root,
        )


def test_gold_author_evidence_rejects_files_outside_artifact_root(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"
    manifest = runtime.prepare_gold_author_evidence(
        payload=payload,
        artifact_root=artifact_root,
        repo_root=tmp_path / "repository",
        source_commit="a" * 40,
        _system_temp_root=tmp_path / "system-temp",
    )
    outside_payload = artifact_root.parent / "outside-payload.json"
    outside_payload.write_text(json.dumps(payload), encoding="utf-8")
    escaped_manifest = deepcopy(manifest)
    escaped_manifest["private_payload_file"] = "../outside-payload.json"
    escaped_manifest["private_payload_file_sha256"] = runtime._file_sha256(
        outside_payload
    )
    escaped_manifest["payload_byte_size"] = outside_payload.stat().st_size
    escaped_manifest["manifest_sha256"] = runtime.canonical_digest(
        runtime._without_digest(escaped_manifest, "manifest_sha256")
    )

    with pytest.raises(ValueError, match="inside the artifact root"):
        runtime._validate_gold_evidence_manifest(
            escaped_manifest,
            artifact_root=artifact_root,
        )


def test_gold_author_evidence_receipt_rejects_raw_output_outside_artifact_root(
    tmp_path,
):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"
    manifest = runtime.prepare_gold_author_evidence(
        payload=payload,
        artifact_root=artifact_root,
        repo_root=tmp_path / "repository",
        source_commit="a" * 40,
        _system_temp_root=tmp_path / "system-temp",
    )
    runtime.record_gold_evidence_receipt(
        artifact_root=artifact_root,
        phase="author",
        provider="external-codex",
        session_id="author-session",
        session_nonce="author-nonce",
        approved_manifest_sha256=manifest["manifest_sha256"],
        input_sha256=payload["external_payload_sha256"],
        raw_output=json.dumps(
            {"cases": [_gold(index) for index in range(1, 11)]}
        ),
    )
    ledger = json.loads(
        (artifact_root / "gold-evidence-receipt-ledger.json").read_text(
            encoding="utf-8"
        )
    )
    author_output = artifact_root / ledger["receipts"][0]["raw_output_file"]
    outside_output = artifact_root.parent / "outside-author.json"
    outside_output.write_bytes(author_output.read_bytes())
    ledger["receipts"][0]["raw_output_file"] = "../outside-author.json"
    ledger["receipts"][0]["receipt_sha256"] = runtime.canonical_digest(
        runtime._without_digest(ledger["receipts"][0], "receipt_sha256")
    )
    ledger["ledger_sha256"] = runtime.canonical_digest(
        runtime._without_digest(ledger, "ledger_sha256")
    )

    with pytest.raises(ValueError, match="inside the artifact root"):
        runtime.validate_gold_evidence_receipt_ledger(
            ledger,
            manifest=manifest,
            artifact_root=artifact_root,
        )


def test_gold_author_evidence_cleans_staging_directory_on_publish_failure(
    tmp_path, monkeypatch
):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"
    original = runtime._write_json_file

    def fail_manifest(path, value):
        if path.name == "gold-author-evidence-manifest.json":
            raise OSError("simulated manifest failure")
        return original(path, value)

    monkeypatch.setattr(runtime, "_write_json_file", fail_manifest)
    with pytest.raises(OSError, match="simulated manifest failure"):
        runtime.prepare_gold_author_evidence(
            payload=payload,
            artifact_root=artifact_root,
            repo_root=tmp_path / "repository",
            source_commit="a" * 40,
            _system_temp_root=tmp_path / "system-temp",
        )

    assert not artifact_root.exists()
    assert not list(artifact_root.parent.glob(".gold-evidence.staging-*"))


def test_gold_author_evidence_receipts_require_independent_chained_sessions(tmp_path):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"
    manifest = runtime.prepare_gold_author_evidence(
        payload=payload,
        artifact_root=artifact_root,
        repo_root=tmp_path / "repository",
        source_commit="a" * 40,
        _system_temp_root=tmp_path / "system-temp",
    )

    author = runtime.record_gold_evidence_receipt(
        artifact_root=artifact_root,
        phase="author",
        provider="external-codex",
        session_id="author-session",
        session_nonce="author-nonce",
        approved_manifest_sha256=manifest["manifest_sha256"],
        input_sha256=payload["external_payload_sha256"],
        raw_output=json.dumps({"cases": [_gold(index) for index in range(1, 11)]}),
    )
    with pytest.raises(ValueError, match="reviewer input hash mismatch"):
        runtime.record_gold_evidence_receipt(
            artifact_root=artifact_root,
            phase="reviewer",
            provider="external-codex",
            session_id="reviewer-session",
            session_nonce="reviewer-nonce",
            approved_manifest_sha256=manifest["manifest_sha256"],
            input_sha256="f" * 64,
            raw_output=json.dumps(_reviewed_gold_output("f" * 64)),
        )
    with pytest.raises(ValueError, match="independent session"):
        runtime.record_gold_evidence_receipt(
            artifact_root=artifact_root,
            phase="reviewer",
            provider="external-codex",
            session_id="author-session",
            session_nonce="author-nonce",
            approved_manifest_sha256=manifest["manifest_sha256"],
            input_sha256=author["raw_output_sha256"],
            raw_output=json.dumps(
                _reviewed_gold_output(author["raw_output_sha256"])
            ),
        )

    reviewer = runtime.record_gold_evidence_receipt(
        artifact_root=artifact_root,
        phase="reviewer",
        provider="external-codex",
        session_id="reviewer-session",
        session_nonce="reviewer-nonce",
        approved_manifest_sha256=manifest["manifest_sha256"],
        input_sha256=author["raw_output_sha256"],
        raw_output=json.dumps(_reviewed_gold_output(author["raw_output_sha256"])),
    )
    ledger = json.loads(
        (artifact_root / "gold-evidence-receipt-ledger.json").read_text(
            encoding="utf-8"
        )
    )
    runtime.validate_gold_evidence_receipt_ledger(
        ledger,
        manifest=manifest,
        artifact_root=artifact_root,
    )
    assert [item["phase"] for item in ledger["receipts"]] == [
        "author",
        "reviewer",
    ]
    assert reviewer["input_sha256"] == author["raw_output_sha256"]


def test_gold_author_evidence_receipt_failure_preserves_previous_ledger(
    tmp_path, monkeypatch
):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    artifact_root = tmp_path / "durable" / "gold-evidence"
    manifest = runtime.prepare_gold_author_evidence(
        payload=payload,
        artifact_root=artifact_root,
        repo_root=tmp_path / "repository",
        source_commit="a" * 40,
        _system_temp_root=tmp_path / "system-temp",
    )
    author = runtime.record_gold_evidence_receipt(
        artifact_root=artifact_root,
        phase="author",
        provider="external-codex",
        session_id="author-session",
        session_nonce="author-nonce",
        approved_manifest_sha256=manifest["manifest_sha256"],
        input_sha256=payload["external_payload_sha256"],
        raw_output=json.dumps({"cases": [_gold(index) for index in range(1, 11)]}),
    )
    ledger_path = artifact_root / "gold-evidence-receipt-ledger.json"
    previous_ledger = ledger_path.read_bytes()
    original = runtime._write_json_file

    def fail_ledger(path, value):
        if path.name == "gold-evidence-receipt-ledger.json":
            raise OSError("simulated ledger failure")
        return original(path, value)

    monkeypatch.setattr(runtime, "_write_json_file", fail_ledger)
    with pytest.raises(OSError, match="simulated ledger failure"):
        runtime.record_gold_evidence_receipt(
            artifact_root=artifact_root,
            phase="reviewer",
            provider="external-codex",
            session_id="reviewer-session",
            session_nonce="reviewer-nonce",
            approved_manifest_sha256=manifest["manifest_sha256"],
            input_sha256=author["raw_output_sha256"],
            raw_output=json.dumps(
                _reviewed_gold_output(author["raw_output_sha256"])
            ),
        )

    assert ledger_path.read_bytes() == previous_ledger
    assert not (artifact_root / "reviewer-raw-output.private.json").exists()
    assert not list(artifact_root.parent.glob(".gold-evidence.staging-*"))
    assert not list(artifact_root.parent.glob(".gold-evidence.backup-*"))


def test_gold_author_evidence_cli_prepares_and_records_receipts(
    tmp_path, monkeypatch, capsys
):
    source_cases = [_case(index) for index in range(1, 11)]
    payload = runtime.prepare_confirmatory_gold_author_payload(
        readiness=_local_transfer_readiness(source_cases),
        selection=_confirmatory_selection(source_cases),
    )
    payload_path = tmp_path / "gold-author-payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    artifact_root = tmp_path / "durable" / "gold-evidence"
    monkeypatch.setattr(
        runtime.tempfile,
        "gettempdir",
        lambda: str(tmp_path / "system-temp"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(Path(runtime.__file__)),
            "prepare-gold-evidence",
            str(payload_path),
            "--artifact-root",
            str(artifact_root),
            "--repo-root",
            str(tmp_path / "repository"),
            "--source-commit",
            "a" * 40,
        ],
    )

    assert runtime.main() == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["status"] == "approval_required"
    assert prepared["provider_execution_allowed"] is False

    raw_output_path = tmp_path / "author-output.json"
    raw_output_path.write_text(
        json.dumps({"cases": [_gold(index) for index in range(1, 11)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(Path(runtime.__file__)),
            "record-gold-receipt",
            str(artifact_root),
            "--phase",
            "author",
            "--provider",
            "external-codex",
            "--session-id",
            "author-session",
            "--session-nonce",
            "author-nonce",
            "--approved-manifest-sha256",
            prepared["manifest_sha256"],
            "--input-sha256",
            payload["external_payload_sha256"],
            "--raw-output-file",
            str(raw_output_path),
        ],
    )

    assert runtime.main() == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["phase"] == "author"
    assert recorded["input_sha256"] == payload["external_payload_sha256"]
    assert "raw_output" not in recorded


def test_confirmatory_runtime_bridge_rejects_transfer_or_skill_drift():
    source_cases = [_case(index) for index in range(1, 11)]
    readiness = _local_transfer_readiness(source_cases)
    public_corpus = {
        "schema_version": 1,
        "status": "frozen",
        "cases": [
            {
                "case_id": case["case_id"],
                "split": "holdout",
                "source_type": "observed_anonymized",
                "task_type": "development",
                "request": case["request"],
                "context_sha256": case["context_sha256"],
            }
            for case in source_cases
        ],
    }
    public_corpus["corpus_sha256"] = runtime.canonical_digest(
        public_corpus["cases"]
    )
    gold, trusted_gold = _signed_gold(
        public_corpus["cases"], [_gold(index) for index in range(1, 11)]
    )
    _, signer_public_key = _signer()
    prior = [runtime._case_fingerprint(_case(99))]

    tampered = deepcopy(readiness)
    tampered["transfer_bundle"]["cases"][0]["request"] = "tampered"
    with pytest.raises(ValueError, match="readiness hash"):
        runtime.prepare_confirmatory_runtime_inputs(
            readiness=tampered,
            public_corpus=public_corpus,
            selection=_confirmatory_selection(source_cases),
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            producer="fresh-batch-curator",
            author_session_id="gold-author",
            reviewer_session_id="gold-reviewer",
            signer="independent-confirmatory-reviewer",
            signer_public_key=signer_public_key,
            trusted_gold_signer_public_keys=trusted_gold,
        )

    substituted = deepcopy(readiness)
    substituted_content = "SUBSTITUTED = True\n"
    substituted["transfer_bundle"]["cases"][0]["files"][0][
        "content_utf8"
    ] = substituted_content
    substituted_metadata = substituted["transfer_manifest"]["cases"][0][
        "files"
    ][0]
    substituted_metadata["blob_sha256"] = runtime._sha256_text(
        substituted_content
    )
    substituted_metadata["byte_size"] = len(substituted_content.encode("utf-8"))
    substituted["transfer_bundle"]["bundle_sha256"] = runtime.canonical_digest(
        runtime._without_digest(
            substituted["transfer_bundle"], "bundle_sha256"
        )
    )
    substituted["transfer_manifest"]["transfer_bundle_sha256"] = substituted[
        "transfer_bundle"
    ]["bundle_sha256"]
    substituted["transfer_manifest"]["manifest_sha256"] = (
        runtime.canonical_digest(
            runtime._without_digest(
                substituted["transfer_manifest"], "manifest_sha256"
            )
        )
    )
    substituted["privacy_audit"]["transfer_manifest_sha256"] = substituted[
        "transfer_manifest"
    ]["manifest_sha256"]
    substituted["privacy_audit"]["audit_sha256"] = runtime.canonical_digest(
        runtime._without_digest(substituted["privacy_audit"], "audit_sha256")
    )
    substituted["readiness_sha256"] = runtime.canonical_digest(
        runtime._without_digest(substituted, "readiness_sha256")
    )
    with pytest.raises(ValueError, match="context hash"):
        runtime.prepare_confirmatory_runtime_inputs(
            readiness=substituted,
            public_corpus=public_corpus,
            selection=_confirmatory_selection(source_cases),
            gold_document=gold,
            trusted_prior_fingerprints=prior,
            skill_sha256="a" * 64,
            producer="fresh-batch-curator",
            author_session_id="gold-author",
            reviewer_session_id="gold-reviewer",
            signer="independent-confirmatory-reviewer",
            signer_public_key=signer_public_key,
            trusted_gold_signer_public_keys=trusted_gold,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda contract: contract["case_labels"][0].update(
                surface="api_payload"
            ),
            "surface quota",
        ),
        (
            lambda contract: contract["case_labels"][0].update(
                ambiguity="medium"
            ),
            "ambiguity quota",
        ),
        (
            lambda contract: contract["case_labels"][2].update(
                selected_object=True
            ),
            "selected-object quota",
        ),
    ],
)
def test_confirmatory_manifest_enforces_semantic_quota(mutation, message):
    cases = [_case(index) for index in range(1, 11)]
    gold, _ = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    manifest, _ = _signed_confirmatory_manifest(cases, gold)
    mutation(manifest["sampling"]["semantic_contract"])
    trusted = _resign_confirmatory_manifest(manifest)

    with pytest.raises(ValueError, match=message):
        runtime.validate_confirmatory_manifest(
            manifest,
            cases=cases,
            gold_document=gold,
            trusted_prior_fingerprints=manifest["prior_fingerprints"],
            trusted_signer_public_keys={trusted},
        )


def test_confirmatory_manifest_rejects_legacy_schema_without_semantic_quota():
    cases = [_case(index) for index in range(1, 11)]
    gold, _ = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    manifest, _ = _signed_confirmatory_manifest(cases, gold)
    manifest["schema_version"] = 1
    manifest["sampling"].pop("semantic_contract")
    trusted = _resign_confirmatory_manifest(manifest)

    with pytest.raises(ValueError, match="fields are invalid"):
        runtime.validate_confirmatory_manifest(
            manifest,
            cases=cases,
            gold_document=gold,
            trusted_prior_fingerprints=manifest["prior_fingerprints"],
            trusted_signer_public_keys={trusted},
        )


def test_batch_a_candidate_selection_is_preregistered_and_disjoint():
    fixture_root = Path(__file__).parent / "fixtures"
    selection = json.loads(
        (fixture_root / "omc_plan_confirmatory_batch_a_selection.json").read_text()
    )
    prior_registry = json.loads(
        (fixture_root / "omc_plan_confirmatory_prior_commits.json").read_text()
    )

    runtime.validate_confirmatory_candidate_selection(
        selection,
        trusted_prior_commits=prior_registry["commits"],
    )

    overlap_selection = deepcopy(selection)
    overlap_selection["cases"][0]["followup_commit"] = prior_registry["commits"][0]
    overlap_selection["selection_sha256"] = runtime.canonical_digest(
        overlap_selection["cases"]
    )
    with pytest.raises(ValueError, match="prior commit overlap"):
        runtime.validate_confirmatory_candidate_selection(
            overlap_selection,
            trusted_prior_commits=prior_registry["commits"],
        )

    chained_selection = deepcopy(selection)
    chained_selection["cases"][1]["baseline_commit"] = selection["cases"][0][
        "followup_commit"
    ]
    chained_selection["selection_sha256"] = runtime.canonical_digest(
        chained_selection["cases"]
    )
    with pytest.raises(ValueError, match="chained commits"):
        runtime.validate_confirmatory_candidate_selection(
            chained_selection,
            trusted_prior_commits=prior_registry["commits"],
        )

    duplicate_path_selection = deepcopy(selection)
    duplicate_path_selection["cases"][1]["repo_alias"] = selection["cases"][0][
        "repo_alias"
    ]
    duplicate_path_selection["cases"][1]["context_candidate_paths"][0] = (
        "./" + selection["cases"][0]["context_candidate_paths"][0]
    )
    duplicate_path_selection["selection_sha256"] = runtime.canonical_digest(
        duplicate_path_selection["cases"]
    )
    with pytest.raises(ValueError, match="context path overlap"):
        runtime.validate_confirmatory_candidate_selection(
            duplicate_path_selection,
            trusted_prior_commits=prior_registry["commits"],
        )


def test_fresh_batch_a_v2_selection_consumes_the_diagnostic_batch():
    fixture_root = Path(__file__).parent / "fixtures"
    old_selection = json.loads(
        (fixture_root / "omc_plan_confirmatory_batch_a_selection.json").read_text()
    )
    fresh_selection = json.loads(
        (fixture_root / "omc_plan_confirmatory_batch_a_v2_selection.json").read_text()
    )
    prior_registry = json.loads(
        (fixture_root / "omc_plan_confirmatory_prior_commits_v2.json").read_text()
    )

    runtime.validate_confirmatory_candidate_selection(
        fresh_selection,
        trusted_prior_commits=prior_registry["commits"],
    )

    old_followups = {case["followup_commit"] for case in old_selection["cases"]}
    fresh_followups = {case["followup_commit"] for case in fresh_selection["cases"]}
    assert old_followups <= set(prior_registry["commits"])
    assert fresh_followups.isdisjoint(prior_registry["commits"])
    assert len(prior_registry["commits"]) == 30


def test_confirmatory_manifest_rejects_gold_role_or_provider_leakage():
    cases = [_case(index) for index in range(1, 11)]
    gold, _ = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    manifest, trusted = _signed_confirmatory_manifest(cases, gold)
    manifest["gold_independence"]["reviewer_session_id"] = "gold-author-session"
    with pytest.raises(ValueError, match="gold sessions must be independent"):
        runtime.validate_confirmatory_manifest(
            manifest,
            cases=cases,
            gold_document=gold,
            trusted_prior_fingerprints=manifest["prior_fingerprints"],
            trusted_signer_public_keys={trusted},
        )

    manifest, trusted = _signed_confirmatory_manifest(cases, gold)
    manifest["gold_independence"]["provider_outputs_available"] = True
    with pytest.raises(ValueError, match="provider outputs must be unavailable"):
        runtime.validate_confirmatory_manifest(
            manifest,
            cases=cases,
            gold_document=gold,
            trusted_prior_fingerprints=manifest["prior_fingerprints"],
            trusted_signer_public_keys={trusted},
        )


def test_confirmatory_budget_stops_before_next_execution():
    state = runtime.new_execution_budget_state({
        "observed_total_token_stop_threshold": 100,
        "maximum_external_calls": 2,
    })
    runtime.consume_execution_budget(
        state,
        {
            "activation": {"attempt_count": 1},
            "usage": {
                "status": "observed",
                "input_tokens": 40,
                "output_tokens": 10,
                "total_tokens": 50,
            },
        },
    )
    runtime.assert_execution_budget_available(state)
    runtime.consume_execution_budget(
        state,
        {
            "activation": {"attempt_count": 1},
            "usage": {
                "status": "observed",
                "input_tokens": 40,
                "output_tokens": 10,
                "total_tokens": 50,
            },
        },
    )
    with pytest.raises(RuntimeError, match="external call budget exhausted"):
        runtime.assert_execution_budget_available(state)


def test_confirmatory_budget_reserves_largest_observed_provider_call(tmp_path):
    state = runtime.new_execution_budget_state({
        "observed_total_token_stop_threshold": 100,
        "maximum_external_calls": 4,
    })
    state["used_total_tokens"] = 70
    receipt = tmp_path / "budget-failure.json"

    reserve = runtime.observed_provider_token_reserve(
        "omc-plan",
        activation_probe={
            "executions": {
                "omc-plan": {"usage": {
                    "status": "observed",
                    "input_tokens": 15,
                    "output_tokens": 5,
                    "total_tokens": 20,
                }},
                "baseline-plan": {"usage": {
                    "status": "observed",
                    "input_tokens": 8,
                    "output_tokens": 2,
                    "total_tokens": 10,
                }},
            },
        },
        executions=[
            {
                "provider_id": "omc-plan",
                "usage": {
                    "status": "observed",
                    "input_tokens": 30,
                    "output_tokens": 10,
                    "total_tokens": 40,
                },
            },
            {
                "provider_id": "baseline-plan",
                "usage": {
                    "status": "observed",
                    "input_tokens": 50,
                    "output_tokens": 10,
                    "total_tokens": 60,
                },
            },
        ],
    )

    assert reserve == 40
    with pytest.raises(RuntimeError, match="projected token reserve exhausted"):
        runtime.assert_execution_budget_available(
            state,
            required_token_reserve=reserve,
            failure_receipt_path=receipt,
            execution_id="confirmatory:case-10:omc-plan",
        )
    assert state["used_total_tokens"] == 70
    assert json.loads(receipt.read_text(encoding="utf-8"))["reason_code"] == (
        "projected_token_stop_threshold_exceeded"
    )


def test_confirmatory_token_budget_is_an_auditable_stop_threshold(tmp_path):
    assert runtime.FROZEN_CONFIRMATORY_BUDGET == {
        "observed_total_token_stop_threshold": 1_200_000,
        "maximum_external_calls": 30,
    }
    state = runtime.new_execution_budget_state({
        "observed_total_token_stop_threshold": 100,
        "maximum_external_calls": 2,
    })
    state["used_total_tokens"] = 99
    receipt = tmp_path / "budget-failure.json"

    runtime.assert_execution_budget_available(state)
    with pytest.raises(RuntimeError, match="observed total token stop threshold exceeded"):
        runtime.consume_execution_budget(
            state,
            {
                "activation": {"attempt_count": 1},
                "usage": {
                    "status": "observed",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            },
            failure_receipt_path=receipt,
            execution_id="confirmatory:case-01:omc-plan",
        )
    assert state["used_total_tokens"] == 101
    assert state["used_external_calls"] == 1
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "status": "failed",
        "reason_code": "observed_token_stop_threshold_exceeded",
        "execution_id": "confirmatory:case-01:omc-plan",
        "execution_budget_state": state,
        "usage": {
            "status": "observed",
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
        },
    }


def test_confirmatory_budget_rejects_overrun_and_tampered_state():
    state = runtime.new_execution_budget_state({
        "observed_total_token_stop_threshold": 100,
        "maximum_external_calls": 2,
    })
    with pytest.raises(RuntimeError, match="observed total token stop threshold exceeded"):
        runtime.consume_execution_budget(
            state,
            {
                "activation": {"attempt_count": 1},
                "usage": {
                    "status": "observed",
                    "input_tokens": 90,
                    "output_tokens": 20,
                    "total_tokens": 110,
                },
            },
        )

    tampered = runtime.new_execution_budget_state({
        "observed_total_token_stop_threshold": 100,
        "maximum_external_calls": 2,
    })
    tampered["used_external_calls"] = -1
    with pytest.raises(ValueError, match="execution budget state"):
        runtime.validate_execution_budget_state(
            tampered,
            expected_budget={
                "observed_total_token_stop_threshold": 100,
                "maximum_external_calls": 2,
            },
        )


def test_confirmatory_budget_must_match_execution_evidence():
    activation_probe = {
        "executions": {
            provider_id: {
                "activation": {"attempt_count": 1},
                "usage": {
                    "status": "observed",
                    "input_tokens": 4,
                    "output_tokens": 1,
                    "total_tokens": 5,
                },
            }
            for provider_id in runtime.PROVIDERS
        }
    }
    executions = [
        {
            "activation": {"attempt_count": 1},
            "usage": {
                "status": "observed",
                "input_tokens": 8,
                "output_tokens": 2,
                "total_tokens": 10,
            },
        }
        for _ in range(20)
    ]
    reported = {
        **runtime.FROZEN_CONFIRMATORY_BUDGET,
        "used_total_tokens": 0,
        "used_external_calls": 0,
    }
    with pytest.raises(ValueError, match="execution budget evidence mismatch"):
        runtime.validate_execution_budget_evidence(
            reported,
            activation_probe=activation_probe,
            executions=executions,
            expected_budget=runtime.FROZEN_CONFIRMATORY_BUDGET,
        )


def test_confirmatory_budget_rejects_inconsistent_activation_usage():
    activation_probe = {
        "executions": {
            provider_id: {
                "activation": {"attempt_count": 1},
                "usage": {
                    "status": "observed",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 0,
                },
            }
            for provider_id in runtime.PROVIDERS
        }
    }
    reported = {
        **runtime.FROZEN_CONFIRMATORY_BUDGET,
        "used_total_tokens": 0,
        "used_external_calls": len(runtime.PROVIDERS),
    }

    with pytest.raises(ValueError, match="budget evidence"):
        runtime.validate_execution_budget_evidence(
            reported,
            activation_probe=activation_probe,
            executions=[],
            expected_budget=runtime.FROZEN_CONFIRMATORY_BUDGET,
        )


def test_activation_uses_hidden_output_nonce_instead_of_unsupported_events():
    protocol = runtime.validate_runtime_protocol(_protocol())
    assert protocol["activation"]["proof_method"] == "output_nonce"
    assert protocol["activation"]["max_attempts"] == 2
    assert "accepted_event_types" not in protocol["activation"]

    evidence = runtime.require_activation_receipt(
        {"runtime_activation_receipt": "secret-nonce"},
        provider_id="omc-plan",
        expected_receipt="secret-nonce",
        baseline_sentinel="unavailable",
    )
    assert evidence["status"] == "observed"

    with pytest.raises(ValueError, match="activation_receipt_mismatch"):
        runtime.require_activation_receipt(
            {"runtime_activation_receipt": "unavailable"},
            provider_id="omc-plan",
            expected_receipt="secret-nonce",
            baseline_sentinel="unavailable",
        )


def test_baseline_must_return_activation_sentinel():
    with pytest.raises(ValueError, match="baseline_skill_activation"):
        runtime.require_activation_receipt(
            {"runtime_activation_receipt": "secret-nonce"},
            provider_id="baseline-plan",
            expected_receipt="secret-nonce",
            baseline_sentinel="unavailable",
        )


def test_instrumented_skill_prioritizes_activation_receipt_without_duplication():
    skill = "---\nskill_name: omc-plan\n---\n\n# OMC Plan\n\nPlan rules.\n"
    instrumented = runtime.instrument_skill(skill, "secret-nonce")

    assert instrumented.startswith("---\nskill_name: omc-plan\n---")
    assert instrumented.count("secret-nonce") == 1
    assert instrumented.index("secret-nonce") < instrumented.index("# OMC Plan")


def test_omc_provider_prompt_embeds_exact_context_before_planning():
    context_files = {
        "src/service.py": "def retry():\n    return 3\n",
        "tests/test_service.py": "def test_retry():\n    assert retry() == 3\n",
    }
    prompt = runtime.build_provider_prompt(
        "omc-plan",
        "Plan this change",
        context_paths=tuple(context_files),
        context_files=context_files,
    )

    assert "Frozen context is embedded below as canonical JSON" in prompt
    assert '"src/service.py"' in prompt
    assert '"tests/test_service.py"' in prompt
    assert "def retry()" in prompt
    assert "Do not run a shell command or call another tool" in prompt
    assert "cat --" not in prompt
    assert "$omc-plan" in prompt


def test_provider_prompt_embeds_frozen_context_without_model_owned_read():
    context_files = {
        "context/service.py": "def retry():\n    return 3\n",
        "context/test_service.py": "def test_retry():\n    assert retry() == 3\n",
    }

    for provider_id in runtime.PROVIDERS:
        prompt = runtime.build_provider_prompt(
            provider_id,
            "Plan this change",
            context_paths=tuple(context_files),
            context_files=context_files,
        )

        assert "Frozen context is embedded below" in prompt
        assert '"context/service.py"' in prompt
        assert "def retry()" in prompt
        assert "Do not run a shell command or call another tool" in prompt
        assert "Use one shell command" not in prompt


def test_provider_prompt_rejects_context_paths_without_frozen_content():
    with pytest.raises(ValueError, match="context paths do not match"):
        runtime.build_provider_prompt(
            "omc-plan",
            "Plan this change",
            context_paths=("context/service.py",),
        )


def test_omc_activation_prompt_uses_native_skill_without_shell_command():
    prompt = runtime.build_provider_prompt("omc-plan", "Return activation receipt")

    assert "apply the loaded skill" in prompt
    assert "Do not run a shell command" in prompt
    assert "Do not create a todo list or use a planning tool" in prompt
    assert "Return only the output-schema fields" in prompt
    assert "descriptive item and task text field as one concise sentence" in prompt
    assert "Keep supports and other ID-reference arrays as short IDs" in prompt
    assert "each list item and task field" not in prompt
    assert "cat --" not in prompt
    assert ".agents/skills/omc-plan/SKILL.md" not in prompt


def test_omc_plan_skill_uses_runner_injected_context_in_isolated_runtime():
    for skill_path in (
        Path(".agents/skills/omc-plan/SKILL.md"),
        Path("templates/.agents/skills/omc-plan/SKILL.md"),
    ):
        skill = skill_path.read_text(encoding="utf-8")

        assert "격리 benchmark" in skill
        assert "runner가 주입한 frozen context만 사용" in skill
        assert "shell·추가 tool 호출 없이 즉시 구조화 결과" in skill


def test_baseline_provider_prompt_limits_context_without_exposing_skill_path():
    context_files = {
        "src/service.py": "def retry():\n    return 3\n",
        "tests/test_service.py": "def test_retry():\n    assert retry() == 3\n",
    }
    prompt = runtime.build_provider_prompt(
        "baseline-plan",
        "Plan this change",
        context_paths=tuple(context_files),
        context_files=context_files,
    )

    assert "Frozen context is embedded below as canonical JSON" in prompt
    assert '"src/service.py"' in prompt
    assert '"tests/test_service.py"' in prompt
    assert "Do not run a shell command or call another tool" in prompt
    assert "cat --" not in prompt
    assert ".agents/skills/omc-plan/SKILL.md" not in prompt
    assert "secret-nonce" not in prompt


def test_activation_probe_materializes_omc_workspace_after_baseline(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "omc-plan" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("# OMC Plan\n", encoding="utf-8")
    artifact_root = tmp_path / "probe"
    calls = []

    def fake_execute_provider(**kwargs):
        calls.append(kwargs["provider_id"])
        omc_workspace = artifact_root / "omc-workspace"
        if kwargs["provider_id"] == "baseline-plan":
            assert not omc_workspace.exists()
        else:
            assert omc_workspace.is_dir()
        return {
            "provider_id": kwargs["provider_id"],
            "activation": {"status": "observed"},
        }

    monkeypatch.setattr(runtime, "execute_provider", fake_execute_provider)

    report = runtime.run_activation_probe(
        protocol=_protocol(),
        skill_path=skill_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        output_schema=Path(runtime.__file__).parent
        / "fixtures/omc_plan_output_schema.json",
        artifact_root=artifact_root,
    )

    assert calls == ["baseline-plan", "omc-plan"]
    assert report["status"] == "pass"


def test_activation_probe_stops_before_omc_workspace_when_baseline_fails(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "omc-plan" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("# OMC Plan\n", encoding="utf-8")
    artifact_root = tmp_path / "probe"
    calls = []

    def fake_execute_provider(**kwargs):
        calls.append(kwargs["provider_id"])
        raise RuntimeError("baseline failed")

    monkeypatch.setattr(runtime, "execute_provider", fake_execute_provider)

    with pytest.raises(RuntimeError, match="baseline failed"):
        runtime.run_activation_probe(
            protocol=_protocol(),
            skill_path=skill_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            output_schema=Path(runtime.__file__).parent
            / "fixtures/omc_plan_output_schema.json",
            artifact_root=artifact_root,
        )

    assert calls == ["baseline-plan"]
    assert not (artifact_root / "omc-workspace").exists()


def test_workspace_parity_allows_only_the_skill_file_delta():
    baseline = {"src/service.py": "hash-a", "tests/test_service.py": "hash-b"}
    omc = {
        **baseline,
        ".agents/skills/omc-plan/SKILL.md": "hash-skill",
    }
    runtime.validate_workspace_parity(
        baseline, omc, allowed_delta=".agents/skills/omc-plan/SKILL.md"
    )
    omc["AGENTS.md"] = "unexpected"
    with pytest.raises(ValueError, match="workspace_mismatch"):
        runtime.validate_workspace_parity(
            baseline, omc, allowed_delta=".agents/skills/omc-plan/SKILL.md"
        )


def test_codex_command_keeps_project_skill_discovery_enabled(tmp_path):
    command = runtime.build_codex_command(
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=tmp_path / "output.json",
    )
    assert "--ignore-user-config" in command
    assert "--ignore-rules" not in command
    assert "--ephemeral" in command
    assert command[command.index("--output-schema") + 1] == str(
        (Path.cwd() / "schema.json").resolve()
    )


def test_execute_provider_preserves_activation_and_usage(tmp_path, monkeypatch):
    output_path = tmp_path / "output.json"
    skill_hash = "c" * 64

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "skill.activated",
                "skill_name": "omc-plan",
                "skill_sha256": skill_hash,
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        assert kwargs["timeout"] == 180
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "secret-nonce",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    result = runtime.execute_provider(
        provider_id="omc-plan",
        request="Plan this change",
        workspace=tmp_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=output_path,
        skill_sha256=skill_hash,
        expected_activation_receipt="secret-nonce",
        baseline_sentinel="unavailable",
        timeout_sec=180,
    )
    assert result["activation"]["status"] == "observed"
    assert "runtime_activation_receipt" not in result["plan"]
    assert result["usage"]["total_tokens"] == 15


def test_execute_provider_rejects_state_sync_as_unsafe_shell_command(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        "/bin/zsh -lc 'python3 scripts/omc.py state sync-session "
                        "--target .'"
                    ),
                },
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "secret-nonce",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="unsafe_shell_command"):
        runtime.execute_provider(
            provider_id="omc-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=output_path,
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            failure_receipt_path=failure_path,
        )

    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["reason_code"] == "unsafe_shell_command"


def test_state_round_trip_gate_allows_one_sync_for_explicit_script_context():
    event = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "sync-1",
            "type": "command_execution",
            "command": "python3 scripts/omc.py state sync-session --target .",
        },
    })

    assert not runtime.contains_redundant_omc_state_round_trip(
        event,
        context_paths=("scripts/omc.py",),
    )


def test_state_round_trip_gate_rejects_status_and_repeated_sync():
    status_event = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "status-1",
            "type": "command_execution",
            "command": "python3 scripts/omc.py state status --target .",
        },
    })
    repeated_sync_events = "\n".join(
        json.dumps({
            "type": "item.completed",
            "item": {
                "id": execution_id,
                "type": "command_execution",
                "command": "python3 scripts/omc.py state sync-session --target .",
            },
        })
        for execution_id in ("sync-1", "sync-2")
    )
    repeated_sync_command = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "sync-combined",
            "type": "command_execution",
            "command": (
                "python3 scripts/omc.py state sync-session --target . && "
                "python3 scripts/omc.py state sync-session --target ."
            ),
        },
    })

    assert runtime.contains_redundant_omc_state_round_trip(
        status_event,
        context_paths=("scripts/omc.py",),
    )
    assert runtime.contains_redundant_omc_state_round_trip(
        repeated_sync_events,
        context_paths=("scripts/omc.py",),
    )
    assert runtime.contains_redundant_omc_state_round_trip(
        repeated_sync_command,
        context_paths=("scripts/omc.py",),
    )


def test_context_round_trip_gate_rejects_multiple_read_commands():
    one_batched_read = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "read-1",
            "type": "command_execution",
            "command": "cat -- context/a.txt context/b.txt",
        },
    })
    repeated_reads = "\n".join([
        json.dumps({
            "type": "item.completed",
            "item": {
                "id": "read-1",
                "type": "command_execution",
                "command": "cat context/a.txt",
            },
        }),
        json.dumps({
            "type": "item.completed",
            "item": {
                "id": "read-2",
                "type": "command_execution",
                "command": "cat context/b.txt",
            },
        }),
    ])

    assert not runtime.contains_redundant_context_round_trip(
        one_batched_read,
        context_paths=("context/a.txt", "context/b.txt"),
    )
    assert runtime.contains_redundant_context_round_trip(
        repeated_reads,
        context_paths=("context/a.txt", "context/b.txt"),
    )


def test_plan_round_trip_gate_rejects_pwd_and_progress_only_commands():
    pwd_event = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "pwd-1",
            "type": "command_execution",
            "command": "/bin/zsh -lc pwd",
        },
    })
    progress_event = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "progress-1",
            "type": "command_execution",
            "command": "printf '%s\\n' 'planning from provided files'",
        },
    })
    context_event = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "read-1",
            "type": "command_execution",
            "command": "cat -- context/a.txt",
        },
    })
    todo_event = json.dumps({
        "type": "item.started",
        "item": {
            "id": "todo-1",
            "type": "todo_list",
            "items": [{"text": "Plan the work", "completed": False}],
        },
    })

    assert runtime.contains_unnecessary_plan_round_trip(pwd_event)
    assert runtime.contains_unnecessary_plan_round_trip(progress_event)
    assert runtime.contains_unnecessary_plan_round_trip(todo_event)
    assert not runtime.contains_unnecessary_plan_round_trip(context_event)
    assert runtime.detect_unnecessary_plan_round_trip(pwd_event) == {
        "kind": "pwd",
        "command_sha256": runtime._sha256_text("/bin/zsh -lc pwd"),
    }
    assert runtime.detect_unnecessary_plan_round_trip(progress_event) == {
        "kind": "progress_only_printf",
        "command_sha256": runtime._sha256_text(
            "printf '%s\\n' 'planning from provided files'"
        ),
    }
    assert runtime.detect_unnecessary_plan_round_trip(todo_event) == {
        "kind": "todo_list",
        "command_sha256": runtime._sha256_text("todo-1"),
    }
    assert runtime.detect_unnecessary_plan_round_trip(
        f"{pwd_event}\n{todo_event}"
    ) == {
        "kind": "todo_list",
        "command_sha256": runtime._sha256_text("todo-1"),
    }


def test_omc_plan_shell_contract_requires_one_exact_read_command():
    expected = "cat -- context/a.txt"
    exact_read = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "read-1",
            "type": "command_execution",
            "command": expected,
        },
    })
    unexpected_first = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "list-1",
            "type": "command_execution",
            "command": "ls -la",
        },
    })
    additional_command = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "list-2",
            "type": "command_execution",
            "command": "git status --short",
        },
    })

    assert runtime.detect_omc_plan_shell_contract_violation(exact_read, expected) is None
    assert runtime.detect_omc_plan_shell_contract_violation("", expected) == {
        "kind": "missing_exact_read",
        "command_sha256": runtime._sha256_text(""),
    }
    assert runtime.detect_omc_plan_shell_contract_violation(
        unexpected_first, expected
    ) == {
        "kind": "unexpected_first_command",
        "command_sha256": runtime._sha256_text("ls -la"),
    }
    assert runtime.detect_omc_plan_shell_contract_violation(
        "\n".join((exact_read, additional_command)), expected
    ) == {
        "kind": "additional_shell_command",
        "command_sha256": runtime._sha256_text("git status --short"),
    }


def test_omc_plan_shell_contract_accepts_codex_shell_wrappers_only_for_exact_read():
    expected = "cat -- context/a.txt"

    def command_event(command):
        return json.dumps({
            "type": "item.completed",
            "item": {
                "id": "read-1",
                "type": "command_execution",
                "command": command,
            },
        })

    for wrapped in (
        "/bin/zsh -c 'cat -- context/a.txt'",
        "/bin/zsh -lc 'cat -- context/a.txt'",
    ):
        assert runtime.detect_omc_plan_shell_contract_violation(
            command_event(wrapped), expected
        ) is None

    compound = (
        "/bin/zsh -lc "
        "'cat -- context/a.txt; ls -la'"
    )
    trailing = (
        "/bin/zsh -lc "
        "'cat -- context/a.txt' ignored"
    )
    for invalid_wrapper in (compound, trailing):
        assert runtime.detect_omc_plan_shell_contract_violation(
            command_event(invalid_wrapper), expected
        ) == {
            "kind": "unexpected_first_command",
            "command_sha256": runtime._sha256_text(invalid_wrapper),
        }


def test_omc_activation_shell_contract_allows_zero_commands_only():
    unexpected_command = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "list-1",
            "type": "command_execution",
            "command": "ls -la",
        },
    })

    assert runtime.detect_omc_plan_shell_contract_violation("", "") is None
    assert runtime.detect_omc_plan_shell_contract_violation(
        unexpected_command, ""
    ) == {
        "kind": "unexpected_shell_command",
        "command_sha256": runtime._sha256_text("ls -la"),
    }


@pytest.mark.parametrize(
    "command",
    (
        "cat -- context/a.txt",
        "/bin/zsh -lc 'rg --files context'",
        "git -C /tmp/work status --short",
        "sed -n '1,20p' context/a.txt",
    ),
)
def test_shell_classifier_measures_read_only_commands(command):
    assert runtime.classify_shell_command(command) == {
        "kind": "read_only_shell_command",
        "command_sha256": runtime._sha256_text(command),
    }


@pytest.mark.parametrize(
    "command",
    (
        "rm -rf context",
        "python3 scripts/omc.py state status",
        "sed -i '' context/a.txt",
        "cat context/a.txt > copied.txt",
        "cat context/a.txt | wc -l",
        "cat $(touch /tmp/omc-shell-proof)",
        "cat `touch /tmp/omc-shell-proof`",
        "cat context/a.txt\nrm -rf context",
        "git -c core.fsmonitor=!touch\\ /tmp/omc-shell-proof status --short",
        "git --config-env=core.fsmonitor=OMC_HOOK status --short",
        "rg --pre /tmp/omc-preprocessor pattern context",
        "rg --hostname-bin /tmp/omc-hostname pattern context",
        "rg --search-zip pattern context",
        "rg -z pattern context",
        "sed -n '1w /tmp/omc-shell-proof' context/a.txt",
        "sed -n '1e touch /tmp/omc-shell-proof' context/a.txt",
        "sed -f /tmp/unsafe.sed context/a.txt",
        "git diff --output=diff.txt",
        "git diff --ext-diff",
        "git show --textconv HEAD",
    ),
)
def test_shell_classifier_blocks_write_compound_and_unknown_commands(command):
    assert runtime.classify_shell_command(command) == {
        "kind": "unsafe_shell_command",
        "command_sha256": runtime._sha256_text(command),
    }


def test_shell_assessment_preserves_reused_id_with_different_commands():
    def command_event(command):
        return json.dumps({
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "command_execution",
                "command": command,
            },
        })

    assert runtime.assess_shell_commands("\n".join((
        command_event("cat context/a.txt"),
        command_event("rm -rf context"),
    ))) == [
        {
            "kind": "read_only_shell_command",
            "command_sha256": runtime._sha256_text("cat context/a.txt"),
        },
        {
            "kind": "unsafe_shell_command",
            "command_sha256": runtime._sha256_text("rm -rf context"),
        },
    ]


def test_plan_round_trip_gate_allows_pwd_as_a_command_argument():
    search_event = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "search-1",
            "type": "command_execution",
            "command": 'rg "pwd" docs/commands.md',
        },
    })
    compound_pwd_event = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "pwd-1",
            "type": "command_execution",
            "command": "/bin/zsh -lc 'cd /tmp && pwd'",
        },
    })

    assert not runtime.contains_unnecessary_plan_round_trip(search_event)
    assert runtime.contains_unnecessary_plan_round_trip(compound_pwd_event)


def test_execute_provider_measures_unnecessary_read_only_round_trip(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "pwd-1",
                    "type": "command_execution",
                    "command": "/bin/zsh -lc pwd",
                },
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 30, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "secret-nonce",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    execution = runtime.execute_provider(
        provider_id="omc-plan",
        request="Plan this change",
        workspace=tmp_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=output_path,
        skill_sha256="c" * 64,
        expected_activation_receipt="secret-nonce",
        baseline_sentinel="unavailable",
        timeout_sec=180,
        failure_receipt_path=failure_path,
    )

    assert execution["usage"]["total_tokens"] == 35
    assert execution["efficiency_violations"] == [{
        "kind": "read_only_shell_command",
        "command_sha256": runtime._sha256_text("/bin/zsh -lc pwd"),
    }]
    assert not failure_path.exists()


def test_execute_provider_rejects_todo_list_round_trip(tmp_path, monkeypatch):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"
    private_events_path = tmp_path / "private" / "events.jsonl"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "item.started",
                "item": {
                    "id": "todo-1",
                    "type": "todo_list",
                    "items": [{"text": "Plan the work", "completed": False}],
                },
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 30, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "secret-nonce",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="todo_list_round_trip"):
        runtime.execute_provider(
            provider_id="omc-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=output_path,
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            failure_receipt_path=failure_path,
            private_events_path=private_events_path,
        )

    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["reason_code"] == "todo_list_round_trip"
    assert failure["offending_command_sha256"] == runtime._sha256_text("todo-1")
    assert "Plan the work" not in failure_path.read_text(encoding="utf-8")
    assert private_events_path.read_text(encoding="utf-8") == Completed.stdout


def test_execute_provider_measures_read_only_shell_with_embedded_context(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"
    private_events_path = tmp_path / "private" / "events.jsonl"
    expected_read = "cat -- context/a.txt"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "read-1",
                    "type": "command_execution",
                    "command": expected_read,
                },
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 30, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "secret-nonce",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    execution = runtime.execute_provider(
        provider_id="omc-plan",
        request="Plan this change",
        workspace=tmp_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=output_path,
        skill_sha256="c" * 64,
        expected_activation_receipt="secret-nonce",
        baseline_sentinel="unavailable",
        timeout_sec=180,
        failure_receipt_path=failure_path,
        private_events_path=private_events_path,
        context_paths=("context/a.txt",),
        context_files={"context/a.txt": "frozen\n"},
    )

    assert execution["efficiency_violations"] == [{
        "kind": "read_only_shell_command",
        "command_sha256": runtime._sha256_text(expected_read),
    }]
    assert execution["events_jsonl_sha256"] == runtime._sha256_text(
        Completed.stdout
    )
    assert private_events_path.read_text(encoding="utf-8") == Completed.stdout
    assert not failure_path.exists()


def test_execute_provider_rejects_unsafe_shell_with_private_evidence(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"
    private_events_path = tmp_path / "private" / "events.jsonl"
    unsafe_command = "rm -rf context"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "unsafe-1",
                    "type": "command_execution",
                    "command": unsafe_command,
                },
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 30, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "secret-nonce",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="unsafe_shell_command"):
        runtime.execute_provider(
            provider_id="omc-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=output_path,
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            failure_receipt_path=failure_path,
            private_events_path=private_events_path,
            context_paths=("context/a.txt",),
            context_files={"context/a.txt": "frozen\n"},
        )

    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["reason_code"] == "unsafe_shell_command"
    assert failure["offending_command_kind"] == "unsafe_shell_command"
    assert failure["offending_command_sha256"] == runtime._sha256_text(
        unsafe_command
    )
    assert unsafe_command not in failure_path.read_text(encoding="utf-8")
    assert private_events_path.read_text(encoding="utf-8") == Completed.stdout


def test_execute_provider_measures_redundant_read_only_context_round_trip(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "read-1",
                    "type": "command_execution",
                    "command": "cat context/a.txt",
                },
            }),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "read-2",
                    "type": "command_execution",
                    "command": "cat context/b.txt",
                },
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 30, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "unavailable",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    execution = runtime.execute_provider(
        provider_id="baseline-plan",
        request="Plan this change",
        workspace=tmp_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=output_path,
        skill_sha256="c" * 64,
        expected_activation_receipt="secret-nonce",
        baseline_sentinel="unavailable",
        timeout_sec=180,
        failure_receipt_path=failure_path,
        context_paths=("context/a.txt", "context/b.txt"),
        context_files={
            "context/a.txt": "a\n",
            "context/b.txt": "b\n",
        },
    )

    assert execution["usage"]["total_tokens"] == 35
    assert execution["efficiency_violations"] == [
        {
            "kind": "read_only_shell_command",
            "command_sha256": runtime._sha256_text("cat context/a.txt"),
        },
        {
            "kind": "read_only_shell_command",
            "command_sha256": runtime._sha256_text("cat context/b.txt"),
        },
    ]
    assert not failure_path.exists()


def test_execute_provider_retries_one_activation_miss_and_counts_all_usage(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    receipts = iter(["unavailable", "secret-nonce"])
    usages = iter([(10, 5), (20, 7)])
    calls = 0

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, input_tokens, output_tokens):
            self.stdout = "\n".join([
                json.dumps({
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                }),
            ])

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": next(receipts),
        }), encoding="utf-8")
        return Completed(*next(usages))

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    result = runtime.execute_provider(
        provider_id="omc-plan",
        request="Plan this change",
        workspace=tmp_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=output_path,
        skill_sha256="c" * 64,
        expected_activation_receipt="secret-nonce",
        baseline_sentinel="unavailable",
        timeout_sec=180,
        max_activation_attempts=2,
    )

    assert calls == 2
    assert result["activation"]["attempt_count"] == 2
    assert result["activation"]["retry_count"] == 1
    assert result["usage"] == {
        "status": "observed",
        "input_tokens": 30,
        "output_tokens": 12,
        "total_tokens": 42,
    }


def test_execute_provider_reuses_embedded_context_without_tools_on_activation_retry(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    receipts = iter(["unavailable", "secret-nonce"])
    calls = 0

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, attempt):
            self.stdout = "\n".join([
                json.dumps({
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }),
            ])

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": next(receipts),
        }), encoding="utf-8")
        return Completed(calls)

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    result = runtime.execute_provider(
        provider_id="omc-plan",
        request="Plan this change",
        workspace=tmp_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=output_path,
        skill_sha256="c" * 64,
        expected_activation_receipt="secret-nonce",
        baseline_sentinel="unavailable",
        timeout_sec=180,
        max_activation_attempts=2,
        context_paths=("context/a.txt",),
        context_files={"context/a.txt": "frozen\n"},
    )

    assert calls == 2
    assert result["activation"]["retry_count"] == 1
    first_attempt = tmp_path / "output.activation-miss-01.json"
    assert json.loads(first_attempt.read_text(encoding="utf-8"))[
        "runtime_activation_receipt"
    ] == "unavailable"


def test_execute_provider_rejects_retry_unsafe_shell_with_reused_execution_id(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"
    receipts = iter(("unavailable", "secret-nonce"))
    commands = iter(("cat context/a.txt", "rm -rf context"))
    calls = 0

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, command):
            self.stdout = "\n".join((
                json.dumps({
                    "type": "item.completed",
                    "item": {
                        "id": "item-1",
                        "type": "command_execution",
                        "command": command,
                    },
                }),
                json.dumps({
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }),
            ))

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": next(receipts),
        }), encoding="utf-8")
        return Completed(next(commands))

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="unsafe_shell_command"):
        runtime.execute_provider(
            provider_id="omc-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=output_path,
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            max_activation_attempts=2,
            failure_receipt_path=failure_path,
            context_paths=("context/a.txt",),
            context_files={"context/a.txt": "frozen\n"},
        )

    assert calls == 2
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["reason_code"] == "unsafe_shell_command"
    assert failure["usage"]["total_tokens"] == 30


def test_execute_provider_rejects_sync_with_embedded_context(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"
    receipts = iter(["unavailable", "secret-nonce"])
    calls = 0

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, execution_id):
            self.stdout = "\n".join([
                json.dumps({
                    "type": "item.completed",
                    "item": {
                        "id": execution_id,
                        "type": "command_execution",
                        "command": (
                            "python3 scripts/omc.py state sync-session --target ."
                        ),
                    },
                }),
                json.dumps({
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }),
            ])

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": next(receipts),
        }), encoding="utf-8")
        return Completed(f"sync-{calls}")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="unsafe_shell_command"):
        runtime.execute_provider(
            provider_id="omc-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=output_path,
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            max_activation_attempts=2,
            failure_receipt_path=failure_path,
            context_paths=("scripts/omc.py",),
            context_files={"scripts/omc.py": "# frozen\n"},
        )

    assert calls == 1
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["reason_code"] == "unsafe_shell_command"
    assert failure["offending_command_kind"] == "unsafe_shell_command"


def test_execute_provider_blocks_after_two_activation_misses_with_usage_receipt(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "output.json"
    failure_path = tmp_path / "failure.json"
    calls = 0

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "unavailable",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="activation_receipt_mismatch"):
        runtime.execute_provider(
            provider_id="omc-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=output_path,
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            max_activation_attempts=2,
            failure_receipt_path=failure_path,
        )

    assert calls == 2
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["reason_code"] == "activation_receipt_mismatch"
    assert failure["attempt_count"] == 2
    assert failure["usage"] == {
        "status": "observed",
        "input_tokens": 20,
        "output_tokens": 10,
        "total_tokens": 30,
    }


def test_execute_provider_persists_timeout_failure_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "failure.json"

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        runtime.execute_provider(
            provider_id="baseline-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=tmp_path / "output.json",
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            failure_receipt_path=receipt,
        )
    assert json.loads(receipt.read_text(encoding="utf-8"))["reason_code"] == "provider_timeout"


def test_execute_provider_persists_invalid_output_failure_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "failure.json"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: Completed())
    with pytest.raises(RuntimeError, match="output"):
        runtime.execute_provider(
            provider_id="baseline-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=tmp_path / "missing.json",
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            failure_receipt_path=receipt,
        )
    assert json.loads(receipt.read_text(encoding="utf-8"))["reason_code"] == "provider_output_invalid"


def test_variability_gate_blocks_large_repeat_delta():
    stable = {
        "baseline-plan": [{"recall": 0.9, "executable": 0.8}, {"recall": 0.85, "executable": 0.82}],
        "omc-plan": [{"recall": 0.9, "executable": 0.9}, {"recall": 0.88, "executable": 0.85}],
    }
    assert runtime.evaluate_variability(stable, max_delta=0.10)["status"] == "pass"
    unstable = deepcopy(stable)
    unstable["omc-plan"][1]["recall"] = 0.60
    assert runtime.evaluate_variability(unstable, max_delta=0.10)["status"] == "blocked"

    invalid = deepcopy(stable)
    invalid["omc-plan"][1]["recall"] = math.nan
    result = runtime.evaluate_variability(invalid, max_delta=0.10)
    assert result["status"] == "blocked"
    assert "omc-plan:recall:invalid" in result["failed_metrics"]


def test_replacement_requires_all_quality_cost_and_provenance_gates():
    result = runtime.decide_replacement(_metrics(), _protocol()["acceptance"])
    assert result["decision"] == "PROVISIONALLY_REPLACEABLE"

    failed = _metrics()
    failed["omc-plan"]["unsupported_assumption_count"] = 2
    result = runtime.decide_replacement(failed, _protocol()["acceptance"])
    assert result["decision"] == "NOT_PROVEN"
    assert "unsupported_assumptions" in result["failed_gates"]


def test_runtime_corpus_rejects_provider_forbidden_case_fields():
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])

    for forbidden_field in (
        "followup_commit",
        "gold",
        "expected_files",
        "private_mapping",
    ):
        invalid = deepcopy(cases)
        invalid[0][forbidden_field] = "must-not-reach-provider"
        with pytest.raises(ValueError, match="runtime case fields"):
            runtime.validate_runtime_corpus(
                invalid,
                gold,
                expected_count=10,
                trusted_signer_public_keys=trusted,
            )


def test_provider_input_envelope_binds_request_context_and_skill():
    case = _case()
    baseline = runtime.build_provider_input_envelope(
        "baseline-plan",
        case,
        allowed_workspace_delta=".agents/skills/omc-plan/SKILL.md",
        instrumented_skill_sha256="a" * 64,
    )
    omc = runtime.build_provider_input_envelope(
        "omc-plan",
        case,
        allowed_workspace_delta=".agents/skills/omc-plan/SKILL.md",
        instrumented_skill_sha256="a" * 64,
    )

    assert baseline["provider_input_sha256"] != omc["provider_input_sha256"]
    changed = deepcopy(case)
    changed["request"] += " Include timeout handling."
    changed_envelope = runtime.build_provider_input_envelope(
        "baseline-plan",
        changed,
        allowed_workspace_delta=".agents/skills/omc-plan/SKILL.md",
        instrumented_skill_sha256="a" * 64,
    )
    assert baseline["provider_input_sha256"] != changed_envelope["provider_input_sha256"]


def test_provider_workspace_manifest_must_match_input_envelope():
    case = _case()
    case["context_files"] = {
        "src/A.py": "first",
        "src/a.py": "second",
    }
    provider_input = runtime.build_provider_input_envelope(
        "baseline-plan",
        case,
        allowed_workspace_delta=".agents/skills/omc-plan/SKILL.md",
        instrumented_skill_sha256="a" * 64,
    )
    actual_manifest = {"src/A.py": runtime._sha256_text("second")}

    with pytest.raises(ValueError, match="workspace_input_mismatch"):
        runtime.validate_provider_workspace_manifest(actual_manifest, provider_input)


def test_single_batch_never_claims_final_replacement():
    result = runtime.decide_replacement(_metrics(), _protocol()["acceptance"])

    assert result == {
        "decision": "PROVISIONALLY_REPLACEABLE",
        "reason_code": "single_batch_gates_passed",
        "failed_gates": [],
    }


def test_replacement_rejects_missing_or_posthoc_evaluation_scope():
    missing_scope = _metrics()
    del missing_scope["evaluation_scope"]
    result = runtime.decide_replacement(
        missing_scope, _protocol()["acceptance"]
    )
    assert result == {
        "decision": "INVALID_RUN",
        "reason_code": "evaluation_scope_invalid",
        "failed_gates": ["evaluation_scope"],
    }

    diagnostic = _metrics()
    diagnostic["evaluation_scope"] = "diagnostic_posthoc_gold_amendment"
    result = runtime.decide_replacement(diagnostic, _protocol()["acceptance"])
    assert result == {
        "decision": "DIAGNOSTIC_ONLY",
        "reason_code": "posthoc_gold_amendment",
        "failed_gates": ["replacement_claim_eligibility"],
    }


def test_replacement_rejects_unjustified_token_increase_and_invalid_run():
    metrics = _metrics()
    metrics["omc-plan"]["weighted_requirement_recall"] = 0.90
    metrics["omc-plan"]["executable_task_rate"] = 0.85
    metrics["omc-plan"]["total_tokens"] = 10501
    result = runtime.decide_replacement(metrics, _protocol()["acceptance"])
    assert result["decision"] == "NOT_PROVEN"
    assert "total_tokens" in result["failed_gates"]

    metrics = _metrics()
    metrics["provenance_complete_count"] = 9
    result = runtime.decide_replacement(metrics, _protocol()["acceptance"])
    assert result["decision"] == "INVALID_RUN"


def test_replacement_rejects_non_finite_or_out_of_range_metrics():
    metrics = _metrics()
    metrics["omc-plan"]["weighted_requirement_recall"] = math.nan
    result = runtime.decide_replacement(metrics, _protocol()["acceptance"])
    assert result["decision"] == "INVALID_RUN"
    assert result["reason_code"] == "provider_metrics_invalid"


def test_superiority_batch_requires_primary_gain_and_positive_confidence_bound():
    protocol = _protocol()
    metrics = _metrics()
    metrics["paired_primary_deltas"] = [0.05] * 10
    result = runtime.decide_superiority_batch(
        metrics, protocol["acceptance"], protocol["superiority"]
    )
    assert result["decision"] == "SUPERIOR_CANDIDATE"
    assert result["primary_gain"] == pytest.approx(0.05)
    assert result["confidence_lower_bound"] > 0

    tied = _metrics()
    tied["omc-plan"]["weighted_requirement_recall"] = 0.90
    tied["omc-plan"]["executable_task_rate"] = 0.85
    tied["omc-plan"]["total_tokens"] = 10000
    tied["paired_primary_deltas"] = [0.0] * 10
    result = runtime.decide_superiority_batch(
        tied, protocol["acceptance"], protocol["superiority"]
    )
    assert result["decision"] == "PROVISIONALLY_REPLACEABLE"

    unstable = _metrics()
    unstable["paired_primary_deltas"] = [0.20, -0.10] * 5
    result = runtime.decide_superiority_batch(
        unstable, protocol["acceptance"], protocol["superiority"]
    )
    assert result["decision"] == "PROVISIONALLY_REPLACEABLE"
    assert result["confidence_lower_bound"] <= 0

    inconsistent = _metrics()
    inconsistent["paired_primary_deltas"] = [0.10] * 10
    result = runtime.decide_superiority_batch(
        inconsistent, protocol["acceptance"], protocol["superiority"]
    )
    assert result["decision"] == "INVALID_RUN"
    assert result["reason_code"] == "paired_primary_metrics_mismatch"


def test_superiority_requires_two_independent_candidate_batches():
    private_key, public_key = _signer()

    def signed_report(
        batch_id,
        artifact_hash,
        decision="SUPERIOR_CANDIDATE",
        *,
        execution_hash=None,
        model="gpt-test",
        reasoning_effort="low",
    ):
        report = {
            "schema_version": 1,
            "batch_id": batch_id,
            "metrics": {},
            "decision": {"decision": decision, "batch_id": batch_id},
            "sealed_provider_batch_sha256": artifact_hash,
            "provider_execution_evidence_sha256": execution_hash or artifact_hash,
                "execution_config": {
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                },
                "claim_scope": runtime.CONFIRMATORY_CLAIM_SCOPE,
                "provenance": {
                "protocol_sha256": "1" * 64,
                "corpus_sha256": "2" * 64,
                "gold_sha256": "3" * 64,
                    "skill_sha256": "4" * 64,
                    "confirmatory_manifest_sha256": "5" * 64,
            },
        }
        report["final_report_attestation"] = runtime.build_final_report_attestation(
            report, private_key=private_key, signer_public_key=public_key
        )
        return report

    candidate = signed_report("candidate-01", "a" * 64)
    confirmation = signed_report("confirmation-01", "b" * 64)
    result = runtime.decide_confirmed_superiority(
        [candidate, confirmation],
        required_batches=2,
        trusted_signer_public_key=public_key,
    )
    assert result["decision"] == "BENCHMARK_SUPERIOR"

    replaceable = signed_report(
        "confirmation-01", "b" * 64, "PROVISIONALLY_REPLACEABLE"
    )
    result = runtime.decide_confirmed_superiority(
        [candidate, replaceable],
        required_batches=2,
        trusted_signer_public_key=public_key,
    )
    assert result["decision"] == "REPLACEABLE"

    copied_artifact = signed_report("confirmation-01", "a" * 64)
    with pytest.raises(ValueError, match="artifact"):
        runtime.decide_confirmed_superiority(
            [candidate, copied_artifact],
            required_batches=2,
            trusted_signer_public_key=public_key,
        )

    reused_execution = signed_report(
        "confirmation-01", "b" * 64, execution_hash="a" * 64
    )
    with pytest.raises(ValueError, match="provider execution evidence"):
        runtime.decide_confirmed_superiority(
            [candidate, reused_execution],
            required_batches=2,
            trusted_signer_public_key=public_key,
        )

    mismatched_model = signed_report(
        "confirmation-01", "b" * 64, model="gpt-other"
    )
    with pytest.raises(ValueError, match="execution config"):
        runtime.decide_confirmed_superiority(
            [candidate, mismatched_model],
            required_batches=2,
            trusted_signer_public_key=public_key,
        )

    mismatched_protocol = signed_report("confirmation-01", "b" * 64)
    mismatched_protocol["provenance"]["protocol_sha256"] = "9" * 64
    mismatched_protocol["final_report_attestation"] = runtime.build_final_report_attestation(
        mismatched_protocol, private_key=private_key, signer_public_key=public_key
    )
    with pytest.raises(ValueError, match="frozen inputs"):
        runtime.decide_confirmed_superiority(
            [candidate, mismatched_protocol],
            required_batches=2,
            trusted_signer_public_key=public_key,
        )

    tampered = signed_report("confirmation-01", "b" * 64)
    tampered["decision"]["decision"] = "REPLACEABLE"
    with pytest.raises(ValueError, match="signature"):
        runtime.decide_confirmed_superiority(
            [candidate, tampered],
            required_batches=2,
            trusted_signer_public_key=public_key,
        )


def test_main_confirms_superiority_from_two_signed_reports(
    tmp_path, monkeypatch, capsys
):
    private_key, public_key = _signer()

    def write_report(batch_id, marker):
        report = {
            "schema_version": 1,
            "batch_id": batch_id,
            "metrics": {},
            "decision": {
                "decision": "SUPERIOR_CANDIDATE",
                "batch_id": batch_id,
            },
            "sealed_provider_batch_sha256": marker * 64,
            "provider_execution_evidence_sha256": marker * 64,
                "execution_config": {
                    "model": "gpt-test",
                    "reasoning_effort": "low",
                },
                "claim_scope": runtime.CONFIRMATORY_CLAIM_SCOPE,
                "provenance": {
                "protocol_sha256": "1" * 64,
                "corpus_sha256": "2" * 64,
                "gold_sha256": "3" * 64,
                    "skill_sha256": "4" * 64,
                    "confirmatory_manifest_sha256": "5" * 64,
            },
        }
        report["final_report_attestation"] = runtime.build_final_report_attestation(
            report, private_key=private_key, signer_public_key=public_key
        )
        path = tmp_path / f"{batch_id}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    candidate = write_report("candidate-01", "a")
    confirmation = write_report("confirmation-01", "b")
    output = tmp_path / "confirmed.json"
    monkeypatch.setattr(sys, "argv", [
        "omc_plan_runtime_pilot.py",
        "confirm-superiority",
        str(candidate),
        str(confirmation),
        "--trusted-adjudicator-public-key",
        public_key,
        "--output",
        str(output),
    ])

    assert runtime.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "BENCHMARK_SUPERIOR"
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_runtime_metrics_are_derived_from_scores_and_observed_usage():
    scores = {
        provider_id: [{
            "weighted_coverage": 0.9,
            "critical_omissions": [],
            "executable_step_rate": 0.8,
            "unsupported_assumptions": [],
            "bloat_ratio": 0.1,
        }]
        for provider_id in runtime.PROVIDERS
    }
    executions = [
        {
            "provider_id": provider_id,
            "case_id": "observed-01",
            "usage": {
                "status": "observed",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        }
        for provider_id in runtime.PROVIDERS
    ]
    metrics = runtime.build_runtime_metrics(
        scores,
        executions,
        expected_case_count=1,
        evaluation_scope="confirmatory",
    )
    assert metrics["evaluation_scope"] == "confirmatory"
    assert metrics["token_measurement_status"] == "observed"
    assert metrics["omc-plan"]["task_evidence_accuracy"] == 0.9
    assert metrics["omc-plan"]["total_tokens"] == 15
    assert metrics["paired_primary_deltas"] == [0.0]


def test_runtime_metrics_reject_usage_total_mismatch():
    scores = {
        provider_id: [{
            "weighted_coverage": 0.9,
            "critical_omissions": [],
            "executable_step_rate": 0.8,
            "unsupported_assumptions": [],
            "bloat_ratio": 0.1,
        }]
        for provider_id in runtime.PROVIDERS
    }
    executions = [
        {
            "provider_id": provider_id,
            "case_id": "observed-01",
            "usage": {
                "status": "observed",
                "input_tokens": 100,
                "output_tokens": 100,
                "total_tokens": 1,
            },
        }
        for provider_id in runtime.PROVIDERS
    ]
    with pytest.raises(ValueError, match="usage integrity mismatch"):
        runtime.build_runtime_metrics(
            scores,
            executions,
            expected_case_count=1,
            evaluation_scope="confirmatory",
        )


def test_assess_cli_can_call_functions_declared_after_main(tmp_path):
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps(_metrics()), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(runtime.__file__)),
            "assess",
            str(Path(runtime.__file__).parent / "fixtures/omc_plan_runtime_protocol.json"),
            str(metrics),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["decision"] == "PROVISIONALLY_REPLACEABLE"


def test_blind_batch_contains_ten_complete_pairs_without_provider_ids():
    cases = [_case(index) for index in range(1, 11)]
    executions = []
    for index, case in enumerate(cases, start=1):
        for provider_id in ("baseline-plan", "omc-plan"):
            executions.append({
                "case_id": case["case_id"],
                "provider_id": provider_id,
                "plan_execution_id": f"exec-{index}-{provider_id}",
                "plan": {"requirements": [], "tasks": [], "dependency_edges": [], "assumptions": []},
                "raw_output": "{}",
            })
    sessions, private_mapping = runtime.build_runtime_blind_batch(
        executions, batch_id="runtime-batch", session_count=5
    )
    assert len(sessions) == 5
    assert sum(len(session["items"]) for session in sessions) == 20
    assert "provider_id" not in json.dumps(sessions)
    assert len(private_mapping) == 20

    with pytest.raises(ValueError, match="exactly 5 sessions"):
        runtime.build_runtime_blind_batch(
            executions, batch_id="runtime-batch", session_count=2
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda gold: gold[0]["required_items"][0].update({"description": ""}), "description"),
        (lambda gold: gold[0]["required_items"][0].update({"weight": 0}), "weight"),
        (lambda gold: gold[0].update({"excluded_scope": [1]}), "excluded_scope"),
        (lambda gold: gold[0].update({"allowed_assumptions": [""]}), "allowed_assumptions"),
        (lambda gold: gold[0].update({"dependency_edges": [{"before": "REQ-1"}]}), "dependency_edges"),
    ],
)
def test_corpus_rejects_incomplete_gold_schema(mutate, message):
    cases = [_case(index) for index in range(1, 11)]
    gold_items = [_gold(index) for index in range(1, 11)]
    for item in gold_items:
        item["required_items"][0]["description"] = "Required behavior"
    mutate(gold_items)
    gold, trusted = _signed_gold(cases, gold_items)
    with pytest.raises(ValueError, match=message):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_runtime_batch_executes_ten_pairs_and_persists_blind_artifacts(tmp_path, monkeypatch):
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# OMC Plan\n", encoding="utf-8")
    skill_hash = runtime._sha256_text(skill_path.read_text(encoding="utf-8"))
    confirmatory_manifest, confirmatory_signer = _signed_confirmatory_manifest(
        cases, gold, skill_sha256=skill_hash
    )
    calls = []

    def fake_probe(**kwargs):
        return {
            "status": "pass",
            "scope": "non_scored_activation_probe",
            "skill_sha256": skill_hash,
            "model": kwargs["model"],
            "reasoning_effort": kwargs["reasoning_effort"],
            "executions": {
                provider_id: {
                    "activation": {"attempt_count": 1},
                    "usage": {
                        "status": "observed",
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                }
                for provider_id in runtime.PROVIDERS
            },
        }

    def fake_execute_provider(**kwargs):
        calls.append((kwargs["provider_id"], kwargs["request"]))
        plan = {
            "requirements_covered": ["REQ-1"],
            "scope_items": [],
            "dependency_edges": [],
            "tasks": [{
                "id": "T-1",
                "target": "src/service.py",
                "action": "Implement bounded retry handling",
                "verify": "Run the service tests",
                "supports": ["REQ-1"],
            }],
            "assumptions": [],
            "decisions_required": [],
        }
        return {
            "provider_id": kwargs["provider_id"],
            "plan": plan,
            "raw_output": json.dumps(plan),
            "events_jsonl": "",
            "activation": {"status": "observed", "skill_sha256": skill_hash},
            "usage": {"status": "observed", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "command_sha256": "a" * 64,
            "prompt_sha256": runtime._sha256_text(
                runtime.build_provider_prompt(
                    kwargs["provider_id"],
                    kwargs["request"],
                    context_paths=kwargs["context_paths"],
                    context_files=kwargs["context_files"],
                )
            ),
        }

    monkeypatch.setattr(runtime, "run_activation_probe", fake_probe)
    monkeypatch.setattr(runtime, "execute_provider", fake_execute_provider)
    runtime_signer, runtime_signer_public_key = _signer()
    result = runtime.run_runtime_batch(
        protocol=_protocol(),
        cases=cases,
        gold_document=gold,
        trusted_signer_public_keys=trusted,
        confirmatory_manifest=confirmatory_manifest,
        trusted_prior_fingerprints=confirmatory_manifest["prior_fingerprints"],
        trusted_confirmatory_signer_public_keys={confirmatory_signer},
        skill_path=skill_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        output_schema=Path(runtime.__file__).parent / "fixtures/omc_plan_output_schema.json",
        artifact_root=tmp_path / "batch",
        batch_id="runtime-batch",
        session_count=5,
        runtime_signer_private_key=runtime_signer,
        runtime_signer_public_key=runtime_signer_public_key,
    )
    assert len(calls) == 20
    assert result["activation_probe"]["status"] == "pass"
    assert result["provider_batch"]["evaluation_scope"] == "confirmatory"
    assert len(result["provider_batch"]["executions"]) == 20
    assert len(result["blind_sessions"]) == 5
    assert (tmp_path / "batch/provider-batch.json").is_file()
    assert (tmp_path / "batch/private-mapping.json").is_file()
    assert (tmp_path / "batch/blind-sessions.json").is_file()
    assert result["provider_batch"]["runtime_attestation"]["signer_public_key"] == runtime_signer_public_key
    assert all(
        runtime._is_sha256(execution.get("provider_input_sha256"))
        for execution in result["provider_batch"]["executions"]
    )
    assert all(
        "events_jsonl" not in execution
        and runtime._is_sha256(execution.get("events_jsonl_sha256"))
        for execution in result["provider_batch"]["executions"]
    )
    assert all(
        "events_jsonl" not in execution
        and runtime._is_sha256(execution.get("events_jsonl_sha256"))
        for execution in result["provider_batch"]["activation_probe"][
            "executions"
        ].values()
    )

    tampered_batch = deepcopy(result["provider_batch"])
    tampered_batch["executions"][0]["provider_input_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="runtime execution provenance mismatch"):
        runtime.validate_runtime_provenance(
            tampered_batch,
            protocol=_protocol(),
            cases=cases,
            gold_document=gold,
            confirmatory_manifest=confirmatory_manifest,
        )

    adjudicator_key, adjudicator_public_key = _signer()
    report = runtime.finalize_runtime_batch(
        protocol=_protocol(),
        cases=cases,
        gold_document=gold,
        trusted_signer_public_keys=trusted,
        confirmatory_manifest=confirmatory_manifest,
        trusted_prior_fingerprints=confirmatory_manifest["prior_fingerprints"],
        trusted_confirmatory_signer_public_keys={confirmatory_signer},
        provider_batch=result["provider_batch"],
        blind_sessions=json.loads(
            (tmp_path / "batch/blind-sessions.json").read_text(encoding="utf-8")
        ),
        private_mapping=result["private_mapping"],
        adjudication_results=_adjudications(result["blind_sessions"]),
        adjudicator_private_key=adjudicator_key,
        trusted_adjudicator_public_key=adjudicator_public_key,
        adjudicator="independent-test-adjudicator",
        artifact_root=tmp_path / "finalized-run-output",
        trusted_runtime_signer_public_key=runtime_signer_public_key,
    )
    assert report["decision"]["decision"] == "PROVISIONALLY_REPLACEABLE"


def test_restore_blind_session_plan_labels_uses_private_mapping():
    sanitized_session = {
        "schema_version": 2,
        "session_id": "session-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": "case-1",
            "plan": {"assumptions": ["[provider] assumption"]},
        }],
    }
    executions = [{
        "provider_id": "omc-plan",
        "case_id": "case-1",
        "plan": {"assumptions": ["omc-plan assumption"]},
    }]
    mapping = {
        "blind-1": {"provider_id": "omc-plan", "case_id": "case-1"}
    }

    restored = restore_blind_session_plan_labels(
        sanitized_session,
        executions=executions,
        private_mapping=mapping,
    )

    assert restored["items"][0]["plan"] == executions[0]["plan"]
    assert sanitized_session["items"][0]["plan"]["assumptions"] == [
        "[provider] assumption"
    ]


def test_diagnostic_rejudge_preserves_provider_evidence_and_blocks_replacement(tmp_path):
    cases = [_case(index) for index in range(1, 11)]
    original_gold, original_trusted = _signed_gold(
        cases, [_gold(index) for index in range(1, 11)]
    )
    amended_items = deepcopy(original_gold["cases"])
    amended_items[0]["required_items"][0]["description"] = "Amended behavior"
    amended_gold, amended_trusted = _signed_gold(cases, amended_items)
    executions = []
    instrumented_skill_sha256 = "d" * 64
    for index, case in enumerate(cases, start=1):
        for provider_id in runtime.PROVIDERS:
            plan = {
                "requirements_covered": ["REQ-1"],
                "scope_items": [],
                "dependency_edges": [],
                "tasks": [{
                    "id": "T-1",
                    "target": "src/service.py",
                    "action": "Implement the required behavior",
                    "verify": "Run tests",
                    "supports": ["REQ-1"],
                }],
                "assumptions": [],
                "decisions_required": [],
            }
            executions.append({
                "case_id": case["case_id"],
                "provider_id": provider_id,
                "plan_execution_id": f"original:{index}:{provider_id}",
                "plan": plan,
                "raw_output": json.dumps(plan),
                "events_jsonl": "",
                "activation": {"status": "observed", "skill_sha256": "b" * 64},
                "usage": {
                    "status": "observed",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
                "command_sha256": "c" * 64,
                "prompt_sha256": runtime._sha256_text(
                    runtime.build_provider_prompt(
                        provider_id,
                        case["request"],
                        context_paths=tuple(case["context_files"]),
                        context_files=case["context_files"],
                    )
                ),
                "provider_input_sha256": runtime.build_provider_input_envelope(
                    provider_id,
                    case,
                    allowed_workspace_delta=_protocol()["execution"][
                        "allowed_workspace_delta"
                    ],
                    instrumented_skill_sha256=instrumented_skill_sha256,
                )["provider_input_sha256"],
            })
    original_sessions, original_mapping = runtime.build_runtime_blind_batch(
        executions,
        batch_id="original-batch",
        session_count=5,
        gold_document=original_gold,
    )
    runtime_signer, runtime_public_key = _signer()
    activation_probe = {
        "status": "pass",
        "skill_sha256": "b" * 64,
    }
    original_batch = {
        "schema_version": 1,
        "batch_id": "original-batch",
        "protocol_sha256": runtime.canonical_digest(_protocol()),
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": original_gold["gold_sha256"],
        "skill_sha256": "b" * 64,
        "instrumented_skill_sha256": instrumented_skill_sha256,
        "activation_probe_sha256": runtime.canonical_digest(activation_probe),
        "activation_probe": activation_probe,
        "model": "gpt-test",
        "reasoning_effort": "low",
        "executions": executions,
    }
    original_batch["runtime_attestation"] = runtime.build_runtime_attestation(
        original_batch,
        original_sessions,
        original_mapping,
        private_key=runtime_signer,
        signer_public_key=runtime_public_key,
    )
    original_evidence = runtime.provider_execution_evidence_digest(original_batch)

    result = runtime.build_diagnostic_rejudge_batch(
        protocol=_protocol(),
        cases=cases,
        original_gold_document=original_gold,
        amended_gold_document=amended_gold,
        trusted_signer_public_keys=original_trusted | amended_trusted,
        original_provider_batch=original_batch,
        original_blind_sessions=original_sessions,
        original_private_mapping=original_mapping,
        trusted_original_runtime_signer_public_key=runtime_public_key,
        runtime_signer_private_key=runtime_signer,
        runtime_signer_public_key=runtime_public_key,
        batch_id="diagnostic-rejudge",
        artifact_root=tmp_path / "diagnostic",
    )

    diagnostic_batch = result["provider_batch"]
    assert diagnostic_batch["evaluation_scope"] == "diagnostic_posthoc_gold_amendment"
    assert diagnostic_batch["gold_sha256"] == amended_gold["gold_sha256"]
    assert runtime.provider_execution_evidence_digest(diagnostic_batch) == original_evidence
    assert diagnostic_batch["source_runtime_attestation_sha256"] == runtime.canonical_digest(
        original_batch["runtime_attestation"]
    )
    runtime.verify_runtime_attestation(
        diagnostic_batch,
        result["blind_sessions"],
        result["private_mapping"],
        trusted_public_key=runtime_public_key,
    )
    assert runtime.decide_replacement(
        {**_metrics(), "evaluation_scope": diagnostic_batch["evaluation_scope"]},
        _protocol()["acceptance"],
    )["decision"] == "DIAGNOSTIC_ONLY"
    assert (tmp_path / "diagnostic/provider-batch.json").is_file()

    occupied_root = tmp_path / "occupied-diagnostic"
    occupied_root.mkdir()
    (occupied_root / "runtime-final-report.json").write_text(
        "stale report", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="artifact root must be empty"):
        runtime.build_diagnostic_rejudge_batch(
            protocol=_protocol(),
            cases=cases,
            original_gold_document=original_gold,
            amended_gold_document=amended_gold,
            trusted_signer_public_keys=original_trusted | amended_trusted,
            original_provider_batch=original_batch,
            original_blind_sessions=original_sessions,
            original_private_mapping=original_mapping,
            trusted_original_runtime_signer_public_key=runtime_public_key,
            runtime_signer_private_key=runtime_signer,
            runtime_signer_public_key=runtime_public_key,
            batch_id="diagnostic-rejudge-rerun",
            artifact_root=occupied_root,
        )


def test_diagnostic_rejudge_rejects_tampered_original_evidence(tmp_path):
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    signer, public_key = _signer()
    sessions, mapping = runtime.build_runtime_blind_batch(
        [], batch_id="original", session_count=5, gold_document=gold
    )
    original_batch = {
        "schema_version": 1,
        "batch_id": "original",
        "protocol_sha256": runtime.canonical_digest(_protocol()),
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": gold["gold_sha256"],
        "skill_sha256": "b" * 64,
        "activation_probe": {"status": "pass", "skill_sha256": "b" * 64},
        "executions": [],
    }
    original_batch["activation_probe_sha256"] = runtime.canonical_digest(
        original_batch["activation_probe"]
    )
    original_batch["runtime_attestation"] = runtime.build_runtime_attestation(
        original_batch,
        sessions,
        mapping,
        private_key=signer,
        signer_public_key=public_key,
    )
    original_batch["executions"] = [{"tampered": True}]

    with pytest.raises(ValueError, match="runtime attestation mismatch"):
        runtime.build_diagnostic_rejudge_batch(
            protocol=_protocol(),
            cases=cases,
            original_gold_document=gold,
            amended_gold_document=gold,
            trusted_signer_public_keys=trusted,
            original_provider_batch=original_batch,
            original_blind_sessions=sessions,
            original_private_mapping=mapping,
            trusted_original_runtime_signer_public_key=public_key,
            runtime_signer_private_key=signer,
            runtime_signer_public_key=public_key,
            batch_id="diagnostic",
            artifact_root=tmp_path / "diagnostic",
        )


def test_runtime_batch_rejects_artifact_root_inside_repository(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    with pytest.raises(ValueError, match="outside the repository"):
        runtime._validate_artifact_root(repo_root / "artifacts", repo_root=repo_root)


def test_finalize_runtime_batch_seals_scores_and_decides(tmp_path):
    from omc_plan_pilot import build_adjudication_provenance

    cases = [_case(index) for index in range(1, 11)]
    gold, trusted_gold = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    executions = []
    skill_sha256 = "a" * 64
    instrumented_skill_sha256 = "d" * 64
    for case in cases:
        for provider_id in runtime.PROVIDERS:
            plan = {
                "requirements_covered": ["REQ-1"],
                "scope_items": [],
                "dependency_edges": [],
                "tasks": [{
                    "id": "T-1",
                    "target": "src/service.py",
                    "action": "Implement bounded retry handling",
                    "verify": "Run the service tests",
                    "supports": ["REQ-1"],
                }],
                "assumptions": [],
                "decisions_required": [],
            }
            executions.append({
                "provider_id": provider_id,
                "case_id": case["case_id"],
                "plan_execution_id": f"runtime:{case['case_id']}:{provider_id}",
                "plan": plan,
                "raw_output": json.dumps(plan, ensure_ascii=False, sort_keys=True),
                "activation": {
                    "status": "observed",
                    "skill_sha256": skill_sha256,
                },
                "prompt_sha256": runtime._sha256_text(
                    runtime.build_provider_prompt(
                        provider_id,
                        case["request"],
                        context_paths=tuple(case["context_files"]),
                        context_files=case["context_files"],
                    )
                ),
                "provider_input_sha256": runtime.build_provider_input_envelope(
                    provider_id,
                    case,
                    allowed_workspace_delta=_protocol()["execution"][
                        "allowed_workspace_delta"
                    ],
                    instrumented_skill_sha256=instrumented_skill_sha256,
                )["provider_input_sha256"],
                "usage": {
                    "status": "observed",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            })
    sessions, mapping = runtime.build_runtime_blind_batch(
        executions,
        batch_id="runtime-finalize",
        session_count=5,
        gold_document=gold,
    )
    adjudications = []
    for session in sessions:
        adjudications.append({
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh:{session['session_id']}",
            "items": [
                {
                    "item_index": index,
                    "requirement_hit_indexes": [0],
                    "scope_violation_indexes": [],
                    "task_requirement_links": [
                        {"task_index": 0, "requirement_indexes": [0]}
                    ],
                    "edge_requirement_links": [],
                    "unsupported_assumption_indexes": [],
                }
                for index, _ in enumerate(session["items"])
            ],
            "_adjudication_provenance": build_adjudication_provenance(session),
        })
    adjudicator_key = Ed25519PrivateKey.generate()
    adjudicator_public_key = base64.b64encode(
        adjudicator_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    runtime_signer, runtime_signer_public_key = _signer()
    confirmatory_manifest, confirmatory_signer = _signed_confirmatory_manifest(
        cases, gold, skill_sha256=skill_sha256
    )
    provider_batch = {
        "schema_version": 1,
        "batch_id": "runtime-finalize",
        "evaluation_scope": "confirmatory",
        "claim_scope": runtime.CONFIRMATORY_CLAIM_SCOPE,
        "confirmatory_manifest_sha256": runtime.canonical_digest(
            confirmatory_manifest
        ),
        "protocol_sha256": runtime.canonical_digest(_protocol()),
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": gold["gold_sha256"],
        "skill_sha256": skill_sha256,
        "instrumented_skill_sha256": instrumented_skill_sha256,
        "model": "gpt-test",
        "reasoning_effort": "low",
        "activation_probe": {
            "status": "pass",
            "skill_sha256": skill_sha256,
            "executions": {
                provider_id: {
                    "activation": {"attempt_count": 1},
                    "usage": {
                        "status": "observed",
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                }
                for provider_id in runtime.PROVIDERS
            },
        },
        "activation_probe_sha256": "",
        "execution_budget": {
            **runtime.FROZEN_CONFIRMATORY_BUDGET,
            "used_total_tokens": 300,
            "used_external_calls": 22,
        },
        "executions": executions,
    }
    provider_batch["activation_probe_sha256"] = runtime.canonical_digest(
        provider_batch["activation_probe"]
    )
    provider_batch["runtime_attestation"] = runtime.build_runtime_attestation(
        provider_batch,
        sessions,
        mapping,
        private_key=runtime_signer,
        signer_public_key=runtime_signer_public_key,
    )
    report = runtime.finalize_runtime_batch(
        protocol=_protocol(),
        cases=cases,
        gold_document=gold,
        trusted_signer_public_keys=trusted_gold,
        confirmatory_manifest=confirmatory_manifest,
        trusted_prior_fingerprints=confirmatory_manifest["prior_fingerprints"],
        trusted_confirmatory_signer_public_keys={confirmatory_signer},
        provider_batch=provider_batch,
        blind_sessions=sessions,
        private_mapping=mapping,
        adjudication_results=adjudications,
        adjudicator_private_key=adjudicator_key,
        trusted_adjudicator_public_key=adjudicator_public_key,
        adjudicator="independent-test-adjudicator",
        artifact_root=tmp_path / "finalized",
        trusted_runtime_signer_public_key=runtime_signer_public_key,
    )
    assert report["decision"]["decision"] == "PROVISIONALLY_REPLACEABLE"
    assert report["metrics"]["evaluation_scope"] == "confirmatory"
    assert report["metrics"]["omc-plan"]["task_evidence_accuracy"] == 1.0
    assert report["provenance"]["protocol_sha256"] == provider_batch["protocol_sha256"]
    assert report["execution_config"] == {
        "model": "gpt-test",
        "reasoning_effort": "low",
    }
    assert report["provider_execution_evidence_sha256"] == (
        provider_batch["runtime_attestation"]["provider_execution_evidence_sha256"]
    )
    runtime.verify_final_report_attestation(
        report, trusted_public_key=adjudicator_public_key
    )
    assert (tmp_path / "finalized/runtime-final-report.json").is_file()

    payload_tampered_manifest = deepcopy(confirmatory_manifest)
    payload_tampered_manifest["transmission"]["payload_sha256"] = "f" * 64
    signer, signer_public_key = _signer()
    payload_tampered_manifest["signoff"]["signer_public_key"] = signer_public_key
    payload_tampered_manifest["signoff"]["signature"] = base64.b64encode(
        signer.sign(
            runtime.confirmatory_manifest_signoff_payload(
                payload_tampered_manifest
            )
        )
    ).decode("ascii")
    with pytest.raises(ValueError, match="transmission payload mismatch"):
        runtime.finalize_runtime_batch(
            protocol=_protocol(),
            cases=cases,
            gold_document=gold,
            trusted_signer_public_keys=trusted_gold,
            confirmatory_manifest=payload_tampered_manifest,
            trusted_prior_fingerprints=payload_tampered_manifest[
                "prior_fingerprints"
            ],
            trusted_confirmatory_signer_public_keys={signer_public_key},
            provider_batch=provider_batch,
            blind_sessions=sessions,
            private_mapping=mapping,
            adjudication_results=adjudications,
            adjudicator_private_key=adjudicator_key,
            trusted_adjudicator_public_key=adjudicator_public_key,
            adjudicator="independent-test-adjudicator",
            artifact_root=tmp_path / "payload-tampered",
            trusted_runtime_signer_public_key=runtime_signer_public_key,
        )


def test_finalize_rejects_tampered_provider_batch():
    runtime_signer, runtime_signer_public_key = _signer()
    provider_batch = {
        "schema_version": 1,
        "batch_id": "runtime-tamper",
        "activation_probe": {"status": "pass", "skill_sha256": "a" * 64},
        "activation_probe_sha256": "",
        "executions": [],
    }
    provider_batch["activation_probe_sha256"] = runtime.canonical_digest(
        provider_batch["activation_probe"]
    )
    provider_batch["runtime_attestation"] = runtime.build_runtime_attestation(
        provider_batch,
        [],
        {},
        private_key=runtime_signer,
        signer_public_key=runtime_signer_public_key,
    )
    provider_batch["batch_id"] = "tampered"
    with pytest.raises(ValueError, match="runtime attestation mismatch"):
        runtime.verify_runtime_attestation(
            provider_batch,
            [],
            {},
            trusted_public_key=runtime_signer_public_key,
        )


def test_finalize_rejects_empty_task_plan_before_scoring():
    executions = []
    for index in range(1, 11):
        for provider_id in runtime.PROVIDERS:
            executions.append({
                "provider_id": provider_id,
                "case_id": f"observed-{index:02d}",
                "plan_execution_id": f"runtime:observed-{index:02d}:{provider_id}",
                "plan": {"tasks": []},
                "raw_output": "{}",
                "activation": {"status": "observed"},
                "usage": {
                    "status": "observed",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            })
    with pytest.raises(ValueError, match="non-empty tasks"):
        runtime.validate_runtime_executions(executions, expected_case_count=10)


def test_runtime_provenance_rejects_batch_not_bound_to_current_inputs():
    protocol = _protocol()
    cases = [_case(index) for index in range(1, 11)]
    gold, _ = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    skill_sha256 = "a" * 64
    confirmatory_manifest, _ = _signed_confirmatory_manifest(
        cases, gold, skill_sha256=skill_sha256
    )
    executions = []
    instrumented_skill_sha256 = "d" * 64
    for case in cases:
        for provider_id in runtime.PROVIDERS:
            executions.append({
                "provider_id": provider_id,
                "case_id": case["case_id"],
                "prompt_sha256": runtime._sha256_text(
                    runtime.build_provider_prompt(
                        provider_id,
                        case["request"],
                        context_paths=tuple(case["context_files"]),
                        context_files=case["context_files"],
                    )
                ),
                "provider_input_sha256": runtime.build_provider_input_envelope(
                    provider_id,
                    case,
                    allowed_workspace_delta=protocol["execution"][
                        "allowed_workspace_delta"
                    ],
                    instrumented_skill_sha256=instrumented_skill_sha256,
                )["provider_input_sha256"],
                "activation": {
                    "status": "observed",
                    "skill_sha256": skill_sha256,
                },
            })
    provider_batch = {
        "evaluation_scope": "confirmatory",
        "claim_scope": runtime.CONFIRMATORY_CLAIM_SCOPE,
        "confirmatory_manifest_sha256": runtime.canonical_digest(
            confirmatory_manifest
        ),
        "protocol_sha256": runtime.canonical_digest(protocol),
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": gold["gold_sha256"],
        "skill_sha256": skill_sha256,
        "instrumented_skill_sha256": instrumented_skill_sha256,
        "activation_probe": {"status": "pass", "skill_sha256": skill_sha256},
        "executions": executions,
    }
    runtime.validate_runtime_provenance(
        provider_batch,
        protocol=protocol,
        cases=cases,
        gold_document=gold,
        confirmatory_manifest=confirmatory_manifest,
    )

    missing_scope = deepcopy(provider_batch)
    del missing_scope["evaluation_scope"]
    with pytest.raises(ValueError, match="runtime evaluation scope is invalid"):
        runtime.validate_runtime_provenance(
            missing_scope,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
            confirmatory_manifest=confirmatory_manifest,
        )

    provider_batch["corpus_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime input provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
            confirmatory_manifest=confirmatory_manifest,
        )

    provider_batch["corpus_sha256"] = runtime.canonical_digest(cases)
    provider_batch["gold_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime input provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
            confirmatory_manifest=confirmatory_manifest,
        )

    provider_batch["gold_sha256"] = gold["gold_sha256"]
    provider_batch["activation_probe"]["skill_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime input provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
            confirmatory_manifest=confirmatory_manifest,
        )

    provider_batch["activation_probe"]["skill_sha256"] = skill_sha256
    provider_batch["executions"][0]["prompt_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime execution provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
            confirmatory_manifest=confirmatory_manifest,
        )
