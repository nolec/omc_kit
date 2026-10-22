"""Synthetic native-prefix fixtures; no live Claude or UI success is claimed."""
import importlib
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import omc_pipeline_guard as guard

CLI = Path(__file__).with_name('omc_home_observation.py')


def test_native_launch_hands_off_approved_work_through_public_claude_start(tmp_path):
    from test_omc_pipeline_guard_session_reset import prebound_project
    context, args = prebound_project(tmp_path)
    # Actual shell + actual hook/context modules; the child is local Python, not Claude.
    before = guard._state_path(tmp_path).read_bytes()
    hook = CLI.parent.parent / 'templates/.agent-hooks/omc-session-start.sh'
    child = '''import json,subprocess,sys,uuid
hook, args, expected = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
payload = json.dumps({'event_id':str(uuid.uuid4()),'session_id':'native-fixture','source':'startup'})
result = subprocess.run(['bash',hook,'claude'],input=payload,text=True,capture_output=True)
assert result.returncode == 0, result.stderr
duplicate = subprocess.run(['bash',hook,'claude'],input=payload,text=True,capture_output=True)
assert duplicate.returncode == 0 and duplicate.stdout == '', duplicate.stderr
check = subprocess.run(args,text=True,capture_output=True)
if check.returncode: print(check.stdout, file=sys.stderr); raise SystemExit(3)
assert json.loads(check.stdout) == expected
print(json.dumps({'type':'result','subtype':'success'}))
'''
    driver = '''import json,sys
from pathlib import Path
from omc_home_observation import tee_native
root=Path(sys.argv[1]); context=json.loads(sys.argv[2])
raise SystemExit(tee_native(root,sys.argv[3:],root/'.omc/native.jsonl',[],context=context))
'''
    import os
    result = subprocess.run([sys.executable, '-c', driver, str(tmp_path), json.dumps(context),
        sys.executable, '-c', child, str(hook), json.dumps(args), json.dumps(context)],
        cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(CLI.parent)},
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert guard._state_path(tmp_path).read_bytes() == before
    assert (tmp_path / '.omc/native.jsonl').read_bytes() == result.stdout


def test_native_launch_drops_inherited_handoff_without_explicit_context(tmp_path, monkeypatch, capfd):
    monkeypatch.setenv('OMC_SESSION_START_HANDOFF', '{"inherited":"must not reuse"}')
    code = module().tee_native(tmp_path, [sys.executable, '-c',
        "import os; print(os.environ.get('OMC_SESSION_START_HANDOFF','absent'))"],
        tmp_path / '.omc/events.jsonl', [])
    assert code == 0
    assert (tmp_path / '.omc/events.jsonl').read_bytes() == b'absent\n'


def module():
    return importlib.import_module('omc_home_observation')


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def home(tmp_path):
    root = tmp_path/'candidate'
    root.mkdir()
    (root/'app.py').write_text('value = 1\n')
    req = {'schema_version': 'omc-home-motion-requirement/v1', 'session_id': 's',
           'run_id': 'r', 'url': 'http://localhost:3034/shop',
           'events_path': '.omc/native.jsonl', 'receipt_path': '.omc/home-receipt.json',
           'preflight_path': '.omc/ready.json', 'preflight_sha256': 'a'*64}
    put(root/'.omc/home-motion-requirement.json', req)
    # A current candidate and a valid six-sample same-track observation.
    observation = {'schema_version': 'omc-home-motion-observation/v1', 'session_id': 's',
                   'run_id': 'r', 'url': req['url'], 'code_snapshot_sha256': guard._code_snapshot_digest(root),
                   'server_binding': 'passed', 'tracks': [{'card_id': '0', 'track_id': '0:0',
                   'samples': [{'elapsed_ms': i*800, 'transform': 'matrix(1,0,0,1,%d,0)' % (i%2*100),
                                'connected': True, 'images_loaded': 2} for i in range(6)]}]}
    basis = {'session_id': 's', 'run_id': 'r', 'checks': [{'id': 'browser-home',
             'home_motion_sha256': hashlib.sha256((root/'.omc/home-motion-requirement.json').read_bytes()).hexdigest()}]}
    return root, req, observation, basis


def rows(observation, command=None, error=False):
    return [{'type': 'system', 'subtype': 'init'},
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'h', 'name': 'Bash',
             'input': {'command': command or 'python3 scripts/omc_home_observation.py observe --target .'}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'h',
             'is_error': error, 'content': json.dumps(observation)}]}}]


def write_events(root, values, suffix=b''):
    path = root/'.omc/native.jsonl'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b''.join(json.dumps(x).encode()+b'\n' for x in values)+suffix)
    return path


def selected(root):
    refs = module().receipt_references(root)
    # Synthetic tests explicitly pick their own single observation, not product policy.
    return refs[0] if refs else {'path': '.omc/missing-receipt.json', 'sha256': '0'*64}


def assess(home, values=None, suffix=b''):
    root, req, obs, basis = home
    write_events(root, rows(obs) if values is None else values, suffix)
    module().publish(root)
    return module().assess_completion_home(root, basis, submission=selected(root))


