from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import omc_product_value_acceptance as acceptance
import omc_product_value_preregistration as preregistration


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(root: Path, name: str) -> tuple[Path, str]:
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "acceptance@example.com")
    _git(repo, "config", "user.name", "Acceptance Test")
    (repo / "src").mkdir()
    (repo / "src" / "value.txt").write_text("baseline\n", encoding="utf-8")
    (repo / "requirements.lock").write_text("dependency==1.0\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return repo, _git(repo, "rev-parse", "HEAD")


def _packet(workload_id: str, repo_alias: str, source_commit: str) -> dict[str, object]:
    request = f"Implement {workload_id}"
    dod = f"Verify {workload_id}"
    verification = {
        "argv": [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('src/value.txt').is_file()",
        ]
    }
    return {
        "schema_version": "omc-product-value-execution-packet/v1",
        "workload_id": workload_id,
        "repo_alias": repo_alias,
        "source_commit": source_commit,
        "request": request,
        "dod": dod,
        "verification": verification,
        "arms": {
            "omc": {"prompt": request, "mode": "bounded_n_child"},
            "baseline": {"prompt": request, "mode": "single_agent"},
        },
    }


def _v4_manifest_and_packet(monkeypatch: pytest.MonkeyPatch):
    children = [
        {"child_id": "child-api", "depends_on": [], "scope_paths": ["src/api"]},
        {"child_id": "child-test", "depends_on": [], "scope_paths": ["tests"]},
        {
            "child_id": "child-ui",
            "depends_on": ["child-api"],
            "scope_paths": ["src/ui"],
        },
    ]
    prompts = {
        "child-api": "Implement the API change.",
        "child-test": "Add regression tests.",
        "child-ui": "Connect the UI to the API.",
    }
    grant = {
        "schema_version": "omc-n-child-dag/v2",
        "mode": "n_child_dag_grant",
        "status": "ready",
        "execution_allowed": True,
        "scheduler_eligible": True,
        "children": children,
        "child_prompts": deepcopy(prompts),
        "max_total_tokens": 10_000,
        "max_total_elapsed_sec": 600,
        "max_output_chars": 100_000,
    }
    environment = {
        "schema_version": "omc-product-value-environment/v3",
        "source_commit": "1" * 40,
        "dependency_lock_path": "requirements.lock",
        "dependency_lock_sha256": "1" * 64,
        "cache_sha256": "2" * 64,
        "runtime_identity_path": sys.executable,
        "runtime_identity_sha256": "3" * 64,
        "cache_path": "/readonly/omc-product-value-cache",
        "readiness": {"argv": []},
    }
    environment_probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": environment["source_commit"],
        "dependency_lock_sha256": environment["dependency_lock_sha256"],
        "cache_sha256": environment["cache_sha256"],
        "runtime_identity_sha256": environment["runtime_identity_sha256"],
        "cache_path": environment["cache_path"],
        "cache_readonly": True,
    }
    environment["readiness"] = {
        "argv": [sys.executable, "-c", f"print({json.dumps(json.dumps(environment_probe))})"]
    }
    packet = {
        "schema_version": "omc-product-value-execution-packet/v3",
        "workload_id": "workload-1",
        "repo_alias": "repo-a",
        "source_commit": "1" * 40,
        "request": "Implement the feature.",
        "dod": "The API, UI, and regression tests pass.",
        "verification": {"argv": [sys.executable, "-c", "raise SystemExit(0)"]},
        "omc_execution": {"grant": grant, "prompts": deepcopy(prompts)},
        "baseline_execution_brief": "",
        "environment_receipt": environment,
        "direct_surface_verification": {
            "path": "surface-verification.json",
            "sha256": "9" * 64,
        },
    }
    packet["baseline_execution_brief"] = acceptance.build_baseline_execution_brief(
        packet["request"], packet["dod"], children, prompts
    )
    workload = {
        "workload_id": "workload-1",
        "repo_alias": "repo-a",
        "repository_identity_sha256": "a" * 64,
        "implementation_type": "api",
        "work_class": "implementation",
        "source_commit": "1" * 40,
        "request_sha256": acceptance.canonical_sha256(packet["request"]),
        "dod_sha256": acceptance.canonical_sha256(packet["dod"]),
        "verification_sha256": acceptance.canonical_sha256(packet["verification"]),
        "expected_child_count": 3,
        "scope_paths": ["src/", "tests/"],
        "evaluation_role": "pilot",
        "pair_id": "pair-1",
        "execution_order": ["omc", "baseline"],
        "execution_packet_sha256": acceptance.canonical_sha256(packet),
        "environment_receipt_sha256": acceptance.canonical_sha256(environment),
    }
    workloads = []
    for index in range(1, 7):
        row = deepcopy(workload)
        row.update({
            "workload_id": f"workload-{index}",
            "repo_alias": "repo-a" if index <= 3 else "repo-b",
            "repository_identity_sha256": "a" * 64 if index <= 3 else "b" * 64,
            "implementation_type": "api" if index % 2 else "ui",
            "source_commit": f"{index}" * 40,
            "evaluation_role": "pilot" if index == 1 else "confirmatory",
            "pair_id": f"pair-{index}",
            "execution_order": ["omc", "baseline"] if index % 2 else ["baseline", "omc"],
            "execution_packet_sha256": acceptance.canonical_sha256(packet) if index == 1 else f"{index + 3:x}" * 64,
            "environment_receipt_sha256": acceptance.canonical_sha256(environment) if index == 1 else f"{index + 8:x}" * 64,
        })
        if index != 1:
            row.update({
                "request_sha256": f"{index}" * 64,
                "dod_sha256": f"{index + 1}" * 64,
                "verification_sha256": f"{index + 2}" * 64,
            })
        workloads.append(row)
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    manifest = preregistration.build_preregistration_v4(
        "product-value-batch-4",
        workloads,
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority={"trusted_root_sha256": "c" * 64},
        execution_contract={
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
        },
    )
    return manifest, deepcopy(manifest["workloads"][0]), deepcopy(packet)


def test_v4_packet_binds_equivalent_omc_and_baseline_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)

    assert acceptance.validate_execution_packet(manifest, workload, packet) == packet


