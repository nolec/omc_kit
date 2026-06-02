from __future__ import annotations

import subprocess
from pathlib import Path

import omc_exec


def test_codex_headless_command_uses_workspace_write_sandbox(tmp_path: Path) -> None:
    cmd = omc_exec._codex_headless_command(tmp_path, "prompt", tmp_path / "out.txt")

    assert "--ignore-user-config" not in cmd
    assert "--ephemeral" in cmd
    assert "-s" in cmd
    assert "workspace-write" in cmd
    assert "-o" in cmd


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
        assert 'model = "gpt-5.4"' in (runtime_home / "config.toml").read_text(encoding="utf-8")
        assert (runtime_home / "version.json").read_text(encoding="utf-8") == '{"version":"1"}'
        assert (runtime_home / "installation_id").read_text(encoding="utf-8") == "abc"
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

    rc = omc_exec._run_codex_headless(tmp_path, "prompt", timeout_sec=5)

    assert rc == 0
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["CODEX_HOME"] != str(source_home)
    assert captured["runtime_exists_during_run"] is True
