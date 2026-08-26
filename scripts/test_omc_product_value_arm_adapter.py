from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ADAPTER = Path(__file__).with_name("omc_product_value_arm_adapter.py")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, request: dict, *, backend: Path):
    return subprocess.run(
        [sys.executable, str(ADAPTER), "execute"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={**os.environ, "OMC_PROVIDER_BACKEND": str(backend)},
    )


def _request(tmp_path: Path, *, arm: str = "baseline") -> tuple[dict, Path]:
    backend = tmp_path / "backend"
    _write_executable(backend, "#!/bin/sh\nexit 0\n")
    scheduler = tmp_path / "scheduler.py"
    scheduler.write_text("# scheduler\n", encoding="utf-8")
    dependency = tmp_path / "omc_executor_shadow.py"
    dependency.write_text("# dependency\n", encoding="utf-8")
    provider = tmp_path / "provider-adapter"
    _write_executable(
        provider,
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'protocol': 'omc-provider/v1', "
        "'hard_total_token_limit': True, 'hard_output_limit': True}))\n"
        "    raise SystemExit(0)\n"
        "request = json.load(sys.stdin)\n"
        "print(json.dumps({'returncode': 0, 'output': 'baseline-ok', "
        "'token_usage': {'input_tokens': 2, 'output_tokens': 3, "
        "'total_tokens': 5}}))\n",
    )
    environment = {"schema_version": "environment/v1", "value": "frozen"}
    request = {
        "protocol": "omc-product-value-arm/v1",
        "arm": arm,
        "packet": {
            "omc_execution": {
                "grant": {"children": [{"child_id": f"child-{i}"} for i in range(3)]},
                "prompts": {f"child-{i}": f"prompt-{i}" for i in range(3)},
            },
            "baseline_execution_brief": "Implement the request as one agent.",
            "environment_receipt": environment,
        },
        "provider_snapshot": {
            "provider_family": "codex",
            "model": "gpt-test",
            "reasoning_profile": "high",
            "backend_sha256": _sha256(backend),
        },
        "limits": {
            "max_total_tokens": 100,
            "max_total_elapsed_sec": 30,
            "max_output_chars": 1000,
        },
        "artifact_root": str(tmp_path / "artifacts"),
        "execution_bundle": {
            "scheduler": str(scheduler),
            "executor_shadow": str(dependency),
            "provider_adapter": str(provider),
        },
    }
    return request, backend


def test_arm_adapter_advertises_bounded_dual_arm_contract() -> None:
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), "capabilities"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "protocol": "omc-product-value-arm/v1",
        "hard_total_token_limit": True,
        "hard_output_limit": True,
        "supported_arms": ["omc", "baseline"],
    }


def test_arm_adapter_rejects_backend_hash_mismatch_before_provider_call(
    tmp_path: Path,
) -> None:
    request, backend = _request(tmp_path)
    request["provider_snapshot"]["backend_sha256"] = "f" * 64

    proc = _run(tmp_path, request, backend=backend)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "parent_review"
    assert result["reason_code"] == "backend_snapshot_mismatch"
    assert result["token_usage"]["total_tokens"] == 0


def test_baseline_arm_returns_provider_usage_and_environment_attestation(
    tmp_path: Path,
) -> None:
    request, backend = _request(tmp_path)

    proc = _run(tmp_path, request, backend=backend)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "completed"
    assert result["output"] == "baseline-ok"
    assert result["token_usage"]["total_tokens"] == 5
    expected = hashlib.sha256(
        json.dumps(
            request["packet"]["environment_receipt"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert result["environment_receipt_sha256"] == expected


def test_baseline_arm_rejects_provider_without_hard_limit_capabilities(
    tmp_path: Path,
) -> None:
    request, backend = _request(tmp_path)
    provider = Path(request["execution_bundle"]["provider_adapter"])
    _write_executable(
        provider,
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'protocol': 'omc-provider/v1', "
        "'hard_total_token_limit': False, 'hard_output_limit': True}))\n"
        "    raise SystemExit(0)\n"
        "raise RuntimeError('execute must not be called')\n",
    )

    proc = _run(tmp_path, request, backend=backend)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "parent_review"
    assert result["reason_code"] == "provider_capability_invalid"
    assert result["token_usage"]["total_tokens"] == 0


def test_omc_arm_runs_frozen_scheduler_and_returns_usage(tmp_path: Path) -> None:
    request, backend = _request(tmp_path, arm="omc")
    scheduler = Path(request["execution_bundle"]["scheduler"])
    scheduler.write_text(
        "import json\n"
        "print(json.dumps({'status': 'completed', "
        "'reason_code': 'dag_completed', 'completed_child_ids': "
        "['child-0', 'child-1', 'child-2'], 'input_tokens': 4, "
        "'output_tokens': 5, 'total_tokens': 9}))\n",
        encoding="utf-8",
    )

    proc = _run(tmp_path, request, backend=backend)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "completed"
    assert result["executed_child_count"] == 3
    assert result["token_usage"]["total_tokens"] == 9


def test_omc_arm_rejects_invalid_scheduler_usage(tmp_path: Path) -> None:
    request, backend = _request(tmp_path, arm="omc")
    scheduler = Path(request["execution_bundle"]["scheduler"])
    scheduler.write_text(
        "import json\n"
        "print(json.dumps({'status': 'completed', "
        "'reason_code': 'dag_completed', 'completed_child_ids': [], "
        "'input_tokens': 4, 'output_tokens': 5, 'total_tokens': 99}))\n",
        encoding="utf-8",
    )

    proc = _run(tmp_path, request, backend=backend)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "parent_review"
    assert result["reason_code"] == "scheduler_usage_invalid"
