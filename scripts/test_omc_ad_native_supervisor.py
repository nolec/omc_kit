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
CURRENT_CORE = '2abc549b16a2ca0c40484bd95828861c5cd432b5'
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


def test_v3_cleanup_deadline_shares_budget_and_preserves_late_unknown():
    import omc_ad_native_supervisor as supervisor
    assert supervisor.cleanup_deadline(30, 0) == 42
    assert supervisor.cleanup_deadline(1760, 0) == 1772
    assert supervisor.cleanup_deadline(1775, 0) == 1772
    assert supervisor.phase_status(42, 42) == 'passed'
    assert supervisor.phase_status(42.001, 42) == 'failed'
    assert supervisor.phase_status(None, 42) == 'unknown'


def absolute_invoke(tmp_path, *, fifo=False, code=None, evidence=None, interrupt=False):
    prompt=tmp_path/'prompt'; runtime=tmp_path/'.omc';runtime.mkdir(exist_ok=True)
    prompt.write_text('synthetic input')
    origin=time.monotonic(); deadline=origin+18
    argv=[sys.executable,str(CLI),'--cwd',str(tmp_path),'--output',str(tmp_path/'out'),
          '--prompt',str(prompt),'--child-pid-file',str(runtime/'child.pid'),
          '--child-exit-file',str(runtime/'child.exit'),
          '--origin-monotonic',str(origin),'--deadline-monotonic',str(deadline),
          '--run-id','v3-synthetic']
    if evidence:argv+=['--phase-evidence',str(evidence)]
    argv+=['--','/bin/sh','-c',RECORDER,'native-launch',sys.executable,'-c',
           code or f'import sys;sys.stdin.read();print({SUCCESS!r},end="")']
    if fifo:
        # Fault injection into public main, inherited by its real fork worker.
        shim=('import sys,time,os;sys.path.insert(0,'+repr(str(ROOT/'scripts'))+');'
              'import omc_ad_native_supervisor as m;old=m.digest;parent=os.getpid();'
              'm.digest=lambda p:(time.sleep(5) if os.getpid()!=parent else None,old(p))[1];'
              'raise SystemExit(m.main())')
        argv=[sys.executable,'-c',shim,*argv[2:]]
    if interrupt:
        proc=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            until=time.monotonic()+3
            ownership=tmp_path/'out/worker/ownership.json'
            while time.monotonic()<until:
                if ownership.exists() and len(json.loads(ownership.read_bytes())['groups'])>=1:
                    time.sleep(.15);break
                time.sleep(.01)
            proc.send_signal(signal.SIGTERM)
            out,err=proc.communicate(timeout=5)
            p=subprocess.CompletedProcess(argv,proc.returncode,out,err)
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
    else:p=subprocess.run(argv,capture_output=True,timeout=22)
    result=tmp_path/'out/result.json'
    return p,json.loads(result.read_bytes()) if result.exists() else None, time.monotonic()-origin


def test_v3_public_absolute_normal_without_phase_evidence_is_not_full_pass(tmp_path):
    p,r,elapsed=absolute_invoke(tmp_path)
    assert r is not None,p.stderr
    assert r['process_result']=='normal',r
    assert r['full_contract_pass'] is False and r['task_evidence']=='unknown'
    assert r['actual_home']=='NOT_VERIFIED' and r['comparison']=='HOLD'
    assert r['cleanup_result']=='confirmed' and elapsed<5


def test_v3_public_absolute_preprocessing_hang_is_bounded(tmp_path):
    p,r,elapsed=absolute_invoke(tmp_path,fifo=True)
    assert r is not None,p.stderr
    assert r['stop_reason']=='preprocessing_deadline'
    assert r['full_contract_pass'] is False
    assert elapsed<4
    assert r['cleanup_deadline_monotonic']<=r['stop_monotonic']+.121


def test_v3_missing_parent_final_record_is_unknown(tmp_path):
    import omc_ad_native_supervisor as supervisor
    assert supervisor.read_absolute_result(tmp_path/'missing','r')['status']=='unknown'


@pytest.mark.parametrize('value,expected',[(1759.999,'passed'),(1760,'passed'),(1760.001,'failed'),
                                         (None,'unknown'),(True,'unknown'),(float('nan'),'unknown')])
def test_v3_deterministic_boundary_clock(value,expected):
    import omc_ad_native_supervisor as supervisor
    assert supervisor.phase_status(value,1760)==expected


def test_v3_public_worker_stop_reclaims_owned_detached_group(tmp_path):
    code='import sys,signal,time;sys.stdin.read();signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'
    p,r,elapsed=absolute_invoke(tmp_path,code=code,interrupt=True)
    assert p.returncode==1 and elapsed<5
    assert r['stop_reason']=='supervisor_signal' and not r['full_contract_pass']
    assert r['signals_sent'],r
    # A late observation cannot replace the sealed cleanup verdict.
    original=r['cleanup_result']
    for row in r['owned_groups']:
        try:os.killpg(row['pid'],signal.SIGKILL)
        except ProcessLookupError:pass
    assert json.loads((tmp_path/'out/result.json').read_bytes())['cleanup_result']==original
    assert any(x['signal']==int(signal.SIGKILL) for x in r['signals_sent'])


def test_v3_final_seal_required_even_with_normal_result(tmp_path):
    import omc_ad_native_supervisor as supervisor
    p,r,_=absolute_invoke(tmp_path)
    assert supervisor.read_absolute_result(tmp_path/'out/result.json','v3-synthetic')['process_result']=='normal'
    (tmp_path/'out/seal.json').unlink()
    assert supervisor.read_absolute_result(tmp_path/'out/result.json','v3-synthetic')['status']=='unknown'


@pytest.mark.parametrize('origin,deadline',[('nan','1'),('1','inf'),('1','0')])
def test_v3_public_absolute_invalid_clock_never_spawns(tmp_path,origin,deadline):
    marker=tmp_path/'unexpected'
    args=[sys.executable,str(CLI),'--cwd',str(tmp_path),'--output',str(tmp_path/'out'),
          '--prompt',str(tmp_path/'missing'),'--child-pid-file',str(tmp_path/'pid'),
          '--child-exit-file',str(tmp_path/'exit'),'--origin-monotonic',origin,
          '--deadline-monotonic',deadline,'--run-id','invalid','--',sys.executable,'-c',
          f'open({str(marker)!r},"w").write("bad")']
    p=subprocess.run(args,capture_output=True,timeout=3)
    assert p.returncode==2 and not marker.exists() and not (tmp_path/'out').exists()


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


@pytest.fixture(scope='module', params=[CORE, '996d80d539d7245443f3a539f1a9fa333b450770',CURRENT_CORE],
                ids=['core637a965', 'core996d80d','core2abc549'])
def core_commit(request):
    return request.param


def archive_supervisor_core(tmp_path_factory,core_commit):
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


@pytest.fixture(scope='module')
def frozen_core(tmp_path_factory,core_commit):
    return archive_supervisor_core(tmp_path_factory,core_commit)


@pytest.fixture(scope='module')
def current_frozen_core(tmp_path_factory):
    return archive_supervisor_core(tmp_path_factory,CURRENT_CORE)


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


