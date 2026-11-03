"""Portable runtime public surfaces; never invokes a model or logs in."""
import ast
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
import tarfile
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT/'scripts/omc_ad_validation_runtime.py'
BASE = '34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc'
CORE = '996d80d539d7245443f3a539f1a9fa333b450770'
FROZEN_HOME_INPUT = ('docs/comparison-dashboard/evidence/ad-validation-runtime-v1/'
                     'core-996d80d/omc_home_observation.py.fixture')
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


@pytest.mark.parametrize('location',['first','second'])
def test_standalone_templates_render_actual_requirement_path(tmp_path,location):
    folder=ROOT/'docs/comparison-dashboard/evidence/ad-standalone-contract-v1'
    candidate=tmp_path/location; requirement=candidate/'.omc/visual-requirement.json'
    requirement.parent.mkdir(parents=True);requirement.write_text('{"policy":"synthetic"}')
    values={'CANDIDATE_ROOT':str(candidate),'REQUIREMENT_SHA256':sha(requirement)}
    def render(name):
        text=(folder/name).read_text()
        for key,value in values.items():text=text.replace('{{'+key+'}}',value)
        assert '{{' not in text
        return text
    contract=json.loads(render('common-input.template.json'));prompt=render('prompt.template.md')
    ref=contract['requirement_file']
    assert ref['relative_path']=='.omc/visual-requirement.json'
    assert Path(ref['absolute_path'])==requirement and ref['sha256']==sha(requirement)
    assert all(value in prompt for value in ref.values())
    assert '.omc/visual-input/visual-requirement.json' not in prompt
    assert 'session_id' not in contract and 'approval' not in contract
    budget=contract['time_budget']
    assert budget['progress_checkpoint_seconds']==300
    assert budget['checkpoint_action']=='record_progress_only'
    assert budget['per_arm_total_seconds']==1800
    assert '300초 경과만으로 중단하지 않습니다' in prompt
    assert (folder/'README.md').is_file()


@pytest.mark.parametrize('defect',['missing','hash','path','placeholder'])
def test_standalone_rendered_documents_reject_bad_requirement(tmp_path,defect):
    folder=ROOT/'docs/comparison-dashboard/evidence/ad-standalone-contract-v1'
    requirement=tmp_path/'.omc/visual-requirement.json';requirement.parent.mkdir();requirement.write_text('{}')
    contract=(folder/'common-input.template.json').read_text().replace('{{CANDIDATE_ROOT}}',str(tmp_path)).replace('{{REQUIREMENT_SHA256}}',sha(requirement))
    prompt=(folder/'prompt.template.md').read_text().replace('{{CANDIDATE_ROOT}}',str(tmp_path)).replace('{{REQUIREMENT_SHA256}}',sha(requirement))
    value=json.loads(contract)
    if defect=='missing':requirement.unlink()
    elif defect=='hash':requirement.write_text('{"changed":true}')
    elif defect=='path':prompt=prompt.replace(str(requirement),str(tmp_path/'wrong.json'))
    else:prompt+=' {{UNFILLED}}'
    # Document acceptance only: this is not a production generator or model compliance test.
    accepted=(requirement.is_file() and value['requirement_file']['sha256']==sha(requirement)
              and value['requirement_file']['absolute_path'] in prompt and '{{' not in prompt)
    assert not accepted


