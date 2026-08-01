import base64
import json
from pathlib import Path

import pytest

import omc_plan_pilot
from omc_plan_pilot import (
    PairExecutionError,
    build_blind_adjudication_sessions,
    build_pilot_report,
    build_provider_prompt,
    codex_adjudicator_executor,
    codex_executor,
    extract_usage_from_jsonl,
    load_protocol,
    run_full_pilot,
    run_provider_pairs,
    seal_blind_adjudications,
    validate_private_key_location,
)


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "schema_name",
    ["omc_plan_output_schema.json", "omc_plan_adjudication_output_schema.json"],
)
def test_codex_output_schemas_avoid_unsupported_unique_items(schema_name):
    schema = json.loads((FIXTURES / schema_name).read_text(encoding="utf-8"))

    def contains_unique_items(value):
        if isinstance(value, dict):
            return "uniqueItems" in value or any(
                contains_unique_items(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_unique_items(item) for item in value)
        return False

    assert not contains_unique_items(schema)


def _protocol():
    return load_protocol(FIXTURES / "omc_plan_pilot_protocol.json")


def _cases():
    document = json.loads(
        (FIXTURES / "omc_plan_benchmark_cases.json").read_text(encoding="utf-8")
    )
    return [case for case in document["cases"] if case["split"] == "development"]


def _public_document():
    return json.loads(
        (FIXTURES / "omc_plan_benchmark_cases.json").read_text(encoding="utf-8")
    )


def _gold_document():
    return json.loads(
        (FIXTURES / "omc_plan_gold_labels.json").read_text(encoding="utf-8")
    )


def _plan(case_id):
    return {
        "requirements_covered": [case_id],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [{
            "id": "task-1",
            "target": "target.py",
            "action": "implement",
            "verify": "pytest",
            "supports": [case_id],
        }],
        "assumptions": [],
        "decisions_required": [],
    }


def _write_fake_codex(path):
    path.write_text(
        r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
output_path = Path(args[args.index("--output-last-message") + 1])
prompt = sys.stdin.read()
if "independent semantic adjudicator" in prompt:
    session = json.loads(prompt.split("\n\n", 1)[1])
    result = {
        "session_id": os.environ.get("FAKE_CODEX_SESSION_ID", session["session_id"]),
        "items": [
            {
                "blind_id": item["blind_id"],
                "case_id": item["case_id"],
                "requirement_hits": [],
                "scope_violations": [],
                "dependency_hits": [],
                "unexpected_dependency_edges": [],
                "task_requirement_links": [],
                "unsupported_assumptions": [],
            }
            for item in session["items"]
        ],
    }
else:
    result = {
        "requirements_covered": ["REQ-1"],
        "scope_items": [],
        "dependency_edges": [],
        "tasks": [],
        "assumptions": [],
        "decisions_required": [],
    }
output_path.write_text(json.dumps(result), encoding="utf-8")
print(json.dumps({"type": "invocation", "args": args}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 11, "output_tokens": 7},
}))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_provider_prompts_differ_only_by_frozen_treatment_block():
    protocol = _protocol()
    case = _cases()[0]

    baseline = build_provider_prompt(protocol, case, "baseline-plan")
    omc = build_provider_prompt(protocol, case, "omc-plan")

    assert baseline["common_prompt_sha256"] == omc["common_prompt_sha256"]
    assert baseline["request_sha256"] == omc["request_sha256"]
    assert baseline["treatment_sha256"] != omc["treatment_sha256"]
    assert baseline["final_prompt"].replace(
        protocol["providers"]["baseline-plan"]["treatment"], "<TREATMENT>"
    ) == omc["final_prompt"].replace(
        protocol["providers"]["omc-plan"]["treatment"], "<TREATMENT>"
    )


def test_protocol_rejects_retry_and_non_development_scope(tmp_path):
    protocol = _protocol()
    protocol["execution"]["retry_limit"] = 1
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")

    with pytest.raises(ValueError, match="retry_limit must be 0"):
        load_protocol(path)

    protocol["execution"]["retry_limit"] = 0
    protocol["split"] = "holdout"
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(ValueError, match="development"):
        load_protocol(path)


