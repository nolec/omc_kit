from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

import omc_product_value_preregistration as preregistration
from omc_product_value_preregistration import (
    build_preregistration,
    canonical_sha256,
    validate_preregistration,
)


def _workloads() -> list[dict[str, object]]:
    return [
        {
            "workload_id": f"workload-{index}",
            "repo_alias": "repo-a" if index < 4 else "repo-b",
            "repository_identity_sha256": "a" * 64 if index < 4 else "b" * 64,
            "implementation_type": "api" if index % 2 else "ui",
            "work_class": "implementation",
            "source_commit": f"{index}" * 40,
            "request_sha256": f"{index}" * 64,
            "dod_sha256": f"{index + 1}" * 64,
            "verification_sha256": f"{index + 2}" * 64,
            "expected_child_count": 3 + (index % 3),
            "scope_paths": [f"src/workload-{index}/"],
        }
        for index in range(1, 6)
    ]


def _workloads_v2() -> list[dict[str, object]]:
    workloads = _workloads()
    workloads.append({
        "workload_id": "workload-6",
        "repo_alias": "repo-b",
        "repository_identity_sha256": "b" * 64,
        "implementation_type": "ui",
        "work_class": "implementation",
        "source_commit": "6" * 40,
        "request_sha256": "6" * 64,
        "dod_sha256": "7" * 64,
        "verification_sha256": "8" * 64,
        "expected_child_count": 3,
        "scope_paths": ["src/workload-6/"],
    })
    for index, workload in enumerate(workloads):
        workload["evaluation_role"] = (
            "pilot" if index == 0 else "confirmatory"
        )
    return workloads


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _registration_authority() -> dict[str, object]:
    return {
        "service_id": preregistration.rfc3161.SIGSTORE_TSA_SERVICE_ID,
        "operator": preregistration.rfc3161.SIGSTORE_TSA_OPERATOR,
        "certificate_chain_sha256": "a" * 64,
        "trusted_root_sha256": "b" * 64,
        "tuf_root_sha256": "c" * 64,
        "valid_for": {
            "start": "2026-08-01T00:00:00+00:00",
            "end": "2026-10-01T00:00:00+00:00",
        },
    }


def _execution_contract() -> dict[str, object]:
    return {
        "provider_snapshot": {
            "provider_family": "codex",
            "model": "gpt-5.3-codex",
            "reasoning_profile": "high",
            "adapter_sha256": "d" * 64,
        },
        "limits": {
            "max_total_tokens": 10_000,
            "max_total_elapsed_sec": 600,
            "max_output_chars": 100_000,
        },
        "runner_schema": "omc-product-value-acceptance/v1",
        "telemetry_schema": "omc-product-value-telemetry/v1",
    }


def _workloads_v3() -> list[dict[str, object]]:
    workloads = _workloads_v2()
    for index, workload in enumerate(workloads, start=1):
        workload.update({
            "pair_id": f"pair-{index}",
            "execution_order": (
                ["omc", "baseline"] if index % 2 else ["baseline", "omc"]
            ),
            "execution_packet_sha256": chr(96 + index) * 64,
        })
    return workloads


def _workloads_v4() -> list[dict[str, object]]:
    workloads = _workloads_v3()
    for index, workload in enumerate(workloads, start=1):
        workload["environment_receipt_sha256"] = f"{index + 6:x}" * 64
    return workloads


def _execution_contract_v4() -> dict[str, object]:
    return {
        "provider_snapshot": {
            "provider_family": "codex",
            "model": "gpt-5.3-codex",
            "reasoning_profile": "high",
            "backend_sha256": "a" * 64,
        },
        "limits": {
            "max_total_tokens": 10_000,
            "max_total_elapsed_sec": 600,
            "max_output_chars": 100_000,
        },
        "runner_schema": "omc-product-value-acceptance/v2",
        "telemetry_schema": "omc-product-value-telemetry/v1",
        "execution_bundle": {
            "acceptance_runner_sha256": "b" * 64,
            "arm_adapter_sha256": "c" * 64,
            "scheduler_sha256": "d" * 64,
            "executor_shadow_sha256": "e" * 64,
            "provider_adapter_sha256": "f" * 64,
        },
        "environment_policy": {
            "receipt_schema": "omc-product-value-environment/v3",
            "probe_schema": "omc-product-value-environment-probe/v1",
            "cache_inventory_schema": "omc-product-value-cache-inventory/v1",
            "cache_inventory_max_entries": 10_000,
            "cache_inventory_max_bytes": 1_073_741_824,
            "same_readonly_cache_required": True,
            "preparation_cost_included": False,
        },
    }


def _artifact_lineage_v5() -> dict[str, str]:
    return {
        "parent_corpus_batch_id": "product-value-batch-20260826-v2-r1",
        "parent_corpus_source_sha256": "1" * 64,
        "parent_corpus_public_payload_sha256": "2" * 64,
        "implementation_commit": "3" * 40,
    }


