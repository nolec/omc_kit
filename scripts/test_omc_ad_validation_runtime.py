"""Portable runtime public surfaces; never invokes a model or logs in."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT/'scripts/omc_ad_validation_runtime.py'
BASE = '34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc'
CORE = '637a9653759cfca9e070450139dfa56cdd67c5d1'
HOME = 'libs/store-manager/components/src/lib/home/Home.tsx'
AD = 'libs/sellerhub-store/src/lib/components/Ad'
BASELINE_PATHS = [AD,'libs/sellerhub-store/src/lib/foundation',HOME,
                  'tsconfig.base.json','libs/sellerhub-store/tsconfig.json']
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
_UNIT_RUNTIME = None
_REAL_REQUESTED = bool(os.environ.get('OMC_AD_RUNTIME_INTEGRATION') == '1' or
    os.environ.get('OMC_AD_RESTORE_STOREFRONT') or os.environ.get('OMC_PREFLIGHT_PLAYWRIGHT_MODULE'))
real_surface = pytest.mark.skipif(not _REAL_REQUESTED,
    reason='external Ad integration not requested; not evidence of recovery completion')


@pytest.mark.parametrize('changed',['query','source'])
def test_restore_uses_validated_bytes_even_if_input_changes(config,tmp_path,monkeypatch,changed):
    import omc_ad_validation_runtime as runtime
    path,cfg,candidate=config
    kit=tmp_path/'detached-kit';kit.mkdir()
    manifest=json.loads(runtime.MANIFEST.read_text())
    copy=shutil.copyfile
    for name in list(manifest['sources'])+['scripts/omc_ad_validation_runtime.py']:
        dest=kit/name;dest.parent.mkdir(parents=True,exist_ok=True);copy(ROOT/name,dest)
    archive=kit/runtime.MANIFEST.relative_to(ROOT)
    archive.parent.mkdir(parents=True,exist_ok=True);archive.write_text(json.dumps(manifest))
    monkeypatch.setattr(runtime,'ROOT',kit)
    monkeypatch.setattr(runtime,'MANIFEST',archive)
    original_query=Path(cfg['queries']['home']['path']).read_bytes()
    original_home=(kit/'docs/comparison-dashboard/evidence/ad-validation-inputs-v1/Home.tsx.fixture').read_bytes()
    probes=0
    target=Path(cfg['queries']['home']['path']) if changed=='query' else kit/'docs/comparison-dashboard/evidence/ad-validation-inputs-v1/Home.tsx.fixture'
    class Probe:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,*args):
            nonlocal probes
            probes+=1
            if probes==2:target.write_bytes(b'document.body.remove(); "changed after validation"')
    monkeypatch.setattr(runtime,'socket',SimpleNamespace(socket=Probe))
    result=runtime.restore(path)
    assert probes==2 and b'changed after validation' in target.read_bytes()
    doc=json.loads(Path(result['runtime']).read_text())
    assert Path(doc['queries']['home']['path']).read_bytes()==original_query
    assert doc['queries']['home']['sha256']==manifest['queries']['home']
    assert (candidate/HOME).read_bytes()==original_home


def test_default_unit_invocation_requires_no_external_environment(tmp_path):
    env={k:v for k,v in os.environ.items() if k not in
         {'OMC_AD_RESTORE_STOREFRONT','OMC_PREFLIGHT_PLAYWRIGHT_MODULE','OMC_AD_RUNTIME_INTEGRATION'}}
    proc=subprocess.run([sys.executable,'-m','pytest',str(Path(__file__)),
        '-q','-k','restore_cli_and_overwrite_refusal','--basetemp',str(tmp_path/'independent')],
        capture_output=True,text=True,env=env,timeout=45)
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert '1 passed' in proc.stdout and 'skipped' not in proc.stdout


def test_explicit_integration_missing_environment_fails_instead_of_skipping(tmp_path):
    env={k:v for k,v in os.environ.items() if k not in
         {'OMC_AD_RESTORE_STOREFRONT','OMC_PREFLIGHT_PLAYWRIGHT_MODULE'}}
    env['OMC_AD_RUNTIME_INTEGRATION']='1'
    proc=subprocess.run([sys.executable,'-m','pytest',str(Path(__file__)),'-q',
        '-k','real_endpoint_navigation_snapshot_query_and_errors','--basetemp',str(tmp_path/'strict')],
        capture_output=True,text=True,env=env,timeout=45)
    assert proc.returncode==1
    assert 'explicit Ad integration requires' in proc.stdout
    assert 'KeyError' not in proc.stdout and 'skipped' not in proc.stdout


def invoke(*args):
    if _UNIT_RUNTIME is not None:
        stdout,stderr=io.StringIO(),io.StringIO()
        previous=sys.argv
        try:
            sys.argv=[str(CLI),*map(str,args)]
            with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                code=_UNIT_RUNTIME.main()
        finally:sys.argv=previous
        return subprocess.CompletedProcess(args,code,stdout.getvalue(),stderr.getvalue())
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=40)


def test_public_runtime_entrypoint_exists():
    assert CLI.is_file(), 'portable restore CLI is absent; archived driver depends on a deleted temporary path'


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def make_config(tmp_path,store,module,baseline,ports):
    candidate = tmp_path/'run'/('candidate-'+tmp_path.name[-30:])
    candidate.mkdir(parents=True)
    names = subprocess.check_output(['git','-C',str(store),'ls-tree','-r','--name-only',baseline,
        *BASELINE_PATHS], text=True).splitlines()
    for name in names:
        dest=candidate/name;dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(subprocess.check_output(['git','-C',str(store),'show',f'{baseline}:{name}']))
    (candidate/'node_modules').symlink_to((store/'node_modules').resolve(),target_is_directory=True)
    from omc_workflow_preflight import BROWSER_QUERY
    query=tmp_path/'home-query.js';query.write_text(BROWSER_QUERY)
    value={'schema_version':'omc-ad-runtime-config/v1','run_root':str(candidate.parent),
        'candidate':str(candidate),'baseline_repo':str(store),'baseline_commit':baseline,
        'core_commit':CORE,'port':ports[0],'control_port':ports[1],'headless':True,
        'playwright_module':str(module),'playwright_package_sha256':sha(module/'package.json'),
        'queries':{'home':{'path':str(query),'sha256':sha(query)}}}
    path=tmp_path/'config.json';path.write_text(json.dumps(value))
    return path,value,candidate


@pytest.fixture
def config(tmp_path,monkeypatch):
    """Self-contained unit baseline; never claims real Ad/browser equivalence."""
    import omc_ad_validation_runtime as runtime
    store=tmp_path/'synthetic-repo';store.mkdir()
    files={HOME:'synthetic baseline Home',AD+'/Ad.tsx':'synthetic baseline Ad',
        'libs/sellerhub-store/src/lib/foundation/index.ts':'synthetic foundation',
        'tsconfig.base.json':'{}','libs/sellerhub-store/tsconfig.json':'{}'}
    for name,text in files.items():
        path=store/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    subprocess.run(['git','-c','init.templateDir=','init','-q',str(store)],check=True)
    subprocess.run(['git','-C',str(store),'add','.'],check=True)
    subprocess.run(['git','-C',str(store),'-c','core.hooksPath=/dev/null','-c','user.name=Fixture',
        '-c','user.email=fixture@example.invalid','commit','--no-gpg-sign','-qm','synthetic unit baseline'],check=True)
    baseline=subprocess.check_output(['git','-C',str(store),'rev-parse','HEAD'],text=True).strip()
    module=store/'node_modules/playwright';module.mkdir(parents=True)
    (module/'package.json').write_text('{"name":"synthetic-unit-package"}')
    class Probe:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,*args):pass
    monkeypatch.setattr(runtime,'BASELINE',baseline)
    monkeypatch.setattr(runtime,'socket',SimpleNamespace(socket=Probe))
    monkeypatch.setattr(sys.modules[__name__],'_UNIT_RUNTIME',runtime)
    return make_config(tmp_path,store,module,baseline,(32001,32002))


@pytest.fixture
def real_config(tmp_path):
    missing=[k for k in ['OMC_AD_RESTORE_STOREFRONT','OMC_PREFLIGHT_PLAYWRIGHT_MODULE'] if not os.environ.get(k)]
    if missing:pytest.fail('explicit Ad integration requires: '+', '.join(missing),pytrace=False)
    return make_config(tmp_path,Path(os.environ['OMC_AD_RESTORE_STOREFRONT']),
        Path(os.environ['OMC_PREFLIGHT_PLAYWRIGHT_MODULE']),BASE,(port(),port()))


def test_restore_cli_and_overwrite_refusal(config):
    path,cfg,candidate=config
    result=invoke('restore','--config',path)
    assert result.returncode==0,result.stdout+result.stderr
    runtime=json.loads((candidate.parent/'runtime.json').read_text())
    assert runtime['candidate']==str(candidate)
    assert runtime['core_commit']==CORE
    assert sha(candidate/HOME)=='cea906960f5e49420c64ce759d9dfc3f7db3f784f379de4f6cfffade7cf58faf'
    assert sha(candidate/'.omc/visual-input/build-pixels.cjs')=='dfb994a6020f70eae41bf35dea2a0c638024e6cea8652e5a7f93aa10315cacc9'
    before=sha(candidate.parent/'runtime.json')
    assert invoke('restore','--config',path).returncode==1
    assert sha(candidate.parent/'runtime.json')==before


def test_recreate_after_owned_run_disappears_without_old_runtime(config):
    path,cfg,candidate=config
    assert invoke('restore','--config',path).returncode==0
    # Only this fixture owns these generated paths; original repo/dependencies stay read-only.
    shutil.rmtree(candidate.parent)
    fresh_parent=candidate.parent.parent/'new-location'
    fresh_parent.mkdir()
    names=subprocess.check_output(['git','-C',cfg['baseline_repo'],'ls-tree','-r','--name-only',cfg['baseline_commit'],
        *BASELINE_PATHS],text=True).splitlines()
    other=fresh_parent/'renamed-candidate';other.mkdir()
    for name in names:
        dest=other/name;dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(subprocess.check_output(['git','-C',cfg['baseline_repo'],'show',cfg['baseline_commit']+':'+name]))
    (other/'node_modules').symlink_to(Path(cfg['baseline_repo'])/'node_modules',target_is_directory=True)
    cfg.update(run_root=str(fresh_parent),candidate=str(other))
    path.write_text(json.dumps(cfg))
    proc=invoke('restore','--config',path)
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert (fresh_parent/'runtime.json').is_file()
    assert not candidate.parent.exists()


@pytest.mark.parametrize('defect',['missing','module','package','baseline','core','port','candidate',
    'outside','symlink','work','run-work','source','query','occupied','build-config'])
def test_refuses_invalid_inputs_before_any_write(config,defect,monkeypatch):
    path,cfg,candidate=config
    if defect=='missing':del cfg['control_port']
    elif defect=='module':cfg['playwright_module']=str(candidate/'absent')
    elif defect=='package':cfg['playwright_package_sha256']='0'*64
    elif defect=='baseline':cfg['baseline_commit']='0'*40
    elif defect=='core':cfg['core_commit']='0'*40
    elif defect=='port':cfg['control_port']=cfg['port']
    elif defect=='candidate':cfg['candidate']=cfg['baseline_repo']
    elif defect=='outside':cfg['queries']['home']['path']=str(candidate/'missing-query')
    elif defect=='symlink':(candidate/'.omc').symlink_to(candidate.parent/'elsewhere')
    elif defect=='work':(candidate/HOME).write_text('existing user work')
    elif defect=='run-work':(candidate.parent/'user-note.txt').write_text('preserve me')
    elif defect=='build-config':(candidate/'libs/sellerhub-store/tsconfig.json').unlink()
    elif defect=='source':
        source=next(candidate.glob(AD+'/*.tsx'));source.write_text('modified user source')
    elif defect=='query':cfg['queries']['home']['sha256']='0'*64
    elif defect=='occupied':
        class Occupied:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def bind(self,*args):raise OSError('port already in use')
        monkeypatch.setattr(_UNIT_RUNTIME,'socket',SimpleNamespace(socket=Occupied))
    path.write_text(json.dumps(cfg))
    result=invoke('restore','--config',path)
    assert result.returncode==1,result.stdout+result.stderr
    assert not (candidate.parent/'runtime.json').exists()
    assert not (candidate.parent/'browser.cjs').exists()


@pytest.mark.parametrize('change',['runtime','restored-home','driver'])
def test_command_rejects_changed_binding_without_connecting(config,change):
    path,cfg,candidate=config
    assert invoke('restore','--config',path).returncode==0
    target={'runtime':candidate.parent/'runtime.json','restored-home':candidate/HOME,
            'driver':candidate.parent/'browser.cjs'}[change]
    target.write_bytes(target.read_bytes()+b'\nchanged')
    proc=command(candidate,'status')
    assert proc.returncode==1
    assert 'mismatch' in proc.stderr or 'JSONDecodeError' in proc.stderr


@contextlib.contextmanager
def browser(config, directory, *, preload=None):
    path,cfg,candidate=config
    result=invoke('restore','--config',path)
    assert result.returncode==0,result.stdout+result.stderr
    server=subprocess.Popen([sys.executable,'-m','http.server',str(cfg['port']),
        '--bind','127.0.0.1','--directory',str(directory)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    log=(candidate.parent/'browser.log').open('w')
    driver=subprocess.Popen(['node',*(['--require',str(preload)] if preload else []),str(candidate.parent/'browser.cjs'),'launch',
        '--config',str(candidate.parent/'runtime.json')],stdout=log,stderr=subprocess.STDOUT)
    try:
        for _ in range(200):
            try:
                status=invoke('command','--runtime',candidate.parent/'runtime.json','--op','status')
                if status.returncode==0:break
            except subprocess.TimeoutExpired:pass
            if driver.poll() is not None:pytest.fail((candidate.parent/'browser.log').read_text())
            time.sleep(.05)
        else:pytest.fail('browser startup timeout')
        yield cfg,candidate
    finally:
        driver.terminate();server.terminate()
        for proc in [driver,server]:
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        log.close()


def command(candidate,op,*args):
    return invoke('command','--runtime',candidate.parent/'runtime.json','--op',op,'--args-json',json.dumps(args))


def assert_cells(case, cells):
    expected={(name,dpr) for name in case['variants'] for dpr in case['dprs']}
    assert len(cells)==case['expected_cells']==len(expected)
    assert {(v['id'],dpr) for v,dpr in cells}==expected
    assert all(v['status']==case['expected_status'] and v['reason']==case['expected_reason'] for v,_ in cells)


@pytest.mark.parametrize('defect',['always-passed','always-failed','wrong-reason','missing','duplicate'])
def test_matrix_comparator_rejects_false_results(defect):
    matrix=json.loads((ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json').read_text())['acceptance_matrix']
    case=next(c for c in matrix['cases'] if c['id']==('V2' if defect=='always-passed' else 'V1'))
    cells=[({'id':n,'status':case['expected_status'],'reason':case['expected_reason']},d) for n in case['variants'] for d in case['dprs']]
    if defect=='always-passed':
        for row,_ in cells:row.update(status='passed',reason=None)
    elif defect=='always-failed':
        for row,_ in cells:row.update(status='failed',reason='border_segment_or_thickness_mismatch')
    elif defect=='wrong-reason':cells[0][0]['reason']='wrong'
    elif defect=='missing':cells.pop()
    else:cells[-1]=cells[0]
    with pytest.raises(AssertionError):assert_cells(case,cells)


@real_surface
def test_real_endpoint_navigation_snapshot_query_and_errors(real_config,tmp_path):
    surface=tmp_path/'surface';surface.mkdir();(surface/'index.html').write_text('<h1>portable surface</h1>')
    with browser(real_config,surface) as (cfg,candidate):
        assert command(candidate,'goto','/index.html').returncode==0
        result=command(candidate,'snapshot');assert result.returncode==0,result.stderr
        snap=json.loads(result.stdout)
        assert snap['url']==f"http://localhost:{cfg['port']}/index.html"
        assert 'portable surface' in snap['text']
        assert Path(snap['screenshot']).is_file()
        assert snap['screenshot_sha256']==sha(snap['screenshot'])
        assert command(candidate,'goto','http://example.com').returncode==1
        refused=command(candidate,'evaluate','document.body.remove()')
        assert refused.returncode==1 and 'query_not_allowlisted' in refused.stderr
        assert command(candidate,'click','body').returncode==1
        assert command(candidate,'wait','NaN').returncode==1
        assert command(candidate,'evaluate','not-a-query-id').returncode==1


@real_surface
@pytest.mark.parametrize('query_selector',['id','frozen-text'])
@pytest.mark.parametrize('replace_at',[1,2])
def test_browser_evaluates_only_the_validated_query_buffer(real_config,tmp_path,query_selector,replace_at):
    """Replace the owned file at a deterministic real read boundary, not the driver."""
    from omc_workflow_preflight import BROWSER_QUERY
    from omc_home_observation import sample_assessment
    surface=tmp_path/'surface';surface.mkdir()
    (surface/'index.html').write_text('''<body><div data-testid="ad-card-large"><div data-testid="ad-track-card-large"><img><img></div></div>
<script>document.querySelectorAll('img').forEach(i=>i.src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'));
let n=0;setInterval(()=>{document.querySelector('[data-testid=ad-track-card-large]').style.transform='translateX('+((++n%2)*-100)+'px)'},900);</script>''')
    candidate=real_config[2];query=candidate.parent/'queries/home.js'
    armed=tmp_path/'armed';replaced=tmp_path/'replaced.json';preload=tmp_path/'concurrent-query-replacement.cjs'
    payload='(()=>{document.body.textContent="UNVALIDATED_QUERY_EXECUTED";return {unvalidated_query_executed:true}})()'
    preload.write_text("const fs=require('fs');const read=fs.readFileSync;let reads=0;fs.readFileSync=function(file,...args){const data=read.call(this,file,...args);if(String(file)==="+json.dumps(str(query))+"&&fs.existsSync("+json.dumps(str(armed))+")){if(++reads==="+str(replace_at)+"){fs.writeFileSync(file,"+json.dumps(payload)+");fs.writeFileSync("+json.dumps(str(replaced))+",JSON.stringify({reads}));}}return data;};")
    with browser(real_config,surface,preload=preload):
        assert command(candidate,'goto','/index.html').returncode==0
        pinned=json.loads((candidate.parent/'runtime.json').read_text())['queries']['home']['sha256']
        armed.write_text('replace after the selected query read returns its old bytes')
        result=command(candidate,'evaluate','home' if query_selector=='id' else BROWSER_QUERY)
        assert json.loads(replaced.read_text())['reads']==replace_at
        assert sha(query)!=pinned  # The replacement really occurred in both branches.
        if replace_at==1:
            assert result.returncode==1 and 'query_not_allowlisted' in result.stderr
        else:
            assert result.returncode==0,result.stdout+result.stderr
            observed=json.loads(result.stdout)
            assert observed.get('unvalidated_query_executed') is not True
            assert sample_assessment(observed)=={'status':'passed','reason':None}
        (tmp_path/'query-execution-result.json').write_text(json.dumps({
            'replace_at':replace_at,'query_selector':query_selector,'query_expected_sha256':pinned,
            'replaced_query_sha256':sha(query),'exit_code':result.returncode,
            'stdout':result.stdout,'stderr':result.stderr},ensure_ascii=False,indent=2))


@pytest.mark.parametrize('place',['A','B'])
@real_surface
def test_real_matrix_in_each_new_location(real_config,tmp_path,place):
    """Use the frozen original fixture and oracle through the restored driver."""
    from test_omc_visual_check import build_site
    from omc_visual_check import assess_report
    surface=tmp_path/place;surface.mkdir()
    matrix=json.loads((ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json').read_text())['acceptance_matrix']
    assert matrix['version']=='runtime-surface-acceptance/v2'
    with browser(real_config,surface) as (cfg,candidate):
        origin=f"http://localhost:{cfg['port']}"
        for case in matrix['cases']:
            if not case['id'].startswith('V'):continue
            cells=[]
            for dpr in case['dprs']:
                case_dir=surface/(case['id']+str(dpr));case_dir.mkdir()
                # Fixture files are part of this candidate snapshot for oracle binding.
                target=candidate/'.omc'/('surface-'+case['id']+str(dpr));target.mkdir()
                doc=build_site(case_dir,(target,origin,{}),case['fixture_mode'],dpr)
                # HTTP server is static and must serve the exact same bytes.
                for name in ['app.src','bundle.js','index.html']:
                    (surface/name).write_bytes((target/name).read_bytes())
                assert command(candidate,'goto','/index.html').returncode==0
                snap=json.loads(command(candidate,'snapshot').stdout)
                assert snap['url']==origin+'/index.html'
                (case_dir/'driver-snapshot.json').write_text(json.dumps(snap,ensure_ascii=False))
                output=case_dir/'capture'
                proc=subprocess.run([sys.executable,str(ROOT/'scripts/omc_visual_check.py'),
                    '--target',str(target),'--manifest',str(doc),'--manifest-sha256',sha(doc),
                    '--output-dir',str(output),'--playwright-module',cfg['playwright_module']],
                    capture_output=True,text=True,timeout=45)
                (case_dir/'stdout.txt').write_text(proc.stdout);(case_dir/'stderr.txt').write_text(proc.stderr)
                report=output/'report.json';value=json.loads(report.read_text())
                assert proc.returncode==case['expected_exit_code'],proc.stdout+proc.stderr
                assert value['status']==case['expected_status']
                assessed=assess_report(target,report,sha(report),sha(doc))
                assert assessed['status']==case['expected_status'],assessed
                assert assessed['reason']==case['aggregate_reason'],assessed
                cells.extend((v,dpr) for v in value['variants'])
                if case['id']=='V1':
                    other=case_dir/'other';other.mkdir()
                    assert assess_report(other,report,sha(report),sha(doc))['reason']=='visual_report_binding_mismatch'
                    assert assess_report(target,report,'0'*64,sha(doc))['reason']=='visual_report_digest_mismatch'
            assert_cells(case,cells)


@pytest.mark.parametrize('scenario,reason',[('delayed-dom',None),('delayed-images',None),
    ('absent','home_ad_not_ready'),('late-ad','home_ad_not_ready'),('late-timer','home_ad_not_ready'),
    ('replaced','home_track_replaced'),('stationary','home_motion_not_observed')])
@real_surface
def test_home_query_via_restored_public_endpoint(real_config,tmp_path,scenario,reason):
    from omc_home_observation import sample_assessment
    surface=tmp_path/'home';surface.mkdir()
    script="""const scenario=SCENARIO;
