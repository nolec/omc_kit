from __future__ import annotations

import base64
import hashlib
import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_product_value_evidence_bundle as bundle


REQUIRED_ARTIFACTS = {
    "preregistration.json": "manifest\n",
    "registration-receipt.json": "receipt\n",
    "packets/pv-01.json": "packet\n",
    "runner/acceptance.py": "acceptance\n",
    "runner/arm-adapter.py": "arm\n",
    "runner/scheduler.py": "scheduler\n",
    "runner/executor-shadow.py": "shadow\n",
    "runner/provider-adapter.py": "provider\n",
}


def _sources(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for relative, content in REQUIRED_ARTIFACTS.items():
        source = root / "sources" / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content, encoding="utf-8")
        result[relative] = source
    return result


def _allow_test_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bundle,
        "_temporary_roots",
        lambda: (Path("/private/tmp"), Path("/tmp")),
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _closure_registry_repo(root: Path) -> tuple[Path, str, dict[str, object]]:
    repository = root / "registry-repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "closure@example.com")
    _git(repository, "config", "user.name", "Closure Test")
    registry_record = {
        "schema_version": 1,
        "batch_id": "product-value-batch-20260826-v5-r1",
        "preregistration_sha256": "6" * 64,
    }
    registry_path = repository / ".omc" / "registry"
    registry_path.mkdir(parents=True)
    (registry_path / f"{registry_record['batch_id']}.json").write_text(
        json.dumps(registry_record),
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "register batch")
    return repository, _git(repository, "rev-parse", "HEAD"), registry_record