def _evidence_contract_v6(
    *,
    replication_role: str = "initial",
    workloads: list[dict[str, object]] | None = None,
) -> dict[str, str | None]:
    selected_workloads = _workloads_v4() if workloads is None else workloads
    return {
        "evidence_tier": "holdout",
        "replication_role": replication_role,
        "development_batch_id": "product-value-development-v1",
        "development_preregistration_sha256": "4" * 64,
        "development_workload_inventory_sha256": "5" * 64,
        "holdout_workload_inventory_sha256": (
            preregistration.workload_inventory_sha256(selected_workloads)
        ),
        "selection_policy_sha256": preregistration.canonical_sha256(
            preregistration.SELECTION_POLICY_V2
        ),
        "prior_holdout_report_sha256": (
            None if replication_role == "initial" else "8" * 64
        ),
    }


def _disjoint_workloads_v4() -> list[dict[str, object]]:
    workloads = _workloads_v4()
    for index, workload in enumerate(workloads, start=1):
        workload.update({
            "workload_id": f"holdout-{index}",
            "repo_alias": "repo-c" if index <= 3 else "repo-d",
            "repository_identity_sha256": (
                "c" * 64 if index <= 3 else "d" * 64
            ),
            "source_commit": preregistration.canonical_sha256(
                f"holdout-source-{index}"
            )[:40],
            "request_sha256": preregistration.canonical_sha256(
                f"holdout-request-{index}"
            ),
            "execution_packet_sha256": preregistration.canonical_sha256(
                f"holdout-packet-{index}"
            ),
        })
    return workloads


def test_builds_and_validates_five_workload_prospective_contract() -> None:
    manifest = build_preregistration("product-value-batch-1", _workloads())

    validated = validate_preregistration(manifest)

    assert validated["status"] == "prepared"
    assert validated["selection_policy"]["posthoc_exclusions_allowed"] is False
    assert validated["comparison_contract"]["provider_call_count_metric_only"] is True
    assert validated["thresholds"]["success_rate_relation"] == "gte_baseline"
    assert validated["thresholds"]["intervention_ratio_max"] == 0.5


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda rows: rows.pop(), "workload_count_invalid"),
        (
            lambda rows: [row.update({"repo_alias": "one-repo"}) for row in rows],
            "repository_coverage_invalid",
        ),
        (
            lambda rows: [
                row.update({"repository_identity_sha256": "a" * 64}) for row in rows
            ],
            "repository_coverage_invalid",
        ),
        (
            lambda rows: rows[0].update({"work_class": "benchmark_maintenance"}),
            "workload_work_class_invalid",
        ),
    ],
)
def test_rejects_ineligible_workload_universe(mutation, reason: str) -> None:
    workloads = _workloads()
    mutation(workloads)

    with pytest.raises(ValueError, match=reason):
        build_preregistration("product-value-batch-1", workloads)


def test_rejects_post_registration_manifest_or_threshold_changes() -> None:
    manifest = build_preregistration("product-value-batch-1", _workloads())
    changed_workload = deepcopy(manifest)
    changed_workload["workloads"][0]["expected_child_count"] = 5
    with pytest.raises(ValueError, match="preregistration_hash_mismatch"):
        validate_preregistration(changed_workload)

    changed_threshold = deepcopy(manifest)
    changed_threshold["thresholds"]["intervention_ratio_max"] = 1.0
    with pytest.raises(ValueError, match="preregistration_hash_mismatch"):
        validate_preregistration(changed_threshold)


def test_rejects_duplicate_source_request_pair() -> None:
    workloads = _workloads()
    workloads[1]["source_commit"] = workloads[0]["source_commit"]
    workloads[1]["request_sha256"] = workloads[0]["request_sha256"]

    with pytest.raises(ValueError, match="workload_source_request_duplicate"):
        build_preregistration("product-value-batch-1", workloads)


def test_rejects_noncanonical_workload_order_even_when_rehashed() -> None:
    manifest = build_preregistration("product-value-batch-1", _workloads())
    manifest["workloads"].reverse()
    manifest.pop("preregistration_sha256")
    manifest["preregistration_sha256"] = canonical_sha256(manifest)

    with pytest.raises(ValueError, match="workload_order_invalid"):
        validate_preregistration(manifest)


def test_rejects_repository_alias_mapped_to_multiple_identities() -> None:
    workloads = _workloads()
    workloads[0]["repository_identity_sha256"] = "b" * 64

    with pytest.raises(ValueError, match="repository_identity_mapping_invalid"):
        build_preregistration("product-value-batch-1", workloads)


def test_omc_cli_validates_preregistration_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(build_preregistration("product-value-batch-1", _workloads())),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-preregistration",
            "validate",
            "--manifest",
            str(manifest_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "claim_eligible": False,
        "preregistration_sha256": build_preregistration(
            "product-value-batch-1", _workloads()
        )["preregistration_sha256"],
        "registration_required": True,
        "status": "prepared",
    }


