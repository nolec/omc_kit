import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest

CLI=Path(__file__).with_name('preflight.py')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def fixture(tmp_path, flags=None):
    root=tmp_path/'retained';root.mkdir()
    for name in ['package.json','index.js','chromium']:(root/name).write_text('retained fixture '+name)
    probe=root/'probe.py';probe.write_text('import json\nprint('+repr(json.dumps(flags or dict(modules_loaded=True,esbuild_processed=True,browser_started=True,browser_closed=True)))+')\n')
    counter=root/'whole-counter'
    command=root/'canary.py';command.write_text('from pathlib import Path\nPath('+repr(str(counter))+').write_text("one model-free canary")\n')
    m=dict(schema_version='omc-retained-dependencies/v1',root=str(root),files={p.name:sha(p) for p in root.iterdir()},runtime_executable=str(Path(sys.executable).resolve()),runtime_sha256=sha(Path(sys.executable).resolve()),probe_argv=[str(Path(sys.executable).resolve()),str(probe)],probe_timeout_seconds=3,continuation=[str(Path(sys.executable).resolve()),str(command)])
    path=tmp_path/'manifest.json';path.write_text(json.dumps(m));return root,path,counter

def run(tmp_path,path,continuation=True):
    assert CLI.is_file(), 'mandatory dependency preflight is absent'
    argv=[sys.executable,str(CLI),'--manifest',str(path),'--manifest-sha256',sha(path),'--record',str(tmp_path/'evidence.json')]
    if continuation:argv+=['--allow-continuation']
    r=subprocess.run(argv,capture_output=True,text=True,timeout=20)
    return r,json.loads((tmp_path/'evidence.json').read_text())

@pytest.mark.parametrize('missing',['package.json','index.js','chromium'])
def test_missing_file_blocks_entire_quality_invocation(tmp_path,missing):
    root,path,counter=fixture(tmp_path);(root/missing).unlink()
    r,v=run(tmp_path,path)
    assert r.returncode==1 and v['status']=='blocked' and v['continuation_calls']==0
    assert not counter.exists()

def test_changed_hash_blocks_quality(tmp_path):
    root,path,counter=fixture(tmp_path);(root/'index.js').write_text('changed')
    r,v=run(tmp_path,path);assert r.returncode==1 and v['continuation_calls']==0 and not counter.exists()

@pytest.mark.parametrize('failed',['modules_loaded','esbuild_processed','browser_started','browser_closed'])
def test_failed_runtime_phase_blocks_quality(tmp_path,failed):
    flags=dict(modules_loaded=True,esbuild_processed=True,browser_started=True,browser_closed=True);flags[failed]=False
    root,path,counter=fixture(tmp_path,flags);r,v=run(tmp_path,path)
    assert r.returncode==1 and v['continuation_calls']==0 and not counter.exists()

def test_success_uses_same_guard_path_and_calls_canary_once(tmp_path):
    root,path,counter=fixture(tmp_path);r,v=run(tmp_path,path)
    assert r.returncode==0 and v['status']=='prepared' and v['continuation_calls']==1
    assert counter.read_text()=='one model-free canary'

def test_prepare_only_never_invokes_continuation(tmp_path):
    root,path,counter=fixture(tmp_path);r,v=run(tmp_path,path,False)
    assert r.returncode==0 and v['continuation_calls']==0 and not counter.exists()

def test_external_file_link_is_rejected(tmp_path):
    root,path,counter=fixture(tmp_path);p=root/'index.js';p.unlink();outside=tmp_path/'outside';outside.write_text('outside');p.symlink_to(outside)
    r,v=run(tmp_path,path);assert r.returncode==1 and v['continuation_calls']==0 and not counter.exists()

def test_post_probe_mutation_is_rejected(tmp_path):
    root,path,counter=fixture(tmp_path);m=json.loads(path.read_text());p=root/'probe.py';p.write_text('from pathlib import Path\nPath('+repr(str(root/'index.js'))+').write_text("late mutation")\n'+p.read_text());m['files']['probe.py']=sha(p);path.write_text(json.dumps(m))
    r,v=run(tmp_path,path);assert r.returncode==1 and v['continuation_calls']==0 and not counter.exists()


