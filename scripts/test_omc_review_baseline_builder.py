import subprocess
import stat
from pathlib import Path

from omc_review_baseline_builder import _redact, build_baseline_workspace


def test_redact_removes_provider_sensitive_values():
    assert "@" not in _redact("owner@example.com", {})
    assert "ghp_" not in _redact("ghp_secret", {})
    assert "/Users/" not in _redact("/Users/private/file", {})
    assert _redact('Bearer secret "owner@example.com"', {}) == 'Bearer <redacted-token> "<redacted-email>"'


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def test_build_baseline_workspace_applies_anonymized_patch(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "app.txt").write_text("private value\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    _git(source, "commit", "--allow-empty", "-qm", "change")
    diff = "diff --git a/app.txt b/app.txt\n--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-public value\n+changed value\n"
    output = tmp_path / "workspace"

    result = build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=diff,
        output=output,
        redactions={"private": "public"},
    )

    assert result["workspace"] == str(output)
    assert _git(output, "diff", "--", "app.txt")
    assert "changed value" in (output / "app.txt").read_text(encoding="utf-8")


def test_build_baseline_workspace_redacts_patch_with_the_same_rules(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "app.txt").write_text("private value\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    _git(source, "commit", "--allow-empty", "-qm", "change")
    diff = "diff --git a/app.txt b/app.txt\n--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-private value\n+ghp_secret\n"
    output = tmp_path / "workspace"

    build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=diff,
        output=output,
        redactions={"private": "public"},
    )

    content = (output / "app.txt").read_text(encoding="utf-8")
    assert "ghp_secret" not in content
    assert "<redacted-github-token>" in content


def test_build_baseline_workspace_aligns_anonymized_context_per_file(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "app.ts").write_text("import { api } from '@private/package';\nexport { api };\n", encoding="utf-8")
    (source / "other.ts").write_text("import { api } from '@private/package';\nexport { api };\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    (source / "app.ts").write_text("import { api, auth } from '@private/package';\nexport { api, auth };\n", encoding="utf-8")
    (source / "other.ts").write_text("import { api, auth } from '@private/package';\nexport { api, auth };\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "change")
    anonymized_diff = (
        _git(source, "show", "--format=", "--binary", "HEAD")
        .replace("diff --git a/app.ts b/app.ts", "diff --git a/app.ts b/app.ts")
        .replace("@private/package", "@public/package", 2)
        + "\n"
    )
    output = tmp_path / "workspace"

    build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=anonymized_diff,
        output=output,
    )

    assert "@public/package" in (output / "app.ts").read_text(encoding="utf-8")
    assert "@private/package" in (output / "other.ts").read_text(encoding="utf-8")


def test_build_baseline_workspace_applies_new_file_only_patch(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "existing.txt").write_text("base\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    _git(source, "commit", "--allow-empty", "-qm", "change")
    diff = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+created\n"
    )
    output = tmp_path / "workspace"

    result = build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=diff,
        output=output,
    )

    assert result["changed_paths"] == ["new.txt"]
    assert (output / "new.txt").read_text(encoding="utf-8") == "created\n"
    assert "diff --git a/new.txt b/new.txt" in _git(output, "diff", "--binary")


def test_build_baseline_workspace_applies_rename_only_patch(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "old-name.txt").write_text("content\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    _git(source, "mv", "old-name.txt", "new-name.txt")
    _git(source, "commit", "-qm", "rename")
    output = tmp_path / "workspace"

    result = build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=_git(source, "show", "--format=", "--binary", "HEAD"),
        output=output,
    )

    assert result["changed_paths"] == ["old-name.txt", "new-name.txt"]
    assert not (output / "old-name.txt").exists()
    assert (output / "new-name.txt").read_text(encoding="utf-8") == "content\n"


def test_build_baseline_workspace_applies_mixed_rename_and_content_patch(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "old-name.txt").write_text("content\n", encoding="utf-8")
    (source / "app.txt").write_text("before\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    _git(source, "mv", "old-name.txt", "new-name.txt")
    (source / "app.txt").write_text("after\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "rename and edit")
    output = tmp_path / "workspace"

    build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=_git(source, "show", "--format=", "--binary", "HEAD"),
        output=output,
    )

    assert not (output / "old-name.txt").exists()
    assert (output / "new-name.txt").read_text(encoding="utf-8") == "content\n"
    assert (output / "app.txt").read_text(encoding="utf-8") == "after\n"


def test_build_baseline_workspace_applies_rename_with_executable_mode(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "old-script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    _git(source, "mv", "old-script.sh", "new-script.sh")
    (source / "new-script.sh").chmod(0o755)
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "rename and chmod")
    output = tmp_path / "workspace"

    build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=_git(source, "show", "--format=", "--binary", "HEAD"),
        output=output,
    )

    assert (output / "new-script.sh").stat().st_mode & stat.S_IXUSR


def test_build_baseline_workspace_redacts_changed_paths_in_result(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    sensitive_path = source / "owner@example.com.txt"
    sensitive_path.write_text("before\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    sensitive_path.write_text("after\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "edit")

    result = build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=subprocess.check_output(
            ["git", "-C", str(source), "show", "--format=", "--binary", "HEAD"],
            text=True,
        ),
        output=tmp_path / "workspace",
    )

    assert "owner@example.com" not in result["changed_paths"]
    assert result["changed_paths"] == ["redacted-email"]


def test_build_baseline_workspace_applies_quoted_path_patch(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    spaced_path = source / "file with spaces.txt"
    spaced_path.write_text("before\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    spaced_path.write_text("after\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "edit")
    output = tmp_path / "workspace"

    build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=subprocess.check_output(
            ["git", "-C", str(source), "show", "--format=", "--binary", "HEAD"],
            text=True,
        ),
        output=output,
    )

    assert (output / "file with spaces.txt").read_text(encoding="utf-8") == "after\n"


def test_build_baseline_workspace_applies_git_quoted_tab_path_patch(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    tabbed_path = source / "file\twith-tab.txt"
    tabbed_path.write_text("before\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    tabbed_path.write_text("after\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "edit")
    output = tmp_path / "workspace"

    build_baseline_workspace(
        source_repo=source,
        source_commit="HEAD",
        diff=subprocess.check_output(
            ["git", "-C", str(source), "show", "--format=", "--binary", "HEAD"],
            text=True,
        ),
        output=output,
    )

    assert (output / "file\twith-tab.txt").read_text(encoding="utf-8") == "after\n"
