import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("omc_wip_notice.py")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "WIP Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "commit", "--allow-empty", "-qm", "saved")
    return tmp_path


def run_notice(root, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, "-B", "-c",
         "import omc_wip_notice, pathlib; raise SystemExit(omc_wip_notice.main(pathlib.Path(__import__('sys').argv[1])))",
         str(root)],
        cwd=SCRIPT.parent,
        env=env,
        input=json.dumps({"cwd": str(cwd or root), "hook_event_name": "SessionStart", "source": "startup"}),
        capture_output=True, text=True, timeout=12,
    )


def wip_path(root):
    path = root / ".omc/wip/latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize(("relation", "message"), [
    ("same", "같은 지점"),
    ("ahead", "현재 이력이 앞섬"),
    ("diverged", "현재 이력에 없음"),
    ("unknown", "확인 불가"),
])
def test_notice_relations(repo, relation, message):
    saved = git(repo, "rev-parse", "HEAD")
    if relation in ("ahead", "diverged"):
        git(repo, "commit", "--allow-empty", "-qm", "next")
    if relation == "diverged":
        saved = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "--detach", "-q", "HEAD^")
    if relation == "unknown":
        saved = "000000000"
    wip_path(repo).write_text(json.dumps({"saved_at": "2026-11-25", "commit": saved}))
    subdir = repo / "subdir"
    subdir.mkdir()
    result = run_notice(repo, subdir)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    assert message in output["additionalContext"]
    assert message in json.loads(result.stdout)["systemMessage"]
    assert "\n" not in output["additionalContext"]


@pytest.mark.parametrize("contents", [None, "{broken", "[]", " " * 65537, '{"saved_at":"today"}', "link"])
def test_invalid_wip_is_silent(repo, contents):
    path = wip_path(repo)
    if contents == "link":
        real = repo / "real.json"
        real.write_text(json.dumps({"saved_at": "today", "commit": "0123"}))
        path.symlink_to(real)
    elif contents is not None:
        path.write_text(contents)
    result = run_notice(repo)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_notice_stays_local_and_read_only(repo):
    tracked = repo / "tracked"
    tracked.write_text("staged")
    git(repo, "add", "tracked")
    tracked.write_text("working")
    ledger = repo / "progress.md"
    ledger.write_text("Task 1")
    other = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "--detach", str(other))
    path = wip_path(repo)
    path.write_text(json.dumps({"saved_at": "today", "commit": git(repo, "rev-parse", "HEAD"),
                                "remaining": "DO NOT INCLUDE THIS MEMO"}))
    files = [path, ledger, tracked, repo / ".git/index"]
    before = {p: p.read_bytes() for p in files}
    status = git(repo, "--no-optional-locks", "status", "--porcelain")
    env = {**os.environ, "OMC_PROJECT_ROOT": str(other), "OMC_WIP_PATH": str(other / "test.json")}
    result = run_notice(repo, env=env)
    assert result.returncode == 0, result.stderr
    assert "같은 지점" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "DO NOT INCLUDE" not in result.stdout
    assert run_notice(repo, other).stdout == ""
    assert {p: p.read_bytes() for p in files} == before
    assert git(repo, "--no-optional-locks", "status", "--porcelain") == status
