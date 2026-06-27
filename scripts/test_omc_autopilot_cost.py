from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
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


def test_run_step_uses_raw_output_sidecar_for_token_parsing(monkeypatch, tmp_path: Path) -> None:
    raw_payload = json.dumps(
        {
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
                "input_tokens_details": {"cached_tokens": 10},
            }
        }
    )

    def fake_run(cmd, **kwargs):
        env = kwargs.get("env") or {}
        raw_output_file = env.get("OMC_RAW_OUTPUT_FILE")
        assert raw_output_file, "raw output sidecar path should be passed via env"
        Path(raw_output_file).write_text(raw_payload, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="사람 친화 텍스트 응답", stderr="")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)

    step = {"id": "review", "prompt": "리뷰"}
    rc, _output, cost_info = omc_autopilot._run_step(tmp_path, step, executor="codex", timeout_sec=30)

    assert rc == 0
    assert cost_info is not None
    assert cost_info["token_usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_read_tokens": 10,
        "cache_write_tokens": 0,
    }


def test_run_step_uses_codex_jsonl_sidecar_for_token_parsing(monkeypatch, tmp_path: Path) -> None:
    raw_payload = "\n".join(
        [
            json.dumps({"type": "response.started", "response": {"id": "resp_123"}}),
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "usage": {
                            "input_tokens": 55,
                            "output_tokens": 14,
                            "input_tokens_details": {"cached_tokens": 9},
                        }
                    },
                }
            ),
        ]
    )

    def fake_run(cmd, **kwargs):
        env = kwargs.get("env") or {}
        raw_output_file = env.get("OMC_RAW_OUTPUT_FILE")
        assert raw_output_file, "raw output sidecar path should be passed via env"
        Path(raw_output_file).write_text(raw_payload, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="human friendly summary", stderr="")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)

    step = {"id": "review", "prompt": "리뷰"}
    rc, _output, cost_info = omc_autopilot._run_step(tmp_path, step, executor="codex", timeout_sec=30)

    assert rc == 0
    assert cost_info is not None
    assert cost_info["token_usage"] == {
        "input_tokens": 55,
        "output_tokens": 14,
        "cache_read_tokens": 9,
        "cache_write_tokens": 0,
    }


