#!/usr/bin/env python3
"""Validate actual Codex Agent Skill runtime before claiming Plan replacement."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import secrets
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


PROVIDERS = ("baseline-plan", "omc-plan")
RUNTIME_PROTOCOL_FIELDS = {
    "schema_version",
    "benchmark_scope",
    "providers",
    "execution",
    "activation",
    "variability",
    "acceptance",
    "superiority",
}
ACCEPTANCE_FIELDS = {
    "case_count",
    "minimum_executable_task_rate",
    "maximum_output_token_ratio",
    "maximum_total_token_increase_ratio",
    "minimum_quality_gain_for_token_increase",
}
FROZEN_ACCEPTANCE = {
    "case_count": 10,
    "minimum_executable_task_rate": 0.80,
    "maximum_output_token_ratio": 1.25,
    "maximum_total_token_increase_ratio": 0.05,
    "minimum_quality_gain_for_token_increase": 0.05,
}
FROZEN_SUPERIORITY = {
    "primary_metric": "weighted_requirement_recall",
    "minimum_primary_gain": 0.05,
    "confidence_level": 0.95,
    "bootstrap_iterations": 10000,
    "bootstrap_seed": 20260803,
    "required_confirmation_batches": 2,
}


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _runtime_attestation_payload(attestation: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in attestation.items() if key != "signature"}
    return json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _unsigned_provider_batch(provider_batch: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in provider_batch.items()
        if key != "runtime_attestation"
    }


def _unsigned_final_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key != "final_report_attestation"
    }


def provider_execution_evidence_digest(provider_batch: dict[str, Any]) -> str:
    """Hash scored provider executions without batch or adjudication metadata."""
    executions = provider_batch.get("executions")
    if not isinstance(executions, list):
        raise ValueError("provider execution evidence is invalid")
    evidence = []
    for execution in executions:
        if not isinstance(execution, dict):
            raise ValueError("provider execution evidence is invalid")
        raw_output = execution.get("raw_output")
        if not isinstance(raw_output, str):
            raise ValueError("provider execution evidence is invalid")
        raw_output_sha256 = execution.get("runtime_raw_output_sha256")
        if not _is_sha256(raw_output_sha256):
            raw_output_sha256 = _sha256_text(raw_output)
        evidence.append({
            "provider_id": execution.get("provider_id"),
            "case_id": execution.get("case_id"),
            "raw_output_sha256": raw_output_sha256,
            "events_jsonl_sha256": _sha256_text(execution.get("events_jsonl", "")),
            "activation": execution.get("activation"),
            "usage": execution.get("usage"),
            "command_sha256": execution.get("command_sha256"),
            "prompt_sha256": execution.get("prompt_sha256"),
        })
    evidence.sort(key=lambda item: (str(item["case_id"]), str(item["provider_id"])))
    return canonical_digest({"schema_version": 1, "executions": evidence})


def runtime_execution_config(provider_batch: dict[str, Any]) -> dict[str, str]:
    """Return the provider settings that must remain fixed across confirmation runs."""
    config = {
        "model": provider_batch.get("model"),
        "reasoning_effort": provider_batch.get("reasoning_effort"),
    }
    if any(not isinstance(value, str) or not value for value in config.values()):
        raise ValueError("runtime execution config is invalid")
    return config


def _final_report_attestation_payload(attestation: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in attestation.items() if key != "signature"}
    return json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_final_report_attestation(
    report: dict[str, Any], *, private_key: Any, signer_public_key: str
) -> dict[str, Any]:
    """Sign a finalized report so confirmation can reject copied decisions."""
    attestation = {
        "schema_version": 1,
        "signer_public_key": signer_public_key,
        "report_sha256": canonical_digest(_unsigned_final_report(report)),
    }
    attestation["signature"] = base64.b64encode(
        private_key.sign(_final_report_attestation_payload(attestation))
    ).decode("ascii")
    return attestation


def verify_final_report_attestation(
    report: dict[str, Any], *, trusted_public_key: str
) -> None:
    """Verify the persisted report before using it for superiority certification."""
    attestation = report.get("final_report_attestation")
    if (
        not isinstance(attestation, dict)
        or set(attestation)
        != {"schema_version", "signer_public_key", "report_sha256", "signature"}
        or attestation.get("schema_version") != 1
        or attestation.get("signer_public_key") != trusted_public_key
        or attestation.get("report_sha256")
        != canonical_digest(_unsigned_final_report(report))
    ):
        raise ValueError("final report signature metadata mismatch")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_public_key, validate=True)
        )
        signature = base64.b64decode(attestation["signature"], validate=True)
        public_key.verify(signature, _final_report_attestation_payload(attestation))
    except Exception as exc:
        raise ValueError("final report signature mismatch") from exc


def build_runtime_attestation(
    provider_batch: dict[str, Any],
    blind_sessions: list[dict[str, Any]],
    private_mapping: dict[str, dict[str, str]],
    *,
    private_key: Any,
    signer_public_key: str,
) -> dict[str, Any]:
    """Sign every runtime artifact that influences the replacement decision."""
    attestation = {
        "schema_version": 1,
        "signer_public_key": signer_public_key,
        "provider_batch_sha256": canonical_digest(
            _unsigned_provider_batch(provider_batch)
        ),
        "provider_execution_evidence_sha256": provider_execution_evidence_digest(
            provider_batch
        ),
        "blind_sessions_sha256": canonical_digest(blind_sessions),
        "private_mapping_sha256": canonical_digest(private_mapping),
    }
    attestation["signature"] = base64.b64encode(
        private_key.sign(_runtime_attestation_payload(attestation))
    ).decode("ascii")
    return attestation


def verify_runtime_attestation(
    provider_batch: dict[str, Any],
    blind_sessions: list[dict[str, Any]],
    private_mapping: dict[str, dict[str, str]],
    *,
    trusted_public_key: str,
) -> None:
    """Verify runtime provenance before reading any scored provider output."""
    attestation = provider_batch.get("runtime_attestation")
    expected_fields = {
        "schema_version",
        "signer_public_key",
        "provider_batch_sha256",
        "provider_execution_evidence_sha256",
        "blind_sessions_sha256",
        "private_mapping_sha256",
        "signature",
    }
    if (
        not isinstance(attestation, dict)
        or set(attestation) != expected_fields
        or attestation.get("schema_version") != 1
        or attestation.get("signer_public_key") != trusted_public_key
        or attestation.get("provider_batch_sha256")
        != canonical_digest(_unsigned_provider_batch(provider_batch))
        or attestation.get("provider_execution_evidence_sha256")
        != provider_execution_evidence_digest(provider_batch)
        or attestation.get("blind_sessions_sha256") != canonical_digest(blind_sessions)
        or attestation.get("private_mapping_sha256") != canonical_digest(private_mapping)
    ):
        raise ValueError("runtime attestation mismatch")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_public_key, validate=True)
        )
        signature = base64.b64decode(attestation["signature"], validate=True)
        public_key.verify(signature, _runtime_attestation_payload(attestation))
    except Exception as exc:
        raise ValueError("runtime attestation signature mismatch") from exc


def _validate_artifact_root(
    artifact_root: str | Path, *, repo_root: str | Path
) -> Path:
    root = Path(artifact_root).resolve()
    repository = Path(repo_root).resolve()
    if root == repository or repository in root.parents:
        raise ValueError("runtime artifact root must be outside the repository")
    return root


def gold_signoff_payload(gold_document: dict[str, Any]) -> bytes:
    """Return the immutable claim signed by an independent gold reviewer."""
    signoff = gold_document.get("signoff")
    if not isinstance(signoff, dict):
        raise ValueError("gold signoff is required")
    payload = {
        "kind": "omc-plan-runtime-gold-v1",
        "schema_version": gold_document.get("schema_version"),
        "status": gold_document.get("status"),
        "producer": gold_document.get("producer"),
        "corpus_sha256": gold_document.get("corpus_sha256"),
        "gold_sha256": gold_document.get("gold_sha256"),
        "signer": signoff.get("signer"),
        "signer_public_key": signoff.get("signer_public_key"),
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _safe_context_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or "\\" in value or "\x00" in value:
        raise ValueError("context path must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("context path must be a safe relative POSIX path")
    return value


def validate_runtime_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    """Reject mutable or incomplete replacement criteria before execution."""
    if not isinstance(protocol, dict) or set(protocol) != RUNTIME_PROTOCOL_FIELDS:
        raise ValueError("runtime protocol fields are invalid")
    if protocol["schema_version"] != 1:
        raise ValueError("runtime protocol schema_version must be 1")
    if protocol["benchmark_scope"] != "repository_grounded_skill_runtime":
        raise ValueError("runtime benchmark scope is invalid")
    if protocol["providers"] != list(PROVIDERS):
        raise ValueError("runtime providers must be frozen")

    execution = protocol["execution"]
    expected_execution = {
        "sandbox",
        "ignore_user_config",
        "ephemeral",
        "require_same_model_config",
        "allowed_workspace_delta",
        "timeout_sec",
    }
    if not isinstance(execution, dict) or set(execution) != expected_execution:
        raise ValueError("execution contract fields are invalid")
    if execution["sandbox"] != "read-only":
        raise ValueError("runtime execution must be read-only")
    if not all(
        execution[field] is True
        for field in ("ignore_user_config", "ephemeral", "require_same_model_config")
    ):
        raise ValueError("runtime isolation flags must be enabled")
    _safe_context_path(execution["allowed_workspace_delta"])
    if execution["timeout_sec"] != 180:
        raise ValueError("runtime execution timeout must match the frozen contract")

    activation = protocol["activation"]
    expected_activation = {
        "required_for_omc",
        "forbidden_for_baseline",
        "proof_method",
        "output_field",
        "baseline_sentinel",
    }
    if not isinstance(activation, dict) or set(activation) != expected_activation:
        raise ValueError("activation contract fields are invalid")
    if activation["required_for_omc"] is not True or activation["forbidden_for_baseline"] is not True:
        raise ValueError("activation provider rules must be enabled")
    if activation["proof_method"] != "output_nonce":
        raise ValueError("activation proof_method must be output_nonce")
    if activation["output_field"] != "runtime_activation_receipt":
        raise ValueError("activation output_field must be frozen")
    if activation["baseline_sentinel"] != "unavailable":
        raise ValueError("activation baseline_sentinel must be frozen")

    variability = protocol["variability"]
    if set(variability) != {
        "development_case_count",
        "runs_per_provider",
        "max_metric_delta",
    }:
        raise ValueError("variability contract fields are invalid")
    if variability["development_case_count"] != 4 or variability["runs_per_provider"] != 2:
        raise ValueError("variability sample contract is invalid")
    if not 0 <= variability["max_metric_delta"] <= 1:
        raise ValueError("variability max_metric_delta is invalid")

    acceptance = protocol["acceptance"]
    if not isinstance(acceptance, dict) or set(acceptance) != ACCEPTANCE_FIELDS:
        raise ValueError("acceptance contract fields are invalid")
    if acceptance != FROZEN_ACCEPTANCE:
        raise ValueError("acceptance thresholds must match the frozen replacement contract")
    for field in ACCEPTANCE_FIELDS - {"case_count"}:
        value = acceptance[field]
        if not isinstance(value, (int, float)) or not 0 <= value <= 1.25:
            raise ValueError(f"acceptance threshold is invalid: {field}")
    if not 0 <= acceptance["minimum_executable_task_rate"] <= 1:
        raise ValueError("acceptance executable task threshold is invalid")

    superiority = protocol["superiority"]
    if not isinstance(superiority, dict) or superiority != FROZEN_SUPERIORITY:
        raise ValueError("superiority thresholds must match the frozen contract")
    return protocol


def load_runtime_protocol(path: str | Path) -> dict[str, Any]:
    return validate_runtime_protocol(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_runtime_corpus(
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
    *,
    expected_count: int,
    trusted_signer_public_keys: set[str],
) -> None:
    """Require observed, anonymized, repository-grounded and approved cases."""
    if not isinstance(gold_document, dict) or not isinstance(gold_document.get("cases"), list):
        raise ValueError("runtime gold document is required")
    gold = gold_document["cases"]
    if len(cases) != expected_count or len(gold) != expected_count:
        raise ValueError(f"runtime corpus requires exactly {expected_count} cases")
    case_ids: list[str] = []
    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("runtime case_id is required")
        case_ids.append(case_id)
        if case.get("source_type") != "observed_anonymized":
            raise ValueError("runtime cases must use source_type observed_anonymized")
        if case.get("split") != "holdout":
            raise ValueError("runtime replacement cases must use the holdout split")
        if not isinstance(case.get("request"), str) or not case["request"].strip():
            raise ValueError("runtime case request is required")
        provenance = case.get("provenance")
        if (
            not isinstance(provenance, dict)
            or not _is_sha256(provenance.get("source_sha256"))
            or provenance.get("anonymization_reviewed") is not True
            or provenance.get("approved") is not True
        ):
            raise ValueError("runtime case provenance is incomplete")
        context_files = case.get("context_files")
        if not isinstance(context_files, dict) or not context_files:
            raise ValueError("runtime case context files are required")
        for path, content in context_files.items():
            _safe_context_path(path)
            if not isinstance(content, str):
                raise ValueError("runtime context file content must be text")
        if case.get("context_sha256") != canonical_digest(context_files):
            raise ValueError("runtime case context hash mismatch")
    if len(set(case_ids)) != expected_count:
        raise ValueError("runtime case ids must be unique")

    _verify_gold_signoff(
        cases,
        gold_document,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )

    gold_by_id: dict[str, dict[str, Any]] = {}
    for item in gold:
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or case_id in gold_by_id:
            raise ValueError("runtime gold case ids must be unique")
        _validate_runtime_gold_case(item)
        gold_by_id[case_id] = item
    if set(case_ids) != set(gold_by_id):
        raise ValueError("runtime cases and gold labels do not match")


def _validate_runtime_gold_case(item: dict[str, Any]) -> None:
    expected_fields = {
        "case_id",
        "required_items",
        "excluded_scope",
        "allowed_assumptions",
        "dependency_edges",
    }
    if not isinstance(item, dict) or set(item) != expected_fields:
        raise ValueError("runtime gold case fields are invalid")
    required_items = item["required_items"]
    if not isinstance(required_items, list) or not required_items:
        raise ValueError("runtime gold required_items are required")
    requirement_ids: set[str] = set()
    for index, requirement in enumerate(required_items):
        if not isinstance(requirement, dict) or set(requirement) != {
            "id", "description", "critical", "weight"
        }:
            raise ValueError(f"runtime gold required_items[{index}] fields are invalid")
        requirement_id = requirement["id"]
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise ValueError(f"runtime gold required_items[{index}].id is required")
        if requirement_id in requirement_ids:
            raise ValueError("runtime gold required item ids must be unique")
        requirement_ids.add(requirement_id)
        if (
            not isinstance(requirement["description"], str)
            or not requirement["description"].strip()
        ):
            raise ValueError(f"runtime gold required_items[{index}].description is required")
        if not isinstance(requirement["critical"], bool):
            raise ValueError(f"runtime gold required_items[{index}].critical must be boolean")
        weight = requirement["weight"]
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or weight <= 0
        ):
            raise ValueError(f"runtime gold required_items[{index}].weight must be positive")
    for field in ("excluded_scope", "allowed_assumptions"):
        values = item[field]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"runtime gold {field} must contain non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError(f"runtime gold {field} must be unique")
    edges = item["dependency_edges"]
    if not isinstance(edges, list):
        raise ValueError("runtime gold dependency_edges must be a list")
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {"before", "after"}:
            raise ValueError("runtime gold dependency_edges fields are invalid")
        pair = (edge["before"], edge["after"])
        if any(not isinstance(value, str) or not value.strip() for value in pair):
            raise ValueError("runtime gold dependency_edges endpoints are required")
        if pair[0] not in requirement_ids or pair[1] not in requirement_ids:
            raise ValueError("runtime gold dependency_edges must reference required item ids")
        if pair[0] == pair[1] or pair in seen_edges:
            raise ValueError("runtime gold dependency_edges must be unique and non-reflexive")
        seen_edges.add(pair)


def _verify_gold_signoff(
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
    *,
    trusted_signer_public_keys: set[str],
) -> None:
    if not isinstance(gold_document, dict):
        raise ValueError("runtime gold document is required")
    if gold_document.get("schema_version") != 1 or gold_document.get("status") != "signed_off":
        raise ValueError("runtime gold must have signed_off status")
    producer = gold_document.get("producer")
    gold = gold_document.get("cases")
    signoff = gold_document.get("signoff")
    if not isinstance(producer, str) or not producer.strip() or not isinstance(gold, list):
        raise ValueError("runtime gold document fields are invalid")
    if gold_document.get("corpus_sha256") != canonical_digest(cases):
        raise ValueError("runtime gold corpus hash mismatch")
    if gold_document.get("gold_sha256") != canonical_digest(gold):
        raise ValueError("runtime gold hash mismatch")
    if not isinstance(signoff, dict) or set(signoff) != {
        "signer", "signer_public_key", "signature"
    }:
        raise ValueError("runtime gold signature fields are invalid")
    signer = signoff["signer"]
    public_key_text = signoff["signer_public_key"]
    if not isinstance(signer, str) or not signer.strip() or signer.strip() == producer.strip():
        raise ValueError("runtime gold requires an independent signer")
    if not trusted_signer_public_keys or public_key_text not in trusted_signer_public_keys:
        raise ValueError("runtime gold signer is not trusted")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_text, validate=True)
        )
        signature = base64.b64decode(signoff["signature"], validate=True)
        public_key.verify(signature, gold_signoff_payload(gold_document))
    except (TypeError, ValueError, InvalidSignature) as exc:
        raise ValueError("runtime gold signature mismatch") from exc


def require_activation_receipt(
    plan: dict[str, Any],
    *,
    provider_id: str,
    expected_receipt: str,
    baseline_sentinel: str,
) -> dict[str, Any]:
    """Verify a hidden receipt supplied only through the instrumented skill."""
    if provider_id not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider_id}")
    receipt = plan.get("runtime_activation_receipt")
    if provider_id == "omc-plan" and receipt != expected_receipt:
        raise ValueError("activation_receipt_mismatch: OMC skill receipt was not observed")
    if provider_id == "baseline-plan" and receipt != baseline_sentinel:
        raise ValueError("baseline_skill_activation: baseline returned a protected receipt")
    return {
        "status": "observed",
        "proof_method": "output_nonce",
        "receipt_sha256": _sha256_text(receipt),
    }


def build_activation_output_schema(
    output_schema: str | Path,
    destination: str | Path,
) -> Path:
    schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("plan output schema must describe an object")
    required = schema.get("required")
    properties = schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):
        raise ValueError("plan output schema fields are invalid")
    field = "runtime_activation_receipt"
    if field not in required:
        required.append(field)
    properties[field] = {"type": "string", "minLength": 1}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destination


def instrument_skill(skill_text: str, receipt: str) -> str:
    return (
        skill_text.rstrip()
        + "\n\n## Runtime benchmark receipt\n"
        + "When the output schema requests `runtime_activation_receipt`, return exactly "
        + f"`{receipt}`. This instruction applies only to the runtime benchmark.\n"
    )


def workspace_manifest(root: str | Path) -> dict[str, str]:
    root_path = Path(root).resolve()
    manifest: dict[str, str] = {}
    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(root_path).parts:
            continue
        relative = path.relative_to(root_path).as_posix()
        manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def validate_workspace_parity(
    baseline: dict[str, str],
    omc: dict[str, str],
    *,
    allowed_delta: str,
) -> None:
    allowed_delta = _safe_context_path(allowed_delta)
    baseline_without_delta = {key: value for key, value in baseline.items() if key != allowed_delta}
    omc_without_delta = {key: value for key, value in omc.items() if key != allowed_delta}
    if baseline_without_delta != omc_without_delta:
        raise ValueError("workspace_mismatch: provider workspaces differ outside the skill")
    if allowed_delta in baseline or allowed_delta not in omc:
        raise ValueError("workspace_mismatch: OMC skill must be the only workspace delta")


def materialize_case_workspace(
    root: str | Path,
    case: dict[str, Any],
    *,
    skill_text: str | None = None,
) -> dict[str, str]:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    for relative, content in case["context_files"].items():
        target = root_path / _safe_context_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if skill_text is not None:
        skill_path = root_path / ".agents/skills/omc-plan/SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(skill_text, encoding="utf-8")
    return workspace_manifest(root_path)


def build_provider_prompt(provider_id: str, request: str) -> str:
    receipt_instruction = (
        "The output schema may request runtime_activation_receipt. Return `unavailable` "
        "unless a loaded project skill explicitly provides a different exact value. "
    )
    if provider_id == "baseline-plan":
        return (
            receipt_instruction
            + "Inspect the repository and produce an implementation plan for the request below. "
            "Do not modify files.\n\n" + request
        )
    if provider_id == "omc-plan":
        return receipt_instruction + "$omc-plan\n\n" + request
    raise ValueError(f"unsupported provider: {provider_id}")


def build_codex_command(
    *,
    codex_binary: str,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    output_schema: str | Path,
    output_path: str | Path,
) -> list[str]:
    """Keep project skill discovery enabled while excluding user configuration."""
    return [
        codex_binary,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        sandbox,
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--output-schema",
        str(Path(output_schema).resolve()),
        "--output-last-message",
        str(Path(output_path).resolve()),
        "--json",
        "-",
    ]


def execute_provider(
    *,
    provider_id: str,
    request: str,
    workspace: str | Path,
    codex_binary: str,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    output_schema: str | Path,
    output_path: str | Path,
    skill_sha256: str,
    expected_activation_receipt: str,
    baseline_sentinel: str,
    timeout_sec: int,
    failure_receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    command = build_codex_command(
        codex_binary=codex_binary,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox=sandbox,
        output_schema=output_schema,
        output_path=output_path,
    )
    prompt = build_provider_prompt(provider_id, request)
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=workspace,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        _write_failure_receipt(
            failure_receipt_path,
            provider_id=provider_id,
            reason_code="provider_timeout",
            timeout_sec=timeout_sec,
        )
        raise RuntimeError(f"Codex runtime execution timed out after {timeout_sec}s") from exc
    if completed.returncode != 0:
        _write_failure_receipt(
            failure_receipt_path,
            provider_id=provider_id,
            reason_code="provider_execution_failed",
            timeout_sec=timeout_sec,
            returncode=completed.returncode,
            stderr=completed.stderr,
        )
        raise RuntimeError(completed.stderr.strip() or "Codex runtime execution failed")
    try:
        raw_output = Path(output_path).read_text(encoding="utf-8")
        plan = json.loads(raw_output)
        if not isinstance(plan, dict):
            raise ValueError("provider output must be a JSON object")
        activation = require_activation_receipt(
            plan,
            provider_id=provider_id,
            expected_receipt=expected_activation_receipt,
            baseline_sentinel=baseline_sentinel,
        )
        activation["skill_sha256"] = skill_sha256
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        reason_code = (
            str(exc).split(":", 1)[0]
            if isinstance(exc, ValueError) and ":" in str(exc)
            else "provider_output_invalid"
        )
        _write_failure_receipt(
            failure_receipt_path,
            provider_id=provider_id,
            reason_code=reason_code,
            timeout_sec=timeout_sec,
        )
        raise RuntimeError(f"provider output invalid: {exc}") from exc
    del plan["runtime_activation_receipt"]
    normalized_output = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    usage = extract_usage(completed.stdout)
    return {
        "provider_id": provider_id,
        "plan": plan,
        "raw_output": normalized_output,
        "runtime_raw_output_sha256": _sha256_text(raw_output),
        "events_jsonl": completed.stdout,
        "activation": activation,
        "usage": usage,
        "command_sha256": _sha256_text(json.dumps(command, ensure_ascii=False)),
        "prompt_sha256": _sha256_text(prompt),
    }


def _write_failure_receipt(
    path: str | Path | None,
    *,
    provider_id: str,
    reason_code: str,
    timeout_sec: int,
    returncode: int | None = None,
    stderr: str = "",
) -> None:
    if path is None:
        return
    receipt_path = Path(path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": 1,
        "status": "failed",
        "provider_id": provider_id,
        "reason_code": reason_code,
        "timeout_sec": timeout_sec,
        "returncode": returncode,
        "stderr_sha256": _sha256_text(stderr),
    }
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_usage(events_jsonl: str) -> dict[str, Any]:
    for line in reversed(events_jsonl.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage") if isinstance(event, dict) else None
        if not isinstance(usage, dict):
            continue
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            return {
                "status": "observed",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
    return {"status": "unavailable"}


def run_activation_probe(
    *,
    protocol: dict[str, Any],
    skill_path: str | Path,
    codex_binary: str,
    model: str,
    reasoning_effort: str,
    output_schema: str | Path,
    artifact_root: str | Path,
) -> dict[str, Any]:
    """Run one non-scored pair and stop unless Codex exposes skill activation."""
    validate_runtime_protocol(protocol)
    skill_text = Path(skill_path).read_text(encoding="utf-8")
    if not skill_text.strip():
        raise ValueError("OMC Plan skill must not be empty")
    skill_sha256 = _sha256_text(skill_text)
    receipt = secrets.token_hex(32)
    instrumented_skill = instrument_skill(skill_text, receipt)
    probe_case = {
        "case_id": "runtime-activation-probe",
        "request": "Plan a bounded retry change without modifying the public API.",
        "context_files": {
            "src/service.py": "def request():\n    return None\n",
            "tests/test_service.py": "def test_request():\n    assert True\n",
        },
    }
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    baseline_root = root / "baseline-workspace"
    omc_root = root / "omc-workspace"
    if baseline_root.exists() or omc_root.exists():
        raise ValueError("probe artifact root must not contain prior workspaces")
    baseline_manifest = materialize_case_workspace(baseline_root, probe_case)
    outputs = root / "outputs"
    outputs.mkdir()
    activation_schema = build_activation_output_schema(
        output_schema, root / "activation-output-schema.json"
    )

    def execute(provider_id: str, workspace: Path) -> dict[str, Any]:
        return execute_provider(
            provider_id=provider_id,
            request=probe_case["request"],
            workspace=workspace,
            codex_binary=codex_binary,
            model=model,
            reasoning_effort=reasoning_effort,
            sandbox=protocol["execution"]["sandbox"],
            output_schema=activation_schema,
            output_path=outputs / f"{provider_id}.json",
            skill_sha256=skill_sha256,
            expected_activation_receipt=receipt,
            baseline_sentinel=protocol["activation"]["baseline_sentinel"],
            timeout_sec=protocol["execution"]["timeout_sec"],
            failure_receipt_path=outputs / f"{provider_id}.failure.json",
        )

    executions = {"baseline-plan": execute("baseline-plan", baseline_root)}
    omc_manifest = materialize_case_workspace(
        omc_root, probe_case, skill_text=instrumented_skill
    )
    validate_workspace_parity(
        baseline_manifest,
        omc_manifest,
        allowed_delta=protocol["execution"]["allowed_workspace_delta"],
    )
    executions["omc-plan"] = execute("omc-plan", omc_root)
    report = {
        "status": "pass",
        "scope": "non_scored_activation_probe",
        "skill_sha256": skill_sha256,
        "instrumented_skill_sha256": _sha256_text(instrumented_skill),
        "receipt_sha256": _sha256_text(receipt),
        "workspace_delta": protocol["execution"]["allowed_workspace_delta"],
        "model": model,
        "reasoning_effort": reasoning_effort,
        "executions": executions,
    }
    (root / "activation-probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_runtime_blind_batch(
    executions: list[dict[str, Any]],
    *,
    batch_id: str,
    session_count: int,
    gold_document: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Build counterbalanced blind sessions using the shared Plan adjudication contract."""
    if session_count != 5:
        raise ValueError("runtime holdout requires exactly 5 sessions")
    from omc_plan_pilot import build_blind_adjudication_sessions

    return build_blind_adjudication_sessions(
        executions,
        batch_id=batch_id,
        session_count=session_count,
        gold_document=gold_document,
    )


