import hashlib
import json
import subprocess
import sys
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import auto_prompt


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


def _sync_task_session(
    repo: Path,
    request: str = "observed task",
    *,
    work_class: str | None = "implementation",
) -> str:
    init = _run("state", "init", "--target", str(repo))
    assert init.returncode == 0, init.stderr
    args = [
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
    ]
    if work_class is not None:
        args.extend(["--work-class", work_class])
    sync = _run(*args)
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


def test_natural_entry_infers_conservative_work_class_for_coding_roles():
    assert auto_prompt._work_class_for_request(
        "latency benchmark fixture를 재측정해줘",
        ["senior_coding"],
    ) == "benchmark_maintenance"
    assert auto_prompt._work_class_for_request(
        "README 문서만 최신화해줘",
        ["senior_coding"],
    ) == "document_only"
    assert auto_prompt._work_class_for_request(
        "Update README capitalization",
        ["senior_coding"],
    ) == "document_only"
    assert auto_prompt._work_class_for_request(
        "Document API usage in README",
        ["senior_coding"],
    ) == "document_only"
    assert auto_prompt._work_class_for_request(
        "로그인 오류를 수정해줘",
        ["senior_coding"],
    ) == "implementation"
    assert auto_prompt._work_class_for_request(
        "README를 업데이트하고 로그인 API를 구현해줘",
        ["senior_coding"],
    ) == "implementation"
    assert auto_prompt._work_class_for_request(
        "benchmark 결과를 표시하는 제품 기능을 구현해줘",
        ["senior_coding"],
    ) == "implementation"
    assert auto_prompt._work_class_for_request(
        "latency benchmark runner를 구현해줘",
        ["senior_coding"],
    ) == "benchmark_maintenance"
    assert auto_prompt._work_class_for_request(
        "benchmark suite를 구현해줘",
        ["senior_coding"],
    ) == "benchmark_maintenance"
    assert auto_prompt._work_class_for_request(
        "benchmark 로드맵 문서만 최신화해줘",
        ["senior_coding"],
    ) == "document_only"
    assert auto_prompt._work_class_for_request(
        "benchmark dashboard를 구현해줘",
        ["senior_coding"],
    ) == "implementation"
    assert auto_prompt._work_class_for_request(
        "benchmark 결과를 UI 컴포넌트로 구현해줘",
        ["senior_coding"],
    ) == "implementation"
    assert auto_prompt._work_class_for_request(
        "상태를 요약해줘",
        ["analysis"],
    ) is None


def test_natural_entry_explicit_work_class_overrides_inference():
    assert auto_prompt._work_class_for_request(
        "benchmark helper를 구현해줘",
        ["senior_coding"],
        explicit="synthetic",
    ) == "synthetic"


def test_natural_prompt_entry_records_inferred_work_class(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)

    result = _run(
        "prompt",
        "latency benchmark fixture를 재측정해줘",
        "--roles",
        "senior_coding",
        "--assume-confirm",
        "--context-mode",
        "lean",
        "--out",
        str(target / "prompt.md"),
        cwd=target,
    )

    assert result.returncode == 0, result.stderr
    latest = _read_json(target / ".omc" / "state" / "latest.json")
    session = _read_json(
        target
        / ".omc"
        / "state"
        / "sessions"
        / latest["latest_session_id"]
        / "session.json"
    )
    assert session["work_class"] == "benchmark_maintenance"


def test_natural_prompt_entry_fails_when_explicit_work_class_has_no_coding_role(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)

    result = _run(
        "prompt",
        "상태를 요약해줘",
        "--roles",
        "analysis",
        "--work-class",
        "synthetic",
        "--assume-confirm",
        "--context-mode",
        "lean",
        "--out",
        str(target / "prompt.md"),
        cwd=target,
    )

    assert result.returncode != 0
    assert "work class requires a senior_coding session" in result.stderr


