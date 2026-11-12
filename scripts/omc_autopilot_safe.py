#!/usr/bin/env python3
"""Isolated, scope-bound execution path for Autopilot candidates."""

from __future__ import annotations

import hashlib
import ast
import shlex
import json
import os
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import omc_autopilot_workspace as workspace
import omc_mission as mission


Runner = Callable[[Path, str], tuple[int, str]]
VERIFICATION_MAX_OUTPUT_BYTES = 1024 * 1024
_PYTEST_REPORT_PREFIX = 'OMC_PYTEST_REPORT:'
_PYTEST_REPORTER = '''import json
_reports = []
import os
from pathlib import Path
import pytest
@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    runtime = os.environ.get('OMC_PYTEST_RUNTIME')
    if runtime:
        root = Path(runtime).resolve()
        paths = [Path(session.config.option.basetemp).resolve()]
        if session.config.pluginmanager.hasplugin('cacheprovider'):
            paths.append((session.config.rootpath / session.config.getini('cache_dir')).resolve())
        if any(root not in path.parents for path in paths):
            pytest.exit('OMC_PYTEST_PATH_CONFLICT', returncode=4)
        print('OMC_PYTEST_PATHS:' + json.dumps([str(path) for path in paths]))
def pytest_runtest_logreport(report):
    _reports.append({'nodeid': report.nodeid, 'when': report.when, 'outcome': report.outcome})
def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line('OMC_PYTEST_REPORT:' + json.dumps(_reports, sort_keys=True))
'''


def _is_pytest_command(argv: list[str]) -> bool:
    return (Path(argv[0]).name in {'pytest', 'py.test'}
            or any(argv[i:i + 2] == ['-m', 'pytest'] for i in range(len(argv) - 1)))


def _readonly_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=root,
                          capture_output=True, text=True, check=True)


def _readonly_source_identity(root: Path) -> dict[str, str]:
    status = _readonly_git(root, 'status', '--porcelain=v1', '--untracked-files=all').stdout
    return dict(schema_version='omc-autopilot-source-identity/v1',
                head=_readonly_git(root,'rev-parse','HEAD').stdout.strip(),
                tree=_readonly_git(root,'rev-parse','HEAD^{tree}').stdout.strip(),
                status_sha256=_sha256_text(status), clean=str(not bool(status.strip())).lower())


def check_pytest_preparation(root: Path, commands: list[list[str]]) -> dict[str, Any]:
    """Read configuration without importing project conftest or plugins."""
    root = root.resolve()
    configs = []
    def result(status, reason, **extra):
        return dict(status=status, reason_code=reason, configs=configs, **extra)
    parse_errors = (ValueError, TypeError, IndexError, ImportError, subprocess.SubprocessError)
    try:
        from _pytest.config.exceptions import UsageError
        parse_errors += (UsageError,)
        from _pytest.config.findpaths import (
            get_common_ancestor, get_dirs_from_args, locate_config,
            load_config_dict_from_file,
        )
        for argv in commands:
            start = 1 if argv and Path(argv[0]).name in {'pytest', 'py.test'} else 3
            if start == 3 and not (len(argv) >= 3 and re.fullmatch(r'python(?:[0-9.]+)?', Path(argv[0]).name)
                                   and argv[1:3] == ['-m', 'pytest']):
                # -B is an interpreter flag, not a pytest option.
                if len(argv) >= 4 and re.fullmatch(r'python(?:[0-9.]+)?', Path(argv[0]).name) and argv[1:4] == ['-B', '-m', 'pytest']:
                    start = 4
                else:
                    return result('UNVERIFIED', 'verification_command_unsupported')
            args = argv[start:]
            explicit = None; positions = []; i = 0
            value_options = {'-k', '-m', '-p', '--tb', '--maxfail', '--deselect', '--ignore', '--ignore-glob', '--import-mode', '--color', '--capture', '--durations', '--verbosity'}
            flags = {'-q', '-qq', '-v', '-vv', '-s', '-x', '--disable-warnings', '--strict-markers', '--strict-config', '--collect-only', '--lf', '--ff'}
            while i < len(args):
                arg = args[i]
                if arg.startswith('@'):
                    return result('UNVERIFIED', 'pytest_response_file')
                if arg in {'-c', '--config-file'} or arg.startswith('--config-file='):
                    if '=' in arg: explicit = arg.split('=', 1)[1]
                    else: i += 1; explicit = args[i]
                elif arg == '-o' or arg == '--override-ini' or arg.startswith(('-o', '--override-ini=')):
                    value = arg.split('=',1)[1] if arg.startswith('--override-ini=') else arg[2:]
                    if not value: i += 1; value=args[i]
                    key=value.split('=',1)[0].strip()
                    if key == 'cache_dir': return result('BLOCKED','pytest_path_conflict', origin='argv', value=value)
                    if key == 'addopts': return result('UNVERIFIED','pytest_addopts_override')
                    return result('UNVERIFIED','pytest_override_unsupported')
                elif arg == '--basetemp' or arg.startswith('--basetemp='):
                    return result('BLOCKED','pytest_path_conflict',origin='argv',value=arg)
                elif arg in value_options: i += 1; _ = args[i]
                elif arg in flags: pass
                elif arg.startswith('-'):
                    if not any(arg.startswith(option+'=') for option in value_options if option.startswith('--')):
                        return result('UNVERIFIED','pytest_option_unsupported',value=arg)
                else: positions.append(arg.split('::',1)[0])
                i += 1
            if explicit:
                candidate=Path(explicit); candidate=candidate if candidate.is_absolute() else root/candidate
            else:
                # Match determine_setup without changing the parent's cwd or
                # constructing a Config (which can import project plugins).
                dirs = get_dirs_from_args([str(root / value) for value in positions]) or [root]
                ancestor = get_common_ancestor(dirs)
                selected_root, candidate, _ = locate_config([ancestor])
                if selected_root is None:
                    has_setup = any((path / 'setup.py').is_file()
                                    for path in (ancestor, *ancestor.parents))
                    if not has_setup and dirs != [ancestor]:
                        _, candidate, _ = locate_config(dirs)
            if candidate is None: continue
            candidate=Path(candidate)
            if any(x.is_symlink() for x in (candidate,*candidate.parents)):
                return result('UNVERIFIED','pytest_config_untrusted')
            candidate=candidate.resolve()
            if root not in candidate.parents: return result('UNVERIFIED','pytest_config_outside')
            relative=candidate.relative_to(root).as_posix()
            ignored=subprocess.run(['git','--no-optional-locks','check-ignore','--no-index','-q','--',relative],cwd=root).returncode
            if ignored == 0: return result('UNVERIFIED','pytest_config_ignored')
            try:
                original=subprocess.run(['git','--no-optional-locks','show','HEAD:'+relative],
                                        cwd=root,capture_output=True,check=True).stdout
            except subprocess.CalledProcessError: return result('UNVERIFIED','pytest_config_untracked')
            raw=candidate.read_bytes()
            if original != raw: return result('BLOCKED','pytest_config_changed',origin=relative)
            config=load_config_dict_from_file(candidate)
            if config is None:
                if candidate.name == 'pyproject.toml': config = {}
                else: return result('UNVERIFIED','pytest_config_unreadable')
            configs.append(dict(path=relative,sha256=hashlib.sha256(raw).hexdigest()))
            addopts=config.get('addopts', '')
            options=shlex.split(addopts) if isinstance(addopts,str) else list(addopts)
            if 'cache_dir' in config or any(x == '--basetemp' or x.startswith('--basetemp=') or 'cache_dir' in x for x in options):
                return result('BLOCKED','pytest_path_conflict',origin=relative,value=config)
            if any(x.startswith('@') or x in {'-c','--config-file','--rootdir','-o','--override-ini'} or x.startswith(('--config-file=','--rootdir=','--override-ini=')) for x in options):
                return result('UNVERIFIED','pytest_config_addopts_unsupported',origin=relative)
            j = 0
            while j < len(options):
                option = options[j]
                if option in value_options:
                    j += 1
                    if j >= len(options): return result('UNVERIFIED','pytest_config_addopts_unsupported',origin=relative)
                elif option not in flags and not any(option.startswith(name+'=') for name in value_options if name.startswith('--')):
                    return result('UNVERIFIED','pytest_config_addopts_unsupported',origin=relative,value=option)
                j += 1

        return result('READY_STATIC',None)
    except parse_errors as exc:
        return result('UNVERIFIED','pytest_config_unresolved',detail=str(exc))


