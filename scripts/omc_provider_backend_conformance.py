#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


CONTRACT_SCHEMA = "omc-provider-backend-conformance-contract/v1"
RECEIPT_SCHEMA = "omc-provider-backend-conformance-receipt/v1"
BACKEND_ATTESTATION_SCHEMA = "omc-provider-backend-attestation/v1"
TOKEN_COUNT_SCHEMA = "omc-provider-token-count/v1"
USAGE_SCHEMA = "omc-provider-usage/v1"
FAILURE_SCHEMA = "omc-provider-failure/v1"
COUNTED_COMPONENTS = ["input_tokens", "reasoning_tokens", "output_tokens"]
REQUIRED_SCENARIOS = {
    "over_limit_pre_call": "blocked",
    "forged_capability": "blocked",
    "forged_usage": "blocked",
    "timeout": "blocked",
    "output_overflow": "blocked",
    "scope_escape": "blocked",
    "symlink_escape": "blocked",
    "partial_failure": "blocked",
    "shadow_success": "passed",
}
SCENARIO_REASON_CODES = {
    "over_limit_pre_call": "budget_reservation_exceeded",
    "forged_capability": "backend_capability_invalid",
    "forged_usage": "budget_usage_receipt_invalid",
    "timeout": "backend_timeout",
    "output_overflow": "budget_output_chars_exceeded",
    "scope_escape": "sandbox_scope_escape",
    "symlink_escape": "sandbox_symlink_escape",
    "partial_failure": "backend_partial_failure",
}


