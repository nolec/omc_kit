"""Regression coverage for contract tests running beside a real approved task."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parent


def _approved_task(root):
    sid = "contract-isolation-fixture"
    state = root / ".omc/state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "latest.json").write_text(json.dumps({
        "latest_session_id": sid, "latest_confirmed_session_id": sid,
    }))
    basis = root / ".omc/verification-bases" / sid
    basis.mkdir(parents=True)
    (basis / "1.json").write_text(json.dumps({
        "session_id": sid, "revision": 1,
        "request": "isolated regression fixture",
        "approval_reference": "synthetic fixture only",
        "checks": [{"id": "tests", "target": "fixture", "method": "pytest",
                    "pass_condition": "passed", "target_version": "fixture-v1"}],
    }))


def _guard(root, *args):
    return subprocess.run(
        [sys.executable, str(SOURCE / "omc_pipeline_guard.py"),
         "--target", str(root), *args],
        capture_output=True, text=True, cwd=root, timeout=30,
    )


def test_contract_suite_preserves_ambient_approved_task(tmp_path):
    """The suite must pass without reading or resetting its checkout's task."""
    shutil.copytree(SOURCE, tmp_path / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    subject = tmp_path / "scripts/test_omc_contract_hash.py"
    _approved_task(tmp_path)
    seeded = _guard(tmp_path, "contract-done", "--content", "preserve me",
                    "--required-checks", "tests")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    protected = [tmp_path / ".omc/pipeline_session.json",
                 tmp_path / ".omc/state/latest.json",
                 *sorted((tmp_path / ".omc/verification-bases").rglob("*.json"))]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(subject), "-q", "-ra"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    assert after == before, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_contract_helper_rejects_failed_command(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "contract_hash_helper_subject", SOURCE / "test_omc_contract_hash.py")
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 1, "guard refusal", "diagnostic"))
    with pytest.raises(AssertionError, match="guard refusal.*diagnostic"):
        subject._run("contract-done")


def test_approved_checks_cannot_be_omitted_or_replaced(tmp_path):
    """Isolation must not weaken the production guard's approved check set."""
    _approved_task(tmp_path)
    valid = _guard(tmp_path, "contract-done", "--required-checks", "tests")
    assert valid.returncode == 0, valid.stdout + valid.stderr
    state = tmp_path / ".omc/pipeline_session.json"
    before = state.read_bytes()
    for args in [(), ("--required-checks", "other")]:
        rejected = _guard(tmp_path, "contract-done", *args)
        assert rejected.returncode == 1
        assert "승인된 필수 검증 목록" in rejected.stdout
        assert state.read_bytes() == before
