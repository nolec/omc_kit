#!/usr/bin/env python3
"""Validate actual Codex Agent Skill runtime before claiming Plan replacement."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
from collections import Counter
from copy import deepcopy
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
    "confirmatory",
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
EVALUATION_SCOPES = {
    "confirmatory",
    "diagnostic_posthoc_gold_amendment",
}
CONFIRMATORY_CLAIM_SCOPE = "single_confirmatory_corpus"
FROZEN_CONFIRMATORY_BUDGET = {
    "observed_total_token_stop_threshold": 1_200_000,
    "maximum_external_calls": 30,
}
FROZEN_CONFIRMATORY_PROTOCOL = {
    "manifest_required": True,
    "claim_scope": CONFIRMATORY_CLAIM_SCOPE,
    **FROZEN_CONFIRMATORY_BUDGET,
}
FROZEN_CONFIRMATORY_SURFACE_COUNTS = {
    "ui_state": 2,
    "api_payload": 2,
    "data_indexing": 2,
    "backend_rules": 2,
    "multi_file_legacy": 2,
}
FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS = {
    "low": 3,
    "medium": 4,
    "high": 3,
}
FROZEN_CONFIRMATORY_MAX_SELECTED_OBJECT_CASES = 2
CONFIRMATORY_ANONYMIZATION_POLICY = {
    "schema_version": 1,
    "path_strategy": "ordered-generic-label-with-extension",
    "content_strategy": "fixed-sensitive-token-redaction",
    "request_strategy": "fixed-sensitive-token-redaction",
    "source": "baseline-only-transfer-readiness",
}
GOLD_EVIDENCE_PRIVACY_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(
        r"(?i)\b(?:[a-z0-9]+[_-])*"
        r"(?:api[_-]?key|access[_-]?token|token|secret|password|private[_-]?key)\b"
        r"\s*[:=]\s*(?:[\"'][^\"'\r\n]{8,}[\"']|"
        r"[^\s\"']{8,}(?=\s|$))"
    ),
    "bearer_token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "local_user_path": re.compile(r"(?:/Users|/home)/[^\s\"']+"),
    "url": re.compile(r"https?://[^\s\"'<>]+"),
    "email": re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    "product_identifier": re.compile(r"(?i)sixshop|식스샵"),
}
MAX_CONTEXT_FILE_COUNT = 20
MAX_CONTEXT_FILE_BYTES = 128 * 1024
MAX_CONTEXT_TOTAL_BYTES = 512 * 1024
RUNTIME_CASE_FIELDS = {
    "case_id",
    "split",
    "source_type",
    "request",
    "provenance",
    "context_files",
    "context_sha256",
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
            "provider_input_sha256": execution.get("provider_input_sha256"),
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


def confirmatory_manifest_signoff_payload(manifest: dict[str, Any]) -> bytes:
    """Return the immutable confirmatory selection claim for signing."""
    signoff = manifest.get("signoff")
    if not isinstance(signoff, dict):
        raise ValueError("confirmatory manifest signoff is required")
    payload = {
        key: value for key, value in manifest.items() if key != "signoff"
    }
    payload["signoff"] = {
        "signer": signoff.get("signer"),
        "signer_public_key": signoff.get("signer_public_key"),
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _case_fingerprint(case: dict[str, Any]) -> dict[str, str]:
    return {
        "case_id": case.get("case_id"),
        "source_sha256": case.get("provenance", {}).get("source_sha256"),
        "context_sha256": case.get("context_sha256"),
    }


def confirmatory_external_payload_digest(
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
    skill_sha256: str,
) -> str:
    if not _is_sha256(skill_sha256):
        raise ValueError("confirmatory skill hash is invalid")
    return canonical_digest({
        "schema_version": 1,
        "cases": cases,
        "gold": gold_document,
        "skill_sha256": skill_sha256,
    })


def _without_digest(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _confirmatory_anonymization_policy_sha256() -> str:
    return canonical_digest(CONFIRMATORY_ANONYMIZATION_POLICY)


def _scrub_confirmatory_text(value: str) -> str:
    value = re.sub(
        r"/(?:Users|home)/[^\s\"'<>]+",
        "<LOCAL_PATH>",
        value,
    )
    value = re.sub(r"https?://[^\s\"'<>]+", "<URL>", value)
    value = re.sub(
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        "<EMAIL>",
        value,
    )
    value = re.sub(r"AKIA[0-9A-Z]{16}", "<REDACTED_KEY>", value)
    return re.sub(r"(?i)sixshop|식스샵", "<PRODUCT>", value)


def _anonymized_context_path(path: str, index: int) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".txt"
    return f"context/file-{index:02d}{suffix}"


def _anonymize_confirmatory_case(case: dict[str, Any]) -> dict[str, Any]:
    source_context = case["context_files"]
    ordered_paths = sorted(source_context)
    aliases = {
        path: _anonymized_context_path(path, index)
        for index, path in enumerate(ordered_paths, 1)
    }
    anonymized_context: dict[str, str] = {}
    replacement_paths = sorted(aliases, key=lambda value: (-len(value), value))
    for path in ordered_paths:
        content = source_context[path]
        for source_path in replacement_paths:
            content = content.replace(source_path, aliases[source_path])
        anonymized_context[aliases[path]] = _scrub_confirmatory_text(content)
    return {
        "case_id": case["case_id"],
        "split": case["split"],
        "source_type": "observed_anonymized",
        "request": _scrub_confirmatory_text(case["request"]),
        "provenance": {
            "source_sha256": case["context_sha256"],
            "anonymization_reviewed": True,
            "approved": True,
        },
        "context_files": anonymized_context,
        "context_sha256": canonical_digest(anonymized_context),
    }


def _readiness_projection_public_corpus(
    readiness: dict[str, Any],
) -> dict[str, Any]:
    bundle = readiness.get("transfer_bundle")
    bundle_cases = bundle.get("cases") if isinstance(bundle, dict) else None
    if not isinstance(bundle_cases, list):
        raise ValueError("local transfer readiness cases are required")
    cases = []
    for item in bundle_cases:
        if not isinstance(item, dict) or not isinstance(item.get("files"), list):
            raise ValueError("local transfer readiness cases are invalid")
        context_files = {}
        for source in item["files"]:
            if not isinstance(source, dict):
                raise ValueError("local transfer readiness files are invalid")
            path = source.get("relative_path")
            content = source.get("content_utf8")
            if (
                not isinstance(path, str)
                or not isinstance(content, str)
                or path in context_files
            ):
                raise ValueError("local transfer readiness files are invalid")
            context_files[path] = content
        request = item.get("request")
        if not isinstance(request, str):
            raise ValueError("local transfer readiness request is invalid")
        cases.append({
            "case_id": item.get("case_id"),
            "split": "holdout",
            "source_type": "observed_anonymized",
            "task_type": "development",
            "request": request,
            "context_sha256": canonical_digest(context_files),
        })
    corpus = {"schema_version": 1, "status": "frozen", "cases": cases}
    corpus["corpus_sha256"] = canonical_digest(cases)
    return corpus


def prepare_confirmatory_gold_author_payload(
    *,
    readiness: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    """Freeze an anonymized baseline-only corpus for independent gold authoring."""
    raw_cases = _runtime_cases_from_transfer_readiness(
        readiness,
        _readiness_projection_public_corpus(readiness),
    )
    labels = selection.get("cases")
    policy = selection.get("selection_policy")
    if not isinstance(labels, list) or not isinstance(policy, dict):
        raise ValueError("confirmatory selection contract is invalid")
    label_by_id = {item.get("case_id"): item for item in labels if isinstance(item, dict)}
    if len(label_by_id) != len(raw_cases) or set(label_by_id) != {
        case["case_id"] for case in raw_cases
    }:
        raise ValueError("confirmatory selection and readiness cases do not match")

    runtime_cases = [_anonymize_confirmatory_case(case) for case in raw_cases]
    semantic_contract = {
        "required_surface_counts": policy.get("required_surface_counts"),
        "required_ambiguity_counts": policy.get("required_ambiguity_counts"),
        "maximum_selected_object_cases": policy.get(
            "maximum_selected_object_cases"
        ),
        "case_labels": [
            {
                "case_id": case["case_id"],
                "surface": label_by_id[case["case_id"]].get("surface"),
                "ambiguity": label_by_id[case["case_id"]].get("ambiguity"),
                "selected_object": label_by_id[case["case_id"]].get(
                    "selected_object"
                ),
            }
            for case in runtime_cases
        ],
    }
    _validate_confirmatory_semantic_contract(
        semantic_contract,
        cases=runtime_cases,
    )
    policy_sha256 = _confirmatory_anonymization_policy_sha256()
    public_cases = [
        {
            "case_id": case["case_id"],
            "split": case["split"],
            "source_type": case["source_type"],
            "task_type": label_by_id[case["case_id"]].get("surface"),
            "request": case["request"],
            "context_sha256": case["context_sha256"],
        }
        for case in runtime_cases
    ]
    public_corpus = {
        "schema_version": 1,
        "status": "frozen",
        "anonymization_policy_sha256": policy_sha256,
        "cases": public_cases,
        "corpus_sha256": canonical_digest(public_cases),
    }
    runtime_corpus = {
        "schema_version": 1,
        "status": "frozen",
        "source_corpus_sha256": public_corpus["corpus_sha256"],
        "cases": runtime_cases,
        "corpus_sha256": canonical_digest(runtime_cases),
    }
    author_cases = [
        {
            "case_id": case["case_id"],
            "request": case["request"],
            "task_type": label_by_id[case["case_id"]].get("surface"),
            "ambiguity": label_by_id[case["case_id"]].get("ambiguity"),
            "context_files": case["context_files"],
        }
        for case in runtime_cases
    ]
    author_packet = {
        "schema_version": 1,
        "purpose": "independent fresh Batch A baseline-only gold authoring",
        "provider_outputs_available": False,
        "anonymization_policy_sha256": policy_sha256,
        "review_rules": [
            "Derive requirements only from the request and supplied baseline context.",
            "Do not infer behavior from a follow-up implementation or provider output.",
            "Return exactly one gold case for every input case in input order.",
        ],
        "cases": author_cases,
    }
    result = {
        "schema_version": 1,
        "status": "approval_required",
        "source_readiness_sha256": readiness.get("readiness_sha256"),
        "anonymization_policy": deepcopy(CONFIRMATORY_ANONYMIZATION_POLICY),
        "anonymization_policy_sha256": policy_sha256,
        "public_corpus": public_corpus,
        "runtime_corpus": runtime_corpus,
        "gold_author_packet": author_packet,
        "external_payload_sha256": canonical_digest(author_packet),
        "provider_execution_allowed": False,
    }
    result["payload_bundle_sha256"] = canonical_digest(result)
    return result


def _write_json_file(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_evidence_file(root: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("gold evidence file path is invalid")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    if resolved == resolved_root or not _path_is_within(resolved, resolved_root):
        raise ValueError("gold evidence file must be inside the artifact root")
    return resolved


def _validate_gold_author_payload(payload: dict[str, Any]) -> None:
    public_corpus = payload.get("public_corpus")
    runtime_corpus = payload.get("runtime_corpus")
    author_packet = payload.get("gold_author_packet")
    policy = payload.get("anonymization_policy")
    if (
        payload.get("status") != "approval_required"
        or payload.get("provider_execution_allowed") is not False
        or not isinstance(public_corpus, dict)
        or not isinstance(runtime_corpus, dict)
        or not isinstance(author_packet, dict)
        or not isinstance(policy, dict)
    ):
        raise ValueError("gold author payload contract is invalid")
    public_cases = public_corpus.get("cases")
    runtime_cases = runtime_corpus.get("cases")
    author_cases = author_packet.get("cases")
    if not all(isinstance(value, list) for value in (
        public_cases,
        runtime_cases,
        author_cases,
    )):
        raise ValueError("gold author payload cases are invalid")
    if (
        len(public_cases) != FROZEN_ACCEPTANCE["case_count"]
        or len(runtime_cases) != len(public_cases)
        or len(author_cases) != len(public_cases)
        or payload.get("external_payload_sha256") != canonical_digest(author_packet)
        or public_corpus.get("corpus_sha256") != canonical_digest(public_cases)
        or runtime_corpus.get("corpus_sha256") != canonical_digest(runtime_cases)
        or runtime_corpus.get("source_corpus_sha256")
        != public_corpus.get("corpus_sha256")
        or payload.get("anonymization_policy_sha256") != canonical_digest(policy)
        or payload.get("payload_bundle_sha256")
        != canonical_digest(_without_digest(payload, "payload_bundle_sha256"))
        or not _is_sha256(payload.get("source_readiness_sha256"))
    ):
        raise ValueError("gold author payload hash mismatch")

    public_by_id = {case.get("case_id"): case for case in public_cases}
    runtime_by_id = {case.get("case_id"): case for case in runtime_cases}
    author_by_id = {case.get("case_id"): case for case in author_cases}
    if not (
        len(public_by_id)
        == len(runtime_by_id)
        == len(author_by_id)
        == len(public_cases)
        and set(public_by_id) == set(runtime_by_id) == set(author_by_id)
    ):
        raise ValueError("gold author payload case ids do not match")
    for case_id, runtime_case in runtime_by_id.items():
        context_files = runtime_case.get("context_files")
        if (
            not isinstance(context_files, dict)
            or public_by_id[case_id].get("request") != runtime_case.get("request")
            or author_by_id[case_id].get("request") != runtime_case.get("request")
            or author_by_id[case_id].get("context_files") != context_files
            or public_by_id[case_id].get("context_sha256")
            != canonical_digest(context_files)
        ):
            raise ValueError("gold author payload exact corpus mismatch")


def _gold_evidence_privacy_report(
    author_packet: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    case_inventory: list[dict[str, Any]] = []
    for case in author_packet["cases"]:
        case_id = case["case_id"]
        request = case["request"]
        context_files = case["context_files"]
        for code, pattern in GOLD_EVIDENCE_PRIVACY_PATTERNS.items():
            if pattern.search(request):
                findings.append({"case_id": case_id, "subject": "request", "code": code})
        files: list[dict[str, Any]] = []
        for path, content in sorted(context_files.items()):
            for code, pattern in GOLD_EVIDENCE_PRIVACY_PATTERNS.items():
                if pattern.search(path):
                    findings.append({"case_id": case_id, "subject": path, "code": code})
                if pattern.search(content):
                    findings.append({"case_id": case_id, "subject": path, "code": code})
            files.append({
                "path": path,
                "byte_size": len(content.encode("utf-8")),
            })
        case_inventory.append({
            "case_id": case_id,
            "files": files,
            "byte_size": sum(item["byte_size"] for item in files),
        })
    counts = {
        code: sum(item["code"] == code for item in findings)
        for code in GOLD_EVIDENCE_PRIVACY_PATTERNS
    }
    return ({
        "schema_version": 1,
        "status": "passed" if not findings else "failed",
        "scanner": "gold-evidence-fixed-patterns-v1",
        "finding_counts": counts,
        "case_inventory": case_inventory,
    }, findings)


def _validate_durable_evidence_root(
    artifact_root: str | Path,
    *,
    repo_root: str | Path,
    system_temp_root: str | Path | None,
) -> Path:
    root = _validate_artifact_root(artifact_root, repo_root=repo_root)
    temporary = Path(
        tempfile.gettempdir() if system_temp_root is None else system_temp_root
    ).resolve()
    if _path_is_within(root, temporary):
        raise ValueError("gold evidence artifact root must not be temporary")
    if root.exists() and any(root.iterdir()):
        raise ValueError("gold evidence artifact root must be empty")
    return root


def prepare_gold_author_evidence(
    *,
    payload: dict[str, Any],
    artifact_root: str | Path,
    repo_root: str | Path,
    source_commit: str,
    _system_temp_root: str | Path | None = None,
) -> dict[str, Any]:
    """Persist the private author payload and publish a redacted approval anchor."""
    _validate_gold_author_payload(payload)
    if not _is_git_sha(source_commit):
        raise ValueError("gold evidence source commit is invalid")
    root = _validate_durable_evidence_root(
        artifact_root,
        repo_root=repo_root,
        system_temp_root=_system_temp_root,
    )
    privacy_report, findings = _gold_evidence_privacy_report(
        payload["gold_author_packet"]
    )
    if findings:
        raise ValueError("gold evidence privacy scan failed")

    context_file_count = sum(
        len(case["context_files"])
        for case in payload["runtime_corpus"]["cases"]
    )
    manifest = {
        "schema_version": 1,
        "status": "approval_required",
        "source_commit": source_commit,
        "approval_tuple": {
            "external_payload_sha256": payload["external_payload_sha256"],
            "payload_bundle_sha256": payload["payload_bundle_sha256"],
            "source_readiness_sha256": payload["source_readiness_sha256"],
            "public_corpus_sha256": payload["public_corpus"]["corpus_sha256"],
            "runtime_corpus_sha256": payload["runtime_corpus"]["corpus_sha256"],
            "anonymization_policy_sha256": payload["anonymization_policy_sha256"],
        },
        "private_payload_file": "gold-author-payload.private.json",
        "private_payload_file_sha256": "",
        "payload_byte_size": 0,
        "case_count": len(payload["runtime_corpus"]["cases"]),
        "context_file_count": context_file_count,
        "privacy_report": privacy_report,
        "provider_execution_allowed": False,
    }

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = root.parent / f".{root.name}.staging-{secrets.token_hex(8)}"
    if root.exists():
        root.rmdir()
    try:
        staging.mkdir()
        private_path = staging / manifest["private_payload_file"]
        _write_json_file(private_path, payload)
        manifest["private_payload_file_sha256"] = _file_sha256(private_path)
        manifest["payload_byte_size"] = private_path.stat().st_size
        manifest["manifest_sha256"] = canonical_digest(manifest)
        _write_json_file(
            staging / "gold-author-evidence-manifest.json",
            manifest,
        )
        staging.replace(root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _validate_gold_evidence_manifest(
    manifest: dict[str, Any],
    *,
    artifact_root: Path,
) -> dict[str, Any]:
    if manifest.get("manifest_sha256") != canonical_digest(
        _without_digest(manifest, "manifest_sha256")
    ):
        raise ValueError("gold evidence manifest hash mismatch")
    private_path = _resolve_evidence_file(
        artifact_root,
        manifest.get("private_payload_file"),
    )
    if (
        not private_path.is_file()
        or manifest.get("private_payload_file_sha256") != _file_sha256(private_path)
        or manifest.get("payload_byte_size") != private_path.stat().st_size
        or manifest.get("provider_execution_allowed") is not False
    ):
        raise ValueError("gold evidence private payload mismatch")
    payload = json.loads(private_path.read_text(encoding="utf-8"))
    _validate_gold_author_payload(payload)
    expected_tuple = {
        "external_payload_sha256": payload["external_payload_sha256"],
        "payload_bundle_sha256": payload["payload_bundle_sha256"],
        "source_readiness_sha256": payload["source_readiness_sha256"],
        "public_corpus_sha256": payload["public_corpus"]["corpus_sha256"],
        "runtime_corpus_sha256": payload["runtime_corpus"]["corpus_sha256"],
        "anonymization_policy_sha256": payload["anonymization_policy_sha256"],
    }
    if manifest.get("approval_tuple") != expected_tuple:
        raise ValueError("gold evidence approval tuple mismatch")
    return payload


def _validate_gold_evidence_raw_output(
    raw_output: str,
    *,
    phase: str,
    expected_case_ids: list[str],
    input_sha256: str,
) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{phase} raw output must be JSON") from exc
    expected_fields = {"cases"}
    if phase == "reviewer":
        expected_fields |= {"decision", "reviewed_author_output_sha256"}
    if not isinstance(parsed, dict) or set(parsed) != expected_fields:
        raise ValueError(f"{phase} raw output fields are invalid")
    cases = parsed.get("cases")
    if not isinstance(cases, list) or len(cases) != len(expected_case_ids):
        raise ValueError(f"{phase} raw output case count is invalid")
    actual_case_ids = [item.get("case_id") for item in cases if isinstance(item, dict)]
    if actual_case_ids != expected_case_ids or len(set(actual_case_ids)) != len(cases):
        raise ValueError(f"{phase} raw output case ids are invalid")
    for item in cases:
        _validate_runtime_gold_case(item)
    if phase == "reviewer" and (
        parsed.get("decision") not in {"approve", "revise"}
        or parsed.get("reviewed_author_output_sha256") != input_sha256
    ):
        raise ValueError("reviewer raw output decision contract is invalid")
    return parsed


def validate_gold_evidence_receipt_ledger(
    ledger: dict[str, Any],
    *,
    manifest: dict[str, Any],
    artifact_root: str | Path,
) -> None:
    root = Path(artifact_root).resolve()
    payload = _validate_gold_evidence_manifest(manifest, artifact_root=root)
    expected_case_ids = [
        case["case_id"] for case in payload["gold_author_packet"]["cases"]
    ]
    if ledger.get("ledger_sha256") != canonical_digest(
        _without_digest(ledger, "ledger_sha256")
    ):
        raise ValueError("gold evidence receipt ledger hash mismatch")
    if ledger.get("approval_manifest_sha256") != manifest.get("manifest_sha256"):
        raise ValueError("gold evidence receipt manifest mismatch")
    receipts = ledger.get("receipts")
    if not isinstance(receipts, list) or len(receipts) > 2:
        raise ValueError("gold evidence receipt sequence is invalid")
    expected_phases = ["author", "reviewer"][:len(receipts)]
    if [item.get("phase") for item in receipts] != expected_phases:
        raise ValueError("gold evidence receipt sequence is invalid")
    previous: dict[str, Any] | None = None
    for receipt in receipts:
        if receipt.get("receipt_sha256") != canonical_digest(
            _without_digest(receipt, "receipt_sha256")
        ):
            raise ValueError("gold evidence receipt hash mismatch")
        if receipt.get("approval_manifest_sha256") != manifest.get(
            "manifest_sha256"
        ):
            raise ValueError("gold evidence receipt approved manifest mismatch")
        raw_path = _resolve_evidence_file(root, receipt.get("raw_output_file"))
        if (
            not raw_path.is_file()
            or receipt.get("raw_output_sha256") != _file_sha256(raw_path)
        ):
            raise ValueError("gold evidence raw output mismatch")
        if previous is None:
            expected_input = manifest["approval_tuple"]["external_payload_sha256"]
        else:
            expected_input = previous["raw_output_sha256"]
            if (
                receipt.get("session_id") == previous.get("session_id")
                or receipt.get("session_nonce") == previous.get("session_nonce")
            ):
                raise ValueError("gold evidence requires an independent session")
        if receipt.get("input_sha256") != expected_input:
            raise ValueError(f"{receipt.get('phase')} input hash mismatch")
        _validate_gold_evidence_raw_output(
            raw_path.read_text(encoding="utf-8"),
            phase=receipt["phase"],
            expected_case_ids=expected_case_ids,
            input_sha256=receipt["input_sha256"],
        )
        previous = receipt


def record_gold_evidence_receipt(
    *,
    artifact_root: str | Path,
    phase: str,
    provider: str,
    session_id: str,
    session_nonce: str,
    approved_manifest_sha256: str,
    input_sha256: str,
    raw_output: str,
) -> dict[str, Any]:
    """Atomically append an author or reviewer receipt to the private evidence root."""
    root = Path(artifact_root).resolve()
    manifest_path = root / "gold-author-evidence-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("gold evidence manifest is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = _validate_gold_evidence_manifest(manifest, artifact_root=root)
    if (
        phase not in {"author", "reviewer"}
        or not all(
            isinstance(value, str) and value.strip()
            for value in (provider, session_id, session_nonce, raw_output)
        )
        or not _is_sha256(approved_manifest_sha256)
        or not _is_sha256(input_sha256)
    ):
        raise ValueError("gold evidence receipt contract is invalid")
    if approved_manifest_sha256 != manifest.get("manifest_sha256"):
        raise ValueError("gold evidence approved manifest mismatch")
    expected_case_ids = [
        case["case_id"] for case in payload["gold_author_packet"]["cases"]
    ]
    _validate_gold_evidence_raw_output(
        raw_output,
        phase=phase,
        expected_case_ids=expected_case_ids,
        input_sha256=input_sha256,
    )

    ledger_path = root / "gold-evidence-receipt-ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        validate_gold_evidence_receipt_ledger(
            ledger,
            manifest=manifest,
            artifact_root=root,
        )
    else:
        ledger = {
            "schema_version": 1,
            "approval_manifest_sha256": manifest["manifest_sha256"],
            "receipts": [],
        }
    receipts = ledger["receipts"]
    if phase != (["author", "reviewer"][len(receipts)] if len(receipts) < 2 else None):
        raise ValueError("gold evidence receipt sequence is invalid")
    if phase == "author":
        expected_input = manifest["approval_tuple"]["external_payload_sha256"]
    else:
        author = receipts[0]
        expected_input = author["raw_output_sha256"]
        if session_id == author["session_id"] or session_nonce == author["session_nonce"]:
            raise ValueError("gold evidence requires an independent session")
    if input_sha256 != expected_input:
        raise ValueError(f"{phase} input hash mismatch")

    raw_relative = f"{phase}-raw-output.private.json"
    raw_bytes = raw_output.encode("utf-8")
    receipt = {
        "schema_version": 1,
        "phase": phase,
        "provider": provider,
        "session_id": session_id,
        "session_nonce": session_nonce,
        "approval_manifest_sha256": approved_manifest_sha256,
        "input_sha256": input_sha256,
        "raw_output_file": raw_relative,
        "raw_output_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }
    receipt["receipt_sha256"] = canonical_digest(receipt)

    staging = root.parent / f".{root.name}.staging-{secrets.token_hex(8)}"
    backup = root.parent / f".{root.name}.backup-{secrets.token_hex(8)}"
    try:
        shutil.copytree(root, staging)
        (staging / raw_relative).write_bytes(raw_bytes)
        next_ledger = {
            "schema_version": 1,
            "approval_manifest_sha256": manifest["manifest_sha256"],
            "receipts": [*receipts, receipt],
        }
        next_ledger["ledger_sha256"] = canonical_digest(next_ledger)
        _write_json_file(
            staging / "gold-evidence-receipt-ledger.json",
            next_ledger,
        )
        root.replace(backup)
        try:
            staging.replace(root)
        except Exception:
            backup.replace(root)
            raise
        shutil.rmtree(backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if root.exists():
            shutil.rmtree(backup, ignore_errors=True)
    return receipt


def _runtime_cases_from_transfer_readiness(
    readiness: dict[str, Any], public_corpus: dict[str, Any]
) -> list[dict[str, Any]]:
    """Materialize executable cases while preserving the signed public corpus."""
    if (
        not isinstance(readiness, dict)
        or readiness.get("readiness_sha256")
        != canonical_digest(_without_digest(readiness, "readiness_sha256"))
    ):
        raise ValueError("local transfer readiness hash mismatch")
    if (
        readiness.get("status") != "approval_required"
        or readiness.get("external_transfer_approved") is not False
        or readiness.get("provider_execution_allowed") is not False
        or readiness.get("replacement_claim_eligible") is not False
    ):
        raise ValueError("local transfer readiness must remain pre-approval")

    bundle = readiness.get("transfer_bundle")
    manifest = readiness.get("transfer_manifest")
    audit = readiness.get("privacy_audit")
    if not all(isinstance(item, dict) for item in (bundle, manifest, audit)):
        raise ValueError("local transfer readiness artifacts are required")
    if bundle.get("bundle_sha256") != canonical_digest(
        _without_digest(bundle, "bundle_sha256")
    ):
        raise ValueError("local transfer bundle hash mismatch")
    if manifest.get("manifest_sha256") != canonical_digest(
        _without_digest(manifest, "manifest_sha256")
    ):
        raise ValueError("local transfer manifest hash mismatch")
    if audit.get("audit_sha256") != canonical_digest(
        _without_digest(audit, "audit_sha256")
    ):
        raise ValueError("local transfer privacy audit hash mismatch")
    if (
        manifest.get("transfer_bundle_sha256") != bundle["bundle_sha256"]
        or audit.get("transfer_manifest_sha256") != manifest["manifest_sha256"]
        or audit.get("finding_count") != len(audit.get("findings", []))
    ):
        raise ValueError("local transfer artifact chain mismatch")

    public_cases = public_corpus.get("cases")
    if (
        not isinstance(public_corpus, dict)
        or public_corpus.get("schema_version") != 1
        or public_corpus.get("status") != "frozen"
        or not isinstance(public_cases, list)
        or public_corpus.get("corpus_sha256") != canonical_digest(public_cases)
    ):
        raise ValueError("signed public corpus contract is invalid")
    bundle_cases = bundle.get("cases")
    manifest_cases = manifest.get("cases")
    if (
        not isinstance(bundle_cases, list)
        or not isinstance(manifest_cases, list)
        or len(bundle_cases) != len(public_cases)
        or len(manifest_cases) != len(public_cases)
    ):
        raise ValueError("local transfer case count mismatch")
    bundle_by_id = {case.get("case_id"): case for case in bundle_cases}
    manifest_by_id = {case.get("case_id"): case for case in manifest_cases}
    if len(bundle_by_id) != len(bundle_cases) or len(manifest_by_id) != len(manifest_cases):
        raise ValueError("local transfer case ids must be unique")

    anonymization_policy_sha256 = public_corpus.get(
        "anonymization_policy_sha256"
    )
    if (
        anonymization_policy_sha256 is not None
        and anonymization_policy_sha256
        != _confirmatory_anonymization_policy_sha256()
    ):
        raise ValueError("confirmatory anonymization policy mismatch")

    runtime_cases: list[dict[str, Any]] = []
    for public_case in public_cases:
        case_id = public_case.get("case_id")
        bundle_case = bundle_by_id.get(case_id)
        manifest_case = manifest_by_id.get(case_id)
        if not isinstance(bundle_case, dict) or not isinstance(manifest_case, dict):
            raise ValueError("public corpus and transfer cases do not match")
        request = bundle_case.get("request")
        if (
            public_case.get("split") != "holdout"
            or public_case.get("source_type") != "observed_anonymized"
            or not _is_sha256(public_case.get("context_sha256"))
            or not isinstance(request, str)
            or manifest_case.get("request_sha256") != _sha256_text(request)
        ):
            raise ValueError("public corpus and transfer case contract mismatch")
        files = bundle_case.get("files")
        manifest_files = manifest_case.get("files")
        if not isinstance(files, list) or not isinstance(manifest_files, list):
            raise ValueError("local transfer case files are required")
        included = {
            item.get("relative_path"): item
            for item in manifest_files
            if isinstance(item, dict)
            and item.get("transfer_disposition") == "included_text"
        }
        context_files: dict[str, str] = {}
        for item in files:
            if not isinstance(item, dict) or set(item) != {
                "relative_path", "content_utf8"
            }:
                raise ValueError("local transfer bundle file fields are invalid")
            path = item["relative_path"]
            content = item["content_utf8"]
            if path in context_files or path not in included:
                raise ValueError("local transfer included file mismatch")
            metadata = included[path]
            if (
                not isinstance(content, str)
                or metadata.get("blob_sha256") != _sha256_text(content)
                or metadata.get("byte_size") != len(content.encode("utf-8"))
            ):
                raise ValueError("local transfer file content hash mismatch")
            context_files[path] = content
        if set(context_files) != set(included):
            raise ValueError("local transfer included file set mismatch")
        source_context_sha256 = canonical_digest(context_files)
        runtime_case = {
            "case_id": case_id,
            "split": "holdout",
            "source_type": "observed_anonymized",
            "request": request,
            "provenance": {
                "source_sha256": source_context_sha256,
                "anonymization_reviewed": True,
                "approved": True,
            },
            "context_files": context_files,
            "context_sha256": source_context_sha256,
        }
        if anonymization_policy_sha256 is not None:
            runtime_case = _anonymize_confirmatory_case(runtime_case)
        if (
            runtime_case["request"] != public_case.get("request")
            or runtime_case["context_sha256"] != public_case["context_sha256"]
        ):
            raise ValueError("public corpus and runtime context hash mismatch")
        runtime_cases.append(runtime_case)
    return runtime_cases


def prepare_confirmatory_runtime_inputs(
    *,
    readiness: dict[str, Any],
    public_corpus: dict[str, Any],
    selection: dict[str, Any],
    gold_document: dict[str, Any],
    trusted_prior_fingerprints: list[dict[str, str]],
    skill_sha256: str,
    producer: str,
    author_session_id: str,
    reviewer_session_id: str,
    signer: str,
    signer_public_key: str,
    trusted_gold_signer_public_keys: set[str],
    approved_payload_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the exact runtime corpus and require approval before signing."""
    cases = _runtime_cases_from_transfer_readiness(readiness, public_corpus)
    _verify_gold_signoff(
        public_corpus["cases"],
        gold_document,
        trusted_signer_public_keys=trusted_gold_signer_public_keys,
    )
    if {item["case_id"] for item in gold_document["cases"]} != {
        item["case_id"] for item in cases
    }:
        raise ValueError("runtime bridge gold case ids do not match")
    if not _is_sha256(skill_sha256):
        raise ValueError("confirmatory skill hash is invalid")
    external_payload_sha256 = confirmatory_external_payload_digest(
        cases, gold_document, skill_sha256
    )
    runtime_corpus = {
        "schema_version": 1,
        "status": "frozen",
        "source_corpus_sha256": public_corpus["corpus_sha256"],
        "cases": cases,
        "corpus_sha256": canonical_digest(cases),
    }
    base = {
        "schema_version": 1,
        "status": "approval_required",
        "external_payload_sha256": external_payload_sha256,
        "runtime_corpus": runtime_corpus,
        "confirmatory_manifest": None,
        "provider_execution_allowed": False,
    }
    if approved_payload_sha256 is None:
        base["preparation_sha256"] = canonical_digest(base)
        return base
    if approved_payload_sha256 != external_payload_sha256:
        raise ValueError("confirmatory approved payload hash mismatch")

    policy = selection.get("selection_policy", {})
    labels = selection.get("cases")
    if not isinstance(labels, list):
        raise ValueError("confirmatory selection cases are required")
    manifest = {
        "schema_version": 4,
        "status": "signed_off",
        "producer": producer,
        "source_corpus_sha256": public_corpus["corpus_sha256"],
        "corpus_sha256": runtime_corpus["corpus_sha256"],
        "gold_sha256": gold_document["gold_sha256"],
        "runtime_runner_sha256": _runtime_runner_sha256(),
        "sampling": {
            "source_window": "preregistered-disjoint-selection",
            "eligibility_rule": "observed requests not used by prior evaluations",
            "ordering_rule": "frozen selection order",
            "sampling_frame_sha256": canonical_digest(labels),
            "semantic_contract": {
                "required_surface_counts": policy.get("required_surface_counts"),
                "required_ambiguity_counts": policy.get("required_ambiguity_counts"),
                "maximum_selected_object_cases": policy.get(
                    "maximum_selected_object_cases"
                ),
                "case_labels": [
                    {
                        "case_id": item.get("case_id"),
                        "surface": item.get("surface"),
                        "ambiguity": item.get("ambiguity"),
                        "selected_object": item.get("selected_object"),
                    }
                    for item in labels
                ],
            },
        },
        "prior_registry_sha256": canonical_digest(trusted_prior_fingerprints),
        "prior_fingerprints": deepcopy(trusted_prior_fingerprints),
        "selected_fingerprints": [_case_fingerprint(case) for case in cases],
        "gold_independence": {
            "author_session_id": author_session_id,
            "reviewer_session_id": reviewer_session_id,
            "provider_outputs_available": False,
        },
        "budget": deepcopy(FROZEN_CONFIRMATORY_BUDGET),
        "transmission": {
            "payload_sha256": external_payload_sha256,
            "approved": True,
        },
        "claim_scope": CONFIRMATORY_CLAIM_SCOPE,
        "signoff": {
            "signer": signer,
            "signer_public_key": signer_public_key,
            "signature": "",
        },
    }
    prepared = {
        **base,
        "status": "signature_required",
        "confirmatory_manifest": manifest,
    }
    prepared["preparation_sha256"] = canonical_digest(prepared)
    return prepared


