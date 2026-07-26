"""Create an anonymized Git baseline that accepts an approved review diff."""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path


_SENSITIVE_PATTERNS = (
    (re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]+\b", re.I), "<redacted-github-token>"),
    (re.compile(r"\bAKIA[0-9A-Z]{8,}\b"), "<redacted-aws-key>"),
    (re.compile(r"\bBearer\s+[^\s'\"]+", re.I), "Bearer <redacted-token>"),
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "<redacted-email>"),
    (re.compile(r"/(?:Users|home)/[^\s'\"]+"), "<redacted-path>"),
)
_QUOTED_PATH_PAIR_RE = re.compile(r'^diff --git ("(?:[^"\\\\]|\\\\.)*") ("(?:[^"\\\\]|\\\\.)*")$')


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT)


def _paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        path: str | None = None
        if line.startswith("--- ") or line.startswith("+++ "):
            header_path = _decode_git_path(line[4:])
            expected_prefix = "a/" if line.startswith("--- ") else "b/"
            if header_path.startswith(expected_prefix):
                path = header_path[2:]
        elif line.startswith("rename from "):
            path = line.removeprefix("rename from ")
        elif line.startswith("rename to "):
            path = line.removeprefix("rename to ")
        if path is not None and path not in paths:
            paths.append(path)
    return paths


def _decode_git_path(value: str) -> str:
    value = value.rstrip("\n")
    if value.startswith('"'):
        return ast.literal_eval(value)
    return value.split("\t", 1)[0]


def _format_git_path(value: str) -> str:
    if any(character.isspace() or character in {'"', "\\"} or ord(character) < 32 for character in value):
        return json.dumps(value, ensure_ascii=False)
    return value


def _diff_git_paths(line: str) -> tuple[str, str] | None:
    """Extract Git paths from either plain or C-style quoted diff headers."""
    header = line.rstrip("\n").removeprefix("diff --git ")
    quoted_match = _QUOTED_PATH_PAIR_RE.match(line.rstrip("\n"))
    if quoted_match:
        old_path = _decode_git_path(quoted_match.group(1))
        new_path = _decode_git_path(quoted_match.group(2))
        return old_path[2:], new_path[2:]
    if not header.startswith("a/"):
        return None
    separator = header.rfind(" b/")
    if separator < 2:
        return None
    return header[2:separator], header[separator + 3:]


def _rename_operations(diff: str) -> list[tuple[str, str, str | None]]:
    operations: list[tuple[str, str, str | None]] = []
    source: str | None = None
    new_mode: str | None = None
    for line in diff.splitlines():
        if line.startswith("new mode "):
            new_mode = line.removeprefix("new mode ")
        elif line.startswith("rename from "):
            source = line.removeprefix("rename from ")
        elif source is not None and line.startswith("rename to "):
            operations.append((source, line.removeprefix("rename to "), new_mode))
            source = None
    return operations


def _split_pure_rename_blocks(diff: str) -> tuple[str, list[tuple[str, str, str | None]]]:
    """Separate rename-only metadata, which git apply cannot consume."""
    patch_blocks: list[str] = []
    rename_operations: list[tuple[str, str, str | None]] = []
    for block in re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE):
        operations = _rename_operations(block)
        if operations and not any(line.startswith("@@") for line in block.splitlines()):
            rename_operations.extend(operations)
        else:
            patch_blocks.append(block)
    return "".join(patch_blocks), rename_operations


def _redact(value: str, redactions: dict[str, str]) -> str:
    for source, replacement in redactions.items():
        value = value.replace(source, replacement)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _redact_path(value: str, redactions: dict[str, str]) -> str:
    """Redact a Git path without inserting characters that break patch headers."""
    return _redact(value, redactions).replace("<", "").replace(">", "")


def _redact_patch(diff: str, redactions: dict[str, str]) -> str:
    """Keep hunk content aligned while making only Git path headers patch-safe."""
    lines: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            paths = _diff_git_paths(line)
            if paths is None:
                lines.append(_redact(line, redactions))
            else:
                old_path, new_path = paths
                lines.append(
                    f"diff --git {_format_git_path(f'a/{_redact_path(old_path, redactions)}')} "
                    f"{_format_git_path(f'b/{_redact_path(new_path, redactions)}')}\n"
                )
        elif line.startswith("--- ") or line.startswith("+++ "):
            prefix = line[:4]
            header_path = _decode_git_path(line[4:])
            path_prefix = "a/" if prefix == "--- " else "b/"
            if header_path.startswith(path_prefix):
                header_path = f"{path_prefix}{_redact_path(header_path[2:], redactions)}"
            lines.append(f"{prefix}{_format_git_path(header_path)}\n")
        elif line.startswith("rename from ") or line.startswith("rename to "):
            prefix, path = line.rstrip("\n").split(" ", 2)[0:2], line.rstrip("\n").split(" ", 2)[2]
            lines.append(f"{' '.join(prefix)} {_redact_path(path, redactions)}\n")
        elif line.startswith("@@"):
            header_end = line.find("@@", 2) + 2
            lines.append(f"{line[:header_end]}{_redact_path(line[header_end:].rstrip(chr(10)), redactions)}\n")
        else:
            lines.append(_redact(line, redactions))
    return "".join(lines)


