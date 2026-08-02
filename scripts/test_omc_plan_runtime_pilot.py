import json
import base64
import math
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import omc_plan_runtime_pilot as runtime


def _protocol():
    return {
        "schema_version": 1,
        "benchmark_scope": "repository_grounded_skill_runtime",
        "providers": ["baseline-plan", "omc-plan"],
        "execution": {
            "sandbox": "read-only",
            "ignore_user_config": True,
            "ephemeral": True,
            "require_same_model_config": True,
            "allowed_workspace_delta": ".agents/skills/omc-plan/SKILL.md",
            "timeout_sec": 180,
        },
        "activation": {
            "required_for_omc": True,
            "forbidden_for_baseline": True,
            "proof_method": "output_nonce",
            "output_field": "runtime_activation_receipt",
            "baseline_sentinel": "unavailable",
        },
        "variability": {
            "development_case_count": 4,
            "runs_per_provider": 2,
            "max_metric_delta": 0.10,
        },
        "acceptance": {
            "case_count": 10,
            "minimum_executable_task_rate": 0.80,
            "maximum_output_token_ratio": 1.25,
            "maximum_total_token_increase_ratio": 0.05,
            "minimum_quality_gain_for_token_increase": 0.05,
        },
    }


def _case(index=1):
    case = {
        "case_id": f"observed-{index:02d}",
        "split": "holdout",
        "source_type": "observed_anonymized",
        "request": "Add bounded retry handling without changing the public API.",
        "provenance": {
            "source_sha256": "a" * 64,
            "anonymization_reviewed": True,
            "approved": True,
        },
        "context_files": {
            "src/service.py": "def run():\n    return None\n",
            "tests/test_service.py": "def test_run():\n    assert True\n",
        },
    }
    case["context_sha256"] = runtime.canonical_digest(case["context_files"])
    return case


def _gold(index=1):
    return {
        "case_id": f"observed-{index:02d}",
        "required_items": [{
            "id": "REQ-1",
            "description": "Required behavior",
            "critical": True,
            "weight": 1,
        }],
        "excluded_scope": [],
        "allowed_assumptions": [],
        "dependency_edges": [],
    }


def _signed_gold(cases, gold_items):
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).decode("ascii")
    document = {
        "schema_version": 1,
        "status": "signed_off",
        "producer": "fixture-author",
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": runtime.canonical_digest(gold_items),
        "cases": gold_items,
        "signoff": {
            "signer": "independent-reviewer",
            "signer_public_key": public_key,
        },
    }
    document["signoff"]["signature"] = base64.b64encode(
        private_key.sign(runtime.gold_signoff_payload(document))
    ).decode("ascii")
    return document, {public_key}


def _signer():
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).decode("ascii")
    return private_key, public_key


def _adjudications(sessions):
    from omc_plan_pilot import build_adjudication_provenance

    return [
        {
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh:{session['session_id']}",
            "items": [
                {
                    "item_index": index,
                    "requirement_hit_indexes": [0],
                    "scope_violation_indexes": [],
                    "task_requirement_links": [
                        {"task_index": 0, "requirement_indexes": [0]}
                    ],
                    "edge_requirement_links": [],
                    "unsupported_assumption_indexes": [],
                }
                for index, _ in enumerate(session["items"])
            ],
            "_adjudication_provenance": build_adjudication_provenance(session),
        }
        for session in sessions
    ]


def _metrics():
    return {
        "valid_case_count": 10,
        "provenance_complete_count": 10,
        "token_measurement_status": "observed",
        "baseline-plan": {
            "weighted_requirement_recall": 0.90,
            "critical_omission_count": 0,
            "executable_task_rate": 0.85,
            "unsupported_assumption_count": 1,
            "task_evidence_accuracy": 0.90,
            "output_tokens": 1000,
            "total_tokens": 10000,
        },
        "omc-plan": {
            "weighted_requirement_recall": 0.95,
            "critical_omission_count": 0,
            "executable_task_rate": 0.90,
            "unsupported_assumption_count": 1,
            "task_evidence_accuracy": 0.92,
            "output_tokens": 1200,
            "total_tokens": 10400,
        },
    }