def test_v4_packet_preserves_legacy_v2_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    packet["schema_version"] = "omc-product-value-execution-packet/v2"
    packet.pop("direct_surface_verification")
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)

    assert acceptance.validate_execution_packet(manifest, workload, packet) == packet


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda packet: packet["omc_execution"]["prompts"].update(
                {"child-api": "changed"}
            ),
            "execution_packet_omc_input_invalid",
        ),
        (
            lambda packet: packet.update({"baseline_execution_brief": "incomplete"}),
            "execution_packet_baseline_brief_invalid",
        ),
        (
            lambda packet: packet["environment_receipt"].update(
                {"cache_sha256": "f" * 64}
            ),
            "execution_packet_environment_mismatch",
        ),
        (
            lambda packet: packet["direct_surface_verification"].update(
                {"sha256": "invalid"}
            ),
            "execution_packet_invalid",
        ),
        (
            lambda packet: packet["omc_execution"]["grant"]["children"][0].update(
                {"depends_on": ["missing-child"]}
            ),
            "execution_packet_omc_input_invalid",
        ),
        (
            lambda packet: (
                packet["omc_execution"]["grant"]["children"][0].update(
                    {"depends_on": ["child-ui"]}
                ),
                packet["omc_execution"]["grant"]["children"][2].update(
                    {"depends_on": ["child-api"]}
                ),
            ),
            "execution_packet_omc_input_invalid",
        ),
        (
            lambda packet: packet["omc_execution"]["grant"]["children"][0].update(
                {"scope_paths": ["outside/"]}
            ),
            "execution_packet_omc_input_invalid",
        ),
    ],
)
def test_v4_packet_rejects_execution_information_or_environment_tampering(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    reason: str,
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    mutation(packet)
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)

    with pytest.raises(ValueError, match=reason):
        acceptance.validate_execution_packet(manifest, workload, packet)


def test_v4_packet_binds_runtime_identity_to_readiness_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    unrelated_runtime = tmp_path / "unrelated-runtime"
    unrelated_runtime.write_text("not the readiness executable\n", encoding="utf-8")
    environment = packet["environment_receipt"]
    environment["runtime_identity_path"] = str(unrelated_runtime)
    environment["runtime_identity_sha256"] = acceptance.canonical_file_sha256(
        unrelated_runtime
    )
    workload["environment_receipt_sha256"] = acceptance.canonical_sha256(environment)
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)

    with pytest.raises(ValueError, match="execution_packet_environment_mismatch"):
        acceptance.validate_execution_packet(manifest, workload, packet)


@pytest.mark.parametrize(
    "readiness",
    [None, {}, {"argv": []}, {"argv": [None]}],
)
def test_v4_packet_rejects_malformed_environment_readiness(
    monkeypatch: pytest.MonkeyPatch,
    readiness,
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    packet["environment_receipt"]["readiness"] = readiness
    workload["environment_receipt_sha256"] = acceptance.canonical_sha256(
        packet["environment_receipt"]
    )
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)

    with pytest.raises(ValueError, match="execution_packet_environment_mismatch"):
        acceptance.validate_execution_packet(manifest, workload, packet)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_a, commit_a = _repo(tmp_path, "repo-a")
    repo_b, commit_b = _repo(tmp_path, "repo-b")
    packets: list[dict[str, object]] = []
    workloads: list[dict[str, object]] = []
    for index in range(1, 7):
        alias = "repo-a" if index <= 3 else "repo-b"
        identity = "a" * 64 if alias == "repo-a" else "b" * 64
        source_commit = commit_a if alias == "repo-a" else commit_b
        packet = _packet(f"workload-{index}", alias, source_commit)
        packets.append(packet)
        workloads.append({
            "workload_id": f"workload-{index}",
            "repo_alias": alias,
            "repository_identity_sha256": identity,
            "implementation_type": "api" if index % 2 else "ui",
            "work_class": "implementation",
            "source_commit": source_commit,
            "request_sha256": acceptance.canonical_sha256(packet["request"]),
            "dod_sha256": acceptance.canonical_sha256(packet["dod"]),
            "verification_sha256": acceptance.canonical_sha256(
                packet["verification"]
            ),
            "expected_child_count": 3 + (index % 3),
            "scope_paths": ["src/"],
            "evaluation_role": "pilot" if index == 1 else "confirmatory",
            "pair_id": f"pair-{index}",
            "execution_order": (
                ["omc", "baseline"] if index % 2 else ["baseline", "omc"]
            ),
            "execution_packet_sha256": acceptance.canonical_sha256(packet),
        })
    authority = {"trusted_root_sha256": "c" * 64}
    monkeypatch.setattr(
        preregistration.rfc3161,
        "validate_trust_identity",
        lambda candidate: None,
    )
    manifest = preregistration.build_preregistration_v3(
        "product-value-batch-3",
        workloads,
        observed_from="2026-09-01T00:00:00+00:00",
        observed_through="2026-09-08T00:00:00+00:00",
        registration_authority=authority,
        execution_contract={
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
        },
    )
    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    for packet in packets:
        (packet_root / f"{packet['workload_id']}.json").write_text(
            json.dumps(packet), encoding="utf-8"
        )
    source_roots = {
        "repo-a": {"path": str(repo_a), "identity_sha256": "a" * 64},
        "repo-b": {"path": str(repo_b), "identity_sha256": "b" * 64},
    }
    registration = {
        "claim_eligible": True,
        "status": "registered",
        "preregistration_sha256": manifest["preregistration_sha256"],
        "registration_receipt_sha256": "e" * 64,
    }
    return manifest, packet_root, source_roots, registration


def _executor(*, arm: str, arm_artifact: Path, expected_child_count: int, **_kwargs) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "completed",
        "reason_code": "execution_completed",
        "elapsed_sec": 1.0 if arm == "omc" else 2.0,
        "output": f"{arm} output",
        "token_usage": {
            "input_tokens": 60 if arm == "omc" else 80,
            "output_tokens": 40,
            "total_tokens": 100 if arm == "omc" else 120,
        },
        "intervention_events": [],
        "review_findings": [],
        "duplicate_executions": 0,
        "budget_violations": 0,
    }
    if arm == "omc":
        dag = arm_artifact / "dag-ledger.json"
        child = arm_artifact / "child-ledger.json"
        dag.write_text('{"status":"completed"}\n', encoding="utf-8")
        child.write_text('{"entries":[]}\n', encoding="utf-8")
        result.update({
            "executed_child_count": expected_child_count,
            "dag_ledger_sha256": acceptance.canonical_file_sha256(dag),
            "child_ledger_sha256": acceptance.canonical_file_sha256(child),
        })
    return result


def _failed_executor(**_kwargs) -> dict[str, object]:
    return {
        "status": "parent_review",
        "reason_code": "provider_failed",
        "elapsed_sec": 0.0,
        "output": "",
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
        "intervention_events": [],
        "review_findings": [],
        "duplicate_executions": 0,
        "budget_violations": 0,
    }


def _paired_clock(manifest: dict[str, object]):
    ticks: list[float] = []
    cursor = 0.0
    for workload in manifest["workloads"]:
        for arm in workload["execution_order"]:
            duration = 1.0 if arm == "omc" else 2.0
            ticks.extend(
                (
                    cursor,
                    cursor + 0.05,
                    cursor + 0.1,
                    cursor + duration / 2,
                    cursor + duration * 0.75,
                    cursor + duration,
                )
            )
            cursor += 10.0
    values = iter(ticks)
    return lambda: next(values)


