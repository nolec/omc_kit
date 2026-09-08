from __future__ import annotations

import copy
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import omc_claude_incremental_value as subject
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_PATH = (
    ROOT / "docs" / "claude_code_omc_incremental_value_preregistration_v1.json"
)
V2_REGISTRATION_PATH = (
    ROOT / "docs" / "claude_code_omc_incremental_value_preregistration_v2.json"
)
RUNBOOK_PATH = ROOT / "docs" / "claude_code_omc_incremental_value_runbook.md"
ROADMAP_PATH = ROOT / "docs" / "automatic_model_routing_roadmap.md"
README_PATH = ROOT / "README.md"
AUTHORIZATION_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))


def _public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


TRUSTED_AUTHORIZATION_PUBLIC_KEY = _public_key_b64(AUTHORIZATION_PRIVATE_KEY)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _git_object_id(value: str) -> str:
    return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_repository_identity_digest_is_derived_from_canonical_subject() -> None:
    repository_identity = {
        "scheme": "git_remote",
        "host": "github.com",
        "owner": "example",
        "repository": "service-a",
    }
    assert subject.repository_identity_sha256(repository_identity) == hashlib.sha256(
        _canonical_json(repository_identity)
    ).hexdigest()


def _common_configuration(index: int) -> dict[str, object]:
    return {
        "claude_code_version": "1.2.3",
        "model_id": "claude-test",
        "reasoning_configuration": "default",
        "permission_mode": "default",
        "timeout_sec": 1200,
        "repository_commit": _git_object_id(f"commit-{index}"),
        "source_tree_hash": _digest(f"tree-{index}"),
        "request_hash": _digest(f"request-{index}"),
        "verification_commands": ["pytest -q"],
        "native_skill_inventory": [],
        "hook_inventory": [],
        "plugin_inventory": [],
        "mcp_inventory": [],
        "environment_hash": _digest(f"environment-{index}"),
    }


def _repository_identity(index: int) -> dict[str, str]:
    return {
        "scheme": "git_remote",
        "host": "github.com",
        "owner": "example",
        "repository": f"service-{index % 2}",
    }


def _roster() -> list[dict[str, object]]:
    return [
        {
            "case_id": f"case-{index:02d}",
            "repository_id": f"repo-{index % 2}",
            "repository_identity": _repository_identity(index),
            "repository_identity_sha256": subject.repository_identity_sha256(
                _repository_identity(index)
            ),
            "request_hash": _digest(f"request-{index}"),
            "common_configuration_sha256": hashlib.sha256(
                _canonical_json(_common_configuration(index))
            ).hexdigest(),
            "arm_order": list(subject.ARMS)
            if index % 2 == 0
            else list(reversed(subject.ARMS)),
        }
        for index in range(10)
    ]


def _registration() -> dict[str, object]:
    return json.loads(REGISTRATION_PATH.read_text(encoding="utf-8"))


def _v2_registration() -> dict[str, object]:
    return json.loads(V2_REGISTRATION_PATH.read_text(encoding="utf-8"))


def _execution_registration() -> tuple[dict[str, object], Ed25519PrivateKey]:
    registration = _registration()
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    execution_public_key = _public_key_b64(private_key)
    registration["execution_authorized"] = True
    registration["trusted_execution_public_key"] = execution_public_key
    registration["case_roster_state"] = "FROZEN"
    registration["case_roster"] = _roster()
    registration["case_roster_sha256"] = hashlib.sha256(
        _canonical_json(registration["case_roster"])
    ).hexdigest()
    subject_payload = {
        "study_id": registration["study_id"],
        "execution_authorized": True,
        "trusted_execution_public_key": execution_public_key,
        "case_roster_sha256": registration["case_roster_sha256"],
    }
    subject_bytes = subject.canonical_authorization_subject_bytes(subject_payload)
    registration["authorization_receipt"] = {
        "subject": subject_payload,
        "signer_public_key": TRUSTED_AUTHORIZATION_PUBLIC_KEY,
        "signature": base64.b64encode(
            AUTHORIZATION_PRIVATE_KEY.sign(subject_bytes)
        ).decode(),
    }
    registration["authorization_receipt_sha256"] = hashlib.sha256(
        subject.canonical_authorization_receipt_bytes(
            registration["authorization_receipt"]
        )
    ).hexdigest()
    return registration, private_key