def test_protocol_rejects_unfrozen_thresholds():
    protocol = _protocol()
    del protocol["acceptance"]["maximum_output_token_ratio"]
    with pytest.raises(ValueError, match="acceptance"):
        runtime.validate_runtime_protocol(protocol)

    protocol = _protocol()
    protocol["acceptance"]["maximum_output_token_ratio"] = 1.20
    with pytest.raises(ValueError, match="frozen"):
        runtime.validate_runtime_protocol(protocol)


def test_corpus_requires_ten_observed_anonymized_cases_and_approved_gold():
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    runtime.validate_runtime_corpus(
        cases, gold, expected_count=10, trusted_signer_public_keys=trusted
    )

    cases[0]["source_type"] = "synthetic_anonymized"
    with pytest.raises(ValueError, match="observed_anonymized"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_corpus_rejects_unsafe_context_paths():
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    cases[0]["context_files"] = {"../secret": "no"}
    with pytest.raises(ValueError, match="context path"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_corpus_rejects_context_or_gold_hash_tampering():
    cases = [_case(index) for index in range(1, 11)]
    gold_items = [_gold(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, gold_items)
    cases[0]["context_files"]["src/service.py"] = "tampered"
    with pytest.raises(ValueError, match="context hash"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )

    cases = [_case(index) for index in range(1, 11)]
    gold["cases"][0]["required_items"].append({"id": "REQ-2", "critical": False, "weight": 1})
    with pytest.raises(ValueError, match="gold hash"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_corpus_rejects_untrusted_or_unsigned_gold():
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    with pytest.raises(ValueError, match="trusted"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=set()
        )
    gold["signoff"]["signature"] = base64.b64encode(b"invalid" * 10).decode("ascii")
    with pytest.raises(ValueError, match="signature"):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_activation_uses_hidden_output_nonce_instead_of_unsupported_events():
    protocol = runtime.validate_runtime_protocol(_protocol())
    assert protocol["activation"]["proof_method"] == "output_nonce"
    assert "accepted_event_types" not in protocol["activation"]

    evidence = runtime.require_activation_receipt(
        {"runtime_activation_receipt": "secret-nonce"},
        provider_id="omc-plan",
        expected_receipt="secret-nonce",
        baseline_sentinel="unavailable",
    )
    assert evidence["status"] == "observed"

    with pytest.raises(ValueError, match="activation_receipt_mismatch"):
        runtime.require_activation_receipt(
            {"runtime_activation_receipt": "unavailable"},
            provider_id="omc-plan",
            expected_receipt="secret-nonce",
            baseline_sentinel="unavailable",
        )


def test_baseline_must_return_activation_sentinel():
    with pytest.raises(ValueError, match="baseline_skill_activation"):
        runtime.require_activation_receipt(
            {"runtime_activation_receipt": "secret-nonce"},
            provider_id="baseline-plan",
            expected_receipt="secret-nonce",
            baseline_sentinel="unavailable",
        )


def test_workspace_parity_allows_only_the_skill_file_delta():
    baseline = {"src/service.py": "hash-a", "tests/test_service.py": "hash-b"}
    omc = {
        **baseline,
        ".agents/skills/omc-plan/SKILL.md": "hash-skill",
    }
    runtime.validate_workspace_parity(
        baseline, omc, allowed_delta=".agents/skills/omc-plan/SKILL.md"
    )
    omc["AGENTS.md"] = "unexpected"
    with pytest.raises(ValueError, match="workspace_mismatch"):
        runtime.validate_workspace_parity(
            baseline, omc, allowed_delta=".agents/skills/omc-plan/SKILL.md"
        )


def test_codex_command_keeps_project_skill_discovery_enabled(tmp_path):
    command = runtime.build_codex_command(
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=tmp_path / "output.json",
    )
    assert "--ignore-user-config" in command
    assert "--ignore-rules" not in command
    assert "--ephemeral" in command
    assert command[command.index("--output-schema") + 1] == str(
        (Path.cwd() / "schema.json").resolve()
    )


def test_execute_provider_preserves_activation_and_usage(tmp_path, monkeypatch):
    output_path = tmp_path / "output.json"
    skill_hash = "c" * 64

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            json.dumps({
                "type": "skill.activated",
                "skill_name": "omc-plan",
                "skill_sha256": skill_hash,
            }),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }),
        ])

    def fake_run(command, **kwargs):
        assert kwargs["timeout"] == 180
        output_path.write_text(json.dumps({
            "requirements": [],
            "runtime_activation_receipt": "secret-nonce",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    result = runtime.execute_provider(
        provider_id="omc-plan",
        request="Plan this change",
        workspace=tmp_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        sandbox="read-only",
        output_schema="schema.json",
        output_path=output_path,
        skill_sha256=skill_hash,
        expected_activation_receipt="secret-nonce",
        baseline_sentinel="unavailable",
        timeout_sec=180,
    )
    assert result["activation"]["status"] == "observed"
    assert "runtime_activation_receipt" not in result["plan"]
    assert result["usage"]["total_tokens"] == 15


def test_execute_provider_persists_timeout_failure_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "failure.json"

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        runtime.execute_provider(
            provider_id="baseline-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=tmp_path / "output.json",
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            failure_receipt_path=receipt,
        )
    assert json.loads(receipt.read_text(encoding="utf-8"))["reason_code"] == "provider_timeout"


def test_execute_provider_persists_invalid_output_failure_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "failure.json"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: Completed())
    with pytest.raises(RuntimeError, match="output"):
        runtime.execute_provider(
            provider_id="baseline-plan",
            request="Plan this change",
            workspace=tmp_path,
            codex_binary="codex",
            model="gpt-test",
            reasoning_effort="low",
            sandbox="read-only",
            output_schema="schema.json",
            output_path=tmp_path / "missing.json",
            skill_sha256="c" * 64,
            expected_activation_receipt="secret-nonce",
            baseline_sentinel="unavailable",
            timeout_sec=180,
            failure_receipt_path=receipt,
        )
    assert json.loads(receipt.read_text(encoding="utf-8"))["reason_code"] == "provider_output_invalid"


def test_variability_gate_blocks_large_repeat_delta():
    stable = {
        "baseline-plan": [{"recall": 0.9, "executable": 0.8}, {"recall": 0.85, "executable": 0.82}],
        "omc-plan": [{"recall": 0.9, "executable": 0.9}, {"recall": 0.88, "executable": 0.85}],
    }
    assert runtime.evaluate_variability(stable, max_delta=0.10)["status"] == "pass"
    unstable = deepcopy(stable)
    unstable["omc-plan"][1]["recall"] = 0.60
    assert runtime.evaluate_variability(unstable, max_delta=0.10)["status"] == "blocked"

    invalid = deepcopy(stable)
    invalid["omc-plan"][1]["recall"] = math.nan
    result = runtime.evaluate_variability(invalid, max_delta=0.10)
    assert result["status"] == "blocked"
    assert "omc-plan:recall:invalid" in result["failed_metrics"]


def test_replacement_requires_all_quality_cost_and_provenance_gates():
    result = runtime.decide_replacement(_metrics(), _protocol()["acceptance"])
    assert result["decision"] == "REPLACEABLE"

    failed = _metrics()
    failed["omc-plan"]["unsupported_assumption_count"] = 2
    result = runtime.decide_replacement(failed, _protocol()["acceptance"])
    assert result["decision"] == "NOT_PROVEN"
    assert "unsupported_assumptions" in result["failed_gates"]


def test_replacement_rejects_unjustified_token_increase_and_invalid_run():
    metrics = _metrics()
    metrics["omc-plan"]["weighted_requirement_recall"] = 0.90
    metrics["omc-plan"]["executable_task_rate"] = 0.85
    metrics["omc-plan"]["total_tokens"] = 10501
    result = runtime.decide_replacement(metrics, _protocol()["acceptance"])
    assert result["decision"] == "NOT_PROVEN"
    assert "total_tokens" in result["failed_gates"]

    metrics = _metrics()
    metrics["provenance_complete_count"] = 9
    result = runtime.decide_replacement(metrics, _protocol()["acceptance"])
    assert result["decision"] == "INVALID_RUN"


def test_replacement_rejects_non_finite_or_out_of_range_metrics():
    metrics = _metrics()
    metrics["omc-plan"]["weighted_requirement_recall"] = math.nan
    result = runtime.decide_replacement(metrics, _protocol()["acceptance"])
    assert result["decision"] == "INVALID_RUN"
    assert result["reason_code"] == "provider_metrics_invalid"


def test_runtime_metrics_are_derived_from_scores_and_observed_usage():
    scores = {
        provider_id: [{
            "weighted_coverage": 0.9,
            "critical_omissions": [],
            "executable_step_rate": 0.8,
            "unsupported_assumptions": [],
            "bloat_ratio": 0.1,
        }]
        for provider_id in runtime.PROVIDERS
    }
    executions = [
        {
            "provider_id": provider_id,
            "case_id": "observed-01",
            "usage": {
                "status": "observed",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        }
        for provider_id in runtime.PROVIDERS
    ]
    metrics = runtime.build_runtime_metrics(
        scores, executions, expected_case_count=1
    )
    assert metrics["token_measurement_status"] == "observed"
    assert metrics["omc-plan"]["task_evidence_accuracy"] == 0.9
    assert metrics["omc-plan"]["total_tokens"] == 15


def test_runtime_metrics_reject_usage_total_mismatch():
    scores = {
        provider_id: [{
            "weighted_coverage": 0.9,
            "critical_omissions": [],
            "executable_step_rate": 0.8,
            "unsupported_assumptions": [],
            "bloat_ratio": 0.1,
        }]
        for provider_id in runtime.PROVIDERS
    }
    executions = [
        {
            "provider_id": provider_id,
            "case_id": "observed-01",
            "usage": {
                "status": "observed",
                "input_tokens": 100,
                "output_tokens": 100,
                "total_tokens": 1,
            },
        }
        for provider_id in runtime.PROVIDERS
    ]
    with pytest.raises(ValueError, match="usage integrity mismatch"):
        runtime.build_runtime_metrics(scores, executions, expected_case_count=1)


def test_assess_cli_can_call_functions_declared_after_main(tmp_path):
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps(_metrics()), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(runtime.__file__)),
            "assess",
            str(Path(runtime.__file__).parent / "fixtures/omc_plan_runtime_protocol.json"),
            str(metrics),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["decision"] == "REPLACEABLE"


def test_blind_batch_contains_ten_complete_pairs_without_provider_ids():
    cases = [_case(index) for index in range(1, 11)]
    executions = []
    for index, case in enumerate(cases, start=1):
        for provider_id in ("baseline-plan", "omc-plan"):
            executions.append({
                "case_id": case["case_id"],
                "provider_id": provider_id,
                "plan_execution_id": f"exec-{index}-{provider_id}",
                "plan": {"requirements": [], "tasks": [], "dependency_edges": [], "assumptions": []},
                "raw_output": "{}",
            })
    sessions, private_mapping = runtime.build_runtime_blind_batch(
        executions, batch_id="runtime-batch", session_count=5
    )
    assert len(sessions) == 5
    assert sum(len(session["items"]) for session in sessions) == 20
    assert "provider_id" not in json.dumps(sessions)
    assert len(private_mapping) == 20

    with pytest.raises(ValueError, match="exactly 5 sessions"):
        runtime.build_runtime_blind_batch(
            executions, batch_id="runtime-batch", session_count=2
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda gold: gold[0]["required_items"][0].update({"description": ""}), "description"),
        (lambda gold: gold[0]["required_items"][0].update({"weight": 0}), "weight"),
        (lambda gold: gold[0].update({"excluded_scope": [1]}), "excluded_scope"),
        (lambda gold: gold[0].update({"allowed_assumptions": [""]}), "allowed_assumptions"),
        (lambda gold: gold[0].update({"dependency_edges": [{"before": "REQ-1"}]}), "dependency_edges"),
    ],
)
def test_corpus_rejects_incomplete_gold_schema(mutate, message):
    cases = [_case(index) for index in range(1, 11)]
    gold_items = [_gold(index) for index in range(1, 11)]
    for item in gold_items:
        item["required_items"][0]["description"] = "Required behavior"
    mutate(gold_items)
    gold, trusted = _signed_gold(cases, gold_items)
    with pytest.raises(ValueError, match=message):
        runtime.validate_runtime_corpus(
            cases, gold, expected_count=10, trusted_signer_public_keys=trusted
        )


