"""Direct CLI regressions for fast/rest execution and isolated pilot evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).with_name("omc_test_suite.py")


def fixture_suite(root, fast="assert True", rest="assert True"):
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/test_fast.py").write_text("def test_fast():\n    " + fast + "\n")
    (root / "scripts/test_rest.py").write_text("def test_rest():\n    " + rest + "\n")
    return root


def invoke(root, record, *extra, mode="run"):
    return subprocess.run([sys.executable, str(SCRIPT), mode, "--target", str(root),
        "--record-dir", str(record), "--fast-file", "scripts/test_fast.py", *extra],
        capture_output=True, text=True, timeout=30)


def test_fast_failure_never_launches_rest(tmp_path):
    root = fixture_suite(tmp_path / "work", "assert False", "open('REST_RAN', 'w').write('bad')")
    result = invoke(root, tmp_path / "record")
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((tmp_path / "record/final.json").read_text())
    assert report["status"] == "blocked" and report["rest_calls"] == 0
    assert not (root / "REST_RAN").exists()


def test_success_executes_exact_selected_union(tmp_path):
    root = fixture_suite(tmp_path / "work")
    result = invoke(root, tmp_path / "record")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed"
    assert set(report["selected"]) == set(report["executed"])
    assert len(report["executed"]) == 2
    assert set(report["fast"]).isdisjoint(report["rest"])


@pytest.mark.parametrize("body", ["import pytest; pytest.skip('not allowed')",
    "import pytest; pytest.xfail('not allowed')", "assert False"])
def test_unapproved_results_block(tmp_path, body):
    root = fixture_suite(tmp_path / "work", body)
    result = invoke(root, tmp_path / "record")
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "blocked"


def test_collect_error_blocks_without_running_tests(tmp_path):
    root = fixture_suite(tmp_path / "work")
    (root / "scripts/test_rest.py").write_text("def broken(:\n")
    result = invoke(root, tmp_path / "record")
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["rest_calls"] == 0


def test_parameterized_and_new_cases_preserve_selection(tmp_path):
    root = fixture_suite(tmp_path / "work")
    (root / "scripts/test_rest.py").write_text("import pytest\n@pytest.mark.parametrize('x',[1,2])\ndef test_rest(x): assert x\n")
    result = invoke(root, tmp_path / "record")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert len(report["selected"]) == 3 and len(report["executed"]) == 3


def test_record_collision_is_rejected(tmp_path):
    root = fixture_suite(tmp_path / "work")
    record = tmp_path / "record"; record.mkdir(); (record / "keep").write_text("original")
    result = invoke(root, record)
    assert result.returncode != 0
    assert (record / "keep").read_text() == "original"


def test_pilot_serial_parallel_same_cases_and_preserved_source(tmp_path):
    root = fixture_suite(tmp_path / "work", "import time; time.sleep(.15)", "import time; time.sleep(.15)")
    spec = tmp_path / "pilot.json"
    spec.write_text(json.dumps({"cases":[{"nodeid":"scripts/test_fast.py::test_fast","group":"a"},
        {"nodeid":"scripts/test_rest.py::test_rest","group":"b"}]}))
    result = invoke(root, tmp_path / "record", "--pilot-file", str(spec), mode="pilot")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "measured" and report["source_preserved"]
    assert report["serial"]["executed"] == report["parallel"]["executed"]
    assert report["parallel"]["overlap"] and not report["serial"]["overlap"]


def test_pilot_unknown_nodeid_blocks_before_execution(tmp_path):
    root = fixture_suite(tmp_path / "work")
    spec = tmp_path / "pilot.json";spec.write_text(json.dumps({"cases":[
        {"nodeid":"scripts/test_fast.py::missing","group":"a"},
        {"nodeid":"scripts/test_rest.py::test_rest","group":"b"}]}))
    result = invoke(root, tmp_path / "record", "--pilot-file", str(spec), mode="pilot")
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["comparison_calls"] == 0


def load_runner():
    import importlib.util
    spec = importlib.util.spec_from_file_location('suite_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('selected,fast', [([], ['scripts/a.py']),
    (['scripts/a.py::x', 'scripts/a.py::x'], ['scripts/a.py']),
    (['scripts/a.py::x'], ['scripts/a.py', 'scripts/a.py']),
    (['scripts/a.py::x'], ['scripts/missing.py'])])
def test_partition_rejects_empty_duplicates_and_stale_files(selected, fast):
    with pytest.raises(ValueError):load_runner().partition(selected, fast)


def test_unknown_tests_are_kept_and_current_markers_preserved(tmp_path):
    root = fixture_suite(tmp_path/'work')
    (root/'scripts/test_rest.py').write_text("import pytest\n@pytest.mark.slow\ndef test_slow(): assert False\ndef test_new(): assert True\n")
    result = invoke(root, tmp_path/'record')
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['rest'] == ['scripts/test_rest.py::test_new']


def test_stdout_stderr_large_and_invalid_utf8_are_drained(tmp_path):
    runner = load_runner()
    result = runner.execute([sys.executable, '-c',
        "import os; os.write(1,b'x'*200000+b'\\xff'); os.write(2,b'y'*200000+b'\\xff')"], tmp_path, 5)
    assert result.returncode == 0
    assert len(result.stdout) == 200001 and result.stdout.endswith('\ufffd')
    assert len(result.stderr) == 200001 and result.stderr.endswith('\ufffd')


def test_timeout_preserves_partial_output_and_stops_owned_child(tmp_path):
    runner = load_runner()
    marker = tmp_path/'leaked'
    child = "import time; from pathlib import Path; time.sleep(1); Path(" + repr(str(marker)) + ").write_text('bad')"
    parent = "import subprocess,sys,time; print('partial',flush=True); subprocess.Popen([sys.executable,'-c',"+repr(child)+"]); time.sleep(10)"
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        runner.execute([sys.executable, '-c', parent], tmp_path, .2)
    assert b'partial' in exc.value.output
    import time
    time.sleep(1)
    assert not marker.exists()


def test_runner_parent_termination_stops_owned_children(tmp_path):
    root = fixture_suite(tmp_path/'work', "import time; time.sleep(10)")
    record = tmp_path/'record'
    p = subprocess.Popen([sys.executable,str(SCRIPT),'run','--target',str(root),
        '--record-dir',str(record),'--fast-file','scripts/test_fast.py'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    import time
    try:
        deadline = time.monotonic()+10
        while not (record/'fast').exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        assert (record/'fast').exists()
        p.terminate();stdout,stderr = p.communicate(timeout=5)
        assert p.returncode != 0
        assert (record/'final.json').is_file()
    finally:
        if p.poll() is None:p.kill();p.wait()


def test_global_junit_destination_is_not_inherited(tmp_path, monkeypatch):
    root = fixture_suite(tmp_path/'work')
    protected = tmp_path/'original.xml';protected.write_text('original')
    monkeypatch.setenv('PYTEST_ADDOPTS','--junitxml='+str(protected))
    result = invoke(root,tmp_path/'record')
    assert result.returncode == 0, result.stdout + result.stderr
    assert protected.read_text() == 'original'


def test_default_record_path_is_fresh_and_internal(tmp_path):
    root = fixture_suite(tmp_path/'work')
    records = []
    for _ in range(2):
        result = subprocess.run([sys.executable,str(SCRIPT),'run','--target',str(root),
            '--fast-file','scripts/test_fast.py'],capture_output=True,text=True,timeout=20)
        assert result.returncode == 0,result.stdout+result.stderr
        record = Path(json.loads(result.stdout)['record_dir'])
        assert record.is_relative_to(root/'.omc/test-suite') and (record/'final.json').is_file()
        records.append(record)
    assert records[0] != records[1]


def test_execution_cannot_silently_drop_collected_node(tmp_path):
    root=fixture_suite(tmp_path/'work')
    (root/'scripts/test_fast.py').write_text('def test_a(): assert True\ndef test_b(): assert True\n')
    (root/'scripts/conftest.py').write_text('def pytest_collection_modifyitems(config,items):\n    if not config.option.collectonly and len(items)>1: items.pop()\n')
    result=invoke(root,tmp_path/'record')
    assert result.returncode==1,result.stdout+result.stderr


@pytest.mark.parametrize('delay', [181, 900])
def test_pilot_copy_over_preparation_or_total_deadline_launches_no_comparison(tmp_path, monkeypatch, delay):
    from types import SimpleNamespace
    runner=load_runner();root=fixture_suite(tmp_path/'work');record=tmp_path/'record';record.mkdir()
    nodes=['scripts/test_fast.py::test_fast','scripts/test_rest.py::test_rest']
    spec=tmp_path/'pilot.json';spec.write_text(json.dumps({'cases':[
        {'nodeid':n,'group':str(i)} for i,n in enumerate(nodes)]}))
    clock=[0.0];monkeypatch.setattr(runner,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    original=runner.shutil.copytree;calls=[]
    def delayed(*args,**kwargs):
        result=original(*args,**kwargs);clock[0]+=delay;return result
    def call(root,folder,nodes,*,timeout,collect=False):
        if collect:return {'selected':globals_nodes}
        calls.append(nodes);folder.mkdir();(folder/'invocation.json').write_text(json.dumps(
            {'started_monotonic':clock[0],'ended_monotonic':clock[0]+.001}))
        return {'selected':nodes}
    globals_nodes=nodes
    monkeypatch.setattr(runner.shutil,'copytree',delayed)
    monkeypatch.setattr(runner,'call_pytest',call)
    state={}
    with pytest.raises(ValueError):
        runner.pilot(SimpleNamespace(target=root,record_dir=record,pilot_file=spec,timeout_sec=840),state)
    assert not calls, 'comparison was launched after preparation deadline'
    assert state['comparison_calls']==0


def test_prepare_deadline_preempts_blocking_io_and_restores_alarm():
    import signal
    import time
    runner=load_runner();previous=signal.getsignal(signal.SIGALRM)
    start=time.monotonic()
    with pytest.raises(ValueError,match='prepare_deadline_exceeded'):
        with runner.deadline_guard(start+.05,'prepare'):
            time.sleep(3)
    assert time.monotonic()-start<1
    assert signal.getsignal(signal.SIGALRM)==previous
    assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)


def test_cancel_between_spawn_and_registration_still_reaps_child(tmp_path,monkeypatch):
    import signal
    import time
    runner=load_runner();original=runner.subprocess.Popen;children=[]
    code="import time;from pathlib import Path;time.sleep(.4);Path('late-write').write_text('bad')"
    def spawn_then_cancel(*args,**kwargs):
        p=original(*args,**kwargs);children.append(p)
        signal.raise_signal(signal.SIGTERM)
        return p
    monkeypatch.setattr(runner.subprocess,'Popen',spawn_then_cancel)
    try:
        with pytest.raises(SystemExit):
            with runner.owned_process_signals():
                runner.execute([sys.executable,'-c',code],tmp_path,3)
        time.sleep(.6)
        assert not (tmp_path/'late-write').exists(),'spawn/registration cancellation window leaked child'
    finally:
        for p in children:
            if p.poll() is None:p.kill()
            p.wait()


def test_owned_child_keeps_sigterm_available_for_graceful_cleanup(tmp_path):
    runner=load_runner()
    code="import signal,time,sys;from pathlib import Path;signal.signal(signal.SIGTERM,lambda *a:(Path('graceful').write_text('closed'),sys.exit(0)));time.sleep(5)"
    with pytest.raises(subprocess.TimeoutExpired):
        with runner.owned_process_signals():
            runner.execute([sys.executable,'-c',code],tmp_path,.3)
    assert (tmp_path/'graceful').exists(),'child inherited blocked SIGTERM'


def test_prepare_alarm_during_spawn_does_not_orphan_child(tmp_path,monkeypatch):
    import signal
    import time
    runner=load_runner();original=runner.subprocess.Popen;children=[]
    def spawn_then_expire(*args,**kwargs):
        p=original(*args,**kwargs);children.append(p)
        signal.raise_signal(signal.SIGALRM)
        return p
    monkeypatch.setattr(runner.subprocess,'Popen',spawn_then_expire)
    try:
        with pytest.raises(ValueError,match='prepare_deadline_exceeded'):
            with runner.deadline_guard(time.monotonic()+5,'prepare'):
                runner.execute([sys.executable,'-c',"import time;from pathlib import Path;time.sleep(.4);Path('alarm-leak').write_text('bad')"],tmp_path,3)
        time.sleep(.6)
        assert not (tmp_path/'alarm-leak').exists(),'preparation alarm orphaned unregistered child'
    finally:
        for p in children:
            if p.poll() is None:p.kill()
            p.wait()
