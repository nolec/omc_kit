from __future__ import annotations

import base64
from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_work_packet_feasibility as feasibility
import omc_rfc3161_timestamp as timestamp
from omc_work_packet_feasibility import (
    build_preregistration,
    capture_case,
    canonical_sha256,
    main,
    validate_preregistration,
    validate_study,
)


CREATED_AT = "2026-08-28T10:00:00Z"
REGISTRATION_KEY = Ed25519PrivateKey.generate()
COLLECTOR_KEY = Ed25519PrivateKey.generate()
EXECUTOR_KEY = Ed25519PrivateKey.generate()
SOURCE_KEY = Ed25519PrivateKey.generate()
REAL_VALIDATE_SIGSTORE_RECEIPT = (
    feasibility.preregistry.validate_sigstore_registration_receipt
)


def _trusted_root() -> dict[str, object]:
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


def _registration_authority() -> dict[str, object]:
    trusted_root = _trusted_root()
    return timestamp.trust_identity(
        trusted_root,
        expected_trusted_root_sha256=timestamp.trusted_root_sha256(trusted_root),
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _trusted_registry_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feasibility.preregistry,
        "validate_registry_anchor",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        feasibility,
        "_validate_source_inventory_anchor",
        lambda *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        feasibility.preregistry,
        "validate_sigstore_registration_receipt",
        lambda *args, **kwargs: None,
    )


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _signed_receipt(
    payload: dict[str, object],
    *,
    private_key: Ed25519PrivateKey,
    signer: str,
) -> dict[str, object]:
    receipt = deepcopy(payload)
    receipt["signoff"] = {
        "signer": signer,
        "signer_public_key": _public_key(private_key),
        "signature": "",
    }
    unsigned = deepcopy(receipt)
    unsigned.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = canonical_sha256(unsigned)
    signing = deepcopy(receipt)
    signing["signoff"]["signature"] = ""
    signing_bytes = json.dumps(
        signing,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt["signoff"]["signature"] = base64.b64encode(
        private_key.sign(signing_bytes)
    ).decode("ascii")
    return receipt


def _manifest() -> dict[str, object]:
    return build_preregistration(
        study_id="work-packet-prospective-v1",
        created_at=CREATED_AT,
        registration_authority_public_key=_public_key(REGISTRATION_KEY),
        registration_authority=_registration_authority(),
        completion_collector_public_key=_public_key(COLLECTOR_KEY),
        executor_public_key=_public_key(EXECUTOR_KEY),
        source_snapshot_public_key=_public_key(SOURCE_KEY),
        source_inventory={
            "source_id": "omc-session-ledger",
            "inventory_path": ".omc/work-packet/source-inventory.json",
            "commit_policy": "git_commit_required",
        },
    )


def _registration_receipt(manifest: dict[str, object]) -> dict[str, object]:
    return _signed_receipt(
        {
            "schema_version": "omc-work-packet-registration-receipt/v1",
            "study_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "registered_at": "2026-08-28T09:59:00Z",
            "observation_starts_at": manifest["created_at"],
        },
        private_key=REGISTRATION_KEY,
        signer="work-packet-registration-authority-v1",
    )


def _registration_proof(manifest: dict[str, object]) -> dict[str, object]:
    trusted_root = _trusted_root()
    return {
        "registry_record": {
            "schema_version": 1,
            "batch_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
        },
        "repository_root": "/tmp/work-packet-registry",
        "registration_receipt": {
            "schema_version": 2,
            "status": "registered",
            "batch_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "registry_commit": "d" * 40,
            "registry_path": ".omc/registry/work-packet.json",
            "registered_at": "2026-08-28T09:59:00Z",
            "registration_evidence": {"fixture": True},
            "receipt_sha256": "e" * 64,
        },
        "trusted_root": trusted_root,
        "approved_trusted_root_sha256": timestamp.trusted_root_sha256(trusted_root),
    }


def _completion_receipt(
    manifest: dict[str, object],
    evidence: dict[str, object],
    *,
    ordinal: int,
    previous_receipt_sha256: str | None,
) -> dict[str, object]:
    return _signed_receipt(
        {
            "schema_version": "omc-work-packet-completion-receipt/v1",
            "study_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "ordinal": ordinal,
            "previous_receipt_sha256": previous_receipt_sha256,
            "feature_id": evidence["feature_id"],
            "work_class": evidence["work_class"],
            "session_id": evidence["session_id"],
            "started_at": evidence["started_at"],
            "completed_at": evidence["completed_at"],
        },
        private_key=COLLECTOR_KEY,
        signer="work-packet-completion-collector-v1",
    )


def _completion_ledger(
    manifest: dict[str, object],
    entries: list[dict[str, object]],
    *,
    source_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot = source_snapshot or _source_snapshot(manifest, entries)
    return _signed_receipt(
        {
            "schema_version": "omc-work-packet-completion-ledger/v1",
            "study_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "observed_through": max(entry["completed_at"] for entry in entries),
            "entries": entries,
            "source_snapshot_sha256": snapshot["receipt_sha256"],
        },
        private_key=COLLECTOR_KEY,
        signer="work-packet-completion-collector-v1",
    )


def _source_snapshot(
    manifest: dict[str, object],
    entries: list[dict[str, object]],
) -> dict[str, object]:
    inventory_path = manifest["source_inventory"]["inventory_path"]
    return _signed_receipt(
        {
            "schema_version": "omc-work-packet-source-snapshot/v1",
            "study_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "source_id": "omc-session-ledger",
            "inventory_commit": "f" * 40,
            "inventory_path": inventory_path,
            "inventory_sha256": canonical_sha256(entries),
            "observed_from": manifest["created_at"],
            "observed_through": max(entry["completed_at"] for entry in entries),
            "entry_count": len(entries),
            "entries_sha256": canonical_sha256(entries),
            "entries": entries,
        },
        private_key=SOURCE_KEY,
        signer="work-packet-source-snapshot-authority-v1",
    )


def _provenance(
    manifest: dict[str, object],
    entries: list[dict[str, object]],
) -> dict[str, object]:
    source_snapshot = _source_snapshot(manifest, entries)
    return {
        "registration_proof": _registration_proof(manifest),
        "source_snapshot": source_snapshot,
        "completion_ledger": _completion_ledger(
            manifest, entries, source_snapshot=source_snapshot
        ),
    }


def _ledger_entry(evidence: dict[str, object]) -> dict[str, object]:
    return {
        key: evidence[key]
        for key in (
            "feature_id",
            "work_class",
            "session_id",
            "started_at",
            "completed_at",
        )
    }


def _execution_receipt(
    manifest: dict[str, object],
    evidence: dict[str, object],
    *,
    request: bytes,
    baseline_output: bytes,
) -> dict[str, object]:
    return _signed_receipt(
        {
            "schema_version": "omc-work-packet-execution-receipt/v1",
            "study_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "execution_id": f"exec-{evidence['session_id']}",
            "session_id": evidence["session_id"],
            "request_sha256": canonical_sha256(request),
            "raw_output_sha256": canonical_sha256(baseline_output),
            "completed_at": evidence["completed_at"],
        },
        private_key=EXECUTOR_KEY,
        signer="work-packet-executor-v1",
    )


def _capture_case(**kwargs):
    manifest = kwargs["manifest"]
    study_root = Path(kwargs["study_root"])
    evidence = kwargs["evidence"]
    existing = sorted((study_root / "cases").glob("*/completion-receipt.json"))
    previous = None
    if existing:
        previous = json.loads(existing[-1].read_text(encoding="utf-8"))[
            "receipt_sha256"
        ]
    prior_evidence = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((study_root / "cases").glob("*/evidence.json"))
    ]
    ledger_entries = [_ledger_entry(item) for item in prior_evidence]
    ledger_entries.append(_ledger_entry(evidence))
    source_snapshot = _source_snapshot(manifest, ledger_entries)
    return capture_case(
        **kwargs,
        registration_receipt=_registration_receipt(manifest),
        registration_proof=_registration_proof(manifest),
        source_snapshot=source_snapshot,
        completion_ledger=_completion_ledger(
            manifest, ledger_entries, source_snapshot=source_snapshot
        ),
        completion_receipt=_completion_receipt(
            manifest,
            evidence,
            ordinal=len(existing) + 1,
            previous_receipt_sha256=previous,
        ),
        execution_receipt=_execution_receipt(
            manifest,
            evidence,
            request=kwargs["request"],
            baseline_output=kwargs["baseline_output"],
        ),
    )


def _validate_study(*, manifest: dict[str, object], study_root: Path):
    return validate_study(
        manifest=manifest,
        study_root=study_root,
        registration_receipt=_registration_receipt(manifest),
        registration_proof=_registration_proof(manifest),
    )


def _evidence(
    *,
    started_at: str = "2026-08-28T10:01:00Z",
    feature_id: str = "feature-01",
) -> dict[str, object]:
    return {
        "schema_version": "omc-work-packet-case-evidence/v1",
        "feature_id": feature_id,
        "work_class": "implementation",
        "started_at": started_at,
        "completed_at": "2026-08-28T10:03:00Z",
        "session_id": "20260828T100100-case0001",
        "git": {
            "commit": "a" * 40,
            "diff_sha256": "b" * 64,
            "changed_file_count": 2,
        },
        "verification": [
            {
                "command": "pytest tests/test_feature.py -q",
                "exit_code": 0,
                "stdout": "1 passed\n",
                "stderr": "",
            }
        ],
        "review": {
            "verdict": "APPROVE",
            "raw_output": "No findings.\nVERDICT: APPROVE\n",
        },
    }


def test_preregistration_freezes_next_five_selection_and_metric_contract() -> None:
    manifest = _manifest()

    assert validate_preregistration(manifest) == manifest
    assert manifest["selection_policy"] == {
        "mode": "next_eligible_completion_v1",
        "target_case_count": 5,
        "eligible_work_classes": ["implementation", "benchmark_maintenance"],
        "excluded": ["synthetic", "document_only", "roadmap_only"],
        "merge_same_feature_followups": True,
    }
    assert manifest["metrics"]["minimum_time_improvement_ratio"] == 0.20
    assert manifest["trusted_authorities"] == {
        "registration_authority_public_key": _public_key(REGISTRATION_KEY),
        "registration_authority": _registration_authority(),
        "completion_collector_public_key": _public_key(COLLECTOR_KEY),
        "executor_public_key": _public_key(EXECUTOR_KEY),
        "source_snapshot_public_key": _public_key(SOURCE_KEY),
    }
    assert manifest["source_inventory"] == {
        "source_id": "omc-session-ledger",
        "inventory_path": ".omc/work-packet/source-inventory.json",
        "commit_policy": "git_commit_required",
    }
    assert manifest["preregistration_sha256"] == canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "preregistration_sha256"
        }
    )


