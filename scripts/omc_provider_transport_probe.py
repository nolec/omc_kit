#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


EVIDENCE_SCHEMA = "omc-provider-transport-feasibility/v1"
CAPABILITY_SCHEMA = "omc-provider-transport-capabilities/v1"
SELF_TEST_SCHEMA = "omc-provider-transport-self-test/v1"
SUPPORTED = "SUPPORTED"
HOLD = "HOLD_TRANSPORT_UNSUPPORTED"
REQUIRED_CHECKS = (
    "cached_input_included",
    "exact_pre_call_input_count",
    "native_output_token_cap",
    "raw_usage_event_stream",
    "reasoning_tokens_reported",
)
REQUIRED_SELF_TEST_CHECKS = (
    "count_generation_payload_match",
    "native_output_cap_forwarded",
    "usage_parsed",
    "failure_is_fail_closed",
)
CANONICAL_COMMANDS = (
    ("version", ("--version",)),
    ("exec_help", ("exec", "--help")),
    ("transport_capabilities", ("transport-capabilities", "--json")),
    ("transport_self_test", ("transport-self-test", "--json")),
)
MAX_OUTPUT_BYTES = 64 * 1024
COMMAND_TIMEOUT_SECONDS = 10
SIGNER_NAME = "omc-provider-transport-probe"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_sha256_file(path: Path) -> str | None:
    try:
        return _sha256_file(path)
    except OSError:
        return None


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        if process.poll() is None:
            process.kill()
    except OSError:
        if process.poll() is None:
            process.kill()


def _probe_environment() -> dict[str, str]:
    allowed = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _resolve_runtime(transport: Path) -> Path:
    try:
        with transport.open("rb") as handle:
            first_line = handle.readline(256).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return transport
    if not first_line.startswith("#!"):
        return transport
    executable = first_line[2:].strip().split()
    if executable in (["/usr/bin/env", "python3"], ["/usr/bin/env", "python"]):
        return Path(sys.executable).resolve()
    if executable and Path(executable[0]).name.startswith("python"):
        return Path(sys.executable).resolve()
    raise ValueError("transport_runtime_not_supported")


def _run_local_command(
    transport: Path,
    action: str,
    args: Sequence[str],
    *,
    runtime: Path,
    cwd: Path,
) -> dict[str, Any]:
    argv = (
        [str(runtime), str(transport), *args]
        if runtime != transport
        else [str(transport), *args]
    )
    timed_out = False
    limit_exceeded = False
    stdout = bytearray()
    stderr = bytearray()
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=_probe_environment(),
            cwd=cwd,
        )
    except OSError as exc:
        returncode = None
        stderr.extend(str(exc).encode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES])
    else:
        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        assert process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _kill_process_group(process)
                    break
                events = selector.select(remaining)
                if not events:
                    timed_out = True
                    _kill_process_group(process)
                    break
                for key, _ in events:
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    buffer = key.data
                    available = MAX_OUTPUT_BYTES - len(buffer)
                    buffer.extend(chunk[: max(0, available)])
                    if len(chunk) > available:
                        limit_exceeded = True
                        _kill_process_group(process)
                        break
                if limit_exceeded:
                    break
        finally:
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
                key.fileobj.close()
            selector.close()
        if process.poll() is None and (timed_out or limit_exceeded):
            _kill_process_group(process)
        returncode = process.wait()

    return {
        "action": action,
        "args": list(args),
        "returncode": returncode,
        "stdout_sha256": _sha256_bytes(bytes(stdout)),
        "stderr_sha256": _sha256_bytes(bytes(stderr)),
        "timed_out": timed_out,
        "limit_exceeded": limit_exceeded,
        "_stdout": bytes(stdout),
    }


def _public_command(command: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in command.items() if not key.startswith("_")}
    if command["action"] in {"transport_capabilities", "transport_self_test"}:
        result["structured_output"] = command.get("_structured_output")
    return result


