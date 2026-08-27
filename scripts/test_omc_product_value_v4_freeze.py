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


def _write_provider_backend(path: Path, *, hard_token_limit: bool = True) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1:] == ['capabilities']:\n"
        "    print(json.dumps({'protocol': 'omc-provider-backend/v1', "
        f"'hard_total_token_limit': {hard_token_limit!r}, "
        "'hard_output_limit': True}))\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_provider_backend_preflight_binds_verified_capabilities(tmp_path: Path) -> None:
    backend = tmp_path / "provider-backend"
    _write_provider_backend(backend)
    snapshot = {
        "provider_family": "codex",
        "model": "gpt-test",
        "reasoning_profile": "low",
        "backend_sha256": freeze.file_sha256(backend),
    }

    receipt = freeze.provider_backend_capability_receipt(snapshot, backend)

    assert receipt == {
        "schema_version": "omc-product-value-provider-backend-capability/v1",
        "status": "verified",
        "backend_sha256": snapshot["backend_sha256"],
        "protocol": "omc-provider-backend/v1",
        "hard_total_token_limit": True,
        "hard_output_limit": True,
    }


def test_provider_backend_preflight_rejects_raw_cli_shape(tmp_path: Path) -> None:
    backend = tmp_path / "raw-cli"
    backend.write_text("#!/bin/sh\necho not-json\nexit 1\n", encoding="utf-8")
    backend.chmod(0o755)
    snapshot = {
        "provider_family": "codex",
        "model": "gpt-test",
        "reasoning_profile": "low",
        "backend_sha256": freeze.file_sha256(backend),
    }

    with pytest.raises(ValueError, match="freeze_provider_backend_incompatible"):
        freeze.provider_backend_capability_receipt(snapshot, backend)


