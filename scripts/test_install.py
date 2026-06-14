#!/usr/bin/env python3
"""install.py 핵심 함수 테스트 — stdlib only (pytest 불필요)"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import install as _install


class TestCopy(unittest.TestCase):
    def test_copy_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.txt"
            dst = Path(tmp) / "sub" / "dst.txt"
            src.write_text("hello", encoding="utf-8")
            _install._copy(src, dst, force=False)
            self.assertEqual(dst.read_text(encoding="utf-8"), "hello")

    def test_copy_skips_existing_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.txt"
            dst = Path(tmp) / "dst.txt"
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            _install._copy(src, dst, force=False)
            self.assertEqual(dst.read_text(encoding="utf-8"), "old")

    def test_copy_overwrites_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.txt"
            dst = Path(tmp) / "dst.txt"
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            _install._copy(src, dst, force=True)
            self.assertEqual(dst.read_text(encoding="utf-8"), "new")

    def test_copy_raises_if_src_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "missing.txt"
            dst = Path(tmp) / "dst.txt"
            with self.assertRaises(FileNotFoundError):
                _install._copy(src, dst, force=False)


class TestWrite(unittest.TestCase):
    def test_write_creates_file_with_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "out.txt"
            _install._write(dst, "content", force=True)
            self.assertEqual(dst.read_text(encoding="utf-8"), "content\n")

    def test_write_skips_existing_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "out.txt"
            dst.write_text("old\n", encoding="utf-8")
            _install._write(dst, "new", force=False)
            self.assertEqual(dst.read_text(encoding="utf-8"), "old\n")


class TestCheckForceRegression(unittest.TestCase):
    def _make_kit(self, tmp: str, rules: dict[str, str], scripts: dict[str, str]) -> Path:
        kit = Path(tmp) / "kit"
        tmpl_rules = kit / "templates" / ".cursor" / "rules"
        tmpl_rules.mkdir(parents=True)
        for name, content in rules.items():
            (tmpl_rules / name).write_text(content, encoding="utf-8")
        kit_scripts = kit / "scripts"
        kit_scripts.mkdir(parents=True)
        for name, content in scripts.items():
            (kit_scripts / name).write_text(content, encoding="utf-8")
        return kit

    def _make_tgt(self, tmp: str, rules: dict[str, str], scripts: dict[str, str]) -> Path:
        tgt = Path(tmp) / "tgt"
        live_rules = tgt / ".cursor" / "rules"
        live_rules.mkdir(parents=True)
        for name, content in rules.items():
            (live_rules / name).write_text(content, encoding="utf-8")
        tgt_scripts = tgt / "scripts"
        tgt_scripts.mkdir(parents=True)
        for name, content in scripts.items():
            (tgt_scripts / name).write_text(content, encoding="utf-8")
        return tgt

    def test_no_diff_returns_true_silently(self):
        """템플릿 ↔ live 동일하면 True, 경고 없음."""
        with tempfile.TemporaryDirectory() as tmp:
            same = "line1\nline2\n"
            kit = self._make_kit(tmp, {"omc-always.md": same}, {})
            tgt = self._make_tgt(tmp, {"omc-always.md": same}, {})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)
            self.assertNotIn("WARN", captured.getvalue())

    def test_ssot_diff_prints_warning(self):
        """live가 templates보다 많으면 SSOT 불일치 경고 출력."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpl_content = "line1\nline2\n"
            live_content = "line1\nline2\nextra line\nextra2\n"
            kit = self._make_kit(tmp, {"omc-always.md": tmpl_content}, {})
            tgt = self._make_tgt(tmp, {"omc-always.md": live_content}, {})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)
            self.assertIn("SSOT 불일치", captured.getvalue())
            self.assertIn("omc-always.md", captured.getvalue())

    def test_ssot_diff_shows_positive_line_count(self):
        """경고 메시지에 live 줄 수 차이(+N)가 포함된다."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpl_content = "a\nb\n"
            live_content = "a\nb\nc\nd\ne\n"
            kit = self._make_kit(tmp, {"omc-always.md": tmpl_content}, {})
            tgt = self._make_tgt(tmp, {"omc-always.md": live_content}, {})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                _install._check_force_regression(kit, tgt)
            self.assertIn("+3", captured.getvalue())

    def test_script_regression_prints_warning(self):
        """scripts/ 회귀 감지 시 경고 출력."""
        base = "x\n" * 5
        live_extra = base + "y\n" * 15
        with tempfile.TemporaryDirectory() as tmp:
            kit = self._make_kit(tmp, {}, {"omc_tdd_check.py": base})
            tgt = self._make_tgt(tmp, {}, {"omc_tdd_check.py": live_extra})
            captured = io.StringIO()
            with patch("sys.stdin", io.StringIO()), patch("sys.stdout", captured):
                result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)
            self.assertIn("WARN", captured.getvalue())

    def test_no_scripts_dir_returns_true(self):
        """scripts/ 디렉토리 없어도 True 반환 (설치 첫 실행 시나리오)."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            (kit / "templates" / ".cursor" / "rules").mkdir(parents=True)
            (kit / "scripts").mkdir(parents=True)
            tgt = Path(tmp) / "tgt"
            tgt.mkdir()
            result = _install._check_force_regression(kit, tgt)
            self.assertTrue(result)