def _sync_directive_session(repo: Path, title: str) -> str:
    sync = _run(
        "state",
        "sync-session",
        "--target",
        str(repo),
        "--mode",
        "autopilot",
        "--title",
        title,
        "--request",
        f"run {title}",
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
    assert receipt["schema_version"] == 2
    assert receipt["session_id"] == session_id
    assert receipt["baseline_commit"] == baseline
    assert receipt["followup_commit"] == followup
    assert receipt["changed_paths"] == ["app.py"]
    assert receipt["provider_outputs_available"] is False
    assert receipt["work_class"] == "implementation"
    session = _read_json(
        target / ".omc" / "state" / "sessions" / session_id / "session.json"
    )
    assert receipt["work_class_locked_at"] == session["created_at"]


def test_state_complete_preserves_explicit_work_class_from_session_start(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    session_id = _sync_task_session(
        target,
        "measure benchmark latency",
        work_class="benchmark_maintenance",
    )

    session_path = target / ".omc" / "state" / "sessions" / session_id / "session.json"
    assert _read_json(session_path)["work_class"] == "benchmark_maintenance"

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "measure benchmark")
    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    receipt_path = target / ".omc" / "state" / "sessions" / session_id / "completion.json"
    receipt = _read_json(receipt_path)
    assert receipt["schema_version"] == 2
    assert receipt["work_class"] == "benchmark_maintenance"
    assert receipt["work_class_locked_at"] == _read_json(session_path)["created_at"]


def test_state_complete_preserves_pre_upgrade_v1_pending_completion(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    baseline = _init_git_repo(target)
    request = "legacy observed completion receipt"
    session_id = _sync_task_session(target, request)
    session_path = target / ".omc" / "state" / "sessions" / session_id / "session.json"
    session = _read_json(session_path)
    session.pop("work_class")
    session.pop("completion_action")
    session.pop("work_id")
    session_path.write_text(json.dumps(session), encoding="utf-8")
    pending_path = target / ".omc" / "state" / "pending-completion.json"
    pending = _read_json(pending_path)
    pending["schema_version"] = 1
    pending.pop("work_class")
    pending.pop("work_class_locked_at")
    pending.pop("work_id")
    pending.pop("root_session_id")
    pending.pop("session_ids")
    pending.pop("rework_count")
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "complete legacy task")
    followup = _git(target, "rev-parse", "HEAD")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr
    receipt = _read_json(session_path.parent / "completion.json")
    assert receipt == {
        "schema_version": 1,
        "session_id": session_id,
        "request_sha256": hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "baseline_commit": baseline,
        "followup_commit": followup,
        "completed_at": receipt["completed_at"],
        "changed_paths": ["app.py"],
        "provider_outputs_available": False,
    }


def test_confirm_legacy_coding_session_skips_pending_completion_safely(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    session_id = _sync_task_session(
        target,
        "legacy coding session",
        work_class="implementation",
    )
    session_path = target / ".omc" / "state" / "sessions" / session_id / "session.json"
    session = _read_json(session_path)
    session.pop("work_class")
    session_path.write_text(json.dumps(session), encoding="utf-8")
    (target / ".omc" / "state" / "pending_completion.json").unlink(
        missing_ok=True
    )

    result = _run(
        "state",
        "confirm",
        "--target",
        str(target),
        "--session-id",
        session_id,
    )

    assert result.returncode == 0, result.stderr
    assert not (target / ".omc" / "state" / "pending_completion.json").exists()


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


def test_state_complete_preserves_task_through_roadmap_sync_commit_session(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    task_session_id = _sync_task_session(target, "task then roadmap sync then commit")
    roadmap_session_id = _sync_directive_session(target, "roadmap-sync-commit")

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "implement task and sync roadmap")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    task_receipt = target / ".omc" / "state" / "sessions" / task_session_id / "completion.json"
    roadmap_receipt = target / ".omc" / "state" / "sessions" / roadmap_session_id / "completion.json"
    assert task_receipt.exists()
    assert not roadmap_receipt.exists()


def test_state_complete_preserves_task_through_roadmap_and_commit_alias(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    task_session_id = _sync_task_session(target, "task then roadmap alias then commit")
    roadmap_session_id = _sync_directive_session(target, "roadmap-and-commit")

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "implement task and sync roadmap through alias")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    task_receipt = target / ".omc" / "state" / "sessions" / task_session_id / "completion.json"
    roadmap_receipt = target / ".omc" / "state" / "sessions" / roadmap_session_id / "completion.json"
    assert task_receipt.exists()
    assert not roadmap_receipt.exists()


def test_state_complete_clears_task_for_unrelated_directive(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    task_session_id = _sync_task_session(target, "task then unrelated directive")
    directive_session_id = _sync_directive_session(target, "deploy-maintenance")

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(target, "add", "app.py")
    _git(target, "commit", "-qm", "unrelated directive commit")

    result = _run("state", "complete", "--target", str(target))
    assert result.returncode == 0, result.stderr

    task_receipt = target / ".omc" / "state" / "sessions" / task_session_id / "completion.json"
    directive_receipt = target / ".omc" / "state" / "sessions" / directive_session_id / "completion.json"
    assert not task_receipt.exists()
    assert not directive_receipt.exists()


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
    text = template.read_text(encoding="utf-8")
    assert "python3 scripts/omc.py state decision-consume-current --target ." in text
    assert "python3 scripts/omc.py state complete --target ." in text


def test_current_local_commit_decision_is_consumed_after_matching_commit(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [
            {
                "id": "confirm",
                "aliases": ["확인"],
                "value": "commit",
                "paths": ["app.py"],
            }
        ],
        ensure_ascii=False,
    )
    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    )
    assert resolved.returncode == 0, resolved.stderr
    _git(target, "add", "app.py")
    authorized = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "git commit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert authorized.returncode == 0, authorized.stdout
    _git(target, "commit", "-qm", "approved update")

    consumed = _run(
        "state",
        "decision-consume-current",
        "--target",
        str(target),
    )

    assert consumed.returncode == 0, consumed.stderr
    assert json.loads(consumed.stdout)["consumed"] is True


def test_current_local_commit_decision_rejects_expired_authorization(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [
            {
                "id": "confirm",
                "aliases": ["확인"],
                "value": "commit",
                "paths": ["app.py"],
            }
        ],
        ensure_ascii=False,
    )
    assert _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    ).returncode == 0
    assert _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    ).returncode == 0
    _git(target, "add", "app.py")
    authorized = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "git commit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert authorized.returncode == 0, authorized.stdout

    latest_path = target / ".omc" / "state" / "latest.json"
    latest = _read_json(latest_path)
    latest["pending_decision"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    _git(target, "commit", "--no-verify", "-qm", "expired update")

    consumed = _run(
        "state",
        "decision-consume-current",
        "--target",
        str(target),
    )

    assert consumed.returncode != 0
    assert json.loads(consumed.stdout)["reason"] == "expired"


def test_post_commit_hook_consumes_authorized_local_commit_decision(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [
            {
                "id": "confirm",
                "aliases": ["확인"],
                "value": "commit",
                "paths": ["app.py"],
            }
        ],
        ensure_ascii=False,
    )
    assert _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    ).returncode == 0
    assert _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    ).returncode == 0
    _git(target, "add", "app.py")
    authorized = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "git commit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert authorized.returncode == 0, authorized.stdout

    (target / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    hook = target / ".git" / "hooks" / "post-commit"
    hook.write_bytes((ROOT / "templates" / "post-commit").read_bytes())
    hook.chmod(0o755)
    _git(target, "commit", "-qm", "approved update")

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    assert latest["pending_decision"]["status"] == "consumed"
    assert latest["pending_decision"]["commit_head"] == _git(
        target, "rev-parse", "HEAD"
    )


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


def test_local_commit_decision_is_scope_bound_and_single_use(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    session_id = _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [
            {
                "id": "1",
                "aliases": ["1", "1번"],
                "value": "selected-group",
            }
        ],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-group",
        "--action",
        "local_commit",
        "--options-json",
        options,
        "--ttl-seconds",
        "600",
    )
    assert opened.returncode == 0, opened.stderr

    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "1번",
    )
    assert resolved.returncode == 0, resolved.stderr
    result = json.loads(resolved.stdout)
    assert result["resolved"] is True
    assert result["action"] == "local_commit"
    assert result["selected_option"] == "1"

    latest = _read_json(target / ".omc" / "state" / "latest.json")
    decision = latest["pending_decision"]
    assert decision["session_id"] == session_id
    assert decision["status"] == "acknowledged"
    assert decision["scope_fingerprint"]

    subprocess.run(["git", "add", "app.py"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "update app"], cwd=target, check=True)
    consumed = _run(
        "state",
        "decision-consume",
        "--target",
        str(target),
        "--decision-id",
        "commit-group",
    )
    assert consumed.returncode == 0, consumed.stderr
    replay = _run(
        "state",
        "decision-consume",
        "--target",
        str(target),
        "--decision-id",
        "commit-group",
    )
    assert replay.returncode != 0