def test_preregistration_rejects_tampering() -> None:
    manifest = _manifest()
    manifest["selection_policy"]["target_case_count"] = 4

    with pytest.raises(ValueError, match="preregistration_hash_mismatch"):
        validate_preregistration(manifest)


def test_capture_preserves_exact_raw_bytes_and_binds_evidence(tmp_path: Path) -> None:
    request = b"request with trailing spaces  \n"
    baseline = b"raw output\n\nVERDICT: APPROVE\n"
    evidence = _evidence()

    receipt = _capture_case(
        manifest=_manifest(),
        study_root=tmp_path,
        case_id="case-01",
        request=request,
        baseline_output=baseline,
        evidence=evidence,
        captured_at="2026-08-28T10:04:00Z",
    )

    case_root = tmp_path / "cases/case-01"
    assert (case_root / "request.txt").read_bytes() == request
    assert (case_root / "baseline-output.txt").read_bytes() == baseline
    assert receipt["request_sha256"] == canonical_sha256(request)
    assert receipt["baseline_output_sha256"] == canonical_sha256(baseline)
    assert receipt["evidence_sha256"] == canonical_sha256(evidence)
    assert receipt["receipt_sha256"] == canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )


def test_capture_rejects_registration_issued_after_observation_start(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    evidence = _evidence()
    request = b"request"
    baseline = b"output"
    registration = _registration_receipt(manifest)
    registration["registered_at"] = "2026-08-28T10:01:00Z"
    registration = _signed_receipt(
        {
            key: value
            for key, value in registration.items()
            if key not in {"receipt_sha256", "signoff"}
        },
        private_key=REGISTRATION_KEY,
        signer="work-packet-registration-authority-v1",
    )

    with pytest.raises(ValueError, match="registration_after_observation_start"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=request,
            baseline_output=baseline,
            evidence=evidence,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=registration,
            **_provenance(manifest, [_ledger_entry(evidence)]),
            completion_receipt=_completion_receipt(
                manifest, evidence, ordinal=1, previous_receipt_sha256=None
            ),
            execution_receipt=_execution_receipt(
                manifest, evidence, request=request, baseline_output=baseline
            ),
        )


def test_capture_rejects_non_contiguous_completion_ordinal(tmp_path: Path) -> None:
    manifest = _manifest()
    first_evidence = _evidence(feature_id="feature-01")
    _capture_case(
        manifest=manifest,
        study_root=tmp_path,
        case_id="case-01",
        request=b"first request",
        baseline_output=b"first output",
        evidence=first_evidence,
        captured_at="2026-08-28T10:04:00Z",
    )
    second_evidence = {
        **_evidence(feature_id="feature-02"),
        "session_id": "session-02",
    }

    with pytest.raises(ValueError, match="case_completion_ordinal_mismatch"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-02",
            request=b"second request",
            baseline_output=b"second output",
            evidence=second_evidence,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=_registration_receipt(manifest),
            **_provenance(
                manifest,
                [_ledger_entry(first_evidence), _ledger_entry(second_evidence)],
            ),
            completion_receipt=_completion_receipt(
                manifest,
                second_evidence,
                ordinal=3,
                previous_receipt_sha256="0" * 64,
            ),
            execution_receipt=_execution_receipt(
                manifest,
                second_evidence,
                request=b"second request",
                baseline_output=b"second output",
            ),
        )


def test_capture_rejects_skipping_earlier_eligible_ledger_entry(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    earlier = _evidence(feature_id="feature-01")
    selected = {
        **_evidence(feature_id="feature-02"),
        "session_id": "session-02",
        "completed_at": "2026-08-28T10:03:30Z",
    }

    with pytest.raises(ValueError, match="completion_ledger_selection_mismatch"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=selected,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=_registration_receipt(manifest),
            **_provenance(manifest, [_ledger_entry(earlier), _ledger_entry(selected)]),
            completion_receipt=_completion_receipt(
                manifest, selected, ordinal=1, previous_receipt_sha256=None
            ),
            execution_receipt=_execution_receipt(
                manifest, selected, request=b"request", baseline_output=b"output"
            ),
        )


def test_capture_rejects_rewriting_previous_completion_ledger(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    first = _evidence(feature_id="feature-01")
    _capture_case(
        manifest=manifest,
        study_root=tmp_path,
        case_id="case-01",
        request=b"first request",
        baseline_output=b"first output",
        evidence=first,
        captured_at="2026-08-28T10:04:00Z",
    )
    rewritten_first = {**first, "session_id": "rewritten-session"}
    second = {
        **_evidence(feature_id="feature-02"),
        "session_id": "session-02",
        "completed_at": "2026-08-28T10:03:30Z",
    }

    with pytest.raises(ValueError, match="completion_ledger_prefix_mismatch"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-02",
            request=b"second request",
            baseline_output=b"second output",
            evidence=second,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=_registration_receipt(manifest),
            **_provenance(
                manifest,
                [_ledger_entry(rewritten_first), _ledger_entry(second)],
            ),
            completion_receipt=_completion_receipt(
                manifest,
                second,
                ordinal=2,
                previous_receipt_sha256=json.loads(
                    (tmp_path / "cases/case-01/completion-receipt.json").read_text(
                        encoding="utf-8"
                    )
                )["receipt_sha256"],
            ),
            execution_receipt=_execution_receipt(
                manifest,
                second,
                request=b"second request",
                baseline_output=b"second output",
            ),
        )


def test_capture_rejects_boolean_completion_ordinal(tmp_path: Path) -> None:
    manifest = _manifest()
    evidence = _evidence()
    completion = _completion_receipt(
        manifest, evidence, ordinal=True, previous_receipt_sha256=None
    )

    with pytest.raises(ValueError, match="case_completion_ordinal_mismatch"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=evidence,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=_registration_receipt(manifest),
            **_provenance(manifest, [_ledger_entry(evidence)]),
            completion_receipt=completion,
            execution_receipt=_execution_receipt(
                manifest, evidence, request=b"request", baseline_output=b"output"
            ),
        )


def test_capture_rejects_registration_authority_key_reuse(tmp_path: Path) -> None:
    manifest = _manifest()
    evidence = _evidence()
    registration = _signed_receipt(
        {
            "schema_version": "omc-work-packet-registration-receipt/v1",
            "study_id": manifest["study_id"],
            "preregistration_sha256": manifest["preregistration_sha256"],
            "registered_at": "2026-08-28T09:59:00Z",
            "observation_starts_at": manifest["created_at"],
        },
        private_key=COLLECTOR_KEY,
        signer="work-packet-registration-authority-v1",
    )

    with pytest.raises(ValueError, match="registration_receipt_signer_untrusted"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=evidence,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=registration,
            **_provenance(manifest, [_ledger_entry(evidence)]),
            completion_receipt=_completion_receipt(
                manifest, evidence, ordinal=1, previous_receipt_sha256=None
            ),
            execution_receipt=_execution_receipt(
                manifest, evidence, request=b"request", baseline_output=b"output"
            ),
        )


def test_capture_rejects_untrusted_external_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()

    def reject_anchor(*args, **kwargs):
        raise ValueError("registry_anchor_untrusted")

    monkeypatch.setattr(
        feasibility.preregistry,
        "validate_registry_anchor",
        reject_anchor,
    )

    with pytest.raises(ValueError, match="registry_anchor_untrusted"):
        _capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=_evidence(),
            captured_at="2026-08-28T10:04:00Z",
        )


def test_external_registration_accepts_full_real_validator_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()
    proof = _registration_proof(manifest)
    registered_at = "2026-08-28T09:59:00+00:00"
    monkeypatch.setattr(
        feasibility.preregistry,
        "validate_sigstore_registration_receipt",
        REAL_VALIDATE_SIGSTORE_RECEIPT,
    )
    monkeypatch.setattr(
        feasibility.preregistry.rfc3161,
        "verify_registration_evidence",
        lambda evidence, **kwargs: {"gen_time": registered_at},
    )
    proof[
        "registration_receipt"
    ] = feasibility.preregistry.prepare_sigstore_registration_receipt(
        batch_id=manifest["study_id"],
        preregistration_sha256=manifest["preregistration_sha256"],
        registry_commit="d" * 40,
        registry_path=".omc/registry/work-packet.json",
        registration_authority=manifest["trusted_authorities"][
            "registration_authority"
        ],
        observation_starts_at=manifest["created_at"],
        registration_evidence={"gen_time": registered_at},
        trusted_root=proof["trusted_root"],
        approved_trusted_root_sha256=proof["approved_trusted_root_sha256"],
    )

    assert (
        feasibility._validate_external_registration_proof(proof, manifest=manifest)
        == proof
    )


def test_capture_rejects_snapshot_from_unregistered_source(tmp_path: Path) -> None:
    manifest = _manifest()
    evidence = _evidence()
    snapshot = _source_snapshot(manifest, [_ledger_entry(evidence)])
    snapshot["source_id"] = "attacker-selected-source"
    snapshot = _signed_receipt(
        {
            key: value
            for key, value in snapshot.items()
            if key not in {"receipt_sha256", "signoff"}
        },
        private_key=SOURCE_KEY,
        signer="work-packet-source-snapshot-authority-v1",
    )

    with pytest.raises(ValueError, match="source_snapshot_binding_mismatch"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=evidence,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=_registration_receipt(manifest),
            registration_proof=_registration_proof(manifest),
            source_snapshot=snapshot,
            completion_ledger=_completion_ledger(
                manifest,
                [_ledger_entry(evidence)],
                source_snapshot=snapshot,
            ),
            completion_receipt=_completion_receipt(
                manifest, evidence, ordinal=1, previous_receipt_sha256=None
            ),
            execution_receipt=_execution_receipt(
                manifest, evidence, request=b"request", baseline_output=b"output"
            ),
        )


def test_capture_rejects_unverifiable_source_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()

    def reject_inventory(*args, **kwargs):
        raise ValueError("source_inventory_anchor_invalid")

    monkeypatch.setattr(
        feasibility,
        "_validate_source_inventory_anchor",
        reject_inventory,
    )

    with pytest.raises(ValueError, match="source_inventory_anchor_invalid"):
        _capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=_evidence(),
            captured_at="2026-08-28T10:04:00Z",
        )


def test_source_inventory_anchor_reads_exact_committed_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()
    entries = [_ledger_entry(_evidence())]
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "inventory@example.com")
    _git(repository, "config", "user.name", "Inventory Test")
    inventory_path = repository / manifest["source_inventory"]["inventory_path"]
    inventory_path.parent.mkdir(parents=True)
    inventory_path.write_text(json.dumps(entries), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "freeze source inventory")
    inventory_commit = _git(repository, "rev-parse", "HEAD")
    snapshot = _source_snapshot(manifest, entries)
    snapshot["inventory_commit"] = inventory_commit

    monkeypatch.undo()
    feasibility._validate_source_inventory_anchor(
        snapshot,
        repository_root=str(repository),
        required_ancestor_commit=inventory_commit,
    )

    snapshot["entries"] = []
    with pytest.raises(ValueError, match="source_inventory_anchor_invalid"):
        feasibility._validate_source_inventory_anchor(
            snapshot,
            repository_root=str(repository),
            required_ancestor_commit=inventory_commit,
        )


