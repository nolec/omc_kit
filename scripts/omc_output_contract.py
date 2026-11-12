#!/usr/bin/env python3
"""Compact machine envelope for OMC skill outputs."""
from __future__ import annotations

import json
import re
from typing import Any


SCHEMA_VERSION = "omc-output/v1"
_ENVELOPE_PREFIX = "OMC_OUTPUT:"
_HIDDEN_ENVELOPE_PREFIX = "<!-- OMC_OUTPUT:"
_HIDDEN_ENVELOPE_SUFFIX = "-->"
_VERDICT_RE = re.compile(
    r"^VERDICT\s*:\s*(PROCEED WITH CAUTION|APPROVE WITH NOTES|PROCEED|APPROVE|BLOCK|REVISE|HOLD)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_STAGE_VERDICTS = {
    "plan": {
        "PROCEED": "ready",
        "HOLD": "unresolved",
        "REVISE": "unresolved",
    },
    "task": {
        "PROCEED": "done",
        "BLOCK": "blocked",
    },
    "review": {
        "APPROVE": "approved",
        "APPROVE WITH NOTES": "approved",
        "REVISE": "blocked",
        "BLOCK": "blocked",
    },
    "critique-plan": {
        "PROCEED": "ready",
        "PROCEED WITH CAUTION": "ready",
        "HOLD": "unresolved",
        "REVISE": "unresolved",
    },
    "critique-code": {
        "PROCEED": "approved",
        "APPROVE": "approved",
        "APPROVE WITH NOTES": "approved",
        "REVISE": "blocked",
        "BLOCK": "blocked",
        "HOLD": "unresolved",
    },
    "investigate": {
        "PROCEED": "ready",
        "REVISE": "unresolved",
        "HOLD": "unresolved",
    },
    "ship": {
        "PROCEED": "ready",
        "BLOCK": "blocked",
    },
}
_RISKS = {"low", "medium", "high"}
_REQUIRED_FIELDS = {
    "schema_version",
    "stage",
    "outcome",
    "risk",
    "next_skill",
    "user_selection_needed",
    "reason_code",
}
_LEGACY_DEFAULTS = {
    ("plan", "PROCEED"): ("low", "omc-task", False, None),
    ("plan", "HOLD"): ("high", "omc-plan", True, "legacy_verdict_only"),
    ("plan", "REVISE"): ("medium", "omc-plan", True, "legacy_verdict_only"),
    ("task", "PROCEED"): ("low", "omc-review", False, None),
    ("task", "BLOCK"): ("high", None, True, "legacy_verdict_only"),
    ("review", "APPROVE"): ("low", None, True, None),
    ("review", "APPROVE WITH NOTES"): ("medium", None, True, None),
    ("review", "REVISE"): ("medium", "omc-task", False, "legacy_verdict_only"),
    ("review", "BLOCK"): ("high", None, True, "legacy_verdict_only"),
    ("critique-plan", "PROCEED"): ("low", "omc-task", False, None),
    ("critique-plan", "PROCEED WITH CAUTION"): ("medium", "omc-task", True, None),
    ("critique-plan", "HOLD"): ("high", "omc-plan", True, "legacy_verdict_only"),
    ("critique-plan", "REVISE"): ("medium", "omc-plan", True, "legacy_verdict_only"),
    ("critique-code", "PROCEED"): ("low", "omc-review", False, None),
    ("critique-code", "APPROVE"): ("low", "omc-review", False, None),
    ("critique-code", "APPROVE WITH NOTES"): ("medium", "omc-review", False, None),
    ("critique-code", "REVISE"): ("medium", "omc-task", False, "legacy_verdict_only"),
    ("critique-code", "BLOCK"): ("high", None, True, "legacy_verdict_only"),
    ("critique-code", "HOLD"): ("high", None, True, "legacy_verdict_only"),
}
_ROUTING_POLICY = {
    ("plan", "PROCEED"): ("omc-task", False),
    ("plan", "HOLD"): ("omc-plan", True),
    ("plan", "REVISE"): ("omc-plan", True),
    ("task", "PROCEED"): ("omc-review", False),
    ("task", "BLOCK"): (None, True),
    ("review", "APPROVE"): (None, None),
    ("review", "APPROVE WITH NOTES"): (None, None),
    ("review", "REVISE"): ("omc-task", False),
    ("review", "BLOCK"): (None, True),
    ("critique-plan", "PROCEED"): ("omc-task", False),
    ("critique-plan", "PROCEED WITH CAUTION"): ("omc-task", True),
    ("critique-plan", "HOLD"): ("omc-plan", True),
    ("critique-plan", "REVISE"): ("omc-plan", True),
    ("critique-code", "PROCEED"): ("omc-review", False),
    ("critique-code", "APPROVE"): ("omc-review", False),
    ("critique-code", "APPROVE WITH NOTES"): ("omc-review", False),
    ("critique-code", "REVISE"): ("omc-task", False),
    ("critique-code", "BLOCK"): (None, True),
    ("critique-code", "HOLD"): (None, True),
}
_REASON_ROUTING_POLICY = {
    ("investigate", "PROCEED"): {
        "root_cause_confirmed": ("omc-task", False),
        "fix_already_applied": ("omc-review", False),
    },
    ("investigate", "REVISE"): {
        "architecture_scope_issue": ("omc-ceo-review", False),
    },
    ("investigate", "HOLD"): {
        "insufficient_evidence": (None, True),
    },
    ("ship", "PROCEED"): {
        "all_gates_passed": (None, True),
    },
    ("ship", "BLOCK"): {
        "test_or_regression_failure": ("omc-investigate", False),
        "tdd_or_test_missing": ("omc-task", False),
        "approval_missing": (None, True),
    },
}
_PROMPT_ROUTING = {
    "plan": (
        "PROCEED=>ready,omc-task,false; HOLD=>unresolved,omc-plan,true; "
        "REVISE=>unresolved,omc-plan,true"
    ),
    "task": "PROCEED=>done,omc-review,false; BLOCK=>blocked,null,true",
    "review": (
        "APPROVE=>approved,null,context; APPROVE WITH NOTES=>approved,null,context; "
        "REVISE=>blocked,omc-task,false; BLOCK=>blocked,null,true"
    ),
    "critique-plan": (
        "PROCEED=>ready,omc-task,false; HOLD=>unresolved,omc-plan,true; "
        "REVISE=>unresolved,omc-plan,true"
    ),
    "critique-code": (
        "PROCEED|APPROVE|APPROVE WITH NOTES=>approved,omc-review,false; "
        "REVISE=>blocked,omc-task,false; BLOCK=>blocked,null,true; "
        "HOLD=>unresolved,null,true"
    ),
    "investigate": (
        "PROCEED+root_cause_confirmed=>ready,omc-task,false; "
        "PROCEED+fix_already_applied=>ready,omc-review,false; "
        "REVISE+architecture_scope_issue=>unresolved,omc-ceo-review,false; "
        "HOLD+insufficient_evidence=>unresolved,null,true"
    ),
    "ship": (
        "PROCEED+all_gates_passed=>ready,null,true; "
        "BLOCK+test_or_regression_failure=>blocked,omc-investigate,false; "
        "BLOCK+tdd_or_test_missing=>blocked,omc-task,false; "
        "BLOCK+approval_missing=>blocked,null,true"
    ),
}


