"""
B항목 + C항목 테스트

B: 새 세션 시작(session-start 커맨드) 시 contract_confirmed 자동 초기화
C: RED 등록 없이 구현 파일에 코드 50줄+ 이면 "GREEN 먼저?" 경고 출력
"""
from __future__ import annotations

import io
import contextlib
import sys
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_pipeline_guard as guard


HANDOFF_ENV = 'OMC_SESSION_START_HANDOFF'
START_INPUT_ENV = 'OMC_SESSION_START_INPUT'


def start_input(source='startup', session_id='native-fixture'):
    return json.dumps({'source': source, 'session_id': session_id})


def prebound_project(root):
    """Real local state/approval and public CLI fixture; no provider invocation."""
    import omc_state
    import omc_validation_command as validation
    scripts = root / 'scripts'
    scripts.mkdir()
    for source in Path(guard.__file__).parent.glob('omc*.py'):
        shutil.copy2(source, scripts / source.name)
    (root / '.gitignore').write_text('.omc/\n__pycache__/\n')
    (root / 'app.py').write_text('value = 1\n')
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                    '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                    'commit', '-qm', 'fixture'], cwd=root, check=True)
    session = omc_state.record_session(root, mode='autopilot', title='omc-task',
        request='approved local comparison', role_ids=['senior_coding'], confirmed=True,
        work_class='implementation', completion_action='start')
    sid = session['session_id']
    # All tool pins refer to actual files; these tools are never executed here.
    tool = scripts / 'omc_validation_command.py'
    tool_ref = {'path': str(tool), 'sha256': hashlib.sha256(tool.read_bytes()).hexdigest()}
    policy = {'schema_version': 'omc-validation-policy/v1',
        'contract_id': validation.CONTRACT, 'target': str(root.resolve()),
        'arm': 'omc-kit', 'session_id': sid, 'run_id': 'start-fixture', 'timeout_sec': 30,
        'tools': {name: tool_ref for name in ('node', 'python', 'jest', 'eslint',
            'baseline_tests', *validation.SCRIPT_PINS)},
        'required_profiles': list(validation.REQUIRED),
        'baseline_scope': {'fixture': True}, 'report_contract': validation.REPORT_CONTRACT}
    path = root / validation.POLICY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy))
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    basis = {'session_id': sid, 'run_id': policy['run_id'], 'revision': 1,
        'request': session['request'], 'approval_reference': 'synthetic fixture approval',
        'validation_commands_policy_sha256': pin, 'checks': [{'id': 'tests',
            'target': 'app.py', 'method': 'local pytest', 'pass_condition': 'passes',
            'target_version': 'fixture'}]}
    proposal = root / '.omc/proposal.json'
    proposal.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root, proposal) == 0
    assert guard.cmd_contract_done(root, required_checks='tests') == 0
    (root / '.omc/hooks.json').write_text(json.dumps({'hooks': {'session_start': [
        {'type': 'shell', 'command': 'python3 scripts/omc_context.py --target .'}]}}))
    context = validation.prebound_task_context(root, policy, pin)
    args = [sys.executable, 'scripts/omc_validation_command.py', 'task-context', '--target', '.',
        '--sha256', pin, '--session-id', sid, '--run-id', policy['run_id'],
        '--basis-sha256', context['basis_sha256']]
    return context, args


def test_explicit_handoff_preserves_contract_once(tmp_path, monkeypatch):
    context, args = prebound_project(tmp_path)
    before = guard._state_path(tmp_path).read_bytes()
    ref = guard.create_session_start_handoff(tmp_path, context)
    monkeypatch.setenv(HANDOFF_ENV, json.dumps(ref))
    monkeypatch.setenv(START_INPUT_ENV, start_input())
    assert guard.cmd_session_start(tmp_path) == 0
    assert guard._state_path(tmp_path).read_bytes() == before
    result = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == context
    # A new start cannot reuse the same handoff, even with identical pins.
    assert guard.cmd_session_start(tmp_path) == 1
    assert guard._load_state(tmp_path)['contract_confirmed'] is False


@pytest.mark.parametrize('drift', ['session', 'work', 'pending', 'basis', 'run', 'policy',
    'candidate', 'head', 'unconfirmed', 'contract', 'contract-hash', 'expired-pipeline',
    'expired-handoff', 'future-handoff', 'digest', 'external', 'symlink', 'malformed'])
