"""Machine-readable output envelope contract tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "omc_output_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("omc_output_contract", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_and_parse_plan_ready_envelope():
    module = _load_module()

    rendered = module.render_envelope(
        stage="plan",
        verdict="PROCEED",
        risk="low",
        next_skill="omc-task",
        user_selection_needed=False,
    )

    parsed = module.parse_envelope(rendered)
    assert parsed == {
        "schema_version": "omc-output/v1",
        "stage": "plan",
        "outcome": "ready",
        "risk": "low",
        "next_skill": "omc-task",
        "user_selection_needed": False,
        "reason_code": None,
        "verdict": "PROCEED",
    }
    assert rendered.splitlines()[-1] == "VERDICT: PROCEED"
    assert rendered.splitlines()[-2].startswith("<!-- OMC_OUTPUT: ")
    assert rendered.splitlines()[-2].endswith(" -->")


def test_parse_accepts_legacy_visible_envelope():
    module = _load_module()
    output = (
        'OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"task",'
        '"outcome":"done","risk":"low","next_skill":"omc-review",'
        '"user_selection_needed":false,"reason_code":null}\n'
        "VERDICT: PROCEED"
    )

    assert module.parse_envelope(output)["stage"] == "task"


def test_parse_rejects_unclosed_hidden_envelope():
    module = _load_module()
    output = (
        '<!-- OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"task",'
        '"outcome":"done","risk":"low","next_skill":"omc-review",'
        '"user_selection_needed":false,"reason_code":null}\n'
        "VERDICT: PROCEED"
    )

    with pytest.raises(module.OutputContractError, match="last two non-empty lines"):
        module.parse_envelope(output)


def test_render_escapes_html_comment_delimiters_in_payload():
    module = _load_module()
    reason_code = "x-->VISIBLE<!--y"

    rendered = module.render_envelope(
        stage="task",
        verdict="BLOCK",
        risk="high",
        next_skill=None,
        user_selection_needed=True,
        reason_code=reason_code,
    )

    assert rendered.count("<!--") == 1
    assert rendered.count("-->") == 1
    assert module.parse_envelope(rendered)["reason_code"] == reason_code


def test_parse_rejects_malformed_marker_before_valid_hidden_envelope():
    module = _load_module()
    valid = module.render_envelope(
        stage="task",
        verdict="PROCEED",
        risk="low",
        next_skill="omc-review",
        user_selection_needed=False,
    )
    output = f"<!-- OMC_OUTPUT: broken\nbody\n{valid}"

    with pytest.raises(module.OutputContractError, match="exactly one OMC_OUTPUT envelope"):
        module.parse_envelope(output)


@pytest.mark.parametrize(
    ("stage", "verdict", "outcome"),
    [
        ("plan", "HOLD", "unresolved"),
        ("task", "PROCEED", "done"),
        ("task", "BLOCK", "blocked"),
        ("review", "APPROVE", "approved"),
        ("review", "APPROVE WITH NOTES", "approved"),
        ("review", "REVISE", "blocked"),
        ("critique-plan", "PROCEED", "ready"),
        ("critique-plan", "HOLD", "unresolved"),
        ("critique-code", "APPROVE WITH NOTES", "approved"),
        ("critique-code", "BLOCK", "blocked"),
        ("investigate", "PROCEED", "ready"),
        ("investigate", "HOLD", "unresolved"),
        ("ship", "PROCEED", "ready"),
        ("ship", "BLOCK", "blocked"),
    ],
)
def test_stage_verdict_mapping(stage: str, verdict: str, outcome: str):
    module = _load_module()
    assert module.outcome_for(stage, verdict) == outcome


def test_parse_rejects_verdict_outcome_conflict():
    module = _load_module()
    output = (
        'OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"task",'
        '"outcome":"done","risk":"high","next_skill":null,'
        '"user_selection_needed":true,"reason_code":"test_failure"}\n'
        "VERDICT: BLOCK"
    )

    with pytest.raises(module.OutputContractError, match="outcome conflicts"):
        module.parse_envelope(output)


def test_parse_rejects_nonempty_content_after_terminal_contract_lines():
    module = _load_module()
    output = module.render_envelope(
        stage="task",
        verdict="PROCEED",
        risk="low",
        next_skill="omc-review",
        user_selection_needed=False,
    )

    with pytest.raises(module.OutputContractError, match="last two non-empty lines"):
        module.parse_envelope(f"{output}\nprovider stderr warning")


def test_parse_rejects_block_without_reason_code():
    module = _load_module()
    output = (
        'OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"task",'
        '"outcome":"blocked","risk":"high","next_skill":null,'
        '"user_selection_needed":true,"reason_code":null}\n'
        "VERDICT: BLOCK"
    )

    with pytest.raises(module.OutputContractError, match="reason_code"):
        module.parse_envelope(output)


def test_normalize_legacy_verdict_once():
    module = _load_module()

    normalized = module.normalize_output("implementation complete\nVERDICT: PROCEED", stage="task")

    parsed = module.parse_envelope(normalized)
    assert parsed["outcome"] == "done"
    assert parsed["next_skill"] == "omc-review"
    assert normalized.count("OMC_OUTPUT:") == 1
    assert normalized.splitlines()[-1] == "VERDICT: PROCEED"


def test_contract_source_distinguishes_raw_and_legacy_outputs():
    module = _load_module()
    raw = module.render_envelope(
        stage="plan",
        verdict="PROCEED",
        risk="low",
        next_skill="omc-task",
        user_selection_needed=False,
    )

    assert module.contract_source(raw, stage="plan") == "raw_compliant"
    assert module.contract_source("VERDICT: PROCEED", stage="plan") == "legacy_normalized"


@pytest.mark.parametrize(
    ("stage", "verdict", "next_skill", "selection_needed", "reason_code"),
    [
        ("plan", "PROCEED", "omc-review", False, None),
        ("plan", "REVISE", "omc-plan", False, "needs_revision"),
        ("task", "BLOCK", "omc-task", True, "test_failure"),
        ("review", "REVISE", None, False, "finding"),
    ],
)
def test_render_rejects_semantically_inconsistent_routing(
    stage: str,
    verdict: str,
    next_skill: str | None,
    selection_needed: bool,
    reason_code: str | None,
):
    module = _load_module()

    with pytest.raises(module.OutputContractError, match="routing policy"):
        module.render_envelope(
            stage=stage,
            verdict=verdict,
            risk="high",
            next_skill=next_skill,
            user_selection_needed=selection_needed,
            reason_code=reason_code,
        )


def test_normalize_rejects_explicit_invalid_envelope_instead_of_repairing():
    module = _load_module()
    output = (
        'OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"review",'
        '"outcome":"approved","risk":"low","next_skill":null,'
        '"user_selection_needed":false,"reason_code":null}\n'
        "VERDICT: BLOCK"
    )

    with pytest.raises(module.OutputContractError):
        module.normalize_output(output, stage="review")


def test_next_skill_uses_canonical_id_not_executor_syntax():
    module = _load_module()

    with pytest.raises(module.OutputContractError, match="next_skill"):
        module.render_envelope(
            stage="plan",
            verdict="PROCEED",
            risk="low",
            next_skill="$omc-task",
            user_selection_needed=False,
        )


def test_compact_envelope_stays_within_output_budget():
    module = _load_module()
    rendered = module.render_envelope(
        stage="review",
        verdict="APPROVE WITH NOTES",
        risk="medium",
        next_skill=None,
        user_selection_needed=True,
    )

    assert len(rendered.encode("utf-8")) <= 260


@pytest.mark.parametrize(
    ("stage", "verdict", "reason_code", "next_skill", "selection_needed"),
    [
        ("critique-plan", "PROCEED", None, "omc-task", False),
        ("critique-plan", "REVISE", "plan_gap", "omc-plan", True),
        ("critique-code", "APPROVE", None, "omc-review", False),
        ("critique-code", "REVISE", "code_risk", "omc-task", False),
        ("investigate", "PROCEED", "root_cause_confirmed", "omc-task", False),
        ("investigate", "PROCEED", "fix_already_applied", "omc-review", False),
        ("investigate", "REVISE", "architecture_scope_issue", "omc-ceo-review", False),
        ("investigate", "HOLD", "insufficient_evidence", None, True),
        ("ship", "PROCEED", "all_gates_passed", None, True),
        ("ship", "BLOCK", "test_or_regression_failure", "omc-investigate", False),
        ("ship", "BLOCK", "tdd_or_test_missing", "omc-task", False),
        ("ship", "BLOCK", "approval_missing", None, True),
    ],
)
def test_extended_stage_reason_aware_routing(
    stage: str,
    verdict: str,
    reason_code: str | None,
    next_skill: str | None,
    selection_needed: bool,
):
    module = _load_module()

    rendered = module.render_envelope(
        stage=stage,
        verdict=verdict,
        risk="medium",
        next_skill=next_skill,
        user_selection_needed=selection_needed,
        reason_code=reason_code,
    )

    parsed = module.parse_envelope(rendered)
    assert parsed["next_skill"] == next_skill
    assert parsed["reason_code"] == reason_code


@pytest.mark.parametrize(
    ("stage", "verdict", "reason_code", "wrong_next_skill"),
    [
        ("investigate", "PROCEED", "root_cause_confirmed", "omc-review"),
        ("investigate", "REVISE", "architecture_scope_issue", "omc-plan"),
        ("ship", "BLOCK", "test_or_regression_failure", "omc-task"),
        ("ship", "BLOCK", "approval_missing", "omc-investigate"),
    ],
)
def test_reason_aware_routing_rejects_wrong_next_skill(
    stage: str,
    verdict: str,
    reason_code: str,
    wrong_next_skill: str,
):
    module = _load_module()

    with pytest.raises(module.OutputContractError, match="routing policy"):
        module.render_envelope(
            stage=stage,
            verdict=verdict,
            risk="high",
            next_skill=wrong_next_skill,
            user_selection_needed=False,
            reason_code=reason_code,
        )


def test_reason_aware_stage_rejects_unknown_reason_code():
    module = _load_module()

    with pytest.raises(module.OutputContractError, match="reason_code"):
        module.render_envelope(
            stage="ship",
            verdict="BLOCK",
            risk="high",
            next_skill=None,
            user_selection_needed=True,
            reason_code="unknown_ship_failure",
        )


@pytest.mark.parametrize(
    ("relative_path", "expected_contract"),
    [
        (".agents/skills/omc-plan/references/workflow.md", "stage=plan / outcome=unresolved|ready"),
        (".agents/skills/omc-task/SKILL.md", "stage=task / outcome=blocked|done"),
        (".agents/skills/omc-review/SKILL.md", "stage=review / outcome=approved|blocked"),
        ("templates/.agents/skills/omc-plan/references/workflow.md", "stage=plan / outcome=unresolved|ready"),
        ("templates/.agents/skills/omc-task/SKILL.md", "stage=task / outcome=blocked|done"),
        ("templates/.agents/skills/omc-review/SKILL.md", "stage=review / outcome=approved|blocked"),
        (".agents/skills/omc-critique/SKILL.md", "stage=critique-plan|critique-code"),
        (".agents/skills/omc-investigate/SKILL.md", "stage=investigate"),
        (".agents/skills/omc-ship/SKILL.md", "stage=ship"),
    ],
)
def test_skill_output_contract_matches_parser_schema(relative_path: str, expected_contract: str, installed_omc_target):
    content = ((ROOT if relative_path.startswith("templates/") else installed_omc_target) / relative_path).read_text(encoding="utf-8")
    assert expected_contract in content


@pytest.mark.parametrize(
    ("stage", "markers"),
    [
        ("plan", ("PROCEED=>ready,omc-task,false", "REVISE=>unresolved,omc-plan,true")),
        ("task", ("PROCEED=>done,omc-review,false", "BLOCK=>blocked,null,true")),
        ("review", ("APPROVE=>approved,null,context", "REVISE=>blocked,omc-task,false")),
        ("critique-plan", ("PROCEED=>ready,omc-task,false", "HOLD=>unresolved,omc-plan,true")),
        ("critique-code", ("approved,omc-review,false", "REVISE=>blocked,omc-task,false")),
        (
            "investigate",
            (
                "PROCEED+root_cause_confirmed=>ready,omc-task,false",
                "REVISE+architecture_scope_issue=>unresolved,omc-ceo-review,false",
                "HOLD+insufficient_evidence=>unresolved,null,true",
            ),
        ),
        (
            "ship",
            (
                "PROCEED+all_gates_passed=>ready,null,true",
                "BLOCK+test_or_regression_failure=>blocked,omc-investigate,false",
                "BLOCK+approval_missing=>blocked,null,true",
            ),
        ),
    ],
)
def test_prompt_contract_declares_stage_routing_policy(stage: str, markers: tuple[str, ...]):
    module = _load_module()
    prompt = module.prompt_contract(stage)
    for marker in markers:
        assert marker in prompt


@pytest.mark.parametrize(
    "stage",
    ("plan", "task", "review", "critique-plan", "critique-code", "investigate", "ship"),
)
def test_prompt_contract_stays_within_byte_budget(stage: str):
    module = _load_module()
    assert len(module.prompt_contract(stage).encode("utf-8")) <= 600


PILOT_SKILL_PATHS = [
    ROOT / prefix / "omc-plan" / "references" / "workflow.md"
    for prefix in (
        Path(".agents/skills"),
        Path("templates/.agents/skills"),
    )
] + [
    ROOT / prefix / skill / "SKILL.md"
    for prefix in (
        Path(".agents/skills"),
        Path("templates/.agents/skills"),
    )
    for skill in ("omc-task", "omc-review")
]
PILOT_SKILL_PATHS += [
    path
    for path in (
        ROOT / ".agent/skills/omc-plan/references/workflow.md",
        ROOT / ".agent/skills/omc-task/SKILL.md",
        ROOT / ".agent/skills/omc-review/SKILL.md",
    )
    if path.exists()
]


@pytest.mark.parametrize("path", PILOT_SKILL_PATHS)
def test_pilot_skills_declare_compact_machine_envelope(path: Path, installed_omc_target):
    path = path if "templates" in path.relative_to(ROOT).parts else installed_omc_target/path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    for marker in (
        "omc-output/v1",
        "<!-- OMC_OUTPUT: {JSON} -->",
        "next_skill",
        "user_selection_needed",
        "reason_code",
        "VERDICT",
    ):
        assert marker in text, f"{path.relative_to(ROOT)} missing {marker}"


def test_pilot_skills_share_the_same_machine_contract_block(installed_omc_target):
    blocks = []
    for path in PILOT_SKILL_PATHS:
        path = path if "templates" in path.relative_to(ROOT).parts else installed_omc_target/path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        marker = "## Machine output contract"
        assert text.count(marker) == 1, f"{path.relative_to(ROOT)} contract block count"
        block = text.split(marker, 1)[1].split("\n## ", 1)[0].strip()
        blocks.append(block)

    assert len(set(blocks)) == 1


# Detached-output regressions use disposable roots only.
def test_detached_public_capabilities_support_separate_record():
    import subprocess, sys, json
    result = subprocess.run([sys.executable, str(MODULE_PATH), 'capabilities'], capture_output=True, text=True)
    assert result.returncode == 0 and 'detached_v1' in result.stdout, result.stdout + result.stderr


@pytest.fixture
def detached_case(tmp_path):
    import time, hashlib
    state=tmp_path/'.omc/state'; state.mkdir(parents=True)
    (state/'latest.json').write_text(json.dumps({'latest_confirmed_session_id':'s','latest_session_id':'s'}))
    ctx={'output_mode':'detached_v1','stage':'review','work_id':'s','session_id':'s',
         'report_id':'r','deadline_epoch':time.time()+60,'references':[],
         'native_call_reference':{'invocation_id':'invoke-r'}}
    file=tmp_path/'.omc/context.json';file.write_text(json.dumps(ctx)); pin=hashlib.sha256(file.read_bytes()).hexdigest()
    raw='판정: APPROVE WITH NOTES\n근거: 경미한 제안\n'+_load_module().render_envelope(stage='review',verdict='APPROVE WITH NOTES',risk='low',next_skill=None,user_selection_needed=True)
    return tmp_path, file, pin, ctx, raw


def test_detached_record_roundtrip_keeps_machine_meaning(detached_case):
    root,file,pin,ctx,raw=detached_case; m=_load_module()
    ref=m.publish_record(root,file,pin,raw)
    doc=m.read_record(root,ref['path'],ref['sha256'],ctx)
    assert doc['machine_payload']['outcome']=='approved' and doc['verdict']=='APPROVE WITH NOTES'
    assert doc['source_kind']=='model_declared'
    assert '판정: 승인·보완 제안 있음' in doc['display']
    assert 'OMC_OUTPUT:' not in doc['display'] and 'VERDICT:' not in doc['display']
    assert doc['raw_source']==raw
    with pytest.raises((m.OutputContractError,OSError)):
        m.publish_record(root,file,pin,raw)


@pytest.mark.parametrize('defect',['hash','session','work','stage','mode','expired','symlink','issuer'])
def test_detached_record_rejects_wrong_context(detached_case,defect):
    import hashlib,time
    root,file,pin,ctx,raw=detached_case;m=_load_module();kw={}
    if defect=='hash':pin='0'*64
    elif defect=='symlink':
        ext=root.parent/(root.name+'-outside.json');ext.write_bytes(file.read_bytes());file.unlink();file.symlink_to(ext)
    elif defect=='issuer':kw={'source_kind':'completion_gate'}
    else:
        ctx[{'session':'session_id','work':'work_id','stage':'stage','mode':'output_mode','expired':'deadline_epoch'}[defect]]=time.time()-1 if defect=='expired' else 'wrong'
        file.write_text(json.dumps(ctx));pin=hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises((m.OutputContractError,OSError,ValueError)):
        m.publish_record(root,file,pin,raw,**kw)


@pytest.mark.parametrize('defect',['display','raw','manifest','mixed','expected_session','missing_manifest'])
def test_detached_consumer_rejects_bad_record(detached_case,defect):
    import hashlib
    root,file,pin,ctx,raw=detached_case;m=_load_module();ref=m.publish_record(root,file,pin,raw)
    manifest=Path(ref['path']);directory=manifest.parent
    if defect=='display':(directory/'display.txt').write_text('다른 상태')
    elif defect=='raw':(directory/'source.txt').write_text('다른 원문')
    elif defect=='manifest':manifest.write_text('{}')
    elif defect=='missing_manifest':manifest.unlink()
    elif defect=='expected_session':ctx['session_id']='different'
    else:kw={'display':m.human_output(raw)+ '\nVERDICT: APPROVE'}
    with pytest.raises((m.OutputContractError,OSError,ValueError)):
        m.read_record(root,ref['path'],ref['sha256'],ctx,**(kw if defect=='mixed' else {}))


@pytest.mark.parametrize('failure',['data','manifest','readback','output','deadline'])
def test_detached_failure_never_claims_delivery_or_overwrites_record(detached_case,monkeypatch,capsys,failure):
    root,file,pin,ctx,raw=detached_case;m=_load_module()
    if failure in ('data','manifest'):
        real=m._write_record_file
        def bad(path,data):
            if (failure=='data' and path.name=='display.txt') or (failure=='manifest' and path.name=='manifest.tmp'):
                raise OSError('synthetic disk full')
            return real(path,data)
        monkeypatch.setattr(m,'_write_record_file',bad)
    elif failure=='readback':
        real=m._write_record_file
        def bad(path,data):
            real(path,data)
            if path.name=='display.txt':path.write_bytes(b'corrupt')
        monkeypatch.setattr(m,'_write_record_file',bad)
    elif failure=='deadline':
        real=m._write_record_file
        def late(path,data):
            real(path,data)
            if path.name=='manifest.tmp':monkeypatch.setattr(m.time,'time',lambda:ctx['deadline_epoch']+1)
        monkeypatch.setattr(m,'_write_record_file',late)
    else:
        def broken(display):raise BrokenPipeError('synthetic output failed')
        monkeypatch.setattr(m,'_write_display',broken)
    code=m.emit_record(root,file,pin,raw)
    captured=capsys.readouterr()
    assert code==2 and not captured.out and '기록 전달을 확인하지 못했습니다' in captured.err
    manifest=root/'.omc/state/output-records/s/r/manifest.json'
    if failure=='output':
        before=manifest.read_bytes(); doc=json.loads(before)
        assert doc['machine_payload']['outcome']=='approved' and manifest.read_bytes()==before
    else:assert not manifest.exists()


def test_detached_cli_publication_outputs_only_korean(detached_case):
    import subprocess,sys,hashlib
    root,file,pin,ctx,raw=detached_case;source=root/'.omc/source.txt';source.write_text(raw)
    args=[sys.executable,str(MODULE_PATH),'publish','--target',str(root),'--output-context',str(file),
          '--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',hashlib.sha256(source.read_bytes()).hexdigest()]
    r=subprocess.run(args,capture_output=True,text=True)
    assert r.returncode==0 and r.stdout.startswith('판정: 승인') and not r.stderr
    assert 'OMC_OUTPUT:' not in r.stdout and 'VERDICT:' not in r.stdout


def test_detached_generic_publisher_cannot_change_gate_authority(detached_case):
    root,file,pin,ctx,raw=detached_case;m=_load_module()
    with pytest.raises(m.OutputContractError):m.publish_record(root,file,pin,raw,source_kind='completion_gate')


@pytest.mark.parametrize('stage,verdict,reason,next_skill,selection',[
 ('plan','PROCEED',None,'omc-task',False),('plan','HOLD','fixture','omc-plan',True),('plan','REVISE','fixture','omc-plan',True),
 ('task','PROCEED',None,'omc-review',False),('task','BLOCK','verification_incomplete',None,True),
 ('review','APPROVE',None,None,True),('review','APPROVE WITH NOTES',None,None,True),('review','REVISE','fixture','omc-task',False),('review','BLOCK','fixture',None,True),
 ('critique-plan','PROCEED',None,'omc-task',False),('critique-plan','REVISE','fixture','omc-plan',True),('critique-plan','HOLD','fixture','omc-plan',True),
 ('critique-code','PROCEED',None,'omc-review',False),('critique-code','APPROVE',None,'omc-review',False),('critique-code','APPROVE WITH NOTES',None,'omc-review',False),
 ('critique-code','REVISE','fixture','omc-task',False),('critique-code','BLOCK','fixture',None,True),('critique-code','HOLD','fixture',None,True),
 ('investigate','PROCEED','root_cause_confirmed','omc-task',False),('investigate','PROCEED','fix_already_applied','omc-review',False),
 ('investigate','REVISE','architecture_scope_issue','omc-ceo-review',False),('investigate','HOLD','insufficient_evidence',None,True),
 ('ship','PROCEED','all_gates_passed',None,True),('ship','BLOCK','test_or_regression_failure','omc-investigate',False),
 ('ship','BLOCK','tdd_or_test_missing','omc-task',False),('ship','BLOCK','approval_missing',None,True)])
def test_detached_all_stage_mappings_remain_unchanged(detached_case,stage,verdict,reason,next_skill,selection):
    import hashlib
    root,file,pin,ctx,raw=detached_case;m=_load_module()
    ctx['stage']=stage;file.write_text(json.dumps(ctx));pin=hashlib.sha256(file.read_bytes()).hexdigest()
    raw='판정: '+verdict+'\n'+m.render_envelope(stage=stage,verdict=verdict,risk='low',next_skill=next_skill,user_selection_needed=selection,reason_code=reason)
    ref=m.publish_record(root,file,pin,raw);doc=m.read_record(root,ref['path'],ref['sha256'],ctx)
    assert {**doc['machine_payload'],'verdict':doc['verdict']}==m.parse_envelope(raw)
    assert 'VERDICT:' not in doc['display'] and 'OMC_OUTPUT:' not in doc['display']


def test_detached_prepare_public_cli_freezes_current_identity(detached_case):
    import subprocess,sys,hashlib
    root,file,pin,ctx,raw=detached_case
    r=subprocess.run([sys.executable,str(MODULE_PATH),'prepare','--target',str(root),'--stage','review','--report-id','new-report','--budget-sec','30'],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    ref=json.loads(r.stdout);m=_load_module();context=m.load_output_context(root,ref['output_context'],ref['output_context_sha256'])
    assert context['work_id']=='s' and context['session_id']=='s' and context['report_id']=='new-report'
    again=subprocess.run([sys.executable,str(MODULE_PATH),'prepare','--target',str(root),'--stage','review','--report-id','new-report','--budget-sec','30'],capture_output=True,text=True)
    assert again.returncode==2 and '문맥을 고정하지 못했습니다' in again.stderr


def test_detached_reference_and_current_code_drift_are_not_accepted(detached_case):
    import hashlib
    root,file,pin,ctx,raw=detached_case;m=_load_module();evidence=root/'evidence.txt';evidence.write_text('frozen')
    ctx['references']=[{'path':'evidence.txt','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),'purpose':'validation'}]
    file.write_text(json.dumps(ctx));pin=hashlib.sha256(file.read_bytes()).hexdigest()
    ref=m.publish_record(root,file,pin,raw);evidence.write_text('changed')
    with pytest.raises(m.OutputContractError):m.read_record(root,ref['path'],ref['sha256'],ctx)


def test_detached_model_revise_publication_is_not_provider_failure(detached_case,capsys):
    root,file,pin,ctx,raw=detached_case;m=_load_module()
    raw='판정: REVISE\n'+m.render_envelope(stage='review',verdict='REVISE',risk='medium',next_skill='omc-task',user_selection_needed=False,reason_code='fixture')
    assert m.emit_record(root,file,pin,raw)==0
    shown=capsys.readouterr();assert shown.out.startswith('판정: 수정 필요') and not shown.err


def test_detached_partial_manifest_write_never_publishes(detached_case,monkeypatch,capsys):
    root,file,pin,ctx,raw=detached_case;m=_load_module();real=m._write_record_file
    def short(path,data):real(path,b'partial' if path.name=='manifest.tmp' else data)
    monkeypatch.setattr(m,'_write_record_file',short)
    assert m.emit_record(root,file,pin,raw)==2
    assert not (root/'.omc/state/output-records/s/r/manifest.json').exists()
    assert not capsys.readouterr().out


def test_detached_partial_stdout_does_not_rewrite_published_verdict(detached_case,monkeypatch,capsys):
    import io
    root,file,pin,ctx,raw=detached_case;m=_load_module()
    class Short(io.StringIO):
        def write(self,value):super().write(value[:5]);return 5
    stream=Short();monkeypatch.setattr(m.sys,'stdout',stream)
    code=m.emit_record(root,file,pin,raw)
    assert code==2 and stream.getvalue()!=''
    doc=json.loads((root/'.omc/state/output-records/s/r/manifest.json').read_bytes())
    assert doc['machine_payload']['outcome']=='approved' and doc['publication']=='published'


def test_detached_abrupt_exit_after_publication_keeps_record_not_delivery(detached_case):
    import subprocess,sys,hashlib
    root,file,pin,ctx,raw=detached_case;source=root/'.omc/source.txt';source.write_text(raw)
    snippet="import sys,os;sys.path.insert(0,sys.argv[1]);import omc_output_contract as m;ref=m.publish_record(m.Path(sys.argv[2]),m.Path(sys.argv[3]),sys.argv[4],m.Path(sys.argv[5]).read_text());os._exit(137)"
    r=subprocess.run([sys.executable,'-c',snippet,str(MODULE_PATH.parent),str(root),str(file),pin,str(source)],capture_output=True,text=True)
    assert r.returncode==137 and not r.stdout
    manifest=root/'.omc/state/output-records/s/r/manifest.json';m=_load_module()
    assert m.read_record(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),ctx)['machine_payload']['outcome']=='approved'
    # Only a published local declaration remains; no native delivery return exists.


@pytest.mark.parametrize('body',['판정: APPROVE','판정: 승인','**판정: APPROVE**','- 판정: 승인','### 판정: APPROVE','**판정**: 승인','판정: **APPROVE**'])
def test_review_fix_public_cli_rejects_body_and_machine_conflict(detached_case,body):
    import subprocess,sys,hashlib
    root,file,pin,ctx,_=detached_case;m=_load_module()
    raw=body+'\n'+m.render_envelope(stage='review',verdict='BLOCK',risk='medium',next_skill=None,user_selection_needed=True,reason_code='fixture_block')
    source=root/'.omc/contradiction.txt';source.write_text(raw);before=source.read_bytes()
    r=subprocess.run([sys.executable,str(MODULE_PATH),'publish','--target',str(root),'--output-context',str(file),
        '--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',hashlib.sha256(before).hexdigest()],capture_output=True,text=True)
    assert r.returncode==2 and not r.stdout,r.stdout+r.stderr
    assert '기록 전달을 확인하지 못했습니다' in r.stderr
    assert not m.record_path(root,ctx).exists()
    assert source.read_bytes()==before


@pytest.mark.parametrize('fence',['```','~~~~'])
def test_review_fix_preserves_verdict_examples_inside_fences(detached_case,fence):
    root,file,pin,ctx,_=detached_case;m=_load_module()
    example=fence+'text\n판정: APPROVE\n'+fence
    raw=example+'\n판정: BLOCK\n'+m.render_envelope(stage='review',verdict='BLOCK',risk='medium',next_skill=None,user_selection_needed=True,reason_code='fixture_block')
    ref=m.publish_record(root,file,pin,raw);doc=m.read_record(root,ref['path'],ref['sha256'],ctx)
    assert example in doc['display']
    assert '판정: 보류' in doc['display']


def test_review_fix_code_example_cannot_hide_canonical_judgment(detached_case):
    root,file,pin,ctx,_=detached_case;m=_load_module()
    raw='```text\n판정: APPROVE\n```\n'+m.render_envelope(stage='review',verdict='BLOCK',risk='medium',next_skill=None,user_selection_needed=True,reason_code='fixture_block')
    shown=m.human_output(raw)
    assert shown.endswith('판정: 보류\n')
    assert '```text\n판정: APPROVE\n```' in shown


def test_review_fix_inline_and_indented_examples_are_not_judgment_headers(detached_case):
    root,file,pin,ctx,_=detached_case;m=_load_module()
    example='`판정: APPROVE`\n    판정: APPROVE'
    raw=example+'\n'+m.render_envelope(stage='review',verdict='BLOCK',risk='medium',next_skill=None,user_selection_needed=True,reason_code='fixture_block')
    shown=m.human_output(raw)
    assert example in shown and shown.endswith('판정: 보류\n')


@pytest.mark.parametrize('label',['APPROVE later','승인합니다'])
def test_review_fix_unrecognized_typed_judgment_cannot_bypass_conflict_check(detached_case,label):
    root,file,pin,ctx,_=detached_case;m=_load_module()
    raw='판정: '+label+'\n'+m.render_envelope(stage='review',verdict='BLOCK',risk='medium',next_skill=None,user_selection_needed=True,reason_code='fixture_block')
    with pytest.raises(m.OutputContractError):m.publish_record(root,file,pin,raw)
    assert not m.record_path(root,ctx).exists()


def test_review_fix_unclosed_example_cannot_swallow_judgment(detached_case):
    root,file,pin,ctx,_=detached_case;m=_load_module()
    raw='```text\n판정: APPROVE\n'+m.render_envelope(stage='review',verdict='BLOCK',risk='medium',next_skill=None,user_selection_needed=True,reason_code='fixture_block')
    with pytest.raises(m.OutputContractError):m.publish_record(root,file,pin,raw)


@pytest.mark.parametrize('label',['보완 필요','수정 필요'])
def test_review_fix_korean_revision_labels_have_one_canonical_view(detached_case,label):
    root,file,pin,ctx,_=detached_case;m=_load_module()
    raw='판정: '+label+'\n'+m.render_envelope(stage='review',verdict='REVISE',risk='medium',next_skill='omc-task',user_selection_needed=False,reason_code='fixture_revise')
    shown=m.human_output(raw)
    assert shown=='판정: 수정 필요\n'
