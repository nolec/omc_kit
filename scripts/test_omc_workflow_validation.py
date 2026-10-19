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


def collect(tmp_path, text, registered=True, external=False, expected="blocked", scope_defect=None):
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
    events=[]
    for i,(cmd,output) in enumerate([
        ('python3 scripts/omc_pipeline_guard.py contract-done','CONTRACT 확인 완료'),
        ('python3 scripts/omc_pipeline_guard.py red-done test_app.js','RED 완료 등록'),
        ('python3 scripts/omc_pipeline_guard.py complete-task --verification-file '+path,response(first=guard.evaluate_task_completion(root,verification,expected_session_id='s')['first'], outcome=expected))]):
        if scope_defect=='skip-complete' and 'complete-task' in cmd:
            continue
        if actual_gate_output and 'complete-task' in cmd:
            output=actual_gate_output
        events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Bash','input':{'command':cmd}}]}}, {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':output}]}}]
    events += [{'type':'result','subtype':'success','result':text}]
    source=tmp_path/'events.jsonl';source.write_text('\n'.join(json.dumps(e) for e in events))
    dest=tmp_path/'collected'
    result=subprocess.run([sys.executable,str(SCRIPT),'--target',str(root),'--events-file',str(source),'--session-id','s','--verification-file',str(verification),'--expected-outcome',expected,'--output-dir',str(dest)],capture_output=True,text=True)
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
