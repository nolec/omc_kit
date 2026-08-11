import json
import subprocess
import sys
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OMC = ROOT / "scripts" / "omc.py"
GUARD = ROOT / "scripts" / "omc_guard.py"


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(OMC), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _init_git_repo(repo: Path) -> str:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "omc-test@example.com")
    _git(repo, "config", "user.name", "OMC Test")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "baseline")
    return _git(repo, "rev-parse", "HEAD")


def _sync_task_session(repo: Path, request: str = "observed task") -> str:
    init = _run("state", "init", "--target", str(repo))
    assert init.returncode == 0, init.stderr
    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(repo),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        request,
        "--roles",
        "senior_coding",
    )
    assert sync.returncode == 0, sync.stderr
    latest = _read_json(repo / ".omc" / "state" / "latest.json")
    return str(latest["latest_session_id"])


def _sync_review_session(repo: Path) -> str:
    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(repo),
        "--mode",
        "autopilot",
        "--title",
        "omc-review",
        "--request",
        "review observed task",
        "--roles",
        "code_review",
    )
    assert sync.returncode == 0, sync.stderr
    latest = _read_json(repo / ".omc" / "state" / "latest.json")
    return str(latest["latest_session_id"])


def _sync_ship_session(repo: Path) -> str:
    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(repo),
        "--mode",
        "autopilot",
        "--title",
        "omc-ship",
        "--request",
        "ship observed task",
        "--roles",
        "directive",
    )
    assert sync.returncode == 0, sync.stderr
    latest = _read_json(repo / ".omc" / "state" / "latest.json")
    return str(latest["latest_session_id"])


def test_state_complete_writes_verified_completion_receipt_for_one_followup_commit(tmp_path: Path):
    target = tmp_path / "repo"
    baseline = _init_git_repo(target)
    request = "observed completion receipt"
    session_id = _sync_task_session(target, request)

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "implement task")
    followup = _git(target, "rev-parse", "HEAD")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    receipt_path = target / ".omc" / "state" / "sessions" / session_id / "completion.json"
    receipt = _read_json(receipt_path)
    assert receipt["schema_version"] == 1
    assert receipt["session_id"] == session_id
    assert receipt["baseline_commit"] == baseline
    assert receipt["followup_commit"] == followup
    assert receipt["changed_paths"] == ["app.py"]
    assert receipt["provider_outputs_available"] is False


