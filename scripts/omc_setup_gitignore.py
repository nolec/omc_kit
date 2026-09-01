#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

BEGIN_MARKER = "# OMC-KIT:BEGIN"
END_MARKER = "# OMC-KIT:END"
MIGRATION_RECEIPT = Path(".omc") / "setup-git-migration.json"

_RESERVED_NAMESPACE_PATTERNS = (
    ("/.agent-hooks/omc-*", lambda path: path.startswith(".agent-hooks/omc-")),
    ("/.agent/skills/omc-*/", lambda path: path.startswith(".agent/skills/omc-")),
    ("/.agents/skills/omc-*/", lambda path: path.startswith(".agents/skills/omc-")),
    ("/.claude/commands/omc-*", lambda path: path.startswith(".claude/commands/omc-")),
    ("/.codex/commands/omc-*", lambda path: path.startswith(".codex/commands/omc-")),
    ("/.cursor/hooks/omc-*", lambda path: path.startswith(".cursor/hooks/omc-")),
    ("/.cursor/rules/omc-*", lambda path: path.startswith(".cursor/rules/omc-")),
    ("/.gemini/commands/omc-*", lambda path: path.startswith(".gemini/commands/omc-")),
    ("/docs/omc_*", lambda path: path.startswith("docs/omc_")),
    (
        "/scripts/omc_*.py",
        lambda path: path.startswith("scripts/omc_") and path.endswith(".py"),
    ),
)


class MigrationStateError(ValueError):
    pass


_MERGED_HOST_PATHS = {
    "AGENTS.md",
    "CODEX.md",
    "CONVENTIONS.md",
    "ETHOS.md",
    ".claude/settings.json",
    ".gemini/settings.json",
}

_V2_EXCLUSIVE_PREFIXES = (
    ".agent-hooks/",
    ".agent/",
    ".agents/",
    ".claude/commands/",
    ".codex/commands/",
    ".cursor/hooks/",
    ".cursor/rules/",
    ".gemini/commands/",
    "prompts/",
)

_V2_EXCLUSIVE_PATHS = {
    ".claude/CLAUDE.md",
    ".codex/hooks.json",
    ".cursor/hooks.json",
    ".cursorignore",
    ".gemini/GEMINI.md",
    "PROMPT_COMMON.md",
    "PROMPT_COMMON_LEAN.md",
    "docs/agent_behavior.md",
    "docs/kit_map.md",
    "docs/next_project_pack.md",
    "docs/quickstart_kr.md",
    "docs/verification_checklist.md",
    "run",
    "scripts/compose_prompt.py",
    "scripts/install.py",
    "scripts/omc.py",
}

_LOCAL_RUNTIME_PATHS = {
    ".omc/context.md",
    ".omc/context/",
    ".omc/hooks.json",
    ".omc/install-receipt.json",
    ".omc/install-source.json",
    ".omc/lessons/",
    ".omc/notepad.md",
    ".omc/pipeline_session.json",
    ".omc/policy.json",
    ".omc/project-memory.json",
    ".omc/runs/",
    ".omc/setup-git-migration.json",
    ".omc/state/",
    ".omc/summary.md",
    ".omc/tasks/",
}


def local_runtime_paths() -> tuple[str, ...]:
    return tuple(sorted(_LOCAL_RUNTIME_PATHS))


def _safe_relative_path(value: str) -> str | None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if not normalized or not path.parts or path == PurePosixPath("."):
        return None
    if path.is_absolute() or ".." in path.parts:
        return None
    if path.parts[0] == ".git":
        return None
    return path.as_posix()


def classify_ownership(relative_path: str, policy: str) -> str:
    safe_path = _safe_relative_path(relative_path)
    if policy == "preserve":
        return "preserved"
    if safe_path is None:
        return "manual_review"
    if safe_path in _MERGED_HOST_PATHS:
        return "merged_host"
    if policy in {"managed_exact", "managed_generated"}:
        return "exclusive_managed"
    return "manual_review"


