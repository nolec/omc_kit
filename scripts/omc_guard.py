#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_state
import omc_utils


_FROZEN_LIVE_REGISTRATIONS = frozenset({
    (
        "completion-quality-live-20260913-v2",
        "67b169155db9e2afce0760fd6dd18bfd7ce76f67b2418e5df66c34e0fcdbb073",
    ),
})


def _is_frozen_live_registration(policy_path: Path) -> bool:
    """Do not restart an explicitly retired study; malformed policy still fails closed."""
    import omc_completion_observation as observation

    if policy_path.is_symlink():
        return False
    try:
        policy = observation._validate_live_record(
            json.loads(policy_path.read_text(encoding="utf-8")),
            artifact_type="policy",
            hash_field="policy_sha256",
        )
    except (OSError, UnicodeDecodeError, ValueError, observation.CaptureError):
        return False
    return (
        policy.get("study_id"), policy.get("cohort_registration_sha256")
    ) in _FROZEN_LIVE_REGISTRATIONS


def _prospective_work_class_lock(project_root: Path):
    import omc_work_class_lock

    return omc_work_class_lock.load_private_key(project_root)


def _report_live_start_runner_failure(project_root: Path, reason: str) -> None:
    try:
        import omc_completion_observation

        omc_completion_observation.record_live_failure(
            project_root, command="live-start", reason=reason
        )
    except Exception:
        pass
    print(f"[OMC-OBSERVATION] OBSERVATION_INVALID reason={reason}")


def _start_registered_live_observation(project_root: Path) -> None:
    """Start enrolled observation without making instrumentation task-critical."""
    opt_out = project_root / '.omc' / 'observations' / 'opt-out.json'
    if opt_out.exists() or opt_out.is_symlink():
        return
    policy_path = project_root / ".omc" / "observation-policy.json"
    transition_path = project_root / ".omc" / "observations" / "transition.json"
    # The observation runner resolves and validates the active transition policy.
    # Even a broken transition must reach it rather than silently skip collection.
    has_transition = transition_path.exists() or transition_path.is_symlink()
    if not has_transition and (
        not policy_path.is_file() or _is_frozen_live_registration(policy_path)
    ):
        return
    script = Path(__file__).resolve().parent / "omc_completion_observation.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "live-start", "--target", str(project_root)],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        _report_live_start_runner_failure(
            project_root, "live_start_runner_unavailable"
        )
        return
    if result.returncode != 0:
        _report_live_start_runner_failure(
            project_root, f"live_start_runner_exit_{result.returncode}"
        )
        return
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        _report_live_start_runner_failure(
            project_root, "live_start_runner_output_invalid"
        )
        return
    if not isinstance(payload, dict) or payload.get("status") not in {
        "COLLECTING", "OBSERVATION_INVALID"
    }:
        _report_live_start_runner_failure(
            project_root, "live_start_runner_output_invalid"
        )
        return
    status = str(payload.get("status") or "OBSERVATION_INVALID")
    suffix = f" reason={payload['reason']}" if payload.get("reason") else ""
    print(f"[OMC-OBSERVATION] {status}{suffix}")

MUTATING_ROLE_IDS = {"directive", "senior_coding"}
READ_ONLY_COMMAND_HINTS = (
    "status",
    "state status",
    "omc status",
    "omc_guard status",
    "omc_pipeline_guard",
    "omc_tdd_check",
    "omc_doctor",
    "doctor",
    "help",
    "logs",
    "tail",
    "cat",
    "sed",
    "rg",
    "grep",
    "ls",
    "find",
)

DESTRUCTIVE_COMMAND_PATTERNS = (
    r"(^|\s)rm(\s|$)",
    r"rm\s+-[^\n]*\br\b[^\n]*\bf\b",
    r"find\s+[^\n]*\s-delete(\s|$)",
    r"(^|\s)git\s+reset\s+--hard(\s|$)",
)

ALLOW_DESTRUCTIVE_FLAG = "#ALLOW_DESTRUCTIVE"
_OBVIOUS_SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".go", ".java", ".js", ".jsx", ".kt", ".php",
    ".py", ".rb", ".rs", ".swift", ".ts", ".tsx",
}