def test_registration_failure_blocks_all_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    calls: list[str] = []

    with pytest.raises(ValueError, match="registration_blocked"):
        acceptance.run_product_value_phase(
            manifest,
            registration,
            packet_root=packet_root,
            source_roots=source_roots,
            artifact_root=tmp_path / "artifacts",
            phase="pilot",
            arm_executor=lambda **kwargs: calls.append(kwargs["arm"]),
            registration_validator=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("invalid registration")
            ),
        )

    assert calls == []


def test_pilot_failure_blocks_confirmatory_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    calls: list[str] = []

    def failing_executor(*, arm: str, **_kwargs):
        calls.append(arm)
        result = _executor(arm=arm, **_kwargs)
        if arm == "omc":
            result["status"] = "parent_review"
        return result

    pilot = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=tmp_path / "artifacts",
        phase="pilot",
        arm_executor=failing_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
    )
    assert pilot["status"] == "pilot_blocked"
    calls.clear()

    with pytest.raises(ValueError, match="pilot_blocked"):
        acceptance.run_product_value_phase(
            manifest,
            registration,
            packet_root=packet_root,
            source_roots=source_roots,
            artifact_root=tmp_path / "artifacts",
            phase="confirmatory",
            arm_executor=failing_executor,
            registration_validator=lambda *_args, **_kwargs: registration,
        )
    assert calls == []


def test_authoritative_finalize_compares_five_pairs_and_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    artifacts = tmp_path / "artifacts"
    validator = lambda *_args, **_kwargs: registration
    monotonic = _paired_clock(manifest)

    pilot = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="pilot",
        arm_executor=_executor,
        registration_validator=validator,
        monotonic=monotonic,
    )
    assert pilot["status"] == "pilot_passed"
    confirmatory = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="confirmatory",
        arm_executor=_executor,
        registration_validator=validator,
        monotonic=monotonic,
    )
    assert confirmatory["status"] == "confirmatory_completed"

    report = acceptance.finalize_product_value_acceptance(manifest, artifacts)
    assert report["verdict"] == "PASS"
    assert report["confirmatory_pair_count"] == 5
    assert report["metrics"]["omc"]["median_total_tokens"] == 100
    assert report["metrics"]["baseline"]["median_total_tokens"] == 120

    raw_output_path = (
        artifacts
        / "confirmatory"
        / "workload-2"
        / "omc-artifacts"
        / "raw-output.txt"
    )
    original_raw_output = raw_output_path.read_text(encoding="utf-8")
    raw_output_path.write_text("tampered output", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_raw_evidence_mismatch"):
        acceptance.finalize_product_value_acceptance(manifest, artifacts)
    raw_output_path.write_text(original_raw_output, encoding="utf-8")

    receipt_path = artifacts / "confirmatory" / "workload-2" / "omc.json"
    envelope = json.loads(receipt_path.read_text(encoding="utf-8"))
    envelope["payload"]["elapsed_sec"] = 999
    receipt_path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        acceptance.finalize_product_value_acceptance(manifest, artifacts)


def test_omc_arm_requires_bounded_scheduler_ledger_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)

    def no_ledger_executor(*, arm: str, **kwargs):
        result = _executor(arm=arm, **kwargs)
        if arm == "omc":
            result.pop("dag_ledger_sha256")
        return result

    with pytest.raises(ValueError, match="omc_scheduler_evidence_invalid"):
        acceptance.run_product_value_phase(
            manifest,
            registration,
            packet_root=packet_root,
            source_roots=source_roots,
            artifact_root=tmp_path / "artifacts",
            phase="pilot",
            arm_executor=no_ledger_executor,
            registration_validator=lambda *_args, **_kwargs: registration,
        )


def test_failed_omc_arm_is_persisted_without_success_ledgers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)

    pilot = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=tmp_path / "artifacts",
        phase="pilot",
        arm_executor=_failed_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
    )

    assert pilot["status"] == "pilot_blocked"
    omc_receipt, _ = acceptance._load_envelope(
        tmp_path / "artifacts" / "pilot" / "workload-1" / "omc.json"
    )
    assert omc_receipt["status"] == "parent_review"
    assert omc_receipt["scheduler_evidence_status"] == "unavailable"
    assert "dag_ledger_sha256" not in omc_receipt


def test_finalize_cannot_pass_when_confirmatory_arms_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    artifacts = tmp_path / "artifacts"
    validator = lambda *_args, **_kwargs: registration
    acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="pilot",
        arm_executor=_executor,
        registration_validator=validator,
    )
    acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="confirmatory",
        arm_executor=_failed_executor,
        registration_validator=validator,
    )

    report = acceptance.finalize_product_value_acceptance(manifest, artifacts)

    assert report["checks"]["all_confirmatory_arms_succeeded"] is False
    assert report["verdict"] == "FAIL"


def test_elapsed_is_measured_by_runner_across_provider_and_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    ticks = iter(
        (
            0.0,
            0.25,
            0.5,
            2.0,
            2.5,
            3.0,
            10.0,
            10.25,
            10.5,
            14.0,
            14.5,
            15.0,
        )
    )

    acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=tmp_path / "artifacts",
        phase="pilot",
        arm_executor=_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
        monotonic=lambda: next(ticks),
    )

    omc_receipt, _ = acceptance._load_envelope(
        tmp_path / "artifacts" / "pilot" / "workload-1" / "omc.json"
    )
    baseline_receipt, _ = acceptance._load_envelope(
        tmp_path / "artifacts" / "pilot" / "workload-1" / "baseline.json"
    )
    assert omc_receipt["provider_reported_elapsed_sec"] == 1.0
    assert omc_receipt["elapsed_sec"] == 3.0
    assert baseline_receipt["provider_reported_elapsed_sec"] == 2.0
    assert baseline_receipt["elapsed_sec"] == 5.0


def test_process_adapter_failure_becomes_pilot_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    adapter = tmp_path / "failing-arm-adapter"
    adapter.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        " print(json.dumps({'protocol':'omc-product-value-arm/v1',"
        "'hard_total_token_limit':True,'hard_output_limit':True,"
        "'supported_arms':['omc','baseline']}))\n"
        "else:\n"
        " sys.exit(1)\n",
        encoding="utf-8",
    )
    adapter.chmod(0o755)
    manifest["execution_contract"]["provider_snapshot"]["adapter_sha256"] = (
        acceptance.canonical_file_sha256(adapter)
    )
    unsigned = deepcopy(manifest)
    unsigned.pop("preregistration_sha256")
    manifest["preregistration_sha256"] = preregistration.canonical_sha256(unsigned)
    registration["preregistration_sha256"] = manifest["preregistration_sha256"]

    result = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=tmp_path / "artifacts",
        phase="pilot",
        arm_executor=acceptance.build_process_arm_executor(
            adapter,
            manifest["execution_contract"]["provider_snapshot"],
        ),
        registration_validator=lambda *_args, **_kwargs: registration,
    )

    assert result["status"] == "pilot_blocked"
    assert (tmp_path / "artifacts" / "pilot" / "workload-1" / "omc.json").is_file()
    assert (
        tmp_path / "artifacts" / "pilot" / "workload-1" / "baseline.json"
    ).is_file()


