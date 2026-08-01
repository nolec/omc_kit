#!/usr/bin/env python3
"""Run a fair, draft-safe OMC Plan versus baseline Plan pilot."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from omc_plan_benchmark import (
    canonical_digest,
    score_plan_batch,
    seal_semantic_adjudication,
    validate_fixture_documents,
)


_PROVIDER_IDS = ("baseline-plan", "omc-plan")
_PROTOCOL_FIELDS = {
    "schema_version",
    "benchmark_scope",
    "split",
    "common_prompt_template",
    "providers",
    "execution",
    "adjudication",
    "measurement",
}


class PairExecutionError(RuntimeError):
    """Raised when a provider pair is incomplete and must be rerun atomically."""


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_path_component(value: Any, label: str) -> str:
    """Reject identifiers that can escape their assigned artifact directory."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"{label} must be a safe path component")
    return value


def load_protocol(path: str | Path) -> dict[str, Any]:
    """Load and strictly validate the frozen pilot protocol."""
    protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(protocol, dict) or set(protocol) != _PROTOCOL_FIELDS:
        raise ValueError("pilot protocol fields are invalid")
    if protocol["schema_version"] != 1:
        raise ValueError("pilot protocol schema_version must be 1")
    if protocol["benchmark_scope"] != "prompt_decomposition_only":
        raise ValueError("pilot benchmark_scope must be prompt_decomposition_only")
    if protocol["split"] != "development":
        raise ValueError("pilot protocol is limited to the development split")
    template = protocol["common_prompt_template"]
    if not isinstance(template, str) or template.count("{{TREATMENT}}") != 1:
        raise ValueError("common prompt requires one treatment placeholder")
    if template.count("{{REQUEST}}") != 1:
        raise ValueError("common prompt requires one request placeholder")
    providers = protocol["providers"]
    if not isinstance(providers, dict) or set(providers) != set(_PROVIDER_IDS):
        raise ValueError("pilot protocol providers are invalid")
    for provider_id in _PROVIDER_IDS:
        provider = providers[provider_id]
        if not isinstance(provider, dict) or set(provider) != {"plan_producer", "treatment"}:
            raise ValueError(f"provider contract is invalid: {provider_id}")
        if not all(isinstance(provider[field], str) and provider[field].strip() for field in provider):
            raise ValueError(f"provider values are required: {provider_id}")
    execution = protocol["execution"]
    if set(execution) != {
        "retry_limit",
        "sandbox",
        "ephemeral",
        "ignore_user_config",
        "load_project_instructions",
        "require_same_model_config",
        "counterbalance_provider_order",
    }:
        raise ValueError("execution contract fields are invalid")
    if execution.get("retry_limit") != 0:
        raise ValueError("retry_limit must be 0")
    if execution.get("sandbox") != "read-only":
        raise ValueError("pilot sandbox must be read-only")
    for field in (
        "ephemeral",
        "ignore_user_config",
        "require_same_model_config",
        "counterbalance_provider_order",
    ):
        if execution.get(field) is not True:
            raise ValueError(f"execution.{field} must be true")
    if execution.get("load_project_instructions") is not False:
        raise ValueError("project instructions must be disabled for prompt parity")
    adjudication = protocol["adjudication"]
    if set(adjudication) != {
        "session_count",
        "pairs_per_session",
        "blind_provider_identity",
        "trusted_public_key_required_before_execution",
        "private_key_generation_allowed",
    }:
        raise ValueError("adjudication contract fields are invalid")
    if adjudication.get("session_count") != 2 or adjudication.get("pairs_per_session") != 2:
        raise ValueError("adjudication must use two sessions with two pairs each")
    if adjudication.get("blind_provider_identity") is not True:
        raise ValueError("adjudication must blind provider identity")
    if adjudication.get("trusted_public_key_required_before_execution") is not True:
        raise ValueError("trusted adjudicator public key must be pinned")
    if adjudication.get("private_key_generation_allowed") is not False:
        raise ValueError("pilot must not generate adjudicator private keys")
    measurement = protocol["measurement"]
    if set(measurement) != {"usage_required_for_cost_claim", "unavailable_status"}:
        raise ValueError("measurement contract fields are invalid")
    if measurement.get("usage_required_for_cost_claim") is not True:
        raise ValueError("usage must be required for cost claims")
    if measurement.get("unavailable_status") != "unavailable":
        raise ValueError("measurement unavailable status is invalid")
    return protocol


