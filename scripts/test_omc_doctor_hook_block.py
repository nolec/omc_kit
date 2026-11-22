"""Check the deployed pipeline guard and optional legacy hook diagnostics."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_PATH = ROOT / "scripts" / "omc_doctor.py"
sys.path.insert(0, str(ROOT / "scripts"))


def _load_doctor():
    spec = importlib.util.spec_from_file_location("omc_doctor", DOCTOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doctor_requires_python_pipeline_guard_without_legacy_shell(tmp_path, monkeypatch):
    doctor = _load_doctor()
    # A deployed-script inventory based on existing files omits a deleted guard.
    monkeypatch.setattr(doctor, "_load_deployed_script_names", lambda root: set())
    guard_path = tmp_path / "scripts/omc_pipeline_guard.py"
    checks = doctor.run_checks(tmp_path)
    assert not next(c for c in checks if c.label == "scripts/omc_pipeline_guard.py").ok

    guard_path.parent.mkdir()
    guard_path.touch()
    checks = doctor.run_checks(tmp_path)
    assert next(c for c in checks if c.label == "scripts/omc_pipeline_guard.py").ok
    assert not any("omc-pipeline-check.sh" in c.label for c in checks)


def test_doctor_warns_when_existing_legacy_pipeline_hook_is_not_executable(tmp_path):
    hook = tmp_path / ".agent-hooks/omc-pipeline-check.sh"
    hook.parent.mkdir()
    hook.touch()
    hook.chmod(0o644)
    doctor = _load_doctor()
    check = next(c for c in doctor.run_checks(tmp_path) if "chmod +x" in c.label)
    assert not check.ok and hook.name in check.detail
    assert check.fix_cmd == "chmod u+x .agent-hooks/omc-pipeline-check.sh"
