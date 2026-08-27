from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Lock
import time

import pytest

import omc_exec
from omc_executor_shadow import (
    build_n_child_dag_grant,
    build_n_child_dag_proposal,
    build_n_child_dag_v2_grant,
    reserve_single_child_execution_grant_file,
)
from omc_n_child_scheduler import (
    build_process_provider_runner,
    execute_n_child_dag_grant_file,
    execute_n_child_provider_adapter,
)
from test_omc_executor_shadow import _n_child_dag_request, _n_child_dag_v2_request


def _v2_grant(project_root):
    proposal = build_n_child_dag_proposal(
        project_root,
        _n_child_dag_v2_request(),
    )
    return build_n_child_dag_v2_grant(
        project_root,
        proposal,
        {
            "approval_id": "dag-approval-v2",
            "dag_id": proposal["dag_id"],
            "operator_confirmed": True,
            "expires_at": "2099-01-01T00:00:00Z",
            "proposal_sha256": proposal["proposal_sha256"],
        },
    )


def _runner_result(returncode=0, output="ok"):
    return {
        "returncode": returncode,
        "output": output,
        "token_usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
        },
    }


def test_n_child_scheduler_executes_ready_waves_and_dependencies(tmp_path):
    grant = _v2_grant(tmp_path)
    calls: list[str] = []

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or _runner_result(),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert result["reason_code"] == "dag_completed"
    assert result["completed_child_ids"] == ["child-1", "child-2", "child-3"]
    assert result["external_call_count"] == 3
    assert calls.index("implement ui") > calls.index("implement api")
    assert calls.index("implement ui") > calls.index("add tests")


def test_omc_cli_executes_n_child_with_capability_checked_process_adapter(tmp_path):
    grant = _v2_grant(tmp_path)
    grant_path = tmp_path / "grant.json"
    prompts_path = tmp_path / "prompts.json"
    adapter_path = tmp_path / "bounded-provider"
    grant_path.write_text(json.dumps(grant), encoding="utf-8")
    prompts_path.write_text(json.dumps(grant["child_prompts"]), encoding="utf-8")
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'hard_total_token_limit': True, 'hard_output_limit': True, "
        "'token_enforcement': {'mode': 'provider_enforced_total', "
        "'request_field': 'max_total_tokens', 'over_limit_behavior': "
        "'reject_before_or_during_generation'}, 'protocol': 'omc-provider/v1'}))\n"
        "else:\n"
        "    request = json.load(sys.stdin)\n"
        "    assert request['max_total_tokens'] == 12000\n"
        "    assert request['max_output_chars'] == 12000\n"
        "    print(json.dumps({'returncode': 0, 'output': 'ok', 'token_usage': "
        "{'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}))\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("omc.py")),
            "execute-n-child",
            "--grant-file",
            str(grant_path),
            "--prompts-file",
            str(prompts_path),
            "--target",
            str(tmp_path),
            "--dag-ledger",
            str(tmp_path / "dag.json"),
            "--child-ledger",
            str(tmp_path / "children.json"),
            "--provider-adapter",
            str(adapter_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "completed"
    assert result["external_call_count"] == 3
    assert result["total_tokens"] == 6


def test_process_provider_adapter_rejects_missing_hard_token_capability(tmp_path):
    adapter_path = tmp_path / "unbounded-provider"
    marker_path = tmp_path / "executed"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'hard_total_token_limit': False, 'protocol': 'omc-provider/v1'}))\n"
        "else:\n"
        f"    pathlib.Path({str(marker_path)!r}).write_text('called')\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)

    try:
        build_process_provider_runner(adapter_path)
    except ValueError as exc:
        assert str(exc) == "provider_token_limit_unsupported"
    else:
        raise AssertionError("unbounded provider adapter must be rejected")
    assert not marker_path.exists()


def test_process_provider_adapter_rejects_boolean_only_enforcement_claim(tmp_path):
    adapter_path = tmp_path / "boolean-only-provider"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'hard_total_token_limit': True, "
        "'hard_output_limit': True, 'protocol': 'omc-provider/v1'}))\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)

    with pytest.raises(ValueError, match="provider_token_limit_unsupported"):
        build_process_provider_runner(adapter_path)


