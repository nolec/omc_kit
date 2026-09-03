#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from omc_git_hooks import resolve_completion_hook

ROOT = Path(__file__).parent.parent


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


class TestCompletionHookResolver(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        return repo

    def test_resolves_default_git_hooks_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))

            result = resolve_completion_hook(repo)

            self.assertEqual(result.backend, "native")
            self.assertEqual(
                result.install_hook_path,
                (repo / ".git" / "hooks" / "post-commit").resolve(),
            )
            self.assertTrue(result.auto_install_allowed)

    def test_resolves_husky_public_hook_without_touching_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            dispatcher = repo / ".husky" / "_" / "post-commit"
            dispatcher.parent.mkdir(parents=True)
            dispatcher.write_text("#!/bin/sh\n# husky dispatcher\n", encoding="utf-8")
            _git(repo, "config", "core.hooksPath", ".husky/_")

            result = resolve_completion_hook(repo)

            self.assertEqual(result.backend, "husky")
            self.assertEqual(
                result.install_hook_path,
                (repo / ".husky" / "post-commit").resolve(),
            )
            self.assertNotEqual(result.install_hook_path, dispatcher)
            self.assertTrue(result.auto_install_allowed)

    def test_resolves_repository_internal_custom_hooks_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            _git(repo, "config", "core.hooksPath", ".githooks")

            result = resolve_completion_hook(repo)

            self.assertEqual(result.backend, "internal_custom")
            self.assertEqual(
                result.install_hook_path,
                (repo / ".githooks" / "post-commit").resolve(),
            )
            self.assertTrue(result.auto_install_allowed)

    def test_blocks_external_shared_hooks_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            shared = root / "shared-hooks"
            _git(repo, "config", "core.hooksPath", str(shared))

            result = resolve_completion_hook(repo)

            self.assertEqual(result.backend, "external_shared")
            self.assertIsNone(result.install_hook_path)
            self.assertFalse(result.auto_install_allowed)
            self.assertEqual(result.reason, "manual_integration_required")


