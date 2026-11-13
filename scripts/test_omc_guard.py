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

    assert result == {"status": "error", "reason": "pending completion is invalid",
                      "reason_code": "pending_lineage_invalid"}
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


@pytest.mark.parametrize('case,reason', [
    ('missing', 'no_pending_completion'), ('json', 'pending_json_invalid'),
    ('list', 'pending_fields_invalid'), ('fields', 'pending_fields_invalid'),
    ('symlink', 'pending_input_invalid'), ('permission', 'pending_read_failed'),
])
def test_completion_classification_pending_inputs(tmp_path, monkeypatch, case, reason):
    target = tmp_path / 'repo'
    _init_git_repo(target)
    pending = target / '.omc/state/pending-completion.json'
    pending.parent.mkdir(parents=True)
    if case == 'json': pending.write_text('{')
    elif case == 'list': pending.write_text('[]')
    elif case == 'fields': pending.write_text('{}')
    elif case == 'symlink': pending.symlink_to(target / 'missing.json')
    elif case == 'permission':
        pending.write_text('{}')
        original = os.open
        def denied(path, *args, **kwargs):
            if Path(path) == pending: raise PermissionError('injected read denial')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(os, 'open', denied)
    before = pending.read_bytes() if case in ('json', 'list', 'fields') else None
    result = omc_state.record_completion_receipt(target)
    assert result['status'] == ('skipped' if case == 'missing' else 'error')
    assert result['reason_code'] == reason
    if before is not None: assert pending.read_bytes() == before


@pytest.mark.parametrize('case,expected_code', [('missing', 0), ('invalid', 1), ('mismatch', 1)])
def test_completion_classification_public_cli(tmp_path, case, expected_code):
    target = tmp_path / 'repo'
    _init_git_repo(target)
    if case == 'mismatch':
        omc_state.record_session(target, mode='autopilot', title='test', request='implement',
            role_ids=['senior_coding'], work_class='implementation',
            completion_action='start', confirmed=True)
        pending = target / '.omc/state/pending-completion.json'
        data = json.loads(pending.read_text()); data['request_sha256'] = '0' * 64
        pending.write_text(json.dumps(data))
    elif case == 'invalid':
        pending = target / '.omc/state/pending-completion.json'
        pending.parent.mkdir(parents=True); pending.write_text('{')
    result = subprocess.run([sys.executable, str(OMC), 'state', 'complete', '--target', str(target)],
        capture_output=True, text=True)
    assert result.returncode == expected_code, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload['status'] == ('skipped' if case == 'missing' else 'error')
    if case != 'missing': assert pending.exists()


def test_t1_sync_frozen_work_returns_diagnosis_without_new_session(tmp_path):
    import time
    root=tmp_path/'frozen';_init_git_repo(root);omc_state.init_state(root)
    session=omc_state.record_session(root,mode='autopilot',title='fixture',request='frozen work',
        role_ids=['senior_coding'],confirmed=True,work_class='implementation',completion_action='start')
    pipeline=root/'.omc/pipeline_session.json'
    pipeline.write_text(json.dumps({'session_id':session['session_id'],'owner_work_id':session['work_id'],
                                    'started_at':time.time()-28900}))
    before=(root/'.omc/state/latest.json').read_bytes();members=list((root/'.omc/state/sessions').iterdir())
    result=subprocess.run([sys.executable,str(GUARD),'sync-require','--target',str(root),'--mode','autopilot',
        '--title','repair','--request','same repair','--roles','senior_coding','--work-class','implementation',
        '--completion-action','continue','--work-id',session['work_id'],'--for','task'],capture_output=True,text=True)
    assert result.returncode!=0
    assert '동결' in result.stdout and 'Traceback' not in result.stderr
    assert (root/'.omc/state/latest.json').read_bytes()==before
    assert list((root/'.omc/state/sessions').iterdir())==members


# Independent entry V0: the source governance is not an entry dependency.
def _isolated_entry(source, destination, *extra, env=None):
    return subprocess.run([sys.executable, str(GUARD), 'start-isolated',
                           '--source', str(source), '--destination', str(destination),
                           '--request', 'independent fixture implementation', *extra],
                          capture_output=True, text=True, env=env, timeout=30)