def test_omc_cli_prepares_and_validates_v2_manifest(tmp_path: Path) -> None:
    workloads_path = tmp_path / "workloads.json"
    authority_path = tmp_path / "authority.json"
    manifest_path = tmp_path / "manifest-v2.json"
    workloads_path.write_text(json.dumps(_workloads_v2()), encoding="utf-8")
    authority_path.write_text(
        json.dumps(_registration_authority()),
        encoding="utf-8",
    )

    prepare = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-preregistration",
            "prepare-v2",
            "--batch-id",
            "product-value-batch-2",
            "--workloads",
            str(workloads_path),
            "--observed-from",
            "2026-09-01T00:00:00+00:00",
            "--observed-through",
            "2026-09-08T00:00:00+00:00",
            "--registration-authority",
            str(authority_path),
            "--out",
            str(manifest_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert prepare.returncode == 0, prepare.stderr
    assert json.loads(prepare.stdout)["status"] == "frozen"
    validate = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-preregistration",
            "validate",
            "--manifest",
            str(manifest_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert validate.returncode == 0, validate.stderr
    assert json.loads(validate.stdout) == {
        "claim_eligible": False,
        "preregistration_sha256": json.loads(
            manifest_path.read_text(encoding="utf-8")
        )["preregistration_sha256"],
        "registration_required": True,
        "status": "frozen",
    }
    record_path = tmp_path / "registry-record.json"
    registry_record = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-preregistration",
            "registry-record",
            "--manifest",
            str(manifest_path),
            "--out",
            str(record_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert registry_record.returncode == 0, registry_record.stderr
    assert json.loads(record_path.read_text(encoding="utf-8")) == (
        preregistration.prepare_registry_record(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    )


def test_v2_registration_requires_exact_registry_and_pre_observation_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_from = datetime(2026, 9, 1, tzinfo=timezone.utc)
    observed_through = observed_from + timedelta(days=7)
    authority = {"trusted_root_sha256": "c" * 64}
    trusted_root = {"fixture": "trusted-root"}
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    monkeypatch.setattr(
        preregistration.rfc3161,
        "trust_identity",
        lambda candidate, **_: authority,
    )
    monkeypatch.setattr(
        preregistration.rfc3161,
        "verify_registration_evidence",
        lambda candidate, **_: candidate,
    )
    manifest = preregistration.build_preregistration_v2(
        "product-value-batch-2",
        _workloads_v2(),
        observed_from=observed_from.isoformat(),
        observed_through=observed_through.isoformat(),
        registration_authority=authority,
    )
    assert manifest["status"] == "frozen"
    assert [
        workload["evaluation_role"] for workload in manifest["workloads"]
    ].count("pilot") == 1
    assert manifest["observation_window"]["observed_from"] == (
        observed_from.isoformat()
    )

    root = tmp_path / "registry"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "registry@example.com")
    _git(root, "config", "user.name", "Registry Test")
    anchor = root / "anchor.txt"
    anchor.write_text("anchor\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "anchor")
    ancestor_commit = _git(root, "rev-parse", "HEAD")
    registry_path = ".omc/registry/product-value-batch-2.json"
    registry_file = root / registry_path
    registry_file.parent.mkdir(parents=True)
    registry_file.write_text(
        json.dumps(preregistration.prepare_registry_record(manifest)),
        encoding="utf-8",
    )
    _git(root, "add", registry_path)
    _git(root, "commit", "-qm", "register")
    registry_commit = _git(root, "rev-parse", "HEAD")
    evidence = {
        "gen_time": (observed_from - timedelta(seconds=1)).isoformat()
    }
    receipt = preregistration.prepare_registration_receipt(
        manifest,
        registry_commit=registry_commit,
        registry_path=registry_path,
        registration_evidence=evidence,
        trusted_root=trusted_root,
        approved_trusted_root_sha256="c" * 64,
    )

    result = preregistration.validate_registered_preregistration(
        manifest,
        repository_root=root,
        registry_commit=registry_commit,
        registry_path=registry_path,
        required_ancestor_commit=ancestor_commit,
        registration_receipt=receipt,
        expected_registration_receipt_sha256=receipt["receipt_sha256"],
        trusted_root=trusted_root,
        approved_trusted_root_sha256="c" * 64,
    )

    assert result == {
        "claim_eligible": True,
        "preregistration_sha256": manifest["preregistration_sha256"],
        "registration_receipt_sha256": receipt["receipt_sha256"],
        "status": "registered",
    }

    with pytest.raises(ValueError, match="registration_receipt_invalid"):
        preregistration.validate_registered_preregistration(
            manifest,
            repository_root=root,
            registry_commit=registry_commit,
            registry_path=registry_path,
            required_ancestor_commit=ancestor_commit,
            registration_receipt=[],
            expected_registration_receipt_sha256=receipt["receipt_sha256"],
            trusted_root=trusted_root,
            approved_trusted_root_sha256="c" * 64,
        )

    wrong_anchor = dict(receipt, registry_path="other-registry.json")
    wrong_anchor["receipt_sha256"] = (
        preregistration.registry.unsigned_document_digest(
            wrong_anchor,
            "receipt_sha256",
        )
    )
    with pytest.raises(ValueError, match="registration_anchor_mismatch"):
        preregistration.validate_registered_preregistration(
            manifest,
            repository_root=root,
            registry_commit=registry_commit,
            registry_path=registry_path,
            required_ancestor_commit=ancestor_commit,
            registration_receipt=wrong_anchor,
            expected_registration_receipt_sha256=wrong_anchor["receipt_sha256"],
            trusted_root=trusted_root,
            approved_trusted_root_sha256="c" * 64,
        )

    wrong_batch = dict(receipt, batch_id="other-batch")
    wrong_batch["receipt_sha256"] = (
        preregistration.registry.unsigned_document_digest(
            wrong_batch,
            "receipt_sha256",
        )
    )
    with pytest.raises(ValueError, match="registration receipt mismatch"):
        preregistration.validate_registered_preregistration(
            manifest,
            repository_root=root,
            registry_commit=registry_commit,
            registry_path=registry_path,
            required_ancestor_commit=ancestor_commit,
            registration_receipt=wrong_batch,
            expected_registration_receipt_sha256=wrong_batch["receipt_sha256"],
            trusted_root=trusted_root,
            approved_trusted_root_sha256="c" * 64,
        )


def test_v2_rejects_observation_window_mutation_after_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    manifest = preregistration.build_preregistration_v2(
        "product-value-batch-2",
        _workloads_v2(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
    )
    manifest["observation_window"]["observed_through"] = (
        "2026-09-09T00:00:00+00:00"
    )

    with pytest.raises(ValueError, match="preregistration_hash_mismatch"):
        validate_preregistration(manifest)


def test_v2_rejects_invalid_pilot_confirmatory_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    workloads = _workloads_v2()
    workloads[0]["evaluation_role"] = "confirmatory"

    with pytest.raises(ValueError, match="evaluation_role_coverage_invalid"):
        preregistration.build_preregistration_v2(
            "product-value-batch-2",
            workloads,
            observed_from="2026-09-01T00:00:00+00:00",
            observed_through="2026-09-08T00:00:00+00:00",
            registration_authority={"trusted_root_sha256": "c" * 64},
        )


def test_v2_rejects_invalid_registration_authority_digest() -> None:
    authority = _registration_authority()
    authority["trusted_root_sha256"] = "not-a-sha256"

    with pytest.raises(ValueError, match="registration_authority_invalid"):
        preregistration.build_preregistration_v2(
            "product-value-batch-2",
            _workloads_v2(),
            observed_from="2026-09-01T00:00:00+00:00",
            observed_through="2026-09-08T00:00:00+00:00",
            registration_authority=authority,
        )


def test_v3_binds_concrete_execution_contract_and_pair_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )

    manifest = preregistration.build_preregistration_v3(
        "product-value-batch-3",
        _workloads_v3(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract(),
    )

    assert manifest["schema_version"] == "omc-product-value-preregistration/v3"
    assert manifest["execution_contract"] == _execution_contract()
    assert manifest["workloads"][0]["pair_id"] == "pair-1"
    assert manifest["workloads"][0]["execution_order"] == ["omc", "baseline"]
    assert validate_preregistration(manifest) == manifest


def test_v5_binds_lineage_bounded_execution_bundle_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )

    manifest = preregistration.build_preregistration_v5(
        "product-value-batch-4",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )

    assert manifest["schema_version"] == "omc-product-value-preregistration/v5"
    assert manifest["claim_scope"] == "bounded_execution_value_v2"
    assert manifest["execution_contract"] == _execution_contract_v4()
    assert manifest["artifact_lineage"] == _artifact_lineage_v5()
    assert validate_preregistration(manifest) == manifest


def test_v6_binds_authoritative_holdout_evidence_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )

    manifest = preregistration.build_preregistration_v6(
        "product-value-holdout-a",
        _workloads_v4(),
        observed_from="2026-10-01T00:00:00+00:00",
        observed_through="2026-10-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
        evidence_contract=_evidence_contract_v6(),
    )

    assert manifest["schema_version"] == preregistration.SCHEMA_VERSION_V6
    assert manifest["claim_scope"] == "bounded_execution_holdout_v1"
    assert manifest["evidence_contract"] == _evidence_contract_v6()
    assert validate_preregistration(manifest) == manifest


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda contract: contract.update({"evidence_tier": "development"}),
            "evidence_contract_invalid",
        ),
        (
            lambda contract: contract.update(
                {"prior_holdout_report_sha256": "8" * 64}
            ),
            "evidence_contract_invalid",
        ),
        (
            lambda contract: contract.update(
                {
                    "replication_role": "replication",
                    "prior_holdout_report_sha256": None,
                }
            ),
            "evidence_contract_invalid",
        ),
    ],
)
def test_v6_rejects_ambiguous_holdout_evidence_contract(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    reason: str,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    evidence_contract = _evidence_contract_v6()
    mutation(evidence_contract)

    with pytest.raises(ValueError, match=reason):
        preregistration.build_preregistration_v6(
            "product-value-holdout-a",
            _workloads_v4(),
            observed_from="2026-10-01T00:00:00+00:00",
            observed_through="2026-10-08T00:00:00+00:00",
            registration_authority={"trusted_root_sha256": "c" * 64},
            execution_contract=_execution_contract_v4(),
            artifact_lineage=_artifact_lineage_v5(),
            evidence_contract=evidence_contract,
        )


def test_v6_rejects_hash_bound_evidence_contract_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    manifest = preregistration.build_preregistration_v6(
        "product-value-holdout-a",
        _workloads_v4(),
        observed_from="2026-10-01T00:00:00+00:00",
        observed_through="2026-10-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
        evidence_contract=_evidence_contract_v6(),
    )
    manifest["evidence_contract"]["selection_policy_sha256"] = "9" * 64

    with pytest.raises(ValueError, match="preregistration_hash_mismatch"):
        validate_preregistration(manifest)


def test_v6_rejects_inventory_or_selection_hash_not_derived_from_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )

    for field in (
        "holdout_workload_inventory_sha256",
        "selection_policy_sha256",
    ):
        contract = _evidence_contract_v6()
        contract[field] = "0" * 64
        with pytest.raises(ValueError, match="evidence_contract_binding_invalid"):
            preregistration.build_preregistration_v6(
                "product-value-holdout-a",
                _workloads_v4(),
                observed_from="2026-10-01T00:00:00+00:00",
                observed_through="2026-10-08T00:00:00+00:00",
                registration_authority={"trusted_root_sha256": "c" * 64},
                execution_contract=_execution_contract_v4(),
                artifact_lineage=_artifact_lineage_v5(),
                evidence_contract=contract,
            )