def check_work_contract(source: Path, contract_path: Path, instruction: str, workspace_parent: Path) -> dict[str, Any]:
    """No approval consumption, clone, runtime creation, or repository writes."""
    source=source.resolve()
    def fail(reason): return dict(status='BLOCKED',reason_code=reason)
    try:
        contract=workspace.load_work_contract(contract_path)
        if _sha256_text(instruction) != contract['instruction_sha256']: return fail('instruction_identity_mismatch')
        preparation = check_pytest_preparation(source, contract['verification_commands'])
        if preparation['status'] != 'READY_STATIC': return preparation
        if _readonly_git(source,'rev-parse','HEAD').stdout.strip() != contract['base_commit']: return fail('source_identity_drift')
        identity=contract['source_identity']
        if _readonly_source_identity(source) != identity: return fail('source_identity_drift')
        packet=mission.load_mission_packet(contract_path.parent/contract['mission_packet_path'])
        if packet['packet_sha256'] != contract['mission_packet_sha256']: return fail('mission_packet_contract_mismatch')
        if packet['schema_version'] != mission.SINGLE_APPROVAL_SCHEMA_VERSION:
            return dict(status='UNVERIFIED',reason_code='single_approval_mission_required')
        receipt_path=contract_path.parent/contract['mission_approval_path']
        if receipt_path.is_symlink(): return fail('mission_approval_symlink')
        raw=receipt_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=contract['mission_approval_sha256']: return fail('mission_approval_contract_mismatch')
        receipt=json.loads(raw)
        mission.validate_mission_approval_receipt(receipt,packet=packet,session_id=contract['mission_approval_session_id'])
        policy=packet['execution']
        if packet['request_sha256']!=contract['instruction_sha256'] or packet['base_commit']!=contract['base_commit']: return fail('mission_identity_mismatch')
        if (contract['promotion_policy']!='isolated_candidate_only' or contract['pipeline_mode']!='lite'
            or contract['change_class']!='implementation' or contract['test_policy']!='required'
            or contract['allowed_operations']!=['modify'] or contract['allowed_paths']!=policy['allowed_paths']
            or contract['verification_commands']!=policy['verification_commands']): return fail('single_approval_scope_mismatch')
        if time.time()>=policy['approval_expires_epoch']: return fail('single_approval_expired')
        parent=workspace_parent.absolute()
        if any(x.is_symlink() for x in (parent,*parent.parents)): return fail('workspace_parent_symlink')
        if parent == source or source in parent.parents: return fail('workspace_parent_inside_source')
        if (parent/contract['run_id']).exists(): return fail('single_approval_already_started')
        common=Path(_readonly_git(source,'rev-parse','--git-common-dir').stdout.strip())
        common=common if common.is_absolute() else source/common
        key=_sha256_text(json.dumps({'session_id':contract['mission_approval_session_id'],'decision_id':receipt['decision_id']},sort_keys=True))
        claims=common/'omc-single-approval-claims'
        if claims.is_symlink(): return fail('approval_claims_untrusted')
        if (claims/(key+'.json')).exists(): return fail('single_approval_already_consumed')
        verifier=Path(policy['verifier_path'])
        if any(x.is_symlink() for x in (verifier,*verifier.parents)): return fail('independent_verifier_symlink')
        if source in verifier.resolve().parents or parent.resolve() in verifier.resolve().parents: return fail('independent_verifier_inside_workspace')
        if hashlib.sha256(verifier.read_bytes()).hexdigest()!=policy['verifier_sha256']: return fail('independent_verifier_mismatch')
        for relative in policy['allowed_paths']:
            path=source/relative
            if not path.is_file() or any(x.is_symlink() for x in (path,*path.parents)): return fail('single_approval_existing_file_required')
        return check_pytest_preparation(source,policy['verification_commands'])
    except OSError as exc:
        return dict(status='ERROR',reason_code='preparation_io_error',detail=str(exc))
    except (ValueError,KeyError,TypeError,workspace.AutopilotWorkspaceError,mission.MissionError,subprocess.SubprocessError) as exc:
        return fail(str(exc))


def _pytest_report(output: str) -> list[dict[str, str]] | None:
    lines = [line[len(_PYTEST_REPORT_PREFIX):] for line in output.splitlines()
             if line.startswith(_PYTEST_REPORT_PREFIX)]
    if len(lines) != 1:
        return None
    try:
        rows = json.loads(lines[0])
    except (ValueError, TypeError):
        return None
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or set(row) != {'nodeid', 'when', 'outcome'}
        or not isinstance(row['nodeid'], str) or not row['nodeid']
        or row['when'] not in {'setup', 'call', 'teardown'}
        or row['outcome'] not in {'passed', 'failed', 'skipped'} for row in rows
    ):
        return None
    return rows