def add_receipt_ownership(entries: dict[str, dict[str, Any]]) -> None:
    for relative_path, entry in entries.items():
        ownership = entry.get("ownership")
        if ownership in {
            "exclusive_managed",
            "merged_host",
            "preserved",
            "manual_review",
        }:
            continue
        entry["ownership"] = classify_ownership(
            relative_path, str(entry.get("policy", ""))
        )


def _v2_ownership(relative_path: str, policy: str) -> str:
    safe_path = _safe_relative_path(relative_path)
    if policy == "preserve":
        return "preserved"
    if safe_path is None:
        return "manual_review"
    if safe_path in _MERGED_HOST_PATHS or safe_path in {
        ".claude/settings.json",
        ".gemini/settings.json",
    }:
        return "merged_host"
    if (
        safe_path in _V2_EXCLUSIVE_PATHS
        or safe_path.startswith(_V2_EXCLUSIVE_PREFIXES)
        or safe_path.startswith("scripts/omc_")
        or safe_path.startswith("docs/omc_")
    ):
        return "exclusive_managed"
    return "manual_review"


def classify_receipt_entry_ownership(
    receipt: dict[str, Any], relative_path: str, entry: dict[str, Any]
) -> str:
    ownership = entry.get("ownership")
    if ownership in {
        "exclusive_managed",
        "merged_host",
        "preserved",
        "manual_review",
    }:
        return str(ownership)
    if receipt.get("schema_version") in {None, 1, 2}:
        return _v2_ownership(relative_path, str(entry.get("policy", "")))
    return "manual_review"


def classify_receipt_paths(receipt: dict[str, Any]) -> dict[str, list[str]]:
    entries = receipt.get("entries", {})
    if not isinstance(entries, dict):
        raise ValueError("invalid install receipt entries")
    result = {
        "exclusive_managed": [],
        "merged_host": [],
        "preserved": [],
        "manual_review": [],
    }
    for relative_path, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            continue
        safe_path = _safe_relative_path(str(relative_path))
        if safe_path is None:
            result["manual_review"].append(str(relative_path))
            continue
        ownership = classify_receipt_entry_ownership(
            receipt, str(relative_path), raw_entry
        )
        result[ownership].append(safe_path)
    return {key: sorted(set(paths)) for key, paths in result.items()}


def _exclusive_paths(receipt: dict[str, Any]) -> list[str]:
    classified = classify_receipt_paths(receipt)
    entries = receipt.get("entries", {})
    setup_created_merged_hosts = {
        path
        for path in classified["merged_host"]
        if isinstance(entries.get(path), dict)
        and entries[path].get("setup_created") is True
    }
    return sorted(
        set(classified["exclusive_managed"])
        | setup_created_merged_hosts
        | set(local_runtime_paths())
    )


def _gitignore_literal_path(path: str) -> str:
    escaped = "".join(
        f"\\{character}" if character in "\\*?[] " else character for character in path
    )
    return f"/{escaped}"


def _git_exclude_path(target: Path) -> Path:
    target = target.resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise MigrationStateError(
            result.stderr.strip() or "git exclude path unavailable"
        )
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else (target / path).resolve()


