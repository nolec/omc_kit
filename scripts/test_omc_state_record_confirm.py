import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OMC = ROOT / "scripts" / "omc.py"


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


def test_core_omc_skills_document_use_sync_session_step():
    skill_files = [
        ROOT / ".agents" / "skills" / "omc-plan" / "SKILL.md",
        ROOT / ".agents" / "skills" / "omc-task" / "SKILL.md",
        ROOT / ".agents" / "skills" / "omc-review" / "SKILL.md",
        ROOT / ".agents" / "skills" / "omc-investigate" / "SKILL.md",
    ]
    needle = "python3 scripts/omc.py state sync-session --target . --mode autopilot"

    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        assert needle in text, f"{path.name} missing OMC session sync step"
