from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_decision_policy_feasibility as feasibility


REVIEWER_KEY = Ed25519PrivateKey.generate()
APPROVER_KEY = Ed25519PrivateKey.generate()


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
        execution_order=[
            f"case-{index}:{arm}"
            for index in range(1, 6)
            for arm in ("baseline", "policy")
        ],
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