def _pytest_cases(checks: list[dict[str, Any]], outcome: str) -> set[str]:
    return {row['nodeid'] for check in checks for row in check.get('pytest_report', [])
            if row['when'] == 'call' and row['outcome'] == outcome}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _terminal_verdict(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    match = re.fullmatch(r"VERDICT:\s*([A-Z ]+)", lines[-1])
    return match.group(1).strip() if match else ""


def _result(status: str, reason_code: str | None, **values: Any) -> dict[str, Any]:
    return {"status": status, "reason_code": reason_code, **values}


def _task_prompt(
    contract: dict[str, Any], instruction: str, briefing: dict[str, Any] | None = None
) -> str:
    test_rule = (
        "검증 명령에 필요한 테스트를 작성하세요."
        if contract["test_policy"] == "required"
        else "새 테스트 파일은 명시된 허용 경로에 포함된 경우에만 작성하세요."
    )
    return (
        "[OMC SAFE AUTOPILOT]\n"
        + (
            f"MISSION_BRIEFING: {json.dumps(briefing, ensure_ascii=False, sort_keys=True)}\n"
            if briefing is not None
            else ""
        )
        +
        f"지시문: {instruction}\n"
        f"허용 경로: {json.dumps(contract['allowed_paths'], ensure_ascii=False)}\n"
        f"허용 작업: {json.dumps(contract['allowed_operations'])}\n"
        f"변경 등급: {contract['change_class']}\n"
        f"{test_rule}\n"
        "허용 경로 밖 파일을 만들거나 수정하지 마세요.\n"
        "Git commit을 만들지 말고, 검증을 위해 staging했다면 종료 전에 index를 원복하세요.\n"
        "마지막 줄에 VERDICT: PROCEED 또는 VERDICT: BLOCK을 출력하세요."
    )


def _review_prompt(packet: dict[str, Any], briefing: dict[str, Any] | None = None) -> str:
    digest = str(packet["packet_sha256"])
    return (
        "아래 immutable review packet만 검토하세요. workspace를 수정하지 마세요.\n"
        + (
            f"MISSION_BRIEFING: {json.dumps(briefing, ensure_ascii=False, sort_keys=True)}\n"
            if briefing is not None
            else ""
        )
        +
        f"REVIEW_PACKET_SHA256: {digest}\n"
        f"{json.dumps(packet, ensure_ascii=False, sort_keys=True)}\n\n"
        f"마지막 두 줄에 정확히 REVIEW_PACKET_SHA256: {digest} 와 "
        "VERDICT: APPROVE 또는 VERDICT: BLOCK을 출력하세요."
    )


def _confined_verification_command(root: Path, argv: list[str], runtime: Path | None = None) -> list[str]:
    root = root.resolve()
    system = platform.system()
    if system == "Darwin":
        sandbox = shutil.which("sandbox-exec")
        if not sandbox:
            raise workspace.AutopilotWorkspaceError(
                "verification_confinement_unavailable"
            )
        root_literal = json.dumps(str(root))
        runtime_rule = f"(allow file-write* (subpath {json.dumps(str(runtime.resolve()))}))" if runtime else ""
        profile = "\n".join(
            (
                "(version 1)",
                "(allow default)",
                "(deny network*)",
                "(deny file-write*)",
                runtime_rule,
                f"(allow file-write* (subpath {root_literal}) (literal \"/dev/null\"))",
            )
        )
        return [sandbox, "-p", profile, "--", *argv]
    if system == "Linux":
        sandbox = shutil.which("bwrap")
        if not sandbox:
            raise workspace.AutopilotWorkspaceError(
                "verification_confinement_unavailable"
            )
        root_text = str(root)
        return [
            sandbox,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            root_text,
            root_text,
            *(["--bind", str(runtime.resolve()), str(runtime.resolve())] if runtime else []),
            "--chdir",
            root_text,
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--",
            *argv,
        ]
    raise workspace.AutopilotWorkspaceError("verification_confinement_unavailable")


def _verification_environment(runtime_root: Path) -> dict[str, str]:
    home = runtime_root / "home"
    temporary = runtime_root / "tmp"
    cache = runtime_root / "cache"
    for path in (home, temporary, cache):
        path.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(cache),
        "CI": "true",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "SYSTEMROOT"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _prepare_verification_runtime(root: Path) -> tuple[Path | None, str | None]:
    runtime_paths = [root / ".omc", root / ".omc" / "runs"]
    runtime_parent = runtime_paths[-1] / "verification-runtime"
    runtime_paths.append(runtime_parent)
    for path in runtime_paths:
        if path.is_symlink():
            return None, "verification_runtime_untrusted"
        try:
            path.mkdir(exist_ok=True)
            metadata = path.lstat()
        except FileExistsError:
            return None, "verification_runtime_untrusted"
        except OSError:
            return None, "verification_runtime_unavailable"
        if not stat.S_ISDIR(metadata.st_mode):
            return None, "verification_runtime_untrusted"
        current_uid = getattr(os, "getuid", lambda: metadata.st_uid)()
        if metadata.st_uid != current_uid:
            return None, "verification_runtime_untrusted"
        if stat.S_IMODE(metadata.st_mode) & 0o700 != 0o700:
            return None, "verification_runtime_untrusted"
    return runtime_parent, None


def _kill_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def _wait_after_termination(process: subprocess.Popen[Any]) -> bool:
    try:
        process.wait(timeout=1)
        return True
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
            return True
        except subprocess.TimeoutExpired:
            return False


def _capture_process_output(
    process: subprocess.Popen[bytes],
    *,
    deadline_monotonic: float,
    max_output_bytes: int,
) -> tuple[bytes, bytes, bool, bool, bool]:
    selector = selectors.DefaultSelector()
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    for name, stream in streams.items():
        if stream is None:
            continue
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, data=name)

    timed_out = False
    output_limit_exceeded = False
    try:
        while selector.get_map() or process.poll() is None:
            remaining_time = deadline_monotonic - time.monotonic()
            if remaining_time <= 0:
                timed_out = True
                _kill_process_group(process)
                break
            events = selector.select(timeout=min(0.05, remaining_time))
            for key, _mask in events:
                remaining_output = max_output_bytes + 1 - sum(
                    len(buffer) for buffer in buffers.values()
                )
                try:
                    chunk = os.read(key.fd, max(1, min(64 * 1024, remaining_output)))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[str(key.data)].extend(chunk)
                if sum(len(buffer) for buffer in buffers.values()) > max_output_bytes:
                    output_limit_exceeded = True
                    _kill_process_group(process)
                    break
            if output_limit_exceeded:
                break

        terminated = True
        if timed_out or output_limit_exceeded:
            terminated = _wait_after_termination(process)
        else:
            process.wait()
        return (
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
            timed_out,
            output_limit_exceeded,
            terminated,
        )
    finally:
        selector.close()
        for stream in streams.values():
            if stream is not None:
                stream.close()


