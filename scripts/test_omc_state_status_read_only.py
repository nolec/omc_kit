from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts' / 'omc.py'

def invoke(target):
    return subprocess.run([sys.executable, '-B', str(CLI), 'state', 'status', '--target', str(target)], text=True, capture_output=True, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'PYTHONDONTWRITEBYTECODE': '1'})

def snapshot(target):
    paths = [target, *target.rglob('*')]
    out = {}
    for p in paths:
        st = p.lstat()
        out[str(p.relative_to(target))] = (st.st_mode, st.st_ino, st.st_mtime_ns, os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
    return out

def init_git(target):
    subprocess.run(['git','init','-q',str(target)], check=True, capture_output=True)

def test_absent_omc_status_does_not_create_anything(tmp_path):
    init_git(tmp_path)
    before = snapshot(tmp_path)
    result = invoke(tmp_path)
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path) == before
    assert '없음' in result.stdout or '미설치' in result.stdout

def test_existing_user_state_is_not_modified_even_on_repeat(tmp_path):
    init_git(tmp_path)
    omc = tmp_path / '.omc'
    omc.mkdir()
    for name, text in [('notepad.md','USER NOTE\n'),('summary.md','USER SUMMARY\n'),('policy.json','{"enforce_confirm": false}\n')]:
        (omc / name).write_text(text)
    before = snapshot(tmp_path)
    for _ in range(2):
        result = invoke(tmp_path)
        assert result.returncode == 0, result.stderr
        assert snapshot(tmp_path) == before

def test_frozen_status_preserves_everything(tmp_path):
    init_git(tmp_path)
    marker = tmp_path / '.omc/edit-resumptions/retirement-pending.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    (tmp_path / '.omc/notepad.md').write_text('FROZEN NOTE')
    before = snapshot(tmp_path)
    result = invoke(tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'FROZEN' in result.stdout
    assert snapshot(tmp_path) == before

@pytest.mark.parametrize('name', ['project-memory.json','state/latest.json','policy.json'])
def test_invalid_state_is_reported_without_repair(tmp_path, name):
    init_git(tmp_path)
    path = tmp_path / '.omc' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{broken')
    before = snapshot(tmp_path)
    result = invoke(tmp_path)
    assert snapshot(tmp_path) == before
    assert '손상' in result.stdout or '확인 불가' in result.stdout or 'invalid' in result.stdout.lower()

def test_symlink_state_is_reported_and_target_untouched(tmp_path):
    target = tmp_path / 'repo'; target.mkdir(); init_git(target)
    external = tmp_path / 'outside.json'; external.write_text('{"entries": []}')
    omc = target / '.omc'; omc.mkdir()
    try: (omc / 'project-memory.json').symlink_to(external)
    except OSError: pytest.skip('symlink unavailable')
    before, ext = snapshot(target), external.read_bytes()
    result = invoke(target)
    assert snapshot(target) == before and external.read_bytes() == ext
    assert '확인 불가' in result.stdout or '심볼릭' in result.stdout

def git(target, *args):
    return subprocess.run(['git','-C',str(target),*args], check=True, capture_output=True, text=True, env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})

def test_status_does_not_infer_request_scope_from_staging(tmp_path):
    init_git(tmp_path)
    git(tmp_path,'config','user.name','Test')
    git(tmp_path,'config','user.email','test@example.invalid')
    for name in ['a.txt','b.txt','한 글.txt']:
        (tmp_path / name).write_text('base')
    git(tmp_path,'add','a.txt','b.txt','한 글.txt')
    git(tmp_path,'commit','-qm','fixture')
    (tmp_path / 'a.txt').write_text('unrelated staged')
    git(tmp_path,'add','a.txt')
    (tmp_path / 'a.txt').write_text('also unstaged')
    (tmp_path / 'b.txt').write_text('requested unstaged')
    (tmp_path / '한 글.txt').write_text('changed')
    (tmp_path / 'new.txt').write_text('untracked')
    before = snapshot(tmp_path)
    result = invoke(tmp_path)
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path) == before
    assert 'Git staged:' in result.stdout and 'a.txt' in result.stdout
    assert 'Git unstaged:' in result.stdout and 'b.txt' in result.stdout and '한 글.txt' in result.stdout
    assert 'Git untracked:' in result.stdout and 'new.txt' in result.stdout
    assert '요청 범위: 미확인' in result.stdout
    assert '현재 커밋 범위' not in result.stdout and '범위 밖 dirty 변경' not in result.stdout