def _obvious_source_changes(project_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError("work_class_scope_unavailable: git status failed")
    paths: list[str] = []
    entries = [entry for entry in result.stdout.split(b"\0") if entry]
    index = 0
    while index < len(entries):
        entry = entries[index].decode("utf-8", errors="surrogateescape")
        status = entry[:2]
        candidate_paths = [entry[3:]]
        if any(marker in {"R", "C"} for marker in status):
            index += 1
            if index < len(entries):
                candidate_paths.append(
                    entries[index].decode("utf-8", errors="surrogateescape")
                )
        paths.extend(
            path
            for path in candidate_paths
            if Path(path).suffix.casefold() in _OBVIOUS_SOURCE_SUFFIXES
        )
        index += 1
    return sorted(paths)


def _latest(project_root: Path) -> dict:
    return omc_state.read_latest(project_root)


def _policy(project_root: Path) -> dict:
    omc_state.init_state(project_root)
    return omc_state.read_policy(project_root)


def _confirmed_roles(latest: dict) -> set[str]:
    return {str(role).strip() for role in latest.get("latest_confirmed_roles", []) if str(role).strip()}


def _resolved_scope(command_name: str, requested_scope: str) -> str:
    if requested_scope != "auto":
        return requested_scope
    normalized = " ".join(command_name.lower().split())
    if normalized in READ_ONLY_COMMAND_HINTS:
        return "read"
    if any(normalized.startswith(f"{hint} ") for hint in READ_ONLY_COMMAND_HINTS):
        return "read"
    return "mutate"


def _has_mutating_role(roles: set[str]) -> bool:
    return bool(roles & MUTATING_ROLE_IDS)


def _is_destructive_command(command_name: str) -> bool:
    normalized = " ".join(command_name.lower().split())
    return any(re.search(pattern, normalized) for pattern in DESTRUCTIVE_COMMAND_PATTERNS)


def _run_tdd_check(project_root: Path, command_name: str) -> int:
    """TDD 체크 스크립트를 실행해 exit code를 반환한다."""
    import subprocess

    tdd_script = project_root / "scripts" / "omc_tdd_check.py"
    if not tdd_script.exists():
        return 0  # 스크립트 없으면 건너뜀

    # ship / git commit 은 staged 모드, 그 외 mutate 는 report-only (경고만)
    staged = command_name in ("git commit", "ship")
    args = ["python3", str(tdd_script)]
    if staged:
        args.append("--staged")
    else:
        args.append("--report-only")

    result = subprocess.run(args, cwd=str(project_root))
    return result.returncode


# 코드 변경을 수반하는 작업에서 TDD 체크를 강제로 실행할 커맨드 이름
_TDD_REQUIRED_COMMANDS = ("git commit", "ship", "deploy")


def _document_snapshot(root, paths):
    """Preserve the real index even while commit --only supplies a temporary one."""
    import omc_edit_resume as resume
    env = dict(os.environ);env.pop('GIT_INDEX_FILE',None)
    def git(*args):
        return subprocess.check_output(['git','--no-optional-locks',*args],cwd=root,env=env)
    tracked=set(git('ls-files','-z').decode().split('\0'))
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('document_scope_required')
    for name in paths:
        if (not isinstance(name,str) or name not in tracked or not name.startswith('docs/') or any(ord(c)<32 for c in name)
                or Path(name).suffix not in {'.md','.json','.html'}):
            raise ValueError('document_scope_invalid')
        resume.local(root,name)
    lines=git('ls-files','--stage').decode().splitlines()
    flags=git('ls-files','-v','-z').decode().split('\0')
    return {'head':git('rev-parse','HEAD').decode().strip(),
            'inventory':resume.inventory(root,paths),
            'index':[line for line in lines if line.split('\t',1)[1] not in paths],
            'flags':[line for line in flags if line and line[2:] not in paths]}


def _check_document_scope(root, command_name):
    import omc_edit_resume as resume
    latest=_latest(root);sid=latest.get('latest_confirmed_session_id')
    if not isinstance(sid,str) or not resume._session_identifier(sid):
        if latest.get('latest_skill') == 'omc-document-commit':
            raise ValueError('document_session_invalid')
        return
    path=root/'.omc/state/sessions'/sid/'document-scope.json'
    if not path.exists():
        if latest.get('latest_skill') == 'omc-document-commit':
            raise ValueError('document_scope_missing')
        return
    value=resume.read(root,path)
    if value.get('session_id') != sid or value.get('snapshot') != _document_snapshot(root,value['paths']):
        raise ValueError('document_preservation_changed')
    if command_name not in {'task','git commit'}:
        raise ValueError('document_command_not_allowed')
    if command_name == 'git commit':
        staged=subprocess.check_output(['git','--no-optional-locks','diff','--cached','--name-only','-z'],cwd=root).decode().split('\0')
        if not set(staged)-{''} or set(staged)-{''}-set(value['paths']):
            raise ValueError('document_commit_scope_mismatch: use git commit --only with approved paths')


def _frozen_task_guard(project_root, *, command_name, work_id=None):
    import omc_edit_resume as resume
    if not resume.frozen(project_root):
        return None
    if os.environ.get(resume.ENV):
        try:
            value=resume.load(project_root)
            if command_name != 'task' or (work_id is not None and work_id != value['binding']['work_id']):
                raise ValueError('resume_command_not_allowed')
            print('[OMC-GUARD] 제한 재개: task 범위만 허용; 기존 세션·승인·기한 보존')
            return 0
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            reason = 'resume_state_invalid' if isinstance(exc,AttributeError) else str(exc)
            print('[OMC-GUARD] 동결 재개 거부: '+reason)
            return 6
    diagnostic=resume.diagnose(project_root)
    print('[OMC-GUARD] 동결 상태: '+json.dumps(diagnostic,ensure_ascii=False))
    print('다음 행동: 상태가 expired이면 propose-edit-resume으로 범위와 해시를 확인한 뒤 명시 승인하세요. 상태 손상·부분 보관은 먼저 조사합니다.')
    return 6


def require_confirmation(project_root: Path, *, command_name: str, scope: str = "auto") -> int:
    if _is_destructive_command(command_name) and ALLOW_DESTRUCTIVE_FLAG not in command_name:
        print(
            f"[OMC-GUARD] blocked: destructive command detected in `{command_name}`."
        )
        print(
            "[OMC-GUARD] policy: delete must go through Trash-safe flow by default."
        )
        print(
            "Use: python omc_kit/scripts/safe_trash.py <path1> <path2> ..."
        )
        print(
            f"If you really need destructive delete, append {ALLOW_DESTRUCTIVE_FLAG} with explicit user approval."
        )
        return 5

    if _resolved_scope(command_name,scope) == 'mutate':
        frozen_result=_frozen_task_guard(project_root,command_name=command_name)
        if frozen_result is not None:
            return frozen_result
    try:
        _check_document_scope(project_root,command_name)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print('[OMC-GUARD] 문서 보존 범위 거부: '+str(exc))
        return 6
    policy = _policy(project_root)
    if not bool(policy.get("enforce_confirm", True)):
        print(f"[OMC-GUARD] bypassed by policy: `{command_name}`")
        return 0
    latest = _latest(project_root)
    latest_session_id = latest.get("latest_session_id")
    latest_confirmed_session_id = latest.get("latest_confirmed_session_id")
    if not latest_session_id:
        print(f"[OMC-GUARD] blocked: `{command_name}` requires an OMC-confirmed session first.")
        print("Run: ./run omc \"작업 요청\"")
        return 2
    if latest_session_id != latest_confirmed_session_id:
        try:
            omc_state.set_session_status(
                project_root,
                status="blocked",
                session_id=str(latest_session_id),
                reason=f"Blocked `{command_name}` until role confirmation is completed.",
            )
        except Exception:
            pass
        print(f"[OMC-GUARD] blocked: latest session `{latest_session_id}` is not confirmed.")
        print("Run: python scripts/omc.py state status")
        print("Then confirm by re-running `./run omc \"작업 요청\"` interactively or:")
        print("python scripts/omc.py state confirm --target .")
        return 3
    roles = _confirmed_roles(latest)
    resolved_scope = _resolved_scope(command_name, scope)
    local_commit_authorization = None
    if command_name == "git commit":
        local_commit_authorization = omc_state.authorize_pending_local_commit(
            project_root
        )
    if (
        resolved_scope == "mutate"
        and not _has_mutating_role(roles)
        and not (
            isinstance(local_commit_authorization, dict)
            and local_commit_authorization.get("authorized") is True
        )
    ):
        if isinstance(local_commit_authorization, dict):
            print(
                "[OMC-GUARD] local_commit receipt rejected: "
                f"{local_commit_authorization.get('reason')}"
            )
        required = ",".join(sorted(MUTATING_ROLE_IDS))
        found = ",".join(sorted(roles)) or "none"
        print(
            f"[OMC-GUARD] blocked: `{command_name}` requires a mutating role "
            f"for scope={resolved_scope}."
        )
        print(f"[OMC-GUARD] found roles={found}; required any of={required}")
        print("Run: ./run omc \"작업 요청\" and confirm roles including directive or senior_coding.")
        return 4
    if (
        isinstance(local_commit_authorization, dict)
        and local_commit_authorization.get("authorized") is True
    ):
        print(
            "[OMC-GUARD] local_commit "
            f"decision={local_commit_authorization.get('decision_id')} scope=staged"
        )
    print(
        f"[OMC-GUARD] confirmed: session={latest_confirmed_session_id} "
        f"roles={','.join(sorted(roles))} scope={resolved_scope}"
    )

    # ── TDD 체크 — ship / git commit / deploy 는 물리적으로 차단 ──────────
    if command_name in _TDD_REQUIRED_COMMANDS:
        tdd_rc = _run_tdd_check(project_root, command_name)
        if tdd_rc != 0:
            print()
            print("===========================================================")
            print(" TDD GATE BLOCK")
            print(f" `{command_name}` 실행 차단: 테스트 없는 구현 파일이 있습니다.")
            print()
            print(" ─── 복구 절차 ───────────────────────────────────────────")
            print(" 1. 빠진 테스트 확인:")
            print("      python3 scripts/omc_tdd_check.py --staged")
            print(" 2. 각 파일에 대응 테스트 파일 작성 → FAIL 확인")
            print(" 3. RED 등록:")
            print("      python3 scripts/omc_pipeline_guard.py red-done <테스트파일>")
            print(" 4. 테스트 GREEN 확인 후 재시도")
            print()
            print(" ─── 예외 허용 (보고만, 차단 없음) ──────────────────────")
            print("      python3 scripts/omc_tdd_check.py --staged --report-only")
            print("===========================================================")
            return 6

    return 0


def _isolated_environment():
    # Per-child only: do not edit the user's Git configuration or HOME.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    for key in ('PYTHONPATH', 'PYTHONHOME', 'OMC_EDIT_RESUME_CONTEXT'):
        env.pop(key, None)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM='1', GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    return env


def _isolated_run(argv, *, cwd, env, deadline, limit=15):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv, 0)
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          check=True, timeout=min(limit, remaining))


