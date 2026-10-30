"""Public CLI regression tests; fixtures are synthetic local repositories only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/omc_ship_preflight.py'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, stderr=subprocess.PIPE).decode().strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('branch', ['main', 'feature'])
@pytest.mark.parametrize('ref', ['origin/main', 'refs/remotes/origin/main'])
def test_public_prepare_separates_clone_remote_from_contract_ref(fixture, branch, ref):
    source, contract, target = fixture
    if branch != 'feature':
        git(source, 'branch', '-m', branch)
    doc = replace_contract(fixture, base_ref=ref)
    source_refs = git(source, 'for-each-ref', '--format=%(refname) %(objectname)')
    source_config = (source / '.git/config').read_bytes()
    source_index = (source / '.git/index').read_bytes()
    quality = (source / '.omc/quality-gates.json').read_bytes()

    prepared = invoke(fixture)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    result = json.loads(prepared.stdout)
    assert result['operation'] == 'prepare'
    assert result['status'] == 'baseline_verified'
    assert result['source_preserved'] and result['cleanup_confirmed'] and result['evidence_complete']
    assert git(target, 'remote').splitlines() == ['omc-source']
    assert git(target, 'config', 'remote.omc-source.url') == str(source)
    assert git(target, 'rev-parse', 'refs/remotes/omc-source/' + branch) == doc['expected_head_oid']
    assert git(target, 'rev-parse', 'refs/remotes/origin/main') == doc['expected_base_oid']
    assert git(target, 'rev-parse', 'HEAD') == doc['expected_head_oid']
    assert (target / '.omc/quality-gates.json').read_bytes() == quality

    target_index = (target / '.git/index').read_bytes()
    checked = invoke(fixture, 'check')
    assert checked.returncode == 0, checked.stdout + checked.stderr
    result = json.loads(checked.stdout)
    assert result['operation'] == 'check' and result['status'] == 'baseline_verified'
    assert result['target_preserved'] and result['cleanup_confirmed'] and result['evidence_complete']
    assert (target / '.git/index').read_bytes() == target_index
    assert git(source, 'for-each-ref', '--format=%(refname) %(objectname)') == source_refs
    assert (source / '.git/config').read_bytes() == source_config
    assert (source / '.git/index').read_bytes() == source_index
    assert (source / '.omc/quality-gates.json').read_bytes() == quality


@pytest.mark.parametrize('ref', ['origin/main', 'refs/remotes/origin/main'])
def test_prepare_rejects_post_clone_contract_ref_mismatch_without_overwrite(fixture, tmp_path, ref):
    source, contract, target = fixture
    git(source, 'branch', '-m', 'main')
    doc = replace_contract(fixture, base_ref=ref)
    source_refs = git(source, 'for-each-ref', '--format=%(refname) %(objectname)')
    source_config = (source / '.git/config').read_bytes()
    source_index = (source / '.git/index').read_bytes()
    env = shim_env(tmp_path, """
if args[6] == 'clone':
    cloned=subprocess.run([real,*args])
    if cloned.returncode:
        sys.exit(cloned.returncode)
    subprocess.run([real,'-C',%r,'update-ref','refs/remotes/origin/main',%r],check=True)
    sys.exit(0)
