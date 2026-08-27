from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from omc_n_child_acceptance import (
    EXPECTED_CASES,
    _build_acceptance_report_from_results,
    _ledger_metrics,
    build_acceptance_manifest,
    build_acceptance_report,
    canonical_sha256,
    load_authoritative_acceptance_results,
    run_acceptance,
    validate_acceptance_manifest,
    write_acceptance_fixture_packets,
)


def _manifest() -> dict:
    cases = []
    for index, expected in enumerate(EXPECTED_CASES, start=1):
        cases.append(
            {
                **expected,
                "request_sha256": f"{'a' * 63}{index}",
            }
        )
    payload = {
        "schema_version": "omc-n-child-acceptance/v1",
        "acceptance_id": "n-child-p0-v1",
        "source_commit": "1" * 40,
        "cases": cases,
        "thresholds": {
            "required_case_count": 5,
            "required_success_count": 2,
            "max_duplicate_executions": 0,
            "max_applied_scope_violations": 0,
            "max_accepted_budget_violations": 0,
            "max_applied_failed_patches": 0,
            "max_missing_receipts": 0,
        },
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _results(manifest: dict) -> list[dict]:
    results = []
    for case in manifest["cases"]:
        if case["kind"] == "success":
            raw_result = {"status": "completed", "children": []}
        elif case["kind"] == "failure":
            raw_result = {
                "status": "review_required",
                "reason_code": "child_not_succeeded",
                "children": [{"status": "failed"}],
            }
        elif case["kind"] == "timeout":
            raw_result = {
                "status": "review_required",
                "reason_code": "child_not_succeeded",
                "children": [{"status": "timeout"}],
            }
        else:
            raw_result = {
                "status": "review_required",
                "reason_code": "scope_path_outside_target",
                "children": [],
            }
        receipt = {
            "schema_version": "omc-n-child-acceptance/v1",
            "acceptance_id": manifest["acceptance_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "case_id": case["case_id"],
            "kind": case["kind"],
            "request_sha256": case["request_sha256"],
            "source_commit": manifest["source_commit"],
            "raw_result": raw_result,
            "metrics": {
                "duplicate_executions": 0,
                "applied_scope_violations": 0,
                "accepted_budget_violations": 0,
                "applied_failed_patches": 0,
            },
            "metric_evidence": {
                "status": "verified",
                "external_call_count": case["child_count"] if case["kind"] == "success" else 1,
                "child_statuses": (
                    ["succeeded"] * case["child_count"]
                    if case["kind"] == "success"
                    else (["failed"] if case["kind"] == "failure"
                          else ["timeout"] if case["kind"] == "timeout"
                          else ["failed"])
                ),
                "scope_violations_detected": 1 if case["kind"] == "policy_violation" else 0,
                "ledger_sha256s": {},
            },
        }
        results.append({
            "case_id": case["case_id"],
            "status": case["expected_status"],
            "receipt_sha256": canonical_sha256(receipt),
            "receipt": receipt,
        })
    return results


def test_manifest_requires_exact_frozen_five_case_contract():
    manifest = _manifest()

    assert validate_acceptance_manifest(manifest)["manifest_sha256"] == manifest["manifest_sha256"]

    tampered = deepcopy(manifest)
    tampered["cases"][0]["child_count"] = 4
    with pytest.raises(ValueError, match="manifest_hash_mismatch"):
        validate_acceptance_manifest(tampered)

    missing = _manifest()
    missing["cases"].pop()
    missing.pop("manifest_sha256")
    missing["manifest_sha256"] = canonical_sha256(missing)
    with pytest.raises(ValueError, match="acceptance_case_count_invalid"):
        validate_acceptance_manifest(missing)

    changed_case = _manifest()
    changed_case["cases"][0]["case_id"] = "replacement-case"
    changed_case.pop("manifest_sha256")
    changed_case["manifest_sha256"] = canonical_sha256(changed_case)
    with pytest.raises(ValueError, match="acceptance_case_catalog_invalid"):
        validate_acceptance_manifest(changed_case)


def test_report_passes_only_when_every_expected_outcome_and_receipt_is_present():
    manifest = _manifest()

    report = _build_acceptance_report_from_results(manifest, _results(manifest))

    assert report["verdict"] == "PASS"
    assert report["case_count"] == 5
    assert report["success_case_count"] == 2

    incomplete = _results(manifest)
    incomplete[2]["receipt_sha256"] = None
    report = _build_acceptance_report_from_results(manifest, incomplete)
    assert report["verdict"] == "FAIL"
    assert report["failures"] == [
        "missing_receipt:provider-failure-3-child",
        "threshold_exceeded:missing_receipts",
    ]

    forged = _results(manifest)
    forged[0]["receipt"]["source_commit"] = "2" * 40
    report = _build_acceptance_report_from_results(manifest, forged)
    assert report["verdict"] == "FAIL"
    assert "receipt_hash_mismatch:success-3-child" in report["failures"]

    wrong_failure = _results(manifest)
    wrong_failure[2]["receipt"]["raw_result"] = {
        "status": "review_required",
        "reason_code": "child_not_succeeded",
        "children": [{"status": "timeout"}],
    }
    wrong_failure[2]["receipt_sha256"] = canonical_sha256(wrong_failure[2]["receipt"])
    report = _build_acceptance_report_from_results(manifest, wrong_failure)
    assert "case_semantic_mismatch:provider-failure-3-child" in report["failures"]


def test_report_rejects_unknown_results_and_metrics_outside_receipt():
    manifest = _manifest()
    results = _results(manifest)
    results.append(deepcopy(results[0]) | {"case_id": "unknown-case"})

    report = _build_acceptance_report_from_results(manifest, results)

    assert "unexpected_result:unknown-case" in report["failures"]

    tampered = _results(manifest)
    tampered[0]["duplicate_executions"] = 0
    tampered[0]["receipt"]["metrics"]["duplicate_executions"] = 2
    tampered[0]["receipt_sha256"] = canonical_sha256(tampered[0]["receipt"])
    report = _build_acceptance_report_from_results(manifest, tampered)
    assert report["totals"]["duplicate_executions"] == 2
    assert "threshold_exceeded:duplicate_executions" in report["failures"]


def test_runner_uses_frozen_commit_and_isolated_clone_per_case(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "tracked.txt").write_text("frozen\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "frozen"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    manifest = _manifest()
    manifest["source_commit"] = commit
    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    seen_roots: list[Path] = []
    for case in manifest["cases"]:
        packet = {
            "schema_version": "omc-n-child-acceptance-case/v1",
            "case_id": case["case_id"],
            "request": {"schema_version": "omc-n-child-dag/v2"},
            "approval": {"operator_confirmed": True},
        }
        case["request_sha256"] = canonical_sha256(packet)
        (packet_root / f"{case['case_id']}.json").write_text(
            json.dumps(packet), encoding="utf-8"
        )
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = canonical_sha256(manifest)

    def fake_execute(packet, workspace, case_artifact, provider_adapter):
        del packet, provider_adapter
        seen_roots.append(workspace)
        assert workspace != source
        assert (workspace / "tracked.txt").read_text(encoding="utf-8") == "frozen\n"
        (workspace / "tracked.txt").write_text("isolated\n", encoding="utf-8")
        kind = manifest["cases"][len(seen_roots) - 1]["kind"]
        child_status = {
            "success": "succeeded",
            "failure": "failed",
            "timeout": "timeout",
            "policy_violation": "failed",
        }[kind]
        executed_count = (
            manifest["cases"][len(seen_roots) - 1]["child_count"]
            if kind == "success"
            else 1
        )
        result = {
            "status": "completed" if kind == "success" else "review_required",
            "reason_code": "scope_policy_violation" if kind == "policy_violation" else "fixture_result",
            "dag_id": case["case_id"],
            "children": [
                {"child_id": f"child-{index}", "status": child_status}
                for index in range(1, executed_count + 1)
            ],
            "completed_child_ids": (
                [f"child-{index}" for index in range(1, executed_count + 1)]
                if kind == "success"
                else []
            ),
            "pending_child_ids": [],
            "failed_child_ids": (
                []
                if kind == "success"
                else [f"child-{index}" for index in range(1, executed_count + 1)]
            ),
            "external_call_count": executed_count,
            "total_elapsed_sec": 0.1,
            "total_output_chars": executed_count,
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
        }
        children = []
        for index in range(1, executed_count + 1):
            children.append({
                "child_id": f"child-{index}",
                "idempotency_key": f"{kind}-{index}",
                "attempt_count": 1,
                "status": child_status,
                "max_total_elapsed_sec": 10,
                "max_output_chars": 100,
                "max_total_tokens": 100,
                "outcome": {
                    "status": child_status,
                    "reason_code": result["reason_code"],
                    "elapsed_sec": 0.1,
                    "output_chars": 1,
                    "token_usage": {
                        "input_tokens": 2,
                        "output_tokens": 3,
                        "total_tokens": 5,
                    },
                    "patch_applied": kind == "success",
                    "scope_violation_detected": kind == "policy_violation",
                },
            })
        (case_artifact / "child-ledger.json").write_text(json.dumps({
            "schema_version": 1, "revision": len(children), "entries": children,
        }), encoding="utf-8")
        (case_artifact / "dag-ledger.json").write_text(json.dumps({
            "schema_version": 1, "revision": 1, "dag": result,
        }), encoding="utf-8")
        return result

    results = run_acceptance(
        manifest,
        packet_root=packet_root,
        source_root=source,
        artifact_root=tmp_path / "artifacts",
        provider_adapter=tmp_path / "adapter",
        case_executor=fake_execute,
    )

    assert len(results) == 5
    assert len({str(path) for path in seen_roots}) == 5
    assert (source / "tracked.txt").read_text(encoding="utf-8") == "frozen\n"
    assert _build_acceptance_report_from_results(manifest, results)["verdict"] == "PASS"
    stored = json.loads(
        (tmp_path / "artifacts" / "success-3-child" / "receipt.json").read_text(encoding="utf-8")
    )
    assert canonical_sha256(stored["receipt"]) == stored["receipt_sha256"]

    authoritative = load_authoritative_acceptance_results(
        manifest,
        tmp_path / "artifacts",
    )
    assert authoritative == results
    assert build_acceptance_report(manifest, tmp_path / "artifacts")["verdict"] == "PASS"


def test_public_report_builder_rejects_unattested_result_list():
    manifest = _manifest()

    with pytest.raises(ValueError, match="acceptance_artifact_root_invalid"):
        build_acceptance_report(manifest, _results(manifest))


def test_authoritative_finalize_rejects_rehashed_receipt_that_disagrees_with_ledgers(tmp_path):
    manifest = _manifest()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    results = _results(manifest)
    for result in results:
        case_root = artifact_root / result["case_id"]
        case_root.mkdir()
        receipt = result["receipt"]
        (case_root / "receipt.json").write_text(
            json.dumps({
                "receipt_sha256": result["receipt_sha256"],
                "receipt": receipt,
            }),
            encoding="utf-8",
        )
        (case_root / "dag-ledger.json").write_text(
            json.dumps({"schema_version": 1, "revision": 1, "dag": receipt["raw_result"]}),
            encoding="utf-8",
        )
        (case_root / "child-ledger.json").write_text(
            json.dumps({"schema_version": 1, "revision": 0, "entries": []}),
            encoding="utf-8",
        )
    results[0]["receipt"]["metrics"]["duplicate_executions"] = 0
    results[0]["receipt"]["metric_evidence"]["ledger_sha256s"] = {}
    results[0]["receipt_sha256"] = canonical_sha256(results[0]["receipt"])
    first_receipt = artifact_root / results[0]["case_id"] / "receipt.json"
    first_receipt.write_text(
        json.dumps({
            "receipt_sha256": results[0]["receipt_sha256"],
            "receipt": results[0]["receipt"],
        }),
        encoding="utf-8",
    )
    (artifact_root / "results.json").write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(ValueError, match="acceptance_ledger_evidence_invalid"):
        load_authoritative_acceptance_results(manifest, artifact_root)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda entry: entry.pop("attempt_count"),
        lambda entry: entry.update({"attempt_count": "1"}),
        lambda entry: entry.update({"max_total_elapsed_sec": "10"}),
        lambda entry: entry["outcome"].pop("patch_applied"),
        lambda entry: entry["outcome"].update({"scope_violation_detected": "false"}),
        lambda entry: entry["outcome"].update({"token_usage": {"total_tokens": 1}}),
    ],
)
def test_ledger_metrics_marks_malformed_terminal_entry_unverified(tmp_path, mutation):
    artifact = tmp_path / "case"
    artifact.mkdir()
    raw_result = {
        "status": "completed",
        "reason_code": "dag_completed",
        "children": [{"child_id": "child-1", "status": "succeeded"}],
        "external_call_count": 1,
    }
    entry = {
        "child_id": "child-1",
        "idempotency_key": "run-child-1",
        "attempt_count": 1,
        "status": "succeeded",
        "max_total_elapsed_sec": 10,
        "max_output_chars": 100,
        "max_total_tokens": 10,
        "outcome": {
            "status": "succeeded",
            "reason_code": "executor_completed",
            "elapsed_sec": 1.0,
            "output_chars": 1,
            "token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "patch_applied": True,
            "scope_violation_detected": False,
        },
    }
    mutation(entry)
    (artifact / "dag-ledger.json").write_text(
        json.dumps({"schema_version": 1, "revision": 1, "dag": raw_result}),
        encoding="utf-8",
    )
    (artifact / "child-ledger.json").write_text(
        json.dumps({"schema_version": 1, "revision": 1, "entries": [entry]}),
        encoding="utf-8",
    )

    metrics, evidence = _ledger_metrics(artifact)

    assert metrics == {
        "duplicate_executions": 0,
        "applied_scope_violations": 0,
        "accepted_budget_violations": 0,
        "applied_failed_patches": 0,
    }
    assert evidence == {
        "status": "unverified",
        "reason_code": "execution_ledger_invalid",
    }


def test_runner_records_fail_closed_receipt_and_continues_after_executor_exception(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "tracked.txt").write_text("frozen\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "frozen"], check=True)
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    manifest = _manifest()
    manifest["source_commit"] = commit
    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    for case in manifest["cases"]:
        packet = {"schema_version": "omc-n-child-acceptance-case/v1", "case_id": case["case_id"], "request": {}, "approval": {}}
        case["request_sha256"] = canonical_sha256(packet)
        (packet_root / f"{case['case_id']}.json").write_text(json.dumps(packet), encoding="utf-8")
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = canonical_sha256(manifest)

    results = run_acceptance(
        manifest,
        packet_root=packet_root,
        source_root=source,
        artifact_root=tmp_path / "artifacts",
        provider_adapter=tmp_path / "adapter",
        case_executor=lambda *_: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert len(results) == 5
    assert all(result["status"] == "parent_review" for result in results)
    assert all(result["receipt"]["metric_evidence"]["status"] == "unverified" for result in results)
    assert _build_acceptance_report_from_results(manifest, results)["verdict"] == "FAIL"


def test_ledger_metrics_detect_duplicate_attempts_and_failed_applied_patch(tmp_path):
    from omc_n_child_acceptance import _metric_result

    manifest = _manifest()
    case = manifest["cases"][2]
    packet = {"case_id": case["case_id"]}
    artifact = tmp_path / "case"
    artifact.mkdir()
    raw_result = {
        "status": "review_required",
        "reason_code": "child_not_succeeded",
        "children": [{"child_id": "child-1", "status": "failed"}],
        "external_call_count": 2,
    }
    (artifact / "dag-ledger.json").write_text(json.dumps({
        "schema_version": 1, "revision": 1, "dag": raw_result,
    }), encoding="utf-8")
    (artifact / "child-ledger.json").write_text(json.dumps({
        "schema_version": 1,
        "revision": 1,
        "entries": [{
            "child_id": "child-1", "idempotency_key": "dup", "attempt_count": 2, "status": "failed",
            "max_total_elapsed_sec": 10, "max_output_chars": 100, "max_total_tokens": 100,
            "outcome": {"status": "failed", "reason_code": "executor_failed", "elapsed_sec": 1, "output_chars": 1, "patch_applied": True, "scope_violation_detected": False},
        }],
    }), encoding="utf-8")

    result = _metric_result(manifest, case, packet, raw_result, artifact)

    assert result["receipt"]["metrics"]["duplicate_executions"] == 1
    assert result["receipt"]["metrics"]["applied_failed_patches"] == 1


def test_default_executor_runs_deterministic_five_case_fixture_end_to_end(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    packet_root = tmp_path / "packets"
    write_acceptance_fixture_packets(source, packet_root)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    manifest = build_acceptance_manifest(
        source_root=source,
        packet_root=packet_root,
        acceptance_id="fixture-e2e-v1",
    )
    adapter = tmp_path / "fixture-provider"
    adapter.write_text(
        "#!/usr/bin/env python3\n"
            "import json, pathlib, sys, time\n"
            "if sys.argv[1] == 'capabilities':\n"
            " print(json.dumps({'protocol':'omc-provider/v1','hard_total_token_limit':True,'hard_output_limit':True,"
            "'token_enforcement':{'mode':'provider_enforced_total','request_field':'max_total_tokens',"
            "'over_limit_behavior':'reject_before_or_during_generation'}}))\n"
        "else:\n"
        " request=json.load(sys.stdin); kind, path=request['prompt'].split(':', 2)[1:]\n"
        " root=pathlib.Path(request['project_root'])\n"
        " if kind == 'timeout': time.sleep(2)\n"
        " if kind == 'success': (root/path).write_text('changed\\n')\n"
        " if kind == 'policy_violation': (root/'outside.txt').write_text('violation\\n')\n"
        " code=1 if kind == 'failure' else 0\n"
        " print(json.dumps({'returncode':code,'output':kind,'token_usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}))\n",
        encoding="utf-8",
    )
    adapter.chmod(0o755)

    results = run_acceptance(
        manifest,
        packet_root=packet_root,
        source_root=source,
        artifact_root=tmp_path / "artifacts",
        provider_adapter=adapter,
    )

    report = _build_acceptance_report_from_results(manifest, results)
    assert report["verdict"] == "PASS", report["failures"]
    assert all(result["receipt"]["metric_evidence"]["status"] == "verified" for result in results)


def test_prepare_binds_frozen_catalog_to_source_commit_and_packets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "tracked.txt").write_text("frozen\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "frozen"], check=True)
    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    for case in EXPECTED_CASES:
        packet = {
            "schema_version": "omc-n-child-acceptance-case/v1",
            "case_id": case["case_id"],
            "request": {"schema_version": "omc-n-child-dag/v2"},
            "approval": {"operator_confirmed": True},
        }
        (packet_root / f"{case['case_id']}.json").write_text(
            json.dumps(packet), encoding="utf-8"
        )

    manifest = build_acceptance_manifest(
        source_root=source,
        packet_root=packet_root,
        acceptance_id="acceptance-v1",
    )

    assert manifest["cases"] == [
        {**case, "request_sha256": canonical_sha256(json.loads(
            (packet_root / f"{case['case_id']}.json").read_text(encoding="utf-8")
        ))}
        for case in EXPECTED_CASES
    ]
    assert validate_acceptance_manifest(manifest) == manifest


def test_omc_cli_validates_frozen_acceptance_manifest(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("omc.py")),
            "n-child-acceptance",
            "validate",
            "--manifest",
            str(manifest_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "ready"
