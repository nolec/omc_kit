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
