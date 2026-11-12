"""Exercise final native-event collection and the public recompare decision."""
import json
import subprocess
import sys
import time
import contextlib
import io
from pathlib import Path

import pytest
import omc_pipeline_guard as guard

SCRIPT = Path(__file__).with_name('omc_workflow_validation.py')


def response(first='구현됨·검증 미완료', outcome='blocked'):
    return first + '\n<!-- OMC_OUTPUT: ' + json.dumps({'schema_version':'omc-output/v1','stage':'task','outcome':outcome,'risk':'medium','next_skill':None if outcome=='blocked' else 'omc-review','user_selection_needed':outcome=='blocked','reason_code':'verification_incomplete' if outcome=='blocked' else None}) + ' -->\nVERDICT: ' + ('BLOCK' if outcome=='blocked' else 'PROCEED')


def test_common_comparison_requires_new_explicit_policy_and_keeps_old_gate(tmp_path):
    import omc_workflow_validation as v
    assert v.assess_common_validation(tmp_path, [], {}, '0'*64)['comparison_entry']=='hold'
    result,dest=collect(tmp_path,response())
    assert 'common_validation' not in json.loads((dest/'assessment.json').read_text())


def test_public_common_cli_renders_three_distinct_statuses(tmp_path):
    runtime=tmp_path/'.omc';runtime.mkdir()
    events=runtime/'events.jsonl';events.write_text(json.dumps({'type':'result','result':'필수 검증 통과·작업 완료'})+'\n')
    selection=runtime/'selection.json';selection.write_text('{}')
    dest=runtime/'common'
    result=subprocess.run([sys.executable,str(SCRIPT),'compare-entry','--target',str(tmp_path),
        '--events-file',str(events),'--selection-file',str(selection),'--policy-sha256','0'*64,
        '--output-dir',str(dest)],capture_output=True,text=True)
    assert result.returncode==1
    text=(dest/'report.txt').read_text()
    assert '공통 업무 검증:' in text and '도구 내부 절차:' in text and '비교 진입:' in text
    assert (dest/'final.txt').read_text()=='필수 검증 통과·작업 완료'
    assert (dest/'native-events.jsonl').read_bytes()==events.read_bytes()
    report=json.loads((dest/'assessment.json').read_bytes())
    assert report['common_work']=='unknown' and report['comparison_entry']=='hold'


@pytest.mark.parametrize('raw', [b'{"type":"system"}\ninvalid-tail', b'1\n', b'[]\n'])
def test_common_cli_keeps_malformed_stream_raw_and_rejects_root_output(tmp_path, raw):
    runtime=tmp_path/'.omc';runtime.mkdir()
    events=runtime/'events.jsonl';events.write_bytes(raw)
    dest=runtime/'invalid'
    args=[sys.executable,str(SCRIPT),'compare-entry','--target',str(tmp_path),
        '--events-file',str(events),'--policy-sha256','0'*64,'--output-dir']
    result=subprocess.run([*args,str(dest)],capture_output=True,text=True)
    assert result.returncode==1
    assert (dest/'native-events.jsonl').read_bytes()==events.read_bytes()
    assert json.loads((dest/'assessment.json').read_bytes())['comparison_entry']=='hold'
    outside=tmp_path/'outside'
    rejected=subprocess.run([*args,str(outside)],capture_output=True,text=True)
    assert rejected.returncode==1 and 'home_path_must_be_runtime' in rejected.stdout
    assert not outside.exists()


