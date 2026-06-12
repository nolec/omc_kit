from __future__ import annotations

import sys
from pathlib import Path

import omc_exec


def test_gemini_headless_command_injects_model_flag() -> None:
    cmd = omc_exec._gemini_headless_command("prompt", model_profile="mini_default")
    assert "-m" in cmd
    assert "gemini-2.5-flash" in cmd


def test_gemini_headless_command_uses_full_model_for_full_default() -> None:
    cmd = omc_exec._gemini_headless_command("prompt", model_profile="full_default")
    assert "-m" in cmd
    mi = cmd.index("-m")
    assert cmd[mi + 1] == "gemini-3-flash-preview"


def test_claude_headless_command_injects_model_flag() -> None:
    cmd = omc_exec._claude_headless_command("prompt", model_profile="mini_default")
    assert "--model" in cmd
    assert "sonnet" in cmd


def test_run_gemini_headless_passes_model_profile(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_gemini_cmd(prompt_text: str, *, model_profile: str = "mini_default") -> list[str]:
        captured["model_profile"] = model_profile
        return ["gemini", "-p", prompt_text, "-m", "gemini-2.5-flash"]

    monkeypatch.setattr(omc_exec, "_gemini_headless_command", fake_gemini_cmd)

    import subprocess
    monkeypatch.setattr(
        omc_exec.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
    )

    omc_exec._run_gemini_headless(tmp_path, "test", timeout_sec=5, model_profile="full_default")
    assert captured["model_profile"] == "full_default"


def test_run_claude_headless_passes_model_profile(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_claude_cmd(prompt_text: str, *, model_profile: str = "mini_default") -> list[str]:
        captured["model_profile"] = model_profile
        return ["claude", "-p", prompt_text, "--model", "sonnet"]

    monkeypatch.setattr(omc_exec, "_claude_headless_command", fake_claude_cmd)

    import subprocess
    monkeypatch.setattr(
        omc_exec.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
    )

    omc_exec._run_claude_headless(tmp_path, "test", timeout_sec=5, model_profile="mini_high")
    assert captured["model_profile"] == "mini_high"


def test_main_passes_model_profile_to_gemini_headless(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("리뷰해줘", encoding="utf-8")

    captured: dict[str, object] = {}

    monkeypatch.setattr(omc_exec, "_detect_executor", lambda _preferred: "gemini")
    monkeypatch.setattr(omc_exec.shutil, "which", lambda _name: "/usr/bin/gemini")

    def fake_run_gemini_headless(project_root, prompt_text, *, timeout_sec, model_profile="mini_default"):
        captured["model_profile"] = model_profile
        return 0

    monkeypatch.setattr(omc_exec, "_run_gemini_headless", fake_run_gemini_headless)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omc_exec.py",
            "--target", str(tmp_path),
            "--prompt-file", str(prompt_file),
            "--executor", "gemini",
            "--execution-mode", "headless",
        ],
    )

    rc = omc_exec.main()
    assert rc == 0
    assert captured["model_profile"] == "mini_high"
