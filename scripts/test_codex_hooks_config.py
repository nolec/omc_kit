#!/usr/bin/env python3
"""Codex .codex/hooks.json 설정 정합성 — stdlib only"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
LIVE_HOOKS = ROOT / ".codex" / "hooks.json"
TEMPLATE_HOOKS = ROOT / "templates" / ".codex" / "hooks.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pretooluse_command(hooks: dict) -> str:
    for entry in hooks.get("hooks", {}).get("PreToolUse", []):
        for h in entry.get("hooks", []):
            return h.get("command", "")
    return ""


def _pretooluse_matcher(hooks: dict) -> str:
    for entry in hooks.get("hooks", {}).get("PreToolUse", []):
        return entry.get("matcher", "")
    return ""


def _posttooluse_entries(hooks: dict) -> list[dict]:
    return hooks.get("hooks", {}).get("PostToolUse", [])


class TestCodexHooksConfig(unittest.TestCase):
    def test_live_hooks_no_omc_block_exit(self):
        """Codex 차단은 exit 2 — OMC_BLOCK_EXIT=1 prefix 금지."""
        cmd = _pretooluse_command(_load(LIVE_HOOKS))
        self.assertNotIn("OMC_BLOCK_EXIT", cmd)
        self.assertIn("omc-pipeline-check.sh", cmd)

    def test_live_hooks_description_not_exit_1(self):
        data = _load(LIVE_HOOKS)
        for entry in data.get("hooks", {}).get("PreToolUse", []):
            for h in entry.get("hooks", []):
                desc = h.get("description", "")
                self.assertNotIn("exit 1", desc.lower())

    def test_live_hooks_has_codex_matchers(self):
        """PreToolUse matcher: Bash + 파일 편집(apply_patch, Write)."""
        matcher = _pretooluse_matcher(_load(LIVE_HOOKS))
        self.assertIn("Bash", matcher)
        self.assertIn("apply_patch", matcher)
        self.assertIn("Write", matcher)

    def test_template_matches_live_policy(self):
        """templates SSOT가 live와 동일 정책."""
        live_cmd = _pretooluse_command(_load(LIVE_HOOKS))
        tpl_cmd = _pretooluse_command(_load(TEMPLATE_HOOKS))
        self.assertEqual(live_cmd, tpl_cmd)
        self.assertNotIn("OMC_BLOCK_EXIT", tpl_cmd)

    def test_posttooluse_soft_guard_exists(self):
        """PostToolUse에 omc-post-file-check.sh 소프트 가드 등록 확인."""
        entries = _posttooluse_entries(_load(LIVE_HOOKS))
        commands = [
            h.get("command", "")
            for entry in entries
            for h in entry.get("hooks", [])
        ]
        self.assertTrue(
            any("omc-post-file-check.sh" in cmd for cmd in commands),
            f"PostToolUse 소프트 가드 없음. 등록된 commands: {commands}",
        )

    def test_posttooluse_soft_guard_in_template(self):
        """templates/.codex/hooks.json에도 소프트 가드 포함."""
        entries = _posttooluse_entries(_load(TEMPLATE_HOOKS))
        commands = [
            h.get("command", "")
            for entry in entries
            for h in entry.get("hooks", [])
        ]
        self.assertTrue(
            any("omc-post-file-check.sh" in cmd for cmd in commands),
            "templates SSOT에 소프트 가드 없음",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
