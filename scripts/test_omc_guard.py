import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_plan_candidate_universe as candidate_universe
import omc_state


ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "omc_guard.py"
OMC = ROOT / "scripts" / "omc.py"
CANDIDATE_UNIVERSE = ROOT / "scripts" / "omc_plan_candidate_universe.py"


def test_document_isolation_contract_is_published():
    contract = (ROOT / 'docs/orchestration_usage.md').read_text()
    assert '## 기존 코드 변경이 있는 문서 작업: 독립 clone V0' in contract
    assert '범위 밖 파일이 스테이징돼 있으면 커밋하지 않습니다' in contract


@pytest.mark.parametrize('case', ['valid', 'changed', 'absolute', 'parent', 'source', 'missing', 'symlink', 'symlink_parent', 'renamed_source', 'unstaged_rename', 'new_document', 'staged_new_document'])
def test_document_isolation_published_input_check(tmp_path, case):
    original = tmp_path / 'original'
    _init_git_repo(original)
    (original / 'docs').mkdir()
    doc = original / 'docs/plan.md'
    doc.write_text('before\n')
    subprocess.run(['git', '-C', str(original), 'add', 'docs/plan.md'], check=True)
    subprocess.run(['git', '-C', str(original), 'commit', '-qm', 'document baseline'], check=True)
    import hashlib
    expected = hashlib.sha256(doc.read_bytes()).hexdigest()
    relative = 'docs/plan.md'
    if case == 'changed': doc.write_text('changed\n')
    elif case == 'absolute': relative = str(doc)
    elif case == 'parent': relative = '../outside.md'
    elif case == 'source': relative = 'app.py'
    elif case == 'missing': relative = 'docs/missing.md'
    elif case == 'symlink':
        doc.unlink(); doc.symlink_to(original / 'app.py')
    elif case == 'symlink_parent':
        (original / 'docs').rename(original / 'docs-actual')
        (original / 'docs').symlink_to(original / 'docs-actual', target_is_directory=True)
    elif case == 'renamed_source':
        subprocess.run(['git', '-C', str(original), 'mv', 'app.py', 'docs/code.md'], check=True); relative = 'docs/code.md'
    elif case == 'unstaged_rename':
        (original / 'app.py').rename(original / 'docs/code.md'); relative = 'docs/code.md'
    elif case in ('new_document', 'staged_new_document'):
        (original / 'docs/new.md').write_text('new document\n'); relative = 'docs/new.md'
        if case == 'staged_new_document':
            subprocess.run(['git', '-C', str(original), 'add', relative], check=True)
    contract = (ROOT / 'docs/orchestration_usage.md').read_text()
    program = contract.split('<!-- OMC:document-isolation-check -->\n```python\n', 1)[1].split('\n```', 1)[0]
    before_index = (original / '.git/index').read_bytes()
    before_doc = doc.read_bytes()
    if case == 'valid':
        initial = subprocess.run([sys.executable, '-c', program, str(original), relative], capture_output=True, text=True)
        assert initial.returncode == 0 and initial.stdout.strip() == '확인 통과: ' + expected
    if case not in ('valid', 'changed'):
        initial = subprocess.run([sys.executable, '-c', program, str(original), relative], capture_output=True, text=True)
        assert initial.returncode == 1 and '확인 통과' not in initial.stdout
    result = subprocess.run([sys.executable, '-c', program, str(original), relative, expected], capture_output=True, text=True)
    assert result.returncode == (0 if case == 'valid' else 1), result.stdout + result.stderr
    assert doc.read_bytes() == before_doc
    assert ('확인 통과' in result.stdout) == (case == 'valid')
    assert (original / '.git/index').read_bytes() == before_index


