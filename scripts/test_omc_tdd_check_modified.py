"""
P0-2 수정 파일 TDD 체크 테스트 — RED 단계

테스트 목적:
  수정된 구현 파일(git status M)에도 TDD 체크를 적용해,
  대응 테스트가 없으면 경고를 출력하는 로직을 검증한다.

현재 상태(RED): 아래 테스트들은 모두 FAIL 해야 한다.
  - _get_modified_impl_files 함수가 아직 존재하지 않음
  - check()가 신규(A)와 수정(M)을 구분하지 않음
  - 수정 파일에 테스트 없을 때 경고 메시지 미출력
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omc_tdd_check as tdd


# ---------------------------------------------------------------------------
# 헬퍼: 가짜 git diff 출력 생성
# ---------------------------------------------------------------------------

def _make_git_output(added: list[str], modified: list[str]) -> str:
    """git diff --name-status 형식 모킹용 출력 생성."""
    lines = []
    for f in added:
        lines.append(f"A\t{f}")
    for f in modified:
        lines.append(f"M\t{f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. _get_modified_impl_files 함수 존재 확인
# ---------------------------------------------------------------------------

class TestGetModifiedImplFiles:
    def test_function_exists(self):
        """_get_modified_impl_files 함수가 모듈에 존재해야 함."""
        assert hasattr(tdd, "_get_modified_impl_files"), \
            "_get_modified_impl_files 함수가 omc_tdd_check에 없음"

    def test_returns_only_modified_files(self, tmp_path: Path):
        """M 상태 파일만 반환하고 A 상태 파일은 포함하지 않음."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Button.tsx").write_text("export const Button = () => null;")
        (tmp_path / "src" / "NewComp.tsx").write_text("export const NewComp = () => null;")

        git_output = "A\tsrc/NewComp.tsx\nM\tsrc/Button.tsx"

        with patch.object(tdd, "_git", return_value=git_output):
            result = tdd._get_modified_impl_files(tmp_path, "origin/main", staged_only=False)

        paths = [str(p) for p in result]
        assert "src/Button.tsx" in paths, "수정 파일(M)이 포함돼야 함"
        assert "src/NewComp.tsx" not in paths, "신규 파일(A)은 제외돼야 함"

    def test_excludes_test_files(self, tmp_path: Path):
        """수정된 테스트 파일은 제외."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Button.spec.tsx").write_text("it('todo', () => {})")

        git_output = "M\tsrc/Button.spec.tsx"

        with patch.object(tdd, "_git", return_value=git_output):
            result = tdd._get_modified_impl_files(tmp_path, "origin/main", staged_only=False)

        assert len(result) == 0, "수정된 테스트 파일은 결과에서 제외돼야 함"

    def test_excludes_bypass_patterns(self, tmp_path: Path):
        """예외 패턴(types.ts, constants/ 등) 파일은 제외."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "types.ts").write_text("export type Foo = string;")

        git_output = "M\tsrc/types.ts"

        with patch.object(tdd, "_git", return_value=git_output):
            result = tdd._get_modified_impl_files(tmp_path, "origin/main", staged_only=False)

        assert len(result) == 0, "types.ts는 예외 패턴으로 제외돼야 함"


# ---------------------------------------------------------------------------
# 2. check() 함수 — 수정 파일 경고 출력
# ---------------------------------------------------------------------------

