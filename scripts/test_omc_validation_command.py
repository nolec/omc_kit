"""Local CLI/receipt regressions. Synthetic fixtures are not a Claude comparison."""
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_omc_native_red_evidence import project, TEST, CONFIG, DEPENDENCIES

SCRIPT = Path(__file__).with_name('omc_validation_command.py')


def module():
    return importlib.import_module('omc_validation_command')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize('drift', [None, 'session', 'run', 'basis', 'contract'])
def test_prebound_task_context_checks_exact_approved_execution(project, drift):
    from test_omc_native_red_evidence import configured, guard
    root = project
    basis = configured(root)
    policy = {'arm': 'omc-kit', 'session_id': 's', 'run_id': 'run'}
    policy_pin = 'a'*64
    basis['validation_commands_policy_sha256'] = policy_pin
    basis['revision'] = 2
    proposal = root/'.omc/prebound.json'; proposal.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root, proposal) == 0
    assert guard.cmd_contract_done(root, required_checks='tests') == 0
    if drift == 'session': policy['session_id'] = 'other'
    elif drift == 'run': policy['run_id'] = 'other'
    elif drift == 'basis': policy_pin = 'b'*64
    elif drift == 'contract':
        state = guard._load_state(root); state['contract_confirmed'] = False
        guard._save_state(root, state)
    before = (root/'.omc/state/latest.json').read_bytes()
    if drift:
        with pytest.raises(ValueError, match='prebound_task_context_mismatch'):
            module().prebound_task_context(root, policy, policy_pin)
    else:
        context = module().prebound_task_context(root, policy, policy_pin)
        assert context['session_id'] == 's' and context['run_id'] == 'run'
    assert (root/'.omc/state/latest.json').read_bytes() == before


def test_selection_path_is_canonical_runtime_and_does_not_change_candidate(project):
    from omc_pipeline_guard import _code_snapshot_digest
    root = project
    before = _code_snapshot_digest(root)
    policy = {'run_id': 'same-run'}
    path = module().selection_path(root, policy)
    assert path == root/'.omc/comparisons/same-run/final-selection.json'
    path.parent.mkdir(parents=True); path.write_text('{}')
    assert _code_snapshot_digest(root) == before
    policy['selection_path'] = 'final-selection.json'
    with pytest.raises(ValueError, match='selection_path_mismatch'):
        module().selection_path(root, policy)
    (root/'final-selection.json').write_text('{}')
    assert _code_snapshot_digest(root) != before


def test_collector_rejects_root_selection_before_reading_events(project, capsys):
    from omc_workflow_validation import common_main
    root = project
    output = root/'.omc/assessment'
    code = common_main(['--target', str(root), '--events-file', str(root/'missing'),
        '--selection-file', str(root/'final-selection.json'), '--policy-sha256', 'a'*64,
        '--output-dir', str(output)])
    assert code == 1
    assert 'home_path_must_be_runtime' in capsys.readouterr().out


def test_runtime_command_recognition_is_exact_and_not_a_shell_exemption(project):
    root = project
    policy = {'run_id': 'same-run'}
    expected = {'selection_path': '.omc/comparisons/same-run/final-selection.json'}
    cmd = 'python3 scripts/omc_validation_command.py selection-path --target .'
    assert module().runtime_command_result(root, cmd, policy, 'a'*64) == expected
    assert module().runtime_command_result(root, cmd+'; true', policy, 'a'*64) is None
    assert module().runtime_command_result(root, cmd+' --target /tmp', policy, 'a'*64) is None
    assert module().runtime_command_result(root, cmd.replace('selection-path', 'freeze'), policy, 'a'*64) is None