class BudgetViolation(ValueError):
    """Raised before a provider call or when trusted usage violates a reservation."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _encode_public_key(public_key: Ed25519PublicKey) -> str:
    return base64.b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _decode_public_key(value: Any) -> Ed25519PublicKey:
    if not isinstance(value, str):
        raise ValueError("conformance_public_key_invalid")
    try:
        raw = base64.b64decode(value, validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("conformance_public_key_invalid") from exc


def _signature_payload(payload: dict[str, Any]) -> bytes:
    return _canonical_bytes(
        {
            key: value
            for key, value in payload.items()
            if key not in {"receipt_sha256", "signature"}
        }
    )


def _with_hash(payload: dict[str, Any], field: str) -> dict[str, Any]:
    result = deepcopy(payload)
    result[field] = canonical_sha256(result)
    return result


def _without(payload: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != field}


def _sign_receipt(
    private_key: Ed25519PrivateKey, payload: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("receipt_private_key_invalid")
    result = deepcopy(payload)
    result["authority_public_key"] = _encode_public_key(private_key.public_key())
    result["signature"] = base64.b64encode(
        private_key.sign(_signature_payload(result))
    ).decode("ascii")
    return _with_hash(result, "receipt_sha256")


def _verify_signed_receipt(
    payload: Any, *, expected_schema: str, trusted_public_key: str
) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != expected_schema
    ):
        raise ValueError("signed_receipt_invalid")
    if payload.get("receipt_sha256") != canonical_sha256(
        _without(payload, "receipt_sha256")
    ):
        raise ValueError("signed_receipt_hash_mismatch")
    if payload.get("authority_public_key") != trusted_public_key:
        raise ValueError("signed_receipt_authority_untrusted")
    public_key = _decode_public_key(trusted_public_key)
    try:
        signature = base64.b64decode(payload.get("signature"), validate=True)
        public_key.verify(signature, _signature_payload(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("signed_receipt_signature_invalid") from exc
    return deepcopy(payload)


def build_conformance_contract(
    *,
    max_total_tokens: int,
    max_output_chars: int,
    metering_authority_public_key: str,
) -> dict[str, Any]:
    if not _positive_int(max_total_tokens) or not _positive_int(max_output_chars):
        raise ValueError("conformance_limits_invalid")
    _decode_public_key(metering_authority_public_key)
    payload = {
        "schema_version": CONTRACT_SCHEMA,
        "limits": {
            "max_total_tokens": max_total_tokens,
            "max_output_chars": max_output_chars,
        },
        "budget_accounting": {
            "scope": "child_execution",
            "counted_components": COUNTED_COMPONENTS,
            "cached_input_included_in_input_tokens": True,
            "retries_count_as_calls": True,
            "exact_input_count_required": True,
            "native_output_cap_required": True,
            "reserve_before_provider_call": True,
            "usage_source": "trusted_transport_raw_response",
            "backend_usage_authoritative": False,
            "metering_authority_public_key": metering_authority_public_key,
        },
        "sandbox_policy": {
            "workspace_mode": "disposable",
            "filesystem_mode": "workspace_only",
            "network_mode": "deny_by_default",
            "environment_mode": "allowlist",
            "environment_allowlist": [],
            "retry_workspace_mode": "new_disposable_workspace",
            "reject_symlink_escape": True,
            "kill_process_group": True,
            "discard_workspace_on_failure": True,
        },
        "threat_model": {
            "backend_capability_trusted": False,
            "backend_usage_trusted": False,
            "host_or_root_compromise_in_scope": False,
            "independent_verifier_required": True,
        },
    }
    return _with_hash(payload, "contract_sha256")


def validate_conformance_contract(payload: Any) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CONTRACT_SCHEMA
    ):
        raise ValueError("conformance_contract_schema_invalid")
    expected_hash = canonical_sha256(_without(payload, "contract_sha256"))
    if payload.get("contract_sha256") != expected_hash:
        raise ValueError("conformance_contract_hash_mismatch")
    limits = payload.get("limits")
    if not isinstance(limits, dict) or any(
        not _positive_int(limits.get(field))
        for field in ("max_total_tokens", "max_output_chars")
    ):
        raise ValueError("conformance_limits_invalid")

    accounting = payload.get("budget_accounting")
    if not isinstance(accounting, dict):
        raise ValueError("budget_accounting_invalid")
    if accounting.get("scope") != "child_execution":
        raise ValueError("budget_scope_invalid")
    if accounting.get("counted_components") != COUNTED_COMPONENTS:
        raise ValueError("budget_components_invalid")
    if accounting.get("cached_input_included_in_input_tokens") is not True:
        raise ValueError("budget_cached_input_semantics_invalid")
    if accounting.get("retries_count_as_calls") is not True:
        raise ValueError("budget_retry_accounting_invalid")
    if accounting.get("exact_input_count_required") is not True:
        raise ValueError("budget_exact_input_count_required")
    if accounting.get("native_output_cap_required") is not True:
        raise ValueError("budget_native_output_cap_required")
    if accounting.get("reserve_before_provider_call") is not True:
        raise ValueError("budget_reservation_required")
    if accounting.get("usage_source") != "trusted_transport_raw_response":
        raise ValueError("budget_usage_source_untrusted")
    if accounting.get("backend_usage_authoritative") is not False:
        raise ValueError("budget_backend_usage_authoritative")
    try:
        _decode_public_key(accounting.get("metering_authority_public_key"))
    except ValueError as exc:
        raise ValueError("budget_metering_authority_invalid")

    sandbox = payload.get("sandbox_policy")
    if not isinstance(sandbox, dict):
        raise ValueError("sandbox_policy_invalid")
    if sandbox.get("workspace_mode") != "disposable":
        raise ValueError("sandbox_workspace_mode_invalid")
    if sandbox.get("filesystem_mode") != "workspace_only":
        raise ValueError("sandbox_filesystem_mode_invalid")
    if sandbox.get("network_mode") != "deny_by_default":
        raise ValueError("sandbox_network_mode_invalid")
    if sandbox.get("environment_mode") != "allowlist" or not isinstance(
        sandbox.get("environment_allowlist"), list
    ):
        raise ValueError("sandbox_environment_policy_invalid")
    if sandbox.get("retry_workspace_mode") != "new_disposable_workspace":
        raise ValueError("sandbox_retry_workspace_invalid")
    for field in (
        "reject_symlink_escape",
        "kill_process_group",
        "discard_workspace_on_failure",
    ):
        if sandbox.get(field) is not True:
            raise ValueError(f"sandbox_{field}_required")

    threat_model = payload.get("threat_model")
    expected_threat_model = {
        "backend_capability_trusted": False,
        "backend_usage_trusted": False,
        "host_or_root_compromise_in_scope": False,
        "independent_verifier_required": True,
    }
    if threat_model != expected_threat_model:
        raise ValueError("conformance_threat_model_invalid")
    return deepcopy(payload)


def build_token_count_receipt(
    *,
    authority_private_key: Ed25519PrivateKey,
    request_sha256: str,
    input_tokens: int,
) -> dict[str, Any]:
    if not _is_sha256(request_sha256) or not _positive_int(input_tokens):
        raise ValueError("token_count_receipt_invalid")
    return _sign_receipt(
        authority_private_key,
        {
            "schema_version": TOKEN_COUNT_SCHEMA,
            "request_sha256": request_sha256,
            "input_tokens": input_tokens,
        },
    )


def build_usage_receipt(
    *,
    authority_private_key: Ed25519PrivateKey,
    call_id: str,
    request_sha256: str,
    native_max_output_tokens: int,
    usage: dict[str, int],
    output_chars: int,
    raw_response_sha256: str,
) -> dict[str, Any]:
    if (
        not isinstance(call_id, str)
        or not call_id
        or not _is_sha256(request_sha256)
        or not _positive_int(native_max_output_tokens)
        or not isinstance(output_chars, int)
        or isinstance(output_chars, bool)
        or output_chars < 0
        or not _is_sha256(raw_response_sha256)
    ):
        raise ValueError("usage_receipt_invalid")
    if not isinstance(usage, dict) or any(
        not isinstance(usage.get(field), int)
        or isinstance(usage.get(field), bool)
        or usage[field] < 0
        for field in COUNTED_COMPONENTS
    ):
        raise ValueError("usage_receipt_invalid")
    return _sign_receipt(
        authority_private_key,
        {
            "schema_version": USAGE_SCHEMA,
            "call_id": call_id,
            "request_sha256": request_sha256,
            "native_max_output_tokens": native_max_output_tokens,
            "usage": deepcopy(usage),
            "output_chars": output_chars,
            "raw_response_sha256": raw_response_sha256,
        },
    )


def build_failure_receipt(
    *,
    authority_private_key: Ed25519PrivateKey,
    call_id: str,
    request_sha256: str,
    reason_code: str,
    raw_transcript_sha256: str,
) -> dict[str, Any]:
    if (
        not isinstance(call_id, str)
        or not call_id
        or not _is_sha256(request_sha256)
        or not isinstance(reason_code, str)
        or not reason_code
        or not _is_sha256(raw_transcript_sha256)
    ):
        raise ValueError("failure_receipt_invalid")
    return _sign_receipt(
        authority_private_key,
        {
            "schema_version": FAILURE_SCHEMA,
            "call_id": call_id,
            "request_sha256": request_sha256,
            "reason_code": reason_code,
            "raw_transcript_sha256": raw_transcript_sha256,
        },
    )


def build_backend_attestation(
    *,
    backend_private_key: Ed25519PrivateKey,
    backend_sha256: str,
    protocol: str = "omc-provider-backend/v1",
) -> dict[str, Any]:
    if not _is_sha256(backend_sha256) or protocol != "omc-provider-backend/v1":
        raise ValueError("backend_attestation_invalid")
    return _sign_receipt(
        backend_private_key,
        {
            "schema_version": BACKEND_ATTESTATION_SCHEMA,
            "backend_sha256": backend_sha256,
            "protocol": protocol,
        },
    )


class TrustedBudgetBroker:
    """Reserve worst-case provider cost and settle only trusted raw usage."""

    def __init__(self, contract: dict[str, Any]) -> None:
        self.contract = validate_conformance_contract(contract)
        self._reservations: dict[str, dict[str, Any]] = {}
        self._settled: list[dict[str, Any]] = []
        self._failures: list[dict[str, Any]] = []
        self._total_tokens = 0
        self._total_output_chars = 0
        self._blocked_reason: str | None = None

    def _verify_metering_receipt(
        self, payload: Any, *, schema: str, kind: str
    ) -> dict[str, Any]:
        trusted_key = self.contract["budget_accounting"][
            "metering_authority_public_key"
        ]
        try:
            return _verify_signed_receipt(
                payload,
                expected_schema=schema,
                trusted_public_key=trusted_key,
            )
        except ValueError as exc:
            reason = str(exc)
            if reason == "signed_receipt_hash_mismatch":
                raise BudgetViolation(f"budget_{kind}_receipt_hash_mismatch") from exc
            if reason == "signed_receipt_authority_untrusted":
                raise BudgetViolation(f"budget_{kind}_authority_untrusted") from exc
            if reason == "signed_receipt_signature_invalid":
                raise BudgetViolation(f"budget_{kind}_signature_invalid") from exc
            raise BudgetViolation(f"budget_{kind}_receipt_invalid") from exc

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def remaining_tokens(self) -> int:
        outstanding = sum(
            reservation["reserved_total_tokens"]
            for reservation in self._reservations.values()
            if reservation["status"] == "reserved"
        )
        return (
            self.contract["limits"]["max_total_tokens"]
            - self._total_tokens
            - outstanding
        )

    def reserve(
        self,
        *,
        call_id: str,
        max_output_tokens: int,
        counter_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        if self._blocked_reason is not None:
            raise BudgetViolation("budget_broker_blocked")
        if not isinstance(call_id, str) or not call_id or call_id in self._reservations:
            raise BudgetViolation("budget_call_id_invalid")
        counter_receipt = self._verify_metering_receipt(
            counter_receipt, schema=TOKEN_COUNT_SCHEMA, kind="counter"
        )
        if not _is_sha256(counter_receipt.get("request_sha256")) or not _positive_int(
            counter_receipt.get("input_tokens")
        ):
            raise BudgetViolation("budget_counter_receipt_invalid")
        input_tokens = counter_receipt["input_tokens"]
        if not _positive_int(max_output_tokens):
            raise BudgetViolation("budget_reservation_invalid")
        reserved_total = input_tokens + max_output_tokens
        outstanding = sum(
            reservation["reserved_total_tokens"]
            for reservation in self._reservations.values()
            if reservation["status"] == "reserved"
        )
        maximum = self.contract["limits"]["max_total_tokens"]
        if self._total_tokens + outstanding + reserved_total > maximum:
            raise BudgetViolation("budget_reservation_exceeded")
        reservation = {
            "call_id": call_id,
            "input_tokens": input_tokens,
            "max_output_tokens": max_output_tokens,
            "reserved_total_tokens": reserved_total,
            "counter_receipt_sha256": counter_receipt["receipt_sha256"],
            "request_sha256": counter_receipt["request_sha256"],
            "status": "reserved",
        }
        self._reservations[call_id] = reservation
        return deepcopy(reservation)

    def settle(
        self,
        *,
        call_id: str,
        usage_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        reservation = self._reservations.get(call_id)
        if reservation is None or reservation["status"] != "reserved":
            raise BudgetViolation("budget_call_not_reserved")
        usage_receipt = self._verify_metering_receipt(
            usage_receipt, schema=USAGE_SCHEMA, kind="usage"
        )
        if (
            usage_receipt.get("call_id") != call_id
            or usage_receipt.get("request_sha256") != reservation["request_sha256"]
            or usage_receipt.get("native_max_output_tokens")
            != reservation["max_output_tokens"]
            or not _is_sha256(usage_receipt.get("raw_response_sha256"))
        ):
            self._seal_indeterminate(
                reservation,
                reason_code="budget_usage_receipt_invalid",
                evidence_receipt=usage_receipt,
            )
            raise BudgetViolation("budget_usage_receipt_invalid")
        usage = usage_receipt.get("usage")
        if not isinstance(usage, dict) or any(
            not isinstance(usage.get(field), int)
            or isinstance(usage.get(field), bool)
            or usage[field] < 0
            for field in COUNTED_COMPONENTS
        ):
            self._seal_indeterminate(
                reservation,
                reason_code="budget_usage_invalid",
                evidence_receipt=usage_receipt,
            )
            raise BudgetViolation("budget_usage_invalid")
        output_chars = usage_receipt.get("output_chars")
        if (
            not isinstance(output_chars, int)
            or isinstance(output_chars, bool)
            or output_chars < 0
        ):
            self._seal_indeterminate(
                reservation,
                reason_code="budget_output_chars_invalid",
                evidence_receipt=usage_receipt,
            )
            raise BudgetViolation("budget_output_chars_invalid")
        if (
            self._total_output_chars + output_chars
            > self.contract["limits"]["max_output_chars"]
        ):
            self._seal_indeterminate(
                reservation,
                reason_code="budget_output_chars_exceeded",
                evidence_receipt=usage_receipt,
            )
            raise BudgetViolation("budget_output_chars_exceeded")
        actual_total = sum(usage[field] for field in COUNTED_COMPONENTS)
        if (
            usage["input_tokens"] != reservation["input_tokens"]
            or usage["reasoning_tokens"] + usage["output_tokens"]
            > reservation["max_output_tokens"]
            or actual_total > reservation["reserved_total_tokens"]
        ):
            self._seal_indeterminate(
                reservation,
                reason_code="budget_usage_exceeds_reservation",
                evidence_receipt=usage_receipt,
            )
            raise BudgetViolation("budget_usage_exceeds_reservation")
        if (
            self._total_tokens + actual_total
            > self.contract["limits"]["max_total_tokens"]
        ):
            self._seal_indeterminate(
                reservation,
                reason_code="budget_total_exceeded",
                evidence_receipt=usage_receipt,
            )
            raise BudgetViolation("budget_total_exceeded")
        reservation["status"] = "settled"
        record = {
            "call_id": call_id,
            "usage": deepcopy(usage),
            "total_tokens": actual_total,
            "usage_receipt_sha256": usage_receipt["receipt_sha256"],
            "raw_response_sha256": usage_receipt["raw_response_sha256"],
            "output_chars": output_chars,
        }
        self._settled.append(record)
        self._total_tokens += actual_total
        self._total_output_chars += output_chars
        return deepcopy(record)

    def _seal_indeterminate(
        self,
        reservation: dict[str, Any],
        *,
        reason_code: str,
        evidence_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        reservation["status"] = "failed_indeterminate"
        self._total_tokens += reservation["reserved_total_tokens"]
        self._blocked_reason = "provider_usage_indeterminate"
        evidence_sha256 = evidence_receipt.get("raw_transcript_sha256")
        if not _is_sha256(evidence_sha256):
            evidence_sha256 = evidence_receipt.get("raw_response_sha256")
        if not _is_sha256(evidence_sha256):
            evidence_sha256 = evidence_receipt["receipt_sha256"]
        record = {
            "call_id": reservation["call_id"],
            "reason_code": reason_code,
            "evidence_receipt_sha256": evidence_receipt["receipt_sha256"],
            "evidence_sha256": evidence_sha256,
            "charged_tokens": reservation["reserved_total_tokens"],
        }
        self._failures.append(record)
        return deepcopy(record)

    def fail(self, *, call_id: str, failure_receipt: dict[str, Any]) -> dict[str, Any]:
        reservation = self._reservations.get(call_id)
        if reservation is None or reservation["status"] != "reserved":
            raise BudgetViolation("budget_call_not_reserved")
        failure_receipt = self._verify_metering_receipt(
            failure_receipt, schema=FAILURE_SCHEMA, kind="failure"
        )
        if (
            failure_receipt.get("call_id") != call_id
            or failure_receipt.get("request_sha256") != reservation["request_sha256"]
            or not isinstance(failure_receipt.get("reason_code"), str)
            or not failure_receipt["reason_code"]
            or not _is_sha256(failure_receipt.get("raw_transcript_sha256"))
        ):
            self._seal_indeterminate(
                reservation,
                reason_code="budget_failure_receipt_invalid",
                evidence_receipt=failure_receipt,
            )
            raise BudgetViolation("budget_failure_receipt_invalid")
        return self._seal_indeterminate(
            reservation,
            reason_code=failure_receipt["reason_code"],
            evidence_receipt=failure_receipt,
        )

    def evidence(self) -> dict[str, Any]:
        has_pending = any(
            reservation["status"] == "reserved"
            for reservation in self._reservations.values()
        )
        return {
            "status": (
                "indeterminate"
                if self._blocked_reason
                else "pending"
                if has_pending
                else "settled"
            ),
            "blocked_reason": self._blocked_reason,
            "contract_sha256": self.contract["contract_sha256"],
            "call_count": len(self._settled),
            "provider_call_authorized": bool(self._reservations),
            "total_tokens": self._total_tokens,
            "remaining_tokens": self.remaining_tokens,
            "total_output_chars": self._total_output_chars,
            "calls": deepcopy(self._settled),
            "failures": deepcopy(self._failures),
        }


def _validate_observations(observations: Any) -> list[dict[str, Any]]:
    if not isinstance(observations, list):
        raise ValueError("conformance_observations_invalid")
    by_scenario: dict[str, dict[str, Any]] = {}
    for row in observations:
        if not isinstance(row, dict) or not isinstance(row.get("scenario"), str):
            raise ValueError("conformance_observation_invalid")
        scenario = row["scenario"]
        if scenario in by_scenario:
            raise ValueError("conformance_scenario_duplicate")
        by_scenario[scenario] = row
    if set(by_scenario) != set(REQUIRED_SCENARIOS):
        raise ValueError("conformance_scenarios_incomplete")
    for scenario, expected_status in REQUIRED_SCENARIOS.items():
        row = by_scenario[scenario]
        if row.get("status") != expected_status:
            raise ValueError("conformance_scenario_outcome_invalid")
        if not _is_sha256(row.get("raw_transcript_sha256")):
            raise ValueError("conformance_transcript_hash_invalid")
        if row.get("harness_executed") is not True:
            raise ValueError("conformance_harness_evidence_missing")
        if row.get("transcript_verified") is not True:
            raise ValueError("conformance_transcript_not_verified")
        if not isinstance(row.get("provider_call_performed"), bool):
            raise ValueError("conformance_provider_call_evidence_missing")
        if row.get("parent_workspace_modified") is not False:
            raise ValueError("conformance_parent_workspace_modified")
        if expected_status == "blocked":
            if row.get("reason_code") != SCENARIO_REASON_CODES[scenario]:
                raise ValueError("conformance_scenario_reason_invalid")
            if row.get("workspace_discarded") is not True:
                raise ValueError("conformance_failed_workspace_not_discarded")
    if by_scenario["over_limit_pre_call"]["provider_call_performed"] is not False:
        raise ValueError("conformance_pre_call_block_invalid")
    for scenario in set(REQUIRED_SCENARIOS) - {"over_limit_pre_call"}:
        if by_scenario[scenario]["provider_call_performed"] is not True:
            raise ValueError("conformance_provider_call_evidence_invalid")
    if by_scenario["timeout"].get("process_group_terminated") is not True:
        raise ValueError("conformance_timeout_cleanup_missing")
    for scenario in ("scope_escape", "symlink_escape"):
        if by_scenario[scenario].get("scope_violation_detected") is not True:
            raise ValueError("conformance_scope_evidence_missing")
    shadow = by_scenario["shadow_success"]
    if (
        shadow.get("workspace_disposable") is not True
        or shadow.get("patch_applied_to_shadow") is not True
        or shadow.get("verification_passed") is not True
    ):
        raise ValueError("conformance_shadow_workspace_not_disposable")
    return deepcopy(observations)


def _validate_backend_attestation(
    payload: Any, *, expected_backend_sha256: str
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("backend_attestation_invalid")
    public_key = payload.get("authority_public_key")
    try:
        validated = _verify_signed_receipt(
            payload,
            expected_schema=BACKEND_ATTESTATION_SCHEMA,
            trusted_public_key=public_key,
        )
    except ValueError as exc:
        raise ValueError("backend_attestation_signature_invalid") from exc
    if validated.get("backend_sha256") != expected_backend_sha256:
        raise ValueError("backend_attestation_hash_mismatch")
    if validated.get("protocol") != "omc-provider-backend/v1":
        raise ValueError("backend_attestation_protocol_invalid")
    return validated


def build_conformance_receipt(
    *,
    contract: dict[str, Any],
    backend_sha256: str,
    verifier_sha256: str,
    backend_attestation: dict[str, Any],
    verifier_private_key: Ed25519PrivateKey,
    fixture_sha256: str,
    observations: list[dict[str, Any]],
    timestamp_receipt_sha256: str,
) -> dict[str, Any]:
    validated_contract = validate_conformance_contract(contract)
    validated_observations = _validate_observations(observations)
    validated_attestation = _validate_backend_attestation(
        backend_attestation, expected_backend_sha256=backend_sha256
    )
    if not isinstance(verifier_private_key, Ed25519PrivateKey):
        raise ValueError("conformance_verifier_private_key_invalid")
    verifier_public_key = _encode_public_key(verifier_private_key.public_key())
    if validated_attestation["authority_public_key"] == verifier_public_key:
        raise ValueError("conformance_signer_not_independent")
    payload = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "evidence_prepared",
        "claim_eligible": False,
        "contract_sha256": validated_contract["contract_sha256"],
        "backend_sha256": backend_sha256,
        "verifier_sha256": verifier_sha256,
        "backend_attestation": validated_attestation,
        "verifier_public_key": verifier_public_key,
        "fixture_sha256": fixture_sha256,
        "timestamp_receipt_sha256": timestamp_receipt_sha256,
        "observations": validated_observations,
    }
    payload["signature"] = base64.b64encode(
        verifier_private_key.sign(_signature_payload(payload))
    ).decode("ascii")
    return _with_hash(payload, "receipt_sha256")


def validate_conformance_receipt(
    payload: Any,
    *,
    contract: dict[str, Any],
    expected_backend_sha256: str,
    expected_verifier_sha256: str,
    expected_fixture_sha256: str,
    trusted_verifier_public_keys: set[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != RECEIPT_SCHEMA:
        raise ValueError("conformance_receipt_schema_invalid")
    if payload.get("receipt_sha256") != canonical_sha256(
        _without(payload, "receipt_sha256")
    ):
        raise ValueError("conformance_receipt_hash_mismatch")
    validated_contract = validate_conformance_contract(contract)
    if payload.get("contract_sha256") != validated_contract["contract_sha256"]:
        raise ValueError("conformance_contract_binding_mismatch")
    if payload.get("backend_sha256") != expected_backend_sha256:
        raise ValueError("conformance_backend_binding_mismatch")
    if payload.get("verifier_sha256") != expected_verifier_sha256:
        raise ValueError("conformance_verifier_binding_mismatch")
    if payload.get("fixture_sha256") != expected_fixture_sha256:
        raise ValueError("conformance_fixture_binding_mismatch")
    for field in (
        "backend_sha256",
        "verifier_sha256",
        "fixture_sha256",
        "timestamp_receipt_sha256",
    ):
        if not _is_sha256(payload.get(field)):
            raise ValueError(f"conformance_{field}_invalid")
    backend_attestation = _validate_backend_attestation(
        payload.get("backend_attestation"),
        expected_backend_sha256=expected_backend_sha256,
    )
    backend_signer = backend_attestation["authority_public_key"]
    verifier_signer = payload.get("verifier_public_key")
    _decode_public_key(backend_signer)
    verifier_public_key = _decode_public_key(verifier_signer)
    if backend_signer == verifier_signer:
        raise ValueError("conformance_signer_not_independent")
    if verifier_signer not in trusted_verifier_public_keys:
        raise ValueError("conformance_verifier_untrusted")
    _validate_observations(payload.get("observations"))
    if (
        payload.get("status") != "evidence_prepared"
        or payload.get("claim_eligible") is not False
    ):
        raise ValueError("conformance_receipt_status_invalid")
    signature = payload.get("signature")
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        verifier_public_key.verify(signature_bytes, _signature_payload(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("conformance_signature_invalid") from exc
    return deepcopy(payload)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-contract")
    prepare.add_argument("--max-total-tokens", type=int, required=True)
    prepare.add_argument("--max-output-chars", type=int, required=True)
    prepare.add_argument("--metering-authority-public-key", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate-contract")
    validate.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-contract":
            contract = build_conformance_contract(
                max_total_tokens=args.max_total_tokens,
                max_output_chars=args.max_output_chars,
                metering_authority_public_key=args.metering_authority_public_key,
            )
            _atomic_write_json(args.output, contract)
            print(
                json.dumps(
                    {
                        "status": "prepared",
                        "contract_sha256": contract["contract_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        validated = validate_conformance_contract(contract)
        print(
            json.dumps(
                {"status": "valid", "contract_sha256": validated["contract_sha256"]},
                sort_keys=True,
            )
        )
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(
            json.dumps({"status": "blocked", "reason_code": str(exc)}, sort_keys=True)
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
