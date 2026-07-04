from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_observed_collect_batch


def test_default_task_sequence_repeats_profile_in_order() -> None:
    task_ids = omc_observed_collect_batch.expand_task_ids(profile="bootstrap", repeats=2)
    assert task_ids == [
        "observed-collect",
        "observed-collect-reverse",
        "observed-ready-surface",
        "observed-collect",
        "observed-collect-reverse",
        "observed-ready-surface",
    ]


def test_custom_task_ids_override_profile() -> None:
    task_ids = omc_observed_collect_batch.expand_task_ids(
        profile="bootstrap",
        repeats=3,
        task_ids=["a", "b"],
    )
    assert task_ids == ["a", "b"]


def test_scale_profile_skips_bootstrap_observed_collect() -> None:
    task_ids = omc_observed_collect_batch.expand_task_ids(profile="scale", repeats=2)
    assert task_ids == [
        "observed-collect-reverse",
        "observed-ready-surface",
        "observed-collect-reverse",
        "observed-ready-surface",
    ]


def test_run_plan_dry_run_prints_order(capsys) -> None:
    code = omc_observed_collect_batch.run_plan(
        root=Path("/tmp/project"),
        task_ids=["observed-collect", "observed-ready-surface"],
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "1. observed-collect" in out
    assert "2. observed-ready-surface" in out


def test_prepare_task_path_creates_unique_runtime_copy(tmp_path: Path) -> None:
    tasks_dir = tmp_path / ".omc" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "observed-collect.json").write_text(
        json.dumps({"id": "observed-collect", "title": "Observed Run Collection"}),
        encoding="utf-8",
    )

    task_path = omc_observed_collect_batch.prepare_task_path(
        root=tmp_path,
        task_id="observed-collect",
        ordinal=3,
        unique_runtime_task=True,
    )

    saved = json.loads(task_path.read_text(encoding="utf-8"))
    assert saved["id"] == "observed-collect-batch-003"
    assert saved["title"] == "Observed Run Collection [batch 3]"


def test_run_plan_executes_all_tasks_until_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    tasks_dir = tmp_path / ".omc" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task_id in ("observed-collect", "observed-collect-reverse"):
        (tasks_dir / f"{task_id}.json").write_text("{}", encoding="utf-8")

    def _fake_run(cmd: list[str], **_: object):
        calls.append(cmd)
        class _Result:
            returncode = 0
        return _Result()

    with patch.object(omc_observed_collect_batch.subprocess, "run", side_effect=_fake_run):
        code = omc_observed_collect_batch.run_plan(
            root=tmp_path,
            task_ids=["observed-collect", "observed-collect-reverse"],
            dry_run=False,
            unique_runtime_tasks=False,
        )

    assert code == 0
    assert calls == [
        [
            sys.executable,
            "scripts/omc_autopilot.py",
            "run",
            "--task",
            str(tmp_path / ".omc" / "tasks" / "observed-collect.json"),
        ],
        [
            sys.executable,
            "scripts/omc_autopilot.py",
            "run",
            "--task",
            str(tmp_path / ".omc" / "tasks" / "observed-collect-reverse.json"),
        ],
    ]


def test_run_plan_stops_on_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    tasks_dir = tmp_path / ".omc" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task_id in ("observed-collect", "observed-collect-reverse", "observed-ready-surface"):
        (tasks_dir / f"{task_id}.json").write_text("{}", encoding="utf-8")

    def _fake_run(cmd: list[str], **_: object):
        calls.append(cmd)
        class _Result:
            def __init__(self, returncode: int):
                self.returncode = returncode
        return _Result(1 if len(calls) == 2 else 0)

    with patch.object(omc_observed_collect_batch.subprocess, "run", side_effect=_fake_run):
        code = omc_observed_collect_batch.run_plan(
            root=tmp_path,
            task_ids=["observed-collect", "observed-collect-reverse", "observed-ready-surface"],
            dry_run=False,
            unique_runtime_tasks=False,
        )

    assert code == 1
    assert len(calls) == 2