def test_v4_process_adapter_uses_execution_bundle_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = tmp_path / "arm-adapter"
    scheduler = tmp_path / "scheduler.py"
    executor_shadow = tmp_path / "omc_executor_shadow.py"
    provider_adapter = tmp_path / "provider-adapter"
    adapter.write_text("#!/bin/sh\n", encoding="utf-8")
    scheduler.write_text("# scheduler\n", encoding="utf-8")
    executor_shadow.write_text("# executor dependency\n", encoding="utf-8")
    provider_adapter.write_text("#!/bin/sh\n", encoding="utf-8")
    adapter.chmod(0o755)
    monkeypatch.setattr(
        acceptance,
        "_run_bounded_adapter_command",
        lambda *_args, **_kwargs: {
            "stdout": json.dumps({
                "protocol": acceptance.ARM_PROTOCOL,
                "hard_total_token_limit": True,
                "hard_output_limit": True,
                "supported_arms": ["omc", "baseline"],
            }),
            "returncode": 0,
            "timed_out": False,
            "limit_exceeded": False,
        },
    )

    executor = acceptance.build_process_arm_executor(
        adapter,
        {"backend_sha256": "a" * 64},
        execution_bundle={
            "acceptance_runner_sha256": acceptance.canonical_file_sha256(
                Path(acceptance.__file__)
            ),
            "arm_adapter_sha256": acceptance.canonical_file_sha256(adapter),
            "scheduler_sha256": acceptance.canonical_file_sha256(scheduler),
            "executor_shadow_sha256": acceptance.canonical_file_sha256(
                executor_shadow
            ),
            "provider_adapter_sha256": acceptance.canonical_file_sha256(
                provider_adapter
            ),
        },
        scheduler_path=scheduler,
        executor_shadow_path=executor_shadow,
        provider_adapter_path=provider_adapter,
    )

    assert callable(executor)


def test_v4_process_adapter_snapshots_scheduler_import_dependency(
    tmp_path: Path,
) -> None:
    adapter = tmp_path / "arm-adapter"
    scheduler = tmp_path / "scheduler-source.py"
    executor_shadow = tmp_path / "executor-source.py"
    provider_adapter = tmp_path / "provider-adapter"
    adapter.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, subprocess, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'protocol': 'omc-product-value-arm/v1', "
        "'hard_total_token_limit': True, 'hard_output_limit': True, "
        "'supported_arms': ['omc', 'baseline']}))\n"
        "else:\n"
        "    request = json.load(sys.stdin)\n"
        "    bundle = request['execution_bundle']\n"
        "    scheduler = pathlib.Path(bundle['scheduler'])\n"
        "    dependency = pathlib.Path(bundle['executor_shadow'])\n"
        "    probe = subprocess.run([sys.executable, str(scheduler), '--help'], "
        "capture_output=True, text=True)\n"
        "    valid = (scheduler.name == 'omc_n_child_scheduler.py' and "
        "dependency.name == 'omc_executor_shadow.py' and "
        "scheduler.parent == dependency.parent and probe.returncode == 0)\n"
        "    print(json.dumps({'status': 'completed' if valid else 'parent_review', "
        "'reason_code': 'completed' if valid else 'dependency_import_failed', "
        "'elapsed_sec': 0.01, 'output': probe.stderr, "
        "'token_usage': {'input_tokens': 1, 'output_tokens': 1, "
        "'total_tokens': 2}, 'intervention_events': [], "
        "'review_findings': [], 'duplicate_executions': 0, "
        "'budget_violations': 0}))\n",
        encoding="utf-8",
    )
    adapter.chmod(0o755)
    scheduler.write_text(
        "import argparse\nimport omc_executor_shadow\n"
        "argparse.ArgumentParser().parse_args()\n",
        encoding="utf-8",
    )
    executor_shadow.write_text("VALUE = 'frozen'\n", encoding="utf-8")
    provider_adapter.write_text("#!/bin/sh\n", encoding="utf-8")
    bundle = {
        "acceptance_runner_sha256": acceptance.canonical_file_sha256(
            Path(acceptance.__file__)
        ),
        "arm_adapter_sha256": acceptance.canonical_file_sha256(adapter),
        "scheduler_sha256": acceptance.canonical_file_sha256(scheduler),
        "executor_shadow_sha256": acceptance.canonical_file_sha256(
            executor_shadow
        ),
        "provider_adapter_sha256": acceptance.canonical_file_sha256(
            provider_adapter
        ),
    }
    executor = acceptance.build_process_arm_executor(
        adapter,
        {"backend_sha256": "a" * 64},
        execution_bundle=bundle,
        scheduler_path=scheduler,
        executor_shadow_path=executor_shadow,
        provider_adapter_path=provider_adapter,
    )

    result = executor(
        arm="omc",
        packet={},
        provider_snapshot={"backend_sha256": "a" * 64},
        limits={
            "max_total_tokens": 100,
            "max_total_elapsed_sec": 10,
            "max_output_chars": 1000,
        },
        arm_artifact=tmp_path / "artifacts",
        workspace=tmp_path,
    )

    assert result["status"] == "completed", result


@pytest.mark.parametrize(
    "tampered_field",
    [
        "acceptance_runner_sha256",
        "arm_adapter_sha256",
        "scheduler_sha256",
        "executor_shadow_sha256",
        "provider_adapter_sha256",
    ],
)
def test_v4_process_adapter_rejects_any_execution_bundle_mismatch(
    tmp_path: Path,
    tampered_field: str,
) -> None:
    adapter = tmp_path / "arm-adapter"
    scheduler = tmp_path / "scheduler.py"
    executor_shadow = tmp_path / "omc_executor_shadow.py"
    provider_adapter = tmp_path / "provider-adapter"
    for path in (adapter, scheduler, executor_shadow, provider_adapter):
        path.write_text("snapshot\n", encoding="utf-8")
    bundle = {
        "acceptance_runner_sha256": acceptance.canonical_file_sha256(
            Path(acceptance.__file__)
        ),
        "arm_adapter_sha256": acceptance.canonical_file_sha256(adapter),
        "scheduler_sha256": acceptance.canonical_file_sha256(scheduler),
        "executor_shadow_sha256": acceptance.canonical_file_sha256(
            executor_shadow
        ),
        "provider_adapter_sha256": acceptance.canonical_file_sha256(
            provider_adapter
        ),
    }
    bundle[tampered_field] = "f" * 64

    with pytest.raises(ValueError, match="execution_bundle_mismatch"):
        acceptance.build_process_arm_executor(
            adapter,
            {"backend_sha256": "a" * 64},
            execution_bundle=bundle,
            scheduler_path=scheduler,
            executor_shadow_path=executor_shadow,
            provider_adapter_path=provider_adapter,
        )