def test_status_non_git_does_not_claim_empty_git_state(tmp_path):
    result = invoke(tmp_path)
    assert 'Git 상태: 확인 불가' in result.stdout
    assert 'ship 차단 힌트' not in result.stdout
    assert 'Git staged: 없음' not in result.stdout

def test_unreadable_core_reports_unknown_without_mutation(tmp_path, monkeypatch):
    import omc_state
    init_git(tmp_path)
    path = tmp_path / '.omc/project-memory.json'
    path.parent.mkdir()
    path.write_text('{"entries": []}')
    before = snapshot(tmp_path)
    original = os.open
    def safe_open(name, *args, **kwargs):
        if Path(name).name == path.name:
            raise PermissionError('fixture denied')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(os, 'open', safe_open)
    out = omc_state.status(tmp_path)
    assert '확인 불가' in out
    assert snapshot(tmp_path) == before

def test_rename_reports_destination_exactly(tmp_path):
    init_git(tmp_path)
    git(tmp_path,'config','user.name','Test');git(tmp_path,'config','user.email','test@example.invalid')
    (tmp_path/'old name.txt').write_text('content')
    git(tmp_path,'add','old name.txt');git(tmp_path,'commit','-qm','fixture')
    git(tmp_path,'mv','old name.txt','새 이름.txt')
    result = invoke(tmp_path)
    assert result.returncode == 0 and '새 이름.txt' in result.stdout
    assert 'Git staged:' in result.stdout

def test_missing_state_does_not_claim_defaults_or_zero(tmp_path):
    init_git(tmp_path)
    result = invoke(tmp_path)
    assert result.returncode == 0
    assert 'enforce_confirm: 확인 불가' in result.stdout
    assert 'memory entries: 확인 불가' in result.stdout
    assert 'latest_session_id: 확인 불가' in result.stdout

@pytest.mark.parametrize('entries', [None, [{'kind':'session','confirmation':None}]])
def test_invalid_memory_structure_reports_unknown(tmp_path, entries):
    init_git(tmp_path)
    p = tmp_path / '.omc/project-memory.json'; p.parent.mkdir()
    p.write_text(json.dumps({'entries':entries}))
    before = snapshot(tmp_path)
    result = invoke(tmp_path)
    assert snapshot(tmp_path) == before
    assert '확인 불가' in result.stdout
    assert 'Traceback' not in result.stderr

def test_tracked_omc_changes_remain_visible_in_git_buckets(tmp_path):
    init_git(tmp_path)
    git(tmp_path,'config','user.name','Test');git(tmp_path,'config','user.email','test@example.invalid')
    omc = tmp_path / '.omc'; omc.mkdir(); p = omc/'policy.json'
    p.write_text('{"enforce_confirm": true}')
    git(tmp_path,'add','-f','.omc/policy.json');git(tmp_path,'commit','-qm','fixture')
    p.write_text('{"enforce_confirm": false}')
    git(tmp_path,'add','-f','.omc/policy.json')
    output = invoke(tmp_path).stdout
    staged = next(line for line in output.splitlines() if line.startswith('- Git staged:'))
    assert '.omc/policy.json' in staged
    assert '.omc/policy.json' in next(line for line in output.splitlines() if '.omc 실행 아티팩트:' in line)

def test_frozen_and_corrupt_state_report_both(tmp_path):
    init_git(tmp_path)
    marker = tmp_path / '.omc/edit-resumptions/retirement-pending.json'
    marker.parent.mkdir(parents=True); marker.write_text('{}')
    (tmp_path/'.omc/policy.json').write_text('{bad')
    before = snapshot(tmp_path); result = invoke(tmp_path)
    assert snapshot(tmp_path) == before
    assert 'FROZEN' in result.stdout and '확인 불가' in result.stdout

def test_missing_git_executable_is_reported(tmp_path, monkeypatch):
    import omc_state
    def fail(*args, **kwargs): raise FileNotFoundError('git unavailable')
    monkeypatch.setattr(omc_state.subprocess, 'run', fail)
    assert omc_state._git_scope_snapshot(tmp_path) is None

def test_fifo_memory_rejected_without_blocking(tmp_path):
    if not hasattr(os, 'mkfifo'): pytest.skip('FIFO unavailable')
    init_git(tmp_path)
    p = tmp_path / '.omc/project-memory.json'; p.parent.mkdir(); os.mkfifo(p)
    result = subprocess.run([sys.executable,'-B',str(CLI),'state','status','--target',str(tmp_path)], capture_output=True,text=True,timeout=3)
    assert '확인 불가' in result.stdout