""" % (str(target), doc['expected_head_oid']))

    result = assert_blocked(invoke(fixture, env=env), 'existing_base_ref_mismatch')
    assert result['phase'] == 'bind_ref'
    assert result['cleanup_confirmed'] and result['evidence_complete']
    assert git(target, 'rev-parse', 'refs/remotes/origin/main') == doc['expected_head_oid']
    assert not (target / '.omc/quality-gates.json').exists()
    assert git(source, 'for-each-ref', '--format=%(refname) %(objectname)') == source_refs
    assert (source / '.git/config').read_bytes() == source_config
    assert (source / '.git/index').read_bytes() == source_index


@pytest.fixture
def fixture(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'feature')
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / '.gitignore').write_text('.omc/\n')
    (source / 'VERSION').write_text('1\n')
    (source / 'commands.md').write_text('test command\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'base')
    base = git(source, 'rev-parse', 'HEAD')
    (source / 'VERSION').write_text('2\n')
    git(source, 'add', 'VERSION')
    git(source, 'commit', '-m', 'candidate')
    git(source, 'update-ref', 'refs/remotes/origin/main', base)
    config = source / '.omc/quality-gates.json'
    config.parent.mkdir()
    config.write_text(json.dumps({'schema_version': 'omc-quality-gates/v1',
        'base_ref': 'origin/main',
        'evidence': [{'path': 'commands.md', 'sha256': digest(source / 'commands.md')}],
        'gates': [{'id': 'test', 'purpose': 'test', 'scope': 'full', 'required': True,
                   'argv': ['python3', '-c', 'raise SystemExit(99)'], 'timeout_sec': 1}]}))
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps({'schema_version': 'omc-ship-preflight/v1',
        'source_root': str(source), 'expected_head_oid': git(source, 'rev-parse', 'HEAD'),
        'base_ref': 'origin/main', 'expected_base_oid': base,
        'quality_config_sha256': digest(config), 'expected_changed_paths': ['VERSION']}))
    return source, contract, tmp_path / 'target'


def invoke(fixture, command='prepare', **kwargs):
    source, contract, target = fixture
    record = contract.parent / ('records-' + command)
    return subprocess.run([sys.executable, str(CLI), command, '--contract', str(contract),
        '--target', str(target), '--record-dir', str(record)], capture_output=True,
        text=True, timeout=20, **kwargs)


@pytest.mark.parametrize('ref', ['origin/main', 'refs/remotes/origin/main'])
def test_public_prepare_and_read_only_check_bind_fixed_ref(fixture, ref):
    source, contract, target = fixture
    doc = json.loads(contract.read_text())
    doc['base_ref'] = ref
    contract.write_text(json.dumps(doc))
    index = (source / '.git/index').read_bytes()
    result = invoke(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'baseline_verified'
    assert git(target, 'rev-parse', 'refs/remotes/origin/main') == doc['expected_base_oid']
    assert git(target, 'rev-parse', 'HEAD') == doc['expected_head_oid']
    assert (target / '.omc/quality-gates.json').read_bytes() == (source / '.omc/quality-gates.json').read_bytes()
    before = (target / '.git/index').read_bytes()
    checked = invoke(fixture, 'check')
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)['status'] == 'baseline_verified'
    assert (target / '.git/index').read_bytes() == before
    assert (source / '.git/index').read_bytes() == index


def replace_contract(fixture, **values):
    path = fixture[1]
    doc = json.loads(path.read_text())
    doc.update(values)
    path.write_text(json.dumps(doc))
    return doc


def assert_blocked(result, reason=None):
    assert result.returncode != 0, result.stdout + result.stderr
    doc = json.loads(result.stdout)
    assert doc['status'] == 'blocked'
    assert doc['exit_code'] == result.returncode
    if reason:
        assert doc['reason_code'] == reason, doc
    return doc


@pytest.mark.parametrize('ref', ['HEAD', 'main', 'refs/heads/main', 'refs/tags/v1',
    '--all', 'origin/main~1', 'origin/a..b', 'origin/main\n', 'origin/'])
def test_ref_contract_rejects_noncanonical_names_before_clone(fixture, ref):
    replace_contract(fixture, base_ref=ref)
    assert_blocked(invoke(fixture))
    assert not fixture[2].exists()


@pytest.mark.parametrize('field,value', [
    ('expected_head_oid', 'abc123'), ('expected_base_oid', 'z'*40),
    ('expected_head_oid', '0'*40), ('quality_config_sha256', '0'*64),
    ('expected_changed_paths', ['other']), ('expected_changed_paths', ['VERSION', 'VERSION']),
    ('expected_changed_paths', ['../escape']), ('expected_changed_paths', ['.git/config']),
    ('expected_changed_paths', ['.']), ('expected_changed_paths', ['a//b'])])
def test_bad_pins_and_scopes_stop_before_clone(fixture, field, value):
    replace_contract(fixture, **{field: value})
    assert_blocked(invoke(fixture))
    assert not fixture[2].exists()


@pytest.mark.parametrize('kind', ['file', 'empty', 'populated', 'symlink', 'broken-link'])
def test_existing_target_is_never_adopted_or_deleted(fixture, kind):
    target = fixture[2]
    outside = target.parent/'outside'
    outside.mkdir()
    (outside/'keep').write_text('protected')
    if kind == 'file': target.write_text('keep')
    elif kind in ['empty', 'populated']:
        target.mkdir()
        if kind == 'populated': (target/'keep').write_text('protected')
    else: target.symlink_to(outside if kind == 'symlink' else target.parent/'absent', target_is_directory=True)
    assert_blocked(invoke(fixture))
    assert target.exists() or target.is_symlink()
    assert (outside/'keep').read_text() == 'protected'
    if kind == 'file': assert target.read_text() == 'keep'
    elif kind == 'populated': assert (target/'keep').read_text() == 'protected'


@pytest.mark.parametrize('kind', ['missing-ref', 'wrong-ref', 'nonancestor', 'dirty', 'staged', 'untracked', 'missing-source'])
def test_source_revalidation_does_not_substitute_another_baseline(fixture, kind):
    source, contract, target = fixture
    if kind == 'missing-ref': git(source, 'update-ref', '-d', 'refs/remotes/origin/main')
    elif kind == 'wrong-ref': git(source, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    elif kind == 'nonancestor':
        oid = git(source, 'commit-tree', git(source, 'rev-parse', 'HEAD^{tree}'), '-m', 'orphan')
        git(source, 'update-ref', 'refs/remotes/origin/main', oid)
        replace_contract(fixture, expected_base_oid=oid)
    elif kind in ['dirty', 'staged']:
        (source/'VERSION').write_text('unrelated edit')
        if kind == 'staged': git(source, 'add', 'VERSION')
    elif kind == 'untracked': (source/'unexpected.config').write_text('keep')
    else: replace_contract(fixture, source_root=str(source.parent/'missing'))
    before = (source/'.git/index').read_bytes()
    assert_blocked(invoke(fixture))
    assert not target.exists()
    assert (source/'.git/index').read_bytes() == before


@pytest.mark.parametrize('kind', ['base', 'head', 'config', 'dirty', 'staged', 'untracked', 'mode'])
def test_read_only_check_rejects_changed_candidate_without_repair(fixture, kind):
    assert invoke(fixture).returncode == 0
    target = fixture[2]
    if kind == 'base': git(target, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    elif kind == 'head': git(target, 'checkout', '--detach', 'HEAD~1')
    elif kind == 'config': (target/'.omc/quality-gates.json').write_text('{}')
    elif kind in ['dirty', 'staged']:
        (target/'VERSION').write_text('new edit')
        if kind == 'staged': git(target, 'add', 'VERSION')
    elif kind == 'untracked': (target/'data.csv').write_text('untracked')
    else: (target/'VERSION').chmod(0o755)
    index = (target/'.git/index').read_bytes()
    version = (target/'VERSION').read_bytes()
    assert_blocked(invoke(fixture, 'check'))
    assert (target/'.git/index').read_bytes() == index
    assert (target/'VERSION').read_bytes() == version


def test_public_success_preserves_raw_evidence_and_does_not_issue_approval(fixture):
    source, contract, target = fixture
    state = source/'.omc/state/review-snapshots'
    state.mkdir(parents=True)
    (state/'current.json').write_text('{"protected":"original"}')
    result = invoke(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(result.stdout)
    assert doc['source_preserved'] and doc['cleanup_confirmed'] and doc['evidence_complete']
    for row in doc['raw_evidence']:
        path = contract.parent/'records-prepare'/row['name']
        assert digest(path) == row['sha256']
        assert path.stat().st_mode & 0o777 == 0o600
    assert not (target/'.omc/state').exists()
    assert not (target/'.omc/state/quality-gate-approval.json').exists()
    assert (state/'current.json').read_text() == '{"protected":"original"}'
    assert all(row['argv'][0] == 'git' for row in doc['commands'])
    disk = json.loads((contract.parent/'records-prepare/final.json').read_text())
    assert disk['status'] == 'blocked' and disk['record_kind'] == 'evidence_only'
    assert disk['observed_check_status'] == 'baseline_verified'


def shim_env(tmp_path, body):
    import shutil
    folder = tmp_path/'bin'
    folder.mkdir()
    real = shutil.which('git')
    script = folder/'git'
    script.write_text('#!'+sys.executable+'\nimport os,sys,subprocess,time\nfrom pathlib import Path\n'
        +'args=sys.argv[1:]\nreal='+repr(real)+'\n'+body+'\nos.execv(real,[real,*args])\n')
    script.chmod(0o755)
    return {**os.environ, 'PATH': str(folder)+os.pathsep+os.environ['PATH']}


@pytest.mark.parametrize('when', ['clone', 'for-each-ref'])
def test_cli_detects_directory_replacement_before_ref_or_config_write(fixture, tmp_path, when):
    source, contract, target = fixture
    env = shim_env(tmp_path, """