class OutputContractError(ValueError):
    """Raised when an OMC output envelope is missing or inconsistent."""


class ContractChoiceError(OutputContractError):
    """Closed-schema hints; rejected user values are never echoed to stderr."""

    def __init__(self, message, *, code, allowed):
        super().__init__(message)
        self.code = code
        self.allowed = tuple(sorted(allowed))


def _envelope_payload_from_line(line: str) -> str | None:
    stripped = str(line).strip()
    if stripped.startswith(_ENVELOPE_PREFIX):
        return stripped[len(_ENVELOPE_PREFIX) :].strip()
    if stripped.startswith(_HIDDEN_ENVELOPE_PREFIX) and stripped.endswith(
        _HIDDEN_ENVELOPE_SUFFIX
    ):
        return stripped[len(_HIDDEN_ENVELOPE_PREFIX) : -len(_HIDDEN_ENVELOPE_SUFFIX)].strip()
    return None


def _html_comment_safe_payload(serialized: str) -> str:
    """Prevent JSON string values from opening or closing the HTML comment."""
    return serialized.replace("--", "\\u002d\\u002d")


def outcome_for(stage: str, verdict: str) -> str:
    normalized_stage = str(stage).strip().lower()
    normalized_verdict = str(verdict).strip().upper()
    if normalized_stage not in _STAGE_VERDICTS:
        raise ContractChoiceError('unsupported stage', code='stage_invalid', allowed=_STAGE_VERDICTS)
    try:
        return _STAGE_VERDICTS[normalized_stage][normalized_verdict]
    except KeyError as exc:
        raise ContractChoiceError(
            f"verdict {normalized_verdict!r} is not valid for stage {normalized_stage!r}",
            code="verdict_invalid", allowed=_STAGE_VERDICTS.get(normalized_stage, {})
        ) from exc


def _validate_next_skill(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"omc-[a-z][a-z-]*", value):
        raise OutputContractError("next_skill must be a canonical omc-* skill id or null")
    return value


def _routing_for(
    stage: str,
    verdict: str,
    reason_code: str | None,
) -> tuple[str | None, bool | None]:
    # Only an explicit review request changes the existing replanning route.
    if (stage, verdict, reason_code) == ("plan", "HOLD", "review_required"):
        return "omc-critique", True
    reason_routes = _REASON_ROUTING_POLICY.get((stage, verdict))
    if reason_routes is not None:
        if reason_code not in reason_routes:
            raise ContractChoiceError("reason_code is not valid for stage verdict routing",
                                      code="reason_code_invalid", allowed=reason_routes)
        return reason_routes[reason_code]
    return _ROUTING_POLICY[(stage, verdict)]


def _validate_payload(payload: dict[str, Any], verdict: str) -> dict[str, Any]:
    missing = sorted(_REQUIRED_FIELDS - payload.keys())
    if missing:
        raise OutputContractError(f"missing envelope fields: {', '.join(missing)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise OutputContractError("unsupported schema_version")

    stage = str(payload.get("stage") or "").strip().lower()
    expected_outcome = outcome_for(stage, verdict)
    if payload.get("outcome") != expected_outcome:
        raise OutputContractError("outcome conflicts with stage verdict mapping")

    risk = str(payload.get("risk") or "").strip().lower()
    if risk not in _RISKS:
        raise OutputContractError("risk must be low, medium, or high")
    next_skill = _validate_next_skill(payload.get("next_skill"))
    selection_needed = payload.get("user_selection_needed")
    if not isinstance(selection_needed, bool):
        raise OutputContractError("user_selection_needed must be boolean")

    reason_code = payload.get("reason_code")
    if reason_code is not None and (not isinstance(reason_code, str) or not reason_code.strip()):
        raise OutputContractError("reason_code must be a non-empty string or null")
    if expected_outcome in {"blocked", "unresolved"} and not reason_code:
        raise OutputContractError("reason_code is required for blocked or unresolved outcomes")

    expected_next_skill, expected_selection = _routing_for(stage, verdict, reason_code)
    if next_skill != expected_next_skill:
        raise OutputContractError("next_skill conflicts with stage routing policy")
    if expected_selection is not None and selection_needed is not expected_selection:
        raise OutputContractError("user_selection_needed conflicts with stage routing policy")

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "outcome": expected_outcome,
        "risk": risk,
        "next_skill": next_skill,
        "user_selection_needed": selection_needed,
        "reason_code": reason_code,
        "verdict": verdict,
    }


_SELECTION_UNSET = object()


def render_decision(*, stage: str, verdict: str, risk: str,
                    reason_code: str | None = None,
                    user_selection_needed=_SELECTION_UNSET) -> str:
    """Derive policy-owned fields for model declarations; grant no authority."""
    stage, verdict = str(stage).strip().lower(), str(verdict).strip().upper()
    outcome_for(stage, verdict)
    if stage == 'task':
        raise OutputContractError('task requires the completion gate')
    if reason_code is not None and (not isinstance(reason_code,str) or not reason_code.strip()):
        raise OutputContractError('reason_code must be a non-empty string or null')
    supplied = user_selection_needed is not _SELECTION_UNSET
    if supplied and not isinstance(user_selection_needed,bool):
        raise OutputContractError('user_selection_needed must be boolean')
    next_skill, expected = _routing_for(stage,verdict,reason_code)
    if expected is None:
        if not supplied:
            raise OutputContractError('user_selection_needed is required for this decision')
        selection = user_selection_needed
    else:
        if supplied and user_selection_needed is not expected:
            raise OutputContractError('user_selection_needed conflicts with stage routing policy')
        selection = expected
    return render_envelope(stage=stage,verdict=verdict,risk=risk,reason_code=reason_code,
                           next_skill=next_skill,user_selection_needed=selection)


