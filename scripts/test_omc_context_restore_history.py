import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).with_name("omc_context_save.py").resolve()


@pytest.mark.parametrize("case, expected", [
    ("same", "같은 지점"),
    ("ahead", "현재 이력이 앞섬"),
    ("outside", "현재 이력에 없음"),
    ("unknown", "확인 불가"),
])
def test_restore_compares_saved_commit_without_changing_wip(tmp_path, case, expected):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True,
        ).strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "later")
    saved = git("rev-parse", "--short", "HEAD") if case in {"same", "outside"} else base[:7]
    if case == "outside":
        git("checkout", "-q", "--detach", base)
    if case == "unknown":
        saved = "missing"
    wip = tmp_path / "wip.json"
    wip.write_text(json.dumps({"commit": saved, "remaining": "saved action"}))
    before = wip.read_bytes(), wip.stat().st_mtime_ns
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "restore"],
        env={**os.environ, "OMC_PROJECT_ROOT": str(tmp_path), "OMC_WIP_PATH": str(wip)},
        capture_output=True, text=True, check=True,
    )
    assert f"저장 커밋과 현재 HEAD: {expected}" in result.stdout
    assert "남은 작업 (저장 당시 메모):\n  saved action" in result.stdout
    assert (wip.read_bytes(), wip.stat().st_mtime_ns) == before
