import json
import os
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "omc_work_class_lock.py"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _config_env(config: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "OMC_REQUIRE_WORK_CLASS_LOCK",
            "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE",
            "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY",
        }
    }
    env["OMC_WORK_CLASS_LOCK_CONFIG_FILE"] = str(config)
    return env


def test_init_creates_external_key_and_config_with_private_permissions(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    config = tmp_path / "config" / "work-class-lock.json"

    result = _run(
        "init",
        "--target",
        str(repository),
        "--config-file",
        str(config),
    )

    assert result.returncode == 0, result.stderr
    document = json.loads(config.read_text(encoding="utf-8"))
    key_path = Path(document["private_key_file"])
    assert document["schema_version"] == 1
    assert document["enabled"] is True
    assert key_path.is_file()
    assert repository not in key_path.parents
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert len(document["trusted_public_key"]) == 44


def test_init_refuses_to_overwrite_existing_custody(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    config = tmp_path / "config" / "work-class-lock.json"
    first = _run("init", "--target", str(repository), "--config-file", str(config))
    original = config.read_text(encoding="utf-8")

    second = _run("init", "--target", str(repository), "--config-file", str(config))

    assert first.returncode == 0
    assert second.returncode != 0
    assert "already exists" in second.stderr
    assert config.read_text(encoding="utf-8") == original


def test_preflight_reports_ready_without_creating_repository_state(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    config = tmp_path / "config" / "work-class-lock.json"
    assert _run("init", "--target", str(repository), "--config-file", str(config)).returncode == 0

    result = _run(
        "preflight",
        "--target",
        str(repository),
        env=_config_env(config),
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ready"
    assert report["source"] == "config"
    assert not (repository / ".omc").exists()


def test_explicit_environment_disable_overrides_enabled_config(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    config = tmp_path / "config" / "work-class-lock.json"
    assert _run("init", "--target", str(repository), "--config-file", str(config)).returncode == 0

    result = _run(
        "preflight",
        "--target",
        str(repository),
        env={
            **os.environ,
            "OMC_WORK_CLASS_LOCK_CONFIG_FILE": str(config),
            "OMC_REQUIRE_WORK_CLASS_LOCK": "0",
        },
    )

    assert result.returncode != 0
    assert "not enabled" in result.stderr


def test_init_preserves_existing_parent_directory_permissions(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    custody = tmp_path / "shared-custody"
    custody.mkdir(mode=0o755)

    result = _run(
        "init",
        "--target",
        str(repository),
        "--config-file",
        str(custody / "work-class-lock.json"),
    )

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(custody.stat().st_mode) == 0o755


def test_invalid_environment_enable_value_fails_closed(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()

    result = _run(
        "preflight",
        "--target",
        str(repository),
        env={**os.environ, "OMC_REQUIRE_WORK_CLASS_LOCK": "typo"},
    )

    assert result.returncode != 0
    assert "must be a boolean" in result.stderr


def test_partial_environment_configuration_fails_closed(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()

    result = _run(
        "preflight",
        "--target",
        str(repository),
        env={
            key: value
            for key, value in {**os.environ, "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE": "/tmp/key"}.items()
            if key != "OMC_REQUIRE_WORK_CLASS_LOCK"
        },
    )

    assert result.returncode != 0
    assert "environment configuration is incomplete" in result.stderr