def test_prebound_public_cli_reuses_guard_without_creating_session(validation):
    from test_omc_native_red_evidence import configured, guard
    root, proposal = validation
    basis = configured(root)
    proposal.update(session_id='s', run_id='run', internal_tools={
        'omc-guard-task': {'path': str(root/'scripts/omc_guard.py'), 'sha256': digest(root/'scripts/omc_guard.py')}})
    latest = root/'.omc/state/latest.json'
    value = json.loads(latest.read_bytes()); value['latest_confirmed_roles'] = ['senior_coding']
    latest.write_text(json.dumps(value))
    root, policy_ref = freeze(validation)
    basis.update(revision=2, validation_commands_policy_sha256=policy_ref['sha256'])
    path = root/'.omc/approved-context.json'; path.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root, path) == 0
    assert guard.cmd_contract_done(root, required_checks='tests') == 0
    before = latest.read_bytes()
    args = ['task-context', '--sha256', policy_ref['sha256'], '--session-id', 's', '--run-id', 'run',
        '--basis-sha256', guard._basis_digest(basis)]
    context_call = cli(root, *args)
    assert context_call.returncode == 0, context_call.stdout+context_call.stderr
    command = ('python3 scripts/omc_validation_command.py task-context --target . --sha256 '
        +policy_ref['sha256']+' --session-id s --run-id run --basis-sha256 '+guard._basis_digest(basis))
    assert module().runtime_command_result(root, command, proposal, policy_ref['sha256']) == json.loads(context_call.stdout)
    wrong = cli(root, *args[:-1], '0'*64)
    assert wrong.returncode == 2 and 'prebound_task_context_mismatch' in wrong.stdout
    result = cli(root, 'run', '--profile', 'omc-guard-task')
    assert result.returncode == 0, result.stdout+result.stderr
    receipt = json.loads((root/json.loads(result.stdout)['receipt']['path']).read_bytes())
    assert receipt['invocations'][0]['argv'][2:] == ['require','--for','task','--target','.']
    assert receipt['execution_context'] == json.loads(context_call.stdout)
    assert latest.read_bytes() == before
    selected = cli(root, 'selection-path')
    assert json.loads(selected.stdout)['selection_path'] == '.omc/comparisons/run/final-selection.json'
    value['latest_session_id'] = value['latest_confirmed_session_id'] = 'new'
    latest.write_text(json.dumps(value))
    blocked = cli(root, 'run', '--profile', 'omc-guard-task')
    assert blocked.returncode == 2
    receipt = json.loads((root/json.loads(blocked.stdout)['receipt']['path']).read_bytes())
    assert receipt['judgment']['reason'] == 'prebound_task_context_mismatch'
    assert receipt['invocations'] == []


def cli(root, *args):
    installed=root/'scripts'/SCRIPT.name
    return subprocess.run([sys.executable, str(installed), *args, '--target', str(root)],
                          capture_output=True, text=True)