def test_handoff_drift_resets_without_restoring_approval(tmp_path, monkeypatch, drift):
    context, _ = prebound_project(tmp_path)
    ref = guard.create_session_start_handoff(tmp_path, context)
    latest_path = tmp_path / '.omc/state/latest.json'
    session_path = tmp_path / '.omc/state/sessions' / context['session_id'] / 'session.json'
    basis_path = tmp_path / '.omc/verification-bases' / context['session_id'] / '1.json'
    basis_before = basis_path.read_bytes()
    def update(path, **fields):
        value = json.loads(path.read_bytes()); value.update(fields)
        path.write_text(json.dumps(value))
    if drift == 'session': update(latest_path, latest_session_id='other', latest_confirmed_session_id='other')
    elif drift == 'work': update(session_path, work_id='other')
    elif drift == 'pending': update(tmp_path / '.omc/state/pending-completion.json', work_id='other')
    elif drift == 'basis': update(basis_path, revision=2)
    elif drift in {'run', 'policy'}:
        update(tmp_path / '.omc/validation-commands/policy.json', **({'run_id': 'other'} if drift == 'run' else {'arm': 'oh-my-claudecode'}))
    elif drift == 'candidate': (tmp_path / 'app.py').write_text('value = 2\n')
    elif drift == 'head':
        subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
            '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
            'commit', '--allow-empty', '-qm', 'new head'], cwd=tmp_path, check=True)
    elif drift == 'unconfirmed': update(session_path, confirmation={'status': 'pending'})
    elif drift in {'contract', 'contract-hash', 'expired-pipeline'}:
        fields = {'contract_confirmed': False} if drift == 'contract' else (
            {'contract_hash': 'changed'} if drift == 'contract-hash' else {'started_at': 0})
        update(guard._state_path(tmp_path), **fields)
    elif drift in {'expired-handoff', 'future-handoff'}:
        path = tmp_path / ref['path']
        update(path, created_at=time.time() + (600 if drift == 'future-handoff' else -3600))
        ref['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif drift == 'digest': ref['sha256'] = '0' * 64
    elif drift == 'external': ref['path'] = str(tmp_path / ref['path'])
    elif drift == 'symlink':
        path = tmp_path / ref['path']; saved = path.with_suffix('.saved')
        path.rename(saved); path.symlink_to(saved.name)
    before_reject = basis_path.read_bytes()
    monkeypatch.setenv(HANDOFF_ENV, 'not-json' if drift == 'malformed' else json.dumps(ref))
    monkeypatch.setenv(START_INPUT_ENV, start_input())
    assert guard.cmd_session_start(tmp_path) == 1
    state = guard._load_state(tmp_path)
    assert state['contract_confirmed'] is False and state['session_id'] == ''
    assert basis_path.read_bytes() == before_reject
    if drift != 'basis': assert before_reject == basis_before


def test_unrequested_start_still_resets_prebound_work(tmp_path, monkeypatch):
    context, _ = prebound_project(tmp_path)
    monkeypatch.delenv(HANDOFF_ENV, raising=False)
    assert guard.cmd_session_start(tmp_path) == 0
    assert guard._load_state(tmp_path)['session_id'] == ''
    assert guard._load_state(tmp_path)['contract_confirmed'] is False


def test_public_hook_reports_invalid_handoff_as_error(tmp_path, monkeypatch):
    from omc_hooks import run_event
    prebound_project(tmp_path)
    monkeypatch.setenv(HANDOFF_ENV, 'not-json')
    result = run_event(tmp_path, 'session_start')
    reset = next(row for row in result['results'] if row.get('name') == 'pipeline_guard.session-start')
    assert reset['status'] == 'error' and reset['rc'] == 1
    assert guard._load_state(tmp_path)['contract_confirmed'] is False


@pytest.mark.parametrize('invalid', ['contract', 'completed'])
def test_handoff_creation_refuses_already_invalid_contract(tmp_path, invalid):
    context, _ = prebound_project(tmp_path)
    if invalid == 'contract':
        state = guard._load_state(tmp_path); state['contract_confirmed'] = False
        guard._save_state(tmp_path, state)
    else:
        path = tmp_path / '.omc/state/sessions' / context['session_id'] / 'session.json'
        value = json.loads(path.read_bytes()); value['lifecycle']['status'] = 'completed'
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='prebound_task_context_mismatch|session_start_work_mismatch'):
        guard.create_session_start_handoff(tmp_path, context)
    assert not list((tmp_path / '.omc/session-start-handoffs').glob('*.json'))


