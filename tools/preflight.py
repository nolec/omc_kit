"""Preparation only by default; this tool does not grant quality execution approval."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
import selectors
import uuid


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):h.update(block)
    return h.hexdigest()


def verify(manifest):
    root=Path(manifest['root']).resolve(strict=True)
    if manifest.get('schema_version')!='omc-retained-dependencies/v1' or not manifest.get('files'):
        raise ValueError('invalid_manifest')
    for rel,pin in manifest['files'].items():
        path=root/rel
        if Path(rel).is_absolute() or '..' in Path(rel).parts:raise ValueError('invalid_file_path')
        # Even an internal link must resolve inside this retained copy.
        path.resolve(strict=True).relative_to(root)
        if not path.is_file() or sha(path)!=pin:raise ValueError('missing_or_changed_file: '+rel)
    for rel,mode in manifest.get('file_modes',{}).items():
        if (root/rel).stat().st_mode!=mode:raise ValueError('file_mode_changed: '+rel)
    for rel,target in manifest.get('links',{}).items():
        path=root/rel
        if not path.is_symlink() or os.readlink(path)!=target:raise ValueError('link_changed: '+rel)
        path.resolve(strict=True).relative_to(root)
    runtime=Path(manifest['runtime_executable'])
    if not runtime.is_absolute() or not os.access(runtime,os.X_OK) or sha(runtime)!=manifest['runtime_sha256']:
        raise ValueError('runtime_binding_mismatch')
    argv=manifest['probe_argv']
    if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv) or argv[0]!=str(runtime):
        raise ValueError('probe_argv_mismatch')
    timeout=manifest['probe_timeout_seconds']
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=60:raise ValueError('invalid_timeout')
    script=Path(argv[1]);script.resolve(strict=True).relative_to(root)
    if script.relative_to(root).as_posix() not in manifest['files']:raise ValueError('probe_script_unbound')
    if set(manifest.get('environment',{}))-{'PLAYWRIGHT_BROWSERS_PATH','ESBUILD_BINARY_PATH'}:raise ValueError('undeclared_environment_override')
    for value in manifest.get('environment',{}).values():Path(value).resolve(strict=True).relative_to(root)
    for item in manifest.get('git_inputs',[]):
        repo=(root/item['path']).resolve(strict=True);repo.relative_to(root)
        observed=subprocess.check_output(['git','--no-optional-locks','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
        if observed!=item['commit'] or (repo/'.git/objects/info/alternates').exists():raise ValueError('git_input_binding_mismatch')
    return root


def identity(pid):
    observed=subprocess.run(['ps','-o','pid=,ppid=,pgid=,lstart=,stat=','-p',str(pid)],capture_output=True,text=True,timeout=2)
    if observed.returncode==1 and not observed.stdout.strip() and not observed.stderr.strip():return None
    if observed.returncode:raise ValueError('process_identity_unavailable')
    parts=observed.stdout.split()
    return dict(pid=int(parts[0]),ppid=int(parts[1]),pgid=int(parts[2]),started=' '.join(parts[3:-1]),state=parts[-1])


def group_state(pgid):
    observed=subprocess.run(['ps','-o','pid=,pgid=,stat=','-g',str(pgid)],capture_output=True,text=True,timeout=2)
    if observed.returncode==1 and not observed.stdout.strip() and not observed.stderr.strip():return 'absent'
    if observed.returncode:raise ValueError('process_group_unavailable')
    members=[line.split() for line in observed.stdout.splitlines() if line.strip()]
    return 'terminated' if all(row[2].startswith('Z') for row in members) else 'live'


def process(argv,root,timeout,environment=None,record_prefix=None,require_browser_ownership=False):
    prefix=Path(record_prefix);streams={};files={};buffers={name:bytearray() for name in ('stdout','stderr','ownership')}
    for name in buffers:
        path=Path(str(prefix)+'.'+name);files[name]=path.open('xb');streams[name]=str(path)
    read_fd,write_fd=os.pipe();ack_read,ack_write=os.pipe();token=uuid.uuid4().hex;selector=selectors.DefaultSelector();owned=[];errors=[];signals=[]
    proc=None;timed_out=False;interrupted=False;shutdown=None;node_killed=False;browser_killed=set();ownership_buffer=b'';capture_complete=True
    def signal_group(pgid,sig,scope):
        try:os.killpg(pgid,sig);outcome='sent'
        except ProcessLookupError:outcome='absent'
        signals.append(dict(pgid=pgid,signal=signal.Signals(sig).name,scope=scope,outcome=outcome))
    def observe_group(pgid):
        try:return group_state(pgid)
        except Exception as exc:
            errors.append('group_observation_failed: '+str(exc));return 'unknown'
    def signal_browser(owner,sig):
        try:current=identity(owner['pid'])
        except Exception as exc:
            errors.append('identity_observation_failed: '+str(exc));return
        if current is not None and (current['pgid']!=owner['pgid'] or current['started']!=owner['started']):
            errors.append('browser_identity_changed');return
        if observe_group(owner['pgid'])=='live':signal_group(owner['pgid'],sig,'chromium')
    try:
        if identity(os.getpid()) is None:raise ValueError('owner_identity_unavailable')
        env={**os.environ,'DEBUG':'pw:browser','NODE_OPTIONS':'','NODE_PATH':'',**(environment or {}),'OMC_PREFLIGHT_OWNERSHIP_FD':str(write_fd),'OMC_PREFLIGHT_OWNERSHIP_TOKEN':token,'OMC_PREFLIGHT_OWNERSHIP_ACK_FD':str(ack_read)}
        proc=subprocess.Popen(tuple(argv),cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,pass_fds=(write_fd,ack_read),env=env)
        os.close(write_fd);write_fd=None;os.close(ack_read);ack_read=None
        node_identity=dict(pid=proc.pid,pgid=proc.pid,owner_pid=os.getpid())
        for pipe,name in ((proc.stdout,'stdout'),(proc.stderr,'stderr'),(read_fd,'ownership')):
            fd=pipe if isinstance(pipe,int) else pipe.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ,name)
        deadline=time.monotonic()+timeout
        while True:
            now=time.monotonic();code=proc.poll()
            if shutdown is None and now>=deadline:
                timed_out=True;shutdown=now;signal_group(proc.pid,signal.SIGTERM,'node')
                for owner in owned:signal_browser(owner,signal.SIGTERM)
            if shutdown is not None:
                if now>=shutdown+.25:
                    for owner in owned:
                        if owner['pid'] not in browser_killed:
                            signal_browser(owner,signal.SIGKILL);browser_killed.add(owner['pid'])
                if now>=shutdown+5 and observe_group(proc.pid)=='live' and not node_killed:
                    signal_group(proc.pid,signal.SIGKILL,'node');node_killed=True
                if now>=shutdown+10:
                    capture_complete=not selector.get_map();break
            if code is not None and not selector.get_map():break
            try:ready=selector.select(.05)
            except KeyboardInterrupt:
                interrupted=True
                if shutdown is None:
                    shutdown=time.monotonic();signal_group(proc.pid,signal.SIGTERM,'node')
                continue
            for key,_ in ready:
                try:chunk=os.read(key.fd,65536)
                except BlockingIOError:continue
                if not chunk:selector.unregister(key.fd);continue
                name=key.data;buffers[name].extend(chunk);files[name].write(chunk);files[name].flush()
                if name!='ownership':continue
                ownership_buffer+=chunk
                while b'\n' in ownership_buffer:
                    line,ownership_buffer=ownership_buffer.split(b'\n',1)
                    try:
                        event=json.loads(line)
                        if (event.get('kind')!='chromium_spawn' or event.get('token')!=token or event.get('owner_pid')!=proc.pid
                            or type(event.get('pid')) is not int or event['pid']<=1 or event.get('pgid')!=event['pid']):raise ValueError('invalid_browser_ownership')
                        current=identity(event['pid'])
                        if current is None or current['ppid']!=proc.pid or current['pgid']!=event['pgid']:raise ValueError('browser_ownership_unconfirmed')
                        if any(x['pid']==event['pid'] for x in owned):raise ValueError('duplicate_browser_ownership')
                        owner=dict(current,verified=True);owned.append(owner)
                        # The launcher cannot return or exit until this identity is retained.
                        os.write(ack_write,b"1")
                        if shutdown is not None:signal_browser(owner,signal.SIGTERM)
                    except Exception as exc:errors.append(str(exc))
        if ownership_buffer:errors.append('partial_browser_ownership')
        if proc.poll() is None or observe_group(proc.pid)=='live':signal_group(proc.pid,signal.SIGKILL,'node')
        proc.wait(timeout=5)
        for owner in owned:
            state=observe_group(owner['pgid'])
            if state=='live':signal_browser(owner,signal.SIGKILL)
            until=time.monotonic()+2
            while observe_group(owner['pgid'])=='live' and time.monotonic()<until:time.sleep(.02)
            owner['termination_confirmed']=observe_group(owner['pgid']) in ('absent','terminated')
        cleanup_confirmed=(proc.poll() is not None and observe_group(proc.pid) in ('absent','terminated') and capture_complete and all(x['termination_confirmed'] for x in owned) and not errors)
        if require_browser_ownership and not owned:cleanup_confirmed=False;errors.append('browser_ownership_missing')
        result=dict(argv=list(argv),pid=proc.pid,pgid=proc.pid,node_identity=node_identity,exit=proc.returncode,timed_out=timed_out,interrupted=interrupted,
                    owned_browsers=owned,signals=signals,cleanup_forced=bool(signals),cleanup_confirmed=cleanup_confirmed,cleanup_errors=errors,capture_complete=capture_complete)
        for name in buffers:
            result[name]=bytes(buffers[name]).decode(errors='replace');result[name+'_path']=streams[name];result[name+'_sha256']=sha(Path(streams[name]))
        return result
    finally:
        selector.close()
        if write_fd is not None:os.close(write_fd)
        os.close(read_fd)
        if ack_read is not None:os.close(ack_read)
        os.close(ack_write)
        if proc is not None:
            if proc.poll() is None:
                signal_group(proc.pid,signal.SIGKILL,'node');proc.wait(timeout=5)
            for owner in owned:
                if observe_group(owner['pgid'])=='live':signal_browser(owner,signal.SIGKILL)
            proc.stdout.close();proc.stderr.close()
        for stream in files.values():stream.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--record',type=Path,required=True);p.add_argument('--allow-continuation',action='store_true');args=p.parse_args()
    args.record.parent.mkdir(parents=True,exist_ok=True)
    try:record=args.record.open('x')
    except OSError as exc:
        print(json.dumps(dict(status='blocked',reason='record_unavailable',continuation_calls=0)))
        return 1
    result=dict(status='blocked',continuation_calls=0,started=time.time())
    try:
        raw=args.manifest.read_bytes();pin=hashlib.sha256(raw).hexdigest()
        if pin!=args.manifest_sha256:raise ValueError('manifest_binding_mismatch')
        m=json.loads(raw);root=verify(m)
        result['manifest_sha256']=pin
        probe=process(m['probe_argv'],root,m['probe_timeout_seconds'],m.get('environment'),str(args.record)+'.probe',m.get('require_browser_ownership',False));result['probe']=probe
        if probe['exit']!=0 or probe['timed_out'] or probe['interrupted'] or not probe['cleanup_confirmed'] or probe['cleanup_forced']:raise ValueError('runtime_probe_failed')
        observed=json.loads(probe['stdout'])
        for flag in ['modules_loaded','esbuild_processed','browser_started','browser_closed']:
            if observed.get(flag) is not True:raise ValueError('runtime_condition_failed: '+flag)
        result['runtime']=observed
        # A successful probe never excuses mutation during preparation.
        if sha(args.manifest)!=pin:raise ValueError('manifest_changed_during_probe')
        verify(m)
        if args.allow_continuation:
            command=m.get('continuation')
            if not isinstance(command,list) or not command or not all(isinstance(x,str) and x for x in command):raise ValueError('continuation_not_authorized')
            result['continuation_calls']=1
            result['continuation']=process(command,root,30,m.get('environment'),str(args.record)+'.continuation')
            continued=result['continuation']
            if continued['exit']!=0 or continued['timed_out'] or continued['interrupted'] or not continued['cleanup_confirmed'] or continued['cleanup_forced']:raise ValueError('continuation_failed')
        result['status']='prepared'
    except Exception as exc:
        result['reason']=type(exc).__name__+': '+str(exc)
    result['finished']=time.time()
    with record:json.dump(result,record,ensure_ascii=False,indent=2)
    print(json.dumps(dict(status=result['status'],record=str(args.record),continuation_calls=result['continuation_calls']),ensure_ascii=False))
    return 0 if result['status']=='prepared' else 1

if __name__=='__main__':raise SystemExit(main())