def seal_confirmatory_runtime_inputs(
    preparation: dict[str, Any],
    *,
    signature: str,
    gold_document: dict[str, Any],
    trusted_prior_fingerprints: list[dict[str, str]],
    skill_sha256: str,
    trusted_gold_signer_public_keys: set[str],
    trusted_confirmatory_signer_public_keys: set[str],
) -> dict[str, Any]:
    """Attach the independent signature and emit an execution-ready receipt."""
    if preparation.get("preparation_sha256") != canonical_digest(
        _without_digest(preparation, "preparation_sha256")
    ):
        raise ValueError("confirmatory preparation hash mismatch")
    if preparation.get("status") != "signature_required":
        raise ValueError("confirmatory preparation is not ready for signature")
    manifest = deepcopy(preparation.get("confirmatory_manifest"))
    if not isinstance(manifest, dict):
        raise ValueError("confirmatory manifest is required")
    runtime_corpus = preparation.get("runtime_corpus")
    if not isinstance(runtime_corpus, dict):
        raise ValueError("confirmatory preparation envelope mismatch")
    cases = runtime_corpus.get("cases")
    if not isinstance(cases, list):
        raise ValueError("confirmatory preparation envelope mismatch")
    computed_corpus_sha256 = canonical_digest(cases)
    computed_payload_sha256 = confirmatory_external_payload_digest(
        cases, gold_document, skill_sha256
    )
    transmission = manifest.get("transmission")
    if (
        runtime_corpus.get("corpus_sha256") != computed_corpus_sha256
        or runtime_corpus.get("source_corpus_sha256")
        != manifest.get("source_corpus_sha256")
        or manifest.get("corpus_sha256") != computed_corpus_sha256
        or not isinstance(transmission, dict)
        or transmission.get("payload_sha256") != computed_payload_sha256
        or preparation.get("external_payload_sha256")
        != computed_payload_sha256
    ):
        raise ValueError("confirmatory preparation envelope mismatch")
    manifest["signoff"]["signature"] = signature
    validate_runtime_corpus(
        cases,
        gold_document,
        expected_count=FROZEN_ACCEPTANCE["case_count"],
        trusted_signer_public_keys=trusted_gold_signer_public_keys,
        signed_corpus_sha256=manifest["source_corpus_sha256"],
    )
    validate_confirmatory_manifest(
        manifest,
        cases=cases,
        gold_document=gold_document,
        trusted_prior_fingerprints=trusted_prior_fingerprints,
        trusted_signer_public_keys=trusted_confirmatory_signer_public_keys,
    )
    receipt = {
        "schema_version": 1,
        "status": "execution_ready",
        "runtime_corpus": runtime_corpus,
        "confirmatory_manifest": manifest,
        "external_payload_sha256": preparation["external_payload_sha256"],
        "provider_execution_allowed": True,
    }
    receipt["receipt_sha256"] = canonical_digest(receipt)
    return receipt