class TestCheckModifiedFiles:
    def test_modified_file_without_test_produces_warning(self, tmp_path: Path, capsys):
        """수정 파일에 테스트가 없으면 경고 메시지 출력."""
        (tmp_path / "src").mkdir()
        impl_file = tmp_path / "src" / "Button.tsx"
        impl_file.write_text("export const Button = () => null;")

        # 신규 파일 없음, 수정 파일만 있음
        def fake_git(args, cwd):
            if "--diff-filter=A" in args or ("--diff-filter=ACM" in args and "--cached" not in args):
                return ""  # 신규 없음
            if "--diff-filter=M" in args:
                return "src/Button.tsx"
            return "src/Button.tsx"

        with patch.object(tdd, "_git", side_effect=fake_git):
            tdd.check(root=tmp_path, staged_only=False, report_only=False)

        captured = capsys.readouterr()
        assert "Button.tsx" in captured.out, "경고에 수정된 파일명이 포함돼야 함"
        assert "테스트" in captured.out or "test" in captured.out.lower(), \
            "테스트 관련 경고 메시지가 출력돼야 함"

    def test_modified_file_with_test_passes_silently(self, tmp_path: Path, capsys):
        """수정 파일에 테스트가 있으면 경고 없이 통과."""
        (tmp_path / "src").mkdir()
        impl_file = tmp_path / "src" / "Button.tsx"
        impl_file.write_text("export const Button = () => null;")
        test_file = tmp_path / "src" / "Button.spec.tsx"
        test_file.write_text("it('todo', () => {})")

        def fake_git(args, cwd):
            if "--diff-filter=M" in args:
                return "src/Button.tsx"
            return ""

        with patch.object(tdd, "_git", side_effect=fake_git):
            result = tdd.check(root=tmp_path, staged_only=False, report_only=False)

        captured = capsys.readouterr()
        assert result == 0, "테스트 있는 수정 파일은 통과해야 함"

    def test_modified_file_warning_does_not_block(self, tmp_path: Path):
        """수정 파일에 테스트 없어도 exit code는 0 (경고만, 차단 아님)."""
        (tmp_path / "src").mkdir()
        impl_file = tmp_path / "src" / "Button.tsx"
        impl_file.write_text("export const Button = () => null;")

        def fake_git(args, cwd):
            if "--diff-filter=M" in args:
                return "src/Button.tsx"
            return ""

        with patch.object(tdd, "_git", side_effect=fake_git):
            result = tdd.check(root=tmp_path, staged_only=False, report_only=False)

        assert result == 0, "수정 파일 테스트 없음은 경고(exit 0)이어야 함, 차단(exit 1) 아님"


# ---------------------------------------------------------------------------
# 3. 신규 파일(A) 차단은 기존대로 유지
# ---------------------------------------------------------------------------

class TestAddedFilesStillBlocked:
    def test_added_file_without_test_still_blocked(self, tmp_path: Path):
        """신규 파일(A)에 테스트 없으면 여전히 차단(exit 1)."""
        (tmp_path / "src").mkdir()
        impl_file = tmp_path / "src" / "NewComp.tsx"
        impl_file.write_text("export const NewComp = () => null;")

        def fake_git(args, cwd):
            # staged or non-staged 모두 신규 파일 반환
            return "src/NewComp.tsx"

        with patch.object(tdd, "_git", side_effect=fake_git):
            result = tdd.check(root=tmp_path, staged_only=False, report_only=False)

        assert result == 1, "신규 파일(A) 테스트 없음은 여전히 차단(exit 1)이어야 함"

    def test_added_file_with_test_still_passes(self, tmp_path: Path):
        """신규 파일(A)에 테스트 있으면 여전히 통과."""
        (tmp_path / "src").mkdir()
        impl_file = tmp_path / "src" / "NewComp.tsx"
        impl_file.write_text("export const NewComp = () => null;")
        test_file = tmp_path / "src" / "NewComp.spec.tsx"
        test_file.write_text("it('todo', () => {})")

        with patch.object(tdd, "_git", return_value="src/NewComp.tsx"):
            result = tdd.check(root=tmp_path, staged_only=False, report_only=False)

        assert result == 0, "테스트 있는 신규 파일은 통과해야 함"


# ---------------------------------------------------------------------------
# 4. staged_only 모드에서도 수정 파일 체크 동작
# ---------------------------------------------------------------------------

class TestStagedModeModified:
    def test_staged_modified_file_checked(self, tmp_path: Path, capsys):
        """--staged 모드에서도 수정 파일 경고가 출력됨."""
        (tmp_path / "src").mkdir()
        impl_file = tmp_path / "src" / "Button.tsx"
        impl_file.write_text("export const Button = () => null;")

        def fake_git(args, cwd):
            if "--cached" in args and "--diff-filter=M" in args:
                return "src/Button.tsx"
            return ""

        with patch.object(tdd, "_git", side_effect=fake_git):
            tdd.check(root=tmp_path, staged_only=True, report_only=False)

        captured = capsys.readouterr()
        assert "Button.tsx" in captured.out, "staged 모드에서도 수정 파일 경고가 출력돼야 함"
