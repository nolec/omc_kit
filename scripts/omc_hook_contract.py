#!/usr/bin/env python3
"""Shared executable OMC hook contract helpers."""
from __future__ import annotations

HOOK_CONTRACT_MARKERS = (
    "session bootstrap",
    "pre-mutate guard",
    "post-mutate soft guard",
    "install/doctor verification",
)

HOOK_CONTRACT_SUMMARY = " / ".join(HOOK_CONTRACT_MARKERS)

CLAUDE_HOOK_CONTRACT = {
    "session_context": {
        "label": ".claude/settings.json (SessionStart + UserPromptSubmit hook)",
        "issue": "session bootstrap",
        "required_hooks": ("SessionStart", "UserPromptSubmit"),
        "commands": (
            ".agent-hooks/omc-session-start.sh claude",
            ".agent-hooks/omc-prompt-inject.sh",
        ),
    },
    "pre_mutate_guard": {
        "label": ".claude/settings.json (PreToolUse hook)",
        "issue": "pre-mutate guard",
        "required_hooks": ("PreToolUse",),
        "matcher": "Write|Edit|MultiEdit",
        "command": ".agent-hooks/omc-pipeline-check.sh",
    },
}

GEMINI_HOOK_CONTRACT = {
    "session_context": {
        "label": ".gemini/settings.json (SessionStart + BeforeAgent hook)",
        "issue": "session bootstrap",
        "required_hooks": ("SessionStart", "BeforeAgent"),
        "commands": (
            ".agent-hooks/omc-session-start.sh gemini",
            ".agent-hooks/omc-before-agent.sh",
        ),
    },
    "pre_mutate_guard": {
        "label": ".gemini/settings.json (BeforeTool hook)",
        "issue": "pre-mutate guard",
        "required_hooks": ("BeforeTool",),
        "matcher": "write_file|replace",
        "command": ".agent-hooks/omc-pipeline-check.sh",
    },
}

CODEX_HOOK_CONTRACT = {
    "session_context": {
        "label": ".codex/hooks.json (SessionStart + UserPromptSubmit hook)",
        "issue": "session bootstrap",
        "required_hooks": ("SessionStart", "UserPromptSubmit"),
        "commands": (
            ".agent-hooks/omc-session-start.sh codex",
            ".agent-hooks/omc-prompt-inject.sh",
        ),
    },
    "pre_mutate_guard": {
        "label": ".codex/hooks.json (PreToolUse hook)",
        "issue": "pre-mutate guard",
        "required_hooks": ("PreToolUse",),
        "matcher": "Bash|apply_patch|Write",
        "command": ".agent-hooks/omc-pipeline-check.sh",
    },
    "post_mutate_soft_guard": {
        "label": ".codex/hooks.json (PostToolUse soft guard)",
        "issue": "post-mutate soft guard",
        "required_hooks": ("PostToolUse",),
        "matcher": "apply_patch|Write",
        "command": ".agent-hooks/omc-post-file-check.sh",
    },
}

def _platform_hook_contracts() -> dict[str, dict]:
    return {
        "claude": CLAUDE_HOOK_CONTRACT,
        "gemini": GEMINI_HOOK_CONTRACT,
        "codex": CODEX_HOOK_CONTRACT,
    }


def _hook_commands(entries: list[dict]) -> list[str]:
    return [
        hook.get("command", "")
        for entry in entries
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    ]


def _matcher_tokens(matcher: str) -> set[str]:
    return {token.strip() for token in matcher.split("|") if token.strip()}


def _required_hook_entries(data: dict, platform: str, contract_key: str) -> dict[str, list[dict]] | None:
    hooks = data.get("hooks", {})
    required_hooks = _platform_hook_contracts()[platform][contract_key]["required_hooks"]
    entries_by_name: dict[str, list[dict]] = {}
    for hook_name in required_hooks:
        entries = hooks.get(hook_name)
        if not isinstance(entries, list):
            return None
        entries_by_name[hook_name] = entries
    return entries_by_name