def test_private_key_must_be_external_and_match_pinned_public_key(tmp_path):
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private_key = cryptography.Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_key = base64.b64encode(private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )).decode("ascii")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    external_key = tmp_path / "external.key"
    external_key.write_text(base64.b64encode(raw_private).decode("ascii"))

    loaded = validate_private_key_location(
        external_key,
        repo_root=Path(__file__).parents[1],
        artifact_root=artifact_root,
        trusted_public_key=public_key,
    )
    assert loaded.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ) == private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    internal_key = artifact_root / "generated.key"
    internal_key.write_text(external_key.read_text())
    with pytest.raises(ValueError, match="outside the repository and artifact root"):
        validate_private_key_location(
            internal_key,
            repo_root=Path(__file__).parents[1],
            artifact_root=artifact_root,
            trusted_public_key=public_key,
        )


def test_pair_failure_removes_partial_outputs_and_requires_new_batch(tmp_path):
    calls = []

    def executor(*, provider_id, case, prompt, execution):
        calls.append((provider_id, case["case_id"]))
        if provider_id == "omc-plan":
            raise RuntimeError("provider failed")
        plan = _plan(case["case_id"])
        return {
            "plan": plan,
            "raw_output": json.dumps(plan),
            "events_jsonl": "",
        }

    with pytest.raises(PairExecutionError, match="rerun the pair with a new batch_id"):
        run_provider_pairs(
            _cases()[:1],
            _protocol(),
            executor=executor,
            artifact_root=tmp_path / "artifacts",
            batch_id="batch-1",
        )

    pair_dir = tmp_path / "artifacts" / "batch-1" / _cases()[0]["case_id"]
    assert not (pair_dir / "baseline-plan.json").exists()
    assert not (pair_dir / "omc-plan.json").exists()
    assert len(calls) == 2


@pytest.mark.parametrize("batch_id", ["../escaped", "/tmp/escaped"])
def test_provider_pairs_reject_batch_ids_that_escape_artifact_root(tmp_path, batch_id):
    calls = []

    def executor(**kwargs):
        calls.append(kwargs)
        raise AssertionError("executor must not run for an unsafe batch id")

    with pytest.raises(ValueError, match="batch_id"):
        run_provider_pairs(
            _cases()[:1],
            _protocol(),
            executor=executor,
            artifact_root=tmp_path / "artifacts",
            batch_id=batch_id,
        )

    assert calls == []


def test_provider_pairs_reject_case_ids_that_escape_batch_root(tmp_path):
    calls = []
    case = {**_cases()[0], "case_id": "../escaped"}

    def executor(**kwargs):
        calls.append(kwargs)
        raise AssertionError("executor must not run for an unsafe case id")

    with pytest.raises(ValueError, match="case_id"):
        run_provider_pairs(
            [case],
            _protocol(),
            executor=executor,
            artifact_root=tmp_path / "artifacts",
            batch_id="safe-batch",
        )

    assert calls == []


def test_complete_pairs_capture_usage_or_explicit_unavailable(tmp_path):
    def executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        events = ""
        if provider_id == "omc-plan":
            events = json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })
        return {
            "plan": plan,
            "raw_output": json.dumps(plan),
            "events_jsonl": events,
        }

    result = run_provider_pairs(
        _cases()[:1],
        _protocol(),
        executor=executor,
        artifact_root=tmp_path / "artifacts",
        batch_id="batch-2",
    )

    executions = {
        execution["provider_id"]: execution
        for execution in result["manifest"]["executions"]
    }
    assert executions["omc-plan"]["usage"]["status"] == "observed"
    assert executions["baseline-plan"]["usage"] == {"status": "unavailable"}
    assert result["manifest"]["cost_claim_allowed"] is False


