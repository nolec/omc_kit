"""
C1 — omc_context_save.py RED 테스트
아직 omc_context_save.py가 없으므로 모두 FAIL 예상
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "omc_context_save.py"
WIP_DIR = ROOT / ".omc" / "wip"


def _run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ROOT), **kwargs,
    )


class TestContextSaveExists:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"{SCRIPT} 없음"

    def test_help(self):
        r = _run("--help")
        assert r.returncode == 0


class TestContextSave:
    def test_save_creates_wip_dir(self):
        """save 실행 시 .omc/wip/ 디렉토리가 생성되어야 한다"""
        _run("save", "--decisions", "테스트 결정", "--remaining", "남은 작업")
        assert WIP_DIR.exists(), ".omc/wip/ 디렉토리 없음"

    def test_save_creates_latest_json(self):
        """save 실행 시 .omc/wip/latest.json이 생성되어야 한다"""
        _run("save", "--decisions", "결정A", "--remaining", "남은B")
        latest = WIP_DIR / "latest.json"
        assert latest.exists(), ".omc/wip/latest.json 없음"

    def test_save_stores_decisions(self):
        """save --decisions 값이 latest.json에 저장되어야 한다"""
        _run("save", "--decisions", "OrderConfirm 컬럼 재구성 완료")
        data = json.loads((WIP_DIR / "latest.json").read_text(encoding="utf-8"))
        assert "OrderConfirm" in data.get("decisions", ""), \
            f"decisions 저장 안 됨: {data}"

    def test_save_stores_remaining(self):
        """save --remaining 값이 latest.json에 저장되어야 한다"""
        _run("save", "--remaining", "취소 모달 연동 미완")
        data = json.loads((WIP_DIR / "latest.json").read_text(encoding="utf-8"))
        assert "취소" in data.get("remaining", ""), \
            f"remaining 저장 안 됨: {data}"

    def test_save_stores_failed_approaches(self):
        """save --failed 값이 latest.json에 저장되어야 한다"""
        _run("save", "--failed", "직접 API 호출 → 상태 꼬임으로 포기")
        data = json.loads((WIP_DIR / "latest.json").read_text(encoding="utf-8"))
        assert "포기" in data.get("failed_approaches", ""), \
            f"failed_approaches 저장 안 됨: {data}"

    def test_save_stores_timestamp(self):
        """save 시 saved_at 타임스탬프가 저장되어야 한다"""
        _run("save", "--decisions", "타임스탬프 테스트")
        data = json.loads((WIP_DIR / "latest.json").read_text(encoding="utf-8"))
        assert "saved_at" in data, f"saved_at 없음: {data}"

    def test_save_stores_git_context(self):
        """save 시 현재 브랜치·커밋 정보가 저장되어야 한다"""
        _run("save", "--decisions", "git 컨텍스트 테스트")
        data = json.loads((WIP_DIR / "latest.json").read_text(encoding="utf-8"))
        assert "branch" in data or "commit" in data, \
            f"git 컨텍스트 없음: {data}"


class TestContextRestore:
    def test_restore_prints_saved_content(self):
        """restore 실행 시 저장된 decisions·remaining이 출력되어야 한다"""
        _run("save", "--decisions", "복원 테스트 결정", "--remaining", "복원 남은 작업")
        r = _run("restore")
        assert r.returncode == 0
        assert "복원 테스트 결정" in r.stdout or "복원 테스트 결정" in r.stderr, \
            f"저장된 decisions가 출력에 없음:\n{r.stdout}"

    def test_restore_no_wip_exits_gracefully(self, tmp_path):
        """wip 파일이 없을 때 restore는 오류 없이 안내 메시지를 출력해야 한다"""
        fake_wip = tmp_path / ".omc" / "wip" / "latest.json"
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "restore"],
            capture_output=True, text=True,
            env={"OMC_WIP_PATH": str(fake_wip), "HOME": str(tmp_path),
                 "PATH": __import__("os").environ.get("PATH", "")},
            cwd=str(ROOT),
        )
        assert r.returncode in (0, 1), f"예상치 못한 종료 코드: {r.returncode}"


@pytest.mark.parametrize(("override", "advance", "expected"), [
    ("count", False, "같은 지점"),
    ("parameters", False, "같은 지점"),
    ("count", True, "현재 이력이 앞섬"),
    ("parameters", True, "현재 이력이 앞섬"),
    ("identity", False, "확인 불가"),
])
def test_restore_git_relationship_with_command_config(tmp_path, override, advance, expected):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(OMC_PROJECT_ROOT=str(tmp_path),
               OMC_WIP_PATH=str(tmp_path / ".omc/wip/latest.json"))

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], env=env, text=True,
        ).strip()

    git("init", "-q")
    git("-c", "user.name=WIP Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-qm", "saved")
    saved = _run("save", "--remaining", "작업 메모", env=env)
    assert saved.returncode == 0, saved.stderr
    if advance:
        git("-c", "user.name=WIP Test", "-c", "user.email=test@example.invalid",
            "commit", "--allow-empty", "-qm", "next")
    head = git("rev-parse", "HEAD")
    wip = Path(env["OMC_WIP_PATH"])
    original = wip.read_bytes()

    if override in ("count", "identity"):
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.worktree",
                   GIT_CONFIG_VALUE_0=str(tmp_path / "other-worktree"))
    if override == "parameters":
        env["GIT_CONFIG_PARAMETERS"] = "invalid command-scoped parameters"
    if override == "identity":
        env["GIT_DIR"] = str(tmp_path / "other-repository")

    result = _run("restore", env=env)
    assert result.returncode == 0, result.stderr
    assert "작업 메모" in result.stdout
    assert wip.read_bytes() == original
    assert f"저장 커밋과 현재 HEAD: {expected}" in result.stdout
    assert f"현재 HEAD  : {'확인 불가' if override == 'identity' else head}" in result.stdout


class TestContextSaveShortForm:
    def test_save_without_args_uses_defaults(self):
        """인수 없이 save 호출 시 git 정보만으로도 저장되어야 한다"""
        r = _run("save")
        assert r.returncode == 0
        assert (WIP_DIR / "latest.json").exists()