def _closure_artifacts(
    root: Path,
    *,
    subject_registry_commit: str | None = None,
) -> dict[str, Path]:
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    repository, registry_commit, registry_record = _closure_registry_repo(root)
    subject = bundle.prepare_failure_closure_subject(
        repository_root=repository,
        batch_id="product-value-batch-20260826-v5-r1",
        preregistration_sha256="6" * 64,
        registry_path=(
            ".omc/registry/product-value-batch-20260826-v5-r1.json"
        ),
        registry_commit=registry_commit,
        diagnostic_receipt_sha256="4" * 64,
        closure_authority_sha256=hashlib.sha256(public_raw).hexdigest(),
        closed_at="2026-08-30T13:00:00+09:00",
        final_decision_deadline="2026-09-05",
        missing_artifacts=(
            "manifest",
            "workload_inventory",
            "execution_packets",
        ),
    )
    if subject_registry_commit is not None:
        subject["registry_commit"] = subject_registry_commit
    receipt = {
        "schema_version": "omc-product-value-authority-receipt/v1",
        "role": "closure",
        "signer_public_key": base64.b64encode(public_raw).decode("ascii"),
        "subject_sha256": bundle._canonical_sha256(subject),
    }
    receipt["signature"] = base64.b64encode(
        private_key.sign(bundle._canonical_bytes(receipt))
    ).decode("ascii")
    sources = root / "closure-sources"
    sources.mkdir()
    subject_path = sources / "closure-subject.json"
    receipt_path = sources / "closure-receipt.json"
    registry_path = sources / "registry-record.json"
    subject_path.write_text(json.dumps(subject), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    registry_path.write_text(json.dumps(registry_record), encoding="utf-8")
    return {
        "closure-subject.json": subject_path,
        "closure-receipt.json": receipt_path,
        "registry-record.json": registry_path,
    }


def test_signed_failure_closure_bundle_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _closure_artifacts(tmp_path)

    index = bundle.publish_evidence_bundle(
        evidence_root,
        batch_id="product-value-batch-20260826-v5-r1-closure",
        artifacts=artifacts,
        repository_root=tmp_path / "registry-repo",
    )

    assert set(index["artifacts"]) == {
        "closure-subject.json",
        "closure-receipt.json",
        "registry-record.json",
    }
    assert bundle.load_evidence_bundle(
        evidence_root,
        batch_id="product-value-batch-20260826-v5-r1-closure",
        repository_root=tmp_path / "registry-repo",
    ) == index


def test_failure_closure_bundle_rejects_uncommitted_registry_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _closure_artifacts(
        tmp_path,
        subject_registry_commit="8" * 40,
    )

    with pytest.raises(ValueError, match="registry anchor is invalid"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-batch-20260826-v5-r1-closure",
            artifacts=artifacts,
            repository_root=tmp_path / "registry-repo",
        )


def test_failure_closure_bundle_rejects_tampered_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _closure_artifacts(tmp_path)
    receipt_path = artifacts["closure-receipt.json"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["signature"] = base64.b64encode(b"invalid").decode("ascii")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="^failure_closure_signature_invalid$"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-batch-20260826-v5-r1-closure",
            artifacts=artifacts,
            repository_root=tmp_path / "registry-repo",
        )


def test_failure_closure_bundle_rejects_mismatched_registry_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _closure_artifacts(tmp_path)
    registry_path = artifacts["registry-record.json"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["preregistration_sha256"] = "f" * 64
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="^failure_closure_registry_invalid$"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-batch-20260826-v5-r1-closure",
            artifacts=artifacts,
            repository_root=tmp_path / "registry-repo",
        )


def test_failure_closure_rejects_closure_after_deadline(tmp_path: Path) -> None:
    repository, registry_commit, _ = _closure_registry_repo(tmp_path)

    with pytest.raises(ValueError, match="^failure_closure_subject_invalid$"):
        bundle.prepare_failure_closure_subject(
            repository_root=repository,
            batch_id="product-value-batch-20260826-v5-r1",
            preregistration_sha256="6" * 64,
            registry_path=(
                ".omc/registry/product-value-batch-20260826-v5-r1.json"
            ),
            registry_commit=registry_commit,
            diagnostic_receipt_sha256="4" * 64,
            closure_authority_sha256="9" * 64,
            closed_at="2026-09-06T00:00:00+09:00",
            final_decision_deadline="2026-09-05",
            missing_artifacts=(
                "manifest",
                "workload_inventory",
                "execution_packets",
            ),
        )


def test_failure_closure_rejects_uncommitted_registry_anchor(tmp_path: Path) -> None:
    repository, _, _ = _closure_registry_repo(tmp_path)

    with pytest.raises(ValueError, match="registry anchor is invalid"):
        bundle.prepare_failure_closure_subject(
            repository_root=repository,
            batch_id="product-value-batch-20260826-v5-r1",
            preregistration_sha256="6" * 64,
            registry_path=(
                ".omc/registry/product-value-batch-20260826-v5-r1.json"
            ),
            registry_commit="8" * 40,
            diagnostic_receipt_sha256="4" * 64,
            closure_authority_sha256="9" * 64,
            closed_at="2026-08-30T13:00:00+09:00",
            final_decision_deadline="2026-09-05",
            missing_artifacts=(
                "manifest",
                "workload_inventory",
                "execution_packets",
            ),
        )


def test_record_failure_closure_is_idempotent_and_no_replace(tmp_path: Path) -> None:
    artifacts = _closure_artifacts(tmp_path)
    subject = json.loads(
        artifacts["closure-subject.json"].read_text(encoding="utf-8")
    )
    receipt = json.loads(
        artifacts["closure-receipt.json"].read_text(encoding="utf-8")
    )
    repository = tmp_path / "registry-repo"

    first = bundle.record_failure_closure(
        repository,
        subject=subject,
        receipt=receipt,
    )
    second = bundle.record_failure_closure(
        repository,
        subject=subject,
        receipt=receipt,
    )

    assert first == second
    assert first.name == f"{subject['batch_id']}.closure.json"
    replacement = dict(receipt)
    replacement["signature"] = base64.b64encode(b"replacement").decode("ascii")
    with pytest.raises(ValueError, match="^failure_closure_marker_exists$"):
        bundle.record_failure_closure(
            repository,
            subject=subject,
            receipt=replacement,
        )


def test_record_failure_closure_concurrent_same_value_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _closure_artifacts(tmp_path)
    subject = json.loads(
        artifacts["closure-subject.json"].read_text(encoding="utf-8")
    )
    receipt = json.loads(
        artifacts["closure-receipt.json"].read_text(encoding="utf-8")
    )
    repository = tmp_path / "registry-repo"
    marker = repository / ".omc" / "registry" / f"{subject['batch_id']}.closure.json"
    payload = {"subject": subject, "receipt": receipt}
    link_called = False

    def concurrent_link(
        source,
        destination,
        *,
        src_dir_fd,
        dst_dir_fd,
        follow_symlinks,
    ):
        nonlocal link_called
        link_called = True
        assert destination == marker.name
        assert src_dir_fd == dst_dir_fd
        assert follow_symlinks is False
        descriptor = bundle.os.open(source, bundle.os.O_RDONLY, dir_fd=src_dir_fd)
        with bundle.os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            staged_payload = handle.read()
        assert json.loads(staged_payload) == payload
        marker.write_text(staged_payload, encoding="utf-8")
        raise FileExistsError(destination)

    monkeypatch.setattr(bundle.os, "link", concurrent_link)

    assert bundle.record_failure_closure(
        repository,
        subject=subject,
        receipt=receipt,
    ) == marker
    assert link_called is True


def test_load_failure_closure_marker_rejects_symlink(tmp_path: Path) -> None:
    artifacts = _closure_artifacts(tmp_path)
    subject = json.loads(
        artifacts["closure-subject.json"].read_text(encoding="utf-8")
    )
    receipt = json.loads(
        artifacts["closure-receipt.json"].read_text(encoding="utf-8")
    )
    repository = tmp_path / "registry-repo"
    marker = repository / ".omc" / "registry" / f"{subject['batch_id']}.closure.json"
    external_marker = tmp_path / "external-closure.json"
    external_marker.write_text(
        json.dumps({"subject": subject, "receipt": receipt}),
        encoding="utf-8",
    )
    marker.symlink_to(external_marker)

    with pytest.raises(ValueError, match="^failure_closure_marker_invalid$"):
        bundle.load_failure_closure_marker(
            repository,
            batch_id=subject["batch_id"],
            preregistration_sha256=subject["preregistration_sha256"],
        )


def test_load_failure_closure_marker_rejects_broken_symlink(tmp_path: Path) -> None:
    artifacts = _closure_artifacts(tmp_path)
    subject = json.loads(
        artifacts["closure-subject.json"].read_text(encoding="utf-8")
    )
    repository = tmp_path / "registry-repo"
    marker = repository / ".omc" / "registry" / f"{subject['batch_id']}.closure.json"
    marker.symlink_to(tmp_path / "missing-closure.json")

    with pytest.raises(ValueError, match="^failure_closure_marker_invalid$"):
        bundle.load_failure_closure_marker(
            repository,
            batch_id=subject["batch_id"],
            preregistration_sha256=subject["preregistration_sha256"],
        )


def test_load_failure_closure_marker_rejects_registry_directory_symlink(
    tmp_path: Path,
) -> None:
    artifacts = _closure_artifacts(tmp_path)
    subject = json.loads(
        artifacts["closure-subject.json"].read_text(encoding="utf-8")
    )
    receipt = json.loads(
        artifacts["closure-receipt.json"].read_text(encoding="utf-8")
    )
    repository = tmp_path / "registry-repo"
    registry = repository / ".omc" / "registry"
    external_registry = tmp_path / "external-registry"
    registry.rename(external_registry)
    registry.symlink_to(external_registry, target_is_directory=True)
    marker = external_registry / f"{subject['batch_id']}.closure.json"
    marker.write_text(
        json.dumps({"subject": subject, "receipt": receipt}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="^failure_closure_marker_invalid$"):
        bundle.load_failure_closure_marker(
            repository,
            batch_id=subject["batch_id"],
            preregistration_sha256=subject["preregistration_sha256"],
        )


def test_record_failure_closure_rejects_registry_directory_symlink(
    tmp_path: Path,
) -> None:
    artifacts = _closure_artifacts(tmp_path)
    subject = json.loads(
        artifacts["closure-subject.json"].read_text(encoding="utf-8")
    )
    receipt = json.loads(
        artifacts["closure-receipt.json"].read_text(encoding="utf-8")
    )
    repository = tmp_path / "registry-repo"
    registry = repository / ".omc" / "registry"
    external_registry = tmp_path / "external-registry"
    registry.rename(external_registry)
    registry.symlink_to(external_registry, target_is_directory=True)

    with pytest.raises(
        ValueError,
        match="^preregistration registry anchor is invalid$",
    ):
        bundle.record_failure_closure(
            repository,
            subject=subject,
            receipt=receipt,
        )


@pytest.mark.parametrize("registry_state", ("absent", "omc-only", "empty"))
def test_load_failure_closure_marker_allows_missing_registry_state(
    tmp_path: Path,
    registry_state: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    if registry_state in {"omc-only", "empty"}:
        (repository / ".omc").mkdir()
    if registry_state == "empty":
        (repository / ".omc" / "registry").mkdir()

    assert bundle.load_failure_closure_marker(
        repository,
        batch_id="unregistered-batch",
        preregistration_sha256="a" * 64,
    ) is None


def test_publish_and_load_evidence_bundle_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _sources(tmp_path)

    index = bundle.publish_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
        artifacts=artifacts,
    )

    assert index["schema_version"] == "omc-product-value-evidence-bundle/v1"
    assert index["batch_id"] == "product-value-v1"
    assert set(index["artifacts"]) == set(REQUIRED_ARTIFACTS)
    assert bundle.load_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
    ) == index


def test_materialize_verified_bundle_isolated_from_source_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    bundle.publish_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
        artifacts=_sources(tmp_path),
    )

    materialized = bundle.materialize_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
    )
    try:
        source = evidence_root / "product-value-v1" / "preregistration.json"
        snapshot = materialized.paths["preregistration.json"]
        assert snapshot != source
        assert snapshot.read_text(encoding="utf-8") == "manifest\n"

        source.write_text("mutated\n", encoding="utf-8")

        assert snapshot.read_text(encoding="utf-8") == "manifest\n"
        assert snapshot.stat().st_mode & 0o222 == 0
    finally:
        snapshot_root = materialized.root
        materialized.cleanup()
    assert not snapshot_root.exists()