def _validate_fingerprint(value: Any, *, label: str) -> dict[str, str]:
    expected = {"case_id", "source_sha256", "context_sha256"}
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or not isinstance(value.get("case_id"), str)
        or not value["case_id"].strip()
        or not _is_sha256(value.get("source_sha256"))
        or not _is_sha256(value.get("context_sha256"))
    ):
        raise ValueError(f"confirmatory {label} fingerprint is invalid")
    return value


def _validate_confirmatory_semantic_contract(
    contract: Any,
    *,
    cases: list[dict[str, Any]],
) -> None:
    expected_fields = {
        "required_surface_counts",
        "required_ambiguity_counts",
        "maximum_selected_object_cases",
        "case_labels",
    }
    if not isinstance(contract, dict) or set(contract) != expected_fields:
        raise ValueError("confirmatory semantic contract is invalid")
    if contract["required_surface_counts"] != FROZEN_CONFIRMATORY_SURFACE_COUNTS:
        raise ValueError("confirmatory surface quota contract is invalid")
    if contract["required_ambiguity_counts"] != FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS:
        raise ValueError("confirmatory ambiguity quota contract is invalid")
    if (
        contract["maximum_selected_object_cases"]
        != FROZEN_CONFIRMATORY_MAX_SELECTED_OBJECT_CASES
    ):
        raise ValueError("confirmatory selected-object quota contract is invalid")

    labels = contract["case_labels"]
    if not isinstance(labels, list) or len(labels) != len(cases):
        raise ValueError("confirmatory semantic case labels are incomplete")
    expected_case_ids = [case.get("case_id") for case in cases]
    actual_case_ids: list[str] = []
    surfaces: list[str] = []
    ambiguities: list[str] = []
    selected_object_count = 0
    for label in labels:
        if not isinstance(label, dict) or set(label) != {
            "case_id",
            "surface",
            "ambiguity",
            "selected_object",
        }:
            raise ValueError("confirmatory semantic case label is invalid")
        case_id = label["case_id"]
        surface = label["surface"]
        ambiguity = label["ambiguity"]
        selected_object = label["selected_object"]
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("confirmatory semantic case id is invalid")
        if surface not in FROZEN_CONFIRMATORY_SURFACE_COUNTS:
            raise ValueError("confirmatory semantic surface is invalid")
        if ambiguity not in FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS:
            raise ValueError("confirmatory semantic ambiguity is invalid")
        if type(selected_object) is not bool:
            raise ValueError("confirmatory selected-object label is invalid")
        actual_case_ids.append(case_id)
        surfaces.append(surface)
        ambiguities.append(ambiguity)
        selected_object_count += int(selected_object)

    if actual_case_ids != expected_case_ids:
        raise ValueError("confirmatory semantic case labels do not match corpus")
    if dict(Counter(surfaces)) != FROZEN_CONFIRMATORY_SURFACE_COUNTS:
        raise ValueError("confirmatory surface quota is not satisfied")
    if dict(Counter(ambiguities)) != FROZEN_CONFIRMATORY_AMBIGUITY_COUNTS:
        raise ValueError("confirmatory ambiguity quota is not satisfied")
    if selected_object_count > FROZEN_CONFIRMATORY_MAX_SELECTED_OBJECT_CASES:
        raise ValueError("confirmatory selected-object quota is exceeded")


