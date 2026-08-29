from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

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
    assert "--evidence-root" in result.stdout
    assert "--batch-id" in result.stdout


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
