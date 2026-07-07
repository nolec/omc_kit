from __future__ import annotations

import sys
from pathlib import Path

import omc_exec


def test_main_uses_task_kind_cli_arg_for_profile_routing(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("아키텍처 설계", encoding="utf-8")

    captured: dict[str, object] = {}

    monkeypatch.setattr(omc_exec, "_detect_executor", lambda _preferred: "codex")
    monkeypatch.setattr(omc_exec, "_is_tty_available", lambda: False)
    monkeypatch.setattr(omc_exec, "_check_codex_auth", lambda: True)
    monkeypatch.setattr(omc_exec.shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run_codex_headless(
        project_root,
        prompt_text,
        *,
        timeout_sec,
        model_profile="mini_default",
        task_kind="task",
    ):
        captured["model_profile"] = model_profile
        captured["task_kind"] = task_kind
        return 0

    monkeypatch.setattr(omc_exec, "_run_codex_headless", fake_run_codex_headless)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omc_exec.py",
            "--target", str(tmp_path),
            "--prompt-file", str(prompt_file),
            "--executor", "codex",
            "--execution-mode", "headless",
            "--task-kind", "plan",
        ],
    )

    rc = omc_exec.main()
    assert rc == 0
    assert captured["model_profile"] == "mini_high"
    assert captured["task_kind"] == "plan"


def test_main_defaults_task_kind_to_task_when_omitted(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("작은 수정", encoding="utf-8")

    captured: dict[str, object] = {}

    monkeypatch.setattr(omc_exec, "_detect_executor", lambda _preferred: "codex")
    monkeypatch.setattr(omc_exec, "_is_tty_available", lambda: False)
    monkeypatch.setattr(omc_exec, "_check_codex_auth", lambda: True)
    monkeypatch.setattr(omc_exec.shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run_codex_headless(
        project_root,
        prompt_text,
        *,
        timeout_sec,
        model_profile="mini_default",
        task_kind="task",
    ):
        captured["model_profile"] = model_profile
        captured["task_kind"] = task_kind
        return 0

    monkeypatch.setattr(omc_exec, "_run_codex_headless", fake_run_codex_headless)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omc_exec.py",
            "--target", str(tmp_path),
            "--prompt-file", str(prompt_file),
            "--executor", "codex",
            "--execution-mode", "headless",
        ],
    )

    rc = omc_exec.main()
    assert rc == 0
    assert captured["model_profile"] == "mini_default"
    assert captured["task_kind"] == "task"


def test_select_model_profile_defaults_task_to_mini_default() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="task",
            request_text="버튼 클릭 버그 수정",
            touched_files=[],
            retry_count=0,
            review_severity=None,
        )
        == "mini_default"
    )


def test_select_model_profile_uses_mini_high_for_plan_work() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="plan",
            request_text="아키텍처 계획 잡아줘",
            touched_files=[],
            retry_count=0,
            review_severity=None,
        )
        == "mini_high"
    )


def test_select_model_profile_escalates_task_with_broader_context_signals() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="task",
            request_text="리팩터링 영향 범위까지 보고 수정",
            touched_files=[
                "src/components/Button.tsx",
                "src/hooks/useButton.ts",
                "src/utils/button.ts",
                "src/state/buttonStore.ts",
            ],
            retry_count=0,
            review_severity=None,
        )
        == "mini_high"
    )


def test_select_model_profile_escalates_to_full_for_high_risk_changes() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="review",
            request_text="공용 API 계약 변경 리뷰",
            touched_files=[
                "src/api/client.ts",
                "src/api/types.ts",
                "src/state/store.ts",
                "src/features/a.ts",
                "src/features/b.ts",
                "src/features/c.ts",
                "src/features/d.ts",
                "src/features/e.ts",
            ],
            retry_count=0,
            review_severity="major",
        )
        == "full_default"
    )


def test_select_model_profile_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("OMC_MODEL_PROFILE", "mini_high")

    assert (
        omc_exec.select_model_profile(
            task_kind="task",
            request_text="작은 수정",
            touched_files=[],
            retry_count=0,
            review_severity=None,
        )
        == "mini_high"
    )


def test_select_model_profile_prefers_explicit_profile_hint() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="task",
            request_text="작은 수정",
            touched_files=[],
            retry_count=0,
            review_severity=None,
            preferred_profile="full_default",
        )
        == "full_default"
    )


def test_select_model_profile_does_not_allow_preferred_profile_to_downgrade_ship() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="ship",
            request_text="배포",
            touched_files=[],
            retry_count=0,
            review_severity=None,
            preferred_profile="mini_default",
        )
        == "full_default"
    )


def test_select_model_profile_does_not_allow_preferred_profile_to_downgrade_high_risk() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="task",
            request_text="구현",
            touched_files=["src/components/Button.tsx"],
            retry_count=0,
            review_severity=None,
            complexity="low",
            risk="high",
            sensitive_paths=[],
            preferred_profile="mini_default",
        )
        == "full_default"
    )


