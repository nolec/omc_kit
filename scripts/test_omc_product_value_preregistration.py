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