def test_document_isolation_real_cli_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setenv('OMC_INSTALLATION_REGISTRY_DIR', str(tmp_path / 'local-data'))
    original = tmp_path / 'original'
    _init_git_repo(original)
    (original / 'docs').mkdir()
    doc = original / 'docs/plan.md'
    doc.write_text('old\n')
    subprocess.run(['git', '-C', str(original), 'add', 'docs/plan.md'], check=True)
    subprocess.run(['git', '-C', str(original), 'commit', '-qm', 'document baseline'], check=True)
    (original / 'app.py').write_text('value = 2\n')
    subprocess.run(['git', '-C', str(original), 'add', 'app.py'], check=True)
    doc.write_text('existing document edits\n')
    before_index = (original / '.git/index').read_bytes()
    before_code, before_doc = (original / 'app.py').read_bytes(), doc.read_bytes()
    before_head = subprocess.check_output(['git', '-C', str(original), 'rev-parse', 'HEAD'])
    (original / '.omc').mkdir()
    receipt = original / '.omc/install-receipt.json'
    receipt.write_bytes(b'{"preserve":true}\n')
    args = [sys.executable, str(GUARD), 'sync-require', '--mode', 'autopilot', '--title', 'docs',
            '--request', 'document only', '--roles', 'senior_coding', '--work-class', 'document_only', '--for', 'task']
    blocked = subprocess.run(args + ['--target', str(original)], capture_output=True, text=True)
    assert blocked.returncode != 0 and 'work_class_scope_conflict' in blocked.stderr
    isolated = tmp_path / 'isolated'
    subprocess.run(['git', 'clone', '--no-hardlinks', '--', str(original), str(isolated)], check=True, capture_output=True)
    for key, value in [('user.name', 'OMC'), ('user.email', 'omc@example.com')]:
        subprocess.run(['git', '-C', str(isolated), 'config', key, value], check=True)
    installed = subprocess.run([sys.executable, str(OMC), 'setup', '--target', str(isolated), '--force', '--skip-session-start'], capture_output=True, text=True)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    deployed = (isolated / 'docs/omc_workflow.md').read_text()
    original_guide = (ROOT / 'docs/orchestration_usage.md').read_text().split('## 기존 코드 변경이 있는 문서 작업: 독립 clone V0', 1)[1].split('## 중단된 workflow-v2의 복구 판단', 1)[0]
    assert '## 기존 코드 변경이 있는 문서 작업: 독립 clone V0' + original_guide in deployed
    (isolated / 'docs/plan.md').write_bytes(before_doc)
    started = subprocess.run(args + ['--target', str(isolated)], capture_output=True, text=True)
    assert started.returncode == 0, started.stdout + started.stderr
    state = omc_state.read_latest(isolated)
    session = json.loads((isolated / '.omc/state/sessions' / state['latest_session_id'] / 'session.json').read_text())
    assert session['work_class'] == 'document_only'
    (isolated / 'docs/plan.md').write_text('existing document edits\nnew document edits\n')
    subprocess.run(['git', '-C', str(isolated), 'add', 'docs/plan.md'], check=True)
    assert subprocess.check_output(['git', '-C', str(isolated), 'diff', '--cached', '--name-only']).strip() == b'docs/plan.md'
    subprocess.run(['git', '-C', str(isolated), 'commit', '-qm', 'document only'], check=True, capture_output=True)
    assert subprocess.check_output(['git', '-C', str(isolated), 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD']).strip() == b'docs/plan.md'
    completion = json.loads((isolated / '.omc/state/sessions' / state['latest_session_id'] / 'completion.json').read_text())
    assert completion['work_class'] == 'document_only' and completion['changed_paths'] == ['docs/plan.md']
    assert (original / '.git/index').read_bytes() == before_index
    assert (original / 'app.py').read_bytes() == before_code and doc.read_bytes() == before_doc
    assert subprocess.check_output(['git', '-C', str(original), 'rev-parse', 'HEAD']) == before_head
    assert receipt.read_bytes() == b'{"preserve":true}\n'


@pytest.mark.parametrize('change', ['new_source', 'source_rename'])
def test_document_isolation_source_changes_still_block_start(tmp_path, change):
    isolated = tmp_path / 'isolated'
    _init_git_repo(isolated)
    if change == 'new_source':
        (isolated / 'new.py').write_text('pass\n')
    else:
        subprocess.run(['git', '-C', str(isolated), 'mv', 'app.py', 'notes.md'], check=True)
    result = subprocess.run([sys.executable, str(GUARD), 'sync-require', '--target', str(isolated),
        '--mode', 'autopilot', '--title', 'docs', '--request', 'document only', '--roles', 'senior_coding',
        '--work-class', 'document_only', '--for', 'task'], capture_output=True, text=True)
    assert result.returncode != 0 and 'work_class_scope_conflict' in result.stderr


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


def test_document_only_start_rejects_obvious_source_changes(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(GUARD), "sync-require", "--target", str(target),
            "--mode", "autopilot", "--title", "omc-task",
            "--request", "document current work", "--roles", "senior_coding",
            "--work-class", "document_only", "--completion-action", "start",
            "--for", "task",
        ],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "work_class_scope_conflict" in result.stderr


def test_document_only_start_rejects_document_renamed_to_source(tmp_path: Path):
    target = tmp_path / "repo"
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
    (target / "README.md").write_text("documentation\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-qm", "baseline"], check=True
    )
    subprocess.run(
        ["git", "-C", str(target), "mv", "README.md", "app.py"], check=True
    )

    result = subprocess.run(
        [
            sys.executable, str(GUARD), "sync-require", "--target", str(target),
            "--mode", "autopilot", "--title", "omc-task",
            "--request", "document current work", "--roles", "senior_coding",
            "--work-class", "document_only", "--completion-action", "start",
            "--for", "task",
        ],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "work_class_scope_conflict" in result.stderr


def test_start_rejects_caller_supplied_work_id(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)

    try:
        omc_state.record_session(
            target, mode="autopilot", title="omc-task", request="new work",
            role_ids=["senior_coding"], work_class="implementation",
            completion_action="start", work_id="reused-id", confirmed=True,
        )
    except ValueError as error:
        assert str(error) == "work id is generated for completion start"
    else:
        raise AssertionError("caller supplied work id was accepted")


def test_continue_rejects_work_class_change(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    started = omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )

    try:
        omc_state.record_session(
            target, mode="autopilot", title="omc-task", request="reclassify",
            role_ids=["senior_coding"], work_class="document_only",
            completion_action="continue", work_id=started["work_id"], confirmed=True,
        )
    except ValueError as error:
        assert str(error) == "completion continuation work class mismatch"
    else:
        raise AssertionError("completion continuation changed work class")


def test_continue_extends_valid_lineage(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    started = omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )

    continued = omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="fix review",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="continue", work_id=started["work_id"], confirmed=True,
    )

    pending = json.loads(
        (target / ".omc" / "state" / "pending-completion.json").read_text(
            encoding="utf-8"
        )
    )
    assert pending["work_id"] == started["work_id"]
    assert pending["root_session_id"] == started["session_id"]
    assert pending["session_ids"] == [started["session_id"], continued["session_id"]]
    assert pending["rework_count"] == 1


def test_continue_rejects_malformed_lineage_without_recording_session(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    started = omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )
    pending_path = target / ".omc" / "state" / "pending-completion.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["session_ids"] = 1
    pending_path.write_text(json.dumps(pending), encoding="utf-8")
    latest_path = target / ".omc" / "state" / "latest.json"
    latest_before = json.loads(latest_path.read_text(encoding="utf-8"))
    sessions_dir = target / ".omc" / "state" / "sessions"
    sessions_before = sorted(path.name for path in sessions_dir.iterdir())

    try:
        omc_state.record_session(
            target, mode="autopilot", title="omc-task", request="fix review",
            role_ids=["senior_coding"], work_class="implementation",
            completion_action="continue", work_id=started["work_id"], confirmed=True,
        )
    except ValueError as error:
        assert str(error) == "completion continuation does not match pending work"
    else:
        raise AssertionError("malformed completion lineage was continued")

    assert json.loads(latest_path.read_text(encoding="utf-8")) == latest_before
    assert sorted(path.name for path in sessions_dir.iterdir()) == sessions_before


@pytest.mark.parametrize('action', ['preserve', 'preserve-if-present'])
@pytest.mark.parametrize('role', ['directive', 'analysis'])
def test_explicit_preserve_keeps_pending_and_records_original_completion(tmp_path, action, role):
    target = tmp_path / 'repo'
    _init_git_repo(target)
    started = omc_state.record_session(
        target, mode='autopilot', title='omc-task', request='implementation',
        role_ids=['senior_coding'], work_class='implementation',
        completion_action='start', confirmed=True,
    )
    pending_path = target / '.omc/state/pending-completion.json'
    before = pending_path.read_bytes()
    preserved = omc_state.record_session(
        target, mode='autopilot', title='local-commit', request='preserve original work',
        role_ids=[role], completion_action=action, confirmed=False,
    )
    omc_state.confirm_session(target, session_id=preserved['session_id'])
    assert pending_path.read_bytes() == before
    (target / 'app.py').write_text('value = 2\n')
    subprocess.run(['git', '-C', str(target), 'add', 'app.py'], check=True)
    subprocess.run(['git', '-C', str(target), 'commit', '--no-verify', '-qm', 'complete'], check=True)
    result = omc_state.record_completion_receipt(target)
    assert result['status'] == 'ok'
    assert (target / '.omc/state/sessions' / started['session_id'] / 'completion.json').is_file()
    assert not (target / '.omc/state/sessions' / preserved['session_id'] / 'completion.json').exists()


def test_preserve_rejects_stale_baseline(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )
    subprocess.run(
        ["git", "-C", str(target), "commit", "--allow-empty", "-qm", "unrelated"],
        check=True,
    )

    try:
        omc_state.record_session(
            target, mode="autopilot", title="omc-task", request="commit old work",
            role_ids=["senior_coding"], work_class="implementation",
            completion_action="preserve", confirmed=True,
        )
    except ValueError as error:
        assert str(error) == "completion preserve does not match pending baseline"
    else:
        raise AssertionError("stale completion candidate was preserved")


def test_preserve_rejects_pending_that_does_not_match_source_session(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )
    pending_path = target / ".omc" / "state" / "pending-completion.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["request_sha256"] = "0" * 64
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    try:
        omc_state.record_session(
            target, mode="autopilot", title="omc-task", request="commit old work",
            role_ids=["senior_coding"], work_class="implementation",
            completion_action="preserve", confirmed=True,
        )
    except ValueError as error:
        assert str(error) == "completion preserve does not match pending session"
    else:
        raise AssertionError("mismatched completion candidate was preserved")


def test_preserve_rejects_work_id_that_does_not_match_source_session(
    tmp_path: Path,
):
    target = tmp_path / "repo"
    _init_git_repo(target)
    omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )
    pending_path = target / ".omc" / "state" / "pending-completion.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["work_id"] = "different-work"
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    try:
        omc_state.record_session(
            target, mode="autopilot", title="omc-task", request="commit old work",
            role_ids=["senior_coding"], work_class="implementation",
            completion_action="preserve", confirmed=True,
        )
    except ValueError as error:
        assert str(error) == "completion preserve does not match pending session"
    else:
        raise AssertionError("mismatched work id was preserved")


