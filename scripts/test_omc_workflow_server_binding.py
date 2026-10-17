"""A ready-looking browser must belong to the exact candidate server."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import omc_workflow_preflight as preflight
from test_omc_workflow_preflight import browser_service, manifest, run, start_bound_server, digest


def candidate(tmp_path, browser_service):
    root = tmp_path/'candidate'
    root.mkdir()
    path = manifest(root, tmp_path, browser_service)
    return root, path, json.loads(path.read_text())


def invoke(root, path, parent):
    marker = parent/'called'
    result = run(root, path, parent/'report.json', '--launch', sys.executable, '-c',
                 f'from pathlib import Path; Path({str(marker)!r}).touch()')
    return result, marker, json.loads((parent/'report.json').read_text())


@pytest.mark.parametrize('defect,reason', [
    ('missing', 'server_binding_required'),
    ('other-root', 'server_candidate_root_mismatch'),
    ('other-launcher', 'server_listener_not_owned'),
    ('reused-pid', 'server_launcher_changed'),
    ('wrong-bundle', 'server_bundle_mismatch'),
    ('not-loaded', 'server_bundle_not_loaded'),
    ('external-file', 'server_bundle_path_invalid'),
    ('redirect', 'server_bundle_path_invalid'),
    ('served-mismatch', 'server_bundle_mismatch'),
    ('external-link', 'server_bundle_path_invalid'),
])
def test_ready_browser_cannot_launch_wrong_candidate(tmp_path, browser_service, defect, reason):
    root, path, doc = candidate(tmp_path, browser_service)
    if defect == 'missing':
        del doc['server_binding']
    elif defect == 'other-root':
        other = tmp_path/'other'; other.mkdir()
        (other/'public/inspection').mkdir(parents=True)
        (other/'public/inspection/bundle.js').write_text('generated fixture')
        origin, proc, started = start_bound_server(other, browser_service[3])
        doc['browser']['origin'] = origin
        browser_service[1].update(url=origin+'/sixshoptest01',
                                   script_urls=[origin+'/public/inspection/bundle.js'])
        doc['server_binding'].update(launcher_pid=proc.pid, launcher_started_at=started)
    elif defect == 'other-launcher':
        # A new launcher can coexist with an older listener on the same port.
        _, proc, started = start_bound_server(root, browser_service[3])
        doc['server_binding'].update(launcher_pid=proc.pid, launcher_started_at=started)
    elif defect == 'reused-pid':
        doc['server_binding']['launcher_started_at'] = 'old process'
    elif defect == 'wrong-bundle':
        doc['server_binding']['bundles'][0]['sha256'] = '0'*64
    elif defect == 'not-loaded':
        browser_service[1]['script_urls'] = []
    elif defect == 'external-file':
        doc['server_binding']['bundles'][0]['file'] = '../checks.json'
    elif defect == 'redirect':
        doc['server_binding']['bundles'][0]['url_path'] = '//external.example/bundle.js'
    elif defect == 'external-link':
        external = tmp_path/'outside.js'; external.write_text('generated fixture')
        bundle = root/'public/inspection/linked.js'
        bundle.symlink_to(external)
        doc['server_binding']['bundles'][0]['file'] = 'public/inspection/linked.js'
    else:
        served = tmp_path/'served'; (served/'public/inspection').mkdir(parents=True)
        (served/'public/inspection/bundle.js').write_text('stale bundle from another candidate')
        origin, proc, started = start_bound_server(root, browser_service[3], directory=served)
        doc['browser']['origin'] = origin
        doc['server_binding'].update(launcher_pid=proc.pid, launcher_started_at=started)
        browser_service[1].update(url=origin+'/sixshoptest01',
                                  script_urls=[origin+'/public/inspection/bundle.js'])
    path.write_text(json.dumps(doc))
    result, marker, report = invoke(root, path, tmp_path)
    assert result.returncode == 2, result.stdout+result.stderr
    assert not marker.exists()
    assert report['status'] == 'blocked'
    assert report['provider_call_count'] == 0
    assert any(code.startswith(reason) for code in report['reason_codes']), report


def test_correct_server_launches_once_and_records_binding(tmp_path, browser_service):
    root, path, doc = candidate(tmp_path, browser_service)
    result, marker, report = invoke(root, path, tmp_path)
    assert result.returncode == 0, result.stdout+result.stderr
    assert marker.exists()
    evidence = report['server_binding']['evidence']
    assert evidence['cwd'] == str(root.resolve())
    assert evidence['listener_pid'] == doc['server_binding']['launcher_pid']
    assert evidence['bundles'][0]['sha256'] == digest(root/'public/inspection/bundle.js')


def test_binding_is_rechecked_after_browser_observation(tmp_path, browser_service):
    root, path, _ = candidate(tmp_path, browser_service)
    browser_service[2].append(lambda: (root/'public/inspection/bundle.js').write_text('changed'))
    result, marker, report = invoke(root, path, tmp_path)
    assert result.returncode == 2
    assert not marker.exists()
    assert 'server_bundle_mismatch' in report['reason_codes']


def test_collection_rejects_changed_server_bundle(tmp_path, browser_service):
    root, path, doc = candidate(tmp_path, browser_service)
    report_path = tmp_path/'report.json'
    assert run(root, path, report_path).returncode == 0
    (root/'public/inspection/bundle.js').write_text('legitimately rebuilt bundle')
    # Both disk and HTTP changed legitimately: current bytes must still agree.
    ready = preflight.assess_scope(root, report_path, digest(report_path), [], set(doc['required_check_ids']))
    assert ready['status'] == 'passed'
    browser_service[1]['script_urls'] = []
    blocked = preflight.assess_scope(root, report_path, digest(report_path), [], set(doc['required_check_ids']))
    assert blocked['status'] != 'passed'
    assert blocked['reason'].startswith('server_bundle_not_loaded')


def test_unavailable_identity_probe_is_fail_closed(tmp_path, monkeypatch):
    def unavailable(*_a, **_kw):
        raise FileNotFoundError('lsof')
    monkeypatch.setattr(subprocess, 'run', unavailable)
    with pytest.raises(ValueError, match='server_identity_unavailable'):
        preflight.process_identity(123)


def test_launcher_ancestor_is_accepted(tmp_path, browser_service):
    root, path, doc = candidate(tmp_path, browser_service)
    parent = preflight.process_identity(os.getpid())
    doc['server_binding'].update(launcher_pid=parent['pid'], launcher_started_at=parent['started_at'])
    path.write_text(json.dumps(doc))
    result, marker, _ = invoke(root, path, tmp_path)
    assert result.returncode == 0, result.stdout+result.stderr
    assert marker.exists()


def test_collection_rejects_missing_listener(tmp_path, browser_service):
    root, path, doc = candidate(tmp_path, browser_service)
    report_path = tmp_path/'report.json'
    assert run(root, path, report_path).returncode == 0
    browser_service[3][0].terminate(); browser_service[3][0].wait(timeout=10)
    result = preflight.assess_scope(root, report_path, digest(report_path), [], set(doc['required_check_ids']))
    assert result['status'] == 'unknown'
    assert result['reason'] == 'server_identity_unavailable'


@pytest.mark.parametrize('revisions,expected', [(['old'], 2), (['current'], 0), (['current', 'old'], 2)])
def test_actual_script_query_response_is_verified(tmp_path, browser_service, revisions, expected):
    root, path, doc = candidate(tmp_path, browser_service)
    code = '''from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  body=b'old competing bundle' if 'revision=old' in self.path else Path('public/inspection/bundle.js').read_bytes()
  self.send_response(200);self.end_headers();self.wfile.write(body)
 def log_message(self,*args): pass
server=HTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True);server.serve_forever()
'''
    proc = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root,
                            stdout=subprocess.PIPE, text=True)
    browser_service[3].append(proc)
    origin = f'http://127.0.0.1:{int(proc.stdout.readline())}'
    doc['browser']['origin'] = origin
    doc['server_binding'].update(launcher_pid=proc.pid,
                                 launcher_started_at=preflight.process_identity(proc.pid)['started_at'])
    browser_service[1].update(url=origin+'/sixshoptest01', script_urls=[
        origin+'/public/inspection/bundle.js?revision='+r for r in revisions])
    path.write_text(json.dumps(doc))
    result, marker, report = invoke(root, path, tmp_path)
    assert result.returncode == expected, result.stdout+result.stderr
    assert marker.exists() is (expected == 0)
    if expected:
        assert 'server_bundle_mismatch' in report['reason_codes']


@pytest.mark.parametrize('problem', ['stopped', 'browser-not-ready'])
@pytest.mark.parametrize('violation', ['file', 'confirmed-write', 'unresolved-write', 'none'])
def test_collection_preserves_scope_and_binding_outcomes(tmp_path, browser_service, problem, violation):
    root, path, doc = candidate(tmp_path, browser_service)
    report_path = tmp_path/'report.json'
    assert run(root, path, report_path).returncode == 0
    outside = root/'outside-scope.py'
    if violation == 'file':
        outside.write_text('outside approved scope')
    if problem == 'stopped':
        browser_service[3][0].terminate(); browser_service[3][0].wait(timeout=10)
    else:
        browser_service[1]['images_loaded'] = 0
    result = preflight.assess_scope(root, report_path, digest(report_path),
        [str(outside)] if violation == 'confirmed-write' else [], set(doc['required_check_ids']),
        [str(outside)] if violation == 'unresolved-write' else [])
    confirmed = violation in {'file', 'confirmed-write'}
    assert result['status'] == ('failed' if confirmed else 'unknown')
    assert result['violations'] == (['outside-scope.py'] if confirmed else [])
    assert result['unresolved_writes'] == (['outside-scope.py'] if violation == 'unresolved-write' else [])
    assert result['server_binding']['status'] == 'unknown'
    assert result['server_binding']['reason'] == ('server_identity_unavailable' if problem == 'stopped' else 'browser_not_ready')
    if confirmed:
        assert result['reason'] == 'out_of_scope_change'


def test_collection_observation_cannot_hide_new_scope_change(tmp_path, browser_service):
    root, path, doc = candidate(tmp_path, browser_service)
    report_path = tmp_path/'report.json'
    assert run(root, path, report_path).returncode == 0
    browser_service[2].append(lambda: (root/'outside-scope.py').write_text('changed during observation'))
    result = preflight.assess_scope(root, report_path, digest(report_path), [], set(doc['required_check_ids']))
    assert result['status'] == 'failed'
    assert result['violations'] == ['outside-scope.py']
