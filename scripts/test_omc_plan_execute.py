"""
C2 — plan→execute 흐름 연결 RED 테스트
omc_context.py build_context 에 WIP 섹션이 포함되어야 한다
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

WIP_FILE = ROOT / ".omc" / "wip" / "latest.json"


def _write_wip(decisions="", remaining="", failed=""):
    """테스트용 WIP 파일을 직접 작성한다"""
    WIP_FILE.parent.mkdir(parents=True, exist_ok=True)
    WIP_FILE.write_text(
        json.dumps({
            "saved_at": "2026-05-19T09:00:00+00:00",
            "branch": "feature/test-branch",
            "commit": "abc1234",
            "commit_subject": "test commit",
            "decisions": decisions,
            "remaining": remaining,
            "failed_approaches": failed,
        }, ensure_ascii=False),
        encoding="utf-8",
    )


class TestBuildContextIncludesWip:
    def test_build_context_includes_wip_section_when_wip_exists(self):
        """WIP 파일이 있으면 build_context 결과에 WIP 섹션이 포함되어야 한다"""
        import omc_context

        _write_wip(decisions="OrderConfirm 컬럼 완성", remaining="취소 모달 연동 미완")
        ctx, _ = omc_context.build_context(ROOT)
        assert "WIP" in ctx or "wip" in ctx.lower() or "남은 작업" in ctx or "취소 모달" in ctx, \
            f"WIP 섹션이 context에 없음:\n{ctx[-500:]}"

    def test_build_context_wip_decisions_included(self):
        """WIP decisions 내용이 context에 포함되어야 한다"""
        import omc_context

        _write_wip(decisions="중요결정_ABC", remaining="")
        ctx, _ = omc_context.build_context(ROOT)
        assert "중요결정_ABC" in ctx, \
            f"decisions 값이 context에 없음:\n{ctx[-500:]}"

    def test_build_context_wip_remaining_included(self):
        """WIP remaining 내용이 context에 포함되어야 한다"""
        import omc_context

        _write_wip(remaining="남은작업_XYZ")
        ctx, _ = omc_context.build_context(ROOT)
        assert "남은작업_XYZ" in ctx, \
            f"remaining 값이 context에 없음:\n{ctx[-500:]}"

    def test_build_context_no_wip_still_works(self, tmp_path):
        """WIP 파일이 없어도 build_context가 정상 동작해야 한다"""
        import importlib
        import os
        import omc_context

        fake_root = tmp_path
        (fake_root / ".omc").mkdir()
        (fake_root / "package.json").write_text('{"name":"test"}', encoding="utf-8")

        ctx, _ = omc_context.build_context(fake_root)
        assert isinstance(ctx, str) and len(ctx) > 0, "WIP 없을 때 context 빈 문자열"


class TestContextStatusOutput:
    def test_omc_context_save_status_shows_wip(self):
        """omc_context_save status 명령이 WIP 정보를 출력해야 한다"""
        import subprocess
        _write_wip(remaining="테스트_남은작업_ABC")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "omc_context_save.py"), "status"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert r.returncode == 0
        assert "테스트_남은작업_ABC" in r.stdout or "테스트_남은작업_ABC" in r.stderr, \
            f"status 출력에 remaining 없음:\n{r.stdout}"