def owned_fixture(tmp_path, mode):
    root,path,counter=fixture(tmp_path)
    child=root/'browser.py';child.write_text("import signal,time,os\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nwhile True:time.sleep(.1)\n")
    probe=root/'probe.py'
    probe.write_text('''import os,sys,json,signal,subprocess,time
from pathlib import Path
root=Path(__file__).parent
mode='''+repr(mode)+'''
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child=None
if mode!='before-ownership':
 child=subprocess.Popen([sys.executable,'-u',str(root/'browser.py')],start_new_session=True)
 (root/'child-pid.json').write_text(json.dumps({'pid':child.pid,'pgid':os.getpgid(child.pid)}))
 fd=os.environ.get('OMC_PREFLIGHT_OWNERSHIP_FD')
 if fd:
  event={'kind':'chromium_spawn','token':os.environ['OMC_PREFLIGHT_OWNERSHIP_TOKEN'],'owner_pid':os.getpid(),'pid':child.pid,'pgid':os.getpgid(child.pid)}
  os.write(int(fd),(json.dumps(event)+'\\n').encode())
if mode=='normal':
 time.sleep(.3)
 os.killpg(child.pid,signal.SIGKILL);child.wait()
 print(json.dumps(dict(modules_loaded=True,esbuild_processed=True,browser_started=True,browser_closed=True)),flush=True)
else:
 os.write(1,b'RAW-STDOUT-\\xff\\n');os.write(2,b'RAW-STDERR-\\xfe\\n')
 while True:time.sleep(.1)
''')
    m=json.loads(path.read_text());m['files']={p.name:sha(p) for p in root.iterdir() if p.is_file()};m['probe_timeout_seconds']=3 if mode=='normal' else .4;m['require_browser_ownership']=mode!='before-ownership';path.write_text(json.dumps(m))
    return root,path,counter


def stop_fixture_child(root):
    import os,signal
    p=root/'child-pid.json'
    if p.exists():
        try:os.killpg(json.loads(p.read_text())['pgid'],signal.SIGKILL)
        except ProcessLookupError:pass


def test_timeout_kills_owned_detached_group_and_preserves_raw(tmp_path):
    root,path,counter=owned_fixture(tmp_path,'hang')
    try:
        r,v=run(tmp_path,path)
        assert r.returncode==1 and v['continuation_calls']==0 and not counter.exists()
        probe=v['probe'];assert probe['timed_out'] is True and probe['cleanup_confirmed'] is True
        assert len(probe['owned_browsers'])==1
        owner=probe['owned_browsers'][0];assert owner['pid']==owner['pgid'] and owner['verified'] is True
        assert owner['termination_confirmed'] is True
        assert any(s['signal']=='SIGKILL' and s['scope']=='chromium' for s in probe['signals'])
        assert b'RAW-STDOUT-\xff\n' in Path(probe['stdout_path']).read_bytes()
        assert b'RAW-STDERR-\xfe\n' in Path(probe['stderr_path']).read_bytes()
        assert sha(Path(probe['stdout_path']))==probe['stdout_sha256']
        assert sha(Path(probe['stderr_path']))==probe['stderr_sha256']
    finally:stop_fixture_child(root)


def test_timeout_before_browser_preserves_partial_output_and_blocks(tmp_path):
    root,path,counter=owned_fixture(tmp_path,'before-ownership');r,v=run(tmp_path,path)
    assert r.returncode==1 and v['continuation_calls']==0 and not counter.exists()
    assert v['probe']['timed_out'] is True
    assert v['probe']['owned_browsers']==[] and v['probe']['cleanup_confirmed'] is True
    assert b'RAW-STDOUT-\xff\n' in Path(v['probe']['stdout_path']).read_bytes()


