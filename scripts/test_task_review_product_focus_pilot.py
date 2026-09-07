import hashlib
import json
from pathlib import Path

import omc_task_review_pilot


CONTRACT_PATH = Path("docs/task_review_product_focus_pilot.md")
PERSONA_STUDY_PATH = Path(
    "docs/task_review_persona_effectiveness_preregistration_v1.json"
)
PERSONA_RUNBOOK_PATH = Path("docs/task_review_persona_operator_runbook.md")


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_contract_freezes_selection_window_and_common_inputs() -> None:
    text = _contract()

    assert "T0" in text
    assert "최대 7일" in text
    assert "chronological first eligible 3" in text
    assert "최소 2개 저장소" in text
    assert "교체하지 않는다" in text
    for field in (
        "request",
        "base commit",
        "DoD",
        "verification",
        "provider",
        "model",
        "reasoning",
        "timeout",
    ):
        assert field in text


def test_contract_defines_symmetric_isolated_arms() -> None:
    text = _contract()

    assert "$omc-task" in text
    assert "$omc-review" in text
    assert "native Codex review" in text
    assert "OMC skill/state injection 없이" in text
    assert "arm별 재시도 1회" in text
    assert "case 1·3은 OMC 먼저" in text
    assert "case 2는 Baseline 먼저" in text
    assert "상대 arm의 출력" in text
    assert "별도 격리 clone" in text


def test_contract_defines_metrics_and_terminal_outcomes() -> None:
    text = _contract()

    for requirement in (
        "completion",
        "end-to-end wall-clock",
        "workflow time",
        "user intervention",
        "rework",
        "fatal violation",
        "INCONCLUSIVE",
        "CONTINUE",
        "REDUCE",
        "STOP",
    ):
        assert requirement in text

    assert "materialization부터 terminal receipt" in text
    assert "실제 사용자 응답 turn" in text
    assert "첫 구현 응답 이후" in text
    assert "통계적 우월성" in text
    assert "자동 적용하지 않는다" in text
    assert "`APPROVE` 또는 `APPROVE WITH NOTES`" in text
    assert "case별 최대 3회" in text
    assert "3회를 초과하면 `REDUCE`" in text


def test_contract_keeps_execution_and_adoption_human_gated() -> None:
    text = _contract()

    assert "pilot-start receipt" in text
    assert "사용자 승인" in text
    assert "선택한 arm만" in text
    assert "별도 작업" in text
    assert "provider 실행은 이 문서 작성 범위에 포함하지 않는다" in text


def test_contract_names_existing_preflight_and_verdict_surfaces() -> None:
    text = _contract()

    assert "omc_task_review_pilot.py" in text
    assert "session state stream" in text
    assert "native review adapter" in text
    assert "preflight" in text


def test_contract_freezes_roster_identity_t0_and_readiness_before_execution() -> None:
    text = _contract()

    for requirement in (
        "task_review_pilot_start",
        "consumed_at",
        "canonical `origin`",
        "root commit",
        "checkpoint",
        "inventory-dry-run",
        "classification_review_required",
        "terminal cursor",
        "provider call 수는 항상 0",
        "PILOT_READY",
        "STOP_ELIGIBILITY_DIVERSITY",
    ):
        assert requirement in text


def test_contract_labels_v2_as_waiting_until_a_readiness_receipt_exists() -> None:
    text = _contract()

    assert "`WAITING_FOR_CASES`" in text
    assert "readiness receipt가 아직 없으므로 `PILOT_READY`가 아니다" in text


def test_v2_is_archival_only_and_cannot_seed_the_fresh_study() -> None:
    text = _contract()
    study = json.loads(PERSONA_STUDY_PATH.read_text(encoding="utf-8"))

    assert "`ARCHIVED_INCOMPLETE`" in text
    assert "v2의 T0·roster·inventory·readiness·receipt를 승계하지 않는다" in text
    assert study["study_id"] == "task-review-persona-effectiveness-20260904-v1"
    assert study["status"] == "approved_not_started"
    assert study["predecessor"]["pilot_id"] == "task-review-pilot-v2"
    assert study["predecessor"]["terminal_status"] == "ARCHIVED_INCOMPLETE"
    assert study["predecessor"]["bindings_reusable"] is False