def test_v6_holdout_contract_requires_disjoint_development_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    development = preregistration.build_preregistration_v5(
        "product-value-development-v1",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )
    contract = _evidence_contract_v6()
    contract["development_batch_id"] = development["batch_id"]
    contract["development_preregistration_sha256"] = development[
        "preregistration_sha256"
    ]
    contract["development_workload_inventory_sha256"] = (
        preregistration.workload_inventory_sha256(development["workloads"])
    )
    holdout = preregistration.build_preregistration_v6(
        "product-value-holdout-a",
        _workloads_v4(),
        observed_from="2026-10-01T00:00:00+00:00",
        observed_through="2026-10-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
        evidence_contract=contract,
    )

    with pytest.raises(ValueError, match="holdout_workload_overlap"):
        preregistration.validate_holdout_evidence_contract(holdout, development)


def test_v6_holdout_contract_accepts_bound_disjoint_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    development = preregistration.build_preregistration_v5(
        "product-value-development-v1",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )
    holdout_workloads = _disjoint_workloads_v4()
    contract = _evidence_contract_v6(workloads=holdout_workloads)
    contract.update({
        "development_batch_id": development["batch_id"],
        "development_preregistration_sha256": development[
            "preregistration_sha256"
        ],
        "development_workload_inventory_sha256": (
            preregistration.workload_inventory_sha256(development["workloads"])
        ),
    })
    holdout = preregistration.build_preregistration_v6(
        "product-value-holdout-a",
        holdout_workloads,
        observed_from="2026-10-01T00:00:00+00:00",
        observed_through="2026-10-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
        evidence_contract=contract,
    )

    result = preregistration.validate_holdout_evidence_contract(
        holdout,
        development,
    )

    assert result["status"] == "validated"
    assert result["holdout_workload_inventory_sha256"] == contract[
        "holdout_workload_inventory_sha256"
    ]