def test_run_step_passes_task_kind_to_omc_exec(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        env = kwargs.get("env") or {}
        raw_output_file = env.get("OMC_RAW_OUTPUT_FILE")
        assert raw_output_file
        Path(raw_output_file).write_text(
            json.dumps({"usage": {"input_tokens": 1, "output_tokens": 1}}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(omc_autopilot.subprocess, "run", fake_run)

    step = {"id": "review", "prompt": "리뷰"}
    rc, _output, _cost_info = omc_autopilot._run_step(tmp_path, step, executor="codex", timeout_sec=30)

    assert rc == 0
    cmd = captured["cmd"]
    assert "--task-kind" in cmd
    assert cmd[cmd.index("--task-kind") + 1] == "review"


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


def test_omc_cost_record_flattens_token_fields(tmp_path: Path) -> None:
    llm_json = json.dumps(
        {
            "usage": {
                "input_tokens": 90,
                "output_tokens": 12,
                "input_tokens_details": {"cached_tokens": 8},
            }
        }
    )

    entry = omc_cost.record(
        tmp_path,
        executor="codex",
        task_title="token record",
        llm_json=llm_json,
        model="gpt-4o-mini",
    )

    assert entry["tokens"] == {
        "input_tokens": 90,
        "output_tokens": 12,
        "cache_read_tokens": 8,
        "cache_write_tokens": 0,
    }
    assert entry["input_tokens"] == 90
    assert entry["output_tokens"] == 12
    assert entry["cache_read_tokens"] == 8
    assert entry["cache_write_tokens"] == 0
    assert "estimated_usd" not in entry
    assert "cost_usd" not in entry


def test_omc_cost_record_flattens_gemini_success_usage_metadata(tmp_path: Path) -> None:
    llm_json = json.dumps(
        {
            "usageMetadata": {
                "promptTokenCount": 44,
                "candidatesTokenCount": 7,
                "totalTokenCount": 51,
            }
        }
    )

    entry = omc_cost.record(
        tmp_path,
        executor="gemini",
        task_title="gemini token record",
        llm_json=llm_json,
        model="gemini-2.5-flash",
    )

    assert entry["tokens"] == {
        "input_tokens": 44,
        "output_tokens": 7,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert entry["input_tokens"] == 44
    assert entry["output_tokens"] == 7
    assert entry["cache_read_tokens"] == 0
    assert entry["cache_write_tokens"] == 0
    assert entry["estimated_usd"] == 0.000031
    assert entry["cost_usd"] == 0.000031


def test_estimate_cost_usd_supports_claude_alias_with_cache_tokens() -> None:
    usage = {
        "input_tokens": 9,
        "output_tokens": 161,
        "cache_read_tokens": 0,
        "cache_write_tokens": 21696,
    }

    cost = omc_cost._estimate_cost_usd(usage, "sonnet")

    assert cost == 0.083802


def test_omc_cost_record_prefers_claude_actual_cost_when_available(tmp_path: Path) -> None:
    llm_json = json.dumps(
        {
            "total_cost_usd": 0.083802,
            "usage": {
                "input_tokens": 9,
                "output_tokens": 161,
                "cache_creation_input_tokens": 21696,
                "cache_read_input_tokens": 0,
            },
        }
    )

    entry = omc_cost.record(
        tmp_path,
        executor="claude",
        task_title="claude token record",
        llm_json=llm_json,
        model="sonnet",
    )

    assert entry["estimated_usd"] == 0.083802
    assert entry["cost_usd"] == 0.083802


def test_parse_openai_usage_supports_codex_jsonl_response_events() -> None:
    jsonl = "\n".join(
        [
            json.dumps({"type": "response.started", "response": {"id": "resp_123"}}),
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "usage": {
                            "input_tokens": 120,
                            "output_tokens": 30,
                            "input_tokens_details": {"cached_tokens": 11},
                        }
                    },
                }
            ),
        ]
    )

    usage = omc_cost._parse_openai_usage(jsonl)

    assert usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_read_tokens": 11,
        "cache_write_tokens": 0,
    }


def test_extract_cost_info_leaves_codex_cost_unknown_without_explicit_pricing() -> None:
    payload = json.dumps(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "input_tokens_details": {"cached_tokens": 0},
            }
        }
    )

    mini = omc_autopilot._extract_cost_info("codex", payload, model_profile="mini_default")
    full = omc_autopilot._extract_cost_info("codex", payload, model_profile="full_default")

    assert mini is not None
    assert full is not None
    assert mini["token_usage"] == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert full["token_usage"] == mini["token_usage"]
    assert mini["cost_estimate"] is None
    assert full["cost_estimate"] is None


def test_extract_cost_info_skips_codex_cost_even_with_priced_model_name() -> None:
    payload = json.dumps(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "input_tokens_details": {"cached_tokens": 0},
            }
        }
    )

    cost_info = omc_autopilot._extract_cost_info("codex", payload, model_profile="mini_default")

    assert cost_info is not None
    assert cost_info["token_usage"] == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert cost_info["cost_estimate"] is None


def test_omc_cost_record_leaves_cost_unknown_without_explicit_pricing(tmp_path: Path) -> None:
    llm_json = json.dumps(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "input_tokens_details": {"cached_tokens": 0},
            }
        }
    )

    entry = omc_cost.record(
        tmp_path,
        executor="codex",
        task_title="codex token record",
        llm_json=llm_json,
        model="gpt-5.4-mini",
    )

    assert entry["tokens"]["input_tokens"] == 1000
    assert "estimated_usd" not in entry
    assert "cost_usd" not in entry


def test_omc_cost_report_marks_unknown_cost_as_na(tmp_path: Path) -> None:
    log_path = tmp_path / ".omc" / "cost_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-25T15:17:26.794102+00:00",
                        "executor": "codex",
                        "model": "gpt-5.4-mini",
                        "task": "codex probe",
                        "git": {"files_changed": 1, "insertions": 10, "deletions": 1},
                        "size": "small",
                        "tokens": {
                            "input_tokens": 1000,
                            "output_tokens": 500,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                        },
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        omc_cost.report(tmp_path, last_n=5)

    output = buf.getvalue()
    assert "codex" in output
    assert "N/A" in output