def test_provider_execution_order_is_counterbalanced_by_case(tmp_path):
    calls = []

    def executor(*, provider_id, case, prompt, execution):
        calls.append((case["case_id"], provider_id))
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    run_provider_pairs(
        _cases()[:2],
        _protocol(),
        executor=executor,
        artifact_root=tmp_path / "artifacts",
        batch_id="batch-counterbalanced",
    )

    assert [provider_id for _, provider_id in calls] == [
        "baseline-plan",
        "omc-plan",
        "omc-plan",
        "baseline-plan",
    ]


def test_usage_parser_requires_complete_input_and_output_counts():
    assert extract_usage_from_jsonl("not-json") == {"status": "unavailable"}
    partial = json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 10},
    })
    assert extract_usage_from_jsonl(partial) == {"status": "unavailable"}


def test_codex_executor_invokes_isolated_cli_and_preserves_jsonl(tmp_path):
    fake_codex = tmp_path / "fake-codex"
    _write_fake_codex(fake_codex)

    result = codex_executor(
        provider_id="baseline-plan",
        case=_cases()[0],
        prompt="build a plan",
        execution={"sandbox": "read-only", "model": "gpt-test", "reasoning_effort": "low"},
        codex_binary=str(fake_codex),
        output_schema=FIXTURES / "omc_plan_output_schema.json",
        workspace=tmp_path,
    )

    invocation = json.loads(result["events_jsonl"].splitlines()[0])
    assert result["plan"]["requirements_covered"] == ["REQ-1"]
    assert "--ignore-user-config" in invocation["args"]
    assert "--ignore-rules" in invocation["args"]
    assert "--ephemeral" in invocation["args"]
    assert invocation["args"][invocation["args"].index("--sandbox") + 1] == "read-only"
    assert extract_usage_from_jsonl(result["events_jsonl"])["total_tokens"] == 18


def test_codex_adjudicator_rejects_mismatched_session_id(tmp_path, monkeypatch):
    fake_codex = tmp_path / "fake-codex"
    _write_fake_codex(fake_codex)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "wrong-session")
    session = {
        "session_id": "expected-session",
        "items": [{"blind_id": "blind-1", "case_id": "case-1"}],
    }

    with pytest.raises(ValueError, match="session_id"):
        codex_adjudicator_executor(
            session=session,
            model="gpt-test",
            reasoning_effort="low",
            codex_binary=str(fake_codex),
            output_schema=FIXTURES / "omc_plan_adjudication_output_schema.json",
            workspace=tmp_path,
        )


def test_adjudicator_prompt_requires_exact_dynamic_labels():
    case = _cases()[0]
    gold = next(
        item for item in _gold_document()["cases"] if item["case_id"] == case["case_id"]
    )
    plan = _plan(case["case_id"])
    session = {
        "session_id": "session-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "plan": plan,
            "raw_output": json.dumps(plan),
            "gold_case": gold,
        }],
    }

    prompt = omc_plan_pilot.build_adjudication_prompt(session)

    assert "Copy labels exactly" in prompt
    assert "dependency_hits may contain only exact gold_case.dependency_edges" in prompt
    assert "unexpected_dependency_edges may contain only exact plan.dependency_edges" in prompt
    assert "Never reverse dependency edges" in prompt
    assert "Never reuse labels from another item" in prompt
    assert "implementation-detail edges are not unexpected by default" in prompt
    assert "dependency hit requires an explicit ordered task path" in prompt
    assert "unsupported_assumptions may contain only exact plan.assumptions strings" in prompt


def test_normalize_adjudication_result_rejects_invented_unexpected_edge():
    case = _cases()[0]
    gold = next(
        item for item in _gold_document()["cases"] if item["case_id"] == case["case_id"]
    )
    plan = _plan(case["case_id"])
    session = {
        "session_id": "session-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "plan": plan,
            "raw_output": json.dumps(plan),
            "gold_case": gold,
        }],
    }
    result = {
        "session_id": "session-1",
        "adjudication_execution_id": "execution-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "requirement_hits": [],
            "scope_violations": [],
            "dependency_hits": [],
            "unexpected_dependency_edges": [{"before": "invented", "after": "edge"}],
            "task_requirement_links": [],
            "unsupported_assumptions": [],
        }],
    }

    with pytest.raises(ValueError, match="unknown semantic label"):
        omc_plan_pilot.normalize_adjudication_result(result, session)