def test_v4_process_adapter_hashes_copied_snapshot_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = tmp_path / "arm-adapter"
    scheduler = tmp_path / "scheduler.py"
    executor_shadow = tmp_path / "omc_executor_shadow.py"
    provider_adapter = tmp_path / "provider-adapter"
    for path in (adapter, scheduler, executor_shadow, provider_adapter):
        path.write_text("frozen\n", encoding="utf-8")
    bundle = {
        "acceptance_runner_sha256": acceptance.canonical_file_sha256(
            Path(acceptance.__file__)
        ),
        "arm_adapter_sha256": acceptance.canonical_file_sha256(adapter),
        "scheduler_sha256": acceptance.canonical_file_sha256(scheduler),
        "executor_shadow_sha256": acceptance.canonical_file_sha256(
            executor_shadow
        ),
        "provider_adapter_sha256": acceptance.canonical_file_sha256(
            provider_adapter
        ),
    }

    def corrupt_copy(_source: Path, destination: Path) -> None:
        Path(destination).write_text("changed-during-copy\n", encoding="utf-8")

    monkeypatch.setattr(acceptance.shutil, "copy2", corrupt_copy)

    with pytest.raises(ValueError, match="execution_bundle_mismatch"):
        acceptance.build_process_arm_executor(
            adapter,
            {"backend_sha256": "a" * 64},
            execution_bundle=bundle,
            scheduler_path=scheduler,
            executor_shadow_path=executor_shadow,
            provider_adapter_path=provider_adapter,
        )


def test_omc_cli_forwards_v4_execution_bundle_paths(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-acceptance",
            "validate",
            "--manifest",
            str(tmp_path / "missing-manifest.json"),
            "--scheduler",
            str(tmp_path / "scheduler.py"),
            "--executor-shadow",
            str(tmp_path / "omc_executor_shadow.py"),
            "--provider-adapter",
            str(tmp_path / "provider-adapter"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert "unrecognized arguments" not in result.stderr
    assert json.loads(result.stdout)["status"] == "blocked"


@pytest.mark.parametrize(
    ("result", "reason_code", "budget_violation"),
    [
        (
            {"returncode": 1, "timed_out": False, "limit_exceeded": False},
            "environment_readiness_failed",
            False,
        ),
        (
            {"returncode": -9, "timed_out": True, "limit_exceeded": False},
            "environment_readiness_timeout",
            True,
        ),
        (
            {"returncode": -9, "timed_out": False, "limit_exceeded": True},
            "environment_readiness_output_limit",
            True,
        ),
    ],
)
def test_environment_readiness_fails_closed_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, object],
    reason_code: str,
    budget_violation: bool,
) -> None:
    monkeypatch.setattr(
        acceptance,
        "_run_bounded_adapter_command",
        lambda *_args, **_kwargs: {"stdout": "", "stderr": "", **result},
    )

    failure = acceptance._environment_readiness_failure(
        {"environment_receipt": {"readiness": {"argv": ["check"]}}},
        tmp_path,
        timeout_sec=1,
        max_response_bytes=1024,
    )

    assert failure is not None
    assert failure["reason_code"] == reason_code
    assert (failure["budget_violations"] == 1) is budget_violation


def test_environment_readiness_missing_command_becomes_failure(tmp_path: Path) -> None:
    failure = acceptance._environment_readiness_failure(
        {
            "environment_receipt": {
                "readiness": {"argv": ["/definitely/missing/omc-readiness"]}
            }
        },
        tmp_path,
        timeout_sec=1,
        max_response_bytes=1024,
    )

    assert failure is not None
    assert failure["reason_code"] == "environment_readiness_unavailable"


def test_environment_readiness_requires_matching_structured_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {
        "schema_version": "omc-product-value-environment/v2",
        "source_commit": "1" * 40,
        "dependency_lock_sha256": "1" * 64,
        "cache_sha256": "2" * 64,
        "runtime_identity_sha256": "3" * 64,
        "cache_path": "/readonly/omc-product-value-cache",
        "readiness": {"argv": ["check"]},
    }
    monkeypatch.setattr(
        acceptance,
        "_run_bounded_adapter_command",
        lambda *_args, **_kwargs: {
            "stdout": "",
            "stderr": "",
            "returncode": 0,
            "timed_out": False,
            "limit_exceeded": False,
        },
    )

    failure = acceptance._environment_readiness_failure(
        {"environment_receipt": environment},
        tmp_path,
        timeout_sec=1,
        max_response_bytes=1024,
    )

    assert failure is not None
    assert failure["reason_code"] == "environment_readiness_mismatch"


def test_environment_readiness_accepts_exact_readonly_cache_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o555)
    environment = {
        "schema_version": "omc-product-value-environment/v2",
        "source_commit": "1" * 40,
        "dependency_lock_sha256": "1" * 64,
        "cache_sha256": "2" * 64,
        "runtime_identity_sha256": "3" * 64,
        "cache_path": str(cache),
        "readiness": {"argv": ["check"]},
    }
    probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": environment["source_commit"],
        "dependency_lock_sha256": environment["dependency_lock_sha256"],
        "cache_sha256": environment["cache_sha256"],
        "runtime_identity_sha256": environment["runtime_identity_sha256"],
        "cache_path": environment["cache_path"],
        "cache_readonly": True,
    }
    monkeypatch.setattr(
        acceptance,
        "_run_bounded_adapter_command",
        lambda *_args, **_kwargs: {
            "stdout": json.dumps(probe),
            "stderr": "",
            "returncode": 0,
            "timed_out": False,
            "limit_exceeded": False,
        },
    )

    assert acceptance._environment_readiness_failure(
        {"environment_receipt": environment},
        tmp_path,
        timeout_sec=1,
        max_response_bytes=1024,
    ) is None


