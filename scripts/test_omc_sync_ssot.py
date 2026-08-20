from __future__ import annotations

import omc_sync_ssot


def test_sync_map_does_not_recreate_singular_skill_templates():
    destinations = {destination for _, destination in omc_sync_ssot.SYNC_MAP}

    assert not any(path.startswith("templates/.agent/skills/") for path in destinations)
