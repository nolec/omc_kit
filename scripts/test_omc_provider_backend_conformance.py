from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from omc_provider_backend_conformance import (
    BudgetViolation,
    TrustedBudgetBroker,
    build_backend_attestation,
    build_conformance_contract,
    build_conformance_receipt,
    build_failure_receipt,
    build_token_count_receipt,
    build_usage_receipt,
    canonical_sha256,
    validate_conformance_contract,
    validate_conformance_receipt,
)


SCRIPT = Path(__file__).with_name("omc_provider_backend_conformance.py")
METERING_KEY = Ed25519PrivateKey.generate()


def _contract() -> dict[str, object]:
    return build_conformance_contract(
        max_total_tokens=100,
        max_output_chars=500,
        metering_authority_public_key=_public_key(METERING_KEY),
    )


def _observations() -> list[dict[str, object]]:
    blocked = [
        "over_limit_pre_call",
        "forged_capability",
        "forged_usage",
        "timeout",
        "output_overflow",
        "scope_escape",
        "symlink_escape",
        "partial_failure",
    ]
    reason_codes = {
        "over_limit_pre_call": "budget_reservation_exceeded",
        "forged_capability": "backend_capability_invalid",
        "forged_usage": "budget_usage_receipt_invalid",
        "timeout": "backend_timeout",
        "output_overflow": "budget_output_chars_exceeded",
        "scope_escape": "sandbox_scope_escape",
        "symlink_escape": "sandbox_symlink_escape",
        "partial_failure": "backend_partial_failure",
    }
    rows = [
        {
            "scenario": scenario,
            "status": "blocked",
            "reason_code": reason_codes[scenario],
            "raw_transcript_sha256": f"{index:x}" * 64,
            "harness_executed": True,
            "transcript_verified": True,
            "provider_call_performed": scenario != "over_limit_pre_call",
            "workspace_discarded": True,
            "parent_workspace_modified": False,
            **({"process_group_terminated": True} if scenario == "timeout" else {}),
            **(
                {"scope_violation_detected": True}
                if scenario in {"scope_escape", "symlink_escape"}
                else {}
            ),
        }
        for index, scenario in enumerate(blocked, start=1)
    ]
    rows.append(
        {
            "scenario": "shadow_success",
            "status": "passed",
            "raw_transcript_sha256": "f" * 64,
            "harness_executed": True,
            "transcript_verified": True,
            "provider_call_performed": True,
            "workspace_disposable": True,
            "patch_applied_to_shadow": True,
            "verification_passed": True,
            "parent_workspace_modified": False,
        }
    )
    return rows


def _count_receipt(*, input_tokens: int, request: str) -> dict[str, object]:
    return build_token_count_receipt(
        authority_private_key=METERING_KEY,
        request_sha256=request * 64,
        input_tokens=input_tokens,
    )


def _usage_receipt(
    *,
    call_id: str,
    request: str,
    max_output_tokens: int,
    input_tokens: int,
    reasoning_tokens: int,
    output_tokens: int,
    output_chars: int = 10,
) -> dict[str, object]:
    return build_usage_receipt(
        authority_private_key=METERING_KEY,
        call_id=call_id,
        request_sha256=request * 64,
        native_max_output_tokens=max_output_tokens,
        usage={
            "input_tokens": input_tokens,
            "reasoning_tokens": reasoning_tokens,
            "output_tokens": output_tokens,
        },
        output_chars=output_chars,
        raw_response_sha256=call_id[-1] * 64,
    )


