"""Completion-report guidance must distinguish code, evidence, and task completion."""
import hashlib
import json
from pathlib import Path

import pytest

from omc_output_contract import OutputContractError, parse_envelope


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stage", ["task", "review"])
@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_completion_reporting_has_explicit_verification_boundaries(stage, platform):
    relative = (
        f"templates/.agents/skills/omc-{stage}/SKILL.md"
        if platform == "codex"
        else f"templates/.claude/commands/{stage}.md"
    )
    text = (ROOT / relative).read_text()
    markers = [
        "완료 판정 계약",
        "대상·방법·통과 조건",
        "통제된 컴포넌트",
        "실제 서비스",
        "필수 검증 통과",
        "미실행",
        "실패",
        "증거 불일치",
        "브라우저 불필요",
        "구현됨·검증 미완료",
        "코드 승인",
        "전체 완료",
        "물리적 차단",
    ]
    assert not [marker for marker in markers if marker not in text]


def test_task_incomplete_report_preserves_existing_machine_contract():
    text = (ROOT / "templates/.agents/skills/omc-task/SKILL.md").read_text()
    assert "outcome=blocked" in text
    assert "verification_incomplete" in text
    assert "outcome=done" in text
    assert "BLOCK=>blocked,null,true" in text
    assert "PROCEED=>done,omc-review,false" in text


def test_review_approval_does_not_emit_a_blocked_machine_outcome():
    text = (ROOT / "templates/.agents/skills/omc-review/SKILL.md").read_text()
    assert "APPROVE/APPROVE WITH NOTES=>approved,null" in text
    assert "user_selection_needed는 boolean" in text


@pytest.mark.parametrize("stage", ["task", "review"])
def test_installed_skill_copies_match_completion_reporting_source(stage):
    expected = (ROOT / f"templates/.agents/skills/omc-{stage}/SKILL.md").read_bytes()
    required = ROOT / f".agents/skills/omc-{stage}/SKILL.md"
    assert required.read_bytes() == expected
    legacy = ROOT / f".agent/skills/omc-{stage}/SKILL.md"
    if legacy.exists():
        assert legacy.read_bytes() == expected


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_task_requires_exact_status_first_line(platform):
    path = (
        "templates/.agents/skills/omc-task/SKILL.md"
        if platform == "codex"
        else "templates/.claude/commands/task.md"
    )
    text = (ROOT / path).read_text()
    for marker in [
        "검증 상태 첫 문장",
        "첫 줄은 정확히",
        "필수 검증 통과·작업 완료",
        "검증 실패·수정 필요",
        "증거 불일치·재검증 필요",
        "구현 상태: 반영됨",
    ]:
        assert marker in text


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_review_separates_manual_suggestion_from_machine_routing(platform):
    path = (
        "templates/.agents/skills/omc-review/SKILL.md"
        if platform == "codex"
        else "templates/.claude/commands/review.md"
    )
    text = (ROOT / path).read_text()
    assert "수동 제안" in text
    assert "기계 라우팅" in text
    assert "BLOCK의 next_skill은 null" in text


def test_retained_failure_responses_are_not_accepted_as_success():
    records = json.loads(
        (ROOT / "scripts/fixtures/omc_completion_reporting_failures.json").read_text()
    )
    for record in records.values():
        assert hashlib.sha256(record["response"].encode()).hexdigest() == record["sha256"]
    task = records["task_body_conflict"]["response"].split("REVIEW_REPORT", 1)[0]
    first = task.split("TASK_REPORT", 1)[1].strip().splitlines()[0]
    assert first.startswith("작업 완료")
    assert first != "구현됨·검증 미완료"
    task = task.split("TASK_REPORT", 1)[1].strip().removesuffix("---").strip()
    assert parse_envelope(task)["outcome"] == "blocked"
    review = records["review_route_conflict"]["response"].split("REVIEW_REPORT", 1)[1].strip()
    with pytest.raises(OutputContractError, match="next_skill conflicts"):
        parse_envelope(review)