@pytest.mark.parametrize(
    "tampered_field",
    ["dependency_lock_sha256", "cache_sha256", "runtime_identity_sha256"],
)
def test_environment_readiness_measures_hashes_instead_of_trusting_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_field: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lock = workspace / "requirements.lock"
    lock.write_text("dependency==1.0\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.write_bytes(b"runtime-v1\n")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "artifact.bin").write_bytes(b"cached-artifact\n")
    (cache / "artifact.bin").chmod(0o444)
    cache.chmod(0o555)
    environment = {
        "schema_version": "omc-product-value-environment/v3",
        "source_commit": "1" * 40,
        "dependency_lock_path": "requirements.lock",
        "dependency_lock_sha256": acceptance.canonical_file_sha256(lock),
        "cache_sha256": acceptance.canonical_cache_inventory_sha256(cache),
        "runtime_identity_path": str(runtime),
        "runtime_identity_sha256": acceptance.canonical_file_sha256(runtime),
        "cache_path": str(cache),
        "readiness": {"argv": ["check"]},
    }
    environment[tampered_field] = "f" * 64
    probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": environment["source_commit"],
        "dependency_lock_sha256": environment["dependency_lock_sha256"],
        "cache_sha256": environment["cache_sha256"],
        "runtime_identity_sha256": environment["runtime_identity_sha256"],
        "cache_path": environment["cache_path"],
        "cache_readonly": True,
    }
    monkeypatch.setattr(
        acceptance,
        "_run_bounded_adapter_command",
        lambda *_args, **_kwargs: {
            "stdout": json.dumps(probe),
            "stderr": "",
            "returncode": 0,
            "timed_out": False,
            "limit_exceeded": False,
        },
    )

    failure = acceptance._environment_readiness_failure(
        {"environment_receipt": environment},
        workspace,
        timeout_sec=1,
        max_response_bytes=1024,
    )

    assert failure is not None
    assert failure["reason_code"] == "environment_readiness_mismatch"


def test_cache_inventory_hash_is_bounded(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "one").write_text("1", encoding="utf-8")

    with pytest.raises(ValueError, match="cache_inventory_limit"):
        acceptance.canonical_cache_inventory_sha256(cache, max_entries=0)

    with pytest.raises(ValueError, match="cache_inventory_limit"):
        acceptance.canonical_cache_inventory_sha256(cache, max_total_bytes=0)

    with pytest.raises(ValueError, match="cache_inventory_timeout"):
        acceptance.canonical_cache_inventory_sha256(
            cache,
            deadline=0.0,
            monotonic=lambda: 1.0,
        )


@pytest.mark.parametrize("cache_state", ["missing", "writable"])
def test_environment_readiness_rejects_unusable_cache_even_when_probe_claims_readonly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_state: str,
) -> None:
    cache = tmp_path / "cache"
    if cache_state == "writable":
        cache.mkdir()
    environment = {
        "schema_version": "omc-product-value-environment/v2",
        "source_commit": "1" * 40,
        "dependency_lock_sha256": "1" * 64,
        "cache_sha256": "2" * 64,
        "runtime_identity_sha256": "3" * 64,
        "cache_path": str(cache),
        "readiness": {"argv": ["check"]},
    }
    probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": environment["source_commit"],
        "dependency_lock_sha256": environment["dependency_lock_sha256"],
        "cache_sha256": environment["cache_sha256"],
        "runtime_identity_sha256": environment["runtime_identity_sha256"],
        "cache_path": environment["cache_path"],
        "cache_readonly": True,
    }
    monkeypatch.setattr(
        acceptance,
        "_run_bounded_adapter_command",
        lambda *_args, **_kwargs: {
            "stdout": json.dumps(probe),
            "stderr": "",
            "returncode": 0,
            "timed_out": False,
            "limit_exceeded": False,
        },
    )

    failure = acceptance._environment_readiness_failure(
        {"environment_receipt": environment},
        tmp_path,
        timeout_sec=1,
        max_response_bytes=1024,
    )

    assert failure is not None
    assert failure["reason_code"] == "environment_readiness_mismatch"


def test_v4_provider_must_attest_environment_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    source_root, source_commit = _repo(tmp_path, "source")
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o555)
    packet["source_commit"] = source_commit
    environment = packet["environment_receipt"]
    environment["source_commit"] = source_commit
    environment["cache_path"] = str(cache)
    environment["cache_sha256"] = acceptance.canonical_cache_inventory_sha256(cache)
    environment["dependency_lock_sha256"] = acceptance.canonical_file_sha256(
        source_root / environment["dependency_lock_path"]
    )
    environment["runtime_identity_path"] = str(Path(sys.executable).resolve())
    environment["runtime_identity_sha256"] = acceptance.canonical_file_sha256(
        Path(environment["runtime_identity_path"])
    )
    probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": source_commit,
        "dependency_lock_sha256": environment["dependency_lock_sha256"],
        "cache_sha256": environment["cache_sha256"],
        "runtime_identity_sha256": environment["runtime_identity_sha256"],
        "cache_path": str(cache),
        "cache_readonly": True,
    }
    environment["readiness"] = {
        "argv": [sys.executable, "-c", f"print({json.dumps(json.dumps(probe))})"]
    }
    workload["source_commit"] = source_commit
    workload["environment_receipt_sha256"] = acceptance.canonical_sha256(environment)
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)

    receipt, _ = acceptance._run_arm(
        manifest=manifest,
        workload=workload,
        packet=packet,
        registration={"registration_receipt_sha256": "a" * 64},
        source_root=source_root,
        phase_root=tmp_path / "artifacts",
        arm="baseline",
        arm_executor=_executor,
        monotonic=acceptance.time.monotonic,
    )

    assert receipt["status"] == "parent_review"
    assert receipt["reason_code"] == "provider_environment_attestation_mismatch"


def test_v4_measured_environment_completes_with_matching_provider_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    source_root, source_commit = _repo(tmp_path, "source")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "artifact.bin").write_bytes(b"cached-artifact\n")
    (cache / "artifact.bin").chmod(0o444)
    cache.chmod(0o555)
    packet["source_commit"] = source_commit
    environment = packet["environment_receipt"]
    environment.update({
        "source_commit": source_commit,
        "dependency_lock_sha256": acceptance.canonical_file_sha256(
            source_root / environment["dependency_lock_path"]
        ),
        "cache_sha256": acceptance.canonical_cache_inventory_sha256(cache),
        "runtime_identity_path": str(Path(sys.executable).resolve()),
        "cache_path": str(cache),
    })
    environment["runtime_identity_sha256"] = acceptance.canonical_file_sha256(
        Path(environment["runtime_identity_path"])
    )
    probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": source_commit,
        "dependency_lock_sha256": environment["dependency_lock_sha256"],
        "cache_sha256": environment["cache_sha256"],
        "runtime_identity_sha256": environment["runtime_identity_sha256"],
        "cache_path": str(cache),
        "cache_readonly": True,
    }
    environment["readiness"] = {
        "argv": [sys.executable, "-c", f"print({json.dumps(json.dumps(probe))})"]
    }
    workload["source_commit"] = source_commit
    workload["environment_receipt_sha256"] = acceptance.canonical_sha256(environment)
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)

    def attested_executor(**kwargs):
        result = _executor(**kwargs)
        result["environment_receipt_sha256"] = workload[
            "environment_receipt_sha256"
        ]
        return result

    receipt, _ = acceptance._run_arm(
        manifest=manifest,
        workload=workload,
        packet=packet,
        registration={"registration_receipt_sha256": "a" * 64},
        source_root=source_root,
        phase_root=tmp_path / "artifacts",
        arm="baseline",
        arm_executor=attested_executor,
        monotonic=acceptance.time.monotonic,
    )

    assert receipt["status"] == "completed"
    assert receipt["success"] is True
    assert receipt["environment_receipt_sha256"] == workload[
        "environment_receipt_sha256"
    ]


