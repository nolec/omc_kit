from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parent
KIT_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import omc_installation_registry as registry


def _source_kit() -> Path:
    return KIT_ROOT


def test_registry_requires_explicit_consent_and_deduplicates_setup_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "registry-state"
    target = tmp_path / "consumer"
    target.mkdir()
    monkeypatch.setenv("OMC_INSTALLATION_REGISTRY_DIR", str(state_dir))

    assert registry.record_successful_setup(target=target, source_kit=_source_kit()) == {
        "status": "disabled"
    }
    assert not state_dir.exists()

    registry.enable()
    first = registry.record_successful_setup(target=target, source_kit=_source_kit())
    second = registry.record_successful_setup(target=target, source_kit=_source_kit())

    assert first["status"] == "recorded"
    assert second == {"status": "unchanged"}
    status = registry.status()
    assert status["enabled"] is True
    assert status["registered_target_count"] == 1
    assert str(target) not in json.dumps(status)


def test_registry_legacy_audit_is_raw_free_and_isolates_unavailable_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMC_INSTALLATION_REGISTRY_DIR", str(tmp_path / "registry-state"))
    present = tmp_path / "present"
    unavailable = tmp_path / "unavailable"
    present.mkdir()
    unavailable.mkdir()
    registry.enable()
    registry.record_successful_setup(target=present, source_kit=_source_kit())
    registry.record_successful_setup(target=unavailable, source_kit=_source_kit())
    (present / ".omc").mkdir()
    (present / ".omc" / "observed-completion-v1.json").write_text("{}", encoding="utf-8")
    unavailable.rmdir()

    result = registry.audit_legacy()

    assert result["status"] == "ok"
    assert {item["state"] for item in result["targets"]} == {
        "LEGACY_PRESENT",
        "SOURCE_UNAVAILABLE",
    }
    rendered = json.dumps(result)
    assert str(present) not in rendered
    assert str(unavailable) not in rendered


def test_registry_rejects_a_symlinked_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir, target_is_directory=True)
    monkeypatch.setenv("OMC_INSTALLATION_REGISTRY_DIR", str(linked_dir))

    with pytest.raises(registry.InstallationRegistryError, match="state_directory_invalid"):
        registry.enable()


def test_disabled_registry_state_error_does_not_block_root_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "not-a-registry-directory"
    state_file.write_text("not enabled", encoding="utf-8")
    target = tmp_path / "consumer"
    monkeypatch.setenv("OMC_INSTALLATION_REGISTRY_DIR", str(state_file))

    result = subprocess.run(
        [
            sys.executable,
            str(KIT_ROOT / "scripts" / "omc.py"),
            "setup",
            "--target",
            str(target),
            "--force",
            "--skip-session-start",
        ],
        cwd=KIT_ROOT,
        env=os.environ | {"OMC_INSTALLATION_REGISTRY_DIR": str(state_file)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout


def test_successful_root_setup_records_only_after_strict_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "registry-state"
    target = tmp_path / "consumer"
    monkeypatch.setenv("OMC_INSTALLATION_REGISTRY_DIR", str(state_dir))
    registry.enable()
    env = os.environ | {"OMC_INSTALLATION_REGISTRY_DIR": str(state_dir)}

    result = subprocess.run(
        [
            sys.executable,
            str(KIT_ROOT / "scripts" / "omc.py"),
            "setup",
            "--target",
            str(target),
            "--force",
            "--skip-session-start",
        ],
        cwd=KIT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert registry.status()["registered_target_count"] == 1
