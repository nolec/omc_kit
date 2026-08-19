"""Machine-readable output envelope contract tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "omc_output_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("omc_output_contract", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_and_parse_plan_ready_envelope():
    module = _load_module()

    rendered = module.render_envelope(
        stage="plan",
        verdict="PROCEED",
        risk="low",
        next_skill="omc-task",
        user_selection_needed=False,
    )

    parsed = module.parse_envelope(rendered)
    assert parsed == {
        "schema_version": "omc-output/v1",
        "stage": "plan",
        "outcome": "ready",
        "risk": "low",
        "next_skill": "omc-task",
        "user_selection_needed": False,
        "reason_code": None,
        "verdict": "PROCEED",
    }
    assert rendered.splitlines()[-1] == "VERDICT: PROCEED"


@pytest.mark.parametrize(
    ("stage", "verdict", "outcome"),
    [
        ("plan", "HOLD", "unresolved"),
        ("task", "PROCEED", "done"),
        ("task", "BLOCK", "blocked"),
        ("review", "APPROVE", "approved"),
        ("review", "APPROVE WITH NOTES", "approved"),
        ("review", "REVISE", "blocked"),
    ],
)
def test_stage_verdict_mapping(stage: str, verdict: str, outcome: str):
    module = _load_module()
    assert module.outcome_for(stage, verdict) == outcome


def test_parse_rejects_verdict_outcome_conflict():
    module = _load_module()
    output = (
        'OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"task",'
        '"outcome":"done","risk":"high","next_skill":null,'
        '"user_selection_needed":true,"reason_code":"test_failure"}\n'
        "VERDICT: BLOCK"
    )

    with pytest.raises(module.OutputContractError, match="outcome conflicts"):
        module.parse_envelope(output)


def test_parse_rejects_nonempty_content_after_terminal_contract_lines():
    module = _load_module()
    output = module.render_envelope(
        stage="task",
        verdict="PROCEED",
        risk="low",
        next_skill="omc-review",
        user_selection_needed=False,
    )

    with pytest.raises(module.OutputContractError, match="last two non-empty lines"):
        module.parse_envelope(f"{output}\nprovider stderr warning")


def test_parse_rejects_block_without_reason_code():
    module = _load_module()
    output = (
        'OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"task",'
        '"outcome":"blocked","risk":"high","next_skill":null,'
        '"user_selection_needed":true,"reason_code":null}\n'
        "VERDICT: BLOCK"
    )

    with pytest.raises(module.OutputContractError, match="reason_code"):
        module.parse_envelope(output)


def test_normalize_legacy_verdict_once():
    module = _load_module()

    normalized = module.normalize_output("implementation complete\nVERDICT: PROCEED", stage="task")

    parsed = module.parse_envelope(normalized)
    assert parsed["outcome"] == "done"
    assert parsed["next_skill"] == "omc-review"
    assert normalized.count("OMC_OUTPUT:") == 1
    assert normalized.splitlines()[-1] == "VERDICT: PROCEED"


def test_contract_source_distinguishes_raw_and_legacy_outputs():
    module = _load_module()
    raw = module.render_envelope(
        stage="plan",
        verdict="PROCEED",
        risk="low",
        next_skill="omc-task",
        user_selection_needed=False,
    )

    assert module.contract_source(raw, stage="plan") == "raw_compliant"
    assert module.contract_source("VERDICT: PROCEED", stage="plan") == "legacy_normalized"


@pytest.mark.parametrize(
    ("stage", "verdict", "next_skill", "selection_needed", "reason_code"),
    [
        ("plan", "PROCEED", "omc-review", False, None),
        ("plan", "REVISE", "omc-plan", False, "needs_revision"),
        ("task", "BLOCK", "omc-task", True, "test_failure"),
        ("review", "REVISE", None, False, "finding"),
    ],
)
def test_render_rejects_semantically_inconsistent_routing(
    stage: str,
    verdict: str,
    next_skill: str | None,
    selection_needed: bool,
    reason_code: str | None,
):
    module = _load_module()

    with pytest.raises(module.OutputContractError, match="routing policy"):
        module.render_envelope(
            stage=stage,
            verdict=verdict,
            risk="high",
            next_skill=next_skill,
            user_selection_needed=selection_needed,
            reason_code=reason_code,
        )


def test_normalize_rejects_explicit_invalid_envelope_instead_of_repairing():
    module = _load_module()
    output = (
        'OMC_OUTPUT: {"schema_version":"omc-output/v1","stage":"review",'
        '"outcome":"approved","risk":"low","next_skill":null,'
        '"user_selection_needed":false,"reason_code":null}\n'
        "VERDICT: BLOCK"
    )

    with pytest.raises(module.OutputContractError):
        module.normalize_output(output, stage="review")


def test_next_skill_uses_canonical_id_not_executor_syntax():
    module = _load_module()

    with pytest.raises(module.OutputContractError, match="next_skill"):
        module.render_envelope(
            stage="plan",
            verdict="PROCEED",
            risk="low",
            next_skill="$omc-task",
            user_selection_needed=False,
        )


def test_compact_envelope_stays_within_output_budget():
    module = _load_module()
    rendered = module.render_envelope(
        stage="review",
        verdict="APPROVE WITH NOTES",
        risk="medium",
        next_skill=None,
        user_selection_needed=True,
    )

    assert len(rendered.encode("utf-8")) <= 260


@pytest.mark.parametrize(
    ("relative_path", "expected_contract"),
    [
        (".agents/skills/omc-plan/references/workflow.md", "stage=plan / outcome=unresolved|ready"),
        (".agents/skills/omc-task/SKILL.md", "stage=task / outcome=blocked|done"),
        (".agents/skills/omc-review/SKILL.md", "stage=review / outcome=approved|blocked"),
        ("templates/.agents/skills/omc-plan/references/workflow.md", "stage=plan / outcome=unresolved|ready"),
        ("templates/.agents/skills/omc-task/SKILL.md", "stage=task / outcome=blocked|done"),
        ("templates/.agents/skills/omc-review/SKILL.md", "stage=review / outcome=approved|blocked"),
        ("templates/.agent/skills/omc-plan/references/workflow.md", "stage=plan / outcome=unresolved|ready"),
        ("templates/.agent/skills/omc-task/SKILL.md", "stage=task / outcome=blocked|done"),
        ("templates/.agent/skills/omc-review/SKILL.md", "stage=review / outcome=approved|blocked"),
    ],
)
def test_skill_output_contract_matches_parser_schema(relative_path: str, expected_contract: str):
    content = (ROOT / relative_path).read_text(encoding="utf-8")
    assert expected_contract in content


@pytest.mark.parametrize(
    ("stage", "markers"),
    [
        ("plan", ("PROCEED=>ready,omc-task,false", "REVISE=>unresolved,omc-plan,true")),
        ("task", ("PROCEED=>done,omc-review,false", "BLOCK=>blocked,null,true")),
        ("review", ("APPROVE=>approved,null,context", "REVISE=>blocked,omc-task,false")),
    ],
)
def test_prompt_contract_declares_stage_routing_policy(stage: str, markers: tuple[str, ...]):
    module = _load_module()
    prompt = module.prompt_contract(stage)
    for marker in markers:
        assert marker in prompt


PILOT_SKILL_PATHS = [
    ROOT / prefix / "omc-plan" / "references" / "workflow.md"
    for prefix in (
        Path(".agents/skills"),
        Path("templates/.agents/skills"),
        Path("templates/.agent/skills"),
    )
] + [
    ROOT / prefix / skill / "SKILL.md"
    for prefix in (
        Path(".agents/skills"),
        Path("templates/.agents/skills"),
        Path("templates/.agent/skills"),
    )
    for skill in ("omc-task", "omc-review")
]
PILOT_SKILL_PATHS += [
    path
    for path in (
        ROOT / ".agent/skills/omc-plan/references/workflow.md",
        ROOT / ".agent/skills/omc-task/SKILL.md",
        ROOT / ".agent/skills/omc-review/SKILL.md",
    )
    if path.exists()
]


@pytest.mark.parametrize("path", PILOT_SKILL_PATHS)
def test_pilot_skills_declare_compact_machine_envelope(path: Path):
    text = path.read_text(encoding="utf-8")
    for marker in (
        "omc-output/v1",
        "OMC_OUTPUT: {JSON}",
        "next_skill",
        "user_selection_needed",
        "reason_code",
        "VERDICT",
    ):
        assert marker in text, f"{path.relative_to(ROOT)} missing {marker}"


def test_pilot_skills_share_the_same_machine_contract_block():
    blocks = []
    for path in PILOT_SKILL_PATHS:
        text = path.read_text(encoding="utf-8")
        marker = "## Machine output contract"
        assert text.count(marker) == 1, f"{path.relative_to(ROOT)} contract block count"
        block = text.split(marker, 1)[1].split("\n## ", 1)[0].strip()
        blocks.append(block)

    assert len(set(blocks)) == 1