def _isolated_git(root, arguments, *, env, deadline, limit=15):
    return _isolated_run(['git', '-c', 'core.hooksPath=' + os.devnull,
                          '-c', 'core.fsmonitor=false', '-c', 'init.templateDir=',
                          '-C', str(root), *arguments], cwd=root, env=env,
                         deadline=deadline, limit=limit).stdout.strip()


def _isolated_paths(destination, paths, *, deadline):
    """Classify lexical aliases and physical directory scopes consistently."""
    blocked = []
    protected_directories = set()
    for parent, dirs, files in os.walk(destination, followlinks=False):
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired('isolated_path_scan', 0)
        if Path(parent) == destination:
            dirs[:] = [name for name in dirs if name not in {'.git', '.omc'}]
        for name in [*dirs, *files]:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('isolated_path_scan', 0)
            path = Path(parent) / name
            if path.is_symlink():
                blocked.append(path.relative_to(destination).as_posix())
                ancestor = path.parent
                while True:
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired('isolated_path_scan', 0)
                    info = ancestor.lstat()
                    protected_directories.add((info.st_dev, info.st_ino))
                    if ancestor == destination:
                        break
                    ancestor = ancestor.parent
    invalid = []
    for name in paths:
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired('isolated_path_scan', 0)
        path = Path(name)
        if (not name or path.is_absolute() or '..' in path.parts
                or any(part.casefold() in {'.git', '.omc'} for part in path.parts)):
            invalid.append(name)
            continue
        normalized = path.as_posix()
        if any(normalized == item or normalized.startswith(item + '/')
               or item.startswith(normalized.rstrip('/') + '/') or normalized == '.'
               for item in blocked):
            invalid.append(name)
            continue
        # lstat each existing component, stopping at a link before looking at
        # descendants. Do not resolve a link into source governance or .git.
        node = destination
        try:
            info = node.lstat()
            for part in path.parts:
                node = node / part
                info = node.lstat()
                if stat.S_ISLNK(info.st_mode):
                    invalid.append(name)
                    break
            else:
                if (stat.S_ISDIR(info.st_mode)
                        and (info.st_dev, info.st_ino) in protected_directories):
                    invalid.append(name)
        except FileNotFoundError:
            pass  # A new ordinary path is allowed once its ancestors are safe.
        except (OSError, ValueError, RuntimeError):
            invalid.append(name)
    return sorted(blocked), invalid