def termination_surface(tmp_path, code, *, mode='normal', tamper=None):
    """Current public supervisor -> real tee -> recorder shell -> synthetic child."""
    runtime = tmp_path/'.omc'; runtime.mkdir(exist_ok=True)
    prompt = tmp_path/'prompt.txt'; prompt.write_text('synthetic input')
    record = runtime/'termination.json'
    pid, exit_file = runtime/'child.pid', runtime/'child.exit'
    runner = ('import sys,os,json;from pathlib import Path;'
              'sys.path.insert(0,sys.argv[1]);from omc_home_observation import tee_native;'
              'os.environ["TEST_WRITER_PID"]=str(os.getpid());'
              'root=Path(sys.argv[2]);'
              'code=tee_native(root,sys.argv[3:],root/".omc/events.jsonl",[], '
              'termination_record=".omc/termination.json",termination_run_id="surface-v2");')
    if tamper == 'missing':
        runner += '(root/".omc/termination.json").unlink();'
    elif tamper == 'invalid-json':
        runner += '(root/".omc/termination.json").write_text("{broken");'
    elif tamper:
        runner += ('p=root/".omc/termination.json";d=json.loads(p.read_text());'
                   f'd.update(json.loads({json.dumps(tamper)!r}));p.write_text(json.dumps(d));')
    runner += 'raise SystemExit(code)'
    launch = [sys.executable,'-c',runner,str(ROOT/'scripts'),str(tmp_path),
              '/bin/sh','-c',RECORDER,'native-launch',sys.executable,'-c',code]
    argv = [sys.executable,str(CLI),'--cwd',str(tmp_path),'--output',str(tmp_path/'out'),
            '--prompt',str(prompt),'--child-pid-file',str(pid),'--child-exit-file',str(exit_file),
            '--termination-record',str(record),'--termination-run-id','surface-v2',
            '--timeout','2','--drain','1','--',*launch]
    env = {**os.environ,'OMC_CHILD_PID_RECORD':str(pid),'OMC_CHILD_EXIT_RECORD':str(exit_file)}
    proc = subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
    try:
        if mode == 'signal':
            deadline=time.monotonic()+5
            while not pid.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.01)
            if proc.poll() is None:proc.send_signal(signal.SIGTERM)
        stdout,stderr=proc.communicate(timeout=8)
        assert (tmp_path/'out/result.json').exists(),stderr.decode()
        result=json.loads((tmp_path/'out/result.json').read_text())
        observed=(json.loads(record.read_text()) if record.is_file() and tamper!='invalid-json' else None)
        return proc.returncode,result,observed,stderr
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
        if pid.exists():
            try:os.killpg(int(pid.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass


@pytest.mark.parametrize('code,expected',[(f'print({SUCCESS!r},end="")',0),('import sys;sys.exit(23)',23)])
def test_termination_public_normal_and_nonzero(tmp_path,code,expected):
    status,result,record,_=termination_surface(tmp_path,code)
    assert record['direct_child_returncode']==expected
    assert record['claude_pid'] is None and record['claude_returncode'] is None
    assert record['wait_completed'] and record['process_group_absent']
    assert result['termination_observation']['status']=='verified'
    assert result['child_cleanup_status']=='confirmed'
    assert status==(0 if expected==0 else 1)


def test_termination_public_signal_preserves_shell_without_exit_file(tmp_path):
    status,result,record,_=termination_surface(tmp_path,'import time;time.sleep(30)',mode='signal')
    assert status==1 and result['status']=='incomplete_or_failed'
    assert result['interrupted']==signal.SIGTERM and result['child_exit_code'] is None
    assert record['direct_child_returncode']==-signal.SIGKILL
    assert record['direct_child_signal']==signal.SIGKILL
    assert record['claude_returncode'] is None
    assert not (tmp_path/'.omc/child.exit').exists()
    assert result['child_cleanup_status']=='confirmed'
    assert result['termination_observation']['status']=='verified'


@pytest.mark.parametrize('tamper',[
    {'run_id':'another-run'}, {'writer_pid':1}, {'direct_child_pid':1},
    {'direct_child_returncode':17}, {'wait_completed':False}, {'process_group_absent':False},
    {'claude_returncode':0}, {'direct_child_returncode':True}, {'drain_complete':False},
    {'signals_sent':['not-a-signal-record']},
])
def test_termination_public_rejects_mismatch_or_incomplete(tmp_path,tamper):
    status,result,_,_=termination_surface(tmp_path,f'print({SUCCESS!r},end="")',tamper=tamper)
    assert status==1 and result['status']!='normal'
    assert result['child_cleanup_status']!='confirmed'


def test_termination_public_writer_killed_leaves_unknown(tmp_path):
    code='import os,signal,time;os.kill(int(os.environ["TEST_WRITER_PID"]),signal.SIGKILL);time.sleep(30)'
    status,result,record,_=termination_surface(tmp_path,code)
    assert status==1 and record is None
    assert result['termination_observation']['status']=='unknown'
    assert result['child_cleanup_status']!='confirmed'


def test_termination_public_storage_failure_not_hidden(tmp_path,monkeypatch):
    code='import os;os.mkdir(os.environ["TEST_TERMINATION_PATH"]);print('+repr(SUCCESS)+',end="")'
    monkeypatch.setenv('TEST_TERMINATION_PATH',str(tmp_path/'.omc/termination.json'))
    status,result,_,stderr=termination_surface(tmp_path,code)
    assert status==1 and b'termination_record_failed' in (tmp_path/'out/stderr.raw').read_bytes()
    assert result['termination_observation']['status']=='unknown'


@pytest.mark.parametrize('tamper',['missing','invalid-json'])
def test_termination_public_missing_or_broken_record_blocks(tmp_path,tamper):
    status,result,_,_=termination_surface(tmp_path,f'print({SUCCESS!r},end="")',tamper=tamper)
    assert status==1 and result['termination_observation']['status']=='unknown'
    assert result['child_cleanup_status']!='confirmed'


@pytest.mark.parametrize('timestamp',[
    10**400, -(10**400), float('inf'), float('-inf'), float('nan'), True, None, 'invalid',
],ids=['huge-integer','huge-negative-integer','infinity','negative-infinity','nan','bool','null','string'])
def test_termination_public_invalid_timestamp_still_records_result(tmp_path,timestamp):
    status,result,_,stderr=termination_surface(tmp_path,f'print({SUCCESS!r},end="")',
                                              tamper={'recorded_at':timestamp})
    assert status==1 and result['status']=='incomplete_or_failed'
    assert result['termination_observation']['status']=='unknown'
    assert result['termination_observation']['reason']
    assert result['child_cleanup_status']=='unconfirmed'
    assert result['normal_terminal_result_count']==1 and result['raw_capture_complete']
    assert (tmp_path/'out/stdout.raw').read_text()==SUCCESS
    assert b'Traceback' not in stderr


@pytest.mark.parametrize('timestamp',[0,1234567890.5],ids=['integer','float'])
def test_termination_public_finite_timestamp_keeps_normal_result(tmp_path,timestamp):
    status,result,record,_=termination_surface(tmp_path,f'print({SUCCESS!r},end="")',
                                              tamper={'recorded_at':timestamp})
    assert status==0 and result['status']=='normal'
    assert result['termination_observation']['status']=='verified'
    assert record['recorded_at']==timestamp


@pytest.mark.parametrize('defect',['existing','symlink','outside','exit-alias','missing-id'])
def test_termination_public_invalid_record_does_not_spawn(tmp_path,defect):
    runtime=tmp_path/'.omc';runtime.mkdir();record=runtime/'termination.json'
    prompt=tmp_path/'prompt';prompt.write_text('x');marker=tmp_path/'spawned'
    if defect=='existing':record.write_text('preserved')
    elif defect=='symlink':record.symlink_to(prompt)
    elif defect=='outside':record=tmp_path/'outside.json'
    elif defect=='exit-alias':record=runtime/'child.exit'
    argv=[sys.executable,str(CLI),'--cwd',str(tmp_path),'--output',str(tmp_path/'out'),
          '--prompt',str(prompt),'--child-pid-file',str(runtime/'child.pid'),
          '--child-exit-file',str(runtime/'child.exit'),'--termination-record',str(record)]
    if defect!='missing-id':argv+=['--termination-run-id','surface-v2']
    p=subprocess.run([*argv,'--',sys.executable,'-c',f'open({str(marker)!r},"w").write("x")'],capture_output=True,timeout=5)
    assert p.returncode==2 and not marker.exists()
    if defect=='existing':assert record.read_text()=='preserved'


@pytest.mark.parametrize('case,reason',[
    ('late','preprocessing_deadline'),
    ('wrong-run','preprocessing_invalid'),
    ('wrong-worker','preprocessing_invalid'),
    ('invalid-time','preprocessing_invalid'),
    ('normal-exit-late','normal_exit_deadline'),
])
def test_review_public_main_rechecks_preparation_and_observed_clock(tmp_path,monkeypatch,case,reason):
    """Real parser and parent loop; deterministic child observations, no provider."""
    import omc_ad_native_supervisor as supervisor
    from types import SimpleNamespace
    origin=100.0;worker=123456
    clock=[1859.0 if case=='normal-exit-late' else 129.5]
    observed=1861.0 if case=='normal-exit-late' else 131.0
    output=tmp_path/'absolute';prompt=tmp_path/'prompt';prompt.write_text('synthetic')
    prepared={'run_id':'review-run','worker_pid':worker,
              'monotonic':130.5 if case=='late' else 110.0}
    if case=='wrong-run':prepared['run_id']='another-run'
    if case=='wrong-worker':prepared['worker_pid']+=1
    if case=='invalid-time':prepared['monotonic']=True
    def fake_fork():
        folder=output/'worker';capture=folder/'capture';capture.mkdir()
        (folder/'prepared.json').write_text(json.dumps(prepared))
        (capture/'result.json').write_text(json.dumps({'status':'normal','child_cleanup_status':'confirmed','raw_capture_complete':True}))
        return worker
    def observe(pid,leader=True):
        clock[0]=observed
        return {'pid':pid,'pgid':pid,'started':'synthetic-owned-worker'}
    monkeypatch.setattr(supervisor.os,'fork',fake_fork)
    monkeypatch.setattr(supervisor.os,'waitpid',lambda *_:(worker,0))
    monkeypatch.setattr(supervisor,'process_identity',observe)
    monkeypatch.setattr(supervisor,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda _:None))
    monkeypatch.setattr(sys,'argv',['omc_ad_native_supervisor.py','--cwd',str(tmp_path),
        '--output',str(output),'--prompt',str(prompt),'--child-pid-file',str(tmp_path/'pid'),
        '--child-exit-file',str(tmp_path/'exit'),'--origin-monotonic',str(origin),
        '--deadline-monotonic','1900','--run-id','review-run','--','/bin/true'])
    assert supervisor.main()==1
    result=json.loads((output/'result.json').read_text())
    assert result['stop_reason']==reason
    assert result['process_result']!='normal' and result['full_contract_pass'] is False


@pytest.mark.parametrize('offset,expected',[(-.001,'passed'),(0,'passed'),(.001,'late')])
def test_review_preparation_timestamp_exact_boundary(tmp_path,offset,expected):
    import omc_ad_native_supervisor as supervisor
    path=tmp_path/'prepared.json'
    path.write_text(json.dumps({'run_id':'r','worker_pid':42,'monotonic':130+offset}))
    result=supervisor.assess_preparation(path,'r',42,100,130,131)
    assert result['status']==expected


@pytest.mark.parametrize('bad',[None,True,float('nan'),float('inf'),10**1000])
def test_review_preparation_invalid_timestamp_is_unknown(tmp_path,bad):
    import omc_ad_native_supervisor as supervisor
    path=tmp_path/'prepared.json'
    path.write_text(json.dumps({'run_id':'r','worker_pid':42,'monotonic':bad}))
    assert supervisor.assess_preparation(path,'r',42,100,130,131)['status']=='invalid'


@pytest.mark.parametrize('publication,expected_reason,expected_code',[
    ('before-read','worker_exited',0),
    ('during-read','worker_exited',0),
    ('late','preprocessing_deadline',1),
    ('future','preprocessing_invalid',1),
])
def test_public_preparation_marker_publication_uses_read_end_clock(tmp_path,monkeypatch,publication,expected_reason,expected_code):
    """Real public parser/parent loop with deterministic worker and publication."""
    import omc_ad_native_supervisor as supervisor
    from types import SimpleNamespace
    output=tmp_path/'absolute';marker=output/'worker/prepared.json'
    prompt=tmp_path/'prompt';prompt.write_text('synthetic')
    worker=123456;clock=[129.99];waits=[0];real_exists=Path.exists
    completed={'before-read':129.98,'during-read':129.995,'late':130.01,'future':130.02}[publication]
    published={'before-read':129.99,'during-read':129.999,'late':130.02,'future':129.999}[publication]
    def write_marker():
        marker.write_text(json.dumps({'run_id':'publication-run','worker_pid':worker,'monotonic':completed}))
    def fork():
        (output/'worker/capture').mkdir()
        if publication=='before-read':write_marker()
        return worker
    def wait(*_):
        waits[0]+=1
        if waits[0]==1:return 0,0
        (output/'worker/capture/result.json').write_text(json.dumps({
            'status':'normal','child_cleanup_status':'confirmed','raw_capture_complete':True}))
        return worker,0
    def exists(path):
        if path==marker and not real_exists(marker):
            write_marker();clock[0]=published
        return real_exists(path)
    monkeypatch.setattr(supervisor.os,'fork',fork)
    monkeypatch.setattr(supervisor.os,'waitpid',wait)
    monkeypatch.setattr(supervisor.os,'kill',lambda *_:None)
    monkeypatch.setattr(supervisor,'process_identity',lambda pid,leader=True:{'pid':pid,'pgid':pid,'started':'synthetic'})
    monkeypatch.setattr(supervisor,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda _:None))
    monkeypatch.setattr(Path,'exists',exists)
    monkeypatch.setattr(sys,'argv',['supervisor','--cwd',str(tmp_path),'--output',str(output),
        '--prompt',str(prompt),'--child-pid-file',str(tmp_path/'pid'),'--child-exit-file',str(tmp_path/'exit'),
        '--origin-monotonic','100','--deadline-monotonic','1900','--run-id','publication-run','--','/bin/true'])
    assert supervisor.main()==expected_code
    result=json.loads((output/'result.json').read_text())
    assert result['stop_reason']==expected_reason
    assert result['process_result']==('normal' if expected_code==0 else 'incomplete_or_failed')
    assert result['full_contract_pass'] is False  # No task-evidence fixture is supplied.


