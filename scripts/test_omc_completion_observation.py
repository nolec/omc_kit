import base64
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_completion_observation as observation
import omc_state


def _key_pair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii")
    return private, public


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fixture(
    index: int,
    repo_id: str = "repo-a",
    key_pair: tuple[Ed25519PrivateKey, str] | None = None,
    executor_key_pair: tuple[Ed25519PrivateKey, str] | None = None,
    roots: dict[str, Path] | None = None,
) -> tuple[dict, dict, bytes, bytes, Ed25519PrivateKey]:
    private, public = key_pair or _key_pair()
    executor_private, executor_public = executor_key_pair or _key_pair()
    roots = roots or {
        "repo-a": Path(tempfile.mkdtemp()) / "repo-a",
        "repo-b": Path(tempfile.mkdtemp()) / "repo-b",
    }
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    request = f"implement case {index}".encode()
    output = f"provider output {index}".encode()
    completion = {
        "schema_version": 2,
        "session_id": f"session-{index}",
        "request_sha256": _sha(request.decode()),
        "baseline_commit": "a" * 40,
        "followup_commit": f"{index:040x}",
        "completed_at": f"2026-09-{index + 1:02d}T00:00:00+00:00",
        "changed_paths": ["app.py"],
        "provider_outputs_available": False,
        "work_class": "implementation",
        "work_class_locked_at": "2026-09-01T00:00:00+00:00",
    }
    lineage = {
        "schema_version": 1,
        "evidence_status": "informational_unverified",
        "session_id": f"session-{index}",
        "work_id": f"work-{index}",
        "root_session_id": f"session-{index}",
        "session_ids": [f"session-{index}"],
        "rework_count": 0,
    }
    terminal = omc_state._seal_capture(
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "capture_validity": "VALID",
            "claim": "CAPTURE_REHEARSAL",
            "session_id": f"session-{index}",
            "work_id": f"work-{index}",
            "start_capture_sha256": "1" * 64,
            "verification_capture_sha256": "2" * 64,
            "completion_sha256": _sha(completion),
            "completion_session_id": f"session-{index}",
            "completion_request_sha256": completion["request_sha256"],
            "completion_lineage_sha256": _sha(lineage),
            "root_session_id": f"session-{index}",
            "session_ids": [f"session-{index}"],
            "rework_count": 0,
            "baseline_commit": "a" * 40,
            "followup_commit": f"{index:040x}",
            "changed_paths": ["app.py"],
            "verification_passed": True,
            "user_outcome": "accepted",
            "correction_evidence": None,
            "captured_at": f"2026-09-{index + 1:02d}T00:00:00+00:00",
            "capture_sha256": "",
            "signoff": {},
        },
        private_key=private,
    )
    registration = observation.build_registration(
        study_id="study-v0",
        frozen_at="2026-09-01T00:00:00+00:00",
        repositories=[
            {"repo_id": name, "root_sha256": hashlib.sha256(str(root.resolve()).encode()).hexdigest(), "trusted_public_key": public}
            for name, root in roots.items()
        ],
        trusted_execution_public_key=executor_public,
    )
    source = {
        "repo_id": repo_id,
        "repository_root_sha256": next(item["root_sha256"] for item in registration["repositories"] if item["repo_id"] == repo_id),
        "terminal": terminal,
        "completion": completion,
        "lineage": lineage,
        "raw_request": request,
        "raw_output": output,
        "execution_receipt": observation._seal_signed(
            {
                "schema_version": observation.SCHEMA,
                "artifact_type": "execution",
                "repo_id": repo_id,
                "repository_root_sha256": next(item["root_sha256"] for item in registration["repositories"] if item["repo_id"] == repo_id),
                "work_id": terminal["work_id"],
                "session_id": terminal["completion_session_id"],
                "terminal_sha256": _sha(terminal),
                "raw_request_sha256": hashlib.sha256(request).hexdigest(),
                "raw_output_sha256": hashlib.sha256(output).hexdigest(),
            },
            private_key=executor_private,
            signer="completion-observation-execution-v0",
        ),
    }
    return registration, source, request, output, private


