from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ADAPTER = Path(__file__).with_name("omc_codex_subscription_adapter.py")


def _write_codex(
    path: Path,
    *,
    logged_in: bool = True,
    output: str = "subscription-ok",
    sleep_sec: float = 0,
) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if sys.argv[1:3] == ['login', 'status']:\n"
        f"    print({'Logged in using ChatGPT' if logged_in else 'Not logged in'!r})\n"
        f"    raise SystemExit({0 if logged_in else 1})\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "assert 'CODEX_API_KEY' not in os.environ\n"
        "assert '--json' in sys.argv and '--ephemeral' in sys.argv\n"
        "assert sys.argv[-1] == '-'\n"
        "assert sys.stdin.read() == 'Return one line.'\n"
        f"import time; time.sleep({sleep_sec!r})\n"
        "print(json.dumps({'type': 'item.completed', 'item': "
        f"{{'type': 'agent_message', 'text': {output!r}}}}}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': "
        "{'input_tokens': 11, 'cached_input_tokens': 3, "
        "'output_tokens': 5, 'reasoning_output_tokens': 2}}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run(
    codex: Path,
    action: str,
    *,
    request: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "OMC_CODEX_BINARY": str(codex),
        "OPENAI_API_KEY": "must-be-removed",
        "CODEX_API_KEY": "must-be-removed",
    }
    return subprocess.run(
        [sys.executable, str(ADAPTER), action],
        input="" if request is None else json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_subscription_adapter_advertises_observed_usage_without_hard_token_claim(
    tmp_path: Path,
) -> None:
    codex = tmp_path / "codex"
    _write_codex(codex)

    completed = _run(codex, "capabilities")

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "authentication": "chatgpt_subscription",
        "execution_profile": "subscription_bounded",
        "hard_bounds": ["elapsed_time", "output_chars", "process_group"],
        "hard_output_limit": True,
        "hard_total_token_limit": False,
        "protocol": "omc-provider/v1",
        "token_usage_mode": "observed_post_call",
    }


def test_subscription_adapter_executes_without_api_keys_and_parses_usage(
    tmp_path: Path,
) -> None:
    codex = tmp_path / "codex"
    _write_codex(codex)
    request = {
        "protocol": "omc-provider/v1",
        "executor": "codex",
        "prompt": "Return one line.",
        "project_root": str(tmp_path),
        "timeout_sec": 5,
        "max_total_tokens": 100,
        "max_output_chars": 100,
    }

    completed = _run(codex, "execute", request=request)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "returncode": 0,
        "output": "subscription-ok",
        "token_usage": {
            "input_tokens": 11,
            "output_tokens": 5,
            "total_tokens": 16,
        },
        "token_usage_mode": "observed_post_call",
    }


def test_subscription_adapter_blocks_when_chatgpt_login_is_unavailable(
    tmp_path: Path,
) -> None:
    codex = tmp_path / "codex"
    _write_codex(codex, logged_in=False)

    completed = _run(codex, "capabilities")

    assert completed.returncode != 0
    assert json.loads(completed.stdout)["reason_code"] == (
        "chatgpt_subscription_auth_unavailable"
    )


def test_subscription_adapter_enforces_elapsed_time_bound(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    _write_codex(codex, sleep_sec=1)
    request = {
        "protocol": "omc-provider/v1",
        "executor": "codex",
        "prompt": "Return one line.",
        "project_root": str(tmp_path),
        "timeout_sec": 0.05,
        "max_total_tokens": 100,
        "max_output_chars": 100,
    }

    completed = _run(codex, "execute", request=request)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["returncode"] == 124
    assert result["reason_code"] == "backend_timeout"


def test_subscription_adapter_enforces_output_character_bound(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    _write_codex(codex, output="x" * 101)
    request = {
        "protocol": "omc-provider/v1",
        "executor": "codex",
        "prompt": "Return one line.",
        "project_root": str(tmp_path),
        "timeout_sec": 5,
        "max_total_tokens": 100,
        "max_output_chars": 100,
    }

    completed = _run(codex, "execute", request=request)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["returncode"] == 65
    assert result["reason_code"] == "backend_output_limit_violated"
