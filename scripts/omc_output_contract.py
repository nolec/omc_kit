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
    r"^VERDICT\s*:\s*(APPROVE WITH NOTES|PROCEED|APPROVE|BLOCK|REVISE|HOLD)\s*$",
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
    try:
        return _STAGE_VERDICTS[normalized_stage][normalized_verdict]
    except KeyError as exc:
        raise OutputContractError(
            f"verdict {normalized_verdict!r} is not valid for stage {normalized_stage!r}"
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
    reason_routes = _REASON_ROUTING_POLICY.get((stage, verdict))
    if reason_routes is not None:
        if reason_code not in reason_routes:
            raise OutputContractError("reason_code is not valid for stage verdict routing")
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
    )