def collect(tmp_path, text, registered=True, external=False, expected="blocked", scope_defect=None, detached=False, detached_defect=None):
    root=tmp_path/'work';root.mkdir(exist_ok=True)
    state=root/'.omc';state.mkdir(exist_ok=True)
    (state/'pipeline_session.json').write_text(json.dumps({'contract_confirmed':registered,'session_id':'s','red_session_id':'s','red_done_tests':['test_app.js']}))
    verification=root/'.omc/verification/s.json';verification.parent.mkdir(exist_ok=True);verification.write_text('{}')
    (state/'state').mkdir(exist_ok=True)
    (state/'state/latest.json').write_text(json.dumps({'latest_session_id':'s','latest_confirmed_session_id':'s'}))
    basis={'session_id':'s','revision':1,'request':'feature','approval_reference':'fixture approval',
           'checks':[{'id':'browser-home','target':'actual home','method':'browser','pass_condition':'visible','target_version':'candidate'}]}
    proposal=root/'proposal.json';proposal.write_text(json.dumps(basis));assert guard.cmd_approve_checks(root,proposal)==0
    pipeline=json.loads((state/'pipeline_session.json').read_text())
    pipeline.update(required_checks=['browser-home'],basis_sha256=guard._basis_digest(basis),started_at=time.time())
    (state/'pipeline_session.json').write_text(json.dumps(pipeline))
    verification.write_text(json.dumps({'session_id':'s','basis_sha256':guard._basis_digest(basis),
        'implementation':{'status':'applied','evidence':'patch'},
        'checks':{'browser-home':{'status':'passed' if expected=='done' else 'unrun','evidence':'output',
                                 'definition_sha256':guard._check_digest(basis['checks'][0])}}}))
    if scope_defect:
        data=json.loads(verification.read_text())
        if scope_defect=='unrun':
            data['checks']['browser-home']['status']='unrun'
        elif scope_defect in {'failed','mismatch'}:
            data['checks']['browser-home']['status']=scope_defect
        elif scope_defect=='scope':
            data['checks']['browser-home']['definition_sha256']='controlled harness'
        elif scope_defect=='revision':
            data['basis_sha256']='old revision'
        elif scope_defect=='missing':
            data['checks']={}
        verification.write_text(json.dumps(data))
    if scope_defect=='unstaged-gate':
        subprocess.run(['git','init','-q'],cwd=root,check=True)
        source=root/'app.py';source.write_text('value=1\n')
        subprocess.run(['git','add','app.py'],cwd=root,check=True)
        pipeline['worktree_baseline']={}
        pipeline['started_at']=time.time()
        (state/'pipeline_session.json').write_text(json.dumps(pipeline))
        data=json.loads(verification.read_text());data['checks']['browser-home']['status']='passed';verification.write_text(json.dumps(data))
        source.write_text('value=2\n')
    data=json.loads(verification.read_text())
    for item in data['checks'].values():
        item['code_snapshot_sha256']=guard._code_snapshot_digest(root)
    verification.write_text(json.dumps(data))
    actual_gate_output=None
    if scope_defect=='unstaged-gate':
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer):
            assert guard.cmd_complete_task(root,verification)==1
        actual_gate_output=buffer.getvalue()
        text=actual_gate_output
    path=str(tmp_path/'outside.json') if external else str(verification)
    detached_meta=None
    if detached:
        import hashlib
        ctx={'output_mode':'detached_v1','stage':'task','work_id':'s','session_id':'s','report_id':'final',
             'deadline_epoch':time.time()+60,'references':[], 'native_call_reference':{'invocation_id':'scheduled-final'}}
        cp=root/'.omc/output-context.json';cp.write_text(json.dumps(ctx));pin=hashlib.sha256(cp.read_bytes()).hexdigest()
        command='python3 scripts/omc_pipeline_guard.py complete-task --verification-file '+str(verification)+' --output-mode detached_v1 --output-context '+str(cp)+' --output-context-sha256 '+pin
        actual=subprocess.run([sys.executable,str(SCRIPT.with_name('omc_pipeline_guard.py')),'--target',str(root),
            'complete-task','--verification-file',str(verification),'--output-mode','detached_v1',
            '--output-context',str(cp),'--output-context-sha256',pin],capture_output=True,text=True)
        assert actual.returncode == (0 if expected == 'done' else 1), actual.stdout + actual.stderr
        from omc_output_contract import record_path
        mp=record_path(root,ctx);mh=hashlib.sha256(mp.read_bytes()).hexdigest()
        detached_meta=(cp,pin,mp,mh,command,actual.stdout)
        text=actual.stdout
        if detached_defect=='display':text+='다른 결과\n'
        if detached_defect=='missing_record':mp.unlink()
        if detached_defect=='model_source':
            doc=json.loads(mp.read_bytes());doc['source_kind']='model_declared';mp.write_text(json.dumps(doc));mh=hashlib.sha256(mp.read_bytes()).hexdigest()
            detached_meta=(cp,pin,mp,mh,command,actual.stdout)
        if detached_defect in {'plan_record', 'observation_record'}:
            from omc_output_contract import render_envelope
            ctx.update(stage='plan', report_id='plan-declaration')
            cp = root/'.omc/plan-context.json'; cp.write_text(json.dumps(ctx))
            if detached_defect == 'observation_record':
                pipeline=json.loads((root/'.omc/pipeline_session.json').read_text())
                pipeline.update(owner_work_id='s',owner_session_id='s',started_at=time.time()-28900)
                (root/'.omc/pipeline_session.json').write_text(json.dumps(pipeline))
                (root/'.omc/state/pending-completion.json').write_text(json.dumps({'work_id':'s','session_ids':['s']}))
                folder=root/'.omc/state/sessions/s';folder.mkdir(parents=True,exist_ok=True)
                (folder/'session.json').write_text(json.dumps({'session_id':'s','work_id':'s','role_ids':['senior_coding'],'confirmation':{'status':'confirmed'}}))
                prepared=subprocess.run([sys.executable,str(SCRIPT.with_name('omc_output_contract.py')),'prepare',
                    '--target',str(root),'--stage','plan','--report-id','observation-collector','--budget-sec','60'],capture_output=True,text=True)
                assert prepared.returncode==0,prepared.stderr
                meta=json.loads(prepared.stdout);cp=Path(meta['output_context']);ctx=json.loads(cp.read_bytes())
            pin = hashlib.sha256(cp.read_bytes()).hexdigest()
            source = root/'.omc/plan-source.txt'
            source.write_text('계획 검토 요청\n' + render_envelope(
                stage='plan', verdict='HOLD', risk='high', next_skill='omc-critique',
                user_selection_needed=True, reason_code='review_required'))
            source_pin = hashlib.sha256(source.read_bytes()).hexdigest()
            args = ['publish', '--target', str(root), '--output-context', str(cp),
                    '--output-context-sha256', pin, '--source-file', str(source),
                    '--source-sha256', source_pin]
            published = subprocess.run([sys.executable, str(SCRIPT.with_name('omc_output_contract.py')), *args],
                                       capture_output=True, text=True)
            assert published.returncode == 0, published.stderr
            mp = record_path(root, ctx); mh = hashlib.sha256(mp.read_bytes()).hexdigest()
            command = 'python3 scripts/omc_output_contract.py ' + ' '.join(args)
            text = published.stdout
            detached_meta = (cp, pin, mp, mh, command, text)
    events=[]
    for i,(cmd,output) in enumerate([
        ('python3 scripts/omc_pipeline_guard.py contract-done','CONTRACT 확인 완료'),
        ('python3 scripts/omc_pipeline_guard.py red-done test_app.js','RED 완료 등록'),
        ('python3 scripts/omc_pipeline_guard.py complete-task --verification-file '+path,response(first=guard.evaluate_task_completion(root,verification,expected_session_id='s')['first'], outcome=expected))]):
        if detached and 'complete-task' in cmd:
            cmd,output=detached_meta[-2:]
            if detached_defect=='missing_call':continue
        if scope_defect=='skip-complete' and 'complete-task' in cmd:
            continue
        if actual_gate_output and 'complete-task' in cmd:
            output=actual_gate_output
        events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Bash','input':{'command':cmd}}]}}, {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':output}]}}]
    if detached:
        events[-1]['message']['content'][0]['exit_code'] = (2 if detached_defect == 'delivery_exit'
            else 0 if expected == 'done' or detached_defect in {'plan_record','observation_record'} else 1)
        events[-1]['message']['content'][0]['is_error'] = events[-1]['message']['content'][0]['exit_code'] != 0
    events += [{'type':'result','subtype':'success','result':text}]
    source=tmp_path/'events.jsonl';source.write_text('\n'.join(json.dumps(e) for e in events))
    dest=tmp_path/'collected'
    extra=[]
    if detached:
        cp,pin,mp,mh,_,_=detached_meta
        extra=['--output-mode','detached_v1','--output-context',str(cp),'--output-context-sha256',pin,'--output-record',str(mp),'--output-record-sha256',mh]
    result=subprocess.run([sys.executable,str(SCRIPT),'--target',str(root),'--events-file',str(source),'--session-id','s','--verification-file',str(verification),'--expected-outcome',expected,'--output-dir',str(dest),*extra],capture_output=True,text=True)
    return result,dest


