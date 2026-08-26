from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from install import _prune_stale_managed_outputs
from omc_setup_gitignore import (
    MigrationStateError,
    apply_git_migration,
    classify_ownership,
    prune_unchanged_legacy,
    rollback_git_migration,
    dry_run_git_migration,
    update_managed_gitignore,
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_classify_ownership_separates_exclusive_merged_and_preserved() -> None:
    assert classify_ownership("scripts/omc.py", "managed_exact") == "exclusive_managed"
    assert classify_ownership(".agents/skills/omc-plan/SKILL.md", "managed_exact") == "exclusive_managed"
    assert classify_ownership("AGENTS.md", "managed_generated") == "merged_host"
    assert classify_ownership("CODEX.md", "managed_exact") == "merged_host"
    assert classify_ownership("CONVENTIONS.md", "managed_exact") == "merged_host"
    assert classify_ownership(".claude/settings.json", "managed_generated") == "merged_host"
    assert classify_ownership(".omc/lessons/local.md", "preserve") == "preserved"


def test_update_managed_gitignore_is_exclusive_only_and_idempotent(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    receipt = {
        "entries": {
            "scripts/omc.py": {"policy": "managed_exact", "ownership": "exclusive_managed"},
            "AGENTS.md": {"policy": "managed_generated", "ownership": "merged_host"},
            ".omc/lessons/local.md": {"policy": "preserve", "ownership": "preserved"},
        }
    }

    first = update_managed_gitignore(tmp_path, receipt)
    second = update_managed_gitignore(tmp_path, receipt)

    assert first == ["scripts/omc.py"]
    assert second == ["scripts/omc.py"]
    content = gitignore.read_text(encoding="utf-8")
    assert content.count("# OMC-KIT:BEGIN") == 1
    assert "/scripts/omc.py" in content
    assert "AGENTS.md" not in content
    assert "local.md" not in content


@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x00", "\x1f", "\x7f"])
def test_receipt_paths_with_control_characters_are_never_managed(
    tmp_path: Path, control: str
) -> None:
    injected = f"scripts/omc_fake.py{control}.env"
    receipt = {
        "schema_version": 2,
        "entries": {injected: {"policy": "managed_exact"}},
    }

    paths = update_managed_gitignore(tmp_path, receipt)

    assert paths == []
    assert ".env" not in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_v2_receipt_keeps_ambiguous_root_files_for_manual_review(tmp_path: Path) -> None:
    assert _git(tmp_path, "init").returncode == 0
    for relative_path in ("scripts/omc.py", "CODEX.md"):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative_path, encoding="utf-8")
    assert _git(tmp_path, "add", "scripts/omc.py", "CODEX.md").returncode == 0
    receipt = {
        "schema_version": 2,
        "entries": {
            "scripts/omc.py": {"policy": "managed_exact"},
            "CODEX.md": {"policy": "managed_exact"},
        },
    }

    report = dry_run_git_migration(tmp_path, receipt)

    assert report["untrack"] == ["scripts/omc.py"]
    assert report["merged_host"] == ["CODEX.md"]
    assert report["manual_review"] == []


def test_apply_and_rollback_preserve_files_and_restore_tracking(tmp_path: Path) -> None:
    assert _git(tmp_path, "init").returncode == 0
    managed = tmp_path / "scripts" / "omc.py"
    managed.parent.mkdir()
    managed.write_text("print('managed')\n", encoding="utf-8")
    host = tmp_path / "AGENTS.md"
    host.write_text("project rules\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    assert _git(tmp_path, "add", "scripts/omc.py", "AGENTS.md").returncode == 0
    receipt = {
        "entries": {
            "scripts/omc.py": {"policy": "managed_exact", "ownership": "exclusive_managed"},
            "AGENTS.md": {"policy": "managed_generated", "ownership": "merged_host"},
        }
    }

    report = apply_git_migration(tmp_path, receipt)

    assert report["untracked"] == ["scripts/omc.py"]
    assert managed.is_file()
    assert _git(tmp_path, "ls-files", "scripts/omc.py").stdout == ""
    assert _git(tmp_path, "ls-files", "AGENTS.md").stdout.strip() == "AGENTS.md"

    restored = rollback_git_migration(tmp_path)
    assert restored == ["scripts/omc.py"]
    assert _git(tmp_path, "ls-files", "scripts/omc.py").stdout.strip() == "scripts/omc.py"


def test_repeated_apply_preserves_original_rollback_receipt(tmp_path: Path) -> None:
    assert _git(tmp_path, "init").returncode == 0
    managed = tmp_path / "scripts" / "omc.py"
    managed.parent.mkdir()
    managed.write_text("print('managed')\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    assert _git(tmp_path, "add", "scripts/omc.py").returncode == 0
    receipt = {
        "entries": {
            "scripts/omc.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
            }
        }
    }

    apply_git_migration(tmp_path, receipt)
    apply_git_migration(tmp_path, receipt)

    assert rollback_git_migration(tmp_path) == ["scripts/omc.py"]
    assert _git(tmp_path, "ls-files", "scripts/omc.py").stdout.strip() == "scripts/omc.py"
    assert gitignore.read_text(encoding="utf-8") == ".venv/\n"
    assert not (tmp_path / ".omc" / "setup-git-migration.json").exists()


def test_apply_treats_receipt_paths_as_literals(tmp_path: Path) -> None:
    assert _git(tmp_path, "init").returncode == 0
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    unrelated = scripts / "app.py"
    unrelated.write_text("print('host')\n", encoding="utf-8")
    assert _git(tmp_path, "add", "scripts/app.py").returncode == 0
    receipt = {
        "schema_version": 3,
        "entries": {
            "scripts/*.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
            }
        },
    }

    report = apply_git_migration(tmp_path, receipt)

    assert report["untracked"] == []
    assert (
        _git(tmp_path, "ls-files", "scripts/app.py").stdout.strip()
        == "scripts/app.py"
    )
    assert "/scripts/\\*.py" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert _git(tmp_path, "check-ignore", "scripts/app.py").returncode == 1


def test_setup_refreshes_active_migration_without_losing_rollback(
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init").returncode == 0
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    first = scripts / "omc.py"
    second = scripts / "omc_guard.py"
    first.write_text("print('first')\n", encoding="utf-8")
    second.write_text("print('second')\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    assert _git(tmp_path, "add", "scripts/omc.py", "scripts/omc_guard.py").returncode == 0
    first_receipt = {
        "schema_version": 3,
        "entries": {
            "scripts/omc.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
            }
        },
    }
    second_receipt = {
        "schema_version": 3,
        "entries": {
            **first_receipt["entries"],
            "scripts/omc_guard.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
            },
        },
    }

    apply_git_migration(tmp_path, first_receipt)
    update_managed_gitignore(tmp_path, second_receipt, sync_migration_receipt=True)
    apply_git_migration(tmp_path, second_receipt)

    assert rollback_git_migration(tmp_path) == [
        "scripts/omc.py",
        "scripts/omc_guard.py",
    ]
    assert gitignore.read_text(encoding="utf-8") == ".venv/\n"


def test_setup_upgrades_v1_migration_receipt_and_preserves_rollback(
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init").returncode == 0
    managed = tmp_path / "scripts" / "omc.py"
    managed.parent.mkdir()
    managed.write_text("print('managed')\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        ".venv/\n\n"
        "# OMC-KIT:BEGIN\n"
        "# Generated by omc_kit setup; do not edit this block.\n"
        "/scripts/omc.py\n"
        "# OMC-KIT:END\n\n"
        "local/\n",
        encoding="utf-8",
    )
    migration = tmp_path / ".omc" / "setup-git-migration.json"
    migration.parent.mkdir()
    migration.write_text(
        json.dumps({"schema_version": 1, "untracked": ["scripts/omc.py"]}),
        encoding="utf-8",
    )
    receipt = {
        "schema_version": 3,
        "entries": {
            "scripts/omc.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
            }
        },
    }

    update_managed_gitignore(tmp_path, receipt, sync_migration_receipt=True)

    upgraded = json.loads(migration.read_text(encoding="utf-8"))
    assert upgraded["schema_version"] == 2
    assert upgraded["gitignore_existed"] is True
    assert upgraded["gitignore_before"] == ".venv/\n\nlocal/\n"
    assert rollback_git_migration(tmp_path) == ["scripts/omc.py"]
    assert gitignore.read_text(encoding="utf-8") == ".venv/\n\nlocal/\n"


def test_v1_migration_without_managed_block_fails_closed(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    migration = tmp_path / ".omc" / "setup-git-migration.json"
    migration.parent.mkdir()
    migration.write_text(
        json.dumps({"schema_version": 1, "untracked": []}),
        encoding="utf-8",
    )

    with pytest.raises(MigrationStateError, match="legacy migration receipt"):
        update_managed_gitignore(
            tmp_path,
            {"schema_version": 3, "entries": {}},
            sync_migration_receipt=True,
        )


def test_setup_refuses_to_refresh_migration_after_user_gitignore_edit(
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init").returncode == 0
    managed = tmp_path / "scripts" / "omc.py"
    managed.parent.mkdir()
    managed.write_text("print('managed')\n", encoding="utf-8")
    assert _git(tmp_path, "add", "scripts/omc.py").returncode == 0
    receipt = {
        "schema_version": 3,
        "entries": {
            "scripts/omc.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
            }
        },
    }
    apply_git_migration(tmp_path, receipt)
    migration_path = tmp_path / ".omc" / "setup-git-migration.json"
    receipt_before = migration_path.read_text(encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "local/\n")
    content_before = gitignore.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="gitignore changed after migration"):
        update_managed_gitignore(tmp_path, receipt, sync_migration_receipt=True)

    assert gitignore.read_text(encoding="utf-8") == content_before
    assert migration_path.read_text(encoding="utf-8") == receipt_before


def test_rollback_refuses_to_overwrite_gitignore_changed_after_apply(
    tmp_path: Path,
) -> None:
    assert _git(tmp_path, "init").returncode == 0
    managed = tmp_path / "scripts" / "omc.py"
    managed.parent.mkdir()
    managed.write_text("print('managed')\n", encoding="utf-8")
    assert _git(tmp_path, "add", "scripts/omc.py").returncode == 0
    receipt = {
        "schema_version": 3,
        "entries": {
            "scripts/omc.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
            }
        },
    }
    apply_git_migration(tmp_path, receipt)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "local-change/\n")

    with pytest.raises(ValueError, match="gitignore changed after migration"):
        rollback_git_migration(tmp_path)

    assert _git(tmp_path, "ls-files", "scripts/omc.py").stdout == ""
    assert (tmp_path / ".omc" / "setup-git-migration.json").is_file()