if args[6] == %r and (args[6] == 'clone' or Path.cwd() == Path(%r)):
    p=subprocess.run([real,*args]); assert p.returncode==0
    dest=Path(%r); dest.rename(dest.with_name('owned-original'))
    dest.mkdir(); (dest/'keep').write_text('do not touch')
    sys.exit(0)
""" % (when, str(target), str(target)))
    doc = assert_blocked(invoke(fixture, env=env), 'directory_identity_changed')
    assert (target/'keep').read_text() == 'do not touch'
    assert not (target/'.git').exists() and not (target/'.omc').exists()
    assert doc['cleanup_confirmed']


def test_cli_detects_source_changes_during_clone_without_undoing_them(fixture, tmp_path):
    source, contract, target = fixture
    env = shim_env(tmp_path, """
if args[6] == 'clone':
    p=subprocess.run([real,*args]); assert p.returncode==0
    Path(%r).write_text('external change')
    sys.exit(0)
""" % str(source/'VERSION'))
    assert_blocked(invoke(fixture, env=env), 'source_preservation_mismatch')
    assert (source/'VERSION').read_text() == 'external change'


def test_clone_error_keeps_full_stderr_and_partial_target(fixture, tmp_path):
    env = shim_env(tmp_path, """
if args[6] == 'clone':
    os.write(2,b'failure-original\\n'*10000)
    sys.exit(17)
