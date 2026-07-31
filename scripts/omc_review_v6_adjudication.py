#!/usr/bin/env python3
"""Build blind review-adjudication packets and score a replacement gate.

The public packet intentionally contains neutral review-set aliases only. The
private alias map is required only after an independent adjudicator classifies
each finding against the signed-off semantic gold labels.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ALIASES = {"omc-review": "review-set-a", "codex": "review-set-b"}
_PROVIDER_BY_ALIAS = {alias: provider for provider, alias in _ALIASES.items()}
_VALID_CLASSIFICATIONS = {"hit", "false_positive"}
_VALID_EVIDENCE = {"accurate", "inaccurate"}
_MINIMUM_OMC_KEY_HIT_COUNT = 2
_NON_ACTIONABLE_EVIDENCE_CLASSES = {"test_quality_only"}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt_signature(
    execution: dict[str, Any],
    output: dict[str, Any],
    receipt_key: bytes,
) -> str:
    if not receipt_key:
        raise ValueError("trusted execution receipt key is required")
    payload = json.dumps(
        {"execution": execution, "output": output},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(receipt_key, payload, hashlib.sha256).hexdigest()


def _validate_receipt_key_anchor(packet: dict[str, Any], receipt_key: bytes) -> None:
    expected = packet.get("receipt_key_sha256")
    actual = hashlib.sha256(receipt_key).hexdigest()
    if not isinstance(expected, str) or not hmac.compare_digest(expected, actual):
        raise ValueError("receipt key does not match packet trust anchor")


def _validate_packet_integrity(packet: dict[str, Any]) -> None:
    expected = packet.get("packet_sha256")
    payload = {key: value for key, value in packet.items() if key != "packet_sha256"}
    if not isinstance(expected, str) or not hmac.compare_digest(expected, _digest(payload)):
        raise ValueError("packet hash mismatch")


def _new_execution_id() -> str:
    return f"omc-exec-{uuid.uuid4().hex}"


def _provider_session_id_from_event_stream(event_stream: bytes) -> str:
    try:
        lines = event_stream.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("fresh adjudicator event stream is invalid") from exc
    session_ids: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("fresh adjudicator event stream is invalid") from exc
        if isinstance(event, dict) and event.get("type") == "thread.started":
            session_ids.append(str(event.get("thread_id") or ""))
    if len(session_ids) != 1 or not session_ids[0].strip():
        raise ValueError(
            "fresh adjudicator event stream must identify one provider session"
        )
    return session_ids[0]


def seal_adjudication_execution(
    packet: dict[str, Any],
    adjudication: dict[str, Any],
    *,
    executor: str,
    model: str,
    recorded_at: str | None = None,
    receipt_key: bytes,
) -> tuple[dict[str, Any], bytes]:
    """Bind adjudication output to runner-issued execution metadata."""
    _validate_adjudication(packet, adjudication)
    _validate_receipt_key_anchor(packet, receipt_key)
    execution = {
        "executor": executor.strip(),
        "model": model.strip(),
        "execution_id": _new_execution_id(),
        "execution_scope": "omc_seal_execution",
        "recorded_at": recorded_at or _timestamp(),
        "input_packet_sha256": packet["packet_sha256"],
        "provider_mapping_visible": False,
    }
    for field in ("executor", "model", "execution_id", "execution_scope", "recorded_at"):
        if not execution[field]:
            raise ValueError(f"adjudicator execution field is required: {field}")
    output = {
        key: deepcopy(value)
        for key, value in adjudication.items()
        if key != "adjudicator_provenance"
    }
    receipt = _receipt_signature(execution, output, receipt_key)
    raw_output = (
        json.dumps(
            {
                "execution": execution,
                "output": output,
                "trusted_receipt_hmac_sha256": receipt,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    sealed = deepcopy(output)
    sealed["adjudicator_provenance"] = {
        **execution,
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "trusted_receipt_hmac_sha256": receipt,
    }
    return sealed, raw_output


def seal_fresh_adjudication_execution(
    packet: dict[str, Any],
    adjudication: dict[str, Any],
    *,
    executor: str,
    model: str,
    execution_id: str,
    provider_session_id: str,
    command_fingerprint: str,
    event_stream: bytes,
    receipt_key: bytes,
    executor_binary_path: str,
    executor_binary_sha256: str,
    tool_free_execution_verified: bool,
    recorded_at: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Bind output to a runner-observed fresh provider session."""
    _validate_adjudication(packet, adjudication)
    _validate_receipt_key_anchor(packet, receipt_key)
    execution = {
        "receipt_version": 3,
        "executor": executor.strip(),
        "model": model.strip(),
        "execution_id": execution_id.strip(),
        "execution_scope": "runner_attested_fresh_session",
        "provider_session_id": provider_session_id.strip(),
        "recorded_at": recorded_at or _timestamp(),
        "input_packet_sha256": packet["packet_sha256"],
        "provider_mapping_visible": False,
        "resume_used": False,
        "command_fingerprint": command_fingerprint.strip(),
        "event_stream_sha256": hashlib.sha256(event_stream).hexdigest(),
        "executor_binary_path": executor_binary_path.strip(),
        "executor_binary_sha256": executor_binary_sha256.strip(),
        "tool_free_execution_verified": tool_free_execution_verified,
        "exit_code": 0,
        "schema_verified": True,
        "semantic_completeness_verified": True,
    }
    required_strings = (
        "executor",
        "model",
        "execution_id",
        "provider_session_id",
        "recorded_at",
        "executor_binary_path",
    )
    for field in required_strings:
        if not execution[field]:
            raise ValueError(f"fresh adjudicator execution field is required: {field}")
    if re.fullmatch(r"[0-9a-f]{64}", execution["command_fingerprint"]) is None:
        raise ValueError("fresh adjudicator command fingerprint is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", execution["executor_binary_sha256"]) is None:
        raise ValueError("fresh adjudicator binary fingerprint is invalid")
    if execution["tool_free_execution_verified"] is not True:
        raise ValueError("fresh adjudicator execution must be tool-free")
    if not event_stream:
        raise ValueError("fresh adjudicator event stream is required")
    observed_session_id = _provider_session_id_from_event_stream(event_stream)
    if observed_session_id != execution["provider_session_id"]:
        raise ValueError(
            "fresh adjudicator provider session does not match event stream"
        )
    output = {
        key: deepcopy(value)
        for key, value in adjudication.items()
        if key != "adjudicator_provenance"
    }
    receipt = _receipt_signature(execution, output, receipt_key)
    raw_output = (
        json.dumps(
            {
                "execution": execution,
                "output": output,
                "event_stream_base64": base64.b64encode(event_stream).decode("ascii"),
                "trusted_receipt_hmac_sha256": receipt,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    sealed = deepcopy(output)
    sealed["adjudicator_provenance"] = {
        **execution,
        "raw_output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "trusted_receipt_hmac_sha256": receipt,
    }
    return sealed, raw_output


def _case_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        case_id = str(case.get("case_id") or "")
        if not case_id or case_id in indexed:
            raise ValueError("case id must be unique")
        indexed[case_id] = case
    return indexed


def _provider_findings(case: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    result = case.get("providers", {}).get(provider)
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise ValueError(f"provider result must be completed: {case.get('case_id')}/{provider}")
    findings = result.get("findings", [])
    if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
        raise ValueError(f"findings must be an object list: {case.get('case_id')}/{provider}")
    return findings


def _actionable_provider_findings(case: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    return [
        finding for finding in _provider_findings(case, provider)
        if finding.get("severity") != "제안"
        and finding.get("evidence_class") not in _NON_ACTIONABLE_EVIDENCE_CLASSES
    ]


def _public_finding(index: int, finding: dict[str, Any]) -> dict[str, Any]:
    """Expose only review content needed for semantic comparison."""
    fields = ("severity", "file", "line", "message", "evidence_class", "evidence")
    public = {field: finding.get(field) for field in fields if finding.get(field) is not None}
    # External runners embed provider-like names in their temporary directory.
    # Keep the semantic file location while removing that identifying path.
    file_name = public.get("file")
    if isinstance(file_name, str):
        normalized = file_name.replace("\\", "/")
        marker = "/workspace/"
        public["file"] = normalized.split(marker, 1)[1] if marker in normalized else normalized.lstrip("/")
    return {"finding_index": index, **public}


def build_blind_adjudication_packet(
    gold: dict[str, Any],
    codex: dict[str, Any],
    omc: dict[str, Any],
    *,
    receipt_key: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a provider-blind packet and a separately persisted alias map."""
    if gold.get("status") != "signed_off":
        raise ValueError("gold labels must be signed_off")
    if codex.get("status") != "completed_native_provider_runs_pending_adjudication" or codex.get("clean_baseline") is not True:
        raise ValueError("native baseline must be completed and clean")
    if omc.get("status") != "completed_omc_runs_pending_adjudication":
        raise ValueError("current review batch must be completed")

    gold_cases = _case_index(gold)
    codex_cases = _case_index(codex)
    omc_cases = _case_index(omc)
    if set(gold_cases) != set(codex_cases) or set(gold_cases) != set(omc_cases):
        raise ValueError("case set mismatch")

    cases: list[dict[str, Any]] = []
    for case_id in sorted(gold_cases):
        gold_case = gold_cases[case_id]
        expected_hash = gold_case.get("diff_sha256")
        if codex_cases[case_id].get("diff_sha256") != expected_hash or omc_cases[case_id].get("diff_sha256") != expected_hash:
            raise ValueError(f"diff hash mismatch: {case_id}")
        gold_findings = gold_case.get("gold_findings", [])
        if not isinstance(gold_findings, list) or any(not isinstance(item, dict) or not item.get("id") for item in gold_findings):
            raise ValueError(f"gold findings must be identified objects: {case_id}")
        cases.append({
            "case_id": case_id,
            "diff_sha256": expected_hash,
            "gold_findings": [{
                "id": item["id"], "severity": item.get("severity"), "file": item.get("file"),
                "line": item.get("line"), "reason": item.get("reason"),
            } for item in gold_findings],
            "review_sets": [
                {"alias": _ALIASES["omc-review"], "findings": [
                    _public_finding(index, item) for index, item in enumerate(
                        _actionable_provider_findings(omc_cases[case_id], "omc-review")
                    )
                ]},
                {"alias": _ALIASES["codex"], "findings": [
                    _public_finding(index, item) for index, item in enumerate(
                        _actionable_provider_findings(codex_cases[case_id], "codex")
                    )
                ]},
            ],
        })
    packet = {
        "status": "pending_blind_semantic_adjudication",
        "recorded_at": _timestamp(),
        "case_count": len(cases),
        "receipt_key_sha256": hashlib.sha256(receipt_key).hexdigest(),
        "instructions": (
            "For every finding, classify semantic equivalence against one gold finding as hit or false_positive. "
            "For hits, mark evidence_accuracy accurate or inaccurate. Each gold finding can match at most one finding per review set."
        ),
        "cases": cases,
    }
    packet["packet_sha256"] = _digest(packet)
    private_mapping = {
        "packet_sha256": packet["packet_sha256"],
        "alias_to_provider": _PROVIDER_BY_ALIAS,
        "warning": "Do not share this mapping with the blind adjudicator.",
    }
    return packet, private_mapping


def _validate_adjudication(packet: dict[str, Any], adjudication: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    _validate_packet_integrity(packet)
    if adjudication.get("status") != "completed" or adjudication.get("provider_outputs_visible") is not False:
        raise ValueError("adjudication must be completed with provider outputs hidden")
    if adjudication.get("packet_sha256") != packet.get("packet_sha256"):
        raise ValueError("adjudication packet hash mismatch")
    cases = adjudication.get("cases")
    if not isinstance(cases, list):
        raise ValueError("adjudication cases must be a list")
    source_cases = {str(item["case_id"]): item for item in packet.get("cases", [])}
    if {str(item.get("case_id") or "") for item in cases if isinstance(item, dict)} != set(source_cases) or len(cases) != len(source_cases):
        raise ValueError("adjudication case set mismatch")
    normalized: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for submitted_case in cases:
        if not isinstance(submitted_case, dict):
            raise ValueError("adjudication case must be an object")
        case_id = str(submitted_case["case_id"])
        source = source_cases[case_id]
        source_sets = {item["alias"]: item for item in source["review_sets"]}
        submitted_sets = submitted_case.get("review_sets")
        if (
            not isinstance(submitted_sets, list)
            or len(submitted_sets) != len(source_sets)
            or {
                item.get("alias")
                for item in submitted_sets
                if isinstance(item, dict)
            }
            != set(source_sets)
        ):
            raise ValueError(f"adjudication review set mismatch: {case_id}")
        normalized[case_id] = {}
        gold_ids = {str(item["id"]) for item in source["gold_findings"]}
        for submitted_set in submitted_sets:
            if not isinstance(submitted_set, dict):
                raise ValueError("adjudication review set must be an object")
            alias = str(submitted_set["alias"])
            labels = submitted_set.get("findings")
            expected_indexes = {item["finding_index"] for item in source_sets[alias]["findings"]}
            if (
                not isinstance(labels, list)
                or len(labels) != len(expected_indexes)
                or {
                    item.get("finding_index")
                    for item in labels
                    if isinstance(item, dict)
                }
                != expected_indexes
            ):
                raise ValueError(f"adjudication finding set mismatch: {case_id}/{alias}")
            claimed_gold: set[str] = set()
            rows: list[dict[str, Any]] = []
            for label in labels:
                if not isinstance(label, dict) or label.get("classification") not in _VALID_CLASSIFICATIONS:
                    raise ValueError(f"invalid classification: {case_id}/{alias}")
                classification = label["classification"]
                gold_id = label.get("gold_finding_id")
                evidence_accuracy = label.get("evidence_accuracy")
                if classification == "hit":
                    if gold_id not in gold_ids or gold_id in claimed_gold or evidence_accuracy not in _VALID_EVIDENCE:
                        raise ValueError(f"invalid hit label: {case_id}/{alias}")
                    claimed_gold.add(str(gold_id))
                elif gold_id is not None or evidence_accuracy is not None:
                    raise ValueError(f"false positive must not claim gold: {case_id}/{alias}")
                rows.append(label)
            normalized[case_id][alias] = rows
    return normalized


def _second_blind_cases(
    packet: dict[str, Any],
    labels: dict[str, dict[str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], int]:
    cases: list[dict[str, Any]] = []
    candidate_count = 0
    for case in packet["cases"]:
        case_id = case["case_id"]
        selected_sets: list[dict[str, Any]] = []
        for review_set in case["review_sets"]:
            labels_by_index = {
                item["finding_index"]: item for item in labels[case_id][review_set["alias"]]
            }
            selected = [
                deepcopy(finding) for finding in review_set["findings"]
                if labels_by_index[finding["finding_index"]]["classification"] == "false_positive"
            ]
            if selected:
                candidate_count += len(selected)
                selected_sets.append({"alias": review_set["alias"], "findings": selected})
        if selected_sets:
            cases.append({
                "case_id": case_id,
                "diff_sha256": case["diff_sha256"],
                "gold_findings": deepcopy(case["gold_findings"]),
                "review_sets": selected_sets,
            })
    return cases, candidate_count


def _validate_adjudicator_provenance(
    packet: dict[str, Any],
    adjudication: dict[str, Any],
    *,
    label: str,
    raw_output: bytes | None = None,
    receipt_key: bytes | None = None,
) -> dict[str, Any]:
    _validate_receipt_key_anchor(packet, receipt_key or b"")
    provenance = adjudication.get("adjudicator_provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"{label} adjudicator provenance is required")
    for field in ("executor", "model", "execution_id", "execution_scope", "recorded_at"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise ValueError(f"{label} adjudicator provenance field is required: {field}")
    if provenance.get("input_packet_sha256") != packet.get("packet_sha256"):
        raise ValueError(f"{label} adjudicator packet provenance mismatch")
    if provenance.get("provider_mapping_visible") is not False:
        raise ValueError(f"{label} adjudicator must not have provider mapping visibility")
    raw_output_sha256 = provenance.get("raw_output_sha256")
    if not isinstance(raw_output_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", raw_output_sha256) is None:
        raise ValueError(f"{label} adjudicator raw output hash is invalid")
    if raw_output is not None:
        if hashlib.sha256(raw_output).hexdigest() != raw_output_sha256:
            raise ValueError(f"{label} adjudicator raw output hash mismatch")
        try:
            envelope = json.loads(raw_output.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} adjudicator execution envelope is invalid") from exc
        if not isinstance(envelope, dict):
            raise ValueError(f"{label} adjudicator execution envelope is invalid")
        execution = envelope.get("execution")
        output = envelope.get("output")
        if not isinstance(execution, dict) or not isinstance(output, dict):
            raise ValueError(f"{label} adjudicator execution envelope is invalid")
        receipt = envelope.get("trusted_receipt_hmac_sha256")
        if not isinstance(receipt, str) or re.fullmatch(r"[0-9a-f]{64}", receipt) is None:
            raise ValueError(f"{label} trusted execution receipt is invalid")
        if provenance.get("trusted_receipt_hmac_sha256") != receipt:
            raise ValueError(f"{label} trusted execution receipt provenance mismatch")
        expected_receipt = _receipt_signature(execution, output, receipt_key or b"")
        if not hmac.compare_digest(receipt, expected_receipt):
            raise ValueError(f"{label} trusted execution receipt mismatch")
        expected_fields = [
            "executor",
            "model",
            "execution_id",
            "execution_scope",
            "recorded_at",
            "input_packet_sha256",
            "provider_mapping_visible",
        ]
        if provenance["execution_scope"] == "runner_attested_fresh_session":
            fresh_fields = [
                "receipt_version",
                "provider_session_id",
                "resume_used",
                "command_fingerprint",
                "event_stream_sha256",
                "executor_binary_path",
                "executor_binary_sha256",
                "tool_free_execution_verified",
                "exit_code",
                "schema_verified",
                "semantic_completeness_verified",
            ]
            expected_fields.extend(fresh_fields)
            if provenance.get("receipt_version") != 3:
                raise ValueError(f"{label} fresh receipt version is invalid")
            if not isinstance(provenance.get("provider_session_id"), str) or not provenance["provider_session_id"].strip():
                raise ValueError(f"{label} provider session id is required")
            if provenance.get("resume_used") is not False:
                raise ValueError(f"{label} fresh adjudication must not resume a session")
            if provenance.get("exit_code") != 0:
                raise ValueError(f"{label} fresh adjudication exit code is invalid")
            if provenance.get("schema_verified") is not True or provenance.get("semantic_completeness_verified") is not True:
                raise ValueError(f"{label} fresh adjudication validation is incomplete")
            if provenance.get("tool_free_execution_verified") is not True:
                raise ValueError(f"{label} fresh adjudication must be tool-free")
            if not isinstance(provenance.get("executor_binary_path"), str) or not provenance["executor_binary_path"].strip():
                raise ValueError(f"{label} fresh adjudicator binary path is required")
            if re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("executor_binary_sha256") or "")) is None:
                raise ValueError(f"{label} fresh adjudicator binary fingerprint is invalid")
            encoded_event_stream = envelope.get("event_stream_base64")
            if not isinstance(encoded_event_stream, str):
                raise ValueError(f"{label} fresh adjudicator event stream is required")
            try:
                event_stream = base64.b64decode(
                    encoded_event_stream, validate=True
                )
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"{label} fresh adjudicator event stream is invalid"
                ) from exc
            if hashlib.sha256(event_stream).hexdigest() != provenance.get("event_stream_sha256"):
                raise ValueError(f"{label} fresh adjudicator event stream hash mismatch")
            if (
                _provider_session_id_from_event_stream(event_stream)
                != provenance["provider_session_id"]
            ):
                raise ValueError(
                    f"{label} provider session does not match event stream"
                )
        expected_execution = {
            field: provenance[field]
            for field in expected_fields
        }
        if execution != expected_execution:
            raise ValueError(f"{label} adjudicator execution envelope metadata mismatch")
        normalized_output = {
            key: value
            for key, value in adjudication.items()
            if key != "adjudicator_provenance"
        }
        if output != normalized_output:
            raise ValueError(f"{label} adjudicator execution envelope output mismatch")
    return provenance


def build_second_blind_packet(
    packet: dict[str, Any], first_adjudication: dict[str, Any]
) -> dict[str, Any]:
    """Select every first-pass false-positive candidate without provider hints.

    Suggestions are excluded from the primary finding contract before this
    packet is built. Evidence metadata is provider-specific, so it cannot be
    used as a second-pass eligibility rule without biasing the comparison.
    """
    labels = _validate_adjudication(packet, first_adjudication)
    cases, candidate_count = _second_blind_cases(packet, labels)
    second_packet = {
        "status": "pending_second_blind_semantic_adjudication",
        "recorded_at": _timestamp(),
        "source_packet_sha256": packet["packet_sha256"],
        "receipt_key_sha256": packet["receipt_key_sha256"],
        "case_count": len(cases),
        "candidate_count": candidate_count,
        "instructions": (
            "Independently classify every finding against the gold findings by semantic meaning. "
            "Do not infer providers or prior adjudication results."
        ),
        "cases": cases,
    }
    second_packet["packet_sha256"] = _digest(second_packet)
    return second_packet


def build_second_adjudication_consensus(
    first_packet: dict[str, Any],
    first_adjudication: dict[str, Any],
    second_packet: dict[str, Any],
    second_adjudication: dict[str, Any],
    first_raw_output: bytes,
    second_raw_output: bytes,
    *,
    receipt_key: bytes | None = None,
) -> dict[str, Any]:
    """Confirm only behavioral false positives that two blind reviewers agree on."""
    if second_packet.get("source_packet_sha256") != first_packet.get("packet_sha256"):
        raise ValueError("second packet does not belong to first packet")
    first_labels = _validate_adjudication(first_packet, first_adjudication)
    expected_cases, expected_candidate_count = _second_blind_cases(first_packet, first_labels)
    expected_candidates: set[tuple[Any, Any, Any]] = set()
    for case in expected_cases:
        case_id = case["case_id"]
        for review_set in case["review_sets"]:
            alias = review_set["alias"]
            expected_candidates.update(
                (case_id, alias, finding["finding_index"])
                for finding in review_set["findings"]
            )
    supplied_candidates = {
        (case.get("case_id"), review_set.get("alias"), finding.get("finding_index"))
        for case in second_packet.get("cases", [])
        if isinstance(case, dict)
        for review_set in case.get("review_sets", [])
        if isinstance(review_set, dict)
        for finding in review_set.get("findings", [])
        if isinstance(finding, dict)
    }
    if expected_candidates != supplied_candidates:
        raise ValueError("second packet candidate set mismatch")
    if (
        second_packet.get("cases") != expected_cases
        or second_packet.get("case_count") != len(expected_cases)
        or second_packet.get("candidate_count") != expected_candidate_count
    ):
        raise ValueError("second packet payload mismatch")
    packet_payload = {
        key: value for key, value in second_packet.items() if key != "packet_sha256"
    }
    if second_packet.get("packet_sha256") != _digest(packet_payload):
        raise ValueError("second packet hash mismatch")
    first_provenance = _validate_adjudicator_provenance(
        first_packet,
        first_adjudication,
        label="first",
        raw_output=first_raw_output,
        receipt_key=receipt_key,
    )
    second_provenance = _validate_adjudicator_provenance(
        second_packet,
        second_adjudication,
        label="second",
        raw_output=second_raw_output,
        receipt_key=receipt_key,
    )
    if first_provenance["execution_id"] == second_provenance["execution_id"]:
        raise ValueError("second adjudication must use an independent seal execution")
    first_is_fresh = (
        first_provenance["execution_scope"] == "runner_attested_fresh_session"
    )
    second_is_fresh = (
        second_provenance["execution_scope"] == "runner_attested_fresh_session"
    )
    fresh_sessions_verified = first_is_fresh and second_is_fresh
    if fresh_sessions_verified and (
        first_provenance["provider_session_id"]
        == second_provenance["provider_session_id"]
    ):
        raise ValueError("second adjudication must use an independent provider session")
    second_labels = _validate_adjudication(second_packet, second_adjudication)
    candidates: list[dict[str, Any]] = []
    confirmed = 0
    unresolved = 0
    for case in second_packet["cases"]:
        case_id = case["case_id"]
        for review_set in case["review_sets"]:
            alias = review_set["alias"]
            first_by_index = {
                item["finding_index"]: item for item in first_labels[case_id][alias]
            }
            second_by_index = {
                item["finding_index"]: item for item in second_labels[case_id][alias]
            }
            for finding in review_set["findings"]:
                index = finding["finding_index"]
                if first_by_index[index]["classification"] != "false_positive":
                    raise ValueError("second packet candidate is not a first-pass false positive")
                second_label = second_by_index[index]
                agreed = second_label["classification"] == "false_positive"
                status = "confirmed_false_positive" if agreed else "unresolved"
                confirmed += int(agreed)
                unresolved += int(not agreed)
                candidates.append({
                    "case_id": case_id,
                    "alias": alias,
                    "finding_index": index,
                    "status": status,
                    "gold_finding_id": second_label.get("gold_finding_id") if not agreed else None,
                })
    return {
        "status": "completed_second_blind_consensus",
        "first_packet_sha256": first_packet["packet_sha256"],
        "second_packet_sha256": second_packet["packet_sha256"],
        "candidate_count": len(candidates),
        "confirmed_false_positive_count": confirmed,
        "unresolved_candidate_count": unresolved,
        "execution_independence": {
            "scope": (
                "runner_attested_fresh_session"
                if fresh_sessions_verified
                else "omc_seal_execution"
            ),
            "verified": True,
            "adjudicator_session_independence_verified": fresh_sessions_verified,
        },
        "candidates": candidates,
    }


def build_replacement_report(
    packet: dict[str, Any],
    private_mapping: dict[str, Any],
    adjudication: dict[str, Any],
    second_packet: dict[str, Any] | None = None,
    second_adjudication: dict[str, Any] | None = None,
    first_raw_output: bytes | None = None,
    second_raw_output: bytes | None = None,
    *,
    receipt_key: bytes | None = None,
) -> dict[str, Any]:
    """Compute semantic metrics and the user's strict OMC replacement gate."""
    if private_mapping.get("packet_sha256") != packet.get("packet_sha256"):
        raise ValueError("private mapping packet hash mismatch")
    alias_to_provider = private_mapping.get("alias_to_provider")
    if alias_to_provider != _PROVIDER_BY_ALIAS:
        raise ValueError("private alias mapping is invalid")
    labels = _validate_adjudication(packet, adjudication)
    if (second_packet is None) != (second_adjudication is None):
        raise ValueError("second packet and adjudication must be provided together")
    if second_packet is not None and (
        first_raw_output is None or second_raw_output is None
    ):
        raise ValueError("first and second raw adjudication outputs are required")
    consensus = (
        build_second_adjudication_consensus(
            packet,
            adjudication,
            second_packet,
            second_adjudication,
            first_raw_output,
            second_raw_output,
            receipt_key=receipt_key,
        )
        if (
            second_packet is not None
            and second_adjudication is not None
            and first_raw_output is not None
            and second_raw_output is not None
        )
        else None
    )
    unresolved_indexes = {
        (item["case_id"], item["alias"], item["finding_index"])
        for item in (consensus or {}).get("candidates", [])
        if item["status"] == "unresolved"
    }
    unresolved_gold_ids = {
        (item["case_id"], item["alias"], item["gold_finding_id"])
        for item in (consensus or {}).get("candidates", [])
        if item["status"] == "unresolved" and item.get("gold_finding_id") is not None
    }
    primary = set(packet.get("decision_policy", {}).get("primary_severity", ["P0", "P1"]))
    metrics = {provider: {"hit_count": 0, "miss_count": 0, "false_positive_count": 0, "unresolved_candidate_count": 0, "key_hit_count": 0, "key_miss_count": 0, "evidence_accurate_count": 0, "evidence_hit_count": 0} for provider in _ALIASES}
    for case in packet["cases"]:
        case_id = case["case_id"]
        gold = {item["id"]: item for item in case["gold_findings"]}
        for alias, rows in labels[case_id].items():
            provider = alias_to_provider[alias]
            excluded_gold = {
                gold_id for candidate_case_id, candidate_alias, gold_id in unresolved_gold_ids
                if candidate_case_id == case_id and candidate_alias == alias
            }
            hits = {
                row["gold_finding_id"] for row in rows
                if row["classification"] == "hit"
            }
            # A second-pass disagreement cannot invalidate a separately
            # confirmed first-pass hit for the same gold finding.
            eligible_gold = set(gold) - (excluded_gold - hits)
            metrics[provider]["hit_count"] += len(hits)
            metrics[provider]["miss_count"] += len(eligible_gold) - len(hits)
            for row in rows:
                location = (case_id, alias, row["finding_index"])
                if location in unresolved_indexes:
                    metrics[provider]["unresolved_candidate_count"] += 1
                elif row["classification"] == "false_positive":
                    metrics[provider]["false_positive_count"] += 1
            metrics[provider]["evidence_hit_count"] += len(hits)
            metrics[provider]["evidence_accurate_count"] += sum(row.get("evidence_accuracy") == "accurate" for row in rows if row["classification"] == "hit")
            key_gold = {
                gold_id for gold_id, item in gold.items()
                if gold_id in eligible_gold and item.get("severity") in primary
            }
            key_hits = hits & key_gold
            metrics[provider]["key_hit_count"] += len(key_hits)
            metrics[provider]["key_miss_count"] += len(key_gold) - len(key_hits)
    providers: dict[str, dict[str, Any]] = {}
    for provider, values in metrics.items():
        key_total = values["key_hit_count"] + values["key_miss_count"]
        hit_total = values["hit_count"] + values["miss_count"]
        evidence_total = values["evidence_hit_count"]
        providers[provider] = {
            **values,
            "semantic_hit_rate": values["hit_count"] / hit_total if hit_total else 1.0,
            "key_detection_rate": values["key_hit_count"] / key_total if key_total else 1.0,
            "evidence_accuracy": values["evidence_accurate_count"] / evidence_total if evidence_total else 1.0,
        }
    omc_metrics = providers["omc-review"]
    codex_metrics = providers["codex"]
    gate = {
        "key_detection_at_least_codex": omc_metrics["key_detection_rate"] >= codex_metrics["key_detection_rate"],
        "minimum_omc_key_hit_count": omc_metrics["key_hit_count"] >= _MINIMUM_OMC_KEY_HIT_COUNT,
        "evidence_accuracy_at_least_codex": omc_metrics["evidence_accuracy"] >= codex_metrics["evidence_accuracy"],
        "false_positives_not_higher": omc_metrics["false_positive_count"] <= codex_metrics["false_positive_count"],
        "adjudicator_session_independence_verified": bool(
            consensus
            and consensus.get("execution_independence", {}).get(
                "adjudicator_session_independence_verified"
            )
            is True
        ),
    }
    return {
        "status": "completed_blind_semantic_comparison",
        "recorded_at": _timestamp(),
        "case_count": packet["case_count"],
        "packet_sha256": packet["packet_sha256"],
        "provider_outputs_visible_to_adjudicator": False,
        "providers": providers,
        "second_blind_consensus": consensus,
        "replacement_gate": gate,
        "replacement_verdict": "omc-replaceable" if all(gate.values()) else "not_replaceable",
        "decision_rule": "OMC is replaceable only when it finds at least two key issues, key detection and evidence accuracy are at least Codex, false positives are not higher, and independent adjudicator sessions are verified.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--gold", required=True, type=Path)
    prepare.add_argument("--codex", required=True, type=Path)
    prepare.add_argument("--omc", required=True, type=Path)
    prepare.add_argument("--receipt-key-file", required=True, type=Path)
    prepare.add_argument("--packet-out", required=True, type=Path)
    prepare.add_argument("--mapping-out", required=True, type=Path)
    prepare_second = commands.add_parser("prepare-second")
    prepare_second.add_argument("--packet", required=True, type=Path)
    prepare_second.add_argument("--first-adjudication", required=True, type=Path)
    prepare_second.add_argument("--output", required=True, type=Path)
    seal = commands.add_parser("seal-adjudication")
    seal.add_argument("--packet", required=True, type=Path)
    seal.add_argument("--adjudication", required=True, type=Path)
    seal.add_argument("--executor", required=True)
    seal.add_argument("--model", required=True)
    seal.add_argument("--receipt-key-file", required=True, type=Path)
    seal.add_argument("--adjudication-out", required=True, type=Path)
    seal.add_argument("--raw-out", required=True, type=Path)
    consensus = commands.add_parser("consensus")
    consensus.add_argument("--packet", required=True, type=Path)
    consensus.add_argument("--first-adjudication", required=True, type=Path)
    consensus.add_argument("--first-adjudication-raw", required=True, type=Path)
    consensus.add_argument("--second-packet", required=True, type=Path)
    consensus.add_argument("--second-adjudication", required=True, type=Path)
    consensus.add_argument("--second-adjudication-raw", required=True, type=Path)
    consensus.add_argument("--receipt-key-file", required=True, type=Path)
    consensus.add_argument("--output", required=True, type=Path)
    score = commands.add_parser("score")
    score.add_argument("--packet", required=True, type=Path)
    score.add_argument("--mapping", required=True, type=Path)
    score.add_argument("--adjudication", required=True, type=Path)
    score.add_argument("--first-adjudication-raw", type=Path)
    score.add_argument("--output", required=True, type=Path)
    score.add_argument("--second-packet", type=Path)
    score.add_argument("--second-adjudication", type=Path)
    score.add_argument("--second-adjudication-raw", type=Path)
    score.add_argument("--receipt-key-file", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        packet, mapping = build_blind_adjudication_packet(
            _load(args.gold),
            _load(args.codex),
            _load(args.omc),
            receipt_key=args.receipt_key_file.read_bytes(),
        )
        _write(args.packet_out, packet)
        _write(args.mapping_out, mapping)
        print(f"blind packet written: {args.packet_out} ({packet['case_count']} cases)")
    elif args.command == "prepare-second":
        packet = build_second_blind_packet(_load(args.packet), _load(args.first_adjudication))
        _write(args.output, packet)
        print(f"second blind packet written: {args.output} ({packet['candidate_count']} candidates)")
    elif args.command == "seal-adjudication":
        adjudication, raw_output = seal_adjudication_execution(
            _load(args.packet),
            _load(args.adjudication),
            executor=args.executor,
            model=args.model,
            receipt_key=args.receipt_key_file.read_bytes(),
        )
        _write(args.adjudication_out, adjudication)
        args.raw_out.parent.mkdir(parents=True, exist_ok=True)
        args.raw_out.write_bytes(raw_output)
        print(f"sealed adjudication written: {args.adjudication_out}")
    elif args.command == "consensus":
        result = build_second_adjudication_consensus(
            _load(args.packet),
            _load(args.first_adjudication),
            _load(args.second_packet),
            _load(args.second_adjudication),
            args.first_adjudication_raw.read_bytes(),
            args.second_adjudication_raw.read_bytes(),
            receipt_key=args.receipt_key_file.read_bytes(),
        )
        _write(args.output, result)
        print(f"second blind consensus written: {args.output} ({result['candidate_count']} candidates)")
    else:
        report = build_replacement_report(
            _load(args.packet), _load(args.mapping), _load(args.adjudication),
            _load(args.second_packet) if args.second_packet else None,
            _load(args.second_adjudication) if args.second_adjudication else None,
            args.first_adjudication_raw.read_bytes() if args.first_adjudication_raw else None,
            args.second_adjudication_raw.read_bytes() if args.second_adjudication_raw else None,
            receipt_key=args.receipt_key_file.read_bytes() if args.receipt_key_file else None,
        )
        _write(args.output, report)
        print(f"replacement report written: {args.output} ({report['replacement_verdict']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
