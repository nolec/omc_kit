from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ADAPTER = Path(__file__).with_name("omc_provider_exec_adapter.py")


def _write_backend(
    path: Path, *, hard_limit: bool = True, enforcement_contract: bool = True
) -> None:
    enforcement = (
        ", 'token_enforcement': {'mode': 'provider_enforced_total', "
        "'request_field': 'max_total_tokens', 'over_limit_behavior': "
        "'reject_before_or_during_generation'}"
        if enforcement_contract
        else ""
    )
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'protocol': 'omc-provider-backend/v1', "
        f"'hard_total_token_limit': {hard_limit!r}, 'hard_output_limit': True"
        f"{enforcement}}}))\n"
        "else:\n"
        "    request = json.load(sys.stdin)\n"
        "    print(json.dumps({'returncode': 0, 'output': 'ok', 'token_usage': "
        "{'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5}}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_adapter(backend: Path, action: str, request: dict | None = None):
    env = {**os.environ, "OMC_PROVIDER_BACKEND": str(backend)}
    return subprocess.run(
        [sys.executable, str(ADAPTER), action],
        input="" if request is None else json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_adapter_advertises_hard_limits_only_after_backend_handshake(tmp_path):
    backend = tmp_path / "backend"
    _write_backend(backend)

    proc = _run_adapter(backend, "capabilities")

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "hard_output_limit": True,
        "hard_total_token_limit": True,
        "protocol": "omc-provider/v1",
        "token_enforcement": {
            "mode": "provider_enforced_total",
            "request_field": "max_total_tokens",
            "over_limit_behavior": "reject_before_or_during_generation",
        },
    }


def test_adapter_rejects_backend_without_hard_token_capability(tmp_path):
    backend = tmp_path / "backend"
    _write_backend(backend, hard_limit=False)

    proc = _run_adapter(backend, "capabilities")

    assert proc.returncode != 0
    assert json.loads(proc.stdout)["reason_code"] == "backend_hard_limit_unsupported"


def test_adapter_rejects_boolean_only_hard_limit_claim(tmp_path):
    backend = tmp_path / "backend"
    _write_backend(backend, enforcement_contract=False)

    proc = _run_adapter(backend, "capabilities")

    assert proc.returncode != 0
    assert json.loads(proc.stdout)["reason_code"] == "backend_enforcement_contract_missing"


def test_adapter_rejects_usage_over_signed_request_limit(tmp_path):
    backend = tmp_path / "backend"
    _write_backend(backend)
    request = {
        "protocol": "omc-provider/v1",
        "executor": "codex",
        "prompt": "implement bounded child",
        "project_root": str(tmp_path),
        "timeout_sec": 5,
        "max_total_tokens": 4,
        "max_output_chars": 100,
    }

    proc = _run_adapter(backend, "execute", request)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["returncode"] == 65
    assert result["reason_code"] == "backend_token_limit_violated"


def test_adapter_kills_backend_when_raw_response_exceeds_bound(tmp_path):
    backend = tmp_path / "backend"
    backend.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'protocol': 'omc-provider-backend/v1', "
        "'hard_total_token_limit': True, 'hard_output_limit': True, "
        "'token_enforcement': {'mode': 'provider_enforced_total', "
        "'request_field': 'max_total_tokens', 'over_limit_behavior': "
        "'reject_before_or_during_generation'}}))\n"
        "else:\n"
        "    print('x' * 1000000)\n",
        encoding="utf-8",
    )
    backend.chmod(0o755)
    request = {
        "protocol": "omc-provider/v1",
        "executor": "codex",
        "prompt": "bounded output",
        "project_root": str(tmp_path),
        "timeout_sec": 5,
        "max_total_tokens": 10,
        "max_output_chars": 20,
    }

    proc = _run_adapter(backend, "execute", request)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["returncode"] == 65
    assert result["reason_code"] == "backend_response_limit_violated"