def render_envelope(
    *,
    stage: str,
    verdict: str,
    risk: str,
    next_skill: str | None,
    user_selection_needed: bool,
    reason_code: str | None = None,
) -> str:
    normalized_verdict = str(verdict).strip().upper()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "stage": str(stage).strip().lower(),
        "outcome": outcome_for(stage, normalized_verdict),
        "risk": str(risk).strip().lower(),
        "next_skill": next_skill,
        "user_selection_needed": user_selection_needed,
        "reason_code": reason_code,
    }
    validated = _validate_payload(payload, normalized_verdict)
    serialized = json.dumps(
        {key: validated[key] for key in _REQUIRED_FIELDS},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    serialized = _html_comment_safe_payload(serialized)
    return (
        f"{_HIDDEN_ENVELOPE_PREFIX} {serialized} {_HIDDEN_ENVELOPE_SUFFIX}\n"
        f"VERDICT: {normalized_verdict}"
    )


def parse_envelope(output: str) -> dict[str, Any]:
    output_lines = str(output).splitlines()
    marker_lines = [line for line in output_lines if _ENVELOPE_PREFIX in line]
    if len(marker_lines) != 1:
        raise OutputContractError("output must contain exactly one OMC_OUTPUT envelope")
    nonempty_lines = [line for line in output_lines if line.strip()]
    if (nonempty_lines and re.fullmatch(r"VERDICT\s*:.*", nonempty_lines[-1], re.IGNORECASE)
            and _VERDICT_RE.fullmatch(nonempty_lines[-1]) is None):
        allowed = {value for values in _STAGE_VERDICTS.values() for value in values}
        if len(nonempty_lines) >= 2:
            try:
                candidate = json.loads(_envelope_payload_from_line(nonempty_lines[-2]) or '{}')
                if isinstance(candidate, dict):
                    stage = str(candidate.get('stage') or '').strip().lower()
                    allowed = _STAGE_VERDICTS.get(stage, allowed)
            except ValueError:
                pass
        raise ContractChoiceError('unsupported VERDICT value', code='verdict_invalid', allowed=allowed)
    if (
        len(nonempty_lines) < 2
        or _envelope_payload_from_line(nonempty_lines[-2]) is None
        or _VERDICT_RE.fullmatch(nonempty_lines[-1]) is None
    ):
        raise OutputContractError(
            "the last two non-empty lines must be OMC_OUTPUT and VERDICT"
        )
    envelope_lines = [
        payload
        for line in output_lines
        if (payload := _envelope_payload_from_line(line)) is not None
    ]
    if len(envelope_lines) != 1:
        raise OutputContractError("output must contain exactly one OMC_OUTPUT envelope")

    verdicts = [match.group(1).upper() for match in _VERDICT_RE.finditer(str(output))]
    if len(verdicts) != 1:
        raise OutputContractError("output must contain exactly one VERDICT line")
    try:
        payload = json.loads(envelope_lines[0])
    except json.JSONDecodeError as exc:
        raise OutputContractError("OMC_OUTPUT must contain valid compact JSON") from exc
    if not isinstance(payload, dict):
        raise OutputContractError("OMC_OUTPUT payload must be an object")
    return _validate_payload(payload, verdicts[0])


def contract_source(output: str, *, stage: str) -> str:
    """Classify valid raw envelopes separately from compatible legacy output."""
    normalized_stage = str(stage).strip().lower()
    if _ENVELOPE_PREFIX in str(output):
        parsed = parse_envelope(output)
        if parsed["stage"] != normalized_stage:
            raise OutputContractError("envelope stage conflicts with pipeline stage")
        return "raw_compliant"
    normalize_output(output, stage=normalized_stage)
    return "legacy_normalized"


def normalize_output(output: str, *, stage: str) -> str:
    """Append one envelope to legacy verdict-only output; never repair explicit envelopes."""
    normalized = str(output).rstrip()
    if _ENVELOPE_PREFIX in normalized:
        parsed = parse_envelope(normalized)
        if parsed["stage"] != str(stage).strip().lower():
            raise OutputContractError("envelope stage conflicts with pipeline stage")
        return normalized

    verdicts = [match.group(1).upper() for match in _VERDICT_RE.finditer(normalized)]
    if len(verdicts) != 1:
        raise OutputContractError("legacy output must contain exactly one VERDICT line")
    verdict = verdicts[0]
    defaults = _LEGACY_DEFAULTS.get((str(stage).strip().lower(), verdict))
    if defaults is None:
        outcome_for(stage, verdict)
        raise OutputContractError("legacy output has no deterministic normalization policy")
    risk, next_skill, selection_needed, reason_code = defaults
    body = _VERDICT_RE.sub("", normalized).rstrip()
    envelope = render_envelope(
        stage=stage,
        verdict=verdict,
        risk=risk,
        next_skill=next_skill,
        user_selection_needed=selection_needed,
        reason_code=reason_code,
    )
    return f"{body}\n{envelope}" if body else envelope


def prompt_contract(stage: str) -> str:
    """Return the compact provider instruction for a pilot stage."""
    normalized_stage = str(stage).strip().lower()
    allowed = _STAGE_VERDICTS.get(normalized_stage)
    if allowed is None:
        raise OutputContractError(f"unsupported stage: {normalized_stage}")
    verdicts = "|".join(allowed)
    return (
        "끝 두 줄: "
        f"`<!-- OMC_OUTPUT: {{JSON}} -->`, `VERDICT: {verdicts}`. "
        f"JSON은 schema_version={SCHEMA_VERSION}, stage={normalized_stage}, outcome, risk, "
        "next_skill(canonical omc-* 또는 null), user_selection_needed(boolean), reason_code를 포함하세요. "
        f"라우팅(outcome,next_skill,user_selection_needed): {_PROMPT_ROUTING[normalized_stage]}; "
        "unresolved/blocked는 reason_code가 필수입니다."
        + (" plan HOLD+review_required만 unresolved,omc-critique,true; "
           "그 외 HOLD/REVISE는 기존 재계획 경로를 유지합니다."
           if normalized_stage == 'plan' else '')
    )

# Detached delivery is opt-in. The v1 machine payload and legacy parser above
# remain unchanged; a record is local custody, not evidence authentication.
import argparse
import hashlib
import os
from pathlib import Path
import sys
import time
import uuid

RECORD_SCHEMA = 'omc-output-record/v1'
_GATE_AUTHORITY = object()
_VERDICT_KO = {'PROCEED':'진행 가능','HOLD':'보류','REVISE':'수정 필요',
               'PROCEED WITH CAUTION':'주의사항 확인 후 진행 가능',
               'BLOCK':'보류','APPROVE':'승인','APPROVE WITH NOTES':'승인·보완 제안 있음'}


def capabilities():
    return {'schema_version':RECORD_SCHEMA,'output_modes':['legacy_inline','detached_v1'], 'render_decision':True, 'stage_source':True}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')


def _local_file(root, path, *, must_exist=True):
    root=Path(root).resolve(); path=Path(path)
    if not path.is_absolute():path=root/path
    try: rel=path.relative_to(root)
    except ValueError:raise OutputContractError('record_path_outside_work')
    if '..' in rel.parts:raise OutputContractError('record_path_escape')
    current=root
    for part in rel.parts:
        current=current/part
        if current.is_symlink():raise OutputContractError('record_symlink_not_allowed')
    if must_exist and not path.is_file():raise OutputContractError('record_file_missing')
    return path


def _read_pinned(root, path, pin):
    file=_local_file(root,path);raw=file.read_bytes()
    if not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin) or _sha(raw)!=pin:
        raise OutputContractError('record_hash_mismatch')
    return raw


