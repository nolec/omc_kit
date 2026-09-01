from __future__ import annotations

import json
from pathlib import Path

import pytest

import omc_mission as mission


def _packet() -> dict[str, object]:
    return {
        "schema_version": mission.MISSION_SCHEMA_VERSION,
        "request_sha256": "a" * 64,
        "base_commit": "b" * 40,
        "outcome": "승인된 변경을 안전하게 완료한다.",
        "deliverables": ["구현", "검증 증거"],
        "definition_of_done": ["요구사항 테스트 통과", "리뷰 입력에 동일 mission 사용"],
        "non_goals": ["자동 배포"],
        "validation": {"max_total_rounds": 2, "max_revisions_per_issue": 1},
    }


def test_freeze_and_load_mission_packet_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "mission.json"

    frozen = mission.freeze_mission_packet(path, _packet())

    assert frozen["packet_sha256"] == mission.load_mission_packet(path)["packet_sha256"]
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(mission.MissionError, match="mission_packet_already_frozen"):
        mission.freeze_mission_packet(path, _packet())


def test_mission_packet_rejects_tamper_and_symlink(tmp_path: Path) -> None:
    path = tmp_path / "mission.json"
    mission.freeze_mission_packet(path, _packet())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["outcome"] = "변조됨"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(mission.MissionError, match="mission_packet_digest_mismatch"):
        mission.load_mission_packet(path)

    link = tmp_path / "mission-link.json"
    link.symlink_to(path)
    with pytest.raises(mission.MissionError, match="mission_packet_symlink"):
        mission.load_mission_packet(link)


def test_approval_receipt_binds_exact_mission_request_base_and_session() -> None:
    packet = mission.validate_mission_packet(_packet())
    receipt = mission.build_mission_approval_receipt(
        decision_id="mission-1",
        session_id="session-1",
        packet=packet,
    )

    mission.validate_mission_approval_receipt(
        receipt,
        packet=packet,
        session_id="session-1",
    )
    changed = dict(packet)
    changed.pop("packet_sha256")
    changed["request_sha256"] = "c" * 64
    with pytest.raises(mission.MissionError, match="mission_approval_binding_mismatch"):
        mission.validate_mission_approval_receipt(
            receipt,
            packet=changed,
            session_id="session-1",
        )


def test_stage_briefing_is_stable_and_contains_only_frozen_mission() -> None:
    packet = mission.validate_mission_packet(_packet())

    task = mission.build_stage_briefing(packet, stage="task")
    critique = mission.build_stage_briefing(packet, stage="critique")
    review = mission.build_stage_briefing(packet, stage="review")

    assert task["mission_packet_sha256"] == critique["mission_packet_sha256"]
    assert task["mission_packet_sha256"] == review["mission_packet_sha256"]
    assert task["mission"] == critique["mission"] == review["mission"]
    assert {task["stage"], critique["stage"], review["stage"]} == {
        "task",
        "critique",
        "review",
    }
