"""omc_hook_contract.py contract tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest
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
    assert contract.codex_hooks_disabled(data)
    assert not contract.codex_has_session_context_hooks(data)
    assert not contract.codex_has_posttooluse_soft_guard(data)










def test_generic_contract_issue_builder_follows_platform_contract_keys():
    codex = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh codex"}]}],
            "PostToolUse": [],
        }
    }
    assert contract.contract_issues(codex, "codex") == ["pre-mutate guard", "post-mutate soft guard"]
    assert contract.codex_contract_issues(codex) == contract.contract_issues(codex, "codex")


def test_codex_contract_reports_missing_soft_guard():
    data = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh codex"}]}],
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
                {"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh codex"}]},
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
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh codex"}]}],
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
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh codex"}]}],
        }
    }

    assert not contract.codex_has_posttooluse_soft_guard(data)
    assert "post-mutate soft guard" in contract.codex_contract_issues(data)


def test_codex_contract_follows_required_hook_metadata_for_session_context():
    data = {
        "hooks": {
            "SessionStart": [{"hooks": [{"command": ".agent-hooks/omc-session-start.sh codex"}]}],
            "UserPromptSubmit": [{"hooks": [{"command": ".agent-hooks/omc-prompt-inject.sh codex"}]}],
        }
    }
    patched = dict(contract.CODEX_HOOK_CONTRACT)
    patched["session_context"] = dict(contract.CODEX_HOOK_CONTRACT["session_context"])
    patched["session_context"]["required_hooks"] = ("SessionStart", "BeforeAgent")

    with patch.object(contract, "CODEX_HOOK_CONTRACT", patched):
        assert not contract.codex_has_session_context_hooks(data)


def test_wip_notice_hook_ownership(tmp_path):
    import json
    from omc_hook_contract import configure_wip_notice_hooks, inspect_wip_notice_hooks

    path = tmp_path / ".codex/hooks.json"
    path.parent.mkdir()
    original = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
        {"type": "command", "command": "echo one", "timeout": 3},
        {"type": "command", "command": "echo two"},
    ]}], "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}
    path.write_text(json.dumps(original))
    path.chmod(0o640)
    assert inspect_wip_notice_hooks(tmp_path) == "disabled"
    configure_wip_notice_hooks(tmp_path, enabled=True)
    before = path.read_bytes()
    configure_wip_notice_hooks(tmp_path, enabled=True)
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o640
    assert inspect_wip_notice_hooks(tmp_path) == "enabled"
    config = json.loads(before)
    assert config["hooks"]["SessionStart"][:-1] == original["hooks"]["SessionStart"]
    assert config["hooks"]["Stop"] == original["hooks"]["Stop"]
    configure_wip_notice_hooks(tmp_path, enabled=False)
    configure_wip_notice_hooks(tmp_path, enabled=False)
    assert inspect_wip_notice_hooks(tmp_path) == "disabled"
    assert json.loads(path.read_bytes()) == original


@pytest.mark.parametrize("raw", [
    '{"hooks":{"Stop":[],"Stop":[]}}',
    '{"hooks":{"Stop":[{"hooks":[{"command":"echo bad"}]}]}}',
    '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"python3 scripts/omc_wip_notice.py"}]}]}}',
    '{"hooks":{"PreToolUse":[{"hooks":[{"type":"command","command":"sh .agent-hooks/omc-pipeline-check.sh"}]}]}}',
    '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo bad","timeout":{}}]}]}}',
    '{"hooks":{"Stop":[{"hooks":[{"type":"prompt"}]}]}}',
    'symlink',
])
def test_wip_notice_malformed_config(tmp_path, raw):
    from omc_hook_contract import configure_wip_notice_hooks, inspect_wip_notice_hooks

    path = tmp_path / ".codex/hooks.json"
    path.parent.mkdir()
    if raw == "symlink":
        other = tmp_path / "other.json"
        other.write_text('{"hooks":{}}')
        path.symlink_to(other)
    else:
        path.write_text(raw)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        inspect_wip_notice_hooks(tmp_path)
    with pytest.raises(ValueError):
        configure_wip_notice_hooks(tmp_path, enabled=True)
    assert path.read_bytes() == before