def _check_isolated_destination(source, destination, *, source_identity=None):
    """Use directory identities, not path spelling, for source containment."""
    current_source = source.stat()
    if source_identity is not None and not os.path.samestat(source_identity, current_source):
        raise ValueError('source_directory_changed')
    if any(path.is_symlink() for path in [destination, *destination.parents]):
        raise ValueError('destination_symlink')
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('destination_overlap')
    for ancestor in [destination, *destination.parents]:
        try:
            info = ancestor.stat()
        except FileNotFoundError:
            continue
        if os.path.samestat(current_source, info):
            raise ValueError('destination_overlap')
    if destination.exists():
        raise ValueError('destination_in_use')
    if not destination.parent.is_dir():
        raise ValueError('destination_parent_missing')
    return current_source


def _isolated_source_binding(hint, *, env, deadline):
    """Bind Git discovery to the worktree and both metadata directories."""
    def query(option):
        return _isolated_git(hint, ['rev-parse', option], env=env, deadline=deadline)
    bare = query('--is-bare-repository') == 'true'
    git_dir = Path(query('--absolute-git-dir')).resolve(strict=True)
    if bare:
        root = git_dir
    else:
        if query('--is-inside-work-tree') != 'true':
            raise ValueError('source_worktree_required')
        root = Path(query('--show-toplevel')).resolve(strict=True)
    common = Path(query('--git-common-dir'))
    common = (hint / common).resolve(strict=True)
    return root, git_dir, common


