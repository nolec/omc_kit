#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
from collections import Counter
from functools import lru_cache
from pathlib import Path

from omc_git_hooks import resolve_completion_hook
from omc_source_hash import source_sha256 as _source_sha256
from omc_quality_gate import readiness as _quality_gate_readiness
import omc_version as _version
from omc_setup_gitignore import local_runtime_paths, _safe_relative_path


def _metadata_path(target: Path) -> Path:
    return target / ".omc" / "install-source.json"


def _receipt_path(target: Path) -> Path:
    return target / ".omc" / "install-receipt.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _looks_like_source_kit(path: Path) -> bool:
    return (
        (path / "templates").is_dir()
        and (path / "scripts" / "install.py").is_file()
        and (path / "prompts" / "team.json").is_file()
    )


def _visible_setup_created_paths(
    target: Path, receipt_schema_version: object, entries: dict[str, object]
) -> tuple[list[str], str | None]:
    if receipt_schema_version != 3:
        return [], None
    repository = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )
    if repository.returncode != 0 or repository.stdout.strip() != "true":
        error = "git-query-failed" if (target / ".git").exists() else None
        return [], error
    candidates = set(local_runtime_paths())
    for rel, entry in sorted(entries.items()):
        if not isinstance(entry, dict) or entry.get("setup_created") is not True:
            continue
        safe_path = _safe_relative_path(rel)
        if safe_path is not None:
            candidates.add(safe_path)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=target,
        check=False,
        capture_output=True,
    )
    if untracked.returncode != 0:
        return [], "git-query-failed"
    visible: list[str] = []
    for raw_path in untracked.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if any(
            path == candidate
            or (candidate.endswith("/") and path.startswith(candidate))
            for candidate in candidates
        ):
            visible.append(path)
    return sorted(set(visible)), None


