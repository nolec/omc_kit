"""
P2-1 교훈 콘솔 출력 개선 테스트 — RED 단계

테스트 목적:
  세션 시작(omc_context.py 실행) 시 BM25 관련 교훈이
  터미널(stdout)에 직접 출력되는지 검증한다.

현재 상태(RED):
  - 교훈은 context.md 파일에만 저장됨
  - 터미널에는 교훈 내용이 출력되지 않음
  - 아래 테스트들은 모두 FAIL 해야 한다
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run_context(tmp_path: Path) -> str:
    """omc_context.py를 실행하고 stdout 반환."""
    (tmp_path / ".omc").mkdir()
    (tmp_path / ".omc" / "lessons").mkdir()

    # 교훈 파일 1개 생성
    lesson = tmp_path / ".omc" / "lessons" / "2026-01-01-test-lesson.md"
    lesson.write_text(
        "# 테스트 교훈\n"
        "## 증상\nfoo 버그\n"
        "## 적용된 규칙\nbar 규칙을 적용한다\n"
        "태그: foo, bar, test\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "omc_context.py"), "--target", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    return result.stdout


class TestLessonConsoleOutput:
    def test_lesson_header_printed_to_stdout(self, tmp_path: Path):
        """교훈 섹션 헤더가 stdout에 출력돼야 한다."""
        out = _run_context(tmp_path)
        assert "교훈" in out, (
            "세션 시작 시 교훈 관련 헤더가 stdout에 출력돼야 함 (현재 출력 안 됨 — RED)"
        )

    def test_lesson_title_printed_to_stdout(self, tmp_path: Path):
        """교훈 제목이 stdout에 출력돼야 한다."""
        out = _run_context(tmp_path)
        assert "테스트 교훈" in out, (
            "교훈 제목이 stdout에 출력돼야 함 (현재 출력 안 됨 — RED)"
        )

    def test_lesson_rule_printed_to_stdout(self, tmp_path: Path):
        """교훈 규칙 요약이 stdout에 출력돼야 한다."""
        out = _run_context(tmp_path)
        assert "bar 규칙" in out, (
            "교훈 규칙 요약이 stdout에 출력돼야 함 (현재 출력 안 됨 — RED)"
        )

    def test_no_lesson_no_output(self, tmp_path: Path):
        """교훈 파일이 없으면 교훈 섹션이 출력되지 않아야 한다."""
        (tmp_path / ".omc").mkdir()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "omc_context.py"), "--target", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert "교훈" not in result.stdout, "교훈 없을 때는 교훈 섹션 출력하지 않아야 함"

    def test_lesson_count_in_output(self, tmp_path: Path):
        """출력에 '교훈 N개' 형태의 개수 정보가 포함돼야 한다."""
        out = _run_context(tmp_path)
        assert "교훈" in out, "교훈 섹션이 출력돼야 함"
        # "관련 교훈 1개" 또는 "최근 교훈 1개" 형태 확인
        import re
        assert re.search(r"교훈\s+\d+개", out), (
            "교훈 개수 (예: '교훈 1개')가 출력에 포함돼야 함"
        )


@pytest.mark.parametrize("query, expected", [
    ("invoices", "2026-01-01-invoices.md"),
    ("unmatched", "2026-01-02-layout.md"),
])
def test_recommendations_identify_exact_lesson_in_all_outputs(tmp_path, monkeypatch, capsys, query, expected):
    import omc_context

    folder = tmp_path / ".omc" / "lessons"
    folder.mkdir(parents=True)
    for name, rule in [
        ("2026-01-01-invoices.md", "invoices rule"),
        ("2026-01-02-layout.md", "layout rule"),
        ("2026-01-03-retired.md", "invoices retired rule"),
    ]:
        (folder / name).write_text(f"# Same title\n## 적용된 규칙\n{rule}\n", encoding="utf-8")
    (folder / "2026-01-03-retired.md.retracted.json").write_text("{}")
    (folder / "2026-01-04-broken.md").write_bytes(b"\xff")
    git_info = {"branch": query}
    monkeypatch.setattr(omc_context, "_collect_git_info", lambda root: git_info)
    summary = omc_context._collect_lessons(tmp_path, git_info, n=1)
    omc_context._print_lessons_to_console(tmp_path, git_info, n=1)
    console = capsys.readouterr().out
    omc_context.write_lessons_inject(tmp_path, top_n=1)
    cursor = (tmp_path / ".cursor/rules/omc-lessons-inject.mdc").read_text()
    for output in (summary, console, cursor):
        assert f"파일: {expected}" in output
        assert "retired.md" not in output
        assert "broken.md" not in output
    shown = subprocess.check_output([
        sys.executable, "-B", str(ROOT / "scripts/omc_lesson.py"),
        "--target", str(tmp_path), "show", expected
    ], text=True)
    assert ("invoices rule" if query == "invoices" else "layout rule") in shown
