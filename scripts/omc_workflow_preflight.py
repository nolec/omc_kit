#!/usr/bin/env python3
"""Operator-pinned benchmark preparation, optional launch, and scope observation.

Reports bind local observations, not approval authority or a filesystem sandbox.
No provider is called unless an explicit --launch argv follows a ready report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import urllib.parse
import urllib.request

SCHEMA = 'omc-workflow-preflight/v1'
BROWSER_QUERY = """(async()=>{
 const ads=[...document.querySelectorAll('[data-testid=ad-card-large]')];
 const images=ads.flatMap(e=>[...e.querySelectorAll('img')]);
 const loaded=i=>i.complete&&i.naturalWidth>0;
 const start=performance.now();
 const tracks=ads.flatMap((ad,a)=>[...ad.querySelectorAll('[data-testid^=ad-track]')].map((track,t)=>({ad,track,card_id:String(a),track_id:a+':'+t,samples:[]})));
 for(let i=0;i<6;i++){
  for(const item of tracks){
   const connected=item.ad.isConnected&&item.track.isConnected&&item.ad.contains(item.track);
   item.samples.push({elapsed_ms:performance.now()-start,connected,
    images_loaded:[...item.ad.querySelectorAll('img')].filter(loaded).length,
    transform:connected?getComputedStyle(item.track).transform:''});
  }
  const until=performance.now()+800;
  while(performance.now()<until)await new Promise(r=>setTimeout(r,Math.ceil(until-performance.now())));
 }
 // Preserve operator readiness: final image load and a change while connected.
 // The opt-in completion evaluator separately requires six fully valid samples.
 const auto_transition=tracks.some(({ad,track,samples})=>ad.isConnected&&ad.contains(track)&&[...ad.querySelectorAll('img')].filter(loaded).length>=2&&new Set(samples.filter(s=>s.connected).map(s=>s.transform)).size>1);
 return {url:location.href,images_loaded:images.filter(loaded).length,auto_transition,script_urls:[...document.scripts].map(s=>s.src).filter(Boolean),
  tracks:tracks.map(({card_id,track_id,samples})=>({card_id,track_id,samples}))};
})()"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_path(value: str, *, prefix: bool = False) -> str:
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
        raise ValueError('invalid_relative_path')
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] in {'.git', '.omc'}:
        raise ValueError('invalid_relative_path')
    return str(p) + ('/' if prefix and value.endswith('/') else '')


def allowed(path: str, paths: list[str]) -> bool:
    return path.startswith('.omc/') or any(
        path.startswith(p) if p.endswith('/') else path == p for p in paths)


def git(root: Path, *args: str, input_data: str | None = None) -> str:
    return subprocess.run(['git', *args], cwd=root, text=True, capture_output=True,
                          timeout=15, check=True, input=input_data).stdout