def test_oversize_state_rejected(tmp_path):
    init_git(tmp_path)
    p = tmp_path / '.omc/project-memory.json'; p.parent.mkdir(); p.write_text(' ' * (8*1024*1024+1))
    result = invoke(tmp_path)
    assert '확인 불가' in result.stdout

def test_status_rejects_session_path_traversal(tmp_path):
    target = tmp_path / 'repo'; target.mkdir(); init_git(target)
    outside = tmp_path / 'outside'; outside.mkdir(); (outside/'session.json').write_text(json.dumps({'kind':'session','request':'SECRET_OUTSIDE'}))
    state = target / '.omc/state'; state.mkdir(parents=True)
    (state/'sessions').mkdir()
    (state/'latest.json').write_text(json.dumps({'latest_session_id':'../../../../outside'}))
    (target/'.omc/project-memory.json').write_text('{"entries": []}')
    result=invoke(target)
    assert 'SECRET_OUTSIDE' not in result.stdout and '확인 불가' in result.stdout

def test_status_json_rejects_parent_swapped_to_symlink(tmp_path, monkeypatch):
    import omc_state
    target=tmp_path/'repo'; target.mkdir(); state=target/'.omc/state/sessions/abc'; state.mkdir(parents=True)
    (state/'session.json').write_text('{"request":"inside"}')
    outside=tmp_path/'outside'; outside.mkdir(); (outside/'session.json').write_text('{"request":"SECRET_OUTSIDE"}')
    original=os.open; moved=state.with_name('abc-original'); swapped=False
    def race(name,*args,**kwargs):
        nonlocal swapped
        if not swapped and Path(name).name in ('abc', 'session.json'):
            swapped=True; state.rename(moved); state.symlink_to(outside, target_is_directory=True)
        return original(name,*args,**kwargs)
    monkeypatch.setattr(os,'open',race)
    with pytest.raises((OSError, ValueError)):
        omc_state._status_read_json(state/'session.json',target)
    assert swapped

def test_git_status_timeout_is_unknown(tmp_path, monkeypatch):
    import omc_state
    def hang(*args, **kwargs): raise subprocess.TimeoutExpired('git status', 5)
    monkeypatch.setattr(omc_state.subprocess, 'run', hang)
    assert omc_state._git_scope_snapshot(tmp_path) is None

def test_oversize_governance_input_is_not_treated_as_readable(tmp_path):
    init_git(tmp_path)
    p = tmp_path/'.omc/pipeline_session.json';p.parent.mkdir();p.write_text('{\"payload\":\"' + 'x'*(8*1024*1024) + '\"}')
    result = invoke(tmp_path)
    assert '상태 확인 불가' in result.stdout and 'pipeline_session.json' in result.stdout

def test_frozen_marker_with_fifo_governance_reports_both(tmp_path):
    if not hasattr(os, 'mkfifo'): pytest.skip('FIFO unavailable')
    init_git(tmp_path)
    marker=tmp_path/'.omc/edit-resumptions/retirement-pending.json';marker.parent.mkdir(parents=True);marker.write_text('{}')
    os.mkfifo(tmp_path/'.omc/pipeline_session.json')
    result=subprocess.run([sys.executable,'-B',str(CLI),'state','status','--target',str(tmp_path)],capture_output=True,text=True,timeout=3)
    assert 'FROZEN' in result.stdout and '확인 불가' in result.stdout

def test_oversized_cohort_config_is_unknown_without_unbounded_read(tmp_path):
    init_git(tmp_path)
    p=tmp_path/'.omc/skill-effectiveness-cohort-v1.json'; p.parent.mkdir(); p.write_text('{"value":"'+'x'*(8*1024*1024)+'"}')
    result=invoke(tmp_path)
    assert 'skill_cohort: INTEGRITY_INVALID' in result.stdout

def test_stale_lifecycle_requires_confirmation_not_false_unfreeze(tmp_path):
    init_git(tmp_path)
    p=tmp_path/'.omc/pipeline_session.json'; p.parent.mkdir()
    p.write_text(json.dumps({'owner_work_id':'old','started_at':1}))
    result=invoke(tmp_path)
    assert 'lifecycle: 확인 불가' in result.stdout and '해제 아님' in result.stdout

def test_status_does_not_reopen_cohort_via_unbounded_reader(tmp_path, monkeypatch):
    import omc_state
    init_git(tmp_path)
    p=tmp_path/'.omc/skill-effectiveness-cohort-v1.json';p.parent.mkdir();p.write_text('{}')
    calls=[]; original=Path.read_text
    def observe(self,*args,**kwargs):
        if self==p: calls.append(self)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',observe)
    output=omc_state.status(tmp_path)
    assert 'skill_cohort: INTEGRITY_INVALID' in output
    assert calls == []

