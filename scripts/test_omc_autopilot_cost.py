from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import omc_autopilot
import omc_cost


# ── 태스크 1: _run_step이 3-tuple (rc, output, cost_info) 반환 ──────────────

def test_run_step_returns_three_tuple(monkeypatch, tmp_path: Path) -> None:
    gemini_stats = json.dumps({
        "response": "2",
        "stats": {
            "models": {
                "gemini-2.5-flash": {
                    "tokens": {
                        "prompt": 100,
                        "candidates": 20,
                    }
                }
            }
        }
    })

    monkeypatch.setattr(
        omc_autopilot.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout=gemini_stats, stderr=""
        ),
    )

    step = {"id": "task", "prompt": "1+1은?"}
    result = omc_autopilot._run_step(tmp_path, step, executor="gemini", timeout_sec=30)
    assert len(result) == 3
    rc, output, cost_info = result
    assert rc == 0
    assert cost_info is not None
    assert cost_info["token_usage"] is not None


# ── 태스크 2: 파싱 실패 시 cost_info가 None으로 silent fallback ─────────────

def test_run_step_cost_info_none_on_unparseable_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        omc_autopilot.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout="plain text output", stderr=""
        ),
    )

    step = {"id": "task", "prompt": "작업"}
    result = omc_autopilot._run_step(tmp_path, step, executor="gemini", timeout_sec=30)
    assert len(result) == 3
    _, _, cost_info = result
    assert cost_info is None


def test_run_step_cost_info_none_on_empty_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        omc_autopilot.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout="", stderr=""
        ),
    )

    step = {"id": "task", "prompt": "작업"}
    result = omc_autopilot._run_step(tmp_path, step, executor="codex", timeout_sec=30)
    assert len(result) == 3
    _, _, cost_info = result
    assert cost_info is None


# ── 태스크 3: cmd_run 호출부가 token_usage/cost_estimate를 step state에 저장 ─

def test_cmd_run_stores_cost_in_step_state(monkeypatch, tmp_path: Path) -> None:
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps({
        "id": "test-cost",
        "title": "비용 추적 테스트",
        "executor": "gemini",
        "steps": [{"id": "s1", "prompt": "1+1"}],
    }), encoding="utf-8")

    fake_cost_info = {
        "token_usage": {"input_tokens": 50, "output_tokens": 10,
                        "cache_read_tokens": 0, "cache_write_tokens": 0},
        "cost_estimate": 0.000195,
    }

    monkeypatch.setattr(
        omc_autopilot,
        "_run_step",
        lambda *a, **kw: (0, "완료", fake_cost_info),
    )

    rc = omc_autopilot.cmd_run(tmp_path, task_file)
    assert rc == 0

    state = omc_autopilot._load_state(tmp_path, "test-cost")
    steps = state.get("steps", {})
    assert "s1" in steps
    s1 = steps["s1"]
    assert s1.get("token_usage") == fake_cost_info["token_usage"]
    assert s1.get("cost_estimate") == fake_cost_info["cost_estimate"]


# ── 태스크 4: _build_benchmark_report에 총 cost/token 집계 ──────────────────

def test_benchmark_report_aggregates_cost() -> None:
    data = {
        "run_id": "r1",
        "status": "completed",
        "executor": "gemini",
        "branch": "feat/x",
        "started_at": "2026-06-12T10:00:00Z",
        "finished_at": "2026-06-12T10:05:00Z",
        "steps": {
            "s1": {
                "status": "completed",
                "started_at": "2026-06-12T10:00:00Z",
                "finished_at": "2026-06-12T10:02:00Z",
                "token_usage": {"input_tokens": 100, "output_tokens": 20,
                                "cache_read_tokens": 0, "cache_write_tokens": 0},
                "cost_estimate": 0.0006,
            },
            "s2": {
                "status": "completed",
                "started_at": "2026-06-12T10:02:00Z",
                "finished_at": "2026-06-12T10:05:00Z",
                "token_usage": {"input_tokens": 200, "output_tokens": 40,
                                "cache_read_tokens": 0, "cache_write_tokens": 0},
                "cost_estimate": 0.0012,
            },
        },
    }

    report = omc_autopilot._build_benchmark_report(data)
    assert "total_cost_usd" in report
    assert abs(report["total_cost_usd"] - 0.0018) < 1e-9
    assert "total_tokens" in report
    assert report["total_tokens"] == 360
