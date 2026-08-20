from __future__ import annotations

from pathlib import Path

import omc_sync_ssot


def test_sync_map_does_not_recreate_singular_skill_templates():
    destinations = {destination for _, destination in omc_sync_ssot.SYNC_MAP}

    assert not any(path.startswith("templates/.agent/skills/") for path in destinations)


def test_sync_map_publishes_canonical_agent_skills():
    assert (
        ".agents/skills/omc-task/SKILL.md",
        "templates/.agents/skills/omc-task/SKILL.md",
    ) in omc_sync_ssot.SYNC_MAP


def test_sync_map_only_accepts_kit_managed_agent_skills():
    for path in (
        Path("omc-task/SKILL.md"),
        Path("omc-plan/references/workflow.md"),
        Path("pr-create/SKILL.md"),
        Path("SKILL_CHECKLIST.md"),
        Path("SKILL_TEMPLATE.md"),
    ):
        assert omc_sync_ssot._is_managed_agent_skill(path)

    assert not omc_sync_ssot._is_managed_agent_skill(Path("project-local/SKILL.md"))
