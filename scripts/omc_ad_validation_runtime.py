#!/usr/bin/env python3
"""Restore a fresh, bounded Ad inspection runtime; never start a provider."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'docs/comparison-dashboard/evidence/ad-validation-runtime-v1/manifest.json'
BASELINE = '34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc'
CORE = '637a9653759cfca9e070450139dfa56cdd67c5d1'
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
    if cfg['baseline_commit'] != BASELINE or cfg['core_commit'] != CORE:
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
    write_json(run/'runtime.json',runtime)
    write_json(run/'restore-receipt.json',{
        'schema_version':'omc-ad-restore-receipt/v1','provider_calls':0,
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
    run = Path(cfg['run_root'])
    receipt = json.loads(child(run,'restore-receipt.json').read_text())
    if (path != run/'runtime.json' or receipt['runtime_sha256'] != digest(path)
            or digest(child(run,'browser.cjs')) != cfg['browser_sha256']):
        raise ValueError('runtime_binding_mismatch')
    for name, pin in receipt['restored_files'].items():
        if digest(child(run,name)) != pin:
            raise ValueError('restored_file_mismatch')
    return cfg


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
    args = parser.parse_args()
    try:
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
