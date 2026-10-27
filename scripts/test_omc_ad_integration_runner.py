"""Public CLI regressions; no real Ad/model/integration execution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

RUNNER = Path(__file__).with_name('omc_ad_integration_runner.py')
DONE = '필수 검증 통과·작업 완료\n<!-- OMC_OUTPUT: {"outcome":"done"} -->\nVERDICT: PROCEED\n'


def test_runner_exists():
    assert RUNNER.is_file(), 'single-process integration finalizer is not implemented'


@pytest.fixture
def case(tmp_path):
    target = tmp_path/'candidate'; target.mkdir()
    subprocess.run(['git', 'init', '-q', str(target)], check=True)
    session = target/'.omc/state/sessions/new-session/session.json'; session.parent.mkdir(parents=True)
    session.write_text(json.dumps({'session_id':'new-session','confirmation':{'status':'confirmed'}}))
    verification = target/'.omc/verification.json'; verification.write_text(json.dumps({'session_id':'new-session'}))
    scripts = target/'scripts'; scripts.mkdir()
    guard = scripts/'omc_pipeline_guard.py'
    guard.write_text('import pathlib,sys\np=pathlib.Path.cwd()\n(p/"guard-called").write_text(str(p)+"\\n"+" ".join(sys.argv))\nprint('+repr(DONE)+')\n')
    config = {'target':str(target),'session_id':'new-session','verification_file':'.omc/verification.json',
              'test_argv':[sys.executable,'-c','print("test-raw-tail")'],
              'evidence_files':[], 'protected_roots':[str(RUNNER.parent.parent)],
              'test_timeout_seconds':2,'cleanup_seconds':1}
    cfg=tmp_path/'config.json'; out=target/'.omc/runs/archive'
    return target, guard, config, cfg, out


def launch(case, offsets=None, fault=None):
    target,guard,config,cfg,out=case;cfg.write_text(json.dumps(config))
    if offsets or fault:
        wrapper=cfg.with_name('entry.py')
        wrapper.write_text('import importlib.util,time,sys,os\n'
          +'s=importlib.util.spec_from_file_location("runner",'+repr(str(RUNNER))+');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
          +'class C(m.Clock):\n def __init__(self): self.offset=0\n def now(self): return time.monotonic()+self.offset\n def phase(self,name): self.offset += '+repr(offsets or {})+'.get(name,0)\n'
          +(fault or '')+'\nsys.exit(m.main(clock=C()))\n')
        cmd=[sys.executable,str(wrapper)]
    else:cmd=[sys.executable,str(RUNNER)]
    p=subprocess.run(cmd+['--config',str(cfg),'--output',str(out)],capture_output=True,text=True,timeout=12)
    final=json.loads((out/'final.json').read_text()) if (out/'final.json').is_file() else None
    return p,final


def consume(out):
    p=subprocess.run([sys.executable,str(RUNNER),'--read-result',str(out)],capture_output=True,text=True,timeout=3)
    return p,json.loads(p.stdout)


def test_public_cli_flows_and_freezes_inputs(case):
    target,guard,config,cfg,out=case
    p,r=launch(case)
    assert p.returncode==0,p.stderr+p.stdout
    assert r['overall_complete'] and r['archive_complete']
    assert (out/'raw/test.stdout').read_text().endswith('test-raw-tail\n')
    assert str(target.resolve()) in (target/'guard-called').read_text()
    assert str(out/'frozen/verification.json') in (target/'guard-called').read_text()
    before=(out/'frozen/verification.json').read_bytes()
    assert hashlib.sha256(before).hexdigest()==r['guard_input_sha256']
    assert consume(out)[0].returncode==0
    assert (out/'frozen/verification.json').read_bytes()==before


@pytest.mark.parametrize('offset,reason',[(10,'insufficient_remaining_budget'),(60,'deadline_exceeded'),(61,'deadline_exceeded')])
def test_public_cli_no_guard_at_budget_boundary(case,offset,reason):
    p,r=launch(case,{'frozen':offset})
    assert p.returncode!=0 and r['guard']['status']==reason
    assert not (case[0]/'guard-called').exists()
    assert not r['overall_complete']


def test_public_cli_just_inside_budget_calls_guard(case):
    p,r=launch(case,{'frozen':9})
    assert p.returncode==0 and r['guard']['status']=='passed'


@pytest.mark.parametrize('code,status',[
 ('raise RuntimeError("guard-original-error")','failed'),
 ('print("no completion claim")','invalid_result'),
 ('import time;time.sleep(5)','timeout')])
def test_public_cli_guard_failures_preserve_output(case,code,status):
    case[1].write_text(code)
    # Advance to leave 0.15 seconds of guard execution time while admission was valid.
    offsets={'guard_spawned':29.85} if status=='timeout' else None
    p,r=launch(case,offsets)
    assert p.returncode!=0 and r['guard']['status']==status
    assert (case[4]/'raw/guard.stderr').is_file()
    if status=='failed': assert 'guard-original-error' in (case[4]/'raw/guard.stderr').read_text()
    assert not r['overall_complete']


def test_public_cli_late_success_is_not_completion(case):
    p,r=launch(case,{'guard_returned':31})
    assert p.returncode!=0 and r['guard']['status']=='late_return'


def test_public_cli_guard_input_tamper_blocks_completion(case):
    case[1].write_text('import pathlib,sys\np=pathlib.Path(sys.argv[-1]);p.write_text("changed")\nprint('+repr(DONE)+')')
    p,r=launch(case)
    assert p.returncode!=0 and r['guard']['status']=='evidence_changed'


def test_public_cli_session_change_blocks_guard(case):
    case[2]['test_argv']=[sys.executable,'-c',
      'from pathlib import Path;Path(".omc/state/sessions/new-session/session.json").write_text("{}")']
    p,r=launch(case)
    assert p.returncode!=0 and r['guard']['status']=='target_session_changed'
    assert not (case[0]/'guard-called').exists()


def test_public_cli_term_ignored_child_is_killed(case):
    case[2]['test_timeout_seconds']=.15
    case[2]['test_argv']=[sys.executable,'-c',
      'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("before-kill",flush=True);time.sleep(10)']
    p,r=launch(case)
    assert p.returncode!=0 and r['test']['status']=='timeout'
    assert r['test']['cleanup']=='confirmed'
    assert 9 in r['test']['signals']
    assert 'before-kill' in (case[4]/'raw/test.stdout').read_text()


def test_public_cli_unconfirmed_writer_only_partial_snapshot(case):
    # Simulate an inaccessible owned group while a real controlled writer was running.
    fault='m.group_gone=lambda pgid: False\n'
    case[2]['test_timeout_seconds']=.1;case[2]['cleanup_seconds']=.2
    case[2]['test_argv']=[sys.executable,'-c',
      'import time\nwhile True: print("writer-data",flush=True);time.sleep(.01)']
    p,r=launch(case,fault=fault)
    assert p.returncode!=0 and not r['archive_complete'] and not r['overall_complete']
    assert r['guard']['status']=='writer_unconfirmed'
    assert not (case[0]/'guard-called').exists()
    assert r['raw_final_sha256'] is None
    assert r['partial_evidence']
    for row in r['partial_evidence']:
        data=(case[4]/row['path']).read_bytes()
        assert len(data)==row['size']<=1024*1024
        assert hashlib.sha256(data).hexdigest()==row['sha256']


@pytest.mark.parametrize('operation',['final','publication','freeze'])
def test_public_cli_storage_failure_never_claims_completion(case,operation):
    if operation=='freeze':
        fault='def broken_buffer(path,data): raise OSError(28,"injected disk full")\nm.write_buffer=broken_buffer\n'
    else:
        fault='original=m.write_json\ndef broken(path,value):\n if path.name=='+repr({'final':'final.tmp','publication':'publication.json'}[operation])+': raise OSError(28,"injected disk full")\n return original(path,value)\nm.write_json=broken\n'
    p,r=launch(case,fault=fault)
    assert p.returncode!=0
    assert 'injected disk full' in p.stdout+p.stderr
    assert consume(case[4])[0].returncode!=0
    assert (case[4]/'raw/test.stdout').is_file()


def test_public_cli_late_publication_rejected(case):
    p,r=launch(case,{'published':61})
    assert p.returncode!=0 and consume(case[4])[0].returncode!=0


def test_public_cli_consumer_rechecks_raw_hash(case):
    p,r=launch(case);assert p.returncode==0
    (case[4]/'raw/test.stdout').write_text('tampered')
    assert consume(case[4])[0].returncode!=0


def test_public_cli_target_must_be_separate(case):
    case[2]['protected_roots'].append(str(case[0]))
    p,r=launch(case)
    assert p.returncode!=0 and not (case[0]/'guard-called').exists()


def test_public_cli_original_input_change_also_blocks(case):
    case[1].write_text('from pathlib import Path\nPath(".omc/verification.json").write_text("{}")\nprint('+repr(DONE)+')')
    p,r=launch(case)
    assert p.returncode!=0 and r['guard']['status']=='evidence_changed'


def test_public_cli_rechecks_budget_at_launch(case):
    p,r=launch(case,{'guard_started':11})
    assert p.returncode!=0 and r['guard']['status']=='insufficient_remaining_budget'
    assert not (case[0]/'guard-called').exists()


def test_public_cli_partial_copy_is_bounded(case):
    case[2]['test_argv']=[sys.executable,'-c','print("x"*(2*1024*1024))']
    p,r=launch(case,fault='m.group_gone=lambda pgid: False\n')
    assert p.returncode!=0 and r['raw_final_sha256'] is None
    rows=r['partial_evidence']
    assert sum(x['size'] for x in rows)<=8*1024*1024
    assert any(x['observed_size']>x['size']==1024*1024 for x in rows)


def test_public_cli_evidence_files_are_pinned(case):
    source=case[0]/'.omc/evidence.txt';source.write_text('frozen-evidence')
    case[2]['evidence_files']=['.omc/evidence.txt']
    p,r=launch(case)
    assert p.returncode==0 and (case[4]/'frozen/evidence-0').read_text()=='frozen-evidence'
    assert str(source.resolve()) in r['input_sources']


def test_public_cli_existing_archive_is_not_overwritten(case):
    p,r=launch(case);assert p.returncode==0
    before=(case[4]/'final.json').read_bytes()
    p,r=launch(case)
    assert p.returncode!=0 and (case[4]/'final.json').read_bytes()==before


def test_public_cli_session_mismatch_never_calls_guard(case):
    case[2]['session_id']='another-session'
    p,r=launch(case)
    assert p.returncode!=0 and not (case[0]/'guard-called').exists()


def test_public_cli_guard_unconfirmed_only_partial(case):
    target=repr(str(case[0]/'guard-called'))
    fault='m.group_gone_original=m.group_gone\nm.group_gone=lambda pid: False if m.Path('+target+').exists() else m.group_gone_original(pid)\n'
    p,r=launch(case,fault=fault)
    assert p.returncode!=0 and r['guard']['cleanup']=='unconfirmed'
    assert r['partial_evidence'] and r['raw_final_sha256'] is None
    assert not r['archive_complete'] and not r['overall_complete']


def test_public_cli_pending_and_modified_final_are_rejected(case):
    p,r=launch(case);assert p.returncode==0
    (case[4]/'pending').touch()
    assert consume(case[4])[0].returncode!=0
    (case[4]/'pending').unlink()
    (case[4]/'final.json').write_text('{}')
    assert consume(case[4])[0].returncode!=0


@pytest.mark.parametrize('kind',['verification','evidence'])
def test_public_cli_replacement_between_read_and_pin_blocks(case,kind):
    target=case[0]
    if kind=='verification':
        source=target/'.omc/verification.json'
        replacement=json.dumps({'session_id':'new-session','changed_after_first_read':True}).encode()
    else:
        source=target/'.omc/input-evidence.txt';source.write_bytes(b'first-buffer')
        case[2]['evidence_files']=['.omc/input-evidence.txt'];replacement=b'replaced-after-first-read'
    fault=('original_read=m.bounded_read\nreplaced=False\ndef race(path,*args,**kwargs):\n global replaced\n data=original_read(path,*args,**kwargs)\n if not replaced and path==m.Path('+repr(str(source.resolve()))+'):\n  replaced=True\n  path.write_bytes('+repr(replacement)+')\n return data\nm.bounded_read=race\n')
    p,r=launch(case,fault=fault)
    assert p.returncode!=0, 'read/copy buffer and pinned original must describe the same version'
    assert not r['overall_complete'] and r['guard']['status']=='evidence_changed'
    assert not (target/'guard-called').exists()


def test_public_cli_verification_freeze_preserves_exact_bytes(case):
    raw=b'{ "session_id" : "new-session" }\n'
    (case[0]/'.omc/verification.json').write_bytes(raw)
    p,r=launch(case)
    assert p.returncode==0
    assert (case[4]/'frozen/verification.json').read_bytes()==raw
    assert r['guard_input_sha256']==hashlib.sha256(raw).hexdigest()
    assert r['input_sources'][str((case[0]/'.omc/verification.json').resolve())]==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('operation',['open','write','close'])
def test_public_cli_actual_partial_copy_failure_preserves_raw(case,operation):
    # Real child produces raw data; only termination observation and partial I/O fail.
    fault=('m.group_gone=lambda pid: False\noriginal_open=m.Path.open\n'
      +'class PartialFailure:\n def __init__(self,stream): self.stream=stream\n def __enter__(self): return self\n'
      +' def write(self,data):\n  if '+repr(operation)+ '=="write": raise OSError(5,"partial write fault")\n  return self.stream.write(data)\n'
      +' def __exit__(self,*args):\n  self.stream.close()\n  if '+repr(operation)+'=="close": raise OSError(5,"partial close fault")\n'
      +'def partial_open(path,mode="r",*args,**kwargs):\n if path.parent.name=="partial" and mode=="xb":\n  if '+repr(operation)+'=="open": raise OSError(5,"partial open fault")\n  return PartialFailure(original_open(path,mode,*args,**kwargs))\n return original_open(path,mode,*args,**kwargs)\nm.Path.open=partial_open\n')
    p,r=launch(case,fault=fault)
    assert p.returncode!=0
    assert 'partial '+operation+' fault' in p.stdout+p.stderr
    assert r['raw_final_sha256'] is None and not r['archive_complete'] and not r['overall_complete']
    assert (case[4]/'raw/test.stdout').read_text()=='test-raw-tail\n'
    assert consume(case[4])[0].returncode!=0



def test_public_cli_duplicate_input_source_cannot_override_pinned_hash(case):
    case[2]['evidence_files']=['.omc/verification.json']
    p,r=launch(case)
    assert p.returncode!=0
    assert 'duplicate input source' in p.stdout+p.stderr
    assert not (case[0]/'guard-called').exists()
    assert not r['overall_complete']