def _public_key(private_key: Ed25519PrivateKey) -> str:
    import base64

    return base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def test_contract_defines_exact_aggregate_accounting_and_fail_closed_sandbox() -> None:
    contract = validate_conformance_contract(_contract())

    assert contract["budget_accounting"] == {
        "scope": "child_execution",
        "counted_components": [
            "input_tokens",
            "reasoning_tokens",
            "output_tokens",
        ],
        "cached_input_included_in_input_tokens": True,
        "retries_count_as_calls": True,
        "exact_input_count_required": True,
        "native_output_cap_required": True,
        "reserve_before_provider_call": True,
        "usage_source": "trusted_transport_raw_response",
        "backend_usage_authoritative": False,
        "metering_authority_public_key": _public_key(METERING_KEY),
    }
    assert contract["sandbox_policy"]["workspace_mode"] == "disposable"
    assert contract["sandbox_policy"]["filesystem_mode"] == "workspace_only"
    assert contract["sandbox_policy"]["network_mode"] == "deny_by_default"
    assert contract["sandbox_policy"]["discard_workspace_on_failure"] is True


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda value: value["budget_accounting"].update(
                {"usage_source": "backend_self_reported"}
            ),
            "budget_usage_source_untrusted",
        ),
        (
            lambda value: value["budget_accounting"].update(
                {"exact_input_count_required": False}
            ),
            "budget_exact_input_count_required",
        ),
        (
            lambda value: value["sandbox_policy"].update(
                {"filesystem_mode": "host_access"}
            ),
            "sandbox_filesystem_mode_invalid",
        ),
        (
            lambda value: value["sandbox_policy"].update(
                {"retry_workspace_mode": "reuse"}
            ),
            "sandbox_retry_workspace_invalid",
        ),
    ],
)
def test_contract_rejects_self_reported_budget_or_weak_sandbox(
    mutation, reason: str
) -> None:
    contract = _contract()
    mutation(contract)
    contract["contract_sha256"] = canonical_sha256(
        {key: value for key, value in contract.items() if key != "contract_sha256"}
    )

    with pytest.raises(ValueError, match=reason):
        validate_conformance_contract(contract)


def test_broker_reserves_every_call_and_settles_raw_transport_usage() -> None:
    broker = TrustedBudgetBroker(_contract())

    first = broker.reserve(
        call_id="call-1",
        max_output_tokens=30,
        counter_receipt=_count_receipt(input_tokens=20, request="a"),
    )
    broker.settle(
        call_id="call-1",
        usage_receipt=_usage_receipt(
            call_id="call-1",
            request="a",
            max_output_tokens=30,
            input_tokens=20,
            reasoning_tokens=8,
            output_tokens=12,
            output_chars=40,
        ),
    )
    second = broker.reserve(
        call_id="call-2",
        max_output_tokens=20,
        counter_receipt=_count_receipt(input_tokens=10, request="b"),
    )
    broker.settle(
        call_id="call-2",
        usage_receipt=_usage_receipt(
            call_id="call-2",
            request="b",
            max_output_tokens=20,
            input_tokens=10,
            reasoning_tokens=5,
            output_tokens=5,
            output_chars=20,
        ),
    )

    assert first["reserved_total_tokens"] == 50
    assert second["reserved_total_tokens"] == 30
    assert broker.total_tokens == 60
    assert broker.remaining_tokens == 40
    assert broker.evidence()["call_count"] == 2


def test_broker_rejects_over_limit_before_provider_call() -> None:
    broker = TrustedBudgetBroker(_contract())

    with pytest.raises(BudgetViolation, match="budget_reservation_exceeded"):
        broker.reserve(
            call_id="call-1",
            max_output_tokens=21,
            counter_receipt=_count_receipt(input_tokens=80, request="a"),
        )

    assert broker.evidence()["call_count"] == 0
    assert broker.evidence()["provider_call_authorized"] is False


def test_broker_remaining_budget_includes_unsettled_reservations() -> None:
    broker = TrustedBudgetBroker(_contract())
    broker.reserve(
        call_id="call-1",
        max_output_tokens=30,
        counter_receipt=_count_receipt(input_tokens=20, request="a"),
    )

    assert broker.remaining_tokens == 50
    assert broker.evidence()["status"] == "pending"
    with pytest.raises(BudgetViolation, match="budget_reservation_exceeded"):
        broker.reserve(
            call_id="call-2",
            max_output_tokens=31,
            counter_receipt=_count_receipt(input_tokens=20, request="b"),
        )


def test_broker_rejects_forged_or_unreserved_usage() -> None:
    broker = TrustedBudgetBroker(_contract())
    broker.reserve(
        call_id="call-1",
        max_output_tokens=10,
        counter_receipt=_count_receipt(input_tokens=20, request="a"),
    )

    with pytest.raises(BudgetViolation, match="budget_usage_exceeds_reservation"):
        broker.settle(
            call_id="call-1",
            usage_receipt=_usage_receipt(
                call_id="call-1",
                request="a",
                max_output_tokens=10,
                input_tokens=20,
                reasoning_tokens=8,
                output_tokens=3,
            ),
        )

    assert broker.total_tokens == 30
    assert broker.evidence()["status"] == "indeterminate"
    assert broker.evidence()["failures"][0]["reason_code"] == (
        "budget_usage_exceeds_reservation"
    )

    with pytest.raises(BudgetViolation, match="budget_call_not_reserved"):
        broker.settle(
            call_id="forged",
            usage_receipt=_usage_receipt(
                call_id="forged",
                request="f",
                max_output_tokens=10,
                input_tokens=1,
                reasoning_tokens=0,
                output_tokens=1,
            ),
        )


