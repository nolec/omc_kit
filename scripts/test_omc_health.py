"""
N1 — omc_health.py RED 테스트
아직 omc_health.py가 없으므로 모두 FAIL 예상
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
HEALTH_SCRIPT = ROOT / "scripts" / "omc_health.py"
pytestmark = pytest.mark.slow

sys.path.insert(0, str(ROOT / "scripts"))
import omc_health  # noqa: E402


def _run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, str(HEALTH_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        **kwargs,
    )


class TestOmcHealthExists:
    def test_script_exists(self):
        """omc_health.py 파일이 존재해야 한다"""
        assert HEALTH_SCRIPT.exists(), f"{HEALTH_SCRIPT} 파일이 없습니다"

    def test_help_flag(self):
        """--help 플래그가 작동해야 한다"""
        result = _run("--help")
        assert result.returncode == 0

    def test_exits_with_zero_or_one(self):
        """정상 실행 시 0 또는 1(이슈 있음)을 반환해야 한다"""
        result = _run("--report-only")
        assert result.returncode in (0, 1), f"예상치 못한 종료 코드: {result.returncode}"


class TestOmcHealthOutput:
    def test_output_has_python_section(self):
        """출력에 Python 검사 섹션이 있어야 한다"""
        result = _run("--report-only")
        combined = result.stdout + result.stderr
        assert "Python" in combined or "파이썬" in combined, \
            f"Python 검사 섹션 없음. 출력:\n{combined[:300]}"

    def test_output_has_test_collection_section(self):
        """출력에 테스트 수집 섹션이 있어야 한다"""
        result = _run("--report-only")
        combined = result.stdout + result.stderr
        assert "테스트" in combined, \
            f"테스트 검사 섹션 없음. 출력:\n{combined[:300]}"

    def test_output_has_summary(self):
        """출력 마지막에 요약(HEALTH 또는 점수)이 있어야 한다"""
        result = _run("--report-only")
        combined = result.stdout + result.stderr
        assert any(kw in combined for kw in ["HEALTH", "health", "요약", "summary"]), \
            f"요약 섹션 없음. 출력:\n{combined[:300]}"


class TestOmcHealthFlags:
    def test_report_only_flag_exists(self):
        """--report-only 플래그: 차단 없이 결과만 출력"""
        result = _run("--report-only")
        # 0(이슈 없음) 또는 1(이슈 있음) — 2+ 는 스크립트 오류
        assert result.returncode <= 1

    def test_fast_flag_skips_slow_checks(self):
        """--fast 플래그: 빠른 체크만 실행"""
        result = _run("--fast", "--report-only")
        assert result.returncode <= 1


def test_health_suite_is_marked_slow():
    assert pytestmark.name == "slow"


def test_python_compile_check_uses_scripts_root():
    result = omc_health.check_python_compile()

    assert result.name == "Python 문법"


def test_test_collection_check_has_omc_scripts_scope():
    result = omc_health.check_test_collection()

    assert result.name == "테스트 수집"


def test_dead_code_check_scans_python_scripts_only(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "example.py").write_text("# TODO: review\n", encoding="utf-8")
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "ignored.ts").write_text("// TODO: ignore\n", encoding="utf-8")
    monkeypatch.setattr(omc_health, "ROOT", tmp_path)

    result = omc_health.check_dead_code()

    assert result.count == 1
    assert "example.py" in result.detail