def build_provider_prompt(
    protocol: dict[str, Any],
    case: dict[str, Any],
    provider_id: str,
) -> dict[str, str]:
    """Build one prompt while exposing hashes that prove treatment-only delta."""
    if provider_id not in _PROVIDER_IDS:
        raise ValueError(f"unknown provider_id: {provider_id}")
    request = case.get("request")
    if not isinstance(request, str) or not request.strip():
        raise ValueError("case request is required")
    template = protocol["common_prompt_template"]
    treatment = protocol["providers"][provider_id]["treatment"]
    final_prompt = template.replace("{{TREATMENT}}", treatment).replace(
        "{{REQUEST}}", request
    )
    return {
        "final_prompt": final_prompt,
        "common_prompt_sha256": _digest_text(template),
        "treatment_sha256": _digest_text(treatment),
        "request_sha256": _digest_text(request),
        "final_prompt_sha256": _digest_text(final_prompt),
    }


def extract_usage_from_jsonl(events_jsonl: str) -> dict[str, Any]:
    """Read complete input/output token usage without inventing missing values."""
    observed: dict[str, Any] | None = None
    for line in events_jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage") if isinstance(event, dict) else None
        if event.get("type") == "turn.completed" and isinstance(usage, dict):
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                observed = {
                    "status": "observed",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                }
    return observed or {"status": "unavailable"}