def _cohort(*, one_repo: bool = False):
    key_pair = _key_pair()
    executor_key_pair = _key_pair()
    base = Path(tempfile.mkdtemp())
    roots = {"repo-a": base / "repo-a", "repo-b": base / "repo-b"}
    registration = None
    candidates = []
    reconciliations = []
    for index in range(10):
        repo_id = "repo-a" if one_repo or index < 5 else "repo-b"
        current, source, _, _, private = _fixture(
            index, repo_id, key_pair, executor_key_pair, roots
        )
        registration = registration or current
        candidate = observation.build_candidate(registration, **source)
        candidates.append(candidate)
        reconciliations.append(observation.build_reconciliation(
            registration, candidate, classification="clarification",
            reconciled_at="2026-09-20T00:00:00+00:00",
            signer_private_key=private,
        ))
        session_dir = roots[repo_id] / ".omc" / "state" / "sessions" / f"session-{index}"
        (session_dir / "capture").mkdir(parents=True, exist_ok=True)
        (session_dir / "capture" / "terminal.json").write_text(json.dumps(source["terminal"]), encoding="utf-8")
        (session_dir / "completion.json").write_text(json.dumps(source["completion"]), encoding="utf-8")
        (session_dir / "completion-lineage.json").write_text(json.dumps(source["lineage"]), encoding="utf-8")
    return registration, candidates, reconciliations, key_pair[0], roots


def test_registration_freezes_v0_scope_and_taxonomy() -> None:
    registration, _, _, _, _ = _fixture(0)
    assert registration["selection"] == {
        "rule": "chronological_first_eligible",
        "count": 10,
        "minimum_repositories": 2,
        "eligible_work_class": "implementation",
    }
    assert registration["primary_corrections"] == [
        "defect_correction", "missing_requirement", "persona_mismatch"
    ]
    assert registration["claim_boundary"] == "CAPTURE_FEASIBILITY_ONLY"


def test_candidate_binds_terminal_completion_lineage_and_raw_bytes() -> None:
    registration, source, request, output, _ = _fixture(0)
    candidate = observation.build_candidate(registration, **source)
    assert candidate["work_id"] == "work-0"
    assert candidate["raw_request_sha256"] == hashlib.sha256(request).hexdigest()
    assert candidate["raw_output_sha256"] == hashlib.sha256(output).hexdigest()
    assert candidate["terminal_sha256"] == _sha(source["terminal"])


@pytest.mark.parametrize("field", ["terminal", "completion", "lineage"])
def test_candidate_rejects_tampered_completion_bundle(field: str) -> None:
    registration, source, _, _, _ = _fixture(0)
    source[field] = copy.deepcopy(source[field])
    source[field]["work_id" if field != "completion" else "followup_commit"] = "tampered"
    with pytest.raises(
        observation.CaptureError,
        match="bundle_invalid|execution_receipt_invalid",
    ):
        observation.build_candidate(registration, **source)


def test_candidate_rejects_nonimplementation_and_wrong_repository_binding() -> None:
    registration, source, _, _, _ = _fixture(0)
    source["completion"]["work_class"] = "synthetic"
    with pytest.raises(observation.CaptureError, match="candidate_ineligible"):
        observation.build_candidate(registration, **source)
    registration, source, _, _, _ = _fixture(0)
    source["repository_root_sha256"] = "f" * 64
    with pytest.raises(observation.CaptureError, match="repository_binding_mismatch"):
        observation.build_candidate(registration, **source)


