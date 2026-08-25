from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

import omc_rfc3161_timestamp as timestamp


def _trusted_root() -> dict:
    return {
        "schema_version": 1,
        "source": "sigstore_tuf",
        "service_id": timestamp.SIGSTORE_TSA_SERVICE_ID,
        "operator": timestamp.SIGSTORE_TSA_OPERATOR,
        "endpoint": timestamp.SIGSTORE_TSA_ENDPOINT,
        "valid_for": {
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2027-01-01T00:00:00+00:00",
        },
        "certificate_chain_pem": [
            "-----BEGIN CERTIFICATE-----\nROOT\n-----END CERTIFICATE-----\n",
            "-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----\n",
        ],
        "tuf_root_sha256": "1" * 64,
    }


def _evidence(claim: dict) -> dict:
    query = b"query-der"
    response = b"response-der"
    trusted_root = _trusted_root()
    return {
        "schema_version": 1,
        "type": "rfc3161",
        "authority": timestamp.trust_identity(
            trusted_root,
            expected_trusted_root_sha256=timestamp.trusted_root_sha256(
                trusted_root
            ),
        ),
        "claim_sha256": timestamp.canonical_claim_sha256(claim),
        "query_der_b64": base64.b64encode(query).decode("ascii"),
        "query_sha256": timestamp.sha256_hex(query),
        "response_der_b64": base64.b64encode(response).decode("ascii"),
        "response_sha256": timestamp.sha256_hex(response),
        "nonce": "0x01ab",
        "gen_time": "2026-08-12T05:00:00+00:00",
        "policy_oid": "1.3.6.1.4.1.57264.2",
        "serial_number": "0x10",
    }


def _query_text(claim: dict, *, digest: str | None = None) -> str:
    value = digest or timestamp.canonical_claim_sha256(claim)
    first = " ".join(value[index:index + 2] for index in range(0, 32, 2))
    second = " ".join(value[index:index + 2] for index in range(32, 64, 2))
    return (
        "Hash Algorithm: sha256\n"
        "Message data:\n"
        f"    0000 - {first}\n"
        f"    0010 - {second}\n"
        "Policy OID: unspecified\n"
        "Nonce: 0x01AB\n"
    )


def test_claim_digest_is_canonical_and_contains_no_raw_registry_payload():
    first = {
        "batch_id": "batch-b-01",
        "registry_commit": "a" * 40,
        "registry_path": "docs/registry.json",
        "preregistration_sha256": "b" * 64,
    }
    second = dict(reversed(list(first.items())))

    assert timestamp.canonical_claim_sha256(first) == (
        timestamp.canonical_claim_sha256(second)
    )
    assert timestamp.registration_claim(**first) == first


def test_trust_identity_rejects_non_sigstore_or_custom_snapshot():
    custom = _trusted_root()
    custom["source"] = "custom_file"
    with pytest.raises(ValueError, match="Sigstore TUF"):
        timestamp.trust_identity(custom)

    wrong_operator = _trusted_root()
    wrong_operator["operator"] = "local.example"
    with pytest.raises(ValueError, match="Sigstore Public Good"):
        timestamp.trust_identity(wrong_operator)


def test_trust_identity_requires_independently_approved_snapshot_digest():
    trusted_root = _trusted_root()

    with pytest.raises(ValueError, match="approved digest"):
        timestamp.trust_identity(trusted_root)

    with pytest.raises(ValueError, match="approved digest"):
        timestamp.trust_identity(
            trusted_root,
            expected_trusted_root_sha256="0" * 64,
        )

    identity = timestamp.trust_identity(
        trusted_root,
        expected_trusted_root_sha256=timestamp.trusted_root_sha256(trusted_root),
    )

    assert identity["trusted_root_sha256"] == timestamp.trusted_root_sha256(
        trusted_root
    )