def test_process_provider_runner_accepts_explicit_subscription_profile(tmp_path):
    adapter_path = tmp_path / "subscription-adapter"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'protocol': 'omc-provider/v1', "
        "'execution_profile': 'subscription_bounded', "
        "'authentication': 'chatgpt_subscription', "
        "'hard_total_token_limit': False, 'hard_output_limit': True, "
        "'token_usage_mode': 'observed_post_call', "
        "'hard_bounds': ['elapsed_time', 'output_chars', 'process_group']}))\n"
        "else:\n"
        "    json.load(sys.stdin)\n"
        "    print(json.dumps({'returncode': 0, 'output': 'ok', "
        "'token_usage': {'input_tokens': 2, 'output_tokens': 3, "
        "'total_tokens': 5}, 'token_usage_mode': 'observed_post_call'}))\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)

    runner = build_process_provider_runner(
        adapter_path,
        capability_profile="subscription_bounded",
    )
    result = runner(
        executor="codex",
        prompt="bounded subscription child",
        project_root=tmp_path,
        timeout_sec=5,
        max_total_tokens=100,
        max_output_chars=100,
    )

    assert result["returncode"] == 0
    assert result["token_usage"]["total_tokens"] == 5


def test_process_provider_runner_does_not_mix_subscription_and_hard_token_profiles(
    tmp_path,
):
    adapter_path = tmp_path / "subscription-adapter"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'protocol': 'omc-provider/v1', "
        "'execution_profile': 'subscription_bounded', "
        "'authentication': 'chatgpt_subscription', "
        "'hard_total_token_limit': False, 'hard_output_limit': True, "
        "'token_usage_mode': 'observed_post_call', "
        "'hard_bounds': ['elapsed_time', 'output_chars', 'process_group']}))\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)

    with pytest.raises(ValueError, match="provider_token_limit_unsupported"):
        build_process_provider_runner(adapter_path)


def test_process_provider_runner_rejects_non_object_capabilities(tmp_path):
    adapter_path = tmp_path / "invalid-provider"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps([]))\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)

    with pytest.raises(ValueError, match="provider_token_limit_unsupported"):
        build_process_provider_runner(adapter_path)


def test_subscription_runner_timeout_does_not_leave_codex_process(
    tmp_path,
    monkeypatch,
):
    pid_path = tmp_path / "codex.pid"
    codex_path = tmp_path / "codex"
    codex_path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        "if sys.argv[1:3] == ['login', 'status']:\n"
        "    time.sleep(0.25)\n"
        "    print('Logged in using ChatGPT')\n"
        "    raise SystemExit(0)\n"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    codex_path.chmod(0o755)
    monkeypatch.setenv("OMC_CODEX_BINARY", str(codex_path))
    adapter_path = Path(__file__).with_name("omc_codex_subscription_adapter.py")
    runner = build_process_provider_runner(
        adapter_path,
        capability_profile="subscription_bounded",
    )

    result = runner(
        executor="codex",
        prompt="orphan-probe",
        project_root=tmp_path,
        timeout_sec=0.5,
        max_total_tokens=100,
        max_output_chars=100,
    )

    assert result["returncode"] == 124
    assert pid_path.is_file()
    pid = int(pid_path.read_text(encoding="utf-8"))
    try:
        time.sleep(0.1)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def test_provider_enforced_runner_keeps_strict_external_timeout(tmp_path):
    adapter_path = tmp_path / "slow-provider"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'protocol': 'omc-provider/v1', "
        "'hard_total_token_limit': True, 'hard_output_limit': True, "
        "'token_enforcement': {'mode': 'provider_enforced_total', "
        "'request_field': 'max_total_tokens', 'over_limit_behavior': "
        "'reject_before_or_during_generation'}}))\n"
        "else:\n"
        "    json.load(sys.stdin)\n"
        "    time.sleep(0.35)\n"
        "    print(json.dumps({'returncode': 0, 'output': 'late-success', "
        "'token_usage': {'input_tokens': 1, 'output_tokens': 1, "
        "'total_tokens': 2}}))\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)
    runner = build_process_provider_runner(adapter_path)

    started = time.monotonic()
    result = runner(
        executor="codex",
        prompt="strict-timeout",
        project_root=tmp_path,
        timeout_sec=0.1,
        max_total_tokens=10,
        max_output_chars=100,
    )
    elapsed = time.monotonic() - started

    assert result["returncode"] == 124
    assert elapsed < 0.3


