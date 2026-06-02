from __future__ import annotations

import json
from pathlib import Path

import omc_state


def _load_session_path(project_root: Path, session_id: str) -> Path:
    return project_root / ".omc" / "state" / "sessions" / session_id / "session.json"


def test_status_and_notepad_flag_stale_active_confirmed_session(tmp_path: Path):
    omc_state.init_state(tmp_path)
    session = omc_state.record_session(
        tmp_path,
        mode="autopilot",
        title="autopilot",
        request="stale 상태 확인",
        role_ids=["directive", "tdd"],
        confirmed=True,
        confirmation_source="test",
    )
    session_id = str(session["session_id"])
    session_path = _load_session_path(tmp_path, session_id)
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["lifecycle"]["updated_at"] = "2026-06-01T00:00:00+00:00"
    session_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rendered_status = omc_state.status(tmp_path)
    rendered_notepad = (tmp_path / ".omc" / "notepad.md").read_text(encoding="utf-8")

    assert "stale_active" in rendered_status
    assert "stale_active" in rendered_notepad
    assert "current_session_reason" in rendered_notepad
    assert "pending_request" not in rendered_notepad