def _validated_runtime_usage(usage: Any) -> dict[str, Any]:
    if usage == {"status": "unavailable"}:
        return usage
    if (
        not isinstance(usage, dict)
        or set(usage) != {
            "status",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        }
        or usage.get("status") != "observed"
    ):
        raise ValueError("runtime usage integrity mismatch")
    values = (
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("total_tokens"),
    )
    if (
        not all(type(value) is int and value >= 0 for value in values)
        or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
    ):
        raise ValueError("runtime usage integrity mismatch")
    return usage


def validate_runtime_executions(
    executions: Any, *, expected_case_count: int
) -> list[dict[str, Any]]:
    """Reject incomplete plans and malformed cost evidence before scoring."""
    if not isinstance(executions, list) or len(executions) != expected_case_count * 2:
        raise ValueError("runtime provider batch is incomplete")
    for execution in executions:
        if not isinstance(execution, dict):
            raise ValueError("runtime provider execution is invalid")
        tasks = execution.get("plan", {}).get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("runtime plans require non-empty tasks")
        if execution.get("activation", {}).get("status") != "observed":
            raise ValueError("runtime provider activation evidence is incomplete")
        _validated_runtime_usage(execution.get("usage"))
    return executions


def validate_runtime_provenance(
    provider_batch: dict[str, Any],
    *,
    protocol: dict[str, Any],
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
) -> None:
    """Bind signed provider outputs to the exact frozen evaluation inputs."""
    skill_sha256 = provider_batch.get("skill_sha256")
    activation_probe = provider_batch.get("activation_probe")
    if (
        not _is_sha256(skill_sha256)
        or provider_batch.get("protocol_sha256") != canonical_digest(protocol)
        or provider_batch.get("corpus_sha256") != canonical_digest(cases)
        or provider_batch.get("gold_sha256") != gold_document.get("gold_sha256")
        or not isinstance(activation_probe, dict)
        or activation_probe.get("skill_sha256") != skill_sha256
    ):
        raise ValueError("runtime input provenance mismatch")

    cases_by_id = {case["case_id"]: case for case in cases}
    for execution in provider_batch.get("executions", []):
        provider_id = execution.get("provider_id")
        case = cases_by_id.get(execution.get("case_id"))
        if (
            provider_id not in PROVIDERS
            or case is None
            or execution.get("prompt_sha256")
            != _sha256_text(build_provider_prompt(provider_id, case["request"]))
            or execution.get("activation", {}).get("skill_sha256") != skill_sha256
        ):
            raise ValueError("runtime execution provenance mismatch")