@pytest.mark.parametrize('completed,expected',[(129.995,'passed'),(130,'passed'),(130.001,'late'),(131.001,'invalid')])
def test_preparation_clock_is_sampled_after_record_bytes(tmp_path,monkeypatch,completed,expected):
    import omc_ad_native_supervisor as supervisor
    from types import SimpleNamespace
    marker=tmp_path/'prepared.json'
    marker.write_text(json.dumps({'run_id':'r','worker_pid':42,'monotonic':completed}))
    clock=[129.99];read=Path.read_bytes
    def finish_read(path):
        raw=read(path)
        if path==marker:clock[0]=131.0
        return raw
    monkeypatch.setattr(Path,'read_bytes',finish_read)
    monkeypatch.setattr(supervisor,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    result=supervisor.assess_preparation(marker,'r',42,100,130,129.99)
    assert result['status']==expected



@pytest.mark.parametrize('case',['normal','internal124','timeout','missing'])
def test_current_core_absolute_public_four_surfaces(tmp_path,current_frozen_core,case):
    """R4: real current-core wrapper under the absolute-mode parent and worker."""
    import omc_ad_native_supervisor as supervisor
    prefix=core_candidate(tmp_path,current_frozen_core,CURRENT_CORE)
    code={'normal':f'print({SUCCESS!r},end="")',
          'internal124':'import sys;sys.stderr.write("internal124");sys.exit(124)',
          'timeout':'import time;print("waiting",flush=True);time.sleep(30)',
          'missing':'pass'}[case]
    prompt=tmp_path/'absolute-prompt';prompt.write_text('synthetic no-provider input')
    origin=time.monotonic();deadline=origin+18;run_id='current-core-'+case
    args=[sys.executable,str(CLI),'--cwd',str(tmp_path),'--output',str(tmp_path/'absolute'),
          '--prompt',str(prompt),'--child-pid-file',str(tmp_path/'.omc/absolute.pid'),
          '--child-exit-file',str(tmp_path/'.omc/absolute.exit'),
          '--origin-monotonic',str(origin),'--deadline-monotonic',str(deadline),'--run-id',run_id,
          '--',*prefix,'/bin/sh','-c',RECORDER,'native-launch',sys.executable,'-c',code]
    result=subprocess.run(args,capture_output=True,timeout=22)
    path=tmp_path/'absolute/result.json';assert path.is_file(),result.stderr
    sealed=supervisor.read_absolute_result(path,run_id);assert sealed['status']!='unknown',sealed
    assert sealed['run_id']==run_id and (path.parent/'seal.json').is_file()
    assert sealed['preprocessing']['status']=='passed',sealed
    assert sealed['full_contract_pass'] is False and sealed['task_evidence']=='unknown'
    capture=path.parent/'worker/capture';assert (capture/'stdout.raw').is_file()
    assert (capture/'stderr.raw').is_file()
    native=tmp_path/'.omc/home-motion/events.jsonl'
    assert (capture/'stdout.raw').read_bytes()==native.read_bytes()
    if case=='normal':
        assert result.returncode==0 and sealed['process_result']=='normal',sealed
        assert sealed['cleanup_result']=='confirmed'
    else:
        assert result.returncode==1 and sealed['process_result']!='normal',sealed
    if case=='internal124':
        legacy=json.loads((capture/'result.json').read_text())
        assert legacy['internal_124'] and legacy['child_exit_code']==124 and not legacy['external_timeout']
    elif case=='timeout':
        assert sealed['stop_reason'] in {'normal_exit_deadline','worker_exited'}
        assert time.monotonic()>=origin+1760*18/1800
    elif case=='missing':
        legacy=json.loads((capture/'result.json').read_text())
        assert legacy['normal_terminal_result_count']==0 and legacy['wrapper_exit_code']==0


def absolute_fault_invoke(tmp_path, fault):
    """Real public main/fork/recording; replace only the failing OS operation."""
    runtime=tmp_path/'.omc';runtime.mkdir(exist_ok=True)
    prompt=tmp_path/'fault-prompt';prompt.write_text('no provider')
    output=tmp_path/'fault-output'
    origin=time.monotonic()
    argv=['--cwd',str(tmp_path),'--output',str(output),'--prompt',str(prompt),
          '--child-pid-file',str(runtime/'pid'),'--child-exit-file',str(runtime/'exit'),
          '--origin-monotonic',str(origin),'--deadline-monotonic',str(origin+18),
          '--run-id','fault-'+fault,'--','/bin/sh','-c',RECORDER,'native-launch',
          sys.executable,'-c',
          (f'import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path({str(runtime/"ready")!r}).write_text("ready");time.sleep(30)'
           if fault.startswith('kill') else f'import sys;sys.stdin.read();print({SUCCESS!r},end="")')]
    shim=r'''
import sys,os,signal,errno,json,time
from pathlib import Path
sys.path.insert(0,sys.argv.pop(1))
import omc_ad_native_supervisor as m
fault=sys.argv.pop(1); parent=os.getpid(); fired=False; interrupted=False
output=Path(sys.argv[sys.argv.index('--output')+1]); oldwait=os.waitpid
oldkill=os.kill; oldkillpg=os.killpg; oldopen=Path.open; oldfork=os.fork; oldunlink=os.unlink; worker=None

def fork():
    global worker
    pid=oldfork()
    if pid:worker=pid
    return pid

def denied(operation,target):
    global fired
    fired=True
    raise PermissionError(errno.EPERM,'injected '+operation,str(target))

def wait(pid,flags):
    global interrupted
    if os.getpid()==parent:
        if fault in ('waitpid','waitpid-seal') and pid==worker and not fired:denied('waitpid',pid)
        if fault.startswith('kill') and not interrupted:
            marker=output/'worker/ownership.json'
            try: ready=len(json.loads(marker.read_bytes())['groups'])>=1
            except (OSError,ValueError,KeyError):ready=False
            if ready and (output.parent/'.omc/ready').is_file():
                interrupted=True
                oldkill(parent,signal.SIGTERM)
    return oldwait(pid,flags)

def kill(pid,sig):
    if os.getpid()==parent and fault=='kill' and sig==signal.SIGTERM:denied('kill',pid)
    return oldkill(pid,sig)

def killpg(pid,sig):
    if os.getpid()==parent and fault in ('killpg-term','killpg-kill') and sig==(
            signal.SIGTERM if fault=='killpg-term' else signal.SIGKILL):denied('killpg',pid)
    return oldkillpg(pid,sig)

class ClosedFault:
    def __init__(self,stream,path):self.stream=stream;self.path=path
    def __enter__(self):return self.stream.__enter__()
    def __exit__(self,*exc):
        # Original context manager really flushes/closes first. Do not fake the
        # supervisor, record bytes, or reader; only the OS-facing close outcome.
        answer=self.stream.__exit__(*exc)
        if fault.endswith('-close-error'):denied('close',self.path)
        elif fault.endswith('-close-late'):
            oldclock=time.monotonic
            time.monotonic=lambda:oldclock()+19
        return answer

def opened(path,*args,**kwargs):
    if os.getpid()==parent:
        if fault=='pending-open-error' and path==output/'finalization.pending':denied('open',path)
        storage='seal' if fault=='waitpid-seal' else fault
        if storage in ('result','seal') and path==output/(storage+'.json'):
            denied('open',path)
        if fault=='result-late' and path==output/'result.json':
            # Simulate a write operation returning after the absolute deadline.
            oldclock=time.monotonic
            time.monotonic=lambda:oldclock()+19

    stream=oldopen(path,*args,**kwargs)
    if os.getpid()==parent and any(c in str(args[0] if args else kwargs.get('mode','r')) for c in 'wax'):
        target='seal.json' if fault.startswith('seal-close-') else 'finalized.json' if fault.startswith('finalized-close-') else None
        if target and path==output/target:return ClosedFault(stream,path)
    return stream

def unlink(path,*args,**kwargs):
    if os.getpid()==parent and fault=='publish-error' and Path(path)==output/'finalization.pending':
        denied('unlink',path)
    return oldunlink(path,*args,**kwargs)

os.fork=fork;os.waitpid=wait;os.kill=kill;os.killpg=killpg;os.unlink=unlink;Path.open=opened
try: raise SystemExit(m.main())
finally:
    # Test-owned cleanup only, after the immutable result; never changes its verdict.
    if os.getpid()==parent:
        try: rows=json.loads((output/'worker/ownership.json').read_bytes())['groups']
        except (OSError,ValueError,KeyError):rows=[]
        for row in rows:
            try:oldkillpg(row['pgid'],signal.SIGKILL)
            except ProcessLookupError:pass
'''
    p=subprocess.run([sys.executable,'-c',shim,str(ROOT/'scripts'),fault,*argv],
                     capture_output=True,timeout=23)
    return p,output,origin


@pytest.mark.parametrize('fault,operation,target_kind',[
    ('waitpid','waitpid','pid'),('kill','kill','pid'),
    ('killpg-term','killpg','pgid'),('killpg-kill','killpg','pgid')])
def test_absolute_runtime_permission_error_public_result(tmp_path,fault,operation,target_kind):
    import omc_ad_native_supervisor as supervisor
    p,output,origin=absolute_fault_invoke(tmp_path,fault)
    assert p.returncode==1,p.stderr
    assert b'supervisor_input_or_setup_error' not in p.stderr
    result=supervisor.read_absolute_result(output/'result.json','fault-'+fault)
    assert result['status']=='incomplete_or_failed',result
    assert result['full_contract_pass'] is False and result['process_result']!='normal'
    assert result['cleanup_result']=='unconfirmed'
    row=next(r for r in result['runtime_errors'] if r['operation']==operation)
    assert row['errno']==1 and row['target_kind']==target_kind and row['target']>1
    assert row['stage'] in ('termination','reap') and 'injected' in row['message']
    if target_kind=='pid':assert row['target']==result['worker_pid']
    else:assert row['target'] in [g['pgid'] for g in result['owned_groups']]
    assert row['exception_type']=='PermissionError' and row['monotonic']>=origin
    if fault.startswith('kill'):assert row['signal']==int(signal.SIGKILL if fault=='killpg-kill' else signal.SIGTERM)
    assert result['cleanup_deadline_monotonic']<=min(result['stop_monotonic']+.12,origin+17.72)
    assert time.monotonic()<origin+19
    assert b'supervisor_runtime_error:' in p.stderr
    assert (output/'seal.json').is_file()


@pytest.mark.parametrize('fault',['result','seal'])
def test_absolute_final_storage_permission_error_public_reader(tmp_path,fault):
    import omc_ad_native_supervisor as supervisor
    p,output,_=absolute_fault_invoke(tmp_path,fault)
    assert p.returncode==1,p.stderr
    assert b'supervisor_input_or_setup_error' not in p.stderr
    errors=[json.loads(line.split(b':',1)[1]) for line in p.stderr.splitlines()
            if line.startswith(b'supervisor_runtime_error:')]
    assert any(e['stage']=='final_record' and e['operation']=='write_'+fault
               and e['errno']==1 and e['target']==str(output/(fault+'.json')) for e in errors),p.stderr
    assert not (output/'seal.json').exists()
    assert (output/'result.json').exists()==(fault=='seal')
    assert supervisor.read_absolute_result(output/'result.json','fault-'+fault)['status']=='unknown'


def test_absolute_primary_error_survives_seal_error_public(tmp_path):
    p,output,_=absolute_fault_invoke(tmp_path,'waitpid-seal')
    assert p.returncode==1,p.stderr
    rows=[json.loads(line.split(b':',1)[1]) for line in p.stderr.splitlines()
          if line.startswith(b'supervisor_runtime_error:')]
    assert rows[0]['operation']=='waitpid' and rows[0]['errno']==1
    assert rows[-1]['operation']=='write_seal' and rows[-1]['errno']==1
    result=json.loads((output/'result.json').read_bytes())
    assert result['runtime_errors'][0]['operation']=='waitpid'
    assert not (output/'seal.json').exists()


def test_absolute_no_seal_after_storage_crosses_deadline_public(tmp_path):
    import omc_ad_native_supervisor as supervisor
    p,output,_=absolute_fault_invoke(tmp_path,'result-late')
    assert p.returncode==1,p.stderr
    assert (output/'result.json').exists() and not (output/'seal.json').exists()
    assert b'absolute final-record deadline exhausted' in p.stderr
    assert supervisor.read_absolute_result(output/'result.json','fault-result-late')['status']=='unknown'


@pytest.mark.parametrize('fault',['seal-close-error','seal-close-late',
                                  'finalized-close-error','finalized-close-late'])
def test_absolute_post_seal_failure_is_not_consumable_public(tmp_path,fault):
    import omc_ad_native_supervisor as supervisor
    p,output,_=absolute_fault_invoke(tmp_path,fault)
    assert p.returncode==1,p.stderr
    # Valid, fully written JSON is deliberately left on disk; presence/hash alone
    # must not promote the failed finalization into a normal process result.
    assert json.loads((output/'result.json').read_bytes())['process_result']=='normal'
    assert json.loads((output/'seal.json').read_bytes())['run_id']=='fault-'+fault
    assert supervisor.read_absolute_result(output/'result.json','fault-'+fault)['status']=='unknown'
    assert b'supervisor_runtime_error:' in p.stderr


@pytest.mark.parametrize('fault',['pending-open-error','publish-error'])
def test_absolute_finalization_barrier_errors_public(tmp_path,fault):
    import omc_ad_native_supervisor as supervisor
    p,output,_=absolute_fault_invoke(tmp_path,fault)
    assert p.returncode==1,p.stderr
    assert supervisor.read_absolute_result(output/'result.json','fault-'+fault)['status']=='unknown'
    errors=[json.loads(line.split(b':',1)[1]) for line in p.stderr.splitlines()
            if line.startswith(b'supervisor_runtime_error:')]
    assert errors[-1]['operation']==('write_pending' if fault=='pending-open-error' else 'publish_finalized')
    assert errors[-1]['errno']==1
    if fault=='pending-open-error':
        assert not (output/'result.json').exists() and not (output/'seal.json').exists()
    else:
        assert (output/'finalization.pending').exists() and (output/'finalized.json').exists()


def test_absolute_new_publication_and_legacy_consumer(tmp_path,current_frozen_core):
    import omc_ad_native_supervisor as supervisor
    import importlib.util
    p,output,_=absolute_fault_invoke(tmp_path,'none')
    assert p.returncode==0,p.stderr
    result=supervisor.read_absolute_result(output/'result.json','fault-none')
    assert result['process_result']=='normal' and result['finalization_protocol']=='pending-publication/v2'
    assert not (output/'finalization.pending').exists()
    finalized=json.loads((output/'finalized.json').read_bytes())
    seal_raw=(output/'seal.json').read_bytes()
    assert finalized['seal_sha256']==hashlib.sha256(seal_raw).hexdigest()
    assert finalized['result_sha256']==hashlib.sha256((output/'result.json').read_bytes()).hexdigest()
    assert result['origin_monotonic']<=finalized['completed_monotonic']<=result['deadline_monotonic']
    legacy_path=current_frozen_core/'scripts/omc_ad_native_supervisor.py'
    spec=importlib.util.spec_from_file_location('frozen_supervisor_v1',legacy_path)
    legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    assert legacy.read_absolute_result(output/'result.json','fault-none')['status']=='unknown'


@pytest.mark.parametrize('damage',['missing','partial','late','result-hash','seal-hash','run-id',
                                   'pending','pending-link','finalized-link'])
def test_absolute_finalization_reader_fail_closed(tmp_path,damage):
    import omc_ad_native_supervisor as supervisor
    p,output,_=absolute_fault_invoke(tmp_path,'none')
    assert p.returncode==0,p.stderr
    target=output/'finalized.json';value=json.loads(target.read_bytes())
    if damage=='missing':target.unlink()
    elif damage=='partial':target.write_bytes(b'{"status":')
    elif damage=='pending':(output/'finalization.pending').write_text('incomplete')
    elif damage=='pending-link':(output/'finalization.pending').symlink_to(output/'absent')
    elif damage=='finalized-link':
        raw=target.read_bytes();target.unlink();(output/'other.json').write_bytes(raw)
        target.symlink_to(output/'other.json')
    else:
        if damage=='late':
            result=json.loads((output/'result.json').read_bytes())
            value['completed_monotonic']=result['deadline_monotonic']+.001
        elif damage=='run-id':value['run_id']='other-run'
        else:value[damage.replace('-','_').replace('_hash','_sha256')]='0'*64
        target.write_text(json.dumps(value))
    assert supervisor.read_absolute_result(output/'result.json','fault-none')['status']=='unknown'


def test_absolute_legacy_v1_receipt_still_readable(tmp_path):
    import omc_ad_native_supervisor as supervisor
    result={'schema_version':'omc-ad-absolute-result/v1','run_id':'legacy',
            'status':'incomplete_or_failed','origin_monotonic':100,'deadline_monotonic':200,
            'full_contract_pass':False}
    raw=json.dumps(result).encode();path=tmp_path/'result.json';path.write_bytes(raw)
    (tmp_path/'seal.json').write_text(json.dumps({'run_id':'legacy',
        'result_sha256':hashlib.sha256(raw).hexdigest(),'completed_monotonic':150}))
    assert supervisor.read_absolute_result(path,'legacy')==result


def dialogue_surface(tmp_path, *, defect=None, absolute=False, timeout=2):
    """Pinned CLI 2.1.138 user/isReplay/uuid contract, no provider process."""
    session = '11111111-1111-4111-8111-111111111111'
    run = 'dialogue-fixture'
    prompt = tmp_path/'prompt.jsonl'
    prompt.write_text(json.dumps({'type':'user','uuid':'initial','session_id':session,
        'parent_tool_use_id':None,'message':{'role':'user','content':'initial'}})+'\n')
    inbox=tmp_path/'responses.jsonl';inbox.touch()
    code = r'''
import json,sys,time,hashlib,os
from pathlib import Path
inbox=Path(sys.argv[1]);session=sys.argv[2];defect=sys.argv[3]
def emit(x): print(json.dumps(x),flush=True)
def append(x):
 with inbox.open('a') as f:f.write(json.dumps(x)+'\n')
def replay(m):
 if defect=='wrong-session':m=dict(m,session_id='other')
 emit(dict(m,isReplay=True))
def result():emit({'type':'result','subtype':'success','is_error':False,'session_id':session})
m=json.loads(sys.stdin.readline());replay(m)
for turn in range(2):
 content=[{'type':'text','text':'question '+str(turn)}]
 emit({'type':'assistant','uuid':'q'+str(turn),'session_id':session,'parent_tool_use_id':None,'message':{'role':'assistant','content':content}})
 result()
 text=('x'*60000 if defect=='large' else 'answer '+str(turn))
 row={'type':'response','run_id':'dialogue-fixture','session_id':session,'sequence':turn+1,
      'question_id':'q'+str(turn),'question_sha256':hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
      'response_id':'response-'+str(turn),'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}
 if defect in ('active','cost-partial','cost-overlap','cost-backward','cost-before-question','cost-multi') and not (defect=='cost-partial' and turn==1):
  journal=inbox.parent/'out/dialogue.jsonl';until=time.monotonic()+1
  while True:
   rows=[json.loads(line) for line in journal.read_text().splitlines(keepends=True) if line.endswith('\n')] if journal.exists() else []
   shown=[r for r in rows if r.get('event')=='message_displayed' and r.get('question_id')=='q'+str(turn)]
   if shown:break
   assert time.monotonic()<until
   time.sleep(.005)
  at=rows[0]['monotonic'] if defect=='cost-before-question' else time.monotonic()
  def mark(kind,when):append({'type':kind,'run_id':'dialogue-fixture','session_id':session,'question_id':'q'+str(turn),'at_monotonic':when})
  mark('active_start',at);time.sleep(.01);end=time.monotonic();mark('active_end',end)
  if defect in ('cost-overlap','cost-backward'):
   mark('active_start',at if defect=='cost-overlap' else at-.0001);mark('active_end',end)
  if defect=='cost-multi':
   at=time.monotonic();mark('active_start',at);time.sleep(.01);mark('active_end',time.monotonic())
 if turn==0 and defect=='partial':
  inbox.write_text('{');time.sleep(10)
 if turn==0 and defect=='replace':
  other=inbox.with_suffix('.new');other.write_text(json.dumps(row)+'\n');other.replace(inbox)
 elif turn==1 and defect=='truncate':inbox.write_text('')
 elif turn==0 and defect=='deep':inbox.write_text('['*1200+']'*1200+'\n')
 elif turn==0 and defect=='oversize':inbox.write_text('x'*65537+'\n')
 else:
  if turn==0 and defect=='wrong-question':row['question_id']='other'
  if turn==0 and defect=='bad-hash':row['sha256']='0'*64
  append(row)
 line=sys.stdin.readline()
 if not line:sys.exit(3)
 m=json.loads(line)
 if defect=='wrong-ack' and turn==0:m['message']['content']='other'
 if defect=='missing-ack' and turn==0:time.sleep(10)
 replay(m)
 if defect=='duplicate' and turn==0:append(row)
result()
append({'type':'close','run_id':'dialogue-fixture','session_id':session})
if defect=='after-close':append({'type':'active_start','run_id':'dialogue-fixture','session_id':session,'question_id':'q1','at_monotonic':time.monotonic()})
assert sys.stdin.read()==''
'''
    launch=['/bin/sh','-c',RECORDER,'native-launch',sys.executable,'-c',code,str(inbox),session,defect or 'none']
    argv=[sys.executable,str(CLI),'--cwd',str(tmp_path),'--output',str(tmp_path/'out'),
          '--prompt',str(prompt),'--child-pid-file',str(tmp_path/'child.pid'),
          '--child-exit-file',str(tmp_path/'child.exit'),'--timeout',str(timeout),'--drain','1',
          '--responses-file',str(inbox),'--dialogue-session-id',session,'--run-id',run]
    if absolute:
        origin=time.monotonic();argv+=['--origin-monotonic',str(origin),'--deadline-monotonic',str(origin+30)]
    proc=subprocess.run([*argv,'--',*launch],capture_output=True,timeout=15)
    file=tmp_path/'out/worker/capture/result.json' if absolute else tmp_path/'out/result.json'
    return proc,json.loads(file.read_bytes()) if file.exists() else None


def test_dialogue_public_multiple_turns_and_explicit_close(tmp_path):
    p,r=dialogue_surface(tmp_path)
    assert p.returncode==0,p.stderr
    assert r['status']=='normal' and r['dialogue']['complete']
    assert r['dialogue']['responses_confirmed']==2
    assert r['terminal_result_count']==3 and r['input']['eof_sent']
    assert r['child_cleanup_status']=='confirmed'
    assert r['dialogue']['user_active_seconds'] is None
    assert r['dialogue']['initial_sha256']==hashlib.sha256((tmp_path/'prompt.jsonl').read_bytes()).hexdigest()


@pytest.mark.parametrize('defect',['wrong-question','bad-hash','wrong-ack','duplicate','replace','oversize','after-close','wrong-session','truncate','deep'])
def test_dialogue_public_rejects_bad_channel_without_retry(tmp_path,defect):
    p,r=dialogue_surface(tmp_path,defect=defect)
    assert p.returncode==1,p.stderr
    assert not r['dialogue']['complete'] and r['dialogue']['error']
    assert r['raw_capture_complete']


@pytest.mark.parametrize('defect',['partial','missing-ack'])
def test_dialogue_public_wait_does_not_extend_deadline(tmp_path,defect):
    start=time.monotonic();p,r=dialogue_surface(tmp_path,defect=defect)
    assert p.returncode==1,p.stderr
    assert r['external_timeout'] and time.monotonic()-start<6
    assert not r['dialogue']['complete']


@pytest.mark.parametrize('scenario',['active','large'])
def test_dialogue_public_cost_marks_and_large_partial_delivery(tmp_path,scenario):
    p,r=dialogue_surface(tmp_path,defect=scenario)
    assert p.returncode==0,p.stderr
    assert r['dialogue']['complete'] and r['dialogue']['responses_confirmed']==2
    if scenario=='active': assert r['dialogue']['user_active_seconds']>=.02
    else: assert r['dialogue']['user_active_seconds'] is None
    assert r['dialogue']['responses_raw_sha256']==hashlib.sha256((tmp_path/'out/responses.raw').read_bytes()).hexdigest()
    events=[json.loads(line)['event'] for line in (tmp_path/'out/dialogue.jsonl').read_text().splitlines()]
    assert events.count('write_completed')==3 and events.count('replay_confirmed')==3


def test_dialogue_absolute_watchdog_keeps_phase_verdict_separate(tmp_path):
    p,r=dialogue_surface(tmp_path,absolute=True)
    assert r is not None and r['dialogue']['complete'] and r['status']=='normal'
    outer=json.loads((tmp_path/'out/result.json').read_bytes())
    assert outer['process_result']=='normal'
    assert p.returncode==0
    assert outer['full_contract_pass'] is False and outer['task_evidence']=='unknown'
    assert outer['comparison']=='HOLD' and outer['actual_home']=='NOT_VERIFIED'


@pytest.mark.parametrize('defect,timeout,reason',[('partial',7,'partial_line'),('missing-ack',12,'replay')])
def test_dialogue_public_internal_wait_limits_do_not_renew_clock(tmp_path,defect,timeout,reason):
    start=time.monotonic();p,r=dialogue_surface(tmp_path,defect=defect,timeout=timeout)
    assert p.returncode==1 and not r['external_timeout']
    assert reason in r['dialogue']['error']
    assert time.monotonic()-start<timeout+1


def test_dialogue_cost_partial_measurement_stays_unknown(tmp_path):
    p,r=dialogue_surface(tmp_path,defect='cost-partial')
    assert p.returncode==0,p.stderr
    assert r['dialogue']['responses_confirmed']==2
    assert r['dialogue']['user_active_seconds'] is None
    assert r['dialogue']['user_active_observed_seconds']>=.01
    assert r['dialogue']['user_active_measurement_complete'] is False


@pytest.mark.parametrize('case',['cost-overlap','cost-backward','cost-before-question'])
def test_dialogue_cost_rejects_invalid_time_order(tmp_path,case):
    p,r=dialogue_surface(tmp_path,defect=case)
    assert p.returncode==1,p.stderr
    assert r['dialogue']['error']=='dialogue_active_marker_out_of_order'
    assert r['dialogue']['user_active_seconds'] is None
    assert r['dialogue']['user_active_measurement_complete'] is False


def test_dialogue_cost_accepts_multiple_nonoverlapping_intervals(tmp_path):
    p,r=dialogue_surface(tmp_path,defect='cost-multi')
    assert p.returncode==0,p.stderr
    assert r['dialogue']['user_active_measurement_complete'] is True
    assert r['dialogue']['user_active_seconds']>=.04
    assert r['dialogue']['user_active_seconds']==r['dialogue']['user_active_observed_seconds']


# Explicit model-free relay surfaces; no server, browser, install, or real hook.
def relay_public_case(root, *, enabled=True, mode='fast', direct=False):
    from test_omc_pipeline_guard_session_reset import prebound_project
    import omc_pipeline_guard as guard
    import omc_native_red_evidence as red
    root.mkdir(parents=True, exist_ok=True)
    print('RELAY_FIXTURE_ROOT='+str(root))
    context, _ = prebound_project(root)
    req = {'schema_version':'omc-home-motion-requirement/v1',
        'session_id':context['session_id'],'run_id':context['run_id'],
        'url':'http://localhost:3999/never-requested','events_path':'.omc/native.jsonl',
        'receipt_path':'.omc/home-receipt.json','preflight_path':'.omc/ready.json',
        'preflight_sha256':'a'*64}
    requirement=root/'.omc/home-motion-requirement.json'
    requirement.write_text(json.dumps(req))
    basis=guard._load_approved_basis(root,context['session_id'])
    basis['revision']=2
    basis['checks'].append({'id':'browser-home','target':'synthetic only',
        'method':'no network','pass_condition':'fixture binding','target_version':'fixture',
        'home_motion_sha256':hashlib.sha256(requirement.read_bytes()).hexdigest()})
    proposal=root/'.omc/proposal2.json';proposal.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root,proposal)==0
    assert guard.cmd_contract_done(root,required_checks='tests,browser-home')==0
    context=red.execution_context(root)
    state_before=guard._state_path(root).read_bytes()
    basis_file=guard._basis_path(root,context['session_id'])/'2.json'
    basis_before=basis_file.read_bytes()
    inbox=root/'.omc/responses.jsonl';inbox.write_text('')
    prompt=root/'.omc/prompt.jsonl'
    initial={'type':'user','uuid':'initial-fixture','session_id':'native-fixture',
        'parent_tool_use_id':None,'message':{'role':'user','content':'probe'}}
    prompt.write_text(json.dumps(initial)+'\n')
    trace=root/'.omc/relay.jsonl';side=root/'.omc/oracle.jsonl'
    if mode=='collision':trace.write_text('preserve this original')
    if mode=='symlink':trace.symlink_to(prompt)
    child=r'''
import sys,json,time,os,hashlib
from pathlib import Path
root=Path(sys.argv[1]);mode=sys.argv[2];direct=sys.argv[3]=='1'
def record(event):
 with (root/'.omc/oracle.jsonl').open('a') as f:f.write(json.dumps({'event':event,'monotonic':time.monotonic(),'trace_id':os.environ.get('OMC_RELAY_TRACE_ID')})+'\n')
def emit(value):print(json.dumps(value),flush=True)
if not direct:
 ref=json.loads(os.environ['OMC_SESSION_START_HANDOFF']);raw=(root/ref['path']).read_bytes()
 assert hashlib.sha256(raw).hexdigest()==ref['sha256']
 binding=json.loads(raw)['binding']['execution_context']
 assert binding['run_id']=='start-fixture' and binding['basis_sha256']
 assert 'OMC_RELAY_TRACE' not in os.environ
 record('approved_handoff_received')
emit({'type':'system','subtype':'hook_started','hook_id':'h'});time.sleep(.15)
if mode=='hook':time.sleep(11)
emit({'type':'system','subtype':'hook_response','hook_id':'h'})
emit({'type':'system','subtype':'init','session_id':'native-fixture'})
line=sys.stdin.readline();record('input_received');message=json.loads(line)
if mode=='six':time.sleep(6)
record('replay_emitted');emit({**message,'isReplay':True})
if mode=='close-write':time.sleep(.2)
content=[{'type':'text','text':'fixture output'}]
emit({'type':'assistant','session_id':'native-fixture','uuid':'q1','parent_tool_use_id':None,'message':{'content':content}})
emit({'type':'result','subtype':'success','is_error':False,'session_id':'native-fixture'})
line=sys.stdin.readline()
if line:
 second=json.loads(line);emit({**second,'isReplay':True})
 emit({'type':'result','subtype':'success','is_error':False,'session_id':'native-fixture'})
 assert sys.stdin.read()==''
'''
    child_argv=[sys.executable,'-u','-c',child,str(root),mode,str(int(direct))]
    # Faults affect the named I/O component only; public main/run_native stay real.
    driver=r'''
import sys,os,json,time,runpy
from pathlib import Path
root=Path(sys.argv[1]);mode=sys.argv[2]
sys.path.insert(0,str(root/'scripts'))
import omc_home_observation as module
def pause(event):
 with (root/'.omc/oracle.jsonl').open('a') as f:f.write(json.dumps({'event':event,'monotonic':time.monotonic()})+'\n')
 time.sleep(11)
if mode=='publisher':
 original=module.publish;done=False
 def slow(root):
  global done
  if not done:done=True;pause('publisher_enter')
  return original(root)
 module.publish=slow
if mode in ('trace-write','trace-first-write','close-write'):
 method='_write_raw' if mode=='trace-first-write' else '_write_payload'
 original=getattr(module.RelayTrace,method);done=False
 def slow(self,raw):
  global done
  if not done and (mode=='trace-first-write' or json.loads(raw)['event']==('trace_closed' if mode=='close-write' else 'raw_received')):
   done=True;pause('trace_write_enter')
   if mode=='trace-first-write':raise OSError('trace first record remains unavailable')
  return original(self,raw)
 setattr(module.RelayTrace,method,slow)
if mode=='bad-binding':
 value=json.loads(os.environ['OMC_RELAY_TRACE']);value['run_id']='wrong'
 os.environ['OMC_RELAY_TRACE']=json.dumps(value)
if mode=='trace-limit':
 module.RelayTrace.MAX_BYTES=1
if mode in ('raw-flush','stdout-write'):
 class Proxy:
  def __init__(self,stream):self.stream=stream;self.done=False
  def __getattr__(self,name):return getattr(self.stream,name)
  def __enter__(self):return self
  def __exit__(self,*args):return self.stream.__exit__(*args)
  def write(self,raw):
   if mode=='stdout-write' and not self.done:self.done=True;pause('stdout_write_enter')
   return self.stream.write(raw)
  def flush(self):
   if mode=='raw-flush' and not self.done:self.done=True;pause('raw_flush_enter')
   return self.stream.flush()
 if mode=='raw-flush':
  original_open=Path.open
  def changed(path,*args,**kwargs):
   stream=original_open(path,*args,**kwargs)
   return Proxy(stream) if path==root/'.omc/native.jsonl' and args and args[0]=='xb' else stream
  Path.open=changed
 else:
  class Output:
   buffer=Proxy(sys.stdout.buffer)
   def __getattr__(self,name):return getattr(sys.__stdout__,name)
  sys.stdout=Output()
sys.argv=[str(root/'scripts/omc_home_observation.py'),'run','--target',str(root),'--launch',*json.loads(sys.argv[3])]
raise SystemExit(module.main())
'''
    launch=child_argv if direct else [sys.executable,'-u','-c',driver,str(root),mode,json.dumps(child_argv)]
    launch=['/bin/sh','-c',RECORDER,'relay-recorder',*launch]
    origin=time.monotonic()
    argv=[sys.executable,str(CLI),'--cwd',str(root),'--output',str(root/'out'),
        '--prompt',str(prompt),'--responses-file',str(inbox),'--dialogue-session-id','native-fixture',
        '--child-pid-file',str(root/'.omc/child.pid'),'--child-exit-file',str(root/'.omc/child.exit'),
        '--timeout','1800','--drain','12','--origin-monotonic',str(origin),
        '--deadline-monotonic',str(origin+1800),'--run-id','start-fixture',
        '--phase-evidence',str(root/'.omc/unrun-phase.json')]
    if enabled and not direct:argv+=['--relay-trace',str(trace)]
    proc=subprocess.Popen([*argv,'--',*launch],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    sent=0;closed=False;capture=root/'out/worker/capture';started=time.monotonic()
    while proc.poll() is None:
        raw=capture/'stdout.raw'
        text=raw.read_text(errors='replace') if raw.exists() else ''
        terminal=sum(1 for line in text.splitlines() if '"type": "result"' in line)
        if terminal and not closed:
            if mode=='followup' and sent==0:
                content=[{'type':'text','text':'fixture output'}]
                row={'type':'response','run_id':'start-fixture','session_id':'native-fixture',
                    'sequence':1,'question_id':'q1','question_sha256':hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                    'response_id':'second-fixture','text':'next','sha256':hashlib.sha256(b'next').hexdigest()}
                with inbox.open('a') as f:f.write(json.dumps(row)+'\n')
                sent=1
            elif mode!='followup' or terminal==2:
                with inbox.open('a') as f:f.write(json.dumps({'type':'close','run_id':'start-fixture','session_id':'native-fixture'})+'\n')
                closed=True
        if time.monotonic()-started>35:
            proc.terminate();proc.wait(timeout=14)
            raise AssertionError('model-free harness deadline; no retry')
        time.sleep(.025)
    stdout,stderr=proc.communicate()
    result_file=capture/'result.json'
    assert result_file.exists(), (stdout,stderr)
    result=json.loads(result_file.read_bytes())
    rows=[json.loads(line) for line in trace.read_text().splitlines()] if trace.is_file() and mode not in ('collision','symlink') else []
    oracle=[json.loads(line) for line in side.read_text().splitlines()] if side.exists() else []
    assert guard._state_path(root).read_bytes()==state_before
    assert basis_file.read_bytes()==basis_before
    if enabled and not direct and oracle and oracle[0]['event']=='approved_handoff_received':
        assert oracle[0]['trace_id']==rows[0]['trace_id']
    if oracle and not direct:assert oracle[0]['event']=='approved_handoff_received' or mode in ('trace-write','trace-first-write','bad-binding','trace-limit')
    return proc,result,rows,oracle,capture


def assert_relay_ok(case):
    proc,result,rows,oracle,capture=case
    assert proc.returncode==0, (capture/'stderr.raw').read_text()
    assert result['dialogue']['complete']
    assert result['relay_trace']['status']=='confirmed'
    assert result['relay_trace']['blocker'] is None
    assert result['raw_capture_complete']
    assert rows and any(r['event']=='trace_closed' for r in rows)
    return result


def test_relay_trace_public_run_native_fast_pair(tmp_path):
    off=relay_public_case(tmp_path/'off',enabled=False)
    on=relay_public_case(tmp_path/'on')
    assert off[0].returncode==0 and off[1]['dialogue']['complete']
    assert 'relay_trace' not in off[1]
    assert_relay_ok(on)
    for stream in ['stdout.raw','stderr.raw']:
        assert (off[4]/stream).read_bytes()==(on[4]/stream).read_bytes()
    def ack(case):
        rows=[json.loads(l) for l in (case[4]/'dialogue.jsonl').read_text().splitlines()]
        return next(r['monotonic'] for r in rows if r['event']=='replay_confirmed')-next(r['monotonic'] for r in rows if r['event']=='write_completed')
    assert ack(on)<10 and abs(ack(on)-ack(off))<=1


def test_relay_trace_public_run_native_six_second_pair(tmp_path):
    off=relay_public_case(tmp_path/'off',enabled=False,mode='six')
    on=relay_public_case(tmp_path/'on',mode='six')
    assert_relay_ok(on)
    assert off[0].returncode==0
    assert (off[4]/'stdout.raw').read_bytes()==(on[4]/'stdout.raw').read_bytes()
    def ack(case):
        r=[json.loads(l) for l in (case[4]/'dialogue.jsonl').read_text().splitlines()]
        return next(x['monotonic'] for x in r if x['event']=='replay_confirmed')-next(x['monotonic'] for x in r if x['event']=='write_completed')
    assert 6<=ack(on)<10 and abs(ack(on)-ack(off))<=1


def test_relay_trace_public_hook_delay(tmp_path):
    case=relay_public_case(tmp_path,mode='hook')
    assert case[1]['dialogue']['error']=='dialogue_replay_unconfirmed'
    assert case[1]['relay_trace']['blocker']=='upstream_wait'
    assert not any(x['event']=='replay_emitted' for x in case[3])


def test_relay_trace_public_publisher_delay(tmp_path):
    case=relay_public_case(tmp_path,mode='publisher')
    assert case[1]['dialogue']['error']=='dialogue_replay_unconfirmed'
    assert case[1]['relay_trace']['blocker']=='publisher'
    assert any(x['event']=='replay_emitted' for x in case[3])


def test_relay_trace_public_raw_and_stdout_delay(tmp_path):
    for mode,blocker in [('raw-flush','raw_store'),('stdout-write','stdout')]:
        case=relay_public_case(tmp_path/mode,mode=mode)
        assert case[1]['dialogue']['error']=='dialogue_replay_unconfirmed'
        assert case[1]['relay_trace']['blocker']==blocker


def test_relay_trace_public_trace_write_delay_and_unknown(tmp_path):
    for mode,blocker in [('trace-write','trace_io'),('trace-first-write',None)]:
        case=relay_public_case(tmp_path/mode,mode=mode)
        assert case[1]['dialogue']['error']=='dialogue_replay_unconfirmed'
        assert case[1]['relay_trace']['status']=='unknown'
        assert case[1]['relay_trace']['blocker']==blocker
        assert any(r['event']=='trace_write_enter' for r in case[3])
        assert case[1]['raw_capture_complete']
        if mode=='trace-write':
            native=(tmp_path/mode/'.omc/native.jsonl').read_bytes()
            assert native==(case[4]/'stdout.raw').read_bytes()


def test_relay_trace_public_initial_ack_lifetime(tmp_path):
    case=relay_public_case(tmp_path,mode='followup')
    assert_relay_ok(case)
    assert case[1]['dialogue']['responses_confirmed']==1
    assert sum(r['event']=='native_event' and r.get('type')=='user' for r in case[2])==1
    assert case[2][-2]['event']=='trace_closed' or any(r['event']=='trace_closed' for r in case[2])


def test_relay_trace_public_binding_and_storage_failures(tmp_path):
    for mode in ['bad-binding','trace-limit']:
        case=relay_public_case(tmp_path/mode,mode=mode)
        assert case[0].returncode!=0
        assert case[1]['relay_trace']['status']=='unknown'
    for mode in ['collision','symlink']:
        root=tmp_path/mode
        try:
            relay_public_case(root,mode=mode)
        except AssertionError as exc:
            assert 'relay' in str(exc).lower() or 'exists' in str(exc).lower()
        else:
            raise AssertionError('unsafe trace path must reject before capture')
        assert (root/'.omc/relay.jsonl').exists()
        if mode=='collision':assert (root/'.omc/relay.jsonl').read_text()=='preserve this original'
        else:assert (root/'.omc/relay.jsonl').is_symlink()
    case=relay_public_case(tmp_path/'reader')
    assert_relay_ok(case)
    import omc_ad_native_supervisor as supervisor
    from types import SimpleNamespace
    path=tmp_path/'reader/.omc/relay.jsonl'
    original=path.read_bytes()
    header=next(row for row in case[2] if row['event']=='header')
    descriptor=dict(header,candidate=str(tmp_path/'reader'))
    for field,value in [('clock_implementation','wrong-clock'),('relay_source_sha256','0'*64),('writer_pid',-1)]:
        rows=[dict(row) for row in case[2]]
        next(row for row in rows if row['event']=='header')[field]=value
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        result=supervisor.relay_assessment(path,descriptor,case[4]/'stdout.raw',
            SimpleNamespace(ack_deadline=None,pending=None,error=None))
        assert result['status']=='unknown'
        path.write_bytes(original)
    from test_omc_home_observation import relay_descriptor
    import omc_home_observation as home
    root=tmp_path/'cap';(root/'.omc').mkdir(parents=True)
    trace=home.RelayTrace(root,relay_descriptor(root))
    with pytest.raises(ValueError):trace.record('oversized',metadata='x'*65537)
    trace.close()


def test_relay_trace_direct_path_is_not_applicable(tmp_path):
    case=relay_public_case(tmp_path,direct=True)
    assert case[0].returncode==0
    assert case[1]['dialogue']['complete'] and 'relay_trace' not in case[1]
    assert not (tmp_path/'.omc/relay.jsonl').exists()


def test_relay_trace_public_close_write_delay(tmp_path):
    started = time.monotonic()
    case = relay_public_case(tmp_path, mode='close-write')
    assert time.monotonic()-started < 5, 'post-ACK trace close held the native runner'
    assert case[0].returncode != 0
    assert case[1]['relay_trace']['status'] == 'unknown'
    assert case[1]['dialogue']['error'] is None
    assert case[1]['raw_capture_complete']
    assert b'fixture output' in (case[4]/'stdout.raw').read_bytes()
    assert (tmp_path/'.omc/native.jsonl').read_bytes() == (case[4]/'stdout.raw').read_bytes()
    assert any(r['event']=='trace_write_enter' for r in case[3])


def test_relay_trace_required_header_and_native_stream(tmp_path):
    case = relay_public_case(tmp_path)
    assert_relay_ok(case)
    import omc_ad_native_supervisor as supervisor
    from types import SimpleNamespace
    path=tmp_path/'.omc/relay.jsonl'
    original=path.read_bytes()
    descriptor=dict(next(r for r in case[2] if r['event']=='header'),candidate=str(tmp_path))
    for event in ('header', 'native_stream'):
        for change in ('missing','renamed','duplicate'):
            rows=[dict(r) for r in case[2]]
            index=next(i for i,r in enumerate(rows) if r['event']==event)
            if change=='missing':rows.pop(index)
            elif change=='renamed':rows[index]['event']='unrecognized_metadata'
            else:rows.insert(index,dict(rows[index]))
            for i,row in enumerate(rows,1):row['sequence']=i
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            result=supervisor.relay_assessment(path,descriptor,case[4]/'stdout.raw',
                SimpleNamespace(ack_deadline=None,pending=None,error=None))
            assert result['status']=='unknown', (event,change,result)
    path.write_bytes(original)


@pytest.mark.parametrize('edge,valid', [('cap',True),('below',True),('above',False),('zero',False),('expired',False),('future',False),('nan',False),('inf',False)])
def test_absolute_deadline_canonical_ulp_contract(edge,valid):
    import math
    import omc_ad_native_supervisor as m
    origin=260345.234567892;deadline=origin+1800.;now=origin+.01
    if edge=='below':deadline=math.nextafter(deadline,-math.inf)
    if edge=='above':deadline=math.nextafter(deadline,math.inf)
    if edge=='zero':deadline=origin
    if edge=='expired':now=deadline
    if edge=='future':now=origin-1
    if edge=='nan':now=float('nan')
    if edge=='inf':origin=float('inf')
    assert hasattr(m,'absolute_span'), 'canonical deadline validation is missing'
    if valid:assert 0<m.absolute_span(origin,deadline,now)<=1800
    else:
        with pytest.raises(ValueError):m.absolute_span(origin,deadline,now)


@pytest.mark.parametrize('edge', ['cap','below','above'])
def test_public_absolute_canonical_cap_boundary(tmp_path,edge):
    import math
    origin=260345.234567892;deadline=origin+1800.
    if edge=='above':deadline=math.nextafter(deadline,math.inf)
    if edge=='below':deadline=math.nextafter(deadline,-math.inf)
    prompt=tmp_path/'prompt';prompt.write_text('synthetic input')
    # Test-only clock in the actual CLI process; real fork/child/stream path remains.
    shim=("import sys,time;sys.path.insert(0,"+repr(str(ROOT/'scripts'))+");"
          "import omc_ad_native_supervisor as m;old=time.monotonic;start=old();"
          "time.monotonic=lambda:"+repr(origin+.01)+"+old()-start;raise SystemExit(m.main())")
    argv=[sys.executable,'-c',shim,'--cwd',str(tmp_path),'--output',str(tmp_path/'out'),
          '--prompt',str(prompt),'--child-pid-file',str(tmp_path/'child.pid'),
          '--child-exit-file',str(tmp_path/'child.exit'),'--origin-monotonic',str(origin),
          '--deadline-monotonic',str(deadline),'--run-id','ulp-public','--',
          '/bin/sh','-c',RECORDER,'native-launch',sys.executable,'-c',
          f'import sys;sys.stdin.read();print({SUCCESS!r},end="")']
    result=subprocess.run(argv,capture_output=True,timeout=8)
    if edge=='above':
        assert result.returncode!=0 and b'invalid absolute deadline' in result.stderr
        assert not (tmp_path/'out').exists() and not (tmp_path/'child.pid').exists()
    else:
        record=json.loads((tmp_path/'out/result.json').read_text())
        assert record['process_result']=='normal' and record['cleanup_result']=='confirmed'
        assert record['full_contract_pass'] is False # No phase evidence manufactured.


@pytest.mark.parametrize('field', ['origin','deadline','now'])
@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-float('inf')])
def test_absolute_deadline_rejects_each_nonfinite_input(field,bad):
    import omc_ad_native_supervisor as m
    values={'origin':100.,'deadline':1900.,'now':101.};values[field]=bad
    with pytest.raises(ValueError):m.absolute_span(values['origin'],values['deadline'],values['now'])
