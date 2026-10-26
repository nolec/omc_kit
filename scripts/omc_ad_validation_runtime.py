#!/usr/bin/env python3
"""Restore a fresh, bounded Ad inspection runtime; never start a provider."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from omc_workflow_preflight import BROWSER_CONTRACT, browser_contract

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json'
BASELINE = '34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc'
CORE = '996d80d539d7245443f3a539f1a9fa333b450770'
NEW_CORE = '7bdbcb81882ef8ce515e48cf9b4fac07fd273176'
SUPPORTED_CORES = (CORE, NEW_CORE)
COMMON = ROOT/'docs/comparison-dashboard/evidence/ad-standalone-contract-v1/common-input.template.json'
HOME = 'libs/store-manager/components/src/lib/home/Home.tsx'
AD = 'libs/sellerhub-store/src/lib/components/Ad'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def write_validated(path, data, pin, *, overwrite=False):
    """Publish the same bytes that passed validation, never reread an input."""
    if hashlib.sha256(data).hexdigest() != pin:
        raise ValueError('validated_bytes_mismatch')
    with path.open('wb' if overwrite else 'xb') as stream:
        stream.write(data)
    if digest(path) != pin:
        raise ValueError('restored_digest_mismatch')


def no_links(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('path_link_refused')
    return path


def child(root, relative):
    path = no_links(root/relative)
    path.resolve().relative_to(root.resolve())
    return path


def load_config(path):
    raw = Path(path).read_bytes()
    cfg = json.loads(raw)
    required = {'schema_version','run_root','candidate','baseline_repo','baseline_commit',
                'core_commit','port','control_port','headless','playwright_module',
                'playwright_package_sha256','queries'}
    if set(cfg) != required or cfg['schema_version'] != 'omc-ad-runtime-config/v1':
        raise ValueError('runtime_config_invalid')
    if cfg['baseline_commit'] != BASELINE or cfg['core_commit'] not in SUPPORTED_CORES:
        raise ValueError('candidate_version_mismatch')
    if type(cfg['headless']) is not bool:
        raise ValueError('headless_must_be_boolean')
    for key in ['run_root','candidate','baseline_repo','playwright_module']:
        if not Path(cfg[key]).is_absolute():
            raise ValueError('absolute_path_required: '+key)
    run = no_links(cfg['run_root']).resolve(strict=True)
    candidate = no_links(cfg['candidate']).resolve(strict=True)
    repo = Path(cfg['baseline_repo']).resolve(strict=True)
    if candidate.parent != run or candidate in (ROOT, repo) or run in (ROOT, repo):
        raise ValueError('fresh_isolated_candidate_required')
    for key in ['port','control_port']:
        if type(cfg[key]) is not int or not 1024 <= cfg[key] <= 65535:
            raise ValueError('invalid_port')
    if cfg['port'] == cfg['control_port']:
        raise ValueError('duplicate_port')
    module = Path(cfg['playwright_module']).resolve(strict=True)
    if digest(module/'package.json') != cfg['playwright_package_sha256']:
        raise ValueError('playwright_package_mismatch')
    return cfg, run, candidate, repo, hashlib.sha256(raw).hexdigest()


def restore(config):
    cfg, run, candidate, repo, config_pin = load_config(config)
    if any(p != candidate for p in run.iterdir()):
        raise ValueError('fresh_run_required')
    manifest_raw = MANIFEST.read_bytes()
    manifest = json.loads(manifest_raw)
    browser_contract(manifest.get('browser_evidence_contract'))
    if manifest.get('core_commit') != CORE:
        raise ValueError('manifest_core_mismatch')
    if (manifest.get('archive_core_commit', CORE) != CORE
            or manifest.get('supported_core_commits', [CORE]) != list(SUPPORTED_CORES)):
        raise ValueError('manifest_core_mismatch')
    manifest_pin = hashlib.sha256(manifest_raw).hexdigest()
    sources = []
    for name, entry in manifest['sources'].items():
        source = child(ROOT, name)
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('source_digest_mismatch: '+name)
        sources.append((data, entry))
    if cfg['queries'].keys() != manifest['queries'].keys():
        raise ValueError('query_allowlist_mismatch')
    query_bytes = {}
    for name, query in cfg['queries'].items():
        if set(query) != {'path','sha256'} or not Path(query['path']).is_absolute():
            raise ValueError('query_config_invalid')
        no_links(query['path'])
        data = Path(query['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != query['sha256'] or query['sha256'] != manifest['queries'][name]:
            raise ValueError('query_digest_mismatch')
        query_bytes[name] = data
    # Validate baseline bytes without trusting a caller-supplied identity label.
    names = subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','--name-only',BASELINE,
        *manifest['baseline_paths']], text=True).splitlines()
    if not names or HOME not in names:
        raise ValueError('baseline_source_missing')
    baseline_pins = {}
    for name in names:
        file = child(candidate, name)
        expected = hashlib.sha256(subprocess.check_output(
            ['git','-C',str(repo),'show',BASELINE+':'+name])).hexdigest()
        if digest(file) != expected:
            raise ValueError('existing_work_or_wrong_candidate: '+name)
        baseline_pins[name] = expected
    if (candidate/'.git').exists():
        head = subprocess.check_output(['git','-C',str(candidate),'rev-parse','HEAD'],text=True).strip()
        dirty = subprocess.check_output(['git','-C',str(candidate),'status','--porcelain'],text=True)
        if head != BASELINE or dirty:
            raise ValueError('candidate_not_clean_baseline')
    else:
        # A minimal extraction may contain only the checked baseline and deps link.
        for file in candidate.rglob('*'):
            rel = file.relative_to(candidate)
            if rel.parts[0] == 'node_modules':
                continue
            no_links(file)
            if file.is_file() and rel.as_posix() not in baseline_pins:
                raise ValueError('existing_unregistered_work')
    outputs = [(data, child(candidate if entry['scope']=='candidate' else run, entry['destination']), entry['sha256'])
               for data, entry in sources if entry.get('destination')]
    for _, dest, _ in outputs:
        if dest.exists() and dest != candidate/HOME:
            raise ValueError('refuse_existing_output: '+str(dest))
    for name in ['runtime.json','restore-receipt.json','queries','browser-profile']:
        dest = child(run, name)
        if dest.exists():
            raise ValueError('refuse_existing_runtime')
    # Do not silently reuse a running server or control endpoint.
    for key in ['port','control_port']:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',cfg[key]))
    # Everything above is read-only. Only the declared new run is written below.
    for data, dest, pin in outputs:
        dest.parent.mkdir(parents=True,exist_ok=True)
        write_validated(dest,data,pin,overwrite=dest==candidate/HOME)
    (run/'queries').mkdir()
    runtime = dict(cfg)
    runtime['queries'] = {}
    for name, query in cfg['queries'].items():
        dest = run/'queries'/(name+'.js')
        write_validated(dest,query_bytes[name],query['sha256'])
        runtime['queries'][name] = {'path':str(dest),'sha256':query['sha256']}
    runtime.update(schema_version='omc-ad-runtime/v1', arms={candidate.name:{'port':cfg['port']}},
                   operator_manifest_sha256=manifest_pin, browser_sha256=digest(run/'browser.cjs'))
    runtime['browser_evidence_contract'] = BROWSER_CONTRACT
    write_json(run/'runtime.json',runtime)
    write_json(run/'restore-receipt.json',{
        'schema_version':'omc-ad-restore-receipt/v1','provider_calls':0,
        'execution_core_commit':cfg['core_commit'],'archive_core_commit':CORE,
        'config_sha256':config_pin,'runtime_sha256':digest(run/'runtime.json'),
        'manifest_sha256':manifest_pin,'baseline_inputs':baseline_pins,
        'restored_files':{str(dest.relative_to(run)):pin for _,dest,pin in outputs},
        'matrix_sha256':hashlib.sha256(json.dumps(manifest['acceptance_matrix'],sort_keys=True).encode()).hexdigest(),
        'operator_sources':{name:digest(ROOT/name) for name in
            ['scripts/omc_ad_validation_runtime.py','scripts/omc_ad_validation_browser.cjs']}})
    return {'runtime':str(run/'runtime.json'),'provider_calls':0,'status':'restored_not_service_ready'}


def runtime_config(path):
    path = no_links(path).resolve(strict=True)
    cfg = json.loads(path.read_text())
    if cfg.get('core_commit') not in SUPPORTED_CORES:
        raise ValueError('candidate_version_mismatch')
    browser_contract(cfg.get('browser_evidence_contract'))
    run = Path(cfg['run_root'])
    receipt = json.loads(child(run,'restore-receipt.json').read_text())
    if (path != run/'runtime.json' or receipt['runtime_sha256'] != digest(path)
            or digest(child(run,'browser.cjs')) != cfg['browser_sha256']):
        raise ValueError('runtime_binding_mismatch')
    for name, pin in receipt['restored_files'].items():
        if digest(child(run,name)) != pin:
            raise ValueError('restored_file_mismatch')
    return cfg


def common_contract(path=None):
    """One durable neutral contract; its hash is pinned separately in manifest."""
    path = no_links(path or COMMON)
    entry = json.loads(MANIFEST.read_bytes())['common_contract']
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('common_contract_digest_mismatch')
    value = json.loads(raw)
    expected = ['tests','lint','build-controlled','build-pixels','controlled','pixels-dpr1','pixels-dpr2']
    rows = value['checks']
    if ([row['id'] for row in rows] != expected
            or [row['timeout_seconds'] for row in rows] != [180,120,90,90,90,90,90]
            or value['auxiliary_seconds'] != 30):
        raise ValueError('common_contract_shape_mismatch')
    return value


def render_common_contract(bindings):
    """Render locations only; manifest hashes remain explicit post-build slots."""
    names = {'CANDIDATE_ROOT','ARM_ROOT','OPERATOR_ROOT','CORE_ROOT','NODE','PYTHON','PORT'}
    if set(bindings) != names or type(bindings['PORT']) is not int or not 1024 <= bindings['PORT'] <= 65535:
        raise ValueError('common_bindings_invalid')
    for name in names-{'PORT'}:
        if not isinstance(bindings[name], str) or not Path(bindings[name]).is_absolute():
            raise ValueError('common_absolute_binding_required')
        no_links(bindings[name])
    candidate = Path(bindings['CANDIDATE_ROOT'])
    requirement = child(candidate, '.omc/visual-requirement.json')
    if not requirement.is_file():
        raise ValueError('common_requirement_missing')
    values = {**bindings, 'REQUIREMENT_SHA256':digest(requirement)}
    def render(value):
        if isinstance(value, dict):return {render(k):render(v) for k,v in value.items()}
        if isinstance(value, list):return [render(v) for v in value]
        if not isinstance(value, str):return value
        for key, text in values.items():
            value=value.replace('@'+key+'@',str(text)).replace('{{'+key+'}}',str(text))
        unresolved=re.findall(r'@[A-Z_0-9]+@|\{\{[^}]+\}\}', value)
        if any(x not in ('@MANIFEST_1_SHA256@','@MANIFEST_2_SHA256@') for x in unresolved):
            raise ValueError('common_unresolved_binding')
        return value
    value=render(common_contract())
    # Provenance lists describe original placeholders, not executable strings.
    return value


def assess_common_result(root, check_id, record):
    """Recompute neutral semantics from raw artifacts, never a passed label."""
    if record.get('timed_out') is not False:raise ValueError('execution_timeout_or_missing')
    bindings=record['bindings']
    if bindings['CANDIDATE_ROOT']!=str(root.resolve()):raise ValueError('command_candidate_mismatch')
    contract=render_common_contract(bindings)
    definition=next(x for x in contract['checks'] if x['id']==check_id)
    # Absolute argv does not bind relative configs or tests to the executed cwd.
    environment=record.get('environment')
    if (record.get('cwd')!=definition['cwd'] or not isinstance(environment,dict)
            or any(environment.get(key)!=value for key,value in definition['environment'].items())):
        raise ValueError('common_execution_context_mismatch')
    artifacts=record['artifacts']
    def artifact(name):
        ref=artifacts[name];p=child(root,ref['path'])
        if digest(p)!=ref['sha256']:raise ValueError('artifact_digest_mismatch')
        return p
    expected=list(definition['argv'])
    if check_id.startswith('pixels-dpr'):
        manifest=artifact('manifest')
        expected=[x.replace('@MANIFEST_'+check_id[-1]+'_SHA256@',digest(manifest)) for x in expected]
    if record['argv']!=expected:raise ValueError('common_command_mismatch')
    for name,pin in contract['fixed_files'].items():
        if digest(no_links(name))!=pin:raise ValueError('fixed_input_mismatch')
    code=record['exit_code']
    if type(code) is not int:raise ValueError('exit_not_observed')
    if check_id=='tests':
        from omc_native_red_evidence import jest_cases
        doc=json.loads(artifact('jest').read_bytes());failed=jest_cases(doc,code)
        def diagnostics(message):
            message=message.replace(str(root.resolve()),'<candidate>')
            message=re.sub(r'[^\s(]*?/node_modules/','<deps>/',message)
            message=re.sub(r':\d+:\d+',':LINE:COL',message)
            return re.sub(r'(?m)^(\s*>?\s*)\d+(\s*\|)',r'\1LINE\2',message)
        names=set();current={}
        for suite in doc['testResults']:
            for case in suite['assertionResults']:
                names.add(case['fullName'])
                if case['status']=='failed':current[case['fullName']]=diagnostics('\n'.join(case['failureMessages']))
        base=contract['baseline_jest']
        if not set(base['names'])<=names:raise ValueError('baseline_test_removed')
        if any(name not in base['failed_diagnostics'] or current[name]!=base['failed_diagnostics'][name] for name in failed):
            raise ValueError('new_failure_or_changed_diagnostics')
    elif code!=0:raise ValueError('common_command_failed')
    if check_id.startswith('build-'):
        artifact('bundle')
    if check_id=='controlled':
        from omc_visual_check import VARIANTS
        obs=json.loads(artifact('observation').read_bytes())
        if (obs.get('url')!=f"http://localhost:{bindings['PORT']}/omc-ad-validation/index.html"
                or obs.get('transitioned') is not True or type(obs.get('arrows')) is not int or obs['arrows']!=0
                or not isinstance(obs.get('variants'),list) or len(obs['variants'])!=6
                or {v.get('variant') for v in obs['variants']}!=set(VARIANTS)
                or any(v.get('border')!='1px solid rgba(0, 0, 0, 0.1)'
                    or v.get('shadow')!='rgba(0, 0, 0, 0.05) 0px 1px 2px 0px'
                    or type(v.get('images')) is not int or v['images']<2 for v in obs['variants'])):
            raise ValueError('controlled_surface_not_passed')
    if check_id.startswith('pixels-dpr'):
        from omc_visual_check import assess_report, VARIANTS
        report=artifact('report');data=json.loads(report.read_bytes());manifest=artifact('manifest')
        meta=json.loads(data['manifest_raw']);requirement=json.loads((root/'.omc/visual-requirement.json').read_bytes())
        policy_pin=hashlib.sha256(json.dumps(requirement['policy'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if (meta['dpr']!=int(check_id[-1]) or meta['run_id']!=requirement['run_id']
                or meta['policy_sha256']!=policy_pin or [v['id'] for v in data['variants']]!=list(VARIANTS)
                or assess_report(root,report,digest(report),digest(manifest))['status']!='passed'):
            raise ValueError('pixel_surface_not_passed')
    return 'passed'


def command(runtime, op, args):
    cfg = runtime_config(runtime)
    data = json.dumps({'op':op,'args':args}).encode()
    req = urllib.request.Request('http://127.0.0.1:'+str(cfg['control_port'])+'/command',
                                 data=data,headers={'Content-Type':'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as response:
        return json.loads(response.read())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation',required=True)
    restore_parser = sub.add_parser('restore');restore_parser.add_argument('--config',required=True)
    cmd = sub.add_parser('command');cmd.add_argument('--runtime',required=True)
    cmd.add_argument('--op',required=True);cmd.add_argument('--args-json',default='[]')
    contract = sub.add_parser('contract');contract.add_argument('--bindings', required=True)
    contract.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        if args.operation == 'contract':
            value=render_common_contract(json.loads(Path(args.bindings).read_bytes()))
            write_json(no_links(args.output), value)
            value={'status':'rendered_not_executed','contract':args.output,'provider_calls':0}
        else:
            value = restore(args.config) if args.operation=='restore' else command(args.runtime,args.op,json.loads(args.args_json))
        print(json.dumps(value,ensure_ascii=False));return 0
    except urllib.error.HTTPError as exc:
        print(json.dumps({'error_type':type(exc).__name__,'error':str(exc),
                          'response_body':exc.read().decode('utf-8',errors='replace')},ensure_ascii=False),file=sys.stderr)
        return 1
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError,urllib.error.URLError) as exc:
        # Keep the original error locally; never collect environment/credentials.
        print(json.dumps({'error_type':type(exc).__name__,'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