def baseline(root: Path) -> dict:
    if Path(git(root, 'rev-parse', '--show-toplevel').strip()).resolve() != root.resolve():
        raise ValueError('target_must_be_git_root')
    index = {}
    for entry in filter(None, git(root, 'ls-files', '--stage', '-z').split('\0')):
        metadata, path = entry.split('\t', 1)
        mode, oid, stage = metadata.split()
        if stage != '0':
            raise ValueError('unmerged_index')
        index[path] = mode + ':' + oid
    names = set(index) | set(filter(None, git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
    result = {}
    for name in sorted(names | {'.git/info/exclude'}):
        if name.startswith('.omc/'):
            continue
        p = root/name
        if p.is_symlink():
            payload = 'link:' + os.readlink(p)
        elif p.is_file():
            # Do not read a regular-looking path through an external parent link.
            p.resolve().relative_to(root.resolve())
            payload = 'file:' + oct(stat.S_IMODE(p.stat().st_mode)) + ':' + sha(p.read_bytes())
        elif not p.exists():
            payload = 'deleted'
        else:
            raise ValueError('unsupported_scope_entry:' + name)
        result[name] = {'working': payload, 'index': index.get(name)}
    return result


def validate_browser_config(config: dict) -> None:
    control = urllib.parse.urlparse(config['control_url'])
    origin = urllib.parse.urlparse(config['origin'])
    if (control.scheme != 'http' or control.hostname not in {'localhost', '127.0.0.1'}
            or control.path != '/command' or control.username or control.password
            or control.query or control.fragment or origin.scheme != 'http'
            or origin.hostname not in {'localhost', '127.0.0.1'} or origin.username or origin.password
            or origin.path or origin.query or origin.fragment
            or not isinstance(config['home_path'], str) or not config['home_path'].startswith('/')
            or type(config['min_images']) is not int or config['min_images'] < 2):
        raise ValueError('browser_config_invalid')
    # Force validation of malformed port syntax before any network operation.
    control.port
    origin.port


def browser_observation(config: dict) -> dict:
    validate_browser_config(config)
    request = urllib.request.Request(config['control_url'], data=json.dumps(
        {'op': 'evaluate', 'args': [BROWSER_QUERY]}).encode(), headers={'Content-Type': 'application/json'})
    # Local control endpoints must not redirect to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        value = json.load(response)
    return value


def browser_readiness(config: dict) -> dict:
    value = browser_observation(config)
    if (not isinstance(value, dict) or value.get('url') != config['origin'] + config['home_path']
            or type(value.get('images_loaded')) is not int or value['images_loaded'] < config['min_images']
            or value.get('auto_transition') is not True):
        raise ValueError('browser_not_ready')
    return value


def identity_command(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('server_identity_unavailable') from exc


def process_identity(pid: int) -> dict:
    if type(pid) is not int or pid <= 0:
        raise ValueError('server_identity_unavailable')
    fields = identity_command(['ps', '-p', str(pid), '-o', 'pid=,ppid=,lstart=']).split()
    if len(fields) != 7 or fields[0] != str(pid) or not fields[1].isdigit():
        raise ValueError('server_identity_unavailable')
    directories = [line[1:] for line in identity_command(
        ['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn']).splitlines() if line.startswith('n')]
    if len(directories) != 1:
        raise ValueError('server_identity_unavailable')
    return {'pid': pid, 'ppid': int(fields[1]), 'started_at': ' '.join(fields[2:]),
            'cwd': str(Path(directories[0]).resolve())}


def listener_pid(port: int) -> int:
    lines = identity_command(['lsof', '-nP', '-iTCP:' + str(port),
                              '-sTCP:LISTEN', '-Fpcn']).splitlines()
    pids = {int(line[1:]) for line in lines if line.startswith('p') and line[1:].isdigit()}
    if len(pids) != 1:
        raise ValueError('server_listener_ambiguous')
    return pids.pop()


def server_binding(root: Path, config: dict, browser: dict, *, observed: dict | None = None,
                   previous: dict | None = None, initial: bool = True) -> dict:
    """Bind the local listener and loaded bundle bytes; not full build provenance."""
    if not isinstance(config, dict):
        raise ValueError('server_binding_required')
    validate_browser_config(browser)
    launcher = process_identity(config.get('launcher_pid'))
    if launcher['started_at'] != config.get('launcher_started_at'):
        raise ValueError('server_launcher_changed')
    origin = urllib.parse.urlparse(browser['origin'])
    listener = process_identity(listener_pid(origin.port or 80))
    if listener['cwd'] != str(root.resolve()):
        raise ValueError('server_candidate_root_mismatch')
    cursor, seen = listener, set()
    while cursor['pid'] != launcher['pid']:
        if cursor['pid'] in seen or len(seen) >= 64 or cursor['ppid'] <= 1:
            raise ValueError('server_listener_not_owned')
        seen.add(cursor['pid'])
        cursor = process_identity(cursor['ppid'])
    if cursor != launcher:
        raise ValueError('server_launcher_changed')
    identity = {'launcher': launcher, 'listener_pid': listener['pid'],
                'started_at': listener['started_at'], 'cwd': listener['cwd']}
    if previous and any(previous.get(k) != v for k, v in identity.items()):
        raise ValueError('server_listener_changed')
    bundles = config.get('bundles')
    if not isinstance(bundles, list) or not bundles:
        raise ValueError('server_bundle_required')
    evidence = []
    for bundle in bundles:
        try:
            name = relative_path(bundle['file'])
            path = root / name
            path.resolve().relative_to(root.resolve())
            url_path = bundle['url_path']
            parsed = urllib.parse.urlparse(url_path)
            if (not url_path.startswith('/') or url_path.startswith('//') or parsed.netloc
                    or parsed.scheme or parsed.query or parsed.fragment or '\\' in url_path
                    or '..' in PurePosixPath(urllib.parse.unquote(parsed.path)).parts
                    or path.is_symlink() or not path.is_file()):
                raise ValueError('invalid bundle')
        except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
            raise ValueError('server_bundle_path_invalid') from exc
        url = browser['origin'] + url_path
        served_urls = [url]
        if observed is not None:
            scripts = observed.get('script_urls')
            if not isinstance(scripts, list):
                raise ValueError('server_bundle_not_loaded')
            # Match the configured origin/path, but fetch every observed revision.
            # Query strings can select different bytes; fragments are not sent over HTTP.
            served_urls = sorted({urllib.parse.urlparse(s)._replace(fragment='').geturl()
                for s in scripts if isinstance(s, str)
                and urllib.parse.urlparse(s)._replace(query='', fragment='').geturl() == url})
            if not served_urls:
                raise ValueError('server_bundle_not_loaded')
        local_sha = sha(path.read_bytes())
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        for served_url in served_urls:
            try:
                with urllib.request.build_opener(NoRedirect).open(served_url, timeout=15) as response:
                    served_sha = sha(response.read())
            except OSError as exc:
                raise ValueError('server_bundle_unavailable') from exc
            if local_sha != served_sha:
                raise ValueError('server_bundle_mismatch')
        if (sha(path.read_bytes()) != local_sha or (initial and local_sha != bundle.get('sha256'))):
            raise ValueError('server_bundle_mismatch')
        evidence.append({'file': name, 'url_path': url_path, 'sha256': local_sha,
                         'script_urls': served_urls})
    # Reject listener replacement while reading its responses.
    if (listener_pid(origin.port or 80) != listener['pid']
            or process_identity(listener['pid']) != listener or process_identity(launcher['pid']) != launcher):
        raise ValueError('server_listener_changed')
    return {**identity, 'bundles': evidence}


def prepare(root: Path, manifest: Path, manifest_sha256: str, task_profile: str | None = None) -> dict:
    report = {'schema_version': SCHEMA, 'target': str(root), 'status': 'blocked',
              'provider_call_count': 0, 'reason_codes': [], 'manifest_sha256': manifest_sha256}
    try:
        raw = manifest.read_bytes()
        if sha(raw) != manifest_sha256:
            raise ValueError('manifest_digest_mismatch')
        doc = json.loads(raw)
        if doc['schema_version'] != SCHEMA:
            raise ValueError('manifest_schema_invalid')
        paths = doc['work_paths']
        if not isinstance(paths, list) or not paths:
            raise ValueError('work_scope_required')
        paths = [relative_path(p, prefix=True) for p in paths]
        inputs = doc['inputs']
        if not isinstance(inputs, dict) or not inputs:
            raise ValueError('inputs_required')
        def check_inputs():
            for name, expected in inputs.items():
                if not isinstance(name, str) or not isinstance(expected, str) or sha((root/name).read_bytes()) != expected:
                    raise ValueError('input_mismatch:' + str(name))
        check_inputs()
        checks_path = doc['required_checks_file']
        if checks_path not in inputs:
            raise ValueError('checks_file_not_frozen')
        checks = json.loads((root/checks_path).read_text())
        ids = doc['required_check_ids']
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids)
                or len(set(ids)) != len(ids) or not isinstance(checks, list)
                or [c['id'] for c in checks] != ids):
            raise ValueError('required_checks_mismatch')
        from omc_visual_check import AD_PROFILE, CHECK_ID, validate_requirement, requirement_path
        visual_requirement = None
        if ((task_profile is not None and task_profile != AD_PROFILE)
                or (doc.get('task_profile') is not None and doc['task_profile'] != AD_PROFILE)):
            raise ValueError('unsupported_task_profile')
        if (task_profile == AD_PROFILE or doc.get('task_profile') == AD_PROFILE
                or 'visual_requirement' in doc or requirement_path(root).exists()
                or any('visual_policy' in c for c in checks)):
            visual_requirement = doc.get('visual_requirement')
            validate_requirement(root, visual_requirement)
            definition = next((c for c in checks if c['id'] == CHECK_ID), {})
            if doc.get('task_profile') != AD_PROFILE or definition.get('visual_policy') != visual_requirement['policy']:
                raise ValueError('visual_required_check_missing_or_policy_mismatch')
            if any(inputs.get(n) != p for n, p in visual_requirement['policy']['builder_inputs'].items()):
                raise ValueError('visual_builder_not_frozen')
            seal = requirement_path(root)
            if seal.exists() and (seal.is_symlink() or json.loads(seal.read_text()) != visual_requirement):
                raise ValueError('visual_requirement_downgrade_or_policy_mismatch')
        artifacts = doc['ignored_artifacts']
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError('ignored_artifacts_required')
        def check_artifacts():
            for item in artifacts:
                name = relative_path(item['path'])
                path = root/name
                if not path.is_file() or path.is_symlink():
                    raise ValueError('artifact_missing:' + name)
                try:
                    match = git(root, 'check-ignore', '-v', '-z', '--stdin', input_data=name+'\0').split('\0')
                except subprocess.CalledProcessError:
                    raise ValueError('artifact_not_ignored:' + name)
                source, _line, pattern, matched, *_ = match
                if source != item['source'] or pattern != item['pattern'] or matched != name:
                    raise ValueError('ignore_rule_mismatch:' + name)
        check_artifacts()
        binding = doc.get('server_binding')
        identity = server_binding(root, binding, doc['browser'])
        browser = browser_readiness(doc['browser'])
        scope_baseline = baseline(root)
        check_artifacts()  # Browser observation must not hide preparation drift.
        check_inputs()  # Reject input changes during browser observation.
        if sha(manifest.read_bytes()) != manifest_sha256:
            raise ValueError('manifest_digest_mismatch')
        identity = server_binding(root, binding, doc['browser'], observed=browser, previous=identity)
        if visual_requirement is not None:
            validate_requirement(root, visual_requirement)
            # Write once before any executor can run; repeated preparation must agree.
            seal = requirement_path(root)
            seal.parent.mkdir(parents=True, exist_ok=True)
            if not seal.exists():
                with seal.open('x') as stream:
                    json.dump(visual_requirement, stream, sort_keys=True)
            report['visual_requirement'] = visual_requirement
        report.update(status='ready', work_paths=paths, required_check_ids=ids,
                      browser=browser, baseline=scope_baseline,
                      server_binding={'config': binding, 'browser': doc['browser'], 'evidence': identity})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        report['reason_codes'].append(str(exc))
    return report


def assess_scope(root: Path, report_file: Path, report_sha256: str, writes: list[str],
                 required_ids: set[str], unresolved_writes: list[str] | None = None) -> dict:
    """Compare Git-visible working/index state plus known direct writes."""
    try:
        raw = report_file.read_bytes()
        if sha(raw) != report_sha256:
            raise ValueError('scope_report_digest_mismatch')
        report = json.loads(raw)
        if (report.get('schema_version') != SCHEMA or report.get('status') != 'ready'
                or report.get('target') != str(root) or set(report['required_check_ids']) != required_ids):
            raise ValueError('scope_report_binding_invalid')
        paths = report['work_paths']
        if not isinstance(paths, list) or not paths:
            raise ValueError('work_scope_required')
        paths = [relative_path(p, prefix=True) for p in paths]
        before = report['baseline']
        if not isinstance(before, dict):
            raise ValueError('scope_baseline_invalid')
        binding_result = None
        if 'server_binding' in report:
            try:
                binding = report['server_binding']
                server_binding(root, binding['config'], binding['browser'],
                               observed=browser_readiness(binding['browser']),
                               previous=binding['evidence'], initial=False)
                binding_result = {'status': 'passed', 'reason': None}
            except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
                binding_result = {'status': 'unknown', 'reason': str(exc)}
        # Even a failed server observation must not skip scope checks. Read the
        # working state afterwards so changes made during observation are included.
        after = baseline(root)
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
        for name in writes:
            try:
                changed.add(str(Path(name).relative_to(root)))
            except ValueError:
                raise ValueError('outside_repository_write')
        violations = sorted(name for name in changed if not allowed(name, paths))
        unresolved = []
        for name in unresolved_writes or []:
            try:
                relative = str(Path(name).relative_to(root))
            except ValueError:
                relative = name
            if not allowed(relative, paths):
                unresolved.append(relative)
        binding_reason = binding_result['reason'] if binding_result else None
        result = {'status': 'failed' if violations else 'unknown' if unresolved or binding_reason else 'passed',
                'violations': violations, 'unresolved_writes': unresolved,
                'reason': 'out_of_scope_change' if violations else 'unconfirmed_out_of_scope_write' if unresolved else binding_reason}
        if binding_result is not None:
            result['server_binding'] = binding_result
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        return {'status': 'unknown', 'violations': [], 'reason': str(exc)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--task-profile', choices=['advertising-visual/v1'])
    parser.add_argument('--launch', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = args.target.resolve()
    try:
        report_path = args.report.absolute()
        if report_path.is_symlink():
            raise ValueError('report_must_be_regular')
        try:
            relative = report_path.resolve().relative_to(root)
        except ValueError:
            relative = None
        if relative is not None and (not relative.parts or relative.parts[0] != '.omc'):
            raise ValueError('report_must_be_external_or_runtime')
        if args.launch is not None and not args.launch:
            raise ValueError('explicit_launch_argv_required')
        report = prepare(root, args.manifest, args.manifest_sha256, args.task_profile)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # A second attempt must use a new path, never overwrite old preparation.
        with report_path.open('x') as output:
            output.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        summary = {key: value for key, value in report.items() if key != 'baseline'}
        summary['report'] = str(report_path)
        print(json.dumps(summary, ensure_ascii=False), file=sys.stderr if args.launch else sys.stdout)
        if report['status'] != 'ready':
            return 2
        if args.launch:
            if (root/'.omc/home-motion-requirement.json').exists():
                from omc_home_observation import run_native
                return run_native(root, args.launch)
            return subprocess.run(args.launch, cwd=root).returncode
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'provider_call_count': 0, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