def test_completion_command_omission_holds_even_with_passed_evidence(tmp_path):
    result,dest=collect(tmp_path,response(first='필수 검증 통과·작업 완료',outcome='done'),expected='done',scope_defect='skip-complete')
    assert result.returncode==1
    assert json.loads((dest/'assessment.json').read_text())['required']['procedure_calls']=='unknown'


def test_truthful_gate_block_for_unstaged_code_is_not_a_false_completion(tmp_path):
    result,dest=collect(tmp_path,response(),scope_defect='unstaged-gate')
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final']=='passed'


@pytest.mark.parametrize('defect',['failed','mismatch'])
def test_failure_or_mismatch_cannot_be_softened_to_unrun_report(tmp_path,defect):
    result,dest=collect(tmp_path,response(),scope_defect=defect)
    assert result.returncode==1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final']=='failed'


@pytest.mark.parametrize('defect,first',[('failed','검증 실패·수정 필요'),('mismatch','증거 불일치·재검증 필요')])
def test_exact_blocked_state_is_accepted_and_raw_is_preserved(tmp_path,defect,first):
    raw=response(first=first)
    result,dest=collect(tmp_path,raw,scope_defect=defect)
    assert result.returncode==0,result.stdout+result.stderr
    assert (dest/'final.txt').read_text()==raw


def test_unrun_cannot_be_misreported_as_failed(tmp_path):
    result,dest=collect(tmp_path,response(first='검증 실패·수정 필요'))
    assert result.returncode==1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final']=='failed'


@pytest.mark.parametrize('defect',['unrun','scope','revision','missing'])
def test_done_claim_is_checked_against_approved_scope_not_caller_expectation(tmp_path,defect):
    raw=response(first='필수 검증 통과·작업 완료',outcome='done')
    result,dest=collect(tmp_path,raw,expected='done',scope_defect=defect)
    assert result.returncode==1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final']=='failed'
    assert (dest/'final.txt').read_text()==raw


def test_collector_preserves_native_final_and_allows_format_only_issue(tmp_path):
    raw='---\n'+response()
    result,dest=collect(tmp_path,raw)
    assert result.returncode==0,result.stderr
    assert (dest/'final.txt').read_text()==raw
    assessment=json.loads((dest/'assessment.json').read_text())
    assert assessment['recompare_entry']=='allow'
    assert assessment['format_quality']['first_line'] is False


@pytest.mark.parametrize('registered,external,outcome',[(False,False,'blocked'),(True,True,'blocked'),(True,False,'done')])
def test_required_defect_holds_even_when_format_is_valid(tmp_path,registered,external,outcome):
    result,dest=collect(tmp_path,response(outcome=outcome),registered,external)
    assert result.returncode==1
    assert json.loads((dest/'assessment.json').read_text())['recompare_entry']=='hold'


def test_conflicting_body_cannot_pass_with_blocked_envelope(tmp_path):
    result,dest=collect(tmp_path,response(first='필수 검증 통과·작업 완료'))
    assert result.returncode==1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final']=='failed'


def test_duplicate_status_is_quality_only(tmp_path):
    result,dest=collect(tmp_path,'구현됨·검증 미완료\n'+response())
    assert result.returncode==0,result.stderr
    assert json.loads((dest/'assessment.json').read_text())['format_quality']['unique_status'] is False


def test_retained_native_response_is_checked_without_rewriting(tmp_path):
    raw=(SCRIPT.parent/'fixtures/omc_workflow_format_failure.txt').read_text()
    result,dest=collect(tmp_path,raw)
    assert result.returncode==0,result.stderr
    assert (dest/'final.txt').read_text()==raw
    assessment=json.loads((dest/'assessment.json').read_text())
    assert assessment['required']['truthful_final']=='passed'
    assert assessment['format_quality']['first_line'] is False
    assert assessment['format_quality']['unique_status'] is False


def test_invalid_native_stream_records_hold(tmp_path):
    source=tmp_path/'events.jsonl';source.write_text('{bad')
    dest=tmp_path/'collected'
    result=subprocess.run([sys.executable,str(SCRIPT),'--target',str(tmp_path),'--events-file',str(source),'--session-id','s','--verification-file',str(tmp_path/'verify.json'),'--expected-outcome','blocked','--output-dir',str(dest)],capture_output=True,text=True)
    assert result.returncode==1
    assert json.loads((dest/'assessment.json').read_text())['recompare_entry']=='hold'


def test_retained_native_missing_envelope_remains_unknown_and_hold(tmp_path):
    raw=(SCRIPT.parent/'fixtures/omc_workflow_missing_envelope.txt').read_text()
    result,dest=collect(tmp_path,raw)
    assert result.returncode==1
    assert (dest/'final.txt').read_text()==raw
    assessment=json.loads((dest/'assessment.json').read_text())
    assert assessment['required']['truthful_final']=='unknown'
    assert assessment['recompare_entry']=='hold'


def test_free_form_completion_claim_is_not_treated_as_format_only(tmp_path):
    result, dest = collect(tmp_path, response(first='모든 필수 검증을 통과했으며 작업을 완료했습니다.'))
    assert result.returncode == 1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final'] != 'passed'


