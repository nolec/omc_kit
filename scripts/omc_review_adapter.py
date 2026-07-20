"""Safe, isolated workspace primitives for review comparison runs."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Iterator


IGNORED_DIR_NAMES = frozenset(
    {".git", ".omc", "node_modules", ".venv", "dist", "build", "coverage", "__pycache__", ".next"}
)
DEFAULT_MAX_BYTES = 100 * 1024 * 1024


@dataclass
class Snapshot:
    path: Path
    initial_hash: str
    final_hash: str | None = None
    mutated: bool = False


def _assert_no_symlinks(root: Path, ignored_dirs: Iterable[str] | None = None) -> None:
    ignored_dirs = _normalize_ignored_dirs(ignored_dirs)
    for path in root.rglob("*"):
        if any(part in ignored_dirs for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"snapshot source cannot contain symlinks: {path.name}")


def _normalize_ignored_dirs(ignored_dirs: Iterable[str] | None) -> frozenset[str]:
    return IGNORED_DIR_NAMES | frozenset(ignored_dirs or ())


def _iter_snapshot_files(
    root: Path, ignored_dirs: Iterable[str] | None = None
) -> Iterator[Path]:
    ignored = _normalize_ignored_dirs(ignored_dirs)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts) or not path.is_file():
            continue
        yield path


def _snapshot_ignore(ignored_dirs: frozenset[str]):
    return lambda _directory, names: [name for name in names if name in ignored_dirs]


def _tree_hash(root: Path, ignored_dirs: Iterable[str] | None = None) -> str:
    digest = hashlib.sha256()
    for path in _iter_snapshot_files(root, ignored_dirs):
        relative = path.relative_to(root)
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_mode & 0o7777).encode("ascii"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def isolated_snapshot(
    source: str | Path,
    *,
    ignored_dirs: Iterable[str] | None = None,
) -> Iterator[Snapshot]:
    """Yield a temporary copy and report mutations before cleanup.

    ``ignored_dirs`` adds project-specific directories to the protected default
    policy; it cannot re-enable built-in protected directories.
    """
    source_path = Path(source)
    if not source_path.is_dir():
        raise ValueError("source directory is required")
    ignored = _normalize_ignored_dirs(ignored_dirs)
    _assert_no_symlinks(source_path, ignored)
    with tempfile.TemporaryDirectory(prefix="omc-review-") as temp_dir:
        snapshot_path = Path(temp_dir) / "workspace"
        shutil.copytree(
            source_path,
            snapshot_path,
            symlinks=False,
            ignore=_snapshot_ignore(ignored),
        )
        snapshot = Snapshot(
            path=snapshot_path,
            initial_hash=_tree_hash(snapshot_path, ignored),
        )
        try:
            yield snapshot
        finally:
            snapshot.final_hash = _tree_hash(snapshot_path, ignored)
            snapshot.mutated = snapshot.final_hash != snapshot.initial_hash