def test_reconciliation_uses_fixed_taxonomy_and_is_bound_to_candidate() -> None:
    registration, source, _, _, private = _fixture(0)
    candidate = observation.build_candidate(registration, **source)
    receipt = observation.build_reconciliation(
        registration, candidate, classification="missing_requirement", reconciled_at="2026-09-20T00:00:00+00:00", signer_private_key=private
    )
    assert receipt["primary_correction"] is True
    with pytest.raises(observation.CaptureError, match="classification_invalid"):
        observation.build_reconciliation(registration, candidate, classification="other", reconciled_at="2026-09-20T00:00:00+00:00", signer_private_key=private)


def test_report_requires_first_ten_eligible_in_order_across_two_repositories() -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    population = observation.build_population_completeness_receipt(
        registration, candidates, closed_at="2026-09-20T00:00:00+00:00", signer_private_key=private, repository_roots=roots
    )
    report = observation.build_report(
        registration, candidates, reconciliations,
        population_completeness_receipt=population,
        approved_registration_sha256=registration["registration_sha256"],
        repository_roots=roots,
    )
    assert report["decision"] == "CAPTURE_FEASIBLE"
    assert report["selected_count"] == 10
    assert report["repository_count"] == 2


@pytest.mark.parametrize("mutation", ["nine", "one_repo", "duplicate", "reordered"])
def test_report_fails_closed_on_incomplete_or_cherry_picked_cohort(mutation: str) -> None:
    registration, candidates, reconciliations, private, roots = _cohort(
        one_repo=mutation == "one_repo"
    )
    if mutation == "nine":
        candidates.pop()
        reconciliations.pop()
    elif mutation == "one_repo":
        pass
    elif mutation == "duplicate":
        candidates[1] = candidates[0]
    else:
        candidates[0], candidates[1] = candidates[1], candidates[0]
    with pytest.raises(observation.CaptureError, match="state_stream_incomplete"):
        observation.build_population_completeness_receipt(
            registration,
            candidates,
            closed_at="2026-09-20T00:00:00+00:00",
            signer_private_key=private,
            repository_roots=roots,
        )


def test_cli_returns_structured_blocked_json_for_malformed_input(tmp_path) -> None:
    malformed = tmp_path / "registration.json"
    candidate = tmp_path / "candidate.json"
    output = tmp_path / "reconciliation.json"
    malformed.write_text("[]", encoding="utf-8")
    candidate.write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc_completion_observation.py",
            "reconcile",
            "--registration",
            str(malformed),
            "--candidate",
            str(candidate),
            "--classification",
            "clarification",
            "--reconciled-at",
            "2026-09-20T00:00:00+00:00",
            "--out",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "BLOCKED",
        "reason": "registration_invalid",
    }
    assert not output.exists()


def test_register_cli_returns_blocked_json_for_malformed_repositories(tmp_path) -> None:
    repositories = tmp_path / "repositories.json"
    output = tmp_path / "registration.json"
    repositories.write_text("[null, null]", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc_completion_observation.py",
            "register",
            "--study-id",
            "study-v0",
            "--frozen-at",
            "2026-09-01T00:00:00+00:00",
            "--repositories",
            str(repositories),
            "--trusted-execution-public-key",
            _key_pair()[1],
            "--out",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "BLOCKED",
        "reason": "registration_invalid",
    }
    assert not output.exists()


@pytest.mark.parametrize("field", ["completion", "lineage"])
def test_candidate_converts_malformed_bundle_structure_to_capture_error(field: str) -> None:
    registration, source, _, _, _ = _fixture(0)
    source[field] = []
    with pytest.raises(observation.CaptureError, match="bundle_invalid"):
        observation.build_candidate(registration, **source)


def test_report_rejects_candidate_rehash_forgery() -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    population = observation.build_population_completeness_receipt(
        registration, candidates, closed_at="2026-09-20T00:00:00+00:00", signer_private_key=private, repository_roots=roots
    )
    forged = {**candidates[3], "raw_output_sha256": "f" * 64}
    forged["candidate_sha256"] = observation.canonical_sha256(
        {**forged, "candidate_sha256": ""}
    )
    candidates[3] = forged
    assert observation.build_report(
        registration, candidates, reconciliations,
        population_completeness_receipt=population,
        approved_registration_sha256=registration["registration_sha256"],
        repository_roots=roots,
    )[
        "decision"
    ] == "CAPTURE_INCOMPLETE"


