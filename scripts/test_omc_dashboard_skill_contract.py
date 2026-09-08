"""Contract tests for the bounded Codex-only omc-dashboard V0 skill."""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SKILL = ROOT / ".agents" / "skills" / "omc-dashboard" / "SKILL.md"
TEMPLATE_SKILL = (
    ROOT / "templates" / ".agents" / "skills" / "omc-dashboard" / "SKILL.md"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing dashboard skill: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _handoff_errors(handoff: dict[str, object]) -> list[str]:
    errors: list[str] = []
    checks_passed = True
    snapshot = handoff.get("source_snapshot")
    if not isinstance(snapshot, dict):
        errors.append("source_snapshot")
        checks_passed = False
    else:
        if not isinstance(snapshot.get("path"), str) or not snapshot["path"].strip():
            errors.append("source_snapshot.path")
            checks_passed = False
        digest = snapshot.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            errors.append("source_snapshot.sha256")
            checks_passed = False
        final_digest = snapshot.get("handoff_sha256")
        if (
            not isinstance(final_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", final_digest) is None
            or final_digest != digest
        ):
            errors.append("source_snapshot.handoff_sha256")
            checks_passed = False
        if not isinstance(snapshot.get("size_bytes"), int) or snapshot["size_bytes"] < 0:
            errors.append("source_snapshot.size_bytes")
            checks_passed = False
        if not isinstance(snapshot.get("row_count"), int) or snapshot["row_count"] < 0:
            errors.append("source_snapshot.row_count")
            checks_passed = False
        if (
            not isinstance(snapshot.get("handoff_size_bytes"), int)
            or snapshot.get("handoff_size_bytes") != snapshot.get("size_bytes")
        ):
            errors.append("source_snapshot.handoff_size_bytes")
            checks_passed = False
        if (
            not isinstance(snapshot.get("handoff_row_count"), int)
            or snapshot.get("handoff_row_count") != snapshot.get("row_count")
        ):
            errors.append("source_snapshot.handoff_row_count")
            checks_passed = False

    interaction = handoff.get("interaction")
    checks = handoff.get("verification")
    if not isinstance(checks, dict):
        return errors + ["verification"]

    required = (
        "data_verification",
        "build_verification",
        "interaction_verification",
        "desktop_render_verification",
        "mobile_render_verification",
        "sensitive_data_check",
    )
    for name in required:
        check = checks.get(name)
        if not isinstance(check, dict):
            errors.append(name)
            checks_passed = False
            continue
        allowed = {"PASS"}
        if name == "interaction_verification" and interaction == "없음":
            allowed.add("N/A")
        if check.get("status") not in allowed:
            errors.append(f"{name}.status")
            checks_passed = False
        if not isinstance(check.get("command_or_method"), str) or not check[
            "command_or_method"
        ].strip():
            errors.append(f"{name}.command_or_method")
            checks_passed = False
        if not isinstance(check.get("evidence"), str) or not check["evidence"].strip():
            errors.append(f"{name}.evidence")
            checks_passed = False

    for name, expected in (
        ("desktop_render_verification", "1440px"),
        ("mobile_render_verification", "390px"),
    ):
        check = checks.get(name)
        if isinstance(check, dict) and check.get("viewport") != expected:
            errors.append(f"{name}.viewport")
            checks_passed = False
    if checks_passed and handoff.get("acceptance_state") != "USER_ACCEPTANCE_PENDING":
        errors.append("acceptance_state")
    if not checks_passed and handoff.get("acceptance_state") == "USER_ACCEPTANCE_PENDING":
        errors.append("acceptance_state")
    return errors


def test_dashboard_skill_live_and_template_are_identical() -> None:
    live = _read(LIVE_SKILL)
    assert live == _read(TEMPLATE_SKILL)
    assert live.startswith("---\nname: omc-dashboard\n")
    assert "\ndescription:" in live.split("---", 2)[1]


def test_dashboard_skill_is_installed_on_both_agent_surfaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "target"
        target.mkdir()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "install.py"), "--target", str(target)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        expected = _read(TEMPLATE_SKILL)
        assert (
            target / ".agents" / "skills" / "omc-dashboard" / "SKILL.md"
        ).read_text(encoding="utf-8") == expected
        assert (
            target / ".agent" / "skills" / "omc-dashboard" / "SKILL.md"
        ).read_text(encoding="utf-8") == expected


def test_dashboard_skill_has_a_bounded_input_and_mutation_contract() -> None:
    text = _read(LIVE_SKILL)

    for marker in (
        "Codex-only V0",
        "JSON 또는 CSV",
        "data_source",
        "audience",
        "questions",
        "output_root",
        "interaction",
        "필터 최대 1개",
        "격리 디렉터리",
        "기존 저장소",
        "명시적 승인",
        "자동 배포·push·PR·commit 금지",
    ):
        assert marker in text


def test_dashboard_skill_requires_independent_completion_evidence() -> None:
    text = _read(LIVE_SKILL)

    required_evidence = (
        "data_verification",
        "build_verification",
        "interaction_verification",
        "desktop_render_verification",
        "mobile_render_verification",
        "sensitive_data_check",
        "1440px",
        "390px",
    )
    for marker in required_evidence:
        assert marker in text

    assert "검증 중 하나라도 실패하거나 실행하지 못하면 완료로 보고하지 않는다" in text
    assert "데이터 경로 테스트는 화면 검증을 대체하지 않는다" in text


def test_dashboard_skill_binds_completion_to_snapshot_and_executed_evidence() -> None:
    text = _read(LIVE_SKILL)

    for marker in (
        "source_snapshot",
        "sha256",
        "size_bytes",
        "row_count",
        "handoff_sha256",
        "handoff_size_bytes",
        "handoff_row_count",
        "handoff 직전",
        "최초 sha256과 일치",
        "같은 snapshot",
        "status: PASS | FAIL | NOT_RUN",
        "command_or_method",
        "evidence",
        "viewport: 1440px",
        "viewport: 390px",
        "acceptance_state: <파생 상태>",
    ):
        assert marker in text


def test_dashboard_handoff_contract_rejects_unexecuted_or_unbound_claims() -> None:
    valid_check = {
        "status": "PASS",
        "command_or_method": "npm test",
        "evidence": "exit=0; report=verification.txt",
    }
    valid = {
        "interaction": "없음",
        "source_snapshot": {
            "path": "evidence/source.csv",
            "sha256": "a" * 64,
            "handoff_sha256": "a" * 64,
            "size_bytes": 123,
            "row_count": 4,
            "handoff_size_bytes": 123,
            "handoff_row_count": 4,
        },
        "acceptance_state": "USER_ACCEPTANCE_PENDING",
        "verification": {
            "data_verification": dict(valid_check),
            "build_verification": dict(valid_check),
            "interaction_verification": {
                "status": "N/A",
                "command_or_method": "frozen input contract inspection",
                "evidence": "interaction=없음",
            },
            "desktop_render_verification": {
                **valid_check,
                "viewport": "1440px",
            },
            "mobile_render_verification": {
                **valid_check,
                "viewport": "390px",
            },
            "sensitive_data_check": dict(valid_check),
        },
    }
    assert _handoff_errors(valid) == []

    invalid = {
        **valid,
        "interaction": "필터 1개",
        "source_snapshot": {"path": "", "sha256": "self-reported"},
        "verification": {
            **valid["verification"],
            "data_verification": {
                "status": "NOT_RUN",
                "command_or_method": "",
                "evidence": "",
            },
            "interaction_verification": {
                "status": "N/A",
                "command_or_method": "inspection",
                "evidence": "claimed",
            },
            "desktop_render_verification": {
                **valid_check,
                "viewport": "desktop",
            },
        },
    }
    errors = _handoff_errors(invalid)
    assert "source_snapshot.sha256" in errors
    assert "source_snapshot.handoff_sha256" in errors
    assert "source_snapshot.handoff_size_bytes" in errors
    assert "source_snapshot.handoff_row_count" in errors
    assert "data_verification.status" in errors
    assert "data_verification.evidence" in errors
    assert "interaction_verification.status" in errors
    assert "desktop_render_verification.viewport" in errors
    assert "acceptance_state" in errors


def test_dashboard_skill_preserves_fail_closed_states_and_handoff() -> None:
    text = _read(LIVE_SKILL)

    for state in (
        "INPUT_CONTRACT_REQUIRED",
        "DATA_CLASSIFICATION_REQUIRED",
        "DATA_VERIFICATION_FAILED",
        "BUILD_FAILED",
        "RENDER_NOT_VERIFIED",
        "SURFACE_VERIFICATION_FAILED",
        "USER_ACCEPTANCE_PENDING",
        "SKILL_IMPLEMENTED_NOT_FORWARD_VALIDATED",
        "WORKFLOW_REPEATABILITY_OBSERVED",
        "NOT_YET_PROVEN",
    ):
        assert state in text

    for field in (
        "artifact_root",
        "run_command",
        "preview_status",
        "limitations",
        "acceptance_state",
    ):
        assert field in text

    assert "다른 실제 데이터" in text
    assert "Claude Artifact와 동등" in text
    assert "주장하지 않는다" in text
