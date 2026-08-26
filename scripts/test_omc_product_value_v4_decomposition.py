from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest

import omc_product_value_v4_decomposition as decomposition
from omc_n_child_scheduler import _validate_grant


def _fixture(tmp_path: Path) -> dict[str, object]:
    workloads = []
    packets = {}
    source_roots = {}
    specs = {}
    for index in range(1, 7):
        workload_id = f"pv-{index:02d}"
        repo_alias = f"source-{index}"
        source = tmp_path / repo_alias
        (source / "src").mkdir(parents=True)
        for child_index in range(1, 4):
            (source / "src" / f"part-{child_index}").mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "v4@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "V4 Test"],
            check=True,
        )
        (source / "src" / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(source), "commit", "-qm", "fixture"], check=True
        )
        source_commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        source_roots[repo_alias] = {
            "path": str(source),
            "identity_sha256": f"{index:x}" * 64,
        }
        workloads.append(
            {
                "workload_id": workload_id,
                "repo_alias": repo_alias,
                "repository_identity_sha256": f"{index:x}" * 64,
                "source_commit": source_commit,
                "expected_child_count": 3,
            }
        )
        packets[workload_id] = {
            "workload_id": workload_id,
            "source_commit": source_commit,
            "request": f"request {index}",
            "dod": f"dod {index}",
            "verification": {"argv": ["python3", "-m", "pytest"]},
        }
        specs[workload_id] = {
            "dag_id": f"product-value-{workload_id}",
            "max_parallelism": 2,
            "children": [
                {
                    "child_id": f"{workload_id}-child-{child_index}",
                    "depends_on": (
                        [] if child_index < 3 else [f"{workload_id}-child-1"]
                    ),
                    "scope_paths": [f"src/part-{child_index}/"],
                    "executor": "codex",
                    "prompt": f"request {index}: step {child_index}",
                    "max_total_tokens": 1000,
                    "max_total_elapsed_sec": 30,
                    "max_output_chars": 2000,
                }
                for child_index in range(1, 4)
            ],
        }
    return {
        "corpus_approval": {
            "schema_version": "omc-product-value-corpus-approval/v1",
            "decision": "approved",
            "batch_id": "product-value-v2-r1",
            "public_payload_sha256": "a" * 64,
            "workload_count": 6,
        },
        "workloads": workloads,
        "packets": packets,
        "source_roots": source_roots,
        "decomposition_specs": specs,
        "limits": {
            "max_total_tokens": 3000,
            "max_total_elapsed_sec": 60,
            "max_output_chars": 6000,
        },
    }


def test_prepare_then_approve_issues_scheduler_eligible_v2_grants(
    tmp_path: Path,
) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    packet = decomposition.prepare_decomposition_approval(
        **inputs,
        approval_expires_at=expires_at,
    )

    assert packet["status"] == "approval_pending"
    assert packet["execution_allowed"] is False
    assert len(packet["decompositions"]) == 6
    assert all(
        proposal["status"] == "ready_for_approval"
        and proposal["execution_allowed"] is False
        for proposal in packet["decompositions"].values()
    )

    receipt = {
        "schema_version": "omc-product-value-v4-decomposition-approval/v1",
        "status": "approved",
        "operator_confirmed": True,
        "approval_id": "operator-approval-1",
        "approval_packet_sha256": packet["approval_packet_sha256"],
        "approval_expires_at": expires_at,
    }
    executions = decomposition.issue_approved_executions(
        approval_packet=packet,
        approval_receipt=receipt,
        source_roots=inputs["source_roots"],
    )

    assert set(executions) == {f"pv-{index:02d}" for index in range(1, 7)}
    assert all(
        execution["grant"]["schema_version"] == "omc-n-child-dag/v2"
        and execution["grant"]["scheduler_eligible"] is True
        and execution["prompts"] == execution["grant"]["child_prompts"]
        for execution in executions.values()
    )