@pytest.fixture
def validation(project):
    root = project
    (root/'scripts').mkdir()
    for script in SCRIPT.parent.glob('omc_*.py'):
        shutil.copy2(script,root/'scripts'/script.name)
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    (root/'.gitignore').write_text('node_modules\n.omc/\n__pycache__/\napps/store-manager/public/omc-ad-*\n')
    (root/Path(TEST).parent/'Ad.styled.ts').write_text('export const style = 1;')
    tools = root.parent/'tools'; tools.mkdir()
    lint = tools/'lint.cjs'; lint.write_text('process.exit(0);')
    controlled = root.parent/'build-controlled.cjs'
    controlled.write_text("const fs=require('fs');const p='apps/store-manager/public/omc-ad-validation';fs.mkdirSync(p,{recursive:true});for(const n of ['index.html','bundle.js'])fs.writeFileSync(p+'/'+n,'built');")
    pixels = root/'.omc/visual-input/build-pixels.cjs'; pixels.parent.mkdir(parents=True)
    pixels.write_text(controlled.read_text().replace('omc-ad-validation', 'omc-ad-pixels'))
    baseline = root/'.omc/baseline.json'
    result = subprocess.run([str(DEPENDENCIES/'.bin/jest'), '--config', CONFIG,
        '--runInBand', '--runTestsByPath', TEST, '--json', '--outputFile', str(baseline)],
        cwd=root, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout+result.stderr
    subprocess.run(['git', 'add', '--', '.gitignore', 'libs'], cwd=root, check=True)
    paths = {'node':Path(shutil.which('node')).resolve(), 'python':Path(sys.executable).resolve(),
             'jest':(DEPENDENCIES/'jest/bin/jest.js').resolve(), 'eslint':lint,
             'controlled_builder':controlled, 'pixel_builder':pixels,
             'visual_checker':SCRIPT.with_name('omc_visual_check.py'),
             'runner':SCRIPT, 'baseline_tests':baseline}
    proposal = {'schema_version':'omc-validation-policy/v1',
        'contract_id':'advertising-comparison/v2', 'run_id':'local-fixture', 'arm':'omc-kit',
        'target':str(root.resolve()), 'tools':{k:{'path':str(p),'sha256':digest(p)}
              for k,p in paths.items() if p.exists()}, 'timeout_sec':30}
    # The new runner itself cannot be hashed before GREEN; add it in freeze().
    return root, proposal


def freeze(validation):
    root, proposal = validation
    installed=root/'scripts'/SCRIPT.name
    proposal['tools']['runner']={'path':str(installed.resolve()),'sha256':digest(installed)}
    f=root/'.omc/proposal-validation.json';f.write_text(json.dumps(proposal))
    result=cli(root,'freeze','--file',str(f),'--sha256',digest(f))
    assert result.returncode==0,result.stdout+result.stderr
    return root,json.loads(result.stdout)


@pytest.mark.parametrize('arm', ['omc-kit', 'oh-my-claudecode'])
@pytest.mark.parametrize('lint_failure,red_final', [
    (False,'passed'), (True,'passed'), ('unavailable','passed'),
    (False,'deleted'), (False,'renamed'), (False,'failed')])
def test_public_cli_complete_local_chain_and_fail_closed_variants(validation, tmp_path, arm, lint_failure, red_final):
    """Synthetic bridge observations, real CLI/Jest/builds/pixel rendering.

    No production home, provider, plugin success or business effect is claimed.
    """
    from PIL import Image
    from test_omc_visual_check import build_site
    from test_omc_workflow_preflight import start_bound_server
    import omc_workflow_preflight as preflight
    root,proposal=validation
    proposal['arm']=arm
    if lint_failure:
        lint=Path(proposal['tools']['eslint']['path'])
        lint.write_text('process.exit(2);' if lint_failure=='unavailable' else 'process.exit(1);')
        proposal['tools']['eslint']['sha256']=digest(lint)
    processes=[]
    public=root/'apps/store-manager/public';public.mkdir(parents=True)
    origin,proc,started=start_bound_server(root,processes,directory=public)
    build_site(tmp_path,(root,origin,{'stale':False}))
    (root/'index.html').write_text((root/'index.html').read_text().replace('/bundle.js','bundle.js'))
    artifact=public/'inspection/bundle.js';artifact.parent.mkdir(parents=True)
    artifact.write_text('fixed served candidate fixture')
    state={'url':origin+'/sixshoptest01'}
    home={'images_loaded':2,'auto_transition':True,'script_urls':[origin+'/inspection/bundle.js'],
          'tracks':[{'card_id':'0','track_id':'0:0','samples':[
              {'elapsed_ms':i*800,'connected':True,'images_loaded':2,'transform':str(i%2)} for i in range(6)]}]}
    class Control(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            op=request['op'];args=request['args']
            if op=='goto':state['url']=origin+args[0];value={'url':state['url']}
            elif op=='reload':value={'url':state['url']}
            elif op=='snapshot':
                path=root/'.omc/browser'/(uuid.uuid4().hex+'.png');path.parent.mkdir(exist_ok=True)
                Image.new('RGB',(1440,1000),'white').save(path)
                value={'url':state['url'],'screenshot':str(path)}
            elif 'const variants=' in args[0]:
                value={'url':state['url'],'arrows':0,'transitioned':True,'variants':[
                    {'variant':v,'border':'1px solid rgba(0, 0, 0, 0.1)',
                     'shadow':'rgba(0, 0, 0, 0.05) 0px 1px 2px 0px','images':2}
                    for v in module().VARIANTS]}
            else:value={'url':state['url'],**home}
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(value).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Control)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        for key,directory in [('controlled_builder','omc-ad-validation'),('pixel_builder','omc-ad-pixels')]:
            script=Path(proposal['tools'][key]['path'])
            script.write_text("const fs=require('fs');const p='apps/store-manager/public/"+directory+"';fs.mkdirSync(p,{recursive:true});for(const n of ['index.html','bundle.js'])fs.copyFileSync(n,p+'/'+n);")
            proposal['tools'][key]['sha256']=digest(script)
        browser={'control_url':f'http://127.0.0.1:{server.server_port}/command',
                 'origin':origin,'home_path':'/sixshoptest01','min_images':2}
        config={'launcher_pid':proc.pid,'launcher_started_at':started,
                'bundles':[{'file':str(artifact.relative_to(root)),'url_path':'/inspection/bundle.js','sha256':digest(artifact)}]}
        binding=preflight.server_binding(root,config,browser,observed={'url':state['url'],**home})
        ready=root/'.omc/ready-common.json';ready.write_text(json.dumps({'status':'ready','target':str(root),
            'server_binding':{'config':config,'browser':browser,'evidence':binding}}))
        playwright=DEPENDENCIES/'playwright'
        assert (playwright/'package.json').is_file(),'local Playwright required, no simulated pixel fallback'
        proposal.update(browser=browser,preflight={'path':str(ready.relative_to(root)),'sha256':digest(ready)},
            visual={'origin':origin,'playwright_module':str(playwright),
                    'package_sha256':digest(playwright/'package.json')})
        root,pin=freeze(validation)
        rows=[]
        query = 'python3 scripts/omc_validation_command.py selection-path --target .'
        rows.extend([{'type':'assistant','message':{'content':[{'type':'tool_use','id':'selection-query',
            'name':'Bash','input':{'command':query}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'selection-query',
                'content':json.dumps(module().runtime_command_result(root, query, proposal, pin['sha256']))}]}}])
        def edited(path,text):
            path.write_text(text)
            ident=uuid.uuid4().hex
            rows.extend([{'type':'assistant','message':{'content':[{'type':'tool_use','id':ident,
                'name':'Edit','input':{'file_path':str(path)}}]}},
                {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':ident,'content':'updated'}]}}])
        def executed(profile):
            result=cli(root,'run','--profile',profile)
            assert result.returncode in (0,1,2),result.stdout+result.stderr
            value=json.loads(result.stdout);ident=uuid.uuid4().hex
            rows.extend([{'type':'assistant','message':{'content':[{'type':'tool_use','id':ident,'name':'Bash',
                'input':{'command':'python3 scripts/omc_validation_command.py run --target . --profile '+profile}}]}},
                {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':ident,
                'content':result.stdout,'is_error':result.returncode!=0}]}}])
            return value['receipt']
        edited(root/TEST,(root/TEST).read_text()+"\ntest('new RED',()=>expect(require('fs').readFileSync(__dirname+'/Ad.tsx','utf8')).toContain('green'));\n"
            +"test('second RED',()=>expect(require('fs').readFileSync(__dirname+'/Ad.tsx','utf8')).toContain('green'));\n")
        red=executed('tests')
        edited(root/Path(TEST).parent/'Ad.tsx','green')
        if red_final!='passed':
            text=(root/TEST).read_text()
            if red_final=='deleted':
                text=text.split("\ntest('new RED'")[0]
            elif red_final=='renamed':
                text=text.replace("test('new RED'", "test('renamed RED'")
            else:
                text=text.replace(".toContain('green')", ".toContain('unresolved')")
            edited(root/TEST,text)
        refs={profile:executed(profile) for profile in module().REQUIRED}
        final='필수 검증 통과·작업 완료\n기존 실패: 1개(미해결)\n로컬 합성 증거 경로 검증; 실제 업무 검증 아님.'
        rows.append({'type':'result','result':final})
        events=root/'.omc/common-events.jsonl';events.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        selection={'schema_version':'omc-common-selection/v2','contract_id':module().CONTRACT,
                   'run_id':proposal['run_id'],'receipts':refs,'red_receipt':red}
        chosen=module().selection_path(root, proposal)
        chosen.parent.mkdir(parents=True);chosen.write_text(json.dumps(selection))
        collector=root/'scripts/omc_workflow_validation.py'
        def assess(name,values=rows,selected=selection):
            event=root/'.omc'/('events-'+name+'.jsonl');event.write_text(''.join(json.dumps(r)+'\n' for r in values))
            choose=chosen;choose.write_text(json.dumps(selected))
            output=root/'.omc'/('common-'+name)
            selection_args = [] if name == 'valid' else ['--selection-file', str(choose)]
            result=subprocess.run([sys.executable,str(collector),'compare-entry','--target',str(root),
                '--events-file',str(event),*selection_args,'--policy-sha256',pin['sha256'],
                '--output-dir',str(output)],capture_output=True,text=True)
            report=json.loads((output/'assessment.json').read_text())
            assert (output/'final.txt').read_text()==next(r['result'] for r in values if r['type']=='result')
            return result,report,output
        result,report,output=assess('valid')
        if red_final!='passed':
            assert result.returncode==1 and report['comparison_entry']=='hold',report
            assert 'red_cases_not_passed' in report['issues'],report
            expected='failed' if red_final=='failed' else 'passed'
            assert report['checks']['tests']==expected,report
            assert report['common_work']==expected,report
            assert len(report['checks'])==5,report
            assert '공통 업무 검증: '+expected in (output/'report.txt').read_text()
            assert (output/'final.txt').read_text()==final
            return
        if lint_failure:
            assert result.returncode==1 and report['comparison_entry']=='hold',report
            expected='unknown' if lint_failure=='unavailable' else 'failed'
            assert report['common_work']==expected and report['checks']['lint']==expected
            assert '공통 업무 검증: '+expected in (output/'report.txt').read_text()
            assert (output/'final.txt').read_text()==final
            return
        assert result.returncode==0,report
        assert report['common_work']=='passed' and report['comparison_entry']=='allow'
        assert set(report['checks'])=={'tests','lint','browser-controlled','browser-home','browser-visual-controlled'}
        assert set(report['profiles'])==set(module().REQUIRED)
        assert report['internal_procedure']==('unknown' if arm=='omc-kit' else 'not_applicable')
        assert '도구 내부 절차:' in (output/'report.txt').read_text()
        import copy
        missing=copy.deepcopy(selection);missing['receipts'].pop('home')
        assert assess('missing',selected=missing)[1]['comparison_entry']=='hold'
        unknown=copy.deepcopy(rows)
        unknown[-1:-1]=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'x','name':'Bash','input':{'command':'python3 -c "print(1)"'}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'x','content':'1'}]}}]
        assert assess('unsupported',values=unknown)[1]['comparison_entry']=='hold'
        unmatched=copy.deepcopy(rows);del unmatched[-2]
        assert assess('missing-return',values=unmatched)[1]['comparison_entry']=='hold'
        late=copy.deepcopy(rows);late[4:6],late[6:8]=late[6:8],late[4:6]
        assert assess('late-red',values=late)[1]['comparison_entry']=='hold'
        omitted=copy.deepcopy(rows);omitted[-1]['result']='필수 검증 통과·작업 완료'
        assert assess('hidden-existing-failure',values=omitted)[1]['comparison_entry']=='hold'
        conflicting=copy.deepcopy(rows);conflicting[-1]['result']+='\n검증 실패·수정 필요'
        assert assess('conflicting-report',values=conflicting)[1]['comparison_entry']=='hold'
        bad_ref=copy.deepcopy(selection);bad_ref['receipts']['lint']['sha256']='0'*64
        assert assess('bad-receipt',selected=bad_ref)[1]['comparison_entry']=='hold'
        product = root/Path(TEST).parent/'Ad.tsx'
        original_product = product.read_bytes()
        product.write_bytes(original_product+b'\n// changed after receipts')
        with pytest.raises(ValueError, match='receipt_candidate_stale'):
            module().validate_receipt(root, refs['lint'], pin['sha256'], 'lint')
        product.write_bytes(original_product)
        (root/'outside-scope.txt').write_text('unexpected')
        assert assess('outside')[1]['comparison_entry']=='hold'
        (root/'outside-scope.txt').unlink()
        (root/TEST).write_text((root/TEST).read_text()+'\n// changed final candidate\n')
        assert assess('stale')[1]['comparison_entry']=='hold'
        assert json.loads((output/'assessment.json').read_text())['comparison_entry']=='allow'
    finally:
        server.shutdown();server.server_close();thread.join()
        for proc in processes:
            proc.terminate();proc.wait(timeout=10);proc.stdout.close()