def test_product_value_registry_record_embeds_public_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    manifest = preregistration.build_preregistration_v5(
        "product-value-batch-durable",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )

    record = preregistration.prepare_registry_record(manifest)

    assert record["schema_version"] == 2
    assert record["preregistration"] == manifest
    assert record["preregistration_sha256"] == manifest["preregistration_sha256"]


def test_registration_metadata_record_binds_validated_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    manifest = preregistration.build_preregistration_v5(
        "product-value-batch-durable",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )
    receipt = {
        "schema_version": 2,
        "status": "registered",
        "batch_id": manifest["batch_id"],
        "preregistration_sha256": manifest["preregistration_sha256"],
        "registry_commit": "4" * 40,
        "registry_path": ".omc/registry/product-value-batch-durable.json",
        "registered_at": "2026-08-31T00:00:00+00:00",
        "registration_evidence": {"type": "rfc3161"},
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = preregistration.registry.unsigned_document_digest(
        receipt,
        "receipt_sha256",
    )

    validation_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        preregistration,
        "validate_registered_preregistration",
        lambda *args, **kwargs: validation_calls.append(kwargs),
    )
    record = preregistration.prepare_registration_metadata_record(
        manifest,
        receipt,
        repository_root=".",
        registry_commit=receipt["registry_commit"],
        registry_path=receipt["registry_path"],
        required_ancestor_commit=manifest["artifact_lineage"][
            "implementation_commit"
        ],
        expected_registration_receipt_sha256=receipt["receipt_sha256"],
        trusted_root={"fixture": "trusted-root"},
        approved_trusted_root_sha256="c" * 64,
    )

    assert record["schema_version"] == "omc-product-value-registration-metadata/v1"
    assert record["registration_receipt_sha256"] == receipt["receipt_sha256"]
    assert record["registry_commit"] == receipt["registry_commit"]
    assert len(validation_calls) == 1

    tampered = deepcopy(receipt)
    tampered["registered_at"] = "2026-08-30T00:00:00+00:00"
    with pytest.raises(ValueError, match="registration_receipt_digest_mismatch"):
        preregistration.prepare_registration_metadata_record(
            manifest,
            tampered,
            repository_root=".",
            registry_commit=receipt["registry_commit"],
            registry_path=receipt["registry_path"],
            required_ancestor_commit=manifest["artifact_lineage"][
                "implementation_commit"
            ],
            expected_registration_receipt_sha256=receipt["receipt_sha256"],
            trusted_root={"fixture": "trusted-root"},
            approved_trusted_root_sha256="c" * 64,
        )


