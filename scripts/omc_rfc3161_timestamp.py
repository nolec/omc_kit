#!/usr/bin/env python3
"""Verify Sigstore Public Good RFC 3161 registration evidence."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SIGSTORE_TSA_SERVICE_ID = "sigstore-public-good-tsa"
SIGSTORE_TSA_OPERATOR = "sigstore.dev"
SIGSTORE_TSA_ENDPOINT = "https://timestamp.sigstore.dev/api/v1/timestamp"
SIGSTORE_TSA_POLICY_OID = "1.3.6.1.4.1.57264.2"

TRUST_SNAPSHOT_FIELDS = {
    "schema_version",
    "source",
    "service_id",
    "operator",
    "endpoint",
    "valid_for",
    "certificate_chain_pem",
    "tuf_root_sha256",
}
TRUST_IDENTITY_FIELDS = {
    "service_id",
    "operator",
    "certificate_chain_sha256",
    "trusted_root_sha256",
    "tuf_root_sha256",
    "valid_for",
}
EVIDENCE_FIELDS = {
    "schema_version",
    "type",
    "authority",
    "claim_sha256",
    "query_der_b64",
    "query_sha256",
    "response_der_b64",
    "response_sha256",
    "nonce",
    "gen_time",
    "policy_oid",
    "serial_number",
}
CLAIM_FIELDS = {
    "batch_id",
    "preregistration_sha256",
    "registry_commit",
    "registry_path",
}
LOWER_HEX = frozenset("0123456789abcdef")


OpenSSLRunner = Callable[..., str]


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _is_lower_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and set(value) <= LOWER_HEX
    )


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def registration_claim(
    *,
    batch_id: str,
    preregistration_sha256: str,
    registry_commit: str,
    registry_path: str,
) -> dict[str, str]:
    claim = {
        "batch_id": batch_id,
        "preregistration_sha256": preregistration_sha256,
        "registry_commit": registry_commit,
        "registry_path": registry_path,
    }
    if (
        not isinstance(batch_id, str)
        or not batch_id.strip()
        or not _is_lower_hex(preregistration_sha256, 64)
        or not _is_lower_hex(registry_commit, 40)
        or not isinstance(registry_path, str)
        or not registry_path.strip()
        or registry_path.startswith("/")
        or ".." in Path(registry_path).parts
    ):
        raise ValueError("registration claim is invalid")
    return claim


def canonical_claim_sha256(claim: dict[str, Any]) -> str:
    if not isinstance(claim, dict) or set(claim) != CLAIM_FIELDS:
        raise ValueError("registration claim fields are invalid")
    normalized = registration_claim(**claim)
    return sha256_hex(_canonical_bytes(normalized))


def trusted_root_sha256(trusted_root: dict[str, Any]) -> str:
    if not isinstance(trusted_root, dict):
        raise ValueError("Sigstore trusted root is invalid")
    return sha256_hex(_canonical_bytes(trusted_root))


def trust_identity(
    trusted_root: dict[str, Any],
    *,
    expected_trusted_root_sha256: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(trusted_root, dict)
        or set(trusted_root) != TRUST_SNAPSHOT_FIELDS
        or trusted_root.get("schema_version") != 1
        or trusted_root.get("source") != "sigstore_tuf"
    ):
        raise ValueError("trusted root must come from Sigstore TUF")
    if (
        trusted_root.get("service_id") != SIGSTORE_TSA_SERVICE_ID
        or trusted_root.get("operator") != SIGSTORE_TSA_OPERATOR
        or trusted_root.get("endpoint") != SIGSTORE_TSA_ENDPOINT
    ):
        raise ValueError("only the Sigstore Public Good TSA is allowed")
    valid_for = trusted_root.get("valid_for")
    chain = trusted_root.get("certificate_chain_pem")
    if (
        not isinstance(valid_for, dict)
        or set(valid_for) != {"start", "end"}
        or not isinstance(chain, list)
        or len(chain) < 2
        or any(
            not isinstance(certificate, str)
            or "-----BEGIN CERTIFICATE-----" not in certificate
            or "-----END CERTIFICATE-----" not in certificate
            for certificate in chain
        )
        or not _is_lower_hex(trusted_root.get("tuf_root_sha256"), 64)
    ):
        raise ValueError("Sigstore trusted root is invalid")
    start = _parse_timestamp(valid_for["start"])
    end = _parse_timestamp(valid_for["end"])
    if start >= end:
        raise ValueError("Sigstore trusted root validity is invalid")
    snapshot_sha256 = trusted_root_sha256(trusted_root)
    if (
        not _is_lower_hex(expected_trusted_root_sha256, 64)
        or not hmac.compare_digest(snapshot_sha256, expected_trusted_root_sha256)
    ):
        raise ValueError("Sigstore trusted root does not match approved digest")
    identity = {
        "service_id": SIGSTORE_TSA_SERVICE_ID,
        "operator": SIGSTORE_TSA_OPERATOR,
        "certificate_chain_sha256": sha256_hex(
            "".join(chain).encode("ascii")
        ),
        "trusted_root_sha256": snapshot_sha256,
        "tuf_root_sha256": trusted_root["tuf_root_sha256"],
        "valid_for": dict(valid_for),
    }
    validate_trust_identity(identity)
    return identity


def validate_trust_identity(identity: Any) -> None:
    if (
        not isinstance(identity, dict)
        or set(identity) != TRUST_IDENTITY_FIELDS
        or identity.get("service_id") != SIGSTORE_TSA_SERVICE_ID
        or identity.get("operator") != SIGSTORE_TSA_OPERATOR
        or not _is_lower_hex(identity.get("certificate_chain_sha256"), 64)
        or not _is_lower_hex(identity.get("trusted_root_sha256"), 64)
        or not _is_lower_hex(identity.get("tuf_root_sha256"), 64)
    ):
        raise ValueError("Sigstore trust identity is invalid")
    valid_for = identity.get("valid_for")
    if not isinstance(valid_for, dict) or set(valid_for) != {"start", "end"}:
        raise ValueError("Sigstore trust identity is invalid")
    if _parse_timestamp(valid_for["start"]) >= _parse_timestamp(valid_for["end"]):
        raise ValueError("Sigstore trust identity is invalid")


def _default_openssl_runner(
    command: list[str], *, input_bytes: bytes | None = None
) -> str:
    try:
        result = subprocess.run(
            command,
            input=input_bytes,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", b"") or b""
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"RFC 3161 OpenSSL verification failed: {detail}") from error
    return result.stdout.decode("utf-8", errors="strict")


def _decode_der(value: Any, *, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{label} is invalid") from error
    if not decoded:
        raise ValueError(f"{label} is invalid")
    return decoded


def _normalized_hex(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("0") or "0"
    if not normalized or set(normalized) - set("0123456789abcdef"):
        raise ValueError("RFC 3161 hexadecimal value is invalid")
    return normalized


def _field(output: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+?)\s*$", output, re.MULTILINE)
    if match is None:
        raise ValueError(f"RFC 3161 {label.lower()} is missing")
    return match.group(1)


def _message_imprint(output: str) -> str:
    match = re.search(
        r"^Message data:\s*$\n(?P<body>(?:\s+[0-9a-fA-F]{4}\s*-.*\n?)+)",
        output,
        re.MULTILINE,
    )
    if match is None:
        raise ValueError("RFC 3161 message imprint is missing")
    octets: list[str] = []
    for line in match.group("body").splitlines():
        payload = line.split("-", 1)[1]
        hex_part = re.split(r"\s{3,}", payload, maxsplit=1)[0]
        octets.extend(re.findall(r"\b[0-9a-fA-F]{2}\b", hex_part))
    digest = "".join(octets).lower()
    if not digest:
        raise ValueError("RFC 3161 message imprint is missing")
    return digest


def _openssl_gen_time(value: str) -> datetime:
    try:
        return datetime.strptime(
            " ".join(value.split()), "%b %d %H:%M:%S %Y GMT"
        ).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ValueError("RFC 3161 generation time is invalid") from error


def build_registration_evidence(
    *,
    query_der: bytes,
    response_der: bytes,
    claim: dict[str, Any],
    trusted_root: dict[str, Any],
    expected_trusted_root_sha256: str,
    observation_starts_at: str,
    openssl_runner: OpenSSLRunner = _default_openssl_runner,
) -> dict[str, Any]:
    """Normalize raw RFC 3161 DER into the persisted evidence contract."""
    if not query_der or not response_der:
        raise ValueError("RFC 3161 query and response are required")
    with tempfile.TemporaryDirectory(prefix="omc-rfc3161-metadata-") as directory:
        root = Path(directory)
        query_path = root / "request.tsq"
        response_path = root / "response.tsr"
        query_path.write_bytes(query_der)
        response_path.write_bytes(response_der)
        query_text = openssl_runner([
            "openssl", "ts", "-query", "-in", str(query_path), "-text"
        ])
        response_text = openssl_runner([
            "openssl", "ts", "-reply", "-in", str(response_path), "-text"
        ])
    query_nonce = _normalized_hex(_field(query_text, "Nonce"))
    response_nonce = _normalized_hex(_field(response_text, "Nonce"))
    if query_nonce != response_nonce:
        raise ValueError("RFC 3161 nonce mismatch")
    generation_time = _openssl_gen_time(_field(response_text, "Time stamp"))
    evidence = {
        "schema_version": 1,
        "type": "rfc3161",
        "authority": trust_identity(
            trusted_root,
            expected_trusted_root_sha256=expected_trusted_root_sha256,
        ),
        "claim_sha256": canonical_claim_sha256(claim),
        "query_der_b64": base64.b64encode(query_der).decode("ascii"),
        "query_sha256": sha256_hex(query_der),
        "response_der_b64": base64.b64encode(response_der).decode("ascii"),
        "response_sha256": sha256_hex(response_der),
        "nonce": f"0x{query_nonce}",
        "gen_time": generation_time.isoformat(),
        "policy_oid": _field(response_text, "Policy OID"),
        "serial_number": f"0x{_normalized_hex(_field(response_text, 'Serial number'))}",
    }
    return verify_registration_evidence(
        evidence,
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=expected_trusted_root_sha256,
        observation_starts_at=observation_starts_at,
        openssl_runner=openssl_runner,
    )


def verify_registration_evidence(
    evidence: dict[str, Any],
    *,
    claim: dict[str, Any],
    trusted_root: dict[str, Any],
    expected_trusted_root_sha256: str,
    observation_starts_at: str,
    now: datetime | None = None,
    openssl_runner: OpenSSLRunner = _default_openssl_runner,
) -> dict[str, Any]:
    """Verify an RFC 3161 response against the frozen Sigstore trust snapshot."""
    if (
        not isinstance(evidence, dict)
        or set(evidence) != EVIDENCE_FIELDS
        or evidence.get("schema_version") != 1
        or evidence.get("type") != "rfc3161"
    ):
        raise ValueError("RFC 3161 registration evidence fields are invalid")
    expected_authority = trust_identity(
        trusted_root,
        expected_trusted_root_sha256=expected_trusted_root_sha256,
    )
    if evidence.get("authority") != expected_authority:
        raise ValueError("RFC 3161 authority identity mismatch")
    expected_claim_sha256 = canonical_claim_sha256(claim)
    if evidence.get("claim_sha256") != expected_claim_sha256:
        raise ValueError("RFC 3161 claim digest mismatch")

    query = _decode_der(evidence.get("query_der_b64"), label="RFC 3161 query")
    response = _decode_der(
        evidence.get("response_der_b64"), label="RFC 3161 response"
    )
    if (
        evidence.get("query_sha256") != sha256_hex(query)
        or evidence.get("response_sha256") != sha256_hex(response)
    ):
        raise ValueError("RFC 3161 DER digest mismatch")

    generation_time = _parse_timestamp(evidence.get("gen_time"))
    observation_start = _parse_timestamp(observation_starts_at)
    if generation_time >= observation_start:
        raise ValueError("RFC 3161 timestamp must be before observation")
    validity_start = _parse_timestamp(expected_authority["valid_for"]["start"])
    validity_end = _parse_timestamp(expected_authority["valid_for"]["end"])
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        raise ValueError("verification time must include timezone")
    if not validity_start <= generation_time <= validity_end:
        raise ValueError("RFC 3161 timestamp is outside TSA validity")

    chain = trusted_root["certificate_chain_pem"]
    with tempfile.TemporaryDirectory(prefix="omc-rfc3161-") as directory:
        root = Path(directory)
        query_path = root / "request.tsq"
        response_path = root / "response.tsr"
        ca_path = root / "root.pem"
        untrusted_path = root / "untrusted.pem"
        query_path.write_bytes(query)
        response_path.write_bytes(response)
        ca_path.write_text(chain[-1], encoding="ascii")
        untrusted_path.write_text("".join(chain[:-1]), encoding="ascii")

        query_text = openssl_runner([
            "openssl", "ts", "-query", "-in", str(query_path), "-text"
        ])
        response_text = openssl_runner([
            "openssl", "ts", "-reply", "-in", str(response_path), "-text"
        ])
        verification = openssl_runner([
            "openssl", "ts", "-verify",
            "-in", str(response_path),
            "-queryfile", str(query_path),
            "-CAfile", str(ca_path),
            "-untrusted", str(untrusted_path),
        ])

    if "Verification: OK" not in verification:
        raise ValueError("RFC 3161 signature verification failed")
    if (
        _field(query_text, "Hash Algorithm").lower() != "sha256"
        or _message_imprint(query_text) != expected_claim_sha256
    ):
        raise ValueError("RFC 3161 message imprint mismatch")
    query_nonce = _normalized_hex(_field(query_text, "Nonce"))
    response_nonce = _normalized_hex(_field(response_text, "Nonce"))
    evidence_nonce = _normalized_hex(str(evidence.get("nonce")))
    if query_nonce != response_nonce or response_nonce != evidence_nonce:
        raise ValueError("RFC 3161 nonce mismatch")
    policy_oid = _field(response_text, "Policy OID")
    serial_number = _field(response_text, "Serial number")
    parsed_generation_time = _openssl_gen_time(_field(response_text, "Time stamp"))
    if (
        policy_oid != SIGSTORE_TSA_POLICY_OID
        or evidence.get("policy_oid") != policy_oid
        or _normalized_hex(serial_number)
        != _normalized_hex(str(evidence.get("serial_number")))
        or parsed_generation_time != generation_time.astimezone(timezone.utc)
    ):
        raise ValueError("RFC 3161 response metadata mismatch")
    return dict(evidence)