@pytest.mark.parametrize('progress', [False, True])
def test_public_compact_keeps_current_work_after_legitimate_progress(tmp_path, progress):
    import os
    context, args = prebound_project(tmp_path)
    ref = guard.create_session_start_handoff(tmp_path, context)
    env = {**os.environ, HANDOFF_ENV: json.dumps(ref)}
    hook = Path(__file__).resolve().parents[1] / 'templates/.agent-hooks/omc-session-start.sh'
    def invoke(source):
        result = subprocess.run(['bash', str(hook), 'claude'], cwd=tmp_path,
            env=env, input=start_input(source), text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return subprocess.run(args, cwd=tmp_path, env=env, text=True, capture_output=True)
    assert invoke('startup').returncode == 0
    consumed = tmp_path / (ref['path'][:-5] + '.consumed.json')
    consumed_before = consumed.read_bytes()
    contract_hash = guard._load_state(tmp_path)['contract_hash']
    if progress:
        (tmp_path / 'app.py').write_text('value = 2\n')
        # The public registration changes pipeline progress, not its CONTRACT.
        assert guard.cmd_allow(tmp_path, 'app.py', reason='synthetic fixture progress') == 0
    for _ in range(2):
        result = invoke('compact')
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == context
        assert guard._load_state(tmp_path)['contract_hash'] == contract_hash
    assert consumed.read_bytes() == consumed_before
    if progress:
        assert (tmp_path / 'app.py').read_text() == 'value = 2\n'
        assert guard._load_state(tmp_path)['allowed_impl_files']


def test_compact_uses_current_contract_ttl_not_initial_handoff_ttl(tmp_path, monkeypatch):
    context, _ = prebound_project(tmp_path)
    ref = guard.create_session_start_handoff(tmp_path, context)
    monkeypatch.setenv(HANDOFF_ENV, json.dumps(ref))
    monkeypatch.setenv(START_INPUT_ENV, start_input())
    assert guard.cmd_session_start(tmp_path) == 0
    now = time.time()
    monkeypatch.setattr(guard.time, 'time', lambda: now + 301)
    monkeypatch.setenv(START_INPUT_ENV, start_input('compact'))
    assert guard.cmd_session_start(tmp_path) == 0
    assert guard._load_state(tmp_path)['contract_confirmed'] is True


@pytest.mark.parametrize('invalid', ['other-native-session', 'clear', 'fork', 'resume',
    'startup', 'missing-native-session', 'contract-hash', 'contract-revoked',
    'work', 'basis', 'head', 'expired-pipeline', 'missing-consumed', 'consumed-tamper'])
def test_compact_never_inherits_another_execution_or_changed_approval(tmp_path, monkeypatch, invalid):
    context, _ = prebound_project(tmp_path)
    ref = guard.create_session_start_handoff(tmp_path, context)
    monkeypatch.setenv(HANDOFF_ENV, json.dumps(ref))
    monkeypatch.setenv(START_INPUT_ENV, start_input())
    assert guard.cmd_session_start(tmp_path) == 0
    source = invalid if invalid in {'clear', 'fork', 'resume', 'startup'} else 'compact'
    sid = 'other' if invalid == 'other-native-session' else (
        '' if invalid == 'missing-native-session' else 'native-fixture')
    def update(path, **fields):
        value = json.loads(path.read_bytes()); value.update(fields)
        path.write_text(json.dumps(value))
    if invalid in {'contract-hash', 'contract-revoked', 'expired-pipeline'}:
        update(guard._state_path(tmp_path), **({'contract_hash': 'changed'} if invalid == 'contract-hash'
            else {'contract_confirmed': False} if invalid == 'contract-revoked' else {'started_at': 0}))
    elif invalid == 'work':
        update(tmp_path / '.omc/state/sessions' / context['session_id'] / 'session.json', work_id='other')
    elif invalid == 'basis':
        update(tmp_path / '.omc/verification-bases' / context['session_id'] / '1.json', revision=2)
    elif invalid == 'head':
        subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
            '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
            'commit', '--allow-empty', '-qm', 'changed'], cwd=tmp_path, check=True)
    elif invalid in {'missing-consumed', 'consumed-tamper'}:
        consumed = tmp_path / (ref['path'][:-5]+'.consumed.json')
        if invalid == 'missing-consumed': consumed.unlink()  # Fixture corruption only.
        else: update(consumed, native_session_id='other')
    monkeypatch.setenv(START_INPUT_ENV, start_input(source, sid))
    assert guard.cmd_session_start(tmp_path) == 1
    assert guard._load_state(tmp_path)['contract_confirmed'] is False


