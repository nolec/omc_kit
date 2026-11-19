import json
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
])
def test_ledger_status(tmp_path, case, expected):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "task")
    end = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "later")
    later = git("rev-parse", "HEAD")
    if case == "outside":
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
    before = snapshot()
    result = subprocess.run([sys.executable, "-B", str(CLI), "state", "status",
        "--target", str(tmp_path), "--superpowers-ledger", str(ledger.relative_to(tmp_path))],
        capture_output=True, text=True)
    assert result.returncode == {"INCLUDED": 0, "NOT_INCLUDED": 1, "UNKNOWN": 2}[expected], result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == expected
    if expected != "UNKNOWN":
        assert report["head"] == git("rev-parse", "HEAD")
        assert report["range_end"] == (later if case == "latest_valid" else end)
    assert snapshot() == before
