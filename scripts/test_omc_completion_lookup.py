import json
from pathlib import Path
import subprocess
import sys

import pytest

CLI = Path(__file__).with_name("omc.py")


@pytest.mark.parametrize("case, status, code", [
    ("match", "MATCH", 0), ("different", "MISMATCH", 1),
    ("absent", "NO_RECORD", 1), ("invalid", "UNKNOWN", 2),
    ("symlink", "UNKNOWN", 2), ("wrong_paths", "UNKNOWN", 2),
])
def test_commit_completion_lookup(tmp_path, case, status, code):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args]).strip().decode()

    git("init", "-q")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-qm", "base")
    baseline = git("rev-parse", "HEAD")
    (tmp_path / "file.txt").write_text("change")
    git("add", "file.txt")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "change")
    head = git("rev-parse", "HEAD")
    receipt = tmp_path / ".omc/state/sessions/session-1/completion.json"
    receipt.parent.mkdir(parents=True)
    if case in {"match", "different", "wrong_paths"}:
        receipt.write_text(json.dumps(dict(schema_version=1, session_id="session-1",
            baseline_commit=baseline, followup_commit=head, changed_paths=["other.txt" if case == "wrong_paths" else "file.txt"])))
    elif case == "invalid":
        receipt.write_text("{")
    elif case == "symlink":
        receipt.symlink_to(tmp_path / "file.txt")

    def snapshot():
        return {p: (p.lstat().st_mode, p.lstat().st_mtime_ns,
                    p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                for p in tmp_path.rglob("*")}

    before = snapshot()
    result = subprocess.run([sys.executable, "-B", str(CLI), "state", "capture-status",
        "--target", str(tmp_path), "--session-id", "session-1",
        "--commit", baseline if case == "different" else head], capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    assert json.loads(result.stdout)["status"] == status
    assert before == snapshot()