def test_capture_rejects_completion_ledger_omitting_source_entry(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    earlier = _evidence(feature_id="feature-01")
    selected = {
        **_evidence(feature_id="feature-02"),
        "session_id": "session-02",
        "completed_at": "2026-08-28T10:03:30Z",
    }
    source_snapshot = _source_snapshot(
        manifest, [_ledger_entry(earlier), _ledger_entry(selected)]
    )

    with pytest.raises(ValueError, match="completion_ledger_source_mismatch"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=selected,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=_registration_receipt(manifest),
            registration_proof=_registration_proof(manifest),
            source_snapshot=source_snapshot,
            completion_ledger=_completion_ledger(
                manifest,
                [_ledger_entry(selected)],
                source_snapshot=source_snapshot,
            ),
            completion_receipt=_completion_receipt(
                manifest, selected, ordinal=1, previous_receipt_sha256=None
            ),
            execution_receipt=_execution_receipt(
                manifest, selected, request=b"request", baseline_output=b"output"
            ),
        )


def test_capture_rejects_raw_output_not_bound_to_executor_receipt(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    evidence = _evidence()
    request = b"request"

    with pytest.raises(ValueError, match="execution_raw_output_hash_mismatch"):
        capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=request,
            baseline_output=b"substituted output",
            evidence=evidence,
            captured_at="2026-08-28T10:04:00Z",
            registration_receipt=_registration_receipt(manifest),
            **_provenance(manifest, [_ledger_entry(evidence)]),
            completion_receipt=_completion_receipt(
                manifest, evidence, ordinal=1, previous_receipt_sha256=None
            ),
            execution_receipt=_execution_receipt(
                manifest,
                evidence,
                request=request,
                baseline_output=b"actual executor output",
            ),
        )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("work_class", "synthetic", "case_work_class_ineligible"),
        ("work_class", "document_only", "case_work_class_ineligible"),
        ("started_at", CREATED_AT, "case_not_prospective"),
    ],
)
def test_capture_rejects_ineligible_or_non_prospective_cases(
    tmp_path: Path,
    field: str,
    value: str,
    error: str,
) -> None:
    evidence = _evidence()
    evidence[field] = value

    with pytest.raises(ValueError, match=error):
        _capture_case(
            manifest=_manifest(),
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=evidence,
            captured_at="2026-08-28T10:04:00Z",
        )


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda value: value.update(verification=[]), "case_verification_missing"),
        (
            lambda value: value.update(review={"verdict": "APPROVE"}),
            "case_review_output_missing",
        ),
        (
            lambda value: value["git"].update(changed_file_count=0),
            "case_change_missing",
        ),
    ],
)
def test_capture_requires_complete_authoritative_evidence(
    tmp_path: Path,
    mutate,
    error: str,
) -> None:
    evidence = _evidence()
    mutate(evidence)

    with pytest.raises(ValueError, match=error):
        _capture_case(
            manifest=_manifest(),
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=evidence,
            captured_at="2026-08-28T10:04:00Z",
        )