def test_broker_rejects_forged_metering_receipt_and_aggregate_output() -> None:
    broker = TrustedBudgetBroker(_contract())
    forged = _count_receipt(input_tokens=10, request="a")
    forged["input_tokens"] = 1
    with pytest.raises(BudgetViolation, match="budget_counter_receipt_hash_mismatch"):
        broker.reserve(
            call_id="call-1",
            max_output_tokens=10,
            counter_receipt=forged,
        )

    broker.reserve(
        call_id="call-1",
        max_output_tokens=10,
        counter_receipt=_count_receipt(input_tokens=10, request="a"),
    )
    broker.settle(
        call_id="call-1",
        usage_receipt=_usage_receipt(
            call_id="call-1",
            request="a",
            max_output_tokens=10,
            input_tokens=10,
            reasoning_tokens=2,
            output_tokens=3,
            output_chars=490,
        ),
    )
    broker.reserve(
        call_id="call-2",
        max_output_tokens=10,
        counter_receipt=_count_receipt(input_tokens=10, request="b"),
    )
    with pytest.raises(BudgetViolation, match="budget_output_chars_exceeded"):
        broker.settle(
            call_id="call-2",
            usage_receipt=_usage_receipt(
                call_id="call-2",
                request="b",
                max_output_tokens=10,
                input_tokens=10,
                reasoning_tokens=2,
                output_tokens=3,
                output_chars=11,
            ),
        )

    assert broker.evidence()["status"] == "indeterminate"


def test_broker_rejects_rehashed_receipt_from_untrusted_metering_key() -> None:
    broker = TrustedBudgetBroker(_contract())
    attacker_key = Ed25519PrivateKey.generate()
    forged = build_token_count_receipt(
        authority_private_key=attacker_key,
        request_sha256="a" * 64,
        input_tokens=1,
    )

    with pytest.raises(BudgetViolation, match="budget_counter_authority_untrusted"):
        broker.reserve(
            call_id="call-1",
            max_output_tokens=10,
            counter_receipt=forged,
        )


def test_broker_rejects_rehashed_output_chars_without_valid_signature() -> None:
    broker = TrustedBudgetBroker(_contract())
    broker.reserve(
        call_id="call-1",
        max_output_tokens=10,
        counter_receipt=_count_receipt(input_tokens=10, request="a"),
    )
    forged = _usage_receipt(
        call_id="call-1",
        request="a",
        max_output_tokens=10,
        input_tokens=10,
        reasoning_tokens=2,
        output_tokens=3,
        output_chars=600,
    )
    forged["output_chars"] = 0
    forged["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in forged.items() if key != "receipt_sha256"}
    )

    with pytest.raises(BudgetViolation, match="budget_usage_signature_invalid"):
        broker.settle(call_id="call-1", usage_receipt=forged)

    assert broker.evidence()["status"] == "pending"


def test_broker_seals_failed_reservation_and_blocks_further_calls() -> None:
    broker = TrustedBudgetBroker(_contract())
    broker.reserve(
        call_id="call-1",
        max_output_tokens=30,
        counter_receipt=_count_receipt(input_tokens=20, request="a"),
    )
    failure = build_failure_receipt(
        authority_private_key=METERING_KEY,
        call_id="call-1",
        request_sha256="a" * 64,
        reason_code="backend_timeout",
        raw_transcript_sha256="e" * 64,
    )

    broker.fail(call_id="call-1", failure_receipt=failure)

    assert broker.total_tokens == 50
    assert broker.evidence()["status"] == "indeterminate"
    assert broker.evidence()["failures"][0]["reason_code"] == "backend_timeout"
    with pytest.raises(BudgetViolation, match="budget_broker_blocked"):
        broker.reserve(
            call_id="call-2",
            max_output_tokens=10,
            counter_receipt=_count_receipt(input_tokens=10, request="b"),
        )


