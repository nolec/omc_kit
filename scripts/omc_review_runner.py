"""Normalize review CLI output into the OMC comparison contract."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


_VERDICT_RE = re.compile(r"\bVERDICT\s*:\s*(APPROVE(?: WITH NOTES)?|REVISE|BLOCK|HOLD|PROCEED)\b", re.IGNORECASE)
_CODEX_APPROVAL_RE = re.compile(
    r"(?:no actionable (?:regressions|findings) (?:were )?(?:identified|found)\.?|"
    r"no evident regressions)",
    re.IGNORECASE,
)
_CODEX_FINDING_RE = re.compile(r"^\s*-\s*\[P[0-3]\]", re.MULTILINE)
_NEXT_ACTION_RE = re.compile(r"^\s*next_action\s*:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_FINDING_RE = re.compile(
    r"^\s*-\s*\[([^\]]+)\]\s*[—-]\s*(.+?)\s*$"
)
_CODEX_FINDING_LINE_RE = re.compile(
    r"^\s*-\s*\[(P[0-3])\]\s+(.+?)\s+[—-]\s+(.+?):(\d+)(?:-\d+)?\s*$"
)
_SEVERITY_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*[—-]\s*(.+?)\s*$")
_LOCATION_RE = re.compile(r"\[([^:\]]+):(\d+)\]\s*(.+)$")
_SEVERITIES = {"치명", "중대", "경미", "제안", "P0", "P1", "P2", "P3"}
_ALLOWED_VERDICTS = {"APPROVE", "APPROVE WITH NOTES", "REVISE", "BLOCK", "HOLD", "PROCEED"}
_SENSITIVE_RE = re.compile(
    r"\b(?:ghp|github_pat)_[A-Za-z0-9_]+\b|\bAKIA[0-9A-Z]{8,}\b|\bBearer\s+\S+|"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|/(?:Users|home)/[^\s'\"]+",
    re.IGNORECASE,
)


def _redact_output(value: str) -> str:
    return _SENSITIVE_RE.sub("<redacted>", value)


def _redact_finding(finding: dict[str, str]) -> dict[str, str]:
    """Keep parsed findings useful without retaining sensitive raw values."""
    return {key: _redact_output(value) for key, value in finding.items()}


def _as_text(value: str | bytes | None) -> str:
    """Normalize subprocess output before it enters the portable result contract."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _codex_verdict(stdout: str) -> str | None:
    """Map Codex review-agent's terminal formats onto the shared verdict contract."""
    explicit = _VERDICT_RE.search(stdout)
    if explicit:
        return explicit.group(1).upper()
    if _CODEX_FINDING_RE.search(stdout):
        return "REVISE"
    if _CODEX_APPROVAL_RE.search(stdout):
        return "APPROVE"
    return None


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
        codex_match = _CODEX_FINDING_LINE_RE.match(raw_line)
        if codex_match:
            findings.append(
                {
                    "severity": codex_match.group(1),
                    "message": codex_match.group(2).strip(),
                    "file": codex_match.group(3).strip(),
                    "line": codex_match.group(4),
                }
            )
            pending = None
            continue
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
    verdict_override: str | None = None,
) -> dict[str, Any]:
    """Return a strict, provider-neutral result for a completed review run."""
    if not provider.strip() or not case_id.strip() or not diff_id.strip():
        raise ValueError("provider, case_id, and diff_id are required")
    _validate_batch_id(batch_id)
    if status not in {"completed", "failed"}:
        raise ValueError(f"unsupported review status: {status}")
    normalized_override = verdict_override.upper() if verdict_override else None
    if normalized_override is not None and normalized_override not in _ALLOWED_VERDICTS:
        raise ValueError("verdict_override requires a supported verdict")
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
        if normalized_override is None and not verdict_match:
            raise ValueError("completed review output requires verdict")
        verdict = normalized_override or verdict_match.group(1).upper()
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
    is_completed = status == "completed"
    result: dict[str, Any] = {
        "case_id": case_id,
        "diff_id": diff_id,
        "prompt_id": ":".join(prompt_parts),
        "execution_mode": "cli_completed" if status == "completed" else "cli_failed",
        "status": status,
        "runner": runner or provider,
        "model": model,
        "verdict": verdict,
        "next_action": (
            _redact_output(_NEXT_ACTION_RE.search(stdout).group(1).strip())
            if is_completed and _NEXT_ACTION_RE.search(stdout)
            else None
        ),
        "findings": [_redact_finding(finding) for finding in _parse_findings(stdout)] if is_completed else [],
        "metrics": metrics,
        "stdout": _redact_output(stdout),
        "stderr": _redact_output(stderr),
    }
    return result


def run_codex_review(
    workdir: str | Path,
    *,
    case_id: str,
    diff_id: str,
    timeout_sec: int,
    result_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run Codex review and reject incomplete output even when the CLI exits zero."""
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, int) or timeout_sec <= 0:
        raise ValueError("timeout_sec requires a positive integer")
    started = time.monotonic()
    try:
        process = subprocess.run(
            ["/Applications/ChatGPT.app/Contents/Resources/codex", "review", "--uncommitted"],
            cwd=str(workdir), text=True, capture_output=True, timeout=timeout_sec,
            env={**os.environ, "TMPDIR": "/private/tmp"}, check=False,
        )
        stdout = _as_text(process.stdout)
        stderr = _as_text(process.stderr)
        codex_verdict = _codex_verdict(stdout)
        status = "completed" if process.returncode == 0 and codex_verdict else "failed"
        result = normalize_review_result(
            provider="codex", case_id=case_id, diff_id=diff_id, status=status,
            stdout=stdout, stderr=stderr, duration_ms=int((time.monotonic() - started) * 1000),
            runner="codex review", verdict_override=codex_verdict,
        )
    except subprocess.TimeoutExpired as error:
        result = normalize_review_result(
            provider="codex", case_id=case_id, diff_id=diff_id, status="failed",
            stdout=_as_text(error.stdout), stderr=_as_text(error.stderr) or "timeout",
            duration_ms=int((time.monotonic() - started) * 1000), runner="codex review",
        )
    except OSError as error:
        result = normalize_review_result(
            provider="codex", case_id=case_id, diff_id=diff_id, status="failed",
            stdout="", stderr=str(error),
            duration_ms=int((time.monotonic() - started) * 1000), runner="codex review",
        )

    if result_path is not None:
        destination = Path(result_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