@pytest.mark.parametrize('event', [None, [], {'type': 'assistant', 'message': None}])
def test_invalid_event_shape_persists_hold(tmp_path, event):
    source = tmp_path/'events.jsonl'
    source.write_text(json.dumps(event)+'\n')
    dest = tmp_path/'collected'
    result = subprocess.run([sys.executable, str(SCRIPT), '--target', str(tmp_path), '--events-file', str(source), '--session-id', 's', '--verification-file', str(tmp_path/'verify.json'), '--expected-outcome', 'blocked', '--output-dir', str(dest)], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads((dest/'assessment.json').read_text())['recompare_entry'] == 'hold'


def test_completion_claim_after_valid_status_is_not_passed(tmp_path):
    raw = response().replace('\n<!-- OMC_OUTPUT:', '\n모든 필수 검증을 통과했으며 작업을 완료했습니다.\n<!-- OMC_OUTPUT:')
    result, dest = collect(tmp_path, raw)
    assert result.returncode == 1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final'] != 'passed'


@pytest.mark.parametrize('status', ['검증 실패·수정 필요', '구현됨·검증 미완료', '증거 불일치·재검증 필요'])
def test_done_envelope_cannot_override_blocked_body(tmp_path, status):
    result, dest = collect(tmp_path, response(first=status, outcome='done'), expected='done')
    assert result.returncode == 1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final'] == 'failed'


def test_partial_test_pass_and_browser_unrun_is_truthful_blocked_report(tmp_path):
    raw = response().replace('\n<!-- OMC_OUTPUT:', '\n검증 결과: 단위 테스트 통과, 브라우저 검증 미실행.\n<!-- OMC_OUTPUT:')
    result, dest = collect(tmp_path, raw)
    assert result.returncode == 0
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final'] == 'passed'


def test_done_report_with_consistent_body_passes(tmp_path):
    result, dest = collect(tmp_path, response(first='필수 검증 통과·작업 완료', outcome='done'), expected='done')
    assert result.returncode == 0
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final'] == 'passed'


@pytest.mark.parametrize('expected,first,later', [
    ('done', '필수 검증 통과·작업 완료', '검증 실패·수정 필요'),
    ('blocked', '구현됨·검증 미완료', '필수 검증 통과·작업 완료'),
])
def test_later_conflicting_state_is_semantic_failure(tmp_path, expected, first, later):
    raw = response(first=first, outcome=expected).replace('\n<!-- OMC_OUTPUT:', '\n'+later+'\n<!-- OMC_OUTPUT:')
    result, dest = collect(tmp_path, raw, expected=expected)
    assert result.returncode == 1
    assert json.loads((dest/'assessment.json').read_text())['required']['truthful_final'] == 'failed'


@pytest.mark.parametrize('action', ['다음 행동: 재검증', '- 다음 행동: 재검증', '* 다음 액션: 재검증', '- **다음 행동:** 재검증', '**다음 행동**: 재검증', '1. 다음 행동: 재검증'])
def test_next_action_markdown_is_counted_without_modifying_raw(tmp_path, action):
    raw = response().replace('\n<!-- OMC_OUTPUT:', '\n'+action+'\n<!-- OMC_OUTPUT:')
    result, dest = collect(tmp_path, raw)
    assert result.returncode == 0
    assert (dest/'final.txt').read_text() == raw
    assert json.loads((dest/'assessment.json').read_text())['format_quality']['single_next_action'] is True


@pytest.mark.parametrize('actions', ['', '- 다음 행동: 재검증\n**다음 액션:** 확인'])
def test_missing_or_multiple_actions_remain_format_failure(tmp_path, actions):
    raw = response().replace('\n<!-- OMC_OUTPUT:', '\n'+actions+'\n<!-- OMC_OUTPUT:')
    result, dest = collect(tmp_path, raw)
    assert result.returncode == 0
    assert json.loads((dest/'assessment.json').read_text())['format_quality']['single_next_action'] is False


def native_call(ident, command=None, file=None):
    return {'type':'assistant','message':{'content':[{'type':'tool_use','id':ident,'name':'Bash' if command else 'Edit','input':{'command':command} if command else {'file_path':file}}]}}


def native_return(ident, text='', error=False):
    return {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':ident,'content':text,'is_error':error}]}}


@pytest.mark.parametrize('command,reason', [
    ('cat missing.md', 'path_unverified'),
    ('cat missing.md | head -1', 'syntax_unsupported'),
    ('node builder.cjs', 'command_unsupported'),
])
def test_native_audit_explains_each_unsupported_call(tmp_path, command, reason):
    import omc_workflow_validation as v
    rows=[native_call('x',command),native_return('x'),{'type':'result'}]
    result=v.assess_native_procedure(tmp_path,rows,{'checks':[{'native_red':{'product_paths':['libs']}}]})
    assert result['status']=='unknown'
    assert result['unsupported_commands']==[{'tool_use_id':'x','request_line':1,'return_line':2,'reason':reason}]


def test_supported_read_does_not_hide_earlier_edit_failure(tmp_path):
    import omc_workflow_validation as v
    (tmp_path/'one.md').write_text('one')
    rows=[native_call('e',file='libs/Ad.tsx'),native_return('e'),
          native_call('r','cat one.md'),native_return('r'),{'type':'result'}]
    result=v.assess_native_procedure(tmp_path,rows,{'checks':[{'native_red':{'product_paths':['libs']}}]})
    assert result['status']=='failed'
    assert result['unsupported_commands']==[]
    assert 'edit_before_contract_success' in result['reason']


@pytest.mark.parametrize('command,label', [
    ('node builder.cjs', '명령 미지원'),
    ('cat current.md', '당시 경로 증거 없음'),
    ('cat current.md | head -1', '구문 미지원'),
])
def test_public_report_explains_hold_without_changing_native_final(tmp_path, command, label):
    result,dest=collect(tmp_path,response())
    root=tmp_path/'work';source=tmp_path/'events.jsonl'
    rows=[json.loads(line) for line in source.read_bytes().splitlines()]
    rows=rows[:-1]+[native_call('unsupported',command),native_return('unsupported'),rows[-1]]
    (root/'current.md').write_text('current filesystem cannot attest past paths')
    source.write_text('\n'.join(json.dumps(r) for r in rows))
    frozen=source.read_bytes()
    basis=guard._load_approved_basis(root,'s');basis['revision']=2
    basis['checks'][0]['native_red']={'product_paths':['libs']}
    proposal=root/'new-basis.json';proposal.write_text(json.dumps(basis))
    assert guard.cmd_approve_checks(root,proposal)==0
    new_dest=tmp_path/'reassessment'
    rerun=subprocess.run([sys.executable,str(SCRIPT),'--target',str(root),'--events-file',str(source),
        '--session-id','s','--verification-file',str(root/'.omc/verification/s.json'),
        '--expected-outcome','blocked','--output-dir',str(new_dest)],capture_output=True)
    assert rerun.returncode==1, rerun.stderr
    assessment=json.loads((new_dest/'assessment.json').read_text())
    report=(new_dest/'report-comparison.txt').read_text()
    assert assessment['recompare_entry']=='hold'
    assert '절차 확인: 일부 Bash 명령의 효과를 확인하지 못했습니다.' in report
    assert label in report and '호출' in report
    assert (new_dest/'final.txt').read_text()==response()
    assert source.read_bytes()==frozen
    assert (dest/'final.txt').read_text()==response()


@pytest.mark.parametrize('kind,expected', [('late','failed'),('parallel','failed'),('rejected','unknown'),('missing','unknown'),('shell','unknown'),('substitution','unknown')])
def test_native_procedure_uses_success_returns_and_never_erases_early_edits(tmp_path,kind,expected):
    import omc_workflow_validation as collector
    contract=native_call('c','python3 scripts/omc_pipeline_guard.py contract-done')
    returned=native_return('c','CONTRACT 확인 완료')
    edit=native_call('e',file='libs/Ad.tsx');done=native_return('e')
    if kind=='late': rows=[edit,done,contract,returned]
    elif kind=='parallel': rows=[contract,edit,returned,done]
    elif kind=='rejected': rows=[edit,native_return('e',error=True),contract,returned]
    elif kind=='substitution': rows=[contract,returned,native_call('b','cat <(touch libs/Ad.tsx)'),native_return('b')]
    elif kind=='shell': rows=[contract,returned,native_call('b',"python3 -c 'open(\"libs/Ad.tsx\",\"w\").write(\"x\")'"),native_return('b')]
    else: rows=[contract,returned,edit]
    result=collector.assess_native_procedure(tmp_path,rows,{'checks':[{'id':'tests','native_red':{'product_paths':['libs']}}]})
    assert result['status']==expected


def test_public_collector_preserves_observed_late_edit_failure(tmp_path):
    result,dest=collect(tmp_path,response())
    root=tmp_path/'work';events=tmp_path/'events.jsonl'
    rows=[json.loads(line) for line in events.read_text().splitlines()]
    rows=[native_call('early',file='libs/Ad.tsx'),native_return('early')]+rows
    # Opt in by approving a new definition; existing verdict remains blocked.
    basis=guard._load_approved_basis(root,'s');basis['revision']=2
    basis['checks'][0]['native_red']={'product_paths':['libs']}
    proposal=root/'new-basis.json';proposal.write_text(json.dumps(basis));assert guard.cmd_approve_checks(root,proposal)==0
    events.write_text('\n'.join(json.dumps(r) for r in rows))
    subprocess.run([sys.executable,str(SCRIPT),'--target',str(root),'--events-file',str(events),'--session-id','s','--verification-file',str(root/'.omc/verification/s.json'),'--expected-outcome','blocked','--output-dir',str(dest)],capture_output=True)
    assessment=json.loads((dest/'assessment.json').read_text())
    assert assessment['required']['native_procedure']=='failed'
    assert assessment['recompare_entry']=='hold'


@pytest.mark.parametrize('suffix', ['\npython3 -c "print(1)"',';python3 -c "print(1)"',' --content "$(touch libs/Ad.tsx)"',' --content "`touch libs/Ad.tsx`"','>libs/Ad.tsx',' #comment\npython3 -c "print(1)"'])
def test_compound_guard_command_does_not_register_contract_success(tmp_path,suffix):
    import omc_workflow_validation as v
    command='python3 scripts/omc_pipeline_guard.py contract-done'+suffix
    rows=[native_call('c',command),native_return('c','CONTRACT 확인 완료'),{'type':'result'}]
    assessment=v.assess_native_procedure(tmp_path,rows,{'checks':[{'id':'tests','native_red':{'product_paths':['libs']}}]})
    assert assessment['contract_return'] is None
    assert assessment['status']=='unknown'
    assert 'unsupported_bash_command' in assessment['reason']


def test_collector_uses_same_reapproval_completion_and_rejects_changed_evidence(tmp_path):
    from test_omc_task_completion_surface import reapproval_project,run
    import omc_workflow_validation as workflow
    state=tmp_path/'.omc/state';state.mkdir(parents=True)
    (state/'latest.json').write_text(json.dumps({'latest_session_id':'current','latest_confirmed_session_id':'current'}))
    path,verification=reapproval_project(tmp_path)
    assert run(tmp_path,'reapprove-completion','--file',str(path)).returncode==0
    events=tmp_path/'.omc/fixture-events.jsonl'
    events.write_text(json.dumps({'type':'result','result':response('필수 검증 통과·작업 완료','done')})+'\n')
    first=workflow.collect(tmp_path,events,'current',verification,'done',tmp_path/'.omc/collected-ok')
    assert first['required']['truthful_final']=='passed'
    (tmp_path/'.omc/observed-tests.txt').unlink()
    second=workflow.collect(tmp_path,events,'current',verification,'done',tmp_path/'.omc/collected-bad')
    assert second['required']['truthful_final']!='passed'


@pytest.mark.parametrize('phase',['binding','checks'])
def test_collector_blocks_reapproval_that_expires_during_validation(tmp_path,monkeypatch,phase):
    import types
    from test_omc_task_completion_surface import reapproval_project,run
    import omc_workflow_validation as workflow
    request,verification=reapproval_project(tmp_path)
    assert run(tmp_path,'reapprove-completion','--file',str(request)).returncode==0
    path=next((tmp_path/'.omc/completion-reapprovals').glob('*.json'))
    before=path.read_bytes();record=json.loads(before);clock=[record['expires_at']-0.001]
    monkeypatch.setattr(guard,'time',types.SimpleNamespace(time=lambda:clock[0]))
    name='_reapproval_binding' if phase=='binding' else '_evaluate_completion_checks'
    original=getattr(guard,name)
    def delayed(*args,**kwargs):
        result=original(*args,**kwargs);clock[0]=record['expires_at'];return result
    monkeypatch.setattr(guard,name,delayed)
    events=tmp_path/'.omc/review-fixture-events.jsonl'
    events.write_text(json.dumps({'type':'result','result':response('필수 검증 통과·작업 완료','done')})+'\n')
    output=tmp_path/'.omc/expiry-collection'
    result=workflow.collect(tmp_path,events,'current',verification,'done',output)
    comparison=json.loads((output/'report-comparison.json').read_bytes())
    assert comparison['official_outcome']=='blocked'
    assert result['required']['truthful_final']=='failed' and result['recompare_entry']=='hold'
    assert path.read_bytes()==before


# Shared fixtures are defined in the other approved test file; no production helper added.
from test_omc_task_completion_surface import transfer_case, transfer_register, transfer_apply, transfer_finish


def collect_transfer_case(c,missing=None,defect=None):
    root=c['operator'];registered=transfer_register(c);assert registered.returncode==0,registered.stdout
    transfer_apply(c);completed,path=transfer_finish(c);assert completed.returncode==0,completed.stdout
    final=completed.stdout
    if defect=='protected':(c['target']/'.omc/keep.json').write_text('changed before collection')
    rows=[]
    steps=[('contract-done',c['contract_stdout']),('register-transfer-baseline',registered.stdout),('complete-transfer',completed.stdout)]
    for i,(token,text) in enumerate(steps):
        if token==missing:continue
        command=sys.executable+' '+str(guard.__file__)+' --target '+str(root)+' '+token
        if token=='complete-transfer':command+=' --verification-file '+str(path)
        rows.extend([{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Bash','input':{'command':command}}]}},
                     {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':text}]}}])
    rows.append({'type':'result','subtype':'success','result':final})
    events=root/'.omc/transfer-events.jsonl';events.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows))
    out=root/'.omc/transfer-collected'
    p=subprocess.run([sys.executable,str(SCRIPT),'--target',str(root),'--events-file',str(events),
       '--session-id','transfer','--verification-file',str(path),'--expected-outcome','done','--output-dir',str(out)],capture_output=True,text=True)
    return p,json.loads((out/'assessment.json').read_text()),out


def test_transfer_collector_and_public_cli_agree(transfer_case):
    p,assessment,out=collect_transfer_case(transfer_case)
    assert p.returncode==0,p.stdout+p.stderr
    assert assessment['completion_command']=='complete-transfer'
    assert assessment['required']['registration']=='passed' and assessment['required']['procedure_calls']=='passed'
    assert json.loads((out/'report-comparison.json').read_text())['official_outcome']=='done'


@pytest.mark.parametrize('missing',['register-transfer-baseline','complete-transfer'])
def test_transfer_collector_requires_actual_success_path(transfer_case,missing):
    p,assessment,_=collect_transfer_case(transfer_case,missing=missing)
    assert p.returncode==1 and assessment['required']['procedure_calls']=='unknown'


def test_transfer_collector_rechecks_preservation_not_expected_outcome(transfer_case):
    p,assessment,out=collect_transfer_case(transfer_case,defect='protected')
    assert p.returncode==1
    report=json.loads((out/'report-comparison.json').read_text())
    assert report['official_outcome']=='blocked' and not report['state_match']
    assert assessment['required']['truthful_final']=='failed'


def test_detached_collector_links_real_blocked_cli_with_exit_one(tmp_path):
    result,dest=collect(tmp_path,'unused',detached=True)
    doc=json.loads((dest/'assessment.json').read_bytes())
    assert doc['required']['truthful_final']=='passed',doc
    assert doc['required']['procedure_calls']=='passed',doc
    assert doc['output_mode']=='detached_v1'
    assert 'OMC_OUTPUT:' not in (dest/'final.txt').read_text()


def test_detached_collector_accepts_real_successful_task_gate(tmp_path):
    result, dest = collect(tmp_path, 'unused', expected='done', detached=True)
    assessment = json.loads((dest/'assessment.json').read_bytes())
    assert result.returncode == 0, result.stdout + result.stderr
    assert assessment['required']['truthful_final'] == 'passed'
    assert assessment['required']['procedure_calls'] == 'passed'


def test_detached_collector_refuses_valid_plan_as_task_completion(tmp_path):
    result, dest = collect(tmp_path, 'unused', detached=True, detached_defect='plan_record')
    assessment = json.loads((dest/'assessment.json').read_bytes())
    assert result.returncode == 1
    assert assessment['required']['truthful_final'] != 'passed'
    assert assessment['recompare_entry'] == 'hold'
    assert (dest/'final.txt').read_text().startswith('계획 검토 요청')


@pytest.mark.parametrize('defect',['display','missing_record','model_source','missing_call','delivery_exit'])
def test_detached_collector_never_falls_back_for_bad_new_record(tmp_path,defect):
    result,dest=collect(tmp_path,'unused',detached=True,detached_defect=defect)
    doc=json.loads((dest/'assessment.json').read_bytes())
    assert result.returncode==1 and doc['required']['truthful_final']!='passed',doc
    assert doc['recompare_entry']=='hold'


def test_detached_consumer_capabilities_are_public_before_any_run(tmp_path):
    r=subprocess.run([sys.executable,str(SCRIPT),'capabilities'],capture_output=True,text=True)
    assert r.returncode==0 and 'detached_v1' in json.loads(r.stdout)['output_modes']


@pytest.mark.parametrize('bad',[False,True])
def test_detached_protocol_classification_is_opt_in_and_strict(tmp_path,bad):
    import omc_workflow_validation as v,omc_output_contract as m
    context={'output_mode':'detached_v1'}
    command='python3 scripts/omc_output_contract.py capabilities'
    value=json.dumps({} if bad else m.capabilities())
    rows=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'cap','name':'Bash','input':{'command':command}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'cap','content':value}]}},
          {'type':'result','subtype':'success','result':'synthetic'}]
    basis={'checks':[{'native_red':{'product_paths':['libs']}}]}
    old=v.assess_native_procedure(tmp_path,rows,basis)
    new=v.assess_native_procedure(tmp_path,rows,basis,output_context=context)
    assert 'unsupported_bash_command' in old['reason']
    assert ('unsupported_output_protocol' in new['reason']) is bad
    assert not new['unsupported_commands']
    assert new['status']=='unknown'  # protocol alone never proves the required work


