from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import omc_cost
import omc_exec


def test_codex_headless_command_uses_workspace_write_sandbox(tmp_path: Path) -> None:
    cmd = omc_exec._codex_headless_command(tmp_path, "prompt", tmp_path / "out.txt")

    assert "--ignore-user-config" not in cmd
    assert "--ephemeral" in cmd
    assert "--json" in cmd
    assert "-s" in cmd
    assert "workspace-write" in cmd
    assert "-o" in cmd


def test_codex_headless_command_full_auto_with_reasoning_effort_preserves_order(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OMC_CODEX_FULL_AUTO", "1")
    cmd = omc_exec._codex_headless_command(
        tmp_path,
        "my prompt",
        tmp_path / "out.txt",
        model_profile="mini_high",
    )
    assert cmd[2] == "--full-auto", f"--full-auto가 index 2여야 함, 실제: {cmd[2]!r}"
    ri = cmd.index("--reasoning-effort")
    assert cmd[ri + 1] == "high", f"--reasoning-effort 값이 'high'여야 함, 실제: {cmd[ri+1]!r}"
    assert cmd[-1] == "my prompt", f"prompt_text가 마지막이어야 함, 실제: {cmd[-1]!r}"


def test_codex_headless_command_reasoning_effort_precedes_prompt(tmp_path: Path) -> None:
    cmd = omc_exec._codex_headless_command(
        tmp_path,
        "my prompt",
        tmp_path / "out.txt",
        model_profile="mini_high",
    )
    ri = cmd.index("--reasoning-effort")
    assert cmd[ri + 1] == "high", f"--reasoning-effort 값이 'high'여야 함, 실제: {cmd[ri+1]!r}"
    assert cmd[-1] == "my prompt", f"prompt_text가 마지막이어야 함, 실제: {cmd[-1]!r}"


def test_codex_headless_command_prompt_text_is_always_last_element(tmp_path: Path) -> None:
    for profile in ("mini_default", "mini_high", "full_default"):
        cmd = omc_exec._codex_headless_command(
            tmp_path,
            "my prompt",
            tmp_path / "out.txt",
            model_profile=profile,
        )
        assert cmd[-1] == "my prompt", f"profile={profile}: cmd[-1]={cmd[-1]!r}"


def test_codex_headless_command_applies_profile_model_and_reasoning_flags(tmp_path: Path) -> None:
    cmd = omc_exec._codex_headless_command(
        tmp_path,
        "prompt",
        tmp_path / "out.txt",
        model_profile="mini_high",
    )

    assert "--model" in cmd
    assert "gpt-5.4-mini" in cmd
    assert "--reasoning-effort" in cmd
    assert "high" in cmd


def test_codex_headless_command_omits_reasoning_effort_when_unsupported(tmp_path: Path) -> None:
    cmd = omc_exec._codex_headless_command(
        tmp_path,
        "prompt",
        tmp_path / "out.txt",
        model_profile="mini_high",
        allow_reasoning_effort=False,
    )

    assert "--model" in cmd
    assert "gpt-5.4-mini" in cmd
    assert "--reasoning-effort" not in cmd


def test_prepare_codex_headless_runtime_uses_temp_codex_home(monkeypatch, tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "gpt-5.3-codex"\n'
        '\n'
        "[notice.model_migrations]\n"
        '"gpt-5.3-codex" = "gpt-5.4"\n',
        encoding="utf-8",
    )
    (source_home / "version.json").write_text('{"version":"1"}', encoding="utf-8")
    (source_home / "installation_id").write_text("abc", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    runtime, env = omc_exec._prepare_codex_headless_runtime()
    try:
        runtime_home = Path(env["CODEX_HOME"])
        assert runtime_home != source_home
        assert runtime_home.exists()
        assert (runtime_home / "auth.json").read_text(encoding="utf-8") == '{"token":"secret"}'
        assert 'model = "gpt-5.4-mini"' in (runtime_home / "config.toml").read_text(encoding="utf-8")
        assert (runtime_home / "version.json").read_text(encoding="utf-8") == '{"version":"1"}'
        assert (runtime_home / "installation_id").read_text(encoding="utf-8") == "abc"
    finally:
        runtime.cleanup()


def test_prepare_codex_headless_runtime_overrides_model_for_full_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "gpt-5.3-codex"\n'
        '\n'
        "[notice.model_migrations]\n"
        '"gpt-5.3-codex" = "gpt-5.4"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    runtime, env = omc_exec._prepare_codex_headless_runtime(model_profile="full_default")
    try:
        runtime_home = Path(env["CODEX_HOME"])
        config_text = (runtime_home / "config.toml").read_text(encoding="utf-8")
        assert 'model = "gpt-5.4"' in config_text
    finally:
        runtime.cleanup()


def test_run_codex_headless_uses_temp_codex_home_env(monkeypatch, tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["runtime_exists_during_run"] = Path(kwargs["env"]["CODEX_HOME"]).exists()
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("VERDICT: PROCEED", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(omc_exec.subprocess, "run", fake_run)
    monkeypatch.setattr(omc_exec, "_codex_supports_reasoning_effort", lambda: False)

    rc = omc_exec._run_codex_headless(tmp_path, "prompt", timeout_sec=5)

    assert rc == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["CODEX_HOME"] != str(source_home)
    assert captured["runtime_exists_during_run"] is True
    assert "--reasoning-effort" not in captured["cmd"]


def test_run_codex_headless_records_cost_log_row(monkeypatch, tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    recorded: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("VERDICT: PROCEED", encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"usage":{"input_tokens":12,"output_tokens":3}}',
            stderr="",
        )

    def fake_record(root, *, executor, session_id=None, task_title=None, llm_json=None, model="", base_ref="HEAD"):
        recorded["root"] = root
        recorded["executor"] = executor
        recorded["task_title"] = task_title
        recorded["llm_json"] = llm_json
        recorded["model"] = model
        return {}

    monkeypatch.setattr(omc_exec.subprocess, "run", fake_run)
    monkeypatch.setattr(omc_exec, "_codex_supports_reasoning_effort", lambda: False)
    monkeypatch.setattr(omc_cost, "record", fake_record)

    rc = omc_exec._run_codex_headless(tmp_path, "prompt", timeout_sec=5, model_profile="mini_high")

    assert rc == 0
    assert recorded["root"] == tmp_path
    assert recorded["executor"] == "codex"
    assert recorded["llm_json"] == '{"usage":{"input_tokens":12,"output_tokens":3}}'
    assert recorded["model"] == "gpt-5.4-mini"


def test_run_codex_headless_records_timeout_row(monkeypatch, tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    recorded: dict[str, object] = {}

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex"], timeout=5)

    def fake_record(root, *, executor, session_id=None, task_title=None, llm_json=None, model="", base_ref="HEAD"):
        recorded["root"] = root
        recorded["executor"] = executor
        recorded["task_title"] = task_title
        recorded["llm_json"] = llm_json
        recorded["model"] = model
        return {}

    monkeypatch.setattr(omc_exec.subprocess, "run", fake_run)
    monkeypatch.setattr(omc_exec, "_codex_supports_reasoning_effort", lambda: False)
    monkeypatch.setattr(omc_cost, "record", fake_record)

    rc = omc_exec._run_codex_headless(tmp_path, "prompt", timeout_sec=5, model_profile="mini_high")

    assert rc == 124
    assert recorded["root"] == tmp_path
    assert recorded["executor"] == "codex"
    assert recorded["llm_json"] == ""
    assert recorded["model"] == "gpt-5.4-mini"


def test_record_headless_cost_warns_on_record_failure(capsys, monkeypatch, tmp_path: Path) -> None:
    def fake_record(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(omc_cost, "record", fake_record)

    omc_exec._record_headless_cost(
        tmp_path,
        executor="codex",
        raw_output='{"usage":{"input_tokens":1,"output_tokens":1}}',
        model_profile="mini_default",
        task_kind="review",
    )

    captured = capsys.readouterr()
    assert "cost record failed" in captured.err.lower()


def test_main_passes_selected_model_profile_to_codex_headless(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("리팩터링 영향 범위까지 보고 수정", encoding="utf-8")

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
        captured["project_root"] = project_root
        captured["prompt_text"] = prompt_text
        captured["timeout_sec"] = timeout_sec
        captured["model_profile"] = model_profile
        captured["task_kind"] = task_kind
        return 0

    monkeypatch.setattr(omc_exec, "_run_codex_headless", fake_run_codex_headless)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omc_exec.py",
            "--target",
            str(tmp_path),
            "--prompt-file",
            str(prompt_file),
            "--executor",
            "codex",
            "--execution-mode",
            "headless",
        ],
    )

    rc = omc_exec.main()

    assert rc == 0
    assert captured["model_profile"] == "mini_high"
    assert captured["task_kind"] == "task"


def test_main_honors_model_profile_cli_override(monkeypatch, tmp_path: Path) -> None:
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
            "--target",
            str(tmp_path),
            "--prompt-file",
            str(prompt_file),
            "--executor",
            "codex",
            "--execution-mode",
            "headless",
            "--model-profile",
            "full_default",
        ],
    )

    rc = omc_exec.main()

    assert rc == 0
    assert captured["model_profile"] == "full_default"
    assert captured["task_kind"] == "task"