def _active_shell_lines(content: str) -> list[str]:
    return [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _delegated_script(line: str) -> str | None:
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    used_exec = tokens[0] == "exec"
    if used_exec:
        tokens = tokens[1:]
    if len(tokens) < 2:
        return None

    command_token = tokens[0]
    if command_token in {".", "source"}:
        return None if used_exec else tokens[1]
    command = Path(command_token).name
    if command not in {"sh", "bash", "dash", "ksh", "zsh"}:
        return None

    arguments = tokens[1:]
    while arguments and arguments[0].startswith("-"):
        option = arguments.pop(0)
        if option == "--":
            break
        if option == "-" or not set(option[1:]).issubset({"e", "u", "x"}):
            return None
    return arguments[0] if arguments else None


def _husky_dispatches_public_hook(hooks_dir: Path, dispatcher_content: str) -> bool:
    dispatcher_lines = _active_shell_lines(dispatcher_content)
    direct_delegation = any(
        (script := _delegated_script(line)) is not None
        and script.endswith("../post-commit")
        for line in dispatcher_lines
    )
    if direct_delegation:
        return True

    helper_delegation = any(
        (script := _delegated_script(line)) is not None and script.endswith("/h")
        for line in dispatcher_lines
    )
    helper = hooks_dir / "h"
    if not helper_delegation or not helper.is_file():
        return False
    try:
        helper_lines = _active_shell_lines(helper.read_text(encoding="utf-8"))
    except OSError:
        return False
    resolves_hook_name = any("basename" in line and '"$0"' in line for line in helper_lines)
    resolves_public_path = any(
        'dirname "$(dirname "$0")"' in line and "$n" in line
        for line in helper_lines
    )
    executes_public_hook = any(
        _delegated_script(line) in {"$s", "${s}"}
        for line in helper_lines
    )
    return resolves_hook_name and resolves_public_path and executes_public_hook


def completion_hook_readiness(target: Path) -> dict[str, object]:
    resolution = resolve_completion_hook(target)
    hook = resolution.install_hook_path
    result: dict[str, object] = {
        "backend": resolution.backend,
        "configured_hooks_path": resolution.configured_hooks_path,
        "effective_hooks_dir": (
            str(resolution.effective_hooks_dir) if resolution.effective_hooks_dir else None
        ),
        "installed_hook_path": str(hook) if hook else None,
        "dispatch_reachable": False,
    }
    if resolution.backend == "not_git_repository":
        result["readiness"] = "not_applicable"
        return result
    if resolution.backend == "external_shared":
        result["readiness"] = "manual_integration_required"
        return result
    if hook is None:
        result["readiness"] = "unresolved"
        return result
    if not hook.is_file():
        result["readiness"] = "missing"
        return result
    try:
        content = hook.read_text(encoding="utf-8")
    except OSError:
        result["readiness"] = "unreadable"
        return result
    if "OMC:POST_COMMIT:V1" not in content:
        result["readiness"] = "local_conflict"
        return result
    if not hook.stat().st_mode & 0o111:
        result["readiness"] = "not_executable"
        return result
    if resolution.backend == "husky":
        dispatcher = resolution.effective_hooks_dir / "post-commit"
        if not dispatcher.is_file() or not dispatcher.stat().st_mode & 0o111:
            result["readiness"] = "unreachable"
            return result
        try:
            dispatcher_content = dispatcher.read_text(encoding="utf-8")
        except OSError:
            result["readiness"] = "unreachable"
            return result
        if not _husky_dispatches_public_hook(
            resolution.effective_hooks_dir,
            dispatcher_content,
        ):
            result["readiness"] = "unreachable"
            return result
    result["readiness"] = "ready"
    result["dispatch_reachable"] = True
    return result


@lru_cache(maxsize=8)
def _cached_source_sha256(source_path: str) -> str:
    return _source_sha256(Path(source_path))


def _source_freshness(
    source_path: object,
    installed_source_sha256: object,
) -> tuple[str, str | None, str | None]:
    if not isinstance(source_path, str) or not source_path.strip():
        return "unknown", None, "missing-source-path"
    try:
        source = Path(source_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return "unknown", None, "unreadable-source-path"
    if not _looks_like_source_kit(source):
        return "unknown", None, "source-unavailable"
    try:
        current_hash = _cached_source_sha256(str(source))
    except OSError:
        return "unknown", None, "source-unreadable"
    if not isinstance(installed_source_sha256, str) or not installed_source_sha256:
        return "unknown", current_hash, "missing-installed-source-hash"
    if current_hash != installed_source_sha256:
        return "update_available", current_hash, "source-hash-mismatch"
    return "up_to_date", current_hash, None


def _is_source_workspace(
    target: Path,
    trusted_source_root: Path | None,
) -> bool:
    if trusted_source_root is None:
        return False
    try:
        authority = trusted_source_root.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    resolved_target = target.resolve()
    return (
        authority == resolved_target
        and _looks_like_source_kit(resolved_target)
    )


def audit_target(
    target: Path,
    *,
    trusted_source_root: Path | None = None,
) -> dict[str, object]:
    resolved = target.resolve()
    completion_hook = completion_hook_readiness(resolved)
    legacy_dir = resolved / "omc_kit" / "templates"
    metadata_path = _metadata_path(resolved)
    receipt_path = _receipt_path(resolved)
    has_metadata = metadata_path.exists()

    source_kind = None
    source_path = None
    metadata_source_sha256 = None
    metadata_error = None
    if has_metadata:
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                source_kind = data.get("source_kind")
                source_path = data.get("source_path")
                metadata_source_sha256 = data.get("source_sha256")
            else:
                metadata_error = "invalid-json-shape"
        except (OSError, json.JSONDecodeError):
            metadata_error = "invalid-json"

    has_receipt = receipt_path.exists()
    receipt_source_sha256 = None
    receipt_target = None
    receipt_schema_version = None
    receipt_entry_counts: dict[str, int] = {}
    receipt_entries: dict[str, object] = {}
    receipt_error = None
    if has_receipt:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or not isinstance(receipt.get("entries"), dict):
                has_receipt = False
                receipt_error = "invalid-json-shape"
            else:
                receipt_source_sha256 = receipt.get("source_sha256")
                receipt_target = receipt.get("target")
                receipt_schema_version = receipt.get("schema_version")
                receipt_entries = receipt["entries"]
                for entry in receipt["entries"].values():
                    if isinstance(entry, dict):
                        status = entry.get("status")
                        if isinstance(status, str):
                            receipt_entry_counts[status] = receipt_entry_counts.get(status, 0) + 1
        except (OSError, json.JSONDecodeError):
            has_receipt = False
            receipt_error = "invalid-json"

    if _is_source_workspace(resolved, trusted_source_root):
        current_source_sha256 = _cached_source_sha256(str(resolved))
        notices = ["source-workspace:trusted-root"]
        if has_receipt:
            notices.append("source-workspace:install-receipt-not-authoritative")
        if legacy_dir.exists():
            notices.append("source-workspace:legacy-embedded-copy-present")
        return {
            "target": str(resolved),
            "target_kind": "source_workspace",
            "completion_hook": completion_hook,
            "has_legacy_embedded_omc_kit": legacy_dir.exists(),
            "has_install_source": has_metadata,
            "source_kind": source_kind,
            "source_path": source_path,
            "metadata_source_sha256": metadata_source_sha256,
            "metadata_error": metadata_error,
            "has_install_receipt": has_receipt,
            "install_source_sha256": receipt_source_sha256,
            "install_entry_counts": receipt_entry_counts,
            "receipt_error": receipt_error,
            "status": "source_workspace",
            "installed_integrity_status": "not_applicable",
            "core_usage_readiness": "ready",
            "source_freshness_status": "source_workspace",
            "quality_gate_readiness": _quality_gate_readiness(resolved),
            "quality_gate_scope": "delivery_validation",
            "quality_gate_core_impact": "does_not_block_core_usage",
            "version_readiness": _version.source_workspace_readiness(resolved),
            "source_freshness_reason": None,
            "current_source_sha256": current_source_sha256,
            "verification_status": "ok",
            "verification_errors": [],
            "verification_notices": notices,
            "verification_issue_counts": {},
        }

    status = "missing"
    if has_metadata and not legacy_dir.exists() and metadata_error is None:
        status = "ok"
    elif has_metadata or legacy_dir.exists():
        status = "warn"

    verification_errors: list[str] = []
    verification_notices: list[str] = []
    if status != "ok":
        verification_errors.append(f"audit:{status}")
    if not has_receipt:
        verification_errors.append("receipt:missing-or-invalid")
    elif receipt_schema_version not in {1, 2, 3}:
        verification_errors.append("receipt:unsupported-schema")
    elif receipt_target != str(resolved):
        verification_errors.append("receipt:target-mismatch")
    if (
        not isinstance(metadata_source_sha256, str)
        or not metadata_source_sha256
        or receipt_source_sha256 != metadata_source_sha256
    ):
        verification_errors.append("receipt:source-mismatch")
    if has_receipt and not receipt_entries:
        verification_errors.append("receipt:empty-entries")

    for rel, entry in sorted(receipt_entries.items()):
        rel_path = Path(rel)
        if (
            not isinstance(entry, dict)
            or rel_path.is_absolute()
            or ".." in rel_path.parts
        ):
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        policy = entry.get("policy")
        entry_status = entry.get("status")
        ownership = entry.get("ownership")
        if policy not in {"managed_exact", "managed_generated", "preserve"}:
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        if receipt_schema_version == 3 and ownership not in {
            "exclusive_managed",
            "merged_host",
            "preserved",
            "manual_review",
        }:
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        if policy == "preserve" and entry_status != "preserved":
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        if policy != "preserve" and entry_status == "blocked":
            source_hash = entry.get("source_sha256")
            target_hash = entry.get("target_sha256")
            if (
                not isinstance(source_hash, str)
                or not source_hash
                or source_hash == target_hash
            ):
                verification_errors.append(f"manifest-policy-error:{rel}")
            else:
                verification_errors.append(f"managed-drift:{rel}")
        elif policy != "preserve" and entry_status != "updated":
            verification_errors.append(f"entry-invalid:{rel}")
            continue
        if policy == "preserve":
            verification_notices.append(f"preserved-local:{rel}")
            continue
        installed = resolved / rel_path
        try:
            resolved_installed = installed.resolve()
        except (OSError, RuntimeError):
            verification_errors.append(f"unreadable:{rel}")
            continue
        if not resolved_installed.is_relative_to(resolved):
            verification_errors.append(f"outside-target:{rel}")
            continue
        current = resolved
        has_symlink_component = False
        for part in rel_path.parts:
            current /= part
            if current.is_symlink():
                has_symlink_component = True
                break
        expected_hash = entry.get("target_sha256")
        if (
            not isinstance(expected_hash, str)
            or not expected_hash
            or not installed.is_file()
            or installed.is_symlink()
            or has_symlink_component
        ):
            verification_errors.append(f"drift:{rel}")
            continue
        try:
            actual_hash = _sha256_file(installed)
        except OSError:
            verification_errors.append(f"unreadable:{rel}")
            continue
        if actual_hash != expected_hash:
            verification_errors.append(f"drift:{rel}")

    visible_setup_paths, visibility_query_error = _visible_setup_created_paths(
        resolved, receipt_schema_version, receipt_entries
    )
    verification_errors.extend(
        f"setup-visibility:{rel}" for rel in visible_setup_paths
    )
    if visibility_query_error is not None:
        verification_errors.append(f"setup-visibility:{visibility_query_error}")

    installed_integrity_status = "ok" if not verification_errors else "failed"
    version_readiness = _version.version_readiness(
        resolved,
        source_path=source_path if isinstance(source_path, str) else None,
        install_integrity_status=installed_integrity_status,
    )
    if version_readiness["receipt_status"] == "invalid":
        verification_errors.append("version:invalid-receipt")
    (
        source_freshness_status,
        current_source_sha256,
        source_freshness_reason,
    ) = _source_freshness(source_path, metadata_source_sha256)
    if source_freshness_status == "update_available":
        verification_errors.append("source:update-available")
    elif source_freshness_status == "unknown":
        verification_errors.append("source:freshness-unknown")

    issue_counts = Counter()
    for issue in verification_errors:
        prefix = issue.split(":", 1)[0]
        if prefix in {"receipt", "audit", "version"}:
            issue_counts["receipt_error"] += 1
        elif prefix == "source":
            issue_counts["source_freshness"] += 1
        elif prefix in {"entry-invalid", "manifest-policy-error"}:
            issue_counts["manifest_policy_error"] += 1
        else:
            issue_counts["managed_drift"] += 1

    if (
        installed_integrity_status != "ok"
        or version_readiness["receipt_status"] == "invalid"
    ):
        core_usage_readiness = "blocked"
    elif source_freshness_status == "update_available":
        core_usage_readiness = "ready_update_available"
    elif source_freshness_status == "unknown":
        core_usage_readiness = "ready_source_unknown"
    else:
        core_usage_readiness = "ready"

    return {
        "target": str(resolved),
        "completion_hook": completion_hook,
        "has_legacy_embedded_omc_kit": legacy_dir.exists(),
        "has_install_source": has_metadata,
        "source_kind": source_kind,
        "source_path": source_path,
        "metadata_source_sha256": metadata_source_sha256,
        "metadata_error": metadata_error,
        "has_install_receipt": has_receipt,
        "install_source_sha256": receipt_source_sha256,
        "install_entry_counts": receipt_entry_counts,
        "receipt_error": receipt_error,
        "status": status,
        "installed_integrity_status": installed_integrity_status,
        "core_usage_readiness": core_usage_readiness,
        "source_freshness_status": source_freshness_status,
        "quality_gate_readiness": _quality_gate_readiness(resolved),
        "quality_gate_scope": "delivery_validation",
        "quality_gate_core_impact": "does_not_block_core_usage",
        "version_readiness": version_readiness,
        "source_freshness_reason": source_freshness_reason,
        "current_source_sha256": current_source_sha256,
        "verification_status": "ok" if not verification_errors else "failed",
        "verification_errors": verification_errors,
        "verification_notices": verification_notices,
        "verification_issue_counts": dict(sorted(issue_counts.items())),
    }


def _render_text(results: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for item in results:
        lines.append(f"== {item['target']} ==")
        lines.append(f"status: {item['status']}")
        lines.append(f"installed_integrity_status: {item['installed_integrity_status']}")
        lines.append(f"core_usage_readiness: {item['core_usage_readiness']}")
        lines.append(f"source_freshness_status: {item['source_freshness_status']}")
        lines.append(f"quality_gate_readiness: {item['quality_gate_readiness']}")
        lines.append(f"quality_gate_scope: {item['quality_gate_scope']}")
        lines.append(f"quality_gate_core_impact: {item['quality_gate_core_impact']}")
        version = item["version_readiness"]
        lines.append(f"omc_installed_version: {version['installed_version']}")
        lines.append(f"omc_source_version: {version['source_version']}")
        lines.append(f"omc_release_status: {version['release_status']}")
        lines.append(f"omc_version_status: {version['overall_status']}")
        if item["source_freshness_reason"] is not None:
            lines.append(f"source_freshness_reason: {item['source_freshness_reason']}")
        if item["current_source_sha256"] is not None:
            lines.append(f"current_source_sha256: {item['current_source_sha256']}")
        lines.append(f"verification_status: {item['verification_status']}")
        if item["verification_errors"]:
            lines.append(f"verification_errors: {item['verification_errors']}")
        if item["verification_notices"]:
            lines.append(f"verification_notices: {item['verification_notices']}")
        if item["verification_issue_counts"]:
            lines.append(
                f"verification_issue_counts: {item['verification_issue_counts']}"
            )
        lines.append(f"legacy_embedded_omc_kit: {item['has_legacy_embedded_omc_kit']}")
        lines.append(f"install_source: {item['has_install_source']}")
        if item["source_kind"] is not None or item["source_path"] is not None:
            lines.append(f"source_kind: {item['source_kind']}")
            lines.append(f"source_path: {item['source_path']}")
        if item["metadata_error"] is not None:
            lines.append(f"metadata_error: {item['metadata_error']}")
        lines.append(f"install_receipt: {item['has_install_receipt']}")
        if item["receipt_error"] is not None:
            lines.append(f"receipt_error: {item['receipt_error']}")
        if item["install_entry_counts"]:
            lines.append(f"install_entry_counts: {item['install_entry_counts']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit OMC install-source metadata and legacy embedded omc_kit state.")
    ap.add_argument("targets", nargs="+", help="Project roots to inspect.")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero unless every target has a verified installation.",
    )
    args = ap.parse_args()

    trusted_source_root = Path(__file__).resolve().parent.parent
    results = [
        audit_target(Path(target), trusted_source_root=trusted_source_root)
        for target in args.targets
    ]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(_render_text(results), end="")
    if args.strict and any(item["verification_status"] != "ok" for item in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
