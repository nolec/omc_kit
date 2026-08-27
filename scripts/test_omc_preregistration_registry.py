from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest

import omc_preregistration_registry as registry


def test_candidate_universe_imports_after_registry_schema_extension() -> None:
    import omc_plan_candidate_universe  # noqa: F401


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_registry_anchor_requires_exact_committed_record(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "registry@example.com")
    _git(root, "config", "user.name", "Registry Test")
    anchor = root / "anchor.txt"
    anchor.write_text("anchor\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "anchor")
    ancestor_commit = _git(root, "rev-parse", "HEAD")

    record = registry.prepare_registry_record(
        batch_id="product-value-1",
        preregistration_sha256="a" * 64,
    )
    registry_path = ".omc/registry/product-value-1.json"
    target = root / registry_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(record), encoding="utf-8")
    _git(root, "add", registry_path)
    _git(root, "commit", "-qm", "register")
    registry_commit = _git(root, "rev-parse", "HEAD")

    registry.validate_registry_anchor(
        record,
        repository_root=root,
        registry_commit=registry_commit,
        registry_path=registry_path,
        required_ancestor_commit=ancestor_commit,
    )

    wrong = dict(record, preregistration_sha256="b" * 64)
    with pytest.raises(ValueError, match="registry record is invalid"):
        registry.validate_registry_anchor(
            wrong,
            repository_root=root,
            registry_commit=registry_commit,
            registry_path=registry_path,
            required_ancestor_commit=ancestor_commit,
        )


def test_registry_anchor_v2_binds_embedded_preregistration(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "registry@example.com")
    _git(root, "config", "user.name", "Registry Test")
    anchor = root / "anchor.txt"
    anchor.write_text("anchor\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "anchor")
    ancestor_commit = _git(root, "rev-parse", "HEAD")
    manifest = {
        "batch_id": "product-value-2",
        "status": "frozen",
        "preregistration_sha256": "",
    }
    manifest["preregistration_sha256"] = registry.unsigned_document_digest(
        manifest,
        "preregistration_sha256",
    )
    record = registry.prepare_registry_record(
        batch_id=manifest["batch_id"],
        preregistration_sha256=manifest["preregistration_sha256"],
        preregistration=manifest,
    )
    registry_path = ".omc/registry/product-value-2.json"
    target = root / registry_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(record), encoding="utf-8")
    _git(root, "add", registry_path)
    _git(root, "commit", "-qm", "register durable manifest")
    registry_commit = _git(root, "rev-parse", "HEAD")

    registry.validate_registry_anchor(
        record,
        repository_root=root,
        registry_commit=registry_commit,
        registry_path=registry_path,
        required_ancestor_commit=ancestor_commit,
    )

    tampered = json.loads(json.dumps(record))
    tampered["preregistration"]["status"] = "changed"
    with pytest.raises(ValueError, match="registry manifest is invalid"):
        registry.validate_registry_anchor(
            tampered,
            repository_root=root,
            registry_commit=registry_commit,
            registry_path=registry_path,
            required_ancestor_commit=ancestor_commit,
        )


def test_sigstore_receipt_binds_claim_and_pre_observation_time(monkeypatch) -> None:
    observation_starts_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    registered_at = observation_starts_at - timedelta(seconds=1)
    authority = {"trusted_root_sha256": "c" * 64}
    trusted_root = {"fixture": "trusted-root"}
    evidence = {"gen_time": registered_at.isoformat()}

    monkeypatch.setattr(
        registry.rfc3161,
        "trust_identity",
        lambda candidate, **_: authority,
    )
    monkeypatch.setattr(
        registry.rfc3161,
        "verify_registration_evidence",
        lambda candidate, **_: candidate,
    )
    receipt = registry.prepare_sigstore_registration_receipt(
        batch_id="product-value-1",
        preregistration_sha256="a" * 64,
        registry_commit="b" * 40,
        registry_path=".omc/registry/product-value-1.json",
        registration_authority=authority,
        observation_starts_at=observation_starts_at.isoformat(),
        registration_evidence=evidence,
        trusted_root=trusted_root,
        approved_trusted_root_sha256="c" * 64,
    )

    registry.validate_sigstore_registration_receipt(
        receipt,
        batch_id="product-value-1",
        preregistration_sha256="a" * 64,
        registration_authority=authority,
        observation_starts_at=observation_starts_at.isoformat(),
        expected_receipt_sha256=receipt["receipt_sha256"],
        trusted_root=trusted_root,
        approved_trusted_root_sha256="c" * 64,
    )

    late = dict(receipt, registered_at=observation_starts_at.isoformat())
    late["receipt_sha256"] = registry.unsigned_document_digest(
        late, "receipt_sha256"
    )
    with pytest.raises(ValueError, match="registration receipt time is invalid"):
        registry.validate_sigstore_registration_receipt(
            late,
            batch_id="product-value-1",
            preregistration_sha256="a" * 64,
            registration_authority=authority,
            observation_starts_at=observation_starts_at.isoformat(),
            expected_receipt_sha256=late["receipt_sha256"],
            trusted_root=trusted_root,
            approved_trusted_root_sha256="c" * 64,
        )
