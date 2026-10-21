#!/usr/bin/env python3
"""Operator-frozen local validation commands, not a shell sandbox or authority.

No model/provider calls. Policy and receipts bind local bytes and observations;
they do not authenticate a hostile operator or contain external side effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import urllib.request
import uuid

from omc_pipeline_guard import _code_snapshot_digest
from omc_native_red_evidence import jest_cases, source, write_new
from omc_workflow_preflight import baseline, browser_observation, server_binding, validate_browser_config
from omc_home_observation import sample_assessment
from omc_visual_check import VARIANTS, assess_report

POLICY = '.omc/validation-commands/policy.json'
CONTRACT = 'advertising-comparison/v2'
REPORT_CONTRACT = {'passed_first_line':'필수 검증 통과·작업 완료',
                   'existing_failures':'기존 실패: {count}개(미해결)'}
AD = 'libs/sellerhub-store/src/lib/components/Ad/'
TEST = AD+'Ad.test.tsx'
CONFIG = 'libs/sellerhub-store/jest.config.ts'
REQUIRED = ('tests', 'lint', 'build-controlled', 'build-pixels', 'controlled', 'home',
            'pixels-dpr1', 'pixels-dpr2')
PROFILES = (*REQUIRED, 'omc-guard-task', 'omc-tdd')
SCRIPT_PINS = ('runner', 'visual_checker', 'controlled_builder', 'pixel_builder')
CONTROLLED_QUERY = """(async()=>{
 const variants=['card-large','card-medium','card-small','banner-small','banner-large','banner-landing'];
 const rows=variants.map(v=>{const e=document.querySelector('[data-testid="ad-'+v+'"]');
 if(!e)return {variant:v};const s=getComputedStyle(e);return {variant:v,border:s.border,
 shadow:s.boxShadow,images:[...e.querySelectorAll('img')].filter(i=>i.complete&&i.naturalWidth>0).length};});
 const tracks=[...document.querySelectorAll('[data-testid^="ad-track"]')];
 const before=tracks.map(e=>getComputedStyle(e).transform);
 await new Promise(r=>setTimeout(r,3700));
 return {url:location.href,variants:rows,arrows:document.querySelectorAll('[aria-label="이전 광고"],[aria-label="다음 광고"]').length,
 transitioned:tracks.some((e,i)=>e.isConnected&&getComputedStyle(e).transform!==before[i])};})()"""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(root, ref):
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str):
        raise ValueError('evidence_reference_required')
    path = source(root, ref['path'])
    raw = path.read_bytes()
    if sha(raw) != ref.get('sha256'):
        raise ValueError('evidence_digest_mismatch')
    return raw


def _tool(policy, name):
    ref = policy['tools'][name]
    path = Path(ref['path'])
    if not path.is_absolute() or not path.is_file() or path.is_symlink() or sha(path.read_bytes()) != ref['sha256']:
        raise ValueError('tool_digest_mismatch:'+name)
    return str(path)


def _validate_policy(root, policy):
    if (policy.get('schema_version') != 'omc-validation-policy/v1'
            or policy.get('contract_id') != CONTRACT or policy.get('target') != str(root.resolve())
            or policy.get('arm') not in {'omc-kit', 'oh-my-claudecode'}
            or not isinstance(policy.get('run_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', policy['run_id'])
            or type(policy.get('timeout_sec')) is not int or not 1 <= policy['timeout_sec'] <= 120):
        raise ValueError('validation_policy_invalid')
    for key in ('node', 'python', 'jest', 'eslint', 'baseline_tests', *SCRIPT_PINS):
        _tool(policy, key)
    # Pin the implementations of all reused oracles, not only this entry point.
    for name, pin in policy.get('oracle_pins', {}).items():
        if sha(Path(name).read_bytes()) != pin:
            raise ValueError('oracle_digest_mismatch')
    if 'browser' in policy:
        validate_browser_config(policy['browser'])
        if policy['browser']['home_path'] != '/sixshoptest01':
            raise ValueError('frozen_home_path_required')
    if 'visual' in policy:
        package=Path(policy['visual']['playwright_module'])/'package.json'
        if sha(package.read_bytes()) != policy['visual']['package_sha256']:
            raise ValueError('playwright_module_changed')


def freeze(root, proposal, pin):
    raw = proposal.read_bytes()
    if sha(raw) != pin:
        raise ValueError('proposal_digest_mismatch')
    policy = json.loads(raw)
    policy['selection_path'] = str(selection_path(root, policy).relative_to(root))
    policy['report_contract'] = REPORT_CONTRACT
    import omc_workflow_validation
    policy['oracle_pins'] = {str(Path(m.__file__).resolve()): sha(Path(m.__file__).read_bytes())
        for m in (sys.modules[__name__], sys.modules['omc_pipeline_guard'],
                  sys.modules['omc_native_red_evidence'], sys.modules['omc_workflow_preflight'],
                  sys.modules['omc_home_observation'], sys.modules['omc_visual_check'],
                  omc_workflow_validation)}
    _validate_policy(root, policy)
    policy['baseline_scope'] = baseline(root)
    policy['baseline_snapshot'] = _code_snapshot_digest(root)
    policy['required_profiles'] = list(REQUIRED)
    return write_new(root, POLICY, policy)


def load_policy(root, pin=None):
    raw = source(root, POLICY).read_bytes()
    if pin is not None and sha(raw) != pin:
        raise ValueError('policy_digest_mismatch')
    policy = json.loads(raw)
    _validate_policy(root, policy)
    selection_path(root, policy)
    if (policy.get('required_profiles') != list(REQUIRED) or not policy.get('baseline_scope')
            or policy.get('report_contract') != REPORT_CONTRACT):
        raise ValueError('required_profiles_changed')
    return policy, sha(raw)


def selection_path(root, policy):
    from omc_home_observation import local
    run_id = policy.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run_id):
        raise ValueError('selection_run_id_invalid')
    name = '.omc/comparisons/'+run_id+'/final-selection.json'
    if policy.get('selection_path', name) != name:
        raise ValueError('selection_path_mismatch')
    return local(root, name)


def prebound_task_context(root, policy, policy_pin):
    """A read-only entry, only for a previously approved OMC comparison task."""
    from omc_native_red_evidence import execution_context
    from omc_pipeline_guard import _load_approved_basis, _load_state
    try:
        context = execution_context(root)
        basis = _load_approved_basis(root, context['session_id'])
        state = _load_state(root)
        if (policy.get('arm') != 'omc-kit' or policy.get('session_id') != context['session_id']
                or policy.get('run_id') != context['run_id']
                or basis.get('validation_commands_policy_sha256') != policy_pin
                or state.get('session_id') != context['session_id']
                or state.get('basis_sha256') != context['basis_sha256']
                or state.get('contract_confirmed') is not True):
            raise ValueError('changed')
        return context
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('prebound_task_context_mismatch') from exc


def runtime_command_result(root, command, policy, policy_pin):
    """Only the two literal, read-only runtime queries; no generic runner bypass."""
    from omc_native_command import single_argv
    args = single_argv(command)
    prefix = ['python3', 'scripts/omc_validation_command.py']
    if args == prefix+['selection-path', '--target', '.']:
        return {'selection_path': str(selection_path(root, policy).relative_to(root))}
    if args[:3] == prefix+['task-context']:
        context = prebound_task_context(root, policy, policy_pin)
        expected = prefix+['task-context', '--target', '.', '--sha256', policy_pin,
            '--session-id', context['session_id'], '--run-id', context['run_id'],
            '--basis-sha256', context['basis_sha256']]
        if args == expected:
            return context
    return None


def _scope(root, policy):
    after = baseline(root)
    before = policy['baseline_scope']
    changed = [name for name in set(before)|set(after) if before.get(name) != after.get(name)]
    if any(not name.startswith(AD) for name in changed):
        raise ValueError('change_outside_product_scope')


def _save_bytes(root, folder, name, data):
    path = source(root, folder+'/'+name)
    with path.open('xb') as stream:
        stream.write(data)
    return {'path':folder+'/'+name, 'sha256':sha(data)}


def _invoke(root, policy, folder, argv, counter):
    environment={**os.environ, 'NX_DAEMON':'false',
                 'PATH':str(Path(_tool(policy,'node')).parent)+os.pathsep+os.environ.get('PATH','')}
    process = subprocess.Popen(argv,cwd=root,env=environment,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,shell=False,start_new_session=True)
    timed_out=False
    try:
        stdout,stderr=process.communicate(timeout=policy['timeout_sec'])
    except subprocess.TimeoutExpired:
        timed_out=True
        if os.name=='posix':
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        else:
            process.kill()
        stdout,stderr=process.communicate()
    code=process.returncode
    return {'argv':argv, 'exit_code':code, 'timed_out':timed_out,
        'stdout':_save_bytes(root, folder, f'{counter}-stdout.txt', stdout),
        'stderr':_save_bytes(root, folder, f'{counter}-stderr.txt', stderr)}


def _browser(policy, op, args):
    config = policy['browser']
    validate_browser_config(config)
    request = urllib.request.Request(config['control_url'], data=json.dumps(
        {'op':op, 'args':args}).encode(), headers={'Content-Type':'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        return json.load(response)


def _tests(root, policy, raw, exit_code):
    value = json.loads(raw)
    failed = jest_cases(value, exit_code)
    base = json.loads(Path(_tool(policy, 'baseline_tests')).read_bytes())
    old_failed = jest_cases(base, int(bool(base.get('numFailedTests'))))
    def diagnostics(doc):
        result = {}
        for suite in doc['testResults']:
            for case in suite['assertionResults']:
                if case['status'] != 'failed':
                    continue
                message = '\n'.join(case['failureMessages']).replace(str(root), '<candidate>')
                message = re.sub(r':\d+:\d+', ':LINE:COL', message)
                message = re.sub(r'(?m)^(\s*>?\s*)\d+(\s*\|)', r'\1LINE\2', message)
                result[case['fullName']] = message
        return result
    before, after = diagnostics(base), diagnostics(value)
    new_failed = sorted(set(failed)-set(old_failed))
    same = all(after[name] == before[name] for name in set(failed)&set(old_failed))
    old_names = {c['fullName'] for s in base['testResults'] for c in s['assertionResults']}
    names = {c['fullName'] for s in value['testResults'] for c in s['assertionResults']}
    if not old_names <= names:
        raise ValueError('baseline_test_removed')
    return {'status':'passed' if not new_failed and same else 'failed',
            'new_failed_cases':new_failed, 'baseline_failed_cases':old_failed,
            'existing_failed_cases':sorted(set(failed)&set(old_failed)),
            'same_existing_diagnostics':same}


def _judge(root, policy, receipt):
    profile = receipt['profile']
    if receipt['before'] != receipt['after']:
        return {'status':'unknown', 'reason':'candidate_changed_during_validation'}
    if any(call.get('timed_out') for call in receipt['invocations']):
        return {'status':'unknown','reason':'validation_command_timeout'}
    if profile == 'tests':
        return _tests(root, policy, _raw(root, receipt['artifacts']['jest']), receipt['exit_code'])
    if profile.startswith('pixels-dpr'):
        ref = receipt['artifacts']['report']
        report=json.loads(_raw(root,ref))
        if report.get('status')=='unknown':
            return {'status':'unknown','reason':report.get('reason') or 'visual_unavailable'}
        return assess_report(root, source(root, ref['path']), ref['sha256'],
                             receipt['artifacts']['manifest']['sha256'])
    if profile=='lint' and receipt['exit_code']==2:
        return {'status':'unknown','reason':'lint_unavailable'}
    if receipt['exit_code'] != 0:
        return {'status':'failed', 'reason':'command_failed'}
    if profile in {'build-controlled', 'build-pixels'}:
        for ref in receipt['artifacts'].values():
            _raw(root, ref)
        return {'status':'passed'}
    if profile == 'controlled':
        obs = json.loads(_raw(root, receipt['artifacts']['observation']))
        rows = obs.get('variants', [])
        if (obs.get('url') != policy['browser']['origin']+'/omc-ad-validation/index.html'
                or not isinstance(rows,list) or len(rows)!=6 or any(not isinstance(r,dict) for r in rows)
                or {r.get('variant') for r in rows}!=set(VARIANTS)
                or any(type(r.get('images')) is not int or r['images']<2 for r in rows)
                or type(obs.get('arrows')) is not int or obs.get('transitioned') is not True):
            return {'status':'unknown','reason':'controlled_surface_not_observed'}
        valid = (obs.get('arrows') == 0
            and all(r.get('border') == '1px solid rgba(0, 0, 0, 0.1)'
                and r.get('shadow') == 'rgba(0, 0, 0, 0.05) 0px 1px 2px 0px'
                and type(r.get('images')) is int and r['images'] >= 2 for r in rows))
        return {'status':'passed' if valid else 'failed'}
    if profile == 'home':
        obs = json.loads(_raw(root, receipt['artifacts']['observation']))
        if obs.get('url') != policy['browser']['origin']+policy['browser']['home_path']:
            raise ValueError('home_url_mismatch')
        ready = json.loads(_raw(root, policy['preflight']))
        server_binding(root, ready['server_binding']['config'], policy['browser'], observed=obs,
                       previous=ready['server_binding']['evidence'], initial=False)
        return sample_assessment(obs)
    return {'status':'passed'}  # Exact internal guard/TDD commands, exit 0 above.


def _command(root, policy, folder, profile):
    node, python = _tool(policy,'node'), _tool(policy,'python')
    if profile=='tests':
        return [node,_tool(policy,'jest'),'--config',CONFIG,'--runInBand',
                '--runTestsByPath',TEST,'--json','--outputFile',str(root/folder/'jest.json')]
    if profile=='lint':
        return [node,_tool(policy,'eslint'),AD+'Ad.tsx',AD+'Ad.styled.ts',TEST,'--max-warnings=0']
    if profile in {'build-controlled','build-pixels'}:
        return [node,_tool(policy,'controlled_builder' if profile=='build-controlled' else 'pixel_builder')]
    if profile.startswith('pixels-dpr'):
        path=root/folder/'manifest.json'
        return [python,_tool(policy,'visual_checker'),'--target',str(root),'--manifest',str(path),
                '--manifest-sha256',sha(path.read_bytes()),'--output-dir',str(root/folder/'visual'),
                '--playwright-module',policy['visual']['playwright_module']]
    if profile in {'omc-guard-task','omc-tdd'}:
        script='scripts/omc_guard.py' if profile=='omc-guard-task' else 'scripts/omc_tdd_check.py'
        ref=policy['internal_tools'][profile]
        if str(source(root,script))!=ref['path'] or sha(source(root,script).read_bytes())!=ref['sha256']:
            raise ValueError('internal_tool_digest_mismatch')
        return [python,script]+(['require','--for','task','--target','.'] if profile=='omc-guard-task' else ['--staged'])
    return None


def run_profile(root, profile):
    if profile not in PROFILES:
        raise ValueError('unsupported_validation_profile')
    policy, pin = load_policy(root)
    if profile.startswith('omc-') and policy['arm'] != 'omc-kit':
        raise ValueError('internal_profile_not_applicable')
    _scope(root, policy)
    ident = uuid.uuid4().hex
    folder = '.omc/validation-commands/calls/'+ident
    source(root, folder).mkdir(parents=True, exist_ok=False)
    receipt = {'schema_version':'omc-validation-receipt/v1', 'call_id':ident,
        'run_id':policy['run_id'], 'arm':policy['arm'], 'target':str(root), 'policy_sha256':pin,
        'profile':profile, 'before':_code_snapshot_digest(root), 'artifacts':{}, 'invocations':[],
        'scope_before':baseline(root), 'exit_code':0}
    try:
        context = prebound_task_context(root, policy, pin) if profile.startswith('omc-') else None
        if context is not None:
            receipt['execution_context'] = context
        if profile == 'tests':
            output = folder+'/jest.json'
        elif profile in {'controlled','home'}:
            _browser(policy, 'goto', ['/omc-ad-validation/index.html' if profile=='controlled' else policy['browser']['home_path']])
            _browser(policy, 'reload', [])
            obs = (_browser(policy,'evaluate',[CONTROLLED_QUERY]) if profile=='controlled'
                   else browser_observation(policy['browser']))
            receipt['artifacts']['observation'] = _save_bytes(root,folder,'observation.json',json.dumps(obs).encode())
            snap = _browser(policy, 'snapshot', [])
            path = source(root, str(Path(snap['screenshot']).relative_to(root)))
            if not str(path.relative_to(root)).startswith('.omc/browser/'):
                raise ValueError('browser_snapshot_path_invalid')
            receipt['artifacts']['screen'] = {'path':str(path.relative_to(root)), 'sha256':sha(path.read_bytes())}
        elif profile.startswith('pixels-dpr'):
            dpr = int(profile[-1]); visual = policy['visual']
            bundle = 'apps/store-manager/public/omc-ad-pixels/bundle.js'
            inputs = [AD+'Ad.tsx', AD+'Ad.styled.ts', TEST,
                      '.omc/visual-input/build-pixels.cjs', 'apps/store-manager/public/omc-ad-pixels/index.html']
            manifest = {'schema_version':'omc-visual-manifest/v1','run_id':policy['run_id'],
                'url':visual['origin']+'/omc-ad-pixels/index.html',
                'bundle_url':visual['origin']+'/omc-ad-pixels/bundle.js',
                'bundle_path':bundle,'bundle_sha256':sha(source(root,bundle).read_bytes()),
                'build_inputs':{n:sha(source(root,n).read_bytes()) for n in inputs},
                'dpr':dpr,'images_per_variant':2}
            ref = _save_bytes(root,folder,'manifest.json',json.dumps(manifest).encode())
            receipt['artifacts']['manifest'] = ref
        argv = _command(root,policy,folder,profile)
        if argv is not None:
            invocation = _invoke(root,policy,folder,argv,0)
            receipt['invocations'].append(invocation)
            receipt.update(exit_code=invocation['exit_code'],stdout=invocation['stdout'],stderr=invocation['stderr'])
            if invocation['timed_out']:
                receipt['after']=_code_snapshot_digest(root)
                receipt['judgment']=_judge(root,policy,receipt)
                receipt['status']=receipt['judgment']['status']
                ref=write_new(root,folder+'/receipt.json',receipt)
                return {'schema_version':'omc-validation-result/v1','status':receipt['status'],'receipt':ref}
        else:
            receipt['stdout'] = receipt['artifacts']['observation']
            receipt['stderr'] = _save_bytes(root,folder,'stderr.txt',b'')
        if profile == 'tests':
            path = source(root,output)
            receipt['artifacts']['jest'] = {'path':output,'sha256':sha(path.read_bytes())}
        if profile in {'build-controlled','build-pixels'}:
            base = 'apps/store-manager/public/'+('omc-ad-validation' if profile=='build-controlled' else 'omc-ad-pixels')
            receipt['outputs'] = {base+'/'+n:sha(source(root,base+'/'+n).read_bytes()) for n in ['index.html','bundle.js']}
            receipt['artifacts'] = {n:_save_bytes(root,folder,n,source(root,base+'/'+n).read_bytes())
                                    for n in ['index.html','bundle.js']}
        if profile.startswith('pixels-dpr'):
            path = source(root,folder+'/visual/report.json')
            receipt['artifacts']['report'] = {'path':str(path.relative_to(root)),'sha256':sha(path.read_bytes())}
        receipt['after'] = _code_snapshot_digest(root)
        if context is not None:
            from omc_native_red_evidence import verify_execution_context
            verify_execution_context(root, context)
        _scope(root,policy)
        _validate_policy(root,policy)
        receipt['judgment'] = _judge(root,policy,receipt)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        receipt['after'] = _code_snapshot_digest(root)
        receipt['judgment'] = {'status':'unknown','reason':str(exc)}
    receipt['status'] = receipt['judgment']['status']
    ref = write_new(root,folder+'/receipt.json',receipt)
    return {'schema_version':'omc-validation-result/v1','status':receipt['status'],'receipt':ref}


def validate_receipt(root, ref, policy_pin, profile=None, *, historical=False):
    policy, pin = load_policy(root,policy_pin)
    receipt = json.loads(_raw(root,ref))
    if (receipt['schema_version'] != 'omc-validation-receipt/v1' or receipt['policy_sha256'] != pin
            or receipt['run_id'] != policy['run_id'] or receipt['arm'] != policy['arm']
            or receipt['target'] != str(root) or receipt['profile'] not in PROFILES
            or (profile is not None and receipt['profile'] != profile)):
        raise ValueError('receipt_binding_mismatch')
    if (not re.fullmatch(r'[a-f0-9]{32}',receipt['call_id'])
            or ref['path'] != '.omc/validation-commands/calls/'+receipt['call_id']+'/receipt.json'):
        raise ValueError('receipt_path_mismatch')
    if not historical and receipt['after'] != _code_snapshot_digest(root):
        raise ValueError('receipt_candidate_stale')
    if receipt['profile'].startswith('omc-') and not historical:
        if receipt.get('execution_context') != prebound_task_context(root, policy, pin):
            raise ValueError('prebound_task_context_mismatch')
    _raw(root,receipt['stdout']); _raw(root,receipt['stderr'])
    folder = str(Path(ref['path']).parent)
    expected = _command(root,policy,folder,receipt['profile'])
    if len(receipt['invocations']) != (0 if expected is None else 1):
        raise ValueError('execution_record_missing')
    for call in receipt['invocations']:
        if (call['argv'] != expected or call['exit_code'] != receipt['exit_code']
                or call['stdout'] != receipt['stdout'] or call['stderr'] != receipt['stderr']):
            raise ValueError('execution_record_mismatch')
        _raw(root,call['stdout']); _raw(root,call['stderr'])
    for artifact in receipt['artifacts'].values():
        _raw(root,artifact)
    if historical and receipt['profile'] in {'controlled','home','pixels-dpr1','pixels-dpr2'}:
        # An intermediate oracle observation is not reused as current quality.
        # Only its fixed invocation, raw bytes and stable before/after are read.
        if receipt['before'] != receipt['after']:
            raise ValueError('candidate_changed_during_validation')
        return receipt
    judgment = _judge(root,policy,receipt)
    if judgment != receipt['judgment'] or receipt['status'] != judgment['status']:
        raise ValueError('receipt_judgment_mismatch')
    if not historical:
        for name,pin in receipt.get('outputs',{}).items():
            if sha(source(root,name).read_bytes()) != pin:
                raise ValueError('build_output_changed')
        _scope(root,policy)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['freeze','run','task-context','selection-path'])
    parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--file',type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--profile',choices=PROFILES)
    parser.add_argument('--session-id')
    parser.add_argument('--run-id')
    parser.add_argument('--basis-sha256')
    args = parser.parse_args(); root = args.target.resolve()
    try:
        if args.action in {'task-context', 'selection-path'}:
            if args.file is not None or args.profile is not None:
                raise ValueError('context_arguments_invalid')
            policy, pin = load_policy(root, args.sha256)
            if args.action == 'task-context':
                if not all((args.sha256, args.session_id, args.run_id, args.basis_sha256)):
                    raise ValueError('explicit_task_context_required')
                result = prebound_task_context(root, policy, pin)
                if any(result[k] != getattr(args, k) for k in ('session_id','run_id','basis_sha256')):
                    raise ValueError('prebound_task_context_mismatch')
            else:
                result = {'selection_path': str(selection_path(root, policy).relative_to(root))}
            code = 0
        elif args.action == 'freeze':
            if args.file is None or not args.sha256 or args.profile is not None:
                raise ValueError('freeze_arguments_invalid')
            result = freeze(root,args.file,args.sha256)
            code = 0
        else:
            if args.file is not None or args.sha256 is not None:
                raise ValueError('run_arguments_invalid')
            result = run_profile(root,args.profile)
            code = {'passed':0,'failed':1,'unknown':2}[result['status']]
        print(json.dumps(result,ensure_ascii=False)); return code
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'unknown','reason':str(exc)},ensure_ascii=False)); return 2


if __name__ == '__main__':
    raise SystemExit(main())