def test_normalize_rejects_cross_item_task_requirement_id():
    case = _cases()[0]
    gold = next(
        item for item in _gold_document()["cases"] if item["case_id"] == case["case_id"]
    )
    plan = _plan(case["case_id"])
    task_id = plan["tasks"][0]["id"]
    session = {
        "session_id": "session-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "plan": plan,
            "raw_output": json.dumps(plan),
            "gold_case": gold,
        }],
    }
    result = {
        "session_id": "session-1",
        "adjudication_execution_id": "execution-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "requirement_hits": [],
            "scope_violations": [],
            "dependency_hits": [],
            "unexpected_dependency_edges": [],
            "task_requirement_links": [{
                "task_id": task_id,
                "requirement_ids": ["REQ-FROM-ANOTHER-ITEM"],
            }],
            "unsupported_assumptions": [],
        }],
    }

    with pytest.raises(ValueError, match="unknown semantic label"):
        omc_plan_pilot.normalize_adjudication_result(result, session)


def test_blind_sessions_split_pairs_without_provider_names():
    executions = []
    for case in _cases():
        for provider_id in ("baseline-plan", "omc-plan"):
            executions.append({
                "provider_id": provider_id,
                "case_id": case["case_id"],
                "plan_execution_id": f"{provider_id}-{case['case_id']}",
                "plan": _plan(case["case_id"]),
                "raw_output": json.dumps(_plan(case["case_id"])),
            })

    sessions, private_mapping = build_blind_adjudication_sessions(
        executions,
        session_count=2,
        batch_id="batch-3",
    )

    assert len(sessions) == 2
    assert sorted(len(session["items"]) for session in sessions) == [4, 4]
    assert "omc-plan" not in json.dumps(sessions)
    assert "baseline-plan" not in json.dumps(sessions)
    assert len(private_mapping) == 8


def test_sealed_blind_results_bind_every_output_to_two_fresh_sessions(tmp_path):
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private_key = cryptography.Ed25519PrivateKey.generate()
    trusted_public_key = base64.b64encode(private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )).decode("ascii")

    def executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    collected = run_provider_pairs(
        _cases(),
        _protocol(),
        executor=executor,
        artifact_root=tmp_path / "artifacts",
        batch_id="batch-4",
    )
    executions = [
        {"provider_id": provider["provider_id"], **execution}
        for provider in collected["provider_batch"]["providers"]
        for execution in provider["executions"]
    ]
    sessions, mapping = build_blind_adjudication_sessions(
        executions, session_count=2, batch_id="batch-4"
    )
    gold_by_id = {case["case_id"]: case for case in _gold_document()["cases"]}
    results = []
    for session in sessions:
        items = []
        for item in session["items"]:
            gold = gold_by_id[item["case_id"]]
            items.append({
                "blind_id": item["blind_id"],
                "case_id": item["case_id"],
                "requirement_hits": [entry["id"] for entry in gold["required_items"]],
                "scope_violations": [],
                "dependency_hits": gold["dependency_edges"],
                "unexpected_dependency_edges": [],
                "task_requirement_links": [{
                    "task_id": "task-1",
                    "requirement_ids": [entry["id"] for entry in gold["required_items"]],
                }],
                "unsupported_assumptions": [],
            })
        results.append({
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh-{session['session_id']}",
            "items": items,
        })

    sealed = seal_blind_adjudications(
        collected["provider_batch"],
        results,
        mapping,
        _gold_document(),
        private_key=private_key,
        trusted_public_key=trusted_public_key,
        adjudicator="independent-codex-adjudicator",
    )

    receipts = [
        execution["semantic_adjudication"]["receipt"]
        for provider in sealed["providers"]
        for execution in provider["executions"]
    ]
    assert len(receipts) == 8
    assert {receipt["adjudication_execution_id"] for receipt in receipts} == {
        f"fresh-{session['session_id']}" for session in sessions
    }
    assert all(receipt["adjudicator_public_key"] == trusted_public_key for receipt in receipts)


