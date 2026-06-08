#!/usr/bin/env python3
"""omc_context._collect_codebase_index() 단위 테스트 — stdlib only"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import omc_context as _ctx


def _make_project(tmp: str, files: dict[str, str]) -> Path:
    """파일 dict {'경로': '내용'} 으로 임시 프로젝트 트리를 생성한다."""
    root = Path(tmp) / "proj"
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _read_index(root: Path) -> str:
    return (root / ".omc" / "context" / "file_index.txt").read_text(encoding="utf-8")


class TestCollectCodebaseIndex(unittest.TestCase):
    def test_output_file_created(self):
        """수집 후 .omc/context/file_index.txt 가 생성된다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_project(tmp, {"src/foo.ts": "export const x = 1;"})
            _ctx._collect_codebase_index(root)
            index_file = root / ".omc" / "context" / "file_index.txt"
            self.assertTrue(index_file.exists(), "file_index.txt 미생성")

    def test_fallback_includes_src_files(self):
        """git 없는 환경에서 src/ 파일이 fallback으로 file_index.txt 에 수집된다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_project(tmp, {
                "src/a.ts": "export const a = 1;",
                "src/b.ts": "export const b = 2;",
            })
            _ctx._collect_codebase_index(root)
            content = _read_index(root)
            self.assertIn("src/a.ts", content)
            self.assertIn("src/b.ts", content)

    def test_excludes_node_modules(self):
        """node_modules 내 파일은 file_index.txt 에 포함되지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_project(tmp, {
                "src/a.ts": "export const a = 1;",
                "node_modules/lib/index.js": "module.exports = {};",
            })
            _ctx._collect_codebase_index(root)
            content = _read_index(root)
            self.assertNotIn("node_modules", content)

    def test_over_limit_truncated(self):
        """300개 상한 초과 시 file_index.txt 에 생략 메시지가 포함된다."""
        with tempfile.TemporaryDirectory() as tmp:
            files = {f"src/f{i}.ts": f"export const v{i} = {i};" for i in range(350)}
            root = _make_project(tmp, files)
            _ctx._collect_codebase_index(root)
            content = _read_index(root)
            self.assertIn("이하 생략", content)

    def test_large_file_skipped(self):
        """500KB 초과 파일은 file_index.txt 에서 제외된다."""
        with tempfile.TemporaryDirectory() as tmp:
            big_content = "x" * (501 * 1024)
            root = _make_project(tmp, {
                "src/small.ts": "export const x = 1;",
                "src/big.ts": big_content,
            })
            _ctx._collect_codebase_index(root)
            content = _read_index(root)
            self.assertIn("src/small.ts", content)
            self.assertNotIn("src/big.ts", content)

    def test_empty_project_returns_empty(self):
        """소스 파일이 없으면 빈 문자열을 반환한다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "empty"
            root.mkdir()
            result = _ctx._collect_codebase_index(root)
            self.assertEqual(result, "")


class TestBuildContextIncludesIndex(unittest.TestCase):
    def test_build_context_has_codebase_summary(self):
        """build_context() 반환값에 '[코드베이스]' 섹션이 포함된다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_project(tmp, {"src/foo.ts": "export const x = 1;"})
            ctx_text, _ = _ctx.build_context(root)
            self.assertIn("[코드베이스]", ctx_text)



class TestWriteLessonsInject(unittest.TestCase):
    """write_lessons_inject() 단위 테스트"""

    def test_creates_mdc_when_lessons_exist(self):
        """교훈이 1개 이상이면 .cursor/rules/omc-lessons-inject.mdc를 생성한다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            lessons_dir = root / ".omc" / "lessons"
            lessons_dir.mkdir(parents=True)
            (lessons_dir / "2026-01-01-test-lesson.md").write_text(
                "# 테스트 교훈\n내용입니다.", encoding="utf-8"
            )
            _ctx.write_lessons_inject(root, top_n=3)
            mdc = root / ".cursor" / "rules" / "omc-lessons-inject.mdc"
            self.assertTrue(mdc.exists(), ".mdc 파일이 생성되어야 한다")
            text = mdc.read_text(encoding="utf-8")
            self.assertIn("테스트 교훈", text)

    def test_skips_when_no_lessons(self):
        """교훈이 0개이면 .mdc 파일을 생성하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            (root / ".omc" / "lessons").mkdir(parents=True)
            _ctx.write_lessons_inject(root, top_n=3)
            mdc = root / ".cursor" / "rules" / "omc-lessons-inject.mdc"
            self.assertFalse(mdc.exists(), "교훈 없으면 파일 생성 금지")

    def test_limits_to_top_n(self):
        """top_n 개수를 초과하는 교훈은 포함하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            lessons_dir = root / ".omc" / "lessons"
            lessons_dir.mkdir(parents=True)
            for i in range(5):
                (lessons_dir / f"2026-01-0{i+1}-lesson-{i}.md").write_text(
                    f"# 교훈{i}\n내용{i}", encoding="utf-8"
                )
            _ctx.write_lessons_inject(root, top_n=2)
            mdc = root / ".cursor" / "rules" / "omc-lessons-inject.mdc"
            text = mdc.read_text(encoding="utf-8")
            # top_n=2이므로 최대 2개 교훈 제목만 포함
            lesson_headers = [l for l in text.split("\n") if l.startswith("## ")]
            self.assertLessEqual(len(lesson_headers), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
