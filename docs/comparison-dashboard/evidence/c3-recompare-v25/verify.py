"""Validate this archived C3 measurement; never read the original temp directory."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


class EvidenceError(Exception):
    def __init__(self, status, reason, path):
        self.result = {"status": status, "reason": reason, "path": str(path)}


def fail(reason, path):
    raise EvidenceError("INVALID", reason, path)


def regular(root, name):
    relative = PurePosixPath(name)
    if not isinstance(name, str) or relative.is_absolute() or ".." in relative.parts or not relative.parts or "\\" in name:
        fail("unsafe_evidence_path", name)
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            fail("symlink_evidence", name)
    if not path.exists():
        raise EvidenceError("UNAVAILABLE", "evidence_missing", name)
    if not path.is_file():
        fail("non_regular_evidence", name)
    return path


def canonical_digest(value):
    """Use the archived omc-autopilot-completion/v1 canonical JSON format."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def task_contract(task, folder):
    if not isinstance(task, dict) or task.get("schema_version") != "omc-autopilot-task/v2" or task.get("id") != "c3-docs":
        fail("task_identity_invalid", folder)
    steps = task.get("steps")
    if not isinstance(steps, list) or len(steps) != 2 or any(not isinstance(step, dict) for step in steps):
        fail("required_task_steps_missing", folder)
    if [step.get("id") for step in steps] != ["roadmap", "dashboard"]:
        fail("required_task_steps_invalid", folder)
    for step in steps:
        sid = step["id"]
        dependencies = [] if sid == "roadmap" else ["roadmap"]
        if step.get("depends_on") != dependencies:
            fail("task_dependency_invalid", folder)
        completion = step.get("completion")
        if not isinstance(completion, dict):
            fail("task_completion_missing", folder)
        expected = {"validator_id": "artifact_sha256", "output_path": sid + ".md", "target_path": "targets/" + sid + ".md"}
        if any(completion.get(key) != value for key, value in expected.items()):
            fail("task_completion_contract_invalid", folder)
        digest = completion.get("expected_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail("task_completion_digest_invalid", folder)
    return steps


def completed_receipts(state, task, steps, completed_ids, folder):
    task_digest = canonical_digest(task)
    if state.get("task_id") != task["id"] or state.get("task_spec_sha256") != task_digest:
        fail("state_task_binding_invalid", folder)
    runtime = state.get("steps")
    required_ids = {"roadmap", "dashboard"}
    if not isinstance(runtime, dict) or not required_ids.issubset(runtime) or set(runtime) - required_ids - {"stale_recovery"}:
        fail("state_required_steps_invalid", folder)
    if any(not isinstance(value, dict) for value in runtime.values()):
        fail("state_step_structure_invalid", folder)
    # The native runner stores its stale-recovery control record beside task steps.
    control = runtime.get("stale_recovery")
    if control is not None and (control.get("status") != "auto_hold" or "completion_receipt" in control):
        fail("recovery_control_record_invalid", folder)
    if {sid for sid in required_ids if runtime[sid].get("status") == "completed"} != completed_ids:
        fail("state_completed_steps_mismatch", folder)
    verified = {}
    for step in steps:
        sid = step["id"]
        if sid not in completed_ids:
            continue
        predecessors = [verified[dependency] for dependency in step["depends_on"]]
        body = {
            "schema_version": "omc-autopilot-completion/v1",
            "task_id": task["id"],
            "task_spec_sha256": task_digest,
            "step_id": sid,
            "validator_id": step["completion"]["validator_id"],
            "artifact_sha256": step["completion"]["expected_sha256"],
            "predecessor_receipts": predecessors,
        }
        expected = {**body, "receipt_sha256": canonical_digest(body)}
        if runtime[sid].get("completion_receipt") != expected:
            fail("completion_receipt_binding_invalid", folder + "/" + sid)
        verified[sid] = expected["receipt_sha256"]


def verify(root):
    try:
        root = Path(root).resolve()
        manifest = json.loads(regular(root, "sha256.json").read_text())
        if not isinstance(manifest, dict) or not manifest:
            fail("invalid_manifest", "sha256.json")
        for name, expected in manifest.items():
            path = regular(root, name)
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                fail("invalid_manifest_digest", name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                fail("evidence_hash_mismatch", name)

        def data(name):
            if name not in manifest:
                fail("unmanifested_evidence", name)
            return json.loads(regular(root, name).read_text())

        def raw(name):
            if name not in manifest:
                fail("unmanifested_evidence", name)
            return regular(root, name).read_text()

        provenance = data("provenance.json")
        if provenance["real_ai_calls"] != 0 or provenance["paired_competitive_measurements"] != 0:
            fail("claim_boundary_mismatch", "provenance.json")
        if provenance["omc_measurements"] != 18 or provenance["ralph_hook_measurements"] != 9:
            fail("provenance_count_mismatch", "provenance.json")
        rows = data("results.json")
        expected_rows = {(arm, case, repeat) for arm in ("before", "current") for case in ("uncertain", "missing_record", "artifact_mismatch") for repeat in (1, 2, 3)}
        if len(rows) != 18 or {(r["arm"], r["case"], r["repeat"]) for r in rows} != expected_rows:
            fail("measurement_set_mismatch", "results.json")
        for row in rows:
            folder = f"{row['arm']}-{row['case']}-{row['repeat']}"
            if row["status"] != "OBSERVED" or row["exit_code"] is None:
                raise EvidenceError("UNAVAILABLE", "measurement_unobserved", folder)
            output = raw(folder + "/resume.stdout")
            stderr = raw(folder + "/resume.stderr")
            resume = data(folder + "/resume.json")
            calls = raw(folder + "/calls.txt").splitlines()
            state = data(folder + "/after-state.json")
            before = data(folder + "/before-state.json")
            task = data(folder + "/task.json")
            steps = task_contract(task, folder)
            completed_ids = {"roadmap", "dashboard"} if row["case"] == "artifact_mismatch" else {"roadmap"}
            completed_receipts(before, task, steps, completed_ids, folder + "/before-state.json")
            completed_receipts(state, task, steps, completed_ids, folder + "/after-state.json")
            if resume["stdout"] != output or resume["stderr"] != stderr or resume["exit_code"] != row["exit_code"]:
                fail("resume_summary_mismatch", folder)
            if len(output.splitlines()) != row["output_lines"] or len(output) != row["output_chars"]:
                fail("output_summary_mismatch", folder)
            if calls != row["calls_after"] or row["additional_calls"] != len(calls) - len(row["calls_before"]):
                fail("call_summary_mismatch", folder)
            if row["additional_calls"] != 0 or row["calls_before"] != calls or row["exit_code"] != 1:
                fail("resume_behavior_mismatch", folder)
            case = row["case"]
            expected_state = "failed" if case == "artifact_mismatch" else "manual_reconciliation_required"
            if state["status"] != expected_state:
                fail("native_state_mismatch", folder)
            if row["arm"] == "current" and case != "artifact_mismatch":
                phrase = "실행 요청 기록만 확인됨" if case == "uncertain" else "유효 실행 기록 없음"
                for text in ("작업:", "roadmap.md", "dashboard.md", "확인 대상:", "판독:", phrase, "다시 실행하지 않고 보류", "판단 불가이면 보류 유지"):
                    if text not in output:
                        fail("surface_requirement_missing", folder)
                if len(output.splitlines()) > 8 or len(output) > 800:
                    fail("surface_budget_exceeded", folder)
            if row["arm"] == "before" and case == "uncertain" and ("중복 실행 방지" not in output or "확인 대상:" in output):
                fail("baseline_surface_mismatch", folder)
            if case == "artifact_mismatch":
                if "receipt" not in output or "roadmap.md" in output:
                    fail("mismatch_gap_observation_changed", folder)
                observation = data(folder + "/artifact-observations.json")
                if observation["capture_phase"] != "post_review_followup" or observation["execution_time_before_file_observed"] is not False:
                    fail("artifact_observation_boundary_invalid", folder)
                expected_files = {"roadmap.md", "dashboard.md", "targets/roadmap.md", "targets/dashboard.md"}
                if len(observation["files"]) != 4 or {item["path"] for item in observation["files"]} != expected_files:
                    fail("artifact_observation_set_invalid", folder)
                for item in observation["files"]:
                    content = regular(root, folder + "/" + item["path"]).read_bytes()
                    if hashlib.sha256(content).hexdigest() != item["sha256"]:
                        fail("artifact_observation_mismatch", folder + "/" + item["path"])
                for step in steps:
                    target = regular(root, folder + "/" + step["completion"]["target_path"]).read_bytes()
                    expected = step["completion"]["expected_sha256"]
                    if hashlib.sha256(target).hexdigest() != expected:
                        fail("declared_target_mismatch", folder)
                    actual = regular(root, folder + "/" + step["completion"]["output_path"]).read_bytes()
                    if (hashlib.sha256(actual).hexdigest() == expected) != (step["id"] == "dashboard"):
                        fail("artifact_difference_missing", folder)
        rival = data("ralph-admitted-results.json")
        expected_policy = {(case, repeat) for case in ("uncertain", "missing_record", "artifact_mismatch") for repeat in (1, 2, 3)}
        if len(rival) != 9 or {(r["case"], r["repeat"]) for r in rival} != expected_policy:
            fail("policy_measurement_set_mismatch", "ralph-admitted-results.json")
        for row in rival:
            folder = f"ralph-admitted-{row['case']}-{row['repeat']}"
            result = data(folder + "/raw.stdout")
            if row["exit_code"] != 0 or row["completion"] != result["completion"]:
                fail("policy_summary_mismatch", folder)
            if row["scope"] != "native_hook_policy_only" or row["not_equivalent_to_provider_resume"] is not True:
                fail("policy_claim_boundary_mismatch", folder)
        graph = data("graph-unsandboxed/result.json")
        if graph["status"] != "UNOBSERVED" or "unavailable on darwin" not in graph["stderr"]:
            fail("graph_exclusion_mismatch", "graph-unsandboxed/result.json")
        return {"status": "VALID", "omc_measurements": len(rows), "ralph_hook_measurements": len(rival), "paired_competitive_measurements": 0, "user_comprehension": "UNOBSERVED"}
    except EvidenceError as error:
        return error.result
    except FileNotFoundError:
        return {"status": "UNAVAILABLE", "reason": "evidence_missing"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"status": "INVALID", "reason": "evidence_structure_invalid"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    result = verify(args.root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"VALID": 0, "INVALID": 1, "UNAVAILABLE": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
