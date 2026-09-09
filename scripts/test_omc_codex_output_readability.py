"""Codex plan/task/review user-output readability contracts."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts/omc_codex_output_readability.py"
NATIVE_FIXTURE_PATH = ROOT / "scripts/fixtures/omc_codex_output_readability_native.json"
SURFACES = {
    "plan": ROOT / ".agents/skills/omc-plan/references/workflow.md",
    "task": ROOT / ".agents/skills/omc-task/SKILL.md",
    "review": ROOT / ".agents/skills/omc-review/SKILL.md",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_validator():
    spec = importlib.util.spec_from_file_location("omc_codex_output_readability", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_codex_surface_declares_its_stage_specific_visible_order():
    expected = {
        "plan": "결정 → 범위 → 태스크 → 승인 요청",
        "task": "결과 → 변경 → 검증 → 다음 행동",
        "review": "finding → 검증 → 판정",
    }
    for stage, path in SURFACES.items():
        assert expected[stage] in _read(path), f"{stage} visible order is missing"


def test_each_codex_surface_declares_shared_normal_output_budget():
    limits = {"plan": "20줄", "task": "20줄", "review": "24줄"}
    for stage, path in SURFACES.items():
        text = _read(path)
        for marker in (
            "첫 3줄 안에 결론",
            "같은 사실 반복 0회",
            "다음 행동은 정확히 1개",
            limits[stage],
            "Machine output contract 두 줄은 줄 수·중복·다음 행동 측정에서 제외",
        ):
            assert marker in text, f"{stage} missing readability marker: {marker}"


def test_each_codex_surface_forbids_exposing_internal_wait_state():
    for stage, path in SURFACES.items():
        assert "`사용자 선택 대기` 직접 노출 금지" in _read(path), stage


def test_task_limits_progress_commentary_without_hiding_required_gates():
    text = _read(SURFACES["task"])
    assert "진행 commentary는 최대 3회" in text
    assert "시작 / 실제 RED 결과 / 최종 TDD GATE" in text
    assert "최종 답변에서 단계별 진행 로그를 재서술하지 않는다" in text


def test_review_collapses_empty_buckets_but_preserves_failure_detail():
    text = _read(SURFACES["review"])
    assert "모든 severity가 비면 한 줄로 합친다" in text
    assert "REVISE/BLOCK은 원인·영향·수정 방향을 유지" in text


def test_plan_preserves_blocking_decisions_in_compact_output():
    text = _read(SURFACES["plan"])
    assert "미해결 결정이 있으면 길이보다 decisions_required를 우선" in text


def test_readability_contract_is_mirrored_to_install_templates():
    mirrors = {
        "plan": ROOT / "templates/.agents/skills/omc-plan/references/workflow.md",
        "task": ROOT / "templates/.agents/skills/omc-task/SKILL.md",
        "review": ROOT / "templates/.agents/skills/omc-review/SKILL.md",
    }
    for stage, source in SURFACES.items():
        assert _read(source) == _read(mirrors[stage]), f"{stage} template drift"


SYNTHETIC_CONTRACT_SAMPLES = [
    ("plan", "ready", """결정: 구현 가능\n범위: 인증 오류 매핑\n태스크: RED 후 최소 수정\n승인 요청: `$omc-task` 진행\n<!-- OMC_OUTPUT: {\"stage\":\"plan\",\"next_skill\":\"omc-task\"} -->\nVERDICT: PROCEED"""),
    ("plan", "blocked", """결정: 범위 확정 필요\n범위: 인증 공급자 미정\ndecisions_required: 공급자를 선택해 주세요\n승인 요청: 인증 공급자를 선택해 주세요\n<!-- OMC_OUTPUT: {\"stage\":\"plan\",\"next_skill\":null} -->\nVERDICT: HOLD"""),
    ("task", "ready", """결과: 구현 완료\n변경: 인증 오류 매핑 추가\n검증: 12 passed\n다음 행동: `$omc-review`\n<!-- OMC_OUTPUT: {\"stage\":\"task\",\"next_skill\":\"omc-review\"} -->\nVERDICT: PROCEED"""),
    ("task", "blocked", """결과: 구현 차단\n원인: fixture가 없음\n영향: 회귀 검증 불가\n수정 방향: fixture를 제공\n다음 행동: 누락된 fixture를 제공해 주세요\n<!-- OMC_OUTPUT: {\"stage\":\"task\",\"next_skill\":null} -->\nVERDICT: BLOCK"""),
    ("review", "ready", """[치명][중대][경미][제안]: 없음\n검증: 12 passed\n판정: APPROVE\n다음 행동: 변경사항을 커밋할지 결정해 주세요\n<!-- OMC_OUTPUT: {\"stage\":\"review\",\"next_skill\":null} -->\nVERDICT: APPROVE"""),
    ("review", "blocked", """[중대] output validator 누락\n원인: 문자열 존재만 검사\n영향: 잘못된 출력도 통과\n수정 방향: 실제 응답을 검증\n검증: 회귀 테스트 실패\n판정: REVISE\n다음 행동: `$omc-task`\n<!-- OMC_OUTPUT: {\"stage\":\"review\",\"next_skill\":\"omc-task\"} -->\nVERDICT: REVISE"""),
]