def test_select_model_profile_uses_metadata_risk_and_complexity_to_escalate() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="task",
            request_text="구현",
            touched_files=["src/components/Button.tsx"],
            retry_count=0,
            review_severity=None,
            complexity="high",
            risk="high",
            sensitive_paths=[],
            preferred_profile=None,
        )
        == "full_default"
    )


def test_select_model_profile_balanced_keeps_current_borderline_review_behavior(monkeypatch) -> None:
    monkeypatch.setenv("OMC_ROUTING_POLICY", "balanced")

    assert (
        omc_exec.select_model_profile(
            task_kind="review",
            request_text="변경 영향 검토",
            touched_files=["src/state/store.ts", "src/features/a.ts", "src/features/b.ts"],
            retry_count=0,
            review_severity=None,
        )
        == "full_default"
    )


def test_select_model_profile_cost_saver_avoids_full_for_borderline_review(monkeypatch) -> None:
    monkeypatch.setenv("OMC_ROUTING_POLICY", "cost_saver")

    assert (
        omc_exec.select_model_profile(
            task_kind="review",
            request_text="변경 영향 검토",
            touched_files=["src/state/store.ts", "src/features/a.ts", "src/features/b.ts"],
            retry_count=0,
            review_severity=None,
        )
        == "mini_high"
    )


def test_select_model_profile_quality_first_escalates_borderline_review_to_full(monkeypatch) -> None:
    monkeypatch.setenv("OMC_ROUTING_POLICY", "quality_first")

    assert (
        omc_exec.select_model_profile(
            task_kind="review",
            request_text="변경 영향 검토",
            touched_files=["src/state/store.ts", "src/features/a.ts", "src/features/b.ts"],
            retry_count=0,
            review_severity=None,
        )
        == "full_default"
    )


def test_resolve_task_routing_includes_reason_summary_for_plan_work() -> None:
    routing = omc_exec.resolve_task_routing(
        task_kind="plan",
        request_text="아키텍처 계획 잡아줘",
        retry_count=0,
        touched_files=[],
        review_severity=None,
    )

    assert routing["model_profile"] == "mini_high"
    assert routing["recommended_policy_profile"] == "balanced"
    assert routing["policy_confidence"] == "high"
    assert routing["routing_reason_summary"] == "plan/review/investigate work prefers broader context"
    assert routing["routing_reason_codes"] == ["plan_or_review_work"]


def test_resolve_task_routing_includes_high_risk_reason_summary() -> None:
    routing = omc_exec.resolve_task_routing(
        task_kind="task",
        request_text="구현",
        retry_count=0,
        touched_files=["src/components/Button.tsx"],
        review_severity=None,
        complexity="low",
        risk="high",
        sensitive_paths=[],
        preferred_profile="mini_default",
    )

    assert routing["model_profile"] == "full_default"
    assert routing["recommended_policy_profile"] == "balanced"
    assert routing["policy_confidence"] == "high"
    assert routing["routing_reason_summary"] == "high risk changes force full model"
    assert routing["routing_reason_codes"] == ["high_risk"]


def test_resolve_task_routing_uses_cost_saver_only_for_explicit_lightweight_cases() -> None:
    decision = omc_exec._resolve_policy_decision(
        task_kind="task",
        request_text="문구 한 줄 수정",
        touched_files=["src/constants/messages.ts"],
        retry_count=0,
        review_severity=None,
        ambiguity_level="low",
        failure_cost="low",
        operator_goal="speed",
    )

    assert decision["recommended_policy_profile"] == "cost_saver"
    assert decision["policy_confidence"] == "high"
    assert decision["user_selection_needed"] is False


def test_resolve_task_routing_keeps_policy_profile_aligned_with_default_model_profile() -> None:
    routing = omc_exec.resolve_task_routing(
        task_kind="task",
        request_text="작은 수정",
        retry_count=0,
        touched_files=[],
        review_severity=None,
    )

    assert routing["model_profile"] == "mini_default"
    assert routing["recommended_policy_profile"] == "balanced"


def test_resolve_policy_summary_accepts_benchmark_input_contract() -> None:
    summary = omc_exec.resolve_policy_summary(
        task_kind="benchmark",
        policy_input={
            "policy_comparison_summary": "policy comparison pending: need more policy pair coverage; reason signals observed",
            "policy_comparison_bottleneck_summary": "policy comparison bottleneck: need more policy pair coverage",
            "next_priority_reason": "need more policy pair coverage",
        },
    )

    assert summary["recommended_policy_profile"] == "balanced"
    assert summary["policy_reason_summary"] == "balanced is the safe default"
    assert summary["policy_confidence"] == "high"
    assert summary["user_selection_needed"] == "no"