def test_pilot_report_blocks_superiority_and_cost_claims_for_draft_gold(tmp_path):
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private_key = cryptography.Ed25519PrivateKey.generate()
    trusted_public_key = base64.b64encode(private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )).decode("ascii")

    def executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    collected = run_provider_pairs(
        _cases(), _protocol(), executor=executor,
        artifact_root=tmp_path / "artifacts", batch_id="batch-5",
    )
    executions = [
        {"provider_id": provider["provider_id"], **execution}
        for provider in collected["provider_batch"]["providers"]
        for execution in provider["executions"]
    ]
    sessions, mapping = build_blind_adjudication_sessions(
        executions, session_count=2, batch_id="batch-5"
    )
    gold = _gold_document()
    gold_by_id = {case["case_id"]: case for case in gold["cases"]}
    results = []
    for session in sessions:
        items = []
        for item in session["items"]:
            items.append({
                "blind_id": item["blind_id"],
                "case_id": item["case_id"],
                "requirement_hits": [],
                "scope_violations": [],
                "dependency_hits": [],
                "unexpected_dependency_edges": [],
                "task_requirement_links": [],
                "unsupported_assumptions": [],
            })
        results.append({
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh-{session['session_id']}",
            "items": items,
        })
    sealed = seal_blind_adjudications(
        collected["provider_batch"], results, mapping, gold,
        private_key=private_key,
        trusted_public_key=trusted_public_key,
        adjudicator="independent-codex-adjudicator",
    )

    report = build_pilot_report(
        _public_document(),
        gold,
        sealed,
        collected["manifest"],
        trusted_adjudicator_public_key=trusted_public_key,
    )

    assert report["benchmark_scope"] == "prompt_decomposition_only"
    assert report["evaluation_status"] == "draft_not_for_comparison"
    assert report["superiority_claim_status"] == "blocked_draft_gold"
    assert report["token_measurement_status"] == "unavailable"
    assert report["cost_claim_status"] == "blocked_usage_unavailable"
    assert report["adjudication_mode"] == "two_session_blind_pilot"


def test_full_pilot_runs_eight_plans_and_two_fresh_adjudications(tmp_path):
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private_key = cryptography.Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    trusted_public_key = base64.b64encode(private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )).decode("ascii")
    key_path = tmp_path / "adjudicator.key"
    key_path.write_text(base64.b64encode(raw_private).decode("ascii"))
    artifact_root = tmp_path / "runtime" / "artifacts"
    provider_calls = []
    adjudicator_calls = []

    def provider_executor(*, provider_id, case, prompt, execution):
        provider_calls.append((provider_id, case["case_id"], execution["retry_limit"]))
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    def adjudicator_executor(*, session, model, reasoning_effort):
        adjudicator_calls.append((session["session_id"], model, reasoning_effort))
        return {
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh-{len(adjudicator_calls)}",
            "items": [{
                "blind_id": item["blind_id"],
                "case_id": item["case_id"],
                "requirement_hits": [],
                "scope_violations": [],
                "dependency_hits": [],
                "unexpected_dependency_edges": [],
                "task_requirement_links": [],
                "unsupported_assumptions": [],
            } for item in session["items"]],
        }

    report = run_full_pilot(
        _public_document(),
        _gold_document(),
        _protocol(),
        provider_executor=provider_executor,
        adjudicator_executor=adjudicator_executor,
        artifact_root=artifact_root,
        batch_id="batch-full",
        model="gpt-test",
        reasoning_effort="low",
        private_key_path=key_path,
        trusted_public_key=trusted_public_key,
        repo_root=Path(__file__).parents[1],
    )

    assert len(provider_calls) == 8
    assert len(adjudicator_calls) == 2
    assert len({call[0] for call in adjudicator_calls}) == 2
    assert all(call[2] == 0 for call in provider_calls)
    assert report["evaluation_status"] == "draft_not_for_comparison"
    assert (artifact_root / "batch-full" / "pilot-report.json").exists()