def _is_git_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_confirmatory_candidate_selection(
    selection: dict[str, Any],
    *,
    trusted_prior_commits: list[str],
) -> None:
    """Validate the frozen candidate set before corpus or gold construction."""
    expected_fields = {
        "schema_version",
        "status",
        "batch_id",
        "selection_policy",
        "cases",
        "selection_sha256",
    }
    if (
        not isinstance(selection, dict)
        or set(selection) != expected_fields
        or selection.get("schema_version") != 1
        or selection.get("status") != "preregistered"
        or not isinstance(selection.get("batch_id"), str)
        or not selection["batch_id"].strip()
    ):
        raise ValueError("confirmatory candidate selection fields are invalid")

    if (
        not isinstance(trusted_prior_commits, list)
        or not trusted_prior_commits
        or len(trusted_prior_commits) != len(set(trusted_prior_commits))
        or any(not _is_git_sha(commit) for commit in trusted_prior_commits)
    ):
        raise ValueError("confirmatory trusted prior commits are invalid")

    policy = selection["selection_policy"]
    expected_policy_fields = {
        "provider_outputs_available_during_selection",
        "prior_registry_sha256",
        "required_surface_counts",
        "required_ambiguity_counts",
        "maximum_selected_object_cases",
    }
    if not isinstance(policy, dict) or set(policy) != expected_policy_fields:
        raise ValueError("confirmatory candidate selection policy is invalid")
    if policy["provider_outputs_available_during_selection"] is not False:
        raise ValueError("confirmatory provider outputs must be unavailable during selection")
    if policy["prior_registry_sha256"] != canonical_digest(trusted_prior_commits):
        raise ValueError("confirmatory prior commit registry mismatch")

    cases = selection["cases"]
    if not isinstance(cases, list) or len(cases) != sum(
        FROZEN_CONFIRMATORY_SURFACE_COUNTS.values()
    ):
        raise ValueError("confirmatory candidate selection requires exactly 10 cases")
    if selection["selection_sha256"] != canonical_digest(cases):
        raise ValueError("confirmatory candidate selection hash mismatch")

    expected_case_fields = {
        "case_id",
        "repo_alias",
        "baseline_commit",
        "followup_commit",
        "request",
        "context_candidate_paths",
        "surface",
        "ambiguity",
        "selected_object",
    }
    case_ids: list[str] = []
    requests: list[str] = []
    followup_commits: list[str] = []
    context_path_keys: list[tuple[str, str]] = []
    semantic_labels: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != expected_case_fields:
            raise ValueError("confirmatory candidate case fields are invalid")
        case_id = case["case_id"]
        repo_alias = case["repo_alias"]
        request = case["request"]
        baseline_commit = case["baseline_commit"]
        followup_commit = case["followup_commit"]
        context_candidate_paths = case["context_candidate_paths"]
        if (
            not isinstance(case_id, str)
            or not case_id.strip()
            or not isinstance(repo_alias, str)
            or not repo_alias.strip()
            or not isinstance(request, str)
            or not request.strip()
            or not _is_git_sha(baseline_commit)
            or not _is_git_sha(followup_commit)
            or baseline_commit == followup_commit
        ):
            raise ValueError("confirmatory candidate case identity is invalid")
        if not isinstance(context_candidate_paths, list) or not context_candidate_paths:
            raise ValueError("confirmatory candidate context paths are required")
        for context_path in context_candidate_paths:
            if not isinstance(context_path, str) or not context_path.strip():
                raise ValueError("confirmatory candidate context path is invalid")
            path = PurePosixPath(context_path)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("confirmatory candidate context path is unsafe")
            context_path_keys.append((repo_alias, path.as_posix()))
        case_ids.append(case_id)
        requests.append(request)
        followup_commits.append(followup_commit)
        semantic_labels.append({
            "case_id": case_id,
            "surface": case["surface"],
            "ambiguity": case["ambiguity"],
            "selected_object": case["selected_object"],
        })

    for values, message in (
        (case_ids, "case ids"),
        (requests, "requests"),
        (followup_commits, "followup commits"),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"confirmatory candidate {message} must be unique")
    if set(followup_commits).intersection(trusted_prior_commits):
        raise ValueError("confirmatory prior commit overlap")
    baseline_commits = {case["baseline_commit"] for case in cases}
    if baseline_commits.intersection(followup_commits):
        raise ValueError("confirmatory selected cases contain chained commits")
    if len(context_path_keys) != len(set(context_path_keys)):
        raise ValueError("confirmatory candidate context path overlap")

    _validate_confirmatory_semantic_contract(
        {
            "required_surface_counts": policy["required_surface_counts"],
            "required_ambiguity_counts": policy["required_ambiguity_counts"],
            "maximum_selected_object_cases": policy[
                "maximum_selected_object_cases"
            ],
            "case_labels": semantic_labels,
        },
        cases=cases,
    )


def validate_confirmatory_manifest(
    manifest: dict[str, Any],
    *,
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
    trusted_prior_fingerprints: list[dict[str, str]],
    trusted_signer_public_keys: set[str],
) -> None:
    """Prove sampling, disjointness, and gold independence before execution."""
    expected_fields = {
        "schema_version",
        "status",
        "producer",
        "source_corpus_sha256",
        "corpus_sha256",
        "gold_sha256",
        "runtime_runner_sha256",
        "sampling",
        "prior_registry_sha256",
        "prior_fingerprints",
        "selected_fingerprints",
        "gold_independence",
        "budget",
        "transmission",
        "claim_scope",
        "signoff",
    }
    schema_version = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or set(manifest) != expected_fields
        or schema_version != 4
        or manifest.get("status") != "signed_off"
        or not isinstance(manifest.get("producer"), str)
        or not manifest["producer"].strip()
    ):
        raise ValueError("confirmatory manifest fields are invalid")
    if (
        manifest.get("source_corpus_sha256") != gold_document.get("corpus_sha256")
        or not _is_sha256(manifest.get("source_corpus_sha256"))
    ):
        raise ValueError("confirmatory source corpus hash mismatch")
    if manifest.get("runtime_runner_sha256") != _runtime_runner_sha256():
        raise ValueError("confirmatory runtime runner hash mismatch")
    if (
        manifest.get("corpus_sha256") != canonical_digest(cases)
        or manifest.get("gold_sha256") != gold_document.get("gold_sha256")
    ):
        raise ValueError("confirmatory manifest input hash mismatch")

    sampling = manifest.get("sampling")
    expected_sampling = {
        "source_window",
        "eligibility_rule",
        "ordering_rule",
        "sampling_frame_sha256",
        "semantic_contract",
    }
    if (
        not isinstance(sampling, dict)
        or set(sampling) != expected_sampling
        or any(
            not isinstance(sampling[field], str) or not sampling[field].strip()
            for field in expected_sampling
            - {"sampling_frame_sha256", "semantic_contract"}
        )
        or not _is_sha256(sampling.get("sampling_frame_sha256"))
    ):
        raise ValueError("confirmatory sampling contract is invalid")
    _validate_confirmatory_semantic_contract(
        sampling["semantic_contract"],
        cases=cases,
    )

    prior = manifest.get("prior_fingerprints")
    selected = manifest.get("selected_fingerprints")
    if not isinstance(prior, list) or not prior:
        raise ValueError("confirmatory prior fingerprints are required")
    if not isinstance(selected, list) or len(selected) != len(cases):
        raise ValueError("confirmatory selected fingerprints are incomplete")
    prior = [
        _validate_fingerprint(item, label="prior") for item in prior
    ]
    if not isinstance(trusted_prior_fingerprints, list) or not trusted_prior_fingerprints:
        raise ValueError("confirmatory trusted prior registry is required")
    trusted_prior = [
        _validate_fingerprint(item, label="trusted prior")
        for item in trusted_prior_fingerprints
    ]
    if (
        manifest.get("prior_registry_sha256") != canonical_digest(trusted_prior)
        or prior != trusted_prior
    ):
        raise ValueError("confirmatory prior registry mismatch")
    selected = [
        _validate_fingerprint(item, label="selected") for item in selected
    ]
    expected_selected = [_case_fingerprint(case) for case in cases]
    if selected != expected_selected:
        raise ValueError("confirmatory selected fingerprints do not match corpus")
    for field, message in (
        ("case_id", "case id overlap"),
        ("source_sha256", "source fingerprint overlap"),
        ("context_sha256", "context fingerprint overlap"),
    ):
        prior_values = {item[field] for item in prior}
        selected_values = [item[field] for item in selected]
        if field != "context_sha256" and len(selected_values) != len(set(selected_values)):
            raise ValueError(f"confirmatory selected {field} values must be unique")
        if prior_values.intersection(selected_values):
            raise ValueError(f"confirmatory {message}")

    independence = manifest.get("gold_independence")
    if not isinstance(independence, dict) or set(independence) != {
        "author_session_id",
        "reviewer_session_id",
        "provider_outputs_available",
    }:
        raise ValueError("confirmatory gold independence fields are invalid")
    author = independence.get("author_session_id")
    reviewer = independence.get("reviewer_session_id")
    if (
        not isinstance(author, str)
        or not author.strip()
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or author == reviewer
    ):
        raise ValueError("confirmatory gold sessions must be independent")
    if independence.get("provider_outputs_available") is not False:
        raise ValueError("confirmatory provider outputs must be unavailable")
    if manifest.get("budget") != FROZEN_CONFIRMATORY_BUDGET:
        raise ValueError("confirmatory execution budget must match the frozen contract")
    transmission = manifest.get("transmission")
    if (
        not isinstance(transmission, dict)
        or set(transmission) != {"payload_sha256", "approved"}
        or not _is_sha256(transmission.get("payload_sha256"))
        or transmission.get("approved") is not True
    ):
        raise ValueError("confirmatory external transmission is not approved")
    if manifest.get("claim_scope") != CONFIRMATORY_CLAIM_SCOPE:
        raise ValueError("confirmatory claim scope is invalid")

    signoff = manifest.get("signoff")
    if not isinstance(signoff, dict) or set(signoff) != {
        "signer",
        "signer_public_key",
        "signature",
    }:
        raise ValueError("confirmatory manifest signature fields are invalid")
    signer = signoff.get("signer")
    public_key_text = signoff.get("signer_public_key")
    if (
        not isinstance(signer, str)
        or not signer.strip()
        or signer.strip() == manifest["producer"].strip()
    ):
        raise ValueError("confirmatory manifest requires an independent signer")
    if public_key_text not in trusted_signer_public_keys:
        raise ValueError("confirmatory manifest signer is not trusted")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_text, validate=True)
        )
        signature = base64.b64decode(signoff["signature"], validate=True)
        public_key.verify(signature, confirmatory_manifest_signoff_payload(manifest))
    except Exception as exc:
        raise ValueError("confirmatory manifest signature mismatch") from exc