def test_frozen_home_manifest_matches_original_git_object():
    manifest=json.loads((ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json').read_text())
    assert 'scripts/omc_home_observation.py' not in manifest['sources']
    entry=manifest['sources'][FROZEN_HOME_INPUT]
    expected=subprocess.check_output(['git','-C',str(ROOT),'show',CORE+':scripts/omc_home_observation.py'])
    assert (ROOT/FROZEN_HOME_INPUT).read_bytes()==expected
    assert entry['sha256']==hashlib.sha256(expected).hexdigest()
    assert 'destination' not in entry


@real_surface
@pytest.mark.parametrize('defect',[None,'changed','missing','digest'])
def test_frozen_home_public_restore_consumes_archive(real_config,tmp_path,defect):
    path,cfg,candidate=real_config
    manifest_path=ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json'
    manifest=json.loads(manifest_path.read_text())
    assert FROZEN_HOME_INPUT in manifest['sources']
    kit=tmp_path/'operator-kit';kit.mkdir()
    for name in [str(CLI.relative_to(ROOT)),*manifest['sources']]:
        dest=kit/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes())
    # A mutable host source must not substitute for the frozen core input.
    host=kit/'scripts/omc_home_observation.py';host.write_text('changed host; not a frozen core')
    frozen=kit/FROZEN_HOME_INPUT
    if defect=='changed':frozen.write_bytes(b'changed frozen input')
    elif defect=='missing':frozen.unlink()
    elif defect=='digest':manifest['sources'][FROZEN_HOME_INPUT]['sha256']='0'*64
    archive=kit/manifest_path.relative_to(ROOT);archive.parent.mkdir(parents=True,exist_ok=True)
    archive.write_text(json.dumps(manifest))
    before={p.relative_to(candidate).as_posix():sha(p) for p in candidate.rglob('*')
            if p.is_file() and 'node_modules' not in p.relative_to(candidate).parts}
    proc=subprocess.run([sys.executable,str(kit/CLI.relative_to(ROOT)),'restore','--config',str(path)],
                        capture_output=True,text=True,timeout=40)
    if defect is None:
        assert proc.returncode==0,proc.stdout+proc.stderr
        assert json.loads((candidate.parent/'runtime.json').read_text())['core_commit']==CORE
        assert (candidate.parent/'restore-receipt.json').is_file()
        assert not (candidate/'scripts/omc_home_observation.py').exists()
    else:
        assert proc.returncode==1,proc.stdout+proc.stderr
        assert not (candidate.parent/'runtime.json').exists()
        assert not (candidate.parent/'restore-receipt.json').exists()
        after={p.relative_to(candidate).as_posix():sha(p) for p in candidate.rglob('*')
               if p.is_file() and 'node_modules' not in p.relative_to(candidate).parts}
        assert after==before
    assert host.read_text()=='changed host; not a frozen core'


@pytest.mark.parametrize('changed',['query','controlled-query','source'])
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
    query_name='controlled' if changed=='controlled-query' else 'home'
    original_query=Path(cfg['queries'][query_name]['path']).read_bytes()
    original_home=(kit/'docs/comparison-dashboard/evidence/ad-validation-inputs-v1/Home.tsx.fixture').read_bytes()
    probes=0
    target=Path(cfg['queries'][query_name]['path']) if changed in {'query','controlled-query'} else kit/'docs/comparison-dashboard/evidence/ad-validation-inputs-v1/Home.tsx.fixture'
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
    assert Path(doc['queries'][query_name]['path']).read_bytes()==original_query
    assert doc['queries'][query_name]['sha256']==manifest['queries'][query_name]
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


def test_v3_accepts_new_core_without_relabeling_historical_fixture(config):
    path,cfg,candidate=config
    cfg['core_commit']='7bdbcb81882ef8ce515e48cf9b4fac07fd273176'
    path.write_text(json.dumps(cfg))
    result=invoke('restore','--config',path)
    assert result.returncode==0,result.stdout+result.stderr
    runtime=json.loads((candidate.parent/'runtime.json').read_text())
    receipt=json.loads((candidate.parent/'restore-receipt.json').read_text())
    assert runtime['core_commit']==cfg['core_commit']
    assert receipt['execution_core_commit']==cfg['core_commit']
    assert receipt['archive_core_commit']==CORE
    assert not (candidate/'scripts/omc_home_observation.py').exists()


CURRENT_CORE = '3d716da4a4f51d06e9fc90735598d2bba822f445'


def test_v4_accepts_063_without_relabeling_historical_fixture(config):
    path,cfg,candidate=config
    cfg['core_commit']=CURRENT_CORE;path.write_text(json.dumps(cfg))
    result=invoke('restore','--config',path)
    assert result.returncode==0,result.stdout+result.stderr
    restored=json.loads((candidate.parent/'runtime.json').read_text())
    receipt=json.loads((candidate.parent/'restore-receipt.json').read_text())
    assert restored['core_commit']==CURRENT_CORE
    assert receipt['execution_core_commit']==CURRENT_CORE
    assert receipt['archive_core_commit']==CORE
    assert not (candidate/'scripts/omc_home_observation.py').exists()


def test_v3_durable_seven_contract_and_shared_budget():
    import omc_ad_validation_runtime as runtime
    contract=runtime.common_contract()
    assert [r['id'] for r in contract['checks']]==[
        'tests','lint','build-controlled','build-pixels','controlled','pixels-dpr1','pixels-dpr2']
    assert sum(r['timeout_seconds'] for r in contract['checks'])==750
    assert contract['auxiliary_seconds']==30
    assert contract['time_budget']['cumulative_targets_seconds']['final_seven_checks']==1680
    assert contract['manifest_generation_python']
    assert contract['checks'][0]['expected_final_exit']==1


@pytest.mark.parametrize('mutation',['missing','duplicate','changed-limit','changed-command'])
def test_v3_common_contract_rejects_drift(tmp_path,monkeypatch,mutation):
    import omc_ad_validation_runtime as runtime
    source=ROOT/'docs/comparison-dashboard/evidence/ad-standalone-contract-v1/common-input.template.json'
    value=json.loads(source.read_text())
    assert 'checks' in value
    if mutation=='missing':value['checks'].pop()
    elif mutation=='duplicate':value['checks'][-1]=value['checks'][0]
    elif mutation=='changed-limit':value['checks'][0]['timeout_seconds']=1
    else:value['checks'][0]['argv']=['true']
    p=tmp_path/'contract.json';p.write_text(json.dumps(value))
    with pytest.raises(ValueError):runtime.common_contract(p)


@pytest.mark.parametrize('location',['one','two'])
def test_v3_public_contract_render_has_no_temporary_dependency(tmp_path,location):
    candidate=tmp_path/location;requirement=candidate/'.omc/visual-requirement.json'
    requirement.parent.mkdir(parents=True);requirement.write_text('{}')
    bindings={'CANDIDATE_ROOT':str(candidate),'ARM_ROOT':str(candidate.parent),
              'OPERATOR_ROOT':str(ROOT),'CORE_ROOT':str(ROOT),'NODE':str(Path(sys.executable).resolve()),
              'PYTHON':str(Path(sys.executable).resolve()),'PORT':3999}
    path=tmp_path/'bindings.json';path.write_text(json.dumps(bindings));output=tmp_path/'contract.json'
    p=subprocess.run([sys.executable,str(CLI),'contract','--bindings',str(path),'--output',str(output)],
                     capture_output=True,text=True,timeout=5)
    assert p.returncode==0,p.stderr
    value=json.loads(output.read_bytes())
    assert value['requirement_file']['absolute_path']==str(requirement)
    assert value['requirement_file']['sha256']==sha(requirement)
    assert value['checks'][0]['cwd']==str(candidate)
    assert '/private/tmp/omc-ad-standalone-20261024T184725Z' not in output.read_text()
    assert value['checks'][-1]['argv'][7]=='@MANIFEST_2_SHA256@'


def test_v3_forged_passed_label_is_not_semantic_evidence(tmp_path):
    import omc_ad_validation_runtime as runtime
    with pytest.raises((KeyError,ValueError)):
        runtime.assess_common_result(tmp_path,'controlled',{'assessment':'passed','timed_out':False})


@real_surface
@pytest.mark.parametrize('selected',[CORE,'7bdbcb81882ef8ce515e48cf9b4fac07fd273176',CURRENT_CORE])
def test_v3_real_restore_then_selected_core_install(real_config,tmp_path,selected):
    path,cfg,candidate=real_config
    cfg['core_commit']=selected;path.write_text(json.dumps(cfg))
    result=subprocess.run([sys.executable,str(CLI),'restore','--config',str(path)],
                          capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    kit=tmp_path/'selected-source';kit.mkdir()
    raw=subprocess.check_output(['git','-C',str(ROOT),'archive',selected])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            name=Path(member.name)
            assert not name.is_absolute() and '..' not in name.parts
            assert member.isdir() or member.isfile()
            if member.isfile():
                dest=kit/name;dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(archive.extractfile(member).read());dest.chmod(member.mode)
    subprocess.run(['git','-c','init.templateDir=','init','-q',str(candidate)],check=True)
    installed=subprocess.run([sys.executable,str(kit/'scripts/install.py'),'--target',str(candidate)],
        capture_output=True,text=True,timeout=60,env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
    (tmp_path/'selected-install.log').write_text(installed.stdout+installed.stderr)
    assert installed.returncode==0,installed.stdout+installed.stderr
    for name in ['omc_home_observation.py','omc_validation_command.py','omc_pipeline_guard.py']:
        expected=subprocess.check_output(['git','-C',str(ROOT),'show',selected+':scripts/'+name])
        assert (candidate/'scripts'/name).read_bytes()==expected
    receipt=json.loads((candidate.parent/'restore-receipt.json').read_bytes())
    assert receipt['execution_core_commit']==selected and receipt['archive_core_commit']==CORE


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
    queries={}
    for name in ['home','controlled']:
        query=tmp_path/(name+'-query.js');query.write_bytes(frozen_query(name).encode('utf8'))
        queries[name]={'path':str(query),'sha256':sha(query)}
    value={'schema_version':'omc-ad-runtime-config/v1','run_root':str(candidate.parent),
        'candidate':str(candidate),'baseline_repo':str(store),'baseline_commit':baseline,
        'core_commit':CORE,'port':ports[0],'control_port':ports[1],'headless':True,
        'playwright_module':str(module),'playwright_package_sha256':sha(module/'package.json'),
        'queries':queries}
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


CANDIDATE_CORE = '996d80d539d7245443f3a539f1a9fa333b450770'
PREVIOUS_CORE = '99773380d11a1e71ea49f2c80318227756d8d56e'
OLDER_CORE = '637a9653759cfca9e070450139dfa56cdd67c5d1'


def test_restore_accepts_exact_996d80d_candidate(config):
    path,cfg,candidate=config
    cfg['core_commit']=CANDIDATE_CORE
    path.write_text(json.dumps(cfg))
    result=invoke('restore','--config',path)
    assert result.returncode==0,result.stdout+result.stderr
    doc=json.loads((candidate.parent/'runtime.json').read_text())
    receipt=json.loads((candidate.parent/'restore-receipt.json').read_text())
    assert doc['core_commit']==CANDIDATE_CORE
    assert receipt['config_sha256']==sha(path)
    assert receipt['runtime_sha256']==sha(candidate.parent/'runtime.json')


@pytest.mark.parametrize('defect',['previous-core','older-core','unknown-core','previous-manifest','older-manifest',
                                 'unknown-manifest','missing-manifest-core'])
def test_restore_core_contract_refuses_before_writing(config,tmp_path,monkeypatch,defect):
    import omc_ad_validation_runtime as runtime
    path,cfg,candidate=config
    cfg['core_commit']=CANDIDATE_CORE
    manifest=json.loads(runtime.MANIFEST.read_text())
    manifest['core_commit']=CANDIDATE_CORE
    if defect=='previous-core':cfg['core_commit']=PREVIOUS_CORE
    elif defect=='older-core':cfg['core_commit']=OLDER_CORE
    elif defect=='unknown-core':cfg['core_commit']='0'*40
    elif defect=='previous-manifest':manifest['core_commit']=PREVIOUS_CORE
    elif defect=='older-manifest':manifest['core_commit']=OLDER_CORE
    elif defect=='unknown-manifest':manifest['core_commit']='0'*40
    else:del manifest['core_commit']
    archive=tmp_path/'core-contract-manifest.json';archive.write_text(json.dumps(manifest))
    monkeypatch.setattr(runtime,'MANIFEST',archive)
    path.write_text(json.dumps(cfg))
    before={p.relative_to(candidate).as_posix():sha(p) for p in candidate.rglob('*') if p.is_file()}
    result=invoke('restore','--config',path)
    assert result.returncode==1,result.stdout+result.stderr
    error=json.loads(result.stderr)['error']
    assert error==('candidate_version_mismatch' if defect in {'previous-core','older-core','unknown-core'} else 'manifest_core_mismatch')
    assert not (candidate.parent/'runtime.json').exists()
    assert not (candidate.parent/'restore-receipt.json').exists()
    assert not (candidate.parent/'browser.cjs').exists()
    assert before=={p.relative_to(candidate).as_posix():sha(p) for p in candidate.rglob('*') if p.is_file()}


@real_surface
@pytest.mark.parametrize('defect',[None,'previous-core','older-core','unknown-core','previous-manifest','older-manifest',
                                 'unknown-manifest','missing-manifest-core'])
def test_real_public_restore_core_contract(real_config,tmp_path,defect):
    path,cfg,candidate=real_config
    kit=tmp_path/'public-cli-kit';kit.mkdir()
    archive=ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json'
    manifest=json.loads(archive.read_text())
    for name in [str(CLI.relative_to(ROOT)),*manifest['sources']]:
        dest=kit/name;dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes((ROOT/name).read_bytes())
    if defect=='previous-core':cfg['core_commit']=PREVIOUS_CORE
    elif defect=='older-core':cfg['core_commit']=OLDER_CORE
    elif defect=='unknown-core':cfg['core_commit']='0'*40
    elif defect=='previous-manifest':manifest['core_commit']=PREVIOUS_CORE
    elif defect=='older-manifest':manifest['core_commit']=OLDER_CORE
    elif defect=='unknown-manifest':manifest['core_commit']='0'*40
    elif defect=='missing-manifest-core':del manifest['core_commit']
    doc=kit/archive.relative_to(ROOT);doc.parent.mkdir(parents=True,exist_ok=True)
    doc.write_text(json.dumps(manifest))
    path.write_text(json.dumps(cfg))
    before={p.relative_to(candidate).as_posix():sha(p) for p in candidate.rglob('*') if p.is_file()}
    result=subprocess.run([sys.executable,str(kit/CLI.relative_to(ROOT)),
        'restore','--config',str(path)],capture_output=True,text=True,timeout=40)
    if defect is None:
        assert result.returncode==0,result.stdout+result.stderr
        runtime=json.loads((candidate.parent/'runtime.json').read_text())
        receipt=json.loads((candidate.parent/'restore-receipt.json').read_text())
        assert runtime['core_commit']==CANDIDATE_CORE
        assert receipt['config_sha256']==sha(path)
        assert receipt['runtime_sha256']==sha(candidate.parent/'runtime.json')
        assert receipt['manifest_sha256']==sha(doc)
        saved=sha(candidate.parent/'runtime.json')
        again=subprocess.run([sys.executable,str(kit/CLI.relative_to(ROOT)),
            'restore','--config',str(path)],capture_output=True,text=True,timeout=40)
        assert again.returncode==1 and sha(candidate.parent/'runtime.json')==saved
    else:
        assert result.returncode==1,result.stdout+result.stderr
        assert json.loads(result.stderr)['error']==('candidate_version_mismatch'
            if defect in {'previous-core','older-core','unknown-core'} else 'manifest_core_mismatch')
        assert before=={p.relative_to(candidate).as_posix():sha(p) for p in candidate.rglob('*') if p.is_file()}
        assert not (candidate.parent/'runtime.json').exists()
        assert not (candidate.parent/'restore-receipt.json').exists()
        assert not (candidate.parent/'browser.cjs').exists()


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


@contextlib.contextmanager
def lifecycle_driver(config, tmp_path, mode='single'):
    """Run the public HTTP driver with a deterministic Playwright lifecycle double."""
    path,cfg,candidate=config
    assert invoke('restore','--config',path).returncode==0
    cfg=json.loads((candidate.parent/'runtime.json').read_text())
    state=tmp_path/'lifecycle-state.json';state.write_text('{}')
    module=Path(cfg['playwright_module'])
    (module/'index.js').write_text("""
const {EventEmitter}=require('events'),fs=require('fs');
const state=()=>JSON.parse(fs.readFileSync(process.env.OMC_TEST_STATE,'utf8'));
const base=process.env.OMC_TEST_ORIGIN,mode=process.env.OMC_TEST_MODE;
const context=new EventEmitter(),browser=new EventEmitter();
let currentClosed=false,contextClosed=false,disconnected=false,created=0;
function sync(){const s=state();
 if(s.pageClosed&&!currentClosed){currentClosed=true;first.emit('close');}
 if(s.contextClosed&&!contextClosed){contextClosed=true;context.emit('close');}
 if(s.disconnected&&!disconnected){disconnected=true;browser.emit('disconnected');}}
function page(name,url,closed=false){const p=new EventEmitter();
 p.isClosed=()=>{sync();return closed||(p===first&&currentClosed);};
 p.url=()=>{sync();return url;};p.route=async()=>{};
 p.goto=async()=>{sync();if(closed||currentClosed||contextClosed||disconnected)throw Error('closed target');};
 p.evaluate=async()=>{sync();if(closed||currentClosed||contextClosed||disconnected)throw Error('closed target');return true;};
 p.mainFrame=()=>p;p.name=name;return p;}
const first=page('first',mode==='closed-first'?'about:blank':base+'/sixshoptest01',mode==='closed-first'||mode==='none');
const second=page('second',base+'/sixshoptest01');
context.pages=()=>{sync();return mode==='closed-first'||mode==='ambiguous'?[first,second]:[first];};
context.newPage=async()=>{created++;throw Error('unexpected automatic page creation');};
context.browser=()=>{sync();return browser;};browser.isConnected=()=>{sync();return !disconnected;};
context.close=async()=>{contextClosed=true;context.emit('close');};
module.exports={chromium:{launchPersistentContext:async()=>context}};
""")
    log=tmp_path/'lifecycle-driver.log'
    with log.open('w') as stream:
        proc=subprocess.Popen(['node',str(candidate.parent/'browser.cjs'),'launch','--config',
            str(candidate.parent/'runtime.json')],stdout=stream,stderr=stream,
            env={**os.environ,'OMC_TEST_STATE':str(state),'OMC_TEST_MODE':mode,
                 'OMC_TEST_ORIGIN':'http://localhost:'+str(cfg['port'])})
        try:
            end=time.monotonic()+5
            while proc.poll() is None and 'control_ready' not in log.read_text() and time.monotonic()<end:
                time.sleep(.01)
            assert proc.poll() is not None or 'control_ready' in log.read_text(),log.read_text()
            yield proc,state,log,candidate,cfg
        finally:
            if proc.poll() is None:proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


def lifecycle_request(cfg, op='status', args=None):
    request=urllib.request.Request('http://127.0.0.1:'+str(cfg['control_port'])+'/command',
        data=json.dumps({'op':op,'args':args or []}).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=5) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as error:return error.code,json.loads(error.read())


@pytest.mark.parametrize('state_key',['pageClosed','contextClosed','disconnected'])
def test_lifecycle_status_rejects_closed_target_and_never_recreates(config,tmp_path,state_key):
    with lifecycle_driver(config,tmp_path) as (proc,state,log,candidate,cfg):
        assert lifecycle_request(cfg)[1]['ready'] is True
        state.write_text(json.dumps({state_key:True}))
        code,value=lifecycle_request(cfg)
        assert value.get('ready') is False,(code,value)
        assert value.get('reason'),value
        code,value=lifecycle_request(cfg,'goto',['http://localhost:'+str(cfg['port'])+'/sixshoptest01'])
        assert code>=400 and 'closed' in value.get('error','') or code>=400 and 'disconnected' in value.get('error','')
        assert 'unexpected automatic page creation' not in log.read_text()


def test_lifecycle_binds_unique_live_page_instead_of_closed_first(config,tmp_path):
    with lifecycle_driver(config,tmp_path,'closed-first') as (proc,state,log,candidate,cfg):
        code,value=lifecycle_request(cfg)
        assert code==200 and value.get('ready') is True
        assert value['url']=='http://localhost:'+str(cfg['port'])+'/sixshoptest01'
        assert lifecycle_request(cfg,'goto',[value['url']])[0]==200


@pytest.mark.parametrize('mode',['ambiguous','none'])
def test_lifecycle_refuses_ambiguous_or_missing_live_page(config,tmp_path,mode):
    with lifecycle_driver(config,tmp_path,mode) as (proc,state,log,candidate,cfg):
        assert proc.poll() is not None,log.read_text()
        assert 'page_binding_' in log.read_text()


def test_lifecycle_live_status_preserves_origin_and_query_guards(config,tmp_path):
    with lifecycle_driver(config,tmp_path) as (proc,state,log,candidate,cfg):
        assert lifecycle_request(cfg)[1]['ready'] is True
        code,value=lifecycle_request(cfg,'goto',['https://outside.invalid'])
        assert code==400 and value['error']=='only_own_origin'
        code,value=lifecycle_request(cfg,'evaluate',['not-an-allowlisted-query'])
        assert code==400 and value['error']=='query_not_allowlisted'


@pytest.mark.parametrize('closed_target',['page','context'])
@real_surface
def test_lifecycle_real_chromium_closed_target_is_not_ready(real_config,tmp_path,closed_target):
    surface=tmp_path/'lifecycle-surface';surface.mkdir()
    (surface/'index.html').write_text('<h1>lifecycle fixture</h1>')
    armed=tmp_path/'close-target';closed=tmp_path/'closed-target'
    preload=tmp_path/'lifecycle-close-preload.cjs'
    preload.write_text("const fs=require('fs'),M=require('module'),load=M._load;M._load=function(name,...args){"
        "const value=load.call(this,name,...args);if(name==="+json.dumps(real_config[1]['playwright_module'])+") {"
        "const launch=value.chromium.launchPersistentContext.bind(value.chromium);"
        "value.chromium.launchPersistentContext=async(...a)=>{const c=await launch(...a);"
        "const timer=setInterval(()=>{if(!fs.existsSync("+json.dumps(str(armed))+"))return;clearInterval(timer);"
        "Promise.resolve("+("c.pages()[0].close()" if closed_target=='page' else "c.close()")+")"
        ".then(()=>fs.writeFileSync("+json.dumps(str(closed))+",'closed'));},10);return c;};}return value;};")
    with browser(real_config,surface,preload=preload) as (cfg,candidate):
        assert command(candidate,'goto','/index.html').returncode==0
        armed.write_text('close once')
        end=time.monotonic()+5
        while not closed.exists() and time.monotonic()<end:time.sleep(.01)
        assert closed.exists(),'fixture did not close the selected target'
        result=command(candidate,'status')
        assert result.returncode==0,result.stdout+result.stderr
        value=json.loads(result.stdout)
        assert value['ready'] is False and 'closed' in value['reason']
        refused=command(candidate,'goto','/index.html')
        assert refused.returncode==1 and 'closed' in refused.stderr


@real_surface
def test_v2_actual_browser_keeps_six_samples_and_raw_execution(real_config,tmp_path):
    from omc_workflow_preflight import browser_observation,read_browser_execution
    surface=tmp_path/'v2-home';surface.mkdir()
    image='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2210%22 height=%2210%22/%3E'
    (surface/'index.html').write_text('<body><div data-testid="ad-card-large"><div data-testid="ad-track-card-large">'
        +f'<img src="{image}"><img src="{image}"></div></div>'
        +'<script>let n=0;setInterval(()=>document.querySelector("[data-testid^=ad-track]").style.transform="translateX("+((++n%2)*-100)+"px)",900)</script>')
    with browser(real_config,surface) as (cfg,candidate):
        assert command(candidate,'goto','/index.html').returncode==0
        config={'control_url':f"http://127.0.0.1:{cfg['control_port']}/command",
            'origin':f"http://localhost:{cfg['port']}",'home_path':'/index.html','min_images':2}
        result=browser_observation(config,root=candidate)
        doc=read_browser_execution(candidate,result['browser_execution'],expected_ops=['evaluate'],observation=result)
        assert len(result['tracks'][0]['samples'])==6 and result['auto_transition'] is True
        assert doc['stages'][0]['termination']=='stopped_confirmed'


@pytest.mark.parametrize('ack_lost',[False,True])
@real_surface
def test_v2_actual_browser_timeout_closes_and_poisoned_channel_cannot_run(real_config,tmp_path,ack_lost):
    from omc_workflow_preflight import BrowserExecution,read_browser_execution
    surface=tmp_path/'slow-navigation';surface.mkdir()
    (surface/'index.html').write_text('<body><script>const end=performance.now()+22000;while(performance.now()<end){}</script>')
    preload=None
    if ack_lost:
        preload=tmp_path/'lost-close-ack.cjs'
        preload.write_text("const M=require('module'),load=M._load;M._load=function(name,...args){const v=load.call(this,name,...args);if(name==="
            +json.dumps(real_config[1]['playwright_module'])+") {const launch=v.chromium.launchPersistentContext.bind(v.chromium);v.chromium.launchPersistentContext=async(...a)=>{const c=await launch(...a),close=c.close.bind(c);c.close=()=>close().then(()=>new Promise(()=>{}));return c;};}return v;};")
    with browser(real_config,surface,preload=preload) as (cfg,candidate):
        config={'control_url':f"http://127.0.0.1:{cfg['control_port']}/command",
            'origin':f"http://localhost:{cfg['port']}",'home_path':'/index.html','min_images':2}
        run=BrowserExecution(candidate,config,['goto','reload'])
        start=time.monotonic()
        with pytest.raises((OSError,ValueError)):
            run.call('goto',['/index.html'])
        ref=run.seal();doc=read_browser_execution(candidate,ref,require_passed=False)
        assert time.monotonic()-start<33
        failed=doc['stages'][0]
        assert failed['state']=='unknown' and doc['stages'][1]['state']=='unentered'
        assert failed['termination']==('cleanup_unconfirmed' if ack_lost else 'stopped_confirmed')
        refused=command(candidate,'goto','/index.html')
        assert refused.returncode==1 and 'browser_channel_poisoned' in refused.stderr


@real_surface
def test_v2_driver_rejects_late_success_when_event_loop_blocks(real_config,tmp_path):
    from omc_workflow_preflight import BROWSER_CONTRACT
    surface=tmp_path/'late';surface.mkdir();(surface/'index.html').write_text('<body>fixture</body>')
    preload=tmp_path/'busy-event-loop.cjs'
    runtime=str(real_config[2].parent/'runtime.json')
    preload.write_text("const fs=require('fs'),read=fs.readFileSync;fs.readFileSync=function(p,...a){if(String(p)==="
        +json.dumps(runtime)+"){const end=performance.now()+40;while(performance.now()<end){}}return read.call(this,p,...a);};")
    with browser(real_config,surface,preload=preload) as (cfg,candidate):
        payload={'op':'status','args':[],'operation_id':'a'*32,'contract':BROWSER_CONTRACT,'work_timeout_ms':10}
        req=urllib.request.Request(f"http://127.0.0.1:{cfg['control_port']}/command",
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req,timeout=20)
        body=json.loads(caught.value.read())
        assert body['error_type']=='TimeoutError'
        assert body['operation']['state']=='unknown'
        assert body['operation']['termination']=='stopped_confirmed'


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
@pytest.mark.parametrize('query_name',['home','controlled'])
@pytest.mark.parametrize('query_selector',['id','frozen-text'])
@pytest.mark.parametrize('replace_at',[1,2])
def test_browser_evaluates_only_the_validated_query_buffer(real_config,tmp_path,query_name,query_selector,replace_at):
    """Replace the owned file at a deterministic real read boundary, not the driver."""
    from omc_workflow_preflight import BROWSER_QUERY
    from omc_home_observation import sample_assessment
    surface=tmp_path/'surface';surface.mkdir()
    (surface/'index.html').write_text('''<body><div data-testid="ad-card-large"><div data-testid="ad-track-card-large"><img><img></div></div>
<script>document.querySelectorAll('img').forEach(i=>i.src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'));
let n=0;setInterval(()=>{document.querySelector('[data-testid=ad-track-card-large]').style.transform='translateX('+((++n%2)*-100)+'px)'},900);</script>''')
    if query_name=='controlled':
        controlled_surface(surface,'normal')
    candidate=real_config[2];query=candidate.parent/'queries'/(query_name+'.js')
    armed=tmp_path/'armed';replaced=tmp_path/'replaced.json';preload=tmp_path/'concurrent-query-replacement.cjs'
    payload='(()=>{document.body.textContent="UNVALIDATED_QUERY_EXECUTED";return {unvalidated_query_executed:true}})()'
    preload.write_text("const fs=require('fs');const read=fs.readFileSync;let reads=0;fs.readFileSync=function(file,...args){const data=read.call(this,file,...args);if(String(file)==="+json.dumps(str(query))+"&&fs.existsSync("+json.dumps(str(armed))+")){if(++reads==="+str(replace_at)+"){fs.writeFileSync(file,"+json.dumps(payload)+");fs.writeFileSync("+json.dumps(str(replaced))+",JSON.stringify({reads}));}}return data;};")
    with browser(real_config,surface,preload=preload):
        assert command(candidate,'goto','/omc-ad-validation/index.html' if query_name=='controlled' else '/index.html').returncode==0
        pinned=json.loads((candidate.parent/'runtime.json').read_text())['queries'][query_name]['sha256']
        armed.write_text('replace after the selected query read returns its old bytes')
        result=command(candidate,'evaluate',query_name if query_selector=='id' else frozen_query(query_name))
        assert json.loads(replaced.read_text())['reads']==replace_at
        assert sha(query)!=pinned  # The replacement really occurred in both branches.
        if replace_at==1:
            assert result.returncode==1 and 'query_not_allowlisted' in result.stderr
        else:
            assert result.returncode==0,result.stdout+result.stderr
            observed=json.loads(result.stdout)
            assert observed.get('unvalidated_query_executed') is not True
            if query_name=='home':
                assert sample_assessment(observed)=={'status':'passed','reason':None}
            else:
                assert len(observed['variants'])==6 and observed['transitioned'] is True
                assert observed['arrows']==0 and all(r['images']==2 for r in observed['variants'])
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


QUERY_PINS = {
    'home': '6b6c3ab4e82beefd0c1b48f49087d6ded68f24e88dfd0e4c4e76ec9b25ea03da',
    'controlled': '78dd0132daeeaa51302f701e87801c84dabfee6df813d5b44dd79d5774fd5d09',
}


def frozen_query(name):
    file,symbol = {'home':('omc_workflow_preflight.py','BROWSER_QUERY'),
                   'controlled':('omc_validation_command.py','CONTROLLED_QUERY')}[name]
    raw=subprocess.check_output(['git','-C',str(ROOT),'show',CORE+':scripts/'+file])
    tree=ast.parse(raw)
    node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(
        isinstance(t,ast.Name) and t.id==symbol for t in n.targets))
    value=ast.literal_eval(node.value)
    assert hashlib.sha256(value.encode('utf8')).hexdigest()==QUERY_PINS[name]
    return value


def add_controlled(config):
    path,cfg,candidate=config
    query=path.parent/'controlled-query.js';query.write_bytes(frozen_query('controlled').encode('utf8'))
    cfg['queries']['controlled']={'path':str(query),'sha256':sha(query)}
    path.write_text(json.dumps(cfg))
    return config


def test_frozen_two_query_allowlist_restores_original_bytes(config):
    path,cfg,candidate=add_controlled(config)
    manifest=json.loads((ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json').read_text())
    assert manifest['queries']==QUERY_PINS
    result=invoke('restore','--config',path)
    assert result.returncode==0,result.stdout+result.stderr
    runtime=json.loads((candidate.parent/'runtime.json').read_text())
    assert set(runtime['queries'])==set(QUERY_PINS)
    for name,pin in QUERY_PINS.items():
        assert Path(runtime['queries'][name]['path']).read_bytes()==frozen_query(name).encode('utf8')
        assert runtime['queries'][name]['sha256']==pin


@pytest.mark.parametrize('defect',['missing-controlled','extra-query','wrong-pin','changed-bytes'])
def test_two_query_allowlist_rejects_missing_extra_or_changed(config,defect):
    path,cfg,candidate=add_controlled(config)
    if defect=='missing-controlled':del cfg['queries']['controlled']
    elif defect=='extra-query':cfg['queries']['arbitrary']=dict(cfg['queries']['controlled'])
    elif defect=='wrong-pin':cfg['queries']['controlled']['sha256']='0'*64
    else:Path(cfg['queries']['controlled']['path']).write_text('document.body.remove()')
    path.write_text(json.dumps(cfg))
    result=invoke('restore','--config',path)
    assert result.returncode==1,result.stdout+result.stderr
    assert not (candidate.parent/'runtime.json').exists()
    assert not (candidate.parent/'browser.cjs').exists()


def controlled_surface(directory,scenario):
    page=directory/'omc-ad-validation';page.mkdir(parents=True)
    # Test-owned observable surfaces, not production Ad or a saved oracle response.
    js="""const scenario=SCENARIO;
const variants=['card-large','card-medium','card-small','banner-small','banner-large','banner-landing'];
for(const v of variants){const e=document.createElement('div');e.dataset.testid='ad-'+v;
e.style.border='1px solid rgba(0,0,0,.1)';e.style.boxShadow='0px 1px 2px 0px rgba(0,0,0,.05)';
const track=document.createElement('div');track.dataset.testid='ad-track-'+v;
for(let i=0;i<2;i++){const img=document.createElement('img');if(!(scenario==='missing-image'&&v==='card-large'&&i===1))
img.src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>');track.append(img);}
e.append(track);document.body.append(e);}
if(scenario==='border')document.querySelector('[data-testid="ad-card-large"]').style.border='3px solid red';
if(scenario==='arrows'){const a=document.createElement('button');a.setAttribute('aria-label','다음 광고');document.body.append(a);}
let n=0;setInterval(()=>{if(scenario==='stationary')return;document.querySelectorAll('[data-testid^="ad-track"]').forEach(t=>t.style.transform='translateX('+((++n)*-10)+'px)');},800);
""".replace('SCENARIO',json.dumps(scenario))
    (page/'bundle.js').write_text(js)
    (page/'index.html').write_text('<meta charset="utf-8"><body><script src="bundle.js"></script>')
    return page


@pytest.fixture(scope='session',params=[CORE,'7bdbcb81882ef8ce515e48cf9b4fac07fd273176',CURRENT_CORE],
                ids=['core996d80d','core7bdbcb8','core063'])
def fixed_core(tmp_path_factory,request):
    selected=request.param
    core=tmp_path_factory.mktemp('controlled-core')
    archive=subprocess.check_output(['git','-C',str(ROOT),'archive',selected])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            name=Path(member.name)
            assert not name.is_absolute() and '..' not in name.parts
            assert member.isfile() or member.isdir(), 'fixed core archive must not contain links'
            if member.isfile():
                dest=core/name;dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(tar.extractfile(member).read())
                dest.chmod(member.mode)
    expected=subprocess.check_output(['git','-C',str(ROOT),'show',selected+':scripts/omc_validation_command.py'])
    assert (core/'scripts/omc_validation_command.py').read_bytes()==expected
    core.with_suffix('.selected-core').write_text(selected)
    return core


def controlled_cli(core,candidate,*args):
    result=subprocess.run([sys.executable,str(core/'scripts/omc_validation_command.py'),*args,
        '--target',str(candidate)],capture_output=True,text=True,timeout=40,
        env={**os.environ,'PYTHONPATH':str(core/'scripts'),'PYTHONDONTWRITEBYTECODE':'1'})
    return result


def freeze_controlled_policy(core,cfg,candidate):
    subprocess.run(['git','-c','init.templateDir=','init','-q',str(candidate)],check=True)
    (candidate/'.gitignore').write_text('.omc/\nnode_modules\n__pycache__/\napps/store-manager/public/omc-ad-*\n')
    subprocess.run(['git','-C',str(candidate),'add','.'],check=True)
    local=candidate/'.omc/controlled-test';local.mkdir(parents=True)
    baseline=local/'baseline.json';baseline.write_text('{"numFailedTests":0,"testResults":[]}')
    deps=Path(cfg['baseline_repo'])/'node_modules'
    paths={'node':Path(shutil.which('node')).resolve(),'python':Path(sys.executable).resolve(),
        'jest':(deps/'jest/bin/jest.js').resolve(),'eslint':(deps/'eslint/bin/eslint.js').resolve(),
        'baseline_tests':baseline,'runner':core/'scripts/omc_validation_command.py',
        'visual_checker':core/'scripts/omc_visual_check.py',
        'controlled_builder':candidate.parent/'build-controlled.cjs',
        'pixel_builder':candidate/'.omc/visual-input/build-pixels.cjs'}
    assert all(p.is_file() for p in paths.values()),paths
    proposal={'schema_version':'omc-validation-policy/v1','contract_id':'advertising-comparison/v2',
        'run_id':'controlled-surface','arm':'omc-kit','target':str(candidate),'timeout_sec':30,
        'tools':{name:{'path':str(path),'sha256':sha(path)} for name,path in paths.items()},
        'browser':{'control_url':f"http://127.0.0.1:{cfg['control_port']}/command",
            'origin':f"http://localhost:{cfg['port']}",'home_path':'/sixshoptest01','min_images':2}}
    path=local/'proposal.json';path.write_text(json.dumps(proposal))
    result=controlled_cli(core,candidate,'freeze','--file',str(path),'--sha256',sha(path))
    assert result.returncode==0,result.stdout+result.stderr
    return candidate/'.omc/validation-commands/policy.json'


@real_surface
@pytest.mark.parametrize('scenario,expected,code',[
    ('normal','passed',0),('border','failed',1),('arrows','failed',1),
    ('missing-image','unknown',2),('stationary','unknown',2)])
def test_fixed_core_controlled_public_cli_observes_real_surface(real_config,tmp_path,fixed_core,scenario,expected,code):
    selected=fixed_core.with_suffix('.selected-core').read_text()
    config,cfg,candidate=real_config;cfg['core_commit']=selected;config.write_text(json.dumps(cfg))
    surface=tmp_path/'controlled';page=controlled_surface(surface,scenario)
    with browser(real_config,surface) as (cfg,candidate):
        policy=freeze_controlled_policy(fixed_core,cfg,candidate)
        result=controlled_cli(fixed_core,candidate,'run','--profile','controlled')
        (candidate.parent/'controlled-native-result.json').write_text(json.dumps({
            'scenario':scenario,'core_commit':selected,'policy_sha256':sha(policy),
            'query_sha256':QUERY_PINS['controlled'],'exit_code':result.returncode,
            'stdout':result.stdout,'stderr':result.stderr},ensure_ascii=False,indent=2))
        assert result.returncode==code,result.stdout+result.stderr
        value=json.loads(result.stdout)
        assert value['status']==expected,value
        ref=value['receipt'];receipt_file=candidate/ref['path'];assert sha(receipt_file)==ref['sha256']
        receipt=json.loads(receipt_file.read_text())
        assert receipt['profile']=='controlled' and receipt['policy_sha256']==sha(policy)
        assert receipt['before']==receipt['after']
        assert receipt['judgment']==({'status':'unknown','reason':'controlled_surface_not_observed'}
            if expected=='unknown' else {'status':expected}),receipt
        obs_ref=receipt['artifacts']['observation'];obs_file=candidate/obs_ref['path']
        assert sha(obs_file)==obs_ref['sha256']
        obs=json.loads(obs_file.read_text());assert obs['url']==f"http://localhost:{cfg['port']}/omc-ad-validation/index.html"
        assert len(obs['variants'])==6 and len({x['variant'] for x in obs['variants']})==6
        assert obs['transitioned']==(scenario!='stationary')
        assert obs['arrows']==(1 if scenario=='arrows' else 0)
        assert all(r['images']==(1 if scenario=='missing-image' and r['variant']=='card-large' else 2) for r in obs['variants'])
        screen=receipt['artifacts']['screen'];assert sha(candidate/screen['path'])==screen['sha256']
        snap=json.loads(command(candidate,'snapshot').stdout)
        assert snap['bundle_hashes'][f"http://localhost:{cfg['port']}/omc-ad-validation/bundle.js"]==sha(page/'bundle.js')
        assert snap['url']==obs['url'] and snap['candidate']==str(candidate)
        (candidate.parent/'controlled-final-snapshot.json').write_text(json.dumps(snap,ensure_ascii=False))


@pytest.mark.parametrize('defect',['wrong-cwd','missing-cwd','relative-cwd','missing-environment',
    'wrong-ci','missing-cache','invalid-environment'])
@pytest.mark.parametrize('check_id',['tests','lint','build-controlled','build-pixels','controlled','pixels-dpr1','pixels-dpr2'])
def test_review_common_result_rejects_execution_context_drift(tmp_path,monkeypatch,defect,check_id):
    import omc_ad_validation_runtime as runtime
    definition={'id':check_id,'argv':['/fixed/node','lint'],
                'cwd':str(tmp_path.resolve()),'environment':{'CI':'1','NX_DAEMON':'false','NX_SKIP_NX_CACHE':'true'}}
    # Fixture only replaces unrelated pinned inputs; call the real shared consumer.
    monkeypatch.setattr(runtime,'render_common_contract',lambda _: {'checks':[definition],'fixed_files':{}})
    record={'timed_out':False,'bindings':{'CANDIDATE_ROOT':str(tmp_path.resolve())},
            'argv':definition['argv'],'cwd':definition['cwd'],'environment':dict(definition['environment']),
            'exit_code':0,'artifacts':{}}
    if defect=='wrong-cwd':record['cwd']=str(tmp_path/'other-candidate')
    elif defect=='missing-cwd':record.pop('cwd')
    elif defect=='relative-cwd':record['cwd']='.'
    elif defect=='missing-environment':record.pop('environment')
    elif defect=='wrong-ci':record['environment']['CI']='0'
    elif defect=='missing-cache':record['environment'].pop('NX_SKIP_NX_CACHE')
    else:record['environment']=[]
    with pytest.raises(ValueError,match='common_execution_context_mismatch'):
        runtime.assess_common_result(tmp_path,check_id,record)


def test_review_common_result_accepts_exact_context_with_extra_environment(tmp_path,monkeypatch):
    import omc_ad_validation_runtime as runtime
    definition={'id':'lint','argv':['/fixed/node','lint'],'cwd':str(tmp_path.resolve()),
                'environment':{'CI':'1','NX_DAEMON':'false','NX_SKIP_NX_CACHE':'true'}}
    monkeypatch.setattr(runtime,'render_common_contract',lambda _: {'checks':[definition],'fixed_files':{}})
    record={'timed_out':False,'bindings':{'CANDIDATE_ROOT':str(tmp_path.resolve())},
            'argv':definition['argv'],'cwd':definition['cwd'],
            'environment':{**definition['environment'],'PATH':'/synthetic/bin'},'exit_code':0,'artifacts':{}}
    assert runtime.assess_common_result(tmp_path,'lint',record)=='passed'


@real_surface
def test_v4_063_seven_real_checks_and_reassessment(real_config,tmp_path):
    """Execute all seven against unchanged real baseline; defects stay failures."""
    import omc_ad_validation_runtime as runtime
    path,cfg,candidate=real_config
    cfg['core_commit']=CURRENT_CORE;path.write_text(json.dumps(cfg))
    core=tmp_path/'current-product';core.mkdir()
    for repo,commit,dest,prefix in [(ROOT,CURRENT_CORE,core,'scripts')]:
        raw=subprocess.check_output(['git','-C',str(repo),'archive',commit,prefix])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                name=Path(member.name)
                assert not name.is_absolute() and '..' not in name.parts
                if member.isfile():
                    file=dest/name;file.parent.mkdir(parents=True,exist_ok=True)
                    file.write_bytes(archive.extractfile(member).read());file.chmod(member.mode)
    assert sha(core/'scripts/omc_visual_check.py')==hashlib.sha256(subprocess.check_output(
        ['git','-C',str(ROOT),'show',CURRENT_CORE+':scripts/omc_visual_check.py'])).hexdigest()
    results=[]
    with browser(real_config,candidate/'apps/store-manager/public') as (cfg,candidate):
        # Supply existing baseline configs/dependencies, never copy current dirty work.
        raw=subprocess.check_output(['git','-C',cfg['baseline_repo'],'archive',BASE])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                name=Path(member.name)
                assert not name.is_absolute() and '..' not in name.parts
                file=candidate/name
                if member.isfile() and not file.exists():
                    file.parent.mkdir(parents=True,exist_ok=True)
                    file.write_bytes(archive.extractfile(member).read());file.chmod(member.mode)
        output=candidate/'.omc/standalone-final';output.mkdir(parents=True)
        policy=json.loads((ROOT/'docs/comparison-dashboard/evidence/ad-validation-inputs-v1/manifest.json').read_bytes())['visual_policy']
        requirement={'profile':'advertising-visual/v1','run_id':'compat-063-seven','policy':policy}
        (candidate/'.omc/visual-requirement.json').write_text(json.dumps(requirement))
        bindings={'CANDIDATE_ROOT':str(candidate),'ARM_ROOT':str(candidate.parent),
            'OPERATOR_ROOT':str(ROOT),'CORE_ROOT':str(core),'NODE':str(Path(shutil.which('node')).resolve()),
            'PYTHON':str(Path(sys.executable).resolve()),'PORT':cfg['port']}
        contract=runtime.render_common_contract(bindings)
        (output/'contract.json').write_text(json.dumps(contract,indent=2))
        source_pins={str(f.relative_to(candidate)):sha(f) for f in (candidate/AD).rglob('*') if f.is_file()}
        def ref(file):
            return {'path':str(file.relative_to(candidate)),'sha256':sha(file)}
        for definition in contract['checks']:
            check=definition['id'];argv=list(definition['argv']);artifacts={}
            if check=='controlled':
                navigation=command(candidate,'goto','/omc-ad-validation/index.html')
                (output/'controlled-navigation.stdout').write_text(navigation.stdout)
                (output/'controlled-navigation.stderr').write_text(navigation.stderr)
            if check.startswith('pixels-dpr'):
                dpr=int(check[-1]);bundle=candidate/'apps/store-manager/public/omc-ad-pixels/bundle.js'
                manifest={'schema_version':'omc-visual-manifest/v1',
                    'url':f"http://localhost:{cfg['port']}/omc-ad-pixels/index.html",
                    'bundle_url':f"http://localhost:{cfg['port']}/omc-ad-pixels/bundle.js",
                    'bundle_path':str(bundle.relative_to(candidate)),'bundle_sha256':sha(bundle),
                    'build_inputs':policy['builder_inputs'],'dpr':dpr,'images_per_variant':2,
                    'run_id':requirement['run_id'],
                    'policy_sha256':hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
                file=output/f'manifest-{dpr}.json';file.write_text(json.dumps(manifest,indent=2))
                argv=[v.replace(f'@MANIFEST_{dpr}_SHA256@',sha(file)) for v in argv]
                artifacts['manifest']=ref(file)
            start=time.monotonic();timed_out=False
            try:
                proc=subprocess.run(argv,cwd=candidate,env={**os.environ,**definition['environment']},
                    capture_output=True,text=True,timeout=definition['timeout_seconds'])
                stdout,stderr,code=proc.stdout,proc.stderr,proc.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out=True;stdout=(exc.stdout or b'');stderr=(exc.stderr or b'');code=None
                stdout=stdout.decode(errors='replace') if isinstance(stdout,bytes) else stdout
                stderr=stderr.decode(errors='replace') if isinstance(stderr,bytes) else stderr
            Path(definition['stdout']).write_text(stdout);Path(definition['stderr']).write_text(stderr)
            artifacts['stdout']=ref(Path(definition['stdout']));artifacts['stderr']=ref(Path(definition['stderr']))
            if check=='tests' and (output/'tests.json').exists():artifacts['jest']=ref(output/'tests.json')
            if check.startswith('build-'):
                folder='omc-ad-validation' if check=='build-controlled' else 'omc-ad-pixels'
                bundle=candidate/f'apps/store-manager/public/{folder}/bundle.js'
                if bundle.exists():artifacts['bundle']=ref(bundle)
            if check=='controlled' and code==0:
                observed=json.loads(stdout)
                if 'operation' in observed:observed=observed['result']
                file=output/'controlled-observation.json';file.write_text(json.dumps(observed))
                artifacts['observation']=ref(file)
                snap=command(candidate,'snapshot');(output/'controlled-snapshot.json').write_text(snap.stdout)
                if snap.returncode==0:artifacts['screen']=ref(Path(json.loads(snap.stdout)['screenshot']))
            if check.startswith('pixels-dpr'):
                file=output/f'pixels-{check[-1]}/report.json'
                if file.exists():artifacts['report']=ref(file)
            record={'check_id':check,'core_commit':CURRENT_CORE,'source_sha256':source_pins,
                'bindings':bindings,'cwd':str(candidate),'environment':definition['environment'],
                'argv':argv,'exit_code':code,'timed_out':timed_out,'started_monotonic':start,
                'ended_monotonic':time.monotonic(),'artifacts':artifacts}
            file=Path(definition['result']);file.write_text(json.dumps(record,indent=2))
            # Reload saved native evidence; no declared success can substitute for it.
            assessment=_assert_required_common_evidence(candidate,check,json.loads(file.read_bytes()))
            expected=('controlled_surface_not_passed' if check=='controlled' else
                'common_command_failed' if check.startswith('pixels-dpr') else 'passed')
            row={'id':check,'record':ref(file),'assessment':assessment,'expected':expected,
                 'matched':assessment==expected,'native_exit':code}
            results.append(row)
            (output/'reassessment.json').write_text(json.dumps(results,indent=2))
        assert [r['id'] for r in results]==[r['id'] for r in contract['checks']]
        assert all(r['matched'] for r in results),results
        assert source_pins=={str(f.relative_to(candidate)):sha(f) for f in (candidate/AD).rglob('*') if f.is_file()}
        for row in results:
            saved=json.loads((candidate/row['record']['path']).read_bytes())
            assert sha(candidate/row['record']['path'])==row['record']['sha256']
            for item in saved['artifacts'].values():assert sha(candidate/item['path'])==item['sha256']


_REQUIRED_COMMON_ARTIFACTS = {
    'tests': {'stdout','stderr','jest'},
    'lint': {'stdout','stderr'},
    'build-controlled': {'stdout','stderr','bundle'},
    'build-pixels': {'stdout','stderr','bundle'},
    'controlled': {'stdout','stderr','observation','screen'},
    'pixels-dpr1': {'stdout','stderr','manifest','report'},
    'pixels-dpr2': {'stdout','stderr','manifest','report'},
}


def _assert_required_common_evidence(candidate,check,record):
    """Test-only complete evidence gate, including expected baseline failures."""
    import omc_ad_validation_runtime as runtime
    import omc_visual_check as visual
    candidate=candidate.resolve()
    assert record['timed_out'] is False
    assert type(record['exit_code']) is int
    assert record['core_commit']==CURRENT_CORE
    assert record['cwd']==str(candidate)
    assert record['bindings']['CANDIDATE_ROOT']==str(candidate)
    assert record['source_sha256']=={str(f.relative_to(candidate)):sha(f)
        for f in (candidate/AD).rglob('*') if f.is_file()}
    artifacts=record['artifacts']
    assert _REQUIRED_COMMON_ARTIFACTS[check]<=artifacts.keys(), 'required_artifact_missing'
    for item in artifacts.values():
        file=runtime.child(candidate,item['path'])
        assert file.is_file() and sha(file)==item['sha256'], 'required_artifact_invalid'
    if check.startswith('pixels-dpr'):
        report_ref=artifacts['report'];manifest_ref=artifacts['manifest']
        report_file=runtime.child(candidate,report_ref['path'])
        manifest_file=runtime.child(candidate,manifest_ref['path'])
        report=json.loads(report_file.read_bytes());manifest=json.loads(manifest_file.read_bytes())
        requirement=json.loads((candidate/'.omc/visual-requirement.json').read_bytes())
        policy=requirement['policy']
        assert record['exit_code']==1, 'baseline_failure_exit_required'
        assert report['schema_version']=='omc-visual-report/v1'
        assert report['target']==str(candidate), 'pixel_candidate_mismatch'
        assert report['code_snapshot_sha256']==visual._code_snapshot_digest(candidate), 'pixel_snapshot_mismatch'
        assert report['manifest_sha256']==manifest_ref['sha256']
        assert report['manifest_raw'].encode()==manifest_file.read_bytes()
        assert type(manifest['dpr']) is int and manifest['dpr']==int(check[-1])
        assert manifest['run_id']==requirement['run_id']
        assert manifest['policy_sha256']==hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert manifest['images_per_variant']==policy['images_per_variant']==2
        assert manifest['build_inputs']==policy['builder_inputs']
        for name,pin in manifest['build_inputs'].items():assert sha(runtime.child(candidate,name))==pin
        assert sha(runtime.child(candidate,manifest['bundle_path']))==manifest['bundle_sha256']
        rows=report['variants']
        assert len(rows)==6 and [r['id'] for r in rows]==list(visual.VARIANTS), 'six_unique_cells_required'
        assert all(r['status'] in {'passed','failed'} for r in rows)
        assert report['status']=='failed' and any(r['status']=='failed' for r in rows)
        assert set(report['evidence_hashes'])=={'screen.png','capture.json'}
        for name,pin in report['evidence_hashes'].items():
            file=report_file.parent/name
            assert file.is_file() and sha(file)==pin, 'pixel_capture_missing_or_changed'
        judged=visual.assess_report(candidate,report_file,report_ref['sha256'],manifest_ref['sha256'])
        assert judged=={'status':'failed','reason':'visual_surface_failed'}, judged
    try:assessment=runtime.assess_common_result(candidate,check,record)
    except (ValueError,KeyError,OSError) as exc:assessment=str(exc)
    expected=('controlled_surface_not_passed' if check=='controlled' else
        'common_command_failed' if check.startswith('pixels-dpr') else 'passed')
    assert assessment==expected
    return assessment


@pytest.fixture
def common_evidence_record(tmp_path,monkeypatch):
    import omc_ad_validation_runtime as runtime
    import omc_visual_check as visual
    candidate=tmp_path/'candidate';candidate.mkdir()
    source=candidate/AD/'Ad.tsx';source.parent.mkdir(parents=True);source.write_text('baseline')
    folder=candidate/'.omc/required';folder.mkdir(parents=True)
    builder=folder/'builder';builder.write_text('fixed builder')
    bundle=candidate/'bundle';bundle.write_text('fixed bundle')
    policy={'schema_version':'omc-visual-policy/v1','criteria':visual.CRITERIA,
        'variants':list(visual.VARIANTS),'dprs':[1,2],'images_per_variant':2,
        'builder_inputs':{str(builder.relative_to(candidate)):sha(builder)}}
    requirement={'profile':visual.AD_PROFILE,'run_id':'synthetic-unit','policy':policy}
    (candidate/'.omc/visual-requirement.json').write_text(json.dumps(requirement))
    manifest={'dpr':1,'run_id':requirement['run_id'],
        'policy_sha256':hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'images_per_variant':2,'build_inputs':policy['builder_inputs'],
        'bundle_path':str(bundle.relative_to(candidate)),'bundle_sha256':sha(bundle)}
    manifest_file=folder/'manifest.json';manifest_file.write_text(json.dumps(manifest))
    for name in ['screen.png','capture.json']:(folder/name).write_text('synthetic; real judge is separately exercised')
    report={'schema_version':'omc-visual-report/v1','target':str(candidate),
        'code_snapshot_sha256':visual._code_snapshot_digest(candidate),'criteria':visual.CRITERIA,
        'manifest_sha256':sha(manifest_file),'manifest_raw':manifest_file.read_text(),
        'evidence_hashes':{n:sha(folder/n) for n in ['screen.png','capture.json']},
        'status':'failed','variants':[{'id':n,'status':'failed'} for n in visual.VARIANTS]}
    report_file=folder/'report.json';report_file.write_text(json.dumps(report))
    paths={'manifest':manifest_file,'report':report_file,'bundle':bundle,'screen':folder/'screen.png'}
    for name in ['stdout','stderr','jest','observation']:
        paths[name]=folder/name;paths[name].write_text('{}')
    refs={n:{'path':str(f.relative_to(candidate)),'sha256':sha(f)} for n,f in paths.items()}
    record={'artifacts':refs,'exit_code':1,'timed_out':False,'core_commit':CURRENT_CORE,
        'cwd':str(candidate),'bindings':{'CANDIDATE_ROOT':str(candidate)},
        'source_sha256':{str(source.relative_to(candidate)):sha(source)}}
    def assess(root,check,value):
        if check=='controlled':raise ValueError('controlled_surface_not_passed')
        if check.startswith('pixels-dpr'):raise ValueError('common_command_failed')
        return 'passed'
    monkeypatch.setattr(runtime,'assess_common_result',assess)
    monkeypatch.setattr(visual,'assess_report',lambda *args: {'status':'failed','reason':'visual_surface_failed'})
    return candidate,record


@pytest.mark.parametrize('check,missing',[(c,n) for c,keys in _REQUIRED_COMMON_ARTIFACTS.items() for n in sorted(keys)])
def test_required_common_evidence_rejects_missing_artifact(common_evidence_record,check,missing):
    candidate,record=common_evidence_record
    record['artifacts'].pop(missing)
    with pytest.raises(AssertionError):_assert_required_common_evidence(candidate,check,record)


@pytest.mark.parametrize('check',list(_REQUIRED_COMMON_ARTIFACTS))
def test_required_common_evidence_accepts_complete_record(common_evidence_record,check):
    candidate,record=common_evidence_record
    record['exit_code']=1 if check=='tests' or check.startswith('pixels-dpr') else 0
    if check=='pixels-dpr2':
        file=candidate/record['artifacts']['manifest']['path'];manifest=json.loads(file.read_text())
        manifest['dpr']=2;file.write_text(json.dumps(manifest))
        record['artifacts']['manifest']['sha256']=sha(file)
        file=candidate/record['artifacts']['report']['path'];report=json.loads(file.read_text())
        report['manifest_raw']=json.dumps(manifest);report['manifest_sha256']=record['artifacts']['manifest']['sha256']
        file.write_text(json.dumps(report));record['artifacts']['report']['sha256']=sha(file)
    _assert_required_common_evidence(candidate,check,record)


@pytest.mark.parametrize('defect',['missing-file','wrong-hash','five-cells','duplicate-cell','wrong-target',
    'wrong-snapshot','wrong-dpr','wrong-run','wrong-policy','missing-pixel-screen','unknown-judge','passed-judge'])
def test_required_pixel_evidence_rejects_incomplete_or_unbound(common_evidence_record,monkeypatch,defect):
    import omc_visual_check as visual
    candidate,record=common_evidence_record
    file=candidate/record['artifacts']['report']['path'];report=json.loads(file.read_text())
    if defect=='missing-file':file.unlink()
    elif defect=='wrong-hash':record['artifacts']['report']['sha256']='0'*64
    elif defect=='five-cells':report['variants'].pop()
    elif defect=='duplicate-cell':report['variants'][-1]=report['variants'][0]
    elif defect=='wrong-target':report['target']=str(candidate/'other')
    elif defect=='wrong-snapshot':report['code_snapshot_sha256']='0'*64
    elif defect in ['wrong-dpr','wrong-run','wrong-policy']:
        manifest=json.loads(report['manifest_raw'])
        key={'wrong-dpr':'dpr','wrong-run':'run_id','wrong-policy':'policy_sha256'}[defect]
        manifest[key]=2 if key=='dpr' else 'wrong';report['manifest_raw']=json.dumps(manifest)
        manifest_file=candidate/record['artifacts']['manifest']['path'];manifest_file.write_text(report['manifest_raw'])
        record['artifacts']['manifest']['sha256']=sha(manifest_file);report['manifest_sha256']=sha(manifest_file)
    elif defect=='missing-pixel-screen':(file.parent/'screen.png').unlink()
    elif defect in ['unknown-judge','passed-judge']:
        monkeypatch.setattr(visual,'assess_report',lambda *args: {'status':'unknown' if defect=='unknown-judge' else 'passed'})
    if defect not in ['missing-file','wrong-hash']:
        file.write_text(json.dumps(report));record['artifacts']['report']['sha256']=sha(file)
    with pytest.raises(AssertionError):_assert_required_common_evidence(candidate,'pixels-dpr1',record)