@pytest.mark.parametrize(("stage", "outcome", "sample"), SYNTHETIC_CONTRACT_SAMPLES)
def test_six_synthetic_samples_satisfy_observable_readability_contract(stage, outcome, sample):
    validator = _load_validator()
    assert validator.validate_output(sample, stage=stage, outcome=outcome) == []


def test_machine_footer_is_excluded_from_all_readability_measurements():
    validator = _load_validator()
    sample = SYNTHETIC_CONTRACT_SAMPLES[2][2]
    metrics = validator.measure_output(sample)
    assert metrics["visible_line_count"] == 4
    assert metrics["next_action_count"] == 1
    assert metrics["duplicate_line_count"] == 0


def test_validator_rejects_overlong_duplicate_and_multi_action_output():
    validator = _load_validator()
    body = "\n".join(["결과: 완료", "변경: 동일", "변경: 동일", "검증: pass"] + [f"세부: {i}" for i in range(18)])
    sample = f"{body}\n다음 행동: `$omc-review`\n다음 행동: `$omc-ship`\n<!-- OMC_OUTPUT: {{}} -->\nVERDICT: PROCEED"
    errors = validator.validate_output(sample, stage="task", outcome="ready")
    assert {"line_budget_exceeded", "duplicate_visible_line", "next_action_count_invalid"} <= set(errors)


def test_validator_rejects_unknown_outcome_fail_closed():
    validator = _load_validator()
    errors = validator.validate_output(
        SYNTHETIC_CONTRACT_SAMPLES[2][2], stage="task", outcome="blokced"
    )
    assert errors == ["unknown_outcome"]


def test_validator_rejects_internal_wait_state_as_visible_next_action():
    validator = _load_validator()
    sample = """[치명][중대][경미][제안]: 없음
검증: 12 passed
판정: APPROVE
다음 행동: 사용자 선택 대기
<!-- OMC_OUTPUT: {"stage":"review","next_skill":null} -->
VERDICT: APPROVE"""
    assert "generic_next_action" in validator.validate_output(
        sample, stage="review", outcome="ready"
    )


def test_validator_accepts_common_markdown_prefixes_from_native_codex_output():
    validator = _load_validator()
    sample = """결정: 구현 계획 확정\n→ 범위: 출력 계약\n- 태스크: validator 검증\n→ 승인 요청: `$omc-task`\n<!-- OMC_OUTPUT: {\"stage\":\"plan\"} -->\nVERDICT: PROCEED"""
    assert validator.validate_output(sample, stage="plan", outcome="ready") == []


def test_provider_prompt_makes_every_required_prefix_non_optional():
    validator = _load_validator()
    prompt = validator.prompt_contract("task")
    assert "각 항목 생략 금지" in prompt
    assert "첫 네 항목의 시작 문자열" in prompt
    for prefix in validator.ORDERED_PREFIXES["task"]:
        assert f"`{prefix}`" in prompt


def test_six_native_codex_forward_samples_have_provenance_and_pass_contract():
    validator = _load_validator()
    fixture = json.loads(NATIVE_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["source"] == "native_codex_cli"
    assert fixture["model"] == "gpt-5.6-luna"
    assert fixture["execution_mode"] == "ephemeral_read_only"
    assert len(fixture["samples"]) == 6
    for sample in fixture["samples"]:
        assert sample["captured_at"]
        assert validator.validate_output(
            sample["output"], stage=sample["stage"], outcome=sample["outcome"]
        ) == []


def test_native_codex_forward_samples_bind_each_output_to_fresh_execution_evidence():
    fixture = json.loads(NATIVE_FIXTURE_PATH.read_text(encoding="utf-8"))
    session_ids = set()
    for sample in fixture["samples"]:
        execution = sample["execution"]
        assert execution["provider_session_id"]
        assert execution["prompt_sha256"] == hashlib.sha256(
            execution["prompt"].encode("utf-8")
        ).hexdigest()
        assert execution["raw_event_sha256"] == hashlib.sha256(
            execution["raw_event_jsonl"].encode("utf-8")
        ).hexdigest()
        assert execution["output_sha256"] == hashlib.sha256(
            sample["output"].encode("utf-8")
        ).hexdigest()
        events = [json.loads(line) for line in execution["raw_event_jsonl"].splitlines()]
        assert events[0] == {
            "type": "thread.started",
            "thread_id": execution["provider_session_id"],
        }
        agent_messages = [
            event["item"]["text"]
            for event in events
            if event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ]
        assert agent_messages == [sample["output"]]
        assert events[-1]["type"] == "turn.completed"
        session_ids.add(execution["provider_session_id"])
    assert len(session_ids) == len(fixture["samples"])