def test_report_rejects_reclassification_with_recomputed_self_hash() -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    reconciliation = reconciliations[3]
    forged = {
        **reconciliation,
        "classification": "defect_correction",
        "primary_correction": True,
    }
    forged["receipt_sha256"] = observation.canonical_sha256(
        {**forged, "receipt_sha256": "", "signoff": {**forged["signoff"], "signature": ""}}
    )
    reconciliations[3] = forged
    population = observation.build_population_completeness_receipt(
        registration, candidates, closed_at="2026-09-20T00:00:00+00:00", signer_private_key=private, repository_roots=roots
    )
    assert observation.build_report(
        registration, candidates, reconciliations,
        population_completeness_receipt=population,
        approved_registration_sha256=registration["registration_sha256"],
        repository_roots=roots,
    )["decision"] == "CAPTURE_INCOMPLETE"


def test_report_requires_authenticated_population_completeness() -> None:
    registration, source, _, _, private = _fixture(0)
    candidate = observation.build_candidate(registration, **source)
    reconciliation = observation.build_reconciliation(
        registration,
        candidate,
        classification="clarification",
        reconciled_at="2026-09-20T00:00:00+00:00",
        signer_private_key=private,
    )
    report = observation.build_report(
        registration,
        [candidate] * 10,
        [reconciliation] * 10,
        approved_registration_sha256=registration["registration_sha256"],
    )
    assert report["decision"] == "CAPTURE_INCOMPLETE"
    assert report["reason"] == "population_completeness_required"


def test_candidate_rejects_unbound_raw_output() -> None:
    registration, source, _, _, _ = _fixture(0)
    source["raw_output"] = b"unrelated output"
    with pytest.raises(observation.CaptureError, match="execution_receipt_invalid"):
        observation.build_candidate(registration, **source)


def test_candidate_rejects_execution_receipt_signed_by_repository_key() -> None:
    registration, source, _, _, repository_private = _fixture(0)
    receipt = source["execution_receipt"]
    source["execution_receipt"] = observation._seal_signed(
        {key: value for key, value in receipt.items() if key not in {"receipt_sha256", "signoff"}},
        private_key=repository_private,
        signer="completion-observation-execution-v0",
    )
    with pytest.raises(observation.CaptureError, match="execution_receipt_invalid"):
        observation.build_candidate(registration, **source)


def test_report_rejects_rehashed_registration_without_frozen_digest() -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    approved = registration["registration_sha256"]
    registration = {**registration, "study_id": "attacker-study"}
    registration["registration_sha256"] = observation.canonical_sha256(
        {**registration, "registration_sha256": ""}
    )
    report = observation.build_report(
        registration,
        candidates,
        reconciliations,
        approved_registration_sha256=approved,
        repository_roots=roots,
    )
    assert report["decision"] == "CAPTURE_INCOMPLETE"
    assert report["reason"] == "registration_approval_mismatch"