def _is_git_repository(target: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=target.resolve(),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _compact_ignore_rules(paths: list[str]) -> list[str]:
    remaining = set(paths)
    patterns: list[str] = []
    for pattern, matches in _RESERVED_NAMESPACE_PATTERNS:
        covered = {path for path in remaining if matches(path)}
        if covered:
            patterns.append(pattern)
            remaining -= covered
    return sorted(patterns + [_gitignore_literal_path(path) for path in remaining])


def _render_block(paths: list[str]) -> str:
    lines = [BEGIN_MARKER, "# Generated by omc_kit setup; do not edit this block."]
    lines.extend(_compact_ignore_rules(paths))
    lines.append(END_MARKER)
    return "\n".join(lines)


def _gitignore_sha256(gitignore: Path) -> str:
    content = gitignore.read_bytes() if gitignore.is_file() else b""
    return hashlib.sha256(content).hexdigest()


def _without_managed_block(content: str) -> str:
    if content.count(BEGIN_MARKER) != content.count(END_MARKER):
        raise MigrationStateError("damaged OMC-KIT ignore marker block")
    if content.count(BEGIN_MARKER) > 1:
        raise MigrationStateError("duplicate OMC-KIT ignore marker blocks")
    if BEGIN_MARKER not in content:
        return content
    before, remainder = content.split(BEGIN_MARKER, 1)
    _, after = remainder.split(END_MARKER, 1)
    prefix = before.rstrip()
    suffix = after.lstrip("\n")
    if prefix and suffix:
        return prefix + "\n\n" + suffix
    if prefix:
        return prefix + "\n"
    return suffix


def _legacy_gitignore_before(gitignore: Path) -> str:
    if not gitignore.is_file():
        raise MigrationStateError("legacy migration receipt requires managed block")
    try:
        existing = gitignore.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise MigrationStateError("invalid migration receipt") from error
    if existing.count(BEGIN_MARKER) != 1 or existing.count(END_MARKER) != 1:
        raise MigrationStateError("legacy migration receipt requires managed block")
    return _without_managed_block(existing)


def _upgrade_v1_migration(payload: dict[str, Any], gitignore: Path) -> dict[str, Any]:
    raw_paths = payload.get("untracked")
    if not isinstance(raw_paths, list):
        raise MigrationStateError("invalid migration receipt")
    paths: list[str] = []
    for raw in raw_paths:
        if not isinstance(raw, str) or (safe_path := _safe_relative_path(raw)) is None:
            raise MigrationStateError("invalid migration receipt")
        paths.append(safe_path)
    return {
        "schema_version": 2,
        "untracked": sorted(set(paths)),
        "gitignore_existed": True,
        "gitignore_before": _legacy_gitignore_before(gitignore),
        "gitignore_after_sha256": _gitignore_sha256(gitignore),
    }


def _load_active_migration(
    migration_path: Path, gitignore: Path, exclude: Path
) -> dict[str, Any] | None:
    if not migration_path.is_file():
        return None
    try:
        payload = json.loads(migration_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationStateError("invalid migration receipt") from error
    if isinstance(payload, dict) and payload.get("schema_version") == 1:
        return _upgrade_v1_migration(payload, gitignore)
    if isinstance(payload, dict) and payload.get("schema_version") == 3:
        if not isinstance(payload.get("untracked"), list) or not isinstance(
            payload.get("exclude_after_sha256"), str
        ):
            raise MigrationStateError("invalid migration receipt")
        if _gitignore_sha256(exclude) != payload["exclude_after_sha256"]:
            raise MigrationStateError("git exclude changed after migration")
        return payload
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 2
        or not isinstance(payload.get("untracked"), list)
        or not isinstance(payload.get("gitignore_after_sha256"), str)
    ):
        raise MigrationStateError("invalid migration receipt")
    if _gitignore_sha256(gitignore) != payload["gitignore_after_sha256"]:
        raise MigrationStateError("gitignore changed after migration")
    return payload


def validate_active_migration(target: Path) -> None:
    target = target.resolve()
    migration_path = target / MIGRATION_RECEIPT
    if not migration_path.is_file():
        return
    try:
        payload = json.loads(migration_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationStateError("invalid migration receipt") from error
    if not isinstance(payload, dict):
        raise MigrationStateError("invalid migration receipt")
    schema_version = payload.get("schema_version")
    if schema_version == 1:
        _upgrade_v1_migration(payload, target / ".gitignore")
    elif schema_version == 2:
        if not isinstance(payload.get("untracked"), list) or not isinstance(
            payload.get("gitignore_after_sha256"), str
        ):
            raise MigrationStateError("invalid migration receipt")
        if (
            _gitignore_sha256(target / ".gitignore")
            != payload["gitignore_after_sha256"]
        ):
            raise MigrationStateError("gitignore changed after migration")
    elif schema_version == 3:
        if not isinstance(payload.get("untracked"), list) or not isinstance(
            payload.get("exclude_after_sha256"), str
        ):
            raise MigrationStateError("invalid migration receipt")
    else:
        raise MigrationStateError("invalid migration receipt")
    if not _is_git_repository(target):
        raise MigrationStateError("active migration requires a Git repository")
    _load_active_migration(
        migration_path,
        target / ".gitignore",
        _git_exclude_path(target),
    )


def update_managed_gitignore(
    target: Path,
    receipt: dict[str, Any],
    *,
    sync_migration_receipt: bool = False,
) -> list[str]:
    target = target.resolve()
    gitignore = target / ".gitignore"
    migration_path = target / MIGRATION_RECEIPT
    paths = _exclusive_paths(receipt)
    if not _is_git_repository(target):
        if migration_path.is_file():
            raise MigrationStateError("active migration requires a Git repository")
        return paths
    exclude = _git_exclude_path(target)
    migration = None
    if sync_migration_receipt:
        migration = _load_active_migration(migration_path, gitignore, exclude)

    gitignore_existing = (
        gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    )
    gitignore_updated = _without_managed_block(gitignore_existing)
    if gitignore_updated != gitignore_existing:
        gitignore.write_text(gitignore_updated, encoding="utf-8")

    exclude_existed = exclude.is_file()
    existing = exclude.read_text(encoding="utf-8") if exclude_existed else ""
    existing_without_block = _without_managed_block(existing)

    block = _render_block(paths)
    updated = (
        existing_without_block.rstrip()
        + ("\n\n" if existing_without_block.strip() else "")
        + block
        + "\n"
    )
    if updated != existing:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(updated, encoding="utf-8")
    if migration is not None:
        if migration.get("schema_version") != 3:
            migration = {
                "schema_version": 3,
                "untracked": migration.get("untracked", []),
                "exclude_existed": exclude_existed,
                "exclude_before": existing_without_block,
            }
        migration["exclude_after_sha256"] = _gitignore_sha256(exclude)
        migration_path.write_text(
            json.dumps(migration, indent=2) + "\n",
            encoding="utf-8",
        )
    return paths


def _run_git(target: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )


def _tracked_exclusive_paths(target: Path, receipt: dict[str, Any]) -> list[str]:
    paths = _exclusive_paths(receipt)
    if not paths:
        return []
    result = _run_git(target, ["--literal-pathspecs", "ls-files", "--", *paths])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return sorted(line for line in result.stdout.splitlines() if line)


def dry_run_git_migration(target: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    target = target.resolve()
    classified = classify_receipt_paths(receipt)
    return {
        "action": "dry-run",
        "untrack": _tracked_exclusive_paths(target, receipt),
        "merged_host": classified["merged_host"],
        "preserved": classified["preserved"],
        "manual_review": classified["manual_review"],
        "local_files_preserved": True,
    }


def apply_git_migration(target: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    target = target.resolve()
    migration_path = target / MIGRATION_RECEIPT
    gitignore = target / ".gitignore"
    exclude = _git_exclude_path(target)
    existing_migration: dict[str, Any] = {}
    if migration_path.is_file():
        existing_migration = (
            _load_active_migration(migration_path, gitignore, exclude) or {}
        )

    paths = _tracked_exclusive_paths(target, receipt)
    exclude_existed = exclude.is_file()
    exclude_before = exclude.read_text(encoding="utf-8") if exclude_existed else ""
    update_managed_gitignore(
        target,
        receipt,
        sync_migration_receipt=bool(existing_migration),
    )
    if migration_path.is_file():
        existing_migration = json.loads(migration_path.read_text(encoding="utf-8"))
    if paths:
        result = _run_git(
            target,
            ["--literal-pathspecs", "rm", "--cached", "--ignore-unmatch", "--", *paths],
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git rm --cached failed")
    migration_path.parent.mkdir(parents=True, exist_ok=True)
    previous_paths = {
        path
        for raw in existing_migration.get("untracked", [])
        if (path := _safe_relative_path(str(raw)))
    }
    untracked = sorted(previous_paths | set(paths))
    migration_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "untracked": untracked,
                "exclude_existed": existing_migration.get(
                    "exclude_existed", exclude_existed
                ),
                "exclude_before": existing_migration.get(
                    "exclude_before", exclude_before
                ),
                "exclude_after_sha256": _gitignore_sha256(exclude),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"action": "apply", "untracked": untracked, "local_files_preserved": True}


def rollback_git_migration(target: Path) -> list[str]:
    target = target.resolve()
    migration_path = target / MIGRATION_RECEIPT
    if not migration_path.is_file():
        raise ValueError("migration receipt not found")
    payload = json.loads(migration_path.read_text(encoding="utf-8"))
    raw_paths = payload.get("untracked", [])
    if not isinstance(raw_paths, list):
        raise ValueError("invalid migration receipt")
    gitignore = target / ".gitignore"
    exclude = _git_exclude_path(target)
    migration = _load_active_migration(migration_path, gitignore, exclude)
    paths = [path for raw in raw_paths if (path := _safe_relative_path(str(raw)))]
    existing = [path for path in paths if (target / path).is_file()]
    if existing:
        result = _run_git(target, ["--literal-pathspecs", "add", "-f", "--", *existing])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git add rollback failed")
    if migration and migration.get("schema_version") == 3:
        if migration.get("exclude_existed"):
            exclude.write_text(
                str(migration.get("exclude_before", "")), encoding="utf-8"
            )
        elif exclude.is_file():
            exclude.unlink()
    elif "gitignore_before" in payload:
        if payload.get("gitignore_existed"):
            gitignore.write_text(str(payload["gitignore_before"]), encoding="utf-8")
        elif gitignore.is_file():
            gitignore.unlink()
    migration_path.unlink()
    return sorted(existing)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prune_unchanged_legacy(
    target: Path,
    previous_receipt: dict[str, Any],
    current_receipt: dict[str, Any],
) -> dict[str, list[str]]:
    target = target.resolve()
    previous_entries = previous_receipt.get("entries", {})
    current_entries = current_receipt.get("entries", {})
    if not isinstance(previous_entries, dict) or not isinstance(current_entries, dict):
        raise ValueError("invalid install receipt entries")
    report = {"deleted": [], "modified_legacy": [], "manual_review": []}
    for relative_path in sorted(set(previous_entries) - set(current_entries)):
        entry = previous_entries[relative_path]
        if not isinstance(entry, dict):
            continue
        safe_path = _safe_relative_path(str(relative_path))
        ownership = classify_receipt_entry_ownership(
            previous_receipt, str(relative_path), entry
        )
        if safe_path is None or ownership != "exclusive_managed":
            report["manual_review"].append(str(relative_path))
            continue
        path = target / safe_path
        if not path.is_file():
            continue
        expected_hash = str(entry.get("target_sha256", ""))
        if not expected_hash or _file_sha256(path) != expected_hash:
            report["modified_legacy"].append(safe_path)
            continue
        path.unlink()
        report["deleted"].append(safe_path)
    return report


def load_receipt(target: Path) -> dict[str, Any]:
    path = target.resolve() / ".omc" / "install-receipt.json"
    if not path.is_file():
        raise ValueError("install receipt not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid install receipt")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage Git ignore state for omc_kit setup outputs."
    )
    parser.add_argument("action", choices=["dry-run", "apply", "rollback"])
    parser.add_argument("--target", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.action == "rollback":
        print(
            json.dumps(
                {"action": "rollback", "restored": rollback_git_migration(args.target)}
            )
        )
        return 0
    receipt = load_receipt(args.target)
    if args.action == "dry-run":
        print(
            json.dumps(
                dry_run_git_migration(args.target, receipt),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(
        json.dumps(
            apply_git_migration(args.target, receipt), ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationStateError as error:
        print(f"[setup-ignore] {error}", file=sys.stderr)
        raise SystemExit(2) from None