def test_validate_trust_identity_rejects_invalid_trusted_root_digest():
    trusted_root = _trusted_root()
    identity = timestamp.trust_identity(
        trusted_root,
        expected_trusted_root_sha256=timestamp.trusted_root_sha256(trusted_root),
    )
    identity["trusted_root_sha256"] = "not-a-sha256"

    with pytest.raises(ValueError, match="trust identity is invalid"):
        timestamp.validate_trust_identity(identity)


def test_verify_evidence_uses_last_certificate_as_trust_anchor():
    claim = timestamp.registration_claim(
        batch_id="batch-b-01",
        preregistration_sha256="b" * 64,
        registry_commit="a" * 40,
        registry_path="docs/registry.json",
    )
    trusted_root = _trusted_root()
    approved_digest = timestamp.trusted_root_sha256(trusted_root)
    evidence = _evidence(claim)

    def openssl_runner(command: list[str], *, input_bytes: bytes | None = None):
        if "-query" in command and "-text" in command:
            return _query_text(claim)
        if "-reply" in command and "-text" in command:
            return (
                "Status: Granted.\n"
                "Policy OID: 1.3.6.1.4.1.57264.2\n"
                "Serial number: 0x10\n"
                "Time stamp: Aug 12 05:00:00 2026 GMT\n"
                "Nonce: 0x01AB\n"
            )
        if "-verify" in command:
            ca_path = command[command.index("-CAfile") + 1]
            untrusted_path = command[command.index("-untrusted") + 1]
            assert open(ca_path, encoding="ascii").read() == trusted_root[
                "certificate_chain_pem"
            ][-1]
            assert open(untrusted_path, encoding="ascii").read() == "".join(
                trusted_root["certificate_chain_pem"][:-1]
            )
            return "Verification: OK\n"
        raise AssertionError(command)

    timestamp.verify_registration_evidence(
        evidence,
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=approved_digest,
        observation_starts_at="2026-08-12T06:00:00+00:00",
        openssl_runner=openssl_runner,
    )


def test_verify_evidence_accepts_inclusive_trust_validity_end():
    claim = timestamp.registration_claim(
        batch_id="batch-b-01",
        preregistration_sha256="b" * 64,
        registry_commit="a" * 40,
        registry_path="docs/registry.json",
    )
    trusted_root = _trusted_root()
    evidence = _evidence(claim)
    evidence["gen_time"] = trusted_root["valid_for"]["end"]

    def openssl_runner(command: list[str], *, input_bytes: bytes | None = None):
        if "-query" in command and "-text" in command:
            return _query_text(claim)
        if "-reply" in command and "-text" in command:
            return (
                "Status: Granted.\n"
                "Policy OID: 1.3.6.1.4.1.57264.2\n"
                "Serial number: 0x10\n"
                "Time stamp: Jan 1 00:00:00 2027 GMT\n"
                "Nonce: 0x01AB\n"
            )
        return "Verification: OK\n"

    timestamp.verify_registration_evidence(
        evidence,
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=timestamp.trusted_root_sha256(trusted_root),
        observation_starts_at="2027-01-01T00:00:01+00:00",
        openssl_runner=openssl_runner,
    )


def test_verify_evidence_binds_claim_nonce_time_and_sigstore_chain():
    claim = timestamp.registration_claim(
        batch_id="batch-b-01",
        preregistration_sha256="b" * 64,
        registry_commit="a" * 40,
        registry_path="docs/registry.json",
    )
    evidence = _evidence(claim)
    calls: list[list[str]] = []

    def openssl_runner(command: list[str], *, input_bytes: bytes | None = None):
        calls.append(command)
        if "-query" in command and "-text" in command:
            return _query_text(claim)
        if "-reply" in command and "-text" in command:
            return (
                "Status: Granted.\n"
                "Policy OID: 1.3.6.1.4.1.57264.2\n"
                "Serial number: 0x10\n"
                "Time stamp: Aug 12 05:00:00 2026 GMT\n"
                "Nonce: 0x01AB\n"
            )
        if "-verify" in command:
            return "Verification: OK\n"
        raise AssertionError(command)

    trusted_root = _trusted_root()
    verified = timestamp.verify_registration_evidence(
        evidence,
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=timestamp.trusted_root_sha256(trusted_root),
        observation_starts_at="2026-08-12T06:00:00+00:00",
        now=datetime(2026, 8, 12, 5, 30, tzinfo=timezone.utc),
        openssl_runner=openssl_runner,
    )

    assert verified["gen_time"] == "2026-08-12T05:00:00+00:00"
    assert any("-verify" in command for command in calls)


