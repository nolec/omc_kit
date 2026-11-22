#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from omc_git_hooks import CompletionHookResolution, resolve_completion_hook
from omc_hook_contract import (
    HOOK_CONTRACT_MARKERS,
    HOOK_CONTRACT_SUMMARY,
    codex_hooks_disabled,
)
from omc_source_hash import (
    DEPLOYED_DOCUMENTS,
    DEPLOYED_PROMPTS,
    DEPLOYED_SCRIPT_EXTRAS as _SCRIPTS_EXTRA,
    SOURCE_ONLY_DOCUMENTS,
    is_deployed_script_name,
    source_sha256 as _source_sha256,
)
from omc_setup_gitignore import (
    MigrationStateError,
    _is_git_repository,
    add_receipt_ownership,
    classify_receipt_entry_ownership,
    update_managed_gitignore,
    validate_active_migration,
)
from omc_version import SourceIdentity, capture_source_identity

AGENTS_OMC_BEGIN = "<!-- OMC:BEGIN -->"
_RETIRED_HUB_RULE = ".cursor/rules/omc-hub-sync.mdc"
_RETIRED_HUB_HOOK = ".agent-hooks/omc-hub-push.sh"
_SOURCE_ONLY_AGENT_HOOKS = {
    _RETIRED_HUB_HOOK,
    ".agent-hooks/omc-before-agent.sh",
    ".agent-hooks/omc-post-file-check.sh",
    ".agent-hooks/omc-session-end.sh",
    ".agent-hooks/omc-prompt-inject.sh",
    ".agent-hooks/omc-session-start.sh",
    ".agent-hooks/omc-pipeline-check.sh",
}
AGENTS_OMC_END = "<!-- OMC:END -->"
AGENTS_OMC_VERSION = "<!-- OMC:AGENTS:V1 -->"
AGENTS_OMC_MARKER = "## OMC — Orchestrated Multi-agent Craft"
AGENTS_ETHOS_MARKER = "## Engineering Ethos"
AGENTS_LEGACY_END_MARKER = '> "PROCEED 판정입니다. 바로 플랜을 작성하겠습니다. [plan 내용 시작]..."'
INSTALL_SOURCE_METADATA = Path(".omc") / "install-source.json"
INSTALL_RECEIPT = Path(".omc") / "install-receipt.json"
_QUICKSTART = "docs/omc_quickstart.md"
_PR_CREATE_SKILL = Path(".agents/skills/pr-create")
_INSTALL_POLICY_ROOT: Path | None = None
_INSTALL_POLICY_MANIFEST: dict[str, dict[str, str]] = {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _should_update_install_entry(relative_path: str, *, force: bool) -> bool:
    if not force:
        return True
    path = Path(relative_path)
    if path.parts and path.parts[0] in {
        "scripts",
        "prompts",
        ".agent",
        ".agent-hooks",
        ".agents",
        ".claude",
        ".gemini",
        ".codex",
        ".cursor",
    }:
        return True
    return path.as_posix().startswith("docs/omc_") or path.as_posix() in {
        "docs/quickstart_kr.md",
        "docs/kit_map.md",
        "docs/next_project_pack.md",
        "docs/agent_behavior.md",
        "docs/verification_checklist.md",
    }


def _receipt_entry(
    *,
    policy: str,
    source_sha256: str,
    target_sha256: str,
    previous_target_sha256: str | None = None,
    setup_created: bool = False,
) -> dict[str, object]:
    if policy == "preserve":
        status = "preserved"
    elif not source_sha256 or source_sha256 != target_sha256:
        status = "blocked"
    else:
        status = "updated"
    if policy == "preserve":
        change = "preserved"
    elif previous_target_sha256 == "":
        change = "created"
    elif previous_target_sha256 == target_sha256:
        change = "unchanged"
    else:
        change = "changed_by_setup"
    return {
        "policy": policy,
        "status": status,
        "change": change,
        "setup_created": setup_created,
        "verification_mode": {
            "preserve": "preserved_existing",
            "managed_generated": "generated_output",
        }.get(policy, "source_exact"),
        "source_sha256": source_sha256,
        "target_sha256": target_sha256,
    }


def _install_summary(
    entries: dict[str, dict[str, str]],
    *,
    stage_timings_ms: dict[str, int],
    removed_by_setup: int = 0,
) -> dict[str, object]:
    """Return a bounded setup summary without exposing project-local paths."""
    change_counts = Counter(
        str(entry.get("change", "unknown")) for entry in entries.values()
    )
    if removed_by_setup:
        change_counts["removed_by_setup"] = removed_by_setup
    return {
        "scan_strategy": "bounded_manifest",
        "managed_entry_count": len(entries),
        "change_counts": dict(sorted(change_counts.items())),
        "stage_timings_ms": dict(stage_timings_ms),
    }


def _git_deleted_host_docs(target: Path) -> set[str]:
    missing = [
        name for name in ("AGENTS.md", "CODEX.md")
        if not (target / name).exists() and not (target / name).is_symlink()
    ]
    if not missing or not _is_git_repository(target):
        return set()
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "status", "--porcelain=v1",
         "-z", "--no-renames", "--untracked-files=no", "--", *missing],
        cwd=target, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError("Unable to check Git deletion status for AGENTS.md and CODEX.md")
    candidates = {name.encode(): name for name in missing}
    return {
        candidates[entry[3:]] for entry in result.stdout.split(b"\0")
        if b"D" in entry[:2] and entry[3:] in candidates
    }


def _is_bounded_previous_managed_path(relative_path: str) -> bool:
    """Accept only paths whose OMC ownership is identifiable without target scan."""
    path = Path(relative_path)
    parts = path.parts
    if not parts or path.is_absolute() or ".." in parts:
        return False
    if any(part in {"__pycache__", ".pytest_cache"} for part in parts):
        return False
    if path.name == ".DS_Store":
        return False
    if len(parts) == 2 and parts[0] == "scripts":
        return path.name.startswith("omc_") or path.name in _SCRIPTS_EXTRA
    if len(parts) == 2 and parts[0] == "docs":
        return path.name.startswith("omc_") or path.as_posix() in {
            "docs/quickstart_kr.md",
            "docs/kit_map.md",
            "docs/next_project_pack.md",
            "docs/agent_behavior.md",
            "docs/verification_checklist.md",
        }
    if len(parts) == 2 and parts[0] == "prompts":
        return (
            path.name.startswith(("ROLE_", "MODE_"))
            or path.name in {"README.md", "team.json"}
        )
    if parts[0] == ".agent-hooks":
        return path.name.startswith("omc-")
    if parts[0] in {".agent", ".agents"}:
        return any(part.startswith("omc-") for part in parts[1:]) or (
            "skills" in parts and "pr-create" in parts
        )
    if parts[0] == ".cursor":
        return (
            path.as_posix() in {".cursor/hooks.json"}
            or path.name.startswith("omc-")
        )
    if parts[0] in {".codex", ".gemini"}:
        return path.name in {"hooks.json", "settings.json", "omc-commands.md"}
    if parts[0] == ".claude":
        command_names = {
            "autopilot.md",
            "benchmark.md",
            "brainstorm.md",
            "ceo-review.md",
            "critique.md",
            "investigate.md",
            "lesson.md",
            "office-hours.md",
            "plan.md",
            "qa.md",
            "reentry.md",
            "retro.md",
            "review.md",
            "ship.md",
            "status.md",
            "task.md",
        }
        return path.name == "settings.json" or path.name in command_names
    return path.as_posix() in {
        ".cursorignore",
        "AGENTS.md",
        "CLAUDE.md",
        "CODEX.md",
        "CONVENTIONS.md",
        "ETHOS.md",
        "GEMINI.md",
        "pre-commit",
        "run",
    }