def _hunk_preimages_by_path(diff: str) -> dict[str, list[str]]:
    """Return complete pre-change hunks grouped by their parent file path."""
    blocks: dict[str, list[str]] = {}
    lines: list[str] = []
    path: str | None = None
    in_hunk = False

    def append_hunk() -> None:
        nonlocal lines
        if in_hunk and path is not None:
            blocks.setdefault(path, []).append("".join(lines))
        lines = []

    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            append_hunk()
            paths = _diff_git_paths(line)
            path = paths[0] if paths is not None else None
            in_hunk = False
        elif line.startswith("@@"):
            append_hunk()
            in_hunk = True
        elif in_hunk and line[:1] in {" ", "-"}:
            lines.append(line[1:])
    append_hunk()
    return blocks


def _align_parent_context(raw: str, source_diff: str, anonymized_diff: str, path: str) -> str:
    """Rewrite only this file's ordered hunk context for direct patch application."""
    source_blocks = _hunk_preimages_by_path(source_diff).get(path, [])
    anonymized_blocks = _hunk_preimages_by_path(anonymized_diff).get(path, [])
    # Synthetic callers may provide a diff for an empty source commit; redaction
    # rules still align that baseline without an authoritative source hunk.
    if not source_blocks:
        return raw
    if len(source_blocks) != len(anonymized_blocks):
        raise ValueError(f"anonymized diff hunk structure changed for {path}")

    cursor = 0
    for source_block, anonymized_block in zip(source_blocks, anonymized_blocks):
        if source_block and source_block != anonymized_block:
            start = raw.find(source_block, cursor)
            if start < 0:
                raise ValueError(f"anonymized diff context does not match parent file: {path}")
            raw = f"{raw[:start]}{anonymized_block}{raw[start + len(source_block):]}"
            cursor = start + len(anonymized_block)
    return raw


def build_baseline_workspace(
    *, source_repo: str | Path, source_commit: str, diff: str, output: str | Path,
    redactions: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build only changed parent files, commit them, then apply the anonymized diff."""
    source = Path(source_repo)
    target = Path(output)
    rules = redactions or {}
    if target.exists():
        raise ValueError("output workspace already exists")
    parent = _run("git", "rev-parse", f"{source_commit}^", cwd=source).strip()
    source_diff = _run("git", "show", "--format=", "--binary", source_commit, cwd=source)
    paths = _paths(diff)
    if not paths:
        raise ValueError("diff has no baseline paths")
    target.mkdir(parents=True)
    for path in paths:
        try:
            raw = _run("git", "show", f"{parent}:{path}", cwd=source)
        except subprocess.CalledProcessError:
            # New files have no parent version; git apply creates them below.
            continue
        destination = target / _redact_path(path, rules)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _redact(_align_parent_context(raw, source_diff, diff, path), rules), encoding="utf-8"
        )
    _run("git", "init", "-q", cwd=target)
    _run("git", "config", "user.email", "omc-review@example.invalid", cwd=target)
    _run("git", "config", "user.name", "OMC Review", cwd=target)
    _run("git", "add", ".", cwd=target)
    _run("git", "commit", "--allow-empty", "-qm", "anonymized baseline", cwd=target)
    patch_diff, rename_operations = _split_pure_rename_blocks(diff)
    if patch_diff.strip():
        patch = target / ".omc-review.patch"
        # Keep patch context aligned with the redacted parent files before applying it.
        patch.write_text(_redact_patch(patch_diff, rules), encoding="utf-8")
        try:
            _run("git", "apply", "--check", str(patch), cwd=target)
            _run("git", "apply", str(patch), cwd=target)
        finally:
            patch.unlink(missing_ok=True)
    for old_path, new_path, new_mode in rename_operations:
        destination = target / _redact_path(new_path, rules)
        _run("git", "mv", "--", _redact_path(old_path, rules), str(destination.relative_to(target)), cwd=target)
        if new_mode in {"100644", "100755"}:
            destination.chmod(int(new_mode[-3:], 8))
    return {"workspace": str(target), "parent_commit": parent, "changed_paths": [_redact_path(path, rules) for path in paths]}
