#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


PROTOCOL = "omc-product-value-arm/v1"
PROVIDER_PROTOCOL = "omc-provider/v1"


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    fields = ("input_tokens", "output_tokens", "total_tokens")
    if any(
        not isinstance(value.get(field), int)
        or isinstance(value[field], bool)
        or value[field] < 0
        for field in fields
    ) or value["input_tokens"] + value["output_tokens"] != value["total_tokens"]:
        return None
    return {field: value[field] for field in fields}


def _result(
    status: str,
    reason_code: str,
    *,
    elapsed_sec: float = 0.0,
    output: str = "",
    token_usage: dict[str, int] | None = None,
    environment_receipt_sha256: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "reason_code": reason_code,
        "elapsed_sec": elapsed_sec,
        "output": output,
        "token_usage": token_usage
        or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "intervention_events": [],
        "review_findings": [],
        "duplicate_executions": 0,
        "budget_violations": 0,
        **extra,
    }
    if environment_receipt_sha256 is not None:
        payload["environment_receipt_sha256"] = environment_receipt_sha256
    return payload


def _request() -> dict[str, Any] | None:
    try:
        payload = json.load(sys.stdin)
    except (UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("protocol") != PROTOCOL
        or payload.get("arm") not in {"omc", "baseline"}
        or not isinstance(payload.get("packet"), dict)
        or not isinstance(payload.get("provider_snapshot"), dict)
        or not isinstance(payload.get("limits"), dict)
        or not isinstance(payload.get("execution_bundle"), dict)
        or not isinstance(payload.get("artifact_root"), str)
    ):
        return None
    return payload


def _backend_matches(snapshot: dict[str, Any]) -> bool:
    raw_path = os.environ.get("OMC_PROVIDER_BACKEND", "").strip()
    expected = snapshot.get("backend_sha256")
    if not raw_path or not isinstance(expected, str) or len(expected) != 64:
        return False
    path = Path(raw_path).expanduser().resolve(strict=False)
    try:
        return path.is_file() and _file_sha256(path) == expected
    except OSError:
        return False