def test_state_complete_skips_completion_receipt_without_followup_commit(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    session_id = _sync_task_session(target)

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    receipt_path = target / ".omc" / "state" / "sessions" / session_id / "completion.json"
    assert not receipt_path.exists()


def test_state_complete_skips_ambiguous_multi_commit_completion(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    session_id = _sync_task_session(target)

    for value in (2, 3):
        (target / "app.py").write_text(f"value = {value}\n", encoding="utf-8")
        _git(target, "add", "app.py")
        _git(target, "commit", "-qm", f"task step {value}")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    receipt_path = target / ".omc" / "state" / "sessions" / session_id / "completion.json"
    assert not receipt_path.exists()


def test_state_complete_attributes_commit_to_task_before_review_session(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    task_session_id = _sync_task_session(target, "task then review then commit")
    review_session_id = _sync_review_session(target)

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "implement reviewed task")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    task_receipt = target / ".omc" / "state" / "sessions" / task_session_id / "completion.json"
    review_receipt = target / ".omc" / "state" / "sessions" / review_session_id / "completion.json"
    assert task_receipt.exists()
    assert not review_receipt.exists()


def test_state_complete_preserves_task_through_review_and_ship_sessions(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    task_session_id = _sync_task_session(target, "task then review then ship then commit")
    review_session_id = _sync_review_session(target)
    ship_session_id = _sync_ship_session(target)

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "ship implemented task")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    task_receipt = target / ".omc" / "state" / "sessions" / task_session_id / "completion.json"
    review_receipt = target / ".omc" / "state" / "sessions" / review_session_id / "completion.json"
    ship_receipt = target / ".omc" / "state" / "sessions" / ship_session_id / "completion.json"
    assert task_receipt.exists()
    assert not review_receipt.exists()
    assert not ship_receipt.exists()


def test_state_complete_uses_latest_explicit_task_when_baseline_is_duplicated(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    stale_session_id = _sync_task_session(target, "abandoned task")
    active_session_id = _sync_task_session(target, "task that produced the commit")

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "implement active task")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    stale_receipt = target / ".omc" / "state" / "sessions" / stale_session_id / "completion.json"
    active_receipt = target / ".omc" / "state" / "sessions" / active_session_id / "completion.json"
    assert not stale_receipt.exists()
    assert active_receipt.exists()


def test_post_commit_template_records_verified_completion_receipt():
    template = ROOT / "templates" / "post-commit"
    assert template.exists()
    assert "python3 scripts/omc.py state complete --target ." in template.read_text(encoding="utf-8")


def _load_state_module():
    module_path = ROOT / "scripts" / "omc_state.py"
    spec = importlib.util.spec_from_file_location("omc_state_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_decision_input_module():
    module_path = ROOT / "scripts" / "omc_decision_input.py"
    spec = importlib.util.spec_from_file_location("omc_decision_input_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_guard(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_state_sync_session_marks_latest_session_confirmed(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    request = "skill sync smoke request"
    record = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-plan",
        "--request",
        request,
        "--roles",
        "analysis",
    )
    assert record.returncode == 0, record.stderr

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    session_id = latest.get("latest_session_id")
    assert session_id, latest
    assert latest.get("latest_confirmed_session_id") == session_id, latest
    assert latest.get("latest_confirmed_request") == request, latest
    assert latest.get("latest_skill") == "omc-plan", latest
    assert latest.get("latest_confirmation", {}).get("status") == "confirmed", latest

    session = _read_json(target / ".omc" / "state" / "sessions" / session_id / "session.json")
    assert session.get("confirmation", {}).get("status") == "confirmed", session
    assert session.get("confirmation", {}).get("source") == "skill_sync", session
    assert session.get("lifecycle", {}).get("status") == "active", session


def test_notepad_omits_pending_lines_when_no_pending_session(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-plan",
        "--request",
        "no pending request",
        "--roles",
        "analysis",
    )
    assert sync.returncode == 0, sync.stderr

    notepad = (target / ".omc" / "notepad.md").read_text(encoding="utf-8")
    assert "pending_roles" not in notepad, notepad
    assert "pending_request" not in notepad, notepad
    assert "pending_session_status" not in notepad, notepad


def test_sync_session_stores_latest_skill_in_latest_json(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    record = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-review",
        "--request",
        "latest_skill 저장 확인",
        "--roles",
        "code_review",
    )
    assert record.returncode == 0, record.stderr

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    assert latest.get("latest_skill") == "omc-review", (
        f"latest_skill 필드 없거나 불일치: {latest}"
    )


def test_notepad_marks_active_session_as_cleanup_needed_when_reason_exists(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "종료 의미 라벨 확인",
        "--roles",
        "senior_coding",
    )
    assert sync.returncode == 0, sync.stderr

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    session_id = str(latest.get("latest_session_id"))
    session_path = target / ".omc" / "state" / "sessions" / session_id / "session.json"
    session = _read_json(session_path)
    session["lifecycle"]["reason"] = "Implementation stopped after reproduction failed."
    session_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    status = _run("state", "status", "--target", str(target))
    assert status.returncode == 0, status.stderr
    assert "latest_session_note: 정리 필요" in status.stdout, status.stdout

    notepad = (target / ".omc" / "notepad.md").read_text(encoding="utf-8")
    assert "current_session_note" in notepad, notepad
    assert "정리 필요" in notepad, notepad


def test_status_matches_latest_after_sequential_sync_session_role_change(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    first = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-plan",
        "--request",
        "first analysis session",
        "--roles",
        "analysis",
    )
    assert first.returncode == 0, first.stderr

    second = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "second coding session",
        "--roles",
        "senior_coding",
    )
    assert second.returncode == 0, second.stderr

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    assert latest.get("latest_roles") == ["senior_coding"], latest
    assert latest.get("latest_confirmed_roles") == ["senior_coding"], latest

    status = _run("state", "status", "--target", str(target))
    assert status.returncode == 0, status.stderr
    assert "senior_coding" in status.stdout, status.stdout
    assert "second coding session" in status.stdout, status.stdout


def test_core_omc_skills_document_use_session_sync_step():
    expected_markers = {}
    for skills_root in (ROOT / ".agent" / "skills", ROOT / ".agents" / "skills"):
        expected_markers.update(
            {
                skills_root / "omc-plan" / "SKILL.md": "python3 scripts/omc.py state sync-session --target . --mode autopilot",
                skills_root / "omc-task" / "SKILL.md": "python3 scripts/omc_guard.py sync-require --target . --mode autopilot",
                skills_root / "omc-review" / "SKILL.md": "python3 scripts/omc.py state sync-session --target . --mode autopilot",
                skills_root / "omc-investigate" / "SKILL.md": "python3 scripts/omc.py state sync-session --target . --mode autopilot",
            }
        )

    for path, needle in expected_markers.items():
        text = path.read_text(encoding="utf-8")
        assert needle in text, f"{path.name} missing session sync step"


def test_core_omc_skill_mirrors_stay_in_sync():
    skill_names = [
        "omc-ceo-review",
        "omc-office-hours",
        "omc-plan",
        "omc-review",
        "omc-ship",
        "omc-status",
        "omc-task",
    ]

    for skill_name in skill_names:
        agent_text = (ROOT / ".agent" / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        agents_text = (ROOT / ".agents" / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert agent_text == agents_text, f"{skill_name} mirror files diverged"


def test_omc_guard_sync_require_records_confirmed_session(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    guarded = _run_guard(
        "sync-require",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "sync require request",
        "--roles",
        "senior_coding",
        "--for",
        "task",
    )
    assert guarded.returncode == 0, guarded.stderr

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    session_id = latest.get("latest_session_id")
    assert latest.get("latest_confirmed_session_id") == session_id, latest

    session = _read_json(target / ".omc" / "state" / "sessions" / session_id / "session.json")
    assert session.get("confirmation", {}).get("status") == "confirmed", session
    assert session.get("confirmation", {}).get("source") == "guard.sync_require", session
    assert session.get("lifecycle", {}).get("status") == "active", session


def test_guard_sync_require_replaces_previous_confirmed_role_for_mutating_gate(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    review = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-review",
        "--request",
        "review session",
        "--roles",
        "code_review",
    )
    assert review.returncode == 0, review.stderr

    guard = _run_guard(
        "sync-require",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-ship",
        "--request",
        "ship session",
        "--roles",
        "directive",
        "--for",
        "ship",
    )
    assert guard.returncode == 0, guard.stdout + guard.stderr
    assert "roles=directive" in guard.stdout, guard.stdout

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    assert latest.get("latest_roles") == ["directive"], latest
    assert latest.get("latest_confirmed_roles") == ["directive"], latest


def test_pending_sync_session_keeps_previous_latest_skill(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    first = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-review",
        "--request",
        "confirmed request",
        "--roles",
        "code_review",
    )
    assert first.returncode == 0, first.stderr

    second = _run(
        "state",
        "record",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "pending request",
        "--roles",
        "senior_coding",
    )
    assert second.returncode == 0, second.stderr

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    assert latest.get("latest_confirmation", {}).get("status") == "pending", latest
    assert latest.get("latest_skill") == "omc-review", latest


def test_status_separates_staged_scope_from_out_of_scope_dirty_changes(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    subprocess.run(["git", "init"], cwd=target, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "OMC Test"], cwd=target, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "omc@example.com"], cwd=target, check=True, capture_output=True, text=True)

    tracked_a = target / "a.py"
    tracked_b = target / "b.py"
    tracked_a.write_text("print('a1')\n", encoding="utf-8")
    tracked_b.write_text("print('b1')\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py", "b.py"], cwd=target, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=target, check=True, capture_output=True, text=True)

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "scope separation",
        "--roles",
        "senior_coding",
    )
    assert sync.returncode == 0, sync.stderr

    tracked_a.write_text("print('a2')\n", encoding="utf-8")
    tracked_b.write_text("print('b2')\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=target, check=True, capture_output=True, text=True)

    status = _run("state", "status", "--target", str(target))
    assert status.returncode == 0, status.stderr
    assert "현재 커밋 범위" in status.stdout, status.stdout
    assert "a.py" in status.stdout, status.stdout
    assert "범위 밖 dirty 변경" in status.stdout, status.stdout
    assert "b.py" in status.stdout, status.stdout


def test_status_includes_latest_run_and_recent_runs_context(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "run visibility",
        "--roles",
        "senior_coding",
    )
    assert sync.returncode == 0, sync.stderr

    first = _run("state", "run-start", "--target", str(target), "--command-name", "make test-a")
    assert first.returncode == 0, first.stderr
    first_run_id = first.stdout.strip()
    finished_first = _run(
        "state",
        "run-finish",
        "--target",
        str(target),
        "--run-id",
        first_run_id,
        "--status",
        "completed",
        "--message",
        "first run complete",
    )
    assert finished_first.returncode == 0, finished_first.stderr

    second = _run("state", "run-start", "--target", str(target), "--command-name", "make test-b")
    assert second.returncode == 0, second.stderr
    second_run_id = second.stdout.strip()
    finished_second = _run(
        "state",
        "run-finish",
        "--target",
        str(target),
        "--run-id",
        second_run_id,
        "--status",
        "failed",
        "--message",
        "second run failed",
    )
    assert finished_second.returncode == 0, finished_second.stderr

    status = _run("state", "status", "--target", str(target))
    assert status.returncode == 0, status.stderr
    assert "- latest_run: `make test-b` (failed)" in status.stdout, status.stdout
    assert "- recent_runs:" in status.stdout, status.stdout
    assert "make test-b(failed)" in status.stdout, status.stdout
    assert "make test-a(completed)" in status.stdout, status.stdout


def test_status_counts_autopilot_run_history_from_omc_runs(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    runs_dir = target / ".omc" / "runs" / "20260701T000000-observed"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "benchmark_source_type": "observed_output",
                "comparison_scope": "same_surface",
                "policy_pair": "baseline->candidate",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    status = _run("state", "status", "--target", str(target))
    assert status.returncode == 0, status.stderr
    assert "- runs: 1" in status.stdout, status.stdout
    assert "- pipeline_history_runs(.omc/runs): 1" in status.stdout, status.stdout


def test_status_counts_state_runs_and_autopilot_run_history_together(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "mixed run count visibility",
        "--roles",
        "senior_coding",
    )
    assert sync.returncode == 0, sync.stderr

    first = _run("state", "run-start", "--target", str(target), "--command-name", "make test-a")
    assert first.returncode == 0, first.stderr
    first_run_id = first.stdout.strip()
    finished_first = _run(
        "state",
        "run-finish",
        "--target",
        str(target),
        "--run-id",
        first_run_id,
        "--status",
        "completed",
        "--message",
        "first run complete",
    )
    assert finished_first.returncode == 0, finished_first.stderr

    second = _run("state", "run-start", "--target", str(target), "--command-name", "make test-b")
    assert second.returncode == 0, second.stderr
    second_run_id = second.stdout.strip()
    finished_second = _run(
        "state",
        "run-finish",
        "--target",
        str(target),
        "--run-id",
        second_run_id,
        "--status",
        "failed",
        "--message",
        "second run failed",
    )
    assert finished_second.returncode == 0, finished_second.stderr

    runs_dir = target / ".omc" / "runs" / "20260701T000000-observed"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "benchmark_source_type": "observed_output",
                "comparison_scope": "same_surface",
                "policy_pair": "baseline->candidate",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    status = _run("state", "status", "--target", str(target))
    assert status.returncode == 0, status.stderr
    assert "- runs: 3" in status.stdout, status.stdout
    assert "- pipeline_history_runs(.omc/runs): 1" in status.stdout, status.stdout


def test_status_calls_out_ship_blocker_when_commit_scope_is_empty(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()

    subprocess.run(["git", "init"], cwd=target, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "OMC Test"], cwd=target, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "omc@example.com"], cwd=target, check=True, capture_output=True, text=True)

    tracked = target / "only_unstaged.py"
    tracked.write_text("print('v1')\n", encoding="utf-8")
    subprocess.run(["git", "add", "only_unstaged.py"], cwd=target, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=target, check=True, capture_output=True, text=True)

    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr

    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-ship",
        "--request",
        "ship blocker hint",
        "--roles",
        "directive",
    )
    assert sync.returncode == 0, sync.stderr

    tracked.write_text("print('v2')\n", encoding="utf-8")

    status = _run("state", "status", "--target", str(target))
    assert status.returncode == 0, status.stderr
    assert "현재 커밋 범위: 없음" in status.stdout, status.stdout
    assert "ship 차단 힌트" in status.stdout, status.stdout
    assert "현재 커밋 범위가 없어 ship 불가" in status.stdout, status.stdout
    assert "다음 조치 힌트" in status.stdout, status.stdout
    assert "먼저 현재 커밋 범위를 만들어야 함" in status.stdout, status.stdout


def test_failed_run_summary_uses_shared_status_followup_input():
    mod = _load_state_module()
    decision_input_mod = _load_decision_input_module()

    decision_input = decision_input_mod.build_status_followup_input(
        request_kind="review",
        returncode=1,
    )
    expected = decision_input_mod.resolve_status_followup_from_input(decision_input)
    reason, next_step = mod._failed_run_summary(
        {
            "progress_message": "failed",
            "result": {"returncode": 1},
        },
        request_kind="review",
    )

    assert (reason, next_step) == expected