class TestCompletionHookExecution(unittest.TestCase):
    def test_commit_only_session_preserves_implementation_completion_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _git(repo, "init", "-q")
            _git(repo, "config", "user.email", "omc-hooks@example.com")
            _git(repo, "config", "user.name", "OMC Hooks")
            (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
            _git(repo, "add", "app.py")
            _git(repo, "commit", "-qm", "baseline")

            install = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "install.py"), "--target", str(repo), "--force"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            env = {**os.environ, "XDG_CONFIG_HOME": str(root / "config")}
            subprocess.run(
                [sys.executable, "scripts/omc_work_class_lock.py", "init", "--target", "."],
                cwd=repo,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            implementation = subprocess.run(
                [
                    sys.executable, "scripts/omc_guard.py", "sync-require",
                    "--target", ".", "--mode", "autopilot", "--title", "omc-task",
                    "--request", "implement behavior", "--roles", "senior_coding",
                    "--work-class", "implementation", "--completion-action", "start",
                    "--for", "task",
                ],
                cwd=repo, env=env, check=False, capture_output=True, text=True,
            )
            self.assertEqual(implementation.returncode, 0, implementation.stdout + implementation.stderr)
            pending_before = json.loads((repo / ".omc/state/pending-completion.json").read_text())

            commit_only = subprocess.run(
                [
                    sys.executable, "scripts/omc_guard.py", "sync-require",
                    "--target", ".", "--mode", "autopilot", "--title", "omc-task",
                    "--request", "document and commit approved existing work", "--roles", "senior_coding",
                    "--work-class", "document_only", "--completion-action", "preserve",
                    "--for", "task",
                ],
                cwd=repo, env=env, check=False, capture_output=True, text=True,
            )
            self.assertEqual(commit_only.returncode, 0, commit_only.stdout + commit_only.stderr)
            pending_after = json.loads((repo / ".omc/state/pending-completion.json").read_text())
            self.assertEqual(pending_after, pending_before)

            (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
            _git(repo, "add", "app.py")
            _git(repo, "commit", "--no-verify", "-qm", "implement behavior")

            receipt_path = (
                repo / ".omc/state/sessions" / pending_before["session_id"] / "completion.json"
            )
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["work_class"], "implementation")
            lineage = json.loads((receipt_path.parent / "completion-lineage.json").read_text())
            self.assertEqual(lineage["work_id"], pending_before["work_id"])
            self.assertEqual(lineage["evidence_status"], "informational_unverified")

    def test_native_and_husky_commits_create_bound_completion_receipts(self):
        for backend in ("native", "husky"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "repo"
                repo.mkdir()
                _git(repo, "init", "-q")
                _git(repo, "config", "user.email", "omc-hooks@example.com")
                _git(repo, "config", "user.name", "OMC Hooks")
                (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
                _git(repo, "add", "app.py")
                _git(repo, "commit", "-qm", "baseline")
                baseline = _git(repo, "rev-parse", "HEAD").stdout.strip()

                if backend == "husky":
                    _git(repo, "config", "core.hooksPath", ".husky/_")
                    dispatcher = repo / ".husky" / "_" / "post-commit"
                    dispatcher.parent.mkdir(parents=True)
                    dispatcher.write_text(
                        '#!/bin/sh\nexec sh "$(dirname "$0")/../post-commit" "$@"\n',
                        encoding="utf-8",
                    )
                    dispatcher.chmod(0o755)

                install = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "install.py"),
                        "--target",
                        str(repo),
                        "--force",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(install.returncode, 0, install.stdout + install.stderr)

                env = os.environ.copy()
                for name in (
                    "OMC_REQUIRE_WORK_CLASS_LOCK",
                    "OMC_WORK_CLASS_LOCK_PRIVATE_KEY_FILE",
                    "OMC_TRUSTED_WORK_CLASS_LOCK_PUBLIC_KEY",
                ):
                    env.pop(name, None)
                env["XDG_CONFIG_HOME"] = str(root / "config")
                init_lock = subprocess.run(
                    [sys.executable, "scripts/omc_work_class_lock.py", "init", "--target", "."],
                    cwd=repo,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(init_lock.returncode, 0, init_lock.stdout + init_lock.stderr)
                guard = subprocess.run(
                    [
                        sys.executable,
                        "scripts/omc_guard.py",
                        "sync-require",
                        "--target",
                        ".",
                        "--mode",
                        "autopilot",
                        "--title",
                        "hook-e2e",
                        "--request",
                        f"{backend} completion receipt",
                        "--roles",
                        "senior_coding",
                        "--work-class",
                        "implementation",
                        "--for",
                        "task",
                    ],
                    cwd=repo,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(guard.returncode, 0, guard.stdout + guard.stderr)
                latest = json.loads((repo / ".omc" / "state" / "latest.json").read_text())
                session_id = latest["latest_session_id"]

                (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
                _git(repo, "add", "app.py")
                commit = subprocess.run(
                    ["git", "commit", "--no-verify", "-qm", "implement change"],
                    cwd=repo,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(commit.returncode, 0, commit.stdout + commit.stderr)
                followup = _git(repo, "rev-parse", "HEAD").stdout.strip()

                session_dir = repo / ".omc" / "state" / "sessions" / session_id
                receipt = json.loads((session_dir / "completion.json").read_text())
                lock = json.loads((session_dir / "work_class_lock.json").read_text())
                self.assertEqual(receipt["schema_version"], 2)
                self.assertEqual(receipt["work_class"], "implementation")
                self.assertEqual(receipt["baseline_commit"], baseline)
                self.assertEqual(receipt["followup_commit"], followup)
                self.assertEqual(receipt["request_sha256"], lock["request_sha256"])
                self.assertFalse((repo / ".omc" / "state" / "pending-completion.json").exists())


if __name__ == "__main__":
    unittest.main()