def _decide(registration: object, pairs: object) -> dict[str, object]:
    return subject.decide_feasibility(
        registration,
        pairs,
        trusted_authorization_public_key=TRUSTED_AUTHORIZATION_PUBLIC_KEY,
    )


def _sign_arm(arm: dict[str, object], private_key: Ed25519PrivateKey) -> None:
    signature = private_key.sign(subject.canonical_bytes(arm))
    arm["signature"] = base64.b64encode(signature).decode()


def _sign_pair(pair: dict[str, object], private_key: Ed25519PrivateKey) -> None:
    signature = private_key.sign(subject.canonical_pair_bytes(pair))
    pair["pair_signature"] = base64.b64encode(signature).decode()


def _pair(index: int, private_key: Ed25519PrivateKey) -> dict[str, object]:
    common = _common_configuration(index)
    metrics = {
        "verified_completion": True,
        "incorrect_completion": False,
        "correction_required": False,
        "active_human_minutes": 3.0,
        "intervention_count": 1,
        "approval_count": 1,
        "wall_clock_seconds": 120.0,
        "input_tokens": 1000,
        "output_tokens": 500,
        "estimated_cost": 1.25,
    }
    pair = {
        "case_id": f"case-{index:02d}",
        "repository_id": f"repo-{index % 2}",
        "repository_identity_sha256": subject.repository_identity_sha256(
            _repository_identity(index)
        ),
        "arm_order": list(subject.ARMS)
        if index % 2 == 0
        else list(reversed(subject.ARMS)),
        "fatal_violation": False,
        "provider_execution_present": True,
        "blind_evaluation_valid": True,
        "metric_capture_complete": True,
        "arms": {
            "raw_claude_code": {
                "common_configuration": common,
                "omc_enabled": False,
                "workspace_identity": f"raw-workspace-{index}",
                "session_identity": f"raw-session-{index}",
                "cache_identity": f"raw-cache-{index}",
                "raw_output_sha256": _digest(f"raw-output-{index}"),
                "final_diff_sha256": _digest(f"raw-diff-{index}"),
                "verification_sha256": _digest(f"raw-verification-{index}"),
                "metrics": copy.deepcopy(metrics),
            },
            "claude_code_with_omc": {
                "common_configuration": copy.deepcopy(common),
                "omc_enabled": True,
                "workspace_identity": f"omc-workspace-{index}",
                "session_identity": f"omc-session-{index}",
                "cache_identity": f"omc-cache-{index}",
                "raw_output_sha256": _digest(f"omc-output-{index}"),
                "final_diff_sha256": _digest(f"omc-diff-{index}"),
                "verification_sha256": _digest(f"omc-verification-{index}"),
                "metrics": copy.deepcopy(metrics),
            },
        },
    }
    _sign_arm(pair["arms"]["raw_claude_code"], private_key)
    _sign_arm(pair["arms"]["claude_code_with_omc"], private_key)
    _sign_pair(pair, private_key)
    return pair


def test_registration_is_separate_and_fail_closed() -> None:
    registration = _registration()
    assert subject.registration_errors(registration) == []
    assert registration["study_id"] == "claude-code-omc-incremental-value-20260908-v1"
    assert registration["state"] == "FEASIBILITY_CONTRACT_IMPLEMENTED_NOT_STARTED"
    assert registration["existing_persona_evidence_reused"] is False
    assert registration["execution_authorized"] is False
    assert registration["case_roster_state"] == "NOT_FROZEN"
    assert registration["case_roster"] == []
    assert registration["execution_authorization"] == subject.AUTHORIZATION_CONTRACT
    assert registration["ordered_rules"][:3] == [
        "registration_invalid:BLOCKED",
        "receipt_binding_invalid:BLOCKED",
        "fatal_violation:STOP",
    ]


def test_v1_registration_is_preserved_byte_for_byte() -> None:
    assert hashlib.sha256(REGISTRATION_PATH.read_bytes()).hexdigest() == (
        "2270070314953ec5dc6faf2220d1d8c7f95e44ddae135d6876dd906643a310fe"
    )


