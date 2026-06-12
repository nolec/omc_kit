from __future__ import annotations

import subprocess
from pathlib import Path

import omc_autopilot


def test_run_step_uses_mini_high_for_plan(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)

    rc, _out = omc_autopilot._run_step(
        tmp_path,
        {"id": "plan", "prompt": "아키텍처 계획"},
        executor="codex",
        timeout_sec=5,
    )

    assert rc == 0
    cmd = captured["cmd"]
    assert "--model-profile" in cmd
    assert "mini_high" in cmd


def test_run_step_uses_mini_default_for_task(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)

    rc, _out = omc_autopilot._run_step(
        tmp_path,
        {"id": "task", "prompt": "버튼 구현"},
        executor="codex",
        timeout_sec=5,
    )

    assert rc == 0
    cmd = captured["cmd"]
    assert "--model-profile" in cmd
    assert "mini_default" in cmd