@pytest.mark.parametrize('exit_code,expected', [(1,'failed'),(2,'unknown')])
def test_cli_lint_failure_and_unavailable_are_distinct(validation, exit_code, expected):
    root,proposal=validation;script=Path(proposal['tools']['eslint']['path'])
    script.write_text('process.exit('+str(exit_code)+');')
    proposal['tools']['eslint']['sha256']=digest(script)
    root,pin=freeze(validation)
    result=cli(root,'run','--profile','lint');value=json.loads(result.stdout)
    assert result.returncode==(1 if expected=='failed' else 2)
    assert module().validate_receipt(root,value['receipt'],pin['sha256'])['status']==expected


def test_cli_timeout_preserves_partial_output_without_retry(validation):
    root,proposal=validation;script=Path(proposal['tools']['controlled_builder']['path'])
    script.write_text("process.stdout.write('started');require('child_process').spawn(process.execPath,"
        "['-e',\"setTimeout(()=>require('fs').writeFileSync('late.txt','bad'),1800)\"],{stdio:'ignore'});setTimeout(()=>{},5000);")
    proposal['tools']['controlled_builder']['sha256']=digest(script);proposal['timeout_sec']=1
    root,pin=freeze(validation)
    result=cli(root,'run','--profile','build-controlled');value=json.loads(result.stdout)
    assert result.returncode==2
    doc=module().validate_receipt(root,value['receipt'],pin['sha256'])
    assert doc['judgment']=={'status':'unknown','reason':'validation_command_timeout'}
    assert len(doc['invocations'])==1
    assert (root/doc['stdout']['path']).read_bytes()==b'started'
    time.sleep(1.2)
    assert not (root/'late.txt').exists(),'owned child must not keep running after timeout'


