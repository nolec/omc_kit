from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_decision_policy_feasibility as feasibility


REVIEWER_KEY = Ed25519PrivateKey.generate()
APPROVER_KEY = Ed25519PrivateKey.generate()
PREREGISTRATION_KEY = Ed25519PrivateKey.generate()
RUNNER_KEY = Ed25519PrivateKey.generate()
ADJUDICATOR_KEY = Ed25519PrivateKey.generate()
POLICY_AUTHOR_KEY = Ed25519PrivateKey.generate()


def _resign_preregistration(packet: dict[str, object]) -> dict[str, object]:
    unsigned = deepcopy(packet)
    unsigned.pop("preregistration_sha256", None)
    unsigned.pop("signature", None)
    unsigned.pop("signer_public_key", None)
    return feasibility._sign_receipt(
        unsigned,
        private_key=PREREGISTRATION_KEY,
        hash_field="preregistration_sha256",
    )


def _resign_execution(receipt: dict[str, object]) -> dict[str, object]:
    unsigned = deepcopy(receipt)
    unsigned.pop("receipt_sha256", None)
    unsigned.pop("signature", None)
    return feasibility._sign_receipt(
        unsigned, private_key=RUNNER_KEY, hash_field="receipt_sha256"
    )


def _write_case(root: Path, index: int) -> dict[str, object]:
    instruction = f"승인된 완료 조건을 충족한 뒤 작업 {index}를 종료한다."
    result = {
        "schema_version": "omc-decision-policy-source-result/v1",
        "run_id": f"run-{index}",
        "status": "hold",
        "instruction": instruction,
        "base_commit": f"{index:x}" * 40,
        "source_tree": f"{index + 5:x}" * 40,
    }
    result_path = root / "runs" / f"run-{index}" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    receipt = feasibility.build_causal_review_receipt(
        run_id=result["run_id"],
        result_sha256=feasibility.file_sha256(result_path),
        observed_failure="validation_loop",
        reviewer_id="independent-causal-reviewer-v1",
        reviewed_at=f"2026-09-02T05:{index:02d}:00Z",
        private_key=REVIEWER_KEY,
    )
    receipt_path = root / "causal" / f"case-{index}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    return {
        "case_id": f"case-{index}",
        "result_path": result_path.relative_to(root).as_posix(),
        "causal_receipt_path": receipt_path.relative_to(root).as_posix(),
        "evidence_summary": "승인된 종료 기준 이후 검증을 반복했다.",
    }


def _corpus(root: Path) -> dict[str, object]:
    return feasibility.build_failure_corpus(
        evidence_root=root,
        study_id="decision-policy-feasibility-v1",
        created_at="2026-09-02T05:30:00Z",
        trusted_causal_reviewer_public_key=feasibility.public_key_b64(REVIEWER_KEY),
        cases=[_write_case(root, index) for index in range(1, 6)],
    )


def _reviewer_public_key() -> str:
    return feasibility.public_key_b64(REVIEWER_KEY)


def _approver_public_key() -> str:
    return feasibility.public_key_b64(APPROVER_KEY)


def _policies() -> list[dict[str, object]]:
    return [
        {
            "case_id": f"case-{index}",
            "decision_priorities": ["승인된 완료 조건을 먼저 충족한다."],
            "tradeoff_policy": ["추가 검증보다 명시된 산출물 완성을 우선한다."],
            "evidence_boundary": ["요청과 동결된 저장소 문맥만 사용한다."],
            "stop_conditions": ["완료 조건과 필수 검증이 충족되면 종료한다."],
        }
        for index in range(1, 6)
    ]


def _policy_packet(root: Path, corpus: dict[str, object]) -> dict[str, object]:
    policies = _policies()
    receipt = feasibility.build_policy_approval_receipt(
        corpus=corpus,
        authored_at="2026-09-02T05:40:00Z",
        author_id="independent-policy-author-v1",
        policies=policies,
        approver_id="user-approver-v1",
        approved_at="2026-09-02T05:45:00Z",
        private_key=APPROVER_KEY,
    )
    return feasibility.build_policy_packet(
        evidence_root=root,
        corpus=corpus,
        authored_at="2026-09-02T05:40:00Z",
        author_id="independent-policy-author-v1",
        policies=policies,
        trusted_approver_public_key=_approver_public_key(),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        approval_receipt=receipt,
    )


