from __future__ import annotations

import omc_exec


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
