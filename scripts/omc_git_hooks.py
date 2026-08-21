#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CompletionHookResolution:
    backend: str
    configured_hooks_path: str | None
    effective_hooks_dir: Path | None
    install_hook_path: Path | None
    auto_install_allowed: bool
    reason: str | None = None


def _git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(target), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_completion_hook(target: Path) -> CompletionHookResolution:
    target = target.resolve()
    top_level_result = _git(target, "rev-parse", "--show-toplevel")
    if top_level_result.returncode != 0:
        return CompletionHookResolution(
            backend="not_git_repository",
            configured_hooks_path=None,
            effective_hooks_dir=None,
            install_hook_path=None,
            auto_install_allowed=False,
            reason="git_repository_required",
        )

    root = Path(top_level_result.stdout.strip()).resolve()
    configured_result = _git(target, "config", "--path", "--get", "core.hooksPath")
    configured = configured_result.stdout.strip() if configured_result.returncode == 0 else None
    effective_result = _git(target, "rev-parse", "--git-path", "hooks")
    if effective_result.returncode != 0 or not effective_result.stdout.strip():
        return CompletionHookResolution(
            backend="unresolved",
            configured_hooks_path=configured,
            effective_hooks_dir=None,
            install_hook_path=None,
            auto_install_allowed=False,
            reason="hooks_path_unresolved",
        )

    effective = Path(effective_result.stdout.strip())
    if not effective.is_absolute():
        effective = root / effective
    effective = effective.resolve()

    if configured is None:
        return CompletionHookResolution(
            backend="native",
            configured_hooks_path=None,
            effective_hooks_dir=effective,
            install_hook_path=effective / "post-commit",
            auto_install_allowed=True,
        )

    if _inside(effective, root) and effective.name == "_" and effective.parent.name == ".husky":
        return CompletionHookResolution(
            backend="husky",
            configured_hooks_path=configured,
            effective_hooks_dir=effective,
            install_hook_path=effective.parent / "post-commit",
            auto_install_allowed=True,
        )

    if _inside(effective, root):
        return CompletionHookResolution(
            backend="internal_custom",
            configured_hooks_path=configured,
            effective_hooks_dir=effective,
            install_hook_path=effective / "post-commit",
            auto_install_allowed=True,
        )

    return CompletionHookResolution(
        backend="external_shared",
        configured_hooks_path=configured,
        effective_hooks_dir=effective,
        install_hook_path=None,
        auto_install_allowed=False,
        reason="manual_integration_required",
    )
