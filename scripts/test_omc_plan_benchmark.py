from __future__ import annotations

import base64
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import validate as validate_json_schema

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omc_plan_benchmark import (
    build_fixture_bundle,
    build_fixture_documents,
    canonical_digest,
    gold_signoff_payload,
    score_plan_batch,
    score_plan,
    seal_semantic_adjudication,
    sign_off_gold_document,
    validate_fixture_bundle,
    validate_fixture_documents,
)

_SIGNER = "independent-test-adjudicator"
_APPROVED_AT = "2026-07-31T09:00:00Z"
_PLAN_PRODUCER = "test-plan-producer"
_PLAN_EXECUTION_ID = "test-plan-execution"
_ADJUDICATION_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
_GOLD_SIGNER_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)


def _public_key(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


_ADJUDICATION_PUBLIC_KEY = _public_key(_ADJUDICATION_PRIVATE_KEY)
_GOLD_SIGNER_PUBLIC_KEY = _public_key(_GOLD_SIGNER_PRIVATE_KEY)


def _bundle():
    cases = []
    gold_cases = []
    for index in range(14):
        case_id = f"plan-case-{index + 1:02d}"
        split = "development" if index < 4 else "holdout"
        cases.append({
            "case_id": case_id,
            "split": split,
            "source_type": "synthetic_anonymized",
            "task_type": "bugfix" if index % 2 == 0 else "feature",
            "request": f"Implement request {index + 1}",
            "context_sha256": f"{index + 1:064x}",
        })
        gold_cases.append({
            "case_id": case_id,
            "required_items": [{
                "id": "REQ-1",
                "weight": 2,
                "critical": True,
            }],
            "excluded_scope": ["SCOPE-OUT"],
            "dependency_edges": [],
            "allowed_assumptions": [],
        })
    bundle = build_fixture_bundle(cases, gold_cases)
    evidence = {
        "reviewer": _SIGNER,
        "decision": "approved",
        "corpus_sha256": bundle["gold"]["corpus_sha256"],
        "gold_sha256": bundle["gold"]["gold_sha256"],
    }
    payload = gold_signoff_payload(
        bundle["gold"],
        signer=_SIGNER,
        approved_at=_APPROVED_AT,
        evidence=evidence,
        signer_public_key=_GOLD_SIGNER_PUBLIC_KEY,
    )
    bundle["gold"] = sign_off_gold_document(
        bundle["gold"],
        signer=_SIGNER,
        approved_at=_APPROVED_AT,
        evidence=evidence,
        signer_public_key=_GOLD_SIGNER_PUBLIC_KEY,
        signature=base64.b64encode(
            _GOLD_SIGNER_PRIVATE_KEY.sign(payload)
        ).decode("ascii"),
        trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
    )
    return bundle


def _semantic_labels(
    gold,
    *,
    requirement_hits=None,
    scope_violations=None,
    dependency_hits=None,
    unexpected_dependency_edges=None,
    task_requirement_links=None,
    unsupported_assumptions=None,
):
    return {
        "case_id": gold["case_id"],
        "gold_case_sha256": canonical_digest(gold),
        "requirement_hits": requirement_hits or [],
        "scope_violations": scope_violations or [],
        "dependency_hits": dependency_hits or [],
        "unexpected_dependency_edges": unexpected_dependency_edges or [],
        "task_requirement_links": task_requirement_links or [],
        "unsupported_assumptions": unsupported_assumptions or [],
    }


def _sealed_semantic_labels(gold, plan, *, raw_output="", **kwargs):
    return seal_semantic_adjudication(
        _semantic_labels(gold, **kwargs),
        plan=plan,
        gold=gold,
        adjudicator="independent-test-adjudicator",
        plan_producer=_PLAN_PRODUCER,
        adjudication_execution_id="test-adjudication-execution",
        plan_execution_id=_PLAN_EXECUTION_ID,
        private_key=_ADJUDICATION_PRIVATE_KEY,
        raw_output=raw_output,
    )


def test_fixture_bundle_requires_four_development_and_ten_holdout_cases():
    bundle = _bundle()
    validate_fixture_bundle(
        bundle,
        trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
    )

    invalid = copy.deepcopy(bundle)
    invalid["cases"][4]["split"] = "development"
    invalid["corpus_sha256"] = canonical_digest(invalid["cases"])

    with pytest.raises(ValueError, match="4 development and 10 holdout"):
        validate_fixture_bundle(
            invalid,
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )


def test_fixture_bundle_rejects_duplicate_case_ids_and_gold_leakage():
    duplicate = _bundle()
    duplicate["cases"][1]["case_id"] = duplicate["cases"][0]["case_id"]
    duplicate["corpus_sha256"] = canonical_digest(duplicate["cases"])
    with pytest.raises(ValueError, match="duplicate case_id"):
        validate_fixture_bundle(
            duplicate,
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )

    leaked = _bundle()
    leaked["cases"][0]["gold"] = {"required_items": ["secret"]}
    leaked["corpus_sha256"] = canonical_digest(leaked["cases"])
    with pytest.raises(ValueError, match="gold data"):
        validate_fixture_bundle(
            leaked,
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )


def test_fixture_bundle_rejects_mutation_after_freeze():
    bundle = _bundle()
    bundle["cases"][0]["request"] = "mutated after freeze"

    with pytest.raises(ValueError, match="corpus hash mismatch"):
        validate_fixture_bundle(
            bundle,
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )


def test_public_and_private_fixture_documents_are_separate_and_hash_linked():
    bundle = _bundle()
    public_document, gold_document = build_fixture_documents(
        bundle["cases"],
        bundle["gold"]["cases"],
    )

    assert "gold" not in public_document
    with pytest.raises(ValueError, match="independent sign-off"):
        validate_fixture_documents(public_document, gold_document)
    validate_fixture_documents(
        public_document,
        gold_document,
        require_signed_off=False,
    )

    gold_document["corpus_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="corpus anchor mismatch"):
        validate_fixture_documents(
            public_document,
            gold_document,
            require_signed_off=False,
        )


def test_separate_fixture_documents_reject_hidden_top_level_payloads():
    bundle = _bundle()
    public_document, gold_document = build_fixture_documents(
        bundle["cases"],
        bundle["gold"]["cases"],
    )
    public_document["gold"] = {"cases": []}

    with pytest.raises(ValueError, match="unsupported public fixture fields"):
        validate_fixture_documents(
            public_document,
            gold_document,
            require_signed_off=False,
        )


def test_gold_schema_version_must_match_public_schema_version():
    bundle = _bundle()
    public_document, gold_document = build_fixture_documents(
        bundle["cases"],
        bundle["gold"]["cases"],
    )
    gold_document["schema_version"] = 999

    with pytest.raises(ValueError, match="schema version mismatch"):
        validate_fixture_documents(
            public_document,
            gold_document,
            require_signed_off=False,
        )


def test_public_fixture_rejects_unrecognized_fields_that_can_leak_answers():
    bundle = _bundle()
    bundle["cases"][0]["expected_answer"] = ["REQ-1"]
    bundle["corpus_sha256"] = canonical_digest(bundle["cases"])

    with pytest.raises(ValueError, match="unsupported public case fields"):
        validate_fixture_bundle(
            bundle,
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )


def test_checked_in_fixture_documents_are_frozen_and_valid():
    fixture_dir = Path(__file__).parent / "fixtures"
    public_document = json.loads(
        (fixture_dir / "omc_plan_benchmark_cases.json").read_text(encoding="utf-8")
    )
    gold_document = json.loads(
        (fixture_dir / "omc_plan_gold_labels.json").read_text(encoding="utf-8")
    )

    assert gold_document["status"] == "draft"
    validate_fixture_documents(
        public_document,
        gold_document,
        require_signed_off=False,
    )
    assert "gold" not in public_document


def test_cli_help_does_not_require_optional_crypto_runtime():
    script = Path(__file__).parent / "omc_plan_benchmark.py"

    result = subprocess.run(
        [sys.executable, "-S", str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_crypto_dependency_has_an_explicit_install_contract():
    requirements = (
        Path(__file__).parent / "requirements-plan-benchmark.txt"
    )

    assert requirements.exists()
    assert any(
        line.strip().startswith("cryptography")
        for line in requirements.read_text(encoding="utf-8").splitlines()
    )


def test_score_plan_reports_quality_and_efficiency_metrics():
    gold = {
        "case_id": "plan-case-01",
        "required_items": [
            {"id": "REQ-1", "weight": 2, "critical": True},
            {"id": "REQ-2", "weight": 1, "critical": False},
        ],
        "excluded_scope": ["SCOPE-OUT"],
        "dependency_edges": [{"before": "REQ-1", "after": "REQ-2"}],
        "allowed_assumptions": ["ASSUME-1"],
    }
    plan = {
        "requirements_covered": ["인증 헤더 조건", "익명 요청 보존"],
        "scope_items": ["SCOPE-OUT"],
        "dependency_edges": [{"before": "task-2", "after": "task-1"}],
        "tasks": [
            {
                "id": "task-1",
                "target": "src/service.py",
                "action": "change behavior",
                "verify": "pytest",
                "supports": ["인증 헤더 조건"],
            },
            {
                "id": "task-2",
                "target": "",
                "action": "extra cleanup",
                "verify": "",
                "supports": [],
            },
        ],
        "assumptions": ["ASSUME-2"],
        "decisions_required": ["Choose migration policy"],
    }
    labels = _sealed_semantic_labels(
        gold,
        plan,
        raw_output="example output",
        requirement_hits=["REQ-1"],
        scope_violations=["SCOPE-OUT"],
        task_requirement_links=[
            {"task_id": "task-1", "requirement_ids": ["REQ-1"]},
            {"task_id": "task-2", "requirement_ids": []},
        ],
        unsupported_assumptions=["ASSUME-2"],
    )

    result = score_plan(
        plan,
        gold,
        labels,
        trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
        expected_plan_producer=_PLAN_PRODUCER,
        expected_plan_execution_id=_PLAN_EXECUTION_ID,
        raw_output="example output",
    )

    assert result["weighted_coverage"] == pytest.approx(2 / 3)
    assert result["critical_omissions"] == []
    assert result["scope_violations"] == ["SCOPE-OUT"]
    assert result["dependency_accuracy"] == 0.0
    assert result["executable_step_rate"] == 0.5
    assert result["unsupported_assumptions"] == ["ASSUME-2"]
    assert result["decision_proxy"] == 1
    assert result["bloat_ratio"] == 0.5
    assert result["output_size_chars"] == len("example output")


def test_score_plan_rejects_tasks_missing_output_schema_fields():
    gold = {
        "case_id": "plan-case-01",
        "required_items": [],
        "excluded_scope": [],
        "dependency_edges": [],
        "allowed_assumptions": [],
    }
    plan = {
        "requirements_covered": [],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [{"id": "task-1", "supports": []}],
        "assumptions": [],
        "decisions_required": [],
    }
    labels = _sealed_semantic_labels(
        gold,
        plan,
        task_requirement_links=[
            {"task_id": "task-1", "requirement_ids": []},
        ],
    )

    with pytest.raises(ValueError, match="missing fields: action, target, verify"):
        score_plan(
            plan,
            gold,
            labels,
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
            expected_plan_producer=_PLAN_PRODUCER,
            expected_plan_execution_id=_PLAN_EXECUTION_ID,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (("target", 123), ("action", None), ("verify", ["pytest"])),
)
def test_score_plan_rejects_non_string_task_execution_fields(field, value):
    gold = {
        "case_id": "plan-case-01",
        "required_items": [],
        "excluded_scope": [],
        "dependency_edges": [],
        "allowed_assumptions": [],
    }
    task = {
        "id": "task-1",
        "target": "src/service.py",
        "action": "change behavior",
        "verify": "pytest",
        "supports": [],
    }
    task[field] = value
    plan = {
        "requirements_covered": [],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [task],
        "assumptions": [],
        "decisions_required": [],
    }
    labels = _sealed_semantic_labels(
        gold,
        plan,
        task_requirement_links=[
            {"task_id": "task-1", "requirement_ids": []},
        ],
    )

    with pytest.raises(ValueError, match=rf"tasks\[0\]\.{field} must be a string"):
        score_plan(
            plan,
            gold,
            labels,
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
            expected_plan_producer=_PLAN_PRODUCER,
            expected_plan_execution_id=_PLAN_EXECUTION_ID,
        )


def test_score_plan_marks_missing_critical_requirement():
    gold = {
        "case_id": "plan-case-01",
        "required_items": [
            {"id": "REQ-1", "weight": 3, "critical": True},
        ],
        "excluded_scope": [],
        "dependency_edges": [],
        "allowed_assumptions": [],
    }
    plan = {
        "requirements_covered": [],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [],
        "assumptions": [],
        "decisions_required": [],
    }
    labels = _sealed_semantic_labels(gold, plan)

    result = score_plan(
        plan,
        gold,
        labels,
        trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
        expected_plan_producer=_PLAN_PRODUCER,
        expected_plan_execution_id=_PLAN_EXECUTION_ID,
    )

    assert result["weighted_coverage"] == 0.0
    assert result["critical_omissions"] == ["REQ-1"]
    assert result["dependency_accuracy"] == 1.0
    assert result["executable_step_rate"] == 1.0


def test_dependency_accuracy_penalizes_unexpected_edges():
    gold = {
        "case_id": "plan-case-01",
        "required_items": [
            {"id": "REQ-1", "weight": 1, "critical": False},
            {"id": "REQ-2", "weight": 1, "critical": False},
        ],
        "excluded_scope": [],
        "dependency_edges": [{"before": "REQ-1", "after": "REQ-2"}],
        "allowed_assumptions": [],
    }
    plan = {
        "requirements_covered": [],
        "scope_items": [],
        "dependency_edges": [
            {"before": "task-1", "after": "task-2"},
            {"before": "task-2", "after": "task-3"},
        ],
        "tasks": [],
        "assumptions": [],
        "decisions_required": [],
    }
    labels = _sealed_semantic_labels(
        gold,
        plan,
        dependency_hits=[{"before": "REQ-1", "after": "REQ-2"}],
        unexpected_dependency_edges=[{"before": "task-2", "after": "task-3"}],
    )

    result = score_plan(
        plan,
        gold,
        labels,
        trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
        expected_plan_producer=_PLAN_PRODUCER,
        expected_plan_execution_id=_PLAN_EXECUTION_ID,
    )

    assert result["dependency_accuracy"] == 0.5


def test_score_plan_requires_semantic_adjudication_bound_to_gold():
    gold = {
        "case_id": "plan-case-01",
        "required_items": [
            {"id": "REQ-1", "weight": 2, "critical": True},
        ],
        "excluded_scope": [],
        "dependency_edges": [],
        "allowed_assumptions": [],
    }
    plan = {
        "requirements_covered": ["요구사항을 구현한다"],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [],
        "assumptions": [],
        "decisions_required": [],
    }

    with pytest.raises(ValueError, match="semantic adjudication"):
        score_plan(
            plan,
            gold,
            None,
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
            expected_plan_producer=_PLAN_PRODUCER,
            expected_plan_execution_id=_PLAN_EXECUTION_ID,
        )

    labels = _sealed_semantic_labels(
        gold,
        plan,
        requirement_hits=["REQ-1"],
    )
    labels["receipt"]["gold_case_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="gold hash mismatch"):
        score_plan(
            plan,
            gold,
            labels,
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
            expected_plan_producer=_PLAN_PRODUCER,
            expected_plan_execution_id=_PLAN_EXECUTION_ID,
        )


def test_output_schema_accepts_natural_language_supports_not_gold_ids():
    fixture_dir = Path(__file__).parent / "fixtures"
    schema = json.loads(
        (fixture_dir / "omc_plan_output_schema.json").read_text(encoding="utf-8")
    )
    plan = {
        "requirements_covered": ["익명 요청 동작을 보존한다"],
        "scope_items": ["인증 헤더 처리"],
        "dependency_edges": [{"before": "inspect", "after": "implement"}],
        "tasks": [{
            "id": "inspect",
            "target": "src/service.py",
            "action": "호출 경로를 확인한다",
            "verify": "pytest",
            "supports": ["익명 요청 동작 보존"],
        }],
        "assumptions": [],
        "decisions_required": [],
    }

    validate_json_schema(plan, schema)
    assert "requirement_ids" not in schema["properties"]["tasks"]["items"]["properties"]


def test_semantic_adjudication_requires_independent_sealed_receipt():
    gold = {
        "case_id": "plan-case-01",
        "required_items": [
            {"id": "REQ-1", "weight": 2, "critical": True},
        ],
        "excluded_scope": [],
        "dependency_edges": [],
        "allowed_assumptions": [],
    }
    plan = {
        "requirements_covered": ["요구사항을 구현한다"],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [],
        "assumptions": [],
        "decisions_required": [],
    }
    labels = _semantic_labels(gold, requirement_hits=["REQ-1"])

    with pytest.raises(ValueError, match="sealed semantic adjudication"):
        score_plan(
            plan,
            gold,
            labels,
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
            expected_plan_producer="planner-a",
            expected_plan_execution_id="plan-execution-01",
        )

    with pytest.raises(ValueError, match="independent"):
        seal_semantic_adjudication(
            labels,
            plan=plan,
            gold=gold,
            adjudicator="planner-a",
            plan_producer="planner-a",
            adjudication_execution_id="execution-01",
            plan_execution_id="plan-execution-01",
            private_key=_ADJUDICATION_PRIVATE_KEY,
        )

    with pytest.raises(ValueError, match="independent execution"):
        seal_semantic_adjudication(
            labels,
            plan=plan,
            gold=gold,
            adjudicator="adjudicator-b",
            plan_producer="planner-a",
            adjudication_execution_id="shared-execution",
            plan_execution_id="shared-execution",
            private_key=_ADJUDICATION_PRIVATE_KEY,
        )


def test_semantic_adjudication_rejects_tampered_labels():
    gold = {
        "case_id": "plan-case-01",
        "required_items": [
            {"id": "REQ-1", "weight": 2, "critical": True},
        ],
        "excluded_scope": [],
        "dependency_edges": [],
        "allowed_assumptions": [],
    }
    plan = {
        "requirements_covered": [],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [],
        "assumptions": [],
        "decisions_required": [],
    }
    sealed = seal_semantic_adjudication(
        _semantic_labels(gold),
        plan=plan,
        gold=gold,
        adjudicator="adjudicator-b",
        plan_producer="planner-a",
        adjudication_execution_id="execution-01",
        plan_execution_id="plan-execution-01",
        private_key=_ADJUDICATION_PRIVATE_KEY,
    )
    sealed["labels"]["requirement_hits"] = ["REQ-1"]

    with pytest.raises(ValueError, match="labels hash mismatch"):
        score_plan(
            plan,
            gold,
            sealed,
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
            expected_plan_producer="planner-a",
            expected_plan_execution_id="plan-execution-01",
        )

    attacker_private_key = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
    sealed = seal_semantic_adjudication(
        _semantic_labels(gold),
        plan=plan,
        gold=gold,
        adjudicator="adjudicator-b",
        plan_producer="planner-a",
        adjudication_execution_id="execution-01",
        plan_execution_id="plan-execution-01",
        private_key=attacker_private_key,
    )
    with pytest.raises(ValueError, match="untrusted adjudicator"):
        score_plan(
            plan,
            gold,
            sealed,
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
            expected_plan_producer="planner-a",
            expected_plan_execution_id="plan-execution-01",
        )


def test_gold_signoff_requires_independent_signer_and_verifiable_evidence():
    bundle = build_fixture_bundle([], [])
    evidence = {
        "reviewer": "fixture-producer",
        "decision": "approved",
        "corpus_sha256": bundle["corpus_sha256"],
        "gold_sha256": bundle["gold"]["gold_sha256"],
    }

    with pytest.raises(ValueError, match="independent"):
        gold_signoff_payload(
            bundle["gold"],
            signer="fixture-producer",
            approved_at=_APPROVED_AT,
            evidence=evidence,
            signer_public_key=_GOLD_SIGNER_PUBLIC_KEY,
        )


def test_gold_signoff_rejects_untrusted_signer_key():
    bundle = build_fixture_bundle([], [])
    attacker_private_key = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
    attacker_public_key = _public_key(attacker_private_key)
    evidence = {
        "reviewer": "external-reviewer",
        "decision": "approved",
        "corpus_sha256": bundle["corpus_sha256"],
        "gold_sha256": bundle["gold"]["gold_sha256"],
    }
    payload = gold_signoff_payload(
        bundle["gold"],
        signer="external-reviewer",
        approved_at=_APPROVED_AT,
        evidence=evidence,
        signer_public_key=attacker_public_key,
    )

    with pytest.raises(ValueError, match="untrusted gold signer"):
        sign_off_gold_document(
            bundle["gold"],
            signer="external-reviewer",
            approved_at=_APPROVED_AT,
            evidence=evidence,
            signer_public_key=attacker_public_key,
            signature=base64.b64encode(
                attacker_private_key.sign(payload)
            ).decode("ascii"),
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )


def test_gold_signoff_rejects_tampered_signed_claim():
    bundle = _bundle()
    bundle["gold"]["signoff"]["approved_at"] = "2026-07-31T09:00:01Z"

    with pytest.raises(ValueError, match="signature mismatch"):
        validate_fixture_bundle(
            bundle,
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )


def test_fixture_bundle_rejects_public_gold_corpus_anchor_mismatch():
    bundle = _bundle()
    bundle["gold"]["corpus_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="corpus anchor mismatch"):
        validate_fixture_bundle(
            bundle,
            trusted_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        )


def test_gold_dependency_edges_must_reference_requirement_ids():
    bundle = _bundle()
    bundle["gold"]["status"] = "draft"
    bundle["gold"]["signoff"] = None
    bundle["gold"]["cases"][0]["dependency_edges"] = [
        {"before": "task-1", "after": "task-2"},
    ]
    bundle["gold"]["gold_sha256"] = canonical_digest(bundle["gold"]["cases"])

    with pytest.raises(ValueError, match="requirement id"):
        validate_fixture_bundle(bundle, require_signed_off=False)


def _plan_batch_documents():
    bundle = _bundle()
    public_document = {
        "schema_version": bundle["schema_version"],
        "status": bundle["status"],
        "cases": bundle["cases"],
        "corpus_sha256": bundle["corpus_sha256"],
    }
    executions = []
    gold_by_id = {case["case_id"]: case for case in bundle["gold"]["cases"]}
    for case in bundle["cases"][:4]:
        case_id = case["case_id"]
        execution_id = f"execution-{case_id}"
        plan = {
            "requirements_covered": ["필수 요구사항을 구현한다"],
            "scope_items": [],
            "dependency_edges": [],
            "tasks": [{
                "id": "task-1",
                "target": "src/service.py",
                "action": "요구사항을 구현한다",
                "verify": "pytest",
                "supports": ["필수 요구사항"],
            }],
            "assumptions": [],
            "decisions_required": [],
        }
        raw_output = json.dumps(plan, ensure_ascii=False)
        labels = seal_semantic_adjudication(
            _semantic_labels(
                gold_by_id[case_id],
                requirement_hits=["REQ-1"],
                task_requirement_links=[{
                    "task_id": "task-1",
                    "requirement_ids": ["REQ-1"],
                }],
            ),
            plan=plan,
            gold=gold_by_id[case_id],
            adjudicator="independent-test-adjudicator",
            plan_producer=_PLAN_PRODUCER,
            adjudication_execution_id=f"adjudication-{case_id}",
            plan_execution_id=execution_id,
            private_key=_ADJUDICATION_PRIVATE_KEY,
            raw_output=raw_output,
        )
        executions.append({
            "case_id": case_id,
            "plan_execution_id": execution_id,
            "plan": plan,
            "raw_output": raw_output,
            "semantic_adjudication": labels,
        })
    result_document = {
        "schema_version": 1,
        "split": "development",
        "providers": [{
            "provider_id": "omc-plan",
            "plan_producer": _PLAN_PRODUCER,
            "executions": executions,
        }],
    }
    gold_document = copy.deepcopy(bundle["gold"])
    gold_document["schema_version"] = bundle["schema_version"]
    return public_document, gold_document, result_document


def test_score_plan_batch_reports_provider_metrics_for_complete_split():
    public_document, gold_document, result_document = _plan_batch_documents()

    report = score_plan_batch(
        public_document,
        gold_document,
        result_document,
        trusted_gold_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
    )

    assert report["schema_version"] == 1
    assert report["split"] == "development"
    assert report["gold_status"] == "signed_off"
    assert report["evaluation_status"] == "verified_signed_off"
    assert report["case_count"] == 4
    provider = report["providers"][0]
    assert provider["provider_id"] == "omc-plan"
    assert provider["summary"]["weighted_coverage_mean"] == 1.0
    assert provider["summary"]["critical_omission_count"] == 0
    assert provider["summary"]["executable_step_rate_mean"] == 1.0
    assert len(provider["case_scores"]) == 4


def test_score_plan_batch_rejects_missing_split_case():
    public_document, gold_document, result_document = _plan_batch_documents()
    result_document["providers"][0]["executions"].pop()

    with pytest.raises(ValueError, match="must cover every development case"):
        score_plan_batch(
            public_document,
            gold_document,
            result_document,
            trusted_gold_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
        )


def test_score_plan_batch_marks_draft_gold_as_not_for_comparison():
    public_document, gold_document, result_document = _plan_batch_documents()
    gold_document["status"] = "draft"
    gold_document["signoff"] = None

    report = score_plan_batch(
        public_document,
        gold_document,
        result_document,
        trusted_gold_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
        trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
        allow_draft_gold=True,
    )

    assert report["gold_status"] == "draft"
    assert report["evaluation_status"] == "draft_not_for_comparison"


def test_score_plan_batch_rejects_raw_output_changed_after_adjudication():
    public_document, gold_document, result_document = _plan_batch_documents()
    result_document["providers"][0]["executions"][0]["raw_output"] = ""

    with pytest.raises(ValueError, match="raw output hash mismatch"):
        score_plan_batch(
            public_document,
            gold_document,
            result_document,
            trusted_gold_signer_public_keys={_GOLD_SIGNER_PUBLIC_KEY},
            trusted_adjudicator_public_keys={_ADJUDICATION_PUBLIC_KEY},
        )


def test_cli_writes_batch_score_report(tmp_path):
    public_document, gold_document, result_document = _plan_batch_documents()
    public_path = tmp_path / "cases.json"
    gold_path = tmp_path / "gold.json"
    results_path = tmp_path / "results.json"
    output_path = tmp_path / "report.json"
    for path, payload in (
        (public_path, public_document),
        (gold_path, gold_document),
        (results_path, result_document),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "omc_plan_benchmark.py"),
            str(public_path),
            str(gold_path),
            "--results",
            str(results_path),
            "--output",
            str(output_path),
            "--trusted-signer-public-key",
            _GOLD_SIGNER_PUBLIC_KEY,
            "--trusted-adjudicator-public-key",
            _ADJUDICATION_PUBLIC_KEY,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["case_count"] == 4
    assert report["providers"][0]["summary"]["weighted_coverage_mean"] == 1.0