def test_process_provider_runner_uses_immutable_handshake_snapshot(tmp_path):
    adapter_path = tmp_path / "bounded-provider"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'hard_total_token_limit': True, 'hard_output_limit': True, "
        "'token_enforcement': {'mode': 'provider_enforced_total', "
        "'request_field': 'max_total_tokens', 'over_limit_behavior': "
        "'reject_before_or_during_generation'}, 'protocol': 'omc-provider/v1'}))\n"
        "else:\n"
        "    json.load(sys.stdin)\n"
        "    print(json.dumps({'returncode': 0, 'output': 'snapshot', 'token_usage': "
        "{'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}))\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)
    runner = build_process_provider_runner(adapter_path)
    adapter_path.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")

    result = runner(
        executor="codex",
        prompt="child",
        project_root=tmp_path,
        timeout_sec=5,
        max_total_tokens=100,
        max_output_chars=100,
    )

    assert result["returncode"] == 0
    assert result["output"] == "snapshot"


def test_process_provider_runner_terminates_oversized_raw_response(tmp_path):
    adapter_path = tmp_path / "noisy-provider"
    adapter_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "if sys.argv[1] == 'capabilities':\n"
        "    print(json.dumps({'hard_total_token_limit': True, 'hard_output_limit': True, "
        "'token_enforcement': {'mode': 'provider_enforced_total', "
        "'request_field': 'max_total_tokens', 'over_limit_behavior': "
        "'reject_before_or_during_generation'}, 'protocol': 'omc-provider/v1'}))\n"
        "else:\n"
        "    json.load(sys.stdin)\n"
        "    print('x' * 200000, flush=True)\n"
        "    time.sleep(5)\n",
        encoding="utf-8",
    )
    adapter_path.chmod(0o755)
    runner = build_process_provider_runner(adapter_path)

    result = runner(
        executor="codex",
        prompt="child",
        project_root=tmp_path,
        timeout_sec=4,
        max_total_tokens=100,
        max_output_chars=100,
    )

    assert result == {
        "returncode": 65,
        "output": "provider adapter output limit exceeded",
    }


