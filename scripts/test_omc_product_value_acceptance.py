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