def test_verify_evidence_rejects_tampering_and_post_window_timestamp():
    claim = timestamp.registration_claim(
        batch_id="batch-b-01",
        preregistration_sha256="b" * 64,
        registry_commit="a" * 40,
        registry_path="docs/registry.json",
    )
    evidence = _evidence(claim)
    evidence["claim_sha256"] = "0" * 64
    trusted_root = _trusted_root()

    with pytest.raises(ValueError, match="claim digest"):
        timestamp.verify_registration_evidence(
            evidence,
            claim=claim,
            trusted_root=trusted_root,
            expected_trusted_root_sha256=timestamp.trusted_root_sha256(
                trusted_root
            ),
            observation_starts_at="2026-08-12T06:00:00+00:00",
        )

    evidence = _evidence(claim)

    def wrong_imprint_runner(command, *, input_bytes=None):
        if "-query" in command and "-text" in command:
            return _query_text(claim, digest="c" * 64)
        if "-reply" in command and "-text" in command:
            return (
                "Policy OID: 1.3.6.1.4.1.57264.2\n"
                "Serial number: 0x10\n"
                "Time stamp: Aug 12 05:00:00 2026 GMT\n"
                "Nonce: 0x01AB\n"
            )
        return "Verification: OK\n"

    with pytest.raises(ValueError, match="message imprint"):
        timestamp.verify_registration_evidence(
            evidence,
            claim=claim,
            trusted_root=trusted_root,
            expected_trusted_root_sha256=timestamp.trusted_root_sha256(
                trusted_root
            ),
            observation_starts_at="2026-08-12T06:00:00+00:00",
            openssl_runner=wrong_imprint_runner,
        )


def test_build_registration_evidence_derives_metadata_then_reverifies():
    claim = timestamp.registration_claim(
        batch_id="batch-b-01",
        preregistration_sha256="b" * 64,
        registry_commit="a" * 40,
        registry_path="docs/registry.json",
    )

    def openssl_runner(command: list[str], *, input_bytes: bytes | None = None):
        if "-query" in command and "-text" in command:
            return _query_text(claim)
        if "-reply" in command and "-text" in command:
            return (
                "Status: Granted.\n"
                "Policy OID: 1.3.6.1.4.1.57264.2\n"
                "Serial number: 0x10\n"
                "Time stamp: Aug 12 05:00:00 2026 GMT\n"
                "Nonce: 0x01AB\n"
            )
        return "Verification: OK\n"

    trusted_root = _trusted_root()
    evidence = timestamp.build_registration_evidence(
        query_der=b"query-der",
        response_der=b"response-der",
        claim=claim,
        trusted_root=trusted_root,
        expected_trusted_root_sha256=timestamp.trusted_root_sha256(trusted_root),
        observation_starts_at="2026-08-12T06:00:00+00:00",
        openssl_runner=openssl_runner,
    )

    assert evidence["nonce"] == "0x1ab"
    assert evidence["gen_time"] == "2026-08-12T05:00:00+00:00"
    assert evidence["response_sha256"] == timestamp.sha256_hex(b"response-der")

    evidence = _evidence(claim)
    evidence["gen_time"] = "2026-08-12T07:00:00+00:00"
    with pytest.raises(ValueError, match="before observation"):
        timestamp.verify_registration_evidence(
            evidence,
            claim=claim,
            trusted_root=trusted_root,
            expected_trusted_root_sha256=timestamp.trusted_root_sha256(
                trusted_root
            ),
            observation_starts_at="2026-08-12T06:00:00+00:00",
        )