def test_provider_backend_preflight_rejects_hash_mismatch(tmp_path: Path) -> None:
    backend = tmp_path / "provider-backend"
    _write_provider_backend(backend)
    snapshot = {
        "provider_family": "codex",
        "model": "gpt-test",
        "reasoning_profile": "low",
        "backend_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="freeze_provider_backend_hash_mismatch"):
        freeze.provider_backend_capability_receipt(snapshot, backend)


def test_provider_backend_preflight_rejects_missing_hard_token_limit(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "provider-backend"
    _write_provider_backend(backend, hard_token_limit=False)
    snapshot = {
        "provider_family": "codex",
        "model": "gpt-test",
        "reasoning_profile": "low",
        "backend_sha256": freeze.file_sha256(backend),
    }

    with pytest.raises(ValueError, match="freeze_provider_backend_incompatible"):
        freeze.provider_backend_capability_receipt(snapshot, backend)


def test_provider_backend_preflight_executes_immutable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "provider-backend"
    _write_provider_backend(backend)
    snapshot = {
        "provider_family": "codex",
        "model": "gpt-test",
        "reasoning_profile": "low",
        "backend_sha256": freeze.file_sha256(backend),
    }
    original_runner = freeze._run_bounded_adapter_command
    executed_paths: list[Path] = []

    def capture(command: list[str], **kwargs: object) -> dict[str, object]:
        executed_paths.append(Path(command[0]).resolve())
        return original_runner(command, **kwargs)

    monkeypatch.setattr(freeze, "_run_bounded_adapter_command", capture)

    freeze.provider_backend_capability_receipt(snapshot, backend)

    assert executed_paths[0] != backend.resolve()
    assert freeze.file_sha256(backend) == snapshot["backend_sha256"]


def _inputs(tmp_path: Path) -> dict[str, object]:
    workloads = []
    packets = {}
    executions = {}
    environments = {}
    surface_verifications = {}
    source_roots = {}
    for index in range(1, 7):
        workload_id = f"pv-{index:02d}"
        alias = f"source-{index}"
        source = tmp_path / alias
        source.mkdir()
        (source / "environment.lock").write_text("frozen\n", encoding="utf-8")
        surface_evidence = source / "surface-verification.json"
        surface_evidence.write_text(
            json.dumps({"workload_id": workload_id, "status": "verified"}),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "freeze@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "Freeze Test"],
            check=True,
        )
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
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
        surface_verifications[workload_id] = {
            "path": "surface-verification.json",
            "sha256": freeze.file_sha256(surface_evidence),
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
    provider_backend = tmp_path / "candidate-provider-backend"
    _write_provider_backend(provider_backend)
    provider_backend_sha256 = freeze.file_sha256(provider_backend)
    return {
        "workloads": workloads,
        "packets": packets,
        "executions": executions,
        "environments": environments,
        "surface_verifications": surface_verifications,
        "source_roots": source_roots,
        "bundle": bundle,
        "provider_backend": provider_backend,
        "provider_capability": {
            "schema_version": "omc-product-value-provider-backend-capability/v1",
            "status": "verified",
            "backend_sha256": provider_backend_sha256,
            "protocol": "omc-provider-backend/v1",
            "hard_total_token_limit": True,
            "hard_output_limit": True,
        },
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
            "backend_sha256": inputs["provider_capability"]["backend_sha256"],
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
            "backend_sha256": inputs["provider_capability"]["backend_sha256"],
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
    assert first["provider_capability"] == inputs["provider_capability"]
    assert set(first["execution_contract"]["execution_bundle"]) == {
        "acceptance_runner_sha256",
        "arm_adapter_sha256",
        "scheduler_sha256",
        "executor_shadow_sha256",
        "provider_adapter_sha256",
    }
    assert len(first["packets"]) == 6
    assert all(
        packet["schema_version"] == "omc-product-value-execution-packet/v3"
        for packet in first["packets"].values()
    )
    assert all(
        packet["direct_surface_verification"]
        == inputs["surface_verifications"][workload_id]
        for workload_id, packet in first["packets"].items()
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
                "backend_sha256": inputs["provider_capability"]["backend_sha256"],
            },
            limits={
                "max_total_tokens": 100,
                "max_total_elapsed_sec": 30,
                "max_output_chars": 1000,
            },
            grant_validator=lambda *_args, **_kwargs: True,
        )


def test_freeze_candidate_rejects_unverified_provider_capability(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    inputs["provider_capability"]["hard_total_token_limit"] = False

    with pytest.raises(
        ValueError, match="freeze_provider_backend_capability_invalid"
    ):
        freeze.freeze_v4_candidate(
            **inputs,
            provider_snapshot={
                "provider_family": "codex",
                "model": "gpt-test",
                "reasoning_profile": "high",
                "backend_sha256": inputs["provider_capability"]["backend_sha256"],
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
                "backend_sha256": inputs["provider_capability"]["backend_sha256"],
            },
            limits={
                "max_total_tokens": 100,
                "max_total_elapsed_sec": 30,
                "max_output_chars": 1000,
            },
            grant_validator=lambda *_args, **_kwargs: True,
        )


def test_prepare_inputs_rejects_workload_without_direct_surface_verification(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    backend = tmp_path / "provider-backend"
    _write_provider_backend(backend)
    environment_specs = {
        workload["workload_id"]: {
            "dependency_lock_path": inputs["environments"][workload["workload_id"]][
                "dependency_lock_path"
            ],
            "runtime_identity_path": inputs["environments"][workload["workload_id"]][
                "runtime_identity_path"
            ],
            "cache_path": inputs["environments"][workload["workload_id"]][
                "cache_path"
            ],
            "readiness": inputs["environments"][workload["workload_id"]]["readiness"],
            "direct_surface_verification_path": "surface-verification.json",
            "direct_surface_verification_sha256": (
                inputs["surface_verifications"][workload["workload_id"]]["sha256"]
                if workload["workload_id"] != "pv-06"
                else "b" * 64
            ),
        }
        for workload in inputs["workloads"]
    }

    with pytest.raises(ValueError, match="freeze_direct_surface_unverified"):
        freeze.prepare_v4_inputs(
            workloads=inputs["workloads"],
            executions=inputs["executions"],
            environment_specs=environment_specs,
            source_roots=inputs["source_roots"],
            provider_snapshot={
                "provider_family": "codex",
                "model": "gpt-test",
                "reasoning_profile": "high",
                "backend_sha256": freeze.file_sha256(backend),
            },
            provider_backend=backend,
            limits={
                "max_total_tokens": 100,
                "max_total_elapsed_sec": 30,
                "max_output_chars": 1000,
            },
        )


def test_prepare_inputs_cli_rejects_surface_evidence_missing_from_frozen_commit(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    backend = tmp_path / "provider-backend"
    _write_provider_backend(backend)
    workload = inputs["workloads"][0]
    workload_id = workload["workload_id"]
    source_root = Path(inputs["source_roots"][workload["repo_alias"]]["path"])
    worktree_only = source_root / "worktree-only-surface.json"
    worktree_only.write_text('{"status":"verified"}\n', encoding="utf-8")

    corpus_root = tmp_path / "corpus"
    (corpus_root / "packets").mkdir(parents=True)
    (corpus_root / "workloads.json").write_text(
        json.dumps(inputs["workloads"]), encoding="utf-8"
    )
    (corpus_root / "source-roots.json").write_text(
        json.dumps(inputs["source_roots"]), encoding="utf-8"
    )
    for packet_id, packet in inputs["packets"].items():
        (corpus_root / "packets" / f"{packet_id}.json").write_text(
            json.dumps(packet), encoding="utf-8"
        )

    execution_specs = tmp_path / "execution-specs.json"
    execution_specs.write_text(json.dumps(inputs["executions"]), encoding="utf-8")
    environment_specs = {
        item["workload_id"]: {
            "dependency_lock_path": inputs["environments"][item["workload_id"]][
                "dependency_lock_path"
            ],
            "runtime_identity_path": inputs["environments"][item["workload_id"]][
                "runtime_identity_path"
            ],
            "cache_path": inputs["environments"][item["workload_id"]]["cache_path"],
            "readiness": inputs["environments"][item["workload_id"]]["readiness"],
            "direct_surface_verification_path": (
                "worktree-only-surface.json"
                if item["workload_id"] == workload_id
                else "surface-verification.json"
            ),
            "direct_surface_verification_sha256": (
                freeze.file_sha256(worktree_only)
                if item["workload_id"] == workload_id
                else inputs["surface_verifications"][item["workload_id"]]["sha256"]
            ),
        }
        for item in inputs["workloads"]
    }
    environment_specs_path = tmp_path / "environment-specs.json"
    environment_specs_path.write_text(json.dumps(environment_specs), encoding="utf-8")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps({
        "provider_family": "codex",
        "model": "gpt-test",
        "reasoning_profile": "high",
        "backend_sha256": freeze.file_sha256(backend),
    }), encoding="utf-8")
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps({
        "max_total_tokens": 100,
        "max_total_elapsed_sec": 30,
        "max_output_chars": 1000,
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="freeze_direct_surface_unverified"):
        freeze.main([
            "prepare-inputs",
            "--corpus-root", str(corpus_root),
            "--execution-specs", str(execution_specs),
            "--environment-specs", str(environment_specs_path),
            "--provider-snapshot", str(provider),
            "--provider-backend", str(backend),
            "--limits", str(limits),
            "--out", str(tmp_path / "prepared"),
        ])


def test_cli_exposes_product_value_freeze_surface() -> None:
    result = subprocess.run(
        ["python3", "scripts/omc.py", "product-value-freeze", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "prepare-inputs" in result.stdout
    assert "prepare" in result.stdout
    assert "validate" in result.stdout


def test_freeze_cli_prepares_inputs_candidate_and_validates_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _inputs(tmp_path)
    backend = tmp_path / "provider-backend"
    _write_provider_backend(backend)
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    (corpus_root / "packets").mkdir()
    (corpus_root / "workloads.json").write_text(
        json.dumps(inputs["workloads"]), encoding="utf-8"
    )
    (corpus_root / "source-roots.json").write_text(
        json.dumps(inputs["source_roots"]), encoding="utf-8"
    )
    for workload_id, packet in inputs["packets"].items():
        (corpus_root / "packets" / f"{workload_id}.json").write_text(
            json.dumps(packet), encoding="utf-8"
        )
    execution_specs = tmp_path / "execution-specs.json"
    execution_specs.write_text(json.dumps(inputs["executions"]), encoding="utf-8")
    environment_specs = tmp_path / "environment-specs.json"
    environment_specs.write_text(
        json.dumps({
            workload["workload_id"]: {
                "dependency_lock_path": inputs["environments"][workload["workload_id"]][
                    "dependency_lock_path"
                ],
                "runtime_identity_path": inputs["environments"][workload["workload_id"]][
                    "runtime_identity_path"
                ],
                "cache_path": inputs["environments"][workload["workload_id"]][
                    "cache_path"
                ],
                "readiness": inputs["environments"][workload["workload_id"]]["readiness"],
                "direct_surface_verification_path": "surface-verification.json",
                "direct_surface_verification_sha256": inputs["surface_verifications"][
                    workload["workload_id"]
                ]["sha256"],
            }
            for workload in inputs["workloads"]
        }),
        encoding="utf-8",
    )
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps({
        "provider_family": "codex",
        "model": "gpt-test",
        "reasoning_profile": "high",
        "backend_sha256": freeze.file_sha256(backend),
    }), encoding="utf-8")
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps({
        "max_total_tokens": 100,
        "max_total_elapsed_sec": 30,
        "max_output_chars": 1000,
    }), encoding="utf-8")
    prepared = tmp_path / "prepared"
    assert freeze.main([
        "prepare-inputs",
        "--corpus-root", str(corpus_root),
        "--execution-specs", str(execution_specs),
        "--environment-specs", str(environment_specs),
        "--provider-snapshot", str(provider),
        "--provider-backend", str(backend),
        "--limits", str(limits),
        "--out", str(prepared),
    ]) == 0

    monkeypatch.setattr(freeze, "_default_grant_validator", lambda *_args: True)
    bundle_args = [
        item
        for name in freeze.BUNDLE_PATH_FIELDS
        for item in (f"--{name.replace('_', '-')}", str(inputs["bundle"][name]))
    ]
    bundle_args.extend(["--provider-backend", str(backend)])
    candidate = tmp_path / "candidate"
    assert freeze.main([
        "prepare",
        "--corpus-root", str(corpus_root),
        "--input-root", str(prepared),
        *bundle_args,
        "--out", str(candidate),
    ]) == 0
    assert freeze.main([
        "validate",
        "--corpus-root", str(corpus_root),
        "--input-root", str(prepared),
        *bundle_args,
        "--candidate-root", str(candidate),
    ]) == 0

    packet_path = candidate / "packets" / "pv-01.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["request"] = "tampered"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    with pytest.raises(ValueError, match="freeze_candidate_artifact_mismatch"):
        freeze.main([
            "validate",
            "--corpus-root", str(corpus_root),
            "--input-root", str(prepared),
            *bundle_args,
            "--candidate-root", str(candidate),
        ])