def test_v2_registration_freezes_three_stage_evidence_ladder() -> None:
    registration = _v2_registration()
    assert subject.evidence_ladder_registration_errors(registration) == []
    assert registration["study_id"] == (
        "claude-code-omc-incremental-value-20260908-v2"
    )
    assert registration["state"] == "EVIDENCE_LADDER_CONTRACT_IMPLEMENTED_NOT_STARTED"
    assert registration["execution_authorized"] is False
    assert registration["claim"] == "NO_SUPERIORITY_CLAIM"
    assert registration["evidence_reuse_between_stages"] is False
    assert registration["stages"]["feasibility"]["pairs"] == 10
    assert registration["stages"]["directional"]["pairs"] == 30
    assert registration["stages"]["directional"]["claim"] == (
        "NO_SUPERIORITY_CLAIM"
    )
    assert registration["stages"]["powered_confirmatory"]["execution_state"] == (
        "SEPARATE_APPROVAL_REQUIRED"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("evidence_reuse_between_stages",), True),
        (("stages", "directional", "claim"), "SUPERIOR"),
        (("stages", "powered_confirmatory", "automatic_entry"), True),
        (("stages", "feasibility", "fatal_incorrect_completion_maximum"), 1),
        (("stages", "directional", "adverse_completion_pairs_maximum"), 1),
    ],
)
def test_v2_registration_rejects_evidence_ladder_weakening(
    path: tuple[str, ...], value: object
) -> None:
    registration = _v2_registration()
    target = registration
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert subject.evidence_ladder_registration_errors(registration)


def test_registration_rejects_persona_identity_and_weak_baseline() -> None:
    registration = _registration()
    registration["study_id"] = "task-review-persona-effectiveness-20260904-v1"
    registration["arms"]["raw_claude_code"]["native_features"] = "disabled"
    errors = subject.registration_errors(registration)
    assert "study_id" in errors
    assert "arms.raw_claude_code.native_features" in errors


def test_registration_rejects_threshold_and_rule_mutation() -> None:
    registration = _registration()
    registration["confirmatory_thresholds"][
        "correction_required_reduction_minimum"
    ] = 0.01
    registration["ordered_rules"] = list(reversed(registration["ordered_rules"]))
    errors = subject.registration_errors(registration)
    assert "confirmatory_thresholds" in errors
    assert "ordered_rules" in errors


@pytest.mark.parametrize(
    "mutation", ["one_repository", "spoofed_digest", "unbalanced", "malformed"]
)
def test_execution_roster_requires_diversity_balance_and_shape(mutation: str) -> None:
    registration, _ = _execution_registration()
    roster = registration["case_roster"]
    if mutation == "one_repository":
        for entry in roster:
            entry["repository_identity"] = _repository_identity(0)
            entry["repository_identity_sha256"] = subject.repository_identity_sha256(
                entry["repository_identity"]
            )
    elif mutation == "spoofed_digest":
        roster[0]["repository_identity_sha256"] = _digest("spoofed-repository")
    elif mutation == "unbalanced":
        for entry in roster:
            entry["arm_order"] = list(subject.ARMS)
    else:
        roster[0]["arm_order"] = [[], list(subject.ARMS)]
    registration["case_roster_sha256"] = hashlib.sha256(
        _canonical_json(roster)
    ).hexdigest()

    errors = subject.registration_errors(
        registration,
        execution_ready=True,
        trusted_authorization_public_key=TRUSTED_AUTHORIZATION_PUBLIC_KEY,
    )
    assert "case_roster" in errors


def test_pair_rejects_configuration_drift_and_cross_arm_contamination() -> None:
    registration, private_key = _execution_registration()
    pair = _pair(1, private_key)
    pair["arms"]["claude_code_with_omc"]["common_configuration"][
        "model_id"
    ] = "different-model"
    pair["arms"]["claude_code_with_omc"]["cache_identity"] = pair["arms"][
        "raw_claude_code"
    ]["cache_identity"]
    errors = subject.pair_errors(
        registration,
        pair,
        trusted_authorization_public_key=TRUSTED_AUTHORIZATION_PUBLIC_KEY,
    )
    assert "arm_configuration_mismatch" in errors
    assert "cross_arm_contamination" in errors