def _run_verification(
    root: Path,
    commands: list[list[str]],
    *,
    deadline_monotonic: float,
    max_output_bytes: int = VERIFICATION_MAX_OUTPUT_BYTES,
    capture_output: bool = False,
    pytest_plugin: Path | None = None,
    external_runtime: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    receipts: list[dict[str, Any]] = []
    runtime_parent, runtime_error = (None, None) if external_runtime else _prepare_verification_runtime(root)
    if runtime_error or (runtime_parent is None and not external_runtime):
        return receipts, runtime_error or "verification_runtime_unavailable"
    for argv in commands:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": None,
                    "output_sha256": _sha256_text(""),
                    "timed_out": True,
                }
            )
            return receipts, "verification_timeout"
        try:
            command = _confined_verification_command(root, argv) if not external_runtime else argv
        except workspace.AutopilotWorkspaceError:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": None,
                    "output_sha256": _sha256_text(""),
                    "confinement_unavailable": True,
                }
            )
            return receipts, "verification_confinement_unavailable"
        with tempfile.TemporaryDirectory(
            prefix="command-", dir=runtime_parent
        ) as raw_runtime:
            runtime_root = Path(raw_runtime)
            environment = _verification_environment(runtime_root)
            if external_runtime:
                metadata = runtime_root.lstat()
                if (runtime_root.is_symlink() or metadata.st_uid != os.getuid()
                        or stat.S_IMODE(metadata.st_mode) != 0o700
                        or root.resolve() in runtime_root.resolve().parents):
                    return receipts, 'verification_runtime_untrusted'
                try:
                    command = _confined_verification_command(root, argv, runtime_root)
                except workspace.AutopilotWorkspaceError:
                    return receipts, 'verification_confinement_unavailable'
                if _is_pytest_command(argv):
                    environment['PYTEST_ADDOPTS'] = shlex.join(['-o', 'cache_dir='+str(runtime_root/'cache/pytest'), '--basetemp='+str(runtime_root/'tmp/pytest')])
                    environment['OMC_PYTEST_RUNTIME'] = str(runtime_root)

            report_required = pytest_plugin is not None and _is_pytest_command(argv)
            if report_required:
                environment.update(PYTEST_PLUGINS=pytest_plugin.stem,
                                   PYTHONPATH=str(pytest_plugin.parent),
                                   PYTHONDONTWRITEBYTECODE='1')
            try:
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    start_new_session=True,
                )
                (
                    stdout_bytes,
                    stderr_bytes,
                    timed_out,
                    output_limit_exceeded,
                    terminated,
                ) = _capture_process_output(
                    process,
                    deadline_monotonic=time.monotonic() + remaining,
                    max_output_bytes=max_output_bytes,
                )
            except OSError:
                receipts.append(
                    {
                        "argv": argv,
                        "exit_code": None,
                        "output_sha256": _sha256_text(""),
                        "start_failed": True,
                    }
                )
                return receipts, "verification_start_failed"
            if external_runtime:
                _kill_process_group(process)
            if not terminated:
                receipts.append(
                    {
                        "argv": argv,
                        "exit_code": None,
                        "output_sha256": _sha256_text(""),
                        "termination_failed": True,
                    }
                )
                return receipts, "verification_termination_failed"
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            output = stdout + stderr
        if timed_out:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": None,
                    "output_sha256": _sha256_text(stdout + stderr),
                    "timed_out": True,
                    **({"runtime_root": str(runtime_root)} if external_runtime else {}),
                }
            )
            return receipts, "verification_timeout"
        if output_limit_exceeded:
            receipts.append(
                {
                    "argv": argv,
                    "exit_code": process.returncode,
                    "output_sha256": _sha256_text(output),
                    "output_limit_exceeded": True,
                }
            )
            return receipts, "verification_output_limit_exceeded"
        receipts.append(
            {
                "argv": argv,
                "exit_code": process.returncode,
                **({"runtime_root": str(runtime_root), "injected_environment": environment} if external_runtime else {}),
                "output_sha256": _sha256_text(output),
                **({'output': output} if capture_output else {}),
            }
        )
        if report_required:
            if 'OMC_PYTEST_PATH_CONFLICT' in output:
                return receipts, 'pytest_effective_path_conflict'
            rows = _pytest_report(stdout)
            if rows is None:
                return receipts, 'pytest_report_missing_or_invalid'
            receipts[-1]['pytest_report'] = rows
            paths = [line.partition(':')[2] for line in stdout.splitlines() if line.startswith('OMC_PYTEST_PATHS:')]
            if external_runtime:
                if len(paths) != 1:
                    return receipts, 'pytest_effective_paths_missing'
                receipts[-1]['effective_paths'] = json.loads(paths[0])
            if process.returncode not in {0, 1}:
                return receipts, 'pytest_execution_failed'
        if process.returncode != 0:
            return receipts, "verification_failed"
    return receipts, None


def _write_immutable_packet(path: Path, packet: dict[str, Any]) -> None:
    data = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_already_exists") from exc
    except OSError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_unwritable") from exc
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise workspace.AutopilotWorkspaceError("review_packet_write_incomplete")
            offset += written
        os.fsync(fd)
    except OSError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_write_failed") from exc
    finally:
        os.close(fd)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise workspace.AutopilotWorkspaceError("review_packet_sync_failed") from exc