def test_native_prefix_passes_before_any_final_result(home):
    assert assess(home)['status'] == 'passed'


def test_publisher_error_preserves_both_raw_streams_and_separate_diagnostic(tmp_path):
    root = tmp_path
    # One write includes a complete event and a partial tail. Neither may vanish.
    raw = b'{"type":"system","subtype":"init"}\npartial-tail'
    driver = """
import sys
from pathlib import Path
from omc_home_observation import tee_native
root=Path(sys.argv[1])
def fail(): raise ValueError('publisher-test-failure')
try:
    tee_native(root,[sys.executable,'-c',sys.argv[2]],root/'.omc/events.jsonl',[fail])
except ValueError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
"""
    import os
    child = f"import os,time;os.write(1,{raw!r});time.sleep(30)"
    result = subprocess.run([sys.executable, '-c', driver, str(root), child],
        env={**os.environ, 'PYTHONPATH': str(CLI.parent)}, capture_output=True, timeout=15)
    assert result.returncode == 1, result.stderr
    assert result.stdout == raw
    assert (root/'.omc/events.jsonl').read_bytes() == raw
    diagnostics = list((root/'.omc/native-stream-errors').glob('*.json'))
    assert len(diagnostics) == 1
    diagnostic = json.loads(diagnostics[0].read_bytes())
    assert diagnostic['reason'] == 'publisher-test-failure'
    assert diagnostic['stream_sha256'] == hashlib.sha256(raw).hexdigest()
    assert diagnostic['child_returncode'] != 0
    assert b'native_publisher_failed' in result.stderr


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process-group regression')
@pytest.mark.parametrize('mode', ['publisher', 'ignore-term', 'outer-termination', 'launch-termination', 'escaped-descendant'])
def test_native_failure_cleanup_is_bounded_with_inherited_stdout(tmp_path, mode):
    import os
    import signal
    raw = b'{"type":"system","subtype":"init"}\n'
    child = """import os,pathlib,signal,sys,time
root=pathlib.Path(sys.argv[1])
if sys.argv[2]=='ignore-term': signal.signal(signal.SIGTERM,signal.SIG_IGN)
pid=os.fork()
if pid==0:
 if sys.argv[2]=='escaped-descendant': os.setsid()
 time.sleep(60)
 os._exit(0)
(root/'child.pid').write_text(str(os.getpid()))
(root/'descendant.pid').write_text(str(pid))
os.write(1,bytes.fromhex(sys.argv[3]))
time.sleep(60)
"""
    driver = """import os,pathlib,signal,subprocess,sys
sys.path.insert(0,sys.argv[1])
from omc_home_observation import tee_native
root=pathlib.Path(sys.argv[2])
if sys.argv[3]=='launch-termination':
 original=subprocess.Popen
 def launch(*args,**kwargs):
  process=original(*args,**kwargs)
  (root/'child.pid').write_text(str(process.pid))
  os.kill(os.getpid(),signal.SIGTERM)
  return process
 subprocess.Popen=launch
def publisher():
 (root/'publisher-entered').write_text('entered')
 if sys.argv[3]!='outer-termination': raise ValueError('bounded-cleanup-fixture')
try:tee_native(root,[sys.executable,'-c',sys.argv[4],str(root),sys.argv[3],sys.argv[5]],root/'.omc/events.jsonl',[publisher])
except ValueError as exc:print(exc,file=sys.stderr);sys.exit(1)
"""
    stderr = (tmp_path/'driver-stderr.txt').open('wb')
    process = subprocess.Popen([sys.executable, '-c', driver, str(CLI.parent), str(tmp_path), mode,
        child, raw.hex()], stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
    started = time.monotonic()
    try:
        if mode == 'outer-termination':
            deadline = started+5
            while not (tmp_path/'publisher-entered').exists() and time.monotonic()<deadline:
                time.sleep(.01)
            assert (tmp_path/'publisher-entered').exists()
            process.terminate()
        try:
            forwarded, _ = process.communicate(timeout=13)
        except subprocess.TimeoutExpired:
            pytest.fail('native runner did not finish bounded cleanup')
        assert process.returncode != 0
        expected = b'' if mode == 'launch-termination' else raw
        assert forwarded == expected == (tmp_path/'.omc/events.jsonl').read_bytes()
        records = list((tmp_path/'.omc/native-stream-errors').glob('*.json'))
        assert len(records) == 1, (tmp_path/'driver-stderr.txt').read_text()
        record = json.loads(records[0].read_bytes())
        assert record['stream_sha256'] == hashlib.sha256(expected).hexdigest()
        assert record['drain_complete'] is (mode != 'escaped-descendant')
        assert record['child_returncode'] is not None
        assert time.monotonic()-started < 13
        # An inherited pipe cannot remain open after process-group cleanup.
        assert record['termination_scope'] == 'process_group'
    finally:
        # Clean only process groups created by this fixture, including RED runs.
        for pid in [process.pid, int((tmp_path/'child.pid').read_text()) if (tmp_path/'child.pid').exists() else None]:
            if pid is not None:
                try: os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError: pass
        if mode == 'escaped-descendant' and (tmp_path/'descendant.pid').exists():
            try: os.kill(int((tmp_path/'descendant.pid').read_text()), signal.SIGKILL)
            except ProcessLookupError: pass
        process.communicate(timeout=3)
        stderr.close()


def test_h02_six_valid_identical_samples_are_unknown_not_ui_failure(home):
    for sample in home[2]['tracks'][0]['samples']:
        sample['transform'] = 'matrix(1,0,0,1,-100,0)'
    result = assess(home)
    assert result == {'status': 'unknown', 'reason': 'home_motion_not_observed'}


@pytest.mark.parametrize('defect', ['missing', 'one', 'reverse', 'same-time', 'short', 'nan', 'infinite', 'bool', 'detached', 'images', 'empty-state'])
def test_invalid_samples_do_not_pass(home, defect):
    samples = home[2]['tracks'][0]['samples']
    if defect == 'missing': samples[2].pop('elapsed_ms')
    elif defect == 'one': del samples[1:]
    elif defect == 'reverse': samples[2]['elapsed_ms'] = -1
    elif defect == 'same-time': samples[2]['elapsed_ms'] = samples[1]['elapsed_ms']
    elif defect == 'short': samples[2]['elapsed_ms'] -= 1
    elif defect == 'nan': samples[2]['elapsed_ms'] = float('nan')
    elif defect == 'infinite': samples[2]['elapsed_ms'] = float('inf')
    elif defect == 'bool': samples[0]['elapsed_ms'] = False
    elif defect == 'detached': samples[2]['connected'] = False
    elif defect == 'images': samples[2]['images_loaded'] = 1
    elif defect == 'empty-state': samples[2]['transform'] = ''
    assert assess(home)['status'] == 'unknown'


def test_delayed_valid_samples_pass(home):
    for i, sample in enumerate(home[2]['tracks'][0]['samples']): sample['elapsed_ms'] = i*950
    assert assess(home)['status'] == 'passed'


def test_different_tracks_cannot_be_combined(home):
    track = home[2]['tracks'][0]
    track['samples'] = track['samples'][:3]
    home[2]['tracks'].append({'card_id': '1', 'track_id': '1:0', 'samples': [dict(s, transform='other') for s in track['samples']]})
    assert assess(home)['status'] == 'unknown'


@pytest.mark.parametrize('field,value', [('run_id','other'),('session_id','other'),('url','http://localhost:3000/other'),('code_snapshot_sha256','0'*64)])
def test_wrong_binding_is_mismatch(home, field, value):
    home[2][field] = value
    assert assess(home)['status'] == 'mismatch'


@pytest.mark.parametrize('kind', ['missing-return', 'partial', 'failed', 'wrong-command', 'operator', 'duplicate-id'])
def test_native_call_and_success_return_required(home, kind):
    values = rows(home[2])
    suffix = b''
    if kind == 'missing-return': values.pop()
    elif kind == 'partial': values.pop(); suffix=b'{"type":'
    elif kind == 'failed': values[-1]['message']['content'][0]['is_error']=True
    elif kind == 'wrong-command': values[1]['message']['content'][0]['input']['command']='echo native'
    elif kind == 'operator': values=values[-1:]; home[2]['provenance']='native'
    elif kind == 'duplicate-id': values.insert(2,values[1])
    assert assess(home, values, suffix)['status'] != 'passed'


def test_final_append_preserves_receipt_but_prefix_tamper_is_mismatch(home):
    root, req, obs, basis = home
    assert assess(home)['status']=='passed'
    path=root/req['events_path']
    with path.open('ab') as stream: stream.write(b'{"type":"result","result":"done"}\n')
    assert module().assess_completion_home(root,basis,submission=selected(root))['status']=='passed'
    path.write_bytes(path.read_bytes().replace(b'"id": "h"', b'"id": "x"', 1))
    assert module().assess_completion_home(root,basis,submission=selected(root))['status']=='mismatch'


def test_candidate_edit_invalidates_prior_observation(home):
    assert assess(home)['status']=='passed'
    (home[0]/'app.py').write_text('value = 2\n')
    assert module().assess_completion_home(home[0],home[3],submission=selected(home[0]))['status']=='mismatch'


def test_non_opted_in_work_is_unchanged(tmp_path):
    assert module().assess_completion_home(tmp_path, {'checks':[{'id':'browser-home'}]}) is None


def test_requirement_cannot_be_dropped_from_basis(home):
    home[3]['checks'][0].pop('home_motion_sha256')
    with pytest.raises(ValueError): module().enforce_requirement(home[0],home[3])


def test_cli_unknown_is_exit_one_not_new_failure(home):
    for sample in home[2]['tracks'][0]['samples']: sample['transform']='same'
    assess(home)
    proposal=home[0]/'.omc/basis.json'; put(proposal,home[3])
    proc=subprocess.run([sys.executable,str(CLI),'assess','--target',str(home[0]),'--basis',str(proposal),
                         '--receipt',selected(home[0])['path'],'--receipt-sha256',selected(home[0])['sha256']],capture_output=True,text=True)
    assert proc.returncode==1
    assert json.loads(proc.stdout)['status']=='unknown'


def gate_fixture(home):
    """Legacy RED fixture isolates the new home gate; not real workflow evidence."""
    root, req, obs, basis = home
    basis.update(revision=1, request='synthetic home contract', approval_reference='fixture')
    basis['checks'][0].update(target=req['url'], method='native observation',
                             pass_condition='motion observed', target_version='candidate')
    put(root/'.omc/verification-bases/s/1.json',basis)
    put(root/'.omc/state/latest.json', {'latest_session_id':'s','latest_confirmed_session_id':'s'})
    put(root/'.omc/pipeline_session.json',{'started_at':time.time(),'contract_confirmed':True,'session_id':'s','red_session_id':'s',
        'red_done_tests':['test_app.py'],'required_checks':['browser-home'], 'basis_sha256':guard._basis_digest(basis)})
    verification={'session_id':'s','basis_sha256':guard._basis_digest(basis),
        'home_motion_result': selected(root),
        'implementation':{'status':'applied','evidence':'synthetic patch'},'checks':{'browser-home':{
        'status':'passed','evidence':'자동 전환 확인 주장', 'definition_sha256':guard._check_digest(basis['checks'][0]),
        'code_snapshot_sha256':guard._code_snapshot_digest(root)}}}
    path=root/'.omc/verification/s.json';put(path,verification)
    return path


def test_complete_cli_blocks_six_identical_states_despite_passed_claim(home):
    for sample in home[2]['tracks'][0]['samples']: sample['transform']='same'
    assert assess(home)['reason']=='home_motion_not_observed'
    verification=gate_fixture(home)
    proc=subprocess.run([sys.executable,str(CLI.with_name('omc_pipeline_guard.py')),'--target',str(home[0]),
                        'complete-task','--verification-file',str(verification)],capture_output=True,text=True)
    assert proc.returncode==1
    assert proc.stdout.startswith('구현됨·검증 미완료\n')
    assert 'home_motion_not_observed' in proc.stdout
    assert 'VERDICT: BLOCK' in proc.stdout


def test_complete_before_final_and_collector_recheck_same_prefix(home):
    from omc_workflow_validation import collect
    root=home[0]
    assert assess(home)['status']=='passed'
    verification=gate_fixture(home)
    proc=subprocess.run([sys.executable,str(CLI.with_name('omc_pipeline_guard.py')),'--target',str(root),
                        'complete-task','--verification-file',str(verification)],capture_output=True,text=True)
    assert proc.returncode==0,proc.stdout
    events=root/home[1]['events_path']
    # Native command trace includes the real CLI return, before its final report.
    with events.open('ab') as stream:
        for row in [
            {'type':'assistant','message':{'content':[{'type':'tool_use','id':'c','name':'Bash',
             'input':{'command':'python3 scripts/omc_pipeline_guard.py contract-done'}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'c','content':'CONTRACT 확인 완료'}]}},
            {'type':'assistant','message':{'content':[{'type':'tool_use','id':'d','name':'Bash',
             'input':{'command':'python3 scripts/omc_pipeline_guard.py red-done test_app.py'}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'d','content':'RED 완료 등록'}]}},
            {'type':'assistant','message':{'content':[{'type':'tool_use','id':'g','name':'Bash',
             'input':{'command':'python3 scripts/omc_pipeline_guard.py complete-task --verification-file '+str(verification)}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'g','content':proc.stdout}]}},
            {'type':'result','subtype':'success','result':proc.stdout}]:
            stream.write(json.dumps(row).encode()+b'\n')
    result=collect(root,events,'s',verification,'done',root/'.omc/collected')
    assert result['required']['home_motion']=='passed'
    assert result['recompare_entry']=='allow',result
    assert (root/'.omc/collected/final.txt').read_text()==proc.stdout
    altered=root/'.omc/altered.jsonl'
    altered.write_bytes(events.read_bytes().replace(b'"id": "h"',b'"id": "x"',1))
    result=collect(root,altered,'s',verification,'done',root/'.omc/collected-bad')
    assert result['home_assessment']['status']=='mismatch'
    assert result['recompare_entry']=='hold'


def test_run_publishes_before_child_can_emit_final(home):
    root,req,obs,basis=home
    gate_fixture(home)
    # Stand-in executor stops before final until its parent has published.
    code="""import json,pathlib,time,sys
for row in json.loads(sys.argv[1]): print(json.dumps(row),flush=True)
p=pathlib.Path(sys.argv[2])
for i in range(200):
 if any(p.parent.glob(p.name+'.*.json')): break
 time.sleep(.01)
else: sys.exit(9)
print(json.dumps({'type':'result','result':'synthetic final'}),flush=True)
"""
    proc=subprocess.run([sys.executable,str(CLI),'run','--target',str(root),'--launch',sys.executable,'-c',code,
                         json.dumps(rows(obs)),str(root/req['receipt_path'])],capture_output=True,text=True,timeout=10)
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert module().assess_completion_home(root,basis,submission=selected(root))['status']=='passed'
    assert json.loads(proc.stdout.splitlines()[-1])['type']=='result'


@pytest.mark.parametrize('defect',['outside','symlink','collision'])
def test_runtime_paths_fail_closed(home,tmp_path,defect):
    root,req,obs,basis=home
    if defect=='outside': req['events_path']='../outside.jsonl'
    elif defect=='collision': req['receipt_path']=req['events_path']
    else:
        outside=tmp_path/'outside';outside.mkdir()
        (root/'.omc/link').symlink_to(outside,target_is_directory=True)
        req['events_path']='.omc/link/events.jsonl'
    put(root/'.omc/home-motion-requirement.json',req)
    assert module().assess_completion_home(root,basis,submission={'path':'.omc/missing.json','sha256':'0'*64})['status']=='mismatch'


def test_existing_browser_query_collects_real_elapsed_six_samples():
    """Execute the real query against a fake DOM/clock, not a live home claim."""
    from omc_workflow_preflight import BROWSER_QUERY
    js=r"""
let elapsed=0;
global.performance={now:()=>elapsed};
global.setTimeout=(f,delay)=>{elapsed+=delay;f();};
const image={complete:true,naturalWidth:100};
const track={isConnected:true};
const ad={isConnected:true,contains:t=>t===track,
 querySelectorAll:s=>s==='img'?[image,image]:[track]};
global.document={querySelectorAll:()=>[ad],scripts:[]};
global.location={href:'http://localhost:3034/shop'};
global.getComputedStyle=()=>({transform:elapsed<2400?'matrix(1,0,0,1,0,0)':'matrix(1,0,0,1,-100,0)'});
eval(process.argv[1]).then(value=>console.log(JSON.stringify(value)));
"""
    result=subprocess.run(['node','-e',js,BROWSER_QUERY],capture_output=True,text=True,check=True)
    observed=json.loads(result.stdout)
    assert observed['auto_transition'] is True
    assert [s['elapsed_ms'] for s in observed['tracks'][0]['samples']]==[0,800,1600,2400,3200,4000]
    assert module().sample_assessment(observed)['status']=='passed'


@pytest.mark.parametrize('scenario,reason', [
    ('delayed-dom', None), ('delayed-images', None),
    ('absent', 'home_ad_not_ready'), ('one-image', 'home_ad_not_ready'),
    ('detached', 'home_ad_not_ready'),
    ('replaced', 'home_track_replaced'), ('stationary', 'home_motion_not_observed'),
])
def test_browser_query_separates_readiness_and_six_samples(scenario, reason):
    from omc_workflow_preflight import BROWSER_QUERY
    js=r"""
let t=0;global.performance={now:()=>t};global.setTimeout=(f,d)=>{t+=d;f()};
const scenario=process.argv[2];
const image={get complete(){return scenario!=='delayed-images'||t>=1200},naturalWidth:100};
const track={get isConnected(){return scenario!=='detached'&&(scenario!=='replaced'||t<1600)}};
const ad={isConnected:true,contains:x=>x===track&&track.isConnected,
 querySelectorAll:s=>s==='img'?(scenario==='one-image'?[image]:[image,image]):[track]};
global.document={querySelectorAll:()=>scenario==='absent'||(scenario==='delayed-dom'&&t<1200)?[]:[ad],scripts:[]};
global.location={href:'http://localhost/shop'};
global.getComputedStyle=()=>({transform:scenario==='stationary'?'A':(Math.floor(t/800)%2?'A':'B')});
eval(process.argv[1]).then(v=>console.log(JSON.stringify({...v,total_elapsed_ms:t})));
"""
    proc=subprocess.run(['node','-e',js,BROWSER_QUERY,scenario],capture_output=True,text=True,check=True,timeout=15)
    observed=json.loads(proc.stdout)
    assessed=module().sample_assessment(observed)
    assert assessed=={'status':'unknown' if reason else 'passed','reason':reason}
    readiness=observed['readiness']
    if scenario in {'absent','one-image','detached'}:
        assert readiness['status']=='timeout'
        assert 5000<=observed['total_elapsed_ms']<5200
        assert observed['tracks']==[]
        assert readiness['polls']>1
    else:
        assert readiness['status']=='ready'
        if scenario.startswith('delayed'):
            assert readiness['elapsed_ms']==1200
        if scenario=='replaced':
            assert observed['collection']['status']=='interrupted'
        else:
            samples=observed['tracks'][0]['samples']
            assert [s['elapsed_ms'] for s in samples]==[0,800,1600,2400,3200,4000]
            assert all(s['connected'] and s['images_loaded']>=2 for s in samples)


@pytest.mark.parametrize('defect',['gap','duration','disconnected','images','count','stationary'])
def test_readiness_metadata_never_relaxes_sample_requirements(home,defect):
    observed=home[2]
    observed['readiness']={'status':'ready'}
    observed['collection']={'status':'complete'}
    samples=observed['tracks'][0]['samples']
    if defect=='gap': samples[1]['elapsed_ms']=100
    elif defect=='duration':
        for i,s in enumerate(samples): s['elapsed_ms']=i*799
    elif defect=='disconnected': samples[2]['connected']=False
    elif defect=='images': samples[2]['images_loaded']=1
    elif defect=='count': samples.pop()
    else:
        for s in samples: s['transform']='same'
    assert module().sample_assessment(observed)['status']=='unknown'


@pytest.mark.parametrize('ready_at,wake_at,expected', [
    (0,0,'passed'), (4999,4999,'passed'), (1200,4900,'passed'),
    (5000,5000,'unknown'), (5001,5100,'unknown'),
    (5050,5100,'unknown'), (1200,5100,'unknown'), (6000,5100,'unknown'),
])
def test_readiness_deadline_precedes_ready_even_when_timer_wakes_late(ready_at,wake_at,expected):
    from omc_workflow_preflight import BROWSER_QUERY
    js=r"""
let t=0,firstWait=true;global.performance={now:()=>t};
global.setTimeout=(f,d)=>{if(firstWait){t=Number(process.argv[3]);firstWait=false;}else t+=d;f()};
const image={complete:true,naturalWidth:100};const track={isConnected:true};
const ad={isConnected:true,contains:x=>x===track,querySelectorAll:s=>s==='img'?[image,image]:[track]};
global.document={querySelectorAll:()=>t>=Number(process.argv[2])?[ad]:[],scripts:[]};
global.location={href:'http://localhost/shop'};
global.getComputedStyle=()=>({transform:Math.floor(t/800)%2?'A':'B'});
eval(process.argv[1]).then(v=>console.log(JSON.stringify({...v,total_elapsed_ms:t})));
"""
    proc=subprocess.run(['node','-e',js,BROWSER_QUERY,str(ready_at),str(wake_at)],capture_output=True,text=True,check=True,timeout=10)
    observed=json.loads(proc.stdout)
    assert module().sample_assessment(observed)=={
        'status':expected,'reason':'home_ad_not_ready' if expected=='unknown' else None}
    if expected=='unknown':
        assert observed['readiness']['status']=='timeout'
        assert observed['readiness']['elapsed_ms']==wake_at
        assert observed['collection']['status']=='unrun'
        assert observed['tracks']==[]
        assert observed['auto_transition'] is False
        assert observed['total_elapsed_ms']==wake_at
    else:
        assert observed['readiness']['status']=='ready'
        assert observed['readiness']['elapsed_ms']<5000
        assert len(observed['tracks'][0]['samples'])==6


def test_observe_uses_current_server_binding_and_detects_candidate_race(home,monkeypatch):
    import omc_workflow_preflight as preflight
    root,req,obs,basis=home
    config={'origin':'http://localhost:3034','home_path':'/shop'}
    ready={'status':'ready','target':str(root),'required_check_ids':['browser-home'],
           'server_binding':{'browser':config,'config':{'fixture':True},'evidence':{'fixture':True}}}
    put(root/req['preflight_path'],ready)
    req['preflight_sha256']=module().sha((root/req['preflight_path']).read_bytes())
    put(root/'.omc/home-motion-requirement.json',req)
    seen=[]
    monkeypatch.setattr(preflight,'browser_observation',lambda c:obs)
    monkeypatch.setattr(preflight,'server_binding',lambda *args,**kw:seen.append(kw))
    assert module().observe(root)['server_binding']=='passed'
    assert seen[0]['initial'] is False and seen[0]['observed'] is obs
    def changed(c):
        (root/'app.py').write_text('changed during query')
        return obs
    monkeypatch.setattr(preflight,'browser_observation',changed)
    with pytest.raises(ValueError,match='candidate_changed'): module().observe(root)


def test_init_pins_preflight_once_and_does_not_launch_provider(tmp_path):
    root=tmp_path
    ready={'status':'ready','target':str(root),'required_check_ids':['browser-home'],
           'server_binding':{'browser':{'origin':'http://localhost:3034','home_path':'/shop'}}}
    put(root/'.omc/ready.json',ready)
    proc=subprocess.run([sys.executable,str(CLI),'init','--target',str(root),'--preflight','.omc/ready.json',
                         '--run-id','r','--session-id','s'],capture_output=True,text=True)
    assert proc.returncode==0,proc.stdout
    pin=json.loads(proc.stdout)['home_motion_sha256']
    assert pin==module().sha((root/'.omc/home-motion-requirement.json').read_bytes())
    again=subprocess.run([sys.executable,str(CLI),'init','--target',str(root),'--preflight','.omc/ready.json',
                          '--run-id','r','--session-id','s'],capture_output=True,text=True)
    assert again.returncode==1
    assert not (root/'.omc/home-motion/events.jsonl').exists()


def test_new_basis_revision_cannot_erase_existing_home_requirement(home):
    root,req,obs,basis=home
    gate_fixture(home)
    proposal=json.loads(json.dumps(basis))
    proposal['revision']=2
    proposal['checks'][0].pop('home_motion_sha256')
    # Synthetic removal models accidental requirement loss, not a real deletion.
    (root/'.omc/home-motion-requirement.json').unlink()
    path=root/'.omc/proposal.json';put(path,proposal)
    assert guard.cmd_approve_checks(root,path)==1
    assert not (root/'.omc/verification-bases/s/2.json').exists()


def test_unpublished_evidence_blocks_without_writing_receipt(home):
    root,req,obs,basis=home
    write_events(root,rows(obs))
    verification=gate_fixture(home)
    result=guard.evaluate_task_completion(root,verification)
    assert result['home_assessment']=={'status':'unknown','reason':'home_native_receipt_missing'}
    assert result['outcome']=='blocked'
    assert not (root/req['receipt_path']).exists()


@pytest.mark.parametrize('moving',[False,True])
def test_browser_query_in_real_isolated_page(moving):
    """Local browser fixture only; does not attest a logged-in service home."""
    import os
    playwright=os.environ.get('OMC_PREFLIGHT_PLAYWRIGHT_MODULE')
    if not playwright:
        pytest.skip('explicit local Playwright module required')
    from omc_workflow_preflight import BROWSER_QUERY
    js=r"""
const {chromium}=require(process.argv[1]);
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage();
  const svg='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"></svg>');
  await page.setContent('<div data-testid="ad-card-large"><div data-testid="ad-track-large" style="transform:translateX(0px)"><img src="'+svg+'"><img src="'+svg+'"></div></div>');
  await page.waitForFunction(()=>[...document.images].every(i=>i.complete&&i.naturalWidth>0));
  if(process.argv[3]==='true')await page.evaluate(()=>{
   let step=0;setInterval(()=>document.querySelector('[data-testid=ad-track-large]').style.transform='translateX('+((++step%2)*-100)+'px)',900);
  });
  console.log(JSON.stringify(await page.evaluate(process.argv[2])));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
"""
    proc=subprocess.run(['node','-e',js,playwright,BROWSER_QUERY,str(moving).lower()],capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    observed=json.loads(proc.stdout)
    assert observed['auto_transition'] is moving
    result=module().sample_assessment(observed)
    assert result['status']==('passed' if moving else 'unknown')
    if not moving: assert result['reason']=='home_motion_not_observed'


@pytest.mark.parametrize('loaded_at',[800,4400])
def test_delayed_images_start_a_fresh_full_sampling_window(loaded_at):
    from omc_workflow_preflight import BROWSER_QUERY
    js=r"""let t=0;global.performance={now:()=>t};global.setTimeout=(f,d)=>{t+=d;f()};
const image={get complete(){return t>=Number(process.argv[2])},get naturalWidth(){return t>=Number(process.argv[2])?100:0}};
const track={isConnected:true};const ad={isConnected:true,contains:x=>x===track,querySelectorAll:s=>s==='img'?[image,image]:[track]};
global.document={querySelectorAll:()=>[ad],scripts:[]};global.location={href:'http://localhost/shop'};
global.getComputedStyle=()=>({transform:t<Number(process.argv[2])+2400?'A':'B'});
eval(process.argv[1]).then(v=>console.log(JSON.stringify(v)));"""
    proc=subprocess.run(['node','-e',js,BROWSER_QUERY,str(loaded_at)],capture_output=True,text=True,check=True)
    observed=json.loads(proc.stdout)
    assert observed['images_loaded']==2
    assert observed['auto_transition'] is True
    assert observed['readiness']['elapsed_ms']==loaded_at
    assert module().sample_assessment(observed)['status']=='passed'


@pytest.mark.parametrize('scenario,reason', [
    ('delayed-dom',None), ('delayed-images',None),
    ('replaced','home_track_replaced'), ('absent','home_ad_not_ready'),
])
def test_readiness_in_real_isolated_browser(scenario,reason):
    """A local fixture, never an attestation of the logged-in service home."""
    import os
    playwright=os.environ.get('OMC_PREFLIGHT_PLAYWRIGHT_MODULE')
    if not playwright: pytest.skip('explicit local Playwright module required')
    from omc_workflow_preflight import BROWSER_QUERY
    js=r"""
const {chromium}=require(process.argv[1]);
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage();await page.setContent('<main></main>');
 await page.evaluate(scenario=>{
  const svg='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>');
  const insert=()=>{
   document.querySelector('main').innerHTML='<div data-testid="ad-card-large"><div data-testid="ad-track-card-large" style="transform:translateX(0px)"><img><img></div></div>';
   const load=()=>document.querySelectorAll('img').forEach(i=>i.src=svg);
   if(scenario==='delayed-images')setTimeout(load,350);else load();
  };
  if(scenario==='delayed-dom')setTimeout(insert,350);
  else if(scenario!=='absent')insert();
  let n=0;setInterval(()=>{const t=document.querySelector('[data-testid=ad-track-card-large]');if(t)t.style.transform='translateX('+((++n%2)*-100)+'px)'},900);
  if(scenario==='replaced')setTimeout(insert,1000);
 },process.argv[3]);
 const observed=await page.evaluate(process.argv[2]);console.log(JSON.stringify(observed));
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
"""
    proc=subprocess.run(['node','-e',js,playwright,BROWSER_QUERY,scenario],capture_output=True,text=True,timeout=25)
    assert proc.returncode==0,proc.stderr
    observed=json.loads(proc.stdout)
    assert module().sample_assessment(observed)=={'status':'unknown' if reason else 'passed','reason':reason}
    if scenario.startswith('delayed'):
        assert observed['readiness']['elapsed_ms']>=300
        samples=observed['tracks'][0]['samples']
        assert len(samples)==6
        assert all(b['elapsed_ms']-a['elapsed_ms']>=800 for a,b in zip(samples,samples[1:]))
        assert samples[-1]['elapsed_ms']-samples[0]['elapsed_ms']>=4000


def append_observation(home,ident='h2'):
    values=rows(home[2])[1:]
    values[0]['message']['content'][0]['id']=ident
    values[1]['message']['content'][0]['tool_use_id']=ident
    with (home[0]/home[1]['events_path']).open('ab') as stream:
        for row in values:stream.write(json.dumps(row).encode()+b'\n')


def test_reobservation_is_preserved_and_requires_explicit_selection(home):
    root,req,obs,basis=home
    for sample in obs['tracks'][0]['samples']:sample['transform']='same'
    write_events(root,rows(obs))
    assert module().publish(root)
    first=module().receipt_references(root)[0]
    first_bytes=(root/first['path']).read_bytes()
    for i,sample in enumerate(obs['tracks'][0]['samples']):sample['transform']='A' if i%2 else 'B'
    append_observation(home)
    assert module().publish(root)
    choices=module().receipt_references(root)
    assert len(choices)==2
    second=next(r for r in choices if r['call_id']=='h2')
    assert module().assess_completion_home(root,basis)['reason']=='home_observation_selection_required'
    assert module().assess_completion_home(root,basis,submission=first)['reason']=='home_motion_not_observed'
    assert module().assess_completion_home(root,basis,submission=second)['status']=='passed'
    assert (root/first['path']).read_bytes()==first_bytes
    assert module().publish(root) is False


def test_candidate_recheck_selects_new_call_without_replacing_old_receipt(home):
    root,req,obs,basis=home
    write_events(root,rows(obs));module().publish(root)
    first=module().receipt_references(root)[0]
    (root/'app.py').write_text('changed candidate')
    obs['code_snapshot_sha256']=guard._code_snapshot_digest(root)
    append_observation(home);module().publish(root)
    second=next(r for r in module().receipt_references(root) if r['call_id']=='h2')
    assert module().assess_completion_home(root,basis,submission=first)['status']=='mismatch'
    assert module().assess_completion_home(root,basis,submission=second)['status']=='passed'


def test_completion_and_collector_use_the_explicit_new_observation(home):
    from omc_workflow_validation import collect
    root,req,obs,basis=home
    for sample in obs['tracks'][0]['samples']:sample['transform']='same'
    write_events(root,rows(obs));module().publish(root)
    first=module().receipt_references(root)[0]
    for i,sample in enumerate(obs['tracks'][0]['samples']):sample['transform']='A' if i%2 else 'B'
    append_observation(home);module().publish(root)
    second=next(r for r in module().receipt_references(root) if r['call_id']=='h2')
    verification=gate_fixture(home)
    record=json.loads(verification.read_text())
    record['home_motion_result']=first;put(verification,record)
    assert guard.evaluate_task_completion(root,verification)['outcome']=='blocked'
    record.pop('home_motion_result');put(verification,record)
    assert guard.evaluate_task_completion(root,verification)['home_assessment']['reason']=='home_observation_selection_required'
    record['home_motion_result']=second;put(verification,record)
    assert guard.evaluate_task_completion(root,verification)['outcome']=='done'
    path=root/req['events_path']
    with path.open('ab') as stream: stream.write(json.dumps({'type':'result','subtype':'success','result':'synthetic incomplete final'}).encode()+b'\n')
    result=collect(root,path,'s',verification,'done',root/'.omc/collected-multiple')
    assert result['home_assessment']['status']=='passed'
    assert (root/first['path']).exists()


@pytest.mark.parametrize('failed',['error-flag','non-json-output'])
def test_failed_observation_does_not_prevent_later_successful_call(home,failed):
    root,req,obs,basis=home
    values=rows(obs)
    if failed=='error-flag':values[-1]['message']['content'][0]['is_error']=True
    else:values[-1]['message']['content'][0]['content']='observation unavailable'
    write_events(root,values)
    assert module().publish(root) is False
    append_observation(home)
    assert module().publish(root) is True
    refs=module().receipt_references(root)
    assert len(refs)==1 and refs[0]['call_id']=='h2'
    assert module().assess_completion_home(root,basis,submission=refs[0])['status']=='passed'


def test_selected_receipt_hash_is_checked(home):
    assert assess(home)['status']=='passed'
    ref=selected(home[0]);ref['sha256']='0'*64
    assert module().assess_completion_home(home[0],home[3],submission=ref)['reason']=='home_selected_receipt_digest_mismatch'


def test_parallel_observation_returns_have_distinct_selectable_receipts(home):
    root,req,obs,basis=home
    first=rows(obs)
    second=rows(obs)[1:]
    second[0]['message']['content'][0]['id']='h2'
    second[1]['message']['content'][0]['tool_use_id']='h2'
    # Two results may be emitted together in one native user-message line.
    first[-1]['message']['content']+=second[-1]['message']['content']
    write_events(root,[first[0],first[1],second[0],first[-1]])
    assert module().publish(root)
    refs=module().receipt_references(root)
    assert {r['call_id'] for r in refs}=={'h','h2'}
    for ref in refs:assert module().assess_completion_home(root,basis,submission=ref)['status']=='passed'