def _subjects(corpus: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "case_id": case["case_id"],
            "request_sha256": case["request_sha256"],
            "base_commit": case["base_commit"],
            "source_tree": case["source_tree"],
            "runner_sha256": "a" * 64,
            "adapter_sha256": "b" * 64,
            "tool_contract_sha256": "c" * 64,
        }
        for case in corpus["cases"]
    ]


def _execution_order() -> list[str]:
    order = []
    for index in range(1, 6):
        arms = ("baseline", "policy") if index % 2 else ("policy", "baseline")
        order.extend(f"case-{index}:{arm}" for arm in arms)
    return order


def _adjudications(
    receipts: list[dict[str, object]], paired_packet: dict[str, object]
) -> list[dict[str, object]]:
    by_case = {
        (receipt["case_id"], receipt["arm"]): receipt for receipt in receipts
    }
    return [
        feasibility.build_blind_adjudication_receipt(
            paired_packet_sha256=paired_packet["paired_packet_sha256"],
            case_id=f"case-{index}",
            baseline_execution_receipt_sha256=by_case[(f"case-{index}", "baseline")]["receipt_sha256"],
            policy_execution_receipt_sha256=by_case[(f"case-{index}", "policy")]["receipt_sha256"],
            winner="tie",
            major_quality_loss=False,
            adjudicator_id="independent-blind-adjudicator-v1",
            adjudicated_at=f"2026-09-03T02:{index:02d}:00Z",
            private_key=ADJUDICATOR_KEY,
        )
        for index in range(1, 6)
    ]


def test_failure_corpus_binds_real_results_and_signed_causal_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _corpus(tmp_path)

    with monkeypatch.context() as patch:
        patch.setattr(
            feasibility,
            "file_sha256",
            lambda _path: (_ for _ in ()).throw(AssertionError("artifact_reopened")),
        )
        assert feasibility.validate_failure_corpus(
            corpus,
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        ) == corpus
    assert corpus["case_count"] == 5

    result_path = tmp_path / corpus["cases"][0]["result_path"]
    result_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="result_digest_mismatch"):
        feasibility.validate_failure_corpus(
            corpus,
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )

    symlink_root = tmp_path / "symlink-parent"
    real_dir = symlink_root / "real"
    real_dir.mkdir(parents=True)
    (real_dir / "result.json").write_text("{}", encoding="utf-8")
    (symlink_root / "alias").symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="result_path_untrusted"):
        feasibility._load_json_artifact(
            symlink_root, "alias/result.json", "result_path"
        )

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * 2_000_001)
    with pytest.raises(ValueError, match="result_path_too_large"):
        feasibility._load_json_artifact(tmp_path, "oversized.json", "result_path")

    self_trusted = _corpus(tmp_path / "self-trusted")
    self_trusted["trusted_causal_reviewer_public_key"] = feasibility.public_key_b64(
        Ed25519PrivateKey.generate()
    )
    self_trusted["corpus_sha256"] = feasibility.canonical_sha256(
        {key: value for key, value in self_trusted.items() if key != "corpus_sha256"}
    )
    with pytest.raises(ValueError, match="causal_reviewer_trust_root_mismatch"):
        feasibility.validate_failure_corpus(
            self_trusted,
            evidence_root=tmp_path / "self-trusted",
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )

    postdated = _corpus(tmp_path / "postdated")
    postdated["created_at"] = "2026-09-02T05:00:00Z"
    postdated["corpus_sha256"] = feasibility.canonical_sha256(
        {key: value for key, value in postdated.items() if key != "corpus_sha256"}
    )
    with pytest.raises(ValueError, match="causal_review_after_corpus_freeze"):
        feasibility.validate_failure_corpus(
            postdated,
            evidence_root=tmp_path / "postdated",
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )


