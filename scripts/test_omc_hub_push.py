from __future__ import annotations

import omc_hub_push


def test_hub_push_map_does_not_recreate_singular_skill_templates():
    destinations = {destination for _, destination in omc_hub_push.SYNC_MAP}

    assert not any(path.startswith("templates/.agent/skills/") for path in destinations)


def test_hub_push_map_publishes_canonical_agent_skills():
    assert (
        ".agents/skills/omc-task/SKILL.md",
        "templates/.agents/skills/omc-task/SKILL.md",
    ) in omc_hub_push.SYNC_MAP