def test_runtime_batch_executes_ten_pairs_and_persists_blind_artifacts(tmp_path, monkeypatch):
    cases = [_case(index) for index in range(1, 11)]
    gold, trusted = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# OMC Plan\n", encoding="utf-8")
    skill_hash = runtime._sha256_text(skill_path.read_text(encoding="utf-8"))
    calls = []

    def fake_probe(**kwargs):
        return {
            "status": "pass",
            "scope": "non_scored_activation_probe",
            "skill_sha256": skill_hash,
            "model": kwargs["model"],
            "reasoning_effort": kwargs["reasoning_effort"],
        }

    def fake_execute_provider(**kwargs):
        calls.append((kwargs["provider_id"], kwargs["request"]))
        plan = {
            "requirements_covered": ["REQ-1"],
            "scope_items": [],
            "dependency_edges": [],
            "tasks": [{
                "id": "T-1",
                "target": "src/service.py",
                "action": "Implement bounded retry handling",
                "verify": "Run the service tests",
                "supports": ["REQ-1"],
            }],
            "assumptions": [],
            "decisions_required": [],
        }
        return {
            "provider_id": kwargs["provider_id"],
            "plan": plan,
            "raw_output": json.dumps(plan),
            "events_jsonl": "",
            "activation": {"status": "observed", "skill_sha256": skill_hash},
            "usage": {"status": "observed", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "command_sha256": "a" * 64,
            "prompt_sha256": runtime._sha256_text(
                runtime.build_provider_prompt(kwargs["provider_id"], kwargs["request"])
            ),
        }

    monkeypatch.setattr(runtime, "run_activation_probe", fake_probe)
    monkeypatch.setattr(runtime, "execute_provider", fake_execute_provider)
    runtime_signer, runtime_signer_public_key = _signer()
    result = runtime.run_runtime_batch(
        protocol=_protocol(),
        cases=cases,
        gold_document=gold,
        trusted_signer_public_keys=trusted,
        skill_path=skill_path,
        codex_binary="codex",
        model="gpt-test",
        reasoning_effort="low",
        output_schema=Path(runtime.__file__).parent / "fixtures/omc_plan_output_schema.json",
        artifact_root=tmp_path / "batch",
        batch_id="runtime-batch",
        session_count=5,
        runtime_signer_private_key=runtime_signer,
        runtime_signer_public_key=runtime_signer_public_key,
    )
    assert len(calls) == 20
    assert result["activation_probe"]["status"] == "pass"
    assert len(result["provider_batch"]["executions"]) == 20
    assert len(result["blind_sessions"]) == 5
    assert (tmp_path / "batch/provider-batch.json").is_file()
    assert (tmp_path / "batch/private-mapping.json").is_file()
    assert (tmp_path / "batch/blind-sessions.json").is_file()
    assert result["provider_batch"]["runtime_attestation"]["signer_public_key"] == runtime_signer_public_key

    adjudicator_key, adjudicator_public_key = _signer()
    report = runtime.finalize_runtime_batch(
        protocol=_protocol(),
        cases=cases,
        gold_document=gold,
        trusted_signer_public_keys=trusted,
        provider_batch=result["provider_batch"],
        blind_sessions=json.loads(
            (tmp_path / "batch/blind-sessions.json").read_text(encoding="utf-8")
        ),
        private_mapping=result["private_mapping"],
        adjudication_results=_adjudications(result["blind_sessions"]),
        adjudicator_private_key=adjudicator_key,
        trusted_adjudicator_public_key=adjudicator_public_key,
        adjudicator="independent-test-adjudicator",
        artifact_root=tmp_path / "finalized-run-output",
        trusted_runtime_signer_public_key=runtime_signer_public_key,
    )
    assert report["decision"]["decision"] == "REPLACEABLE"


def test_runtime_batch_rejects_artifact_root_inside_repository(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    with pytest.raises(ValueError, match="outside the repository"):
        runtime._validate_artifact_root(repo_root / "artifacts", repo_root=repo_root)


def test_finalize_runtime_batch_seals_scores_and_decides(tmp_path):
    from omc_plan_pilot import build_adjudication_provenance

    cases = [_case(index) for index in range(1, 11)]
    gold, trusted_gold = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    executions = []
    skill_sha256 = "a" * 64
    for case in cases:
        for provider_id in runtime.PROVIDERS:
            plan = {
                "requirements_covered": ["REQ-1"],
                "scope_items": [],
                "dependency_edges": [],
                "tasks": [{
                    "id": "T-1",
                    "target": "src/service.py",
                    "action": "Implement bounded retry handling",
                    "verify": "Run the service tests",
                    "supports": ["REQ-1"],
                }],
                "assumptions": [],
                "decisions_required": [],
            }
            executions.append({
                "provider_id": provider_id,
                "case_id": case["case_id"],
                "plan_execution_id": f"runtime:{case['case_id']}:{provider_id}",
                "plan": plan,
                "raw_output": json.dumps(plan, ensure_ascii=False, sort_keys=True),
                "activation": {
                    "status": "observed",
                    "skill_sha256": skill_sha256,
                },
                "prompt_sha256": runtime._sha256_text(
                    runtime.build_provider_prompt(provider_id, case["request"])
                ),
                "usage": {
                    "status": "observed",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            })
    sessions, mapping = runtime.build_runtime_blind_batch(
        executions,
        batch_id="runtime-finalize",
        session_count=5,
        gold_document=gold,
    )
    adjudications = []
    for session in sessions:
        adjudications.append({
            "session_id": session["session_id"],
            "adjudication_execution_id": f"fresh:{session['session_id']}",
            "items": [
                {
                    "item_index": index,
                    "requirement_hit_indexes": [0],
                    "scope_violation_indexes": [],
                    "task_requirement_links": [
                        {"task_index": 0, "requirement_indexes": [0]}
                    ],
                    "edge_requirement_links": [],
                    "unsupported_assumption_indexes": [],
                }
                for index, _ in enumerate(session["items"])
            ],
            "_adjudication_provenance": build_adjudication_provenance(session),
        })
    adjudicator_key = Ed25519PrivateKey.generate()
    adjudicator_public_key = base64.b64encode(
        adjudicator_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    runtime_signer, runtime_signer_public_key = _signer()
    provider_batch = {
        "schema_version": 1,
        "batch_id": "runtime-finalize",
        "protocol_sha256": runtime.canonical_digest(_protocol()),
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": gold["gold_sha256"],
        "skill_sha256": skill_sha256,
        "activation_probe": {"status": "pass", "skill_sha256": skill_sha256},
        "activation_probe_sha256": "",
        "executions": executions,
    }
    provider_batch["activation_probe_sha256"] = runtime.canonical_digest(
        provider_batch["activation_probe"]
    )
    provider_batch["runtime_attestation"] = runtime.build_runtime_attestation(
        provider_batch,
        sessions,
        mapping,
        private_key=runtime_signer,
        signer_public_key=runtime_signer_public_key,
    )
    report = runtime.finalize_runtime_batch(
        protocol=_protocol(),
        cases=cases,
        gold_document=gold,
        trusted_signer_public_keys=trusted_gold,
        provider_batch=provider_batch,
        blind_sessions=sessions,
        private_mapping=mapping,
        adjudication_results=adjudications,
        adjudicator_private_key=adjudicator_key,
        trusted_adjudicator_public_key=adjudicator_public_key,
        adjudicator="independent-test-adjudicator",
        artifact_root=tmp_path / "finalized",
        trusted_runtime_signer_public_key=runtime_signer_public_key,
    )
    assert report["decision"]["decision"] == "REPLACEABLE"
    assert report["metrics"]["omc-plan"]["task_evidence_accuracy"] == 1.0
    assert (tmp_path / "finalized/runtime-final-report.json").is_file()


def test_finalize_rejects_tampered_provider_batch():
    runtime_signer, runtime_signer_public_key = _signer()
    provider_batch = {
        "schema_version": 1,
        "batch_id": "runtime-tamper",
        "activation_probe": {"status": "pass", "skill_sha256": "a" * 64},
        "activation_probe_sha256": "",
        "executions": [],
    }
    provider_batch["activation_probe_sha256"] = runtime.canonical_digest(
        provider_batch["activation_probe"]
    )
    provider_batch["runtime_attestation"] = runtime.build_runtime_attestation(
        provider_batch,
        [],
        {},
        private_key=runtime_signer,
        signer_public_key=runtime_signer_public_key,
    )
    provider_batch["batch_id"] = "tampered"
    with pytest.raises(ValueError, match="runtime attestation mismatch"):
        runtime.verify_runtime_attestation(
            provider_batch,
            [],
            {},
            trusted_public_key=runtime_signer_public_key,
        )


def test_finalize_rejects_empty_task_plan_before_scoring():
    executions = []
    for index in range(1, 11):
        for provider_id in runtime.PROVIDERS:
            executions.append({
                "provider_id": provider_id,
                "case_id": f"observed-{index:02d}",
                "plan_execution_id": f"runtime:observed-{index:02d}:{provider_id}",
                "plan": {"tasks": []},
                "raw_output": "{}",
                "activation": {"status": "observed"},
                "usage": {
                    "status": "observed",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            })
    with pytest.raises(ValueError, match="non-empty tasks"):
        runtime.validate_runtime_executions(executions, expected_case_count=10)


def test_runtime_provenance_rejects_batch_not_bound_to_current_inputs():
    protocol = _protocol()
    cases = [_case(index) for index in range(1, 11)]
    gold, _ = _signed_gold(cases, [_gold(index) for index in range(1, 11)])
    skill_sha256 = "a" * 64
    executions = []
    for case in cases:
        for provider_id in runtime.PROVIDERS:
            executions.append({
                "provider_id": provider_id,
                "case_id": case["case_id"],
                "prompt_sha256": runtime._sha256_text(
                    runtime.build_provider_prompt(provider_id, case["request"])
                ),
                "activation": {
                    "status": "observed",
                    "skill_sha256": skill_sha256,
                },
            })
    provider_batch = {
        "protocol_sha256": runtime.canonical_digest(protocol),
        "corpus_sha256": runtime.canonical_digest(cases),
        "gold_sha256": gold["gold_sha256"],
        "skill_sha256": skill_sha256,
        "activation_probe": {"status": "pass", "skill_sha256": skill_sha256},
        "executions": executions,
    }
    runtime.validate_runtime_provenance(
        provider_batch,
        protocol=protocol,
        cases=cases,
        gold_document=gold,
    )

    provider_batch["corpus_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime input provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
        )

    provider_batch["corpus_sha256"] = runtime.canonical_digest(cases)
    provider_batch["gold_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime input provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
        )

    provider_batch["gold_sha256"] = gold["gold_sha256"]
    provider_batch["activation_probe"]["skill_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime input provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
        )

    provider_batch["activation_probe"]["skill_sha256"] = skill_sha256
    provider_batch["executions"][0]["prompt_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="runtime execution provenance mismatch"):
        runtime.validate_runtime_provenance(
            provider_batch,
            protocol=protocol,
            cases=cases,
            gold_document=gold,
        )