def test_n_child_scheduler_rejects_legacy_grant_without_provider_call(tmp_path):
    calls = []

    result = execute_n_child_dag_grant_file(
        build_n_child_dag_grant(_n_child_dag_request()),
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=_n_child_dag_request()["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "dag_grant_not_scheduler_eligible"
    assert calls == []


def test_n_child_scheduler_never_resumes_existing_dag_ledger(tmp_path):
    grant = _v2_grant(tmp_path)
    dag_path = tmp_path / "dag.json"
    calls = []
    kwargs = {
        "trusted_target": tmp_path,
        "prompts": grant["child_prompts"],
        "project_root": tmp_path,
        "runner": lambda **call: calls.append(call)
        or _runner_result(),
        "now": lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    }

    first = execute_n_child_dag_grant_file(
        grant,
        dag_path,
        tmp_path / "children.json",
        **kwargs,
    )
    second = execute_n_child_dag_grant_file(
        grant,
        dag_path,
        tmp_path / "children.json",
        **kwargs,
    )

    assert first["status"] == "completed"
    assert second["status"] == "blocked"
    assert second["reason_code"] == "dag_already_started"
    assert len(calls) == 3


def test_n_child_scheduler_stops_dependents_after_child_failure(tmp_path):
    grant = _v2_grant(tmp_path)
    calls = []

    def runner(**kwargs):
        calls.append(kwargs["prompt"])
        return _runner_result(
            returncode=1 if kwargs["prompt"] == "implement api" else 0,
            output="result",
        )

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    children = {child["child_id"]: child for child in result["children"]}
    assert result["status"] == "review_required"
    assert result["parent_review"]["status"] == "review_required"
    assert children["child-1"]["status"] == "failed"
    assert children["child-2"]["status"] == "not_started"
    assert children["child-2"]["reason_code"] == "dependency_not_succeeded"
    assert "implement ui" not in calls


def test_n_child_scheduler_rejects_tampered_v2_grant_before_ledger_or_call(
    tmp_path,
):
    grant = _v2_grant(tmp_path)
    grant["max_parallelism"] = 3
    calls = []

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result == {
        "status": "blocked",
        "reason_code": "dag_grant_binding_mismatch",
    }
    assert not (tmp_path / "dag.json").exists()
    assert calls == []


def test_n_child_scheduler_rejects_expired_parent_grant_before_call(tmp_path):
    grant = _v2_grant(tmp_path)
    grant["approval_expires_at"] = "2026-08-23T00:00:00Z"
    calls = []

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "dag_grant_expired"
    assert calls == []


def test_n_child_scheduler_rejects_tampered_parent_approval(tmp_path):
    grant = _v2_grant(tmp_path)
    grant["approval_id"] = grant["child_grants"][0]["approval_id"]

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "dag_grant_binding_mismatch"


def test_n_child_scheduler_blocks_malformed_flattened_budget(tmp_path):
    grant = _v2_grant(tmp_path)
    grant["max_total_elapsed_sec"] = "not-a-number"

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "blocked"
    assert result["reason_code"] == "dag_grant_binding_mismatch"


def test_n_child_scheduler_blocks_duplicate_child_claim_without_provider_call(
    tmp_path,
):
    grant = _v2_grant(tmp_path)
    child_ledger = tmp_path / "children.json"
    first_grant = grant["child_grants"][0]
    reservation = reserve_single_child_execution_grant_file(
        first_grant,
        child_ledger,
        expected_scope_hash=first_grant["scope_hash"],
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    calls = []

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        child_ledger,
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert reservation["status"] == "reserved"
    assert result["status"] == "review_required"
    assert result["reason_code"] == "child_reservation_failed"
    assert calls == []


def test_n_child_scheduler_terminalizes_prior_reservation_when_batch_aborts(tmp_path):
    grant = _v2_grant(tmp_path)
    child_ledger = tmp_path / "children.json"
    later_ready_grant = grant["child_grants"][2]
    reserve_single_child_execution_grant_file(
        later_ready_grant,
        child_ledger,
        expected_scope_hash=later_ready_grant["scope_hash"],
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        child_ledger,
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: _runner_result(),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    ledger = json.loads(child_ledger.read_text(encoding="utf-8"))
    first = next(entry for entry in ledger["entries"] if entry["child_id"] == "child-1")
    assert result["reason_code"] == "child_reservation_failed"
    assert first["status"] == "failed"
    assert first["outcome"]["reason_code"] == "reservation_batch_aborted"


def test_n_child_scheduler_enforces_granted_parallelism(tmp_path):
    grant = _v2_grant(tmp_path)
    barrier = Barrier(2)
    lock = Lock()
    active = 0
    max_active = 0

    def runner(**kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        if kwargs["prompt"] != "implement ui":
            barrier.wait(timeout=2)
        with lock:
            active -= 1
        return _runner_result()

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert max_active == grant["max_parallelism"] == 2


def test_n_child_scheduler_stops_future_wave_when_wall_budget_is_exceeded(
    tmp_path,
):
    grant = _v2_grant(tmp_path)
    scheduler_times = iter([0.0, 241.0])
    calls = []

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or _runner_result(),
        scheduler_monotonic=lambda: next(scheduler_times),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["reason_code"] == "dag_budget_exceeded"
    assert result["total_elapsed_sec"] == 241.0
    assert "implement ui" not in calls


def test_n_child_scheduler_routes_timeout_to_parent_review(tmp_path):
    grant = _v2_grant(tmp_path)

    def runner(**kwargs):
        return _runner_result(
            returncode=124 if kwargs["prompt"] == "implement api" else 0,
            output="result",
        )

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    children = {child["child_id"]: child for child in result["children"]}
    assert result["status"] == "review_required"
    assert children["child-1"]["status"] == "timeout"
    assert children["child-2"]["reason_code"] == "dependency_not_succeeded"


def test_n_child_scheduler_preserves_timeout_when_token_usage_is_unavailable(tmp_path):
    grant = _v2_grant(tmp_path)

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **_kwargs: {"returncode": 124, "output": "timed out"},
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["children"][0]["status"] == "timeout"
    assert result["children"][0]["reason_code"] == "executor_timeout"


def test_n_child_scheduler_passes_signed_child_token_cap_to_runner(tmp_path):
    grant = _v2_grant(tmp_path)
    observed_caps = []

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: observed_caps.append(
            (kwargs["max_total_tokens"], kwargs["max_output_chars"])
        )
        or _runner_result(),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert observed_caps == [(12_000, 12_000)] * 3


def test_n_child_scheduler_blocks_default_provider_without_hard_token_limit(
    monkeypatch, tmp_path
):
    grant = _v2_grant(tmp_path)
    calls = []
    monkeypatch.setattr(
        omc_exec,
        "run_headless_executor_once",
        lambda **kwargs: calls.append(kwargs) or _runner_result(),
    )

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["children"][0]["reason_code"] == "provider_token_limit_unsupported"
    assert result["external_call_count"] == 0
    assert calls == []


def test_n_child_provider_adapter_terminalizes_unsupported_default_provider(tmp_path):
    grant = _v2_grant(tmp_path)
    child_grant = grant["child_grants"][0]
    child_ledger = tmp_path / "children.json"
    reservation = reserve_single_child_execution_grant_file(
        child_grant,
        child_ledger,
        expected_scope_hash=child_grant["scope_hash"],
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    result = execute_n_child_provider_adapter(
        child_grant,
        child_ledger,
        prompt=grant["child_prompts"]["child-1"],
        project_root=tmp_path,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
        dag_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        dag_ledger_path=tmp_path / "dag.json",
        scope_paths=["src/api"],
        max_elapsed_sec=120,
    )

    ledger = json.loads(child_ledger.read_text(encoding="utf-8"))
    assert reservation["status"] == "reserved"
    assert result["status"] == "failed"
    assert result["reason_code"] == "provider_token_limit_unsupported"
    assert result["external_call_performed"] is False
    assert ledger["entries"][0]["status"] == "failed"


def test_n_child_scheduler_quarantines_out_of_scope_provider_changes(tmp_path):
    grant = _v2_grant(tmp_path)

    def runner(**kwargs):
        if kwargs["prompt"] == "implement api":
            (kwargs["project_root"] / "outside.txt").write_text(
                "out of scope", encoding="utf-8"
            )
        return _runner_result()

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert not (tmp_path / "outside.txt").exists()


def test_n_child_scheduler_applies_only_in_scope_provider_changes(tmp_path):
    grant = _v2_grant(tmp_path)

    def runner(**kwargs):
        if kwargs["prompt"] == "implement api":
            destination = kwargs["project_root"] / "src/api/generated.txt"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("generated", encoding="utf-8")
        return _runner_result()

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert (
        tmp_path / "src/api/generated.txt"
    ).read_text(encoding="utf-8") == "generated"


def test_n_child_scheduler_does_not_expose_external_symlink_targets(tmp_path):
    external = tmp_path.parent / f"{tmp_path.name}-external"
    external.mkdir()
    (external / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "linked-docs").symlink_to(external, target_is_directory=True)
    grant = _v2_grant(tmp_path)
    exposed = []

    def runner(**kwargs):
        exposed.append((kwargs["project_root"] / "linked-docs/secret.txt").exists())
        return _runner_result()

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert exposed == [False, False, False]


def test_n_child_scheduler_rejects_new_symlink_provider_changes(tmp_path):
    external = tmp_path.parent / f"{tmp_path.name}-external-target.txt"
    external.write_text("secret", encoding="utf-8")
    grant = _v2_grant(tmp_path)

    def runner(**kwargs):
        if kwargs["prompt"] == "implement api":
            destination = kwargs["project_root"] / "src/api/external-link"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(external)
        return _runner_result()

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    destination = tmp_path / "src/api/external-link"
    assert result["status"] == "review_required"
    assert not destination.is_symlink()
    assert not destination.exists()


def test_n_child_scheduler_hides_dag_ledger_from_provider_workspace(tmp_path):
    grant = _v2_grant(tmp_path)
    exposed = []

    def runner(**kwargs):
        dag_ledger = kwargs["project_root"] / "src/api/dag.json"
        if kwargs["prompt"] == "implement api":
            exposed.append(dag_ledger.exists())
            if dag_ledger.exists():
                dag_ledger.write_text("corrupted", encoding="utf-8")
        return {
            "returncode": 0,
            "output": "ok",
            "token_usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
            },
        }

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "src/api/dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert exposed == [False]


def test_n_child_scheduler_rejects_provider_token_cap_violation(tmp_path):
    grant = _v2_grant(tmp_path)

    def runner(**kwargs):
        destination = kwargs["project_root"] / "src/api/over-budget.txt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("must not be applied", encoding="utf-8")
        return {
            "returncode": 0,
            "output": "ok",
            "token_usage": {
                "input_tokens": 12_000,
                "output_tokens": 1,
                "total_tokens": 12_001,
            },
        }

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["reason_code"] == "child_not_succeeded"
    assert result["children"][0]["reason_code"] == "provider_token_limit_violated"
    assert not (tmp_path / "src/api/over-budget.txt").exists()


def test_n_child_scheduler_routes_missing_token_usage_to_parent_review(tmp_path):
    grant = _v2_grant(tmp_path)

    def runner(**kwargs):
        destination = kwargs["project_root"] / "src/api/missing-usage.txt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("must not be applied", encoding="utf-8")
        return {"returncode": 0, "output": "ok"}

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["reason_code"] == "child_not_succeeded"
    assert result["children"][0]["reason_code"] == "token_usage_unavailable"
    assert not (tmp_path / "src/api/missing-usage.txt").exists()


def test_n_child_scheduler_excludes_provider_cost_log_from_child_patch(tmp_path):
    grant = _v2_grant(tmp_path)

    def runner(**kwargs):
        cost_log = kwargs["project_root"] / ".omc/cost_log.jsonl"
        cost_log.parent.mkdir(parents=True, exist_ok=True)
        cost_log.write_text('{"executor":"codex"}\n', encoding="utf-8")
        if kwargs["prompt"] == "implement api":
            destination = kwargs["project_root"] / "src/api/generated.txt"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("generated", encoding="utf-8")
        return _runner_result()

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert not (tmp_path / ".omc/cost_log.jsonl").exists()
    assert (tmp_path / "src/api/generated.txt").read_text(encoding="utf-8") == "generated"


def test_n_child_scheduler_supports_empty_project_snapshot(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    grant = _v2_grant(project_root)

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=project_root,
        prompts=grant["child_prompts"],
        project_root=project_root,
        runner=lambda **kwargs: _runner_result(),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"


def test_n_child_scheduler_does_not_open_wave_after_elapsed_budget_is_consumed(
    tmp_path,
):
    request = _n_child_dag_v2_request()
    request["children"][1]["depends_on"] = []
    request["aggregate_budget"]["max_total_elapsed_sec"] = 120
    proposal = build_n_child_dag_proposal(tmp_path, request)
    grant = build_n_child_dag_v2_grant(
        tmp_path,
        proposal,
        {
            "approval_id": "dag-approval-v2",
            "dag_id": proposal["dag_id"],
            "operator_confirmed": True,
            "expires_at": "2099-01-01T00:00:00Z",
            "proposal_sha256": proposal["proposal_sha256"],
        },
    )
    scheduler_times = iter([0.0, 120.0])
    calls = []

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs["prompt"])
        or _runner_result(),
        scheduler_monotonic=lambda: next(scheduler_times),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "review_required"
    assert result["reason_code"] == "dag_budget_exceeded"
    assert len(calls) == 2


def test_n_child_scheduler_caps_child_timeout_to_remaining_elapsed_budget(tmp_path):
    grant = _v2_grant(tmp_path)
    scheduler_times = iter([0.0, 150.0, 160.0])
    timeouts = {}

    def runner(**kwargs):
        timeouts[kwargs["prompt"]] = kwargs["timeout_sec"]
        return _runner_result()

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=runner,
        scheduler_monotonic=lambda: next(scheduler_times),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
    assert timeouts["implement ui"] == 90.0


def test_n_child_scheduler_terminalizes_provider_adapter_exception(tmp_path):
    grant = _v2_grant(tmp_path)

    def broken_monotonic():
        raise RuntimeError("clock failed")

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: _runner_result(),
        monotonic=broken_monotonic,
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    persisted = (tmp_path / "dag.json").read_text(encoding="utf-8")
    child_ledger = json.loads(
        (tmp_path / "children.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "indeterminate"
    assert result["parent_review"]["status"] == "review_required"
    assert '"status":"running"' not in persisted
    assert all(entry["status"] != "running" for entry in child_ledger["entries"])


def test_n_child_scheduler_ignores_transient_child_ledger_files(tmp_path):
    grant = _v2_grant(tmp_path)
    transient = tmp_path / ".children.json.racing.tmp"
    transient.write_text("partial", encoding="utf-8")

    result = execute_n_child_dag_grant_file(
        grant,
        tmp_path / "dag.json",
        tmp_path / "children.json",
        trusted_target=tmp_path,
        prompts=grant["child_prompts"],
        project_root=tmp_path,
        runner=lambda **kwargs: _runner_result(),
        now=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert result["status"] == "completed"