def start_isolated(args):
    """Start new work without consulting or locking the source OMC state.

    This creates a workspace, not an arbitrary-command sandbox. Setup and verify
    remain blocked until their execution boundary is independently established.
    """
    destination = Path(os.path.abspath(args.destination))
    result = {'workspace_route': 'ISOLATED', 'clone_path': str(destination),
              'source_governance': 'NOT_CHECKED', 'source_execution': 'NOT_CHECKED',
              'registration': 'not_started'}
    phase = 'prepare'
    try:
        if not math.isfinite(args.timeout_sec) or not 0 < args.timeout_sec <= 600:
            raise ValueError('invalid_timeout')
        if not args.request.strip():
            raise ValueError('request_required')
        deadline = time.monotonic() + args.timeout_sec
        source = args.source.resolve(strict=True)
        env = _isolated_environment()
        binding = _isolated_source_binding(source, env=env, deadline=deadline)
        source = binding[0]
        identities = [_check_isolated_destination(root, destination) for root in binding]
        result['source_root'] = str(source)
        baseline = args.baseline or 'HEAD'
        if args.baseline and not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', baseline):
            raise ValueError('baseline_oid_required')
        oid = _isolated_git(source, ['rev-parse', '--verify', baseline + '^{commit}'],
                            env=env, deadline=deadline)
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
            raise ValueError('source_oid_invalid')
        result['baseline_oid'] = oid
        # Exclusive reservation belongs to this entry; partial destinations are
        # retained on failure and never reset, reused, or automatically deleted.
        current_binding = _isolated_source_binding(source, env=env, deadline=deadline)
        for original, current, identity in zip(binding, current_binding, identities):
            if current != original:
                raise ValueError('source_directory_changed')
            _check_isolated_destination(current, destination, source_identity=identity)
        destination.mkdir(mode=0o700)
        _isolated_git(destination, ['clone', '--no-hardlinks', '--dissociate', '--no-checkout', '--',
                                   str(source), str(destination)],
                      env=env, deadline=deadline, limit=60)
        _isolated_git(destination, ['checkout', '--detach', oid], env=env, deadline=deadline)
        if not (destination / '.git').is_dir() or (destination / '.git').is_symlink():
            raise ValueError('destination_git_not_independent')
        common = _isolated_git(destination, ['rev-parse', '--git-common-dir'], env=env, deadline=deadline)
        if (destination / common).resolve() != destination / '.git':
            raise ValueError('destination_common_dir_shared')
        if _isolated_git(destination, ['rev-parse', 'HEAD'], env=env, deadline=deadline) != oid:
            raise ValueError('destination_head_mismatch')
        # Committed runtime metadata cannot be mistaken for fresh governance.
        omc = destination / '.omc'
        if omc.is_symlink() or any((omc / name).is_symlink() or (omc / name).exists()
                                  for name in ('state', 'pipeline_session.json', 'edit-resumptions',
                                               'observations', 'observation-policy.json', 'isolated-entry.json')):
            raise ValueError('destination_runtime_state_present')
        if any((omc / name).is_symlink() for name in
               ('project-memory.json', 'policy.json', 'notepad.md', 'summary.md', 'hooks.json')):
            raise ValueError('destination_runtime_state_present')
        if (destination / '.git/objects/info/alternates').exists():
            raise ValueError('destination_objects_shared')
        blocked, invalid = _isolated_paths(destination, args.write_path, deadline=deadline)
        result.update(blocked_edit_paths=blocked,
                      write_paths=[Path(name).as_posix() for name in args.write_path],
                      stages={
                          'READ': {'status': 'allowed', 'reason': 'clone_ready_no_external_link_traversal'},
                          'EDIT': {'status': 'blocked' if invalid else 'allowed' if args.write_path else 'needs_scope',
                                   'reason': 'unsafe_write_path' if invalid else 'contract_required', 'requires_contract': True},
                          'SETUP': {'status': 'blocked', 'reason': 'execution_boundary_not_attested'},
                          'VERIFY': {'status': 'blocked', 'reason': 'execution_boundary_not_attested'},
                      })
        phase = 'register'
        # Trusted external kit, never the clone's checked-out Python or hooks.
        _isolated_run([sys.executable, '-I', '-B', str(Path(__file__).resolve().with_name('omc_state.py')),
                       'init', '--target', str(destination)], cwd=destination, env=env,
                      deadline=deadline, limit=30)
        _isolated_run([
            sys.executable, '-I', '-B', str(Path(__file__).resolve()), 'sync-require',
            '--target', str(destination), '--mode', 'autopilot', '--title', 'omc-task',
            '--request', args.request, '--roles', 'senior_coding', '--work-class', 'implementation',
            '--completion-action', 'start', '--for', 'task'],
            cwd=destination, env=env, deadline=deadline, limit=30)
        pending = json.loads((omc / 'state/pending-completion.json').read_text())
        result.update(registration='started', session_id=pending['session_id'], work_id=pending['work_id'])
        with (omc / 'isolated-entry.json').open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except subprocess.TimeoutExpired:
        result.update(reason_code='preparation_timeout', failure_stage=phase)
        code = 2
    except FileExistsError:
        result.update(reason_code='destination_in_use', failure_stage=phase)
        code = 1
    except subprocess.CalledProcessError as exc:
        result.update(reason_code='registration_failed' if phase == 'register' else 'git_preparation_failed',
                      failure_stage=phase, native_exit=exc.returncode)
        code = 2
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result.update(reason_code=str(exc) if isinstance(exc, ValueError) else 'preparation_unavailable',
                      failure_stage=phase)
        code = 1 if isinstance(exc, ValueError) else 2
    print(json.dumps(result, ensure_ascii=False))
    return code


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Block execution unless the latest OMC session is confirmed.")
    sub = ap.add_subparsers(dest="command", required=True)

    isolated = sub.add_parser('start-isolated', help='Start independent work without reading source OMC state.')
    isolated.add_argument('--source', type=Path, required=True)
    isolated.add_argument('--destination', type=Path, required=True)
    isolated.add_argument('--request', required=True)
    isolated.add_argument('--baseline', help='Optional exact commit OID; defaults to the source HEAD.')
    isolated.add_argument('--write-path', action='append', default=[])
    isolated.add_argument('--timeout-sec', type=float, default=120)

    sync_require = sub.add_parser(
        "sync-require",
        help="Atomically sync a confirmed skill session and immediately run the guard.",
    )
    sync_require.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    sync_require.add_argument("--mode", required=True, help="OMC mode name.")
    sync_require.add_argument("--title", required=True, help="Mode title.")
    sync_require.add_argument("--request", required=True, help="Request text.")
    sync_require.add_argument("--roles", required=True, help="Comma-separated role ids.")
    sync_require.add_argument(
        "--work-class",
        choices=["implementation", "synthetic", "document_only", "benchmark_maintenance"],
    )
    sync_require.add_argument(
        "--completion-action",
        choices=["start", "continue", "preserve"],
        default="start",
    )
    sync_require.add_argument("--work-id")
    sync_require.add_argument("--document-path",action="append",help="Explicit existing document paths; preserve unrelated dirty/index state.")
    sync_require.add_argument(
        "--for",
        dest="command_name",
        required=True,
        help="Human-readable command name to guard after syncing.",
    )
    sync_require.add_argument(
        "--scope",
        choices=["auto", "read", "mutate"],
        default="auto",
        help="Guard level. auto treats known read-only commands as read and everything else as mutate.",
    )

    require = sub.add_parser("require", help="Require a confirmed latest OMC session.")
    require.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    require.add_argument("--for", dest="command_name", required=True, help="Human-readable command name.")
    require.add_argument(
        "--scope",
        choices=["auto", "read", "mutate"],
        default="auto",
        help="Guard level. auto treats known read-only commands as read and everything else as mutate.",
    )

    status = sub.add_parser("status", help="Show latest and confirmed session summary.")
    status.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    return ap