def test_publish_rejects_ephemeral_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="^evidence_root_ephemeral$"):
        bundle.publish_evidence_bundle(
            Path("/private/tmp") / "omc-evidence",
            batch_id="product-value-v1",
            artifacts=_sources(tmp_path),
        )


def test_publish_rejects_missing_required_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _sources(tmp_path)
    artifacts.pop("registration-receipt.json")

    with pytest.raises(ValueError, match="^evidence_bundle_artifacts_invalid$"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-v1",
            artifacts=artifacts,
        )

    assert not (evidence_root / "product-value-v1").exists()


def test_publish_rejects_artifact_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _sources(tmp_path)
    artifacts["../private.json"] = next(iter(artifacts.values()))

    with pytest.raises(ValueError, match="^evidence_bundle_artifacts_invalid$"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-v1",
            artifacts=artifacts,
        )


def test_load_rejects_artifact_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    bundle.publish_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
        artifacts=_sources(tmp_path),
    )
    target = evidence_root / "product-value-v1" / "packets" / "pv-01.json"
    target.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="^evidence_bundle_digest_mismatch$"):
        bundle.load_evidence_bundle(
            evidence_root,
            batch_id="product-value-v1",
        )


def test_publish_does_not_replace_existing_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _sources(tmp_path)
    first = bundle.publish_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
        artifacts=artifacts,
    )

    with pytest.raises(ValueError, match="^evidence_bundle_exists$"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-v1",
            artifacts=artifacts,
        )

    assert json.loads(
        (evidence_root / "product-value-v1" / "bundle-index.json").read_text(
            encoding="utf-8"
        )
    ) == first