def test_receipt_cannot_substitute_an_arbitrary_argv(validation):
    root,pin=freeze(validation)
    value=json.loads(cli(root,'run','--profile','lint').stdout)
    path=root/value['receipt']['path'];doc=json.loads(path.read_text())
    doc['invocations'][0]['argv']=['python3','-c','print(1)']
    path.write_text(json.dumps(doc));value['receipt']['sha256']=digest(path)
    with pytest.raises(ValueError,match='execution_record_mismatch'):
        module().validate_receipt(root,value['receipt'],pin['sha256'])


def test_actual_cli_runs_fixed_builder_and_validates_receipt(validation):
    root,pin=freeze(validation)
    result=cli(root,'run','--profile','build-controlled')
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    receipt=module().validate_receipt(root,value['receipt'],pin['sha256'])
    assert receipt['status']=='passed'
    assert receipt['before']==receipt['after']
    assert (root/'apps/store-manager/public/omc-ad-validation/bundle.js').read_text()=='built'
    from omc_workflow_validation import assess_native_procedure
    rows=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'v','name':'Bash',
        'input':{'command':'python3 scripts/omc_validation_command.py run --target . --profile build-controlled'}}]}},
        {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'v','content':result.stdout}]}},
        {'type':'result'}]
    basis={'validation_commands_policy_sha256':pin['sha256'],
           'checks':[{'native_red':{'product_paths':[str(Path(TEST).parent)]}}]}
    audit=assess_native_procedure(root,rows,basis)
    assert audit['status']=='unknown' and audit['reason']=='required_success_return_missing'
    basis['validation_commands_policy_sha256']='0'*64
    assert 'validation_command_unknown:' in assess_native_procedure(root,rows,basis)['reason']