def test_isolated_entry_new_work_without_source_governance(tmp_path):
    source = tmp_path / 'source'; _init_git_repo(source)
    (source/'.omc').mkdir(); (source/'.omc/pipeline_session.json').write_text('malformed')
    (source/'app.py').write_text('unfinished source change\n')
    subprocess.run(['git','-C',str(source),'add','app.py'],check=True)
    before = (source/'.git/index').read_bytes()
    destination = tmp_path/'new'
    result = _isolated_entry(source,destination,'--write-path','app.py')
    assert result.returncode == 0, result.stdout+result.stderr
    data = json.loads(result.stdout)
    assert data['source_governance'] == data['source_execution'] == 'NOT_CHECKED'
    assert data['workspace_route'] == 'ISOLATED'
    assert (destination/'.git').is_dir()
    assert (destination/'app.py').read_text() == 'value = 1\n'
    assert (source/'app.py').read_text() == 'unfinished source change\n'
    assert (source/'.git/index').read_bytes() == before
    assert (source/'.omc/pipeline_session.json').read_text() == 'malformed'
    pending=json.loads((destination/'.omc/state/pending-completion.json').read_text())
    assert pending['work_id'] == data['work_id'] and data['session_id'] == pending['session_id']
    state=json.loads((destination/'.omc/pipeline_session.json').read_text())
    assert not state['contract_confirmed'] and not state['red_done_tests']
    assert data['stages']['READ']['status'] == data['stages']['EDIT']['status'] == 'allowed'
    assert data['stages']['VERIFY']['status'] == 'blocked'


def test_isolated_entry_does_not_wait_for_source_lock(tmp_path):
    import fcntl,time
    source=tmp_path/'source';_init_git_repo(source)
    folder=source/'.omc/state';folder.mkdir(parents=True)
    with (folder/'.lock').open('w') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        started=time.monotonic()
        result=_isolated_entry(source,tmp_path/'new','--timeout-sec','5')
    assert result.returncode == 0, result.stdout+result.stderr
    assert time.monotonic()-started < 5
    assert json.loads(result.stdout)['stages']['EDIT']['status']=='needs_scope'


@pytest.mark.parametrize('relation', ['same','inside','ancestor','existing','symlink'])
def test_isolated_entry_rejects_unsafe_destination(tmp_path,relation):
    source=tmp_path/'source';_init_git_repo(source)
    destination={'same':source,'inside':source/'new','ancestor':tmp_path}.get(relation,tmp_path/'new')
    if relation=='existing': destination.mkdir();(destination/'keep').write_text('preserve')
    if relation=='symlink': destination.symlink_to(source,target_is_directory=True)
    result=_isolated_entry(source,destination)
    assert result.returncode == 1, result.stdout+result.stderr
    assert json.loads(result.stdout)['reason_code'].startswith('destination_')
    if relation=='existing': assert (destination/'keep').read_text()=='preserve'


