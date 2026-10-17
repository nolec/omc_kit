"""Public entry-point tests: preparation must gate even a stand-in executor."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import omc_pipeline_guard as guard
from test_omc_workflow_validation import collect as legacy_collect, response

SCRIPT = Path(__file__).with_name('omc_workflow_preflight.py')
COLLECTOR = SCRIPT.with_name('omc_workflow_validation.py')


def start_bound_server(root, processes, *, directory=None, port=0):
    if not shutil.which('lsof') or not shutil.which('ps'):
        pytest.skip('server identity integration requires lsof and ps')
    code = '''import functools,sys
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
handler=functools.partial(SimpleHTTPRequestHandler,directory=sys.argv[1])
server=ThreadingHTTPServer(('127.0.0.1',int(sys.argv[2])),handler)
print(server.server_port,flush=True)
server.serve_forever()
'''
    proc = subprocess.Popen([sys.executable, '-u', '-c', code, str(directory or root), str(port)],
                            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    processes.append(proc)
    actual_port = int(proc.stdout.readline())
    started = subprocess.check_output(['ps', '-p', str(proc.pid), '-o', 'lstart='], text=True).strip()
    return f'http://127.0.0.1:{actual_port}', proc, ' '.join(started.split())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def browser_service():
    payload = {'url': 'http://localhost:3023/sixshoptest01', 'images_loaded': 2,
               'auto_transition': True}
    payload_hook = []
    processes = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert request['op'] == 'evaluate'
            for hook in payload_hook:
                hook()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/command', payload, payload_hook, processes
    server.shutdown()
    server.server_close()
    thread.join()
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)
        proc.stdout.close()


def manifest(root, parent, browser_service, *, checks=None):
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    artifact = root/'public/inspection/bundle.js'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('generated fixture')
    exclude = root/'.git/info/exclude'
    exclude.write_text('/public/inspection/\n')
    source = root/'src/input.txt'
    source.parent.mkdir(exist_ok=True)
    source.write_text('frozen task input')
    checks = checks or ['tests', 'lint', 'browser-controlled', 'browser-home']
    definitions = parent/'checks.json'
    definitions.write_text(json.dumps([{'id': k} for k in checks]))
    origin, proc, started = start_bound_server(root, browser_service[3])
    bundle_path = '/public/inspection/bundle.js'
    browser_service[1].update(url=origin+'/sixshoptest01', script_urls=[origin+bundle_path])
    value = {'schema_version': 'omc-workflow-preflight/v1',
             'inputs': {'src/input.txt': digest(source), '../checks.json': digest(definitions)},
             'required_checks_file': '../checks.json', 'required_check_ids': checks,
             'ignored_artifacts': [{'path': 'public/inspection/bundle.js',
                                    'source': '.git/info/exclude', 'pattern': '/public/inspection/'}],
             'work_paths': ['src/'],
             'browser': {'control_url': browser_service[0], 'origin': origin,
                         'home_path': '/sixshoptest01', 'min_images': 2},
             'server_binding': {'launcher_pid': proc.pid, 'launcher_started_at': started,
                                'bundles': [{'file': 'public/inspection/bundle.js',
                                             'url_path': bundle_path, 'sha256': digest(artifact)}]}}
    path = parent/'manifest.json'
    path.write_text(json.dumps(value))
    return path


def run(root, path, report, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), '--target', str(root),
        '--manifest', str(path), '--manifest-sha256', digest(path), '--report', str(report),
        *extra], capture_output=True, text=True)


@pytest.mark.parametrize('defect', ['missing-ignore', 'replacement-ignore', 'input-change',
                                  'missing-images', 'no-transition', 'missing-check'])
def test_invalid_preparation_never_invokes_executor(tmp_path, browser_service, defect):
    root = tmp_path/'candidate'; root.mkdir()
    path = manifest(root, tmp_path, browser_service)
    if defect == 'missing-ignore': (root/'.git/info/exclude').write_text('')
    elif defect == 'replacement-ignore': (root/'public/.gitignore').write_text('inspection/\n')
    elif defect == 'input-change': (root/'src/input.txt').write_text('changed')
    elif defect == 'missing-images': browser_service[1]['images_loaded'] = 0
    elif defect == 'no-transition': browser_service[1]['auto_transition'] = False
    else:
        checks = tmp_path/'checks.json'; checks.write_text('[{"id":"tests"}]')
        doc = json.loads(path.read_text()); doc['inputs']['../checks.json'] = digest(checks)
        path.write_text(json.dumps(doc))
    marker = tmp_path/'called'
    result = run(root, path, tmp_path/'report.json', '--launch', sys.executable, '-c',
                 f'from pathlib import Path; Path({str(marker)!r}).touch()')
    assert result.returncode != 0
    assert not marker.exists()
    report = json.loads((tmp_path/'report.json').read_text())
    assert report['status'] == 'blocked'
    assert report['provider_call_count'] == 0
    expected = {'missing-ignore': 'artifact_not_ignored:', 'replacement-ignore': 'ignore_rule_mismatch:',
                'input-change': 'input_mismatch:', 'missing-images': 'browser_not_ready',
                'no-transition': 'browser_not_ready', 'missing-check': 'required_checks_mismatch'}
    assert any(reason.startswith(expected[defect]) for reason in report['reason_codes'])


def test_ready_public_entrypoint_freezes_scope_and_launches_only_explicit_executor(tmp_path, browser_service):
    root = tmp_path/'candidate'; root.mkdir()
    path = manifest(root, tmp_path, browser_service)
    marker = tmp_path/'called'
    result = run(root, path, tmp_path/'report.json', '--launch', sys.executable, '-c',
                 f'from pathlib import Path; Path({str(marker)!r}).touch()')
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
    report = json.loads((tmp_path/'report.json').read_text())
    assert report['status'] == 'ready'
    assert report['work_paths'] == ['src/']
    assert report['manifest_sha256'] == digest(path)
    assert 'src/input.txt' in report['baseline']
    assert not (root/'public/.gitignore').exists()


@pytest.mark.parametrize('defect', ['removed-rule', 'replacement-rule', 'removed-artifact'])
def test_browser_wait_changes_block_executor(tmp_path, browser_service, defect):
    root = tmp_path/'candidate'; root.mkdir()
    path = manifest(root, tmp_path, browser_service)
    def change():
        if defect == 'removed-artifact':
            (root/'public/inspection/bundle.js').unlink()
        else:
            (root/'.git/info/exclude').write_text('')
            if defect == 'replacement-rule':
                (root/'public/.gitignore').write_text('inspection/\n')
    browser_service[2].append(change)
    marker = tmp_path/'called'
    result = run(root, path, tmp_path/'report.json', '--launch', sys.executable, '-c',
                 f'from pathlib import Path; Path({str(marker)!r}).touch()')
    assert result.returncode == 2
    assert not marker.exists()
    report = json.loads((tmp_path/'report.json').read_text())
    expected = {'removed-rule': 'artifact_not_ignored:',
                'replacement-rule': 'ignore_rule_mismatch:', 'removed-artifact': 'artifact_missing:'}
    assert any(r.startswith(expected[defect]) for r in report['reason_codes'])


# Explicit integration dependency: run with OMC_PREFLIGHT_PLAYWRIGHT_MODULE pointing
# at an installed Playwright module. No browser downloads or provider calls.
@pytest.mark.skipif(not os.environ.get('OMC_PREFLIGHT_PLAYWRIGHT_MODULE'),
                    reason='requires explicit local Playwright module')
@pytest.mark.parametrize('moving_card', [False, True])
def test_real_browser_requires_card_transition(tmp_path, browser_service, moving_card):
    from omc_workflow_preflight import BROWSER_QUERY
    script = r'''
const {chromium}=require(process.env.OMC_PREFLIGHT_PLAYWRIGHT_MODULE);
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  await page.route('http://localhost:3023/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html>'}));
  await page.goto('http://localhost:3023/sixshoptest01');
  await page.evaluate(moving=>{
   const img='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>');
   document.body.innerHTML=`<div data-testid="ad-card-large"><div id="card" data-testid="ad-track-card-large" style="transform:translateX(0px)"><img src="${img}"><img src="${img}"></div></div><div data-testid="ad-banner-small"><div id="banner" data-testid="ad-track-banner-small" style="transform:translateX(0px)"></div></div>`;
   let i=0;setInterval(()=>{i++;document.getElementById('banner').style.transform=`translateX(${i%2*100}px)`;if(moving)document.getElementById('card').style.transform=`translateX(${i%2*100}px)`;},300);
  },process.argv[1]==='true');
  await page.waitForFunction(()=>[...document.images].every(i=>i.complete&&i.naturalWidth>0));
  console.log(JSON.stringify(await page.evaluate(process.argv[2])));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
'''
    observed = subprocess.run(['node', '-e', script, json.dumps(moving_card), BROWSER_QUERY],
                              capture_output=True, text=True, timeout=30, check=True)
    browser_service[1].update(json.loads(observed.stdout))
    root = tmp_path/'candidate'; root.mkdir()
    path = manifest(root, tmp_path, browser_service)
    marker = tmp_path/'called'
    result = run(root, path, tmp_path/'report.json', '--launch', sys.executable, '-c',
                 f'from pathlib import Path; Path({str(marker)!r}).touch()')
    assert result.returncode == (0 if moving_card else 2)
    assert marker.exists() is moving_card


def test_report_is_write_once_and_never_launches_on_manifest_digest_mismatch(tmp_path, browser_service):
    root = tmp_path/'candidate'; root.mkdir()
    path = manifest(root, tmp_path, browser_service)
    report = tmp_path/'report.json'
    assert run(root, path, report).returncode == 0
    original = report.read_bytes()
    assert run(root, path, report).returncode != 0
    assert report.read_bytes() == original
    result = subprocess.run([sys.executable, str(SCRIPT), '--target', str(root), '--manifest',
        str(path), '--manifest-sha256', '0'*64, '--report', str(tmp_path/'wrong.json')],
        capture_output=True, text=True)
    assert result.returncode != 0


def strict_candidate(tmp_path, browser_service):
    raw = response(first='필수 검증 통과·작업 완료', outcome='done').replace(
        '\n<!-- OMC_OUTPUT:', '\n다음 행동: 변경사항을 리뷰합니다.\n<!-- OMC_OUTPUT:')
    _, dest = legacy_collect(tmp_path, raw, expected='done')
    root = tmp_path/'work'
    path = manifest(root, tmp_path, browser_service, checks=['browser-home'])
    state = guard._load_state(root); state['worktree_baseline'] = {}; guard._save_state(root, state)
    report = root/'.omc/preflight.json'
    assert run(root, path, report).returncode == 0
    return root, report, tmp_path/'events.jsonl', raw


def strict_collect(root, report, events, dest):
    verification = root/'.omc/verification/s.json'
    value = json.loads(verification.read_text())
    for check in value['checks'].values(): check['code_snapshot_sha256'] = guard._code_snapshot_digest(root)
    verification.write_text(json.dumps(value))
    result = subprocess.run([sys.executable, str(COLLECTOR), '--target', str(root),
        '--events-file', str(events), '--session-id', 's', '--verification-file', str(verification),
        '--expected-outcome', 'done', '--output-dir', str(dest), '--scope-report', str(report),
        '--scope-report-sha256', digest(report)], capture_output=True, text=True)
    return result, json.loads((dest/'assessment.json').read_text())


@pytest.mark.parametrize('defect', ['new-file', 'index-only', 'direct-write-restored'])
def test_strict_collection_holds_out_of_scope_changes(tmp_path, browser_service, defect):
    root, report, events, raw = strict_candidate(tmp_path, browser_service)
    if defect == 'new-file':
        (root/'public/.gitignore').write_text('inspection/\n')
    elif defect == 'index-only':
        p = root/'proposal.json'; original = p.read_bytes(); p.write_text('index-only')
        subprocess.run(['git', 'add', 'proposal.json'], cwd=root, check=True); p.write_bytes(original)
    else:
        rows = [json.loads(s) for s in events.read_text().splitlines()]
        rows.insert(0, {'type':'assistant','message':{'content':[{'type':'tool_use','id':'outside',
            'name':'Write','input':{'file_path':str(root/'public/.gitignore'),'content':'inspection/'}}]}})
        rows.insert(1, {'type':'user','message':{'content':[{'type':'tool_result',
            'tool_use_id':'outside','content':'File written','is_error':False}]}})
        events.write_text('\n'.join(json.dumps(x) for x in rows))
    result, assessment = strict_collect(root, report, events, tmp_path/'strict')
    assert result.returncode == 1
    assert assessment['required']['work_scope'] == 'failed'
    assert assessment['recompare_entry'] == 'hold'
    assert (tmp_path/'strict/final.txt').read_text() == raw


@pytest.mark.parametrize('defect', ['first-line', 'duplicate-next'])
def test_strict_collection_rejects_format_without_repairing_raw(tmp_path, browser_service, defect):
    root, report, events, raw = strict_candidate(tmp_path, browser_service)
    raw = '---\n'+raw if defect == 'first-line' else raw.replace('다음 행동:', '다음 행동: 검토\n다음 행동:')
    rows = [json.loads(s) for s in events.read_text().splitlines()]; rows[-1]['result'] = raw
    events.write_text('\n'.join(json.dumps(x) for x in rows))
    result, assessment = strict_collect(root, report, events, tmp_path/'strict')
    assert result.returncode == 1
    assert assessment['required']['report_format'] == 'failed'
    assert (tmp_path/'strict/final.txt').read_text() == raw


def test_strict_collection_accepts_in_scope_change_with_valid_format(tmp_path, browser_service):
    root, report, events, _ = strict_candidate(tmp_path, browser_service)
    (root/'src/input.txt').write_text('permitted change')
    result, assessment = strict_collect(root, report, events, tmp_path/'strict')
    assert result.returncode == 0, result.stdout + result.stderr
    assert assessment['required']['work_scope'] == 'passed'
    assert assessment['required']['report_format'] == 'passed'


@pytest.mark.parametrize('tool_state', ['rejected', 'unresolved'])
def test_scope_does_not_invent_a_successful_write(tmp_path, browser_service, tool_state):
    root, report, events, _ = strict_candidate(tmp_path, browser_service)
    rows = [json.loads(s) for s in events.read_text().splitlines()]
    rows.insert(0, {'type':'assistant','message':{'content':[{'type':'tool_use','id':'denied',
        'name':'Write','input':{'file_path':str(root/'public/.gitignore'),'content':'inspection/'}}]}})
    if tool_state == 'rejected':
        rows.insert(1, {'type':'user','message':{'content':[{'type':'tool_result',
            'tool_use_id':'denied','content':'Denied','is_error':True}]}})
    events.write_text('\n'.join(json.dumps(x) for x in rows))
    result, assessment = strict_collect(root, report, events, tmp_path/'strict')
    assert assessment['required']['work_scope'] == ('passed' if tool_state == 'rejected' else 'unknown')
    assert result.returncode == (0 if tool_state == 'rejected' else 1)


@pytest.mark.parametrize('defect', ['digest', 'target', 'checks'])
def test_scope_report_is_bound_to_operator_pin_and_target(tmp_path, browser_service, defect):
    root, report, events, _ = strict_candidate(tmp_path, browser_service)
    value = json.loads(report.read_text())
    if defect == 'target': value['target'] = str(tmp_path/'other')
    elif defect == 'checks': value['required_check_ids'] = ['other']
    report.write_text(json.dumps(value))
    # target/check defects are pinned as bytes, so the semantic binding must also reject them.
    result, assessment = strict_collect(root, report, events, tmp_path/'strict')
    if defect == 'digest':
        assert result.returncode == 0
        verification = root/'.omc/verification/s.json'
        result = subprocess.run([sys.executable, str(COLLECTOR), '--target', str(root),
            '--events-file', str(events), '--session-id', 's', '--verification-file', str(verification),
            '--expected-outcome', 'done', '--output-dir', str(tmp_path/'wrong-pin'),
            '--scope-report', str(report), '--scope-report-sha256', '0'*64], capture_output=True, text=True)
        assessment = json.loads((tmp_path/'wrong-pin/assessment.json').read_text())
    assert result.returncode == 1
    assert assessment['required']['work_scope'] == 'unknown'
