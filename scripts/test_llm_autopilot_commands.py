"""
test_llm_autopilot_commands.py — LLM 커맨드 파일 autopilot pipeline 반영 확인

T1: Claude autopilot.md 존재 + pipeline 포함 + 따옴표 처리
T2: Gemini omc-commands.md pipeline 포함 + 고급 사용 섹션 유지
T3: Codex omc-commands.md pipeline 포함
T4: Claude plan.md에 omc.py autopilot 구버전 참조 없음
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
CLAUDE_PLAN_REQUIRED_MARKERS = [
    "python3 scripts/omc.py state sync-session --target . --mode autopilot --title \"omc-plan\" --request \"<현재 작업 한 줄 요약>\" --roles analysis",
    "python3 scripts/omc.py state status --target .",
    "CONTRACT",
    "목표",
    "범위 (포함)",
    "범위 (제외)",
    "DoD",
    "제약",
    "사용자 컨펌",
    "입력",
    "출력",
    "성공 지표",
    "실패 정책",
    "영향받는 파일",
    "plan full",
    "plan lite",
    "RED",
    "GREEN",
    "VERIFY",
    "python3 scripts/omc.py state confirm --target .",
    "주추천 1개",
    "자동으로 진행하지는 않습니다.",
]


def _read(path: Path) -> str:
    assert path.exists(), f"파일 없음: {path}"
    return path.read_text(encoding="utf-8")


# ── T1: Claude autopilot.md ─────────────────────────────────────────────

def test_claude_autopilot_md_exists():
    assert (TEMPLATES / ".claude" / "commands" / "autopilot.md").exists()


def test_claude_autopilot_contains_pipeline():
    text = _read(TEMPLATES / ".claude" / "commands" / "autopilot.md")
    assert "pipeline" in text, "pipeline 키워드 없음"


def test_claude_autopilot_quoted_arguments():
    """--instruction 인수에 따옴표가 있어야 한다."""
    text = _read(TEMPLATES / ".claude" / "commands" / "autopilot.md")
    assert '"$ARGUMENTS"' in text or "'$ARGUMENTS'" in text, (
        '--instruction "$ARGUMENTS" 따옴표 없음'
    )


# ── T2: Gemini pipeline + 고급 사용 섹션 ────────────────────────────────

def test_gemini_autopilot_contains_pipeline():
    text = _read(TEMPLATES / ".gemini" / "commands" / "omc-commands.md")
    assert "pipeline" in text


def test_gemini_legacy_preserved():
    """구버전 task 파일 방식이 고급 사용 섹션에 남아있어야 한다."""
    text = _read(TEMPLATES / ".gemini" / "commands" / "omc-commands.md")
    assert "task-file" in text, "구버전 --task-file 내용이 완전히 삭제됨"
    assert "고급 사용" in text, "고급 사용 섹션 헤딩 없음"
    assert "omc_autopilot.py status" in text, "status 명령 없음"


# ── T3: Codex pipeline ───────────────────────────────────────────────────

def test_codex_autopilot_contains_pipeline():
    text = _read(TEMPLATES / ".codex" / "commands" / "omc-commands.md")
    assert "pipeline" in text


# ── T4: Claude plan.md 구버전 참조 없음 ─────────────────────────────────

def test_claude_plan_no_legacy_autopilot():
    text = _read(TEMPLATES / ".claude" / "commands" / "plan.md")
    assert "omc.py autopilot" not in text, (
        "plan.md에 구버전 omc.py autopilot 참조 남아있음"
    )


def test_claude_plan_contains_contract_markers():
    text = _read(TEMPLATES / ".claude" / "commands" / "plan.md")
    missing = [marker for marker in CLAUDE_PLAN_REQUIRED_MARKERS if marker not in text]
    assert not missing, f"Claude plan.md 필수 마커 누락: {missing}"


def test_deployed_claude_plan_matches_template():
    template = _read(TEMPLATES / ".claude" / "commands" / "plan.md")
    deployed = _read(ROOT / ".claude" / "commands" / "plan.md")
    assert deployed == template, "deployed .claude/commands/plan.md가 template와 다름"


# ── T5: pipeline-status 문서화 확인 ─────────────────────────────────────────


def test_claude_autopilot_deployed_exists():
    """deployed .claude/commands/autopilot.md가 존재해야 한다."""
    assert (ROOT / ".claude" / "commands" / "autopilot.md").exists(), (
        ".claude/commands/autopilot.md 없음 — templates에서 동기화 필요"
    )


def test_templates_include_pipeline_status():
    """templates 3개 파일 모두 pipeline-status 키워드를 포함해야 한다."""
    files = [
        TEMPLATES / ".claude" / "commands" / "autopilot.md",
        TEMPLATES / ".gemini" / "commands" / "omc-commands.md",
        TEMPLATES / ".codex" / "commands" / "omc-commands.md",
    ]
    for f in files:
        text = _read(f)
        assert "pipeline-status" in text, (
            f"pipeline-status 미문서화: {f.relative_to(ROOT)}"
        )


def test_deployed_include_pipeline_status():
    """deployed 3개 파일 모두 pipeline-status 키워드를 포함해야 한다."""
    files = [
        ROOT / ".claude" / "commands" / "autopilot.md",
        ROOT / ".gemini" / "commands" / "omc-commands.md",
        ROOT / ".codex" / "commands" / "omc-commands.md",
    ]
    for f in files:
        text = _read(f)
        assert "pipeline-status" in text, (
            f"pipeline-status 미문서화(deployed): {f.relative_to(ROOT)}"
        )