def test_registration_metadata_rejects_unverified_rfc3161_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _registration_authority()
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    monkeypatch.setattr(
        preregistration.rfc3161,
        "trust_identity",
        lambda *args, **kwargs: authority,
    )
    monkeypatch.setattr(
        preregistration.registry,
        "validate_registry_anchor",
        lambda *args, **kwargs: None,
    )
    manifest = preregistration.build_preregistration_v5(
        "product-value-batch-unverified-receipt",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority=authority,
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )
    receipt = {
        "schema_version": 2,
        "status": "registered",
        "batch_id": manifest["batch_id"],
        "preregistration_sha256": manifest["preregistration_sha256"],
        "registry_commit": "4" * 40,
        "registry_path": ".omc/registry/product-value-batch-unverified-receipt.json",
        "registered_at": "2026-08-31T00:00:00+00:00",
        "registration_evidence": {"type": "rfc3161"},
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = preregistration.registry.unsigned_document_digest(
        receipt,
        "receipt_sha256",
    )

    with pytest.raises(ValueError, match="RFC 3161 registration evidence fields"):
        preregistration.prepare_registration_metadata_record(
            manifest,
            receipt,
            repository_root=".",
            registry_commit=receipt["registry_commit"],
            registry_path=receipt["registry_path"],
            required_ancestor_commit=manifest["artifact_lineage"][
                "implementation_commit"
            ],
            expected_registration_receipt_sha256=receipt["receipt_sha256"],
            trusted_root={"fixture": "trusted-root"},
            approved_trusted_root_sha256="c" * 64,
        )


def test_receipt_record_cli_writes_durable_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration,
        "validate_registered_preregistration",
        lambda *args, **kwargs: None,
    )
    manifest = preregistration.build_preregistration_v5(
        "product-value-batch-durable",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority=_registration_authority(),
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )
    receipt = {
        "schema_version": 2,
        "status": "registered",
        "batch_id": manifest["batch_id"],
        "preregistration_sha256": manifest["preregistration_sha256"],
        "registry_commit": "4" * 40,
        "registry_path": ".omc/registry/product-value-batch-durable.json",
        "registered_at": "2026-08-31T00:00:00+00:00",
        "registration_evidence": {"type": "rfc3161"},
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = preregistration.registry.unsigned_document_digest(
        receipt,
        "receipt_sha256",
    )
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    trusted_root_path = tmp_path / "trusted-root.json"
    output_path = tmp_path / "receipt-record.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    trusted_root_path.write_text("{}", encoding="utf-8")

    result = preregistration.main(
        [
            "receipt-record",
            "--manifest",
            str(manifest_path),
            "--registration-receipt",
            str(receipt_path),
            "--repository-root",
            str(tmp_path),
            "--registry-commit",
            receipt["registry_commit"],
            "--registry-path",
            receipt["registry_path"],
            "--required-ancestor-commit",
            manifest["artifact_lineage"]["implementation_commit"],
            "--expected-registration-receipt-sha256",
            receipt["receipt_sha256"],
            "--trusted-root",
            str(trusted_root_path),
            "--approved-trusted-root-sha256",
            "c" * 64,
            "--out",
            str(output_path),
        ]
    )

    assert result == 0
    record = json.loads(output_path.read_text(encoding="utf-8"))
    assert record["registration_receipt_sha256"] == receipt["receipt_sha256"]


def test_v4_manifest_remains_valid_without_v5_artifact_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )

    manifest = preregistration.build_preregistration_v4(
        "product-value-legacy-batch-4",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
    )

    assert "artifact_lineage" not in manifest
    assert validate_preregistration(manifest) == manifest


