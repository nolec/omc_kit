from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from omc_state import _find_constitution


ROOT = Path(__file__).resolve().parents[1]
SESSION_HOOK = ROOT / "templates" / ".agent-hooks" / "omc-session-start.sh"


def _session_root(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "omc.py").write_text(
        """from pathlib import Path
import os
import sys
import time

root = Path.cwd()
with (root / ".omc" / "calls.log").open("a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
failure = os.environ.get("OMC_TEST_FAIL_ONCE", "")
is_state_init = sys.argv[1:3] == ["state", "init"]
is_session_hook = sys.argv[1:3] == ["hook", "session_start"]
should_fail = (failure == "state_init" and is_state_init) or (failure == "session_hook" and is_session_hook)
failure_marker = root / ".omc" / f"failed-{failure}"
if should_fail and not failure_marker.exists():
    failure_marker.touch()
    time.sleep(float(os.environ.get("OMC_TEST_FAILURE_DELAY", "0")))
    raise SystemExit(1)
if sys.argv[1:3] == ["state", "init"] and "--force" in sys.argv:
    (root / ".omc" / "summary.md").write_text("# fresh common summary\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )
    omc = tmp_path / ".omc"
    omc.mkdir()
    (omc / "summary.md").write_text("# bounded summary\n", encoding="utf-8")
    return tmp_path


def _run_session_hook(root: Path, payload: dict, **extra_env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SESSION_HOOK), "codex"],
        cwd=root,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ, **extra_env},
        check=False,
    )


def test_constitution_ignores_executor_overlays(tmp_path: Path) -> None:
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".gemini" / "GEMINI.md").write_text("GEMINI OVERLAY", encoding="utf-8")
    (tmp_path / ".claude" / "CLAUDE.md").write_text("CLAUDE OVERLAY", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("COMMON AGENTS", encoding="utf-8")

    assert _find_constitution(tmp_path) == "COMMON AGENTS"


def test_same_stable_event_is_emitted_once(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    event_id = f"event-{uuid.uuid4()}"

    first = _run_session_hook(root, {"event_id": event_id})
    second = _run_session_hook(root, {"event_id": event_id})

    assert "# fresh common summary" in first.stdout
    assert second.stdout == ""
    calls = (root / ".omc" / "calls.log").read_text(encoding="utf-8").splitlines()
    assert sum("hook session_start" in call for call in calls) == 1


def test_concurrent_event_does_not_reclaim_lock_before_owner_writes_pid(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    event_id = f"event-{uuid.uuid4()}"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    marker = tmp_path / "lock-created"
    mkdir_shim = shim_dir / "mkdir"
    mkdir_shim.write_text(
        """#!/usr/bin/env bash
/bin/mkdir "$@"
status=$?
if [[ $status -eq 0 && "$*" == *omc-session-start-*.lock* ]]; then
  : >"$OMC_TEST_LOCK_MARKER"
  sleep 1
fi
exit $status
""",
        encoding="utf-8",
    )
    mkdir_shim.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        "OMC_TEST_LOCK_MARKER": str(marker),
    }
    first = subprocess.Popen(
        ["bash", str(SESSION_HOOK), "codex"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert first.stdin is not None
    first.stdin.write(json.dumps({"event_id": event_id}))
    first.stdin.close()
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()

    second = _run_session_hook(root, {"event_id": event_id})
    first.wait(timeout=5)

    assert second.stdout == ""
    calls = (root / ".omc" / "calls.log").read_text(encoding="utf-8").splitlines()
    assert sum("hook session_start" in call for call in calls) == 1


def test_missing_event_id_never_suppresses_session_context(tmp_path: Path) -> None:
    root = _session_root(tmp_path)

    first = _run_session_hook(root, {"session_id": "not-a-stable-event"})
    second = _run_session_hook(root, {"session_id": "not-a-stable-event"})

    assert "# fresh common summary" in first.stdout
    assert "# fresh common summary" in second.stdout
    calls = (root / ".omc" / "calls.log").read_text(encoding="utf-8").splitlines()
    assert sum("hook session_start" in call for call in calls) == 2


def test_session_start_rebuilds_stale_summary_before_output(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    (root / ".omc" / "summary.md").write_text("GEMINI OVERLAY\n", encoding="utf-8")

    result = _run_session_hook(root, {})

    assert "# fresh common summary" in result.stdout
    assert "GEMINI OVERLAY" not in result.stdout


def test_same_event_retries_after_state_init_failure(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    event_id = f"event-{uuid.uuid4()}"

    first = _run_session_hook(root, {"event_id": event_id}, OMC_TEST_FAIL_ONCE="state_init")
    second = _run_session_hook(root, {"event_id": event_id}, OMC_TEST_FAIL_ONCE="state_init")

    assert first.stdout == ""
    assert "# fresh common summary" in second.stdout


def test_same_event_retries_after_session_hook_failure(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    event_id = f"event-{uuid.uuid4()}"

    first = _run_session_hook(root, {"event_id": event_id}, OMC_TEST_FAIL_ONCE="session_hook")
    second = _run_session_hook(root, {"event_id": event_id}, OMC_TEST_FAIL_ONCE="session_hook")

    assert first.stdout == ""
    assert "# fresh common summary" in second.stdout


def test_concurrent_event_takes_over_after_owner_failure(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    event_id = f"event-{uuid.uuid4()}"
    env = {
        **os.environ,
        "OMC_TEST_FAIL_ONCE": "state_init",
        "OMC_TEST_FAILURE_DELAY": "0.5",
    }
    first = subprocess.Popen(
        ["bash", str(SESSION_HOOK), "codex"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert first.stdin is not None
    first.stdin.write(json.dumps({"event_id": event_id}))
    first.stdin.close()
    failure_marker = root / ".omc" / "failed-state_init"
    deadline = time.monotonic() + 2
    while not failure_marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert failure_marker.exists()

    second = _run_session_hook(root, {"event_id": event_id})
    first.wait(timeout=5)

    assert second.stdout != ""
    assert "# fresh common summary" in second.stdout
    calls = (root / ".omc" / "calls.log").read_text(encoding="utf-8").splitlines()
    assert sum("hook session_start" in call for call in calls) == 1


def test_same_event_retries_after_summary_output_failure(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    event_id = f"event-{uuid.uuid4()}"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    cat_shim = shim_dir / "cat"
    cat_shim.write_text(
        """#!/usr/bin/env bash
if [[ "${1:-}" == */.omc/summary.md ]]; then
  exit 1
fi
exec /bin/cat "$@"
""",
        encoding="utf-8",
    )
    cat_shim.chmod(0o755)

    first = _run_session_hook(
        root,
        {"event_id": event_id},
        PATH=f"{shim_dir}:{os.environ['PATH']}",
    )
    second = _run_session_hook(root, {"event_id": event_id})

    assert "# fresh common summary" not in first.stdout
    assert "# fresh common summary" in second.stdout


def test_diagnostics_store_shape_and_hash_but_not_values(tmp_path: Path) -> None:
    root = _session_root(tmp_path)
    diagnostics = tmp_path / "hook-diagnostics.jsonl"
    secret_value = "must-not-be-persisted"

    _run_session_hook(
        root,
        {"event_id": f"event-{uuid.uuid4()}", "secret": secret_value},
        OMC_HOOK_DIAGNOSTICS="1",
        OMC_HOOK_DIAGNOSTICS_FILE=str(diagnostics),
    )

    raw = diagnostics.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert secret_value not in raw
    assert record["fields"] == ["event_id", "secret"]
    assert record["payload_bytes"] > 0
    assert len(record["sha256"]) == 64