def _run_candidate_git(
    root: Path,
    *args: str,
    capture_output: bool = False,
    text: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[Any]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=capture_output,
            text=text,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise workspace.AutopilotWorkspaceError("candidate_git_command_failed") from exc
    if check and result.returncode != 0:
        raise workspace.AutopilotWorkspaceError("candidate_git_command_failed")
    return result


def _commit_candidate(root: Path, delta: list[dict[str, Any]]) -> str:
    _run_candidate_git(root, "config", "user.email", "omc-autopilot@example.invalid")
    _run_candidate_git(root, "config", "user.name", "OMC Autopilot")
    paths = sorted({str(item["path"]) for item in delta})
    if not paths:
        raise workspace.AutopilotWorkspaceError("candidate_has_no_changes")
    _run_candidate_git(root, "add", "-A", "--", *paths)
    staged_output = _run_candidate_git(
        root, "diff", "--cached", "--name-only", "-z", capture_output=True
    ).stdout
    try:
        staged_paths = staged_output.decode("utf-8").rstrip("\0").split("\0")
    except UnicodeDecodeError as exc:
        raise workspace.AutopilotWorkspaceError("candidate_staged_scope_mismatch") from exc
    if sorted(path for path in staged_paths if path) != paths:
        raise workspace.AutopilotWorkspaceError("candidate_staged_scope_mismatch")
    staged = _run_candidate_git(root, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        raise workspace.AutopilotWorkspaceError("candidate_has_no_changes")
    if staged.returncode != 1:
        raise workspace.AutopilotWorkspaceError("candidate_staged_diff_invalid")
    _run_candidate_git(
        root,
        "-c",
        "commit.gpgSign=false",
        "commit",
        "--no-verify",
        "-qm",
        "OMC Autopilot candidate",
    )
    return _run_candidate_git(root, "rev-parse", "HEAD", capture_output=True, text=True).stdout.strip()


def _terminal_packet_verdict(output: str, packet_digest: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    evidence_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith("REVIEW_PACKET_SHA256:") or line.startswith("VERDICT:")
    ]
    if evidence_indexes != [len(lines) - 2, len(lines) - 1]:
        return None
    if lines[-2] != f"REVIEW_PACKET_SHA256: {packet_digest}":
        return None

    verdict_match = re.fullmatch(r"VERDICT:\s*([A-Z][A-Z ]*)", lines[-1])
    return verdict_match.group(1).strip() if verdict_match else None


def _run_readonly_stage(
    *, root: Path, prompt: str, runner: Runner, packet_digest: str, stage: str,
    capture_output: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    before = workspace.snapshot_workspace(root)
    git_control_before = (
        workspace.snapshot_git_control_plane(root)
        if (root / ".git").is_dir()
        else None
    )
    rc, output = runner(root, prompt)
    after = workspace.snapshot_workspace(root)
    if git_control_before is not None:
        try:
            workspace.validate_git_control_plane(root, git_control_before)
        except workspace.AutopilotWorkspaceError:
            return None, f"{stage}_side_effect_detected"
    if before != after:
        return None, f"{stage}_side_effect_detected"
    if rc != 0:
        return None, f"{stage}_failed"
    verdict = _terminal_packet_verdict(output, packet_digest)
    if verdict is None:
        return None, f"{stage}_evidence_unbound"
    return {"verdict": verdict, "output_sha256": _sha256_text(output),
            **({'output': output} if capture_output else {})}, None


def _run_single_approval(
    *, source: Path, contract: dict[str, Any], packet: dict[str, Any],
    approval: dict[str, Any], instruction: str, task_runner: Runner,
    review_runner: Runner, workspace_parent: Path,
    deadline_monotonic: float | None,
) -> dict[str, Any]:
    """One implementation workspace, bounded repairs, independent Python oracle.

    This is opt-in through a v2 mission. Legacy execution and approval rules
    are unchanged. Governance is written by the parent, never by the executor.
    """
    import contextlib
    import io
    import omc_state
    import omc_pipeline_guard as gate

    try:
        policy = mission.validate_single_execution(packet['execution'])
    except (KeyError, TypeError, mission.MissionError) as exc:
        return _result('blocked', str(exc), completed_stages=[])
    started = time.monotonic()
    remaining_approval = policy['approval_expires_epoch'] - time.time()
    deadline = min(started + policy['max_time_sec'], started + remaining_approval,
                   deadline_monotonic if deadline_monotonic is not None else float('inf'))
    # Leave bounded time for capturing the terminal result and cleanup.
    execute_until = deadline - min(2.0, policy['max_time_sec'] / 10)
    stages: list[str] = []
    attempts: list[dict[str, Any]] = []
    isolated = None
    run_root = None

    def finish(status, reason, **values):
        result = _result(status, reason, completed_stages=list(stages),
                         attempts=list(attempts), isolated_workspace=str(isolated) if isolated else None,
                         **values)
        if run_root is not None:
            try:
                _write_immutable_packet(run_root / 'result.json', result)
            except workspace.AutopilotWorkspaceError:
                return _result('blocked', 'result_delivery_failed', completed_stages=list(stages),
                               isolated_workspace=str(isolated))
        return result

    def check_time():
        if time.monotonic() >= execute_until or time.time() >= policy['approval_expires_epoch']:
            raise workspace.AutopilotWorkspaceError('single_approval_deadline_exceeded')

    def guarded_call(function, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            code = function(*args, **kwargs)
        if code:
            raise workspace.AutopilotWorkspaceError('single_approval_registration_failed')

    def governance():
        return {str(p.relative_to(isolated)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (isolated / '.omc').rglob('*')
                if p.is_file() and p != isolated / '.omc/cost_log.jsonl'}

    def oracle_original():
        verifier = Path(policy['verifier_path'])
        # Refuse symlink components and all mutable candidate locations.
        if any(p.is_symlink() for p in (verifier, *verifier.parents)):
            raise workspace.AutopilotWorkspaceError('independent_verifier_symlink')
        if not verifier.is_file():
            raise workspace.AutopilotWorkspaceError('independent_verifier_mismatch')
        raw = verifier.read_bytes()
        if hashlib.sha256(raw).hexdigest() != policy['verifier_sha256']:
            raise workspace.AutopilotWorkspaceError('independent_verifier_mismatch')
        if verifier == source or source in verifier.parents or workspace_parent.resolve() in verifier.parents:
            raise workspace.AutopilotWorkspaceError('independent_verifier_inside_workspace')
        return raw

    try:
        check_time()
        if (contract['promotion_policy'] != 'isolated_candidate_only'
                or contract['change_class'] != 'implementation'
                or contract['pipeline_mode'] != 'lite'
                or contract['test_policy'] != 'required'
                or contract['allowed_operations'] != ['modify']
                or contract['allowed_paths'] != policy['allowed_paths']
                or contract['verification_commands'] != policy['verification_commands']):
            return finish('blocked', 'single_approval_scope_mismatch')
        verifier_bytes = oracle_original()
        if workspace_parent.is_symlink():
            return finish('blocked', 'workspace_parent_symlink')
        workspace_parent.mkdir(parents=True, exist_ok=True)
        claimed = workspace_parent / contract['run_id']
        try:
            claimed.mkdir(mode=0o700)
        except FileExistsError:
            return finish('blocked', 'single_approval_already_started')
        run_root = claimed
        os.chmod(run_root, 0o700)
        # A run name or runtime directory is not the approval identity. Store
        # consumption in the repository's common Git metadata so worktrees and
        # alternate run roots cannot reset the same approval's execution budget.
        common = Path(workspace._git(source, 'rev-parse', '--git-common-dir').stdout.strip())
        common = (common if common.is_absolute() else source / common).resolve()
        claims = common / 'omc-single-approval-claims'
        if claims.is_symlink():
            return finish('blocked', 'approval_claims_untrusted')
        claims.mkdir(mode=0o700, exist_ok=True)
        approval_key = _sha256_text(json.dumps({
            'session_id': contract['mission_approval_session_id'],
            'decision_id': approval['decision_id'],
        }, sort_keys=True))
        try:
            _write_immutable_packet(claims / (approval_key + '.json'), {
                'schema_version': 'omc-single-approval-consumed/v1',
                'approval_session_id': contract['mission_approval_session_id'],
                'decision_id': approval['decision_id'],
                'mission_packet_sha256': packet['packet_sha256'],
                'receipt_sha256': approval['receipt_sha256'],
                'run_id': contract['run_id'], 'run_root': str(run_root),
            })
        except workspace.AutopilotWorkspaceError as exc:
            if str(exc) == 'review_packet_already_exists':
                return finish('blocked', 'single_approval_already_consumed')
            raise
        pytest_plugin = run_root / ('omc_pytest_probe_' + contract['contract_sha256'][:16] + '.py')
        pytest_plugin.write_text(_PYTEST_REPORTER)
        os.chmod(pytest_plugin, 0o400)
        reporter_sha256 = _sha256_text(_PYTEST_REPORTER)

        def verify(root, commands):
            if hashlib.sha256(pytest_plugin.read_bytes()).hexdigest() != reporter_sha256:
                raise workspace.AutopilotWorkspaceError('pytest_reporter_changed')
            checks, error = _run_verification(root, commands,
                deadline_monotonic=execute_until, capture_output=True, pytest_plugin=pytest_plugin, external_runtime=True)
            if hashlib.sha256(pytest_plugin.read_bytes()).hexdigest() != reporter_sha256:
                raise workspace.AutopilotWorkspaceError('pytest_reporter_changed')
            return checks, error

        oracle = run_root / 'verifier.py'
        oracle.write_bytes(verifier_bytes)
        os.chmod(oracle, 0o400)
        isolated = workspace.materialize_isolated_clone(source, run_root / 'implementation', contract)
        for relative in policy['allowed_paths']:
            path = isolated / relative
            if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
                raise workspace.AutopilotWorkspaceError('single_approval_existing_file_required')
        control = workspace.snapshot_git_control_plane(isolated)
        before = workspace.snapshot_workspace(isolated)
        def test_nodes():
            nodes = {}
            for relative in policy['test_paths']:
                tree = ast.parse((isolated / relative).read_text())
                def collect(statements, prefix=()):
                    for node in statements:
                        if isinstance(node, ast.ClassDef):
                            collect(node.body, (*prefix, node.name))
                        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                            key = (relative, *prefix, node.name)
                            if key in nodes:
                                raise workspace.AutopilotWorkspaceError('duplicate_test_name')
                            nodes[key] = ast.dump(node, include_attributes=False)
                collect(tree.body)
            return nodes
        retained_tests = test_nodes()

        def check_retained_tests():
            current = test_nodes()
            if any(current.get(key) != value for key, value in retained_tests.items()):
                raise workspace.AutopilotWorkspaceError('baseline_tests_changed')
        with contextlib.redirect_stdout(io.StringIO()):
            omc_state.init_state(isolated)
            session = omc_state.record_session(
                isolated, mode='autopilot', title='single approved work', request=instruction,
                role_ids=['senior_coding'], confirmed=True, work_class='implementation',
                completion_action='start')
        # Approval session is the authorizing context; execution session is new.
        _write_immutable_packet(run_root / 'approval-link.json', {
            'approval_session_id': contract['mission_approval_session_id'],
            'execution_session_id': session['session_id'], 'work_id': session['work_id'],
            'mission_packet_sha256': packet['packet_sha256'],
            'work_contract_sha256': contract['contract_sha256'],
            'approval_receipt_sha256': approval['receipt_sha256'],
        })
        metadata = isolated / '.omc/state/single-approval'
        metadata.mkdir(parents=True)
        methods = {'tests': json.dumps(policy['verification_commands']),
                   'oracle': policy['verifier_sha256'], 'review': packet['packet_sha256']}
        checks = [{'id': name, 'target': ','.join(policy['allowed_paths']),
                   'method': methods[name], 'pass_condition': 'passed', 'target_version': contract['base_commit']}
                  for name in ('tests', 'oracle', 'review')]
        proposal = metadata / 'checks.json'
        proposal.write_text(json.dumps({'session_id': session['session_id'], 'revision': 1,
            'request': instruction, 'approval_reference': approval['decision_id'], 'checks': checks}))
        guarded_call(gate.cmd_approve_checks, isolated, proposal)
        guarded_call(gate.cmd_contract_done, isolated, required_checks='tests,oracle,review')
        governance_expected = governance()
        baseline_workspace = workspace.snapshot_workspace(isolated)
        # Approved commands must work at baseline; pre-existing failures are not RED.
        baseline, error = verify(isolated, policy['verification_commands'])
        _write_immutable_packet(run_root / 'baseline.json', {'checks': baseline, 'error': error})
        if error:
            return finish('blocked', 'baseline_' + error)
        required_cases = _pytest_cases(baseline, 'passed')
        if not required_cases:
            return finish('blocked', 'baseline_no_passed_tests')
        if workspace.snapshot_workspace(isolated) != baseline_workspace or governance() != governance_expected:
            return finish('blocked', 'baseline_side_effect_detected')
        workspace.validate_git_control_plane(isolated, control)
        red_prompt = (_task_prompt(contract, instruction, mission.build_stage_briefing(packet, stage='task'))
                      + '\n[OMC RED ONLY] 기존 테스트 파일에 요구사항 재현 테스트만 추가하세요. 구현 파일·검증 기준·내부 기록은 수정하지 마세요. 검증은 부모가 실행하므로 테스트를 직접 실행하지 마세요. 캐시 등 새 파일도 만들지 마세요.')
        check_time()
        rc, output = task_runner(isolated, red_prompt)
        _write_immutable_packet(run_root / 'red-task.json', {'returncode': rc, 'output': output})
        if rc or _terminal_verdict(output) != 'PROCEED':
            return finish('blocked', 'red_task_failed')
        workspace.validate_git_control_plane(isolated, control)
        if governance() != governance_expected:
            return finish('blocked', 'governance_changed')
        check_retained_tests()
        red_delta = workspace.compute_workspace_delta(before, workspace.snapshot_workspace(isolated))
        # Parent-owned governance was not in the baseline snapshot.
        red_delta = [d for d in red_delta if not d['path'].startswith('.omc/')]
        if not red_delta or any(d['path'] not in policy['test_paths'] or d['operation'] != 'modify' for d in red_delta):
            return finish('blocked', 'red_scope_violation')
        red_snapshot = workspace.snapshot_workspace(isolated)
        red_checks, red_error = verify(isolated, policy['verification_commands'])
        if workspace.snapshot_workspace(isolated) != red_snapshot:
            return finish('blocked', 'red_verification_side_effect_detected')
        if governance() != governance_expected:
            return finish('blocked', 'governance_changed')
        workspace.validate_git_control_plane(isolated, control)
        red_output = '\n'.join(c.get('output', '') for c in red_checks)
        failed = sorted(_pytest_cases(red_checks, 'failed'))
        if red_error != 'verification_failed' or policy['red_case_id'] not in failed or red_checks[-1].get('exit_code') != 1:
            return finish('blocked', 'red_evidence_missing')
        state = gate._load_state(isolated)
        red_file = metadata / 'red.txt'
        red_file.write_text(red_output)
        test_file = policy['red_case_id'].split('::')[0]
        evidence = {'session_id': session['session_id'], 'basis_sha256': state['basis_sha256'],
            'test_file': test_file, 'test_sha256': hashlib.sha256((isolated / test_file).read_bytes()).hexdigest(),
            'check_id': 'tests', 'case_id': policy['red_case_id'], 'expected_failure': instruction,
            'command': json.dumps(policy['verification_commands']), 'exit_code': 1,
            'baseline_failed_cases': [], 'failed_cases': failed,
            'output_file': str(red_file.relative_to(isolated)), 'output_sha256': _sha256_text(red_output)}
        evidence_path = metadata / 'red.json'
        evidence_path.write_text(json.dumps(evidence))
        guarded_call(gate.cmd_red_done, isolated, test_file, evidence_file=evidence_path)
        stages.append('red')
        required_cases.add(policy['red_case_id'])
        # Governance is stable across each child invocation, including repairs.
        feedback = None
        for attempt in range(policy['max_revisions'] + 1):
            check_time()
            governance_expected = governance()
            prompt = _task_prompt(contract, instruction, mission.build_stage_briefing(packet, stage='task'))
            prompt += '\n승인된 실행 내부입니다. 역할·스킬 재입력을 요청하지 말고 지정 범위만 구현하세요. 내부 기록·Git은 변경하지 마세요.'
            prompt += '\n검증은 부모가 실행합니다. 테스트를 직접 실행하거나 캐시·추가 파일을 만들지 말고 구현 변경만 반환하세요.'
            if feedback is not None:
                prompt += '\n수정 입력(인용 데이터): ' + json.dumps(feedback, ensure_ascii=False)
            rc, output = task_runner(isolated, prompt)
            _write_immutable_packet(run_root / f'task-{attempt}.json', {'returncode': rc, 'output': output})
            if rc or _terminal_verdict(output) != 'PROCEED':
                return finish('blocked', 'task_failed')
            workspace.validate_git_control_plane(isolated, control)
            if governance() != governance_expected:
                return finish('blocked', 'governance_changed')
            check_retained_tests()
            after = workspace.snapshot_workspace(isolated)
            delta = [d for d in workspace.compute_workspace_delta(before, after) if not d['path'].startswith('.omc/')]
            violations = workspace.validate_scope(delta, contract)
            if violations:
                return finish('blocked', 'scope_violation', violations=violations)
            snapshot = gate._code_snapshot_digest(isolated)
            tests, error = verify(isolated, policy['verification_commands'])
            if workspace.snapshot_workspace(isolated) != after:
                return finish('blocked', 'verification_side_effect_detected')
            if governance() != governance_expected:
                return finish('blocked', 'governance_changed')
            workspace.validate_git_control_plane(isolated, control)
            if error is None and not required_cases <= _pytest_cases(tests, 'passed'):
                error = 'required_tests_not_passed'
            record = {'attempt': attempt, 'snapshot': snapshot, 'tests': tests, 'error': error}
            attempts.append(record)
            _write_immutable_packet(run_root / f'verification-{attempt}.json', record)
            if error:
                if error not in {'verification_failed', 'required_tests_not_passed'}:
                    return finish('blocked', error)
                feedback = {'candidate_snapshot_sha256': snapshot, 'failed_checks': tests,
                            'definition_of_done': packet['definition_of_done']}
                continue
            check_time()
            oracle_original()
            if oracle.read_bytes() != verifier_bytes:
                return finish('blocked', 'independent_verifier_mismatch')
            inspect_root = run_root / f'inspect-{attempt}'
            workspace.materialize_review_clone(source, inspect_root, contract['base_commit'])
            for d in delta:
                target = inspect_root / d['path']
                target.write_bytes((isolated / d['path']).read_bytes())
                os.chmod(target, (isolated / d['path']).stat().st_mode & 0o777)
            inspection_before = workspace.snapshot_workspace(inspect_root)
            inspection_git = workspace.snapshot_git_control_plane(inspect_root)
            oracle_checks, oracle_error = _run_verification(inspect_root,
                [[sys.executable, '-B', str(oracle), str(inspect_root)]],
                deadline_monotonic=execute_until, capture_output=True, external_runtime=True)
            oracle_original()
            if oracle.read_bytes() != verifier_bytes:
                return finish('blocked', 'independent_verifier_mismatch')
            if workspace.snapshot_workspace(inspect_root) != inspection_before:
                return finish('blocked', 'independent_verifier_side_effect')
            workspace.validate_git_control_plane(inspect_root, inspection_git)
            _write_immutable_packet(run_root / f'oracle-{attempt}.json', {'checks': oracle_checks, 'error': oracle_error})
            if oracle_error:
                if oracle_error != 'verification_failed':
                    return finish('blocked', oracle_error)
                feedback = {'candidate_snapshot_sha256': snapshot, 'independent_checks': oracle_checks,
                            'definition_of_done': packet['definition_of_done']}
                continue
            check_time()
            review_packet = {'schema_version': 'omc-single-review/v1', 'base_commit': contract['base_commit'],
                'candidate_snapshot_sha256': snapshot, 'delta': delta,
                'patch': workspace._git(isolated, 'diff', '--binary', 'HEAD').stdout,
                'tests': tests, 'oracle': oracle_checks, 'mission_packet_sha256': packet['packet_sha256']}
            review_digest = hashlib.sha256(json.dumps(review_packet, sort_keys=True).encode()).hexdigest()
            review_packet['packet_sha256'] = review_digest
            _write_immutable_packet(run_root / f'review-{attempt}.json', review_packet)
            prompt = _review_prompt(review_packet, mission.build_stage_briefing(packet, stage='review'))
            prompt = prompt.replace('VERDICT: APPROVE 또는 VERDICT: BLOCK', 'VERDICT: APPROVE 또는 VERDICT: REVISE 또는 VERDICT: BLOCK')
            review, error = _run_readonly_stage(root=inspect_root, prompt=prompt, runner=review_runner,
                                               packet_digest=review_digest, stage='review', capture_output=True)
            if error or review is None:
                return finish('blocked', error or 'review_failed')
            oracle_original()
            if oracle.read_bytes() != verifier_bytes:
                return finish('blocked', 'independent_verifier_mismatch')
            _write_immutable_packet(run_root / f'review-result-{attempt}.json', review)
            if review['verdict'] == 'REVISE':
                feedback = {'candidate_snapshot_sha256': snapshot, 'review': review,
                            'definition_of_done': packet['definition_of_done']}
                continue
            if review['verdict'] != 'APPROVE':
                return finish('blocked', 'review_rejected')
            check_time()
            if gate._code_snapshot_digest(isolated) != snapshot:
                return finish('blocked', 'candidate_changed_after_review')
            workspace.validate_git_control_plane(isolated, control)
            _run_candidate_git(isolated, 'add', '--', *policy['allowed_paths'])
            guarded_call(gate.cmd_verification_template, isolated)
            state = gate._load_state(isolated)
            basis = gate._load_approved_basis(isolated, session['session_id'])
            verification = {'session_id': session['session_id'], 'basis_sha256': state['basis_sha256'],
                'implementation': {'status': 'applied', 'evidence': snapshot},
                'checks': {c['id']: {'status': 'passed', 'evidence': str(run_root),
                    'definition_sha256': gate._check_digest(c), 'code_snapshot_sha256': snapshot} for c in basis['checks']}}
            verification_file = metadata / 'verification.json'
            verification_file.write_text(json.dumps(verification))
            evaluation = gate._evaluate_implementation_completion(isolated, verification_file)
            _write_immutable_packet(run_root / 'completion-gate.json', evaluation)
            if evaluation['outcome'] != 'done':
                return finish('blocked', 'completion_gate_failed')
            stages.extend(['task', 'verification', 'independent_verification', 'review', 'completion_gate'])
            candidate = _commit_candidate(isolated, delta)
            workspace.validate_candidate_scope(isolated=isolated, contract=contract,
                                               candidate_commit=candidate, expected_delta=delta)
            if gate._code_snapshot_digest(isolated) != snapshot:
                return finish('blocked', 'candidate_commit_mismatch')
            if _readonly_source_identity(source) != contract['source_identity']:
                return finish('blocked', 'source_identity_drift')
            if time.monotonic() >= deadline:
                return finish('blocked', 'single_approval_deadline_exceeded')
            stages.append('candidate')
            return finish('candidate_ready', None, candidate_commit=candidate,
                          candidate_snapshot_sha256=snapshot, source_modified=False)
        return finish('blocked', 'revision_budget_exhausted')
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, workspace.AutopilotWorkspaceError,
            mission.MissionError, subprocess.SubprocessError) as exc:
        return finish('blocked', str(exc))


def run_safe_pipeline(
    *,
    source: Path,
    contract_path: Path,
    instruction: str,
    task_runner: Runner,
    review_runner: Runner,
    workspace_parent: Path,
    critique_runner: Runner | None = None,
    deadline_monotonic: float | None = None,
    expected_contract_sha256: str | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    contract = workspace.load_work_contract(contract_path)
    if expected_contract_sha256 is not None and contract['contract_sha256'] != expected_contract_sha256:
        return _result('blocked', 'work_contract_changed_before_execution', completed_stages=[])
    completed_stages: list[str] = []

    def finish(status: str, reason_code: str | None, **values: Any) -> dict[str, Any]:
        return _result(
            status,
            reason_code,
            completed_stages=list(completed_stages),
            **values,
        )

    mission_packet: dict[str, Any] | None = None
    mission_receipt: dict[str, Any] | None = None
    if contract["schema_version"] == workspace.WORK_CONTRACT_SCHEMA_VERSION_V2:
        packet_path = contract_path.parent / str(contract["mission_packet_path"])
        approval_path = contract_path.parent / str(contract["mission_approval_path"])
        try:
            mission_packet = mission.load_mission_packet(packet_path)
            if mission_packet["packet_sha256"] != contract["mission_packet_sha256"]:
                return finish("blocked", "mission_packet_contract_mismatch")
            if approval_path.is_symlink():
                return finish("blocked", "mission_approval_symlink")
            approval_bytes = approval_path.read_bytes()
            approval_digest = hashlib.sha256(approval_bytes).hexdigest()
            if approval_digest != contract["mission_approval_sha256"]:
                return finish("blocked", "mission_approval_contract_mismatch")
            mission_receipt = json.loads(approval_bytes.decode("utf-8"))
            mission.validate_mission_approval_receipt(
                mission_receipt,
                packet=mission_packet,
                session_id=str(contract["mission_approval_session_id"]),
            )
        except (OSError, json.JSONDecodeError, mission.MissionError):
            return finish("blocked", "mission_approval_invalid")

    if _sha256_text(instruction) != contract["instruction_sha256"]:
        return finish("blocked", "instruction_identity_mismatch")
    if mission_packet is not None:
        if mission_packet["request_sha256"] != contract["instruction_sha256"]:
            return finish("blocked", "mission_request_identity_mismatch")
        if mission_packet["base_commit"] != contract["base_commit"]:
            return finish("blocked", "mission_base_commit_mismatch")
    if _readonly_source_identity(source) != contract["source_identity"]:
        return finish("blocked", "source_identity_drift")
    if mission_packet is not None and mission_packet['schema_version'] == mission.SINGLE_APPROVAL_SCHEMA_VERSION:
        preparation = check_work_contract(source, contract_path, instruction, workspace_parent)
        if preparation['status'] != 'READY_STATIC':
            return finish('blocked', preparation['reason_code'], preparation=preparation)
        return _run_single_approval(
            source=source, contract=contract, packet=mission_packet,
            approval=mission_receipt, instruction=instruction,
            task_runner=task_runner, review_runner=review_runner,
            workspace_parent=workspace_parent, deadline_monotonic=deadline_monotonic,
        )
    if contract['promotion_policy'] != 'branch_ref_only':
        return finish('blocked', 'single_approval_mission_required')
    if contract["pipeline_mode"] == "full" and critique_runner is None:
        return finish("blocked", "full_critique_runner_missing")

    if workspace_parent.is_symlink():
        return finish("blocked", "workspace_parent_symlink")
    try:
        workspace_parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return finish("blocked", "workspace_parent_unwritable")
    if not workspace_parent.is_dir() or workspace_parent.is_symlink():
        return finish("blocked", "workspace_parent_untrusted")
    run_root = workspace_parent / str(contract["run_id"])
    isolated = run_root / "workspace"
    try:
        run_root.mkdir()
    except FileExistsError:
        return finish("blocked", "run_workspace_exists")
    except OSError:
        return finish("blocked", "run_workspace_unwritable")
    try:
        workspace.materialize_isolated_clone(source, isolated, contract)
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc))

    _runtime_parent, runtime_error = _prepare_verification_runtime(isolated)
    if runtime_error:
        return finish(
            "blocked", runtime_error, isolated_workspace=str(isolated)
        )

    before = workspace.snapshot_workspace(isolated)
    git_control_before = workspace.snapshot_git_control_plane(isolated)
    task_briefing = (
        mission.build_stage_briefing(mission_packet, stage="task")
        if mission_packet is not None
        else None
    )
    task_rc, task_output = task_runner(
        isolated, _task_prompt(contract, instruction, task_briefing)
    )
    if task_rc != 0 or _terminal_verdict(task_output) != "PROCEED":
        return finish("blocked", "task_failed", isolated_workspace=str(isolated))

    git_control_after_task = workspace.snapshot_git_control_plane(isolated)
    if git_control_after_task != git_control_before:
        reason_code = (
            "task_git_state_changed"
            if git_control_after_task.get("HEAD") != git_control_before.get("HEAD")
            else "task_git_control_changed"
        )
        return finish(
            "blocked", reason_code, isolated_workspace=str(isolated)
        )

    try:
        workspace.validate_precommit_git_state(isolated, contract)
    except workspace.AutopilotWorkspaceError:
        return finish("blocked", "task_git_state_changed", isolated_workspace=str(isolated))

    after = workspace.snapshot_workspace(isolated)
    delta = workspace.compute_workspace_delta(before, after)
    violations = workspace.validate_scope(delta, contract)
    if violations:
        return finish(
            "blocked",
            "scope_violation",
            violations=violations,
            isolated_workspace=str(isolated),
        )
    completed_stages.append("task")

    deadline = deadline_monotonic if deadline_monotonic is not None else time.monotonic() + 7200
    tests, verification_error = _run_verification(
        isolated,
        contract["verification_commands"],
        deadline_monotonic=deadline,
    )
    if verification_error:
        return finish(
            "blocked",
            verification_error,
            tests=tests,
            isolated_workspace=str(isolated),
        )

    try:
        workspace.validate_git_control_plane(isolated, git_control_before)
    except workspace.AutopilotWorkspaceError:
        return finish(
            "blocked",
            "verification_git_control_changed",
            tests=tests,
            isolated_workspace=str(isolated),
        )

    verified_after = workspace.snapshot_workspace(isolated)
    if verified_after != after:
        return finish(
            "blocked",
            "verification_side_effect_detected",
            tests=tests,
            isolated_workspace=str(isolated),
        )
    try:
        workspace.validate_precommit_git_state(isolated, contract)
    except workspace.AutopilotWorkspaceError:
        return finish(
            "blocked",
            "verification_git_state_changed",
            tests=tests,
            isolated_workspace=str(isolated),
        )
    completed_stages.append("verification")

    try:
        candidate_commit = _commit_candidate(isolated, delta)
        workspace.validate_candidate_scope(
            isolated=isolated,
            contract=contract,
            candidate_commit=candidate_commit,
            expected_delta=delta,
        )
        patch_result = _run_candidate_git(
            isolated,
            "diff",
            "--binary",
            f"{contract['base_commit']}..{candidate_commit}",
            capture_output=True,
            text=True,
        )
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc), isolated_workspace=str(isolated))
    packet = workspace.build_review_packet(
        contract=contract,
        candidate_commit=candidate_commit,
        delta=delta,
        patch=patch_result.stdout,
        tests=tests,
        executor_receipt={"workspace_write_confined": True, "executor": contract["executor"]},
    )
    packet_path = run_root / "review_packet.json"
    try:
        _write_immutable_packet(packet_path, packet)
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc), isolated_workspace=str(isolated))
    packet_digest = str(packet["packet_sha256"])
    critique_prompt = _review_prompt(
        packet,
        mission.build_stage_briefing(mission_packet, stage="critique")
        if mission_packet is not None
        else None,
    )
    review_prompt = _review_prompt(
        packet,
        mission.build_stage_briefing(mission_packet, stage="review")
        if mission_packet is not None
        else None,
    )

    if critique_runner is not None:
        critique_workspace = run_root / "critique-workspace"
        try:
            workspace.materialize_review_clone(
                isolated, critique_workspace, str(contract["base_commit"])
            )
        except workspace.AutopilotWorkspaceError as exc:
            return finish(
                "blocked", str(exc), review_packet_sha256=packet_digest
            )
        critique, error = _run_readonly_stage(
            root=critique_workspace,
            prompt=critique_prompt,
            runner=critique_runner,
            packet_digest=packet_digest,
            stage="critique",
        )
        if error:
            return finish("blocked", error, review_packet_sha256=packet_digest)
        if critique and critique["verdict"] not in {"PROCEED", "APPROVE", "APPROVE WITH NOTES"}:
            return finish("blocked", "critique_rejected", review_packet_sha256=packet_digest)
        completed_stages.append("critique")

    review_workspace = run_root / "review-workspace"
    try:
        workspace.materialize_review_clone(
            isolated, review_workspace, str(contract["base_commit"])
        )
    except workspace.AutopilotWorkspaceError as exc:
        return finish(
            "blocked", str(exc), review_packet_sha256=packet_digest
        )
    review, error = _run_readonly_stage(
        root=review_workspace,
        prompt=review_prompt,
        runner=review_runner,
        packet_digest=packet_digest,
        stage="review",
    )
    if error:
        return finish("blocked", error, review_packet_sha256=packet_digest)
    if review is None or review["verdict"] != "APPROVE":
        return finish("blocked", "review_rejected", review_packet_sha256=packet_digest)
    completed_stages.append("review")

    try:
        candidate_branch = workspace.promote_candidate_branch(
            source=source,
            isolated=isolated,
            contract=contract,
            candidate_commit=candidate_commit,
        )
    except workspace.AutopilotWorkspaceError as exc:
        return finish("blocked", str(exc), review_packet_sha256=packet_digest)
    completed_stages.append("promotion")
    return finish(
        "candidate_ready",
        None,
        candidate_branch=candidate_branch,
        candidate_commit=candidate_commit,
        review_packet_sha256=packet_digest,
        mission_packet_sha256=(
            mission_packet["packet_sha256"] if mission_packet is not None else None
        ),
        mission_approval_sha256=(
            contract.get("mission_approval_sha256")
            if mission_receipt is not None
            else None
        ),
        review=review,
        isolated_workspace=str(isolated),
    )