def new_execution_budget_state(budget: dict[str, int]) -> dict[str, int]:
    if (
        not isinstance(budget, dict)
        or set(budget) != set(FROZEN_CONFIRMATORY_BUDGET)
        or any(type(value) is not int or value <= 0 for value in budget.values())
    ):
        raise ValueError("confirmatory execution budget is invalid")
    return {
        **budget,
        "used_total_tokens": 0,
        "used_external_calls": 0,
    }


def observed_provider_token_reserve(
    provider_id: str,
    *,
    activation_probe: dict[str, Any],
    executions: list[dict[str, Any]],
) -> int:
    """Reserve the largest observed call for the same provider before another call."""
    if provider_id not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider_id}")
    candidates: list[dict[str, Any]] = []
    probe_executions = activation_probe.get("executions", {})
    if isinstance(probe_executions, dict) and isinstance(
        probe_executions.get(provider_id), dict
    ):
        candidates.append(probe_executions[provider_id])
    candidates.extend(
        execution
        for execution in executions
        if execution.get("provider_id") == provider_id
    )
    totals = [
        _validated_runtime_usage(candidate.get("usage"))["total_tokens"]
        for candidate in candidates
    ]
    return max(totals, default=0)


def assert_execution_budget_available(
    state: dict[str, int],
    *,
    required_external_calls: int = 1,
    required_token_reserve: int = 0,
    failure_receipt_path: str | Path | None = None,
    execution_id: str | None = None,
) -> None:
    """Enforce external-call and observed token reserve caps before execution."""
    if type(required_external_calls) is not int or required_external_calls <= 0:
        raise ValueError("confirmatory required external calls are invalid")
    if type(required_token_reserve) is not int or required_token_reserve < 0:
        raise ValueError("confirmatory required token reserve is invalid")
    remaining_calls = (
        state["maximum_external_calls"] - state["used_external_calls"]
    )
    if remaining_calls < required_external_calls:
        raise RuntimeError("confirmatory external call budget exhausted")
    if (
        state["used_total_tokens"] + required_token_reserve
        <= state["observed_total_token_stop_threshold"]
    ):
        return
    if failure_receipt_path is not None:
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("confirmatory budget failure execution id is required")
        receipt_path = Path(failure_receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "failed",
                    "reason_code": "projected_token_stop_threshold_exceeded",
                    "execution_id": execution_id,
                    "execution_budget_state": state,
                    "required_token_reserve": required_token_reserve,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    raise RuntimeError("confirmatory projected token reserve exhausted")


def validate_execution_budget_state(
    state: dict[str, int], *, expected_budget: dict[str, int]
) -> None:
    expected_fields = {
        *FROZEN_CONFIRMATORY_BUDGET,
        "used_total_tokens",
        "used_external_calls",
    }
    if (
        not isinstance(state, dict)
        or set(state) != expected_fields
        or {key: state.get(key) for key in FROZEN_CONFIRMATORY_BUDGET}
        != expected_budget
        or any(type(value) is not int or value < 0 for value in state.values())
        or state["observed_total_token_stop_threshold"] <= 0
        or state["maximum_external_calls"] <= 0
        or state["used_total_tokens"] > state["observed_total_token_stop_threshold"]
        or state["used_external_calls"] > state["maximum_external_calls"]
    ):
        raise ValueError("confirmatory execution budget state is invalid")


def consume_execution_budget(
    state: dict[str, int],
    execution: dict[str, Any],
    *,
    failure_receipt_path: str | Path | None = None,
    execution_id: str | None = None,
) -> None:
    """Record observed usage and stop the batch before any later execution."""
    if failure_receipt_path is not None and (
        not isinstance(execution_id, str) or not execution_id.strip()
    ):
        raise ValueError("confirmatory budget failure execution id is required")
    usage = _validated_runtime_usage(execution.get("usage"))
    if usage.get("status") != "observed":
        raise RuntimeError("confirmatory execution usage is unavailable")
    attempt_count = execution.get("activation", {}).get("attempt_count", 1)
    if type(attempt_count) is not int or attempt_count <= 0:
        raise RuntimeError("confirmatory execution attempt count is invalid")
    total_tokens = usage.get("total_tokens")
    if type(total_tokens) is not int or total_tokens < 0:
        raise RuntimeError("confirmatory execution token usage is invalid")
    next_calls = state["used_external_calls"] + attempt_count
    next_tokens = state["used_total_tokens"] + total_tokens
    state["used_external_calls"] = next_calls
    state["used_total_tokens"] = next_tokens
    reason_code = None
    message = None
    if next_calls > state["maximum_external_calls"]:
        reason_code = "external_call_budget_exceeded"
        message = "confirmatory external call budget exceeded"
    elif next_tokens > state["observed_total_token_stop_threshold"]:
        reason_code = "observed_token_stop_threshold_exceeded"
        message = "confirmatory observed total token stop threshold exceeded"
    if reason_code is None:
        return
    if failure_receipt_path is not None:
        receipt_path = Path(failure_receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "failed",
                    "reason_code": reason_code,
                    "execution_id": execution_id,
                    "execution_budget_state": state,
                    "usage": usage,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    raise RuntimeError(message)


def validate_execution_budget_evidence(
    reported_state: dict[str, int],
    *,
    activation_probe: dict[str, Any],
    executions: list[dict[str, Any]],
    expected_budget: dict[str, int],
) -> None:
    validate_execution_budget_state(
        reported_state, expected_budget=expected_budget
    )
    probe_executions = activation_probe.get("executions")
    if (
        not isinstance(probe_executions, dict)
        or set(probe_executions) != set(PROVIDERS)
    ):
        raise ValueError("confirmatory activation budget evidence is invalid")
    observed_state = new_execution_budget_state(expected_budget)
    try:
        for execution in probe_executions.values():
            consume_execution_budget(observed_state, execution)
        for execution in executions:
            consume_execution_budget(observed_state, execution)
    except (RuntimeError, ValueError) as exc:
        raise ValueError("confirmatory execution budget evidence is invalid") from exc
    if reported_state != observed_state:
        raise ValueError("confirmatory execution budget evidence mismatch")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime_runner_sha256() -> str:
    """Hash the exact runtime implementation bound by a confirmatory manifest."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


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
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
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
        "max_attempts",
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
    if activation["max_attempts"] != 2:
        raise ValueError("activation max_attempts must match the frozen contract")

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

    if protocol["confirmatory"] != FROZEN_CONFIRMATORY_PROTOCOL:
        raise ValueError("confirmatory contract must match the frozen protocol")

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
    signed_corpus_sha256: str | None = None,
) -> None:
    """Require observed, anonymized, repository-grounded and approved cases."""
    if not isinstance(gold_document, dict) or not isinstance(gold_document.get("cases"), list):
        raise ValueError("runtime gold document is required")
    gold = gold_document["cases"]
    if len(cases) != expected_count or len(gold) != expected_count:
        raise ValueError(f"runtime corpus requires exactly {expected_count} cases")
    case_ids: list[str] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != RUNTIME_CASE_FIELDS:
            raise ValueError("runtime case fields are invalid")
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
        if len(context_files) > MAX_CONTEXT_FILE_COUNT:
            raise ValueError("runtime context file count exceeds limit")
        total_context_bytes = 0
        for path, content in context_files.items():
            _safe_context_path(path)
            if not isinstance(content, str):
                raise ValueError("runtime context file content must be text")
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > MAX_CONTEXT_FILE_BYTES:
                raise ValueError("runtime context file size exceeds limit")
            total_context_bytes += content_bytes
        if total_context_bytes > MAX_CONTEXT_TOTAL_BYTES:
            raise ValueError("runtime context total size exceeds limit")
        if case.get("context_sha256") != canonical_digest(context_files):
            raise ValueError("runtime case context hash mismatch")
    if len(set(case_ids)) != expected_count:
        raise ValueError("runtime case ids must be unique")

    _verify_gold_signoff(
        cases,
        gold_document,
        trusted_signer_public_keys=trusted_signer_public_keys,
        signed_corpus_sha256=signed_corpus_sha256,
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
    signed_corpus_sha256: str | None = None,
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
    expected_corpus_sha256 = signed_corpus_sha256 or canonical_digest(cases)
    if gold_document.get("corpus_sha256") != expected_corpus_sha256:
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


def build_provider_prompt(
    provider_id: str,
    request: str,
    *,
    context_paths: tuple[str, ...] = (),
    context_files: dict[str, str] | None = None,
) -> str:
    receipt_instruction = (
        "The output schema may request runtime_activation_receipt. Return `unavailable` "
        "unless a loaded project skill explicitly provides a different exact value. "
    )
    normalized_context_paths = tuple(sorted(_safe_context_path(path) for path in context_paths))
    normalized_context_files = {
        _safe_context_path(path): content
        for path, content in (context_files or {}).items()
    }
    if tuple(sorted(normalized_context_files)) != normalized_context_paths:
        raise ValueError("provider prompt context paths do not match frozen context")
    context_payload = json.dumps(
        normalized_context_files,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    context_instruction = (
        "Frozen context is embedded below as canonical JSON. Treat each key as its "
        "relative path and its value as the complete file content. Do not run a shell "
        "command or call another tool; plan directly from this context. "
        f"<frozen_context>{context_payload}</frozen_context> "
        if normalized_context_files
        else "No context files were provided. Do not run a shell command or call another tool; "
    )
    if provider_id == "baseline-plan":
        return (
            receipt_instruction
            + context_instruction
            + "Produce an implementation plan for the request below. Do not modify files.\n\n"
            + request
        )
    if provider_id == "omc-plan":
        return (
            receipt_instruction
            + context_instruction
            + "apply the loaded skill and produce the implementation "
            + "plan immediately.\n\n"
            + "$omc-plan\n\n"
            + request
        )
    raise ValueError(f"unsupported provider: {provider_id}")


def build_provider_input_envelope(
    provider_id: str,
    case: dict[str, Any],
    *,
    allowed_workspace_delta: str,
    instrumented_skill_sha256: str,
) -> dict[str, Any]:
    """Bind a provider call to its exact request, context, prompt, and skill delta."""
    if provider_id not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider_id}")
    case_id = case.get("case_id")
    request = case.get("request")
    context_files = case.get("context_files")
    if (
        not isinstance(case_id, str)
        or not case_id.strip()
        or not isinstance(request, str)
        or not request.strip()
        or not isinstance(context_files, dict)
        or not context_files
        or any(not isinstance(content, str) for content in context_files.values())
    ):
        raise ValueError("provider input case is invalid")
    delta_path = _safe_context_path(allowed_workspace_delta)
    if not _is_sha256(instrumented_skill_sha256):
        raise ValueError("provider input skill hash is invalid")

    context_manifest = {
        _safe_context_path(path): _sha256_text(content)
        for path, content in context_files.items()
    }
    workspace = dict(context_manifest)
    provider_delta_sha256 = None
    if provider_id == "omc-plan":
        workspace[delta_path] = instrumented_skill_sha256
        provider_delta_sha256 = instrumented_skill_sha256
    context_paths = tuple(sorted(context_manifest))
    prompt_sha256 = _sha256_text(
        build_provider_prompt(
            provider_id,
            request,
            context_paths=context_paths,
            context_files=context_files,
        )
    )
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "provider_id": provider_id,
        "request_sha256": _sha256_text(request),
        "context_sha256": canonical_digest(context_files),
        "context_paths": list(context_paths),
        "workspace_manifest_sha256": canonical_digest(workspace),
        "allowed_workspace_delta": delta_path,
        "provider_delta_sha256": provider_delta_sha256,
        "prompt_sha256": prompt_sha256,
    }
    return {**payload, "provider_input_sha256": canonical_digest(payload)}


def validate_provider_workspace_manifest(
    actual_manifest: dict[str, str],
    provider_input: dict[str, Any],
) -> None:
    """Ensure the sealed input describes the workspace the provider will read."""
    expected_sha256 = provider_input.get("workspace_manifest_sha256")
    if (
        not _is_sha256(expected_sha256)
        or canonical_digest(actual_manifest) != expected_sha256
    ):
        raise ValueError("workspace_input_mismatch: materialized provider input differs")


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


def contains_redundant_omc_state_round_trip(
    events_jsonl: str,
    *,
    context_paths: tuple[str, ...] = (),
) -> bool:
    script_available = "scripts/omc.py" in context_paths
    sync_calls_by_execution: dict[str, int] = {}
    sync_marker = "python3 scripts/omc.py state sync-session "
    for line_index, line in enumerate(events_jsonl.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not isinstance(command, str):
            continue
        if "python3 scripts/omc.py state status " in command:
            return True
        sync_calls = command.count(sync_marker)
        if sync_calls == 0:
            continue
        if not script_available:
            return True
        execution_id = item.get("id")
        execution_key = (
            execution_id if isinstance(execution_id, str) else f"line:{line_index}"
        )
        sync_calls_by_execution[execution_key] = max(
            sync_calls_by_execution.get(execution_key, 0),
            sync_calls,
        )
    return sum(sync_calls_by_execution.values()) > 1


def contains_redundant_context_round_trip(
    events_jsonl: str,
    *,
    context_paths: tuple[str, ...] = (),
) -> bool:
    """Reject context reads spread across more than one shell execution."""
    normalized_paths = tuple(_safe_context_path(path) for path in context_paths)
    if not normalized_paths:
        return False
    read_execution_ids: set[str] = set()
    for line_index, line in enumerate(events_jsonl.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not isinstance(command, str) or not any(
            path in command for path in normalized_paths
        ):
            continue
        execution_id = item.get("id")
        read_execution_ids.add(
            execution_id if isinstance(execution_id, str) else f"line:{line_index}"
        )
    return len(read_execution_ids) > 1


def _runs_pwd_command(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    if not tokens:
        return False

    shell_name = PurePosixPath(tokens[0]).name
    if shell_name in {"sh", "bash", "zsh"}:
        for index, token in enumerate(tokens[1:], start=1):
            if token.startswith("-") and "c" in token and index + 1 < len(tokens):
                return _runs_pwd_command(" ".join(tokens[index + 1 :]))
        return False

    expecting_command = True
    for token in tokens:
        if token and set(token) <= {";", "&", "|"}:
            expecting_command = True
            continue
        if not expecting_command:
            continue
        if "=" in token and not token.startswith(("/", ".")):
            continue
        if PurePosixPath(token).name == "pwd":
            return True
        expecting_command = False
    return False


def detect_unnecessary_plan_round_trip(events_jsonl: str) -> dict[str, str] | None:
    """Describe the first shell turn that adds no planning evidence."""
    seen_execution_ids: set[str] = set()
    for line_index, line in enumerate(events_jsonl.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        execution_id = item.get("id")
        execution_key = (
            execution_id if isinstance(execution_id, str) else f"line:{line_index}"
        )
        if execution_key in seen_execution_ids:
            continue
        seen_execution_ids.add(execution_key)
        command = item.get("command")
        if not isinstance(command, str):
            continue
        stripped = command.strip()
        normalized = stripped.lower()
        if _runs_pwd_command(normalized):
            return {
                "kind": "pwd",
                "command_sha256": _sha256_text(stripped),
            }
        if "printf " in normalized and not any(
            marker in normalized for marker in ("cat ", "rg ", "sed ", "git ")
        ):
            return {
                "kind": "progress_only_printf",
                "command_sha256": _sha256_text(stripped),
            }
    return None


def _shell_command_payload(command: str) -> str:
    """Unwrap the exact `sh -c` form emitted by Codex command events."""
    stripped = command.strip()
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return stripped
    if not tokens or PurePosixPath(tokens[0]).name not in {"sh", "bash", "zsh"}:
        return stripped

    payload_index = None
    for index, token in enumerate(tokens[1:], start=1):
        if not token.startswith("-") or token.startswith("--"):
            return stripped
        if "c" in token[1:]:
            payload_index = index + 1
            break
    if payload_index is None or len(tokens) != payload_index + 1:
        return stripped
    return tokens[payload_index].strip()


def detect_omc_plan_shell_contract_violation(
    events_jsonl: str,
    expected_command: str,
) -> dict[str, str] | None:
    """Require exactly one command execution matching the frozen context read."""
    commands: list[str] = []
    seen_execution_ids: set[str] = set()
    for line_index, line in enumerate(events_jsonl.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        execution_id = item.get("id")
        execution_key = (
            execution_id if isinstance(execution_id, str) else f"line:{line_index}"
        )
        if execution_key in seen_execution_ids:
            continue
        seen_execution_ids.add(execution_key)
        command = item.get("command")
        if isinstance(command, str):
            commands.append(command.strip())

    if not expected_command:
        if not commands:
            return None
        return {
            "kind": "unexpected_shell_command",
            "command_sha256": _sha256_text(commands[0]),
        }
    if not commands:
        return {
            "kind": "missing_exact_read",
            "command_sha256": _sha256_text(""),
        }
    if _shell_command_payload(commands[0]) != expected_command:
        return {
            "kind": "unexpected_first_command",
            "command_sha256": _sha256_text(commands[0]),
        }
    if len(commands) > 1:
        return {
            "kind": "additional_shell_command",
            "command_sha256": _sha256_text(commands[1]),
        }
    return None


def contains_unnecessary_plan_round_trip(events_jsonl: str) -> bool:
    """Reject shell turns that add no planning evidence."""
    return detect_unnecessary_plan_round_trip(events_jsonl) is not None


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
    max_activation_attempts: int = 1,
    failure_receipt_path: str | Path | None = None,
    context_paths: tuple[str, ...] = (),
    context_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    if type(max_activation_attempts) is not int or not 1 <= max_activation_attempts <= 2:
        raise ValueError("max_activation_attempts must be 1 or 2")
    command = build_codex_command(
        codex_binary=codex_binary,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox=sandbox,
        output_schema=output_schema,
        output_path=output_path,
    )
    prompt = build_provider_prompt(
        provider_id,
        request,
        context_paths=context_paths,
        context_files=context_files,
    )
    attempt_limit = max_activation_attempts if provider_id == "omc-plan" else 1
    attempt_events: list[str] = []
    attempt_usages: list[dict[str, Any]] = []
    for attempt_index in range(1, attempt_limit + 1):
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
            raise RuntimeError(
                f"Codex runtime execution timed out after {timeout_sec}s"
            ) from exc
        if completed.returncode != 0:
            _write_failure_receipt(
                failure_receipt_path,
                provider_id=provider_id,
                reason_code="provider_execution_failed",
                timeout_sec=timeout_sec,
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
            raise RuntimeError(
                completed.stderr.strip() or "Codex runtime execution failed"
            )
        attempt_events.append(completed.stdout)
        attempt_usages.append(extract_usage(completed.stdout))
        if (
            provider_id == "omc-plan"
            and contains_redundant_omc_state_round_trip(
                "\n".join(attempt_events),
                context_paths=context_paths,
            )
        ):
            _write_failure_receipt(
                failure_receipt_path,
                provider_id=provider_id,
                reason_code="redundant_omc_state_round_trip",
                timeout_sec=timeout_sec,
                usage=_aggregate_attempt_usage(attempt_usages),
            )
            raise RuntimeError("redundant_omc_state_round_trip")
        if contains_redundant_context_round_trip(
            completed.stdout,
            context_paths=context_paths,
        ):
            _write_failure_receipt(
                failure_receipt_path,
                provider_id=provider_id,
                reason_code="redundant_context_round_trip",
                timeout_sec=timeout_sec,
                usage=_aggregate_attempt_usage(attempt_usages),
            )
            raise RuntimeError("redundant_context_round_trip")
        round_trip_violation = (
            detect_unnecessary_plan_round_trip("\n".join(attempt_events))
            if provider_id == "omc-plan"
            else None
        )
        if round_trip_violation is None:
            round_trip_violation = detect_omc_plan_shell_contract_violation(
                completed.stdout,
                "",
            )
        if round_trip_violation is not None:
            _write_failure_receipt(
                failure_receipt_path,
                provider_id=provider_id,
                reason_code="unnecessary_plan_round_trip",
                timeout_sec=timeout_sec,
                usage=_aggregate_attempt_usage(attempt_usages),
                offending_command_kind=round_trip_violation["kind"],
                offending_command_sha256=round_trip_violation["command_sha256"],
            )
            raise RuntimeError("unnecessary_plan_round_trip")
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
            activation["attempt_count"] = attempt_index
            activation["retry_count"] = attempt_index - 1
            break
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            activation_miss = (
                provider_id == "omc-plan"
                and isinstance(exc, ValueError)
                and str(exc).startswith("activation_receipt_mismatch:")
            )
            if activation_miss and attempt_index < attempt_limit:
                miss_path = Path(output_path).with_name(
                    f"{Path(output_path).stem}.activation-miss-{attempt_index:02d}"
                    f"{Path(output_path).suffix}"
                )
                miss_path.write_text(raw_output, encoding="utf-8")
                continue
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
                attempt_count=attempt_index if activation_miss else None,
                usage=(
                    _aggregate_attempt_usage(attempt_usages)
                    if activation_miss
                    else None
                ),
            )
            raise RuntimeError(f"provider output invalid: {exc}") from exc
    del plan["runtime_activation_receipt"]
    normalized_output = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    usage = _aggregate_attempt_usage(attempt_usages)
    return {
        "provider_id": provider_id,
        "plan": plan,
        "raw_output": normalized_output,
        "runtime_raw_output_sha256": _sha256_text(raw_output),
        "events_jsonl": "\n".join(attempt_events),
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
    attempt_count: int | None = None,
    usage: dict[str, Any] | None = None,
    offending_command_kind: str | None = None,
    offending_command_sha256: str | None = None,
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
    if attempt_count is not None:
        receipt["attempt_count"] = attempt_count
    if usage is not None:
        receipt["usage"] = usage
    if offending_command_kind is not None:
        receipt["offending_command_kind"] = offending_command_kind
    if offending_command_sha256 is not None:
        receipt["offending_command_sha256"] = offending_command_sha256
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


def _aggregate_attempt_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    if not usages or any(usage.get("status") != "observed" for usage in usages):
        return {"status": "unavailable"}
    input_tokens = sum(usage["input_tokens"] for usage in usages)
    output_tokens = sum(usage["output_tokens"] for usage in usages)
    return {
        "status": "observed",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


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
    instrumented_skill_sha256 = _sha256_text(instrumented_skill)
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
            max_activation_attempts=protocol["activation"]["max_attempts"],
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
        "instrumented_skill_sha256": instrumented_skill_sha256,
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


def _load_prior_registry(path: str | Path) -> list[dict[str, str]]:
    registry = _load_json(path)
    if (
        not isinstance(registry, dict)
        or set(registry) != {"schema_version", "fingerprints"}
        or registry.get("schema_version") != 1
        or not isinstance(registry.get("fingerprints"), list)
    ):
        raise ValueError("confirmatory trusted prior registry file is invalid")
    return registry["fingerprints"]


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


def build_diagnostic_rejudge_batch(
    *,
    protocol: dict[str, Any],
    cases: list[dict[str, Any]],
    original_gold_document: dict[str, Any],
    amended_gold_document: dict[str, Any],
    trusted_signer_public_keys: set[str],
    original_provider_batch: dict[str, Any],
    original_blind_sessions: list[dict[str, Any]],
    original_private_mapping: dict[str, dict[str, str]],
    trusted_original_runtime_signer_public_key: str,
    runtime_signer_private_key: Any,
    runtime_signer_public_key: str,
    batch_id: str,
    artifact_root: str | Path,
    repo_root: str | Path = Path(__file__).resolve().parents[1],
) -> dict[str, Any]:
    """Rebind attested provider outputs to amended gold for diagnostics only."""
    root = _validate_artifact_root(artifact_root, repo_root=repo_root)
    if root.exists() and any(root.iterdir()):
        raise ValueError("runtime artifact root must be empty")
    validate_runtime_protocol(protocol)
    expected_count = protocol["acceptance"]["case_count"]
    validate_runtime_corpus(
        cases,
        original_gold_document,
        expected_count=expected_count,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )
    validate_runtime_corpus(
        cases,
        amended_gold_document,
        expected_count=expected_count,
        trusted_signer_public_keys=trusted_signer_public_keys,
    )
    verify_runtime_attestation(
        original_provider_batch,
        original_blind_sessions,
        original_private_mapping,
        trusted_public_key=trusted_original_runtime_signer_public_key,
    )
    if original_gold_document.get("gold_sha256") == amended_gold_document.get(
        "gold_sha256"
    ):
        raise ValueError("diagnostic rejudge requires amended gold")
    original_scope = original_provider_batch.get("evaluation_scope")
    if original_scope not in {None, "confirmatory"}:
        raise ValueError("diagnostic rejudge requires an original confirmatory batch")
    original_for_validation = deepcopy(original_provider_batch)
    original_for_validation["evaluation_scope"] = (
        "diagnostic_posthoc_gold_amendment"
    )
    validate_runtime_provenance(
        original_for_validation,
        protocol=protocol,
        cases=cases,
        gold_document=original_gold_document,
    )
    executions = validate_runtime_executions(
        original_provider_batch.get("executions"),
        expected_case_count=expected_count,
    )
    original_evidence_sha256 = provider_execution_evidence_digest(
        original_provider_batch
    )
    sessions, private_mapping = build_runtime_blind_batch(
        executions,
        batch_id=batch_id,
        session_count=5,
        gold_document=amended_gold_document,
    )
    provider_batch = deepcopy(_unsigned_provider_batch(original_provider_batch))
    provider_batch.update({
        "batch_id": batch_id,
        "evaluation_scope": "diagnostic_posthoc_gold_amendment",
        "gold_sha256": amended_gold_document["gold_sha256"],
        "source_runtime_attestation_sha256": canonical_digest(
            original_provider_batch["runtime_attestation"]
        ),
        "source_provider_execution_evidence_sha256": original_evidence_sha256,
    })
    if provider_execution_evidence_digest(provider_batch) != original_evidence_sha256:
        raise ValueError("diagnostic rejudge changed provider execution evidence")
    provider_batch["runtime_attestation"] = build_runtime_attestation(
        provider_batch,
        sessions,
        private_mapping,
        private_key=runtime_signer_private_key,
        signer_public_key=runtime_signer_public_key,
    )
    verify_runtime_attestation(
        provider_batch,
        sessions,
        private_mapping,
        trusted_public_key=runtime_signer_public_key,
    )

    root.mkdir(parents=True, exist_ok=True)
    (root / "provider-batch.json").write_text(
        json.dumps(provider_batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "private-mapping.json").write_text(
        json.dumps(private_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "blind-sessions.json").write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sessions_root = root / "blind-sessions"
    sessions_root.mkdir(exist_ok=True)
    for index, session in enumerate(sessions, start=1):
        (sessions_root / f"session-{index:02d}.json").write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {
        "provider_batch": provider_batch,
        "blind_sessions": sessions,
        "private_mapping": private_mapping,
    }


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
    confirmatory_manifest: dict[str, Any] | None = None,
) -> None:
    """Bind signed provider outputs to the exact frozen evaluation inputs."""
    if provider_batch.get("evaluation_scope") not in EVALUATION_SCOPES:
        raise ValueError("runtime evaluation scope is invalid")
    if provider_batch.get("evaluation_scope") == "confirmatory":
        if (
            not isinstance(confirmatory_manifest, dict)
            or provider_batch.get("confirmatory_manifest_sha256")
            != canonical_digest(confirmatory_manifest)
            or provider_batch.get("claim_scope") != CONFIRMATORY_CLAIM_SCOPE
        ):
            raise ValueError("runtime confirmatory provenance mismatch")
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
        expected_input = (
            build_provider_input_envelope(
                provider_id,
                case,
                allowed_workspace_delta=protocol["execution"]["allowed_workspace_delta"],
                instrumented_skill_sha256=provider_batch.get(
                    "instrumented_skill_sha256"
                ),
            )
            if provider_id in PROVIDERS and case is not None
            else None
        )
        if (
            provider_id not in PROVIDERS
            or case is None
            or execution.get("prompt_sha256")
            != _sha256_text(build_provider_prompt(
                provider_id,
                case["request"],
                context_paths=tuple(case["context_files"]),
                context_files=case["context_files"],
            ))
            or execution.get("provider_input_sha256")
            != expected_input["provider_input_sha256"]
            or execution.get("activation", {}).get("skill_sha256") != skill_sha256
        ):
            raise ValueError("runtime execution provenance mismatch")


def run_runtime_batch(
    *,
    protocol: dict[str, Any],
    cases: list[dict[str, Any]],
    gold_document: dict[str, Any],
    trusted_signer_public_keys: set[str],
    confirmatory_manifest: dict[str, Any],
    trusted_prior_fingerprints: list[dict[str, str]],
    trusted_confirmatory_signer_public_keys: set[str],
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
        signed_corpus_sha256=confirmatory_manifest.get(
            "source_corpus_sha256"
        ),
    )
    validate_confirmatory_manifest(
        confirmatory_manifest,
        cases=cases,
        gold_document=gold_document,
        trusted_prior_fingerprints=trusted_prior_fingerprints,
        trusted_signer_public_keys=trusted_confirmatory_signer_public_keys,
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
    if confirmatory_manifest["transmission"]["payload_sha256"] != (
        confirmatory_external_payload_digest(cases, gold_document, skill_sha256)
    ):
        raise ValueError("confirmatory external transmission payload mismatch")
    budget_state = new_execution_budget_state(confirmatory_manifest["budget"])
    assert_execution_budget_available(
        budget_state,
        required_external_calls=1 + protocol["activation"]["max_attempts"],
    )
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
    for provider_id, execution in activation_probe["executions"].items():
        consume_execution_budget(
            budget_state,
            execution,
            failure_receipt_path=root / "confirmatory-budget-failure.json",
            execution_id=f"{batch_id}:activation-probe:{provider_id}",
        )
    receipt = secrets.token_hex(32)
    instrumented_skill = instrument_skill(skill_text, receipt)
    instrumented_skill_sha256 = _sha256_text(instrumented_skill)
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
            actual_manifest = (
                baseline_manifest if provider_id == "baseline-plan" else omc_manifest
            )
            provider_input = build_provider_input_envelope(
                provider_id,
                case,
                allowed_workspace_delta=protocol["execution"]["allowed_workspace_delta"],
                instrumented_skill_sha256=instrumented_skill_sha256,
            )
            validate_provider_workspace_manifest(actual_manifest, provider_input)
            output_path = root / "outputs" / case["case_id"] / f"{provider_id}.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            remaining_calls = (
                budget_state["maximum_external_calls"]
                - budget_state["used_external_calls"]
            )
            max_attempts = (
                1
                if provider_id == "baseline-plan"
                else min(protocol["activation"]["max_attempts"], remaining_calls)
            )
            assert_execution_budget_available(
                budget_state,
                required_external_calls=max_attempts,
                required_token_reserve=(
                    observed_provider_token_reserve(
                        provider_id,
                        activation_probe=activation_probe,
                        executions=executions,
                    )
                    * max_attempts
                ),
                failure_receipt_path=root / "confirmatory-budget-failure.json",
                execution_id=f"{batch_id}:{case['case_id']}:{provider_id}",
            )
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
                max_activation_attempts=max_attempts,
                failure_receipt_path=output_path.with_suffix(".failure.json"),
                context_paths=tuple(case["context_files"]),
                context_files=case["context_files"],
            )
            if execution.get("prompt_sha256") != provider_input["prompt_sha256"]:
                raise ValueError("runtime provider prompt does not match input envelope")
            executions.append({
                **execution,
                "case_id": case["case_id"],
                "plan_execution_id": f"{batch_id}:{case['case_id']}:{provider_id}",
                "provider_input_sha256": provider_input["provider_input_sha256"],
            })
            consume_execution_budget(
                budget_state,
                execution,
                failure_receipt_path=root / "confirmatory-budget-failure.json",
                execution_id=f"{batch_id}:{case['case_id']}:{provider_id}",
            )

    sessions, private_mapping = build_runtime_blind_batch(
        executions,
        batch_id=batch_id,
        session_count=session_count,
        gold_document=gold_document,
    )
    provider_batch = {
        "schema_version": 1,
        "batch_id": batch_id,
        "evaluation_scope": "confirmatory",
        "claim_scope": confirmatory_manifest["claim_scope"],
        "confirmatory_manifest_sha256": canonical_digest(confirmatory_manifest),
        "protocol_sha256": canonical_digest(protocol),
        "corpus_sha256": canonical_digest(cases),
        "gold_sha256": gold_document["gold_sha256"],
        "skill_sha256": skill_sha256,
        "instrumented_skill_sha256": instrumented_skill_sha256,
        "activation_probe_sha256": canonical_digest(activation_probe),
        "activation_probe": activation_probe,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "execution_budget": budget_state,
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
    evaluation_scope: str,
) -> dict[str, Any]:
    """Derive replacement metrics only from signed scores and observed usage."""
    if evaluation_scope not in EVALUATION_SCOPES:
        raise ValueError("runtime evaluation scope is invalid")
    metrics: dict[str, Any] = {
        "evaluation_scope": evaluation_scope,
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
    confirmatory_manifest: dict[str, Any],
    trusted_prior_fingerprints: list[dict[str, str]],
    trusted_confirmatory_signer_public_keys: set[str],
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
        restore_blind_session_plan_labels,
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
        signed_corpus_sha256=confirmatory_manifest.get(
            "source_corpus_sha256"
        ),
    )
    validate_confirmatory_manifest(
        confirmatory_manifest,
        cases=cases,
        gold_document=gold_document,
        trusted_prior_fingerprints=trusted_prior_fingerprints,
        trusted_signer_public_keys=trusted_confirmatory_signer_public_keys,
    )
    skill_sha256 = provider_batch.get("skill_sha256")
    if confirmatory_manifest["transmission"]["payload_sha256"] != (
        confirmatory_external_payload_digest(cases, gold_document, skill_sha256)
    ):
        raise ValueError("confirmatory external transmission payload mismatch")
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
        confirmatory_manifest=confirmatory_manifest,
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
    validate_execution_budget_evidence(
        provider_batch.get("execution_budget"),
        activation_probe=activation_probe,
        executions=executions,
        expected_budget=confirmatory_manifest["budget"],
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
        normalization_session = restore_blind_session_plan_labels(
            session,
            executions=executions,
            private_mapping=private_mapping,
            gold_document=gold_document,
        )
        normalized_results.append(
            normalize_adjudication_result(result, normalization_session)
        )

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
        scores_by_provider,
        executions,
        expected_case_count=expected_count,
        evaluation_scope=provider_batch["evaluation_scope"],
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
        "claim_scope": provider_batch.get("claim_scope"),
        "provenance": {
            "protocol_sha256": provider_batch.get("protocol_sha256"),
            "corpus_sha256": provider_batch.get("corpus_sha256"),
            "gold_sha256": provider_batch.get("gold_sha256"),
            "skill_sha256": provider_batch.get("skill_sha256"),
            "confirmatory_manifest_sha256": provider_batch.get(
                "confirmatory_manifest_sha256"
            ),
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
    validate_parser.add_argument("--confirmatory-manifest", required=True)
    validate_parser.add_argument("--trusted-prior-registry", required=True)
    validate_parser.add_argument(
        "--trusted-confirmatory-signer-public-key", action="append", required=True
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
    batch_parser.add_argument("--confirmatory-manifest", required=True)
    batch_parser.add_argument("--trusted-prior-registry", required=True)
    batch_parser.add_argument(
        "--trusted-confirmatory-signer-public-key", action="append", required=True
    )
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

    diagnostic_parser = subparsers.add_parser("prepare-diagnostic-rejudge")
    diagnostic_parser.add_argument("protocol")
    diagnostic_parser.add_argument("cases")
    diagnostic_parser.add_argument("original_gold")
    diagnostic_parser.add_argument("amended_gold")
    diagnostic_parser.add_argument("original_provider_batch")
    diagnostic_parser.add_argument("original_blind_sessions")
    diagnostic_parser.add_argument("original_private_mapping")
    diagnostic_parser.add_argument(
        "--trusted-gold-signer-public-key", action="append", required=True
    )
    diagnostic_parser.add_argument(
        "--trusted-original-runtime-signer-public-key", required=True
    )
    diagnostic_parser.add_argument(
        "--runtime-signer-private-key-file", required=True
    )
    diagnostic_parser.add_argument(
        "--trusted-runtime-signer-public-key", required=True
    )
    diagnostic_parser.add_argument("--artifact-root", required=True)
    diagnostic_parser.add_argument("--batch-id", required=True)
    diagnostic_parser.add_argument(
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
    finalize_parser.add_argument("--confirmatory-manifest", required=True)
    finalize_parser.add_argument("--trusted-prior-registry", required=True)
    finalize_parser.add_argument(
        "--trusted-confirmatory-signer-public-key", action="append", required=True
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

    gold_author_parser = subparsers.add_parser("prepare-gold-author")
    gold_author_parser.add_argument("readiness")
    gold_author_parser.add_argument("selection")
    gold_author_parser.add_argument("--output", required=True)

    gold_evidence_parser = subparsers.add_parser("prepare-gold-evidence")
    gold_evidence_parser.add_argument("payload")
    gold_evidence_parser.add_argument("--artifact-root", required=True)
    gold_evidence_parser.add_argument("--source-commit", required=True)
    gold_evidence_parser.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )

    gold_receipt_parser = subparsers.add_parser("record-gold-receipt")
    gold_receipt_parser.add_argument("artifact_root")
    gold_receipt_parser.add_argument(
        "--phase", choices=("author", "reviewer"), required=True
    )
    gold_receipt_parser.add_argument("--provider", required=True)
    gold_receipt_parser.add_argument("--session-id", required=True)
    gold_receipt_parser.add_argument("--session-nonce", required=True)
    gold_receipt_parser.add_argument("--approved-manifest-sha256", required=True)
    gold_receipt_parser.add_argument("--input-sha256", required=True)
    gold_receipt_parser.add_argument("--raw-output-file", required=True)

    prepare_confirmatory_parser = subparsers.add_parser(
        "prepare-confirmatory"
    )
    prepare_confirmatory_parser.add_argument("readiness")
    prepare_confirmatory_parser.add_argument("public_corpus")
    prepare_confirmatory_parser.add_argument("selection")
    prepare_confirmatory_parser.add_argument("gold")
    prepare_confirmatory_parser.add_argument("trusted_prior_registry")
    prepare_confirmatory_parser.add_argument("--skill-file", required=True)
    prepare_confirmatory_parser.add_argument(
        "--trusted-gold-signer-public-key", action="append", required=True
    )
    prepare_confirmatory_parser.add_argument("--producer", required=True)
    prepare_confirmatory_parser.add_argument(
        "--author-session-id", required=True
    )
    prepare_confirmatory_parser.add_argument(
        "--reviewer-session-id", required=True
    )
    prepare_confirmatory_parser.add_argument("--signer", required=True)
    prepare_confirmatory_parser.add_argument(
        "--signer-public-key", required=True
    )
    prepare_confirmatory_parser.add_argument("--approved-payload-sha256")
    prepare_confirmatory_parser.add_argument("--output", required=True)

    seal_confirmatory_parser = subparsers.add_parser("seal-confirmatory")
    seal_confirmatory_parser.add_argument("preparation")
    seal_confirmatory_parser.add_argument("gold")
    seal_confirmatory_parser.add_argument("trusted_prior_registry")
    seal_confirmatory_parser.add_argument("--signature-file", required=True)
    seal_confirmatory_parser.add_argument("--skill-file", required=True)
    seal_confirmatory_parser.add_argument(
        "--trusted-gold-signer-public-key", action="append", required=True
    )
    seal_confirmatory_parser.add_argument(
        "--trusted-confirmatory-signer-public-key",
        action="append",
        required=True,
    )
    seal_confirmatory_parser.add_argument("--output", required=True)

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

    if args.command == "prepare-gold-author":
        result = prepare_confirmatory_gold_author_payload(
            readiness=_load_json(args.readiness),
            selection=_load_json(args.selection),
        )
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({
            "status": result["status"],
            "external_payload_sha256": result["external_payload_sha256"],
            "output": str(Path(args.output).resolve()),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "prepare-gold-evidence":
        result = prepare_gold_author_evidence(
            payload=_load_json(args.payload),
            artifact_root=args.artifact_root,
            repo_root=args.repo_root,
            source_commit=args.source_commit,
        )
        print(json.dumps({
            "status": result["status"],
            "provider_execution_allowed": result["provider_execution_allowed"],
            "manifest_sha256": result["manifest_sha256"],
            "artifact_root": str(Path(args.artifact_root).resolve()),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "record-gold-receipt":
        result = record_gold_evidence_receipt(
            artifact_root=args.artifact_root,
            phase=args.phase,
            provider=args.provider,
            session_id=args.session_id,
            session_nonce=args.session_nonce,
            approved_manifest_sha256=args.approved_manifest_sha256,
            input_sha256=args.input_sha256,
            raw_output=Path(args.raw_output_file).read_text(encoding="utf-8"),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "prepare-confirmatory":
        skill_sha256 = _sha256_text(
            Path(args.skill_file).read_text(encoding="utf-8")
        )
        result = prepare_confirmatory_runtime_inputs(
            readiness=_load_json(args.readiness),
            public_corpus=_load_json(args.public_corpus),
            selection=_load_json(args.selection),
            gold_document=_load_json(args.gold),
            trusted_prior_fingerprints=_load_prior_registry(
                args.trusted_prior_registry
            ),
            skill_sha256=skill_sha256,
            producer=args.producer,
            author_session_id=args.author_session_id,
            reviewer_session_id=args.reviewer_session_id,
            signer=args.signer,
            signer_public_key=args.signer_public_key,
            trusted_gold_signer_public_keys=set(
                args.trusted_gold_signer_public_key
            ),
            approved_payload_sha256=args.approved_payload_sha256,
        )
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({
            "status": result["status"],
            "external_payload_sha256": result["external_payload_sha256"],
            "output": str(Path(args.output).resolve()),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "seal-confirmatory":
        skill_sha256 = _sha256_text(
            Path(args.skill_file).read_text(encoding="utf-8")
        )
        result = seal_confirmatory_runtime_inputs(
            _load_json(args.preparation),
            signature=Path(args.signature_file).read_text(
                encoding="utf-8"
            ).strip(),
            gold_document=_load_json(args.gold),
            trusted_prior_fingerprints=_load_prior_registry(
                args.trusted_prior_registry
            ),
            skill_sha256=skill_sha256,
            trusted_gold_signer_public_keys=set(
                args.trusted_gold_signer_public_key
            ),
            trusted_confirmatory_signer_public_keys=set(
                args.trusted_confirmatory_signer_public_key
            ),
        )
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({
            "status": result["status"],
            "provider_execution_allowed": result[
                "provider_execution_allowed"
            ],
            "output": str(Path(args.output).resolve()),
        }, ensure_ascii=False, indent=2))
        return 0

    protocol = load_runtime_protocol(args.protocol)
    if args.command == "validate-corpus":
        cases = _load_json(args.cases)
        gold = _load_json(args.gold)
        confirmatory_manifest = _load_json(args.confirmatory_manifest)
        validate_runtime_corpus(
            cases["cases"],
            gold,
            expected_count=protocol["acceptance"]["case_count"],
            trusted_signer_public_keys=set(args.trusted_gold_signer_public_key),
            signed_corpus_sha256=confirmatory_manifest.get(
                "source_corpus_sha256"
            ),
        )
        validate_confirmatory_manifest(
            confirmatory_manifest,
            cases=cases["cases"],
            gold_document=gold,
            trusted_prior_fingerprints=_load_prior_registry(
                args.trusted_prior_registry
            ),
            trusted_signer_public_keys=set(
                args.trusted_confirmatory_signer_public_key
            ),
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
    if args.command == "prepare-diagnostic-rejudge":
        from omc_plan_pilot import validate_private_key_location

        artifact_root = _validate_artifact_root(
            args.artifact_root, repo_root=args.repo_root
        )
        runtime_signer_private_key = validate_private_key_location(
            args.runtime_signer_private_key_file,
            repo_root=args.repo_root,
            artifact_root=artifact_root,
            trusted_public_key=args.trusted_runtime_signer_public_key,
        )
        cases = _load_json(args.cases)
        build_diagnostic_rejudge_batch(
            protocol=protocol,
            cases=cases["cases"],
            original_gold_document=_load_json(args.original_gold),
            amended_gold_document=_load_json(args.amended_gold),
            trusted_signer_public_keys=set(args.trusted_gold_signer_public_key),
            original_provider_batch=_load_json(args.original_provider_batch),
            original_blind_sessions=_load_json(args.original_blind_sessions),
            original_private_mapping=_load_json(args.original_private_mapping),
            trusted_original_runtime_signer_public_key=(
                args.trusted_original_runtime_signer_public_key
            ),
            runtime_signer_private_key=runtime_signer_private_key,
            runtime_signer_public_key=args.trusted_runtime_signer_public_key,
            batch_id=args.batch_id,
            artifact_root=artifact_root,
            repo_root=args.repo_root,
        )
        print(artifact_root / "provider-batch.json")
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
            confirmatory_manifest=_load_json(args.confirmatory_manifest),
            trusted_prior_fingerprints=_load_prior_registry(
                args.trusted_prior_registry
            ),
            trusted_confirmatory_signer_public_keys=set(
                args.trusted_confirmatory_signer_public_key
            ),
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
        confirmatory_manifest=_load_json(args.confirmatory_manifest),
        trusted_prior_fingerprints=_load_prior_registry(
            args.trusted_prior_registry
        ),
        trusted_confirmatory_signer_public_keys=set(
            args.trusted_confirmatory_signer_public_key
        ),
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
    evaluation_scope = metrics.get("evaluation_scope")
    if evaluation_scope not in EVALUATION_SCOPES:
        return {
            "decision": "INVALID_RUN",
            "reason_code": "evaluation_scope_invalid",
            "failed_gates": ["evaluation_scope"],
        }
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
    if evaluation_scope == "diagnostic_posthoc_gold_amendment":
        return {
            "decision": "DIAGNOSTIC_ONLY",
            "reason_code": "posthoc_gold_amendment",
            "failed_gates": ["replacement_claim_eligibility"],
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
        "decision": "NOT_PROVEN" if failed else "PROVISIONALLY_REPLACEABLE",
        "reason_code": (
            "quality_or_cost_gate_failed" if failed else "single_batch_gates_passed"
        ),
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
    if replacement["decision"] != "PROVISIONALLY_REPLACEABLE":
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
        "decision": (
            "SUPERIOR_CANDIDATE" if is_candidate else "PROVISIONALLY_REPLACEABLE"
        ),
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
        "confirmatory_manifest_sha256",
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
    if any(
        report.get("claim_scope") != CONFIRMATORY_CLAIM_SCOPE
        for report in batch_reports
    ):
        raise ValueError("superiority claim scope is invalid")

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
    elif all(
        decision in {"SUPERIOR_CANDIDATE", "PROVISIONALLY_REPLACEABLE"}
        for decision in decisions
    ):
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