def test_prune_legacy_removes_only_hash_matching_files(tmp_path: Path) -> None:
    unchanged = tmp_path / "scripts" / "omc_old.py"
    modified = tmp_path / "scripts" / "omc_modified.py"
    unchanged.parent.mkdir()
    unchanged.write_text("old\n", encoding="utf-8")
    modified.write_text("changed\n", encoding="utf-8")
    previous = {
        "entries": {
            "scripts/omc_old.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
                "target_sha256": _sha256(unchanged),
            },
            "scripts/omc_modified.py": {
                "policy": "managed_exact",
                "ownership": "exclusive_managed",
                "target_sha256": hashlib.sha256(b"original\n").hexdigest(),
            },
        }
    }

    report = prune_unchanged_legacy(tmp_path, previous, {"entries": {}})

    assert report["deleted"] == ["scripts/omc_old.py"]
    assert report["modified_legacy"] == ["scripts/omc_modified.py"]
    assert not unchanged.exists()
    assert modified.exists()


def test_prune_legacy_preserves_v2_ambiguous_merged_file(tmp_path: Path) -> None:
    codex = tmp_path / "CODEX.md"
    codex.write_text("project rules\n", encoding="utf-8")
    previous = {
        "schema_version": 2,
        "entries": {
            "CODEX.md": {
                "policy": "managed_exact",
                "target_sha256": _sha256(codex),
            }
        },
    }

    report = prune_unchanged_legacy(tmp_path, previous, {"schema_version": 3, "entries": {}})

    assert report["deleted"] == []
    assert report["manual_review"] == ["CODEX.md"]
    assert codex.exists()


def test_installer_force_prune_preserves_merged_host(tmp_path: Path) -> None:
    codex = tmp_path / "CODEX.md"
    codex.write_text("project rules\n", encoding="utf-8")
    digest = _sha256(codex)
    manifest = {
        "CODEX.md": {
            "policy": "managed_exact",
            "ownership": "merged_host",
            "previously_managed": True,
            "registered_current_install": False,
            "previous_target_sha256": digest,
        }
    }

    removed = _prune_stale_managed_outputs(tmp_path, manifest, force=True)

    assert removed == 0
    assert codex.exists()
    assert "CODEX.md" in manifest
