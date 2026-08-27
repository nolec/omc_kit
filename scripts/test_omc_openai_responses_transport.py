from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from omc_openai_responses_transport import execute_request
from omc_provider_transport_probe import probe_transport


TRANSPORT = Path(__file__).with_name("omc_openai_responses_transport.py")


def _request(tmp_path: Path, *, max_total_tokens: int = 20, max_output_chars: int = 20):
    return {
        "protocol": "omc-provider/v1",
        "executor": "codex",
        "prompt": "Return ok.",
        "project_root": str(tmp_path),
        "timeout_sec": 2,
        "max_total_tokens": max_total_tokens,
        "max_output_chars": max_output_chars,
    }


def _run(action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TRANSPORT), action],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "OMC_OPENAI_MODEL": "gpt-test"},
    )


def _completed_usage(*, input_tokens: int = 7, output_text: str = "ok"):
    reasoning_tokens = 2
    visible_output_tokens = 3
    return {
        "output": output_text,
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens": reasoning_tokens + visible_output_tokens,
            "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            "total_tokens": input_tokens + reasoning_tokens + visible_output_tokens,
        },
        "raw_response": b"response.completed fixture",
    }


def test_transport_probe_accepts_responses_capabilities() -> None:
    expected_sha256 = hashlib.sha256(TRANSPORT.read_bytes()).hexdigest()
    assert probe_transport(
        TRANSPORT,
        expected_transport_sha256=expected_sha256,
        signer_private_key=Ed25519PrivateKey.generate(),
    )["status"] == "SUPPORTED"


def test_backend_capabilities_match_provider_enforcement_contract() -> None:
    completed = _run("capabilities")

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["protocol"] == "omc-provider-backend/v1"
    assert payload["hard_total_token_limit"] is True
    assert payload["hard_output_limit"] is True
    assert payload["token_enforcement"]["mode"] == "provider_enforced_total"


def test_execute_counts_exact_payload_then_applies_native_output_cap(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def count_tokens(payload: dict[str, Any], **_kwargs: Any) -> tuple[int, bytes]:
        calls.append(("count", payload))
        return 7, b'{"input_tokens":7}'

    def create_response(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(("create", payload))
        return _completed_usage()

    result = execute_request(
        _request(tmp_path),
        api_key="test-key",
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        count_tokens=count_tokens,
        create_response=create_response,
    )

    assert result["returncode"] == 0
    assert result["output"] == "ok"
    assert result["token_usage"] == {
        "cached_input_tokens": 4,
        "input_tokens": 7,
        "output_tokens": 5,
        "reasoning_tokens": 2,
        "total_tokens": 12,
    }
    assert result["native_max_output_tokens"] == 13
    assert calls == [
        ("count", {"input": "Return ok.", "model": "gpt-test"}),
        (
            "create",
            {
                "input": "Return ok.",
                "model": "gpt-test",
                "max_output_tokens": 13,
                "stream": True,
            },
        ),
    ]


def test_execute_rejects_exhausted_budget_before_generation(tmp_path: Path) -> None:
    generation_called = False

    def count_tokens(_payload: dict[str, Any], **_kwargs: Any) -> tuple[int, bytes]:
        return 7, b'{"input_tokens":7}'

    def create_response(_payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal generation_called
        generation_called = True
        return _completed_usage()

    result = execute_request(
        _request(tmp_path, max_total_tokens=7),
        api_key="test-key",
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        count_tokens=count_tokens,
        create_response=create_response,
    )

    assert result["reason_code"] == "backend_token_budget_exhausted"
    assert generation_called is False


def test_execute_rejects_usage_that_differs_from_exact_count(tmp_path: Path) -> None:
    result = execute_request(
        _request(tmp_path),
        api_key="test-key",
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        count_tokens=lambda _payload, **_kwargs: (7, b'{"input_tokens":7}'),
        create_response=lambda _payload, **_kwargs: _completed_usage(input_tokens=8),
    )

    assert result["reason_code"] == "backend_usage_exceeds_reservation"


def test_execute_requires_api_key_without_contacting_transport(tmp_path: Path) -> None:
    contacted = False

    def count_tokens(_payload: dict[str, Any], **_kwargs: Any) -> tuple[int, bytes]:
        nonlocal contacted
        contacted = True
        return 7, b"{}"

    result = execute_request(
        _request(tmp_path),
        api_key="",
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        count_tokens=count_tokens,
        create_response=lambda _payload, **_kwargs: _completed_usage(),
    )

    assert result["reason_code"] == "backend_auth_missing"
    assert contacted is False


def test_execute_rejects_output_over_character_limit(tmp_path: Path) -> None:
    result = execute_request(
        _request(tmp_path, max_output_chars=2),
        api_key="test-key",
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        count_tokens=lambda _payload, **_kwargs: (7, b'{"input_tokens":7}'),
        create_response=lambda _payload, **_kwargs: _completed_usage(
            output_text="too long"
        ),
    )

    assert result["reason_code"] == "backend_output_limit_violated"


def test_execute_rejects_non_openai_https_endpoint_before_sending_key(
    tmp_path: Path,
) -> None:
    contacted = False

    def count_tokens(_payload: dict[str, Any], **_kwargs: Any) -> tuple[int, bytes]:
        nonlocal contacted
        contacted = True
        return 7, b"{}"

    result = execute_request(
        _request(tmp_path),
        api_key="secret-key",
        model="gpt-test",
        base_url="https://example.invalid/v1",
        count_tokens=count_tokens,
        create_response=lambda _payload, **_kwargs: _completed_usage(),
    )

    assert result["reason_code"] == "backend_base_url_invalid"
    assert contacted is False


def test_execute_fails_closed_when_transport_times_out_while_reading(
    tmp_path: Path,
) -> None:
    def count_tokens(_payload: dict[str, Any], **_kwargs: Any) -> tuple[int, bytes]:
        raise TimeoutError("read timed out")

    result = execute_request(
        _request(tmp_path),
        api_key="test-key",
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        count_tokens=count_tokens,
        create_response=lambda _payload, **_kwargs: _completed_usage(),
    )

    assert result["reason_code"] == "backend_transport_error"