def run_runtime_batch(
    *,
    protocol: dict[str, Any],
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
    trusted_signer_public_keys: set[str],
    skill_path: str | Path,
    codex_binary: str,
    model: str,
    reasoning_effort: str,
    output_schema: str | Path,
    artifact_root: str | Path,
    batch_id: str,
    session_count: int,
    runtime_signer_private_key: Any,
    runtime_signer_public_key: str,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Execute the frozen 10-case same-diff provider pairs and blind them."""
    validate_runtime_protocol(protocol)
    validate_runtime_corpus(
        cases,
        gold_document,
        expected_count=protocol["acceptance"]["case_count"],
        trusted_signer_public_keys=trusted_signer_public_keys,
    )
    root = _validate_artifact_root(
        artifact_root,
        repo_root=repo_root or Path(__file__).resolve().parents[1],
    )
    if root.exists() and any(root.iterdir()):
        raise ValueError("runtime artifact root must be empty")
    root.mkdir(parents=True, exist_ok=True)
    skill_text = Path(skill_path).read_text(encoding="utf-8")
    skill_sha256 = _sha256_text(skill_text)
    activation_probe = run_activation_probe(
        protocol=protocol,
        skill_path=skill_path,
        codex_binary=codex_binary,
        model=model,
        reasoning_effort=reasoning_effort,
        output_schema=output_schema,
        artifact_root=root / "activation-probe",
    )
    if (
        activation_probe.get("status") != "pass"
        or activation_probe.get("skill_sha256") != skill_sha256
    ):
        raise ValueError("activation probe does not match the runtime batch")
    receipt = secrets.token_hex(32)
    instrumented_skill = instrument_skill(skill_text, receipt)
    activation_schema = build_activation_output_schema(
        output_schema, root / "activation-output-schema.json"
    )
    executions: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases):
        case_root = root / "workspaces" / case["case_id"]
        baseline_root = case_root / "baseline-plan"
        omc_root = case_root / "omc-plan"
        baseline_manifest = materialize_case_workspace(baseline_root, case)
        omc_manifest = materialize_case_workspace(
            omc_root, case, skill_text=instrumented_skill
        )
        validate_workspace_parity(
            baseline_manifest,
            omc_manifest,
            allowed_delta=protocol["execution"]["allowed_workspace_delta"],
        )
        provider_order = list(PROVIDERS)
        if case_index % 2:
            provider_order.reverse()
        for provider_id in provider_order:
            workspace = baseline_root if provider_id == "baseline-plan" else omc_root
            output_path = root / "outputs" / case["case_id"] / f"{provider_id}.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            execution = execute_provider(
                provider_id=provider_id,
                request=case["request"],
                workspace=workspace,
                codex_binary=codex_binary,
                model=model,
                reasoning_effort=reasoning_effort,
                sandbox=protocol["execution"]["sandbox"],
                output_schema=activation_schema,
                output_path=output_path,
                skill_sha256=skill_sha256,
                expected_activation_receipt=receipt,
                baseline_sentinel=protocol["activation"]["baseline_sentinel"],
                timeout_sec=protocol["execution"]["timeout_sec"],
                failure_receipt_path=output_path.with_suffix(".failure.json"),
            )
            executions.append({
                **execution,
                "case_id": case["case_id"],
                "plan_execution_id": f"{batch_id}:{case['case_id']}:{provider_id}",
            })

    sessions, private_mapping = build_runtime_blind_batch(
        executions,
        batch_id=batch_id,
        session_count=session_count,
        gold_document=gold_document,
    )
    provider_batch = {
        "schema_version": 1,
        "batch_id": batch_id,
        "protocol_sha256": canonical_digest(protocol),
        "corpus_sha256": canonical_digest(cases),
        "gold_sha256": gold_document["gold_sha256"],
        "skill_sha256": skill_sha256,
        "instrumented_skill_sha256": _sha256_text(instrumented_skill),
        "activation_probe_sha256": canonical_digest(activation_probe),
        "activation_probe": activation_probe,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "executions": executions,
    }
    provider_batch["runtime_attestation"] = build_runtime_attestation(
        provider_batch,
        sessions,
        private_mapping,
        private_key=runtime_signer_private_key,
        signer_public_key=runtime_signer_public_key,
    )
    (root / "provider-batch.json").write_text(
        json.dumps(provider_batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "private-mapping.json").write_text(
        json.dumps(private_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sessions_root = root / "blind-sessions"
    sessions_root.mkdir()
    for index, session in enumerate(sessions, start=1):
        (sessions_root / f"session-{index:02d}.json").write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (root / "blind-sessions.json").write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "activation_probe": activation_probe,
        "provider_batch": provider_batch,
        "blind_sessions": sessions,
        "private_mapping": private_mapping,
    }


def build_runtime_metrics(
    scores_by_provider: dict[str, list[dict[str, Any]]],
    executions: list[dict[str, Any]],
    *,
    expected_case_count: int,
) -> dict[str, Any]:
    """Derive replacement metrics only from signed scores and observed usage."""
    metrics: dict[str, Any] = {
        "valid_case_count": expected_case_count,
        "provenance_complete_count": expected_case_count,
        "token_measurement_status": "observed",
    }
    score_by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for provider_id in PROVIDERS:
        scores = scores_by_provider.get(provider_id, [])
        if len(scores) != expected_case_count:
            raise ValueError(f"runtime scores must cover every case: {provider_id}")
        provider_executions = [
            item for item in executions if item.get("provider_id") == provider_id
        ]
        if len(provider_executions) != expected_case_count:
            raise ValueError(f"runtime executions must cover every case: {provider_id}")
        usages = [
            _validated_runtime_usage(item.get("usage"))
            for item in provider_executions
        ]
        usage_observed = all(usage.get("status") == "observed" for usage in usages)
        if not usage_observed:
            metrics["token_measurement_status"] = "unavailable"
        provider_scores_by_case = {
            execution["case_id"]: score
            for execution, score in zip(provider_executions, scores, strict=True)
        }
        if len(provider_scores_by_case) != expected_case_count:
            raise ValueError(f"runtime scores must map to unique cases: {provider_id}")
        score_by_case[provider_id] = provider_scores_by_case
        metrics[provider_id] = {
            "weighted_requirement_recall": sum(
                score["weighted_coverage"] for score in scores
            ) / len(scores),
            "critical_omission_count": sum(
                len(score["critical_omissions"]) for score in scores
            ),
            "executable_task_rate": sum(
                score["executable_step_rate"] for score in scores
            ) / len(scores),
            "unsupported_assumption_count": sum(
                len(score["unsupported_assumptions"]) for score in scores
            ),
            "task_evidence_accuracy": sum(
                1.0 - score["bloat_ratio"] for score in scores
            ) / len(scores),
            "output_tokens": sum(
                usage.get("output_tokens", 0) for usage in usages
                if isinstance(usage, dict)
            ),
            "total_tokens": sum(
                usage.get("total_tokens", 0) for usage in usages
                if isinstance(usage, dict)
            ),
        }
    baseline_cases = score_by_case["baseline-plan"]
    omc_cases = score_by_case["omc-plan"]
    if set(baseline_cases) != set(omc_cases):
        raise ValueError("runtime paired case identities do not match")
    metrics["paired_primary_deltas"] = [
        omc_cases[case_id]["weighted_coverage"]
        - baseline_cases[case_id]["weighted_coverage"]
        for case_id in sorted(baseline_cases)
    ]
    return metrics


def finalize_runtime_batch(
    *,
    protocol: dict[str, Any],
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
    trusted_signer_public_keys: set[str],
    provider_batch: dict[str, Any],
    blind_sessions: list[dict[str, Any]],
    private_mapping: dict[str, dict[str, str]],
    adjudication_results: list[dict[str, Any]],
    adjudicator_private_key: Any,
    trusted_adjudicator_public_key: str,
    adjudicator: str,
    artifact_root: str | Path,
    trusted_runtime_signer_public_key: str,
) -> dict[str, Any]:
    """Normalize, seal, score, and decide one runtime batch end to end."""
    from omc_plan_benchmark import score_plan
    from omc_plan_pilot import (
        normalize_adjudication_result,
        seal_blind_adjudications,
        validate_adjudication_result_contract,
    )

    validate_runtime_protocol(protocol)
    expected_count = protocol["acceptance"]["case_count"]
    validate_runtime_corpus(
        cases,
        gold_document,
        expected_count=expected_count,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )
    verify_runtime_attestation(
        provider_batch,
        blind_sessions,
        private_mapping,
        trusted_public_key=trusted_runtime_signer_public_key,
    )
    validate_runtime_provenance(
        provider_batch,
        protocol=protocol,
        cases=cases,
        gold_document=gold_document,
    )
    activation_probe = provider_batch.get("activation_probe")
    if (
        not isinstance(activation_probe, dict)
        or activation_probe.get("status") != "pass"
        or provider_batch.get("activation_probe_sha256")
        != canonical_digest(activation_probe)
    ):
        raise ValueError("runtime activation probe mismatch")
    executions = validate_runtime_executions(
        provider_batch.get("executions"), expected_case_count=expected_count
    )
    execution_identities = [
        (item.get("provider_id"), item.get("case_id"))
        for item in executions if isinstance(item, dict)
    ]
    expected_identities = {
        (provider_id, case["case_id"])
        for provider_id in PROVIDERS
        for case in cases
    }
    if (
        len(execution_identities) != len(set(execution_identities))
        or set(execution_identities) != expected_identities
    ):
        raise ValueError("runtime provider batch identities are invalid")
    sessions_by_id = {session.get("session_id"): session for session in blind_sessions}
    if len(sessions_by_id) != 5 or None in sessions_by_id:
        raise ValueError("runtime adjudication requires exactly 5 distinct sessions")
    if len(adjudication_results) != 5:
        raise ValueError("runtime adjudication results must cover exactly 5 sessions")
    normalized_results = []
    for result in adjudication_results:
        session = sessions_by_id.get(result.get("session_id"))
        if session is None:
            raise ValueError("runtime adjudication session is unknown")
        validate_adjudication_result_contract(result, session)
        normalized_results.append(normalize_adjudication_result(result, session))

    scoring_batch = {
        "schema_version": 1,
        "split": "holdout",
        "providers": [
            {
                "provider_id": provider_id,
                "plan_producer": f"runtime-{provider_id}",
                "executions": [
                    {
                        "case_id": item["case_id"],
                        "plan_execution_id": item["plan_execution_id"],
                        "plan": item["plan"],
                        "raw_output": item["raw_output"],
                    }
                    for item in executions
                    if item["provider_id"] == provider_id
                ],
            }
            for provider_id in PROVIDERS
        ],
    }
    sealed_batch = seal_blind_adjudications(
        scoring_batch,
        normalized_results,
        private_mapping,
        gold_document,
        private_key=adjudicator_private_key,
        trusted_public_key=trusted_adjudicator_public_key,
        adjudicator=adjudicator,
    )
    gold_by_id = {item["case_id"]: item for item in gold_document["cases"]}
    scores_by_provider: dict[str, list[dict[str, Any]]] = {}
    for provider in sealed_batch["providers"]:
        scores_by_provider[provider["provider_id"]] = [
            score_plan(
                execution["plan"],
                gold_by_id[execution["case_id"]],
                execution["semantic_adjudication"],
                trusted_adjudicator_public_keys={trusted_adjudicator_public_key},
                expected_plan_producer=provider["plan_producer"],
                expected_plan_execution_id=execution["plan_execution_id"],
                raw_output=execution["raw_output"],
            )
            for execution in provider["executions"]
        ]
    metrics = build_runtime_metrics(
        scores_by_provider, executions, expected_case_count=expected_count
    )
    decision = decide_superiority_batch(
        metrics,
        protocol["acceptance"],
        protocol["superiority"],
        batch_id=provider_batch.get("batch_id"),
    )
    report = {
        "schema_version": 1,
        "batch_id": provider_batch.get("batch_id"),
        "metrics": metrics,
        "decision": decision,
        "sealed_provider_batch_sha256": canonical_digest(sealed_batch),
        "provider_execution_evidence_sha256": provider_batch[
            "runtime_attestation"
        ]["provider_execution_evidence_sha256"],
        "execution_config": runtime_execution_config(provider_batch),
        "provenance": {
            "protocol_sha256": provider_batch.get("protocol_sha256"),
            "corpus_sha256": provider_batch.get("corpus_sha256"),
            "gold_sha256": provider_batch.get("gold_sha256"),
            "skill_sha256": provider_batch.get("skill_sha256"),
        },
    }
    report["final_report_attestation"] = build_final_report_attestation(
        report,
        private_key=adjudicator_private_key,
        signer_public_key=trusted_adjudicator_public_key,
    )
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "sealed-provider-batch.json").write_text(
        json.dumps(sealed_batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "runtime-final-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-corpus")
    validate_parser.add_argument("protocol")
    validate_parser.add_argument("cases")
    validate_parser.add_argument("gold")
    validate_parser.add_argument(
        "--trusted-gold-signer-public-key", action="append", required=True
    )

    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("protocol")
    assess_parser.add_argument("metrics")
    assess_parser.add_argument("--output")

    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("protocol")
    probe_parser.add_argument("--skill-file", required=True)
    probe_parser.add_argument("--model", required=True)
    probe_parser.add_argument("--reasoning-effort", default="low")
    probe_parser.add_argument("--codex-binary", default="codex")
    probe_parser.add_argument("--output-schema", required=True)
    probe_parser.add_argument("--artifact-root", required=True)

    batch_parser = subparsers.add_parser("run-batch")
    batch_parser.add_argument("protocol")
    batch_parser.add_argument("cases")
    batch_parser.add_argument("gold")
    batch_parser.add_argument("--trusted-gold-signer-public-key", action="append", required=True)
    batch_parser.add_argument("--skill-file", required=True)
    batch_parser.add_argument("--model", required=True)
    batch_parser.add_argument("--reasoning-effort", default="low")
    batch_parser.add_argument("--codex-binary", default="codex")
    batch_parser.add_argument("--output-schema", required=True)
    batch_parser.add_argument("--artifact-root", required=True)
    batch_parser.add_argument("--batch-id", required=True)
    batch_parser.add_argument("--runtime-signer-private-key-file", required=True)
    batch_parser.add_argument("--trusted-runtime-signer-public-key", required=True)
    batch_parser.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )

    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("protocol")
    finalize_parser.add_argument("cases")
    finalize_parser.add_argument("gold")
    finalize_parser.add_argument("provider_batch")
    finalize_parser.add_argument("blind_sessions")
    finalize_parser.add_argument("private_mapping")
    finalize_parser.add_argument("adjudication_results")
    finalize_parser.add_argument(
        "--trusted-gold-signer-public-key", action="append", required=True
    )
    finalize_parser.add_argument("--trusted-adjudicator-public-key", required=True)
    finalize_parser.add_argument("--trusted-runtime-signer-public-key", required=True)
    finalize_parser.add_argument("--adjudicator-private-key-file", required=True)
    finalize_parser.add_argument("--adjudicator", default="independent-codex-adjudicator")
    finalize_parser.add_argument("--artifact-root", required=True)
    finalize_parser.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )

    confirm_parser = subparsers.add_parser("confirm-superiority")
    confirm_parser.add_argument("reports", nargs=2)
    confirm_parser.add_argument(
        "--trusted-adjudicator-public-key", required=True
    )
    confirm_parser.add_argument("--output")

    args = parser.parse_args()
    if args.command == "confirm-superiority":
        result = decide_confirmed_superiority(
            [_load_json(path) for path in args.reports],
            required_batches=FROZEN_SUPERIORITY["required_confirmation_batches"],
            trusted_signer_public_key=args.trusted_adjudicator_public_key,
        )
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        print(payload)
        return 0

    protocol = load_runtime_protocol(args.protocol)
    if args.command == "validate-corpus":
        cases = _load_json(args.cases)
        gold = _load_json(args.gold)
        validate_runtime_corpus(
            cases["cases"],
            gold,
            expected_count=protocol["acceptance"]["case_count"],
            trusted_signer_public_keys=set(args.trusted_gold_signer_public_key),
        )
        print("runtime corpus valid")
        return 0
    if args.command == "assess":
        result = decide_replacement(_load_json(args.metrics), protocol["acceptance"])
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        print(payload)
        return 0
    if args.command == "probe":
        run_activation_probe(
            protocol=protocol,
            skill_path=args.skill_file,
            codex_binary=args.codex_binary,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            output_schema=args.output_schema,
            artifact_root=args.artifact_root,
        )
        print(Path(args.artifact_root).resolve() / "activation-probe.json")
        return 0
    if args.command == "finalize":
        from omc_plan_pilot import validate_private_key_location

        artifact_root = Path(args.artifact_root).resolve()
        private_key = validate_private_key_location(
            args.adjudicator_private_key_file,
            repo_root=args.repo_root,
            artifact_root=artifact_root,
            trusted_public_key=args.trusted_adjudicator_public_key,
        )
        cases = _load_json(args.cases)
        report = finalize_runtime_batch(
            protocol=protocol,
            cases=cases["cases"],
            gold_document=_load_json(args.gold),
            trusted_signer_public_keys=set(args.trusted_gold_signer_public_key),
            provider_batch=_load_json(args.provider_batch),
            blind_sessions=_load_json(args.blind_sessions),
            private_mapping=_load_json(args.private_mapping),
            adjudication_results=_load_json(args.adjudication_results),
            adjudicator_private_key=private_key,
            trusted_adjudicator_public_key=args.trusted_adjudicator_public_key,
            adjudicator=args.adjudicator,
            artifact_root=artifact_root,
            trusted_runtime_signer_public_key=args.trusted_runtime_signer_public_key,
        )
        print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
        return 0
    cases = _load_json(args.cases)
    gold = _load_json(args.gold)
    from omc_plan_pilot import validate_private_key_location

    artifact_root = Path(args.artifact_root).resolve()
    runtime_signer_private_key = validate_private_key_location(
        args.runtime_signer_private_key_file,
        repo_root=args.repo_root,
        artifact_root=artifact_root,
        trusted_public_key=args.trusted_runtime_signer_public_key,
    )
    run_runtime_batch(
        protocol=protocol,
        cases=cases["cases"],
        gold_document=gold,
        trusted_signer_public_keys=set(args.trusted_gold_signer_public_key),
        skill_path=args.skill_file,
        codex_binary=args.codex_binary,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        output_schema=args.output_schema,
        artifact_root=artifact_root,
        batch_id=args.batch_id,
        session_count=5,
        runtime_signer_private_key=runtime_signer_private_key,
        runtime_signer_public_key=args.trusted_runtime_signer_public_key,
        repo_root=args.repo_root,
    )
    print(Path(args.artifact_root).resolve() / "provider-batch.json")
    return 0


def evaluate_variability(
    runs: dict[str, list[dict[str, float]]], *, max_delta: float
) -> dict[str, Any]:
    failures: list[str] = []
    deltas: dict[str, dict[str, float]] = {}
    for provider_id in PROVIDERS:
        provider_runs = runs.get(provider_id, [])
        if len(provider_runs) < 2:
            failures.append(f"{provider_id}:insufficient_runs")
            continue
        metrics = set(provider_runs[0])
        if not metrics or any(set(item) != metrics for item in provider_runs):
            failures.append(f"{provider_id}:metric_mismatch")
            continue
        deltas[provider_id] = {}
        for metric in sorted(metrics):
            try:
                values = [float(item[metric]) for item in provider_runs]
            except (TypeError, ValueError):
                failures.append(f"{provider_id}:{metric}:invalid")
                continue
            if not all(math.isfinite(value) for value in values):
                failures.append(f"{provider_id}:{metric}:invalid")
                continue
            delta = max(values) - min(values)
            deltas[provider_id][metric] = delta
            if delta > max_delta:
                failures.append(f"{provider_id}:{metric}")
    return {
        "status": "blocked" if failures else "pass",
        "max_metric_delta": max_delta,
        "deltas": deltas,
        "failed_metrics": failures,
    }


def decide_replacement(
    metrics: dict[str, Any], acceptance: dict[str, Any]
) -> dict[str, Any]:
    """Apply a frozen non-inferiority and cost gate without subjective overrides."""
    expected_count = acceptance["case_count"]
    if (
        metrics.get("valid_case_count") != expected_count
        or metrics.get("provenance_complete_count") != expected_count
    ):
        return {
            "decision": "INVALID_RUN",
            "reason_code": "provenance_invalid",
            "failed_gates": ["valid_cases_or_provenance"],
        }
    baseline = metrics.get("baseline-plan")
    omc = metrics.get("omc-plan")
    if not isinstance(baseline, dict) or not isinstance(omc, dict):
        return {
            "decision": "INVALID_RUN",
            "reason_code": "provider_metrics_missing",
            "failed_gates": ["provider_metrics"],
        }
    if not _valid_provider_metrics(baseline) or not _valid_provider_metrics(omc):
        return {
            "decision": "INVALID_RUN",
            "reason_code": "provider_metrics_invalid",
            "failed_gates": ["provider_metrics"],
        }

    failed: list[str] = []
    if omc["weighted_requirement_recall"] < baseline["weighted_requirement_recall"]:
        failed.append("weighted_requirement_recall")
    if omc["critical_omission_count"] != 0 or omc["critical_omission_count"] > baseline["critical_omission_count"]:
        failed.append("critical_omissions")
    if (
        omc["executable_task_rate"] < baseline["executable_task_rate"]
        or omc["executable_task_rate"] < acceptance["minimum_executable_task_rate"]
    ):
        failed.append("executable_task_rate")
    if omc["unsupported_assumption_count"] > baseline["unsupported_assumption_count"]:
        failed.append("unsupported_assumptions")
    if omc["task_evidence_accuracy"] < baseline["task_evidence_accuracy"]:
        failed.append("task_evidence_accuracy")
    if omc["output_tokens"] > baseline["output_tokens"] * acceptance["maximum_output_token_ratio"]:
        failed.append("output_bloat")

    if metrics.get("token_measurement_status") != "observed":
        failed.append("total_tokens")
    elif omc["total_tokens"] > baseline["total_tokens"]:
        increase_ratio = (omc["total_tokens"] - baseline["total_tokens"]) / baseline["total_tokens"]
        quality_gain = max(
            omc["weighted_requirement_recall"] - baseline["weighted_requirement_recall"],
            omc["executable_task_rate"] - baseline["executable_task_rate"],
        )
        if (
            increase_ratio > acceptance["maximum_total_token_increase_ratio"]
            or quality_gain < acceptance["minimum_quality_gain_for_token_increase"]
        ):
            failed.append("total_tokens")

    return {
        "decision": "NOT_PROVEN" if failed else "REPLACEABLE",
        "reason_code": "quality_or_cost_gate_failed" if failed else "all_gates_passed",
        "failed_gates": sorted(set(failed)),
    }


def decide_superiority_batch(
    metrics: dict[str, Any],
    acceptance: dict[str, Any],
    superiority: dict[str, Any],
    *,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Classify one frozen batch without promoting it to final superiority."""
    if superiority != FROZEN_SUPERIORITY:
        raise ValueError("superiority thresholds must match the frozen contract")
    replacement = decide_replacement(metrics, acceptance)
    if replacement["decision"] != "REPLACEABLE":
        return {**replacement, "batch_id": batch_id}

    deltas = metrics.get("paired_primary_deltas")
    expected_count = acceptance["case_count"]
    if (
        not isinstance(deltas, list)
        or len(deltas) != expected_count
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -1 <= value <= 1
            for value in deltas
        )
    ):
        return {
            "decision": "INVALID_RUN",
            "reason_code": "paired_primary_metrics_invalid",
            "failed_gates": ["paired_primary_metrics"],
            "batch_id": batch_id,
        }

    primary_gain = sum(deltas) / len(deltas)
    aggregate_gain = (
        metrics["omc-plan"][superiority["primary_metric"]]
        - metrics["baseline-plan"][superiority["primary_metric"]]
    )
    if not math.isclose(primary_gain, aggregate_gain, rel_tol=1e-9, abs_tol=1e-12):
        return {
            "decision": "INVALID_RUN",
            "reason_code": "paired_primary_metrics_mismatch",
            "failed_gates": ["paired_primary_metrics"],
            "batch_id": batch_id,
        }
    lower_bound = _deterministic_bootstrap_lower_bound(
        deltas,
        confidence_level=superiority["confidence_level"],
        iterations=superiority["bootstrap_iterations"],
        seed=superiority["bootstrap_seed"],
    )
    is_candidate = (
        primary_gain + 1e-12 >= superiority["minimum_primary_gain"]
        and lower_bound > 0
    )
    return {
        "decision": "SUPERIOR_CANDIDATE" if is_candidate else "REPLACEABLE",
        "reason_code": (
            "primary_superiority_candidate"
            if is_candidate
            else "noninferior_without_primary_superiority"
        ),
        "failed_gates": [] if is_candidate else ["primary_superiority"],
        "batch_id": batch_id,
        "primary_metric": superiority["primary_metric"],
        "primary_gain": primary_gain,
        "confidence_lower_bound": lower_bound,
    }