const insert=()=>{document.body.innerHTML='<div data-testid="ad-card-large"><div data-testid="ad-track-card-large"><img><img></div></div>';
 const load=()=>document.querySelectorAll('img').forEach(i=>i.src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'));
 if(scenario==='delayed-images')setTimeout(load,450);else load();};
if(scenario==='delayed-dom')setTimeout(insert,450);
else if(scenario==='late-ad')setTimeout(insert,5200);
else if(scenario==='late-timer')setTimeout(()=>{const end=performance.now()+400;while(performance.now()<end){}insert()},4800);
else if(scenario!=='absent')insert();
let n=0;setInterval(()=>{const t=document.querySelector('[data-testid=ad-track-card-large]');if(t)t.style.transform='translateX('+((++n%2)*-100)+'px)'},900);
if(scenario==='replaced')setTimeout(insert,1400);
""".replace('SCENARIO',json.dumps(scenario))
    if scenario=='stationary':script=script.replace("if(t)t.style.transform", "if(false)t.style.transform")
    (surface/'index.html').write_text('<body><script>'+script+'</script>')
    with browser(real_config,surface) as (_,candidate):
        assert command(candidate,'goto','/index.html').returncode==0
        # Preserve compatibility with the actual frozen preflight caller, not only a new wrapper.
        from omc_workflow_preflight import browser_observation
        cfg=json.loads((candidate.parent/'runtime.json').read_text())
        observed=browser_observation({'control_url':f"http://127.0.0.1:{cfg['control_port']}/command",
            'origin':f"http://localhost:{cfg['port']}",'home_path':'/index.html','min_images':2})
        assert sample_assessment(observed)=={'status':'unknown' if reason else 'passed','reason':reason}
        (candidate.parent/'home-observation.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2))
        if scenario in ('late-ad','late-timer','absent'):
            assert observed['tracks']==[]
            assert observed['readiness']['status']=='timeout'


@pytest.mark.parametrize('place',['A','B'])
@real_surface
def test_restored_builders_use_real_baseline_ad(real_config,place):
    _,_,candidate=real_config
    with browser(real_config,candidate/'apps/store-manager/public') as (cfg,_):
        for builder in [candidate.parent/'build-controlled.cjs',candidate/'.omc/visual-input/build-pixels.cjs']:
            result=subprocess.run(['node',str(builder)],cwd=candidate,capture_output=True,text=True,timeout=60)
            assert result.returncode==0,result.stdout+result.stderr
        for name in ['omc-ad-validation','omc-ad-pixels']:
            out=candidate/'apps/store-manager/public'/name
            assert len((out/'bundle.js').read_bytes())>10000
            assert command(candidate,'goto','/'+name+'/index.html').returncode==0
            snap=json.loads(command(candidate,'snapshot').stdout)
            bundle=f"http://localhost:{cfg['port']}/{name}/bundle.js"
            assert snap['bundle_hashes'][bundle]==sha(out/'bundle.js')
            assert all(variant in snap['text'] for variant in ['card-large','card-medium','card-small','banner-small','banner-large','banner-landing'])
            (candidate.parent/(name+'-snapshot.json')).write_text(json.dumps(snap,ensure_ascii=False))
        # Preserve baseline classification separately; a baseline design failure is expected.
        out=candidate/'apps/store-manager/public/omc-ad-pixels'
        for dpr in [1,2]:
            manifest={'schema_version':'omc-visual-manifest/v1','url':f"http://localhost:{cfg['port']}/omc-ad-pixels/index.html",
                'bundle_url':f"http://localhost:{cfg['port']}/omc-ad-pixels/bundle.js",'bundle_path':str((out/'bundle.js').relative_to(candidate)),
                'bundle_sha256':sha(out/'bundle.js'),'build_inputs':{'.omc/visual-input/build-pixels.cjs':sha(candidate/'.omc/visual-input/build-pixels.cjs')},
                'dpr':dpr,'images_per_variant':2}
            doc=candidate.parent/f'baseline-pixels-{dpr}.json';doc.write_text(json.dumps(manifest))
            output=candidate.parent/f'baseline-capture-{dpr}'
            proc=subprocess.run([sys.executable,str(ROOT/'scripts/omc_visual_check.py'),'--target',str(candidate),
                '--manifest',str(doc),'--manifest-sha256',sha(doc),'--output-dir',str(output),
                '--playwright-module',cfg['playwright_module']],capture_output=True,text=True,timeout=45)
            (candidate.parent/f'baseline-pixels-{dpr}-stdout.txt').write_text(proc.stdout)
            (candidate.parent/f'baseline-pixels-{dpr}-stderr.txt').write_text(proc.stderr)
            report=json.loads((output/'report.json').read_text())
            assert len(report['variants'])==6,proc.stdout+proc.stderr
            assert proc.returncode==1 and report['status']=='failed',report