def validate_private_key_location(
    private_key_path: str | Path,
    *,
    repo_root: str | Path,
    artifact_root: str | Path,
    trusted_public_key: str,
) -> Any:
    """Load an externally supplied raw Ed25519 key and verify its pinned identity."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_path = Path(private_key_path).expanduser().resolve()
    roots = (Path(repo_root).resolve(), Path(artifact_root).resolve())
    if any(_is_relative_to(key_path, root) for root in roots):
        raise ValueError("private key must be outside the repository and artifact root")
    try:
        raw = base64.b64decode(key_path.read_text(encoding="utf-8").strip(), validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    except (OSError, ValueError) as exc:
        raise ValueError("adjudicator private key is invalid") from exc
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    actual_public_key = base64.b64encode(public_raw).decode("ascii")
    if actual_public_key != trusted_public_key:
        raise ValueError("private key does not match the pinned trusted public key")
    return private_key


def _validate_provider_plan_uniqueness(plan: dict[str, Any]) -> None:
    """Enforce uniqueness constraints unsupported by Codex output schemas."""
    for field in (
        "requirements_covered",
        "scope_items",
        "assumptions",
        "decisions_required",
    ):
        values = plan.get(field)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"invalid plan field: {field}")
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate plan value: {field}")

    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("invalid plan field: tasks")
    task_ids = []
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            raise ValueError("invalid plan task")
        task_ids.append(task["id"])
        supports = task.get("supports")
        if (
            not isinstance(supports, list)
            or any(not isinstance(value, str) for value in supports)
            or len(supports) != len(set(supports))
        ):
            raise ValueError("duplicate plan value: task supports")
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("duplicate plan value: task id")


def run_provider_pairs(
    cases: list[dict[str, Any]],
    protocol: dict[str, Any],
    *,
    executor: Callable[..., dict[str, Any]],
    artifact_root: str | Path,
    batch_id: str,
    model: str = "test-model",
    reasoning_effort: str = "low",
) -> dict[str, Any]:
    """Execute baseline/OMC pairs atomically with no automatic retry."""
    artifact_root = Path(artifact_root).resolve()
    batch_id = _safe_path_component(batch_id, "batch_id")
    root = artifact_root / batch_id
    if root.exists():
        raise ValueError("batch_id already exists; use a new batch_id")
    case_ids = [_safe_path_component(case.get("case_id"), "case_id") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id values must be unique")
    root.mkdir(parents=True)
    providers = {
        provider_id: {
            "provider_id": provider_id,
            "plan_producer": protocol["providers"][provider_id]["plan_producer"],
            "executions": [],
        }
        for provider_id in _PROVIDER_IDS
    }
    manifest_executions: list[dict[str, Any]] = []
    try:
        for case_index, case in enumerate(cases):
            pair_dir = root / case_ids[case_index]
            pair_dir.mkdir()
            pair_results: list[tuple[str, dict[str, Any], dict[str, str]]] = []
            pair_error: Exception | None = None
            provider_order = (
                _PROVIDER_IDS if case_index % 2 == 0 else tuple(reversed(_PROVIDER_IDS))
            )
            for provider_id in provider_order:
                prompt = build_provider_prompt(protocol, case, provider_id)
                try:
                    result = executor(
                        provider_id=provider_id,
                        case=case,
                        prompt=prompt["final_prompt"],
                        execution={
                            "model": model,
                            "reasoning_effort": reasoning_effort,
                            **protocol["execution"],
                        },
                    )
                    pair_results.append((provider_id, result, prompt))
                except Exception as exc:  # pair completion is more important than early exit
                    pair_error = exc
            if pair_error is not None or len(pair_results) != 2:
                shutil.rmtree(pair_dir, ignore_errors=True)
                raise PairExecutionError(
                    f"pair failed for {case['case_id']}; rerun the pair with a new batch_id"
                ) from pair_error
            if any(
                not isinstance(result.get("plan"), dict)
                or not isinstance(result.get("raw_output"), str)
                for _, result, _ in pair_results
            ):
                shutil.rmtree(pair_dir, ignore_errors=True)
                raise PairExecutionError(
                    f"pair produced invalid output for {case['case_id']}; "
                    "rerun the pair with a new batch_id"
                )
            try:
                for _, result, _ in pair_results:
                    _validate_provider_plan_uniqueness(result["plan"])
            except ValueError as exc:
                shutil.rmtree(pair_dir, ignore_errors=True)
                raise PairExecutionError(
                    f"pair produced invalid plan contract for {case['case_id']}; "
                    "rerun the pair with a new batch_id"
                ) from exc
            for provider_id, result, prompt in pair_results:
                plan = result.get("plan")
                raw_output = result.get("raw_output")
                execution_id = f"{batch_id}:{case['case_id']}:{provider_id}"
                execution = {
                    "case_id": case["case_id"],
                    "plan_execution_id": execution_id,
                    "plan": plan,
                    "raw_output": raw_output,
                    "semantic_adjudication": None,
                }
                providers[provider_id]["executions"].append(execution)
                usage = extract_usage_from_jsonl(result.get("events_jsonl", ""))
                metadata = {
                    "provider_id": provider_id,
                    "case_id": case["case_id"],
                    "plan_execution_id": execution_id,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "raw_output_sha256": canonical_digest(raw_output),
                    "usage": usage,
                    **{key: value for key, value in prompt.items() if key != "final_prompt"},
                }
                manifest_executions.append(metadata)
                (pair_dir / f"{provider_id}.json").write_text(
                    json.dumps({"execution": execution, "metadata": metadata}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    except Exception:
        raise
    usage_complete = all(
        item["usage"]["status"] == "observed" for item in manifest_executions
    )
    provider_batch = {
        "schema_version": 1,
        "split": "development",
        "providers": [providers[provider_id] for provider_id in _PROVIDER_IDS],
    }
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "benchmark_scope": protocol["benchmark_scope"],
        "model": model,
        "reasoning_effort": reasoning_effort,
        "retry_limit": 0,
        "token_measurement_status": "observed" if usage_complete else "unavailable",
        "cost_claim_allowed": usage_complete,
        "executions": manifest_executions,
    }
    (root / "provider-batch.json").write_text(
        json.dumps(provider_batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"provider_batch": provider_batch, "manifest": manifest}


def load_completed_provider_batch(
    artifact_root: str | Path,
    batch_id: str,
    *,
    expected_cases: list[dict[str, Any]],
    protocol: dict[str, Any],
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    """Load and verify one provider stage without repeating paid calls."""
    root = Path(artifact_root).resolve() / _safe_path_component(batch_id, "batch_id")
    provider_batch = json.loads(
        (root / "provider-batch.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("batch_id") != batch_id:
        raise ValueError("resume manifest batch_id mismatch")
    if manifest.get("model") != model or manifest.get("reasoning_effort") != reasoning_effort:
        raise ValueError("resume model settings mismatch")
    if manifest.get("retry_limit") != 0:
        raise ValueError("resume manifest must preserve retry_limit 0")
    if manifest.get("benchmark_scope") != protocol.get("benchmark_scope"):
        raise ValueError("resume benchmark scope mismatch")

    if (
        not isinstance(provider_batch, dict)
        or set(provider_batch) != {"schema_version", "split", "providers"}
        or provider_batch.get("schema_version") != 1
        or provider_batch.get("split") != "development"
    ):
        raise ValueError("resume provider batch contract mismatch")
    providers = provider_batch.get("providers")
    if not isinstance(providers, list) or len(providers) != len(_PROVIDER_IDS):
        raise ValueError("resume provider batch is incomplete")
    provider_ids: set[str] = set()
    for provider in providers:
        if (
            not isinstance(provider, dict)
            or set(provider) != {"provider_id", "plan_producer", "executions"}
            or provider.get("provider_id") not in _PROVIDER_IDS
        ):
            raise ValueError("resume provider batch is incomplete")
        provider_id = provider["provider_id"]
        if provider_id in provider_ids:
            raise ValueError("resume provider batch is incomplete")
        provider_ids.add(provider_id)
        if provider.get("plan_producer") != protocol["providers"][provider_id]["plan_producer"]:
            raise ValueError("provider provenance mismatch")
    expected_case_ids = {case["case_id"] for case in expected_cases}
    cases_by_id = {case["case_id"]: case for case in expected_cases}
    provider_executions = [
        (provider["provider_id"], execution)
        for provider in providers
        for execution in provider.get("executions", [])
    ]
    identities = [
        (provider_id, execution.get("case_id"))
        for provider_id, execution in provider_executions
    ]
    expected_identities = {
        (provider_id, case_id)
        for provider_id in _PROVIDER_IDS
        for case_id in expected_case_ids
    }
    manifest_executions = manifest.get("executions")
    if (
        set(identities) != expected_identities
        or len(identities) != len(expected_identities)
        or not isinstance(manifest_executions, list)
        or len(manifest_executions) != len(expected_identities)
    ):
        raise ValueError("resume provider executions are incomplete")

    manifest_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for metadata in manifest_executions:
        if not isinstance(metadata, dict):
            raise ValueError("resume provider manifest is invalid")
        identity = (metadata.get("provider_id"), metadata.get("case_id"))
        if identity in manifest_by_identity:
            raise ValueError("resume provider manifest contains duplicate executions")
        manifest_by_identity[identity] = metadata
    if set(manifest_by_identity) != expected_identities:
        raise ValueError("resume provider manifest is incomplete")

    usage_complete = True
    for metadata in manifest_executions:
        usage = metadata.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("provider usage integrity mismatch")
        if usage.get("status") == "observed":
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            total_tokens = usage.get("total_tokens")
            if (
                not all(type(value) is int and value >= 0 for value in (
                    input_tokens,
                    output_tokens,
                    total_tokens,
                ))
                or total_tokens != input_tokens + output_tokens
            ):
                raise ValueError("provider usage integrity mismatch")
        elif usage == {"status": "unavailable"}:
            usage_complete = False
        else:
            raise ValueError("provider usage integrity mismatch")
    expected_usage_status = "observed" if usage_complete else "unavailable"
    if (
        manifest.get("token_measurement_status") != expected_usage_status
        or manifest.get("cost_claim_allowed") is not usage_complete
    ):
        raise ValueError("provider usage integrity mismatch")

    for provider_id, execution in provider_executions:
        case_id = execution["case_id"]
        metadata = manifest_by_identity[(provider_id, case_id)]
        raw_output = execution.get("raw_output")
        prompt = build_provider_prompt(protocol, cases_by_id[case_id], provider_id)
        if (
            not isinstance(raw_output, str)
            or canonical_digest(raw_output) != metadata.get("raw_output_sha256")
            or execution.get("plan_execution_id")
            != metadata.get("plan_execution_id")
            or metadata.get("model") != model
            or metadata.get("reasoning_effort") != reasoning_effort
            or any(
                metadata.get(key) != prompt[key]
                for key in (
                    "common_prompt_sha256",
                    "treatment_sha256",
                    "request_sha256",
                    "final_prompt_sha256",
                )
            )
        ):
            raise ValueError("provider output integrity mismatch")
        try:
            parsed_output = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ValueError("provider output integrity mismatch") from exc
        if parsed_output != execution.get("plan"):
            raise ValueError("provider output integrity mismatch")
        try:
            _validate_provider_plan_uniqueness(parsed_output)
        except ValueError as exc:
            raise ValueError("provider plan contract mismatch") from exc
    return {"provider_batch": provider_batch, "manifest": manifest}


def build_blind_adjudication_sessions(
    executions: list[dict[str, Any]],
    *,
    session_count: int,
    batch_id: str,
    gold_document: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Split four complete pairs over fresh blind adjudicator sessions."""
    if session_count != 2:
        raise ValueError("pilot adjudication requires exactly two sessions")
    by_case: dict[str, list[dict[str, Any]]] = {}
    for execution in executions:
        by_case.setdefault(execution["case_id"], []).append(execution)
    if any(len(pair) != 2 for pair in by_case.values()):
        raise ValueError("blind adjudication requires complete provider pairs")
    sessions = [
        {"schema_version": 1, "session_id": f"{batch_id}:adjudication:{index + 1}", "items": []}
        for index in range(session_count)
    ]
    mapping: dict[str, dict[str, str]] = {}
    gold_by_id = (
        {case["case_id"]: case for case in gold_document["cases"]}
        if gold_document is not None
        else {}
    )
    for case_index, case_id in enumerate(sorted(by_case)):
        session = sessions[case_index % session_count]
        pair = sorted(by_case[case_id], key=lambda item: item["provider_id"])
        if case_index % 2:
            pair.reverse()
        for item_index, execution in enumerate(pair):
            blind_id = f"{batch_id}:blind:{case_index + 1}:{item_index + 1}"
            blind_item = {
                "blind_id": blind_id,
                "case_id": case_id,
                "plan": execution["plan"],
                "raw_output": execution["raw_output"],
            }
            if gold_document is not None:
                if case_id not in gold_by_id:
                    raise ValueError(f"missing gold case for blind adjudication: {case_id}")
                blind_item["gold_case"] = gold_by_id[case_id]
            session["items"].append(blind_item)
            mapping[blind_id] = {
                "provider_id": execution["provider_id"],
                "case_id": case_id,
                "plan_execution_id": execution["plan_execution_id"],
                "session_id": session["session_id"],
            }
    return sessions, mapping