""")
    doc = assert_blocked(invoke(fixture, env=env), 'git_failed')
    row = doc['commands'][-1]
    assert row['exit_code'] == 17
    raw = fixture[1].parent/'records-prepare'/row['stderr']
    assert raw.read_bytes() == b'failure-original\n'*10000
    assert fixture[2].is_dir()
    assert doc['cleanup_confirmed']


def site_env(tmp_path, code):
    folder = tmp_path/'site'
    folder.mkdir()
    (folder/'sitecustomize.py').write_text(code)
    return {**os.environ, 'PYTHONPATH': str(folder)}


def test_probe_permission_error_is_unconfirmed_not_success(fixture, tmp_path):
    env = site_env(tmp_path, """
import os
original=os.killpg
def probe(pid,sig):
    if sig==0:raise PermissionError(1,'injected process probe failure')
    return original(pid,sig)
os.killpg=probe
""")
    doc = assert_blocked(invoke(fixture, env=env), 'cleanup_unconfirmed')
    assert doc['exit_code'] == 125 and not doc['cleanup_confirmed']
    assert doc['cleanup_errors'][0]['errno'] == 1
    record = fixture[1].parent/'records-prepare'
    assert (record/'partial.json').is_file() and not (record/'final.json').exists()


@pytest.mark.parametrize('failure', ['late', 'write-error', 'partial-config'])
def test_record_or_config_write_failure_never_becomes_a_successful_disk_result(fixture, tmp_path, failure):
    original_config = (fixture[0]/'.omc/quality-gates.json').read_bytes()
    env = site_env(tmp_path, """
import os,time
original=os.fdopen
clock=time.monotonic
state={'offset':0,'fired':False}
time.monotonic=lambda:clock()+state['offset']
class Proxy:
    def __init__(self,f):self.f=f
    def __getattr__(self,n):return getattr(self.f,n)
    def __enter__(self):return self
    def __exit__(self,*a):return self.f.__exit__(*a)
    def write(self,raw):
        hit=(b'record_kind' in raw) if %r!='partial-config' else (b'omc-quality-gates/v1' in raw)
        if hit and not state['fired']:
            state['fired']=True
            if %r=='late':
                n=self.f.write(raw);state['offset']=10000;return n
            self.f.write(raw[:8]);self.f.flush()
            raise OSError(28,'injected partial write')
        return self.f.write(raw)
def opened(*a,**k):return Proxy(original(*a,**k))
os.fdopen=opened
""" % (failure, failure))
    doc = assert_blocked(invoke(fixture, env=env))
    assert not (doc['status'] == 'baseline_verified')
    record = fixture[1].parent/'records-prepare'
    final = record/'final.json'
    if final.exists():
        try: saved = json.loads(final.read_text())
        except ValueError: saved = {'status': 'invalid'}
        assert saved['status'] != 'baseline_verified'
    if failure == 'partial-config':
        assert (fixture[0]/'.omc/quality-gates.json').read_bytes() == original_config
        assert (fixture[2]/'.omc/quality-gates.json').read_bytes() != (fixture[0]/'.omc/quality-gates.json').read_bytes()


def test_timeout_kills_only_owned_group_and_records_original_error(fixture, tmp_path):
    env = site_env(tmp_path, """