def test_completion_receipt_rejects_inconsistent_v3_lineage(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    session = omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )
    pending_path = target / ".omc" / "state" / "pending-completion.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["rework_count"] = 7
    pending_path.write_text(json.dumps(pending), encoding="utf-8")
    (target / "app.py").write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "--no-verify", "-qm", "complete"],
        check=True,
    )

    result = omc_state.record_completion_receipt(target)

    assert result == {"status": "skipped", "reason": "pending completion is invalid"}
    session_dir = target / ".omc" / "state" / "sessions" / session["session_id"]
    assert not (session_dir / "completion-lineage.json").exists()


def test_record_session_reads_git_snapshot_once(tmp_path: Path, monkeypatch):
    target = tmp_path / "repo"
    _init_git_repo(target)
    calls = 0

    def fake_git_info(_project_root: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"branch": "main", "head": "baseline", "dirty": False}

    monkeypatch.setattr(omc_state, "_git_info", fake_git_info)
    monkeypatch.setattr(omc_state, "_rewrite_notepad", lambda _project_root: None)
    session = omc_state.record_session(
        target, mode="autopilot", title="omc-task", request="implementation",
        role_ids=["senior_coding"], work_class="implementation",
        completion_action="start", confirmed=True,
    )

    assert calls == 1
    assert session["git"]["head"] == "baseline"