def test_local_commit_decision_rejects_commit_from_unselected_group(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "other.py").write_text("other = 1\n", encoding="utf-8")
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit selected group")
    options = json.dumps(
        [
            {"id": "1", "aliases": ["1"], "value": "app", "paths": ["app.py"]},
            {"id": "2", "aliases": ["2"], "value": "other", "paths": ["other.py"]},
        ],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "grouped-commit",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "1",
    )
    assert resolved.returncode == 0, resolved.stderr

    subprocess.run(["git", "add", "other.py"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "commit unselected group"], cwd=target, check=True)
    consumed = _run(
        "state",
        "decision-consume",
        "--target",
        str(target),
        "--decision-id",
        "grouped-commit",
    )
    assert consumed.returncode != 0
    assert json.loads(consumed.stdout)["reason"] == "commit_scope_mismatch"


def test_local_commit_decision_consumes_commit_from_selected_group(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "other.py").write_text("other = 1\n", encoding="utf-8")
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit selected group")
    options = json.dumps(
        [
            {"id": "1", "aliases": ["1"], "value": "app", "paths": ["app.py"]},
            {"id": "2", "aliases": ["2"], "value": "other", "paths": ["other.py"]},
        ],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "grouped-commit",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "1",
    )
    assert resolved.returncode == 0, resolved.stderr

    subprocess.run(["git", "add", "app.py"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "commit selected group"], cwd=target, check=True)
    consumed = _run(
        "state",
        "decision-consume",
        "--target",
        str(target),
        "--decision-id",
        "grouped-commit",
    )
    assert consumed.returncode == 0, consumed.stderr
    assert json.loads(consumed.stdout)["consumed"] is True