def test_report_rejects_population_digest_recomputed_without_signature() -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    population = observation.build_population_completeness_receipt(
        registration,
        candidates,
        closed_at="2026-09-20T00:00:00+00:00",
        signer_private_key=private,
        repository_roots=roots,
    )
    population["candidate_sha256s"] = population["candidate_sha256s"][1:]
    population["receipt_sha256"] = observation.canonical_sha256(
        {
            **population,
            "receipt_sha256": "",
            "signoff": {**population["signoff"], "signature": ""},
        }
    )
    report = observation.build_report(
        registration,
        candidates,
        reconciliations,
        population_completeness_receipt=population,
        approved_registration_sha256=registration["registration_sha256"],
        repository_roots=roots,
    )
    assert report["decision"] == "CAPTURE_INCOMPLETE"
    assert report["reason"] == "population_completeness_required"


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", "wrong-schema"), ("artifact_type", "reconciliation")],
)
def test_report_rejects_signed_population_with_wrong_artifact_metadata(
    field: str, value: str
) -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    population = observation.build_population_completeness_receipt(
        registration,
        candidates,
        closed_at="2026-09-20T00:00:00+00:00",
        signer_private_key=private,
        repository_roots=roots,
    )
    population = observation._seal_signed(
        {
            **{
                key: item
                for key, item in population.items()
                if key not in {"receipt_sha256", "signoff"}
            },
            field: value,
        },
        private_key=private,
        signer="completion-observation-population-v0",
    )
    report = observation.build_report(
        registration,
        candidates,
        reconciliations,
        population_completeness_receipt=population,
        approved_registration_sha256=registration["registration_sha256"],
        repository_roots=roots,
    )
    assert report["decision"] == "CAPTURE_INCOMPLETE"
    assert report["reason"] == "population_completeness_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [("study_id", "other-study"), ("registration_sha256", "f" * 64)],
)
def test_report_rejects_signed_reconciliation_from_another_registration(
    field: str, value: str
) -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    population = observation.build_population_completeness_receipt(
        registration,
        candidates,
        closed_at="2026-09-20T00:00:00+00:00",
        signer_private_key=private,
        repository_roots=roots,
    )
    receipt = reconciliations[0]
    reconciliations[0] = observation._seal_signed(
        {
            **{
                key: item
                for key, item in receipt.items()
                if key not in {"receipt_sha256", "signoff"}
            },
            field: value,
        },
        private_key=private,
        signer="completion-observation-reconciliation-v0",
    )
    report = observation.build_report(
        registration,
        candidates,
        reconciliations,
        population_completeness_receipt=population,
        approved_registration_sha256=registration["registration_sha256"],
        repository_roots=roots,
    )
    assert report["decision"] == "CAPTURE_INCOMPLETE"
    assert report["reason"] == "reconciliation_invalid"


def test_population_closure_requires_registered_state_streams(tmp_path) -> None:
    registration, candidates, _, private, _ = _cohort()
    with pytest.raises(observation.CaptureError, match="state_stream_incomplete"):
        observation.build_population_completeness_receipt(
            registration,
            candidates,
            closed_at="2026-09-20T00:00:00+00:00",
            signer_private_key=private,
            repository_roots={"repo-a": tmp_path / "a", "repo-b": tmp_path / "b"},
        )


def test_population_closure_rejects_unsubmitted_earlier_state_entry() -> None:
    registration, candidates, _, private, roots = _cohort()
    source_terminal = candidates[0]["terminal"]
    completion = {**candidates[0]["completion"], "session_id": "session-extra"}
    lineage = {
        **candidates[0]["lineage"],
        "session_id": "session-extra",
        "work_id": "work--1",
        "root_session_id": "session-extra",
        "session_ids": ["session-extra"],
    }
    terminal = omc_state._seal_capture(
        {
            **source_terminal,
            "session_id": "session-extra",
            "work_id": "work--1",
            "completion_session_id": "session-extra",
            "completion_sha256": _sha(completion),
            "completion_lineage_sha256": _sha(lineage),
            "root_session_id": "session-extra",
            "session_ids": ["session-extra"],
            "capture_sha256": "",
            "signoff": {},
        },
        private_key=private,
    )
    session_dir = roots["repo-a"] / ".omc" / "state" / "sessions" / "session-extra"
    (session_dir / "capture").mkdir(parents=True)
    for path, document in (
        (session_dir / "capture" / "terminal.json", terminal),
        (session_dir / "completion.json", completion),
        (session_dir / "completion-lineage.json", lineage),
    ):
        path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(observation.CaptureError, match="state_stream_incomplete"):
        observation.build_population_completeness_receipt(
            registration,
            candidates,
            closed_at="2026-09-20T00:00:00+00:00",
            signer_private_key=private,
            repository_roots=roots,
        )


