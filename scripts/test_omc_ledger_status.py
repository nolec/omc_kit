import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

CLI = Path(__file__).resolve().with_name("omc.py")


@pytest.mark.parametrize("case, expected", [
    ("included", "INCLUDED"), ("outside", "NOT_INCLUDED"),
    ("missing", "UNKNOWN"), ("malformed", "UNKNOWN"),
    ("symlink", "UNKNOWN"), ("reverse", "UNKNOWN"),
    ("no_completion", "UNKNOWN"), ("unknown_oid", "UNKNOWN"),
    ("latest_valid", "INCLUDED"), ("full_oid", "INCLUDED"),
    ("config_included", "INCLUDED"), ("config_outside", "NOT_INCLUDED"),
    ("redirected_worktree", "UNKNOWN"), ("redirected_git_dir", "UNKNOWN"),
    ("redirected_objects", "UNKNOWN"),
])
def test_ledger_status(tmp_path, case, expected):
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-C", str(tmp_path), *args],
            text=True,
        ).strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "task")
    end = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "later")
    later = git("rev-parse", "HEAD")
    if case in ("outside", "config_outside"):
        git("checkout", "-q", "--detach", base)
    ledger = tmp_path / ".superpowers/sdd/example/progress.md"
    ledger.parent.mkdir(parents=True)
    content = "# SDD ledger — plan: docs/example.md\n"
    if case != "no_completion":
        a, b = (end, base) if case == "reverse" else (base, end)
        if case == "unknown_oid":
            b = "0" * 40
        length = 40 if case == "full_oid" else 7
        content += f"Task 1: complete (commits {a[:length]}..{b[:length]}, tests: pytest → 1 passed)\n"
    if case == "latest_valid":
        content += f"Task 2: complete (commits {end[:7]}..{later[:7]}, tests: pytest → 1 passed)\n"
    if case == "malformed":
        content += "Task 2: complete (missing commit range)\n"
    if case == "symlink":
        ledger.symlink_to(tmp_path / "elsewhere")
    elif case != "missing":
        ledger.write_text(content)
    def snapshot():
        return {p: (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                for p in tmp_path.rglob("*")}
    env = os.environ.copy()
    if case.startswith(("config_", "redirected_")):
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.worktree",
                   GIT_CONFIG_VALUE_0=str(tmp_path / "unrelated-worktree"),
                   GIT_CONFIG_PARAMETERS="invalid command-scoped Git config")
    overrides = {"redirected_worktree": "GIT_WORK_TREE", "redirected_git_dir": "GIT_DIR",
                 "redirected_objects": "GIT_OBJECT_DIRECTORY"}
    if case in overrides:
        env[overrides[case]] = str(tmp_path / "other-repository")
    before = snapshot()
    result = subprocess.run([sys.executable, "-B", str(CLI), "state", "status",
        "--target", str(tmp_path), "--superpowers-ledger", str(ledger.relative_to(tmp_path))],
        capture_output=True, text=True, env=env)
    assert result.returncode == {"INCLUDED": 0, "NOT_INCLUDED": 1, "UNKNOWN": 2}[expected], result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == expected
    if case in overrides:
        assert report["reason"] == "git_environment_override"
        assert "head" not in report
    if expected != "UNKNOWN":
        assert report["head"] == git("rev-parse", "HEAD")
        assert report["range_end"] == (later if case == "latest_valid" else end)
    assert snapshot() == before