def test_v4_environment_change_during_provider_blocks_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    source_root, source_commit = _repo(tmp_path, "source")
    cache = tmp_path / "cache"
    cache.mkdir()
    cached_artifact = cache / "artifact.bin"
    cached_artifact.write_bytes(b"cached-artifact\n")
    cached_artifact.chmod(0o444)
    cache.chmod(0o555)
    packet["source_commit"] = source_commit
    environment = packet["environment_receipt"]
    environment.update({
        "source_commit": source_commit,
        "dependency_lock_sha256": acceptance.canonical_file_sha256(
            source_root / environment["dependency_lock_path"]
        ),
        "cache_sha256": acceptance.canonical_cache_inventory_sha256(cache),
        "runtime_identity_path": str(Path(sys.executable).resolve()),
        "cache_path": str(cache),
    })
    environment["runtime_identity_sha256"] = acceptance.canonical_file_sha256(
        Path(environment["runtime_identity_path"])
    )
    probe = {
        "schema_version": "omc-product-value-environment-probe/v1",
        "source_commit": source_commit,
        "dependency_lock_sha256": environment["dependency_lock_sha256"],
        "cache_sha256": environment["cache_sha256"],
        "runtime_identity_sha256": environment["runtime_identity_sha256"],
        "cache_path": str(cache),
        "cache_readonly": True,
    }
    environment["readiness"] = {
        "argv": [sys.executable, "-c", f"print({json.dumps(json.dumps(probe))})"]
    }
    workload["source_commit"] = source_commit
    workload["environment_receipt_sha256"] = acceptance.canonical_sha256(environment)
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)

    def mutating_executor(**kwargs):
        cached_artifact.chmod(0o644)
        cached_artifact.write_bytes(b"changed-during-provider\n")
        result = _executor(**kwargs)
        result["environment_receipt_sha256"] = workload[
            "environment_receipt_sha256"
        ]
        return result

    receipt, _ = acceptance._run_arm(
        manifest=manifest,
        workload=workload,
        packet=packet,
        registration={"registration_receipt_sha256": "a" * 64},
        source_root=source_root,
        phase_root=tmp_path / "artifacts",
        arm="baseline",
        arm_executor=mutating_executor,
        monotonic=acceptance.time.monotonic,
    )

    assert receipt["status"] == "parent_review"
    assert receipt["reason_code"] == "environment_changed_after_execution"


def test_v4_artifact_binding_requires_environment_receipt_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, workload, _ = _v4_manifest_and_packet(monkeypatch)

    with pytest.raises(ValueError, match="artifact_environment_binding_mismatch"):
        acceptance._validate_environment_receipt_binding(
            manifest,
            workload,
            {"environment_receipt_sha256": "f" * 64},
        )


def test_v4_provider_failure_reason_is_not_replaced_by_missing_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    source_root, source_commit = _repo(tmp_path, "source")
    packet["source_commit"] = source_commit
    workload["source_commit"] = source_commit
    monkeypatch.setattr(
        acceptance,
        "_environment_readiness_failure",
        lambda *_args, **_kwargs: None,
    )

    receipt, _ = acceptance._run_arm(
        manifest=manifest,
        workload=workload,
        packet=packet,
        registration={"registration_receipt_sha256": "a" * 64},
        source_root=source_root,
        phase_root=tmp_path / "artifacts",
        arm="baseline",
        arm_executor=_failed_executor,
        monotonic=acceptance.time.monotonic,
    )

    assert receipt["reason_code"] == "provider_failed"


def test_v4_phase_result_uses_v2_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    (packet_root / "workload-1.json").write_text(
        json.dumps(packet), encoding="utf-8"
    )
    registration = {
        "claim_eligible": True,
        "status": "registered",
        "preregistration_sha256": manifest["preregistration_sha256"],
        "registration_receipt_sha256": "a" * 64,
    }
    monkeypatch.setattr(
        acceptance,
        "validate_execution_packet",
        lambda _manifest, _workload, candidate: candidate,
    )
    monkeypatch.setattr(
        acceptance,
        "_source_root",
        lambda _workload, _source_roots: tmp_path,
    )
    monkeypatch.setattr(
        acceptance,
        "_run_arm",
        lambda **kwargs: ({"success": True}, kwargs["arm"][0] * 64),
    )

    result = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots={},
        artifact_root=tmp_path / "artifacts",
        phase="pilot",
        arm_executor=_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
    )

    assert result["schema_version"] == "omc-product-value-acceptance/v2"


def test_missing_environment_readiness_writes_receipt_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, workload, packet = _v4_manifest_and_packet(monkeypatch)
    source_root, source_commit = _repo(tmp_path, "source")
    packet["source_commit"] = source_commit
    packet["environment_receipt"]["source_commit"] = source_commit
    packet["environment_receipt"]["readiness"] = {
        "argv": ["/definitely/missing/omc-readiness"]
    }
    workload["source_commit"] = source_commit
    workload["environment_receipt_sha256"] = acceptance.canonical_sha256(
        packet["environment_receipt"]
    )
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)
    provider_calls = 0

    def executor(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must not run")

    receipt, _ = acceptance._run_arm(
        manifest=manifest,
        workload=workload,
        packet=packet,
        registration={"registration_receipt_sha256": "a" * 64},
        source_root=source_root,
        phase_root=tmp_path / "artifacts",
        arm="baseline",
        arm_executor=executor,
        monotonic=acceptance.time.monotonic,
    )

    assert provider_calls == 0
    assert receipt["reason_code"] == "environment_readiness_unavailable"
    assert receipt["status"] == "parent_review"


def test_malformed_process_adapter_result_becomes_pilot_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    adapter = tmp_path / "malformed-arm-adapter"
    adapter.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        " print(json.dumps({'protocol':'omc-product-value-arm/v1',"
        "'hard_total_token_limit':True,'hard_output_limit':True,"
        "'supported_arms':['omc','baseline']}))\n"
        "else:\n"
        " print('{}')\n",
        encoding="utf-8",
    )
    adapter.chmod(0o755)
    manifest["execution_contract"]["provider_snapshot"]["adapter_sha256"] = (
        acceptance.canonical_file_sha256(adapter)
    )
    unsigned = deepcopy(manifest)
    unsigned.pop("preregistration_sha256")
    manifest["preregistration_sha256"] = preregistration.canonical_sha256(unsigned)
    registration["preregistration_sha256"] = manifest["preregistration_sha256"]
    artifacts = tmp_path / "artifacts"

    result = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="pilot",
        arm_executor=acceptance.build_process_arm_executor(
            adapter,
            manifest["execution_contract"]["provider_snapshot"],
        ),
        registration_validator=lambda *_args, **_kwargs: registration,
    )

    assert result["status"] == "pilot_blocked"
    omc_receipt, _ = acceptance._load_envelope(
        artifacts / "pilot" / "workload-1" / "omc.json"
    )
    assert omc_receipt["status"] == "parent_review"
    assert omc_receipt["reason_code"] == "provider_result_invalid"
    assert (artifacts / "pilot" / "index.json").is_file()


