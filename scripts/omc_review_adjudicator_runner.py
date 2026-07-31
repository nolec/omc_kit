#!/usr/bin/env python3
"""Run a blind adjudicator in a fresh, isolated Codex execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from omc_review_v6_adjudication import (
    _validate_adjudication,
    _validate_packet_integrity,
    seal_fresh_adjudication_execution,
)


_DEFAULT_CODEX_PATH = "/Applications/ChatGPT.app/Contents/Resources/codex"
_SCHEMA_SOURCE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "omc_review_adjudication_output_schema.json"
)
_ALLOWED_ENVIRONMENT = {
    "ALL_PROXY",
    "CODEX_HOME",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TMPDIR",
    "USER",
}


def _as_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else value.encode("utf-8")


def _trusted_codex_identity(codex_path: str) -> dict[str, str]:
    """Resolve and fingerprint the single trusted Codex executable."""
    try:
        trusted = Path(_DEFAULT_CODEX_PATH).resolve(strict=True)
        candidate = Path(codex_path).resolve(strict=True)
    except OSError as exc:
        raise ValueError("trusted Codex binary is unavailable") from exc
    if candidate != trusted or not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError("trusted Codex binary path does not match")
    return {
        "path": str(candidate),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
    }


def _parse_codex_jsonl(event_stream: bytes) -> tuple[str, dict[str, Any]]:
    """Extract one provider-issued thread id and the final structured message."""
    thread_ids: list[str] = []
    final_messages: list[str] = []
    try:
        lines = event_stream.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("invalid Codex JSONL encoding") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Codex JSONL event") from exc
        if not isinstance(event, dict):
            raise ValueError("invalid Codex JSONL event")
        if event.get("type") == "thread.started":
            thread_ids.append(str(event.get("thread_id") or ""))
        item = event.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") not in (None, "agent_message")
        ):
            raise ValueError(
                f"Codex adjudication must be tool-free: {item.get('type')}"
            )
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            final_messages.append(item["text"])
    if len(thread_ids) != 1:
        raise ValueError("Codex JSONL must contain exactly one thread.started event")
    if not thread_ids[0].strip():
        raise ValueError("provider thread id is required")
    if not final_messages:
        raise ValueError("final adjudication output is required")
    try:
        output = json.loads(final_messages[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("final adjudication output is not valid JSON") from exc
    if not isinstance(output, dict):
        raise ValueError("final adjudication output must be an object")
    return thread_ids[0], output


def _build_codex_command(
    *,
    codex_path: str,
    schema_path: Path,
    model: str,
) -> list[str]:
    return [
        codex_path,
        "exec",
        "--ephemeral",
        "--json",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        model,
        "--output-schema",
        str(schema_path),
        "-",
    ]


def _sanitized_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = source if source is not None else os.environ
    return {
        key: value
        for key, value in environment.items()
        if key in _ALLOWED_ENVIRONMENT
    }


def _prompt(packet: dict[str, Any]) -> bytes:
    rendered = json.dumps(
        packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        "Act as a blind semantic review adjudicator. Use only the packet below. "
        "Do not inspect parent directories, repository files, prior runs, or infer "
        "provider identities. Classify every supplied finding exactly once and "
        "return only JSON matching the output schema. For a hit, set "
        "gold_finding_id to one supplied gold id and evidence_accuracy to accurate "
        "or inaccurate. For a false_positive, set both gold_finding_id and "
        "evidence_accuracy to null. Do not match one gold id more than once within "
        "the same review set.\n\n"
        f"PACKET:\n{rendered}\n"
    ).encode("utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_fresh_codex_adjudication(
    packet: dict[str, Any],
    *,
    receipt_key: bytes,
    output_dir: str | Path,
    model: str,
    timeout_sec: int,
    codex_path: str = _DEFAULT_CODEX_PATH,
    source_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute and atomically persist one runner-attested fresh session."""
    _validate_packet_integrity(packet)
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, int) or timeout_sec <= 0:
        raise ValueError("timeout_sec requires a positive integer")
    if not model.strip():
        raise ValueError("model is required")
    binary_identity = _trusted_codex_identity(codex_path)
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError(f"output directory already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="omc-adjudicator-work-") as workspace:
        workspace_path = Path(workspace)
        schema_path = workspace_path / "output-schema.json"
        output_schema = json.loads(_SCHEMA_SOURCE.read_text(encoding="utf-8"))
        output_schema["properties"]["packet_sha256"]["const"] = packet[
            "packet_sha256"
        ]
        _write_json(schema_path, output_schema)
        command = _build_codex_command(
            codex_path=binary_identity["path"],
            schema_path=schema_path,
            model=model,
        )
        command_fingerprint = hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode()
        ).hexdigest()
        process = subprocess.run(
            command,
            cwd=str(workspace_path),
            input=_prompt(packet),
            capture_output=True,
            timeout=timeout_sec,
            env=_sanitized_environment(source_env),
            check=False,
        )
        event_stream = _as_bytes(process.stdout)
        stderr = _as_bytes(process.stderr)
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = event_stream.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Codex adjudicator failed with exit {process.returncode}: {detail}"
            )
        provider_session_id, adjudication = _parse_codex_jsonl(event_stream)
        _validate_adjudication(packet, adjudication)
        execution_id = f"omc-runner-{uuid.uuid4().hex}"
        sealed, receipt_envelope = seal_fresh_adjudication_execution(
            packet,
            adjudication,
            executor="codex",
            model=model,
            execution_id=execution_id,
            provider_session_id=provider_session_id,
            command_fingerprint=command_fingerprint,
            event_stream=event_stream,
            receipt_key=receipt_key,
            executor_binary_path=binary_identity["path"],
            executor_binary_sha256=binary_identity["sha256"],
            tool_free_execution_verified=True,
        )

    staging = destination.with_name(
        f".{destination.name}.tmp-{uuid.uuid4().hex}"
    )
    try:
        staging.mkdir()
        _write_json(staging / "adjudication.json", sealed)
        (staging / "receipt-envelope.json").write_bytes(receipt_envelope)
        (staging / "event-stream.jsonl").write_bytes(event_stream)
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "adjudication": sealed,
        "receipt_envelope": receipt_envelope,
        "event_stream": event_stream,
        "output_dir": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--receipt-key-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout-sec", type=int, default=600)
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    result = run_fresh_codex_adjudication(
        packet,
        receipt_key=args.receipt_key_file.read_bytes(),
        output_dir=args.output_dir,
        model=args.model,
        timeout_sec=args.timeout_sec,
    )
    session_id = result["adjudication"]["adjudicator_provenance"][
        "provider_session_id"
    ]
    print(f"fresh adjudication written: {args.output_dir} ({session_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