def _identity_object(root, path):
    value = json.loads(_local_file(root, path).read_bytes())
    if not isinstance(value, dict):
        raise OutputContractError('record_identity_object_required')
    return value


def _work_identity(root, sid):
    root=Path(root).resolve();session=root/'.omc/state/sessions'/sid/'session.json'
    session_work=_identity_object(root,session).get('work_id') if session.exists() else None
    pending=root/'.omc/state/pending-completion.json'
    pending_work=_identity_object(root,pending).get('work_id') if pending.exists() else None
    if session_work and pending_work and session_work!=pending_work:raise OutputContractError('record_work_lineage_mismatch')
    return pending_work or session_work or sid


def _context(root, value, *, live_deadline=True):
    root=Path(root).resolve()
    if not isinstance(value,dict) or value.get('output_mode')!='detached_v1':
        raise OutputContractError('detached_mode_required')
    from omc_edit_resume import frozen, load
    if frozen(root):
        resume = load(root)
        if value.get('edit_resume') != {'grant_id': resume['grant_id'], 'generation': resume['generation']}:
            raise OutputContractError('resume_output_generation_mismatch')
    for key in ('session_id','work_id','report_id'):
        if not isinstance(value.get(key),str) or not re.fullmatch('[A-Za-z0-9_-]{1,120}',value[key]):
            raise OutputContractError('invalid_record_identity')
    if value.get('stage') not in _STAGE_VERDICTS:raise OutputContractError('invalid_record_stage')
    deadline=value.get('deadline_epoch')
    if isinstance(deadline,bool) or not isinstance(deadline,(int,float)) or not 0 < deadline < float('inf'):
        raise OutputContractError('record_deadline_required')
    if live_deadline and time.time()>=deadline:raise OutputContractError('record_deadline_expired')
    latest=json.loads(_local_file(root,'.omc/state/latest.json').read_bytes())
    if latest.get('latest_confirmed_session_id')!=value['session_id']:
        raise OutputContractError('record_session_mismatch')
    expected_work=_work_identity(root,value['session_id'])
    if value['work_id']!=expected_work:raise OutputContractError('record_work_mismatch')
    call=value.get('native_call_reference')
    if not isinstance(call,dict) or not isinstance(call.get('invocation_id'),str) or not call['invocation_id']:
        raise OutputContractError('record_call_reference_required')
    refs=value.get('references')
    if not isinstance(refs,list):raise OutputContractError('record_references_required')
    for ref in refs:
        if not isinstance(ref,dict) or not isinstance(ref.get('purpose'),str):raise OutputContractError('invalid_record_reference')
        _read_pinned(root,ref['path'],ref['sha256'])
    return value


def load_output_context(root, path, pin, *, live_deadline=True):
    return _context(root,json.loads(_read_pinned(root,path,pin)),live_deadline=live_deadline)


def record_path(root, context):
    from omc_edit_resume import frozen, load
    if frozen(root):
        return Path(root).resolve()/load(root)['generation']/'output-records'/context['report_id']/'manifest.json'
    return Path(root).resolve()/'.omc/state/output-records'/context['work_id']/context['report_id']/'manifest.json'


def human_output(raw):
    payload=parse_envelope(raw)
    # Only remove the validated terminal envelope; keep quoted/code examples.
    lines=str(raw).splitlines()
    while lines and not lines[-1].strip():lines.pop()
    body=lines[:-2];fence=None;has_judgment=False
    known_values=set(_VERDICT_KO)|set(_VERDICT_KO.values())|{'차단','보완 필요'}
    expected_values={payload['verdict'],_VERDICT_KO[payload['verdict']]}
    if payload['verdict']=='BLOCK':expected_values.add('차단')
    if payload['verdict']=='REVISE':expected_values.add('보완 필요')
    for i,line in enumerate(body):
        if line.startswith(('    ','\t')):continue
        stripped=line.lstrip()
        marker=re.match(r'^(`{3,}|~{3,})(.*)$',stripped)
        if marker:
            run,tail=marker.groups()
            if fence is None:fence=(run[0],len(run))
            elif run[0]==fence[0] and len(run)>=fence[1] and not tail.strip():fence=None
            continue
        if fence is not None or stripped.startswith('>'):continue
        if stripped.startswith('`') and stripped.endswith('`'):continue
        visible=re.sub(r'^#{1,6}\s+','',stripped)
        visible=re.sub(r'^(?:[-*+]|[0-9]+[.)])\s+','',visible).replace('**','').replace('__','').strip('*_` ')
        match=re.fullmatch(r'(판정|결정)\s*:\s*(.*?)',visible)
        if not match:continue
        label,value=match.groups();value=value.strip('*_` ')
        if value not in known_values:
            if label=='판정':raise OutputContractError('unrecognized_body_judgment')
            continue  # free-form plan decisions still get a canonical judgment
        if value not in expected_values:raise OutputContractError('body_verdict_conflict')
        body[i]=label+': '+_VERDICT_KO[payload['verdict']]
        has_judgment=True
    if fence is not None:raise OutputContractError('unclosed_body_code_fence')
    if not has_judgment:body.append('판정: '+_VERDICT_KO[payload['verdict']])
    return '\n'.join(body).rstrip()+'\n'