@pytest.mark.parametrize(("timed_out_command", "reason_code"), [
    ("checkout", "source_checkout_timeout"),
    ("diff", "workspace_diff_timeout"),
])
def test_git_timeout_becomes_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timed_out_command: str,
    reason_code: str,
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    original_git = acceptance._git
    observed_timeouts: list[float] = []

    def bounded_git(root: Path, *args: str, timeout: float | None = None):
        if args[0] == timed_out_command:
            assert timeout is not None
            observed_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(args, timeout)
        return original_git(root, *args, timeout=timeout)

    monkeypatch.setattr(acceptance, "_git", bounded_git)
    artifacts = tmp_path / "artifacts"

    result = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="pilot",
        arm_executor=_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
    )

    assert result["status"] == "pilot_blocked"
    omc_receipt, _ = acceptance._load_envelope(
        artifacts / "pilot" / "workload-1" / "omc.json"
    )
    assert omc_receipt["status"] == "parent_review"
    assert omc_receipt["reason_code"] == reason_code
    assert observed_timeouts
    assert max(observed_timeouts) <= manifest["execution_contract"]["limits"][
        "max_total_elapsed_sec"
    ]


def test_concurrent_phase_is_rejected_before_duplicate_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    artifacts = tmp_path / "artifacts"
    acceptance._bind_registration_gate(manifest, artifacts, registration)
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    call_lock = threading.Lock()

    def blocking_executor(**kwargs):
        with call_lock:
            calls.append(kwargs["arm"])
        if kwargs["arm"] == "omc":
            entered.set()
            assert release.wait(timeout=5)
        return _executor(**kwargs)

    def run_phase():
        try:
            return acceptance.run_product_value_phase(
                manifest,
                registration,
                packet_root=packet_root,
                source_roots=source_roots,
                artifact_root=artifacts,
                phase="pilot",
                arm_executor=blocking_executor,
                registration_validator=lambda *_args, **_kwargs: registration,
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_phase)
        assert entered.wait(timeout=5)
        second = pool.submit(run_phase)
        second_result = second.result(timeout=5)
        release.set()
        first_result = first.result(timeout=5)

    assert first_result["status"] == "pilot_passed"
    assert second_result == "acceptance_phase_in_progress"
    assert calls == ["omc", "baseline"]


def test_verification_output_limit_becomes_failure_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    packet_path = packet_root / "workload-1.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["verification"] = {
        "argv": [sys.executable, "-c", "print('x' * 4096)"]
    }
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    workload = manifest["workloads"][0]
    workload["verification_sha256"] = acceptance.canonical_sha256(
        packet["verification"]
    )
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)
    manifest["execution_contract"]["limits"]["max_output_chars"] = 128
    unsigned = deepcopy(manifest)
    unsigned.pop("preregistration_sha256")
    manifest["preregistration_sha256"] = preregistration.canonical_sha256(unsigned)
    registration["preregistration_sha256"] = manifest["preregistration_sha256"]
    artifacts = tmp_path / "artifacts"

    result = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="pilot",
        arm_executor=_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
    )

    assert result["status"] == "pilot_blocked"
    omc_receipt, _ = acceptance._load_envelope(
        artifacts / "pilot" / "workload-1" / "omc.json"
    )
    assert omc_receipt["status"] == "parent_review"
    assert omc_receipt["reason_code"] == "verification_output_limit_exceeded"
    assert omc_receipt["budget_violations"] == 1
    stdout = artifacts / "pilot" / "workload-1" / "omc-artifacts" / "verification-stdout.txt"
    assert stdout.stat().st_size <= 129


def test_verification_timeout_preserves_simultaneous_output_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        acceptance,
        "_run_bounded_adapter_command",
        lambda *_args, **_kwargs: {
            "returncode": -9,
            "stdout": "x" * 129,
            "stderr": "",
            "timed_out": True,
            "limit_exceeded": True,
        },
    )
    artifacts = tmp_path / "artifacts"

    result = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=artifacts,
        phase="pilot",
        arm_executor=_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
    )

    assert result["status"] == "pilot_blocked"
    omc_receipt, _ = acceptance._load_envelope(
        artifacts / "pilot" / "workload-1" / "omc.json"
    )
    assert omc_receipt["status"] == "parent_review"
    assert omc_receipt["reason_code"] == "verification_output_limit_exceeded"
    assert omc_receipt["budget_violations"] == 1
    assert omc_receipt["verification"]["returncode"] == 124


def test_verification_receives_only_remaining_elapsed_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, packet_root, source_roots, registration = _fixture(tmp_path, monkeypatch)
    packet_path = packet_root / "workload-1.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["verification"] = {
        "argv": [sys.executable, "-c", "import time; time.sleep(1)"]
    }
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    workload = manifest["workloads"][0]
    workload["verification_sha256"] = acceptance.canonical_sha256(
        packet["verification"]
    )
    workload["execution_packet_sha256"] = acceptance.canonical_sha256(packet)
    unsigned = deepcopy(manifest)
    unsigned.pop("preregistration_sha256")
    manifest["preregistration_sha256"] = preregistration.canonical_sha256(unsigned)
    registration["preregistration_sha256"] = manifest["preregistration_sha256"]
    ticks = iter(
        (
            0.0,
            300.0,
            590.0,
            599.99,
            600.0,
            600.0,
            1_000.0,
            1_300.0,
            1_590.0,
            1_599.99,
            1_600.0,
            1_600.0,
        )
    )

    result = acceptance.run_product_value_phase(
        manifest,
        registration,
        packet_root=packet_root,
        source_roots=source_roots,
        artifact_root=tmp_path / "artifacts",
        phase="pilot",
        arm_executor=_executor,
        registration_validator=lambda *_args, **_kwargs: registration,
        monotonic=lambda: next(ticks),
    )

    assert result["status"] == "pilot_blocked"
    omc_receipt, _ = acceptance._load_envelope(
        tmp_path / "artifacts" / "pilot" / "workload-1" / "omc.json"
    )
    assert omc_receipt["verification"]["returncode"] == 124


def test_cli_exposes_product_value_acceptance_surface() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/omc.py", "product-value-acceptance", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "run-pilot" in result.stdout
    assert "run-confirmatory" in result.stdout
    assert "finalize" in result.stdout
