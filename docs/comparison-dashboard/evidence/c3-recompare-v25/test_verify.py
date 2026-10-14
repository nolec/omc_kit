"""The archived package, not the original temporary measurement, is the input."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent


def checker():
    spec = importlib.util.spec_from_file_location("c3_evidence_verify", ROOT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def archived(tmp_path):
    target = tmp_path / "relocated-evidence"
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    assert checker().verify(target)["status"] == "VALID", "mutation tests need a valid starting package"
    return target


def rehash(root, name):
    path = root / "sha256.json"
    manifest = json.loads(path.read_text())
    manifest[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))


def test_relocated_package_is_the_input(archived):
    result = checker().verify(archived)
    assert result["status"] == "VALID"
    assert result["omc_measurements"] == 18
    assert result["paired_competitive_measurements"] == 0


@pytest.mark.parametrize("name", ["results.json", "current-uncertain-1/resume.stdout", "current-artifact_mismatch-1/roadmap.md"])
def test_missing_evidence_is_unavailable(archived, name):
    (archived / name).unlink()
    result = checker().verify(archived)
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "evidence_missing"


def test_corrupted_output_is_invalid(archived):
    (archived / "current-uncertain-1/resume.stdout").write_text("corrupted output\n")
    assert checker().verify(archived)["status"] == "INVALID"


def test_rehashed_output_still_needs_surface_proof(archived):
    name = "current-uncertain-1/resume.stdout"
    (archived / name).write_text("corrupted output\n")
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


def test_rehashed_summary_must_match_call_log(archived):
    name = "results.json"
    rows = json.loads((archived / name).read_text())
    rows[0]["calls_after"].append("roadmap")
    rows[0]["additional_calls"] = 1
    (archived / name).write_text(json.dumps(rows))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


def test_rehashed_artifact_must_match_recorded_observation(archived):
    name = "current-artifact_mismatch-1/roadmap.md"
    (archived / name).write_text("corrupted artifact\n")
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


@pytest.mark.parametrize("entry", ["../outside.txt", "/private/tmp/outside.txt"])
def test_manifest_cannot_escape_archive(archived, entry):
    path = archived / "sha256.json"
    manifest = json.loads(path.read_text())
    manifest[entry] = "0" * 64
    path.write_text(json.dumps(manifest))
    assert checker().verify(archived)["status"] == "INVALID"


def test_symlink_evidence_is_invalid(archived):
    path = archived / "current-uncertain-1/resume.stdout"
    saved = path.read_bytes()
    other = archived.parent / "external-output"
    other.write_bytes(saved)
    path.unlink()
    path.symlink_to(other)
    assert checker().verify(archived)["status"] == "INVALID"


def test_invalid_json_is_reported(archived):
    path = archived / "sha256.json"
    path.write_text("{broken")
    assert checker().verify(archived)["status"] == "INVALID"


def test_rehashed_provenance_counts_must_match_rows(archived):
    name = "provenance.json"
    data = json.loads((archived / name).read_text())
    data["omc_measurements"] = 24
    (archived / name).write_text(json.dumps(data))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


def test_artifact_observation_cannot_drop_file_hashes(archived):
    name = "current-artifact_mismatch-1/artifact-observations.json"
    data = json.loads((archived / name).read_text())
    data["files"] = []
    (archived / name).write_text(json.dumps(data))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


def test_cli_uses_adjacent_package_from_other_cwd(archived):
    result = subprocess.run([sys.executable, str(archived / "verify.py")], cwd=archived.parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "VALID"


def test_cli_missing_is_distinct_from_corrupt(archived):
    path = archived / "current-uncertain-1/resume.stdout"
    path.unlink()
    result = subprocess.run([sys.executable, str(archived / "verify.py")], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "UNAVAILABLE"


def test_cli_corrupt_is_invalid(archived):
    (archived / "current-uncertain-1/resume.stdout").write_text("corrupt output")
    result = subprocess.run([sys.executable, str(archived / "verify.py")], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "INVALID"


@pytest.mark.parametrize("change", ["empty", "missing", "duplicate", "reversed_dependency", "missing_completion", "wrong_validator", "wrong_output", "wrong_target", "bad_digest", "wrong_task_id"])
def test_required_task_contract_is_validated(archived, change):
    name = "current-artifact_mismatch-1/task.json"
    task = json.loads((archived / name).read_text())
    if change == "empty":
        task["steps"] = []
    elif change == "missing":
        task["steps"].pop()
    elif change == "duplicate":
        task["steps"][1]["id"] = "roadmap"
    elif change == "reversed_dependency":
        task["steps"][0]["depends_on"] = ["dashboard"]
    elif change == "missing_completion":
        task["steps"][0].pop("completion")
    elif change == "wrong_validator":
        task["steps"][0]["completion"]["validator_id"] = "json_object_fields"
    elif change == "wrong_output":
        task["steps"][0]["completion"]["output_path"] = "dashboard.md"
    elif change == "wrong_target":
        task["steps"][0]["completion"]["target_path"] = "targets/dashboard.md"
    elif change == "bad_digest":
        task["steps"][0]["completion"]["expected_sha256"] = "unknown"
    else:
        task["id"] = "other-task"
    (archived / name).write_text(json.dumps(task))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


@pytest.mark.parametrize("state_name", ["before-state.json", "after-state.json"])
@pytest.mark.parametrize("field", ["missing_receipt", "task_id", "task_spec_sha256", "step_id", "validator_id", "artifact_sha256", "predecessor_receipts", "receipt_sha256"])
def test_completed_receipt_is_bound_to_task_and_goal(archived, state_name, field):
    name = "current-artifact_mismatch-1/" + state_name
    state = json.loads((archived / name).read_text())
    step = state["steps"]["roadmap"]
    if field == "missing_receipt":
        step.pop("completion_receipt")
    else:
        receipt = step["completion_receipt"]
        receipt[field] = [] if field == "predecessor_receipts" else "0" * 64
        if field == "predecessor_receipts":
            receipt[field] = ["0" * 64]
    (archived / name).write_text(json.dumps(state))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


def test_self_consistent_receipt_still_needs_target_hash(archived):
    name = "current-artifact_mismatch-1/before-state.json"
    state = json.loads((archived / name).read_text())
    receipt = state["steps"]["roadmap"]["completion_receipt"]
    receipt["artifact_sha256"] = "0" * 64
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    receipt["receipt_sha256"] = hashlib.sha256(encoded).hexdigest()
    (archived / name).write_text(json.dumps(state))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


def test_dashboard_receipt_depends_on_roadmap_receipt(archived):
    name = "current-artifact_mismatch-1/before-state.json"
    state = json.loads((archived / name).read_text())
    receipt = state["steps"]["dashboard"]["completion_receipt"]
    receipt["predecessor_receipts"] = []
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    receipt["receipt_sha256"] = hashlib.sha256(encoded).hexdigest()
    (archived / name).write_text(json.dumps(state))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"


def test_receipt_check_applies_to_uncertain_case_too(archived):
    name = "current-uncertain-1/before-state.json"
    state = json.loads((archived / name).read_text())
    state["steps"]["roadmap"]["completion_receipt"]["artifact_sha256"] = "0" * 64
    (archived / name).write_text(json.dumps(state))
    rehash(archived, name)
    assert checker().verify(archived)["status"] == "INVALID"
