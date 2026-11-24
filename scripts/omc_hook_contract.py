#!/usr/bin/env python3
"""Shared executable OMC hook contract helpers."""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Literal

HOOK_CONTRACT_MARKERS = (
    "session bootstrap",
    "pre-mutate guard",
    "post-mutate soft guard",
    "install/doctor verification",
)

HOOK_CONTRACT_SUMMARY = " / ".join(HOOK_CONTRACT_MARKERS)



CODEX_HOOK_CONTRACT = {
    "session_context": {
        "label": ".codex/hooks.json (SessionStart + UserPromptSubmit hook)",
        "issue": "session bootstrap",
        "required_hooks": ("SessionStart", "UserPromptSubmit"),
        "commands": (
            ".agent-hooks/omc-session-start.sh codex",
            ".agent-hooks/omc-prompt-inject.sh codex",
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










def codex_has_session_context_hooks(data: dict) -> bool:
    return evaluate_hook_contract(data, "codex", "session_context")


def codex_has_pre_mutate_guard(data: dict) -> bool:
    return evaluate_hook_contract(data, "codex", "pre_mutate_guard")


def codex_has_posttooluse_soft_guard(data: dict) -> bool:
    return evaluate_hook_contract(data, "codex", "post_mutate_soft_guard")






def codex_contract_issues(data: dict) -> list[str]:
    return contract_issues(data, "codex")


def codex_hooks_disabled(data: dict) -> bool:
    """Validate an explicit disabled event map; malformed or executable hooks fail."""
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return False
    hooks = data["hooks"]
    required = {"SessionStart", "PreToolUse", "UserPromptSubmit", "PostToolUse"}
    return required.issubset(hooks) and all(isinstance(v, list) and not v for v in hooks.values())


_WIP_SCRIPT = "omc_wip_notice.py"
_LEGACY_CODEX_COMMANDS = (
    "omc-session-start.sh",
    "omc-prompt-inject.sh",
    "omc-pipeline-check.sh",
    "omc-post-file-check.sh",
    "omc-session-end.sh",
    "omc-before-agent.sh",
    "omc-hub-push.sh",
)


def _notice_group(root: Path) -> dict:
    script = root / "scripts" / _WIP_SCRIPT
    return {
        "matcher": "startup",
        "hooks": [{"type": "command", "command": f"python3 -B {shlex.quote(str(script))}", "timeout": 8}],
    }


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key in Codex hooks: {key}")
        obj[key] = value
    return obj


def _read_project_hooks(root: Path) -> tuple[dict, int | None]:
    path = root / ".codex/hooks.json"
    if (path.parent.is_symlink() or (path.parent.exists() and not path.parent.is_dir())
            or path.is_symlink()):
        raise ValueError("Codex hook target must be a regular local file")
    if not path.exists():
        return {"hooks": {}}, None
    if not path.is_file():
        raise ValueError("Codex hook target must be a regular local file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError(f"Cannot read Codex hook configuration: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise ValueError("Codex hooks must have a hooks object")
    return data, _find_notice_group(data, root)


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_handler_options(handler: dict, event: str) -> None:
    for key in ("timeout", "additionalContextLimit", "additional_context_limit"):
        value = handler.get(key)
        if value is not None and (type(value) is not int or value < 0 or value >= 2**64):
            raise ValueError(f"Invalid Codex hook {key} for {event}")
    for key in ("statusMessage", "status_message"):
        value = handler.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Invalid Codex hook {key} for {event}")
    if "async" in handler and type(handler["async"]) is not bool:
        raise ValueError(f"Invalid Codex hook async for {event}")


def _find_notice_group(data: dict, root: Path) -> int | None:
    expected = _notice_group(root)
    notice_index = None
    for event, groups in data["hooks"].items():
        if not isinstance(groups, list):
            raise ValueError(f"Invalid Codex hook groups for {event}")
        for index, group in enumerate(groups):
            if (not isinstance(group, dict) or not isinstance(group.get("hooks"), list)
                    or not isinstance(group.get("matcher", ""), str)):
                raise ValueError(f"Invalid Codex hook group for {event}")
            if event == "SessionStart" and group == expected:
                if notice_index is not None:
                    raise ValueError("Duplicate OMC WIP notice hooks")
                notice_index = index
                continue
            for handler in group["hooks"]:
                if not isinstance(handler, dict):
                    raise ValueError(f"Invalid Codex hook handler for {event}")
                _validate_handler_options(handler, event)
                kind = handler.get("type")
                if kind == "command":
                    commands = [handler.get("command")]
                    commands.extend(handler[key] for key in ("commandWindows", "command_windows") if key in handler)
                    if not all(_nonempty_text(command) for command in commands):
                        raise ValueError(f"Invalid Codex hook command for {event}")
                    if any(marker in command for command in commands
                           for marker in (_WIP_SCRIPT, *_LEGACY_CODEX_COMMANDS)):
                        raise ValueError("Legacy or unrecognized OMC Codex hook")
                elif kind == "mcp_tool":
                    if not all(_nonempty_text(handler.get(key)) for key in ("server", "tool")):
                        raise ValueError(f"Invalid Codex MCP hook for {event}")
                    if "input" in handler and not isinstance(handler["input"], dict):
                        raise ValueError(f"Invalid Codex MCP hook input for {event}")
                elif kind in ("prompt", "agent"):
                    if not _nonempty_text(handler.get("prompt")):
                        raise ValueError(f"Invalid Codex hook prompt for {event}")
                else:
                    raise ValueError(f"Unknown Codex hook handler for {event}")
    return notice_index


def inspect_wip_notice_hooks(root: Path) -> Literal["disabled", "enabled"]:
    _, index = _read_project_hooks(root)
    return "enabled" if index is not None else "disabled"


def configure_wip_notice_hooks(
    root: Path, *, enabled: bool | None, default: dict | None = None
) -> None:
    """Edit only the exact OMC group and serialize cooperating writers."""
    path = root / ".codex/hooks.json"
    path.parent.mkdir(exist_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        data, index = _read_project_hooks(root)
        missing = not path.exists()
        if missing and default is not None:
            data = default
        if enabled is True and index is None:
            data["hooks"].setdefault("SessionStart", []).append(_notice_group(root))
        elif enabled is False and index is not None:
            del data["hooks"]["SessionStart"][index]
        elif not (missing and default is not None):
            return
        _replace_project_hooks(path, data)
    finally:
        os.close(directory)


def _replace_project_hooks(path: Path, data: dict) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
