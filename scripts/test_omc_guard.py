import base64
import json
import os
import subprocess
import sys
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
    return key_path, base64.b64encode(raw_public_key).decode("ascii")


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
    assert "work class lock key configuration is required" in result.stderr
    assert not (target / ".omc" / "state" / "latest.json").exists()


def test_sync_require_rejects_repository_internal_work_class_lock_key(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    external_key_path, public_key = _work_class_lock_key(tmp_path)
    internal_key_path = target / "work-class-lock.key"
    internal_key_path.write_text(external_key_path.read_text())

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