def test_task_review_pilot_start_decision_requires_exact_binding_and_receipt(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    session_id = _sync_task_session(target, "start task review pilot")
    binding = {
        "session_id": session_id,
        "roster_sha256": "a" * 64,
        "pilot_contract_sha256": "b" * 64,
        "source_commit": _git(target, "rev-parse", "HEAD"),
    }
    options = json.dumps(
        [{"id": "approve", "aliases": ["승인"], "value": binding}],
        ensure_ascii=False,
    )
    opened = _run(
        "state", "decision-open", "--target", str(target),
        "--decision-id", "pilot-start", "--action", "task_review_pilot_start",
        "--options-json", options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state", "decision-resolve", "--target", str(target), "--response", "승인"
    )
    assert resolved.returncode == 0, resolved.stderr
    receipt_path = target / ".omc/state/task-review-pilot/pilot-start.json"
    consumed = _run(
        "state", "decision-consume", "--target", str(target),
        "--decision-id", "pilot-start", "--receipt-output", str(receipt_path),
    )
    assert consumed.returncode == 0, consumed.stderr
    receipt = _read_json(receipt_path)
    assert receipt["schema_version"] == "omc-task-review-pilot-start/v1"
    assert receipt["action"] == "task_review_pilot_start"
    assert receipt["binding"] == binding
    assert receipt["consumed_at"]


def test_task_review_pilot_start_rejects_invalid_binding(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    session_id = _sync_task_session(target, "start task review pilot")
    options = json.dumps(
        [{
            "id": "approve",
            "aliases": ["승인"],
            "value": {"session_id": session_id, "roster_sha256": "a" * 64},
        }],
        ensure_ascii=False,
    )
    opened = _run(
        "state", "decision-open", "--target", str(target),
        "--decision-id", "pilot-start", "--action", "task_review_pilot_start",
        "--options-json", options,
    )
    assert opened.returncode != 0


def test_local_commit_decision_requires_paths_for_multiple_groups(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit selected group")
    options = json.dumps(
        [
            {"id": "1", "aliases": ["1"], "value": "app"},
            {"id": "2", "aliases": ["2"], "value": "other"},
        ],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "grouped-commit",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode != 0
    assert "paths" in json.loads(opened.stdout)["reason"]


def test_local_commit_decision_rejects_non_object_option_with_structured_error(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _sync_task_session(target, "commit selected group")

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "malformed-option",
        "--action",
        "local_commit",
        "--options-json",
        "[null]",
    )
    assert opened.returncode == 2
    result = json.loads(opened.stdout)
    assert result["opened"] is False
    assert result["reason"] == "each option must be a JSON object"
    assert "Traceback" not in opened.stderr


def test_local_commit_decision_rejects_stale_confirmed_session(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _sync_task_session(target, "old confirmed task")
    pending = _run(
        "state",
        "record",
        "--target",
        str(target),
        "--mode",
        "autopilot",
        "--title",
        "omc-task",
        "--request",
        "new pending task",
        "--roles",
        "senior_coding",
        "--work-class",
        "implementation",
    )
    assert pending.returncode == 0, pending.stderr
    options = json.dumps(
        [{"id": "confirm", "aliases": ["확인"], "value": "commit"}],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "stale-commit",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode != 0
    assert "current confirmed session" in json.loads(opened.stdout)["reason"]


def test_local_commit_decision_rejects_committed_path_outside_open_scope(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [{"id": "confirm", "aliases": ["확인"], "value": "commit"}],
        ensure_ascii=False,
    )
    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    )
    assert resolved.returncode == 0, resolved.stderr

    (target / "outside.py").write_text("outside = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py", "outside.py"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "commit outside scope"], cwd=target, check=True)
    consumed = _run(
        "state",
        "decision-consume",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
    )
    assert consumed.returncode != 0
    assert json.loads(consumed.stdout)["reason"] == "commit_scope_mismatch"


def test_local_commit_decision_rejects_content_changed_after_acknowledgement(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [{"id": "confirm", "aliases": ["확인"], "value": "commit"}],
        ensure_ascii=False,
    )
    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    )
    assert resolved.returncode == 0, resolved.stderr

    (target / "app.py").write_text("value = 'unapproved'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "replace approved content"], cwd=target, check=True)
    consumed = _run(
        "state",
        "decision-consume",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
    )
    assert consumed.returncode != 0
    assert json.loads(consumed.stdout)["reason"] == "commit_content_mismatch"


def test_local_commit_decision_rejects_scope_drift_and_privileged_action(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [{"id": "confirm", "aliases": ["응", "확인"], "value": "commit"}],
        ensure_ascii=False,
    )

    privileged = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "push",
        "--action",
        "push",
        "--options-json",
        options,
    )
    assert privileged.returncode != 0

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")

    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "응",
    )
    assert resolved.returncode != 0
    result = json.loads(resolved.stdout)
    assert result["resolved"] is False
    assert result["reason"] == "scope_changed"


def test_local_commit_decision_survives_staging_but_expires(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [{"id": "confirm", "aliases": ["확인"], "value": "commit"}],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    subprocess.run(["git", "add", "app.py"], cwd=target, check=True)

    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    )
    assert resolved.returncode == 0, resolved.stderr

    latest_path = target / ".omc" / "state" / "latest.json"
    latest = _read_json(latest_path)
    latest["pending_decision"]["status"] = "pending"
    latest["pending_decision"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    expired = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    )
    assert expired.returncode != 0
    assert json.loads(expired.stdout)["reason"] == "expired"


def test_local_commit_decision_is_superseded_by_new_session(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [{"id": "confirm", "aliases": ["확인"], "value": "commit"}],
        ensure_ascii=False,
    )
    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr

    _sync_task_session(target, "different task")
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    )
    assert resolved.returncode != 0
    assert json.loads(resolved.stdout)["reason"] == "no_pending_decision"
    latest = _read_json(target / ".omc" / "state" / "latest.json")
    assert latest["decision_history"][-1]["status"] == "superseded"


def test_local_commit_decision_rejects_duplicate_aliases(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [
            {"id": "one", "aliases": ["1"], "value": "group-one"},
            {"id": "two", "aliases": [" 1 "], "value": "group-two"},
        ],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-group",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode != 0
    assert "unique" in json.loads(opened.stdout)["reason"]


def test_local_commit_decision_ignores_tracked_omc_state_changes(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr
    subprocess.run(["git", "add", ".omc"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "track omc state"], cwd=target, check=True)
    _sync_task_session(target, "commit approved changes")
    options = json.dumps(
        [{"id": "confirm", "aliases": ["확인"], "value": "commit"}],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "commit-confirm",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "확인",
    )
    assert resolved.returncode == 0, resolved.stdout


def test_local_commit_decision_rejects_unapproved_tracked_omc_policy(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    init = _run("state", "init", "--target", str(target))
    assert init.returncode == 0, init.stderr
    subprocess.run(["git", "add", ".omc"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "track omc baseline"], cwd=target, check=True)
    _sync_task_session(target, "commit app only")
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    (target / ".omc" / "policy.json").write_text(
        json.dumps({"enforce_confirm": False}), encoding="utf-8"
    )
    options = json.dumps(
        [{"id": "1", "aliases": ["1"], "value": "app", "paths": ["app.py"]}],
        ensure_ascii=False,
    )

    opened = _run(
        "state",
        "decision-open",
        "--target",
        str(target),
        "--decision-id",
        "app-only",
        "--action",
        "local_commit",
        "--options-json",
        options,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = _run(
        "state",
        "decision-resolve",
        "--target",
        str(target),
        "--response",
        "1",
    )
    assert resolved.returncode == 0, resolved.stderr

    subprocess.run(["git", "add", "app.py", ".omc/policy.json"], cwd=target, check=True)
    subprocess.run(["git", "commit", "-qm", "app plus unapproved policy"], cwd=target, check=True)
    consumed = _run(
        "state",
        "decision-consume",
        "--target",
        str(target),
        "--decision-id",
        "app-only",
    )
    assert consumed.returncode != 0
    assert json.loads(consumed.stdout)["reason"] == "commit_scope_mismatch"


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
        "--work-class",
        "implementation",
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
        "--work-class",
        "implementation",
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
        plan_skill = skills_root / "omc-plan" / "SKILL.md"
        plan_workflow = skills_root / "omc-plan" / "references" / "workflow.md"
        plan_skill_text = plan_skill.read_text(encoding="utf-8")
        plan_workflow_text = plan_workflow.read_text(encoding="utf-8")
        assert "일반 요청:`references/workflow.md`" in plan_skill_text
        assert (
            "python3 scripts/omc.py state sync-session --target . --mode autopilot"
            in plan_workflow_text
        )
        expected_markers.update(
            {
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
        "--work-class",
        "implementation",
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
        "--work-class",
        "implementation",
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
        "--work-class",
        "implementation",
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
        "--work-class",
        "implementation",
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
        "--work-class",
        "implementation",
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