def _write_record_file(path, data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _current_binding(root):
    from omc_pipeline_guard import _code_snapshot_digest
    root=Path(root).resolve()
    binding={'code_snapshot_sha256':_code_snapshot_digest(root)}
    for name in ('latest.json','pending-completion.json'):
        f=root/'.omc/state'/name
        binding[name]=_sha(_local_file(root,f).read_bytes()) if f.exists() else None
    return binding


def _gate_binding(root, verification):
    root=Path(root).resolve();files={}
    def pin(name):
        try:
            path=_local_file(root,name,must_exist=False)
            files[str(path)]={'sha256':_sha(path.read_bytes())} if path.is_file() else {'status':'missing'}
        except (OutputContractError,OSError):
            files[str(name)]={'status':'rejected_path'}
    verification=Path(verification)
    if not verification.is_absolute():verification=root/verification
    pin(verification)
    from omc_pipeline_guard import _state_path
    pipeline=_state_path(root);pin(pipeline)
    from omc_edit_resume import ENV
    if os.environ.get(ENV):
        ref=json.loads(os.environ[ENV]);pin(ref['path'])
    try:state=json.loads(pipeline.read_bytes()) if pipeline.is_file() and not pipeline.is_symlink() else {}
    except (OSError,ValueError):state={}
    latest=json.loads(_local_file(root,'.omc/state/latest.json').read_bytes())
    sid=state.get('owner_session_id') or state.get('session_id') or latest['latest_confirmed_session_id']
    if not isinstance(sid,str) or not re.fullmatch('[A-Za-z0-9_-]{1,120}',sid):raise OutputContractError('invalid_gate_owner')
    folder=root/'.omc/verification-bases'/sid
    if folder.exists():
        _local_file(root,folder/'placeholder',must_exist=False)
        for file in sorted(folder.glob('*.json')):pin(file)
    session=root/'.omc/state/sessions'/latest['latest_confirmed_session_id']/'session.json'
    pin(session)
    try:
        record=json.loads(_local_file(root,verification).read_bytes())
        baseline=state.get('maintenance_baseline_ref')
        if baseline:pin(baseline['path'])
        operation_ref=record.get('maintenance_operation_ref')
        if operation_ref:
            pin(operation_ref['path'])
            operation=json.loads(_local_file(root,operation_ref['path']).read_bytes())
            pin(operation['baseline_ref']['path'])
            pin(operation['native_prefix_ref']['path'])
        for check in record.get('checks',{}).values():
            for ref in check.get('evidence_files',[]):pin(ref['path'])
    except (OSError,ValueError,TypeError,KeyError,AttributeError):pass
    return {'verification_file':str(verification),'files':files}


def preflight_record(root, context_path, context_pin, raw):
    """Validate without reserving a report ID or writing any record."""
    context = load_output_context(root, context_path, context_pin)
    if not isinstance(raw, str):
        raise OutputContractError('raw_text_required')
    parsed = parse_envelope(raw)
    if parsed['stage'] != context['stage']:
        raise OutputContractError('record_stage_mismatch')
    return context, parsed, human_output(raw)


def _publish(root, context_path, context_pin, raw, *, source_kind, authority=None, gate_verification=None):
    root=Path(root).resolve()
    context, parsed, display = preflight_record(root, context_path, context_pin, raw)
    if source_kind not in {'model_declared','legacy_normalized','completion_gate'}:
        raise OutputContractError('record_source_kind_invalid')
    if source_kind=='completion_gate' and authority is not _GATE_AUTHORITY:
        raise OutputContractError('gate_authority_not_from_publisher')
    if source_kind=='completion_gate' and gate_verification is None:raise OutputContractError('gate_verification_required')
    manifest=record_path(root,context)
    _local_file(root,manifest,must_exist=False)
    manifest.parent.parent.mkdir(parents=True,exist_ok=True)
    manifest.parent.mkdir(mode=0o700)  # exclusive report reservation; never overwrite
    source=raw.encode('utf-8');shown=display.encode('utf-8')
    _write_record_file(manifest.parent/'source.txt',source)
    _write_record_file(manifest.parent/'display.txt',shown)
    value={'schema_version':RECORD_SCHEMA,'context':context,'context_sha256':context_pin,
           'source_kind':source_kind,'machine_payload':{k:parsed[k] for k in _REQUIRED_FIELDS},
           'verdict':parsed['verdict'],'raw_source_sha256':_sha(source),'display_utf8_sha256':_sha(shown),
           'files':{'source.txt':_sha(source),'display.txt':_sha(shown)},
           'binding':_current_binding(root),'publication':'published','claim_boundary':'LOCAL_CUSTODY_NOT_AUTHENTICATION'}
    if source_kind=='completion_gate':value['gate_binding']=_gate_binding(root,gate_verification)
    for name,pin in value['files'].items():_read_pinned(root,manifest.parent/name,pin)
    _context(root,context)  # references and deadline must still hold before publication
    data=_json_bytes(value);temp=manifest.parent/'manifest.tmp';_write_record_file(temp,data)
    _context(root,context)
    _read_pinned(root,temp,_sha(data))
    for name,pin in value['files'].items():_read_pinned(root,manifest.parent/name,pin)
    os.link(temp,manifest)  # atomic publish, fails if the final name already exists
    return {'path':str(manifest),'sha256':_sha(data)}


def publish_record(root, context_path, context_pin, raw, *, source_kind='model_declared'):
    return _publish(root,context_path,context_pin,raw,source_kind=source_kind)


def read_record(root, path, pin, expected_context, *, display=None):
    # Historical consumption checks identity/references, not today's wall clock.
    context=_context(root,expected_context,live_deadline=False)
    file=_local_file(root,path)
    if file!=record_path(root,context):raise OutputContractError('record_location_mismatch')
    value=json.loads(_read_pinned(root,file,pin))
    if not isinstance(value,dict):raise OutputContractError('record_manifest_object_required')
    if value.get('schema_version')!=RECORD_SCHEMA or value.get('context')!=context or value.get('publication')!='published':
        raise OutputContractError('record_context_mismatch')
    if value.get('source_kind') not in {'completion_gate','model_declared','legacy_normalized'}:
        raise OutputContractError('record_source_kind_invalid')
    if value.get('binding')!=_current_binding(root):raise OutputContractError('record_live_binding_mismatch')
    raw=_read_pinned(root,file.parent/'source.txt',value['raw_source_sha256']).decode('utf-8')
    shown=_read_pinned(root,file.parent/'display.txt',value['display_utf8_sha256']).decode('utf-8')
    parsed=parse_envelope(raw)
    if ({k:parsed[k] for k in _REQUIRED_FIELDS}!=value['machine_payload'] or parsed['verdict']!=value['verdict']
            or parsed['stage']!=context['stage'] or human_output(raw)!=shown):
        raise OutputContractError('record_payload_mismatch')
    if value['source_kind']=='completion_gate':
        binding=value.get('gate_binding')
        if not isinstance(binding,dict) or binding!=_gate_binding(root,binding.get('verification_file','')):
            raise OutputContractError('record_gate_evidence_mismatch')
    if display is not None and display!=shown:raise OutputContractError('record_display_mismatch')
    return {**value,'raw_source':raw,'display':shown}


def _write_display(display):
    count=sys.stdout.write(display)
    if count!=len(display):raise OSError('short output write')
    sys.stdout.flush()


def _failure_code(exc, phase):
    """Never put arbitrary exception text (paths, secrets) in diagnostics."""
    messages = {
        'next_skill conflicts with stage routing policy': 'next_skill_conflict',
        'user_selection_needed conflicts with stage routing policy': 'selection_conflict',
        'outcome conflicts with stage verdict mapping': 'outcome_conflict',
        'unrecognized_body_judgment': 'body_judgment_invalid',
        'body_verdict_conflict': 'body_verdict_conflict',
    }
    if isinstance(exc, ContractChoiceError) and exc.code in {'reason_code_invalid', 'verdict_invalid', 'stage_invalid'}:
        return exc.code
    text = str(exc)
    if text in messages:
        return messages[text]
    if text in {'record_deadline_expired', 'record_hash_mismatch',
                'record_session_mismatch', 'record_stage_mismatch',
                'record_work_mismatch', 'record_identity_object_required',
                'detached_mode_required', 'record_file_missing',
                'record_path_outside_work', 'record_path_escape',
                'record_symlink_not_allowed'}:
        return text
    if isinstance(exc, json.JSONDecodeError):
        return 'invalid_json'
    if isinstance(exc, BrokenPipeError):
        return 'display_write_failed'
    if isinstance(exc, OSError):
        return 'source_unreadable' if phase == 'source_read' else 'record_io_failed'
    if 'verdict' in text.lower() and ('multiple' in text or 'one' in text):
        return 'verdict_count_invalid'
    return 'invalid_output_contract'


def _assert_failure_directory(root, directory):
    """Reopen the managed path without following links and compare identity.

    A directory descriptor remains writable after its directory is moved. It
    does not, by itself, prove the file still lives under the managed root.
    These observations detect replacement; they are not a namespace lock.
    """
    root = Path(root).absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open(root.anchor, flags)
    try:
        for part in (*root.parts[1:], '.omc', 'state', 'output-failures'):
            child = os.open(part, flags, dir_fd=current)
            os.close(current); current = child
        observed = os.fstat(current)
        held = os.fstat(directory)
        if (observed.st_dev, observed.st_ino) != (held.st_dev, held.st_ino):
            raise OutputContractError('record_diagnostic_directory_changed')
    finally:
        os.close(current)


def _write_failure_diagnostic(root, context, context_pin, raw, phase, code, *, allow_expired=False):
    """Use directory descriptors: input identities never select a write path."""
    root = Path(root).absolute()
    if '..' in root.parts:
        raise OutputContractError('record_path_escape')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root.anchor, flags)
    try:
        for part in root.parts[1:]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory); directory = child
        for part in ('.omc', 'state', 'output-failures'):
            try: os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError: pass
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory); directory = child
        # A diagnostic is not a publication or completion receipt. A failed or
        # unverified context never gets here; its only evidence is native stderr.
        _context(root, context, live_deadline=not allow_expired)
        _assert_failure_directory(root, directory)
        data = _json_bytes({
            'schema_version': 'omc-output-failure/v1', 'kind': 'failure_diagnostic',
            'phase': phase, 'error_code': code, 'context_status': 'verified',
            'report_id': context['report_id'], 'stage': context['stage'],
            'context_sha256': context_pin,
            'source_sha256': _sha(raw.encode('utf-8')) if isinstance(raw, str) else None,
            'source_status': 'observed' if isinstance(raw, str) else 'unavailable',
            **({'deadline_checked': False, 'draft_status': 'validated_for_failure_explanation'}
               if allow_expired else {}),
        })
        name = uuid.uuid4().hex + '.json'
        _assert_failure_directory(root, directory)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            _assert_failure_directory(root, directory)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        _assert_failure_directory(root, directory)
        # If storage crossed the deadline, do not claim diagnostic delivery.
        _context(root, context, live_deadline=not allow_expired)
        _assert_failure_directory(root, directory)
    finally:
        os.close(directory)


