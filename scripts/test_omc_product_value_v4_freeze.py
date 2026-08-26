from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import omc_product_value_v4_freeze as freeze


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _inputs(tmp_path: Path) -> dict[str, object]:
    workloads = []
    packets = {}
    executions = {}
    environments = {}
    source_roots = {}
    for index in range(1, 7):
        workload_id = f"pv-{index:02d}"
        alias = f"source-{index}"
        source = tmp_path / alias
        source.mkdir()
        (source / "environment.lock").write_text("frozen\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "freeze@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "Freeze Test"],
            check=True,
        )
        subprocess.run(["git", "-C", str(source), "add", "environment.lock"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "commit", "-qm", "fixture"],
            check=True,
        )
        source_commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        packet = {
            "schema_version": "omc-product-value-execution-packet/v1",
            "workload_id": workload_id,
            "repo_alias": alias,
            "source_commit": source_commit,
            "request": f"request {index}",
            "dod": f"dod {index}",
            "verification": {"argv": ["python3", "-m", "pytest"]},
            "arms": {
                "omc": {"prompt": f"request {index}", "mode": "bounded_n_child"},
                "baseline": {"prompt": f"request {index}", "mode": "single_agent"},
            },
        }
        packets[workload_id] = packet
        workloads.append({
            "workload_id": workload_id,
            "repo_alias": alias,
            "repository_identity_sha256": f"{index:x}" * 64,
            "implementation_type": "python" if index < 4 else "frontend",
            "work_class": "implementation",
            "source_commit": packet["source_commit"],
            "request_sha256": _sha(packet["request"]),
            "dod_sha256": _sha(packet["dod"]),
            "verification_sha256": _sha(packet["verification"]),
            "expected_child_count": 3,
            "scope_paths": ["src/"],
            "evaluation_role": "pilot" if index == 1 else "confirmatory",
            "pair_id": f"pair-{index}",
            "execution_order": ["omc", "baseline"] if index % 2 else ["baseline", "omc"],
            "execution_packet_sha256": _sha(packet),
        })
        prompts = {f"child-{child}": f"step {child}" for child in range(1, 4)}
        executions[workload_id] = {
            "grant": {
                "schema_version": "omc-n-child-dag/v2",
                "mode": "n_child_dag_grant",
                "status": "ready",
                "execution_allowed": True,
                "scheduler_eligible": True,
                "children": [
                    {
                        "child_id": child_id,
                        "depends_on": [],
                        "scope_paths": ["src/"],
                    }
                    for child_id in prompts
                ],
                "child_prompts": prompts,
                "max_total_tokens": 100,
                "max_total_elapsed_sec": 30,
                "max_output_chars": 1000,
            },
            "prompts": prompts,
        }
        runtime = tmp_path / f"python-{index}"
        runtime.write_text("runtime\n", encoding="utf-8")
        cache = tmp_path / f"cache-{index}"
        cache.mkdir()
        cache.chmod(0o555)
        environments[workload_id] = {
            "schema_version": "omc-product-value-environment/v3",
            "source_commit": packet["source_commit"],
            "dependency_lock_path": "environment.lock",
            "dependency_lock_sha256": freeze.file_sha256(source / "environment.lock"),
            "cache_sha256": freeze.cache_inventory_sha256(cache),
            "runtime_identity_path": str(runtime.resolve()),
            "runtime_identity_sha256": freeze.file_sha256(runtime),
            "cache_path": str(cache.resolve()),
            "readiness": {"argv": [str(runtime.resolve()), "--version"]},
        }
        source_roots[alias] = {
            "path": str(source),
            "identity_sha256": f"{index:x}" * 64,
        }
    bundle = {}
    for name in freeze.BUNDLE_PATH_FIELDS:
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        bundle[name] = path
    return {
        "workloads": workloads,
        "packets": packets,
        "executions": executions,
        "environments": environments,
        "source_roots": source_roots,
        "bundle": bundle,
    }


def test_freeze_candidate_is_deterministic_and_binds_complete_bundle(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)

    first = freeze.freeze_v4_candidate(
        **inputs,
        provider_snapshot={
            "provider_family": "codex",
            "model": "gpt-test",
            "reasoning_profile": "high",
            "backend_sha256": "a" * 64,
        },
        limits={
            "max_total_tokens": 100,
            "max_total_elapsed_sec": 30,
            "max_output_chars": 1000,
        },
        grant_validator=lambda *_args, **_kwargs: True,
    )
    second = freeze.freeze_v4_candidate(
        **inputs,
        provider_snapshot={
            "provider_family": "codex",
            "model": "gpt-test",
            "reasoning_profile": "high",
            "backend_sha256": "a" * 64,
        },
        limits={
            "max_total_tokens": 100,
            "max_total_elapsed_sec": 30,
            "max_output_chars": 1000,
        },
        grant_validator=lambda *_args, **_kwargs: True,
    )

    assert first == second
    assert first["status"] == "candidate_frozen"
    assert set(first["execution_contract"]["execution_bundle"]) == {
        "acceptance_runner_sha256",
        "arm_adapter_sha256",
        "scheduler_sha256",
        "executor_shadow_sha256",
        "provider_adapter_sha256",
    }
    assert len(first["packets"]) == 6
    assert all(
        packet["schema_version"] == "omc-product-value-execution-packet/v2"
        for packet in first["packets"].values()
    )
    output = tmp_path / "frozen-candidate"
    receipt = freeze.write_v4_candidate(first, output)
    assert receipt["candidate_sha256"] == first["candidate_sha256"]
    assert json.loads((output / "candidate.json").read_text()) == first
    assert len(list((output / "packets").glob("*.json"))) == 6

    with pytest.raises(ValueError, match="freeze_output_exists"):
        freeze.write_v4_candidate(first, output)


def test_freeze_candidate_rejects_missing_environment(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs["environments"].pop("pv-06")

    with pytest.raises(ValueError, match="freeze_input_coverage_invalid"):
        freeze.freeze_v4_candidate(
            **inputs,
            provider_snapshot={
                "provider_family": "codex",
                "model": "gpt-test",
                "reasoning_profile": "high",
                "backend_sha256": "a" * 64,
            },
            limits={
                "max_total_tokens": 100,
                "max_total_elapsed_sec": 30,
                "max_output_chars": 1000,
            },
            grant_validator=lambda *_args, **_kwargs: True,
        )


def test_freeze_candidate_rejects_source_commit_not_in_repository(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    packet = inputs["packets"]["pv-01"]
    workload = inputs["workloads"][0]
    environment = inputs["environments"]["pv-01"]
    packet["source_commit"] = "f" * 40
    workload["source_commit"] = packet["source_commit"]
    workload["execution_packet_sha256"] = _sha(packet)
    environment["source_commit"] = packet["source_commit"]

    with pytest.raises(ValueError, match="freeze_source_commit_unavailable"):
        freeze.freeze_v4_candidate(
            **inputs,
            provider_snapshot={
                "provider_family": "codex",
                "model": "gpt-test",
                "reasoning_profile": "high",
                "backend_sha256": "a" * 64,
            },
            limits={
                "max_total_tokens": 100,
                "max_total_elapsed_sec": 30,
                "max_output_chars": 1000,
            },
            grant_validator=lambda *_args, **_kwargs: True,
        )
