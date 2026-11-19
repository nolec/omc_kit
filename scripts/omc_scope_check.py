#!/usr/bin/env python3
"""Read-only comparison of explicit paths with the current Git index."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import subprocess


def check(target: Path, expected: list[str]) -> dict:
    result = {'result': 'UNKNOWN', 'path_comparison': 'UNKNOWN',
              'content_verified': False, 'content_status': 'NOT_VERIFIED',
              'approval_inferred': False, 'scope_type': 'staged_paths',
              'worktree_coverage': 'git_status_only_may_hide_changes'}
    if not expected:
        return {**result, 'reason': 'expected_paths_required'}
    if (len(set(expected)) != len(expected) or any(
        not p or '\\' in p or '\x00' in p or PurePosixPath(p).is_absolute()
        or '..' in PurePosixPath(p).parts or PurePosixPath(p).as_posix() != p
        or p == '.' for p in expected
    )):
        return {**result, 'reason': 'expected_paths_invalid'}
    redirect_vars = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                     'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                     'GIT_NAMESPACE', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS')
    if any(name in os.environ for name in redirect_vars):
        return {**result, 'reason': 'git_environment_override'}
    try:
        root = subprocess.run(
            ['git', '-c', 'core.fsmonitor=false', '-C', str(target), 'rev-parse', '--show-toplevel'],
            capture_output=True, check=False, timeout=5,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
        )
        if root.returncode:
            return {**result, 'reason': 'git_unavailable'}
        if Path(os.fsdecode(root.stdout.rstrip(b'\n'))).resolve() != target.resolve():
            return {**result, 'reason': 'target_not_repository_root'}
        proc = subprocess.run(
            ['git', '-c', 'core.fsmonitor=false', '-C', str(target), 'status',
             '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames', '--ignore-submodules=none'],
            capture_output=True, check=False, timeout=5,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
        )
    except (OSError, subprocess.TimeoutExpired):
        return {**result, 'reason': 'git_unavailable'}
    if proc.returncode:
        return {**result, 'reason': 'git_unavailable'}
    staged, unstaged, untracked = set(), set(), set()
    for entry in proc.stdout.split(b'\0'):
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b' ':
            return {**result, 'reason': 'git_output_invalid'}
        code, path = entry[:2], os.fsdecode(entry[3:])
        if code in {b'DD', b'AU', b'UD', b'UA', b'DU', b'AA', b'UU'}:
            return {**result, 'reason': 'unmerged_paths'}
        if code == b'??':
            untracked.add(path)
        else:
            if code[:1] != b' ': staged.add(path)
            if code[1:2] != b' ': unstaged.add(path)
    missing, unexpected = set(expected) - staged, staged - set(expected)
    overlap = sorted(staged & unstaged)
    comparison = 'MISMATCH' if missing or unexpected else 'MATCH'
    warnings = ['staged_and_unstaged_content_not_verified'] if overlap else []
    return {**result, 'result': comparison, 'path_comparison': comparison,
            'staged_and_unstaged': overlap, 'warnings': warnings,
            'expected': sorted(expected), 'staged': sorted(staged),
            'unstaged': sorted(unstaged), 'untracked': sorted(untracked),
            'missing': sorted(missing), 'unexpected': sorted(unexpected)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=Path.cwd())
    parser.add_argument('--expected-path', action='append', default=[])
    args = parser.parse_args()
    result = check(args.target, args.expected_path)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return {'MATCH': 0, 'MISMATCH': 1, 'UNKNOWN': 2}[result['result']]


if __name__ == '__main__':
    raise SystemExit(main())