def test_isolated_entry_external_link_only_blocks_its_edit(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'external.py').symlink_to(source/'app.py')
    subprocess.run(['git','-C',str(source),'add','external.py'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','link fixture'],check=True)
    result=_isolated_entry(source,tmp_path/'new','--write-path','app.py')
    assert result.returncode == 0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert 'external.py' in value['blocked_edit_paths']
    assert value['stages']['EDIT']['status']=='allowed'
    blocked=_isolated_entry(source,tmp_path/'other','--write-path','external.py')
    assert blocked.returncode == 0,blocked.stdout+blocked.stderr
    assert json.loads(blocked.stdout)['stages']['EDIT']['status']=='blocked'


def test_isolated_entry_disables_injected_checkout_filter(tmp_path):
    import os
    source=tmp_path/'source';_init_git_repo(source)
    (source/'.gitattributes').write_text('app.py filter=outside\n')
    subprocess.run(['git','-C',str(source),'add','.gitattributes'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','filter fixture'],check=True)
    marker=tmp_path/'must-not-write'
    env=dict(os.environ,GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='filter.outside.smudge',
             GIT_CONFIG_VALUE_0='touch '+str(marker))
    result=_isolated_entry(source,tmp_path/'new',env=env)
    assert result.returncode == 0,result.stdout+result.stderr
    assert not marker.exists()


def test_isolated_entry_timeout_keeps_partial_destination(tmp_path,monkeypatch,capsys):
    import argparse,omc_guard as runtime
    source=tmp_path/'source';_init_git_repo(source)
    def stalled(*args,**kwargs): raise subprocess.TimeoutExpired(args[0],.01)
    monkeypatch.setattr(runtime.subprocess,'run',stalled)
    args=argparse.Namespace(source=source,destination=tmp_path/'new',request='fixture',
                            baseline=None,write_path=[],timeout_sec=.01)
    assert runtime.start_isolated(args)==2
    assert json.loads(capsys.readouterr().out)['reason_code']=='preparation_timeout'


def test_isolated_entry_rejects_destination_reuse(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    destination=tmp_path/'new'
    assert _isolated_entry(source,destination).returncode==0
    before=(destination/'.omc/state/pending-completion.json').read_bytes()
    second=_isolated_entry(source,destination)
    assert second.returncode==1
    assert (destination/'.omc/state/pending-completion.json').read_bytes()==before



def test_isolated_entry_dissociates_borrowed_objects(tmp_path):
    pool=tmp_path/'pool';_init_git_repo(pool)
    source=tmp_path/'source'
    subprocess.run(['git','clone','--shared','--quiet',str(pool),str(source)],check=True)
    result=_isolated_entry(source,tmp_path/'new')
    assert result.returncode==0,result.stdout+result.stderr
    assert not (tmp_path/'new/.git/objects/info/alternates').exists()


def test_isolated_entry_scan_obeys_total_deadline(tmp_path):
    import time,omc_guard as runtime
    (tmp_path/'file.py').write_text('value=1\n')
    with pytest.raises(subprocess.TimeoutExpired):
        runtime._isolated_paths(tmp_path,[],deadline=time.monotonic()-1)



def test_isolated_entry_pins_oid_while_source_changes(tmp_path,monkeypatch,capsys):
    import argparse,omc_guard as runtime
    source=tmp_path/'source';_init_git_repo(source)
    oid=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    original=runtime._isolated_git
    changed=False
    def concurrent_change(root,arguments,**kwargs):
        nonlocal changed
        value=original(root,arguments,**kwargs)
        if root==source and arguments[:2]==['rev-parse','--verify'] and not changed:
            changed=True
            (source/'app.py').write_text('value = 9\n')
            subprocess.run(['git','-C',str(source),'add','app.py'],check=True)
            subprocess.run(['git','-C',str(source),'commit','-qm','concurrent user change'],check=True)
        return value
    monkeypatch.setattr(runtime,'_isolated_git',concurrent_change)
    args=argparse.Namespace(source=source,destination=tmp_path/'new',request='fixture',
                            baseline=None,write_path=['app.py'],timeout_sec=20)
    assert runtime.start_isolated(args)==0
    value=json.loads(capsys.readouterr().out)
    assert value['baseline_oid']==oid
    assert (tmp_path/'new/app.py').read_text()=='value = 1\n'
    assert (source/'app.py').read_text()=='value = 9\n'


def test_isolated_entry_concurrent_destination_has_one_owner(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    destination=tmp_path/'new'
    argv=[sys.executable,str(GUARD),'start-isolated','--source',str(source),
          '--destination',str(destination),'--request','fixture']
    children=[subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    outputs=[child.communicate(timeout=30) for child in children]
    assert sorted(child.returncode for child in children)==[0,1],outputs
    assert len(list((destination/'.omc/state/sessions').iterdir()))==1


def test_isolated_entry_leaves_existing_execution_alive(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(15)'])
    try:
        folder=source/'.omc/state/runs';folder.mkdir(parents=True)
        (folder/'old.json').write_text(json.dumps({'status':'running','pid':child.pid}))
        result=_isolated_entry(source,tmp_path/'new')
        assert result.returncode==0,result.stdout+result.stderr
        assert child.poll() is None
    finally:
        child.terminate();child.wait(timeout=10)


def test_isolated_entry_fresh_evidence_and_incomplete_gate(tmp_path):
    import hashlib
    source=tmp_path/'source';_init_git_repo(source)
    destination=tmp_path/'new'
    entry=_isolated_entry(source,destination,'--write-path','app.py','--write-path','test_app.py')
    assert entry.returncode==0,entry.stdout+entry.stderr
    data=json.loads(entry.stdout)
    assert data['stages']['VERIFY']['status']=='blocked'
    script=GUARD.with_name('omc_pipeline_guard.py')
    def pipeline(*args):
        return subprocess.run([sys.executable,str(script),'--target',str(destination),*args],
                              capture_output=True,text=True,timeout=30)
    sid=data['session_id']
    proposal={'session_id':sid,'revision':1,'request':'fixture own implementation',
              'approval_reference':'explicit fixture approval','checks':[{'id':'tests','target':'app.py',
              'method':'python -m pytest test_app.py -q','pass_condition':'pass','target_version':data['baseline_oid']}]}
    approved=destination/'.omc/checks.json';approved.write_text(json.dumps(proposal))
    assert pipeline('approve-checks','--file',str(approved)).returncode==0
    assert pipeline('contract-done','--required-checks','tests').returncode==0
    test=destination/'test_app.py';test.write_text('from app import value\ndef test_value():\n    assert value == 2\n')
    argv=[sys.executable,'-m','pytest','test_app.py','-q']
    red=subprocess.run(argv,cwd=destination,capture_output=True,text=True,timeout=30)
    assert red.returncode==1 and 'assert 1 == 2' in red.stdout
    output=destination/'.omc/red.txt';output.write_text(red.stdout+red.stderr)
    state=json.loads((destination/'.omc/pipeline_session.json').read_text())
    evidence={'session_id':sid,'basis_sha256':state['basis_sha256'],'test_file':'test_app.py',
              'test_sha256':hashlib.sha256(test.read_bytes()).hexdigest(),'check_id':'tests',
              'case_id':'test_value','expected_failure':'value should be 2','command':' '.join(argv),
              'exit_code':red.returncode,'baseline_failed_cases':[],'failed_cases':['test_value'],
              'output_file':'.omc/red.txt','output_sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
    path=destination/'.omc/red.json';path.write_text(json.dumps(evidence))
    assert pipeline('red-done','test_app.py','--evidence-file',str(path)).returncode==0
    (destination/'app.py').write_text('value = 2\n')
    green=subprocess.run(argv,cwd=destination,capture_output=True,text=True,timeout=30)
    assert green.returncode==0,green.stdout+green.stderr
    subprocess.run(['git','-C',str(destination),'add','app.py','test_app.py'],check=True)
    template=pipeline('verification-template');assert template.returncode==0,template.stdout
    verification=json.loads(template.stdout)
    verification['implementation']={'status':'applied','evidence':'fixture app.py value=2'}
    verification['checks']['tests'].update(status='unrun',evidence='entry success is not verification success')
    target=destination/'.omc/verification.json';target.write_text(json.dumps(verification))
    incomplete=pipeline('complete-task','--verification-file',str(target))
    assert incomplete.returncode==1 and incomplete.stdout.startswith('구현됨·검증 미완료')
    verification['checks']['tests'].update(status='passed',evidence=green.stdout)
    target.write_text(json.dumps(verification))
    completed=pipeline('complete-task','--verification-file',str(target))
    assert completed.returncode==0,completed.stdout+completed.stderr
    assert completed.stdout.startswith('필수 검증 통과·작업 완료')
    assert not (source/'.omc').exists()



def test_isolated_entry_does_not_edit_reserved_metadata_via_link(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'alias.py').symlink_to('.git/config')
    subprocess.run(['git','-C',str(source),'add','alias.py'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','reserved link fixture'],check=True)
    result=_isolated_entry(source,tmp_path/'new','--write-path','alias.py')
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['stages']['EDIT']['status']=='blocked'



def test_isolated_entry_scans_nested_metadata_named_link(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'folder').mkdir();(source/'folder/.omc').symlink_to(source,target_is_directory=True)
    subprocess.run(['git','-C',str(source),'add','folder/.omc'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','nested link fixture'],check=True)
    result=_isolated_entry(source,tmp_path/'new','--write-path','folder')
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert 'folder/.omc' in value['blocked_edit_paths']
    assert value['stages']['EDIT']['status']=='blocked'



def test_isolated_entry_does_not_import_clone_python_shadow(tmp_path):
    import os
    source=tmp_path/'source';_init_git_repo(source)
    marker=tmp_path/'unexpected-import'
    (source/'subprocess.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("unsafe clone import")\nraise RuntimeError("shadow import")\n')
    subprocess.run(['git','-C',str(source),'add','subprocess.py'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','shadow fixture'],check=True)
    result=_isolated_entry(source,tmp_path/'new',env=dict(os.environ,PYTHONPATH='.'))
    assert not marker.exists(),result.stdout+result.stderr
    assert result.returncode==0,result.stdout+result.stderr


def test_isolated_entry_does_not_inherit_previous_resume_context(tmp_path):
    import os
    source=tmp_path/'source';_init_git_repo(source)
    env=dict(os.environ,OMC_EDIT_RESUME_CONTEXT='{"path":"unrelated-old-context","sha256":"invalid"}')
    result=_isolated_entry(source,tmp_path/'new',env=env)
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert value['registration']=='started'


def test_isolated_entry_supports_isolated_python_invocation(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    result=subprocess.run([sys.executable,'-I',str(GUARD),'start-isolated',
                           '--source',str(source),'--destination',str(tmp_path/'new'),
                           '--request','fixture'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['registration']=='started'



@pytest.mark.parametrize('name',['isolated-entry.json','project-memory.json','policy.json'])
def test_isolated_entry_rejects_runtime_metadata_alias_before_registration(tmp_path,name):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'.omc').mkdir();(source/'.omc'/name).symlink_to(source/'app.py')
    subprocess.run(['git','-C',str(source),'add','.omc/'+name],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','runtime alias fixture'],check=True)
    before=(source/'app.py').read_bytes()
    result=_isolated_entry(source,tmp_path/'new')
    assert (source/'app.py').read_bytes()==before,result.stdout+result.stderr
    assert result.returncode==1,result.stdout+result.stderr
    assert json.loads(result.stdout)['reason_code']=='destination_runtime_state_present'
    assert not (tmp_path/'new/.omc/state').exists()



@pytest.mark.parametrize('source_spelling,destination_spelling', [('Source','source'),('source','Source')])
def test_isolated_entry_case_alias_cannot_create_inside_source(tmp_path,source_spelling,destination_spelling):
    source=tmp_path/'Source';_init_git_repo(source)
    if not (tmp_path/'source').exists():
        pytest.skip('requires a case-insensitive filesystem')
    destination=tmp_path/destination_spelling/'inside'
    before=(source/'.git/index').read_bytes()
    result=_isolated_entry(tmp_path/source_spelling,destination)
    assert result.returncode==1,result.stdout+result.stderr
    assert json.loads(result.stdout)['reason_code']=='destination_overlap'
    assert not (source/'inside').exists()
    assert (source/'.git/index').read_bytes()==before


@pytest.mark.parametrize('spelling',['folder','./folder','folder//','./folder//','FOLDER','./FOLDER'])
def test_isolated_entry_equivalent_scope_spellings_cannot_skip_links(tmp_path,spelling):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'folder').mkdir();(source/'folder/link.py').symlink_to(source/'app.py')
    subprocess.run(['git','-C',str(source),'add','folder/link.py'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','scope spelling fixture'],check=True)
    aliases_existing_folder=(source/spelling).is_dir()
    result=_isolated_entry(source,tmp_path/'new','--write-path',spelling)
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert value['stages']['EDIT']['status']==('blocked' if aliases_existing_folder else 'allowed')
    assert value['write_paths']==[Path(spelling).as_posix()]
    assert (source/'app.py').read_text()=='value = 1\n'


@pytest.mark.parametrize('spelling',['folder/good.py','./folder//good.py','folder/new.py'])
def test_isolated_entry_normalized_ordinary_scope_stays_allowed(tmp_path,spelling):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'folder').mkdir();(source/'folder/good.py').write_text('value=1\n')
    (source/'folder/link.py').symlink_to(source/'app.py')
    subprocess.run(['git','-C',str(source),'add','folder'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','ordinary scope fixture'],check=True)
    result=_isolated_entry(source,tmp_path/'new','--write-path',spelling)
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert value['stages']['EDIT']['status']=='allowed'
    assert value['write_paths']==[Path(spelling).as_posix()]


def test_isolated_entry_rechecks_destination_before_reservation(tmp_path,monkeypatch,capsys):
    import argparse,omc_guard as runtime
    source=tmp_path/'Source';_init_git_repo(source)
    parent=tmp_path/'outside';parent.mkdir()
    original=runtime._isolated_git
    changed=False
    def replace_parent(root,arguments,**kwargs):
        nonlocal changed
        value=original(root,arguments,**kwargs)
        if root==source and arguments[:2]==['rev-parse','--verify'] and not changed:
            changed=True;parent.rmdir();parent.symlink_to(source,target_is_directory=True)
        return value
    monkeypatch.setattr(runtime,'_isolated_git',replace_parent)
    args=argparse.Namespace(source=source,destination=parent/'inside',request='fixture',
                            baseline=None,write_path=[],timeout_sec=20)
    assert runtime.start_isolated(args)==1
    value=json.loads(capsys.readouterr().out)
    assert value['reason_code']=='destination_symlink'
    assert not (source/'inside').exists()



@pytest.mark.parametrize('hint,reason',[('.git','source_worktree_required'),('folder','destination_overlap')])
def test_isolated_entry_binds_source_root_before_creating_destination(tmp_path,hint,reason):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'folder').mkdir();(source/'folder/file.txt').write_text('fixture')
    subprocess.run(['git','-C',str(source),'add','folder'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','subdirectory fixture'],check=True)
    destination=source/'inside'
    result=_isolated_entry(source/hint,destination)
    assert not destination.exists(),result.stdout+result.stderr
    assert result.returncode==1,result.stdout+result.stderr
    assert json.loads(result.stdout)['reason_code']==reason


def test_isolated_entry_subdirectory_source_starts_from_worktree_root(tmp_path):
    source=tmp_path/'source';_init_git_repo(source)
    (source/'folder').mkdir();(source/'folder/file.txt').write_text('fixture')
    subprocess.run(['git','-C',str(source),'add','folder'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-qm','subdirectory fixture'],check=True)
    result=_isolated_entry(source/'folder',tmp_path/'new','--write-path','app.py')
    assert result.returncode==0,result.stdout+result.stderr
    assert (tmp_path/'new/app.py').read_text()=='value = 1\n'
    assert Path(json.loads(result.stdout)['source_root']).samefile(source)


@pytest.mark.parametrize('inside',[False,True])
def test_isolated_entry_bare_source_keeps_safe_boundary(tmp_path,inside):
    source=tmp_path/'source';_init_git_repo(source)
    bare=tmp_path/'bare.git'
    subprocess.run(['git','clone','--bare','--quiet',str(source),str(bare)],check=True)
    destination=bare/'inside' if inside else tmp_path/'new'
    result=_isolated_entry(bare,destination)
    assert result.returncode==(1 if inside else 0),result.stdout+result.stderr
    if inside:
        assert not destination.exists()
        assert json.loads(result.stdout)['reason_code']=='destination_overlap'
    else:
        assert (destination/'app.py').read_text()=='value = 1\n'


@pytest.mark.parametrize('metadata',['--absolute-git-dir','--git-common-dir'])
def test_isolated_entry_linked_worktree_protects_shared_git_metadata(tmp_path,metadata):
    source=tmp_path/'source';_init_git_repo(source)
    linked=tmp_path/'linked'
    subprocess.run(['git','-C',str(source),'worktree','add','--detach','--quiet',str(linked),'HEAD'],check=True)
    raw=subprocess.check_output(['git','-C',str(linked),'rev-parse',metadata],text=True).strip()
    root=Path(raw) if Path(raw).is_absolute() else linked/raw
    destination=root/'inside'
    result=_isolated_entry(linked,destination)
    assert not destination.exists(),result.stdout+result.stderr
    assert result.returncode==1,result.stdout+result.stderr
    assert json.loads(result.stdout)['reason_code']=='destination_overlap'


def test_isolated_entry_rechecks_repository_binding_before_creation(tmp_path,monkeypatch,capsys):
    import argparse,omc_guard as runtime
    source=tmp_path/'source';_init_git_repo(source)
    alternate=tmp_path/'alternate'
    subprocess.run(['git','clone','--no-hardlinks','--quiet',str(source),str(alternate)],check=True)
    original=runtime._isolated_git
    switched=False
    def switch_git_directory(root,arguments,**kwargs):
        nonlocal switched
        value=original(root,arguments,**kwargs)
        if root==source and arguments[:2]==['rev-parse','--verify'] and not switched:
            switched=True
            (source/'.git').rename(tmp_path/'retired.git')
            (source/'.git').write_text('gitdir: '+str(alternate/'.git')+'\n')
        return value
    monkeypatch.setattr(runtime,'_isolated_git',switch_git_directory)
    destination=alternate/'.git/inside'
    args=argparse.Namespace(source=source,destination=destination,request='fixture',
                            baseline=None,write_path=[],timeout_sec=20)
    assert runtime.start_isolated(args)==1
    assert not destination.exists()
    assert json.loads(capsys.readouterr().out)['reason_code']=='source_directory_changed'