def decide_confirmed_superiority(
    batch_reports: list[dict[str, Any]],
    *,
    required_batches: int,
    trusted_signer_public_key: str,
) -> dict[str, Any]:
    """Require distinct candidate and confirmation batches for certification."""
    if required_batches != FROZEN_SUPERIORITY["required_confirmation_batches"]:
        raise ValueError("required superiority batch count is not frozen")
    if len(batch_reports) != required_batches:
        raise ValueError("superiority requires every confirmation batch")
    for report in batch_reports:
        verify_final_report_attestation(
            report, trusted_public_key=trusted_signer_public_key
        )

    batch_ids = [report.get("batch_id") for report in batch_reports]
    if any(not isinstance(batch_id, str) or not batch_id for batch_id in batch_ids):
        raise ValueError("superiority batches require independent identities")
    if len(set(batch_ids)) != required_batches:
        raise ValueError("superiority batches must be independent")

    artifact_hashes = [
        report.get("sealed_provider_batch_sha256") for report in batch_reports
    ]
    if any(not _is_sha256(value) for value in artifact_hashes):
        raise ValueError("superiority artifact hashes are invalid")
    if len(set(artifact_hashes)) != required_batches:
        raise ValueError("superiority artifact hashes must be independent")

    execution_hashes = [
        report.get("provider_execution_evidence_sha256") for report in batch_reports
    ]
    if any(not _is_sha256(value) for value in execution_hashes):
        raise ValueError("superiority provider execution evidence is invalid")
    if len(set(execution_hashes)) != required_batches:
        raise ValueError("superiority provider execution evidence must be independent")

    execution_configs = [report.get("execution_config") for report in batch_reports]
    expected_config_fields = {"model", "reasoning_effort"}
    if any(
        not isinstance(config, dict)
        or set(config) != expected_config_fields
        or any(
            not isinstance(config[field], str) or not config[field]
            for field in expected_config_fields
        )
        for config in execution_configs
    ):
        raise ValueError("superiority execution config is invalid")
    if any(config != execution_configs[0] for config in execution_configs[1:]):
        raise ValueError("superiority execution config does not match")

    provenance_fields = {
        "protocol_sha256",
        "corpus_sha256",
        "gold_sha256",
        "skill_sha256",
    }
    provenances = [report.get("provenance") for report in batch_reports]
    if any(
        not isinstance(provenance, dict)
        or set(provenance) != provenance_fields
        or any(not _is_sha256(provenance[field]) for field in provenance_fields)
        for provenance in provenances
    ):
        raise ValueError("superiority frozen inputs are invalid")
    if any(provenance != provenances[0] for provenance in provenances[1:]):
        raise ValueError("superiority frozen inputs do not match")

    decision_payloads = [report.get("decision") for report in batch_reports]
    if any(
        not isinstance(decision, dict)
        or decision.get("batch_id") != batch_id
        for decision, batch_id in zip(decision_payloads, batch_ids, strict=True)
    ):
        raise ValueError("superiority batch decision provenance is invalid")

    decisions = [decision.get("decision") for decision in decision_payloads]
    if any(decision == "INVALID_RUN" for decision in decisions):
        final = "INVALID_RUN"
        reason = "invalid_confirmation_batch"
    elif any(decision == "NOT_PROVEN" for decision in decisions):
        final = "NOT_PROVEN"
        reason = "quality_or_cost_gate_failed"
    elif all(decision == "SUPERIOR_CANDIDATE" for decision in decisions):
        final = "BENCHMARK_SUPERIOR"
        reason = "superiority_reproduced"
    elif all(decision in {"SUPERIOR_CANDIDATE", "REPLACEABLE"} for decision in decisions):
        final = "REPLACEABLE"
        reason = "superiority_not_reproduced"
    else:
        raise ValueError("superiority batch decision is invalid")
    return {
        "decision": final,
        "reason_code": reason,
        "batch_ids": batch_ids,
        "sealed_provider_batch_sha256": artifact_hashes,
        "provider_execution_evidence_sha256": execution_hashes,
        "execution_config": execution_configs[0],
    }