def test_issue_rejects_packet_tampering(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    packet = decomposition.prepare_decomposition_approval(
        **inputs,
        approval_expires_at=expires_at,
    )
    receipt = {
        "schema_version": "omc-product-value-v4-decomposition-approval/v1",
        "status": "approved",
        "operator_confirmed": True,
        "approval_id": "operator-approval-1",
        "approval_packet_sha256": packet["approval_packet_sha256"],
        "approval_expires_at": expires_at,
    }
    tampered = deepcopy(packet)
    tampered["decompositions"]["pv-01"]["children"][0]["prompt"] = "tampered"

    with pytest.raises(ValueError, match="decomposition_approval_packet_invalid"):
        decomposition.issue_approved_executions(
            approval_packet=tampered,
            approval_receipt=receipt,
            source_roots=inputs["source_roots"],
        )


def test_issue_rejects_legacy_packet_without_target_binding(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    packet = decomposition.prepare_decomposition_approval(
        **inputs,
        approval_expires_at=expires_at,
    )
    legacy = deepcopy(packet)
    for proposal in legacy["decompositions"].values():
        proposal.pop("target_binding")
        proposal.pop("decomposition_sha256")
        proposal["decomposition_sha256"] = decomposition.freeze.canonical_sha256(
            proposal
        )
    legacy.pop("approval_packet_sha256")
    legacy["approval_packet_sha256"] = decomposition.freeze.canonical_sha256(legacy)
    receipt = {
        "schema_version": decomposition.SCHEMA_VERSION,
        "status": "approved",
        "operator_confirmed": True,
        "approval_id": "legacy-operator-approval",
        "approval_packet_sha256": legacy["approval_packet_sha256"],
        "approval_expires_at": expires_at,
    }

    with pytest.raises(ValueError, match="decomposition_approval_packet_invalid"):
        decomposition.issue_approved_executions(
            approval_packet=legacy,
            approval_receipt=receipt,
            source_roots=inputs["source_roots"],
        )


def test_prepare_rejects_budget_or_scope_drift(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    inputs["decomposition_specs"]["pv-01"]["children"][1]["scope_paths"] = [
        "src/part-1/nested"
    ]

    with pytest.raises(ValueError, match="decomposition_proposal_invalid"):
        decomposition.prepare_decomposition_approval(
            **inputs,
            approval_expires_at=expires_at,
        )


def test_cli_round_trip_requires_exact_approval_hash(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    packet = decomposition.prepare_decomposition_approval(
        **inputs,
        approval_expires_at=expires_at,
    )

    with pytest.raises(ValueError, match="decomposition_approval_receipt_invalid"):
        decomposition.validate_approval_receipt(
            packet,
            {
                "schema_version": decomposition.SCHEMA_VERSION,
                "status": "approved",
                "operator_confirmed": True,
                "approval_id": "operator-approval-1",
                "approval_packet_sha256": "b" * 64,
                "approval_expires_at": expires_at,
            },
        )


def test_prepare_rejects_cycle_before_requesting_approval(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    children = inputs["decomposition_specs"]["pv-01"]["children"]
    children[0]["depends_on"] = [children[2]["child_id"]]

    with pytest.raises(ValueError, match="decomposition_proposal_invalid"):
        decomposition.prepare_decomposition_approval(
            **inputs,
            approval_expires_at=expires_at,
        )


def test_prepare_rejects_duplicate_dag_id(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    inputs["decomposition_specs"]["pv-02"]["dag_id"] = inputs[
        "decomposition_specs"
    ]["pv-01"]["dag_id"]

    with pytest.raises(ValueError, match="decomposition_identifier_collision"):
        decomposition.prepare_decomposition_approval(
            **inputs,
            approval_expires_at=expires_at,
        )


def test_issue_rejects_source_commit_drift_after_approval(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    packet = decomposition.prepare_decomposition_approval(
        **inputs,
        approval_expires_at=expires_at,
    )
    receipt = {
        "schema_version": decomposition.SCHEMA_VERSION,
        "status": "approved",
        "operator_confirmed": True,
        "approval_id": "operator-approval-1",
        "approval_packet_sha256": packet["approval_packet_sha256"],
        "approval_expires_at": expires_at,
    }
    source = Path(inputs["source_roots"]["source-1"]["path"])
    (source / "src" / "README.md").write_text("drift\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-qm", "drift"], check=True
    )

    with pytest.raises(ValueError, match="decomposition_source_root_invalid"):
        decomposition.issue_approved_executions(
            approval_packet=packet,
            approval_receipt=receipt,
            source_roots=inputs["source_roots"],
        )


def test_issued_grant_accepts_clean_fresh_clone_of_approved_commit(
    tmp_path: Path,
) -> None:
    inputs = _fixture(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    packet = decomposition.prepare_decomposition_approval(
        **inputs,
        approval_expires_at=expires_at,
    )
    receipt = {
        "schema_version": decomposition.SCHEMA_VERSION,
        "status": "approved",
        "operator_confirmed": True,
        "approval_id": "operator-approval-1",
        "approval_packet_sha256": packet["approval_packet_sha256"],
        "approval_expires_at": expires_at,
    }
    executions = decomposition.issue_approved_executions(
        approval_packet=packet,
        approval_receipt=receipt,
        source_roots=inputs["source_roots"],
    )
    workload = inputs["workloads"][0]
    source = Path(inputs["source_roots"]["source-1"]["path"])
    fresh = tmp_path / "fresh-clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(source), str(fresh)],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(fresh),
            "checkout",
            "-q",
            "--detach",
            workload["source_commit"],
        ],
        check=True,
    )
    execution = executions[workload["workload_id"]]

    proposal, _expires_at, error = _validate_grant(
        execution["grant"],
        trusted_target=fresh,
        prompts=execution["prompts"],
        now=lambda: datetime.now(timezone.utc),
    )

    assert error is None
    assert proposal is not None