def _has_session_context_hooks(data: dict, platform: str) -> bool:
    required_entries = _required_hook_entries(data, platform, "session_context")
    if required_entries is None:
        return False

    contract = _platform_hook_contracts()[platform]["session_context"]
    first_hook_name, second_hook_name = contract["required_hooks"]
    first_commands = _hook_commands(required_entries[first_hook_name])
    second_commands = _hook_commands(required_entries[second_hook_name])
    first_expected, second_expected = contract["commands"]
    return any(first_expected in command for command in first_commands) and any(
        second_expected in command for command in second_commands
    )


def _has_pre_mutate_guard(data: dict, platform: str) -> bool:
    required_entries = _required_hook_entries(data, platform, "pre_mutate_guard")
    if required_entries is None:
        return False

    contract = _platform_hook_contracts()[platform]["pre_mutate_guard"]
    hook_name = contract["required_hooks"][0]
    entries = required_entries[hook_name]
    commands = _hook_commands(entries)
    command_ok = any(contract["command"] in command for command in commands)
    matcher_tokens = _matcher_tokens(contract["matcher"])
    matcher_ok = any(_matcher_tokens(entry.get("matcher", "")) == matcher_tokens for entry in entries)
    return command_ok and matcher_ok


def _has_post_mutate_soft_guard(data: dict, platform: str) -> bool:
    required_entries = _required_hook_entries(data, platform, "post_mutate_soft_guard")
    if required_entries is None:
        return False

    contract = _platform_hook_contracts()[platform]["post_mutate_soft_guard"]
    hook_name = contract["required_hooks"][0]
    entries = required_entries[hook_name]
    commands = _hook_commands(entries)
    command_ok = any(
        contract["command"] in command or "omc-post-file-check.sh" in command
        for command in commands
    )
    matcher_tokens = _matcher_tokens(contract["matcher"])
    matcher_ok = any(_matcher_tokens(entry.get("matcher", "")) == matcher_tokens for entry in entries)
    return command_ok and matcher_ok


def evaluate_hook_contract(data: dict, platform: str, contract_key: str) -> bool:
    evaluators = {
        "session_context": _has_session_context_hooks,
        "pre_mutate_guard": _has_pre_mutate_guard,
        "post_mutate_soft_guard": _has_post_mutate_soft_guard,
    }
    evaluator = evaluators.get(contract_key)
    if evaluator is None:
        raise KeyError(f"Unknown contract key: {contract_key}")
    return evaluator(data, platform)


def contract_issues(data: dict, platform: str) -> list[str]:
    issues: list[str] = []
    for contract_key, contract in _platform_hook_contracts()[platform].items():
        if not evaluate_hook_contract(data, platform, contract_key):
            issues.append(contract["issue"])
    return issues


def claude_has_session_context_hooks(data: dict) -> bool:
    return evaluate_hook_contract(data, "claude", "session_context")


def claude_has_pre_mutate_guard(data: dict) -> bool:
    return evaluate_hook_contract(data, "claude", "pre_mutate_guard")


def gemini_has_session_context_hooks(data: dict) -> bool:
    return evaluate_hook_contract(data, "gemini", "session_context")


def gemini_has_pre_mutate_guard(data: dict) -> bool:
    return evaluate_hook_contract(data, "gemini", "pre_mutate_guard")


def codex_has_session_context_hooks(data: dict) -> bool:
    return evaluate_hook_contract(data, "codex", "session_context")


def codex_has_pre_mutate_guard(data: dict) -> bool:
    return evaluate_hook_contract(data, "codex", "pre_mutate_guard")


def codex_has_posttooluse_soft_guard(data: dict) -> bool:
    return evaluate_hook_contract(data, "codex", "post_mutate_soft_guard")


def claude_contract_issues(data: dict) -> list[str]:
    return contract_issues(data, "claude")


def gemini_contract_issues(data: dict) -> list[str]:
    return contract_issues(data, "gemini")


def codex_contract_issues(data: dict) -> list[str]:
    return contract_issues(data, "codex")