def _choice_guidance(exc):
    if not isinstance(exc, ContractChoiceError):
        return ''
    if exc.code == 'stage_invalid':
        label = '지원하지 않는 단계입니다.'
        choices = set(_STAGE_VERDICTS)
    elif exc.code == 'reason_code_invalid':
        label = '허용되지 않은 reason_code입니다.'
        choices = {reason for routes in _REASON_ROUTING_POLICY.values() for reason in routes}
    elif exc.code == 'verdict_invalid':
        label = '지원하지 않는 판정값입니다.'
        choices = {verdict for values in _STAGE_VERDICTS.values() for verdict in values}
    else:
        return ''
    # Only built-in schema values may reach the user-facing hint.
    allowed = [value for value in exc.allowed if value in choices]
    return label + (' 허용값: ' + ', '.join(allowed) + '.' if allowed else '')


def _failure_draft(root, context_path, context_pin, raw, draft, phase):
    """Explain an unconfirmed delivery; never create a verdict or authority."""
    context = load_output_context(root, context_path, context_pin, live_deadline=False)
    if context['stage'] not in {'plan', 'critique-plan'} or context.get('draft_binding') != _current_binding(root):
        raise OutputContractError('draft_identity_or_binding_mismatch')
    if parse_envelope(raw)['stage'] != context['stage']:
        raise OutputContractError('draft_stage_mismatch')
    source = _local_file(root, draft['source_file'])
    if _read_pinned(root, source, draft['source_pin']).decode('utf-8') != raw:
        raise OutputContractError('draft_source_mismatch')
    summary = _local_file(root, draft['summary_file'])
    if not any(_local_file(root, ref['path']) == summary and ref['sha256'] == draft['summary_pin']
               for ref in context['references']):
        raise OutputContractError('draft_summary_reference_missing')
    text = _read_pinned(root, summary, draft['summary_pin']).decode('utf-8')
    state = '확인 불가'
    if phase == 'preflight':
        state = '이번 호출은 발행 전 검증에서 중단됨; 기존 기록 상태는 확인 불가'
    manifest = record_path(root, context)
    if manifest.exists():
        try:
            read_record(root, manifest, _sha(_local_file(root, manifest).read_bytes()), context)
            state = '발행됨·전달 미확인'
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            state = '확인 불가'
    lines = ['공식 판정 전달 미확인', '기록 상태: ' + state]
    if text.strip() and len(text) <= 1200 and len(text.splitlines()) <= 12:
        # Indent every line so draft text cannot impersonate the status header.
        lines += ['작성 초안·공식 승인 아님', *['  ' + line for line in text.splitlines()], '초안 끝·공식 판정 전달 미확인']
    else:
        lines.append('요약이 비어 있거나 12줄·1,200자를 초과해 본문을 표시하지 않습니다.')
    lines += ['원문 위치: ' + str(source), '자동 후속 진행을 보류합니다.']
    return '\n'.join(lines), context