def _deterministic_bootstrap_lower_bound(
    values: list[float], *, confidence_level: float, iterations: int, seed: int
) -> float:
    """Return a reproducible one-sided bootstrap lower confidence bound."""
    means = []
    size = len(values)
    for iteration in range(iterations):
        sample = []
        for draw in range(size):
            digest = hashlib.sha256(f"{seed}:{iteration}:{draw}".encode()).digest()
            sample.append(values[int.from_bytes(digest[:8], "big") % size])
        means.append(sum(sample) / size)
    means.sort()
    lower_index = max(0, math.floor((1 - confidence_level) * iterations) - 1)
    return means[lower_index]


def _valid_provider_metrics(metrics: dict[str, Any]) -> bool:
    rate_fields = {
        "weighted_requirement_recall",
        "executable_task_rate",
        "task_evidence_accuracy",
    }
    count_fields = {"critical_omission_count", "unsupported_assumption_count"}
    token_fields = {"output_tokens", "total_tokens"}
    if not (rate_fields | count_fields | token_fields) <= set(metrics):
        return False
    for field in rate_fields:
        value = metrics[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            return False
    for field in count_fields | token_fields:
        value = metrics[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    return metrics["total_tokens"] > 0


if __name__ == "__main__":
    raise SystemExit(main())
