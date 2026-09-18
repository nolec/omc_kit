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


def test_successful_root_setup_automatically_enables_raw_free_cohort(tmp_path: Path) -> None:
    target = tmp_path / "consumer"
    state_dir = tmp_path / "registry-state"

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
        env=os.environ | {"OMC_INSTALLATION_REGISTRY_DIR": str(state_dir)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    config = json.loads(
        (target / ".omc" / "skill-effectiveness-cohort-v1.json").read_text(encoding="utf-8")
    )
    assert config["enabled"] is True
    assert config["enrollment_source"] == "setup"


def test_setup_enables_v2_only_with_an_explicit_shared_activation(tmp_path: Path) -> None:
    pilot = tmp_path / "pilot"
    non_pilot = tmp_path / "non-pilot"
    state_dir = tmp_path / "registry-state"
    activation_id = "6cf7a4aa-02cf-4bf1-a9f2-0aa6f68caf51"
    activation_at = "2026-09-18T05:00:00+00:00"
    env = os.environ | {"OMC_INSTALLATION_REGISTRY_DIR": str(state_dir)}

    pilot_result = subprocess.run(
        [
            sys.executable, str(KIT_ROOT / "scripts" / "omc.py"), "setup",
            "--target", str(pilot), "--force", "--skip-session-start",
            "--skill-cohort-v2-activation-id", activation_id,
            "--skill-cohort-v2-activation-at", activation_at,
        ], cwd=KIT_ROOT, env=env, text=True, capture_output=True, check=False,
    )
    non_pilot_result = subprocess.run(
        [
            sys.executable, str(KIT_ROOT / "scripts" / "omc.py"), "setup",
            "--target", str(non_pilot), "--force", "--skip-session-start",
        ], cwd=KIT_ROOT, env=env, text=True, capture_output=True, check=False,
    )

    assert pilot_result.returncode == 0, pilot_result.stderr + pilot_result.stdout
    assert non_pilot_result.returncode == 0, non_pilot_result.stderr + non_pilot_result.stdout
    v2 = json.loads((pilot / ".omc" / "skill-effectiveness-cohort-v2.json").read_text(encoding="utf-8"))
    assert v2["activation_id"] == activation_id
    assert v2["activation_at"] == activation_at
    assert not (non_pilot / ".omc" / "skill-effectiveness-cohort-v2.json").exists()


def test_setup_rejects_a_conflicting_v2_activation_before_install(tmp_path: Path) -> None:
    target = tmp_path / "pilot"
    state_dir = tmp_path / "registry-state"
    original_id = "6cf7a4aa-02cf-4bf1-a9f2-0aa6f68caf51"
    conflicting_id = "5af93a6b-67bd-4c1d-a10e-4ea085e3fce9"
    activation_at = "2026-09-18T05:00:00+00:00"
    (target / ".omc").mkdir(parents=True)
    (target / ".omc" / "skill-effectiveness-cohort-v2.json").write_text(
        json.dumps({
            "schema_version": 1, "generation": "v2", "enabled": True,
            "activation_id": original_id, "activation_at": activation_at,
            "enrollment_session_ids": [], "enrollment_source": "setup",
        }), encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable, str(KIT_ROOT / "scripts" / "omc.py"), "setup",
            "--target", str(target), "--force", "--skip-session-start",
            "--skill-cohort-v2-activation-id", conflicting_id,
            "--skill-cohort-v2-activation-at", activation_at,
        ], cwd=KIT_ROOT,
        env=os.environ | {"OMC_INSTALLATION_REGISTRY_DIR": str(state_dir)},
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 2
    assert not (target / ".omc" / "install-receipt.json").exists()
    assert json.loads((target / ".omc" / "skill-effectiveness-cohort-v2.json").read_text(encoding="utf-8"))["activation_id"] == original_id


def test_setup_preflights_invalid_cohort_config_before_install_or_registry_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "consumer"
    config = target / ".omc" / "skill-effectiveness-cohort-v1.json"
    config.parent.mkdir(parents=True)
    config.write_text("{broken", encoding="utf-8")
    state_dir = tmp_path / "registry-state"
    env = os.environ | {"OMC_INSTALLATION_REGISTRY_DIR": str(state_dir)}
    monkeypatch.setenv("OMC_INSTALLATION_REGISTRY_DIR", str(state_dir))
    registry.enable()

    result = subprocess.run(
        [sys.executable, str(KIT_ROOT / "scripts" / "omc.py"), "setup", "--target", str(target), "--force", "--skip-session-start"],
        cwd=KIT_ROOT, env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 2
    assert config.read_text(encoding="utf-8") == "{broken"
    assert not (target / ".omc" / "install-receipt.json").exists()
    assert registry.status()["registered_target_count"] == 0