import subprocess,time
original=subprocess.Popen.wait
state={'fired':False}
def waited(self,*a,**k):
    if isinstance(self.args,list) and 'clone' in self.args and not state['fired']:
        state['fired']=True;time.sleep(.05)
        raise subprocess.TimeoutExpired(self.args,120)
    return original(self,*a,**k)
subprocess.Popen.wait=waited
""")
    doc = assert_blocked(invoke(fixture, env=env), 'git_timeout')
    assert doc['exit_code'] == 124 and doc['cleanup_confirmed']
    assert all(p['confirmed'] for p in doc['processes'])
    assert doc['cleanup_deadline_monotonic'] <= doc['work_terminal_monotonic'] + 20
    assert doc['record_deadline_monotonic'] <= doc['cleanup_deadline_monotonic'] + 10


@pytest.mark.parametrize('delta', [-0.00001, 0, 0.00001])
def test_public_work_deadline_before_equal_after(fixture, tmp_path, delta):
    env = site_env(tmp_path, """
import time,subprocess
clock=time.monotonic
state={'start':None,'fixed':None,'count':0}
def now():
    if state['fixed'] is not None:return state['fixed']
    value=clock()
    if state['start'] is None:state['start']=value
    return value
time.monotonic=now
original=subprocess.Popen.wait
def waited(self,*a,**k):
    result=original(self,*a,**k)
    if isinstance(self.args,list) and 'check-ref-format' in self.args:
        state['count']+=1
        if state['count']==5:state['fixed']=state['start']+210+%r
    return result
subprocess.Popen.wait=waited
""" % delta)
    result = invoke(fixture, env=env)
    if delta < 0:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        doc = assert_blocked(result, 'work_deadline_exceeded')
        assert doc['exit_code'] == 124 and doc['cleanup_confirmed']


@pytest.mark.parametrize('delta', [-0.00001, 0, 0.00001])
def test_public_final_record_deadline_before_equal_after(fixture, tmp_path, delta):
    env = site_env(tmp_path, """
import time,os,json
clock=time.monotonic
state={'fixed':None}
time.monotonic=lambda:clock() if state['fixed'] is None else state['fixed']
original=os.fdopen
class Proxy:
    def __init__(self,f):self.f=f
    def __getattr__(self,n):return getattr(self.f,n)
    def __enter__(self):return self
    def __exit__(self,*a):return self.f.__exit__(*a)
    def write(self,raw):
        n=self.f.write(raw)
        if b'record_kind' in raw:
            state['fixed']=json.loads(raw)['record_deadline_monotonic']+%r
        return n
os.fdopen=lambda *a,**k:Proxy(original(*a,**k))
""" % delta)
    result = invoke(fixture, env=env)
    if delta < 0:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        doc = assert_blocked(result, 'evidence_incomplete')
        assert not doc['evidence_complete']
        stored = json.loads((fixture[1].parent/'records-prepare/final.json').read_text())
        assert stored['status'] == 'blocked'


@pytest.mark.parametrize('kind', ['overlap', 'record-exists', 'record-link', 'shared-git'])
def test_public_output_and_git_ownership_boundaries(fixture, kind, tmp_path):
    source, contract, target = fixture
    if kind == 'overlap':
        result = invoke((source, contract, source))
    elif kind == 'record-exists':
        record = tmp_path/'records-prepare';record.mkdir();(record/'keep').write_text('protected')
        result = invoke(fixture)
        assert (record/'keep').read_text() == 'protected'
    elif kind == 'record-link':
        outside = tmp_path/'outside';outside.mkdir()
        (tmp_path/'records-prepare').symlink_to(outside, target_is_directory=True)
        result = invoke(fixture)
        assert not list(outside.iterdir())
    else:
        target.mkdir();(target/'.git').write_text('gitdir: '+str(source/'.git')+'\n')
        before = (source/'.git/index').read_bytes()
        result = invoke(fixture, 'check')
        assert (source/'.git/index').read_bytes() == before
    assert_blocked(result)


def test_public_wrong_config_ref_is_not_rewritten_to_match_contract(fixture):
    source, contract, target = fixture
    config = source/'.omc/quality-gates.json'
    doc = json.loads(config.read_text());doc['base_ref'] = 'origin/other'
    config.write_text(json.dumps(doc))
    replace_contract(fixture, quality_config_sha256=digest(config))
    before = config.read_bytes()
    assert_blocked(invoke(fixture), 'config_base_ref_mismatch')
    assert config.read_bytes() == before and not target.exists()


def test_timeout_reaps_a_real_owned_descendant_group(fixture, tmp_path):
    marker = tmp_path/'child-ready'
    env = shim_env(tmp_path, """