def test_capture_rejects_duplicate_case_and_sixth_case(tmp_path: Path) -> None:
    manifest = _manifest()
    for index in range(1, 6):
        _capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id=f"case-{index:02d}",
            request=f"request-{index}".encode(),
            baseline_output=f"output-{index}".encode(),
            evidence={
                **_evidence(feature_id=f"feature-{index:02d}"),
                "session_id": f"session-{index}",
            },
            captured_at="2026-08-28T10:04:00Z",
        )

    with pytest.raises(ValueError, match="case_already_captured"):
        _capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-01",
            request=b"request",
            baseline_output=b"output",
            evidence=_evidence(),
            captured_at="2026-08-28T10:04:00Z",
        )

    with pytest.raises(ValueError, match="study_case_limit_reached"):
        _capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-06",
            request=b"request",
            baseline_output=b"output",
            evidence=_evidence(),
            captured_at="2026-08-28T10:04:00Z",
        )


def test_capture_merges_same_feature_followups_by_rejecting_second_case(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    _capture_case(
        manifest=manifest,
        study_root=tmp_path,
        case_id="case-01",
        request=b"first request",
        baseline_output=b"first output",
        evidence=_evidence(feature_id="same-feature"),
        captured_at="2026-08-28T10:04:00Z",
    )

    with pytest.raises(ValueError, match="case_feature_already_captured"):
        _capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id="case-02",
            request=b"followup request",
            baseline_output=b"followup output",
            evidence=_evidence(feature_id="same-feature"),
            captured_at="2026-08-28T10:04:00Z",
        )