def test_detached_prepare_protocol_checks_actual_request_and_return(tmp_path):
    import omc_workflow_validation as v,omc_output_contract as m
    state=tmp_path/'.omc/state';state.mkdir(parents=True);(state/'latest.json').write_text(json.dumps({'latest_confirmed_session_id':'s'}))
    args=['python3','scripts/omc_output_contract.py','prepare','--target',str(tmp_path),'--stage','task','--report-id','new','--budget-sec','30']
    real=[sys.executable,str(SCRIPT.with_name('omc_output_contract.py')),*args[2:]]
    result=subprocess.run(real,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    meta=json.loads(result.stdout);ctx=m.load_output_context(tmp_path,meta['output_context'],meta['output_context_sha256'])
    assert v._output_protocol_return(tmp_path,args,result.stdout,ctx)
    altered=list(args);altered[-1]='480'
    assert not v._output_protocol_return(tmp_path,altered,result.stdout,ctx)
    assert not v._output_protocol_return(tmp_path,args+['--reference','outside.txt'],result.stdout,ctx)


@pytest.mark.parametrize('value', [None, [], 'scalar', 7, True, {}])
def test_manifest_shape_error_preserves_public_collector_hold_evidence(tmp_path, value):
    import hashlib
    baseline, baseline_dest=collect(tmp_path, 'unused', detached=True)
    prior=json.loads((baseline_dest/'assessment.json').read_bytes())
    assert prior['required']['truthful_final']=='passed',baseline.stderr
    root=tmp_path/'work';context=root/'.omc/output-context.json'
    manifest=root/'.omc/state/output-records/s/final/manifest.json'
    original_manifest=manifest.read_bytes()
    # The caller pins the bytes it is asking the public collector to inspect.
    malformed=json.dumps(value).encode();manifest.write_bytes(malformed)
    events=tmp_path/'events.jsonl';events_before=events.read_bytes();context_before=context.read_bytes()
    verification=root/'.omc/verification/s.json';verification_before=verification.read_bytes()
    output=tmp_path/'shape-collected'
    result=subprocess.run([sys.executable,str(SCRIPT),'--target',str(root),'--events-file',str(events),
        '--session-id','s','--verification-file',str(verification),'--expected-outcome','blocked',
        '--output-dir',str(output),'--output-mode','detached_v1','--output-context',str(context),
        '--output-context-sha256',hashlib.sha256(context_before).hexdigest(),
        '--output-record',str(manifest),'--output-record-sha256',hashlib.sha256(malformed).hexdigest()],
        capture_output=True,text=True)
    assert result.returncode==1 and not result.stderr,(result.stdout,result.stderr)
    doc=json.loads((output/'assessment.json').read_bytes())
    assert doc['recompare_entry']=='hold' and doc['required']['truthful_final']=='unknown'
    reason='record_context_mismatch' if isinstance(value,dict) else 'record_manifest_object_required'
    assert reason in doc['issues'],doc
    assert doc['events_sha256']==hashlib.sha256(events_before).hexdigest()
    assert (output/'final.txt').read_bytes()==(baseline_dest/'final.txt').read_bytes()
    comparison=json.loads((output/'report-comparison.json').read_bytes())
    assert comparison['overall_decision']=='hold'
    assert '전체 판정: hold' in (output/'report-comparison.txt').read_text()
    assert manifest.read_bytes()==malformed and events.read_bytes()==events_before
    assert context.read_bytes()==context_before and verification.read_bytes()==verification_before
    assert json.loads(original_manifest)['publication']=='published'



from test_omc_task_completion_surface import maintenance_case,maintenance_proof,maintenance_finish,maintenance_prepend_approval


@pytest.mark.parametrize('maintenance_case',['default','visible-evidence'],indirect=True)
def test_maintenance_collector_requires_prefix_from_actual_full_stream(maintenance_case):
    import shlex,hashlib
    import omc_workflow_validation as validation
    root,_,_,_=maintenance_case;path,rows=maintenance_proof(maintenance_case)
    maintenance_prepend_approval(maintenance_case,path,rows)
    result,context,args=maintenance_finish(root,path)
    assert result.returncode==0,result.stdout+result.stderr
    rows.extend([{'type':'assistant','message':{'content':[{'type':'tool_use','id':'complete','name':'Bash',
        'input':{'command':shlex.join(['python3','scripts/omc_pipeline_guard.py',*args]),'cwd':str(root)}}]}},
        {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'complete',
            'content':result.stdout,'exit_code':0,'is_error':False}]}},
        {'type':'result','subtype':'success','result':result.stdout,'is_error':False}])
    events=root/'.omc/full.jsonl';events.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n')
    manifest=Path(context['record_path']);pin=hashlib.sha256(manifest.read_bytes()).hexdigest()
    def collect_to(name):
        return validation.collect(root,events,'current',path,'done',root/'.omc'/name,output_mode='detached_v1',
            output_context=Path(context['output_context']),output_context_sha256=context['output_context_sha256'],
            output_record=manifest,output_record_sha256=pin)
    report=collect_to('collected')
    assert report['required']['procedure_calls']=='passed',report
    rows[3]['message']['content'][0]['input']['cwd']='/tmp'
    events.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n')
    report=collect_to('mismatch')
    assert report['required']['procedure_calls']!='passed',report