def test_pair_rejects_omc_flag_and_receipt_digest_failures() -> None:
    registration, private_key = _execution_registration()
    pair = _pair(1, private_key)
    pair["arms"]["raw_claude_code"]["omc_enabled"] = True
    pair["arms"]["claude_code_with_omc"]["raw_output_sha256"] = "self-reported"
    errors = subject.pair_errors(
        registration,
        pair,
        trusted_authorization_public_key=TRUSTED_AUTHORIZATION_PUBLIC_KEY,
    )
    assert "raw_claude_code.omc_enabled" in errors
    assert "claude_code_with_omc.raw_output_sha256" in errors


def test_feasibility_requires_ten_unique_valid_pairs() -> None:
    registration, private_key = _execution_registration()
    result = _decide(
        registration, [_pair(i, private_key) for i in range(9)]
    )
    assert result == {
        "status": "INCONCLUSIVE",
        "reason": "insufficient_valid_pairs",
        "valid_pairs": 9,
        "claim": "NO_SUPERIORITY_CLAIM",
    }


def test_feasibility_pass_never_claims_superiority() -> None:
    registration, private_key = _execution_registration()
    result = _decide(
        registration, [_pair(i, private_key) for i in range(10)]
    )
    assert result == {
        "status": "FEASIBILITY_PASS",
        "reason": "measurement_contract_executable",
        "valid_pairs": 10,
        "claim": "NO_SUPERIORITY_CLAIM",
    }


def test_feasibility_prioritizes_fatal_and_binding_failures() -> None:
    registration, private_key = _execution_registration()
    fatal_pairs = [_pair(i, private_key) for i in range(10)]
    fatal_pairs[3]["fatal_violation"] = True
    _sign_pair(fatal_pairs[3], private_key)
    assert _decide(registration, fatal_pairs)["status"] == "STOP"

    invalid_pairs = [_pair(i, private_key) for i in range(10)]
    invalid_pairs[2]["arms"]["raw_claude_code"]["final_diff_sha256"] = "bad"
    result = _decide(registration, invalid_pairs)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "receipt_binding_invalid"


