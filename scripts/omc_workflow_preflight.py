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
 const tracks=ads.flatMap(ad=>[...ad.querySelectorAll('[data-testid^=ad-track]')].map(track=>({ad,track,states:new Set()})));
 for(let i=0;i<6;i++){
  for(const {ad,track,states} of tracks){
   if(ad.isConnected&&ad.contains(track))states.add(getComputedStyle(track).transform);
  }
  await new Promise(r=>setTimeout(r,800));
 }
 const auto_transition=tracks.some(({ad,track,states})=>ad.isConnected&&ad.contains(track)&&states.size>1&&[...ad.querySelectorAll('img')].filter(loaded).length>=2);
 return {url:location.href,images_loaded:images.filter(loaded).length,auto_transition};
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


def browser_readiness(config: dict) -> dict:
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
    request = urllib.request.Request(config['control_url'], data=json.dumps(
        {'op': 'evaluate', 'args': [BROWSER_QUERY]}).encode(), headers={'Content-Type': 'application/json'})
    # Local control endpoints must not redirect to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        value = json.load(response)
    if (not isinstance(value, dict) or value.get('url') != config['origin'] + config['home_path']
            or type(value.get('images_loaded')) is not int or value['images_loaded'] < config['min_images']
            or value.get('auto_transition') is not True):
        raise ValueError('browser_not_ready')
    return value


def prepare(root: Path, manifest: Path, manifest_sha256: str) -> dict:
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
        browser = browser_readiness(doc['browser'])
        scope_baseline = baseline(root)
        check_artifacts()  # Browser observation must not hide preparation drift.
        check_inputs()  # Reject input changes during browser observation.
        if sha(manifest.read_bytes()) != manifest_sha256:
            raise ValueError('manifest_digest_mismatch')
        report.update(status='ready', work_paths=paths, required_check_ids=ids,
                      browser=browser, baseline=scope_baseline)
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
        return {'status': 'failed' if violations else 'unknown' if unresolved else 'passed',
                'violations': violations, 'unresolved_writes': unresolved,
                'reason': 'out_of_scope_change' if violations else 'unconfirmed_out_of_scope_write' if unresolved else None}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        return {'status': 'unknown', 'violations': [], 'reason': str(exc)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
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
        report = prepare(root, args.manifest, args.manifest_sha256)
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
            return subprocess.run(args.launch, cwd=root).returncode
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'provider_call_count': 0, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