def test_normalize_adjudication_result_rejects_unknown_semantic_credit():
    case = _cases()[0]
    gold = next(
        item for item in _gold_document()["cases"] if item["case_id"] == case["case_id"]
    )
    plan = _plan(case["case_id"])
    session = {
        "session_id": "session-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "plan": plan,
            "raw_output": json.dumps(plan),
            "gold_case": gold,
        }],
    }
    expected_edge = gold["dependency_edges"][0]
    result = {
        "session_id": "session-1",
        "adjudication_execution_id": "execution-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "requirement_hits": [gold["required_items"][0]["id"], "UNKNOWN"],
            "scope_violations": [*gold["excluded_scope"], "UNKNOWN"],
            "dependency_hits": [expected_edge, {"before": "x", "after": "y"}],
            "unexpected_dependency_edges": [expected_edge, {"before": "x", "after": "y"}],
            "task_requirement_links": [
                {"task_id": "task-1", "requirement_ids": [gold["required_items"][0]["id"], "UNKNOWN"]},
                {"task_id": "unknown-task", "requirement_ids": [gold["required_items"][0]["id"]]},
            ],
            "unsupported_assumptions": ["UNKNOWN"],
        }],
    }

    with pytest.raises(ValueError, match="unknown semantic label"):
        omc_plan_pilot.normalize_adjudication_result(result, session)


def test_normalize_adjudication_result_rejects_duplicate_blind_ids():
    case = _cases()[0]
    gold = next(
        item for item in _gold_document()["cases"] if item["case_id"] == case["case_id"]
    )
    plan = _plan(case["case_id"])
    session = {
        "session_id": "session-1",
        "items": [{
            "blind_id": "blind-1",
            "case_id": case["case_id"],
            "plan": plan,
            "raw_output": json.dumps(plan),
            "gold_case": gold,
        }],
    }
    item = {
        "blind_id": "blind-1",
        "case_id": case["case_id"],
        "requirement_hits": [],
        "scope_violations": [],
        "dependency_hits": [],
        "unexpected_dependency_edges": [],
        "task_requirement_links": [],
        "unsupported_assumptions": [],
    }
    result = {
        "session_id": "session-1",
        "adjudication_execution_id": "execution-1",
        "items": [item, dict(item)],
    }

    with pytest.raises(ValueError, match="blind adjudication ids must be unique"):
        omc_plan_pilot.normalize_adjudication_result(result, session)


def test_run_provider_pairs_rejects_duplicate_plan_values_before_adjudication(tmp_path):
    def provider_executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        plan["requirements_covered"] *= 2
        raw_output = json.dumps(plan)
        return {"plan": plan, "raw_output": raw_output, "events_jsonl": ""}

    with pytest.raises(PairExecutionError, match="invalid plan contract"):
        omc_plan_pilot.run_provider_pairs(
            _cases(),
            _protocol(),
            executor=provider_executor,
            artifact_root=tmp_path / "artifacts",
            batch_id="duplicate-plan",
            model="gpt-test",
            reasoning_effort="low",
        )

    assert not (tmp_path / "artifacts" / "duplicate-plan" / "provider-batch.json").exists()


