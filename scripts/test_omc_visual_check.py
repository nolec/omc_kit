"""Real rendered surfaces, then public collector decisions. No provider calls."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import omc_pipeline_guard as guard
from test_omc_workflow_validation import collect as legacy_collect, response
from test_omc_workflow_preflight import browser_service, manifest as preflight_manifest, run as preflight_run

SCRIPT = Path(__file__).with_name('omc_visual_check.py')
COLLECTOR = SCRIPT.with_name('omc_workflow_validation.py')
MODULE = os.environ.get('OMC_PREFLIGHT_PLAYWRIGHT_MODULE')
VISUAL_ID = 'browser-visual-controlled'
VARIANTS = ['card-large', 'card-medium', 'card-small', 'banner-small', 'banner-large', 'banner-landing']
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def required_candidate(tmp_path):
    text = response(first='필수 검증 통과·작업 완료', outcome='done').replace(
        '\n<!-- OMC_OUTPUT:', '\n다음 행동: 리뷰합니다.\n<!-- OMC_OUTPUT:')
    legacy_collect(tmp_path, text, expected='done')
    root = tmp_path/'work'
    basis = json.loads((root/'proposal.json').read_text())
    basis['revision'] = 2
    basis['checks'].append({'id': VISUAL_ID, 'target': 'controlled six', 'method': 'pixels',
        'pass_condition': 'visible image and border', 'target_version': 'candidate',
        'visual_manifest_sha256': '0'*64})
    set_basis(root, basis)
    return root, basis


def set_basis(root, basis):
    (root/'proposal.json').write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root, root/'proposal.json') == 0
    state = guard._load_state(root)
    state.update(required_checks=[i['id'] for i in basis['checks']], basis_sha256=guard._basis_digest(basis))
    guard._save_state(root, state)
    verification = root/'.omc/verification/s.json'
    value = json.loads(verification.read_text())
    value['basis_sha256'] = guard._basis_digest(basis)
    for item in basis['checks']:
        value['checks'][item['id']] = {'status':'passed', 'evidence':'fixture',
            'definition_sha256':guard._check_digest(item), 'code_snapshot_sha256':guard._code_snapshot_digest(root)}
    verification.write_text(json.dumps(value))


def collect(root, tmp_path, *extra):
    if '--visual-report' in extra:
        record_path = root/'.omc/verification/s.json'
        record = json.loads(record_path.read_text())
        item = record['checks'][VISUAL_ID]
        report = Path(extra[extra.index('--visual-report')+1])
        # Legacy single-report approvals now also expose evidence to complete-task.
        internal = root/'.omc/legacy-capture'
        import shutil
        shutil.copytree(report.parent, internal, dirs_exist_ok=True)
        item.update(report_file='.omc/legacy-capture/report.json',
                    report_sha256=extra[extra.index('--visual-report-sha256')+1])
        record_path.write_text(json.dumps(record))
    return subprocess.run([sys.executable, str(COLLECTOR), '--target', str(root),
        '--events-file', str(tmp_path/'events.jsonl'), '--session-id', 's',
        '--verification-file', str(root/'.omc/verification/s.json'), '--expected-outcome', 'done',
        '--output-dir', str(tmp_path/'visual-collected'), *extra], capture_output=True, text=True)


def test_visual_required_cannot_be_omitted(tmp_path):
    root, _ = required_candidate(tmp_path)
    result = collect(root, tmp_path)
    assert result.returncode == 1, result.stdout
    value = json.loads((tmp_path/'visual-collected/assessment.json').read_text())
    assert value['required']['visual_controlled'] == 'unknown'
    assert value['recompare_entry'] == 'hold'


def test_completion_replays_visual_instead_of_trusting_passed(tmp_path):
    root, _ = required_candidate(tmp_path)
    result = guard.evaluate_task_completion(root, root/'.omc/verification/s.json')
    assert result['outcome'] == 'blocked'
    assert any('visual' in p for p in result['problems'])


def test_ad_approval_requires_prepared_requirement(tmp_path):
    root, basis = required_candidate(tmp_path)
    basis.update(revision=3, task_profile='advertising-visual/v1')
    (root/'proposal.json').write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root, root/'proposal.json') == 1


@pytest.fixture
def site(tmp_path):
    root = tmp_path/'work'; root.mkdir(exist_ok=True)
    behavior = {'stale': False}
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)
        def do_GET(self):
            if self.path == '/bundle.js' and behavior['stale']:
                self.send_response(200); self.end_headers(); self.wfile.write(b'// stale bundle'); return
            super().do_GET()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield root, f'http://127.0.0.1:{server.server_port}', behavior
    server.shutdown(); server.server_close(); thread.join()


def build_site(tmp_path, site, mode='border', dpr=1):
    root, origin, behavior = site
    css = 'border:1px solid rgba(0,0,0,.1);' if mode != 'overlay' else ''
    if mode == 'inset': css = 'box-shadow:inset 0 0 0 1px rgba(0,0,0,.1);'
    if mode == 'thick': css = 'border:2px solid rgba(0,0,0,.1);'
    if mode == 'color': css = 'border:1px solid red;'
    if mode == 'partial': css += 'border-bottom:0;'
    marker = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect width="400" height="200" fill="white"/><rect x="160" y="60" width="80" height="80" fill="rgb(200,20,80)"/></svg>'
    js = "const variants="+json.dumps(VARIANTS)+";const svg="+json.dumps(marker)+";"
    js += "const sizes=[[340,200],[298,170],[340,160],[708,56],[708,80],[708,50]];variants.forEach((v,i)=>{const e=document.createElement('div');e.dataset.testid='ad-'+v;e.style.cssText=`position:absolute;left:${i<3?20+i*440:20}px;top:${i<3?20:300+(i-3)*120}px;width:${sizes[i][0]}px;height:${sizes[i][1]}px;box-sizing:border-box;overflow:hidden;background:white;"+css+"`;"
    js += "for(let j=0;j<2;j++){const img=document.createElement('img');img.src='data:image/svg+xml,'+encodeURIComponent(svg);img.style.cssText=`position:absolute;left:${j*100}%;top:0;width:100%;height:100%;`;e.append(img);}"
    if mode == 'overlay':
        js += "const line=document.createElement('span');line.style.cssText='position:absolute;inset:0;border:1px solid rgba(0,0,0,.1);box-sizing:border-box;pointer-events:none';e.append(line);"
    if mode == 'hidden': js += "e.querySelectorAll('img').forEach(i=>i.style.visibility='hidden');"
    if mode == 'removed': js += "e.querySelectorAll('img').forEach(i=>i.remove());"
    if mode == 'covered': js += "const cover=document.createElement('span');cover.style.cssText='position:absolute;inset:4px;background:white';e.append(cover);"
    if mode == 'loading': js += "e.querySelectorAll('img').forEach(i=>i.src='data:image/png;base64,broken');"
    if mode == 'unaligned': js += "e.style.transform='translateY(.25px)';"
    if mode == 'composited': js += "e.style.transform='translateY(1px)';"
    if mode == 'unstable': js += "e.animate([{transform:'translateX(0px)'},{transform:'translateX(5px)'}],{duration:400,iterations:Infinity});"
    js += 'document.body.append(e);});'
    (root/'app.src').write_text(js); (root/'bundle.js').write_text(js)
    (root/'index.html').write_text('<!doctype html><style>body{margin:0;background:#fafafa}</style><body><script src="/bundle.js"></script></body>')
    doc = {'schema_version':'omc-visual-manifest/v1', 'url':origin+'/index.html',
           'bundle_url':origin+'/bundle.js', 'bundle_path':'bundle.js', 'bundle_sha256':sha(root/'bundle.js'),
           'build_inputs':{'app.src':sha(root/'app.src'), 'index.html':sha(root/'index.html')},
           'dpr':dpr, 'images_per_variant':2}
    path = tmp_path/'visual-manifest.json'; path.write_text(json.dumps(doc))
    if mode == 'stale': behavior['stale'] = True
    return path


def capture(root, manifest, output):
    return subprocess.run([sys.executable, str(SCRIPT), '--target', str(root), '--manifest', str(manifest),
        '--manifest-sha256', sha(manifest), '--output-dir', str(output), '--playwright-module', MODULE],
        capture_output=True, text=True, timeout=40)


@pytest.mark.skipif(not MODULE, reason='explicit local Playwright module required')
@pytest.mark.parametrize('dpr', [1, 2])
@pytest.mark.parametrize('mode', ['border', 'overlay'])
def test_real_normal_24_combinations(tmp_path, site, mode, dpr):
    path = build_site(tmp_path, site, mode, dpr)
    result = capture(site[0], path, tmp_path/'capture')
    assert result.returncode == 0, result.stdout+result.stderr
    value = json.loads((tmp_path/'capture/report.json').read_text())
    assert value['status'] == 'passed'
    assert len(value['variants']) == 6
    assert all(v['status'] == 'passed' for v in value['variants'])


@pytest.mark.skipif(not MODULE, reason='explicit local Playwright module required')
@pytest.mark.parametrize('mode,expected', [('inset','failed'),('partial','failed'),('thick','failed'),
    ('color','failed'),('hidden','failed'),('removed','failed'),('covered','failed'),
    ('loading','unknown'),('unaligned','unknown'),('composited','unknown'),('unstable','unknown'),('stale','unknown')])
def test_real_defects_and_uncertainty(tmp_path, site, mode, expected):
    path = build_site(tmp_path, site, mode)
    result = capture(site[0], path, tmp_path/'capture')
    assert result.returncode != 0
    assert json.loads((tmp_path/'capture/report.json').read_text())['status'] == expected


@pytest.mark.skipif(not MODULE, reason='explicit local Playwright module required')
@pytest.mark.parametrize('defect', ['none','inset','snapshot','digest','manifest','pixels','result'])
def test_render_to_collector_surface_pipeline(tmp_path, site, defect):
    root, basis = required_candidate(tmp_path)
    path = build_site(tmp_path, site, 'inset' if defect == 'inset' else 'border')
    basis['revision'] = 3
    basis['checks'][-1]['visual_manifest_sha256'] = sha(path)
    set_basis(root, basis)
    capture(root, path, tmp_path/'capture')
    report = tmp_path/'capture/report.json'; pin = sha(report)
    if defect == 'snapshot': (root/'app.src').write_text('changed candidate')
    if defect == 'digest': pin = '0'*64
    if defect == 'pixels': (tmp_path/'capture/screen.png').write_bytes(b'changed evidence')
    if defect == 'result':
        value = json.loads(report.read_text()); value['status'] = 'failed'
        report.write_text(json.dumps(value)); pin = sha(report)
    if defect == 'manifest':
        basis['revision'] = 4; basis['checks'][-1]['visual_manifest_sha256'] = '0'*64; set_basis(root, basis)
    result = collect(root, tmp_path, '--visual-report',str(report),'--visual-report-sha256',pin)
    value = json.loads((tmp_path/'visual-collected/assessment.json').read_text())
    assert result.returncode == (0 if defect == 'none' else 1), result.stdout+result.stderr
    assert value['required']['visual_controlled'] == ('passed' if defect == 'none' else 'failed' if defect == 'inset' else 'unknown')


def test_existing_evidence_directory_is_never_modified(tmp_path):
    output = tmp_path/'existing'; output.mkdir(); marker = output/'screen.png'; marker.write_bytes(b'old')
    result = subprocess.run([sys.executable,str(SCRIPT),'--target',str(tmp_path),
        '--manifest',str(tmp_path/'absent.json'),'--manifest-sha256','0'*64,
        '--output-dir',str(output),'--playwright-module','unused'],capture_output=True,text=True)
    assert result.returncode == 2
    assert marker.read_bytes() == b'old'
    assert not (output/'report.json').exists()


def cli(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT.with_name('omc_pipeline_guard.py')),
                           '--target', str(root), *args], capture_output=True, text=True)


def profile_candidate(tmp_path, site, browser_service):
    """Public preparation/approval/contract/RED lifecycle; no pipeline-state edits."""
    import omc_visual_check as visual
    root = site[0]
    build_site(tmp_path, site)
    builder = root/'builder.py'
    builder.write_text("from pathlib import Path\nPath('bundle.js').write_bytes(Path('app.src').read_bytes())\n")
    preflight = preflight_manifest(root, tmp_path, browser_service, checks=['tests', VISUAL_ID])
    policy = {'schema_version':'omc-visual-policy/v1', 'criteria':visual.CRITERIA,
              'variants':VARIANTS, 'dprs':[1, 2], 'images_per_variant':2,
              'builder_inputs':{'builder.py':sha(builder)}}
    requirement = {'profile':visual.AD_PROFILE, 'run_id':'controlled-run-1', 'policy':policy}
    check_definitions = [dict(id=i, target='controlled ad fixture', method='real Chromium pixels' if i == VISUAL_ID else 'pytest',
        pass_condition='all required cells pass', target_version='current candidate',
        **({'visual_policy':policy} if i == VISUAL_ID else {})) for i in ['tests', VISUAL_ID]]
    definitions = tmp_path/'checks.json'; definitions.write_text(json.dumps(check_definitions))
    doc = json.loads(preflight.read_text())
    doc.update(task_profile=visual.AD_PROFILE, visual_requirement=requirement)
    doc['inputs'].update({'builder.py':sha(builder), '../checks.json':sha(definitions)})
    preflight.write_text(json.dumps(doc))
    ready = preflight_run(root, preflight, tmp_path/'ready.json', '--task-profile', visual.AD_PROFILE)
    assert ready.returncode == 0, ready.stdout+ready.stderr
    session = subprocess.run([sys.executable, str(SCRIPT.with_name('omc.py')), 'state', 'record',
        '--target', str(root), '--mode','autopilot','--title','visual regression',
        '--request','controlled visual task','--roles','senior_coding','--confirm'], capture_output=True, text=True)
    assert session.returncode == 0, session.stdout+session.stderr
    sid = json.loads((root/'.omc/state/latest.json').read_text())['latest_confirmed_session_id']
    basis = {'session_id':sid, 'revision':1, 'request':'controlled ad test',
             'approval_reference':'test operator fixture', 'checks':check_definitions,
             'task_profile':visual.AD_PROFILE, 'run_id':requirement['run_id']}
    proposal = root/'.omc/proposal.json'; proposal.write_text(json.dumps(basis))
    assert cli(root, 'approve-checks', '--file', str(proposal)).returncode == 0
    contract = cli(root, 'contract-done', '--required-checks', 'tests,'+VISUAL_ID)
    assert contract.returncode == 0, contract.stdout+contract.stderr
    # Actual synthetic RED command, registered through the public CLI.
    test = root/'test_surface.py'; test.write_text('def test_new_surface():\n    assert False\n')
    red = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(test)], capture_output=True, text=True)
    assert red.returncode == 1
    output = root/'.omc/red.txt'; output.write_text(red.stdout+red.stderr)
    evidence = dict(session_id=sid, basis_sha256=guard._basis_digest(basis),
        test_file=test.name, test_sha256=sha(test), check_id='tests', case_id='test_new_surface',
        expected_failure='synthetic lifecycle registration', command='python3 -m pytest -q test_surface.py',
        exit_code=1, baseline_failed_cases=[], failed_cases=['test_new_surface'],
        output_file='.omc/red.txt', output_sha256=sha(output))
    evidence_file = root/'.omc/red.json'; evidence_file.write_text(json.dumps(evidence))
    registered = cli(root, 'red-done', test.name, '--evidence-file', str(evidence_file))
    assert registered.returncode == 0, registered.stdout+registered.stderr
    test.write_text('def test_new_surface():\n    assert True\n')
    green = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(test)], capture_output=True, text=True)
    assert green.returncode == 0, green.stdout+green.stderr
    (root/'.omc/lifecycle-outputs.json').write_text(json.dumps([contract.stdout, registered.stdout]))
    return root, sid, basis, policy, proposal


@pytest.mark.skipif(not MODULE, reason='explicit local Playwright module required')
@pytest.mark.parametrize('defect,status', [('none','passed'), ('inset','failed'),
    ('missing','unknown'), ('duplicate','mismatch'), ('mixed-run','mismatch'),
    ('candidate','mismatch'), ('pixels','mismatch'), ('builder','mismatch'),
    ('missing-file','unknown'), ('failed-missing','failed'), ('mixed-candidate','mismatch'),
    ('failed-digest-first','failed'), ('failed-digest-last','failed'),
    ('failed-json-first','failed'), ('failed-json-last','failed'),
    ('failed-entry-first','failed'), ('failed-entry-last','failed'),
    ('failed-policy-first','failed'), ('failed-policy-last','failed'),
    ('failed-snapshot-first','failed'), ('failed-snapshot-last','failed'), ('reverse','passed')])
def test_public_profile_surface_completion_and_collection(tmp_path, site, browser_service, defect, status):
    import omc_visual_check as visual
    root, sid, basis, policy, proposal = profile_candidate(tmp_path, site, browser_service)
    approved_bytes = (root/'.omc/verification-bases'/sid/'1.json').read_bytes()
    # Modify candidate AFTER immutable approval, then run the frozen builder.
    source = root/'app.src'
    source.write_text(source.read_text().replace('border:1px solid rgba(0,0,0,.1);',
        'box-shadow:inset 0 0 0 1px rgba(0,0,0,.1);' if defect == 'inset' or defect.startswith('failed-')
        else 'border:1px solid rgba(0,0,0,.1);/* candidate changed */'))
    subprocess.run([sys.executable, 'builder.py'], cwd=root, check=True)
    # Public completion requires the current implementation to be staged.
    subprocess.run(['git','add','app.src','bundle.js','index.html','builder.py','test_surface.py'], cwd=root, check=True)
    policy_pin = visual.sha(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode())
    reports = []
    for dpr in [1, 2]:
        manifest = {'schema_version':'omc-visual-manifest/v1', 'url':site[1]+'/index.html',
            'bundle_url':site[1]+'/bundle.js', 'bundle_path':'bundle.js','bundle_sha256':sha(root/'bundle.js'),
            'build_inputs':{n:sha(root/n) for n in ['app.src','index.html','builder.py']},
            'dpr':dpr,'images_per_variant':2,'run_id':basis['run_id'],'policy_sha256':policy_pin}
        manifest_path = root/f'.omc/manifest-{dpr}.json'; manifest_path.write_text(json.dumps(manifest))
        directory = root/f'.omc/capture-{dpr}'
        captured = capture(root, manifest_path, directory)
        assert captured.returncode == (1 if defect == 'inset' or defect.startswith('failed-') else 0), captured.stdout+captured.stderr
        report = directory/'report.json'; reports.append({'path':str(report.relative_to(root)), 'sha256':sha(report)})
    template = cli(root, 'verification-template'); assert template.returncode == 0, template.stdout
    record = json.loads(template.stdout)
    record['implementation'] = {'status':'applied','evidence':'candidate built after approval'}
    for item in record['checks'].values():
        item.update(status='passed', evidence='caller claims passed; must replay pixels')
    record['visual_result_set'] = {'schema_version':'omc-visual-result-set/v1',
        'run_id':basis['run_id'], 'policy_sha256':policy_pin,
        'code_snapshot_sha256':guard._code_snapshot_digest(root), 'reports':reports}
    if defect in ('missing', 'failed-missing'): reports.pop()
    if defect == 'duplicate': reports[1] = reports[0]
    if defect == 'mixed-run': record['visual_result_set']['run_id'] = 'other-run'
    if defect == 'candidate': source.write_text('newer candidate')
    if defect == 'builder': (root/'builder.py').write_text('changed builder')
    if defect == 'pixels': (root/'.omc/capture-1/screen.png').write_bytes(b'changed pixels')
    if defect == 'missing-file': (root/'.omc/capture-2/report.json').unlink()
    if defect == 'mixed-candidate':
        path = root/reports[1]['path']; value = json.loads(path.read_text())
        value['code_snapshot_sha256'] = '0'*64
        path.write_text(json.dumps(value)); reports[1]['sha256'] = sha(path)
    if defect.startswith('failed-digest-'): reports[1]['sha256'] = '0'*64
    if defect.startswith('failed-json-'):
        path = root/reports[1]['path']; path.write_text('{invalid report JSON')
        reports[1]['sha256'] = sha(path)
    if defect.startswith('failed-entry-'): reports[1] = None
    if defect.startswith(('failed-policy-', 'failed-snapshot-')):
        path = root/reports[1]['path']; value = json.loads(path.read_text())
        manifest = json.loads(value['manifest_raw']); manifest['dpr'] = 1
        if defect.startswith('failed-policy-'): manifest['policy_sha256'] = '0'*64
        else: value['code_snapshot_sha256'] = '0'*64
        value['manifest_raw'] = json.dumps(manifest)
        value['manifest_sha256'] = hashlib.sha256(value['manifest_raw'].encode()).hexdigest()
        path.write_text(json.dumps(value)); reports[1]['sha256'] = sha(path)
    if defect.endswith('-first') or defect == 'reverse': reports.reverse()
    verification = root/f'.omc/verification/{sid}.json'; verification.parent.mkdir(exist_ok=True)
    verification.write_text(json.dumps(record))
    completed = cli(root, 'complete-task', '--verification-file', str(verification))
    assert completed.returncode == (0 if status == 'passed' else 1), completed.stdout+completed.stderr
    first = {'passed':'필수 검증 통과·작업 완료','failed':'검증 실패·수정 필요',
             'unknown':'구현됨·검증 미완료','mismatch':'증거 불일치·재검증 필요'}[status]
    assert completed.stdout.splitlines()[0] == first, completed.stdout
    # Collector reads real CLI output verbatim; no repaired final or state edits.
    events = []
    contract_output, red_output = json.loads((root/'.omc/lifecycle-outputs.json').read_text())
    for i, (command, text) in enumerate([
        ('python3 scripts/omc_pipeline_guard.py contract-done',contract_output),
        ('python3 scripts/omc_pipeline_guard.py red-done test_surface.py',red_output),
        ('python3 scripts/omc_pipeline_guard.py complete-task --verification-file '+str(verification), completed.stdout)]):
        events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Bash','input':{'command':command}}]}},
                   {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':text}]}}]
    events.append({'type':'result','subtype':'success','result':completed.stdout})
    event_path = root/'.omc/events.jsonl'; event_path.write_text('\n'.join(map(json.dumps,events)))
    subprocess.run([sys.executable,str(COLLECTOR),'--target',str(root),'--events-file',str(event_path),
        '--session-id',sid,'--verification-file',str(verification),'--expected-outcome','done' if status=='passed' else 'blocked',
        '--output-dir',str(root/'.omc/collected')],capture_output=True,text=True,check=False)
    result = json.loads((root/'.omc/collected/assessment.json').read_text())
    assert result['required']['visual_controlled'] == status
    assert result['recompare_entry'] == ('allow' if status=='passed' else 'hold'), result
    assert (root/'.omc/verification-bases'/sid/'1.json').read_bytes() == approved_bytes


@pytest.mark.skipif(not MODULE, reason='explicit local Playwright module required')
def test_prepared_ad_requirement_cannot_be_removed_or_changed(tmp_path, site, browser_service):
    root, sid, basis, policy, proposal = profile_candidate(tmp_path, site, browser_service)
    for defect in ['profile', 'id', 'policy']:
        changed = json.loads(json.dumps(basis)); changed['revision'] = 2
        if defect == 'profile': changed.pop('task_profile')
        if defect == 'id': changed['checks'].pop()
        if defect == 'policy': changed['checks'][-1]['visual_policy']['dprs'] = [1]
        proposal.write_text(json.dumps(changed))
        assert cli(root,'approve-checks','--file',str(proposal)).returncode == 1
    assert not (root/'.omc/verification-bases'/sid/'2.json').exists()
