#!/usr/bin/env python3
"""Bounded opaque-image border oracle for an isolated, pixel-aligned fixture.

Submitted build provenance and hashes are bindings, not proof of an honest build.
This does not validate arbitrary pages or replace authenticated home QA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import urllib.parse

from omc_pipeline_guard import _code_snapshot_digest

CHECK_ID = 'browser-visual-controlled'
VARIANTS = ['card-large', 'card-medium', 'card-small', 'banner-small', 'banner-large', 'banner-landing']
CRITERIA = {'viewport': [1440, 1000], 'alignment_device_px': 0.01, 'segment': [0.2, 0.8],
            'edge_rgb_range': [226, 233], 'inside_rgb_min': 252, 'coverage': 0.95,
            'marker_rgb': [200, 20, 80], 'marker_tolerance': 3,
            'claim': 'defined_straight_segments_only', 'ancestor_transforms': 'unsupported'}

CAPTURE_JS = r'''
const fs=require('fs'),crypto=require('crypto');
const {chromium}=require(process.argv[1]);
const m=JSON.parse(process.argv[2]),out=process.argv[3];
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:m.dpr});
  const received=[];
  page.on('response',r=>{if(r.url()===m.bundle_url)received.push(r.body().then(b=>crypto.createHash('sha256').update(b).digest('hex')))});
  await page.goto(m.url,{waitUntil:'networkidle',timeout:15000});
  const read=()=>page.evaluate(ids=>ids.map(id=>{
   const matches=document.querySelectorAll(`[data-testid="ad-${id}"]`),e=matches[0];
   if(matches.length!==1)return {id,missing:true};
   const r=e.getBoundingClientRect();
   let transformed=false;for(let p=e;p;p=p.parentElement){if(getComputedStyle(p).transform!=='none')transformed=true;}
   return {id,rect:{x:r.x,y:r.y,width:r.width,height:r.height},
    transformed,
    running:e.getAnimations({subtree:true}).some(a=>a.playState==='running'),
    images:[...e.querySelectorAll('img')].map(i=>({src:i.currentSrc||i.src,complete:i.complete,width:i.naturalWidth}))};
  }),['card-large','card-medium','card-small','banner-small','banner-large','banner-landing']);
  const first=await read();await page.waitForTimeout(100);const before=await read();
  await page.screenshot({path:out+'/screen.png',fullPage:false});const after=await read();
  fs.writeFileSync(out+'/capture.json',JSON.stringify({url:page.url(),dpr:m.dpr,
   viewport:page.viewportSize(),first,before,after,bundle_hashes:await Promise.all(received)}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
'''


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def local_file(root: Path, name: str) -> Path:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError('invalid_build_path')
    result = root/name
    result.resolve().relative_to(root.resolve())
    if result.is_symlink() or not result.is_file():
        raise ValueError('build_input_missing')
    return result


def manifest_value(root: Path, raw: bytes, pin: str) -> dict:
    if sha(raw) != pin:
        raise ValueError('visual_manifest_digest_mismatch')
    m = json.loads(raw)
    if (m['schema_version'] != 'omc-visual-manifest/v1' or type(m['dpr']) is not int
            or m['dpr'] not in (1, 2) or type(m['images_per_variant']) is not int
            or m['images_per_variant'] < 1 or not m['build_inputs']):
        raise ValueError('unsupported_visual_manifest')
    urls = [urllib.parse.urlparse(m[k]) for k in ['url', 'bundle_url']]
    if any(u.scheme != 'http' or u.hostname not in {'127.0.0.1', 'localhost'}
           or u.username or u.password or u.fragment for u in urls) or urls[0].netloc != urls[1].netloc:
        raise ValueError('local_fixture_origin_required')
    for name, expected in m['build_inputs'].items():
        if sha(local_file(root, name).read_bytes()) != expected:
            raise ValueError('build_input_mismatch:' + name)
    if sha(local_file(root, m['bundle_path']).read_bytes()) != m['bundle_sha256']:
        raise ValueError('bundle_file_mismatch')
    return m


def judge(m: dict, capture: dict, screenshot: Path) -> list[dict]:
    from PIL import Image
    im = Image.open(screenshot).convert('RGB')
    dpr = m['dpr']
    if (im.size != (1440*dpr, 1000*dpr) or capture['url'] != m['url']
            or capture['viewport'] != {'width':1440, 'height':1000}
            or capture['dpr'] != dpr or capture['bundle_hashes'] != [m['bundle_sha256']]):
        raise ValueError('served_surface_mismatch')
    result = []
    for index, name in enumerate(VARIANTS):
        row = {'id':name, 'status':'unknown', 'reason':None}
        a, b, c = [capture[key][index] for key in ['first', 'before', 'after']]
        if a != b or b != c or b.get('running') or b.get('missing') or b.get('id') != name:
            row['reason'] = 'capture_unstable_or_target_missing'
        elif b.get('transformed'):
            row['reason'] = 'unsupported_ancestor_transform'
        else:
            rect = b['rect']; coords = [rect[k]*dpr for k in ['x','y','width','height']]
            if any(abs(v-round(v)) > CRITERIA['alignment_device_px'] for v in coords):
                row['reason'] = 'unaligned_geometry'
            elif len(b['images']) != m['images_per_variant']:
                row.update(status='failed', reason='expected_image_missing')
            elif any(not i['complete'] or i['width'] <= 0 for i in b['images']):
                row['reason'] = 'image_not_loaded'
            else:
                x, y, w, h = map(round, coords)
                if min(x,y) < 0 or min(w,h) < 12*dpr or x+w > im.width or y+h > im.height:
                    row['reason'] = 'unsupported_or_clipped_geometry'
                else:
                    row.update(status='passed', rect=rect, edges={}, samples={})
                    marker = [im.getpixel((x+w//2+dx,y+h//2+dy)) for dx in [-2,0,2] for dy in [-2,0,2]]
                    row['marker_samples'] = marker
                    if not all(all(abs(v-e)<=3 for v,e in zip(p,CRITERIA['marker_rgb'])) for p in marker):
                        row.update(status='failed', reason='image_marker_not_visible')
                    for side in ['left','right','top','bottom']:
                        length = h if side in ['left','right'] else w
                        positions = range(int(length*.2), int(length*.8))
                        edge, inside, points = [], [], []
                        for along in positions:
                            for depth in range(dpr):
                                if side == 'left': q=(x+depth,y+along); z=(x+dpr+depth,y+along)
                                elif side == 'right': q=(x+w-1-depth,y+along); z=(x+w-1-dpr-depth,y+along)
                                elif side == 'top': q=(x+along,y+depth); z=(x+along,y+dpr+depth)
                                else: q=(x+along,y+h-1-depth); z=(x+along,y+h-1-dpr-depth)
                                p, inner = im.getpixel(q), im.getpixel(z)
                                edge.append(all(226<=v<=233 for v in p)); inside.append(all(v>=252 for v in inner))
                                points.append({'edge_xy':q,'edge_rgb':p,'inside_xy':z,'inside_rgb':inner})
                        scores = {'edge':sum(edge)/len(edge), 'inside':sum(inside)/len(inside)}
                        row['edges'][side] = scores; row['samples'][side] = points
                        if min(scores.values()) < .95:
                            row.update(status='failed', reason=row['reason'] or 'border_segment_or_thickness_mismatch')
        result.append(row)
    return json.loads(json.dumps(result))


def assess_report(root: Path, report_file: Path | None, pin: str | None, manifest_pin: str) -> dict:
    try:
        if report_file is None or not pin or not manifest_pin:
            raise ValueError('required_visual_result_missing')
        raw = report_file.read_bytes()
        if sha(raw) != pin:
            raise ValueError('visual_report_digest_mismatch')
        r = json.loads(raw)
        if (r['schema_version'] != 'omc-visual-report/v1' or r['target'] != str(root.resolve())
                or r['manifest_sha256'] != manifest_pin or r['criteria'] != CRITERIA
                or r['code_snapshot_sha256'] != _code_snapshot_digest(root)):
            raise ValueError('visual_report_binding_mismatch')
        m = manifest_value(root, r['manifest_raw'].encode(), manifest_pin)
        for name, expected in r['evidence_hashes'].items():
            if name not in ['screen.png','capture.json'] or sha((report_file.parent/name).read_bytes()) != expected:
                raise ValueError('visual_evidence_mismatch')
        if set(r['evidence_hashes']) != {'screen.png','capture.json'}:
            raise ValueError('visual_evidence_missing')
        rows = judge(m, json.loads((report_file.parent/'capture.json').read_text()), report_file.parent/'screen.png')
        status = overall(rows)
        if rows != r['variants'] or status != r['status']:
            raise ValueError('visual_result_not_reproducible')
        return {'status':status, 'reason':None if status=='passed' else 'visual_surface_'+status}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, ZeroDivisionError, ImportError) as exc:
        return {'status':'unknown', 'reason':str(exc)}


def overall(rows: list[dict]) -> str:
    return 'failed' if any(r['status']=='failed' for r in rows) else 'unknown' if any(r['status']=='unknown' for r in rows) else 'passed'


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--playwright-module', required=True)
    args = p.parse_args(); root = args.target.resolve(); output = args.output_dir.resolve()
    report = {'schema_version':'omc-visual-report/v1','target':str(root),'status':'unknown',
              'reason':None,'criteria':CRITERIA,'manifest_sha256':args.manifest_sha256,'variants':[]}
    created = False
    try:
        try:
            relative = output.relative_to(root)
        except ValueError:
            relative = None
        if relative is not None and (not relative.parts or relative.parts[0] != '.omc'):
            raise ValueError('output_must_be_external_or_runtime')
        output.mkdir(parents=True, exist_ok=False)
        created = True
        raw = args.manifest.read_bytes(); m = manifest_value(root, raw, args.manifest_sha256)
        snapshot = _code_snapshot_digest(root)
        report.update(manifest_raw=raw.decode(), code_snapshot_sha256=snapshot)
        run = subprocess.run(['node','-e',CAPTURE_JS,args.playwright_module,json.dumps(m),str(output)],
                             text=True,capture_output=True,timeout=30)
        (output/'browser-output.txt').write_text(run.stdout+run.stderr)
        if run.returncode:
            raise ValueError('browser_capture_unavailable')
        manifest_value(root, args.manifest.read_bytes(), args.manifest_sha256)
        if snapshot != _code_snapshot_digest(root):
            raise ValueError('candidate_changed_during_capture')
        rows = judge(m,json.loads((output/'capture.json').read_text()),output/'screen.png')
        report.update(variants=rows,status=overall(rows),evidence_hashes={n:sha((output/n).read_bytes()) for n in ['screen.png','capture.json']})
    except (OSError,ValueError,KeyError,TypeError,AttributeError,IndexError,ZeroDivisionError,ImportError,subprocess.SubprocessError) as exc:
        report['reason'] = str(exc)
    # A new output directory is mandatory; never overwrite an earlier report.
    if not created:
        print(json.dumps({'status':'unknown','reason':report['reason']})); return 2
    try:
        with (output/'report.json').open('x') as f:
            f.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    except OSError:
        print(json.dumps({'status':'unknown','reason':'output_not_writable_or_exists'})); return 2
    print(json.dumps({'status':report['status'],'reason':report['reason'],'report':str(output/'report.json')}))
    return {'passed':0,'failed':1,'unknown':2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