def test_feasibility_blocks_unapproved_execution_and_signature_tampering() -> None:
    _, private_key = _execution_registration()
    blocked = _decide(
        _registration(), [_pair(i, private_key) for i in range(10)]
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "execution_not_authorized"

    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pairs[0]["arms"]["raw_claude_code"]["metrics"]["input_tokens"] = 9999
    tampered = _decide(registration, pairs)
    assert tampered["status"] == "BLOCKED"
    assert tampered["reason"] == "receipt_binding_invalid"

    pairs = [_pair(i, private_key) for i in range(10)]
    pairs[0]["fatal_violation"] = True
    pairs[0]["pair_signature"] = "invalid"
    unsigned_semantics = _decide(registration, pairs)
    assert unsigned_semantics["status"] == "BLOCKED"
    assert unsigned_semantics["reason"] == "receipt_binding_invalid"


def test_execution_authorization_cannot_self_select_its_trust_anchor() -> None:
    registration, execution_private_key = _execution_registration()
    attacker_key = Ed25519PrivateKey.generate()
    attacker_public_key = _public_key_b64(attacker_key)
    receipt = registration["authorization_receipt"]
    receipt["signer_public_key"] = attacker_public_key
    receipt["signature"] = base64.b64encode(
        attacker_key.sign(
            subject.canonical_authorization_subject_bytes(receipt["subject"])
        )
    ).decode()
    registration["authorization_receipt_sha256"] = hashlib.sha256(
        subject.canonical_authorization_receipt_bytes(receipt)
    ).hexdigest()

    result = _decide(
        registration, [_pair(i, execution_private_key) for i in range(10)]
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "registration_invalid"


def test_pair_must_match_frozen_case_roster() -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pairs[0]["case_id"] = "post-selected-case"
    _sign_pair(pairs[0], private_key)
    result = _decide(registration, pairs)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "receipt_binding_invalid"

    pairs = [_pair(i, private_key) for i in range(10)]
    pairs[0]["arm_order"] = list(reversed(pairs[0]["arm_order"]))
    _sign_pair(pairs[0], private_key)
    result = _decide(registration, pairs)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "receipt_binding_invalid"


def test_pair_cannot_re_sign_common_configuration_after_authorization() -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pair = pairs[0]
    for arm in pair["arms"].values():
        arm["common_configuration"]["model_id"] = "post-authorization-model"
        _sign_arm(arm, private_key)
    _sign_pair(pair, private_key)

    result = _decide(registration, pairs)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "receipt_binding_invalid"


def test_execution_roster_requires_immutable_repository_identity() -> None:
    registration, _ = _execution_registration()
    roster = registration["case_roster"]
    for entry in roster:
        entry["repository_identity"] = _repository_identity(0)
        entry["repository_identity_sha256"] = subject.repository_identity_sha256(
            entry["repository_identity"]
        )
    registration["case_roster_sha256"] = hashlib.sha256(
        _canonical_json(roster)
    ).hexdigest()
    errors = subject.registration_errors(
        registration,
        execution_ready=True,
        trusted_authorization_public_key=TRUSTED_AUTHORIZATION_PUBLIC_KEY,
    )
    assert "case_roster" in errors


def test_signed_fatal_violation_precedes_environment_mismatch() -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pair = pairs[0]
    pair["fatal_violation"] = True
    pair["arms"]["claude_code_with_omc"]["common_configuration"][
        "model_id"
    ] = "different-model"
    _sign_arm(pair["arms"]["claude_code_with_omc"], private_key)
    _sign_pair(pair, private_key)

    result = _decide(registration, pairs)
    assert result["status"] == "STOP"
    assert result["reason"] == "fatal_violation"


def test_signed_metric_schema_failure_is_feasibility_fail() -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pair = pairs[0]
    del pair["arms"]["raw_claude_code"]["metrics"]["input_tokens"]
    _sign_arm(pair["arms"]["raw_claude_code"], private_key)
    _sign_pair(pair, private_key)

    result = _decide(registration, pairs)
    assert result["status"] == "FEASIBILITY_FAIL"
    assert result["reason"] == "metric_capture_incomplete"


def test_provider_absence_precedes_environment_mismatch() -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pair = pairs[0]
    pair["provider_execution_present"] = False
    pair["arms"]["claude_code_with_omc"]["common_configuration"][
        "model_id"
    ] = "different-model"
    _sign_arm(pair["arms"]["claude_code_with_omc"], private_key)
    _sign_pair(pair, private_key)

    result = _decide(registration, pairs)
    assert result["status"] == "INCONCLUSIVE"
    assert result["reason"] == "provider_execution_absent"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_sec", -1),
        ("verification_commands", []),
        ("repository_commit", "not-a-digest"),
        ("model_id", ""),
    ],
)
def test_invalid_frozen_configuration_cannot_pass(
    field: str, value: object
) -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pair = pairs[0]
    for arm in pair["arms"].values():
        arm["common_configuration"][field] = value
        _sign_arm(arm, private_key)
    _sign_pair(pair, private_key)

    result = _decide(registration, pairs)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "receipt_binding_invalid"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_metric_is_feasibility_fail(value: float) -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pair = pairs[0]
    pair["arms"]["raw_claude_code"]["metrics"]["estimated_cost"] = value
    _sign_arm(pair["arms"]["raw_claude_code"], private_key)
    _sign_pair(pair, private_key)

    result = _decide(registration, pairs)
    assert result["status"] == "FEASIBILITY_FAIL"
    assert result["reason"] == "metric_capture_incomplete"


@pytest.mark.parametrize(
    "metric",
    ["intervention_count", "approval_count", "input_tokens", "output_tokens"],
)
def test_fractional_count_metric_is_feasibility_fail(metric: str) -> None:
    registration, private_key = _execution_registration()
    pairs = [_pair(i, private_key) for i in range(10)]
    pair = pairs[0]
    pair["arms"]["raw_claude_code"]["metrics"][metric] = 1.5
    _sign_arm(pair["arms"]["raw_claude_code"], private_key)
    _sign_pair(pair, private_key)

    result = _decide(registration, pairs)
    assert result["status"] == "FEASIBILITY_FAIL"
    assert result["reason"] == "metric_capture_incomplete"


