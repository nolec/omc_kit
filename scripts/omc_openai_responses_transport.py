#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


VERSION = "omc-openai-responses-transport/1.0"
PROTOCOL = "omc-provider/v1"
BACKEND_PROTOCOL = "omc-provider-backend/v1"
CAPABILITY_SCHEMA = "omc-provider-transport-capabilities/v1"
SELF_TEST_SCHEMA = "omc-provider-transport-self-test/v1"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
MAX_JSON_RESPONSE_BYTES = 1024 * 1024
MAX_STREAM_RESPONSE_BYTES = 16 * 1024 * 1024
TOKEN_ENFORCEMENT_CONTRACT = {
    "mode": "provider_enforced_total",
    "request_field": "max_total_tokens",
    "over_limit_behavior": "reject_before_or_during_generation",
}
TRANSPORT_CAPABILITIES = {
    "schema_version": CAPABILITY_SCHEMA,
    "cached_input_included": True,
    "exact_pre_call_input_count": True,
    "native_output_token_cap": True,
    "raw_usage_event_stream": True,
    "reasoning_tokens_reported": True,
}


class TransportFailure(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _blocked(reason_code: str, message: str, *, returncode: int = 69) -> dict[str, Any]:
    return {
        "returncode": returncode,
        "output": message,
        "reason_code": reason_code,
    }


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_request(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
        return None
    if not isinstance(payload.get("prompt"), str) or not payload["prompt"]:
        return None
    if not isinstance(payload.get("executor"), str) or not payload["executor"]:
        return None
    if not _positive_number(payload.get("timeout_sec")):
        return None
    if not _positive_int(payload.get("max_total_tokens")):
        return None
    if not _positive_int(payload.get("max_output_chars")):
        return None
    project_root = payload.get("project_root")
    if not isinstance(project_root, str) or not Path(project_root).resolve().is_dir():
        return None
    return payload


def _validate_base_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise TransportFailure("backend_base_url_invalid", "provider base URL invalid")
    if (
        parsed.scheme == "https"
        and parsed.hostname == "api.openai.com"
        and parsed.port in {None, 443}
    ):
        return value
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        return value
    raise TransportFailure("backend_base_url_invalid", "provider base URL invalid")


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _open_request(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout_sec: float,
):
    request = Request(
        url,
        data=_canonical_bytes(payload),
        headers=_headers(api_key),
        method="POST",
    )
    try:
        return urlopen(request, timeout=timeout_sec)
    except HTTPError as exc:
        exc.close()
        raise TransportFailure(
            "backend_http_error", f"provider HTTP status {exc.code}"
        ) from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise TransportFailure("backend_transport_error", "provider request failed") from exc


def _count_input_tokens(
    payload: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    timeout_sec: float,
) -> tuple[int, bytes]:
    with _open_request(
        f"{_validate_base_url(base_url)}/responses/input_tokens",
        payload,
        api_key=api_key,
        timeout_sec=timeout_sec,
    ) as response:
        raw = response.read(MAX_JSON_RESPONSE_BYTES + 1)
    if len(raw) > MAX_JSON_RESPONSE_BYTES:
        raise TransportFailure("backend_count_response_too_large", "token count response too large")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportFailure("backend_count_response_invalid", "token count response invalid") from exc
    input_tokens = decoded.get("input_tokens") if isinstance(decoded, dict) else None
    if not _positive_int(input_tokens):
        raise TransportFailure("backend_count_response_invalid", "token count missing")
    return input_tokens, raw


def _create_streaming_response(
    payload: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    timeout_sec: float,
    max_output_chars: int,
) -> dict[str, Any]:
    output: list[str] = []
    output_chars = 0
    completed_response: dict[str, Any] | None = None
    raw = bytearray()
    with _open_request(
        f"{_validate_base_url(base_url)}/responses",
        payload,
        api_key=api_key,
        timeout_sec=timeout_sec,
    ) as response:
        while True:
            remaining = MAX_STREAM_RESPONSE_BYTES - len(raw)
            if remaining <= 0:
                raise TransportFailure("backend_stream_too_large", "provider stream too large")
            line = response.readline(remaining + 1)
            if not line:
                break
            raw.extend(line)
            if len(raw) > MAX_STREAM_RESPONSE_BYTES:
                raise TransportFailure("backend_stream_too_large", "provider stream too large")
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data or data == b"[DONE]":
                continue
            try:
                event = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportFailure("backend_stream_invalid", "provider stream invalid") from exc
            if not isinstance(event, dict):
                raise TransportFailure("backend_stream_invalid", "provider event invalid")
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise TransportFailure("backend_stream_invalid", "output delta invalid")
                output_chars += len(delta)
                if output_chars > max_output_chars:
                    raise TransportFailure(
                        "backend_output_limit_violated", "backend output limit exceeded"
                    )
                output.append(delta)
            elif event_type == "response.completed":
                value = event.get("response")
                if not isinstance(value, dict) or completed_response is not None:
                    raise TransportFailure("backend_stream_invalid", "completion event invalid")
                completed_response = value
            elif event_type in {"response.failed", "response.incomplete", "error"}:
                raise TransportFailure("backend_generation_failed", "provider generation failed")
    if completed_response is None:
        raise TransportFailure("backend_usage_missing", "completion usage missing")
    return {
        "output": "".join(output),
        "usage": completed_response.get("usage"),
        "raw_response": bytes(raw),
        "response_id": completed_response.get("id"),
    }


def _usage_values(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    input_details = value.get("input_tokens_details")
    output_details = value.get("output_tokens_details")
    if not isinstance(input_details, dict) or not isinstance(output_details, dict):
        return None
    result = {
        "input_tokens": value.get("input_tokens"),
        "cached_input_tokens": input_details.get("cached_tokens"),
        "output_tokens": value.get("output_tokens"),
        "reasoning_tokens": output_details.get("reasoning_tokens"),
        "total_tokens": value.get("total_tokens"),
    }
    if any(not _nonnegative_int(item) for item in result.values()):
        return None
    if result["cached_input_tokens"] > result["input_tokens"]:
        return None
    if result["reasoning_tokens"] > result["output_tokens"]:
        return None
    if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        return None
    return result


CountTokens = Callable[..., tuple[int, bytes]]
CreateResponse = Callable[..., dict[str, Any]]


def execute_request(
    payload: Any,
    *,
    api_key: str,
    model: str,
    base_url: str,
    count_tokens: CountTokens = _count_input_tokens,
    create_response: CreateResponse = _create_streaming_response,
) -> dict[str, Any]:
    request = _validate_request(payload)
    if request is None:
        return _blocked("provider_request_invalid", "provider request invalid", returncode=64)
    if not api_key:
        return _blocked("backend_auth_missing", "provider authentication missing")
    if not model or not re.fullmatch(r"[A-Za-z0-9._:-]+", model):
        return _blocked("backend_model_invalid", "provider model invalid")
    try:
        base_url = _validate_base_url(base_url)
        input_payload = {"input": request["prompt"], "model": model}
        input_tokens, raw_count = count_tokens(
            input_payload,
            api_key=api_key,
            base_url=base_url,
            timeout_sec=float(request["timeout_sec"]),
        )
        if not _positive_int(input_tokens):
            raise TransportFailure("backend_count_response_invalid", "token count invalid")
        max_output_tokens = request["max_total_tokens"] - input_tokens
        if max_output_tokens <= 0:
            return _blocked(
                "backend_token_budget_exhausted",
                "input consumes total token budget",
                returncode=65,
            )
        generation_payload = {
            **input_payload,
            "max_output_tokens": max_output_tokens,
            "stream": True,
        }
        completed = create_response(
            generation_payload,
            api_key=api_key,
            base_url=base_url,
            timeout_sec=float(request["timeout_sec"]),
            max_output_chars=request["max_output_chars"],
        )
    except TransportFailure as exc:
        return _blocked(exc.reason_code, str(exc), returncode=65)
    except (TimeoutError, OSError):
        return _blocked(
            "backend_transport_error", "provider request failed", returncode=65
        )

    output = completed.get("output") if isinstance(completed, dict) else None
    raw_response = completed.get("raw_response") if isinstance(completed, dict) else None
    usage = _usage_values(completed.get("usage") if isinstance(completed, dict) else None)
    if not isinstance(output, str) or not isinstance(raw_response, bytes) or usage is None:
        return _blocked("backend_usage_missing", "provider usage invalid", returncode=65)
    if len(output) > request["max_output_chars"]:
        return _blocked(
            "backend_output_limit_violated", "backend output limit exceeded", returncode=65
        )
    if (
        usage["input_tokens"] != input_tokens
        or usage["output_tokens"] > max_output_tokens
        or usage["total_tokens"] > request["max_total_tokens"]
    ):
        return _blocked(
            "backend_usage_exceeds_reservation",
            "provider usage exceeds reservation",
            returncode=65,
        )
    return {
        "returncode": 0,
        "output": output,
        "token_usage": usage,
        "native_max_output_tokens": max_output_tokens,
        "request_sha256": _sha256(_canonical_bytes(input_payload)),
        "count_response_sha256": _sha256(raw_count),
        "raw_response_sha256": _sha256(raw_response),
        "response_id": completed.get("response_id"),
    }


def _backend_capabilities() -> dict[str, Any]:
    return {
        "protocol": BACKEND_PROTOCOL,
        "hard_total_token_limit": True,
        "hard_output_limit": True,
        "token_enforcement": TOKEN_ENFORCEMENT_CONTRACT,
        "transport": "openai-responses",
        "transport_version": VERSION,
    }


def _transport_self_test() -> dict[str, Any]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def count_tokens(payload: dict[str, Any], **_kwargs: Any) -> tuple[int, bytes]:
        calls.append(("count", payload))
        return 4, b'{"input_tokens":4}'

    def create_response(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(("create", payload))
        return {
            "output": "ok",
            "usage": {
                "input_tokens": 4,
                "input_tokens_details": {"cached_tokens": 1},
                "output_tokens": 3,
                "output_tokens_details": {"reasoning_tokens": 1},
                "total_tokens": 7,
            },
            "raw_response": b"self-test-response",
            "response_id": "self-test",
        }

    def timeout_count(
        _payload: dict[str, Any], **_kwargs: Any
    ) -> tuple[int, bytes]:
        raise TimeoutError("self-test timeout")

    request = {
        "protocol": PROTOCOL,
        "executor": "self-test",
        "prompt": "Return ok.",
        "project_root": os.getcwd(),
        "timeout_sec": 1,
        "max_total_tokens": 10,
        "max_output_chars": 8,
    }
    result = execute_request(
        request,
        api_key="self-test-key",
        model="self-test-model",
        base_url="https://api.openai.com/v1",
        count_tokens=count_tokens,
        create_response=create_response,
    )
    failure = execute_request(
        request,
        api_key="self-test-key",
        model="self-test-model",
        base_url="https://api.openai.com/v1",
        count_tokens=timeout_count,
        create_response=create_response,
    )
    count_payload = calls[0][1] if len(calls) >= 1 else None
    generation_payload = calls[1][1] if len(calls) >= 2 else None
    return {
        "schema_version": SELF_TEST_SCHEMA,
        "count_generation_payload_match": (
            isinstance(count_payload, dict)
            and isinstance(generation_payload, dict)
            and {
                "input": generation_payload.get("input"),
                "model": generation_payload.get("model"),
            }
            == count_payload
        ),
        "native_output_cap_forwarded": (
            isinstance(generation_payload, dict)
            and generation_payload.get("max_output_tokens") == 6
        ),
        "usage_parsed": (
            result.get("returncode") == 0
            and result.get("token_usage", {}).get("total_tokens") == 7
        ),
        "failure_is_fail_closed": (
            failure.get("returncode") != 0
            and failure.get("reason_code") == "backend_transport_error"
        ),
    }


def _execute_cli() -> int:
    try:
        payload = json.load(sys.stdin)
    except (UnicodeError, json.JSONDecodeError):
        payload = None
    result = execute_request(
        payload,
        api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        model=os.environ.get("OMC_OPENAI_MODEL", "").strip(),
        base_url=os.environ.get("OMC_OPENAI_BASE_URL", DEFAULT_BASE_URL).strip(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        print(VERSION)
        return 0
    if args == ["exec", "--help"]:
        print("execute OMC provider request with exact count and native output cap")
        return 0
    if args == ["transport-capabilities", "--json"]:
        print(json.dumps(TRANSPORT_CAPABILITIES, sort_keys=True))
        return 0
    if args == ["transport-self-test", "--json"]:
        print(json.dumps(_transport_self_test(), sort_keys=True))
        return 0
    if args == ["capabilities"]:
        print(json.dumps(_backend_capabilities(), sort_keys=True))
        return 0
    if args == ["execute"]:
        return _execute_cli()
    print(json.dumps(_blocked("backend_action_invalid", "backend action invalid")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