def test_diagnosis_does_not_trust_self_asserted_result_field(tmp_path: Path) -> None:
    result = {
        "schema_version": "omc-decision-policy-source-result/v1",
        "run_id": "run-policy",
        "status": "hold",
        "instruction": "작업",
        "base_commit": "c" * 40,
        "source_tree": "d" * 40,
        "decision_policy_failure": "validation_loop",
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")

    report = feasibility.diagnose_candidate_artifacts(
        [path],
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
    )

    assert report["eligible_count"] == 0
    assert report["excluded"][0]["reason"] == "causal_receipt_missing"

    receipt = feasibility.build_causal_review_receipt(
        run_id="run-policy",
        result_sha256=feasibility.file_sha256(path),
        observed_failure="validation_loop",
        reviewer_id="independent-causal-reviewer-v1",
        reviewed_at="2026-09-02T05:10:00Z",
        private_key=REVIEWER_KEY,
    )
    (tmp_path / "causal-review.json").write_text(
        json.dumps(receipt, sort_keys=True), encoding="utf-8"
    )
    report = feasibility.diagnose_candidate_artifacts(
        [path],
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
    )
    assert report["eligible_count"] == 1
    assert report["status"] == "COLLECTING"

    duplicate_report = feasibility.diagnose_candidate_artifacts(
        [path] * 5,
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
    )
    assert duplicate_report["eligible_count"] == 1
    assert duplicate_report["status"] == "COLLECTING"
    assert [item["reason"] for item in duplicate_report["excluded"]] == [
        "duplicate_result"
    ] * 4

    untrusted = feasibility.diagnose_candidate_artifacts(
        [path],
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=feasibility.public_key_b64(
            Ed25519PrivateKey.generate()
        ),
    )
    assert untrusted["eligible_count"] == 0
    assert untrusted["excluded"][0]["reason"] == "causal_receipt_untrusted"


def test_policy_packet_requires_signed_approval_and_aware_chronology(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    packet = _policy_packet(tmp_path, corpus)

    assert feasibility.validate_policy_packet(
        packet,
        corpus=corpus,
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
        execution_not_before="2026-09-02T06:00:00Z",
    ) == packet

    self_trusted = deepcopy(packet)
    self_trusted["trusted_approver_public_key"] = feasibility.public_key_b64(
        Ed25519PrivateKey.generate()
    )
    self_trusted["policy_packet_sha256"] = feasibility.canonical_sha256(
        {
            key: value
            for key, value in self_trusted.items()
            if key != "policy_packet_sha256"
        }
    )
    with pytest.raises(ValueError, match="policy_approver_trust_root_mismatch"):
        feasibility.validate_policy_packet(
            self_trusted,
            corpus=corpus,
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
            trusted_approver_public_key=_approver_public_key(),
            execution_not_before="2026-09-02T06:00:00Z",
        )

    with pytest.raises(ValueError, match="authored_at_timezone_required"):
        feasibility.build_policy_approval_receipt(
            corpus=corpus,
            authored_at="2026-09-02T05:40:00",
            author_id="independent-policy-author-v1",
            policies=_policies(),
            approver_id="user-approver-v1",
            approved_at="2026-09-02T05:45:00Z",
            private_key=APPROVER_KEY,
        )

    early_policies = _policies()
    early_receipt = feasibility.build_policy_approval_receipt(
        corpus=corpus,
        authored_at="2026-09-02T05:20:00Z",
        author_id="independent-policy-author-v1",
        policies=early_policies,
        approver_id="user-approver-v1",
        approved_at="2026-09-02T05:45:00Z",
        private_key=APPROVER_KEY,
    )
    with pytest.raises(ValueError, match="policy_authored_before_corpus_freeze"):
        feasibility.build_policy_packet(
            evidence_root=tmp_path,
            corpus=corpus,
            authored_at="2026-09-02T05:20:00Z",
            author_id="independent-policy-author-v1",
            policies=early_policies,
            trusted_approver_public_key=_approver_public_key(),
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
            approval_receipt=early_receipt,
        )


def test_paired_packet_binds_case_inputs_and_rejects_arm_drift(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    policies = _policy_packet(tmp_path, corpus)
    packet = feasibility.build_paired_packet(
        evidence_root=tmp_path,
        corpus=corpus,
        policy_packet=policies,
        provider="codex-subscription",
        model="gpt-5.5",
        reasoning="high",
        timeout_sec=1200,
        execution_not_before="2026-09-02T06:00:00Z",
        execution_order=_execution_order(),
        execution_subjects=_subjects(corpus),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )

    assert feasibility.validate_paired_packet(
        packet,
        corpus=corpus,
        policy_packet=policies,
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    ) == packet

    invalid = deepcopy(packet)
    invalid["case_subjects"][0]["policy"]["runner_sha256"] = "f" * 64
    invalid["paired_packet_sha256"] = feasibility.canonical_sha256(
        {key: value for key, value in invalid.items() if key != "paired_packet_sha256"}
    )
    with pytest.raises(ValueError, match="paired_subject_mismatch"):
        feasibility.validate_paired_packet(
            invalid,
            corpus=corpus,
            policy_packet=policies,
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
            trusted_approver_public_key=_approver_public_key(),
        )

    unknown = deepcopy(packet)
    unknown["case_subjects"][0]["case_id"] = "unknown-case"
    unknown["case_subjects"][0]["baseline"]["case_id"] = "unknown-case"
    unknown["case_subjects"][0]["policy"]["case_id"] = "unknown-case"
    unknown["paired_packet_sha256"] = feasibility.canonical_sha256(
        {key: value for key, value in unknown.items() if key != "paired_packet_sha256"}
    )
    with pytest.raises(ValueError, match="paired_subject_unknown_case"):
        feasibility.validate_paired_packet(
            unknown,
            corpus=corpus,
            policy_packet=policies,
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
            trusted_approver_public_key=_approver_public_key(),
        )


def _authorities() -> list[dict[str, str]]:
    roles = [
        "preregistration_signer",
        "causal_reviewer",
        "policy_approver",
        "runner_operator",
        "blind_adjudicator",
    ]
    keys = {
        "preregistration_signer": PREREGISTRATION_KEY,
        "causal_reviewer": REVIEWER_KEY,
        "policy_approver": APPROVER_KEY,
        "runner_operator": RUNNER_KEY,
        "blind_adjudicator": ADJUDICATOR_KEY,
    }
    return [
        {
            "role": role,
            "operator_id": f"operator-{index}",
            "custody_id": f"custody-{index}",
            "public_key": feasibility.public_key_b64(keys[role]),
        }
        for index, role in enumerate(roles, start=1)
    ]


def _preregistration() -> dict[str, object]:
    return feasibility.build_prospective_preregistration(
        study_id="decision-policy-prospective-v1",
        registered_at="2026-09-02T04:00:00Z",
        observation_start="2026-09-02T05:00:00Z",
        observation_end="2026-09-02T06:00:00Z",
        source_repositories=["consumer-a", "consumer-b"],
        failure_taxonomy=sorted(feasibility.OBSERVED_FAILURES),
        exclusion_rules=["non-implementation", "missing-source-result"],
        authorities=_authorities(),
        private_key=PREREGISTRATION_KEY,
    )


def test_preregistration_freezes_selection_metrics_authorities_and_claim() -> None:
    packet = _preregistration()
    trusted_key = feasibility.public_key_b64(PREREGISTRATION_KEY)
    assert feasibility.validate_prospective_preregistration(
        packet, trusted_preregistration_public_key=trusted_key
    ) == packet
    assert packet["execution_policy"] == {
        "arm_order": "balanced_alternating",
        "fresh_session_per_arm": True,
        "shared_context_forbidden": True,
        "identical_provider_controls": True,
    }

    late = deepcopy(packet)
    late["registered_at"] = late["observation_start"]
    late = _resign_preregistration(late)
    with pytest.raises(ValueError, match="preregistration_chronology_invalid"):
        feasibility.validate_prospective_preregistration(
            late, trusted_preregistration_public_key=trusted_key
        )

    reused = deepcopy(packet)
    reused["authorities"][1]["public_key"] = reused["authorities"][0]["public_key"]
    reused = _resign_preregistration(reused)
    with pytest.raises(ValueError, match="authority_not_independent"):
        feasibility.validate_prospective_preregistration(
            reused, trusted_preregistration_public_key=trusted_key
        )

    malformed = _authorities()
    malformed[-1]["public_key"] = "not-an-ed25519-key"
    with pytest.raises(ValueError, match="authority_public_key_invalid"):
        feasibility.build_prospective_preregistration(
            study_id="decision-policy-malformed-authority",
            registered_at="2026-09-03T00:00:00Z",
            observation_start="2026-09-03T01:00:00Z",
            observation_end="2026-09-10T01:00:00Z",
            source_repositories=["consumer-a"],
            failure_taxonomy=sorted(feasibility.OBSERVED_FAILURES),
            exclusion_rules=["non-implementation"],
            authorities=malformed,
            private_key=PREREGISTRATION_KEY,
        )


def test_candidate_inventory_is_append_only_and_selects_first_five(tmp_path: Path) -> None:
    preregistration = _preregistration()
    sources = [_write_case(tmp_path, index) for index in range(1, 7)]
    excluded_source = sources[1]
    exclusion_receipt = feasibility.build_exclusion_review_receipt(
        candidate_id="case-2",
        run_id="run-2",
        result_sha256=feasibility.file_sha256(tmp_path / excluded_source["result_path"]),
        exclusion_reason="non-implementation",
        reviewer_id="independent-causal-reviewer-v1",
        reviewed_at="2026-09-02T05:02:00Z",
        private_key=REVIEWER_KEY,
    )
    exclusion_path = tmp_path / "exclusions" / "case-2.json"
    exclusion_path.parent.mkdir(parents=True)
    exclusion_path.write_text(json.dumps(exclusion_receipt, sort_keys=True), encoding="utf-8")
    entries = []
    for index, source in enumerate(sources, start=1):
        entry = {
            "candidate_id": f"case-{index}",
            "repository_id": "consumer-a" if index % 2 else "consumer-b",
            "observed_at": f"2026-09-02T05:{index:02d}:00Z",
            "result_path": source["result_path"],
            "causal_receipt_path": source["causal_receipt_path"],
            "exclusion_reason": "non-implementation" if index == 2 else None,
        }
        if index == 2:
            entry["exclusion_receipt_path"] = exclusion_path.relative_to(tmp_path).as_posix()
        entries.append(entry)
    forged = [
        {
            "candidate_id": "case-forged",
            "run_id": "run-forged",
            "repository_id": "consumer-a",
            "observed_at": "2026-09-03T01:00:00Z",
            "failure_type": "validation_loop",
            "eligibility": "eligible",
            "reason": "causal-review-confirmed",
            "result_sha256": "a" * 64,
        }
    ]
    with pytest.raises(ValueError, match="inventory_evidence_descriptor_missing"):
        feasibility.build_candidate_inventory(
            preregistration=preregistration,
            entries=forged,
            trusted_preregistration_public_key=feasibility.public_key_b64(
                PREREGISTRATION_KEY
            ),
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )

    out_of_order = deepcopy(entries)
    out_of_order[0], out_of_order[-1] = out_of_order[-1], out_of_order[0]
    with pytest.raises(ValueError, match="inventory_chronology_invalid"):
        feasibility.build_candidate_inventory(
            preregistration=preregistration,
            entries=out_of_order,
            trusted_preregistration_public_key=feasibility.public_key_b64(
                PREREGISTRATION_KEY
            ),
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )
    inventory = feasibility.build_candidate_inventory(
        preregistration=preregistration,
        entries=entries,
        trusted_preregistration_public_key=feasibility.public_key_b64(
            PREREGISTRATION_KEY
        ),
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
    )
    validated = feasibility.validate_candidate_inventory(
        inventory,
        preregistration=preregistration,
        trusted_preregistration_public_key=feasibility.public_key_b64(
            PREREGISTRATION_KEY
        ),
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
    )
    assert validated["selected_candidate_ids"] == [
        "case-1",
        "case-3",
        "case-4",
        "case-5",
        "case-6",
    ]

    skipped = deepcopy(inventory)
    skipped["entries"][2]["sequence"] = 4
    with pytest.raises(ValueError, match="inventory_sequence_invalid"):
        feasibility.validate_candidate_inventory(
            skipped,
            preregistration=preregistration,
            trusted_preregistration_public_key=feasibility.public_key_b64(
                PREREGISTRATION_KEY
            ),
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )


def test_deterministic_verdict_applies_noninferiority_and_safety_veto(tmp_path: Path) -> None:
    preregistration = _preregistration()
    corpus = _corpus(tmp_path)
    policy_packet = _policy_packet(tmp_path, corpus)
    inventory_entries = []
    for index, case in enumerate(corpus["cases"], start=1):
        inventory_entries.append(
            {
                "candidate_id": case["case_id"],
                "repository_id": "consumer-a" if index % 2 else "consumer-b",
                "observed_at": f"2026-09-02T05:{index:02d}:00Z",
                "result_path": case["result_path"],
                "causal_receipt_path": case["causal_receipt_path"],
                "exclusion_reason": None,
            }
        )
    inventory = feasibility.build_candidate_inventory(
        preregistration=preregistration,
        entries=inventory_entries,
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
    )
    subjects = _subjects(corpus)
    paired_packet = feasibility.build_paired_packet(
        evidence_root=tmp_path,
        corpus=corpus,
        policy_packet=policy_packet,
        provider="codex-subscription",
        model="gpt-5.5",
        reasoning="high",
        timeout_sec=2,
        execution_not_before="2026-09-03T01:00:00Z",
        execution_order=_execution_order(),
        execution_subjects=subjects,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )
    subject_by_case = {subject["case_id"]: subject for subject in subjects}
    receipts = []
    for sequence, item in enumerate(_execution_order(), start=1):
        case_id, arm = item.split(":")
        receipts.append(
            feasibility.build_execution_receipt(
                preregistration_sha256=preregistration["preregistration_sha256"],
                inventory_sha256=inventory["inventory_sha256"],
                paired_packet_sha256=paired_packet["paired_packet_sha256"],
                subject=subject_by_case[case_id],
                arm=arm,
                sequence=sequence,
                session_id=f"session-{sequence}-{arm}",
                completion=True,
                major_regressions=0,
                critical_omissions=1,
                scope_violations=0,
                abandoned=False,
                validation_rounds=3 if arm == "baseline" else 1,
                user_interventions=2 if arm == "baseline" else 1,
                elapsed_ms=1000,
                total_tokens=1000,
                artifact_bytes=1000,
                provider_calls=1,
                attempts=1,
                executed_at=f"2026-09-03T01:{sequence:02d}:00Z",
                provider="codex-subscription",
                model="gpt-5.5",
                reasoning="high",
                private_key=RUNNER_KEY,
            )
        )
    report = feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory,
        paired_packet=paired_packet,
        corpus=corpus,
        policy_packet=policy_packet,
        evidence_root=tmp_path,
        receipts=receipts,
        adjudication_receipts=_adjudications(receipts, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )
    assert report["verdict"] == "FEASIBILITY_PASS"
    assert report["claim_scope"] == "decision_policy_feasibility_only"

    token_only_improvement = deepcopy(receipts)
    for index, receipt in enumerate(token_only_improvement):
        receipt["validation_rounds"] = 2
        receipt["total_tokens"] = 900 if receipt["arm"] == "policy" else 1000
        unsigned = dict(receipt)
        unsigned.pop("receipt_sha256")
        unsigned.pop("signature")
        token_only_improvement[index] = feasibility._sign_receipt(
            unsigned, private_key=RUNNER_KEY, hash_field="receipt_sha256"
        )
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path,
        receipts=token_only_improvement,
        adjudication_receipts=_adjudications(token_only_improvement, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "FEASIBILITY_PASS"

    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=receipts,
        adjudication_receipts=[],
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    out_of_order_execution = deepcopy(receipts)
    out_of_order_execution[-1]["executed_at"] = "2026-09-03T01:01:00Z"
    out_of_order_execution[-1] = _resign_execution(out_of_order_execution[-1])
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path,
        receipts=out_of_order_execution,
        adjudication_receipts=_adjudications(out_of_order_execution, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    premature_adjudication = _adjudications(receipts, paired_packet)
    premature_adjudication[0]["adjudicated_at"] = "2026-09-03T01:01:30Z"
    unsigned_adjudication = dict(premature_adjudication[0])
    unsigned_adjudication.pop("receipt_sha256")
    unsigned_adjudication.pop("signature")
    premature_adjudication[0] = feasibility._sign_receipt(
        unsigned_adjudication,
        private_key=ADJUDICATOR_KEY,
        hash_field="receipt_sha256",
    )
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=receipts,
        adjudication_receipts=premature_adjudication,
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    paired_timeout_exceeded = deepcopy(receipts)
    paired_timeout_exceeded[-1]["elapsed_ms"] = 2001
    paired_timeout_exceeded[-1] = _resign_execution(paired_timeout_exceeded[-1])
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path,
        receipts=paired_timeout_exceeded,
        adjudication_receipts=_adjudications(paired_timeout_exceeded, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    omission_regression = deepcopy(receipts)
    policy_index = next(
        index for index, receipt in enumerate(omission_regression)
        if receipt["arm"] == "policy"
    )
    omission_regression[policy_index]["critical_omissions"] = 2
    omission_regression[policy_index] = _resign_execution(
        omission_regression[policy_index]
    )
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path,
        receipts=omission_regression,
        adjudication_receipts=_adjudications(omission_regression, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "FEASIBILITY_FAIL"

    wrong_provider = deepcopy(receipts)
    wrong_provider[-1]["provider"] = "different-provider"
    wrong_provider[-1] = _resign_execution(wrong_provider[-1])
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=wrong_provider,
        adjudication_receipts=_adjudications(wrong_provider, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    early_execution = deepcopy(receipts)
    early_execution[-1]["executed_at"] = "2026-09-03T00:59:59Z"
    early_execution[-1] = _resign_execution(early_execution[-1])
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=early_execution,
        adjudication_receipts=_adjudications(early_execution, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    quality_loss = _adjudications(receipts, paired_packet)
    quality_loss[-1]["major_quality_loss"] = True
    unsigned = dict(quality_loss[-1])
    unsigned.pop("receipt_sha256")
    unsigned.pop("signature")
    quality_loss[-1] = feasibility._sign_receipt(
        unsigned, private_key=ADJUDICATOR_KEY, hash_field="receipt_sha256"
    )
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=receipts,
        adjudication_receipts=quality_loss,
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "FEASIBILITY_FAIL"

    mismatched_entries = deepcopy(inventory_entries)
    mismatched_entries[0]["candidate_id"] = "case-2"
    mismatched_entries[1]["candidate_id"] = "case-1"
    mismatched_inventory = feasibility.build_candidate_inventory(
        preregistration=preregistration,
        entries=mismatched_entries,
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        evidence_root=tmp_path,
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
    )
    with pytest.raises(ValueError, match="execution_subject_inventory_mismatch"):
        feasibility.evaluate_paired_results(
            preregistration=preregistration,
            inventory=mismatched_inventory, paired_packet=paired_packet, corpus=corpus,
            policy_packet=policy_packet, evidence_root=tmp_path, receipts=receipts,
            adjudication_receipts=_adjudications(receipts, paired_packet),
            trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
            trusted_approver_public_key=_approver_public_key(),
        )

    unsafe = deepcopy(receipts)
    unsafe[-1]["major_regressions"] = 1
    unsigned = dict(unsafe[-1])
    unsigned.pop("receipt_sha256")
    unsigned.pop("signature")
    unsafe[-1] = feasibility._sign_receipt(
        unsigned, private_key=RUNNER_KEY, hash_field="receipt_sha256"
    )
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=unsafe,
        adjudication_receipts=_adjudications(unsafe, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "FEASIBILITY_FAIL"

    incomplete = receipts[:-1]
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=incomplete,
        adjudication_receipts=_adjudications(receipts, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    malformed = deepcopy(receipts)
    malformed[-1]["case_id"] = ""
    unsigned = dict(malformed[-1])
    unsigned.pop("receipt_sha256")
    unsigned.pop("signature")
    malformed[-1] = feasibility._sign_receipt(
        unsigned, private_key=RUNNER_KEY, hash_field="receipt_sha256"
    )
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=malformed,
        adjudication_receipts=_adjudications(receipts, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    forged = deepcopy(receipts)
    forged[-1]["total_tokens"] = 1
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=forged,
        adjudication_receipts=_adjudications(receipts, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"

    over_limit = deepcopy(receipts)
    over_limit[-1]["artifact_bytes"] = preregistration["resource_limits"]["max_artifact_bytes"] + 1
    unsigned = dict(over_limit[-1])
    unsigned.pop("receipt_sha256")
    unsigned.pop("signature")
    over_limit[-1] = feasibility._sign_receipt(
        unsigned, private_key=RUNNER_KEY, hash_field="receipt_sha256"
    )
    assert feasibility.evaluate_paired_results(
        preregistration=preregistration,
        inventory=inventory, paired_packet=paired_packet, corpus=corpus,
        policy_packet=policy_packet, evidence_root=tmp_path, receipts=over_limit,
        adjudication_receipts=_adjudications(over_limit, paired_packet),
        trusted_preregistration_public_key=feasibility.public_key_b64(PREREGISTRATION_KEY),
        trusted_causal_reviewer_public_key=_reviewer_public_key(),
        trusted_approver_public_key=_approver_public_key(),
    )["verdict"] == "INCONCLUSIVE"


def test_excluded_inventory_candidate_requires_signed_reason_receipt(tmp_path: Path) -> None:
    preregistration = _preregistration()
    source = _write_case(tmp_path, 1)
    entry = {
        "candidate_id": "case-1",
        "repository_id": "consumer-a",
        "observed_at": "2026-09-03T01:00:00Z",
        "result_path": source["result_path"],
        "causal_receipt_path": source["causal_receipt_path"],
        "exclusion_reason": "non-implementation",
    }
    with pytest.raises(ValueError, match="inventory_exclusion_receipt_missing"):
        feasibility.build_candidate_inventory(
            preregistration=preregistration,
            entries=[entry],
            trusted_preregistration_public_key=feasibility.public_key_b64(
                PREREGISTRATION_KEY
            ),
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )


def test_inventory_observed_at_must_come_from_signed_receipt(tmp_path: Path) -> None:
    preregistration = _preregistration()
    source = _write_case(tmp_path, 1)
    entry = {
        "candidate_id": "case-1",
        "repository_id": "consumer-a",
        "observed_at": "2026-09-03T01:00:00Z",
        "result_path": source["result_path"],
        "causal_receipt_path": source["causal_receipt_path"],
        "exclusion_reason": None,
    }
    with pytest.raises(ValueError, match="inventory_observed_at_unbound"):
        feasibility.build_candidate_inventory(
            preregistration=preregistration,
            entries=[entry],
            trusted_preregistration_public_key=feasibility.public_key_b64(
                PREREGISTRATION_KEY
            ),
            evidence_root=tmp_path,
            trusted_causal_reviewer_public_key=_reviewer_public_key(),
        )