def test_state_stream_uses_repo_and_work_id_as_timestamp_tie_breakers() -> None:
    registration, _, _, private, roots = _cohort()
    terminal_path = roots["repo-a"] / ".omc/state/sessions/session-1/capture/terminal.json"
    terminal = json.loads(terminal_path.read_text())
    terminal = omc_state._seal_capture(
        {**terminal, "captured_at": "2026-09-01T00:00:00+00:00", "capture_sha256": "", "signoff": {}},
        private_key=private,
    )
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    inventory = observation._scan_state_streams(
        registration, roots, "2026-09-20T00:00:00+00:00"
    )
    assert [item["work_id"] for item in inventory[:2]] == ["work-0", "work-1"]


def test_registration_requires_independent_executor_trust_anchor() -> None:
    repo_private, repo_public = _key_pair()
    _, executor_public = _key_pair()
    registration = observation.build_registration(
        study_id="study-v0",
        frozen_at="2026-09-01T00:00:00+00:00",
        repositories=[
            {"repo_id": "repo-a", "root_sha256": "a" * 64, "trusted_public_key": repo_public},
            {"repo_id": "repo-b", "root_sha256": "b" * 64, "trusted_public_key": repo_public},
        ],
        trusted_execution_public_key=executor_public,
    )
    assert registration["trusted_execution_public_key"] == executor_public
    assert registration["trusted_execution_public_key"] != repo_public


def test_receipt_builders_reject_noncollector_signer() -> None:
    registration, candidates, _, _, roots = _cohort()
    wrong_private, _ = _key_pair()
    with pytest.raises(observation.CaptureError, match="collector_signer_mismatch"):
        observation.build_reconciliation(
            registration,
            candidates[0],
            classification="clarification",
            reconciled_at="2026-09-20T00:00:00+00:00",
            signer_private_key=wrong_private,
        )
    with pytest.raises(observation.CaptureError, match="collector_signer_mismatch"):
        observation.build_population_completeness_receipt(
            registration,
            candidates,
            closed_at="2026-09-20T00:00:00+00:00",
            signer_private_key=wrong_private,
            repository_roots=roots,
        )


@pytest.mark.parametrize(
    "reconciled_at",
    ["not-a-timestamp", "2026-08-31T23:59:59+00:00"],
)
def test_reconciliation_rejects_invalid_or_precapture_timestamp(
    reconciled_at: str,
) -> None:
    registration, source, _, _, private = _fixture(0)
    candidate = observation.build_candidate(registration, **source)
    with pytest.raises(
        observation.CaptureError, match="reconciliation_timestamp_invalid"
    ):
        observation.build_reconciliation(
            registration,
            candidate,
            classification="clarification",
            reconciled_at=reconciled_at,
            signer_private_key=private,
        )


def test_report_rejects_reconciliation_after_population_close() -> None:
    registration, candidates, reconciliations, private, roots = _cohort()
    reconciliations[0] = observation.build_reconciliation(
        registration,
        candidates[0],
        classification="clarification",
        reconciled_at="2026-09-21T00:00:00+00:00",
        signer_private_key=private,
    )
    population = observation.build_population_completeness_receipt(
        registration,
        candidates,
        closed_at="2026-09-20T00:00:00+00:00",
        signer_private_key=private,
        repository_roots=roots,
    )
    report = observation.build_report(
        registration,
        candidates,
        reconciliations,
        population_completeness_receipt=population,
        approved_registration_sha256=registration["registration_sha256"],
        repository_roots=roots,
    )
    assert report["decision"] == "CAPTURE_INCOMPLETE"
    assert report["reason"] == "reconciliation_invalid"