def _emit_failure(root, context_path, context_pin, raw, phase, exc, *, draft=None):
    code = _failure_code(exc, phase)
    explanation = None
    verified_context = None
    if draft is not None and isinstance(raw, str):
        try:
            explanation, verified_context = _failure_draft(root, context_path, context_pin, raw, draft, phase)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            pass
    saved = False
    try:
        context = verified_context or load_output_context(root, context_path, context_pin)
        if verified_context is not None:
            _write_failure_diagnostic(root, context, context_pin, raw, phase, code, allow_expired=True)
        else:
            _write_failure_diagnostic(root, context, context_pin, raw, phase, code)
        saved = True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        # Invalid JSON/hash/session/deadline, unsafe target or a storage error:
        # never invent identity, renew the deadline or replace a sealed record.
        pass
    try:
        if explanation is not None:
            # Diagnostics can perform I/O: recheck identity and bytes before
            # exposing a draft after that boundary as well.
            try:
                explanation, _ = _failure_draft(root, context_path, context_pin, raw, draft, phase)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                explanation = None
        guidance = _choice_guidance(exc)
        message = ('기록 발행 전 형식 검증에서 거부됐습니다. ' + guidance + ' 원문을 보존하고 후속 진행을 보류합니다. '
                   if guidance else '기록 전달을 확인하지 못했습니다. 원문을 보존하고 후속 진행을 보류합니다. ')
        print((explanation or message) + f'\n[오류 코드: {code}; 별도 오류 기록: {"보존됨" if saved else "확인 불가"}]',
              file=sys.stderr)
    except OSError:
        pass
    return 2


def emit_record(root, context_path, context_pin, raw, *, source_kind='model_declared', _authority=None, _gate_verification=None, draft=None):
    phase = 'publication_or_delivery'
    try:
        if draft is not None and source_kind == 'model_declared':
            phase = 'preflight'
            preflight_record(root, context_path, context_pin, raw)
            phase = 'publication'
        ref=_publish(root,context_path,context_pin,raw,source_kind=source_kind,authority=_authority,gate_verification=_gate_verification)
        if draft is not None: phase = 'readback'
        context=load_output_context(root,context_path,context_pin)
        doc=read_record(root,ref['path'],ref['sha256'],context)
        _context(root,context)
        if draft is not None: phase = 'display'
        _write_display(doc['display'])
        # A valid model declaration is a successful publication even for REVISE.
        # Only the completion gate owns the legacy business 0/1 exit contract.
        return 1 if source_kind=='completion_gate' and doc['machine_payload']['outcome']=='blocked' else 0
    except (OSError,ValueError,TypeError,KeyError,UnicodeError) as exc:
        # Publication may already have succeeded. Never rewrite its verdict/status.
        return _emit_failure(root, context_path, context_pin, raw, phase, exc,
                             draft=draft if source_kind == 'model_declared' else None)