@pytest.mark.parametrize(
    ("receipt_call_id", "request_marker"),
    [("call-2", "a"), ("call-1", "b")],
)
def test_broker_seals_trusted_failure_binding_mismatch(
    receipt_call_id: str, request_marker: str
) -> None:
    broker = TrustedBudgetBroker(_contract())
    broker.reserve(
        call_id="call-1",
        max_output_tokens=10,
        counter_receipt=_count_receipt(input_tokens=10, request="a"),
    )
    failure = build_failure_receipt(
        authority_private_key=METERING_KEY,
        call_id=receipt_call_id,
        request_sha256=request_marker * 64,
        reason_code="backend_timeout",
        raw_transcript_sha256="e" * 64,
    )

    with pytest.raises(BudgetViolation, match="budget_failure_receipt_invalid"):
        broker.fail(call_id="call-1", failure_receipt=failure)

    assert broker.total_tokens == 20
    assert broker.evidence()["status"] == "indeterminate"
    assert broker.evidence()["failures"][0]["reason_code"] == (
        "budget_failure_receipt_invalid"
    )


def test_broker_does_not_seal_untrusted_failure_receipt() -> None:
    broker = TrustedBudgetBroker(_contract())
    broker.reserve(
        call_id="call-1",
        max_output_tokens=10,
        counter_receipt=_count_receipt(input_tokens=10, request="a"),
    )
    failure = build_failure_receipt(
        authority_private_key=Ed25519PrivateKey.generate(),
        call_id="call-1",
        request_sha256="a" * 64,
        reason_code="backend_timeout",
        raw_transcript_sha256="e" * 64,
    )

    with pytest.raises(BudgetViolation, match="budget_failure_authority_untrusted"):
        broker.fail(call_id="call-1", failure_receipt=failure)

    assert broker.total_tokens == 0
    assert broker.evidence()["status"] == "pending"


@pytest.mark.parametrize(
    ("request_marker", "max_output_tokens"),
    [("b", 10), ("a", 11)],
)
def test_broker_rejects_usage_not_bound_to_request_and_native_cap(
    request_marker: str, max_output_tokens: int
) -> None:
    broker = TrustedBudgetBroker(_contract())
    broker.reserve(
        call_id="call-1",
        max_output_tokens=10,
        counter_receipt=_count_receipt(input_tokens=20, request="a"),
    )

    with pytest.raises(BudgetViolation, match="budget_usage_receipt_invalid"):
        broker.settle(
            call_id="call-1",
            usage_receipt=_usage_receipt(
                call_id="call-1",
                request=request_marker,
                max_output_tokens=max_output_tokens,
                input_tokens=20,
                reasoning_tokens=2,
                output_tokens=3,
            ),
        )


def test_receipt_binds_independent_verifier_and_all_adversarial_cases() -> None:
    contract = _contract()
    backend_key = Ed25519PrivateKey.generate()
    verifier_key = Ed25519PrivateKey.generate()
    backend_attestation = build_backend_attestation(
        backend_private_key=backend_key,
        backend_sha256="a" * 64,
    )
    receipt = build_conformance_receipt(
        contract=contract,
        backend_sha256="a" * 64,
        verifier_sha256="b" * 64,
        backend_attestation=backend_attestation,
        verifier_private_key=verifier_key,
        fixture_sha256="c" * 64,
        observations=_observations(),
        timestamp_receipt_sha256="d" * 64,
    )

    validated = validate_conformance_receipt(
        receipt,
        contract=contract,
        expected_backend_sha256="a" * 64,
        expected_verifier_sha256="b" * 64,
        expected_fixture_sha256="c" * 64,
        trusted_verifier_public_keys={_public_key(verifier_key)},
    )

    assert validated["status"] == "evidence_prepared"
    assert validated["claim_eligible"] is False
    assert len(validated["observations"]) == 9


