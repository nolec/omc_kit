from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALL_PATH = ROOT / "scripts" / "install.py"
DOCTOR_PATH = ROOT / "scripts" / "omc_doctor.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


INSTALL = _load_module(INSTALL_PATH, "install_module")
DOCTOR = _load_module(DOCTOR_PATH, "doctor_module")


def test_deployed_script_contract_excludes_kit_only_auto_prompt(tmp_path: Path):
    kit_root = tmp_path / "kit"
    scripts_dir = kit_root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in [
        "omc.py",
        "omc_chat.py",
        "omc_exec.py",
        "omc_guard.py",
        "omc_state.py",
        "omc_hooks.py",
        "omc_role_suggest.py",
        "omc_tdd_check.py",
        "omc_pipeline_guard.py",
        "omc_context.py",
        "omc_lesson.py",
        "omc_cost.py",
        "omc_run.py",
        "omc_domain.py",
        "omc_utils.py",
        "omc_peer_review.py",
        "omc_autopilot.py",
        "install.py",
        "compose_prompt.py",
        "auto_prompt.py",
    ]:
        (scripts_dir / name).write_text(f"# {name}\n", encoding="utf-8")

    deployed = INSTALL._deployed_script_names(kit_root)

    assert "omc.py" in deployed
    assert "auto_prompt.py" not in deployed


def test_doctor_script_checks_follow_deployed_script_contract(tmp_path: Path):
    target = tmp_path / "target"
    scripts_dir = target / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in [
        "omc.py",
        "omc_chat.py",
        "omc_exec.py",
        "omc_guard.py",
        "omc_state.py",
        "omc_hooks.py",
        "omc_role_suggest.py",
        "omc_tdd_check.py",
        "omc_pipeline_guard.py",
        "omc_context.py",
        "omc_lesson.py",
        "omc_cost.py",
        "omc_run.py",
        "omc_domain.py",
        "omc_utils.py",
        "omc_peer_review.py",
        "omc_autopilot.py",
        "compose_prompt.py",
    ]:
        (scripts_dir / name).write_text(f"# {name}\n", encoding="utf-8")

    (scripts_dir / "install.py").write_text(INSTALL_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    checks = DOCTOR._build_checks(target)
    labels = {check.label: check for check in checks}

    assert "scripts/auto_prompt.py" not in labels
    assert labels["scripts/omc.py"].ok is True


def test_doctor_falls_back_to_default_script_contract_when_install_loader_fails(tmp_path: Path):
    target = tmp_path / "target"
    scripts_dir = target / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "omc.py").write_text("# omc.py\n", encoding="utf-8")
    (scripts_dir / "install.py").write_text("raise RuntimeError('broken install contract')\n", encoding="utf-8")

    checks = DOCTOR._build_checks(target)
    labels = {check.label: check for check in checks}

    assert "scripts/omc.py" in labels
    assert labels["scripts/omc.py"].ok is True