def test_v5_rejects_missing_or_mutated_artifact_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    manifest = preregistration.build_preregistration_v5(
        "product-value-batch-4",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )

    manifest["artifact_lineage"].pop("implementation_commit")
    manifest.pop("preregistration_sha256")
    manifest["preregistration_sha256"] = canonical_sha256(manifest)

    with pytest.raises(ValueError, match="artifact_lineage_invalid"):
        validate_preregistration(manifest)


def test_v5_registration_rejects_ancestor_outside_artifact_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    monkeypatch.setattr(
        preregistration.registry,
        "validate_registry_anchor",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        preregistration.registry,
        "validate_sigstore_registration_receipt",
        lambda *args, **kwargs: None,
    )
    manifest = preregistration.build_preregistration_v5(
        "product-value-batch-5",
        _workloads_v4(),
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract=_execution_contract_v4(),
        artifact_lineage=_artifact_lineage_v5(),
    )
    receipt = {
        "registry_commit": "4" * 40,
        "registry_path": ".omc/registry/product-value-batch-5.json",
        "receipt_sha256": "5" * 64,
    }

    with pytest.raises(ValueError, match="artifact_lineage_commit_mismatch"):
        preregistration.validate_registered_preregistration(
            manifest,
            repository_root=".",
            registry_commit=receipt["registry_commit"],
            registry_path=receipt["registry_path"],
            required_ancestor_commit="4" * 40,
            registration_receipt=receipt,
            expected_registration_receipt_sha256=receipt["receipt_sha256"],
            trusted_root={"fixture": "trusted-root"},
            approved_trusted_root_sha256="c" * 64,
        )

    result = preregistration.validate_registered_preregistration(
        manifest,
        repository_root=".",
        registry_commit=receipt["registry_commit"],
        registry_path=receipt["registry_path"],
        required_ancestor_commit=manifest["artifact_lineage"][
            "implementation_commit"
        ],
        registration_receipt=receipt,
        expected_registration_receipt_sha256=receipt["receipt_sha256"],
        trusted_root={"fixture": "trusted-root"},
        approved_trusted_root_sha256="c" * 64,
    )
    assert result["claim_eligible"] is True


def test_prepare_v5_cli_writes_valid_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    workloads = tmp_path / "workloads.json"
    authority = tmp_path / "authority.json"
    contract = tmp_path / "execution-contract.json"
    lineage = tmp_path / "artifact-lineage.json"
    output = tmp_path / "manifest.json"
    workloads.write_text(json.dumps(_workloads_v4()), encoding="utf-8")
    authority.write_text(
        json.dumps({"trusted_root_sha256": "c" * 64}), encoding="utf-8"
    )
    contract.write_text(json.dumps(_execution_contract_v4()), encoding="utf-8")
    lineage.write_text(json.dumps(_artifact_lineage_v5()), encoding="utf-8")

    result = preregistration.main([
        "prepare-v5",
        "--batch-id",
        "product-value-batch-4",
        "--workloads",
        str(workloads),
        "--observed-from",
        "2026-09-01T00:00:00+00:00",
        "--observed-through",
        "2026-09-08T00:00:00+00:00",
        "--registration-authority",
        str(authority),
        "--execution-contract",
        str(contract),
        "--artifact-lineage",
        str(lineage),
        "--out",
        str(output),
    ])

    assert result == 0
    assert validate_preregistration(json.loads(output.read_text()))[
        "schema_version"
    ] == preregistration.SCHEMA_VERSION_V5


def test_prepare_v6_cli_writes_valid_holdout_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    inputs = {
        "workloads": _workloads_v4(),
        "authority": {"trusted_root_sha256": "c" * 64},
        "execution-contract": _execution_contract_v4(),
        "artifact-lineage": _artifact_lineage_v5(),
        "evidence-contract": _evidence_contract_v6(),
    }
    paths: dict[str, Path] = {}
    for name, payload in inputs.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "manifest.json"

    result = preregistration.main([
        "prepare-v6",
        "--batch-id",
        "product-value-holdout-a",
        "--workloads",
        str(paths["workloads"]),
        "--observed-from",
        "2026-10-01T00:00:00+00:00",
        "--observed-through",
        "2026-10-08T00:00:00+00:00",
        "--registration-authority",
        str(paths["authority"]),
        "--execution-contract",
        str(paths["execution-contract"]),
        "--artifact-lineage",
        str(paths["artifact-lineage"]),
        "--evidence-contract",
        str(paths["evidence-contract"]),
        "--out",
        str(output),
    ])

    assert result == 0
    manifest = validate_preregistration(json.loads(output.read_text()))
    assert manifest["schema_version"] == preregistration.SCHEMA_VERSION_V6
    assert manifest["evidence_contract"]["evidence_tier"] == "holdout"