def test_document_only_check_fails_closed_when_git_status_fails(tmp_path: Path):
    target = tmp_path / "not-a-repository"
    target.mkdir()

    result = subprocess.run(
        [
            sys.executable, str(GUARD), "sync-require", "--target", str(target),
            "--mode", "autopilot", "--title", "omc-task", "--request", "docs",
            "--roles", "senior_coding", "--work-class", "document_only",
            "--completion-action", "start", "--for", "task",
        ],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "work_class_scope_unavailable" in result.stderr


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


def test_preserve_guard_seals_reference_without_mutating_lineage(tmp_path: Path):
    target = tmp_path / "repo"
    _init_git_repo(target)
    key_path, public_key = _work_class_lock_key(tmp_path)
    env = {**os.environ, "OMC_REQUIRE_WORK_CLASS_LOCK": "1",
           "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE": str(key_path),
           "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY": public_key}
    command = [sys.executable, str(GUARD), "sync-require", "--target", str(target),
               "--mode", "autopilot", "--title", "omc-task", "--request", "synthetic test",
               "--roles", "senior_coding", "--work-class", "implementation", "--for", "task"]
    started = subprocess.run(command + ["--completion-action", "start"], env=env, capture_output=True, text=True)
    assert started.returncode == 0, started.stderr
    pending_path = target / ".omc/state/pending-completion.json"
    pending_bytes = pending_path.read_bytes()
    pending = json.loads(pending_bytes)
    original_dir = target / ".omc/state/sessions" / pending["session_id"]
    original_lock = (original_dir / "work_class_lock.json").read_bytes()
    preserved = subprocess.run(command + ["--completion-action", "preserve"], env=env, capture_output=True, text=True)
    assert preserved.returncode == 0, preserved.stderr
    latest = json.loads((target / ".omc/state/latest.json").read_text())
    directory = target / ".omc/state/sessions" / latest["latest_session_id"]
    session = json.loads((directory / "session.json").read_text())
    lock = json.loads((directory / "work_class_lock.json").read_text())
    assert session["confirmation"]["status"] == "confirmed"
    assert session["work_id"] == pending["work_id"]
    assert session["lineage_root_session_id"] is None
    assert session["lineage_index"] is None
    assert lock["schema_version"] == 1
    candidate_universe._validate_work_class_lock_receipt_envelope(lock, expected_status="frozen")
    candidate_universe._verify_document(
        lock, digest_field="receipt_sha256", trusted_public_keys={public_key},
        expected_digest=lock["receipt_sha256"],
        expected_signer="independent-work-class-lock-v1", label="preserve lock",
    )
    assert pending_path.read_bytes() == pending_bytes
    assert (original_dir / "work_class_lock.json").read_bytes() == original_lock
    assert not (directory / "completion-lineage.json").exists()


def test_preserve_lock_rejects_partial_or_non_preserve_lineage(tmp_path: Path):
    import pytest
    target = tmp_path / "repo"
    _init_git_repo(target)
    start = omc_state.record_session(target, mode="autopilot", title="task", request="test",
                                    role_ids=["senior_coding"], work_class="implementation",
                                    completion_action="start", confirmed=True)
    preserve = omc_state.record_session(target, mode="autopilot", title="task", request="test",
                                       role_ids=["senior_coding"], work_class="implementation",
                                       completion_action="preserve", confirmed=True)
    draft = candidate_universe.prepare_work_class_lock_receipt(preserve)
    assert draft["schema_version"] == 1
    assert "work_id" not in draft
    for action in ("start", "continue", None):
        with pytest.raises(ValueError, match="source session is invalid"):
            candidate_universe.prepare_work_class_lock_receipt({**preserve, "completion_action": action})
    for field, value in (("lineage_root_session_id", start["session_id"]),
                         ("lineage_previous_session_id", start["session_id"]), ("lineage_index", 0)):
        with pytest.raises(ValueError, match="source session is invalid"):
            candidate_universe.prepare_work_class_lock_receipt({**preserve, field: value})


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
    full_head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert session["git"]["head"] == full_head
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