def _run_json(
    command: list[str],
    payload: dict[str, Any] | None,
    *,
    cwd: Path,
    timeout: float,
) -> tuple[int, dict[str, Any] | None, str]:
    try:
        proc = subprocess.run(
            command,
            input="" if payload is None else json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return 70, None, "adapter unavailable"
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = None
    return proc.returncode, result if isinstance(result, dict) else None, proc.stderr


def _baseline(request: dict[str, Any], environment_hash: str) -> dict[str, Any]:
    bundle = request["execution_bundle"]
    provider = Path(str(bundle.get("provider_adapter", ""))).resolve(strict=False)
    brief = request["packet"].get("baseline_execution_brief")
    limits = request["limits"]
    if not provider.is_file() or not isinstance(brief, str) or not brief.strip():
        return _result("parent_review", "baseline_input_invalid")
    capability_returncode, capabilities, _capability_stderr = _run_json(
        [str(provider), "capabilities"],
        None,
        cwd=Path.cwd(),
        timeout=min(10.0, float(limits.get("max_total_elapsed_sec", 0))),
    )
    if (
        capability_returncode != 0
        or capabilities is None
        or capabilities.get("protocol") != PROVIDER_PROTOCOL
        or capabilities.get("hard_total_token_limit") is not True
        or capabilities.get("hard_output_limit") is not True
    ):
        return _result(
            "parent_review",
            "provider_capability_invalid",
            environment_receipt_sha256=environment_hash,
        )
    started = time.monotonic()
    returncode, provider_result, stderr = _run_json(
        [str(provider), "execute"],
        {
            "protocol": PROVIDER_PROTOCOL,
            "executor": request["provider_snapshot"].get("provider_family"),
            "prompt": brief,
            "project_root": str(Path.cwd()),
            "timeout_sec": limits.get("max_total_elapsed_sec"),
            "max_total_tokens": limits.get("max_total_tokens"),
            "max_output_chars": limits.get("max_output_chars"),
        },
        cwd=Path.cwd(),
        timeout=float(limits.get("max_total_elapsed_sec", 0)),
    )
    elapsed = time.monotonic() - started
    usage = _usage(provider_result.get("token_usage")) if provider_result else None
    if returncode != 0 or provider_result is None or provider_result.get("returncode") != 0 or usage is None:
        return _result(
            "parent_review",
            "provider_failed",
            elapsed_sec=elapsed,
            output=(provider_result or {}).get("output", stderr),
            token_usage=usage,
            environment_receipt_sha256=environment_hash,
        )
    return _result(
        "completed",
        "completed",
        elapsed_sec=elapsed,
        output=provider_result.get("output", ""),
        token_usage=usage,
        environment_receipt_sha256=environment_hash,
    )


def _omc(request: dict[str, Any], environment_hash: str) -> dict[str, Any]:
    bundle = request["execution_bundle"]
    scheduler = Path(str(bundle.get("scheduler", ""))).resolve(strict=False)
    dependency = Path(str(bundle.get("executor_shadow", ""))).resolve(strict=False)
    provider = Path(str(bundle.get("provider_adapter", ""))).resolve(strict=False)
    execution = request["packet"].get("omc_execution")
    limits = request["limits"]
    if (
        not scheduler.is_file()
        or not dependency.is_file()
        or not provider.is_file()
        or not isinstance(execution, dict)
        or not isinstance(execution.get("grant"), dict)
        or not isinstance(execution.get("prompts"), dict)
    ):
        return _result("parent_review", "omc_execution_bundle_invalid")
    artifacts = Path(request["artifact_root"]).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    grant_path = artifacts / "grant.json"
    prompts_path = artifacts / "prompts.json"
    dag_ledger = artifacts / "dag-ledger.json"
    child_ledger = artifacts / "child-ledger.json"
    grant_path.write_text(json.dumps(execution["grant"], sort_keys=True), encoding="utf-8")
    prompts_path.write_text(json.dumps(execution["prompts"], sort_keys=True), encoding="utf-8")
    started = time.monotonic()
    returncode, scheduler_result, stderr = _run_json(
        [
            sys.executable,
            str(scheduler),
            "--grant-file",
            str(grant_path),
            "--prompts-file",
            str(prompts_path),
            "--target",
            str(Path.cwd()),
            "--dag-ledger",
            str(dag_ledger),
            "--child-ledger",
            str(child_ledger),
            "--provider-adapter",
            str(provider),
        ],
        None,
        cwd=Path.cwd(),
        timeout=float(limits.get("max_total_elapsed_sec", 0)),
    )
    elapsed = time.monotonic() - started
    if scheduler_result is None:
        return _result(
            "parent_review",
            "scheduler_failed",
            elapsed_sec=elapsed,
            output=stderr,
            environment_receipt_sha256=environment_hash,
        )
    usage = _usage({
        "input_tokens": scheduler_result.get("input_tokens", 0),
        "output_tokens": scheduler_result.get("output_tokens", 0),
        "total_tokens": scheduler_result.get("total_tokens", 0),
    })
    if usage is None:
        return _result(
            "parent_review",
            "scheduler_usage_invalid",
            elapsed_sec=elapsed,
            output=json.dumps(scheduler_result, ensure_ascii=False, sort_keys=True),
            environment_receipt_sha256=environment_hash,
        )
    completed = returncode == 0 and scheduler_result.get("status") == "completed"
    extra: dict[str, Any] = {
        "executed_child_count": len(scheduler_result.get("completed_child_ids", [])),
        "scope_violations": [],
    }
    if dag_ledger.is_file() and child_ledger.is_file():
        extra.update({
            "dag_ledger_sha256": _file_sha256(dag_ledger),
            "child_ledger_sha256": _file_sha256(child_ledger),
        })
    return _result(
        "completed" if completed else "parent_review",
        "completed" if completed else scheduler_result.get("reason_code", "scheduler_failed"),
        elapsed_sec=elapsed,
        output=json.dumps(scheduler_result, ensure_ascii=False, sort_keys=True),
        token_usage=usage,
        environment_receipt_sha256=environment_hash,
        **extra,
    )


def capabilities() -> int:
    print(json.dumps({
        "protocol": PROTOCOL,
        "hard_total_token_limit": True,
        "hard_output_limit": True,
        "supported_arms": ["omc", "baseline"],
    }, sort_keys=True))
    return 0


def execute() -> int:
    request = _request()
    if request is None:
        print(json.dumps(_result("parent_review", "arm_request_invalid"), sort_keys=True))
        return 0
    if not _backend_matches(request["provider_snapshot"]):
        print(json.dumps(_result("parent_review", "backend_snapshot_mismatch"), sort_keys=True))
        return 0
    environment_hash = _canonical_sha256(request["packet"].get("environment_receipt"))
    result = (
        _omc(request, environment_hash)
        if request["arm"] == "omc"
        else _baseline(request, environment_hash)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["capabilities"]:
        return capabilities()
    if args == ["execute"]:
        return execute()
    print(json.dumps(_result("parent_review", "arm_action_invalid"), sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