def test_fresh_persona_study_freezes_the_external_executor_and_key_custody() -> None:
    study = json.loads(PERSONA_STUDY_PATH.read_text(encoding="utf-8"))

    assert study["execution"]["mode"] == "external_codex_manual_receipt"
    assert study["execution"]["adapter_development_allowed"] is False
    assert study["execution"]["provider_execution_in_repository"] is False
    assert study["execution"]["required_environment"] == [
        "OMC_TASK_REVIEW_PILOT_TRUSTED_EXECUTION_PUBLIC_KEY",
        "OMC_TASK_REVIEW_PILOT_TRUSTED_RECONCILIATION_PUBLIC_KEY",
        "OMC_TASK_REVIEW_PERSONA_TRUSTED_ADJUDICATION_PUBLIC_KEY",
        "OMC_TASK_REVIEW_PERSONA_TRUSTED_STUDY_PUBLIC_KEY",
    ]
    assert study["fresh_bindings"] == [
        "signed_study_registration",
        "canonical_repository_roster",
        "append_only_case_enrollments",
        "state_evidence_cursor_chain",
        "execution_authority",
        "reconciliation_authority",
        "blind_adjudication_authority",
    ]
    assert study["key_custody"]["distinct_role_keys_required"] is True
    assert study["key_custody"]["private_keys_in_repository_allowed"] is False


def test_persona_effectiveness_metric_requires_ten_pairs_and_real_corrections() -> None:
    study = json.loads(PERSONA_STUDY_PATH.read_text(encoding="utf-8"))
    design = study["design"]
    decision = study["decision"]

    assert design["persona"] == "solo_saas_founder_multi_repository"
    assert design["pair_count"] == 10
    assert design["arms"] == ["direct_codex", "omc_persona"]
    assert "timeout_sec" in design["same_case_inputs"]
    assert "timeout" not in design["same_case_inputs"]
    assert design["blind_adjudication"] is True
    assert design["arm_specific_treatment"] == {
        "direct_codex": {"persona_contract": None},
        "omc_persona": {
            "persona_contract_sha256": omc_task_review_pilot.PERSONA_CONTRACT_SHA256
        },
    }
    assert design["primary_metric"] == "blind_correction_required_case_rate"
    assert decision["minimum_relative_reduction"] == 0.30
    assert decision["minimum_baseline_correction_events"] == 3
    assert "post-implementation instruction" in decision["correction_event_definition"]
    assert decision["correction_rate_denominator"] == "ten_paired_cases_per_arm"
    assert "SHA-256" in decision["correction_evidence_binding"]
    assert "O_NOFOLLOW" in decision["correction_evidence_binding"]
    assert decision["completion_non_inferiority_required"] is True
    assert decision["verification_non_inferiority_required"] is True
    assert decision["total_human_intervention_non_inferiority_required"] is True
    assert decision["median_wall_clock_noninferiority_ratio"] == 1.15
    assert decision["missing_or_invalid_receipt_outcome"] == "blocked"
    assert decision["operational_preflight_error_outcome"] == "blocked"
    assert decision["provider_execution_absent_outcome"] == "INCONCLUSIVE"
    assert decision["decision_precedence"] == [
        "fatal_violation",
        "provider_execution_absent",
        "blinding_failed",
        "completion_noninferiority",
        "verification_noninferiority",
        "total_human_intervention_noninferiority",
        "median_wall_clock_noninferiority",
        "blind_quality_noninferiority",
        "minimum_baseline_correction_events",
        "minimum_relative_reduction",
    ]