def main() -> int:
    args = _parser().parse_args()
    if args.command == 'start-isolated':
        return start_isolated(args)
    project_root = omc_utils.project_root(args.target)
    if args.command == "sync-require":
        role_ids = [x.strip() for x in args.roles.split(",") if x.strip()]
        if "senior_coding" in role_ids and args.work_class is None:
            raise ValueError("work class is required for a senior_coding session")
        document_snapshot = None
        if args.document_path:
            if (project_root/'.omc/state/pending-completion.json').exists():
                raise ValueError('document_requires_work_separation')
            if (role_ids != ['directive'] or args.work_class is not None
                    or args.completion_action != 'start' or args.command_name != 'task'):
                raise ValueError('document_session_scope_invalid')
            document_snapshot = _document_snapshot(project_root,args.document_path)
        if (
            args.work_class == "document_only"
            and args.completion_action == "start"
            and _obvious_source_changes(project_root)
        ):
            raise ValueError("work_class_scope_conflict: document_only includes source changes")
        import omc_edit_resume as resume
        if resume.frozen(project_root):
            if os.environ.get(resume.ENV) and (role_ids != ['senior_coding']
                    or args.work_class != 'implementation' or args.completion_action != 'continue'
                    or not args.work_id):
                print('[OMC-GUARD] 동결 재개 요청 범위 불일치')
                return 6
            return _frozen_task_guard(project_root,command_name=args.command_name,work_id=args.work_id)
        lock_private_key = (
            _prospective_work_class_lock(project_root)
            if "senior_coding" in role_ids
            else None
        )
        session = omc_state.record_session(
            project_root,
            mode=args.mode,
            title="omc-document-commit" if document_snapshot is not None else args.title,
            request=args.request,
            role_ids=role_ids,
            work_class=args.work_class,
            completion_action=args.completion_action,
            work_id=args.work_id,
            confirmed=lock_private_key is None,
            confirmation_source=(
                "guard.sync_require" if lock_private_key is None else None
            ),
        )
        if document_snapshot is not None:
            if document_snapshot != _document_snapshot(project_root,args.document_path):
                raise ValueError('document_preparation_changed')
            from omc_edit_resume import write_new
            write_new(project_root/'.omc/state/sessions'/session['session_id']/'document-scope.json',
                      {'session_id':session['session_id'],'paths':args.document_path,'snapshot':document_snapshot})
        if lock_private_key is not None:
            import omc_plan_candidate_universe as candidate_universe

            candidate_universe.seal_session_work_class_lock(
                project_root,
                session,
                lock_private_key,
            )
            omc_state.confirm_session(
                project_root,
                session_id=str(session["session_id"]),
            )
        guard_result = require_confirmation(
            project_root, command_name=args.command_name, scope=args.scope
        )
        if guard_result == 0 and (
            args.work_class == "implementation"
            and args.completion_action == "start"
        ):
            _start_registered_live_observation(project_root)
        return guard_result
    if args.command == "require":
        return require_confirmation(project_root, command_name=args.command_name, scope=args.scope)
    latest = _latest(project_root)
    policy = _policy(project_root)
    print(f"latest_session_id={latest.get('latest_session_id')}")
    print(f"latest_confirmed_session_id={latest.get('latest_confirmed_session_id')}")
    print(f"latest_confirmed_roles={','.join(latest.get('latest_confirmed_roles', []))}")
    print(f"active_run_id={latest.get('active_run_id')}")
    print(f"latest_run_id={latest.get('latest_run_id')}")
    print(f"enforce_confirm={policy.get('enforce_confirm', True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
