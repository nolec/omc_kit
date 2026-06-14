"""omc_hook_contract.py contract tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import omc_hook_contract as contract


def _load_template_hooks() -> dict:
    path = ROOT / "templates" / ".codex" / "hooks.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_template_json(path_str: str) -> dict:
    path = ROOT / path_str
    return json.loads(path.read_text(encoding="utf-8"))


def test_codex_template_satisfies_shared_contract():
    data = _load_template_hooks()
    assert contract.codex_has_session_context_hooks(data)
    assert contract.codex_has_posttooluse_soft_guard(data)
    assert contract.codex_contract_issues(data) == []


def test_claude_template_satisfies_shared_contract():
    data = _load_template_json("templates/.claude/settings.json")
    assert contract.claude_has_session_context_hooks(data)
    assert contract.claude_has_pre_mutate_guard(data)


def test_gemini_template_satisfies_shared_contract():
    data = _load_template_json("templates/.gemini/settings.json")
    assert contract.gemini_has_session_context_hooks(data)
    assert contract.gemini_has_pre_mutate_guard(data)


def test_generic_contract_evaluator_handles_session_and_guard_keys():
    codex = _load_template_hooks()
    claude = _load_template_json("templates/.claude/settings.json")

    assert contract.evaluate_hook_contract(codex, "codex", "session_context")
    assert contract.evaluate_hook_contract(codex, "codex", "post_mutate_soft_guard")
    assert contract.evaluate_hook_contract(claude, "claude", "pre_mutate_guard")


def test_generic_contract_issue_builder_follows_platform_contract_keys():
    codex = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh"}]}],
            "PostToolUse": [],
        }
    }
    claude = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh claude"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh"}]}],
        }
    }

    assert contract.contract_issues(codex, "codex") == ["pre-mutate guard", "post-mutate soft guard"]
    assert contract.contract_issues(claude, "claude") == ["pre-mutate guard"]
    assert contract.codex_contract_issues(codex) == contract.contract_issues(codex, "codex")
    assert contract.claude_contract_issues(claude) == contract.contract_issues(claude, "claude")


def test_codex_contract_reports_missing_soft_guard():
    data = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh"}]}],
            "PostToolUse": [],
        }
    }

    assert not contract.codex_has_posttooluse_soft_guard(data)
    assert "post-mutate soft guard" in contract.codex_contract_issues(data)


def test_codex_contract_rejects_commands_in_wrong_hook_bucket():
    data = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]},
                {"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh"}]},
            ],
            "UserPromptSubmit": [],
            "PostToolUse": [
                {"matcher": "apply_patch|Write", "hooks": [{"command": ".agent-hooks/omc-post-file-check.sh"}]}
            ],
        }
    }

    assert not contract.codex_has_session_context_hooks(data)
    assert "session bootstrap" in contract.codex_contract_issues(data)


def test_codex_contract_accepts_soft_guard_matcher_in_any_order():
    data = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh"}]}],
            "PostToolUse": [
                {"matcher": "Write|apply_patch", "hooks": [{"command": ".agent-hooks/omc-post-file-check.sh"}]}
            ],
        }
    }

    assert contract.codex_has_posttooluse_soft_guard(data)


def test_codex_contract_requires_declared_hook_keys_to_exist():
    data = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh"}]}],
        }
    }

    assert not contract.codex_has_posttooluse_soft_guard(data)
    assert "post-mutate soft guard" in contract.codex_contract_issues(data)


def test_codex_contract_follows_required_hook_metadata_for_session_context():
    data = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh"}]}],
        }
    }
    patched = dict(contract.CODEX_HOOK_CONTRACT)
    patched["session_context"] = dict(contract.CODEX_HOOK_CONTRACT["session_context"])
    patched["session_context"]["required_hooks"] = ("SessionStart", "BeforeAgent")

    with patch.object(contract, "CODEX_HOOK_CONTRACT", patched):
        assert not contract.codex_has_session_context_hooks(data)
