#!/usr/bin/env python3
"""Fail-closed contract validator for the Claude Code + OMC feasibility study."""
from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


STUDY_ID = "claude-code-omc-incremental-value-20260908-v1"
PERSONA_STUDY_ID = "task-review-persona-effectiveness-20260904-v1"
ARMS = ("raw_claude_code", "claude_code_with_omc")
FROZEN_FIELDS = (
    "claude_code_version",
    "model_id",
    "reasoning_configuration",
    "permission_mode",
    "timeout_sec",
    "repository_commit",
    "source_tree_hash",
    "request_hash",
    "verification_commands",
    "native_skill_inventory",
    "hook_inventory",
    "plugin_inventory",
    "mcp_inventory",
    "environment_hash",
)
METRICS = (
    "verified_completion",
    "incorrect_completion",
    "correction_required",
    "active_human_minutes",
    "intervention_count",
    "approval_count",
    "wall_clock_seconds",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
)
BOOLEAN_METRICS = METRICS[:3]
CONTINUOUS_METRICS = (
    "active_human_minutes",
    "wall_clock_seconds",
    "estimated_cost",
)
COUNT_METRICS = (
    "intervention_count",
    "approval_count",
    "input_tokens",
    "output_tokens",
)
DIGEST_FIELDS = (
    "raw_output_sha256",
    "final_diff_sha256",
    "verification_sha256",
)
AUTHORIZATION_KEY_ENV = (
    "OMC_CLAUDE_INCREMENTAL_VALUE_TRUSTED_AUTHORIZATION_PUBLIC_KEY"
)
AUTHORIZATION_CONTRACT = {
    "trusted_authorization_public_key_source": f"environment:{AUTHORIZATION_KEY_ENV}",
    "receipt_required": True,
    "receipt_subject_fields": [
        "study_id",
        "execution_authorized",
        "trusted_execution_public_key",
        "case_roster_sha256",
    ],
}
CONFIRMATORY_THRESHOLDS = {
    "correction_required_reduction_minimum": 0.3,
    "median_wall_clock_ratio_maximum": 1.15,
    "median_token_ratio_maximum": 1.25,
}
ORDERED_RULES = [
    "registration_invalid:BLOCKED",
    "receipt_binding_invalid:BLOCKED",
    "fatal_violation:STOP",
    "provider_execution_absent:INCONCLUSIVE",
    "arm_or_environment_mismatch:INCONCLUSIVE",
    "blinding_failed:INCONCLUSIVE",
    "insufficient_valid_pairs:INCONCLUSIVE",
    "metric_capture_incomplete:FEASIBILITY_FAIL",
    "all_feasibility_gates_passed:FEASIBILITY_PASS",
]


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _git_object_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is not None
    )


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _common_configuration_valid(configuration: object) -> bool:
    if not isinstance(configuration, dict) or set(configuration) != set(FROZEN_FIELDS):
        return False
    if any(
        not _nonempty_string(configuration.get(field))
        for field in (
            "claude_code_version",
            "model_id",
            "reasoning_configuration",
            "permission_mode",
        )
    ):
        return False
    timeout = configuration.get("timeout_sec")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        return False
    if not _git_object_id(configuration.get("repository_commit")):
        return False
    if any(
        not _digest(configuration.get(field))
        for field in ("source_tree_hash", "request_hash", "environment_hash")
    ):
        return False
    commands = configuration.get("verification_commands")
    if (
        not isinstance(commands, list)
        or not commands
        or any(not _nonempty_string(command) for command in commands)
    ):
        return False
    return all(
        isinstance(configuration.get(field), list)
        for field in (
            "native_skill_inventory",
            "hook_inventory",
            "plugin_inventory",
            "mcp_inventory",
        )
    )


