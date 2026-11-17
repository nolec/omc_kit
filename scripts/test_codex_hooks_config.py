"""Codex automatic hooks stay explicitly disabled."""
import json
from pathlib import Path
import pytest
from omc_hook_contract import codex_hooks_disabled
ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('name', ['.codex/hooks.json', 'templates/.codex/hooks.json'])
def test_automatic_hooks_are_disabled(name):
    assert codex_hooks_disabled(json.loads((ROOT / name).read_text()))

@pytest.mark.parametrize('data', [None, {}, {'hooks': None}, {'hooks': {}},
    {'hooks': {'SessionStart': [], 'PreToolUse': [], 'UserPromptSubmit': [], 'PostToolUse': [], 'Other': [{'command': 'unexpected'}]}},
    {'hooks': {'SessionStart': [], 'PreToolUse': [], 'UserPromptSubmit': [], 'PostToolUse': None}}])
def test_invalid_or_enabled_configuration_is_not_disabled(data):
    assert not codex_hooks_disabled(data)