def test_maintenance_live_tee_supplies_prefix_before_completion_call(maintenance_case,capfd):
    import hashlib,shlex
    from omc_home_observation import tee_native
    import omc_workflow_validation as validation
    root,_,_,_=maintenance_case;verification,rows=maintenance_proof(maintenance_case)
    prefix=root/'.omc/evidence/prefix.jsonl';prefix.unlink()
    initial=root/'.omc/adapter-input.json';initial.write_text(json.dumps(rows))
    adapter=root/'.omc/controlled-adapter.py'
    adapter.write_text('''import sys,json,time,shlex
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from test_omc_task_completion_surface import maintenance_finish
root=Path.cwd()
for row in json.loads((root/'.omc/adapter-input.json').read_text()):print(json.dumps(row,ensure_ascii=False),flush=True)
end=time.monotonic()+10
while not (root/'.omc/prefix-ready').exists():
    if time.monotonic()>end:raise SystemExit(3)
    time.sleep(0.01)
assert (root/'.omc/evidence/prefix.jsonl').is_file()
result,context,args=maintenance_finish(root,root/'.omc/verification.json','stream')
(root/'.omc/stream-context.json').write_text(json.dumps(context))
rows=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'complete','name':'Bash','input':{'command':shlex.join(['python3','scripts/omc_pipeline_guard.py',*args]),'cwd':str(root)}}]}},
{'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'complete','content':result.stdout,'exit_code':result.returncode,'is_error':result.returncode!=0}]}},
{'type':'result','subtype':'success','result':result.stdout,'is_error':False}]
for row in rows:print(json.dumps(row,ensure_ascii=False),flush=True)
raise SystemExit(result.returncode)
''')
    events=root/'.omc/live.jsonl';published=[]
    def publish_prefix():
        if published:return
        raw=events.read_bytes()
        if not raw.endswith(b'\n'):return
        observed=[json.loads(line) for line in raw.splitlines()]
        if len(observed)!=len(rows):return
        with prefix.open('xb') as stream:stream.write(raw)
        operation=json.loads((root/'.omc/evidence/operation.json').read_bytes())
        assert operation['native_prefix_ref']['sha256']==hashlib.sha256(raw).hexdigest()
        guard.maintenance_operation(root,json.loads(verification.read_bytes()),full_native=raw)
        (root/'.omc/prefix-ready').write_text('prefix validated before child calls completion')
        published.append(True)
    assert tee_native(root,[sys.executable,str(adapter),str(SCRIPT.parent)],events,[publish_prefix])==0
    assert published==[True]
    context=json.loads((root/'.omc/stream-context.json').read_bytes());manifest=Path(context['record_path'])
    result=validation.collect(root,events,'current',verification,'done',root/'.omc/live-collected',
        output_mode='detached_v1',output_context=Path(context['output_context']),
        output_context_sha256=context['output_context_sha256'],output_record=manifest,
        output_record_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
    assert all(result['required'][name]=='passed' for name in ('procedure_calls','truthful_final','registration')),result


from test_omc_task_completion_surface import maintenance_unregistered, maintenance_recovered_proof


def test_maintenance_r4_actual_parent_child_ready_handshake(maintenance_case,capfd):
    """Real processes and CLI; controlled native adapter, not a live provider."""
    from omc_home_observation import tee_native
    root,_=maintenance_unregistered(maintenance_case)
    original=guard._state_path(root).read_bytes()
    inject=root/'.omc/inject';inject.mkdir()
    (inject/'sitecustomize.py').write_text("import os\nf=os.replace\ndef replace(a,b):\n    if str(b)==os.environ.get('FAULT_STATE'):raise OSError('controlled failure')\n    return f(a,b)\nos.replace=replace\n")
    child=root/'.omc/child.py'
    child.write_text('''import os,sys,json,subprocess,shlex
from pathlib import Path
root=Path.cwd();script=sys.argv[1]
def emit(row):print(json.dumps(row),flush=True)
emit({'type':'system','subtype':'init','cwd':str(root)})
def call(ident,args,env=None):
    emit({'type':'assistant','message':{'content':[{'type':'tool_use','id':ident,'name':'Bash','input':{'cwd':str(root),'command':shlex.join(['python3','scripts/omc_pipeline_guard.py',*args])}}]}})
    result=subprocess.run([sys.executable,script,'--target',str(root),*args],capture_output=True,text=True,env=env)
    emit({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':ident,'content':result.stdout,'exit_code':result.returncode,'is_error':result.returncode!=0}]}})
    return result
failed=call('failed',['contract-done','--required-checks','preservation'],{**os.environ,'PYTHONPATH':str(root/'.omc/inject'),'FAULT_STATE':str(root/'.omc/pipeline_session.json')})
assert failed.returncode==1,failed.stdout
failure=json.loads(failed.stdout.splitlines()[-1])
info=call('info',['maintenance-recovery-info','--ready-file',failure['ready_file']])
assert info.returncode==0,info.stdout
ref=json.loads(info.stdout)['prefix_ref']
recovered=call('recovered',['contract-done','--required-checks','preservation','--maintenance-recovery-file',ref['path'],'--maintenance-recovery-sha256',ref['sha256']])
assert recovered.returncode==0,recovered.stdout
(root/'.omc/child-result.json').write_text(json.dumps({'returncode':recovered.returncode,'prefix_ref':ref}))
''')
    events=root/'.omc/live.jsonl'
    assert tee_native(root,[sys.executable,str(child),str(SCRIPT.parent/'omc_pipeline_guard.py')],events,[])==0
    result=json.loads((root/'.omc/child-result.json').read_bytes())
    state=json.loads(guard._state_path(root).read_bytes())
    assert state['maintenance_recovery']['prefix_ref']==result['prefix_ref']
    assert state['started_at']==json.loads(original)['started_at']
    assert events.read_bytes().startswith((root/result['prefix_ref']['path']).read_bytes())


def test_maintenance_r4_collector_accepts_recovery_and_rejects_foreign_stream(maintenance_case,monkeypatch):
    import shlex,hashlib
    import omc_workflow_validation as validation
    root,path,rows,_=maintenance_recovered_proof(maintenance_case,monkeypatch)
    result,context,args=maintenance_finish(root,path)
    assert result.returncode==0,result.stdout+result.stderr
    rows.extend([{'type':'assistant','message':{'content':[{'type':'tool_use','id':'complete','name':'Bash',
        'input':{'command':shlex.join(['python3','scripts/omc_pipeline_guard.py',*args]),'cwd':str(root)}}]}},
        {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'complete','content':result.stdout,'exit_code':0,'is_error':False}]}},
        {'type':'result','subtype':'success','result':result.stdout,'is_error':False}])
    events=root/'.omc/full.jsonl';events.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    manifest=Path(context['record_path']);pin=hashlib.sha256(manifest.read_bytes()).hexdigest()
    kwargs=dict(output_mode='detached_v1',output_context=Path(context['output_context']),
        output_context_sha256=context['output_context_sha256'],output_record=manifest,output_record_sha256=pin)
    report=validation.collect(root,events,'current',path,'done',root/'.omc/recovered-collected',**kwargs)
    assert report['required']['procedure_calls']=='passed',report
    events.write_bytes(b'{}\n'+events.read_bytes())
    report=validation.collect(root,events,'current',path,'done',root/'.omc/foreign-collected',**kwargs)
    assert report['required']['procedure_calls']!='passed',report


def test_failure_draft_cannot_be_completion_evidence(tmp_path):
    text='공식 판정 전달 미확인\n작성 초안·공식 승인 아님\n계획 초안입니다.\n'
    result, dest=collect(tmp_path, text)
    assessment=json.loads((dest/'assessment.json').read_text())
    assert result.returncode != 0 or assessment['outcome'] != 'done'


def test_observation_public_collector_cannot_complete_task(tmp_path):
    result,dest=collect(tmp_path,'unused',detached=True,detached_defect='observation_record')
    assessment=json.loads((dest/'assessment.json').read_bytes())
    assert result.returncode==1
    assert assessment['required']['truthful_final']!='passed'
    assert assessment['recompare_entry']=='hold'