def normalize_adjudication_result(
    result: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    """Canonicalize duplicate labels and reject unknown semantic claims."""
    session_items = {item["blind_id"]: item for item in session["items"]}
    output_items = result.get("items")
    if not isinstance(output_items, list):
        raise ValueError("adjudication items must be a list")
    output_blind_ids = [
        item.get("blind_id") for item in output_items if isinstance(item, dict)
    ]
    if len(output_blind_ids) != len(output_items) or len(output_blind_ids) != len(set(output_blind_ids)):
        raise ValueError("blind adjudication ids must be unique")
    if set(output_blind_ids) != set(session_items):
        raise ValueError("adjudication output does not cover the blind session")

    normalized_items = []
    for item in output_items:
        source = session_items[item["blind_id"]]
        if item.get("case_id") != source["case_id"]:
            raise ValueError("adjudication case_id mismatch")
        gold = source["gold_case"]
        plan = source["plan"]
        requirement_ids = {entry["id"] for entry in gold["required_items"]}
        excluded_scope = set(gold["excluded_scope"])
        expected_edges = {
            (edge["before"], edge["after"]) for edge in gold["dependency_edges"]
        }
        plan_edges = {
            (edge["before"], edge["after"]) for edge in plan["dependency_edges"]
        }
        task_ids = {task["id"] for task in plan["tasks"]}
        assumptions = set(plan["assumptions"])

        def unique_strings(values: Any, allowed: set[str]) -> list[str]:
            if not isinstance(values, list) or any(
                not isinstance(value, str) or value not in allowed for value in values
            ):
                raise ValueError("unknown semantic label")
            return list(dict.fromkeys(values))

        def unique_edges(
            values: Any,
            *,
            allowed: set[tuple[str, str]] | None = None,
            forbidden: set[tuple[str, str]] | None = None,
        ) -> list[dict[str, str]]:
            if not isinstance(values, list):
                raise ValueError("unknown semantic label")
            forbidden_edges = forbidden or set()
            seen: set[tuple[str, str]] = set()
            edges = []
            for edge in values:
                if (
                    not isinstance(edge, dict)
                    or set(edge) != {"before", "after"}
                    or not all(isinstance(edge[key], str) for key in ("before", "after"))
                ):
                    raise ValueError("unknown semantic label")
                identity = (edge["before"], edge["after"])
                if (allowed is not None and identity not in allowed) or identity in forbidden_edges:
                    raise ValueError("unknown semantic label")
                if identity in seen:
                    continue
                seen.add(identity)
                edges.append({"before": identity[0], "after": identity[1]})
            return edges

        links: dict[str, list[str]] = {}
        raw_links = item["task_requirement_links"]
        if not isinstance(raw_links, list):
            raise ValueError("unknown semantic label")
        for link in raw_links:
            if not isinstance(link, dict) or set(link) != {"task_id", "requirement_ids"}:
                raise ValueError("unknown semantic label")
            task_id = link["task_id"]
            if task_id not in task_ids:
                raise ValueError("unknown semantic label")
            current = links.setdefault(task_id, [])
            for requirement_id in unique_strings(
                link["requirement_ids"], requirement_ids
            ):
                if requirement_id not in current:
                    current.append(requirement_id)

        dependency_hits = unique_edges(item["dependency_hits"], allowed=expected_edges)
        normalized_items.append({
            **item,
            "requirement_hits": unique_strings(item["requirement_hits"], requirement_ids),
            "scope_violations": unique_strings(item["scope_violations"], excluded_scope),
            "dependency_hits": dependency_hits,
            "unexpected_dependency_edges": unique_edges(
                item["unexpected_dependency_edges"], allowed=plan_edges
            ),
            "task_requirement_links": [
                {"task_id": task_id, "requirement_ids": requirement_ids_for_task}
                for task_id, requirement_ids_for_task in links.items()
            ],
            "unsupported_assumptions": unique_strings(
                item["unsupported_assumptions"], assumptions
            ),
        })
    return {**result, "items": normalized_items}


def _encoded_public_key(private_key: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def seal_blind_adjudications(
    provider_batch: dict[str, Any],
    adjudication_results: list[dict[str, Any]],
    private_mapping: dict[str, dict[str, str]],
    gold_document: dict[str, Any],
    *,
    private_key: Any,
    trusted_public_key: str,
    adjudicator: str,
) -> dict[str, Any]:
    """Restore private provider identities and seal every blind semantic label."""
    if _encoded_public_key(private_key) != trusted_public_key:
        raise ValueError("adjudicator key does not match the pinned trusted public key")
    if len(adjudication_results) != 2:
        raise ValueError("pilot requires results from exactly two adjudicator sessions")
    session_ids = {result.get("session_id") for result in adjudication_results}
    if len(session_ids) != 2 or None in session_ids:
        raise ValueError("adjudicator sessions must be fresh and distinct")
    output_by_blind_id: dict[str, tuple[dict[str, Any], str]] = {}
    for result in adjudication_results:
        execution_id = result.get("adjudication_execution_id")
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("adjudication execution id is required")
        items = result.get("items")
        if not isinstance(items, list):
            raise ValueError("adjudication items must be a list")
        for item in items:
            blind_id = item.get("blind_id") if isinstance(item, dict) else None
            if not isinstance(blind_id, str) or blind_id in output_by_blind_id:
                raise ValueError("blind adjudication ids must be unique")
            mapping = private_mapping.get(blind_id)
            if mapping is None or mapping["session_id"] != result["session_id"]:
                raise ValueError("blind adjudication session mapping mismatch")
            output_by_blind_id[blind_id] = (item, execution_id)
    if set(output_by_blind_id) != set(private_mapping):
        raise ValueError("blind adjudication must cover every provider output")

    gold_by_id = {case["case_id"]: case for case in gold_document["cases"]}
    sealed_batch = deepcopy(provider_batch)
    execution_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    producer_by_provider: dict[str, str] = {}
    for provider in sealed_batch["providers"]:
        producer_by_provider[provider["provider_id"]] = provider["plan_producer"]
        for execution in provider["executions"]:
            execution_by_identity[(provider["provider_id"], execution["case_id"])] = execution

    semantic_fields = {
        "requirement_hits",
        "scope_violations",
        "dependency_hits",
        "unexpected_dependency_edges",
        "task_requirement_links",
        "unsupported_assumptions",
    }
    for blind_id, mapping in private_mapping.items():
        item, adjudication_execution_id = output_by_blind_id[blind_id]
        if item.get("case_id") != mapping["case_id"]:
            raise ValueError("blind adjudication case mismatch")
        if not semantic_fields.issubset(item):
            raise ValueError("blind adjudication semantic fields are incomplete")
        execution = execution_by_identity[(mapping["provider_id"], mapping["case_id"])]
        gold_case = gold_by_id[mapping["case_id"]]
        labels = {
            "case_id": mapping["case_id"],
            "gold_case_sha256": canonical_digest(gold_case),
            **{field: deepcopy(item[field]) for field in semantic_fields},
        }
        execution["semantic_adjudication"] = seal_semantic_adjudication(
            labels,
            plan=execution["plan"],
            gold=gold_case,
            adjudicator=adjudicator,
            plan_producer=producer_by_provider[mapping["provider_id"]],
            adjudication_execution_id=adjudication_execution_id,
            plan_execution_id=execution["plan_execution_id"],
            private_key=private_key,
            raw_output=execution["raw_output"],
        )
    return sealed_batch


def build_pilot_report(
    public_document: dict[str, Any],
    gold_document: dict[str, Any],
    sealed_provider_batch: dict[str, Any],
    manifest: dict[str, Any],
    *,
    trusted_adjudicator_public_key: str,
) -> dict[str, Any]:
    """Score the pilot while keeping unsupported superiority/cost claims blocked."""
    scored = score_plan_batch(
        public_document,
        gold_document,
        sealed_provider_batch,
        trusted_gold_signer_public_keys=set(),
        trusted_adjudicator_public_keys={trusted_adjudicator_public_key},
        allow_draft_gold=True,
    )
    gold_status = scored["gold_status"]
    usage_status = manifest.get("token_measurement_status", "unavailable")
    return {
        **scored,
        "benchmark_scope": manifest.get(
            "benchmark_scope", "prompt_decomposition_only"
        ),
        "adjudication_mode": "two_session_blind_pilot",
        "token_measurement_status": usage_status,
        "superiority_claim_status": (
            "blocked_pilot_scope"
            if gold_status == "signed_off"
            else "blocked_draft_gold"
        ),
        "cost_claim_status": (
            "allowed_observed_usage"
            if usage_status == "observed"
            else "blocked_usage_unavailable"
        ),
    }


def run_full_pilot(
    public_document: dict[str, Any],
    gold_document: dict[str, Any],
    protocol: dict[str, Any],
    *,
    provider_executor: Callable[..., dict[str, Any]],
    adjudicator_executor: Callable[..., dict[str, Any]],
    artifact_root: str | Path,
    batch_id: str,
    model: str,
    reasoning_effort: str,
    private_key_path: str | Path,
    trusted_public_key: str,
    repo_root: str | Path,
    adjudicator: str = "independent-codex-adjudicator",
    resume_provider_batch: bool = False,
) -> dict[str, Any]:
    """Run the 8+2 call development pilot and write one draft-safe report."""
    artifact_root = Path(artifact_root).resolve()
    repo_root = Path(repo_root).resolve()
    if _is_relative_to(artifact_root, repo_root):
        raise ValueError("artifact root must be outside the repository")
    validate_fixture_documents(
        public_document,
        gold_document,
        require_signed_off=False,
        trusted_signer_public_keys=set(),
    )
    private_key = validate_private_key_location(
        private_key_path,
        repo_root=repo_root,
        artifact_root=artifact_root,
        trusted_public_key=trusted_public_key,
    )
    development_cases = [
        case for case in public_document["cases"] if case.get("split") == "development"
    ]
    if len(development_cases) != 4:
        raise ValueError("pilot requires exactly four development cases")
    if resume_provider_batch:
        collected = load_completed_provider_batch(
            artifact_root,
            batch_id,
            expected_cases=development_cases,
            protocol=protocol,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    else:
        collected = run_provider_pairs(
            development_cases,
            protocol,
            executor=provider_executor,
            artifact_root=artifact_root,
            batch_id=batch_id,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    executions = [
        {"provider_id": provider["provider_id"], **execution}
        for provider in collected["provider_batch"]["providers"]
        for execution in provider["executions"]
    ]
    sessions, mapping = build_blind_adjudication_sessions(
        executions,
        session_count=protocol["adjudication"]["session_count"],
        batch_id=batch_id,
        gold_document=gold_document,
    )
    root = artifact_root / batch_id
    adjudication_artifacts = [
        *root.glob("adjudication-session-*"),
        *(root / name for name in (
            "private-provider-mapping.json",
            "sealed-provider-batch.json",
            "pilot-report.json",
        ) if (root / name).exists()),
    ]
    if adjudication_artifacts:
        raise ValueError("adjudication artifacts already exist")
    adjudication_results = []
    for index, session in enumerate(sessions, start=1):
        (root / f"adjudication-session-{index}-blind.json").write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raw_result = adjudicator_executor(
            session=deepcopy(session),
            model=model,
            reasoning_effort=reasoning_effort,
        )
        (root / f"adjudication-session-{index}-result.json").write_text(
            json.dumps(raw_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        normalized_result = normalize_adjudication_result(raw_result, session)
        (root / f"adjudication-session-{index}-normalized.json").write_text(
            json.dumps(normalized_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        adjudication_results.append(normalized_result)
    (root / "private-provider-mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sealed_batch = seal_blind_adjudications(
        collected["provider_batch"],
        adjudication_results,
        mapping,
        gold_document,
        private_key=private_key,
        trusted_public_key=trusted_public_key,
        adjudicator=adjudicator,
    )
    report = build_pilot_report(
        public_document,
        gold_document,
        sealed_batch,
        collected["manifest"],
        trusted_adjudicator_public_key=trusted_public_key,
    )
    (root / "sealed-provider-batch.json").write_text(
        json.dumps(sealed_batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "pilot-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def codex_executor(
    *,
    provider_id: str,
    case: dict[str, Any],
    prompt: str,
    execution: dict[str, Any],
    codex_binary: str,
    output_schema: str | Path,
    workspace: str | Path,
) -> dict[str, Any]:
    """Execute one isolated Codex call and preserve its JSONL events."""
    del provider_id, case
    output_path = Path(workspace) / f"last-{uuid.uuid4().hex}.json"
    command = [
        codex_binary,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        execution["sandbox"],
        "--model",
        execution["model"],
        "--config",
        f'model_reasoning_effort="{execution["reasoning_effort"]}"',
        "--output-schema",
        str(output_schema),
        "--output-last-message",
        str(output_path),
        "--json",
        "-",
    ]
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=workspace,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Codex execution failed")
    raw_output = output_path.read_text(encoding="utf-8")
    return {
        "plan": json.loads(raw_output),
        "raw_output": raw_output,
        "events_jsonl": completed.stdout,
    }


def build_adjudication_prompt(session: dict[str, Any]) -> str:
    """Build the dynamic-label contract used by blind adjudicators."""
    instructions = (
        "You are an independent semantic adjudicator for implementation plans. "
        "Provider identities are intentionally hidden. For every item, compare the plan "
        "only with gold_case and return conservative semantic labels. A requirement hit "
        "requires explicit plan support; do not infer unstated behavior. Preserve blind_id, "
        "case_id, and session_id exactly. Copy labels exactly; never paraphrase, translate, "
        "change punctuation, or invent labels. requirement_hits may contain only exact "
        "gold_case.required_items[].id values. scope_violations may contain only exact "
        "gold_case.excluded_scope strings. dependency_hits may contain only exact "
        "gold_case.dependency_edges objects. unexpected_dependency_edges may contain only "
        "exact plan.dependency_edges objects. Never reverse dependency edges; when a plan "
        "edge is unexpected, copy its original before and after values without changing "
        "their direction. A dependency hit requires an explicit ordered task path whose "
        "task_requirement_links implement the gold before requirement before the gold after "
        "requirement. Ordinary implementation-detail edges are not unexpected by default; "
        "mark a plan edge unexpected only when it contradicts a gold dependency order. "
        "task_requirement_links.task_id may contain "
        "only exact plan.tasks[].id values and requirement_ids may contain only exact "
        "gold_case.required_items[].id values. unsupported_assumptions may contain only "
        "exact plan.assumptions strings. Never reuse labels from another item; evaluate "
        "each item with only its own plan and gold_case. Use an empty array when no allowed "
        "value applies. "
        "Return only schema-valid JSON."
    )
    return instructions + "\n\n" + json.dumps(
        session, ensure_ascii=False, sort_keys=True
    )


def codex_adjudicator_executor(
    *,
    session: dict[str, Any],
    model: str,
    reasoning_effort: str,
    codex_binary: str,
    output_schema: str | Path,
    workspace: str | Path,
) -> dict[str, Any]:
    """Run one fresh blind adjudication session in an isolated workspace."""
    output_path = Path(workspace) / f"adjudication-{uuid.uuid4().hex}.json"
    prompt = build_adjudication_prompt(session)
    command = [
        codex_binary,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--output-schema",
        str(output_schema),
        "--output-last-message",
        str(output_path),
        "--json",
        "-",
    ]
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=workspace,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Codex adjudication failed")
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if result.get("session_id") != session["session_id"]:
        raise ValueError("adjudication session_id does not match the requested session")
    result["adjudication_execution_id"] = (
        f"{session['session_id']}:fresh:{uuid.uuid4().hex}"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases")
    parser.add_argument("gold")
    parser.add_argument("protocol")
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--trusted-adjudicator-public-key", required=True)
    parser.add_argument("--adjudicator-private-key-file", required=True)
    parser.add_argument(
        "--resume-provider-batch",
        action="store_true",
        help="Reuse a complete provider batch and rerun only blind adjudication.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
    )
    parser.add_argument(
        "--output-schema",
        default=str(Path(__file__).parent / "fixtures" / "omc_plan_output_schema.json"),
    )
    parser.add_argument(
        "--adjudication-output-schema",
        default=str(
            Path(__file__).parent
            / "fixtures"
            / "omc_plan_adjudication_output_schema.json"
        ),
    )
    args = parser.parse_args()
    protocol = load_protocol(args.protocol)
    public_document = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    gold_document = json.loads(Path(args.gold).read_text(encoding="utf-8"))

    def provider_executor(**kwargs: Any) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="omc-plan-provider-") as workspace:
            return codex_executor(
                **kwargs,
                codex_binary=args.codex_binary,
                output_schema=args.output_schema,
                workspace=workspace,
            )

    def adjudicator_executor(**kwargs: Any) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="omc-plan-adjudicator-") as workspace:
            return codex_adjudicator_executor(
                **kwargs,
                codex_binary=args.codex_binary,
                output_schema=args.adjudication_output_schema,
                workspace=workspace,
            )

    run_full_pilot(
        public_document,
        gold_document,
        protocol,
        provider_executor=provider_executor,
        adjudicator_executor=adjudicator_executor,
        artifact_root=args.artifact_root,
        batch_id=args.batch_id,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        private_key_path=args.adjudicator_private_key_file,
        trusted_public_key=args.trusted_adjudicator_public_key,
        repo_root=args.repo_root,
        resume_provider_batch=args.resume_provider_batch,
    )
    print(Path(args.artifact_root).resolve() / args.batch_id / "pilot-report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