def test_real_jest_preserves_existing_failures_and_records_new_red(validation):
    root,pin=freeze(validation)
    first=cli(root,'run','--profile','tests');assert first.returncode==0,first.stdout
    with (root/TEST).open('a') as f:f.write("\ntest('new RED',()=>expect(1).toBe(2));\n")
    red=cli(root,'run','--profile','tests');assert red.returncode==1,red.stdout
    doc=module().validate_receipt(root,json.loads(red.stdout)['receipt'],pin['sha256'])
    assert doc['exit_code']==1 and doc['judgment']['new_failed_cases']==['new RED']


@pytest.mark.parametrize('expected,exit_code,status', [(3,1,'failed'),(1,0,'passed')])
def test_baseline_diagnostic_change_and_resolution_are_distinct(validation,expected,exit_code,status):
    root,pin=freeze(validation)
    path=root/TEST;path.write_text(path.read_text().replace('toBe(2)','toBe('+str(expected)+')'))
    result=cli(root,'run','--profile','tests')
    doc=module().validate_receipt(root,json.loads(result.stdout)['receipt'],pin['sha256'])
    assert doc['exit_code']==exit_code and doc['status']==status
    assert doc['judgment']['new_failed_cases']==[]
    assert doc['judgment']['same_existing_diagnostics'] is (expected==1)