def test_publish_atomically_renames_complete_staging_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    destination = evidence_root / "product-value-v1"
    real_publish = bundle._atomic_rename_no_replace
    observed = False

    def atomic_publish(staging: Path, target: Path) -> None:
        nonlocal observed
        observed = True
        assert target == destination
        assert not target.exists()
        assert (staging / "bundle-index.json").is_file()
        assert {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file() and path.name != "bundle-index.json"
        } == set(REQUIRED_ARTIFACTS)
        real_publish(staging, target)

    monkeypatch.setattr(bundle, "_atomic_rename_no_replace", atomic_publish)
    bundle.publish_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
        artifacts=_sources(tmp_path),
    )

    assert observed is True
    assert destination.is_dir()


def test_publish_does_not_replace_late_empty_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    destination = evidence_root / "product-value-v1"
    real_publish = bundle._atomic_rename_no_replace

    def atomic_publish(staging: Path, target: Path) -> None:
        destination.mkdir()
        real_publish(staging, target)

    monkeypatch.setattr(bundle, "_atomic_rename_no_replace", atomic_publish)
    with pytest.raises(ValueError, match="^evidence_bundle_exists$"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-v1",
            artifacts=_sources(tmp_path),
        )

    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_publish_failure_removes_staging_without_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()

    def fail_publish(_source: Path, _destination: Path) -> None:
        raise OSError("publish failed")

    monkeypatch.setattr(bundle, "_atomic_rename_no_replace", fail_publish)
    with pytest.raises(OSError, match="^publish failed$"):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-v1",
            artifacts=_sources(tmp_path),
        )

    assert not (evidence_root / "product-value-v1").exists()
    assert list(evidence_root.glob(".product-value-v1-*")) == []