if args[6]=='clone':
    import signal
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    def stop(sig,frame):
        try:child.wait(timeout=2)
        finally:sys.exit(143)
    signal.signal(signal.SIGTERM,stop)
    Path(%r).write_text(str(child.pid))
    child.wait()
    sys.exit(0)
""" % str(marker))
    site = site_env(tmp_path, """
import subprocess,time
from pathlib import Path
original=subprocess.Popen.wait
state={'fired':False}
def waited(self,*a,**k):
    if isinstance(self.args,list) and 'clone' in self.args and not state['fired']:
        state['fired']=True
        end=time.monotonic()+2
        while not Path(%r).exists() and time.monotonic()<end:time.sleep(.01)
        raise subprocess.TimeoutExpired(self.args,120)
    return original(self,*a,**k)
subprocess.Popen.wait=waited
""" % str(marker))
    env['PYTHONPATH'] = site['PYTHONPATH']
    result = invoke(fixture, env=env)
    doc = assert_blocked(result, 'git_timeout')
    assert doc['cleanup_confirmed'] and result.returncode == 124
    assert marker.is_file()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    for row in doc['processes']:
        with pytest.raises(ProcessLookupError): os.killpg(row['pgid'], 0)


@pytest.mark.parametrize('flag', ['--assume-unchanged', '--skip-worktree'])
@pytest.mark.parametrize('scenario', ['changed', 'deleted', 'clean', 'mode', 'symlink'])
@pytest.mark.parametrize('operation', ['prepare', 'check'])
def test_fixed_head_comparison_ignores_index_hints(fixture, flag, scenario, operation):
    source, contract, target = fixture
    if operation == 'check':
        result = invoke(fixture)
        assert result.returncode == 0, result.stdout + result.stderr
    root = source if operation == 'prepare' else target
    git(root, 'update-index', flag, 'VERSION')
    path = root/'VERSION'
    if scenario == 'changed': path.write_text('unreviewed hidden bytes\n')
    elif scenario == 'deleted': path.unlink()
    elif scenario == 'mode': path.chmod(0o755)
    elif scenario == 'symlink':
        path.unlink()
        path.symlink_to('commands.md')
    before = (root/'.git/index').read_bytes()
    flags = git(root, 'ls-files', '-v')
    result = invoke(fixture, operation)
    if scenario == 'clean':
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)['status'] == 'baseline_verified'
    else:
        assert_blocked(result)
    assert (root/'.git/index').read_bytes() == before
    assert git(root, 'ls-files', '-v') == flags
    if operation == 'prepare' and scenario != 'clean':
        assert not target.exists(), 'a mismatching source must stop before clone'


@pytest.mark.parametrize('flag', ['--assume-unchanged', '--skip-worktree'])
@pytest.mark.parametrize('scenario', ['clean', 'changed', 'deleted'])
def test_tracked_symlink_is_checked_as_head_blob_without_following_it(fixture, flag, scenario):
    source, contract, target = fixture
    (source/'link').symlink_to('VERSION')
    git(source, 'add', 'link')
    git(source, 'commit', '-m', 'tracked symlink')
    replace_contract(fixture, expected_head_oid=git(source, 'rev-parse', 'HEAD'),
                     expected_changed_paths=['VERSION', 'link'])
    prepared = invoke(fixture)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    git(target, 'update-index', flag, 'link')
    if scenario != 'clean':
        (target/'link').unlink()
        if scenario == 'changed': (target/'link').symlink_to('commands.md')
    before = (target/'.git/index').read_bytes()
    result = invoke(fixture, 'check')
    if scenario == 'clean':
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert_blocked(result)
    assert (target/'.git/index').read_bytes() == before
