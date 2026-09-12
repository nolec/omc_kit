import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_completion_observation as observation
from omc_source_hash import source_sha256
import omc_state


@pytest.fixture(autouse=True)
def _fixed_live_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime.fromisoformat("2026-09-10T00:00:00+00:00")
    monkeypatch.setattr(observation, "_now", lambda: fixed)


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


def _live_repo(
    tmp_path: Path,
    *,
    repo_id: str = "repo",
    repository_roots: dict[str, Path] | None = None,
    registration: dict | None = None,
) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "app.py").write_text("before\n", encoding="utf-8")
    (root / "managed.txt").write_text("managed\n", encoding="utf-8")
    (root / ".gitignore").write_text(".omc/\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "app.py", "managed.txt", ".gitignore"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    request = "implement live observation case"
    pending = {
        "schema_version": 3,
        "session_id": "session-a",
        "baseline_head": head,
        "request_sha256": observation.canonical_sha256(request),
        "work_class": "implementation",
        "work_class_locked_at": "2026-09-10T00:00:00+00:00",
        "work_id": "a" * 32,
        "root_session_id": "session-a",
        "session_ids": ["session-a"],
        "rework_count": 0,
    }
    state = root / ".omc" / "state"
    state.mkdir(parents=True)
    (state / "pending-completion.json").write_text(json.dumps(pending), encoding="utf-8")
    _write_live_session(root, pending, request=request)
    source_root = Path(__file__).resolve().parents[1]
    source_hash = source_sha256(source_root)
    install_source = {
        "source_kind": "external",
        "source_path": str(source_root),
        "source_sha256": source_hash,
    }
    install_receipt = {
        "schema_version": 3,
        "omc_version": (source_root / "VERSION").read_text().strip(),
        "source_sha256": source_hash,
        "source_revision": subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "target": str(root.resolve()),
        "installed_at": "2026-09-10T00:00:00+00:00",
        "updated_at": "2026-09-10T00:00:00+00:00",
        "entries": {
            "managed.txt": {
                "policy": "managed_exact",
                "status": "updated",
                "ownership": "exclusive_managed",
                "source_sha256": hashlib.sha256(b"managed\n").hexdigest(),
                "target_sha256": hashlib.sha256(b"managed\n").hexdigest(),
                "previous_target_sha256": "",
                "registered_current_install": True,
                "setup_created": False,
            }
        },
    }
    (root / ".omc" / "install-source.json").write_text(json.dumps(install_source))
    (root / ".omc" / "install-receipt.json").write_text(json.dumps(install_receipt))
    roster = repository_roots or {
        repo_id: root,
        "reserved-repo": tmp_path / "reserved-repo",
    }
    if registration is None:
        registration = observation.build_live_registration(
            study_id="completion-quality-feasibility-01",
            repository_roots=roster,
            observation_started_at=observation._now().isoformat(),
            output=tmp_path / "registration.json",
        )
    observation.enable_live_observation(
        root,
        executor_surface="codex",
        repo_id=repo_id,
        registration=registration,
    )
    return root, pending


def _write_live_session(root: Path, pending: dict, *, request: str) -> None:
    session = {
        "session_id": pending["session_id"],
        "work_id": pending["work_id"],
        "request": request,
        "role_ids": ["senior_coding"],
        "confirmation": {"status": "confirmed"},
        "git": {"head": pending["baseline_head"]},
        "work_class": pending["work_class"],
        "created_at": pending["work_class_locked_at"],
    }
    directory = root / ".omc" / "state" / "sessions" / pending["session_id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "session.json").write_text(json.dumps(session), encoding="utf-8")


def _replace_live_pending(root: Path, pending: dict, ordinal: int) -> dict:
    request = f"implementation sample {ordinal}"
    updated = {
        **pending,
        "session_id": f"session-{ordinal}",
        "root_session_id": f"session-{ordinal}",
        "session_ids": [f"session-{ordinal}"],
        "rework_count": 0,
        "work_id": f"{ordinal:032x}",
        "request_sha256": observation.canonical_sha256(request),
    }
    _write_live_session(root, updated, request=request)
    (root / ".omc" / "state" / "pending-completion.json").write_text(
        json.dumps(updated), encoding="utf-8"
    )
    return updated


def test_live_start_accepts_pending_created_by_real_session_producer(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    session = omc_state.record_session(
        root,
        mode="autopilot",
        title="omc-task",
        request="implement through the real pending producer",
        role_ids=["senior_coding"],
        work_class="implementation",
        completion_action="start",
        confirmed=True,
        confirmation_source="test",
    )

    pending = json.loads(
        (root / ".omc" / "state" / "pending-completion.json").read_text()
    )
    full_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert pending["session_id"] == session["session_id"]
    assert pending["baseline_head"] == full_head
    assert observation.start_live_observation(root)["status"] == "COLLECTING"


def _tamper_live_completion_baseline(root: Path, work_id: str) -> None:
    path = (
        root
        / ".omc"
        / "observations"
        / "live"
        / work_id
        / "first-completion.json"
    )
    completion = json.loads(path.read_text(encoding="utf-8"))
    completion["baseline_commit"] = "f" * 40
    completion["completion_snapshot_sha256"] = observation.canonical_sha256(
        {**completion, "completion_snapshot_sha256": ""}
    )
    path.write_text(json.dumps(completion), encoding="utf-8")


def test_live_observation_captures_uncommitted_first_completion(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    started = observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    completed = observation.capture_live_completion(
        root,
        raw_report=b"first completion report",
        raw_verification=b"1 passed",
        unrun_items=["real provider smoke"],
    )
    assert started["work_id"] == pending["work_id"]
    assert completed["commit_bound"] is False
    assert completed["status"] == "AWAITING_USER_OUTCOME"
    assert completed["changed_paths"] == ["app.py"]
    assert base64.b64decode(completed["raw_report_base64"]) == b"first completion report"
    assert completed["unrun_items"] == ["real provider smoke"]


def test_live_completion_uses_one_pending_snapshot_when_current_work_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, first_pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    second_pending = {**first_pending, "work_id": "b" * 32}
    snapshots = iter((first_pending, second_pending))
    monkeypatch.setattr(observation, "_live_pending", lambda _root: next(snapshots))

    completed = observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )

    assert completed["work_id"] == first_pending["work_id"]
    assert (
        root
        / ".omc"
        / "observations"
        / "live"
        / first_pending["work_id"]
        / "first-completion.json"
    ).is_file()
    assert not (
        root
        / ".omc"
        / "observations"
        / "live"
        / second_pending["work_id"]
        / "first-completion.json"
    ).exists()


def test_live_observation_reentry_requires_exact_work_and_baseline(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    continued = {**pending, "session_id": "session-b", "session_ids": ["session-a", "session-b"], "rework_count": 1}
    _write_live_session(root, continued, request="implement live observation case")
    (root / ".omc" / "state" / "pending-completion.json").write_text(
        json.dumps(continued), encoding="utf-8"
    )
    assert observation.live_observation_status(root)["status"] == "COLLECTING"
    continued["work_id"] = "b" * 32
    (root / ".omc" / "state" / "pending-completion.json").write_text(
        json.dumps(continued), encoding="utf-8"
    )
    with pytest.raises(observation.CaptureError, match="pending_completion_invalid"):
        observation.live_observation_status(root)


def test_live_followup_preserves_raw_text_and_requires_authoritative_classification(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    with pytest.raises(observation.CaptureError, match="classification_required"):
        observation.record_live_outcome(
            root, outcome="correction_required", raw_followup=b"fix edge case"
        )
    result = observation.record_live_outcome(
        root,
        outcome="correction_required",
        raw_followup=b"fix edge case",
        classification="defect_correction",
    )
    assert base64.b64decode(result["raw_followup_base64"]) == b"fix edge case"
    assert result["classification"] == "defect_correction"
    accepted = observation.record_live_outcome(
        root, outcome="accepted", raw_followup=b"accepted"
    )
    assert accepted["outcome"] == "accepted"
    with pytest.raises(observation.CaptureError, match="outcome_already_recorded"):
        observation.record_live_outcome(root, outcome="accepted", raw_followup=b"again")


def test_live_cli_reports_observation_invalid_without_blocking_product_work(tmp_path: Path) -> None:
    root = tmp_path / "missing-state"
    root.mkdir()
    result = subprocess.run(
        [sys.executable, "scripts/omc_completion_observation.py", "live-status", "--target", str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "claim_boundary": "OBSERVATION_ONLY",
        "reason": "pending_completion_invalid",
        "status": "OBSERVATION_INVALID",
    }


def test_live_observation_requires_explicit_repository_enrollment(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    (root / ".omc" / "observation-policy.json").unlink()
    with pytest.raises(observation.CaptureError, match="live_observation_not_enabled"):
        observation.start_live_observation(root)


def test_live_policy_seals_capture_all_and_global_closure_contract(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    policy = json.loads((root / ".omc" / "observation-policy.json").read_text())
    receipt_bytes = (root / ".omc" / "install-receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    assert policy["capture_rule"] == "all_eligible_starts"
    assert policy["closure_selection_rule"] == "global_chronological_first_eligible"
    assert policy["closure_sample_target"] == 5
    assert policy["replacement_allowed"] is False
    assert policy["eligible_work_class"] == "implementation"
    assert policy["installed_omc_version"] == receipt["omc_version"]
    assert policy["installed_source_sha256"]
    assert policy["install_receipt_sha256"] == hashlib.sha256(receipt_bytes).hexdigest()

    policy["closure_sample_target"] = 6
    policy["policy_sha256"] = observation.canonical_sha256(
        {**policy, "policy_sha256": ""}
    )
    (root / ".omc" / "observation-policy.json").write_text(json.dumps(policy))
    with pytest.raises(observation.CaptureError, match="live_observation_not_enabled"):
        observation.start_live_observation(root)


def test_live_registration_must_be_external_and_prospective(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    roster = {"repo": root, "second": tmp_path / "second"}
    with pytest.raises(observation.CaptureError, match="live_registration_invalid"):
        observation.build_live_registration(
            study_id="study",
            repository_roots=roster,
            observation_started_at="2026-09-01T00:00:00+00:00",
            output=tmp_path / "past-registration.json",
        )
    with pytest.raises(observation.CaptureError, match="live_registration_invalid"):
        observation.build_live_registration(
            study_id="study",
            repository_roots=roster,
            observation_started_at=observation._now().isoformat(),
            output=root / "registration.json",
        )


def test_live_policy_rejects_install_receipt_changed_after_enrollment(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    receipt_path = root / ".omc" / "install-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["omc_version"] = "9.9.9"
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(observation.CaptureError, match="live_install_identity_invalid"):
        observation.start_live_observation(root)


def test_live_enable_audits_the_same_install_receipt_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _live_repo(tmp_path)
    policy_path = root / ".omc" / "observation-policy.json"
    policy_path.unlink()
    receipt_path = root / ".omc" / "install-receipt.json"
    valid_receipt = receipt_path.read_bytes()
    invalid_receipt = json.loads(valid_receipt)
    invalid_receipt["entries"]["managed.txt"]["target_sha256"] = "f" * 64
    receipt_path.write_text(json.dumps(invalid_receipt), encoding="utf-8")
    real_audit = observation.omc_install_audit.audit_target

    def replace_before_audit(target: Path, **kwargs: object) -> dict[str, object]:
        receipt_path.write_bytes(valid_receipt)
        return real_audit(target, **kwargs)

    monkeypatch.setattr(
        observation.omc_install_audit, "audit_target", replace_before_audit
    )
    with pytest.raises(observation.CaptureError, match="live_install_identity_invalid"):
        observation.enable_live_observation(
            root,
            executor_surface="codex",
            repo_id="repo",
            registration=json.loads((tmp_path / "registration.json").read_text()),
        )
    assert not policy_path.exists()


def test_live_enable_rejects_non_utf8_install_receipt(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    policy_path = root / ".omc" / "observation-policy.json"
    policy_path.unlink()
    receipt_path = root / ".omc" / "install-receipt.json"
    receipt = receipt_path.read_text(encoding="utf-8")
    receipt_path.write_bytes(receipt.encode("utf-16"))

    with pytest.raises(observation.CaptureError, match="live_install_identity_invalid"):
        observation.enable_live_observation(
            root,
            executor_surface="codex",
            repo_id="repo",
            registration=json.loads((tmp_path / "registration.json").read_text()),
        )
    assert not policy_path.exists()


def test_live_observation_captures_every_eligible_start_before_closure(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    starts = [observation.start_live_observation(root)]
    for ordinal in range(2, 7):
        pending = _replace_live_pending(root, pending, ordinal)
        starts.append(observation.start_live_observation(root))

    assert [item["selection_ordinal"] for item in starts] == [1, 2, 3, 4, 5, 6]
    assert starts[5]["status"] == "COLLECTING"
    assert (
        root / ".omc" / "observations" / "live" / f"{6:032x}" / "start.json"
    ).exists()


def test_live_allocation_is_atomic_without_a_local_sample_cap(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    for ordinal in range(2, 5):
        pending = _replace_live_pending(root, pending, ordinal)
        observation.start_live_observation(root)
    policy = observation._live_policy(root)
    fifth = _replace_live_pending(root, pending, 5)
    sixth = _replace_live_pending(root, fifth, 6)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: observation._allocate_live_start(root, policy, item),
                (fifth, sixth),
            )
        )

    assert [item["status"] for item in results] == ["COLLECTING", "COLLECTING"]
    starts = observation._live_cohort_starts(root, policy)
    assert [item["selection_ordinal"] for item in starts] == [1, 2, 3, 4, 5, 6]


def test_live_closure_selects_global_first_five_across_repositories(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    roster = {
        "repo-a": tmp_path / "first" / "repo",
        "repo-b": tmp_path / "second" / "repo",
    }
    registration = observation.build_live_registration(
        study_id="completion-quality-feasibility-01",
        repository_roots=roster,
        observation_started_at=observation._now().isoformat(),
        output=tmp_path / "registration.json",
    )
    first, pending = _live_repo(
        tmp_path / "first", repo_id="repo-a", repository_roots=roster,
        registration=registration,
    )
    second, second_pending = _live_repo(
        tmp_path / "second", repo_id="repo-b", repository_roots=roster,
        registration=registration,
    )
    starts = []
    for root, current, ordinals in (
        (first, pending, (1, 3, 5)),
        (second, second_pending, (2, 4, 6)),
    ):
        for ordinal in ordinals:
            current = _replace_live_pending(root, current, ordinal)
            start = observation.start_live_observation(root)
            start["started_at"] = (
                datetime.fromisoformat(registration["observation_started_at"])
                + timedelta(minutes=ordinal)
            ).isoformat()
            start["start_sha256"] = observation.canonical_sha256(
                {**start, "start_sha256": ""}
            )
            (root / ".omc" / "observations" / "live" / current["work_id"] / "start.json").write_text(
                json.dumps(start), encoding="utf-8"
            )
            starts.append(start)

    result = observation.close_live_cohort(
        {"repo-a": first, "repo-b": second},
        registration=registration,
        closed_at=registration["observation_ends_at"],
        output=tmp_path / "closure.json",
    )

    assert result["decision"] == "INCONCLUSIVE"
    assert [(item["repo_id"], item["work_id"]) for item in result["selected"]] == [
        ("repo-a", f"{ordinal:032x}") if ordinal % 2 else ("repo-b", f"{ordinal:032x}")
        for ordinal in range(1, 6)
    ]
    assert result["population_count"] == 6
    assert (tmp_path / "closure.json").is_file()


def test_cross_session_prompt_uses_exact_work_identity_or_quarantines(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    roster = {
        "repo-a": tmp_path / "first" / "repo",
        "repo-b": tmp_path / "second" / "repo",
    }
    registration = observation.build_live_registration(
        study_id="completion-quality-feasibility-01",
        repository_roots=roster,
        observation_started_at=observation._now().isoformat(),
        output=tmp_path / "registration.json",
    )
    first, first_pending = _live_repo(
        tmp_path / "first", repo_id="repo-a", repository_roots=roster,
        registration=registration,
    )
    second, second_pending = _live_repo(
        tmp_path / "second", repo_id="repo-b", repository_roots=roster,
        registration=registration,
    )
    _replace_live_pending(second, second_pending, 2)
    for root in (first, second):
        observation.start_live_observation(root)
        (root / "app.py").write_text("after\n", encoding="utf-8")
        observation.capture_live_completion(
            root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
        )

    exact = observation.route_live_prompt(
        {"repo-a": first, "repo-b": second},
        raw_prompt="수용".encode(),
        executor_surface="codex",
        work_id=first_pending["work_id"],
        quarantine_root=tmp_path / "quarantine",
    )
    assert exact["status"] == "OUTCOME_RECORDED"
    assert observation.live_observation_status(first)["status"] == "CLOSED"

    ambiguous = observation.route_live_prompt(
        {"repo-a": first, "repo-b": second},
        raw_prompt=b"please fix this",
        executor_surface="codex",
        work_id="f" * 32,
        quarantine_root=tmp_path / "quarantine",
    )
    assert ambiguous["status"] == "TARGET_RESOLUTION_REQUIRED"
    quarantine = list((tmp_path / "quarantine").glob("*.json"))
    assert len(quarantine) == 1
    record = json.loads(quarantine[0].read_text(encoding="utf-8"))
    assert base64.b64decode(record["raw_prompt_base64"]) == b"please fix this"
    assert record["candidate_work_ids"] == [f"{2:032x}"]


def test_cross_session_prompt_finds_awaiting_work_after_pending_changes(tmp_path: Path) -> None:
    root, first_pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    second_pending = _replace_live_pending(root, first_pending, 2)
    observation.start_live_observation(root)

    result = observation.route_live_prompt(
        {"repo": root},
        raw_prompt="수용".encode(),
        executor_surface="codex",
        work_id=first_pending["work_id"],
        quarantine_root=tmp_path / "quarantine",
    )

    assert result["status"] == "OUTCOME_RECORDED"
    assert result["work_id"] == first_pending["work_id"]
    assert not (tmp_path / "quarantine").exists()
    assert observation.live_observation_status(root)["work_id"] == second_pending["work_id"]


def test_cross_session_prompt_quarantines_self_rehashed_malformed_start(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    start_path = (
        root / ".omc" / "observations" / "live" / pending["work_id"] / "start.json"
    )
    start = json.loads(start_path.read_text(encoding="utf-8"))
    start.pop("session_ids")
    start["start_sha256"] = observation.canonical_sha256(
        {**start, "start_sha256": ""}
    )
    start_path.write_text(json.dumps(start), encoding="utf-8")

    result = observation.route_live_prompt(
        {"repo": root},
        raw_prompt="수용".encode(),
        executor_surface="codex",
        work_id=pending["work_id"],
        quarantine_root=tmp_path / "quarantine",
    )

    assert result["status"] == "TARGET_RESOLUTION_REQUIRED"
    assert len(list((tmp_path / "quarantine").glob("*.json"))) == 1


def test_cross_session_prompt_quarantines_completion_with_wrong_start_baseline(
    tmp_path: Path,
) -> None:
    root, first_pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    _tamper_live_completion_baseline(root, first_pending["work_id"])
    second_pending = _replace_live_pending(root, first_pending, 2)
    observation.start_live_observation(root)

    result = observation.route_live_prompt(
        {"repo": root},
        raw_prompt="수용".encode(),
        executor_surface="codex",
        work_id=first_pending["work_id"],
        quarantine_root=tmp_path / "quarantine",
    )

    assert result["status"] == "TARGET_RESOLUTION_REQUIRED"
    assert observation.live_observation_status(root)["work_id"] == second_pending["work_id"]


def test_cross_session_prompt_without_work_identity_does_not_quarantine(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    result = observation.route_live_prompt(
        {"repo": root},
        raw_prompt=b"start another task",
        executor_surface="codex",
        work_id=None,
        quarantine_root=tmp_path / "quarantine",
    )
    assert result["status"] == "WORK_ID_REQUIRED"
    assert not (tmp_path / "quarantine").exists()


def test_live_prompt_uses_one_pending_snapshot_when_current_work_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, first_pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    second_pending = {**first_pending, "work_id": "b" * 32}
    snapshots = iter((first_pending, second_pending))
    monkeypatch.setattr(observation, "_live_pending", lambda _root: next(snapshots))

    result = observation.record_live_prompt(
        root,
        raw_prompt=b"please explain",
        executor_surface="codex",
    )

    assert result["work_id"] == first_pending["work_id"]
    assert (
        root
        / ".omc"
        / "observations"
        / "live"
        / first_pending["work_id"]
        / "followup-001.json"
    ).is_file()
    assert not (
        root
        / ".omc"
        / "observations"
        / "live"
        / second_pending["work_id"]
        / "followup-001.json"
    ).exists()


def test_expected_live_prompt_state_does_not_create_capture_failure(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc_completion_observation.py",
            "live-prompt",
            "--target",
            str(root),
            "--executor-surface",
            "codex",
        ],
        env={**os.environ, "OMC_LIVE_PROMPT_BASE64": base64.b64encode(b"hello").decode()},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["reason"] == "live_prompt_not_expected"
    assert not (root / ".omc" / "observations" / "live-failures").exists()


def test_live_closure_rejects_self_rehashed_raw_completion_tampering(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    roster = {
        "repo-a": tmp_path / "first" / "repo",
        "repo-b": tmp_path / "second" / "repo",
    }
    registration = observation.build_live_registration(
        study_id="completion-quality-feasibility-01",
        repository_roots=roster,
        observation_started_at=observation._now().isoformat(),
        output=tmp_path / "registration.json",
    )
    first, _ = _live_repo(
        tmp_path / "first", repo_id="repo-a", repository_roots=roster,
        registration=registration,
    )
    second, _ = _live_repo(
        tmp_path / "second", repo_id="repo-b", repository_roots=roster,
        registration=registration,
    )
    for root in (first, second):
        observation.start_live_observation(root)
        (root / "app.py").write_text("after\n", encoding="utf-8")
        completion = observation.capture_live_completion(
            root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
        )
        path = root / ".omc" / "observations" / "live" / ("a" * 32) / "first-completion.json"
        completion["raw_report_base64"] = base64.b64encode(b"forged").decode()
        completion["completion_snapshot_sha256"] = observation.canonical_sha256(
            {**completion, "completion_snapshot_sha256": ""}
        )
        path.write_text(json.dumps(completion), encoding="utf-8")
    result = observation.close_live_cohort(
        {"repo-a": first, "repo-b": second},
        registration=registration,
        closed_at=registration["observation_ends_at"],
        output=tmp_path / "closure.json",
    )
    assert result["decision"] == "CAPTURE_FAILED"


@pytest.mark.parametrize(
    "malformed_snapshot",
    [
        "garbage",
        {"path": "app.py", "state": "present", "byte_length": 6},
        {"path": "app.py", "state": "unknown", "byte_length": 6, "sha256": "f" * 64},
    ],
)
def test_live_closure_rejects_self_rehashed_malformed_file_snapshot(
    tmp_path: Path, malformed_snapshot: object
) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    roster = {
        "repo-a": tmp_path / "first" / "repo",
        "repo-b": tmp_path / "second" / "repo",
    }
    registration = observation.build_live_registration(
        study_id="completion-quality-feasibility-01",
        repository_roots=roster,
        observation_started_at=observation._now().isoformat(),
        output=tmp_path / "registration.json",
    )
    for parent, repo_id in ((tmp_path / "first", "repo-a"), (tmp_path / "second", "repo-b")):
        root, _ = _live_repo(
            parent, repo_id=repo_id, repository_roots=roster, registration=registration
        )
        observation.start_live_observation(root)
        (root / "app.py").write_text("after\n", encoding="utf-8")
        completion = observation.capture_live_completion(
            root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
        )
        completion["file_snapshots"] = [malformed_snapshot]
        completion["completion_snapshot_sha256"] = observation.canonical_sha256(
            {**completion, "completion_snapshot_sha256": ""}
        )
        path = root / ".omc" / "observations" / "live" / ("a" * 32) / "first-completion.json"
        path.write_text(json.dumps(completion), encoding="utf-8")

    result = observation.close_live_cohort(
        roster,
        registration=registration,
        closed_at=registration["observation_ends_at"],
        output=tmp_path / "closure.json",
    )
    assert result["decision"] == "CAPTURE_FAILED"


@pytest.mark.parametrize("mutation", ["malformed_unrun", "missing_status", "unknown_field"])
def test_live_closure_rejects_self_rehashed_malformed_completion_schema(
    tmp_path: Path, mutation: str
) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    roster = {
        "repo-a": tmp_path / "first" / "repo",
        "repo-b": tmp_path / "second" / "repo",
    }
    registration = observation.build_live_registration(
        study_id="completion-quality-feasibility-01",
        repository_roots=roster,
        observation_started_at=observation._now().isoformat(),
        output=tmp_path / "registration.json",
    )
    for parent, repo_id in ((tmp_path / "first", "repo-a"), (tmp_path / "second", "repo-b")):
        root, pending = _live_repo(
            parent, repo_id=repo_id, repository_roots=roster, registration=registration
        )
        observation.start_live_observation(root)
        (root / "app.py").write_text("after\n", encoding="utf-8")
        completion = observation.capture_live_completion(
            root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
        )
        if mutation == "malformed_unrun":
            completion["unrun_items"] = [{}]
        elif mutation == "missing_status":
            completion.pop("status")
        else:
            completion["unexpected"] = True
        completion["completion_snapshot_sha256"] = observation.canonical_sha256(
            {**completion, "completion_snapshot_sha256": ""}
        )
        path = (
            root
            / ".omc"
            / "observations"
            / "live"
            / pending["work_id"]
            / "first-completion.json"
        )
        path.write_text(json.dumps(completion), encoding="utf-8")

    result = observation.close_live_cohort(
        roster,
        registration=registration,
        closed_at=registration["observation_ends_at"],
        output=tmp_path / "closure.json",
    )
    assert result["decision"] == "CAPTURE_FAILED"


def test_live_closure_rejects_completion_with_wrong_start_baseline(tmp_path: Path) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    roster = {
        "repo-a": tmp_path / "first" / "repo",
        "repo-b": tmp_path / "second" / "repo",
    }
    registration = observation.build_live_registration(
        study_id="completion-quality-feasibility-01",
        repository_roots=roster,
        observation_started_at=observation._now().isoformat(),
        output=tmp_path / "registration.json",
    )
    for parent, repo_id in ((tmp_path / "first", "repo-a"), (tmp_path / "second", "repo-b")):
        root, pending = _live_repo(
            parent, repo_id=repo_id, repository_roots=roster, registration=registration
        )
        observation.start_live_observation(root)
        (root / "app.py").write_text("after\n", encoding="utf-8")
        observation.capture_live_completion(
            root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
        )
        _tamper_live_completion_baseline(root, pending["work_id"])

    result = observation.close_live_cohort(
        roster,
        registration=registration,
        closed_at=registration["observation_ends_at"],
        output=tmp_path / "closure.json",
    )
    assert result["decision"] == "CAPTURE_FAILED"


def test_live_status_rejects_tampered_completion_snapshot(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    completed = observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    completed["raw_report_sha256"] = "f" * 64
    path = root / ".omc" / "observations" / "live" / ("a" * 32) / "first-completion.json"
    path.write_text(json.dumps(completed), encoding="utf-8")
    with pytest.raises(observation.CaptureError, match="live_observation_invalid"):
        observation.live_observation_status(root)


def test_live_status_rejects_self_rehashed_malformed_completion_schema(
    tmp_path: Path,
) -> None:
    root, pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    completed = observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    completed.pop("status")
    completed["completion_snapshot_sha256"] = observation.canonical_sha256(
        {**completed, "completion_snapshot_sha256": ""}
    )
    path = (
        root
        / ".omc"
        / "observations"
        / "live"
        / pending["work_id"]
        / "first-completion.json"
    )
    path.write_text(json.dumps(completed), encoding="utf-8")

    with pytest.raises(observation.CaptureError, match="live_observation_binding_mismatch"):
        observation.live_observation_status(root)


def test_live_status_rejects_completion_with_wrong_start_baseline(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    _tamper_live_completion_baseline(root, pending["work_id"])

    with pytest.raises(observation.CaptureError, match="live_observation_binding_mismatch"):
        observation.live_observation_status(root)


def test_codex_task_and_review_surfaces_drive_live_observation_without_user_copy() -> None:
    task_skill = Path(".agents/skills/omc-task/SKILL.md").read_text(encoding="utf-8")
    review_skill = Path(".agents/skills/omc-review/SKILL.md").read_text(encoding="utf-8")
    assert "omc_completion_observation.py live-start" in task_skill
    assert "omc_completion_observation.py live-capture" in task_skill
    assert "관찰 실패는 구현을 차단하지" in task_skill
    assert "omc_completion_observation.py live-status" in review_skill
    assert "수용 / 수정 필요 / 보류" in review_skill
    assert "사용자에게 원문 복사를 요구하지" in review_skill
    hook_template = Path("templates/.agent-hooks/omc-prompt-inject.sh").read_text(
        encoding="utf-8"
    )
    assert "live-route-prompt" in hook_template
    assert "OMC_COMPLETION_OBSERVATION_REGISTRY" in hook_template
    assert "OMC_COMPLETION_OBSERVATION_QUARANTINE" in hook_template


def test_live_start_rejects_path_traversal_work_id(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    pending["work_id"] = "../../../../escaped"
    (root / ".omc" / "state" / "pending-completion.json").write_text(
        json.dumps(pending), encoding="utf-8"
    )
    with pytest.raises(observation.CaptureError, match="pending_completion_invalid"):
        observation.start_live_observation(root)
    assert not (tmp_path / "escaped" / "start.json").exists()


@pytest.mark.parametrize("classification", observation.TAXONOMY)
def test_live_prompt_preserves_every_followup_taxonomy(
    tmp_path: Path, classification: str
) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    followup = observation.record_live_prompt(
        root, raw_prompt=b"please revisit", executor_surface="codex"
    )
    assert followup["classification_status"] == "pending_user_confirmation"
    classified = observation.classify_live_followup(
        root,
        followup_index=1,
        classification=classification,
        raw_confirmation=classification.encode(),
    )
    assert classified["classification"] == classification
    assert classified["primary_correction"] is (
        classification in observation.PRIMARY_CORRECTIONS
    )
    assert base64.b64decode(classified["raw_confirmation_base64"]) == classification.encode()


def test_live_prompt_records_exact_acceptance_without_review_skill_reentry(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    accepted = observation.record_live_prompt(
        root, raw_prompt="수용".encode(), executor_surface="codex"
    )
    assert accepted["outcome"] == "accepted"
    assert observation.live_observation_status(root)["status"] == "CLOSED"
    hook = Path(".agent-hooks/omc-prompt-inject.sh").read_text(encoding="utf-8")
    assert "live-prompt" in hook
    assert "OMC_LIVE_PROMPT" in hook


def test_live_prompt_uses_next_taxonomy_reply_to_classify_pending_followup(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    observation.record_live_prompt(
        root, raw_prompt=b"why was this omitted?", executor_surface="codex"
    )
    classified = observation.record_live_prompt(
        root, raw_prompt="요구사항 누락".encode(), executor_surface="codex"
    )
    assert classified["status"] == "CLASSIFICATION_RECORDED"
    assert classified["classification"] == "missing_requirement"
    live_root = root / ".omc" / "observations" / "live" / ("a" * 32)
    assert len(list(live_root.glob("followup-*.json"))) == 1


def test_user_prompt_hook_records_acceptance_in_a_later_general_turn(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    hook = Path(".agent-hooks/omc-prompt-inject.sh").resolve()
    script = Path("scripts/omc_completion_observation.py").resolve()
    result = subprocess.run(
        [str(hook), "codex"],
        cwd=root,
        env={**os.environ, "PROMPT": "수용", "OMC_COMPLETION_OBSERVATION_SCRIPT": str(script)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "사용자 수용/보류 원문 기록 완료" in result.stdout
    assert observation.live_observation_status(root)["status"] == "CLOSED"


def test_write_once_removes_partial_file_when_serialization_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "receipt.json"

    def broken_dump(*args, **kwargs):
        args[1].write("partial")
        raise OSError("disk failure")

    monkeypatch.setattr(observation.json, "dump", broken_dump)
    with pytest.raises(OSError, match="disk failure"):
        observation._write_once(destination, {"value": 1})
    assert not destination.exists()


def test_live_pending_rejects_session_lineage_not_backed_by_session_receipts(tmp_path: Path) -> None:
    root, pending = _live_repo(tmp_path)
    pending.update({
        "session_id": "missing-session",
        "session_ids": ["session-a", "missing-session"],
        "rework_count": 1,
    })
    (root / ".omc" / "state" / "pending-completion.json").write_text(
        json.dumps(pending), encoding="utf-8"
    )
    with pytest.raises(observation.CaptureError, match="pending_completion_invalid"):
        observation.start_live_observation(root)


def test_live_acceptance_rejects_unclassified_followup(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    observation.record_live_prompt(
        root, raw_prompt=b"please revisit", executor_surface="codex"
    )
    with pytest.raises(observation.CaptureError, match="classification_required"):
        observation.record_live_prompt(
            root, raw_prompt="수용".encode(), executor_surface="codex"
        )
    live_root = root / ".omc" / "observations" / "live" / ("a" * 32)
    assert not (live_root / "terminal.json").exists()


def test_user_prompt_hook_surfaces_active_observation_failure(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    completed = observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    completed["raw_report_sha256"] = "f" * 64
    live_root = root / ".omc" / "observations" / "live" / ("a" * 32)
    (live_root / "first-completion.json").write_text(json.dumps(completed), encoding="utf-8")
    result = subprocess.run(
        [str(Path(".agent-hooks/omc-prompt-inject.sh").resolve()), "codex"],
        cwd=root,
        env={
            **os.environ,
            "PROMPT": "수용",
            "OMC_COMPLETION_OBSERVATION_SCRIPT": str(
                Path("scripts/omc_completion_observation.py").resolve()
            ),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "OBSERVATION_INVALID" in result.stdout
    assert "live_observation_invalid" in result.stdout
    failures = list(
        (root / ".omc" / "observations" / "live-failures").glob("*.json")
    )
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["command"] == "live-prompt"
    assert failure["reason"] == "live_observation_invalid"


def test_user_prompt_hook_preserves_trailing_newlines_as_utf8_bytes(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    raw_prompt = "수용\n\n"
    result = subprocess.run(
        [str(Path(".agent-hooks/omc-prompt-inject.sh").resolve()), "codex"],
        cwd=root,
        env={
            **os.environ,
            "PROMPT": raw_prompt,
            "OMC_COMPLETION_OBSERVATION_SCRIPT": str(
                Path("scripts/omc_completion_observation.py").resolve()
            ),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    terminal = json.loads(
        (root / ".omc" / "observations" / "live" / ("a" * 32) / "terminal.json").read_text()
    )
    assert base64.b64decode(terminal["raw_followup_base64"]) == raw_prompt.encode()


def test_user_prompt_hook_preserves_stdin_json_trailing_newlines(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    raw_prompt = "수용\n\n"
    environment = {
        **os.environ,
        "OMC_COMPLETION_OBSERVATION_SCRIPT": str(
            Path("scripts/omc_completion_observation.py").resolve()
        ),
    }
    environment.pop("PROMPT", None)
    result = subprocess.run(
        [str(Path(".agent-hooks/omc-prompt-inject.sh").resolve()), "codex"],
        cwd=root,
        env=environment,
        input=json.dumps({"prompt": raw_prompt}, ensure_ascii=False),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    terminal = json.loads(
        (root / ".omc" / "observations" / "live" / ("a" * 32) / "terminal.json").read_text()
    )
    assert base64.b64decode(terminal["raw_followup_base64"]) == raw_prompt.encode()


def test_shared_prompt_hook_does_not_collect_claude_prompt_in_codex_study(tmp_path: Path) -> None:
    root, _ = _live_repo(tmp_path)
    observation.start_live_observation(root)
    (root / "app.py").write_text("after\n", encoding="utf-8")
    observation.capture_live_completion(
        root, raw_report=b"done", raw_verification=b"pass", unrun_items=[]
    )
    subprocess.run(
        [str(Path(".agent-hooks/omc-prompt-inject.sh").resolve()), "claude"],
        cwd=root,
        env={
            **os.environ,
            "PROMPT": "수용",
            "OMC_COMPLETION_OBSERVATION_SCRIPT": str(
                Path("scripts/omc_completion_observation.py").resolve()
            ),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert observation.live_observation_status(root)["status"] == "AWAITING_USER_OUTCOME"


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
