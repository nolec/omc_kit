from __future__ import annotations

import omc_exec


def test_adapt_prompt_includes_actual_gemini_tool_names() -> None:
    result = omc_exec._adapt_prompt_for_executor("작업해줘", executor="gemini")
    for tool in ("glob", "grep_search", "run_shell_command", "read_file", "write_file", "replace"):
        assert tool in result, f"실제 도구 이름 '{tool}'이 adapter에 없음"


def test_adapt_prompt_excludes_legacy_tool_names() -> None:
    result = omc_exec._adapt_prompt_for_executor("작업해줘", executor="gemini")
    for stale in ("invoke_agent",):
        assert stale not in result, f"구 도구 이름 '{stale}'이 adapter에 남아 있음"


def test_adapt_prompt_noop_for_non_gemini() -> None:
    prompt = "작업해줘"
    assert omc_exec._adapt_prompt_for_executor(prompt, executor="codex") == prompt
    assert omc_exec._adapt_prompt_for_executor(prompt, executor="claude") == prompt