def test_full_pilot_can_resume_complete_provider_batch_without_repeating_calls(tmp_path):
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private_key = cryptography.Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    trusted_public_key = base64.b64encode(private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )).decode("ascii")
    key_path = tmp_path / "adjudicator.key"
    key_path.write_text(base64.b64encode(raw_private).decode("ascii"))
    artifact_root = tmp_path / "runtime" / "artifacts"
    batch_id = "batch-resume"

    def initial_provider(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    omc_plan_pilot.run_provider_pairs(
        _cases(),
        _protocol(),
        executor=initial_provider,
        artifact_root=artifact_root,
        batch_id=batch_id,
        model="gpt-test",
        reasoning_effort="low",
    )

    def forbidden_provider(**kwargs):
        raise AssertionError("resume must not repeat provider calls")

    def adjudicator_executor(*, session, model, reasoning_effort):
        assert not (artifact_root / batch_id / "private-provider-mapping.json").exists()
        return {
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh-{session['session_id']}",
            "items": [{
                "blind_id": item["blind_id"],
                "case_id": item["case_id"],
                "requirement_hits": [],
                "scope_violations": [],
                "dependency_hits": [],
                "unexpected_dependency_edges": [],
                "task_requirement_links": [],
                "unsupported_assumptions": [],
            } for item in session["items"]],
        }

    report = run_full_pilot(
        _public_document(),
        _gold_document(),
        _protocol(),
        provider_executor=forbidden_provider,
        adjudicator_executor=adjudicator_executor,
        artifact_root=artifact_root,
        batch_id=batch_id,
        model="gpt-test",
        reasoning_effort="low",
        private_key_path=key_path,
        trusted_public_key=trusted_public_key,
        repo_root=Path(__file__).parents[1],
        resume_provider_batch=True,
    )

    root = artifact_root / batch_id
    assert report["evaluation_status"] == "draft_not_for_comparison"
    assert (root / "adjudication-session-1-result.json").exists()
    normalized = json.loads(
        (root / "adjudication-session-1-normalized.json").read_text(encoding="utf-8")
    )
    assert all(not item["dependency_hits"] for item in normalized["items"])

    with pytest.raises(ValueError, match="adjudication artifacts already exist"):
        run_full_pilot(
            _public_document(),
            _gold_document(),
            _protocol(),
            provider_executor=forbidden_provider,
            adjudicator_executor=adjudicator_executor,
            artifact_root=artifact_root,
            batch_id=batch_id,
            model="gpt-test",
            reasoning_effort="low",
            private_key_path=key_path,
            trusted_public_key=trusted_public_key,
            repo_root=Path(__file__).parents[1],
            resume_provider_batch=True,
        )


def test_resume_rejects_tampered_provider_output(tmp_path):
    artifact_root = tmp_path / "artifacts"
    batch_id = "batch-tampered"

    def provider_executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    omc_plan_pilot.run_provider_pairs(
        _cases(),
        _protocol(),
        executor=provider_executor,
        artifact_root=artifact_root,
        batch_id=batch_id,
        model="gpt-test",
        reasoning_effort="low",
    )
    provider_batch_path = artifact_root / batch_id / "provider-batch.json"
    provider_batch = json.loads(provider_batch_path.read_text(encoding="utf-8"))
    provider_batch["providers"][0]["executions"][0]["raw_output"] += "tampered"
    provider_batch_path.write_text(json.dumps(provider_batch), encoding="utf-8")

    with pytest.raises(ValueError, match="provider output integrity mismatch"):
        omc_plan_pilot.load_completed_provider_batch(
            artifact_root,
            batch_id,
            expected_cases=_cases(),
            protocol=_protocol(),
            model="gpt-test",
            reasoning_effort="low",
        )


def test_resume_rejects_duplicate_plan_values_with_matching_output_hash(tmp_path):
    artifact_root = tmp_path / "artifacts"
    batch_id = "batch-duplicate-plan"

    def provider_executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    omc_plan_pilot.run_provider_pairs(
        _cases(),
        _protocol(),
        executor=provider_executor,
        artifact_root=artifact_root,
        batch_id=batch_id,
        model="gpt-test",
        reasoning_effort="low",
    )
    root = artifact_root / batch_id
    provider_batch_path = root / "provider-batch.json"
    provider_batch = json.loads(provider_batch_path.read_text(encoding="utf-8"))
    execution = provider_batch["providers"][0]["executions"][0]
    execution["plan"]["requirements_covered"] *= 2
    execution["raw_output"] = json.dumps(execution["plan"])
    provider_batch_path.write_text(json.dumps(provider_batch), encoding="utf-8")

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = next(
        item
        for item in manifest["executions"]
        if item["plan_execution_id"] == execution["plan_execution_id"]
    )
    metadata["raw_output_sha256"] = omc_plan_pilot.canonical_digest(
        execution["raw_output"]
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="provider plan contract mismatch"):
        omc_plan_pilot.load_completed_provider_batch(
            artifact_root,
            batch_id,
            expected_cases=_cases(),
            protocol=_protocol(),
            model="gpt-test",
            reasoning_effort="low",
        )


def test_resume_rejects_usage_claim_without_observed_execution_usage(tmp_path):
    artifact_root = tmp_path / "artifacts"
    batch_id = "batch-usage-tampered"

    def provider_executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    omc_plan_pilot.run_provider_pairs(
        _cases(),
        _protocol(),
        executor=provider_executor,
        artifact_root=artifact_root,
        batch_id=batch_id,
        model="gpt-test",
        reasoning_effort="low",
    )
    manifest_path = artifact_root / batch_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["token_measurement_status"] = "observed"
    manifest["cost_claim_allowed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="provider usage integrity mismatch"):
        omc_plan_pilot.load_completed_provider_batch(
            artifact_root,
            batch_id,
            expected_cases=_cases(),
            protocol=_protocol(),
            model="gpt-test",
            reasoning_effort="low",
        )


def test_resume_rejects_tampered_provider_producer(tmp_path):
    artifact_root = tmp_path / "artifacts"
    batch_id = "batch-producer-tampered"

    def provider_executor(*, provider_id, case, prompt, execution):
        plan = _plan(case["case_id"])
        return {"plan": plan, "raw_output": json.dumps(plan), "events_jsonl": ""}

    omc_plan_pilot.run_provider_pairs(
        _cases(),
        _protocol(),
        executor=provider_executor,
        artifact_root=artifact_root,
        batch_id=batch_id,
        model="gpt-test",
        reasoning_effort="low",
    )
    provider_batch_path = artifact_root / batch_id / "provider-batch.json"
    provider_batch = json.loads(provider_batch_path.read_text(encoding="utf-8"))
    provider_batch["providers"][0]["plan_producer"] = "tampered-producer"
    provider_batch_path.write_text(json.dumps(provider_batch), encoding="utf-8")

    with pytest.raises(ValueError, match="provider provenance mismatch"):
        omc_plan_pilot.load_completed_provider_batch(
            artifact_root,
            batch_id,
            expected_cases=_cases(),
            protocol=_protocol(),
            model="gpt-test",
            reasoning_effort="low",
        )


def test_full_pilot_validates_fixtures_before_provider_execution(tmp_path, monkeypatch):
    public_document = _public_document()
    public_document["corpus_sha256"] = "0" * 64
    provider_calls = []

    monkeypatch.setattr(
        omc_plan_pilot,
        "validate_private_key_location",
        lambda *args, **kwargs: object(),
    )

    def provider_executor(**kwargs):
        provider_calls.append(kwargs)
        raise AssertionError("provider must not run for invalid fixtures")

    with pytest.raises(ValueError, match="corpus"):
        run_full_pilot(
            public_document,
            _gold_document(),
            _protocol(),
            provider_executor=provider_executor,
            adjudicator_executor=lambda **kwargs: {},
            artifact_root=tmp_path / "artifacts",
            batch_id="invalid-fixture",
            model="gpt-test",
            reasoning_effort="low",
            private_key_path=tmp_path / "unused.key",
            trusted_public_key="unused",
            repo_root=Path(__file__).parents[1],
        )

    assert provider_calls == []