def test_policy_decision_low_confidence_falls_back_to_balanced_and_requires_selection() -> None:
    decision = omc_exec._resolve_policy_decision(
        task_kind="task",
        request_text="빠르게 끝내고 싶지만 영향이 클 수도 있음",
        touched_files=["src/state/store.ts"],
        retry_count=0,
        review_severity=None,
        ambiguity_level="high",
        failure_cost="high",
        operator_goal="speed",
    )

    assert decision["recommended_policy_profile"] == "balanced"
    assert decision["policy_confidence"] == "low"
    assert decision["user_selection_needed"] is True


def test_policy_decision_quality_goal_with_high_failure_cost_prefers_quality_first() -> None:
    decision = omc_exec._resolve_policy_decision(
        task_kind="task",
        request_text="정확도를 우선해서 설계 영향까지 고려",
        touched_files=["src/features/a.ts"],
        retry_count=0,
        review_severity=None,
        ambiguity_level="medium",
        failure_cost="high",
        operator_goal="quality",
    )

    assert decision["recommended_policy_profile"] == "quality_first"
    assert decision["policy_confidence"] == "high"
    assert decision["user_selection_needed"] is False


def test_resolve_task_routing_recommends_codex_for_balanced_task_work() -> None:
    routing = omc_exec.resolve_task_routing(
        task_kind="task",
        request_text="작은 수정",
        retry_count=0,
        touched_files=[],
        review_severity=None,
    )

    assert routing["recommended_executor"] == "codex"
    assert routing["executor_reason_summary"] == "balanced task work stays on codex by default"
    assert routing["executor_fallback"] == "gemini"


def test_resolve_task_routing_recommends_claude_for_quality_first_plan_work() -> None:
    routing = omc_exec.resolve_task_routing(
        task_kind="plan",
        request_text="복잡한 설계 영향도까지 같이 검토",
        retry_count=0,
        touched_files=["src/features/a.ts"],
        review_severity=None,
        ambiguity_level="medium",
        failure_cost="high",
        operator_goal="quality",
    )

    assert routing["recommended_policy_profile"] == "quality_first"
    assert routing["recommended_executor"] == "claude"
    assert routing["executor_reason_summary"] == "quality-first planning work prefers claude for broader reasoning"
    assert routing["executor_fallback"] == "codex"


def test_resolve_policy_summary_surfaces_executor_recommendation() -> None:
    summary = omc_exec.resolve_policy_summary(
        task_kind="benchmark",
        policy_input=omc_exec.build_policy_summary_input(
            policy_comparison_summary="policy comparison ready: baseline comparison wording can be enabled",
            policy_comparison_bottleneck_summary="policy comparison bottleneck: readiness requirements are not met",
            next_priority_reason="operator should validate broader quality tradeoffs before rollout",
        ),
    )

    assert summary["recommended_policy_profile"] == "balanced"
    assert summary["recommended_executor"] == "codex"
    assert summary["executor_reason_summary"] == "balanced task work stays on codex by default"
    assert summary["executor_fallback"] == "gemini"


def test_select_model_profile_uses_full_default_for_ship() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="ship",
            request_text="배포 전 최종 검증",
            touched_files=[],
            retry_count=0,
            review_severity=None,
        )
        == "full_default"
    )


def test_select_model_profile_escalates_to_full_after_multiple_retries() -> None:
    assert (
        omc_exec.select_model_profile(
            task_kind="task",
            request_text="한 번 더 재시도해서 고쳐",
            touched_files=["src/components/Button.tsx"],
            retry_count=2,
            review_severity=None,
        )
        == "full_default"
    )


def test_main_passes_touched_files_to_model_profile_routing(monkeypatch, tmp_path: Path) -> None:
    """--touched-files 인자가 select_model_profile의 touched_files로 실제 전달되어야 한다."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("수정", encoding="utf-8")

    captured: dict[str, object] = {}

    monkeypatch.setattr(omc_exec, "_detect_executor", lambda _preferred: "codex")
    monkeypatch.setattr(omc_exec, "_is_tty_available", lambda: False)
    monkeypatch.setattr(omc_exec, "_check_codex_auth", lambda: True)
    monkeypatch.setattr(omc_exec.shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run_codex_headless(
        project_root,
        prompt_text,
        *,
        timeout_sec,
        model_profile="mini_default",
        task_kind="task",
    ):
        captured["model_profile"] = model_profile
        captured["task_kind"] = task_kind
        return 0

    monkeypatch.setattr(omc_exec, "_run_codex_headless", fake_run_codex_headless)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omc_exec.py",
            "--target", str(tmp_path),
            "--prompt-file", str(prompt_file),
            "--executor", "codex",
            "--execution-mode", "headless",
            "--task-kind", "review",
            "--touched-files", "scripts/api/client.ts", "scripts/state/store.ts",
            "scripts/api/types.ts", "scripts/api/a.ts", "scripts/api/b.ts",
            "scripts/api/c.ts", "scripts/api/d.ts", "scripts/api/e.ts",
        ],
    )

    rc = omc_exec.main()
    assert rc == 0
    # review + 8개 sensitive path → full_default 에스컬레이션
    assert captured["model_profile"] == "full_default"
    assert captured["task_kind"] == "review"