def test_persona_registration_contract_matches_preregistration_ssot() -> None:
    study = json.loads(PERSONA_STUDY_PATH.read_text(encoding="utf-8"))
    decision = study["decision"]

    assert omc_task_review_pilot.PERSONA_PREREGISTRATION_SHA256 == hashlib.sha256(
        PERSONA_STUDY_PATH.read_bytes()
    ).hexdigest()
    assert omc_task_review_pilot.PERSONA_CONTRACT_REVISION == study["contract_revision"]
    assert omc_task_review_pilot.PERSONA_CONTRACT_SHA256 == hashlib.sha256(
        json.dumps(
            study["persona_contract"],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert (
        omc_task_review_pilot.PERSONA_MINIMUM_RELATIVE_REDUCTION
        == study["decision"]["minimum_relative_reduction"]
    )
    assert (
        omc_task_review_pilot.PERSONA_MINIMUM_BASELINE_CORRECTION_EVENTS
        == study["decision"]["minimum_baseline_correction_events"]
    )
    assert study["contract_revision"] == 6
    assert study["pre_t0_rehearsal"]["required"] is True
    assert study["inconclusive_followup_policy"]["same_study_extension_allowed"] is False
    assert study["amendment"]["measurement_source_changed"] is True
    assert study["amendment"]["schema_changed"] is True
    assert study["amendment"]["outcome_changed"] is True
    assert study["amendment"]["semantic_threshold_changed"] is False
    assert study["amendment"]["previous_document_sha256"] == (
        "909e4ee3c0657b204871534efdeab0ded7b04dc432cee092eaa28f3e4f35b86d"
    )
    assert decision["ordered_rules"] == [
        {"condition": rule["condition"], "outcome": rule["outcome"]}
        for rule in omc_task_review_pilot.PERSONA_DECISION_RULES
    ]


def test_persona_operator_runbook_exposes_signing_and_reverification_surfaces() -> None:
    text = PERSONA_RUNBOOK_PATH.read_text(encoding="utf-8")
    help_text = omc_task_review_pilot._parser().format_help()

    assert "persona-signing-payload" in text
    assert "persona-verify-decision" in text
    assert "persona-prepare-blind-evaluation" in text
    assert "omc-task-review-persona-adjudication/v4" in text
    assert "private key와 decoded payload는 저장소에 저장하지 않는다" in text
    assert "registration, mapping, enrollment, terminal, adjudication" in text
    assert "persona-signing-payload" in help_text
    assert "persona-verify-decision" in help_text
    assert "persona-prepare-blind-evaluation" in help_text


def test_persona_study_preregisters_mapping_before_execution() -> None:
    study = json.loads(PERSONA_STUDY_PATH.read_text(encoding="utf-8"))
    execution = study["execution"]
    common = execution["common_sequence"]
    completion = execution["complete_decision_path"]
    shortfall = execution["deadline_shortfall_path"]

    assert execution["receipt_verifiers"] == [
        "scripts/omc_task_review_pilot.py persona-decision",
        "scripts/omc_task_review_pilot.py persona-collection-close",
    ]
    assert "required_sequence" not in execution
    assert common == [
        "excluded_synthetic_protocol_rehearsal",
        "signed_anonymous_arm_mapping",
        "signed_study_registration_and_t0",
        "case_enrollment_with_state_evidence",
        "persona_freeze_case",
        "persona_paired_dry_run",
        "external_codex_execution",
        "signed_execution_receipt",
        "arm_receipt",
        "terminal_receipt",
    ]
    assert completion == [
        "repeat_enrollment_through_case_ten",
        "blind_adjudication",
        "persona_decision",
    ]
    assert shortfall == [
        "deadline_reached_before_case_ten",
        "signed_collection_close",
        "inconclusive_decision",
    ]
    proof = study["design"]["preregistration_proof"]
    assert "co-signed registration" in proof
    assert "21-day deadline" in proof
    assert "previous enrollment" in proof
    assert "state-evidence cursor" in proof
