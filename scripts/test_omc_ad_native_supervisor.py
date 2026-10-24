"""Public subprocess surfaces; synthetic children, never a provider call."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time

import pytest

CLI = Path(__file__).with_name('omc_ad_native_supervisor.py')
ROOT = CLI.parent.parent
CORE = '637a9653759cfca9e070450139dfa56cdd67c5d1'
SUCCESS = '{"type":"result","subtype":"success","is_error":false}\n'
RECORDER = ('printf "%s\\n" "$$" > "$OMC_CHILD_PID_RECORD"; '
            '"$@"; child_status=$?; printf "%s\\n" "$child_status" '
            '> "$OMC_CHILD_EXIT_RECORD"; exit "$child_status"')


def invoke(tmp_path, code, *, wrapper=None, timeout=3, drain=1, name='out', env=None, caller=None):
    prompt = tmp_path / 'prompt.txt'
    if not prompt.exists():
        prompt.write_text('synthetic prompt\n')
    pid, exit_file = tmp_path / 'child.pid', tmp_path / 'child.exit'
    launch = wrapper or ['/bin/sh', '-c', RECORDER, 'native-launch', sys.executable, '-c', code]
    argv = [sys.executable, str(CLI), '--cwd', str(tmp_path), '--output', str(tmp_path/name),
            '--prompt', str(prompt), '--child-pid-file', str(pid),
            '--child-exit-file', str(exit_file), '--timeout', str(timeout),
            '--drain', str(drain), '--', *launch]
    before = time.monotonic()
    p = subprocess.run(argv, cwd=caller, capture_output=True, env={**os.environ, **(env or {})}, timeout=20)
    result_file = tmp_path/name/'result.json'
    result = json.loads(result_file.read_bytes()) if result_file.is_file() else None
    if wrapper is None and result is not None and result['child_cleanup_status'] != 'confirmed' and pid.exists():
        # Only groups created by this test recorder; never revise the result.
        try:
            os.killpg(int(pid.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass
    return p, result, time.monotonic()-before, argv


def test_public_normal_execution_and_raw_bytes(tmp_path):
    p, r, _, _ = invoke(tmp_path, f'import sys;sys.stdin.read();print({SUCCESS!r},end="");sys.stderr.write("err-tail")')
    assert p.returncode == 0, p.stderr
    assert r['status'] == 'normal'
    assert r['child_cleanup_status'] == 'confirmed'
    assert r['normal_terminal_result_count'] == 1
    assert (tmp_path/'out/stdout.raw').read_bytes() == SUCCESS.encode()
    assert (tmp_path/'out/stderr.raw').read_bytes() == b'err-tail'
    assert r['streams']['stdout']['sha256'] == hashlib.sha256(SUCCESS.encode()).hexdigest()


def test_partial_prompt_delivery_cannot_be_normal(tmp_path):
    (tmp_path/'prompt.txt').write_bytes(b'x'*4000000)
    code=f'import sys;sys.stdin.buffer.read(1);print({SUCCESS!r},end="")'
    p,r,_,_=invoke(tmp_path,code)
    assert p.returncode==1 and r['status']!='normal'
    assert not r['input']['complete']
    assert r['input']['bytes_sent']<r['input']['expected_bytes']


def test_full_prompt_delivery_is_bound_to_snapshot(tmp_path):
    payload=b'x'*4000000
    (tmp_path/'prompt.txt').write_bytes(payload)
    code='import sys,hashlib,json;raw=sys.stdin.buffer.read();print(json.dumps({"type":"result","subtype":"success","is_error":False,"input_sha256":hashlib.sha256(raw).hexdigest()}))'
    p,r,_,_=invoke(tmp_path,code)
    assert p.returncode==0 and r['input']['complete'],p.stderr
    assert r['input']['eof_sent'] and r['input']['bytes_sent']==len(payload)
    assert r['input']['sha256_sent']==hashlib.sha256(payload).hexdigest()


def test_empty_prompt_sends_eof_and_records_empty_hash(tmp_path):
    (tmp_path/'prompt.txt').write_bytes(b'')
    p,r,_,_=invoke(tmp_path,f'import sys;assert sys.stdin.buffer.read()==b"";print({SUCCESS!r},end="")')
    assert p.returncode==0 and r['input']['complete'] and r['input']['eof_sent']
    assert r['input']['bytes_sent']==r['input']['expected_bytes']==0
    assert r['input']['sha256_sent']==hashlib.sha256(b'').hexdigest()


def test_prompt_replacement_after_launch_uses_frozen_bytes(tmp_path):
    prompt=tmp_path/'prompt.txt';payload=b'x'*4000000;prompt.write_bytes(payload)
    code=f'import sys,hashlib,json;open({str(prompt)!r},"wb").write(b"y"*4000000);raw=sys.stdin.buffer.read();print(json.dumps({{"type":"result","subtype":"success","is_error":False,"input_sha256":hashlib.sha256(raw).hexdigest()}}))'
    p,r,_,_=invoke(tmp_path,code)
    row=json.loads((tmp_path/'out/stdout.raw').read_bytes())
    assert row['input_sha256']==hashlib.sha256(payload).hexdigest()
    assert p.returncode==0 and r['input']['complete']
    assert hashlib.sha256(prompt.read_bytes()).hexdigest()!=row['input_sha256']


@pytest.mark.parametrize('kind',['explicit-relative','relative-path-entry','empty-path-entry'])
def test_executable_hash_and_launch_share_candidate_resolution(tmp_path,kind):
    caller=tmp_path/'caller';caller.mkdir()
    template='#!'+sys.executable+'\nimport os,sys,json\nsys.stdin.buffer.read()\nopen(os.environ["OMC_CHILD_PID_RECORD"],"w").write(str(os.getpid()))\nopen(os.environ["OMC_CHILD_EXIT_RECORD"],"w").write("0")\nprint(json.dumps({"type":"result","subtype":"success","is_error":False,"variant":%r}))\n'
    sub='bin' if kind=='relative-path-entry' else ''
    for base,label in [(caller,'caller-A'),(tmp_path,'candidate-B')]:
        path=base/sub/'launch';path.parent.mkdir(exist_ok=True);path.write_text(template%label);path.chmod(0o755)
    token='./launch' if kind=='explicit-relative' else 'launch'
    env={'PATH':('bin' if sub else '')+os.pathsep+os.environ['PATH']}
    p,r,_,_=invoke(tmp_path,'',wrapper=[token],caller=caller,env=env)
    actual=(tmp_path/sub/'launch').resolve()
    inv=json.loads((tmp_path/'out/invocation.json').read_bytes())
    assert p.returncode==0 and r['status']=='normal'
    assert str(actual) in inv['launch_file_sha256']
    assert inv['argv'][0]==str(actual)
    assert inv['launch_file_sha256'][str(actual)]==hashlib.sha256(actual.read_bytes()).hexdigest()
    assert json.loads((tmp_path/'out/stdout.raw').read_bytes())['variant']=='candidate-B'


@pytest.mark.parametrize('local_file',['executable','nonexecutable','absent'])
def test_path_miss_never_falls_back_to_candidate_file(tmp_path,local_file):
    name='omc-local-only-surface-probe'
    marker=tmp_path/'unexpected-start'
    if local_file!='absent':
        script='#!'+sys.executable+f'\nopen({str(marker)!r},"w").write("started")\n'
        (tmp_path/name).write_text(script)
        (tmp_path/name).chmod(0o755 if local_file=='executable' else 0o644)
    # Candidate cwd is deliberately not on PATH.
    p,r,_,_=invoke(tmp_path,'',wrapper=[name],env={'PATH':'/usr/bin:/bin'})
    assert not marker.exists()
    assert p.returncode==2 and r is not None
    assert not r['wrapper_started'] and not r['attempt_consumed']
    assert r['launch_error']=='executable_not_on_path'
    inv=json.loads((tmp_path/'out/invocation.json').read_bytes())
    assert inv['requested_argv']==[name] and inv['argv']==[]
    assert inv['launch_file_sha256']=={}
    assert not (tmp_path/'child.pid').exists() and not (tmp_path/'child.exit').exists()


@pytest.fixture(scope='module', params=[CORE, '996d80d539d7245443f3a539f1a9fa333b450770'],
                ids=['core637a965', 'core996d80d'])
def core_commit(request):
    return request.param


@pytest.fixture(scope='module')
def frozen_core(tmp_path_factory, core_commit):
    root = tmp_path_factory.mktemp('supervisor-core')
    archive = root/'core.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', 'archive', core_commit, 'scripts'], cwd=ROOT, stdout=stream, check=True)
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            assert (member.name == 'scripts' or member.name.startswith('scripts/')) and '..' not in Path(member.name).parts
            assert member.isdir() or member.isfile()
        stream.extractall(root)
    expected = subprocess.check_output(['git', 'show', core_commit+':scripts/omc_home_observation.py'], cwd=ROOT)
    assert (root/'scripts/omc_home_observation.py').read_bytes() == expected
    return root


def core_candidate(tmp_path, frozen_core, core_commit):
    # Real frozen state, home requirement, basis and CONTRACT; no fake core guard.
    setup = '''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import omc_state,omc_home_observation as home,omc_pipeline_guard as guard
root=Path(sys.argv[2]); (root/'.omc').mkdir(exist_ok=True)
s=omc_state.record_session(root,mode='autopilot',title='synthetic supervisor',request='local supervisor surface',role_ids=['senior_coding'],confirmed=True,work_class='benchmark_maintenance',completion_action='start')
ready=root/'.omc/ready.json'
ready.write_text(json.dumps({'status':'ready','target':str(root),'required_check_ids':['browser-home'],'server_binding':{'browser':{'origin':'http://localhost:3999','home_path':'/sixshoptest01'}}}))
req=home.initialize(root,'.omc/ready.json','supervisor-fixture',s['session_id'])
basis={'session_id':s['session_id'],'run_id':'supervisor-fixture','revision':1,'request':s['request'],'approval_reference':'synthetic local CLI fixture','checks':[{'id':'browser-home','target':'synthetic stream only','method':'local child','pass_condition':'stream custody only','target_version':'core'+sys.argv[3][:7],'home_motion_sha256':req['home_motion_sha256']}]}
p=root/'.omc/proposal.json';p.write_text(json.dumps(basis))
assert guard.cmd_approve_checks(root,p)==0
assert guard.cmd_contract_done(root,required_checks='browser-home')==0
'''
    p = subprocess.run([sys.executable, '-c', setup, str(frozen_core/'scripts'), str(tmp_path), core_commit], capture_output=True, timeout=20)
    assert p.returncode == 0, p.stdout+p.stderr
    return [sys.executable, str(frozen_core/'scripts/omc_home_observation.py'), 'run', '--target', str(tmp_path), '--launch']


@pytest.mark.parametrize('case', ['normal', 'internal124', 'timeout', 'missing'])
def test_fixed_core_public_four_surfaces(tmp_path, frozen_core, core_commit, case):
    prefix = core_candidate(tmp_path, frozen_core, core_commit)
    code = {'normal': f'print({SUCCESS!r},end="")', 'internal124': 'import sys;sys.stderr.write("internal124");sys.exit(124)',
            'timeout': 'import time;print("{\\"type\\":\\"system\\"}",flush=True);time.sleep(30)', 'missing': 'pass'}[case]
    p, r, elapsed, _ = invoke(tmp_path, code, wrapper=prefix+['/bin/sh','-c',RECORDER,'native-launch',sys.executable,'-c',code], timeout=.7 if case=='timeout' else 5, drain=2)
    assert r is not None, p.stderr
    assert (tmp_path/'out/stdout.raw').read_bytes() == (tmp_path/'.omc/home-motion/events.jsonl').read_bytes()
    assert r['wrapper_started'] and r['attempt_consumed']
    pin=json.loads((tmp_path/'out/invocation.json').read_bytes())['launch_file_sha256']
    core_path=frozen_core/'scripts/omc_home_observation.py'
    assert pin[str(core_path)]==hashlib.sha256(core_path.read_bytes()).hexdigest()
    if case == 'normal':
        assert p.returncode == 0 and r['status'] == 'normal', r
    else:
        assert p.returncode == 1 and r['status'] != 'normal'
    if case == 'internal124':
        assert r['child_exit_code'] == 124 and r['wrapper_exit_code'] == 124
        assert r['internal_124'] and not r['external_timeout']
    if case == 'timeout':
        assert r['external_timeout'] and elapsed < 4
        diagnostics = list((tmp_path/'.omc/native-stream-errors').glob('*.json'))
        assert diagnostics, r
    if case == 'missing':
        assert r['wrapper_exit_code'] == 0 and r['normal_terminal_result_count'] == 0


@pytest.mark.parametrize('output', [SUCCESS*2, '{bad}\n', SUCCESS.rstrip('\n'), 'x'*(1048576+1)+'\n', '{"type":"result","subtype":"success","is_error":true}\n'], ids=['duplicate','invalid','partial','oversize','error-result'])
def test_json_boundaries_are_not_normal(tmp_path, output):
    payload = tmp_path/'payload'
    payload.write_text(output)
    p,r,_,_=invoke(tmp_path, f'import sys;sys.stdout.buffer.write(open({str(payload)!r},"rb").read())')
    assert p.returncode == 1 and r['status'] != 'normal'
    assert (tmp_path/'out/stdout.raw').read_bytes() == output.encode()
    assert r['parser_max_buffer_bytes'] <= 1048576


def test_second_launch_does_not_start_child(tmp_path):
    invoke(tmp_path, f'print({SUCCESS!r},end="")')
    marker=tmp_path/'second'
    p,_,_,_=invoke(tmp_path,f'open({str(marker)!r},"w").write("started")')
    assert p.returncode == 2 and not marker.exists()


@pytest.mark.parametrize('bad', ['nan', 'negative', 'drain', 'outside', 'symlink', 'collision'])
def test_invalid_inputs_never_start_wrapper(tmp_path, bad):
    prompt=tmp_path/'prompt';prompt.write_text('input')
    marker=tmp_path/'started'
    argv=[sys.executable,str(CLI),'--cwd',str(tmp_path),'--output',str(tmp_path/'out'),
          '--prompt',str(prompt),'--child-pid-file',str(tmp_path/'pid'),
          '--child-exit-file',str(tmp_path/'exit'),'--timeout','2','--drain','1']
    if bad=='nan':argv[-3]='nan'
    elif bad=='negative':argv[-3]='-1'
    elif bad=='drain':argv[-1]='13'
    elif bad=='outside':argv[argv.index(str(tmp_path/'pid'))]=str(tmp_path.parent/'outside')
    elif bad=='symlink':(tmp_path/'pid').symlink_to(prompt)
    else:argv[argv.index(str(tmp_path/'exit'))]=str(tmp_path/'pid')
    p=subprocess.run([*argv,'--',sys.executable,'-c',f'open({str(marker)!r},"w").write("x")'],capture_output=True,timeout=5)
    assert p.returncode==2 and not marker.exists()


def test_spawn_failure_retains_claim_and_reports_not_started(tmp_path):
    p,r,_,_=invoke(tmp_path,'',wrapper=[str(tmp_path/'no-executable')])
    assert p.returncode==2 and not r['wrapper_started'] and not r['attempt_consumed']
    assert r['status']!='normal' and (tmp_path/'out/invocation.json').exists()


def test_result_record_failure_uses_stderr_and_nonzero(tmp_path):
    code=f'import os;os.mkdir({str(tmp_path/"out/result.json")!r});print({SUCCESS!r},end="")'
    p,r,_,_=invoke(tmp_path,code)
    assert p.returncode==1 and b'result_record_failed' in p.stderr
    assert (tmp_path/'out/stdout.raw').read_bytes()==SUCCESS.encode()


def test_nonregular_child_record_does_not_block_supervisor(tmp_path):
    code=f'import os;os.mkfifo(os.environ["OMC_CHILD_PID_RECORD"]);print({SUCCESS!r},end="")'
    p,r,elapsed,_=invoke(tmp_path,code,wrapper=[sys.executable,'-c',code],timeout=.3,drain=.3)
    assert p.returncode==1 and r['child_cleanup_status']=='unconfirmed' and elapsed<2


def test_core_sources_preserved_after_surfaces(frozen_core, core_commit):
    for name in ['omc_home_observation.py','omc_pipeline_guard.py','omc_native_red_evidence.py']:
        raw=subprocess.check_output(['git','show',core_commit+':scripts/'+name],cwd=ROOT)
        assert (frozen_core/'scripts'/name).read_bytes()==raw


def test_b2_large_unterminated_dual_output_timeout(tmp_path):
    code='import os,time\nwhile True:\n os.write(1,b"x"*65536);os.write(2,b"e"*65536);time.sleep(.001)'
    p,r,elapsed,_=invoke(tmp_path,code,timeout=.25,drain=.4)
    assert p.returncode == 1 and r['external_timeout'] and elapsed<2
    assert r['streams']['stdout']['bytes'] > 1048576
    assert r['streams']['stderr']['bytes'] > 1048576
    assert r['parser_max_buffer_bytes'] <= 1048576


def test_b3_unread_prompt_does_not_block_deadline(tmp_path):
    (tmp_path/'prompt.txt').write_bytes(b'x'*4000000)
    p,r,elapsed,_=invoke(tmp_path,'import time;time.sleep(30)',timeout=.2,drain=.3)
    assert p.returncode == 1 and r['external_timeout'] and elapsed<2


def test_b4_output_storage_failure(tmp_path):
    # POSIX RLIMIT_FSIZE causes a real EFBIG on test-owned raw files only.
    launcher='import os,resource,signal,sys;signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(12000,12000));os.execv(sys.executable,[sys.executable]+sys.argv[1:])'
    _,_,_,argv=invoke(tmp_path,f'print({SUCCESS!r},end="")',name='seed')
    (tmp_path/'child.pid').unlink();(tmp_path/'child.exit').unlink()
    argv[argv.index(str(tmp_path/'seed'))]=str(tmp_path/'limited')
    argv[-1]='import os;os.write(1,b"x"*50000)'
    p=subprocess.run([sys.executable,'-c',launcher,*argv[1:]],capture_output=True,timeout=10)
    r=json.loads((tmp_path/'limited/result.json').read_bytes())
    assert p.returncode == 1 and not r['raw_capture_complete']
    assert r['storage_errors'] and (tmp_path/'limited/stdout.raw').stat().st_size == 12000


@pytest.mark.parametrize('sig', [signal.SIGINT,signal.SIGTERM])
def test_b5_external_supervisor_signal(tmp_path,sig):
    _,_,_,argv=invoke(tmp_path,'pass',name='seed')
    argv[argv.index(str(tmp_path/'seed'))]=str(tmp_path/'signalled')
    (tmp_path/'child.pid').unlink();(tmp_path/'child.exit').unlink()
    argv[-1]='import time;time.sleep(30)'
    p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/'child.pid').exists() and time.monotonic()<deadline:time.sleep(.01)
        assert (tmp_path/'child.pid').exists()
        p.send_signal(sig);p.communicate(timeout=5)
        r=json.loads((tmp_path/'signalled/result.json').read_bytes())
        assert p.returncode == 1 and r['interrupted'] == sig
    finally:
        if p.poll() is None:p.kill();p.wait()
        child=int((tmp_path/'child.pid').read_text())
        try:os.killpg(child,signal.SIGKILL)
        except ProcessLookupError:pass


@pytest.mark.parametrize('scenario',['stuck-wrapper','inherited-pipe'])
def test_b1_b6_detached_child_not_falsely_clean(tmp_path,scenario):
    # Owned deliberately faulty wrapper; its child setsid and ignores TERM.
    code='''import os,signal,time
pid=os.fork()
if pid==0:
 os.setsid();signal.signal(signal.SIGTERM,signal.SIG_IGN)
 open(os.environ['OMC_CHILD_PID_RECORD'],'w').write(str(os.getpid()))
 time.sleep(30);os._exit(0)
signal.signal(signal.SIGTERM,signal.SIG_IGN)
'''+ ('time.sleep(30)' if scenario=='stuck-wrapper' else 'time.sleep(.1)')
    try:
        p,r,elapsed,_=invoke(tmp_path,code,wrapper=[sys.executable,'-c',code],timeout=.3,drain=.4)
        assert p.returncode == 1 and elapsed<2
        assert r['child_cleanup_status'] in ('unconfirmed','incomplete')
        assert not r['raw_capture_complete']
    finally:
        if (tmp_path/'child.pid').exists():
            # Test cleanup is separate from supervisor result.
            try:os.killpg(int((tmp_path/'child.pid').read_text()),signal.SIGKILL)
            except ProcessLookupError:pass


def test_selected_core_identity_reaches_public_basis(tmp_path, frozen_core, core_commit):
    core_candidate(tmp_path, frozen_core, core_commit)
    expected = subprocess.check_output(
        ['git', 'show', core_commit+':scripts/omc_home_observation.py'], cwd=ROOT)
    assert (frozen_core/'scripts/omc_home_observation.py').read_bytes() == expected
    proposal = json.loads((tmp_path/'.omc/proposal.json').read_bytes())
    assert proposal['checks'][0]['target_version'] == 'core'+core_commit[:7]
