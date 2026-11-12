"""Machine-readable output envelope contract tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "omc_output_contract.py"


def test_caution_verdict_preserves_explicit_user_selection():
    module = _load_module()
    raw = module.render_envelope(
        stage='critique-plan', verdict='PROCEED WITH CAUTION', risk='medium',
        next_skill='omc-task', user_selection_needed=True,
    )
    parsed = module.parse_envelope(raw)
    assert parsed['verdict'] == 'PROCEED WITH CAUTION'
    assert parsed['outcome'] == 'ready'
    assert parsed['user_selection_needed'] is True
    with pytest.raises(module.OutputContractError):
        module.render_envelope(stage='critique-plan', verdict='PROCEED WITH CAUTION',
            risk='medium', next_skill='omc-task', user_selection_needed=False)
    with pytest.raises(module.OutputContractError):
        module.render_envelope(stage='plan', verdict='PROCEED WITH CAUTION',
            risk='medium', next_skill='omc-task', user_selection_needed=True)


def test_caution_verdict_publication_and_consumer(detached_case):
    import hashlib
    import subprocess
    import sys
    root, file, pin, ctx, _ = detached_case
    module = _load_module()
    ctx['stage'] = 'critique-plan'
    file.write_text(json.dumps(ctx))
    pin = hashlib.sha256(file.read_bytes()).hexdigest()
    raw = '경미한 주의사항을 확인하세요.\n' + module.render_envelope(
        stage='critique-plan', verdict='PROCEED WITH CAUTION', risk='medium',
        next_skill='omc-task', user_selection_needed=True)
    source = root / '.omc/caution.txt'
    source.write_text(raw)
    result = subprocess.run([sys.executable, str(MODULE_PATH), 'publish', '--target', str(root),
        '--output-context', str(file), '--output-context-sha256', pin,
        '--source-file', str(source), '--source-sha256', hashlib.sha256(source.read_bytes()).hexdigest()],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '판정: 주의사항 확인 후 진행 가능' in result.stdout
    assert 'VERDICT:' not in result.stdout
    manifest = module.record_path(root, ctx)
    record = module.read_record(root, manifest, hashlib.sha256(manifest.read_bytes()).hexdigest(), ctx,
        display=result.stdout)
    assert record['verdict'] == 'PROCEED WITH CAUTION'
    assert record['machine_payload']['user_selection_needed'] is True


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
        assert "detached_v1" in block and "사용자 최종 답변에 붙이지 않는다" in block
        assert "— 마지막 두 줄은" not in block
        blocks.append(block)

    assert len(set(blocks)) == 1


# Detached-output regressions use disposable roots only.
def _routing_publish_case(case, *, reason='review_required', next_skill='omc-critique'):
    import hashlib
    root, context, _, ctx, _ = case
    ctx.update(stage='plan')
    context.write_text(json.dumps(ctx))
    payload = dict(schema_version='omc-output/v1', stage='plan', outcome='unresolved',
                   risk='high', next_skill=next_skill, user_selection_needed=True,
                   reason_code=reason)
    raw = '계획 검토 요청\n<!-- OMC_OUTPUT: ' + json.dumps(payload) + ' -->\nVERDICT: HOLD\n'
    source = root/'.omc/routing-source.txt'
    source.write_text(raw)
    argv = ['publish', '--target', str(root), '--output-context', str(context),
            '--output-context-sha256', hashlib.sha256(context.read_bytes()).hexdigest(),
            '--source-file', str(source), '--source-sha256', hashlib.sha256(source.read_bytes()).hexdigest()]
    return root, context, ctx, source, argv


def test_plan_review_required_public_cli_roundtrip(detached_case):
    import subprocess, sys, hashlib
    root, context, ctx, source, argv = _routing_publish_case(detached_case)
    result = subprocess.run([sys.executable, str(MODULE_PATH), *argv], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    m = _load_module(); manifest = m.record_path(root, ctx)
    doc = m.read_record(root, manifest, hashlib.sha256(manifest.read_bytes()).hexdigest(), ctx,
                        display=result.stdout)
    assert doc['source_kind'] == 'model_declared'
    assert doc['machine_payload']['next_skill'] == 'omc-critique'
    assert '판정: 보류' in result.stdout and not result.stderr


def test_invalid_route_cli_records_safe_failure(detached_case):
    import subprocess, sys, hashlib
    root, _, _, source, argv = _routing_publish_case(detached_case, reason='scope_unclear')
    result = subprocess.run([sys.executable, str(MODULE_PATH), *argv], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout
    assert 'next_skill_conflict' in result.stderr
    records = list((root/'.omc/state/output-failures').glob('*.json'))
    assert len(records) == 1
    record = json.loads(records[0].read_bytes())
    assert record['error_code'] == 'next_skill_conflict'
    assert record['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert record['context_status'] == 'verified'
    assert record['kind'] == 'failure_diagnostic'
    assert not list((root/'.omc/state/output-records').glob('**/manifest.json'))


@pytest.mark.parametrize('reason,next_skill,code', [
    ('scope_unclear', 'omc-plan', 0), ('unknown_existing_reason', 'omc-plan', 0),
    ('review_required', 'omc-plan', 2), ('scope_unclear', 'omc-critique', 2),
])
def test_plan_default_and_explicit_routes_public_cli(detached_case, reason, next_skill, code):
    import subprocess, sys
    _, _, _, _, argv = _routing_publish_case(detached_case, reason=reason, next_skill=next_skill)
    result = subprocess.run([sys.executable, str(MODULE_PATH), *argv], capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    assert bool(result.stdout) == (code == 0)


@pytest.mark.parametrize('defect,diagnostic', [
    ('duplicate_verdict', True), ('body_judgment', True), ('context_hash', False),
    ('malformed_json', False), ('nonobject_json', False), ('unsafe_identity', False),
    ('unreadable_source', True), ('expired', False), ('unknown_deadline', False),
    ('unsafe_target', False),
])
def test_public_failure_identity_and_storage_boundaries(detached_case, defect, diagnostic):
    import hashlib, subprocess, sys, time
    root, context, ctx, source, argv = _routing_publish_case(detached_case, reason='scope_unclear')
    if defect == 'duplicate_verdict': source.write_text(source.read_text()+'VERDICT: HOLD\n')
    elif defect == 'body_judgment': source.write_text('판정: 보류 — 설명\n'+source.read_text())
    elif defect == 'malformed_json': context.write_text('{not JSON')
    elif defect == 'nonobject_json': context.write_text('[]')
    elif defect in {'unsafe_identity', 'expired', 'unknown_deadline'}:
        if defect == 'unsafe_identity': ctx['report_id'] = '../../outside-SENSITIVE'
        elif defect == 'expired': ctx['deadline_epoch'] = time.time()-1
        else: ctx.pop('deadline_epoch')
        context.write_text(json.dumps(ctx))
    elif defect == 'unsafe_target':
        alias = root.parent/(root.name+'-link'); alias.symlink_to(root, target_is_directory=True)
        argv[argv.index('--target')+1] = str(alias)
    argv[argv.index('--output-context-sha256')+1] = hashlib.sha256(context.read_bytes()).hexdigest()
    argv[argv.index('--source-sha256')+1] = hashlib.sha256(source.read_bytes()).hexdigest()
    if defect == 'context_hash': argv[argv.index('--output-context-sha256')+1] = '0'*64
    if defect == 'unreadable_source': source.unlink()
    result = subprocess.run([sys.executable, str(MODULE_PATH), *argv], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout
    assert '오류 코드:' in result.stderr and 'Traceback' not in result.stderr
    assert 'SENSITIVE' not in result.stderr
    records = list((root/'.omc/state/output-failures').glob('*.json'))
    assert bool(records) is diagnostic
    if diagnostic:
        doc = json.loads(records[0].read_bytes())
        assert doc['context_status'] == 'verified'
        if defect == 'unreadable_source':
            assert doc['source_sha256'] is None and doc['source_status'] == 'unavailable'
        else: assert len(doc['source_sha256']) == 64
    assert not (root/'outside-SENSITIVE').exists()
    assert not list((root/'.omc/state/output-records').glob('**/manifest.json'))


@pytest.mark.parametrize('fault', ['data_write', 'diagnostic_write', 'post_seal_display', 'late_storage'])
def test_public_cli_injected_io_failures_keep_native_evidence(detached_case, fault):
    import hashlib, subprocess, sys
    root, _, ctx, _, argv = _routing_publish_case(detached_case)
    # Faults are injected only in a disposable process invoking the public CLI;
    # no product flag, runtime environment mutation or actual service is used.
    patches = {
        'data_write': "real=m._write_record_file\ndef write(p,b):\n if p.name=='display.txt': raise OSError('SENSITIVE disk error')\n return real(p,b)\nm._write_record_file=write\n",
        'diagnostic_write': "def bad(*a,**kw): raise OSError('SENSITIVE diagnostic error')\nm._write_failure_diagnostic=bad\nm._write_display=bad\n",
        'post_seal_display': "def bad(*a,**kw): raise BrokenPipeError('SENSITIVE pipe')\nm._write_display=bad\n",
        'late_storage': "real=m._write_record_file\ndef write(p,b):\n real(p,b)\n if p.name=='manifest.tmp': m.time.time=lambda:1e20\nm._write_record_file=write\n",
    }
    wrapper = ('import sys\nsys.path.insert(0,'+repr(str(MODULE_PATH.parent))+')\n'
               'import omc_output_contract as m\n'+patches[fault]+'sys.exit(m.output_cli(sys.argv[1:]))')
    result = subprocess.run([sys.executable, '-B', '-c', wrapper, *argv], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout
    assert '오류 코드:' in result.stderr and 'SENSITIVE' not in result.stderr
    m = _load_module(); manifest = m.record_path(root, ctx)
    if fault in {'post_seal_display', 'diagnostic_write'}:
        before = manifest.read_bytes()
        doc = m.read_record(root, manifest, hashlib.sha256(before).hexdigest(), ctx)
        assert doc['machine_payload']['next_skill'] == 'omc-critique'
        assert manifest.read_bytes() == before
    else: assert not manifest.exists()
    diagnostics = list((root/'.omc/state/output-failures').glob('*.json'))
    assert bool(diagnostics) == (fault in {'data_write', 'post_seal_display'})


def test_error_directory_symlink_is_not_followed(detached_case):
    import subprocess, sys
    root, _, _, _, argv = _routing_publish_case(detached_case, reason='scope_unclear')
    outside = root.parent/(root.name+'-external'); outside.mkdir()
    (root/'.omc/state/output-failures').symlink_to(outside, target_is_directory=True)
    result = subprocess.run([sys.executable, str(MODULE_PATH), *argv], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout
    assert not list(outside.iterdir())


@pytest.mark.parametrize('phase', ['before_write', 'after_write', 'final_context'])
@pytest.mark.parametrize('replacement', ['directory', 'parent_directory', 'symlink'])
def test_public_cli_never_confirms_relocated_diagnostics(detached_case, phase, replacement):
    import subprocess, sys
    root, _, _, _, argv = _routing_publish_case(detached_case, reason='scope_unclear')
    # A deterministic filesystem change in this disposable CLI process, not a
    # change to the product source or to any original candidate's evidence.
    wrapper = '''import sys,pathlib,shutil
sys.path.insert(0,SCRIPTS)
import omc_output_contract as m
root=pathlib.Path(ROOT)
phase=PHASE
replacement=REPLACEMENT
changed=False
calls=0
def replace_directory():
 global changed
 if changed: return
 changed=True
 original=root/'.omc/state/output-failures'
 if replacement=='parent_directory': original=original.parent
 moved=root.parent/(root.name+'-relocated')
 original.rename(moved)
 if replacement=='parent_directory': shutil.copytree(moved,original)
 elif replacement=='symlink': original.symlink_to(moved,target_is_directory=True)
 else: original.mkdir()
real_context=m._context
def context(*a,**kw):
 global calls
 calls+=1
 if (phase=='before_write' and calls==3) or (phase=='final_context' and calls==4):
  replace_directory()
 return real_context(*a,**kw)
m._context=context
real_fsync=m.os.fsync
def fsync(fd):
 real_fsync(fd)
 if phase=='after_write': replace_directory()
m.os.fsync=fsync
sys.exit(m.output_cli(sys.argv[1:]))
'''
    for name, value in [('SCRIPTS', str(MODULE_PATH.parent)), ('ROOT', str(root)),
                        ('PHASE', phase), ('REPLACEMENT', replacement)]:
        wrapper = wrapper.replace(name, repr(value))
    result = subprocess.run([sys.executable, '-B', '-c', wrapper, *argv], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout
    assert '별도 오류 기록: 확인 불가' in result.stderr, result.stderr
    moved = root.parent/(root.name+'-relocated')
    diagnostic_dir = moved/'output-failures' if replacement == 'parent_directory' else moved
    if phase == 'before_write': assert not list(diagnostic_dir.glob('*.json'))
    else: assert list(diagnostic_dir.glob('*.json'))  # Preserve partial evidence; never delete it.
    assert not list((root/'.omc/state/output-records').glob('**/manifest.json'))


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


@pytest.mark.parametrize('record', ['session', 'pending'])
@pytest.mark.parametrize('value', [None, [], 'scalar', 7, True])
@pytest.mark.parametrize('command', ['prepare', 'publish'])
def test_review_json_identity_shapes_return_korean_delivery_error(tmp_path, record, value, command):
    import hashlib, re, subprocess, sys, time
    root=tmp_path;state=root/'.omc/state';state.mkdir(parents=True)
    (state/'latest.json').write_text(json.dumps({'latest_confirmed_session_id':'s'}))
    file=state/'sessions/s/session.json' if record=='session' else state/'pending-completion.json'
    file.parent.mkdir(parents=True,exist_ok=True);file.write_text(json.dumps(value));original=file.read_bytes()
    if command=='prepare':
        args=['prepare','--target',str(root),'--stage','review','--report-id','shape-error','--budget-sec','30']
    else:
        context={'output_mode':'detached_v1','stage':'review','work_id':'s','session_id':'s',
                 'report_id':'shape-error','deadline_epoch':time.time()+60,'references':[],
                 'native_call_reference':{'invocation_id':'shape-error'}}
        context_file=root/'.omc/context.json';context_file.write_text(json.dumps(context))
        source=root/'.omc/source.txt';source.write_text('판정: 승인\n'+_load_module().render_envelope(
            stage='review',verdict='APPROVE',risk='low',next_skill=None,user_selection_needed=True))
        args=['publish','--target',str(root),'--output-context',str(context_file),
              '--output-context-sha256',hashlib.sha256(context_file.read_bytes()).hexdigest(),
              '--source-file',str(source),'--source-sha256',hashlib.sha256(source.read_bytes()).hexdigest()]
    result=subprocess.run([sys.executable,str(MODULE_PATH),*args],capture_output=True,text=True)
    assert result.returncode==2,(result.stdout,result.stderr)
    assert not result.stdout and re.search('[가-힣]',result.stderr)
    assert 'Traceback' not in result.stderr and 'AttributeError' not in result.stderr
    assert file.read_bytes()==original
    assert not list((root/'.omc').rglob('manifest.json'))



@pytest.mark.parametrize('record', ['session','pending'])
@pytest.mark.parametrize('value', [{}, {'work_id':'s'}])
def test_review_valid_json_identity_objects_still_prepare_and_publish(tmp_path, record, value):
    import hashlib, subprocess, sys
    root=tmp_path;state=root/'.omc/state';state.mkdir(parents=True)
    (state/'latest.json').write_text(json.dumps({'latest_confirmed_session_id':'s'}))
    file=state/'sessions/s/session.json' if record=='session' else state/'pending-completion.json'
    file.parent.mkdir(parents=True,exist_ok=True);file.write_text(json.dumps(value));original=file.read_bytes()
    prepared=subprocess.run([sys.executable,str(MODULE_PATH),'prepare','--target',str(root),
                            '--stage','review','--report-id','valid-shape','--budget-sec','30'],capture_output=True,text=True)
    assert prepared.returncode==0,prepared.stderr
    meta=json.loads(prepared.stdout);source=root/'.omc/source.txt'
    source.write_text('판정: 승인\n'+_load_module().render_envelope(stage='review',verdict='APPROVE',risk='low',next_skill=None,user_selection_needed=True))
    published=subprocess.run([sys.executable,str(MODULE_PATH),'publish','--target',str(root),
        '--output-context',meta['output_context'],'--output-context-sha256',meta['output_context_sha256'],
        '--source-file',str(source),'--source-sha256',hashlib.sha256(source.read_bytes()).hexdigest()],capture_output=True,text=True)
    assert published.returncode==0,published.stderr
    assert published.stdout=='판정: 승인\n' and not published.stderr
    assert file.read_bytes()==original and Path(meta['record_path']).is_file()



def _choice_source(case, reason='approval_missing', verdict='BLOCK'):
    import hashlib
    root, context, _, ctx, _ = case
    ctx['stage'] = 'ship'
    context.write_text(json.dumps(ctx))
    payload = dict(schema_version='omc-output/v1', stage='ship', outcome='blocked',
                   risk='low', next_skill=None, user_selection_needed=True, reason_code=reason)
    raw = '판정: 보류\n<!-- OMC_OUTPUT: ' + json.dumps(payload) + ' -->\nVERDICT: ' + verdict + '\n'
    source = root/'.omc/choice-source.txt'
    source.write_text(raw)
    args = ['--target', str(root), '--output-context', str(context),
            '--output-context-sha256', hashlib.sha256(context.read_bytes()).hexdigest(),
            '--source-file', str(source), '--source-sha256', hashlib.sha256(source.read_bytes()).hexdigest()]
    return root, context, ctx, source, raw, args


@pytest.mark.parametrize('reason,verdict,code,hint', [
    ('quality_unconfigured-SENSITIVE', 'BLOCK', 'reason_code_invalid', 'approval_missing'),
    ('approval_missing', 'BLOCKED', 'verdict_invalid', 'BLOCK'),
    ('approval_missing', 'APPROVE', 'verdict_invalid', 'PROCEED'),
])
@pytest.mark.parametrize('command', ['validate', 'publish'])
def test_preflight_invalid_choices_have_precise_safe_public_errors(detached_case, reason, verdict, code, hint, command):
    import subprocess, sys, hashlib
    root, context, ctx, source, raw, args = _choice_source(detached_case, reason, verdict)
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, str(MODULE_PATH), command, *args], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout
    assert code in result.stderr and hint in result.stderr and '허용값' in result.stderr
    assert 'SENSITIVE' not in result.stderr and 'Traceback' not in result.stderr
    assert all(Path(p).read_bytes() == data for p, data in before.items())
    assert not (root/'.omc/state/output-records').exists()
    diagnostics = list((root/'.omc/state/output-failures').glob('*.json'))
    assert len(diagnostics) == (1 if command == 'publish' else 0)
    if diagnostics:
        doc = json.loads(diagnostics[0].read_bytes())
        assert doc['error_code'] == code and doc['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_preflight_validate_is_read_only_and_publish_reuses_the_same_contract(detached_case):
    import subprocess, sys, hashlib
    root, context, ctx, source, raw, args = _choice_source(detached_case)
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    validated = subprocess.run([sys.executable, str(MODULE_PATH), 'validate', *args], capture_output=True, text=True)
    assert validated.returncode == 0 and not validated.stderr
    doc = json.loads(validated.stdout)
    assert doc == {'status':'valid', 'stage':'ship', 'verdict':'BLOCK', 'reason_code':'approval_missing', 'publication':'not_performed'}
    assert before == {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    published = subprocess.run([sys.executable, str(MODULE_PATH), 'publish', *args], capture_output=True, text=True)
    assert published.returncode == 0 and published.stdout == '판정: 보류\n'
    module = _load_module(); manifest = module.record_path(root, ctx)
    assert (manifest.parent/'source.txt').read_bytes() == raw.encode()
    sealed = manifest.read_bytes()
    source.write_text(raw.replace('approval_missing', 'quality_unconfigured'))
    args[args.index('--source-sha256')+1] = hashlib.sha256(source.read_bytes()).hexdigest()
    rejected = subprocess.run([sys.executable, str(MODULE_PATH), 'publish', *args], capture_output=True, text=True)
    assert rejected.returncode == 2 and 'reason_code_invalid' in rejected.stderr
    assert manifest.read_bytes() == sealed


def test_preflight_publish_cannot_bypass_shared_validation(detached_case, monkeypatch):
    module = _load_module()
    root, context, pin, ctx, raw = detached_case
    seen = []
    def reject(*args):
        seen.append(args)
        raise module.OutputContractError('test preflight rejection')
    monkeypatch.setattr(module, 'preflight_record', reject)
    with pytest.raises(module.OutputContractError):
        module.publish_record(root, context, pin, raw)
    assert len(seen) == 1 and not (root/'.omc/state/output-records').exists()


@pytest.mark.parametrize('defect', ['source_hash', 'context_hash'])
def test_preflight_validate_keeps_existing_pin_protection(detached_case, defect):
    import subprocess, sys
    root, _, _, source, raw, args = _choice_source(detached_case)
    flag = '--source-sha256' if defect == 'source_hash' else '--output-context-sha256'
    args[args.index(flag)+1] = '0'*64
    result = subprocess.run([sys.executable, str(MODULE_PATH), 'validate', *args], capture_output=True, text=True)
    assert result.returncode == 2 and 'record_hash_mismatch' in result.stderr
    assert not (root/'.omc/state/output-records').exists()
    assert not (root/'.omc/state/output-failures').exists()
    assert source.read_text() == raw


@pytest.mark.parametrize('command', ['validate', 'publish'])
def test_preflight_unknown_stage_is_not_misreported_as_bad_verdict(detached_case, command):
    import subprocess, sys, hashlib
    root, _, _, source, raw, args = _choice_source(detached_case)
    raw = raw.replace('"stage": "ship"', '"stage": "SENSITIVE-stage"')
    source.write_text(raw)
    args[args.index('--source-sha256')+1] = hashlib.sha256(source.read_bytes()).hexdigest()
    result = subprocess.run([sys.executable, str(MODULE_PATH), command, *args], capture_output=True, text=True)
    assert result.returncode == 2 and not result.stdout
    assert 'stage_invalid' in result.stderr and '지원하지 않는 단계' in result.stderr
    assert '허용값' in result.stderr and 'ship' in result.stderr
    assert 'SENSITIVE' not in result.stderr
    assert not (root/'.omc/state/output-records').exists()


from test_omc_task_completion_surface import maintenance_case,maintenance_proof,maintenance_finish


@pytest.mark.parametrize('evidence',['baseline','operation','prefix'])
def test_maintenance_detached_gate_pins_all_custody_files(maintenance_case,evidence):
    import hashlib
    import omc_output_contract as output
    root,_,baseline,_=maintenance_case
    path,_=maintenance_proof(maintenance_case)
    result,context,_=maintenance_finish(root,path)
    assert result.returncode==0,result.stdout+result.stderr
    ctx=output.load_output_context(root,context['output_context'],context['output_context_sha256'])
    manifest=Path(context['record_path']);pin=hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert output.read_record(root,manifest,pin,ctx,display=result.stdout)['source_kind']=='completion_gate'
    target={'baseline':Path(baseline['baseline_file']),'operation':root/'.omc/evidence/operation.json',
            'prefix':root/'.omc/evidence/prefix.jsonl'}[evidence]
    target.write_bytes(target.read_bytes()+b' ')
    with pytest.raises(output.OutputContractError):output.read_record(root,manifest,pin,ctx,display=result.stdout)


from test_omc_task_completion_surface import maintenance_recovered_proof


def test_maintenance_r4_detached_record_pins_failed_prefix(maintenance_case,monkeypatch):
    import hashlib
    import omc_output_contract as output
    root,path,_,ref=maintenance_recovered_proof(maintenance_case,monkeypatch)
    result,context,_=maintenance_finish(root,path)
    assert result.returncode==0,result.stdout+result.stderr
    ctx=output.load_output_context(root,context['output_context'],context['output_context_sha256'])
    manifest=Path(context['record_path']);pin=hashlib.sha256(manifest.read_bytes()).hexdigest()
    output.read_record(root,manifest,pin,ctx,display=result.stdout)
    target=root/ref['path'];target.write_bytes(target.read_bytes()+b' ')
    with pytest.raises(output.OutputContractError):output.read_record(root,manifest,pin,ctx,display=result.stdout)


@pytest.mark.parametrize('expired', [True, False])
@pytest.mark.parametrize('text,shown', [('핵심 계획', True), ('', False), ('가' * 1200, True), ('가' * 1201, False), ('\n'.join(['가'] * 12), True), ('\n'.join(['가'] * 13), False)], ids=['short','empty','char_limit','char_over','line_limit','line_over'])
def test_failure_summary_public_cli_expired(detached_case, text, shown, expired):
    import hashlib, subprocess, sys, time
    root, file, _, ctx, _ = detached_case
    m = _load_module()
    summary = root / '.omc/summary.txt'; summary.write_text(text)
    raw = '계획 초안\n' + m.render_envelope(stage='plan', verdict='HOLD', risk='high', next_skill='omc-critique', user_selection_needed=True, reason_code='review_required')
    source = root / '.omc/source.txt'; source.write_text(raw)
    ctx.update(stage='plan', deadline_epoch=time.time()+(-1 if expired else 60),
        references=[{'path':str(summary), 'sha256':hashlib.sha256(summary.read_bytes()).hexdigest(), 'purpose':'draft_summary'}],
        draft_binding=m._current_binding(root))
    file.write_text(json.dumps(ctx)); pin=hashlib.sha256(file.read_bytes()).hexdigest()
    args=[sys.executable,str(MODULE_PATH),'publish','--target',str(root),
        '--output-context',str(file),'--output-context-sha256',pin,
        '--source-file',str(source),'--source-sha256',hashlib.sha256(source.read_bytes()).hexdigest(),
        '--draft-summary-file',str(summary),'--draft-summary-sha256',hashlib.sha256(summary.read_bytes()).hexdigest()]
    result=subprocess.run(args,capture_output=True,text=True)
    if not expired:
        assert result.returncode == 0 and result.stdout and not result.stderr
        assert m.record_path(root, ctx).exists()
        return
    assert result.returncode==2 and not result.stdout
    assert '공식 판정 전달 미확인' in result.stderr
    assert ('작성 초안·공식 승인 아님' in result.stderr) == shown
    assert 'record_deadline_expired' in result.stderr
    assert not m.record_path(root,ctx).exists()
    assert source.read_text()==raw


def test_failure_summary_skill_instructions(installed_omc_target):
    for relative in ['.agents/skills/omc-plan/references/workflow.md','.agents/skills/omc-critique/SKILL.md']:
        text=(installed_omc_target/relative).read_text()
        assert '발행 직전에' in text and '--draft-summary-file' in text
        assert '1,200자' in text and '종료2' in text


@pytest.mark.parametrize('defect', ['session','work','stage','binding','source','summary','reference','symlink','during_diagnostic'])
def test_failure_summary_rejects_unbound_inputs(detached_case, monkeypatch, capsys, defect):
    import hashlib, time
    root,file,_,ctx,_=detached_case; m=_load_module()
    summary=root/'.omc/summary.txt'; summary.write_text('DO_NOT_LEAK')
    raw=m.render_envelope(stage='plan',verdict='HOLD',risk='high',next_skill='omc-critique',user_selection_needed=True,reason_code='review_required')
    source=root/'.omc/source.txt';source.write_text(raw)
    summary_pin=hashlib.sha256(summary.read_bytes()).hexdigest()
    ctx.update(stage='plan',deadline_epoch=time.time()-1,references=[{'path':str(summary),'sha256':summary_pin,'purpose':'draft'}],draft_binding=m._current_binding(root))
    draft={'source_file':source,'source_pin':hashlib.sha256(source.read_bytes()).hexdigest(),'summary_file':summary,'summary_pin':summary_pin}
    if defect in ('session','work'):ctx[defect+'_id']='different'
    elif defect=='stage':ctx['stage']='review'
    elif defect=='binding':ctx['draft_binding']={}
    elif defect=='source':source.write_text('changed')
    elif defect=='summary':summary.write_text('changed')
    elif defect=='reference':ctx['references']=[]
    elif defect=='during_diagnostic':
        real=m._write_failure_diagnostic
        def changed(*args, **kwargs):
            real(*args, **kwargs)
            summary.write_text('changed after diagnostic')
        monkeypatch.setattr(m,'_write_failure_diagnostic',changed)
    else:
        original=summary.with_suffix('.original');summary.rename(original);summary.symlink_to(original)
    file.write_text(json.dumps(ctx));pin=hashlib.sha256(file.read_bytes()).hexdigest()
    assert m.emit_record(root,file,pin,raw,draft=draft)==2
    output=capsys.readouterr()
    assert not output.out and 'DO_NOT_LEAK' not in output.err
    assert '작성 초안·공식 승인 아님' not in output.err


@pytest.mark.parametrize('failure', ['display', 'publication', 'diagnostic'])
def test_failure_summary_display_failure_preserves_publication(detached_case, monkeypatch, capsys, failure):
    import hashlib
    root,file,_,ctx,_=detached_case;m=_load_module()
    summary=root/'.omc/summary.txt';summary.write_text('확인할 계획')
    raw=m.render_envelope(stage='plan',verdict='HOLD',risk='high',next_skill='omc-critique',user_selection_needed=True,reason_code='review_required')
    source=root/'.omc/source.txt';source.write_text(raw)
    pin_summary=hashlib.sha256(summary.read_bytes()).hexdigest()
    ctx.update(stage='plan',references=[{'path':str(summary),'sha256':pin_summary,'purpose':'draft'}],draft_binding=m._current_binding(root))
    file.write_text(json.dumps(ctx));pin=hashlib.sha256(file.read_bytes()).hexdigest()
    draft={'source_file':source,'source_pin':hashlib.sha256(source.read_bytes()).hexdigest(),'summary_file':summary,'summary_pin':pin_summary}
    def broken(_):raise BrokenPipeError('injected display failure')
    if failure == 'display':
        monkeypatch.setattr(m,'_write_display',broken)
    else:
        def unavailable(*args, **kwargs):raise OSError('injected storage failure')
        monkeypatch.setattr(m,'_publish',unavailable)
        if failure == 'diagnostic':monkeypatch.setattr(m,'_write_failure_diagnostic',unavailable)
    assert m.emit_record(root,file,pin,raw,draft=draft)==2
    out=capsys.readouterr();assert not out.out
    assert '작성 초안·공식 승인 아님' in out.err
    if failure != 'display':
        assert '기록 상태: 확인 불가' in out.err and not m.record_path(root,ctx).exists()
        if failure == 'diagnostic':assert '별도 오류 기록: 확인 불가' in out.err
        return
    assert '발행됨·전달 미확인' in out.err
    manifest=m.record_path(root,ctx);before=manifest.read_bytes()
    record=m.read_record(root,manifest,hashlib.sha256(before).hexdigest(),ctx)
    assert record['publication']=='published' and manifest.read_bytes()==before


def _frozen_observation_root(root):
    import time, subprocess
    state=root/'.omc/state';state.mkdir(parents=True)
    (state/'latest.json').write_text(json.dumps({'latest_confirmed_session_id':'reader','latest_session_id':'reader'}))
    for sid,roles in [('reader',['analysis']),('owner',['senior_coding'])]:
        folder=state/'sessions'/sid;folder.mkdir(parents=True)
        (folder/'session.json').write_text(json.dumps({'session_id':sid,'work_id':'work','role_ids':roles,'confirmation':{'status':'confirmed'}}))
    (state/'pending-completion.json').write_text(json.dumps({'work_id':'work','session_ids':['owner']}))
    (root/'.omc/pipeline_session.json').write_text(json.dumps({'session_id':'owner','owner_session_id':'owner','owner_work_id':'work','started_at':time.time()-28900}))
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    (root/'app.py').write_text('value=1\n');subprocess.run(['git','add','app.py'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','base'],cwd=root,check=True,capture_output=True)


def _observation_prepare(root,stage='plan',report='observation'):
    import subprocess,sys
    return subprocess.run([sys.executable,str(MODULE_PATH),'prepare','--target',str(root),
        '--stage',stage,'--report-id',report,'--budget-sec','60'],capture_output=True,text=True)


@pytest.mark.parametrize('stage',['plan','critique-plan'])
def test_observation_frozen_publication_and_preservation(tmp_path,stage):
    import hashlib,subprocess,sys
    root=tmp_path/'work';root.mkdir();_frozen_observation_root(root)
    protected=[root/'.git/index',root/'.git/HEAD',*list((root/'.omc/state').rglob('*.json')),root/'.omc/pipeline_session.json',root/'app.py']
    before={p:p.read_bytes() for p in protected}
    prepared=_observation_prepare(root,stage)
    assert prepared.returncode==0,prepared.stderr
    meta=json.loads(prepared.stdout);m=_load_module()
    ctx=m.load_output_context(root,meta['output_context'],meta['output_context_sha256'])
    assert ctx['context_kind']=='observation_only'
    raw='관찰용 계획\n'+m.render_envelope(stage=stage,verdict='HOLD',risk='high',next_skill='omc-plan',user_selection_needed=True,reason_code='scope_unclear' if stage=='plan' else 'fixture')
    src=root/'.omc/source.txt';src.write_text(raw)
    result=subprocess.run([sys.executable,str(MODULE_PATH),'publish','--target',str(root),'--output-context',meta['output_context'],'--output-context-sha256',meta['output_context_sha256'],'--source-file',str(src),'--source-sha256',hashlib.sha256(src.read_bytes()).hexdigest()],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    manifest=m.record_path(root,ctx)
    assert 'output-observations' in str(manifest)
    doc=m.read_record(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),ctx,display=result.stdout)
    assert doc['source_kind']=='model_declared'
    assert all(p.read_bytes()==data for p,data in before.items())
    duplicate=_observation_prepare(root,stage)
    assert duplicate.returncode==2


@pytest.mark.parametrize('stage',['task','review','ship','investigate'])
def test_observation_frozen_execution_stages_still_rejected(tmp_path,stage):
    root=tmp_path/'work';root.mkdir();_frozen_observation_root(root)
    result=_observation_prepare(root,stage)
    assert result.returncode==2
    assert 'resume_context_required' in result.stderr


@pytest.mark.parametrize('defect',['malformed','different_work','retirement','slot_symlink'])
def test_observation_bad_governance_has_structured_prepare_error(tmp_path,defect):
    import omc_edit_resume as resume
    root=tmp_path/'work';root.mkdir();_frozen_observation_root(root)
    if defect=='malformed':(root/'.omc/pipeline_session.json').write_text('bad')
    elif defect=='different_work':(root/'.omc/state/pending-completion.json').write_text(json.dumps({'work_id':'other'}))
    elif defect=='retirement':
        marker=root/resume.RETIREMENT_PENDING;marker.parent.mkdir(parents=True);marker.write_text('{}')
    else:
        slot=resume.slot_path(root,'work');slot.parent.mkdir(parents=True);slot.symlink_to(root/'app.py')
    result=_observation_prepare(root)
    assert result.returncode==2
    assert 'reason_code:' in result.stderr and '다음 행동:' in result.stderr


@pytest.mark.parametrize('defect',['code','index','lifecycle','context_hash','deadline','stage','issuer'])
def test_observation_binding_and_authority_fail_closed(tmp_path,defect):
    import hashlib,time
    root=tmp_path/'work';root.mkdir();_frozen_observation_root(root)
    prepared=_observation_prepare(root);assert prepared.returncode==0,prepared.stderr
    meta=json.loads(prepared.stdout);m=_load_module();file=Path(meta['output_context'])
    ctx=json.loads(file.read_bytes());pin=meta['output_context_sha256']
    raw='계획\n'+m.render_envelope(stage='plan',verdict='HOLD',risk='high',next_skill='omc-plan',user_selection_needed=True,reason_code='scope_unclear')
    if defect=='code':(root/'app.py').write_text('value=2\n')
    elif defect=='index':(root/'.git/index').write_bytes((root/'.git/index').read_bytes()+b'changed')
    elif defect=='lifecycle':(root/'.omc/pipeline_session.json').write_text('{}')
    elif defect=='context_hash':pin='0'*64
    elif defect=='deadline':ctx['deadline_epoch']=time.time()-1
    elif defect=='stage':ctx['stage']='task'
    if defect in {'deadline','stage'}:
        file=root/'.omc/altered-context.json';file.write_text(json.dumps(ctx));pin=hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises((m.OutputContractError,OSError,ValueError)):
        m.publish_record(root,file,pin,raw,source_kind='legacy_normalized' if defect=='issuer' else 'model_declared')


def test_observation_readable_expired_slot_only_reports(tmp_path):
    import omc_edit_resume as resume
    root=tmp_path/'work';root.mkdir();_frozen_observation_root(root)
    gen='.omc/edit-resumptions/generations/expired'
    (root/gen).mkdir(parents=True);(root/gen/'pipeline.json').write_text('{}')
    slot=resume.slot_path(root,'work');slot.parent.mkdir(parents=True)
    slot.write_text(json.dumps({'grant_id':'past','generation':gen,'boot':'past-boot','t0':0,'deadline':1,'binding':{'root':str(root.resolve()),'work_id':'work'}}))
    assert _observation_prepare(root).returncode==0
    assert _observation_prepare(root,'task','cannot-resume').returncode==2


@pytest.mark.parametrize('confirmation',[None,'confirmed',[],True])
def test_observation_nested_confirmation_rejected_publicly(tmp_path,confirmation):
    root=tmp_path/'work';root.mkdir();_frozen_observation_root(root)
    session=root/'.omc/state/sessions/reader/session.json'
    data=json.loads(session.read_text());data['confirmation']=confirmation;session.write_text(json.dumps(data))
    before={p:p.read_bytes() for p in (root/'.omc').rglob('*.json')}
    result=_observation_prepare(root)
    assert result.returncode==2,result.stderr
    assert 'observation_session_binding_invalid' in result.stderr
    assert '다음 행동:' in result.stderr and 'Traceback' not in result.stderr
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert not (root/'.omc/state/output-observations').exists()


@pytest.mark.parametrize('members',[None,'owner',{'owner':True},['owner','owner'],['owner',None]])
def test_observation_nested_lineage_rejected_publicly(tmp_path,members):
    root=tmp_path/'work';root.mkdir();_frozen_observation_root(root)
    pending=root/'.omc/state/pending-completion.json'
    data=json.loads(pending.read_text());data['session_ids']=members;pending.write_text(json.dumps(data))
    before=pending.read_bytes();result=_observation_prepare(root)
    assert result.returncode==2,result.stderr
    assert 'observation_work_binding_invalid' in result.stderr
    assert '다음 행동:' in result.stderr and 'Traceback' not in result.stderr
    assert pending.read_bytes()==before
    assert not (root/'.omc/state/output-observations').exists()


# Reuse the existing literal oracle, never the production routing resolver.
_DECISION_CASES = [row for row in test_detached_all_stage_mappings_remain_unchanged.pytestmark[0].args[1]
                   if row[0] != 'task'] + [
    ('plan','HOLD','review_required','omc-critique',True),
    ('review','APPROVE',None,None,False),('review','APPROVE WITH NOTES',None,None,False)]


@pytest.mark.parametrize('stage,verdict,reason,next_skill,selection',_DECISION_CASES)
def test_decision_derived_fields_match_fixed_oracle(stage,verdict,reason,next_skill,selection):
    m=_load_module();kw={'user_selection_needed':selection} if stage=='review' and verdict.startswith('APPROVE') else {}
    parsed=m.parse_envelope(m.render_decision(stage=stage,verdict=verdict,risk='low',reason_code=reason,**kw))
    assert (parsed['next_skill'],parsed['user_selection_needed'])==(next_skill,selection)
    assert parsed['verdict']==verdict and parsed['stage']==stage


@pytest.mark.parametrize('value',[None,0,1,'false'])
def test_decision_requires_real_review_selection_bool(value):
    m=_load_module()
    with pytest.raises(m.OutputContractError):m.render_decision(stage='review',verdict='APPROVE',risk='low',user_selection_needed=value)


def test_decision_rejects_missing_selection_conflict_and_task():
    m=_load_module()
    for kw in [dict(stage='review',verdict='APPROVE'),dict(stage='review',verdict='REVISE',reason_code='fixture',user_selection_needed=True),dict(stage='task',verdict='PROCEED')]:
        with pytest.raises(m.OutputContractError):m.render_decision(risk='low',**kw)
    # The existing explicit interface must still reject the old mistake.
    with pytest.raises(m.OutputContractError,match='next_skill conflicts'):
        m.render_envelope(stage='review',verdict='APPROVE',risk='low',next_skill='omc-ship',user_selection_needed=True)


def _decision_cli(root,*args):
    import subprocess,sys
    return subprocess.run([sys.executable,'-B',str(MODULE_PATH),*args],cwd=root,text=True,capture_output=True,timeout=10)


@pytest.mark.parametrize('stage,verdict,reason,next_skill,selection',_DECISION_CASES)
def test_decision_public_cli_publication_surface(detached_case,stage,verdict,reason,next_skill,selection):
    import hashlib
    root,*_=detached_case;m=_load_module();before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    args=['render-decision','--stage',stage,'--verdict',verdict,'--risk','low']
    if reason:args+=['--reason-code',reason]
    if stage=='review' and verdict.startswith('APPROVE'):args+=['--user-selection-needed',str(selection).lower()]
    result=_decision_cli(root,*args);assert result.returncode==0,result.stderr
    assert {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
    payload=m.parse_envelope(result.stdout);assert (payload['next_skill'],payload['user_selection_needed'])==(next_skill,selection)
    prep=_decision_cli(root,'prepare','--target',str(root),'--stage',stage,'--report-id','decision','--budget-sec','60')
    assert prep.returncode==0,prep.stderr
    meta=json.loads(prep.stdout);source=root/'.omc/decision.txt';source.write_text('결과 근거: fixture\n'+result.stdout)
    published=_decision_cli(root,'publish','--target',str(root),'--output-context',meta['output_context'],'--output-context-sha256',meta['output_context_sha256'],'--source-file',str(source),'--source-sha256',hashlib.sha256(source.read_bytes()).hexdigest());assert published.returncode==0,published.stderr
    context=m.load_output_context(root,meta['output_context'],meta['output_context_sha256']);manifest=Path(meta['record_path'])
    record=m.read_record(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),context,display=published.stdout)
    assert record['verdict']==verdict and record['machine_payload']=={k:v for k,v in payload.items() if k!='verdict'}


@pytest.mark.parametrize('extra',[
    ['--stage','task','--verdict','PROCEED'],['--stage','unknown','--verdict','PROCEED'],
    ['--stage','review','--verdict','APPROVE'],
    ['--stage','review','--verdict','APPROVE','--user-selection-needed','null'],
    ['--stage','review','--verdict','REVISE','--reason-code','fixture','--user-selection-needed','true'],
    ['--stage','review','--verdict','APPROVE','--user-selection-needed','true','--next-skill','omc-ship'],
    ['--stage','investigate','--verdict','PROCEED','--reason-code','unknown'],
])
def test_decision_public_cli_rejects_without_state_writes(tmp_path,extra):
    before=list(tmp_path.iterdir());r=_decision_cli(tmp_path,'render-decision','--risk','low',*extra)
    assert r.returncode==2 and not r.stdout and list(tmp_path.iterdir())==before


def test_decision_capability_is_additive():
    m=_load_module();v=m.capabilities();assert v['render_decision'] is True
    assert v['schema_version']=='omc-output-record/v1' and v['output_modes']==['legacy_inline','detached_v1']


def _stage_case(case, stage='review'):
    import hashlib
    root, context, _, ctx, raw = case
    ctx['stage'] = stage
    context.write_text(json.dumps(ctx))
    m = _load_module()
    if stage == 'ship':
        raw = '검사 근거\n' + m.render_envelope(stage='ship', verdict='PROCEED', risk='low', reason_code='all_gates_passed', next_skill=None, user_selection_needed=True)
    source = root.parent / (root.name + '-external.txt')
    source.write_text(raw)
    return root, context, hashlib.sha256(context.read_bytes()).hexdigest(), ctx, source, hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize('stage',['review','ship'])
def test_stage_source_cli_roundtrip_and_preservation(detached_case,stage):
    import hashlib
    root, context, pin, ctx, source, sha = _stage_case(detached_case,stage)
    before=source.read_bytes(); m=_load_module()
    direct=_decision_cli(root,'publish','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',sha)
    assert direct.returncode==2 and 'record_path_outside_work' in direct.stderr
    result=_decision_cli(root,'stage-source','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',sha)
    assert result.returncode==0,result.stderr
    meta=json.loads(result.stdout);local=Path(meta['source_file'])
    assert local.read_bytes()==before==source.read_bytes() and meta['source_sha256']==sha
    assert local.stat().st_mode & 0o777 == 0o600
    assert local==m.record_path(root,ctx).parent.parent/'.source-staging/r.txt'
    assert not m.record_path(root,ctx).exists()
    args=['--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(local),'--source-sha256',sha]
    assert _decision_cli(root,'validate',*args).returncode==0
    pub=_decision_cli(root,'publish',*args);assert pub.returncode==0,pub.stderr
    manifest=m.record_path(root,ctx)
    record=m.read_record(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),ctx,display=pub.stdout)
    assert record['raw_source'].encode()==before
    assert _decision_cli(root,'stage-source',*args).returncode==2


@pytest.mark.parametrize('defect',['hash','stage','expired','session','work','observation','task','source_link','parent_link','large','duplicate','manifest'])
def test_stage_source_rejects_unsafe_input(detached_case,defect):
    import time
    root,context,pin,ctx,source,sha=_stage_case(detached_case);m=_load_module()
    if defect=='hash':sha='0'*64
    if defect=='stage':ctx['stage']='ship'
    if defect=='expired':ctx['deadline_epoch']=time.time()-1
    if defect=='session':ctx['session_id']='other'
    if defect=='work':ctx['work_id']='other'
    if defect=='observation':ctx['context_kind']='observation_only'
    if defect=='task':ctx['stage']='task'
    if defect=='source_link':
        moved=source.with_suffix('.raw');source.rename(moved);source.symlink_to(moved)
    if defect=='parent_link':
        alias=source.parent/(root.name+'-alias');alias.symlink_to(source.parent,target_is_directory=True);source=alias/source.name
    if defect=='large':source.write_bytes(b'x'*(1024*1024+1))
    if defect=='duplicate':
        local=m.record_path(root,ctx).parent.parent/'.source-staging/r.txt';local.parent.mkdir(parents=True);local.write_text('preserve')
    if defect=='manifest':m.record_path(root,ctx).parent.mkdir(parents=True)
    import hashlib
    context.write_text(json.dumps(ctx));pin=hashlib.sha256(context.read_bytes()).hexdigest()
    protected={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result=_decision_cli(root,'stage-source','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',sha)
    assert result.returncode==2 and not result.stdout
    assert all(p.read_bytes()==v for p,v in protected.items())
    assert not m.record_path(root,ctx).exists()


@pytest.mark.parametrize('operation',['write','fsync','link'])
def test_stage_source_io_failure_has_no_success_copy(detached_case,monkeypatch,operation):
    root,context,pin,ctx,source,sha=_stage_case(detached_case);m=_load_module();before=source.read_bytes()
    def fail(*a,**kw):raise PermissionError('injected')
    monkeypatch.setattr(m.os,operation,fail)
    with pytest.raises(OSError):m.stage_source(root,context,pin,source,sha)
    assert source.read_bytes()==before
    folder=m.record_path(root,ctx).parent.parent/'.source-staging'
    assert not list(folder.glob('*')) and not m.record_path(root,ctx).exists()


def test_stage_source_concurrent_one_winner(detached_case):
    from concurrent.futures import ThreadPoolExecutor
    root,context,pin,ctx,source,sha=_stage_case(detached_case)
    def invoke():return _decision_cli(root,'stage-source','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',sha)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:invoke(),range(2)))
    assert sorted(r.returncode for r in results)==[0,2]


def test_stage_source_rechecks_context_before_commit(detached_case,monkeypatch):
    root,context,pin,ctx,source,sha=_stage_case(detached_case);m=_load_module();original=m.os.fsync
    def change(fd):
        original(fd)
        (root/'.omc/state/latest.json').write_text(json.dumps({'latest_confirmed_session_id':'changed'}))
    monkeypatch.setattr(m.os,'fsync',change)
    with pytest.raises(m.OutputContractError):m.stage_source(root,context,pin,source,sha)
    assert not (m.record_path(root,ctx).parent.parent/'.source-staging/r.txt').exists()


def test_stage_source_capability():
    assert _load_module().capabilities()['stage_source'] is True


def test_stage_source_resume_generation_and_rotation(detached_case,monkeypatch):
    import sys,types
    root,context,pin,ctx,source,sha=_stage_case(detached_case)
    resume={'grant_id':'grant','generation':'.omc/resume/g1'}
    monkeypatch.setitem(sys.modules,'omc_edit_resume',types.SimpleNamespace(frozen=lambda _:True,load=lambda _:resume))
    ctx['edit_resume']=dict(resume)
    import hashlib
    context.write_text(json.dumps(ctx));pin=hashlib.sha256(context.read_bytes()).hexdigest();m=_load_module()
    result=m.stage_source(root,context,pin,source,sha)
    assert Path(result['source_file'])==root/'.omc/resume/g1/output-records/.source-staging/r.txt'
    resume['generation']='.omc/resume/g2'
    with pytest.raises(m.OutputContractError,match='generation'):m.stage_source(root,context,pin,source,sha)


@pytest.mark.parametrize('defect',['source_change','parent_change','destination_link','zero_write','second_fsync'])
def test_stage_source_race_and_partial_failure(detached_case,monkeypatch,defect):
    root,context,pin,ctx,source,sha=_stage_case(detached_case);m=_load_module()
    folder=m.record_path(root,ctx).parent.parent/'.source-staging'
    outside=root.parent/(root.name+'-outside');outside.mkdir()
    if defect=='source_change':
        original=m.os.read
        def read(fd,size):
            data=original(fd,size)
            if data:source.write_bytes(b'changed')
            return data
        monkeypatch.setattr(m.os,'read',read)
    elif defect=='zero_write':monkeypatch.setattr(m.os,'write',lambda *a:0)
    elif defect=='destination_link':
        folder.parent.mkdir(parents=True,exist_ok=True);folder.symlink_to(outside,target_is_directory=True)
    else:
        original=m.os.fsync;count=[0]
        def sync(fd):
            count[0]+=1
            if defect=='second_fsync' and count[0]==2:raise OSError('injected directory sync')
            original(fd)
            if defect=='parent_change' and count[0]==1:
                folder.rename(folder.with_name('moved'));folder.symlink_to(outside,target_is_directory=True)
        monkeypatch.setattr(m.os,'fsync',sync)
    with pytest.raises((OSError,m.OutputContractError)):m.stage_source(root,context,pin,source,sha)
    assert not list(outside.iterdir()) and not m.record_path(root,ctx).exists()
    # A fully committed but unacknowledged copy may remain after directory fsync failure.
    if defect!='second_fsync':assert not (folder/'r.txt').exists()


def test_stage_source_real_sealed_review_roundtrip(detached_case):
    import subprocess,sys,hashlib
    root,context,pin,ctx,source,sha=_stage_case(detached_case)
    def git(*args):return subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    git('init','-q');git('config','user.email','fixture@example.invalid');git('config','user.name','fixture')
    (root/'.gitignore').write_text('.omc/\n');(root/'file.txt').write_text('before')
    git('add','.gitignore','file.txt');git('commit','-qm','baseline');base=git('rev-parse','HEAD')
    (root/'file.txt').write_text('after');git('add','file.txt');index=(root/'.git/index').read_bytes()
    cli=ROOT/'scripts/omc_review_snapshot.py'
    def command(*args):
        p=subprocess.run([sys.executable,str(cli),*args],cwd=root,capture_output=True,text=True)
        assert p.returncode==0,p.stdout+p.stderr
        return json.loads(p.stdout)
    capture=command('capture-review','--target',str(root),'--base-commit',base)
    seal=command('seal-review-output','--target',str(root),'--review-snapshot',capture['snapshot_path'],'--review-snapshot-sha256',capture['snapshot_sha256'],'--review-output',str(source))
    assert Path(seal['evidence_path']).is_file()
    result=_decision_cli(root,'stage-source','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',sha)
    assert result.returncode==0,result.stderr
    local=Path(json.loads(result.stdout)['source_file']);assert hashlib.sha256(local.read_bytes()).hexdigest()==sha
    args=['--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(local),'--source-sha256',sha]
    assert _decision_cli(root,'validate',*args).returncode==0
    p=_decision_cli(root,'publish',*args);assert p.returncode==0,p.stderr
    m=_load_module();manifest=m.record_path(root,ctx)
    rec=m.read_record(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),ctx,display=p.stdout)
    assert rec['raw_source']==source.read_text() and (root/'.git/index').read_bytes()==index


def test_stage_source_tampered_copy_is_not_publishable(detached_case):
    root,context,pin,ctx,source,sha=_stage_case(detached_case);m=_load_module()
    local=Path(m.stage_source(root,context,pin,source,sha)['source_file']);local.write_text('tampered')
    r=_decision_cli(root,'publish','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(local),'--source-sha256',sha)
    assert r.returncode==2 and not m.record_path(root,ctx).exists()


@pytest.mark.parametrize('stage',['review','ship'])
@pytest.mark.parametrize('normal_first',[False,True])
def test_stage_source_report_id_matches_old_folder_name(detached_case,stage,normal_first):
    import hashlib
    root,context,pin,ctx,source,sha=_stage_case(detached_case,stage)
    m=_load_module()
    if normal_first:m.stage_source(root,context,pin,source,sha)
    ctx['report_id']='source-staging'
    context.write_text(json.dumps(ctx));pin=hashlib.sha256(context.read_bytes()).hexdigest()
    staged=_decision_cli(root,'stage-source','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',str(source),'--source-sha256',sha)
    assert staged.returncode==0,staged.stderr
    local=json.loads(staged.stdout)
    assert Path(local['source_file']).parent.name=='.source-staging'
    assert Path(local['source_file']).read_bytes()==source.read_bytes()
    pub=_decision_cli(root,'publish','--target',str(root),'--output-context',str(context),'--output-context-sha256',pin,'--source-file',local['source_file'],'--source-sha256',sha)
    assert pub.returncode==0,pub.stderr
    manifest=m.record_path(root,ctx)
    m.read_record(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),ctx,display=pub.stdout)
