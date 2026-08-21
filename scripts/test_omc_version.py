from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import omc_version
import install
from omc_source_hash import source_sha256


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_kit(root: Path, version: str = "0.1.0") -> Path:
    source = root / "kit"
    (source / "templates").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "prompts").mkdir()
    (source / "VERSION").write_text(version + "\n", encoding="utf-8")
    (source / "scripts" / "install.py").write_text("# installer\n", encoding="utf-8")
    (source / "prompts" / "team.json").write_text("{}\n", encoding="utf-8")
    return source


def _installed_target(root: Path, source: Path, version: str = "0.1.0") -> Path:
    target = root / "project"
    managed = target / "scripts" / "omc.py"
    managed.parent.mkdir(parents=True)
    managed.write_text("# installed\n", encoding="utf-8")
    metadata = target / ".omc"
    metadata.mkdir()
    source_hash = source_sha256(source)
    (metadata / "install-source.json").write_text(
        json.dumps(
            {
                "source_kind": "external",
                "source_path": str(source),
                "source_sha256": source_hash,
            }
        ),
        encoding="utf-8",
    )
    (metadata / "install-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "omc_version": version,
                "source_sha256": source_hash,
                "source_revision": None,
                "target": str(target.resolve()),
                "installed_at": "2026-08-21T00:00:00+00:00",
                "updated_at": "2026-08-21T00:00:00+00:00",
                "entries": {
                    "scripts/omc.py": {
                        "policy": "managed_exact",
                        "status": "updated",
                        "target_sha256": _sha256(managed),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return target


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.1.0", (0, 1, 0)), ("12.34.56\n", (12, 34, 56))],
)
def test_parse_version_accepts_stable_semver(raw: str, expected: tuple[int, int, int]):
    assert omc_version.parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["", "1", "1.2", "v1.2.3", "1.2.3-alpha", "1.02.3"])
def test_parse_version_rejects_unsupported_versions(raw: str):
    with pytest.raises(omc_version.VersionContractError):
        omc_version.parse_version(raw)


def test_source_revision_is_optional_when_git_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("git unavailable")

    monkeypatch.setattr(omc_version.subprocess, "run", unavailable)

    assert omc_version.source_revision(tmp_path) is None


def test_version_readiness_separates_release_source_and_integrity(tmp_path: Path):
    source = _source_kit(tmp_path)
    target = _installed_target(tmp_path, source)

    report = omc_version.version_readiness(
        target,
        source_path=str(source),
        install_integrity_status="ok",
    )

    assert report == {
        "installed_version": "0.1.0",
        "source_version": "0.1.0",
        "receipt_status": "current",
        "release_status": "up_to_date",
        "source_status": "unchanged",
        "install_integrity": "clean",
        "overall_status": "up_to_date",
    }


def test_version_readiness_reports_upgrade_and_source_modification(tmp_path: Path):
    source = _source_kit(tmp_path, "0.2.0")
    target = _installed_target(tmp_path, source, "0.1.0")
    receipt_path = target / ".omc" / "install-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["source_sha256"] = "old-source"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = omc_version.version_readiness(
        target,
        source_path=str(source),
        install_integrity_status="ok",
    )

    assert report["release_status"] == "upgrade_available"
    assert report["source_status"] == "modified"
    assert report["overall_status"] == "upgrade_available"


def test_version_readiness_keeps_source_unavailable_nonfatal(tmp_path: Path):
    source = _source_kit(tmp_path)
    target = _installed_target(tmp_path, source)

    report = omc_version.version_readiness(
        target,
        source_path=str(tmp_path / "missing"),
        install_integrity_status="ok",
    )

    assert report["installed_version"] == "0.1.0"
    assert report["source_version"] is None
    assert report["release_status"] == "source_unavailable"
    assert report["source_status"] == "unavailable"
    assert report["overall_status"] == "source_unavailable"


def test_version_readiness_accepts_v1_as_legacy_notice(tmp_path: Path):
    source = _source_kit(tmp_path)
    target = _installed_target(tmp_path, source)
    receipt_path = target / ".omc" / "install-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.pop("omc_version")
    receipt["schema_version"] = 1
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = omc_version.version_readiness(
        target,
        source_path=str(source),
        install_integrity_status="ok",
    )

    assert report["receipt_status"] == "legacy"
    assert report["release_status"] == "legacy_receipt"
    assert report["overall_status"] == "legacy_receipt"


def test_omc_version_json_surface(tmp_path: Path):
    source = _source_kit(tmp_path)
    target = _installed_target(tmp_path, source)

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "omc.py"),
            "version",
            "--target",
            str(target),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["installed_version"] == "0.1.0"
    assert payload["overall_status"] == "up_to_date"


def test_install_receipt_v2_preserves_original_install_time(tmp_path: Path):
    source = _source_kit(tmp_path)
    target = tmp_path / "project"
    receipt_path = target / ".omc" / "install-receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": "2026-08-01T00:00:00+00:00",
                "source_sha256": "old",
                "target": str(target.resolve()),
                "entries": {"scripts/omc.py": {"status": "updated"}},
            }
        ),
        encoding="utf-8",
    )

    receipt = install._write_install_receipt(
        target,
        source_identity=omc_version.capture_source_identity(source),
        entries={"scripts/omc.py": {"status": "updated"}},
    )

    assert receipt["schema_version"] == 2
    assert receipt["omc_version"] == "0.1.0"
    assert receipt["installed_at"] == "2026-08-01T00:00:00+00:00"
    assert receipt["updated_at"] != receipt["installed_at"]
    assert "created_at" not in receipt


def test_install_metadata_and_receipt_share_one_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_kit(tmp_path)
    target = tmp_path / "project"
    identity = omc_version.SourceIdentity(
        version="0.1.0",
        sha256="frozen-source-hash",
        revision="a" * 40,
    )

    def unexpected_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("source identity must not be read again")

    monkeypatch.setattr(install, "capture_source_identity", unexpected_read)
    monkeypatch.setattr(install, "_source_sha256", unexpected_read)

    install._write_install_source_metadata(
        target,
        source,
        source_identity=identity,
    )
    receipt = install._write_install_receipt(
        target,
        source_identity=identity,
        entries={},
    )
    metadata = json.loads(
        (target / ".omc" / "install-source.json").read_text(encoding="utf-8")
    )

    assert metadata["omc_version"] == receipt["omc_version"] == identity.version
    assert metadata["source_sha256"] == receipt["source_sha256"] == identity.sha256
    assert metadata["source_revision"] == receipt["source_revision"] == identity.revision


def test_auto_update_upgrades_matching_v1_receipt(tmp_path: Path):
    source = _source_kit(tmp_path)
    target = _installed_target(tmp_path, source)
    receipt_path = target / ".omc" / "install-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["schema_version"] = 1
    receipt.pop("omc_version")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    update = install._classify_auto_update(source, target)

    assert update["status"] == "update_available"


def test_source_version_file_is_not_a_managed_target_path(tmp_path: Path):
    source = _source_kit(tmp_path)
    target = tmp_path / "project"

    manifest = install._build_install_manifest(source, target)

    assert "VERSION" not in manifest