def test_compact_cannot_consume_the_first_start_handoff(tmp_path, monkeypatch):
    context, _ = prebound_project(tmp_path)
    ref = guard.create_session_start_handoff(tmp_path, context)
    monkeypatch.setenv(HANDOFF_ENV, json.dumps(ref))
    monkeypatch.setenv(START_INPUT_ENV, start_input('compact'))
    assert guard.cmd_session_start(tmp_path) == 1
    assert not (tmp_path / (ref['path'][:-5]+'.consumed.json')).exists()


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / ".omc").mkdir()
    (tmp_path / "src").mkdir()
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# B: 세션 시작 시 contract_confirmed 초기화
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionResetClearsContract:
    def test_reset_clears_contract_confirmed(self, tmp_root: Path):
        """cmd_reset 실행 시 contract_confirmed가 False로 초기화돼야 한다."""
        guard.cmd_contract_done(tmp_root)
        assert guard._load_state(tmp_root).get("contract_confirmed") is True

        guard.cmd_reset(tmp_root)

        assert guard._load_state(tmp_root).get("contract_confirmed") is False

    def test_session_start_resets_contract(self, tmp_root: Path):
        """session_start 후 contract_confirmed가 False여야 한다."""
        guard.cmd_contract_done(tmp_root)

        result = guard.cmd_session_start(tmp_root)
        assert result == 0

        state = guard._load_state(tmp_root)
        assert state.get("contract_confirmed") is False

    def test_session_start_preserves_red_done_files(self, tmp_root: Path):
        """session_start 후에도 red_done_files는 유지돼야 한다."""
        test_file = tmp_root / "src" / "Foo.spec.ts"
        test_file.write_text("test('foo', () => {})")
        guard.cmd_red_done(tmp_root, str(test_file))

        guard.cmd_session_start(tmp_root)

        state = guard._load_state(tmp_root)
        norm = guard._normalize_path(tmp_root, str(test_file))
        norm_list = [guard._normalize_path(tmp_root, f) for f in state.get("red_done_tests", [])]
        assert norm in norm_list

    def test_session_start_idempotent(self, tmp_root: Path):
        """session_start를 여러 번 실행해도 동일하게 contract=False 유지."""
        guard.cmd_contract_done(tmp_root)
        guard.cmd_session_start(tmp_root)
        guard.cmd_session_start(tmp_root)
        assert guard._load_state(tmp_root).get("contract_confirmed") is False


# ─────────────────────────────────────────────────────────────────────────────
# C: 줄 수 기반 "GREEN 먼저?" 경고
# ─────────────────────────────────────────────────────────────────────────────

class TestLineLengthWarning:
    def _make_impl_file(self, tmp_root: Path, lines: int) -> Path:
        impl = tmp_root / "src" / "BigComponent.tsx"
        impl.write_text("\n".join([f"const line{i} = {i};" for i in range(lines)]))
        return impl

    def _run_check(self, tmp_root: Path, impl: Path) -> str:
        # 절대 경로에 'test_' 패턴이 포함될 수 있으므로 상대 경로 사용
        rel_path = impl.relative_to(tmp_root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            guard.cmd_check(tmp_root, str(rel_path))
        return buf.getvalue()

    def test_no_warning_for_small_file(self, tmp_root: Path):
        """50줄 미만 파일은 경고 없이 통과."""
        impl = self._make_impl_file(tmp_root, 30)
        output = self._run_check(tmp_root, impl)
        assert "GREEN 먼저" not in output

    def test_warning_for_large_file_without_red(self, tmp_root: Path):
        """50줄 이상 파일이 RED 없이 check되면 경고를 출력해야 한다."""
        impl = self._make_impl_file(tmp_root, 60)
        output = self._run_check(tmp_root, impl)
        assert "GREEN 먼저" in output, (
            f"60줄 구현 파일에 RED 없으면 'GREEN 먼저' 경고 출력 필요. 실제: {repr(output)}"
        )

    def test_no_warning_for_large_file_with_red(self, tmp_root: Path):
        """RED가 등록된 파일은 줄 수 경고 없이 통과."""
        impl = self._make_impl_file(tmp_root, 60)
        test_file = tmp_root / "src" / "BigComponent.spec.tsx"
        test_file.write_text("test('big', () => {})")
        guard.cmd_red_done(tmp_root, "src/BigComponent.spec.tsx")
        guard.cmd_allow(tmp_root, "src/BigComponent.tsx", reason="RED 완료 후 구현")
        output = self._run_check(tmp_root, impl)
        assert "GREEN 먼저" not in output

    def test_warning_exits_zero(self, tmp_root: Path):
        """줄 수 경고는 exit 0 (차단 아님)."""
        impl = self._make_impl_file(tmp_root, 60)
        rel_path = impl.relative_to(tmp_root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = guard.cmd_check(tmp_root, str(rel_path))
        assert result == 0