@pytest.mark.parametrize('defect', ['source-during', 'stale', 'pin', 'raw', 'missing'])
def test_receipt_rejects_changed_or_unbound_evidence(validation, defect):
    root,proposal=validation
    if defect=='source-during':
        path=Path(proposal['tools']['controlled_builder']['path'])
        path.write_text(path.read_text()+"require('fs').appendFileSync('"+str(Path(TEST).parent/'Ad.tsx')+"','changed');")
        proposal['tools']['controlled_builder']['sha256']=digest(path)
    root,pin=freeze(validation)
    result=cli(root,'run','--profile','build-controlled');value=json.loads(result.stdout)
    if defect=='source-during':
        assert result.returncode==2 and value['status']=='unknown'
        return
    receipt=module().validate_receipt(root,value['receipt'],pin['sha256'])
    if defect=='stale':(root/TEST).write_text('changed')
    elif defect=='pin':Path(proposal['tools']['controlled_builder']['path']).write_text('changed')
    elif defect=='raw':(root/receipt['stdout']['path']).write_text('changed')
    else:(root/receipt['stdout']['path']).unlink()
    with pytest.raises((ValueError,OSError)):
        module().validate_receipt(root,value['receipt'],pin['sha256'])


def test_freeze_is_write_once_and_unknown_profile_never_runs(validation):
    root,pin=freeze(validation)
    assert cli(root,'run','--profile','arbitrary-python').returncode==2
    assert cli(root,'run','--profile','lint','--command','touch bad').returncode==2
    assert not (root/'bad').exists()
    with pytest.raises((ValueError,OSError)):
        module().freeze(root,root/'.omc/proposal-validation.json',
                        digest(root/'.omc/proposal-validation.json'))
