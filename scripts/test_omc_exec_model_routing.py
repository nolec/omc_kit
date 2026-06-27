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