def test_publish_preserves_complete_bundle_when_post_publish_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    destination = evidence_root / "product-value-v1"
    real_fsync = bundle._fsync_directory

    def fail_destination_fsync(path: Path) -> None:
        if path == destination:
            raise OSError("directory fsync failed")
        real_fsync(path)

    monkeypatch.setattr(bundle, "_fsync_directory", fail_destination_fsync)
    with pytest.raises(
        ValueError,
        match="^evidence_bundle_durability_indeterminate$",
    ):
        bundle.publish_evidence_bundle(
            evidence_root,
            batch_id="product-value-v1",
            artifacts=_sources(tmp_path),
        )

    assert destination.is_dir()
    assert bundle.load_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
    )["batch_id"] == "product-value-v1"


def test_cli_publishes_and_verifies_without_mutable_artifact_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _allow_test_root(monkeypatch)
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    artifacts = _sources(tmp_path)
    artifact_map = tmp_path / "artifact-map.json"
    artifact_map.write_text(
        json.dumps({key: str(value) for key, value in artifacts.items()}),
        encoding="utf-8",
    )

    assert bundle.main([
        "publish",
        "--evidence-root",
        str(evidence_root),
        "--batch-id",
        "product-value-v1",
        "--artifacts",
        str(artifact_map),
    ]) == 0
    published = json.loads(capsys.readouterr().out)
    assert published["status"] == "published"

    assert bundle.main([
        "verify",
        "--evidence-root",
        str(evidence_root),
        "--batch-id",
        "product-value-v1",
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"
    assert verified["bundle_sha256"] == published["bundle_sha256"]
    assert verified["artifact_count"] == len(REQUIRED_ARTIFACTS)
    assert "artifact_paths" not in verified
    assert "artifact_paths" not in published


def test_omc_cli_exposes_product_value_evidence_surface() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/omc.py", "product-value-evidence", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "publish" in result.stdout
    assert "verify" in result.stdout
    assert "prepare-closure" in result.stdout
    assert "validate-closure" in result.stdout
    assert "--evidence-root" in result.stdout
    assert "--batch-id" in result.stdout


def test_omc_cli_prepares_failure_closure_subject(tmp_path: Path) -> None:
    output = tmp_path / "closure-subject.json"
    repository = Path.cwd()
    registry_path = ".omc/registry/product-value-batch-20260826-v5-r1.json"
    registry_record = json.loads(
        (repository / registry_path).read_text(encoding="utf-8")
    )
    registry_commit = _git(
        repository,
        "log",
        "-1",
        "--format=%H",
        "--",
        registry_path,
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-evidence",
            "prepare-closure",
            "--batch-id",
            "product-value-batch-20260826-v5-r1",
            "--preregistration-sha256",
            registry_record["preregistration_sha256"],
            "--registry-path",
            registry_path,
            "--registry-commit",
            registry_commit,
            "--diagnostic-receipt-sha256",
            "4" * 64,
            "--closure-authority-sha256",
            "9" * 64,
            "--closed-at",
            "2026-08-30T13:00:00+09:00",
            "--final-decision-deadline",
            "2026-09-05",
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "closure_prepared"
    assert json.loads(output.read_text(encoding="utf-8"))["execution_eligible"] is False


def test_omc_cli_rejects_closure_without_git_registry_anchor(tmp_path: Path) -> None:
    artifacts = _closure_artifacts(
        tmp_path,
        subject_registry_commit="8" * 40,
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/omc.py",
            "product-value-evidence",
            "validate-closure",
            "--subject",
            str(artifacts["closure-subject.json"]),
            "--receipt",
            str(artifacts["closure-receipt.json"]),
            "--registry-record",
            str(artifacts["registry-record.json"]),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    blocked = json.loads(result.stdout)
    assert blocked["status"] == "blocked"
    assert "registry" in blocked["reason_code"]


def test_cli_surface_is_available_from_a_clean_source_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch)
    source = Path(bundle.__file__).resolve()
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    (scripts / source.name).write_bytes(source.read_bytes())
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "omc@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "OMC Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)
    clone = tmp_path / "clean-clone"
    subprocess.run(
        ["git", "clone", "-q", str(repository), str(clone)],
        check=True,
    )

    spec = importlib.util.spec_from_file_location(
        "clean_clone_evidence_bundle",
        clone / "scripts" / source.name,
    )
    assert spec is not None and spec.loader is not None
    clean_bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clean_bundle)
    clean_bundle._temporary_roots = lambda: (Path("/private/tmp"), Path("/tmp"))
    evidence_root = tmp_path / "durable-evidence"
    evidence_root.mkdir()
    expected = bundle.publish_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
        artifacts=_sources(tmp_path),
    )

    recovered = clean_bundle.load_evidence_bundle(
        evidence_root,
        batch_id="product-value-v1",
    )

    assert recovered["bundle_sha256"] == expected["bundle_sha256"]
