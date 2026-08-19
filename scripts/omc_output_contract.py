#!/usr/bin/env python3
"""Compact machine envelope for OMC plan, task, and review outputs."""
from __future__ import annotations

import json
import re
from typing import Any


SCHEMA_VERSION = "omc-output/v1"
_ENVELOPE_PREFIX = "OMC_OUTPUT:"
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
}


class OutputContractError(ValueError):
    """Raised when an OMC output envelope is missing or inconsistent."""


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

    expected_next_skill, expected_selection = _ROUTING_POLICY[(stage, verdict)]
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
    return f"{_ENVELOPE_PREFIX} {serialized}\nVERDICT: {normalized_verdict}"


def parse_envelope(output: str) -> dict[str, Any]:
    nonempty_lines = [line for line in str(output).splitlines() if line.strip()]
    if (
        len(nonempty_lines) < 2
        or not nonempty_lines[-2].startswith(_ENVELOPE_PREFIX)
        or _VERDICT_RE.fullmatch(nonempty_lines[-1]) is None
    ):
        raise OutputContractError(
            "the last two non-empty lines must be OMC_OUTPUT and VERDICT"
        )
    envelope_lines = [
        line[len(_ENVELOPE_PREFIX) :].strip()
        for line in str(output).splitlines()
        if line.startswith(_ENVELOPE_PREFIX)
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
        "마지막 두 줄은 compact machine contract여야 합니다: "
        f"`OMC_OUTPUT: {{JSON}}` 뒤 `VERDICT: {verdicts}`. "
        f"JSON은 schema_version={SCHEMA_VERSION}, stage={normalized_stage}, outcome, risk, "
        "next_skill(canonical omc-* 또는 null), user_selection_needed(boolean), reason_code를 포함하세요. "
        f"라우팅(outcome,next_skill,user_selection_needed): {_PROMPT_ROUTING[normalized_stage]}; "
        "unresolved/blocked는 reason_code가 필수입니다."
    )