def test_validate_study_reports_collecting_then_complete(tmp_path: Path) -> None:
    manifest = _manifest()
    assert (
        _validate_study(manifest=manifest, study_root=tmp_path)["status"]
        == "collecting"
    )

    for index in range(1, 6):
        _capture_case(
            manifest=manifest,
            study_root=tmp_path,
            case_id=f"case-{index:02d}",
            request=f"request-{index}".encode(),
            baseline_output=f"output-{index}".encode(),
            evidence={
                **_evidence(feature_id=f"feature-{index:02d}"),
                "session_id": f"session-{index}",
            },
            captured_at="2026-08-28T10:04:00Z",
        )

    report = _validate_study(manifest=manifest, study_root=tmp_path)
    assert report["status"] == "ready_for_projection"
    assert report["case_count"] == 5
    assert report["report_sha256"] == canonical_sha256(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )


def test_captured_evidence_tampering_is_detected(tmp_path: Path) -> None:
    manifest = _manifest()
    _capture_case(
        manifest=manifest,
        study_root=tmp_path,
        case_id="case-01",
        request=b"request",
        baseline_output=b"output",
        evidence=_evidence(),
        captured_at="2026-08-28T10:04:00Z",
    )
    evidence_path = tmp_path / "cases/case-01/evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["review"]["verdict"] = "BLOCK"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="case_evidence_hash_mismatch"):
        _validate_study(manifest=manifest, study_root=tmp_path)


