from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

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
