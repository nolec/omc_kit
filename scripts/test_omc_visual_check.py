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