def test_cli_preregister_and_validate_expose_direct_surface(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    study_root = tmp_path / "study"
    registration_path = tmp_path / "registration.json"
    registration_proof_path = tmp_path / "registration-proof.json"

    assert (
        main(
            [
                "preregister",
                "--study-id",
                "work-packet-prospective-v1",
                "--created-at",
                CREATED_AT,
                "--registration-authority-public-key",
                _public_key(REGISTRATION_KEY),
                "--registration-authority",
                json.dumps(_registration_authority()),
                "--completion-collector-public-key",
                _public_key(COLLECTOR_KEY),
                "--executor-public-key",
                _public_key(EXECUTOR_KEY),
                "--source-snapshot-public-key",
                _public_key(SOURCE_KEY),
                "--source-inventory",
                json.dumps(
                    {
                        "source_id": "omc-session-ledger",
                        "inventory_path": ".omc/work-packet/source-inventory.json",
                        "commit_policy": "git_commit_required",
                    }
                ),
                "--output",
                str(manifest_path),
            ]
        )
        == 0
    )
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["study_id"] == (
        "work-packet-prospective-v1"
    )
    assert json.loads(capsys.readouterr().out)["status"] == "preregistered"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registration_path.write_text(
        json.dumps(_registration_receipt(manifest)), encoding="utf-8"
    )
    registration_proof_path.write_text(
        json.dumps(_registration_proof(manifest)), encoding="utf-8"
    )

    assert (
        main(
            [
                "validate",
                "--manifest",
                str(manifest_path),
                "--study-root",
                str(study_root),
                "--registration-receipt",
                str(registration_path),
                "--registration-proof",
                str(registration_proof_path),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "collecting"


def test_cli_capture_binds_signed_provenance_receipts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _manifest()
    evidence = _evidence()
    request = b"cli request\n"
    baseline = b"cli raw output\n"
    paths = {
        "manifest": tmp_path / "manifest.json",
        "registration": tmp_path / "registration.json",
        "registration_proof": tmp_path / "registration-proof.json",
        "source_snapshot": tmp_path / "source-snapshot.json",
        "completion": tmp_path / "completion.json",
        "ledger": tmp_path / "ledger.json",
        "execution": tmp_path / "execution.json",
        "evidence": tmp_path / "evidence.json",
        "request": tmp_path / "request.txt",
        "baseline": tmp_path / "baseline.txt",
    }
    source_snapshot = _source_snapshot(manifest, [_ledger_entry(evidence)])
    json_payloads = {
        "manifest": manifest,
        "registration": _registration_receipt(manifest),
        "registration_proof": _registration_proof(manifest),
        "source_snapshot": source_snapshot,
        "completion": _completion_receipt(
            manifest, evidence, ordinal=1, previous_receipt_sha256=None
        ),
        "ledger": _completion_ledger(
            manifest, [_ledger_entry(evidence)], source_snapshot=source_snapshot
        ),
        "execution": _execution_receipt(
            manifest, evidence, request=request, baseline_output=baseline
        ),
        "evidence": evidence,
    }
    for name, payload in json_payloads.items():
        paths[name].write_text(json.dumps(payload), encoding="utf-8")
    paths["request"].write_bytes(request)
    paths["baseline"].write_bytes(baseline)

    assert (
        main(
            [
                "capture",
                "--manifest",
                str(paths["manifest"]),
                "--study-root",
                str(tmp_path / "study"),
                "--case-id",
                "case-01",
                "--request",
                str(paths["request"]),
                "--baseline-output",
                str(paths["baseline"]),
                "--evidence",
                str(paths["evidence"]),
                "--registration-receipt",
                str(paths["registration"]),
                "--registration-proof",
                str(paths["registration_proof"]),
                "--source-snapshot",
                str(paths["source_snapshot"]),
                "--completion-ledger",
                str(paths["ledger"]),
                "--completion-receipt",
                str(paths["completion"]),
                "--execution-receipt",
                str(paths["execution"]),
                "--captured-at",
                "2026-08-28T10:04:00Z",
            ]
        )
        == 0
    )
    capture_output = json.loads(capsys.readouterr().out)
    assert (
        capture_output["execution_receipt_sha256"]
        == json_payloads["execution"]["receipt_sha256"]
    )

    report = _validate_study(manifest=manifest, study_root=tmp_path / "study")
    assert report["case_count"] == 1
