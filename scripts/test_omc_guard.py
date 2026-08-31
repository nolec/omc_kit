import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_plan_candidate_universe as candidate_universe


ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "omc_guard.py"
OMC = ROOT / "scripts" / "omc.py"
CANDIDATE_UNIVERSE = ROOT / "scripts" / "omc_plan_candidate_universe.py"


def _init_git_repo(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "config", "user.email", "omc@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(target), "config", "user.name", "OMC"],
        check=True,
    )
    (target / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-qm", "baseline"],
        check=True,
    )


def _acknowledge_local_commit(target: Path) -> None:
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    synced = subprocess.run(
        [
            sys.executable,
            str(OMC),
            "state",
            "sync-session",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-review",
            "--request",
            "reviewed local commit",
            "--roles",
            "code_review",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert synced.returncode == 0, synced.stderr
    options = json.dumps(
        [
            {
                "id": "confirm",
                "aliases": ["확인"],
                "value": "commit",
                "paths": ["app.py"],
            }
        ],
        ensure_ascii=False,
    )
    opened = subprocess.run(
        [
            sys.executable,
            str(OMC),
            "state",
            "decision-open",
            "--target",
            str(target),
            "--decision-id",
            "reviewed-commit",
            "--action",
            "local_commit",
            "--options-json",
            options,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert opened.returncode == 0, opened.stderr
    resolved = subprocess.run(
        [
            sys.executable,
            str(OMC),
            "state",
            "decision-resolve",
            "--target",
            str(target),
            "--response",
            "확인",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    subprocess.run(["git", "-C", str(target), "add", "app.py"], check=True)


def test_git_commit_guard_accepts_scope_bound_local_commit_receipt(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _acknowledge_local_commit(target)

    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "git commit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert "local_commit decision=reviewed-commit" in result.stdout


def test_git_commit_guard_rejects_local_commit_receipt_after_content_drift(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _acknowledge_local_commit(target)
    (target / "app.py").write_text("value = 3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "app.py"], check=True)

    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "git commit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "local_commit receipt rejected: staged_content_changed" in result.stdout


def test_git_commit_guard_clears_prior_authorization_after_expiry(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    _acknowledge_local_commit(target)

    authorized = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "git commit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert authorized.returncode == 0, authorized.stdout

    latest_path = target / ".omc" / "state" / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    latest["pending_decision"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    latest_path.write_text(json.dumps(latest), encoding="utf-8")

    rejected = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "git commit",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert rejected.returncode == 4
    assert "local_commit receipt rejected: expired" in rejected.stdout
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert latest["pending_decision"]["authorization"] is False


def _work_class_lock_key(tmp_path: Path) -> tuple[Path, str]:
    private_key = Ed25519PrivateKey.generate()
    raw_private_key = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    raw_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    key_path = tmp_path / "work-class-lock.key"
    key_path.write_text(base64.b64encode(raw_private_key).decode("ascii"))
    key_path.chmod(0o600)
    return key_path, base64.b64encode(raw_public_key).decode("ascii")


def _work_class_lock_config_env(config_path: Path) -> dict[str, str]:
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
    env["OMC_WORK_CLASS_LOCK_CONFIG_FILE"] = str(config_path)
    return env


def test_sync_require_forwards_explicit_work_class(tmp_path: Path):
    target = tmp_path / "repo"
    target.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "measure a latency benchmark",
            "--roles",
            "senior_coding",
            "--work-class",
            "benchmark_maintenance",
            "--for",
            "task",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    latest = json.loads(
        (target / ".omc" / "state" / "latest.json").read_text(encoding="utf-8")
    )
    session = json.loads(
        (
            target
            / ".omc"
            / "state"
            / "sessions"
            / latest["latest_session_id"]
            / "session.json"
        ).read_text(encoding="utf-8")
    )
    assert session["work_class"] == "benchmark_maintenance"


def test_sync_require_rejects_missing_work_class_for_coding_session(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    target.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "unclassified coding task",
            "--roles",
            "senior_coding",
            "--for",
            "task",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "work class is required" in result.stderr


def test_sync_require_seals_required_work_class_lock_before_completion(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    subprocess.run(
        ["git", "-C", str(target), "config", "core.abbrev", "12"],
        check=True,
    )
    key_path, public_key = _work_class_lock_key(tmp_path)
    env = {
        **os.environ,
        "OMC_REQUIRE_WORK_CLASS_LOCK": "1",
        "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE": str(key_path),
        "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY": public_key,
    }

    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "observed implementation",
            "--roles",
            "senior_coding",
            "--work-class",
            "implementation",
            "--for",
            "task",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    latest = json.loads(
        (target / ".omc" / "state" / "latest.json").read_text()
    )
    lock_path = (
        target
        / ".omc"
        / "state"
        / "sessions"
        / latest["latest_session_id"]
        / "work_class_lock.json"
    )
    lock = json.loads(lock_path.read_text())
    session = json.loads((lock_path.parent / "session.json").read_text())
    assert len(session["git"]["head"]) == 12
    assert lock["status"] == "frozen"
    assert lock["work_class"] == "implementation"
    assert lock["signoff"]["signer_public_key"] == public_key

    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-qm", "followup"],
        check=True,
    )
    completion = subprocess.run(
        [
            sys.executable,
            str(OMC),
            "state",
            "complete",
            "--target",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completion.returncode == 0, completion.stderr
    assert candidate_universe._locked_completion_work_class(
        {
            "repository_root": str(target),
            "session_id": latest["latest_session_id"],
            "work_class": "implementation",
        },
        trusted_work_class_lock_public_keys={public_key},
    ) == "implementation"


def test_sync_require_uses_external_work_class_lock_config(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    key_path, public_key = _work_class_lock_key(tmp_path)
    config_path = tmp_path / "work-class-lock.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": True,
                "private_key_file": str(key_path),
                "trusted_public_key": public_key,
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "observed implementation",
            "--roles",
            "senior_coding",
            "--work-class",
            "implementation",
            "--for",
            "task",
        ],
        env=_work_class_lock_config_env(config_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    latest = json.loads((target / ".omc" / "state" / "latest.json").read_text())
    lock_path = (
        target
        / ".omc"
        / "state"
        / "sessions"
        / latest["latest_session_id"]
        / "work_class_lock.json"
    )
    assert json.loads(lock_path.read_text())["signoff"]["signer_public_key"] == public_key


def test_prepare_work_class_lock_cli_resolves_stored_short_sha(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    subprocess.run(
        ["git", "-C", str(target), "config", "core.abbrev", "12"],
        check=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "observed implementation",
            "--roles",
            "senior_coding",
            "--work-class",
            "implementation",
            "--for",
            "task",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    latest = json.loads(
        (target / ".omc" / "state" / "latest.json").read_text()
    )
    session_path = (
        target
        / ".omc"
        / "state"
        / "sessions"
        / latest["latest_session_id"]
        / "session.json"
    )
    output_path = tmp_path / "work-class-lock-draft.json"

    prepared = subprocess.run(
        [
            sys.executable,
            str(CANDIDATE_UNIVERSE),
            "prepare-work-class-lock",
            str(session_path),
            "--repository-root",
            str(target),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert prepared.returncode == 0, prepared.stderr
    draft = json.loads(output_path.read_text())
    assert len(draft["baseline_commit"]) == 40
    assert draft["baseline_commit"].startswith(
        json.loads(session_path.read_text())["git"]["head"]
    )


def test_sync_require_lock_failure_leaves_latest_session_unconfirmed(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    target.mkdir()
    subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
    key_path, public_key = _work_class_lock_key(tmp_path)
    env = {
        **os.environ,
        "OMC_REQUIRE_WORK_CLASS_LOCK": "1",
        "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE": str(key_path),
        "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY": public_key,
    }
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "observed implementation",
            "--roles",
            "senior_coding",
            "--work-class",
            "implementation",
            "--for",
            "task",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0

    latest = json.loads(
        (target / ".omc" / "state" / "latest.json").read_text()
    )
    assert latest["latest_session_id"] != latest["latest_confirmed_session_id"]
    required = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "require",
            "--target",
            str(target),
            "--for",
            "task",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert required.returncode != 0
    assert "is not confirmed" in required.stdout


def test_sync_require_fails_before_session_when_required_lock_key_is_missing(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "observed implementation",
            "--roles",
            "senior_coding",
            "--work-class",
            "implementation",
            "--for",
            "task",
        ],
        env={**os.environ, "OMC_REQUIRE_WORK_CLASS_LOCK": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "work class lock environment configuration is incomplete" in result.stderr
    assert not (target / ".omc" / "state" / "latest.json").exists()


def test_sync_require_rejects_repository_internal_work_class_lock_key(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    external_key_path, public_key = _work_class_lock_key(tmp_path)
    internal_key_path = target / "work-class-lock.key"
    internal_key_path.write_text(external_key_path.read_text())
    internal_key_path.chmod(0o600)

    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "observed implementation",
            "--roles",
            "senior_coding",
            "--work-class",
            "implementation",
            "--for",
            "task",
        ],
        env={
            **os.environ,
            "OMC_REQUIRE_WORK_CLASS_LOCK": "1",
            "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE": str(internal_key_path),
            "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY": public_key,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "must be outside the repository" in result.stderr
    assert not (target / ".omc" / "state" / "latest.json").exists()


def test_sync_require_rejects_group_readable_work_class_lock_key(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    key_path, public_key = _work_class_lock_key(tmp_path)
    key_path.chmod(0o640)

    result = subprocess.run(
        [
            sys.executable,
            str(GUARD),
            "sync-require",
            "--target",
            str(target),
            "--mode",
            "autopilot",
            "--title",
            "omc-task",
            "--request",
            "observed implementation",
            "--roles",
            "senior_coding",
            "--work-class",
            "implementation",
            "--for",
            "task",
        ],
        env={
            **os.environ,
            "OMC_REQUIRE_WORK_CLASS_LOCK": "1",
            "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE": str(key_path),
            "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY": public_key,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "permissions must be 0600" in result.stderr
    assert not (target / ".omc" / "state" / "latest.json").exists()