def _source_directory(path, *, create=False):
    """Open an absolute directory chain without following any links."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise OutputContractError('record_path_escape')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    held = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                try: os.mkdir(part, mode=0o700, dir_fd=held)
                except FileExistsError: pass
            child = os.open(part, flags, dir_fd=held)
            os.close(held); held = child
        return held
    except BaseException:
        os.close(held)
        raise


def _source_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def stage_source(root, context_path, context_pin, source_file, source_pin):
    """Stage explicitly pinned report bytes; this grants no approval authority."""
    import stat
    import uuid
    root = Path(root).resolve()
    context = load_output_context(root, context_path, context_pin)
    if context.get('context_kind') is not None or context['stage'] not in {'review','ship'}:
        raise OutputContractError('source_stage_forbidden')
    if not isinstance(source_pin,str) or not re.fullmatch('[0-9a-f]{64}',source_pin):
        raise OutputContractError('record_hash_mismatch')
    manifest = record_path(root,context)
    _local_file(root,manifest,must_exist=False)
    if manifest.parent.exists():
        raise OutputContractError('source_report_already_reserved')
    source = Path(source_file)
    if not source.is_absolute():
        raise OutputContractError('source_absolute_path_required')
    parent = _source_directory(source.parent)
    try:
        fd = os.open(source.name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):raise OutputContractError('source_regular_file_required')
            if before.st_size > 1024*1024:raise OutputContractError('source_too_large')
            chunks=[];total=0
            while True:
                block=os.read(fd,min(65536,1024*1024+1-total))
                if not block:break
                chunks.append(block);total+=len(block)
                if total>1024*1024:raise OutputContractError('source_too_large')
            raw=b''.join(chunks)
            after=os.fstat(fd)
            observed=os.stat(source.name,dir_fd=parent,follow_symlinks=False)
            check=_source_directory(source.parent)
            try:
                if _source_identity(os.fstat(parent))[:2] != _source_identity(os.fstat(check))[:2]:
                    raise OutputContractError('source_directory_changed')
            finally:os.close(check)
            if _source_identity(before)!=_source_identity(after) or _source_identity(before)!=_source_identity(observed):
                raise OutputContractError('source_changed')
        finally:os.close(fd)
    finally:os.close(parent)
    if _sha(raw)!=source_pin:raise OutputContractError('record_hash_mismatch')
    preflight_record(root,context_path,context_pin,raw.decode('utf-8'))
    folder=manifest.parent.parent/'.source-staging'
    _local_file(root,folder,must_exist=False)
    directory=_source_directory(folder,create=True)
    temporary='.'+uuid.uuid4().hex+'.tmp'; destination=context['report_id']+'.txt'
    created=False
    try:
        fd=os.open(temporary,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600,dir_fd=directory)
        created=True
        try:
            view=memoryview(raw)
            while view:
                count=os.write(fd,view)
                if count<=0:raise OSError('source short write')
                view=view[count:]
            os.fsync(fd)
        finally:os.close(fd)
        load_output_context(root,context_path,context_pin)
        _local_file(root,folder,must_exist=False)
        check=_source_directory(folder)
        try:
            if _source_identity(os.fstat(directory))[:2]!=_source_identity(os.fstat(check))[:2]:
                raise OutputContractError('source_directory_changed')
        finally:os.close(check)
        if manifest.parent.exists():raise OutputContractError('source_report_already_reserved')
        os.link(temporary,destination,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
        os.fsync(directory)
        load_output_context(root,context_path,context_pin)
        check=_source_directory(folder)
        try:
            if _source_identity(os.fstat(directory))[:2]!=_source_identity(os.fstat(check))[:2]:
                raise OutputContractError('source_directory_changed')
        finally:os.close(check)
        return {'source_file':str(folder/destination),'source_sha256':source_pin}
    finally:
        if created:
            try:os.unlink(temporary,dir_fd=directory)
            except OSError:pass  # no success claim from a leftover private temporary
        os.close(directory)


def output_cli(argv=None):
    parser=argparse.ArgumentParser(description='OMC 별도 기록과 한글 판정')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('capabilities')
    decision=sub.add_parser('render-decision',help='규칙에서 파생 필드를 계산하는 순수 판정 생성기')
    decision.add_argument('--stage',choices=[s for s in _STAGE_VERDICTS if s!='task'],required=True)
    decision.add_argument('--verdict',required=True)
    decision.add_argument('--risk',choices=sorted(_RISKS),required=True)
    decision.add_argument('--reason-code')
    decision.add_argument('--user-selection-needed',choices=['true','false'],default=argparse.SUPPRESS)
    staging=sub.add_parser('stage-source',help='외부 원문을 보존하고 발행용 사본 저장')
    staging.add_argument('--target',type=Path,required=True)
    staging.add_argument('--output-context',type=Path,required=True)
    staging.add_argument('--output-context-sha256',required=True)
    staging.add_argument('--source-file',type=Path,required=True)
    staging.add_argument('--source-sha256',required=True)
    prep=sub.add_parser('prepare')
    prep.add_argument('--target',type=Path,required=True)
    prep.add_argument('--stage',choices=list(_STAGE_VERDICTS),required=True)
    prep.add_argument('--report-id',required=True)
    prep.add_argument('--budget-sec',type=float,required=True)
    prep.add_argument('--reference',action='append',default=[])
    for command in ('validate', 'publish'):
        entry = sub.add_parser(command, help='읽기 전용 발행 사전 검증' if command == 'validate' else '검증 후 기록 발행')
        entry.add_argument('--target',type=Path,required=True)
        entry.add_argument('--output-context',type=Path,required=True)
        entry.add_argument('--output-context-sha256',required=True)
        entry.add_argument('--source-file',type=Path,required=True)
        entry.add_argument('--source-sha256',required=True)
        entry.add_argument('--draft-summary-file',type=Path)
        entry.add_argument('--draft-summary-sha256')
    args=parser.parse_args(argv)
    if args.command=='capabilities':
        print(json.dumps(capabilities()));return 0
    if args.command=='stage-source':
        try:
            result=stage_source(args.target,args.output_context,args.output_context_sha256,args.source_file,args.source_sha256)
            print(json.dumps(result,ensure_ascii=False));return 0
        except (OSError,ValueError,KeyError,TypeError) as exc:
            print('발행용 사본을 확인하지 못했습니다. 원문을 보존합니다. [오류 코드: '+str(exc)+']',file=sys.stderr);return 2
    if args.command=='render-decision':
        try:
            options=({'user_selection_needed':args.user_selection_needed=='true'}
                     if hasattr(args,'user_selection_needed') else {})
            print(render_decision(stage=args.stage,verdict=args.verdict,risk=args.risk,
                                  reason_code=args.reason_code,**options))
            return 0
        except OutputContractError as exc:
            print('판정 생성 입력을 확인하세요: '+str(exc),file=sys.stderr)
            return 2
    if args.command=='prepare':
        try:
            root=args.target.resolve()
            if not 0 < args.budget_sec <= 480:raise OutputContractError('bounded_budget_required')
            latest=json.loads(_local_file(root,'.omc/state/latest.json').read_bytes())
            sid=latest['latest_confirmed_session_id'];pending=root/'.omc/state/pending-completion.json'
            work=_work_identity(root,sid)
            created=time.time()
            context={'output_mode':'detached_v1','stage':args.stage,'session_id':sid,'work_id':work,
                     'report_id':args.report_id,'created_epoch':created,'budget_sec':args.budget_sec,'deadline_epoch':created+args.budget_sec,
                     'native_call_reference':{'invocation_id':args.report_id},
                     'references':[{'path':str(_local_file(root,p)),'sha256':_sha(_local_file(root,p).read_bytes()),'purpose':'explicit_evidence'} for p in args.reference]}
            if args.reference:
                context['draft_binding'] = _current_binding(root)
            from omc_edit_resume import frozen, load
            if frozen(root):
                resume=load(root);context['edit_resume']={'grant_id':resume['grant_id'],'generation':resume['generation']}
            _context(root,context)
            file=record_path(root,context).parent.parent/(context['report_id']+'.context.json')
            _local_file(root,file,must_exist=False);file.parent.mkdir(parents=True,exist_ok=True)
            data=_json_bytes(context);_write_record_file(file,data)
            print(json.dumps({'output_context':str(file),'output_context_sha256':_sha(data),
                              'record_path':str(record_path(root,context))},ensure_ascii=False));return 0
        except (OSError,ValueError,KeyError,TypeError):
            print('출력 기록의 새 실행 문맥을 고정하지 못했습니다.',file=sys.stderr);return 2
    if args.command == 'validate':
        try:
            raw = _read_pinned(args.target, args.source_file, args.source_sha256).decode('utf-8')
            _, parsed, _ = preflight_record(args.target, args.output_context, args.output_context_sha256, raw)
            print(json.dumps({'status':'valid', 'stage':parsed['stage'], 'verdict':parsed['verdict'],
                              'reason_code':parsed['reason_code'], 'publication':'not_performed'}))
            return 0
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            print('기록 사전 검증 실패. ' + _choice_guidance(exc) +
                  f' [오류 코드: {_failure_code(exc, "source_read")}]', file=sys.stderr)
            return 2
    try:raw=_read_pinned(args.target,args.source_file,args.source_sha256).decode('utf-8')
    except (OSError,ValueError,UnicodeError) as exc:
        return _emit_failure(args.target, args.output_context, args.output_context_sha256,
                             None, 'source_read', exc)
    draft = ({'source_file': args.source_file, 'source_pin': args.source_sha256,
              'summary_file': args.draft_summary_file, 'summary_pin': args.draft_summary_sha256}
             if args.draft_summary_file is not None and args.draft_summary_sha256 else None)
    return emit_record(args.target,args.output_context,args.output_context_sha256,raw,draft=draft)


if __name__=='__main__':
    raise SystemExit(output_cli())