def canonical_bytes(value: dict[str, object]) -> bytes:
    subject = {key: item for key, item in value.items() if key != "signature"}
    return json.dumps(
        subject, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_pair_bytes(value: dict[str, object]) -> bytes:
    subject = {key: item for key, item in value.items() if key != "pair_signature"}
    return json.dumps(
        subject, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_authorization_subject_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_authorization_receipt_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_case_roster_bytes(value: list[object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_common_configuration_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _repository_identity_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "scheme",
        "host",
        "owner",
        "repository",
    }:
        return False
    if value.get("scheme") != "git_remote":
        return False
    host = value.get("host")
    owner = value.get("owner")
    repository = value.get("repository")
    return (
        isinstance(host, str)
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) is not None
        and isinstance(owner, str)
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", owner) is not None
        and isinstance(repository, str)
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", repository)
        is not None
        and not repository.endswith(".git")
    )


def repository_identity_sha256(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _public_key(registration: dict[str, object]) -> Ed25519PublicKey | None:
    try:
        encoded = registration["trusted_execution_public_key"]
        if not isinstance(encoded, str):
            return None
        raw = base64.b64decode(encoded, validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except (KeyError, TypeError, ValueError):
        return None


def _signature_valid(public_key: Ed25519PublicKey | None, arm: dict[str, object]) -> bool:
    if public_key is None:
        return False
    try:
        encoded = arm.get("signature")
        if not isinstance(encoded, str):
            return False
        public_key.verify(base64.b64decode(encoded, validate=True), canonical_bytes(arm))
        return True
    except (InvalidSignature, TypeError, ValueError):
        return False


def _pair_signature_valid(
    public_key: Ed25519PublicKey | None, pair: dict[str, object]
) -> bool:
    if public_key is None:
        return False
    try:
        encoded = pair.get("pair_signature")
        if not isinstance(encoded, str):
            return False
        public_key.verify(
            base64.b64decode(encoded, validate=True), canonical_pair_bytes(pair)
        )
        return True
    except (InvalidSignature, TypeError, ValueError):
        return False


def _authorization_receipt_valid(
    registration: dict[str, object], trusted_authorization_public_key: str | None
) -> bool:
    receipt = registration.get("authorization_receipt")
    if not isinstance(receipt, dict) or not isinstance(
        trusted_authorization_public_key, str
    ):
        return False
    expected_subject = {
        "study_id": registration.get("study_id"),
        "execution_authorized": True,
        "trusted_execution_public_key": registration.get(
            "trusted_execution_public_key"
        ),
        "case_roster_sha256": registration.get("case_roster_sha256"),
    }
    if receipt.get("subject") != expected_subject:
        return False
    if receipt.get("signer_public_key") != trusted_authorization_public_key:
        return False
    try:
        receipt_digest = hashlib.sha256(
            canonical_authorization_receipt_bytes(receipt)
        ).hexdigest()
        if registration.get("authorization_receipt_sha256") != receipt_digest:
            return False
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_authorization_public_key, validate=True)
        )
        signature = receipt.get("signature")
        if not isinstance(signature, str):
            return False
        public_key.verify(
            base64.b64decode(signature, validate=True),
            canonical_authorization_subject_bytes(expected_subject),
        )
        return True
    except (InvalidSignature, TypeError, ValueError):
        return False


def _reject_json_constant(_: str) -> None:
    raise ValueError("json_constant_invalid")


def load_regular_json(path: Path) -> Any:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("nofollow_unavailable")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("input_not_regular_file") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("input_not_regular_file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return json.load(handle, parse_constant=_reject_json_constant)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _case_roster_valid(registration: dict[str, object], execution_ready: bool) -> bool:
    roster = registration.get("case_roster")
    if not execution_ready:
        return (
            registration.get("case_roster_state") == "NOT_FROZEN"
            and roster == []
            and "case_roster_sha256" not in registration
        )
    if registration.get("case_roster_state") != "FROZEN" or not isinstance(
        roster, list
    ):
        return False
    if len(roster) != 10:
        return False
    expected_digest = hashlib.sha256(canonical_case_roster_bytes(roster)).hexdigest()
    if registration.get("case_roster_sha256") != expected_digest:
        return False
    case_ids: list[str] = []
    repository_identities: set[str] = set()
    first_arm_counts = {arm: 0 for arm in ARMS}
    for entry in roster:
        if not isinstance(entry, dict) or set(entry) != {
            "case_id",
            "repository_id",
            "repository_identity",
            "repository_identity_sha256",
            "request_hash",
            "common_configuration_sha256",
            "arm_order",
        }:
            return False
        case_id = entry.get("case_id")
        repository_id = entry.get("repository_id")
        repository_identity = entry.get("repository_identity")
        arm_order = entry.get("arm_order")
        if (
            not _nonempty_string(case_id)
            or not _nonempty_string(repository_id)
            or not _repository_identity_valid(repository_identity)
            or entry.get("repository_identity_sha256")
            != repository_identity_sha256(repository_identity)
            or not _digest(entry.get("request_hash"))
            or not _digest(entry.get("common_configuration_sha256"))
            or not isinstance(arm_order, list)
            or arm_order
            not in (list(ARMS), list(reversed(ARMS)))
        ):
            return False
        case_ids.append(case_id)
        repository_identities.add(repository_identity_sha256(repository_identity))
        first_arm_counts[arm_order[0]] += 1
    return (
        len(case_ids) == len(set(case_ids))
        and len(repository_identities) >= 2
        and set(first_arm_counts.values()) == {5}
    )


def registration_errors(
    registration: object,
    *,
    execution_ready: bool = False,
    trusted_authorization_public_key: str | None = None,
) -> list[str]:
    if not isinstance(registration, dict):
        return ["registration"]
    errors: list[str] = []
    if registration.get("study_id") != STUDY_ID or registration.get("study_id") == PERSONA_STUDY_ID:
        errors.append("study_id")
    if registration.get("schema_version") != "omc-claude-incremental-value/v1":
        errors.append("schema_version")
    if registration.get("state") != "FEASIBILITY_CONTRACT_IMPLEMENTED_NOT_STARTED":
        errors.append("state")
    if registration.get("existing_persona_evidence_reused") is not False:
        errors.append("existing_persona_evidence_reused")
    if execution_ready:
        if registration.get("execution_authorized") is not True:
            errors.append("execution_authorized")
        if not _authorization_receipt_valid(
            registration, trusted_authorization_public_key
        ):
            errors.append("authorization_receipt")
        if _public_key(registration) is None:
            errors.append("trusted_execution_public_key")
    elif registration.get("execution_authorized") is not False:
        errors.append("execution_authorized")
    if registration.get("claim_scope") != "claude_code_omc_incremental_value":
        errors.append("claim_scope")
    if registration.get("execution_authorization") != AUTHORIZATION_CONTRACT:
        errors.append("execution_authorization")
    if not _case_roster_valid(registration, execution_ready):
        errors.append("case_roster")

    arms = registration.get("arms")
    if not isinstance(arms, dict) or set(arms) != set(ARMS):
        errors.append("arms")
    else:
        raw = arms.get("raw_claude_code")
        treatment = arms.get("claude_code_with_omc")
        if not isinstance(raw, dict) or raw.get("native_features") != "frozen_default":
            errors.append("arms.raw_claude_code.native_features")
        if not isinstance(raw, dict) or raw.get("omc_enabled") is not False:
            errors.append("arms.raw_claude_code.omc_enabled")
        if not isinstance(treatment, dict) or treatment.get("native_features") != "same_as_raw_arm":
            errors.append("arms.claude_code_with_omc.native_features")
        if not isinstance(treatment, dict) or treatment.get("omc_enabled") is not True:
            errors.append("arms.claude_code_with_omc.omc_enabled")

    if registration.get("frozen_fields") != list(FROZEN_FIELDS):
        errors.append("frozen_fields")
    if registration.get("allowed_difference") != ["omc_managed_configuration"]:
        errors.append("allowed_difference")
    sample = registration.get("sample")
    if not isinstance(sample, dict):
        errors.append("sample")
    else:
        if sample.get("feasibility_pairs") != 10:
            errors.append("sample.feasibility_pairs")
        if sample.get("confirmatory_pairs") != 30:
            errors.append("sample.confirmatory_pairs")
        if sample.get("minimum_repositories") != 2:
            errors.append("sample.minimum_repositories")
        if sample.get("minimum_baseline_correction_events") != 10:
            errors.append("sample.minimum_baseline_correction_events")
    if registration.get("metrics") != list(METRICS):
        errors.append("metrics")
    if registration.get("feasibility_claim") != "NO_SUPERIORITY_CLAIM":
        errors.append("feasibility_claim")
    if registration.get("confirmatory_thresholds") != CONFIRMATORY_THRESHOLDS:
        errors.append("confirmatory_thresholds")
    if registration.get("ordered_rules") != ORDERED_RULES:
        errors.append("ordered_rules")
    return errors


def pair_errors(
    registration: object,
    pair: object,
    *,
    trusted_authorization_public_key: str | None = None,
) -> list[str]:
    errors = [
        f"registration.{item}"
        for item in registration_errors(
            registration,
            execution_ready=True,
            trusted_authorization_public_key=trusted_authorization_public_key,
        )
    ]
    if not isinstance(pair, dict):
        return errors + ["pair"]
    if not isinstance(pair.get("case_id"), str) or not pair["case_id"].strip():
        errors.append("case_id")
    arms = pair.get("arms")
    if not isinstance(arms, dict) or set(arms) != set(ARMS):
        return errors + ["arms"]
    raw = arms["raw_claude_code"]
    treatment = arms["claude_code_with_omc"]
    if not isinstance(raw, dict) or not isinstance(treatment, dict):
        return errors + ["arms"]
    public_key = _public_key(registration) if isinstance(registration, dict) else None
    if not _pair_signature_valid(public_key, pair):
        errors.append("pair_signature")
    if raw.get("omc_enabled") is not False:
        errors.append("raw_claude_code.omc_enabled")
    if treatment.get("omc_enabled") is not True:
        errors.append("claude_code_with_omc.omc_enabled")
    raw_config = raw.get("common_configuration")
    treatment_config = treatment.get("common_configuration")
    if (
        not isinstance(raw_config, dict)
        or not isinstance(treatment_config, dict)
        or raw_config != treatment_config
        or set(raw_config) != set(FROZEN_FIELDS)
    ):
        errors.append("arm_configuration_mismatch")
    elif not _common_configuration_valid(raw_config):
        errors.append("arm_configuration_invalid")
    roster = registration.get("case_roster") if isinstance(registration, dict) else None
    roster_entry = next(
        (
            entry
            for entry in roster
            if isinstance(entry, dict) and entry.get("case_id") == pair.get("case_id")
        ),
        None,
    ) if isinstance(roster, list) else None
    if (
        not isinstance(roster_entry, dict)
        or pair.get("repository_id") != roster_entry.get("repository_id")
        or pair.get("repository_identity_sha256")
        != roster_entry.get("repository_identity_sha256")
        or pair.get("arm_order") != roster_entry.get("arm_order")
        or not isinstance(raw_config, dict)
        or raw_config.get("request_hash") != roster_entry.get("request_hash")
        or hashlib.sha256(
            canonical_common_configuration_bytes(raw_config)
        ).hexdigest()
        != roster_entry.get("common_configuration_sha256")
    ):
        errors.append("case_roster_mismatch")
    for identity in ("workspace_identity", "session_identity", "cache_identity"):
        raw_value = raw.get(identity)
        treatment_value = treatment.get(identity)
        if (
            not isinstance(raw_value, str)
            or not raw_value.strip()
            or not isinstance(treatment_value, str)
            or not treatment_value.strip()
            or raw_value == treatment_value
        ):
            if "cross_arm_contamination" not in errors:
                errors.append("cross_arm_contamination")
    for arm_name, arm in ((ARMS[0], raw), (ARMS[1], treatment)):
        if not _signature_valid(public_key, arm):
            errors.append(f"{arm_name}.signature")
        for field in DIGEST_FIELDS:
            if not _digest(arm.get(field)):
                errors.append(f"{arm_name}.{field}")
        metrics = arm.get("metrics")
        if not isinstance(metrics, dict) or set(metrics) != set(METRICS):
            errors.append(f"{arm_name}.metrics")
        elif (
            any(not isinstance(metrics[name], bool) for name in BOOLEAN_METRICS)
            or any(
                not isinstance(metrics[name], (int, float))
                or isinstance(metrics[name], bool)
                or not math.isfinite(metrics[name])
                or metrics[name] < 0
                for name in CONTINUOUS_METRICS
            )
            or any(
                isinstance(metrics[name], bool)
                or not isinstance(metrics[name], int)
                or metrics[name] < 0
                for name in COUNT_METRICS
            )
        ):
            errors.append(f"{arm_name}.metrics")
    return errors


def decide_feasibility(
    registration: object,
    pairs: object,
    *,
    trusted_authorization_public_key: str | None = None,
) -> dict[str, object]:
    claim = "NO_SUPERIORITY_CLAIM"
    if not isinstance(registration, dict):
        return {"status": "BLOCKED", "reason": "registration_invalid", "valid_pairs": 0, "claim": claim}
    if registration.get("execution_authorized") is not True:
        if registration_errors(registration):
            return {"status": "BLOCKED", "reason": "registration_invalid", "valid_pairs": 0, "claim": claim}
        return {"status": "BLOCKED", "reason": "execution_not_authorized", "valid_pairs": 0, "claim": claim}
    if registration_errors(
        registration,
        execution_ready=True,
        trusted_authorization_public_key=trusted_authorization_public_key,
    ):
        return {"status": "BLOCKED", "reason": "registration_invalid", "valid_pairs": 0, "claim": claim}
    if not isinstance(pairs, list):
        return {"status": "BLOCKED", "reason": "pairs_invalid", "valid_pairs": 0, "claim": claim}
    error_sets = [
        pair_errors(
            registration,
            pair,
            trusted_authorization_public_key=trusted_authorization_public_key,
        )
        for pair in pairs
    ]
    binding_errors = [
        error
        for errors in error_sets
        for error in errors
        if error
        not in {
            "cross_arm_contamination",
            "arm_configuration_mismatch",
            "arm_configuration_invalid",
        }
        and not error.endswith(".metrics")
    ]
    if binding_errors:
        return {"status": "BLOCKED", "reason": "receipt_binding_invalid", "valid_pairs": 0, "claim": claim}
    if any(isinstance(pair, dict) and pair.get("fatal_violation") is True for pair in pairs):
        return {"status": "STOP", "reason": "fatal_violation", "valid_pairs": 0, "claim": claim}
    if any(not isinstance(pair, dict) or pair.get("provider_execution_present") is not True for pair in pairs):
        return {"status": "INCONCLUSIVE", "reason": "provider_execution_absent", "valid_pairs": 0, "claim": claim}
    if any(
        any(
            error in errors
            for error in (
                "cross_arm_contamination",
                "arm_configuration_mismatch",
                "arm_configuration_invalid",
            )
        )
        for errors in error_sets
    ):
        return {"status": "INCONCLUSIVE", "reason": "arm_or_environment_mismatch", "valid_pairs": 0, "claim": claim}
    if any(not isinstance(pair, dict) or pair.get("blind_evaluation_valid") is not True for pair in pairs):
        return {"status": "INCONCLUSIVE", "reason": "blinding_failed", "valid_pairs": 0, "claim": claim}
    case_ids = [pair["case_id"] for pair in pairs]
    if len(case_ids) != len(set(case_ids)):
        return {"status": "INCONCLUSIVE", "reason": "duplicate_case_id", "valid_pairs": len(set(case_ids)), "claim": claim}
    if len(pairs) != 10:
        return {"status": "INCONCLUSIVE", "reason": "insufficient_valid_pairs", "valid_pairs": len(pairs), "claim": claim}
    if any(
        pair.get("metric_capture_complete") is not True for pair in pairs
    ) or any(error.endswith(".metrics") for errors in error_sets for error in errors):
        return {"status": "FEASIBILITY_FAIL", "reason": "metric_capture_incomplete", "valid_pairs": 10, "claim": claim}
    return {"status": "FEASIBILITY_PASS", "reason": "measurement_contract_executable", "valid_pairs": 10, "claim": claim}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-registration")
    validate.add_argument("--registration", type=Path, required=True)
    decide = subparsers.add_parser("decide-feasibility")
    decide.add_argument("--registration", type=Path, required=True)
    decide.add_argument("--pairs", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        registration = load_regular_json(args.registration)
        if args.command == "validate-registration":
            errors = registration_errors(registration)
            print(json.dumps({"valid": not errors, "errors": errors, "execution_authorized": False}, ensure_ascii=False, sort_keys=True))
            return 0 if not errors else 2
        pairs = load_regular_json(args.pairs)
        result = decide_feasibility(
            registration,
            pairs,
            trusted_authorization_public_key=os.environ.get(AUTHORIZATION_KEY_ENV),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "FEASIBILITY_PASS" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