def _build_install_manifest(source_kit: Path, target: Path) -> dict[str, dict[str, str]]:
    """Return managed install paths without scanning unrelated project files."""
    manifest: dict[str, dict[str, str]] = {}
    receipt_path = target / INSTALL_RECEIPT
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                "manifest_policy_error:invalid-install-receipt"
            ) from error
        entries = receipt.get("entries") if isinstance(receipt, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("manifest_policy_error:invalid-install-receipt")
        for rel, entry in sorted(entries.items()):
            rel_path = Path(rel)
            preserved_retired_hub = (
                isinstance(entry, dict)
                and rel == _RETIRED_HUB_RULE
                and receipt.get("schema_version") == 3
                and entry.get("policy") == "preserve"
                and entry.get("ownership") == "preserved"
            )
            if (
                not isinstance(entry, dict)
                or (entry.get("policy") not in {
                    "managed_exact",
                    "managed_generated",
                } and not preserved_retired_hub)
                or not _is_bounded_previous_managed_path(rel)
                or rel_path.is_absolute()
                or ".." in rel_path.parts
            ):
                continue
            if rel_path.parts[0] in {".claude", ".gemini", ".cursor", ".agent"} or rel in {".cursorignore", "CLAUDE.md", "GEMINI.md"}:
                continue
            target_file = target / rel_path
            if preserved_retired_hub and any(
                (target / parent).is_symlink()
                for parent in (rel_path, *rel_path.parents)
            ):
                raise ValueError("manifest_policy_error:retired-hub-rule-symlink")
            manifest[rel] = {
                "policy": str(entry["policy"]),
                "source_sha256": str(entry.get("source_sha256", "")),
                "target_sha256": (
                    _sha256_file(target_file) if target_file.is_file() else ""
                ),
                "previously_managed": True,
                "ownership": str(entry.get("ownership", "")),
                "previous_receipt_schema_version": receipt.get("schema_version"),
                "previous_target_sha256": str(entry.get("target_sha256", "")),
                "setup_created": entry.get("setup_created") is True,
                "registered_current_install": False,
            }
    for source in sorted((source_kit / "scripts").glob("*.py")):
        if not (source.name.startswith("omc_") or source.name in _SCRIPTS_EXTRA):
            continue
        rel = f"scripts/{source.name}"
        target_file = target / rel
        manifest[rel] = {
            "policy": "managed_exact",
            "source_sha256": _sha256_file(source),
            "target_sha256": _sha256_file(target_file) if target_file.is_file() else "",
            "registered_current_install": False,
        }
    return manifest


def _write_install_receipt(
    target: Path,
    *,
    source_identity: SourceIdentity,
    entries: dict[str, dict[str, str]],
) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    installed_at = now
    path = target / INSTALL_RECEIPT
    reinstall = path.is_file()
    if reinstall:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, dict):
            candidate = previous.get("installed_at") or previous.get("created_at")
            if isinstance(candidate, str) and candidate:
                installed_at = candidate
    add_receipt_ownership(entries)
    receipt: dict[str, object] = {
        "schema_version": 3,
        "omc_version": source_identity.version,
        "source_sha256": source_identity.sha256,
        "source_revision": source_identity.revision,
        "target": str(target.resolve()),
        "installed_at": installed_at,
        "updated_at": now,
        "entries": entries,
    }
    update_managed_gitignore(
        target, receipt, sync_migration_receipt=True, preserve_existing=reinstall,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def _classify_auto_update(
    source_kit: Path,
    target: Path,
    *,
    source_identity: SourceIdentity | None = None,
) -> dict[str, object]:
    """Classify whether a session-start update may safely proceed.

    Automatic updates are allowed only when the source is present and every
    previously managed file still matches the last install receipt.
    """
    result: dict[str, object] = {
        "status": "source_missing",
        "source_sha256": "",
        "conflicts": [],
    }
    if not source_kit.is_dir():
        return result

    identity = source_identity or capture_source_identity(source_kit)
    result["source_sha256"] = identity.sha256
    receipt_path = target / INSTALL_RECEIPT
    if not receipt_path.is_file():
        result["status"] = "update_available"
        return result

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["status"] = "local_conflict"
        result["conflicts"] = [".omc/install-receipt.json"]
        return result

    conflicts: list[str] = []
    entries = receipt.get("entries", {})
    if isinstance(entries, dict):
        for rel, entry in entries.items():
            if not isinstance(entry, dict) or entry.get("policy") not in {
                "managed_exact",
                "managed_generated",
            }:
                continue
            target_file = target / str(rel)
            current_hash = _sha256_file(target_file) if target_file.is_file() else ""
            if current_hash != str(entry.get("target_sha256", "")):
                conflicts.append(str(rel))

    if isinstance(entries, dict):
        manifest = _build_install_manifest(source_kit, target)
        for rel, entry in manifest.items():
            if rel in entries or entry.get("policy") != "managed_exact":
                continue
            if (target / rel).is_file():
                conflicts.append(rel)

    if conflicts:
        result["status"] = "local_conflict"
        result["conflicts"] = conflicts
    elif (
        receipt.get("schema_version") in {2, 3}
        and receipt.get("omc_version") == identity.version
        and receipt.get("source_sha256") == identity.sha256
    ):
        result["status"] = "up_to_date"
    else:
        result["status"] = "update_available"
    return result


def _auto_update_exit_code(status: str) -> int:
    """Return a stable process contract for session-start auto updates."""
    if status == "local_conflict":
        return 10
    if status == "source_missing":
        return 11
    return 0


def _finalize_install_manifest(target: Path, manifest: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Mark generated managed outputs with their final content hash."""
    for rel, entry in manifest.items():
        target_file = target / rel
        if entry.get("previously_managed") and not entry.get(
            "registered_current_install"
        ):
            entry["source_sha256"] = ""
            entry["stale_managed"] = True
            previous_hash = entry.get("previous_target_sha256")
            ownership = classify_receipt_entry_ownership(
                {"schema_version": entry.get("previous_receipt_schema_version")},
                rel,
                entry,
            )
            if target_file.is_symlink() or (target_file.is_file() and (
                ownership != "exclusive_managed"
                or not previous_hash
                or _sha256_file(target_file) != previous_hash
            )):
                # Keep unverified content out of future force-install pruning.
                entry["policy"] = "preserve"
        if entry.get("registered_generated") and target_file.is_file():
            digest = _sha256_file(target_file)
            entry["policy"] = "managed_generated"
            entry["source_sha256"] = digest
            entry["target_sha256"] = digest
    return manifest


def _retain_previous_optional_outputs(
    target: Path, manifest: dict[str, dict[str, str]]
) -> None:
    """Preserve previously installed files outside the default deployment."""
    for rel, entry in list(manifest.items()):
        relative = Path(rel)
        source_only_doc = relative.parent == Path("docs") and relative.name in SOURCE_ONLY_DOCUMENTS
        if not source_only_doc and rel not in _SOURCE_ONLY_AGENT_HOOKS and not relative.is_relative_to(_PR_CREATE_SKILL):
            continue
        installed = target / relative
        if not installed.is_file() or any(
            (target / parent).is_symlink() for parent in (relative, *relative.parents)
        ):
            manifest.pop(rel)
            continue
        digest = entry["target_sha256"]
        if digest == entry["previous_target_sha256"] == entry["source_sha256"]:
            entry["registered_current_install"] = True
        else:
            _register_preserved_output(installed)


def _prune_stale_managed_outputs(
    target: Path,
    manifest: dict[str, dict[str, str]],
    *,
    force: bool,
) -> int:
    """Forget missing retired paths and prune unchanged files on force installs."""
    removed = 0
    for rel, entry in list(manifest.items()):
        if (
            not entry.get("previously_managed")
            or entry.get("registered_current_install")
            or not _is_bounded_previous_managed_path(rel)
        ):
            continue
        path = target / rel
        if any((target / parent).is_symlink() for parent in (Path(rel), *Path(rel).parents)):
            continue
        if not path.exists():
            manifest.pop(rel)
            continue
        if not force or not path.is_file():
            continue
        ownership = classify_receipt_entry_ownership(
            {"schema_version": entry.get("previous_receipt_schema_version")},
            rel,
            entry,
        )
        if ownership != "exclusive_managed":
            continue
        previous_hash = str(entry.get("previous_target_sha256", ""))
        if not previous_hash or _sha256_file(path) != previous_hash:
            if (
                rel == _RETIRED_HUB_RULE
                and len(previous_hash) == 64
                and all(char in "0123456789abcdef" for char in previous_hash)
                and path.is_file()
            ):
                # Retired source, known prior ownership, changed regular file:
                # retain bytes without claiming source equality or ownership.
                entry["policy"] = "preserve"
                entry["ownership"] = "preserved"
                entry["source_sha256"] = ""
            continue
        path.unlink()
        removed += 1
        manifest.pop(rel, None)
    return removed


def _register_generated_output(
    path: Path,
    *,
    previous_target_sha256: str | None = None,
    require_previously_managed: bool = False,
) -> None:
    if _INSTALL_POLICY_ROOT is None:
        return
    try:
        rel = str(path.resolve().relative_to(_INSTALL_POLICY_ROOT.resolve()))
    except ValueError:
        return
    entry = _INSTALL_POLICY_MANIFEST.get(rel)
    if require_previously_managed and not (
        entry and entry.get("previously_managed")
    ):
        return
    if entry is None:
        entry = _INSTALL_POLICY_MANIFEST.setdefault(
            rel,
            {"policy": "managed_exact", "source_sha256": "", "target_sha256": ""},
        )
    entry["registered_generated"] = True
    entry["registered_current_install"] = True
    if previous_target_sha256 is not None:
        entry["previous_target_sha256"] = previous_target_sha256


def _register_preserved_output(path: Path) -> None:
    """Record an intentionally retained project-local file as non-blocking."""
    if _INSTALL_POLICY_ROOT is None or not path.is_file():
        return
    try:
        rel = str(path.resolve().relative_to(_INSTALL_POLICY_ROOT.resolve()))
    except ValueError:
        return
    digest = _sha256_file(path)
    _INSTALL_POLICY_MANIFEST[rel] = {
        "policy": "preserve",
        "source_sha256": "",
        "target_sha256": digest,
        "previous_target_sha256": digest,
        "registered_current_install": True,
    }


def _retain_existing_host_output(path: Path, manifest: dict[str, dict[str, str]]) -> None:
    if path.is_symlink() or not path.is_file():
        manifest.pop(path.name, None)
        return
    previous = manifest.get(path.name, {})
    previous_hash = previous.get("previous_target_sha256")
    if previous_hash and previous_hash == previous.get("target_sha256"):
        _register_generated_output(path, previous_target_sha256=previous_hash)
    else:
        _register_preserved_output(path)


def _reset_install_policy_context() -> None:
    global _INSTALL_POLICY_ROOT, _INSTALL_POLICY_MANIFEST
    _INSTALL_POLICY_ROOT = None
    _INSTALL_POLICY_MANIFEST = {}


def _copy(src: Path, dst: Path, *, force: bool, verbatim: bool = False) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    rel = ""
    entry = None
    if _INSTALL_POLICY_ROOT is not None:
        try:
            rel = str(dst.resolve().relative_to(_INSTALL_POLICY_ROOT.resolve()))
        except ValueError:
            rel = ""
        entry = _INSTALL_POLICY_MANIFEST.get(rel)
        if entry and entry.get("policy") == "preserve":
            if force:
                return
        elif rel:
            previous_target_sha256 = str(
                entry.get("target_sha256", "")
                if entry is not None
                else (_sha256_file(dst) if dst.is_file() else "")
            )
            _INSTALL_POLICY_MANIFEST[rel] = {
                "policy": "managed_exact",
                "source_sha256": _sha256_file(src),
                "target_sha256": _sha256_file(dst) if dst.is_file() else "",
                "previous_target_sha256": previous_target_sha256,
                "registered_current_install": True,
            }
            entry = _INSTALL_POLICY_MANIFEST[rel]
    if dst.exists() and not force:
        if rel and entry is None:
            entry = _INSTALL_POLICY_MANIFEST[rel]
        if entry is not None:
            entry["policy"] = "preserve"
            entry["source_sha256"] = ""
            entry["target_sha256"] = _sha256_file(dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if verbatim:
        dst.write_bytes(src.read_bytes())
    else:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _copy_quickstart(
    source_kit: Path, target: Path, manifest: dict[str, dict[str, str]], *, force: bool
) -> None:
    """Keep project content at this path, even during a force install."""
    dst = target / _QUICKSTART
    if dst.is_symlink() or dst.parent.is_symlink() or (dst.exists() and not dst.is_file()):
        manifest.pop(_QUICKSTART, None)
        return
    if dst.is_file():
        previous = manifest.get(_QUICKSTART, {})
        ownership = classify_receipt_entry_ownership(
            {"schema_version": previous.get("previous_receipt_schema_version")},
            _QUICKSTART,
            previous,
        )
        if ownership != "exclusive_managed" or previous.get("previous_target_sha256") != _sha256_file(dst):
            _register_preserved_output(dst)
            return
        if not force:
            # Retain the prior ownership and content for a later explicit refresh.
            previous["registered_current_install"] = True
            return
    _copy(source_kit / _QUICKSTART, dst, force=force, verbatim=True)


def _install_post_commit_hook(template: Path, hook: Path, *, force: bool) -> None:
    marker = "OMC:POST_COMMIT:V1"
    if not hook.exists():
        _copy(template, hook, force=force)
        _ensure_executable(hook)
        return

    existing = hook.read_text(encoding="utf-8")
    if marker not in existing:
        if not force:
            print(f"[WARN] existing post-commit hook preserved; rerun with --force to chain OMC: {hook}")
            return
        backup = hook.with_name("post-commit.omc-original")
        if backup.exists():
            print(f"[WARN] existing post-commit hook preserved; backup already exists: {backup}")
            return
        backup.write_text(existing, encoding="utf-8")
        backup.chmod(hook.stat().st_mode)
        _ensure_executable(backup)

    _copy(template, hook, force=True if marker not in existing else force)
    _ensure_executable(hook)


def _install_completion_hook(
    template: Path,
    target: Path,
    *,
    force: bool,
) -> CompletionHookResolution:
    resolution = resolve_completion_hook(target)
    if resolution.auto_install_allowed and resolution.install_hook_path is not None:
        _install_post_commit_hook(template, resolution.install_hook_path, force=force)
        return resolution

    sample = target / "scripts" / "post-commit.sample"
    _copy(template, sample, force=force)
    if resolution.backend == "external_shared":
        print(
            "[WARN] external core.hooksPath preserved; "
            f"manual post-commit integration required: {resolution.effective_hooks_dir}"
        )
    return resolution


def _remove_legacy_overlay(dst: Path, marker: str) -> None:
    if not dst.exists():
        return
    try:
        content = dst.read_text(encoding="utf-8")
    except OSError:
        return
    if marker in content:
        dst.unlink()


def _write_generated_file(dst: Path, content: str, *, force: bool) -> None:
    """Write a generated target and register it for receipt finalization."""
    if dst.exists() and not force:
        if dst.is_file():
            _register_generated_output(
                dst,
                previous_target_sha256=_sha256_file(dst),
                require_previously_managed=True,
            )
        return
    previous_target_sha256 = _sha256_file(dst) if dst.is_file() else ""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content.rstrip() + "\n", encoding="utf-8")
    _register_generated_output(
        dst, previous_target_sha256=previous_target_sha256
    )


def _extract_agents_omc_block(template_text: str) -> str:
    if AGENTS_OMC_BEGIN in template_text and AGENTS_OMC_END in template_text:
        start = template_text.index(AGENTS_OMC_BEGIN)
        end = template_text.index(AGENTS_OMC_END) + len(AGENTS_OMC_END)
        return template_text[start:end].rstrip() + "\n"
    return template_text.rstrip() + "\n"


def _merge_agents_template(dst: Path, omc_block: str) -> None:
    omc_block = omc_block.rstrip() + "\n"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(omc_block, encoding="utf-8")
        _register_generated_output(dst, previous_target_sha256="")
        return

    current = dst.read_text(encoding="utf-8")
    block_lines = [line for line in omc_block.splitlines() if line.strip()]
    block_start = block_lines[0] if block_lines else AGENTS_OMC_BEGIN
    block_end = block_lines[-1] if block_lines else AGENTS_OMC_END

    if block_start in current and block_end in current and current.index(block_start) <= current.index(block_end):
        start = current.index(block_start)
        end = current.index(block_end) + len(block_end)
        while end < len(current) and current[end] == "\n":
            end += 1
        merged = current[:start] + omc_block + current[end:]
    else:
        legacy_start = None
        if AGENTS_ETHOS_MARKER in current and AGENTS_OMC_MARKER in current:
            ethos_idx = current.index(AGENTS_ETHOS_MARKER)
            omc_idx = current.index(AGENTS_OMC_MARKER)
            if ethos_idx <= omc_idx:
                legacy_start = ethos_idx
        if legacy_start is None and AGENTS_OMC_MARKER in current:
            legacy_start = current.index(AGENTS_OMC_MARKER)

        if legacy_start is not None:
            prefix = current[:legacy_start].rstrip()
            suffix = ""
            legacy_end = current.find(AGENTS_LEGACY_END_MARKER, legacy_start)
            if legacy_end != -1:
                legacy_end += len(AGENTS_LEGACY_END_MARKER)
                while legacy_end < len(current) and current[legacy_end] == "\n":
                    legacy_end += 1
                suffix = current[legacy_end:].lstrip("\n")
            merged = (prefix + "\n\n" if prefix else "") + omc_block
            if suffix:
                merged += "\n" + suffix
        else:
            merged = current.rstrip() + "\n\n" + omc_block

    _write_generated_file(dst, merged, force=True)


def _deployed_script_names(kit_root: Path) -> set[str]:
    scripts_src = kit_root / "scripts"
    names: set[str] = set()
    if scripts_src.exists():
        for src in sorted(scripts_src.glob("*.py")):
            if is_deployed_script_name(src.name):
                names.add(src.name)
    return names


def _ensure_executable(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode
    try:
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except PermissionError:
        print(
            f"[WARN] 실행 권한 설정 실패: {path}\n"
            "       현재 환경 권한(샌드박스/OS 정책)으로 chmod가 차단되었습니다.\n"
            "       권한 허용 후 아래 명령으로 재실행하세요:\n"
            "       python3 scripts/omc.py setup --target ."
        )








def _assert_codex_hook_contract(hooks_path: Path) -> None:
    import json

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    issues = [] if codex_hooks_disabled(data) else ["automatic hooks must be disabled"]
    if not issues:
        return
    detail = ", ".join(issues)
    raise ValueError(f"Codex hook contract invalid: missing {detail}")






def _kit_root(script_path: Path | None = None) -> Path:
    # omc_kit/scripts/install.py -> kit root is parent of scripts/
    here = (script_path or Path(__file__)).resolve()
    candidate = here.parents[1]
    return candidate

def _templates_root(kit_root: Path) -> Path:
    return kit_root / "templates"


def _looks_like_source_kit(path: Path) -> bool:
    return (
        _templates_root(path).is_dir()
        and (path / "scripts" / "install.py").exists()
        and (path / "prompts" / "team.json").exists()
    )


def _install_source_metadata_path(target: Path) -> Path:
    return target / INSTALL_SOURCE_METADATA


def _write_install_source_metadata(
    target: Path,
    source_kit: Path,
    *,
    source_identity: SourceIdentity | None = None,
) -> None:
    identity = source_identity or capture_source_identity(source_kit)
    metadata_path = _install_source_metadata_path(target)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_kind": "external",
        "source_path": str(source_kit.resolve()),
        "installer_version": "v1",
        "omc_version": identity.version,
        "source_sha256": identity.sha256,
        "source_revision": identity.revision,
    }
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_install_source_metadata(target: Path) -> dict[str, str] | None:
    metadata_path = _install_source_metadata_path(target)
    if not metadata_path.exists():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"OMC install source metadata is unreadable: {metadata_path}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"OMC install source metadata is invalid: {metadata_path}")
    return {str(k): str(v) for k, v in data.items()}


def _resolve_source_kit(current_kit: Path, target: Path) -> Path:
    if current_kit.resolve() != target.resolve() and _looks_like_source_kit(current_kit):
        return current_kit.resolve()

    metadata = _read_install_source_metadata(target)
    if metadata is not None:
        raw_source = metadata.get("source_path", "").strip()
        if not raw_source:
            raise SystemExit("OMC install source metadata is missing source_path")
        source_kit = Path(raw_source).expanduser()
        if not _looks_like_source_kit(source_kit):
            raise SystemExit(f"OMC install source path is missing or invalid: {source_kit}")
        return source_kit.resolve()

    legacy_embedded = target / "omc_kit" / "templates"
    if legacy_embedded.is_dir():
        raise SystemExit(
            "OMC setup blocked: legacy embedded omc_kit detected. "
            "Rerun setup from the source kit to refresh install-source metadata."
        )

    raise SystemExit(
        "OMC install source metadata is missing. "
        "Rerun setup from the source kit so this project can track its install source."
    )



def _check_force_regression(kit: Path, tgt: Path) -> bool:
    """--force 실행 전 kit < live 버전 회귀 위험을 감지해 사용자에게 경고.

    핵심 omc 스크립트를 샘플링해 kit이 더 오래됐으면 경고 후 확인을 요청.
    또한 templates/ ↔ live 규칙 파일 차이를 표시해 SSOT 불일치로 인한
    라이브 전용 수정사항 소실을 사전에 알린다.
    True = 계속 진행, False = 사용자가 중단 선택.
    """
    import difflib

    kit_scripts = kit / "scripts"
    live_scripts = tgt / "scripts"
    if not kit_scripts.is_dir() or not live_scripts.is_dir():
        return True

    # 핵심 스크립트 샘플링 (전부 비교하면 느림)
    _SAMPLE = ["omc_tdd_check.py", "omc_pipeline_guard.py", "omc_doctor.py", "omc_hub_push.py"]
    regressions: list[str] = []

    for name in _SAMPLE:
        kit_file = kit_scripts / name
        live_file = live_scripts / name
        if not kit_file.exists() or not live_file.exists():
            continue
        kit_lines = kit_file.read_text(encoding="utf-8").splitlines()
        live_lines = live_file.read_text(encoding="utf-8").splitlines()
        if kit_lines == live_lines:
            continue
        # 라인 수 차이로 간단 판단: kit이 더 적으면 기능이 빠진 것
        diff = len(live_lines) - len(kit_lines)
        if diff > 10:
            regressions.append(f"  {name}: live +{diff}줄 (kit이 더 오래된 버전)")

    # templates/ ↔ live 규칙 파일 SSOT 불일치 검사
    # install.py가 덮어쓸 파일 중 live에서만 수정된 내용을 사전 표시
    ssot_warnings: list[str] = []
    templates = _templates_root(kit)
    _RULE_DIRS = []  # Codex has no mirrored IDE rule tree.
    for tmpl_dir, live_dir in _RULE_DIRS:
        if not tmpl_dir.is_dir() or not live_dir.is_dir():
            continue
        for tmpl_file in sorted(tmpl_dir.rglob("*")):
            if not tmpl_file.is_file():
                continue
            rel = tmpl_file.relative_to(tmpl_dir)
            live_file = live_dir / rel
            if not live_file.exists():
                continue
            tmpl_text = tmpl_file.read_text(encoding="utf-8")
            live_text = live_file.read_text(encoding="utf-8")
            if tmpl_text == live_text:
                continue
            live_lines_count = len(live_text.splitlines())
            tmpl_lines_count = len(tmpl_text.splitlines())
            diff_lines = live_lines_count - tmpl_lines_count
            sign = f"+{diff_lines}" if diff_lines >= 0 else str(diff_lines)
            ssot_warnings.append(
                f"  {live_file.relative_to(tgt)}: live {sign}줄 (덮어쓰면 소실)"
            )

    has_warnings = bool(regressions or ssot_warnings)
    if not has_warnings:
        return True

    if not sys.stdin.isatty():
        if regressions:
            print("[WARN] --force 버전 회귀 감지 (non-interactive 자동 진행):")
            for r in regressions:
                print(r)
        if ssot_warnings:
            print("[WARN] --force SSOT 불일치 감지 — 아래 파일의 live 수정사항이 덮어써집니다:")
            for w in ssot_warnings:
                print(w)
            print("[WARN] live → templates 동기화 후 재실행을 권장합니다.")
        print("[WARN] TTY 환경에서 재실행하면 상세 안내와 확인을 받을 수 있습니다.")
        return True

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" ⚠️  --force 경고")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if regressions:
        print(" [버전 회귀] kit의 스크립트가 live 프로젝트보다 오래된 것 같습니다.")
        for r in regressions:
            print(r)
        print()

    if ssot_warnings:
        print(" [SSOT 불일치] 아래 파일은 live에서만 수정돼 있습니다.")
        print(" --force 실행 시 templates 버전으로 덮어써져 live 수정사항이 소실됩니다.")
        print()
        for w in ssot_warnings:
            print(w)
        print()
        print(" 권장 조치 (SSOT):")
        print("   1. live 파일을 source kit의 templates/ 에 먼저 복사하세요")
        print("      Codex 스킬 SSOT: templates/.agents/skills/")
        print("   2. 또는: python3 scripts/omc_sync_ssot.py")
        print("   3. 그 후 install --force 재실행")
        print()

    if regressions:
        print(" 권장 조치 (버전 회귀):")
        print("   1. hub에서 최신 pull: cd /path/to/omc_kit && git pull")
        print("   2. source kit에서 필요한 변경을 명시적으로 검토하세요 (Hub 자동 동기화는 폐기됨)")
        print("   3. 그 후 install --force 재실행")
        print()

    try:
        ans = input(" 그래도 계속 진행할까요? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    return ans in ("y", "yes")


def _main() -> int:
    setup_started = time.perf_counter()
    ap = argparse.ArgumentParser(description="Install OMC kit into a target repository.")
    ap.add_argument("--target", type=Path, required=True, help="Target repository root.")
    ap.add_argument("--force", action="store_true", help="Overwrite existing files.")
    ap.add_argument("--with-pr-create", action="store_true", help="Install the optional pr-create skill.")
    ap.add_argument("--with-research-tasks", action="store_true", help="Install bundled research task definitions.")
    ap.add_argument("--with-shared-lessons", action="store_true", help="Install bundled shared lessons.")
    ap.add_argument(
        "--auto-update",
        action="store_true",
        help="Only update when the installed OMC files are conflict-free.",
    )
    ap.add_argument(
        "--migrate-shared-tasks",
        action="store_true",
        help="Requires --force; migrate matching legacy shared tasks before refreshing them.",
    )
    args = ap.parse_args()

    kit = _kit_root()
    tgt = args.target.resolve()
    force = bool(args.force)
    auto_update = bool(args.auto_update)
    migrate_shared_tasks = bool(args.migrate_shared_tasks)
    # Refuse an enabled/unknown target before copying or rewriting any file.
    existing_codex_hooks = tgt / ".codex" / "hooks.json"
    if existing_codex_hooks.exists() or existing_codex_hooks.is_symlink():
        if existing_codex_hooks.is_symlink() or existing_codex_hooks.parent.is_symlink():
            raise ValueError("Codex hook target must be a regular local file")
        _assert_codex_hook_contract(existing_codex_hooks)
    source_kit = _resolve_source_kit(kit, tgt)
    source_identity = capture_source_identity(source_kit)
    if auto_update:
        update = _classify_auto_update(
            source_kit,
            tgt,
            source_identity=source_identity,
        )
        print(f"[auto-update] {update['status']}")
        if update["status"] != "update_available":
            if update.get("conflicts"):
                print("[auto-update] 충돌 파일: " + ", ".join(update["conflicts"]))
            return _auto_update_exit_code(str(update["status"]))
        force = True
    validate_active_migration(tgt)
    reinstall = (tgt / INSTALL_RECEIPT).is_file()
    deleted_host_docs = _git_deleted_host_docs(tgt) if reinstall and not force else set()
    templates = _templates_root(source_kit)
    global _INSTALL_POLICY_ROOT, _INSTALL_POLICY_MANIFEST
    manifest_started = time.perf_counter()
    install_manifest = _build_install_manifest(source_kit, tgt)
    manifest_finished = time.perf_counter()
    _INSTALL_POLICY_ROOT = tgt
    _INSTALL_POLICY_MANIFEST = install_manifest

    if force and not _check_force_regression(source_kit, tgt):
        print("[install] 중단됨")
        return 1

    to_copy = [
        (source_kit / "prompts" / name, tgt / "prompts" / name)
        for name in sorted(DEPLOYED_PROMPTS)
    ]

    # scripts: 화이트리스트 방식 — 명시된 파일만 배포 (기본값: 제외)
    # omc_*.py 전체 자동 포함 + 비-omc_ 명시 목록
    # 새 파일 추가 시 이 목록에 없으면 자동 제외됨 (안전한 기본값)
    scripts_src = source_kit / "scripts"
    for name in sorted(_deployed_script_names(source_kit)):
        to_copy.append((scripts_src / name, tgt / "scripts" / name))

    for s, d in to_copy:
        _copy(s, d, force=force)

    for name in sorted(DEPLOYED_DOCUMENTS):
        if f"docs/{name}" == _QUICKSTART:
            _copy_quickstart(source_kit, tgt, install_manifest, force=force)
        else:
            _copy(source_kit / "docs" / name, tgt / "docs" / name, force=force)

    _write_install_source_metadata(
        tgt,
        source_kit,
        source_identity=source_identity,
    )

    run_template = templates / "run"
    if run_template.exists():
        dst = tgt / "run"
        _copy(run_template, dst, force=force)
        _ensure_executable(dst)

    # Bootstrap instructions for agentic tools.
    # 타겟에 파일이 이미 있으면 OMC 섹션만 추가, 없으면 전체 복사.
    # 마커가 있는 파일은 중복 추가 방지. 없는 파일은 전체 복사.
    _MERGE_MARKERS = {
        "CODEX.md":  "## OMC Overlay For Codex",
    }
    _handled = {"ETHOS.md", *deleted_host_docs}

    ethos = tgt / "ETHOS.md"
    ethos_template = templates / "ETHOS.md"
    ethos_created = False
    if ethos.exists() or ethos.is_symlink():
        _retain_existing_host_output(ethos, install_manifest)
    elif ethos_template.is_file():
        _copy(ethos_template, ethos, force=force)
        ethos_created = True

    agents_tpl = templates / "AGENTS.md"
    if agents_tpl.exists() and "AGENTS.md" not in deleted_host_docs:
        _handled.add("AGENTS.md")
        agents_dst = tgt / "AGENTS.md"
        if reinstall and (agents_dst.exists() or agents_dst.is_symlink()):
            _retain_existing_host_output(agents_dst, install_manifest)
        else:
            agents_block = _extract_agents_omc_block(agents_tpl.read_text(encoding="utf-8"))
            _merge_agents_template(agents_dst, agents_block)

    # 마커 기반 병합 대상 (기존 파일에 섹션 추가)
    for tgt_name, marker in _MERGE_MARKERS.items():
        tgt_file = tgt / tgt_name
        tpl_file = templates / tgt_name
        if tgt_name in deleted_host_docs or not tpl_file.exists():
            continue
        _handled.add(tgt_name)
        if tgt_file.exists() and not force:
            previous_target_sha256 = _sha256_file(tgt_file)
            cur = tgt_file.read_text(encoding="utf-8")
            if marker not in cur:
                block = tpl_file.read_text(encoding="utf-8")
                tgt_file.write_text(cur.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")
            _register_generated_output(
                tgt_file,
                previous_target_sha256=previous_target_sha256,
                require_previously_managed=True,
            )
        else:
            _copy(tpl_file, tgt_file, force=force)

    # templates/ 루트의 나머지 .md 파일 자동 복사 (수동 등록 불필요)
    for tpl_file in sorted(templates.glob("*.md")):
        if tpl_file.name in _handled:
            continue
        if tpl_file.name in {"CLAUDE.md", "GEMINI.md"}:
            continue
        _copy(tpl_file, tgt / tpl_file.name, force=force)

    # ── Shared agent-hooks (.agent-hooks/) ──────────────────────────────────
    agent_hooks_src = templates / ".agent-hooks"
    if agent_hooks_src.exists():
        for src in agent_hooks_src.rglob("*"):
            if not src.is_file():
                continue
            relative = Path(".agent-hooks") / src.relative_to(agent_hooks_src)
            if relative.as_posix() in _SOURCE_ONLY_AGENT_HOOKS:
                continue
            dst = tgt / relative
            _copy(src, dst, force=force)
            _ensure_executable(dst)

    # ── Codex CLI commands (.codex/commands/) ────────────────────────────────
    codex_cmds_src = templates / ".codex" / "commands"
    if codex_cmds_src.exists():
        for src in codex_cmds_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(codex_cmds_src)
            dst = tgt / ".codex" / "commands" / rel
            _copy(src, dst, force=force)

    # ── Codex CLI hooks (.codex/hooks.json) ──────────────────────────────────
    codex_hooks_src = templates / ".codex" / "hooks.json"
    if codex_hooks_src.exists():
        _assert_codex_hook_contract(codex_hooks_src)
        _copy(codex_hooks_src, tgt / ".codex" / "hooks.json", force=force)

    # ── Cursor project hooks (optional, when template files exist) ───────────
    # ── Codex skills: templates/.agents/skills is the sole source ──
    # Single source of truth: templates/.agents/skills/
    _retain_previous_optional_outputs(tgt, install_manifest)
    agent_skills_src = templates / ".agents" / "skills"
    if agent_skills_src.exists():
        for src in agent_skills_src.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(agent_skills_src)
            relative = Path(".agents/skills") / rel
            dst = tgt / relative
            if relative.is_relative_to(_PR_CREATE_SKILL):
                if not args.with_pr_create or dst.exists() or any(
                    (tgt / parent).is_symlink() for parent in (relative, *relative.parents)
                ):
                    continue
            _copy(src, dst, force=force)

    # ── .omc/lessons/ — Compound Engineering 교훈 디렉토리 ──────────────────
    lessons_dir = tgt / ".omc" / "lessons"
    if not lessons_dir.exists():
        lessons_dir.mkdir(parents=True, exist_ok=True)
        gitkeep = lessons_dir / ".gitkeep"
        gitkeep.write_text("# Compound Engineering 교훈 디렉토리\n", encoding="utf-8")
        print(f"[install] created {lessons_dir.relative_to(tgt)}/")

    # Manual lifecycle commands remain available; installation never enables them.
    omc_hooks_path = tgt / ".omc" / "hooks.json"
    if not omc_hooks_path.exists():
        _write_generated_file(omc_hooks_path, json.dumps({
            "version": 1, "hooks": {
                "session_start": [], "session_end": [],
                "pre_compact": [], "post_compact": [],
            },
        }, indent=2) + "\n", force=False)

    print(f"Installed OMC kit into: {tgt}")

    if ethos_created:
        _setup_ethos_section5(tgt)
    migrate_tasks = force and migrate_shared_tasks
    _install_shared_tasks(
        source_kit,
        tgt,
        enabled=args.with_research_tasks or migrate_tasks,
        force=migrate_tasks,
        migrate=migrate_tasks,
    )
    _install_shared_lessons(source_kit, tgt, enabled=args.with_shared_lessons)

    apply_finished = time.perf_counter()
    receipt_started = time.perf_counter()
    removed_by_setup = _prune_stale_managed_outputs(
        tgt, _INSTALL_POLICY_MANIFEST, force=force
    )
    # Retired hooks/rules can survive ownership-safe pruning; do not imply
    # that a non-force installation has disabled an older executable.
    if not force:
        print("[install] Hub retirement: non-force preserves existing executables/hooks; retirement is not guaranteed.")
    for retired_rel in (".cursor/rules/omc-hub-sync.mdc",):
        if (tgt / retired_rel).exists() or (tgt / retired_rel).is_symlink():
            print(f"[install] Hub retirement: preserved residue {retired_rel}; inspect separately.")
    for retired_rel, stub_source in (
        ("scripts/omc_hub_push.py", source_kit / "scripts/omc_hub_push.py"),
        (_RETIRED_HUB_HOOK, templates / _RETIRED_HUB_HOOK),
    ):
        installed = tgt / retired_rel
        if installed.is_symlink() or (
            installed.is_file() and _sha256_file(installed) != _sha256_file(stub_source)
        ):
            print(f"[install] Hub retirement: preserved non-stub {retired_rel}; retirement is not guaranteed.")
    receipt_manifest = _finalize_install_manifest(tgt, _INSTALL_POLICY_MANIFEST)
    receipt_entries = {
        rel: _receipt_entry(
            policy=entry["policy"],
            source_sha256=entry["source_sha256"],
            target_sha256=_sha256_file(tgt / rel) if (tgt / rel).is_file() else "",
            previous_target_sha256=str(entry.get("previous_target_sha256", "")),
            setup_created=(
                entry.get("setup_created") is True
                or (
                    not entry.get("previously_managed")
                    and str(entry.get("previous_target_sha256", "")) == ""
                )
            ),
        )
        for rel, entry in receipt_manifest.items()
    }
    _write_install_receipt(
        tgt,
        source_identity=source_identity,
        entries=receipt_entries,
    )
    receipt_finished = time.perf_counter()
    summary = _install_summary(
        receipt_entries,
        stage_timings_ms={
            "manifest": round((manifest_finished - manifest_started) * 1000),
            "apply": round((apply_finished - manifest_finished) * 1000),
            "receipt": round((receipt_finished - receipt_started) * 1000),
            "total": round((receipt_finished - setup_started) * 1000),
        },
        removed_by_setup=removed_by_setup,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    _reset_install_policy_context()

    return 0



def _detect_project_context(tgt: "Path") -> dict:
    """package.json / nx.json / 디렉토리 구조를 스캔해 프로젝트 맥락 자동 감지."""
    import json as _json

    ctx: dict = {"stack": "", "structure": "", "patterns": "", "forbidden": ""}

    # ── 스택 ─────────────────────────────────────────────────────────────────
    stack_parts: list = []
    pkg_path = tgt / "package.json"
    if pkg_path.exists():
        try:
            pkg = _json.loads(pkg_path.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "typescript" in deps or (tgt / "tsconfig.json").exists():
                stack_parts.append("TypeScript")
            for fw, label in (
                ("react", "React"), ("next", "Next.js"),
                ("vue", "Vue"), ("svelte", "Svelte"),
                ("express", "Express"), ("fastify", "Fastify"),
                ("hono", "Hono"),
            ):
                if fw in deps:
                    stack_parts.append(label)
        except Exception:
            pass
    for py_file in ("pyproject.toml", "requirements.txt", "setup.py"):
        if (tgt / py_file).exists():
            stack_parts.append("Python")
            break
    for tool_file, label in (
        ("nx.json", "Nx monorepo"), ("turbo.json", "Turborepo"),
        ("lerna.json", "Lerna"), ("pnpm-workspace.yaml", "pnpm workspace"),
    ):
        if (tgt / tool_file).exists():
            stack_parts.append(label)
            break
    ctx["stack"] = " + ".join(stack_parts)

    # ── 디렉토리 구조 ────────────────────────────────────────────────────────
    structure_lines: list = []
    for dname, hint in (
        ("apps", "앱별 진입점"), ("libs", "재사용 라이브러리"),
        ("packages", "공유 패키지"), ("src", "소스 루트"),
        ("services", "백엔드 서비스"),
    ):
        d = tgt / dname
        if d.is_dir():
            subdirs = sorted(x.name for x in d.iterdir() if x.is_dir())[:5]
            sample = ", ".join(subdirs) + ("..." if len(subdirs) == 5 else "")
            if subdirs:  # 서브디렉토리 없는 빈 폴더 제외
                structure_lines.append(f"`{dname}/` — {hint} ({sample})")
    ctx["structure"] = "\n".join(f"- {s}" for s in structure_lines)

    # ── 기본 패턴 / 금지 ────────────────────────────────────────────────────
    ctx["patterns"] = "기존 파일 옆 패턴 먼저 → 같은 lib 내 패턴 → 새 패턴은 팀에 공유"
    ctx["forbidden"] = (
        "`any` 사용 금지, 컴포넌트 내 직접 API 호출 금지 (훅 분리)"
        if "TypeScript" in ctx["stack"]
        else "(직접 채우세요)"
    )
    return ctx


def _setup_ethos_section5(tgt: "Path") -> None:
    """ETHOS.md 섹션 5를 자동 감지 초안으로 채운다.

    대상 프로젝트(tgt)를 스캔해 스택·구조·패턴을 추론하고 초안을 생성.
    사용자는 Y/n/edit 중 선택만 하면 됨. 플레이스홀더가 없으면 건너뜀.
    """
    import re as _re

    ethos_path = tgt / "ETHOS.md"
    if not ethos_path.exists():
        return

    content = ethos_path.read_text(encoding="utf-8")
    if "___" not in content:
        return

    ctx = _detect_project_context(tgt)

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" ETHOS.md 섹션 5 — 프로젝트 맥락 자동 감지")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print(f"  스택     : {ctx['stack'] or '(감지 실패 — 직접 입력 필요)'}")
    print(f"  구조     :")
    for line in ctx["structure"].splitlines():
        print(f"    {line}")
    print(f"  패턴     : {ctx['patterns']}")
    print(f"  금지     : {ctx['forbidden']}")
    print()

    try:
        ans = input("이 내용으로 채울까요? [Y/n/edit]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n[ETHOS] 건너뜀")
        return

    if ans == "n":
        print("[ETHOS] 건너뜀 — ETHOS.md를 직접 편집하세요.")
        return

    if ans == "edit":
        print("수정할 항목만 입력하세요. (Enter = 감지값 유지)")
        try:
            v = input(f"  스택 [{ctx['stack']}]: ").strip()
            if v:
                ctx["stack"] = v
            v = input(f"  패턴 [{ctx['patterns']}]: ").strip()
            if v:
                ctx["patterns"] = v
            v = input(f"  금지 [{ctx['forbidden']}]: ").strip()
            if v:
                ctx["forbidden"] = v
        except (EOFError, KeyboardInterrupt):
            print("\n[ETHOS] 건너뜀")
            return

    section5 = "\n## 5. 이 프로젝트 맥락\n\n"
    if ctx["stack"]:
        section5 += f"**스택:** {ctx['stack']}\n\n"
    if ctx["structure"]:
        section5 += f"**디렉토리 구조:**\n{ctx['structure']}\n\n"
    if ctx["patterns"]:
        section5 += f"**패턴 우선순위:** {ctx['patterns']}\n\n"
    if ctx["forbidden"]:
        section5 += f"**금지:** {ctx['forbidden']}\n"

    new_content = _re.sub(
        r"## 5\. 이 프로젝트 맥락.*?(?=\n---|\Z)",
        section5,
        content,
        flags=_re.DOTALL,
    )
    ethos_path.write_text(new_content, encoding="utf-8")
    _register_generated_output(ethos_path)
    print(f"[ETHOS] 섹션 5 저장 완료 → {ethos_path.relative_to(tgt)}")



def _install_shared_lessons(kit: Path, tgt: Path, *, enabled: bool = True) -> None:
    """templates/shared_lessons/ 에 있는 교훈 파일을 .omc/lessons/ 에 복사한다.

    이미 존재하는 파일은 덮어쓰지 않아 프로젝트 고유 교훈을 보호한다.
    """
    shared_dir = kit / "templates" / "shared_lessons"
    if not shared_dir.is_dir():
        return

    lessons_dir = tgt / ".omc" / "lessons"
    candidates = sorted(shared_dir.glob("*.md"))
    if not candidates:
        return

    if lessons_dir.is_symlink() or lessons_dir.parent.is_symlink():
        return
    if enabled:
        lessons_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for src in candidates:
        dst = lessons_dir / src.name
        if dst.is_symlink():
            continue
        if dst.exists():
            _register_preserved_output(dst)
            skipped += 1
            continue
        if not enabled:
            continue
        _copy(src, dst, force=True)
        copied += 1

    if copied or skipped:
        print(
            f"[shared_lessons] {copied}개 복사, {skipped}개 건너뜀"
            f" → {lessons_dir.relative_to(tgt)}"
        )


def _install_shared_tasks(
    kit: Path,
    tgt: Path,
    *,
    enabled: bool = True,
    force: bool = False,
    migrate: bool = False,
) -> None:
    """templates/shared_tasks/ 에 있는 태스크 파일을 .omc/tasks/ 에 복사한다.

    기본 설치는 기존 파일을 보존한다. ``--force``에서는 OMC marker가 있는
    태스크만 갱신하고, ``migrate``가 명시된 경우에만 동일 ID의 legacy
    markerless 태스크를 OMC 관리 대상으로 승격한다.
    """
    shared_dir = kit / "templates" / "shared_tasks"
    if not shared_dir.is_dir():
        return

    tasks_dir = tgt / ".omc" / "tasks"
    candidates = sorted(shared_dir.glob("*.json"))
    if not candidates:
        return

    if tasks_dir.is_symlink() or tasks_dir.parent.is_symlink():
        return
    if enabled:
        tasks_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for src in candidates:
        dst = tasks_dir / src.name
        if dst.is_symlink():
            continue
        if dst.exists():
            if not enabled or not force:
                _register_preserved_output(dst)
                skipped += 1
                continue
            try:
                src_data = json.loads(src.read_text(encoding="utf-8"))
                dst_data = json.loads(dst.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _register_preserved_output(dst)
                skipped += 1
                continue
            if not isinstance(src_data, dict) or not isinstance(dst_data, dict):
                _register_preserved_output(dst)
                skipped += 1
                continue
            managed = (
                src_data.get("omc_managed") is True
                and (
                    dst_data.get("omc_managed") is True
                    or (
                        migrate
                        and dst_data.get("id") == src_data.get("id")
                    )
                )
            )
            if not managed:
                _register_preserved_output(dst)
                skipped += 1
                continue
        if not enabled:
            continue
        _copy(src, dst, force=True)
        copied += 1

    if copied or skipped:
        print(
            f"[shared_tasks] {copied}개 복사, {skipped}개 건너뜀"
            f" → {tasks_dir.relative_to(tgt)}"
        )

def main() -> int:
    try:
        try:
            return _main()
        except MigrationStateError as error:
            print(f"[install] migration preflight failed: {error}", file=sys.stderr)
            return 2
        except ValueError as error:
            if str(error).startswith("manifest_policy_error:"):
                print(f"[install] {error}", file=sys.stderr)
                return 2
            raise
    finally:
        _reset_install_policy_context()


if __name__ == "__main__":
    raise SystemExit(main())
