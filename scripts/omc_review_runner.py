"""Normalize review CLI output into the OMC comparison contract."""
from __future__ import annotations

import math
import re
from typing import Any


_VERDICT_RE = re.compile(r"\bVERDICT\s*:\s*(APPROVE(?: WITH NOTES)?|REVISE|BLOCK|HOLD|PROCEED)\b", re.IGNORECASE)
_NEXT_ACTION_RE = re.compile(r"^\s*next_action\s*:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_FINDING_RE = re.compile(
    r"^\s*-\s*\[([^\]]+)\]\s*[—-]\s*(.+?)\s*$"
)
_SEVERITY_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*[—-]\s*(.+?)\s*$")
_LOCATION_RE = re.compile(r"\[([^:\]]+):(\d+)\]\s*(.+)$")
_SEVERITIES = {"치명", "중대", "경미", "제안", "P0", "P1", "P2", "P3"}
_SENSITIVE_RE = re.compile(
    r"\b(?:ghp|github_pat)_[A-Za-z0-9_]+\b|\bAKIA[0-9A-Z]{8,}\b|\bBearer\s+\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
    re.IGNORECASE,
)


def _redact_output(value: str) -> str:
    return _SENSITIVE_RE.sub("<redacted>", value)


def _validate_batch_id(batch_id: str | None) -> None:
    if not batch_id:
        return
    normalized = batch_id.replace("\\", "/")
    if normalized.startswith("/") or ":/" in normalized or ".." in normalized.split("/"):
        raise ValueError("non-anonymized batch_id")
    if _SENSITIVE_RE.search(batch_id):
        raise ValueError("sensitive value for batch_id")


def _parse_findings(stdout: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    pending: dict[str, str] | None = None
    for raw_line in stdout.splitlines():
        match = _FINDING_RE.match(raw_line)
        if match and match.group(1).strip() in _SEVERITIES:
            severity = match.group(1).strip()
            body = match.group(2).strip()
            location = _LOCATION_RE.search(body)
            if location:
                pending = {
                    "severity": severity,
                    "file": location.group(1).strip(),
                    "line": location.group(2),
                    "message": location.group(3).strip(),
                }
                findings.append(pending)
            else:
                pending = {"severity": severity, "message": body}
                findings.append(pending)
            continue
        header = _SEVERITY_HEADER_RE.match(raw_line)
        if header and header.group(1).strip() in _SEVERITIES:
            pending = {"severity": header.group(1).strip(), "message": header.group(2).strip()}
            findings.append(pending)
            continue
        if pending is not None and not pending.get("file"):
            location = _LOCATION_RE.search(raw_line)
            if location:
                pending["file"] = location.group(1).strip()
                pending["line"] = location.group(2)
                pending["message"] = location.group(3).strip()
    return findings


def normalize_review_result(
    *,
    provider: str,
    case_id: str,
    diff_id: str,
    status: str,
    stdout: str,
    stderr: str,
    duration_ms: int | float,
    batch_id: str | None = None,
    input_tokens: int | float | None = None,
    output_tokens: int | float | None = None,
    cost_usd: int | float | None = None,
    runner: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Return a strict, provider-neutral result for a completed review run."""
    if not provider.strip() or not case_id.strip() or not diff_id.strip():
        raise ValueError("provider, case_id, and diff_id are required")
    _validate_batch_id(batch_id)
    if status not in {"completed", "failed"}:
        raise ValueError(f"unsupported review status: {status}")
    for name, value in (
        ("duration_ms", duration_ms),
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("cost_usd", cost_usd),
    ):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{name} requires non-negative finite number")
    if status == "completed":
        verdict_match = _VERDICT_RE.search(stdout)
        if not verdict_match:
            raise ValueError("completed review output requires verdict")
        verdict = verdict_match.group(1).upper()
    else:
        verdict = "unknown"

    metrics: dict[str, int | float] = {"duration_ms": duration_ms}
    for name, value in (
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("cost_usd", cost_usd),
    ):
        if value is not None:
            metrics[name] = value

    prompt_parts = [provider]
    if batch_id:
        prompt_parts.append(batch_id)
    prompt_parts.append(case_id)
    result: dict[str, Any] = {
        "case_id": case_id,
        "diff_id": diff_id,
        "prompt_id": ":".join(prompt_parts),
        "execution_mode": "cli_completed" if status == "completed" else "cli_failed",
        "status": status,
        "runner": runner or provider,
        "model": model,
        "verdict": verdict,
        "next_action": (_NEXT_ACTION_RE.search(stdout).group(1).strip() if _NEXT_ACTION_RE.search(stdout) else None),
        "findings": _parse_findings(stdout),
        "metrics": metrics,
        "stdout": _redact_output(stdout),
        "stderr": _redact_output(stderr),
    }
    return result