class TestSharedLessons(unittest.TestCase):
    def test_shared_lessons_copied_to_target(self):
        """install 실행 시 templates/shared_lessons/ 가 .omc/lessons/ 에 복사된다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_lessons"
            shared.mkdir(parents=True)
            (shared / "lesson-a.md").write_text("# lesson a", encoding="utf-8")
            (shared / "lesson-b.md").write_text("# lesson b", encoding="utf-8")

            tgt = Path(tmp) / "tgt"
            tgt.mkdir()

            _install._install_shared_lessons(kit, tgt)

            lessons_dir = tgt / ".omc" / "lessons"
            self.assertTrue((lessons_dir / "lesson-a.md").exists())
            self.assertTrue((lessons_dir / "lesson-b.md").exists())

    def test_shared_lessons_skips_existing(self):
        """이미 존재하는 교훈 파일은 덮어쓰지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shared = kit / "templates" / "shared_lessons"
            shared.mkdir(parents=True)
            (shared / "lesson-a.md").write_text("template version", encoding="utf-8")

            tgt = Path(tmp) / "tgt"
            lessons_dir = tgt / ".omc" / "lessons"
            lessons_dir.mkdir(parents=True)
            (lessons_dir / "lesson-a.md").write_text("custom version", encoding="utf-8")

            _install._install_shared_lessons(kit, tgt)

            self.assertEqual(
                (lessons_dir / "lesson-a.md").read_text(encoding="utf-8"),
                "custom version",
            )

    def test_shared_lessons_no_dir_is_noop(self):
        """templates/shared_lessons/ 가 없으면 조용히 통과한다."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            (kit / "templates").mkdir(parents=True)
            tgt = Path(tmp) / "tgt"
            tgt.mkdir()
            _install._install_shared_lessons(kit, tgt)
            self.assertFalse((tgt / ".omc" / "lessons").exists())


class TestInstallGeminiSettings(unittest.TestCase):
    def _settings_path(self, tmp: str) -> Path:
        p = Path(tmp) / ".gemini" / "settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def test_creates_with_beforetool_when_no_existing(self):
        """기존 파일 없을 때 BeforeTool 포함 settings.json 생성."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._settings_path(tmp)
            _install._install_gemini_settings(path, force=False)
            import json
            data = json.loads(path.read_text())
            self.assertIn("BeforeTool", data["hooks"])
            matcher = data["hooks"]["BeforeTool"][0]["matcher"]
            self.assertEqual(matcher, "write_file|replace")

    def test_adds_beforetool_to_existing_without_it(self):
        """기존 파일에 BeforeTool 없을 때 추가 (non-force)."""
        with tempfile.TemporaryDirectory() as tmp:
            import json
            path = self._settings_path(tmp)
            path.write_text(json.dumps({"hooks": {"SessionStart": []}}), encoding="utf-8")
            _install._install_gemini_settings(path, force=False)
            data = json.loads(path.read_text())
            self.assertIn("BeforeTool", data["hooks"])
            self.assertIn("SessionStart", data["hooks"])  # 기존 키 보존

    def test_skips_beforetool_if_already_present(self):
        """기존 파일에 BeforeTool 있으면 덮어쓰지 않는다 (non-force)."""
        with tempfile.TemporaryDirectory() as tmp:
            import json
            path = self._settings_path(tmp)
            custom = {"hooks": {"BeforeTool": [{"matcher": "custom_matcher"}]}}
            path.write_text(json.dumps(custom), encoding="utf-8")
            _install._install_gemini_settings(path, force=False)
            data = json.loads(path.read_text())
            self.assertEqual(data["hooks"]["BeforeTool"][0]["matcher"], "custom_matcher")

    def test_force_overwrites_with_beforetool(self):
        """force=True 시 전체 덮어쓰기 — BeforeTool 포함."""
        with tempfile.TemporaryDirectory() as tmp:
            import json
            path = self._settings_path(tmp)
            path.write_text(json.dumps({"hooks": {"BeforeTool": [{"matcher": "old"}]}}),
                            encoding="utf-8")
            _install._install_gemini_settings(path, force=True)
            data = json.loads(path.read_text())
            self.assertEqual(data["hooks"]["BeforeTool"][0]["matcher"], "write_file|replace")


class TestHookContractMarkers(unittest.TestCase):
    def test_install_source_declares_shared_hook_contract_markers(self):
        text = (Path(__file__).parent / "install.py").read_text(encoding="utf-8")
        for marker in (
            "session bootstrap",
            "pre-mutate guard",
            "post-mutate soft guard",
            "install/doctor verification",
        ):
            self.assertIn(marker, text, f"install.py에 공통 훅 계약 marker 누락: {marker}")

    def test_install_doc_uses_runtime_hook_contract_summary(self):
        doc = _install._install_claude_settings.__doc__ or ""
        self.assertIn(
            _install.HOOK_CONTRACT_SUMMARY,
            doc,
            "HOOK_CONTRACT_SUMMARY 실제 값이 install docstring에 반영되지 않았습니다",
        )
        self.assertNotIn(
            "HOOK_CONTRACT_SUMMARY",
            doc,
            "상수 이름만 남아 있고 실제 값으로 치환되지 않았습니다",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