def test_receipt_rejects_same_signer_missing_case_or_tampering() -> None:
    contract = _contract()
    backend_key = Ed25519PrivateKey.generate()
    verifier_key = Ed25519PrivateKey.generate()
    backend_attestation = build_backend_attestation(
        backend_private_key=backend_key,
        backend_sha256="a" * 64,
    )
    receipt = build_conformance_receipt(
        contract=contract,
        backend_sha256="a" * 64,
        verifier_sha256="b" * 64,
        backend_attestation=backend_attestation,
        verifier_private_key=verifier_key,
        fixture_sha256="c" * 64,
        observations=_observations(),
        timestamp_receipt_sha256="d" * 64,
    )

    with pytest.raises(ValueError, match="conformance_signer_not_independent"):
        build_conformance_receipt(
            contract=contract,
            backend_sha256="a" * 64,
            verifier_sha256="b" * 64,
            backend_attestation=build_backend_attestation(
                backend_private_key=verifier_key,
                backend_sha256="a" * 64,
            ),
            verifier_private_key=verifier_key,
            fixture_sha256="c" * 64,
            observations=_observations(),
            timestamp_receipt_sha256="d" * 64,
        )

    missing = deepcopy(receipt)
    missing["observations"] = missing["observations"][:-1]
    missing["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in missing.items() if key != "receipt_sha256"}
    )
    with pytest.raises(ValueError, match="conformance_scenarios_incomplete"):
        validate_conformance_receipt(
            missing,
            contract=contract,
            expected_backend_sha256="a" * 64,
            expected_verifier_sha256="b" * 64,
            expected_fixture_sha256="c" * 64,
            trusted_verifier_public_keys={_public_key(verifier_key)},
        )

    tampered = deepcopy(receipt)
    tampered["observations"][0]["status"] = "passed"
    with pytest.raises(ValueError, match="conformance_receipt_hash_mismatch"):
        validate_conformance_receipt(
            tampered,
            contract=contract,
            expected_backend_sha256="a" * 64,
            expected_verifier_sha256="b" * 64,
            expected_fixture_sha256="c" * 64,
            trusted_verifier_public_keys={_public_key(verifier_key)},
        )

    wrong_fixture = deepcopy(receipt)
    with pytest.raises(ValueError, match="conformance_fixture_binding_mismatch"):
        validate_conformance_receipt(
            wrong_fixture,
            contract=contract,
            expected_backend_sha256="a" * 64,
            expected_verifier_sha256="b" * 64,
            expected_fixture_sha256="e" * 64,
            trusted_verifier_public_keys={_public_key(verifier_key)},
        )

    untrusted = deepcopy(receipt)
    with pytest.raises(ValueError, match="conformance_verifier_untrusted"):
        validate_conformance_receipt(
            untrusted,
            contract=contract,
            expected_backend_sha256="a" * 64,
            expected_verifier_sha256="b" * 64,
            expected_fixture_sha256="c" * 64,
            trusted_verifier_public_keys={_public_key(Ed25519PrivateKey.generate())},
        )

    bad_signature = deepcopy(receipt)
    bad_signature["signature"] = bad_signature["signature"][:-2] + "AA"
    bad_signature["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in bad_signature.items() if key != "receipt_sha256"}
    )
    with pytest.raises(ValueError, match="conformance_signature_invalid"):
        validate_conformance_receipt(
            bad_signature,
            contract=contract,
            expected_backend_sha256="a" * 64,
            expected_verifier_sha256="b" * 64,
            expected_fixture_sha256="c" * 64,
            trusted_verifier_public_keys={_public_key(verifier_key)},
        )

    forged_attestation = deepcopy(receipt)
    forged_attestation["backend_attestation"] = build_backend_attestation(
        backend_private_key=backend_key,
        backend_sha256="e" * 64,
    )
    forged_attestation["receipt_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in forged_attestation.items()
            if key != "receipt_sha256"
        }
    )
    with pytest.raises(ValueError, match="backend_attestation_hash_mismatch"):
        validate_conformance_receipt(
            forged_attestation,
            contract=contract,
            expected_backend_sha256="a" * 64,
            expected_verifier_sha256="b" * 64,
            expected_fixture_sha256="c" * 64,
            trusted_verifier_public_keys={_public_key(verifier_key)},
        )


def test_receipt_rejects_scenario_without_execution_evidence() -> None:
    observations = _observations()
    del observations[0]["harness_executed"]

    with pytest.raises(ValueError, match="conformance_harness_evidence_missing"):
        build_conformance_receipt(
            contract=_contract(),
            backend_sha256="a" * 64,
            verifier_sha256="b" * 64,
            backend_attestation=build_backend_attestation(
                backend_private_key=Ed25519PrivateKey.generate(),
                backend_sha256="a" * 64,
            ),
            verifier_private_key=Ed25519PrivateKey.generate(),
            fixture_sha256="c" * 64,
            observations=observations,
            timestamp_receipt_sha256="d" * 64,
        )


def test_cli_prepares_and_validates_contract(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.json"
    prepare = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "prepare-contract",
            "--max-total-tokens",
            "100",
            "--max-output-chars",
            "500",
            "--metering-authority-public-key",
            _public_key(METERING_KEY),
            "--output",
            str(contract_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert prepare.returncode == 0, prepare.stderr

    validate = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "validate-contract",
            "--contract",
            str(contract_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert validate.returncode == 0, validate.stderr
    assert json.loads(validate.stdout)["status"] == "valid"