def test_normal_owned_browser_identity_is_recorded(tmp_path):
    root,path,counter=owned_fixture(tmp_path,'normal')
    try:
        r,v=run(tmp_path,path)
        assert r.returncode==0 and counter.exists()
        assert v['probe']['cleanup_confirmed'] is True
        assert v['probe']['owned_browsers'][0]['termination_confirmed'] is True
    finally:stop_fixture_child(root)


def test_runtime_spawn_waits_for_ownership_before_parent_exit(tmp_path,monkeypatch):
    import os,shutil
    root,path,counter=fixture(tmp_path)
    (root/'package.json').write_text('{}')
    browsers=root/'browsers';browsers.mkdir()
    child=browsers/'fake-chromium'
    child.write_text('#!'+sys.executable+'\nimport signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nwhile True:time.sleep(.1)\n');child.chmod(0o755)
    pw=root/'playwright';pw.mkdir();es=root/'esbuild';es.mkdir()
    for module in [pw,es]:(module/'package.json').write_text('{"version":"fixture"}')
    (es/'index.js').write_text('exports.transformSync=()=>({code:"const answer = 42;"});')
    (pw/'index.js').write_text('const cp=require("child_process"),fs=require("fs"),path=require("path");exports.chromium={launch:async()=>{const child=cp.spawn(path.join(__dirname,"../browsers/fake-chromium"),[],{detached:true,stdio:"ignore"});fs.writeFileSync(path.join(__dirname,"../child-pid.json"),JSON.stringify({pid:child.pid,pgid:child.pid}));process.exit(0);}};')
    probe=root/'runtime-probe.cjs';shutil.copyfile(CLI.with_name('runtime-probe.cjs'),probe)
    wrappers=root/'wrappers';wrappers.mkdir();ps=wrappers/'ps';ps.write_text('#!/bin/sh\nsleep 0.15\nexec /bin/ps "$@"\n');ps.chmod(0o755)
    monkeypatch.setenv('PATH',str(wrappers)+os.pathsep+os.environ['PATH'])
    node=Path(shutil.which('node')).resolve();m=json.loads(path.read_text());m.update(runtime_executable=str(node),runtime_sha256=sha(node),probe_argv=[str(node),str(probe),str(path)],probe_timeout_seconds=3,require_browser_ownership=True,modules={'playwright':str(pw),'esbuild':str(es)},versions={'playwright':'fixture','esbuild':'fixture'},environment={'PLAYWRIGHT_BROWSERS_PATH':str(browsers)})
    m['files']={str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()};path.write_text(json.dumps(m))
    try:
        r,v=run(tmp_path,path)
        assert r.returncode==1 and v['continuation_calls']==0 and not counter.exists()
        assert len(v['probe']['owned_browsers'])==1
        assert v['probe']['owned_browsers'][0]['termination_confirmed'] is True
        assert v['probe']['cleanup_confirmed'] is True
        assert v['probe']['cleanup_forced'] is True
    finally:stop_fixture_child(root)


def test_continuation_timeout_zero_exit_is_blocked(tmp_path,monkeypatch):
    import importlib.util
    root,path,counter=fixture(tmp_path)
    command=root/'canary.py';command.write_text('import signal,time,sys\nsignal.signal(signal.SIGTERM,lambda *_:sys.exit(0))\nwhile True:time.sleep(.01)\n')
    m=json.loads(path.read_text());m['files']['canary.py']=sha(command);path.write_text(json.dumps(m))
    spec=importlib.util.spec_from_file_location('preflight_timeout_test',CLI);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=module.process
    def bounded(argv,*args,**kwargs):
        if str(command) in argv:args=(args[0],.2,*args[2:])
        return original(argv,*args,**kwargs)
    monkeypatch.setattr(module,'process',bounded)
    monkeypatch.setattr(sys,'argv',[str(CLI),'--manifest',str(path),'--manifest-sha256',sha(path),'--record',str(tmp_path/'evidence.json'),'--allow-continuation'])
    code=module.main();v=json.loads((tmp_path/'evidence.json').read_text())
    assert v['continuation']['exit']==0 and v['continuation']['timed_out'] is True
    assert code==1 and v['status']=='blocked'