def test_lifecycle_eight_hour_boundary_keeps_writes_frozen_semantics(tmp_path, monkeypatch):
    import omc_state
    init_git(tmp_path); p=tmp_path/'.omc/pipeline_session.json';p.parent.mkdir()
    monkeypatch.setattr(omc_state.time, 'time', lambda: 30000.0)
    p.write_text(json.dumps({'owner_work_id':'w','started_at':1200}))
    assert 'lifecycle: 확인 불가' not in omc_state.status(tmp_path)
    p.write_text(json.dumps({'owner_work_id':'w','started_at':1199}))
    assert 'lifecycle: 확인 불가' in omc_state.status(tmp_path)
    pending=tmp_path/'.omc/state/pending-completion.json';pending.parent.mkdir();pending.write_text('{"work_id":"w"}')
    assert 'lifecycle: FROZEN' in omc_state.status(tmp_path)

def test_status_rejects_symlinked_runs_outside_target(tmp_path):
    target=tmp_path/'repo';target.mkdir();init_git(target)
    omc=target/'.omc';omc.mkdir();outside=tmp_path/'outside';(outside/'run').mkdir(parents=True)
    (outside/'run/result.json').write_text('{}')
    try: (omc/'runs').symlink_to(outside,target_is_directory=True)
    except OSError: pytest.skip('symlink unavailable')
    output=invoke(target).stdout
    assert '확인 불가' in output and 'pipeline_history_runs(.omc/runs): 1' not in output

def test_frozen_marker_survives_later_status_read_failure(tmp_path):
    target=tmp_path/'repo';target.mkdir();init_git(target)
    marker=target/'.omc/edit-resumptions/retirement-pending.json';marker.parent.mkdir(parents=True);marker.write_text('{}')
    outside=tmp_path/'outside';outside.mkdir()
    try: (target/'.omc/runs').symlink_to(outside,target_is_directory=True)
    except OSError: pytest.skip('symlink unavailable')
    output=invoke(target).stdout
    assert 'lifecycle: FROZEN' in output and '확인 불가' in output

def test_status_does_not_execute_configured_fsmonitor(tmp_path):
    init_git(tmp_path)
    marker=tmp_path/'monitor-ran'; hook=tmp_path/'fsmonitor-test.sh'
    hook.write_text('#!/bin/sh\nprintf invoked > "'+str(marker)+'"\nprintf "builtin:fake\\0"\n')
    hook.chmod(0o755)
    git(tmp_path,'config','core.fsmonitor',str(hook))
    result=invoke(tmp_path)
    assert result.returncode == 0
    assert not marker.exists()

def test_missing_referenced_session_is_not_replaced_with_older_request(tmp_path):
    init_git(tmp_path); omc=tmp_path/'.omc';state=omc/'state';state.mkdir(parents=True)
    (omc/'project-memory.json').write_text(json.dumps({'entries':[{'kind':'session','request':'OLD_OTHER_REQUEST','lifecycle':{},'confirmation':{}}]}))
    (state/'latest.json').write_text(json.dumps({'latest_session_id':'missing-id'}))
    result=invoke(tmp_path)
    latest = next(line for line in result.stdout.splitlines() if line.startswith('- latest:'))
    assert 'OLD_OTHER_REQUEST' not in latest and '확인 불가' in latest

def test_missing_referenced_run_is_reported(tmp_path):
    init_git(tmp_path); omc=tmp_path/'.omc'; state=omc/'state';state.mkdir(parents=True)
    (omc/'project-memory.json').write_text('{"entries":[]}')
    (state/'latest.json').write_text(json.dumps({'active_run_id':'missing-run'}))
    output=invoke(tmp_path).stdout
    assert '참조 실행: 확인 불가' in output

def test_status_does_not_follow_symlinked_lifecycle_slot(tmp_path):
    import hashlib
    target=tmp_path/'repo';target.mkdir();init_git(target)
    omc=target/'.omc';omc.mkdir();(omc/'pipeline_session.json').write_text(json.dumps({'owner_work_id':'w','started_at':9999999999}))
    outside=tmp_path/'outside';outside.mkdir();(outside/(hashlib.sha256(b'w').hexdigest()+'.json')).write_text('{}')
    slots=omc/'edit-resumptions/slots';slots.parent.mkdir()
    try: slots.symlink_to(outside,target_is_directory=True)
    except OSError: pytest.skip('symlink unavailable')
    output=invoke(target).stdout
    assert 'lifecycle: FROZEN' not in output and '확인 불가' in output