def _extract_structured_output(command: dict[str, Any]) -> dict[str, Any] | None:
    try:
        raw = json.loads(command["_stdout"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if command["action"] == "transport_capabilities":
        allowed = ("schema_version", *REQUIRED_CHECKS)
    elif command["action"] == "transport_self_test":
        allowed = ("schema_version", *REQUIRED_SELF_TEST_CHECKS)
    else:
        return None
    return {name: raw[name] for name in allowed if name in raw}


def _parse_capabilities(command: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    checks = {name: False for name in REQUIRED_CHECKS}
    if (
        command["returncode"] != 0
        or command["timed_out"]
        or command["limit_exceeded"]
    ):
        return checks, ["transport_capabilities_unavailable"]

    raw = command.get("_structured_output")
    if not isinstance(raw, dict) or raw.get("schema_version") != CAPABILITY_SCHEMA:
        return checks, ["transport_capabilities_unavailable"]

    reasons: list[str] = []
    for name in REQUIRED_CHECKS:
        value = raw.get(name)
        checks[name] = value is True
        if value is not True:
            reasons.append(f"{name}_missing")
    return checks, reasons


def _parse_self_test(command: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    checks = {name: False for name in REQUIRED_SELF_TEST_CHECKS}
    if (
        command["returncode"] != 0
        or command["timed_out"]
        or command["limit_exceeded"]
    ):
        return checks, ["transport_self_test_unavailable"]
    raw = command.get("_structured_output")
    if not isinstance(raw, dict) or raw.get("schema_version") != SELF_TEST_SCHEMA:
        return checks, ["transport_self_test_unavailable"]
    reasons: list[str] = []
    for name in REQUIRED_SELF_TEST_CHECKS:
        value = raw.get(name)
        checks[name] = value is True
        if value is not True:
            reasons.append(f"self_test_{name}_missing")
    return checks, reasons


def _derive_assessment(
    commands: list[dict[str, Any]],
    *,
    transport_sha256: str,
    transport_sha256_after: str | None,
) -> tuple[dict[str, bool], dict[str, bool], list[str]]:
    checks, reasons = _parse_capabilities(commands[2])
    self_test_checks, self_test_reasons = _parse_self_test(commands[3])
    reasons.extend(self_test_reasons)
    if any(
        command["returncode"] != 0
        or command["timed_out"]
        or command["limit_exceeded"]
        for command in commands[:2]
    ):
        reasons.insert(0, "transport_surface_unavailable")
    if transport_sha256_after != transport_sha256:
        reasons.insert(0, "transport_binary_changed")
    return checks, self_test_checks, list(dict.fromkeys(reasons))


def _evidence_hash(evidence: dict[str, Any]) -> str:
    payload = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    return _sha256_bytes(_canonical_bytes(payload))


def _public_key_text(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _signature_payload(evidence: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    signoff = dict(payload.get("signoff") or {})
    signoff["signature"] = ""
    payload["signoff"] = signoff
    return _canonical_bytes(payload)


def _seal_evidence(
    evidence: dict[str, Any],
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    evidence["signoff"] = {
        "signer": SIGNER_NAME,
        "signer_public_key": _public_key_text(private_key),
        "signature": "",
    }
    evidence["signoff"]["signature"] = base64.b64encode(
        private_key.sign(_signature_payload(evidence))
    ).decode("ascii")
    evidence["evidence_sha256"] = _evidence_hash(evidence)
    return evidence


def _verify_evidence_signature(
    evidence: dict[str, Any],
    *,
    trusted_signer_public_keys: set[str],
) -> None:
    signoff = evidence.get("signoff")
    if not isinstance(signoff, dict) or set(signoff) != {
        "signer",
        "signer_public_key",
        "signature",
    }:
        raise ValueError("transport_probe_signature_invalid")
    public_key = signoff.get("signer_public_key")
    if (
        signoff.get("signer") != SIGNER_NAME
        or not isinstance(public_key, str)
        or public_key not in trusted_signer_public_keys
    ):
        raise ValueError("transport_probe_signer_untrusted")
    try:
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        ).verify(
            base64.b64decode(signoff.get("signature"), validate=True),
            _signature_payload(evidence),
        )
    except (InvalidSignature, binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("transport_probe_signature_invalid") from exc


def _copy_transport_snapshot(source: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as source_handle, destination.open("xb") as target_handle:
        for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
            digest.update(chunk)
            target_handle.write(chunk)
    destination.chmod(0o500)
    return digest.hexdigest()


def _preflight_hold_evidence(
    path: Path,
    *,
    expected_transport_sha256: str,
    transport_sha256: str | None,
    reason_code: str,
    signer_private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA,
        "status": HOLD,
        "claim_eligible": False,
        "external_call_performed": False,
        "transport_name": path.name,
        "expected_transport_sha256": expected_transport_sha256,
        "transport_sha256": transport_sha256,
        "transport_sha256_after": transport_sha256,
        "transport_snapshot_sha256": transport_sha256,
        "transport_snapshot_sha256_after": transport_sha256,
        "runtime_path": None,
        "runtime_sha256": None,
        "runtime_sha256_after": None,
        "checks": {name: False for name in REQUIRED_CHECKS},
        "self_test_checks": {name: False for name in REQUIRED_SELF_TEST_CHECKS},
        "reason_codes": [reason_code],
        "commands": [],
    }
    _seal_evidence(evidence, signer_private_key)
    return validate_probe_evidence(
        evidence,
        trusted_signer_public_keys={_public_key_text(signer_private_key)},
    )


def probe_transport(
    transport: str | os.PathLike[str],
    *,
    expected_transport_sha256: str,
    signer_private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    path = Path(transport).expanduser().resolve()
    if not os.access(path, os.X_OK):
        return _preflight_hold_evidence(
            path,
            expected_transport_sha256=expected_transport_sha256,
            transport_sha256=None,
            reason_code="transport_binary_unavailable",
            signer_private_key=signer_private_key,
        )

    with tempfile.TemporaryDirectory(prefix="omc-transport-probe-") as temporary:
        cwd = Path(temporary)
        snapshot = cwd / "transport.snapshot"
        try:
            transport_sha256 = _copy_transport_snapshot(path, snapshot)
        except OSError:
            return _preflight_hold_evidence(
                path,
                expected_transport_sha256=expected_transport_sha256,
                transport_sha256=None,
                reason_code="transport_binary_unavailable",
                signer_private_key=signer_private_key,
            )
        if transport_sha256 != expected_transport_sha256:
            return _preflight_hold_evidence(
                path,
                expected_transport_sha256=expected_transport_sha256,
                transport_sha256=transport_sha256,
                reason_code="transport_hash_not_approved",
                signer_private_key=signer_private_key,
            )
        try:
            runtime = _resolve_runtime(snapshot)
        except ValueError:
            return _preflight_hold_evidence(
                path,
                expected_transport_sha256=expected_transport_sha256,
                transport_sha256=transport_sha256,
                reason_code="transport_runtime_not_supported",
                signer_private_key=signer_private_key,
            )
        runtime_sha256 = _safe_sha256_file(runtime)
        if runtime_sha256 is None:
            return _preflight_hold_evidence(
                path,
                expected_transport_sha256=expected_transport_sha256,
                transport_sha256=transport_sha256,
                reason_code="transport_runtime_unavailable",
                signer_private_key=signer_private_key,
            )
        commands = [
            _run_local_command(snapshot, action, args, runtime=runtime, cwd=cwd)
            for action, args in CANONICAL_COMMANDS
        ]
        transport_snapshot_sha256_after = _safe_sha256_file(snapshot)
        runtime_sha256_after = _safe_sha256_file(runtime)
    for command in commands:
        command["_structured_output"] = _extract_structured_output(command)
    transport_sha256_after = _safe_sha256_file(path)
    checks, self_test_checks, reasons = _derive_assessment(
        commands,
        transport_sha256=transport_sha256,
        transport_sha256_after=transport_sha256_after,
    )
    if runtime_sha256_after != runtime_sha256:
        reasons.insert(0, "transport_runtime_changed")
    if transport_snapshot_sha256_after != transport_sha256:
        reasons.insert(0, "transport_snapshot_changed")

    evidence: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA,
        "status": (
            SUPPORTED
            if not reasons and all(checks.values()) and all(self_test_checks.values())
            else HOLD
        ),
        "claim_eligible": False,
        "external_call_performed": False,
        "transport_name": path.name,
        "expected_transport_sha256": expected_transport_sha256,
        "transport_sha256": transport_sha256,
        "transport_sha256_after": transport_sha256_after,
        "transport_snapshot_sha256": transport_sha256,
        "transport_snapshot_sha256_after": transport_snapshot_sha256_after,
        "runtime_path": str(runtime),
        "runtime_sha256": runtime_sha256,
        "runtime_sha256_after": runtime_sha256_after,
        "checks": checks,
        "self_test_checks": self_test_checks,
        "reason_codes": reasons,
        "commands": [_public_command(command) for command in commands],
    }
    _seal_evidence(evidence, signer_private_key)
    return validate_probe_evidence(
        evidence,
        trusted_signer_public_keys={_public_key_text(signer_private_key)},
    )


def validate_probe_evidence(
    evidence: dict[str, Any],
    *,
    trusted_signer_public_keys: set[str],
) -> dict[str, Any]:
    if evidence.get("schema_version") != EVIDENCE_SCHEMA:
        raise ValueError("transport_probe_schema_invalid")
    if evidence.get("evidence_sha256") != _evidence_hash(evidence):
        raise ValueError("transport_probe_evidence_hash_mismatch")
    _verify_evidence_signature(
        evidence,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )
    if evidence.get("status") not in {SUPPORTED, HOLD}:
        raise ValueError("transport_probe_status_invalid")
    if evidence.get("claim_eligible") is not False:
        raise ValueError("transport_probe_claim_must_remain_false")
    if evidence.get("external_call_performed") is not False:
        raise ValueError("transport_probe_external_call_forbidden")
    expected_transport_sha256 = evidence.get("expected_transport_sha256")
    if not isinstance(expected_transport_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_transport_sha256
    ):
        raise ValueError("transport_probe_expected_hash_invalid")
    checks = evidence.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED_CHECKS):
        raise ValueError("transport_probe_checks_invalid")
    if any(type(checks[name]) is not bool for name in REQUIRED_CHECKS):
        raise ValueError("transport_probe_checks_invalid")
    self_test_checks = evidence.get("self_test_checks")
    if not isinstance(self_test_checks, dict) or set(self_test_checks) != set(
        REQUIRED_SELF_TEST_CHECKS
    ):
        raise ValueError("transport_probe_self_test_checks_invalid")
    if any(type(self_test_checks[name]) is not bool for name in REQUIRED_SELF_TEST_CHECKS):
        raise ValueError("transport_probe_self_test_checks_invalid")

    commands = evidence.get("commands")
    if not isinstance(commands, list):
        raise ValueError("transport_probe_commands_invalid")

    reasons = evidence.get("reason_codes")
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        raise ValueError("transport_probe_reason_codes_invalid")

    if not commands:
        transport_sha256 = evidence.get("transport_sha256")
        preflight_valid = False
        if reasons == ["transport_binary_unavailable"]:
            preflight_valid = (
                transport_sha256 is None
                and evidence.get("transport_sha256_after") is None
            )
        elif reasons == ["transport_hash_not_approved"]:
            preflight_valid = (
                isinstance(transport_sha256, str)
                and bool(re.fullmatch(r"[0-9a-f]{64}", transport_sha256))
                and transport_sha256 != expected_transport_sha256
                and evidence.get("transport_sha256_after") == transport_sha256
            )
        elif tuple(reasons) in {
            ("transport_runtime_not_supported",),
            ("transport_runtime_unavailable",),
        }:
            preflight_valid = (
                isinstance(transport_sha256, str)
                and transport_sha256 == expected_transport_sha256
                and evidence.get("transport_sha256_after") == transport_sha256
            )
        if (
            evidence.get("status") != HOLD
            or not preflight_valid
            or evidence.get("runtime_path") is not None
            or evidence.get("runtime_sha256") is not None
            or evidence.get("runtime_sha256_after") is not None
            or any(checks.values())
            or any(self_test_checks.values())
        ):
            raise ValueError("transport_probe_preflight_hold_invalid")
        if evidence.get("transport_snapshot_sha256") != transport_sha256 or evidence.get(
            "transport_snapshot_sha256_after"
        ) != transport_sha256:
            raise ValueError("transport_probe_preflight_hold_invalid")
        return evidence

    if len(commands) != len(CANONICAL_COMMANDS):
        raise ValueError("transport_probe_commands_invalid")
    if any(not isinstance(item, dict) for item in commands):
        raise ValueError("transport_probe_commands_invalid")
    assessment_commands: list[dict[str, Any]] = []
    for item, (expected_action, expected_args) in zip(commands, CANONICAL_COMMANDS):
        assessment_item = dict(item)
        if item.get("action") != expected_action or item.get("args") != list(expected_args):
            raise ValueError("transport_probe_commands_invalid")
        if item.get("returncode") is not None and type(item["returncode"]) is not int:
            raise ValueError("transport_probe_commands_invalid")
        if type(item.get("timed_out")) is not bool or type(item.get("limit_exceeded")) is not bool:
            raise ValueError("transport_probe_commands_invalid")
        for hash_name in ("stdout_sha256", "stderr_sha256"):
            value = item.get(hash_name)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("transport_probe_commands_invalid")
        if expected_action in {"transport_capabilities", "transport_self_test"}:
            structured = item.get("structured_output")
            if structured is not None and not isinstance(structured, dict):
                raise ValueError("transport_probe_commands_invalid")
            allowed = {
                "schema_version",
                *(
                    REQUIRED_CHECKS
                    if expected_action == "transport_capabilities"
                    else REQUIRED_SELF_TEST_CHECKS
                ),
            }
            if isinstance(structured, dict) and (
                not set(structured).issubset(allowed)
                or any(
                    type(value) is not bool
                    for key, value in structured.items()
                    if key != "schema_version"
                )
            ):
                raise ValueError("transport_probe_commands_invalid")
            assessment_item["_structured_output"] = structured
        elif "structured_output" in item or "stdout_base64" in item:
            raise ValueError("transport_probe_commands_invalid")
        assessment_commands.append(assessment_item)

    transport_sha256 = evidence.get("transport_sha256")
    if not isinstance(transport_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", transport_sha256
    ):
        raise ValueError("transport_probe_transport_hash_invalid")
    transport_sha256_after = evidence.get("transport_sha256_after")
    if transport_sha256_after is not None and (
        not isinstance(transport_sha256_after, str)
        or not re.fullmatch(r"[0-9a-f]{64}", transport_sha256_after)
    ):
        raise ValueError("transport_probe_transport_hash_invalid")
    if transport_sha256 != expected_transport_sha256:
        raise ValueError("transport_probe_transport_hash_unapproved")
    transport_snapshot_sha256 = evidence.get("transport_snapshot_sha256")
    transport_snapshot_sha256_after = evidence.get("transport_snapshot_sha256_after")
    if transport_snapshot_sha256 != transport_sha256 or (
        not isinstance(transport_snapshot_sha256_after, str)
        or not re.fullmatch(r"[0-9a-f]{64}", transport_snapshot_sha256_after)
    ):
        raise ValueError("transport_probe_snapshot_invalid")
    runtime_path = evidence.get("runtime_path")
    if not isinstance(runtime_path, str) or not Path(runtime_path).is_absolute():
        raise ValueError("transport_probe_runtime_invalid")
    runtime_sha256 = evidence.get("runtime_sha256")
    runtime_sha256_after = evidence.get("runtime_sha256_after")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (runtime_sha256, runtime_sha256_after)
    ):
        raise ValueError("transport_probe_runtime_invalid")
    expected_checks, expected_self_tests, expected_reasons = _derive_assessment(
        assessment_commands,
        transport_sha256=transport_sha256,
        transport_sha256_after=transport_sha256_after,
    )
    if runtime_sha256_after != runtime_sha256:
        expected_reasons.insert(0, "transport_runtime_changed")
    if transport_snapshot_sha256_after != transport_snapshot_sha256:
        expected_reasons.insert(0, "transport_snapshot_changed")
    if checks != expected_checks:
        raise ValueError("transport_probe_checks_inconsistent")
    if self_test_checks != expected_self_tests:
        raise ValueError("transport_probe_self_test_checks_inconsistent")
    if reasons != expected_reasons:
        raise ValueError("transport_probe_reason_codes_inconsistent")

    supported = (
        all(checks.values()) and all(self_test_checks.values()) and not reasons
    )
    command_success = all(
        item["returncode"] == 0 and not item["timed_out"] and not item["limit_exceeded"]
        for item in commands
    )
    if supported and not command_success:
        raise ValueError("transport_probe_status_inconsistent")
    expected_status = SUPPORTED if supported else HOLD
    if evidence["status"] != expected_status:
        raise ValueError("transport_probe_status_inconsistent")
    return evidence


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _load_private_key(
    path: Path,
    *,
    repo_root: Path,
    artifact_root: Path,
) -> Ed25519PrivateKey:
    key_path = path.expanduser().resolve()
    custody_roots = (
        repo_root.expanduser().resolve(),
        artifact_root.expanduser().resolve(),
    )
    if any(_is_relative_to(key_path, root) for root in custody_roots):
        raise ValueError("transport_probe_private_key_location_invalid")
    try:
        raw = base64.b64decode(
            key_path.read_text(encoding="utf-8").strip(),
            validate=True,
        )
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (OSError, binascii.Error, ValueError) as exc:
        raise ValueError("transport_probe_private_key_invalid") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe provider transport capabilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--transport", type=Path, required=True)
    probe.add_argument("--expected-transport-sha256", required=True)
    probe.add_argument("--signer-private-key-file", type=Path, required=True)
    probe.add_argument("--repo-root", type=Path, required=True)
    probe.add_argument("--artifact-root", type=Path, required=True)
    probe.add_argument("--expected-signer-public-key", required=True)
    probe.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--evidence", type=Path, required=True)
    validate.add_argument("--trusted-signer-public-key", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "validate":
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        validate_probe_evidence(
            evidence,
            trusted_signer_public_keys=set(args.trusted_signer_public_key),
        )
        print(json.dumps({"status": "VALID", "evidence_sha256": evidence["evidence_sha256"]}))
        return 0

    signer_private_key = _load_private_key(
        args.signer_private_key_file,
        repo_root=args.repo_root,
        artifact_root=args.artifact_root,
    )
    if _public_key_text(signer_private_key) != args.expected_signer_public_key:
        raise ValueError("transport_probe_signer_key_mismatch")
    evidence = probe_transport(
        args.transport,
        expected_transport_sha256=args.expected_transport_sha256,
        signer_private_key=signer_private_key,
    )
    _write_json_atomic(args.output, evidence)
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "evidence_sha256": evidence["evidence_sha256"],
                "reason_codes": evidence["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0 if evidence["status"] == SUPPORTED else 2


if __name__ == "__main__":
    raise SystemExit(main())