def test_loader_never_reads_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    replacement = tmp_path / "replacement.json"
    source.write_text('{"source": true}', encoding="utf-8")
    replacement.write_text('{"replacement": true}', encoding="utf-8")
    original_lstat = subject.os.lstat

    def replace_after_lstat(path: Path) -> object:
        info = original_lstat(path)
        source.unlink()
        source.symlink_to(replacement)
        return info

    monkeypatch.setattr(subject.os, "lstat", replace_after_lstat)
    assert subject.load_regular_json(source) != {"replacement": True}


def test_loader_rejects_non_standard_json_number(tmp_path: Path) -> None:
    source = tmp_path / "nan.json"
    source.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="json_constant_invalid"):
        subject.load_regular_json(source)


def test_cli_validates_registration_without_authorizing_execution() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc_claude_incremental_value.py"),
            "validate-registration",
            "--registration",
            str(REGISTRATION_PATH),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["execution_authorized"] is False


def test_cli_validates_v2_ladder_without_authorizing_execution() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc_claude_incremental_value.py"),
            "validate-registration",
            "--registration",
            str(V2_REGISTRATION_PATH),
            "--previous-registration",
            str(REGISTRATION_PATH),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "errors": [],
        "execution_authorized": False,
        "valid": True,
    }


def test_cli_validates_v2_ladder_outside_repository_cwd(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc_claude_incremental_value.py"),
            "validate-registration",
            "--registration",
            str(V2_REGISTRATION_PATH),
            "--previous-registration",
            str(REGISTRATION_PATH),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["valid"] is True


def test_cli_v2_requires_actual_predecessor() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc_claude_incremental_value.py"),
            "validate-registration",
            "--registration",
            str(V2_REGISTRATION_PATH),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["errors"] == ["previous_registration_required"]


def test_cli_v2_rejects_tampered_predecessor(tmp_path: Path) -> None:
    predecessor = tmp_path / "v1.json"
    predecessor.write_bytes(REGISTRATION_PATH.read_bytes() + b"\n")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc_claude_incremental_value.py"),
            "validate-registration",
            "--registration",
            str(V2_REGISTRATION_PATH),
            "--previous-registration",
            str(predecessor),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "previous_registration.sha256" in json.loads(result.stdout)["errors"]


def test_cli_v2_rejects_symlink_predecessor(tmp_path: Path) -> None:
    predecessor = tmp_path / "v1.json"
    predecessor.symlink_to(REGISTRATION_PATH)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc_claude_incremental_value.py"),
            "validate-registration",
            "--registration",
            str(V2_REGISTRATION_PATH),
            "--previous-registration",
            str(predecessor),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "input_not_regular_file"


def test_cli_rejects_symlink_registration(tmp_path: Path) -> None:
    link = tmp_path / "registration.json"
    link.symlink_to(REGISTRATION_PATH)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "omc_claude_incremental_value.py"),
            "validate-registration",
            "--registration",
            str(link),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "input_not_regular_file"


def test_runbook_and_current_docs_preserve_claim_boundaries() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    roadmap = ROADMAP_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")
    for text in (runbook, roadmap, readme):
        assert "claude-code-omc-incremental-value-20260908-v2" in text
        assert "claude-code-omc-incremental-value-20260908-v1" in text
        assert "EVIDENCE_LADDER_CONTRACT_IMPLEMENTED_NOT_STARTED" in text
        assert "NO_SUPERIORITY_CLAIM" in text
        assert "raw_claude_code" in text
        assert "claude_code_with_omc" in text
    assert "기존 Codex Persona Pilot evidence를 재사용하지 않는다" in runbook
    assert "실제 Claude Code 실행은 별도 승인 전 금지" in runbook
    assert "trusted Ed25519 execution public key" in runbook
    assert "pair envelope도 별도로 서명" in runbook
    assert subject.AUTHORIZATION_KEY_ENV in runbook
    assert "10쌍 Stage F" in roadmap
    assert "30쌍 Stage D" in roadmap
    assert "별도 승인" in roadmap