def test_omc_cli_accepts_prepare_v5_command() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-preregistration",
            "prepare-v5",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert "invalid choice" not in result.stderr
    assert "--batch-id" in result.stderr


def test_omc_cli_accepts_prepare_v6_command() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-preregistration",
            "prepare-v6",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert "invalid choice" not in result.stderr
    assert "--batch-id" in result.stderr
    assert "--evidence-contract" in result.stderr


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda contract, workloads: contract["execution_bundle"].pop(
                "scheduler_sha256"
            ),
            "execution_contract_invalid",
        ),
        (
            lambda contract, workloads: contract["environment_policy"].update(
                {"preparation_cost_included": True}
            ),
            "execution_contract_invalid",
        ),
        (
            lambda contract, workloads: workloads[0].update(
                {"environment_receipt_sha256": "invalid"}
            ),
            "environment_receipt_sha256_invalid",
        ),
    ],
)
def test_v4_rejects_incomplete_execution_provenance(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    reason: str,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    contract = _execution_contract_v4()
    workloads = _workloads_v4()
    mutation(contract, workloads)

    with pytest.raises(ValueError, match=reason):
        preregistration.build_preregistration_v5(
            "product-value-batch-4",
            workloads,
            observed_from="2026-09-01T00:00:00+00:00",
            observed_through="2026-09-08T00:00:00+00:00",
            registration_authority={"trusted_root_sha256": "c" * 64},
            execution_contract=contract,
            artifact_lineage=_artifact_lineage_v5(),
        )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda contract, workloads: contract["provider_snapshot"].pop("model"),
            "execution_contract_invalid",
        ),
        (
            lambda contract, workloads: workloads[1].update(
                {"pair_id": workloads[0]["pair_id"]}
            ),
            "pair_id_duplicate",
        ),
        (
            lambda contract, workloads: workloads[0].update(
                {"execution_order": ["omc", "omc"]}
            ),
            "execution_order_invalid",
        ),
    ],
)
def test_v3_rejects_incomplete_or_ambiguous_execution_binding(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    reason: str,
) -> None:
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    contract = _execution_contract()
    workloads = _workloads_v3()
    mutation(contract, workloads)

    with pytest.raises(ValueError, match=reason):
        preregistration.build_preregistration_v3(
            "product-value-batch-3",
            workloads,
            observed_from="2026-09-01T00:00:00+00:00",
            observed_through="2026-09-08T00:00:00+00:00",
            registration_authority={"trusted_root_sha256": "c" * 64},
            execution_contract=contract,
        )


def test_v1_cannot_enter_registration_flow() -> None:
    manifest = build_preregistration("product-value-batch-1", _workloads())

    with pytest.raises(ValueError, match="registration_requires_v2"):
        preregistration.prepare_registry_record(manifest)


@pytest.mark.parametrize(
    ("command", "extra_args"),
    [
        (
            "prepare-receipt",
            [
                "--registration-evidence",
                "payload.json",
                "--out",
                "receipt.json",
            ],
        ),
        (
            "validate-registration",
            [
                "--repository-root",
                ".",
                "--required-ancestor-commit",
                "b" * 40,
                "--registration-receipt",
                "payload.json",
                "--expected-registration-receipt-sha256",
                "c" * 64,
            ],
        ),
        (
            "receipt-record",
            [
                "--repository-root",
                ".",
                "--required-ancestor-commit",
                "b" * 40,
                "--registration-receipt",
                "payload.json",
                "--expected-registration-receipt-sha256",
                "c" * 64,
                "--out",
                "receipt-record.json",
            ],
        ),
    ],
)
def test_omc_cli_forwards_registration_commands_and_arguments(
    tmp_path: Path,
    command: str,
    extra_args: list[str],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    payload_path = tmp_path / "payload.json"
    trusted_root_path = tmp_path / "trusted-root.json"
    manifest_path.write_text(
        json.dumps(build_preregistration("product-value-batch-1", _workloads())),
        encoding="utf-8",
    )
    payload_path.write_text("{}", encoding="utf-8")
    trusted_root_path.write_text("{}", encoding="utf-8")
    resolved_extra_args = [
        str(tmp_path / value) if value.endswith(".json") else value
        for value in extra_args
    ]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-preregistration",
            command,
            "--manifest",
            str(manifest_path),
            "--registry-commit",
            "a" * 40,
            "--registry-path",
            ".omc/registry/product-value-batch-1.json",
            "--trusted-root",
            str(trusted_root_path),
            "--approved-trusted-root-sha256",
            "d" * 64,
            *resolved_extra_args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "reason_code": "registration_requires_v2",
        "status": "blocked",
    }
