#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
import fcntl
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omc_utils  # noqa: E402
from omc_decision_input import (  # noqa: E402
    build_status_followup_input,
    resolve_status_followup_from_input,
)

_LOCK_REGISTRY: dict[str, dict[str, object]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _iso_now() -> str:
    return _now().isoformat(timespec="seconds")


def _find_constitution(project_root: Path) -> str | None:
    """프로젝트의 근간이 되는 규칙 문서를 우선순위대로 찾아 내용을 반환합니다."""
    candidates = [
        project_root / ".gemini" / "GEMINI.md",
        project_root / ".claude" / "CLAUDE.md",
        project_root / "GEMINI.md",
        project_root / "CLAUDE.md",
        project_root / "CONTRACT.md",
        project_root / "AGENTS.md",
    ]
    for path in candidates:
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            # 너무 길면 상단 100줄만 가져오거나 요약하는 등의 처리가 필요할 수 있으나,
            # 일단 전체를 가져오되 요약본에서는 가독성을 위해 적절히 처리합니다.
            lines = content.splitlines()
            if len(lines) > 50:
                return "\n".join(lines[:50]) + "\n\n...(truncated for brevity, see " + path.name + ")"
            return content
    return None


def _slug_now() -> str:
    return _now().strftime("%Y%m%dT%H%M%S")



def _omc_root(project_root: Path) -> Path:
    return project_root / ".omc"


def _state_dir(project_root: Path) -> Path:
    return _omc_root(project_root) / "state"


def _sessions_dir(project_root: Path) -> Path:
    return _state_dir(project_root) / "sessions"


def _memory_path(project_root: Path) -> Path:
    return _omc_root(project_root) / "project-memory.json"


def _notepad_path(project_root: Path) -> Path:
    return _omc_root(project_root) / "notepad.md"


def _summary_path(project_root: Path) -> Path:
    return _omc_root(project_root) / "summary.md"


def _latest_path(project_root: Path) -> Path:
    return _state_dir(project_root) / "latest.json"


def _hooks_path(project_root: Path) -> Path:
    return _omc_root(project_root) / "hooks.json"


def _compact_dir(project_root: Path) -> Path:
    return _state_dir(project_root) / "compactions"


def _policy_path(project_root: Path) -> Path:
    return _omc_root(project_root) / "policy.json"


def _lock_path(project_root: Path) -> Path:
    return _state_dir(project_root) / ".lock"


def _session_path(project_root: Path, session_id: str) -> Path:
    return _sessions_dir(project_root) / session_id / "session.json"


def _runs_dir(project_root: Path) -> Path:
    return _state_dir(project_root) / "runs"


def _run_path(project_root: Path, run_id: str) -> Path:
    return _runs_dir(project_root) / f"{run_id}.json"


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


@contextmanager
def _omc_lock(project_root: Path):
    path = _lock_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    state = _LOCK_REGISTRY.get(key)
    if state is None:
        fh = path.open("a+", encoding="utf-8")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        state = {"fh": fh, "depth": 1}
        _LOCK_REGISTRY[key] = state
    else:
        state["depth"] = int(state["depth"]) + 1
    try:
        yield
    finally:
        state = _LOCK_REGISTRY.get(key)
        if state is None:
            return
        depth = int(state["depth"]) - 1
        if depth > 0:
            state["depth"] = depth
            return
        fh = state["fh"]
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
        _LOCK_REGISTRY.pop(key, None)


def _write_json(path: Path, payload: dict) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _ensure_tree(project_root: Path) -> None:
    omc_root = _omc_root(project_root)
    state_dir = _state_dir(project_root)
    sessions_dir = _sessions_dir(project_root)
    omc_root.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    _runs_dir(project_root).mkdir(parents=True, exist_ok=True)
    _compact_dir(project_root).mkdir(parents=True, exist_ok=True)

    memory_path = _memory_path(project_root)
    if not memory_path.exists():
        _write_json(
            memory_path,
            {
                "version": 1,
                "created_at": _iso_now(),
                "updated_at": _iso_now(),
                "entries": [],
            },
        )

    notepad_path = _notepad_path(project_root)
    if not notepad_path.exists():
        _atomic_write_text(
            notepad_path,
            "# OMC Notepad\n\n- 생성됨: 아직 기록 없음\n",
        )

    summary_path = _summary_path(project_root)
    if not summary_path.exists():
        _atomic_write_text(
            summary_path,
            "# OMC Summary\n\n- 생성됨: 아직 요약 없음\n",
        )

    latest_path = _latest_path(project_root)
    if not latest_path.exists():
        _write_json(
            latest_path,
            {
                "version": 1,
                "updated_at": _iso_now(),
                "latest_session_id": None,
                "latest_confirmed_session_id": None,
                "latest_confirmed_roles": [],
                "latest_confirmed_request": None,
                "latest_confirmation": {"status": "none"},
                "active_run_id": None,
                "latest_run_id": None,
            },
        )

    hooks_path = _hooks_path(project_root)
    if not hooks_path.exists():
        _write_json(
            hooks_path,
            {
                "version": 1,
                "hooks": {
                    "session_start": [
                        {"type": "shell", "command": "python3 scripts/omc_context.py --target ."},
                        {"type": "builtin", "name": "refresh_notepad"},
                        {
                            "type": "shell",
                            "name": "omc_status_check",
                            "command": "python3 scripts/omc.py state status",
                            "description": "새 세션 시작 시 OMC 상태 자동 출력 — LLM이 confirmed 여부를 즉시 파악",
                        },
                        {
                            "type": "shell",
                            "name": "role_reminder",
                            "command": "echo '[OMC] 역할 선언 필수: analysis(읽기전용) | directive(파일수정/실행) | analysis+directive(둘다)'",
                            "description": "역할 구분을 LLM에게 상기시킴",
                        },
                    ],
                    "session_end": [
                        {"type": "builtin", "name": "refresh_notepad"},
                        {"type": "builtin", "name": "auto_compact"},
                    ],
                    "pre_compact": [{"type": "builtin", "name": "snapshot_memory"}],
                    "post_compact": [{"type": "builtin", "name": "refresh_notepad"}],
                },
            },
        )

    policy_path = _policy_path(project_root)
    if not policy_path.exists():
        _write_json(
            policy_path,
            {
                "version": 1,
                "enforce_confirm": True,
                "auto_compact_threshold_count": 50,
                "auto_compact_keep_entries": 25,
                "notes": "Mutating commands should be blocked until the latest OMC session is confirmed.",
            },
        )
    else:
        # 기존 policy.json에 auto_compact 키가 없으면 비침습적으로 추가
        try:
            existing = _read_json(policy_path, {})
            updated = False
            if "auto_compact_threshold_count" not in existing:
                existing["auto_compact_threshold_count"] = 50
                updated = True
            if "auto_compact_keep_entries" not in existing:
                existing["auto_compact_keep_entries"] = 25
                updated = True
            if updated:
                _write_json(policy_path, existing)
        except Exception:
            pass


def _git_info(project_root: Path) -> dict[str, object]:
    def run_git(*args: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(project_root), *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    head = run_git("rev-parse", "--short", "HEAD")
    branch = run_git("branch", "--show-current")
    last_commit = run_git("log", "-1", "--pretty=%s")
    dirty_lines = run_git("status", "--short")
    dirty_count = len([line for line in (dirty_lines or "").splitlines() if line.strip()])
    return {
        "head": head,
        "branch": branch,
        "last_commit": last_commit,
        "dirty_count": dirty_count,
    }


def _git_scope_snapshot(project_root: Path) -> dict[str, list[str]]:
    proc = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {"staged": [], "unstaged": [], "omc_artifacts": [], "untracked": []}

    staged: list[str] = []
    unstaged: list[str] = []
    omc_artifacts: list[str] = []
    untracked: list[str] = []

    for raw_line in proc.stdout.splitlines():
        if not raw_line:
            continue
        status_code = raw_line[:2]
        path_part = raw_line[3:] if len(raw_line) > 3 else ""
        path = path_part.split(" -> ")[-1].strip()
        if not path:
            continue
        if path.startswith(".omc/"):
            omc_artifacts.append(path)
            continue
        if status_code == "??":
            untracked.append(path)
            continue
        if status_code[0] != " ":
            staged.append(path)
        if len(status_code) > 1 and status_code[1] != " ":
            unstaged.append(path)

    return {
        "staged": sorted(dict.fromkeys(staged)),
        "unstaged": sorted(dict.fromkeys(unstaged)),
        "omc_artifacts": sorted(dict.fromkeys(omc_artifacts)),
        "untracked": sorted(dict.fromkeys(untracked)),
    }


def _format_scope_bucket(paths: list[str], *, empty: str = "없음", limit: int = 4) -> str:
    if not paths:
        return empty
    sample = paths[:limit]
    text = ", ".join(sample)
    if len(paths) > limit:
        text += f" 외 {len(paths) - limit}개"
    return f"{len(paths)}개 ({text})"


def _format_recent_runs_bucket(runs: list[dict[str, object]], *, limit: int = 3) -> str:
    if not runs:
        return "없음"
    items: list[str] = []
    for entry in runs[:limit]:
        items.append(f"{entry.get('command_name', '')}({entry.get('status', '')})")
    text = ", ".join(items)
    if len(runs) > limit:
        text += f" 외 {len(runs) - limit}개"
    return text


def _excerpt(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _request_kind(request: str, project_root: Path | None = None) -> str:
    t = _normalize(request)
    if any(k in t for k in ["디버", "debug", "버그", "error", "exception", "trace", "stack", "재현", "원인"]):
        return "debug"
    if any(k in t for k in ["리뷰", "review", "code review", "diff", "pull request", "merge request", "검토"]):
        return "review"
    if any(k in t for k in ["설계", "design", "아키텍처", "구조", "interface", "api"]):
        return "design"
    # 도메인 키워드는 policy.json의 "domain_keywords" 배열에서 읽어옵니다 (하드코딩 금지)
    if project_root is not None:
        policy = _read_json(_policy_path(project_root), {})
        domain_kws = [str(k).lower() for k in policy.get("domain_keywords", [])]
        if domain_kws and any(k in t for k in domain_kws):
            return "domain"
    if any(k in t for k in ["문서", "docs", "레퍼", "reference", "찾아", "검색", "조사", "탐색"]):
        return "research"
    return "build"


def _blocked_questions(request_kind: str) -> list[str]:
    templates = {
        "debug": [
            "재현 절차 1개와 실제 에러/로그를 같이 볼까요?",
            "기대 동작과 현재 오동작을 한 줄씩 고정할까요?",
        ],
        "review": [
            "검토 대상 파일이나 diff 범위를 먼저 고정할까요?",
            "버그/리스크 중심인지 스타일/구조까지 볼지 정할까요?",
        ],
        "design": [
            "포함 범위와 제외 범위를 먼저 고정할까요?",
            "완료 조건을 인터페이스/데이터 계약 기준으로 잡을까요?",
        ],
        "domain": [
            "라이브 실행 없이 분석/검증만 할지 먼저 고정할까요?",
            "지표 정의, 비용 가정, 검증 구간 중 무엇부터 볼지 정할까요?",
        ],
        "research": [
            "찾아야 하는 대상과 산출 형태를 먼저 고정할까요?",
            "요약만 필요한지, 링크/근거까지 필요한지 정할까요?",
        ],
        "build": [
            "목표와 완료 조건을 한 줄씩 먼저 고정할까요?",
            "포함 범위와 제외 범위를 먼저 정할까요?",
        ],
    }
    return templates.get(request_kind, templates["build"])


def _handoff_focus(request_kind: str) -> tuple[str, str]:
    templates = {
        "debug": (
            "재현 경로와 실제 로그를 먼저 고정",
            "에러 재현 1건과 기대/실제 동작 차이를 바로 확인",
        ),
        "review": (
            "검토 범위와 기준을 먼저 고정",
            "대상 diff/파일과 버그 중심 여부를 먼저 확정",
        ),
        "design": (
            "범위와 데이터 계약을 먼저 고정",
            "포함/제외 범위와 완료 조건을 먼저 합의",
        ),
        "domain": (
            "검증 범위와 비용 가정을 먼저 고정",
            "라이브 실행 없이 분석/검증만 할지와 비용 가정을 먼저 확정",
        ),
        "research": (
            "조사 범위와 산출 형식을 먼저 고정",
            "요약만 필요한지 근거/링크까지 필요한지 먼저 확정",
        ),
        "build": (
            "완료 조건과 범위를 먼저 고정",
            "바로 구현할지, 먼저 설계/검토할지 우선순위를 정리",
        ),
    }
    return templates.get(request_kind, templates["build"])


def _entry_summary(entry: dict[str, object]) -> str:
    kind = str(entry.get("kind", "entry"))
    if kind == "session":
        role_ids = ",".join([str(r) for r in (entry.get("role_ids") or [])])
        status = str(entry.get("lifecycle", {}).get("status", "unknown"))
        return f"{entry.get('created_at', '')} | {status} | {entry.get('mode', '')} | {role_ids} | {_excerpt(str(entry.get('request', '')))}"
    if kind == "run":
        return (
            f"{entry.get('created_at', '')} | {entry.get('status', '')} | "
            f"{entry.get('command_name', '')} | {_excerpt(str(entry.get('summary', '')))}"
        )
    if kind == "note":
        return f"{entry.get('created_at', '')} | {entry.get('note_kind', 'note')} | {_excerpt(str(entry.get('text', '')))}"
    return f"{entry.get('created_at', '')} | {kind}"


def _result_summary_bits(entry: dict[str, object]) -> list[str]:
    result = entry.get("result")
    if not isinstance(result, dict):
        return []
    bits: list[str] = []
    for key in ["trades", "win_rate_pct", "avg_net_pnl_pct", "avg_net_pct", "profit_factor", "best_val_loss"]:
        if key in result:
            bits.append(f"{key}={result[key]}")
    return bits


def _recent_runs(
    entries: list[dict[str, object]],
    *,
    session_id: str | None = None,
    statuses: tuple[str, ...] = ("completed",),
    limit: int = 3,
) -> list[dict[str, object]]:
    runs = [
        entry
        for entry in entries
        if entry.get("kind") == "run" and str(entry.get("status")) in set(statuses)
    ]
    if session_id is not None:
        runs = [entry for entry in runs if str(entry.get("session_id")) == str(session_id)]
    return list(reversed(runs))[:limit]


def _pipeline_history_run_count(project_root: Path) -> int:
    runs_dir = project_root / ".omc" / "runs"
    if not runs_dir.exists():
        return 0
    count = 0
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        if (run_dir / "result.json").exists():
            count += 1
    return count


def _display_run_count(project_root: Path, state_runs: list[dict[str, object]]) -> int:
    return len(state_runs) + _pipeline_history_run_count(project_root)


def _failed_run_summary(entry: dict[str, object], *, request_kind: str) -> tuple[str, str]:
    result = entry.get("result")
    result_dict = result if isinstance(result, dict) else {}
    returncode = result_dict.get("returncode")
    error = result_dict.get("error")
    stderr_tail = result_dict.get("stderr_tail")
    
    if error:
        reason = _excerpt(str(error), 120)
    elif stderr_tail:
        reason = f"exit_code={returncode} | tail: {_excerpt(str(stderr_tail).strip(), 100)}"
    elif returncode is not None:
        reason = f"exit_code={returncode}"
    else:
        reason = _excerpt(str(entry.get("progress_message", "failed")), 120)

    decision_input = build_status_followup_input(
        request_kind=request_kind,
        returncode=returncode if isinstance(returncode, int) else None,
    )
    _, next_step = resolve_status_followup_from_input(decision_input)
    return reason, next_step


def _run_outcome_line(entry: dict[str, object]) -> str:
    status = str(entry.get("status", "unknown"))
    progress = _excerpt(str(entry.get("progress_message", "")), 120)
    if status == "failed":
        result = entry.get("result")
        result_dict = result if isinstance(result, dict) else {}
        stderr_tail = result_dict.get("stderr_tail")
        if "error" in result_dict:
            progress = _excerpt(str(result_dict["error"]), 120)
        elif stderr_tail:
            progress = f"exit_code={result_dict.get('returncode')} | {stderr_tail.strip()}"
        elif "returncode" in result_dict:
            progress = f"exit_code={result_dict['returncode']}"
    return progress


def _distinct_recent_notes(notes: list[dict[str, object]], limit: int = 3) -> list[dict[str, object]]:
    def priority(entry: dict[str, object]) -> int:
        return {
            "blocked_hint": 100,
            "handoff": 90,
            "hook": 20,
        }.get(str(entry.get("note_kind", "")), 50)

    seen: set[tuple[str, str]] = set()
    ranked: list[tuple[int, int, dict[str, object]]] = []
    recent = list(reversed(notes[-8:]))
    for idx, entry in enumerate(recent):
        key = (str(entry.get("note_kind", "")), str(entry.get("text", "")))
        if key in seen:
            continue
        seen.add(key)
        ranked.append((priority(entry), -idx, entry))
    ranked.sort(reverse=True)
    return [entry for _, _, entry in ranked[:limit]]


def _should_skip_note(entries: list[dict[str, object]], *, note_kind: str, text: str) -> bool:
    for entry in reversed(entries):
        if entry.get("kind") != "note":
            continue
        return str(entry.get("note_kind", "")) == note_kind and str(entry.get("text", "")) == text.strip()
    return False


def _latest_pending_session(sessions: list[dict[str, object]]) -> dict[str, object] | None:
    return next(
        (
            entry
            for entry in reversed(sessions)
            if str(entry.get("confirmation", {}).get("status", "pending")) != "confirmed"
            and str(entry.get("lifecycle", {}).get("status", "")) not in {"superseded", "blocked"}
        ),
        None,
    )


def _session_entries(project_root: Path) -> list[dict[str, object]]:
    memory = _read_json(
        _memory_path(project_root),
        {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
    )
    entries = list(memory.get("entries", []))
    return [entry for entry in entries if entry.get("kind") == "session"]


def _load_session_entry(project_root: Path, session_id: str | None) -> dict[str, object] | None:
    if not session_id:
        return None
    path = _session_path(project_root, str(session_id))
    if not path.exists():
        return None
    return _read_json(path, {})


def _load_run_entry(project_root: Path, run_id: str | None) -> dict[str, object] | None:
    if not run_id:
        return None
    path = _run_path(project_root, str(run_id))
    if not path.exists():
        return None
    return _read_json(path, {})


def _compact_entries_with_note_policy(entries: list[dict[str, object]], *, keep_entries: int) -> list[dict[str, object]]:
    trimmed = list(entries[-keep_entries:]) if len(entries) > keep_entries else list(entries)
    last_idx_by_kind: dict[str, int] = {}
    for idx, entry in enumerate(trimmed):
        if entry.get("kind") == "note":
            kind = str(entry.get("note_kind", "note"))
            last_idx_by_kind[kind] = idx

    note_kinds_to_prune = {"handoff", "blocked_hint", "hook"}
    kept: list[dict[str, object]] = []
    for idx, entry in enumerate(trimmed):
        if entry.get("kind") != "note":
            kept.append(entry)
            continue
        note_kind = str(entry.get("note_kind", "note"))
        if note_kind in note_kinds_to_prune and last_idx_by_kind.get(note_kind) != idx:
            continue
        kept.append(entry)
    return kept


def _latest_run_for_session(
    runs: list[dict[str, object]],
    *,
    session_id: str | None,
    statuses: tuple[str, ...] = ("completed", "failed", "aborted"),
) -> dict[str, object] | None:
    if not session_id:
        return None
    return next(
        (
            entry
            for entry in reversed(runs)
            if str(entry.get("session_id")) == str(session_id) and str(entry.get("status")) in set(statuses)
        ),
        None,
    )


def _format_handoff_note(session: dict[str, object], *, project_root: Path) -> str:
    memory = _read_json(
        _memory_path(project_root),
        {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
    )
    request = str(session.get("request", ""))
    request_kind = _request_kind(request, project_root)
    recent_runs = _recent_runs(
        list(memory.get("entries", [])),
        session_id=str(session.get("session_id")),
        statuses=("failed", "completed", "aborted"),
        limit=2,
    )
    last_run_line = "none"
    next_line = next_priority
    if recent_runs:
        run = recent_runs[0]
        bits = _result_summary_bits(run)
        suffix = f" | {', '.join(bits)}" if bits else ""
        last_run_line = f"{run.get('command_name', '')} ({run.get('status', '')}) | {_run_outcome_line(run)}{suffix}"
        if str(run.get("status")) == "failed":
            _, next_line = _failed_run_summary(run, request_kind=request_kind)
    return "\n".join(
        [
            f"[이전 요청] {_excerpt(request, 180)}",
            f"[요청 유형] {request_kind}",
            f"[최근 결과] {last_run_line}",
            f"[handoff 포인트] {focus}",
            f"[다음 1순위] {next_line}",
        ]
    )


def _format_blocked_note(session: dict[str, object], *, reason: str | None) -> str:
    role_ids = ", ".join([str(r) for r in (session.get("role_ids") or [])])
    request = str(session.get("request", ""))
    request_kind = _request_kind(request)
    questions = _blocked_questions(request_kind)
    return "\n".join(
        [
            f"[blocked] {_excerpt(reason or 'Execution blocked pending confirmation.', 180)}",
            f"[대기 요청] {_excerpt(request, 180)}",
            f"[요청 유형] {request_kind}",
            f"[추천 역할] {role_ids or 'none'}",
            "[질문 템플릿] 아래 역할로 진행할까요? 그대로면 Enter, 수정이면 `+role,-role` 또는 `a,b,c`",
            f"[후속 질문 1] {questions[0]}",
            f"[후속 질문 2] {questions[1]}",
            "[다음 액션] `./run omc \"작업 요청\"`를 다시 열어 confirm 하거나 `python scripts/omc.py state confirm --target .` 실행",
        ]
    )


def _render_notepad(project_root: Path, memory: dict) -> str:
    entries = list(memory.get("entries", []))
    sessions = [entry for entry in entries if entry.get("kind") == "session"]
    runs = [entry for entry in entries if entry.get("kind") == "run"]
    notes = [entry for entry in entries if entry.get("kind") == "note"]
    latest_meta = _read_json(_latest_path(project_root), {"latest_session_id": None, "latest_confirmed_session_id": None})
    latest_session = _load_session_entry(project_root, str(latest_meta.get("latest_session_id")))
    if latest_session is None:
        latest_session = sessions[-1] if sessions else None
    latest_confirmed = next(
        (entry for entry in reversed(sessions) if str(entry.get("confirmation", {}).get("status", "pending")) == "confirmed"),
        None,
    )
    if latest_confirmed is None:
        latest_confirmed = _load_session_entry(project_root, str(latest_meta.get("latest_confirmed_session_id")))
    latest_pending = _latest_pending_session(sessions)
    if latest_pending and str(latest_pending.get("lifecycle", {}).get("status")) not in {"waiting_input", "blocked", "superseded"}:
        latest_pending = None
    # active run은 latest meta를 SSOT로 사용한다.
    # 과거 비정상 종료로 남은 stale running 엔트리를 summary가 active로 오인하지 않도록 방지.
    active_run = _load_run_entry(project_root, str(latest_meta.get("active_run_id")))
    if active_run is not None and str(active_run.get("status")) != "running":
        active_run = None
    latest_run = runs[-1] if runs else None
    git = _git_info(project_root)

    lines: list[str] = ["# OMC Notepad", ""]
    lines.append(f"- project: `{project_root.name}`")
    lines.append(f"- updated_at: {_iso_now()}")
    if git.get("head"):
        branch = git.get("branch") or "detached"
        lines.append(
            f"- git: `{branch}` @ `{git['head']}`"
            + (f" (dirty {git['dirty_count']})" if git.get("dirty_count") else "")
        )
    if git.get("last_commit"):
        lines.append(f"- last_commit: {_excerpt(str(git['last_commit']), 80)}")

    if latest_session:
        lines.append(f"- current_mode: `{latest_session.get('mode', '')}`")
        role_ids = ", ".join([str(r) for r in (latest_session.get("role_ids") or [])])
        lines.append(f"- current_roles: `{role_ids}`")
        lines.append(f"- current_request: {_excerpt(str(latest_session.get('request', '')))}")
        conf = latest_session.get("confirmation", {})
        lifecycle = latest_session.get("lifecycle", {})
        lines.append(f"- current_confirmation: `{conf.get('status', 'pending')}`")
        lines.append(f"- current_session_status: `{lifecycle.get('status', 'unknown')}`")
        if lifecycle.get("reason"):
            lines.append(f"- current_session_reason: {_excerpt(str(lifecycle.get('reason', '')), 140)}")
            if lifecycle.get("status") == "active":
                lines.append("- current_session_note: `정리 필요`")
    if latest_confirmed:
        confirmed_roles = ", ".join([str(r) for r in (latest_confirmed.get("role_ids") or [])])
        lines.append(f"- confirmed_roles: `{confirmed_roles}`")
        lines.append(f"- confirmed_request: {_excerpt(str(latest_confirmed.get('request', '')))}")
    if latest_pending:
        pending_roles = ", ".join([str(r) for r in (latest_pending.get("role_ids") or [])])
        lines.append(f"- pending_roles: `{pending_roles}`")
        lines.append(f"- pending_request: {_excerpt(str(latest_pending.get('request', '')))}")
        pending_lifecycle = latest_pending.get("lifecycle", {})
        lines.append(f"- pending_session_status: `{pending_lifecycle.get('status', 'unknown')}`")
    if active_run:
        lines.append(f"- active_run: `{active_run.get('command_name', '')}`")
        lines.append(f"- active_run_status: `{active_run.get('status', '')}`")
        if active_run.get("phase"):
            lines.append(f"- active_run_phase: `{active_run.get('phase')}`")
        if active_run.get("progress_message"):
            lines.append(f"- active_run_progress: {_excerpt(str(active_run.get('progress_message', '')))}")
    elif latest_run:
        lines.append(f"- latest_run: `{latest_run.get('command_name', '')}` ({latest_run.get('status', '')})")

    lines.append("")
    lines.append("## Recent Sessions")
    if sessions:
        for entry in sessions[-5:][::-1]:
            lines.append(f"- {_entry_summary(entry)}")
    else:
        lines.append("- none")

    if notes:
        lines.append("")
        lines.append("## Recent Notes")
        for entry in notes[-5:][::-1]:
            lines.append(f"- {_entry_summary(entry)}")

    if runs:
        lines.append("")
        lines.append("## Recent Runs")
        for entry in runs[-5:][::-1]:
            lines.append(f"- {_entry_summary(entry)}")

    return "\n".join(lines).rstrip() + "\n"


def _render_summary(project_root: Path, memory: dict) -> str:
    entries = list(memory.get("entries", []))
    sessions = [entry for entry in entries if entry.get("kind") == "session"]
    runs = [entry for entry in entries if entry.get("kind") == "run"]
    notes = [entry for entry in entries if entry.get("kind") == "note"]
    latest_meta = _read_json(_latest_path(project_root), {"latest_session_id": None, "latest_confirmed_session_id": None})
    latest_session = _load_session_entry(project_root, str(latest_meta.get("latest_session_id")))
    if latest_session is None:
        latest_session = sessions[-1] if sessions else None
    latest_confirmed = next(
        (entry for entry in reversed(sessions) if str(entry.get("confirmation", {}).get("status", "pending")) == "confirmed"),
        None,
    )
    if latest_confirmed is None:
        latest_confirmed = _load_session_entry(project_root, str(latest_meta.get("latest_confirmed_session_id")))
    latest_pending = _latest_pending_session(sessions)
    if latest_pending and str(latest_pending.get("lifecycle", {}).get("status")) not in {"waiting_input", "blocked", "superseded"}:
        latest_pending = None
    # active run은 latest meta를 SSOT로 사용한다.
    # 과거 비정상 종료로 남은 stale running 엔트리를 summary가 active로 오인하지 않도록 방지.
    active_run = _load_run_entry(project_root, str(latest_meta.get("active_run_id")))
    if active_run is not None and str(active_run.get("status")) != "running":
        active_run = None
    latest_confirmed_run = _latest_run_for_session(
        runs,
        session_id=str(latest_confirmed.get("session_id")) if latest_confirmed else None,
    )
    if latest_confirmed_run is None:
        latest_confirmed_run = _load_run_entry(project_root, str(latest_meta.get("latest_run_id")))
    recent_finished_runs = [
        entry for entry in reversed(runs) if str(entry.get("status")) in {"completed", "failed", "aborted"}
    ][:2]
    recent_notes = _distinct_recent_notes(notes, limit=2)
    latest_confirmed_request_meta = latest_meta.get("latest_confirmed_request")
    confirmed_request = (
        _excerpt(str(latest_confirmed_request_meta), 120)
        if latest_confirmed_request_meta
        else (_excerpt(str(latest_confirmed.get("request", "")), 120) if latest_confirmed else "none")
    )
    pending_request = _excerpt(str(latest_pending.get("request", "")), 120) if latest_pending else "none"
    same_request = confirmed_request != "none" and confirmed_request == pending_request

    lines: list[str] = ["# OMC Summary", ""]
    lines.append(f"- project: `{project_root.name}`")
    lines.append(f"- updated_at: {_iso_now()}")

    constitution = _find_constitution(project_root)
    if constitution:
        lines.append("")
        lines.append("## Project Constitution")
        lines.append(constitution)

    confirmed_roles_meta = latest_meta.get("latest_confirmed_roles") or []
    if latest_confirmed or confirmed_roles_meta:
        role_ids = ", ".join([str(r) for r in (confirmed_roles_meta if confirmed_roles_meta else (latest_confirmed.get("role_ids") or []))])
        lines.append(f"- confirmed_roles: `{role_ids}`")
        lines.append(f"- current_focus: {confirmed_request}")

    if latest_pending and not same_request:
        pending_roles = ", ".join([str(r) for r in (latest_pending.get("role_ids") or [])])
        lines.append(f"- pending_roles: `{pending_roles}`")
        lines.append(f"- pending_focus: {pending_request}")
    if latest_confirmed_run:
        lines.append(
            f"- confirmed_run_context: `{latest_confirmed_run.get('command_name', '')}` "
            f"{latest_confirmed_run.get('status', '')} | {_excerpt(_run_outcome_line(latest_confirmed_run), 100)}"
        )

    if latest_session and latest_session.get("lifecycle", {}).get("status") in {"blocked", "waiting_input", "superseded"}:
        lifecycle = latest_session.get("lifecycle", {})
        lines.append(f"- session_status: `{lifecycle.get('status', 'unknown')}`")
        if lifecycle.get("reason"):
            lines.append(f"- session_status_reason: {_excerpt(str(lifecycle.get('reason', '')), 180)}")

    if active_run:
        lines.append(f"- active_run: `{active_run.get('command_name', '')}`")
        lines.append(f"- active_phase: `{active_run.get('phase', '')}`")
        lines.append(f"- active_progress: {_excerpt(str(active_run.get('progress_message', '')), 180)}")
    elif recent_finished_runs:
        latest_run = recent_finished_runs[0]
        lines.append(
            f"- latest_outcome: `{latest_run.get('command_name', '')}` {latest_run.get('status', '')} | "
            f"{_excerpt(_run_outcome_line(latest_run), 100)}"
        )

    lines.append("")
    lines.append("## Key Context")
    if latest_confirmed:
        lines.append(f"- Confirmed Request: {confirmed_request}")
    else:
        lines.append("- Confirmed Request: none")
    if latest_pending:
        if same_request:
            lines.append("- Pending Request: same as confirmed")
        else:
            lines.append(f"- Pending Request: {pending_request}")
    else:
        lines.append("- Pending Request: none")

    if len(recent_finished_runs) > 1:
        lines.append("")
        lines.append("## Recent Outcomes")
        for entry in recent_finished_runs[1:]:
            metric_bits: list[str] = []
            metrics = entry.get("metrics", {})
            if isinstance(metrics, dict):
                for key in ["epoch", "best_val_loss"]:
                    if key in metrics:
                        metric_bits.append(f"{key}={metrics[key]}")
            metric_bits.extend(_result_summary_bits(entry))
            suffix = f" | {', '.join(metric_bits)}" if metric_bits else ""
            lines.append(
                f"- {entry.get('command_name', '')}: {entry.get('status', '')} | "
                f"{_excerpt(_run_outcome_line(entry), 100)}{suffix}"
            )

    if recent_notes:
        lines.append("")
        lines.append("## Notes")
        for entry in recent_notes:
            lines.append(f"- {_excerpt(str(entry.get('text', '')), 110)}")

    lines.append("")
    lines.append("## Token Policy")
    lines.append("- Use this summary as the primary OMC context block.")
    lines.append("- Consult full notepad/history only when current summary is insufficient.")
    return "\n".join(lines).rstrip() + "\n"


def _rewrite_notepad(project_root: Path) -> None:
    memory = _read_json(
        _memory_path(project_root),
        {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
    )
    _atomic_write_text(_notepad_path(project_root), _render_notepad(project_root, memory))
    _atomic_write_text(_summary_path(project_root), _render_summary(project_root, memory))


def init_state(project_root: Path, *, force: bool = False) -> None:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        if force:
            _rewrite_notepad(project_root)


def _upsert_memory_entry(project_root: Path, entry: dict[str, object], *, keep_entries: int = 80) -> None:
    memory_path = _memory_path(project_root)
    memory = _read_json(
        memory_path,
        {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
    )
    entries = list(memory.get("entries", []))
    replaced = False
    for idx, existing in enumerate(entries):
        if (
            existing.get("kind") == entry.get("kind")
            and entry.get("kind") == "run"
            and existing.get("run_id") == entry.get("run_id")
        ):
            entries[idx] = entry
            replaced = True
            break
        if (
            existing.get("kind") == entry.get("kind")
            and entry.get("kind") == "session"
            and existing.get("session_id") == entry.get("session_id")
        ):
            entries[idx] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    if len(entries) > keep_entries:
        entries = entries[-keep_entries:]
    memory["entries"] = entries
    memory["updated_at"] = _iso_now()
    _write_json(memory_path, memory)


def _update_session_entry(
    project_root: Path,
    *,
    session_id: str,
    mutate: Callable[[dict], None],
    keep_entries: int = 80,
) -> dict[str, object]:
    session_path = _session_path(project_root, session_id)
    if not session_path.exists():
        raise FileNotFoundError(session_path)
    session = _read_json(session_path, {})
    mutate(session)
    _write_json(session_path, session)
    _upsert_memory_entry(project_root, session, keep_entries=keep_entries)
    return session


def record_session(
    project_root: Path,
    *,
    mode: str,
    title: str,
    request: str,
    role_ids: list[str],
    prompt_path: str | None = None,
    base_paths: list[str] | None = None,
    team_paths: list[str] | None = None,
    confirmed: bool = False,
    confirmation_source: str | None = None,
    routing: dict[str, object] | None = None,
    keep_entries: int = 80,
) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        prev_latest = _read_json(
            _latest_path(project_root),
            {
                "latest_session_id": None,
                "latest_confirmed_session_id": None,
                "latest_confirmed_roles": [],
                "latest_confirmed_request": None,
            },
        )
        prev_latest_session_id = prev_latest.get("latest_session_id")
        session_id = f"{_slug_now()}-{uuid.uuid4().hex[:8]}"
        entry = {
            "kind": "session",
            "session_id": session_id,
            "created_at": _iso_now(),
            "mode": mode,
            "title": title,
            "request": request.strip(),
            "role_ids": list(role_ids),
            "prompt_path": prompt_path,
            "base_paths": list(base_paths or []),
            "team_paths": list(team_paths or []),
            "routing": dict(routing or {}),
            "confirmation": {
                "status": "confirmed" if confirmed else "pending",
                "confirmed_at": _iso_now() if confirmed else None,
                "source": confirmation_source if confirmed else None,
            },
            "lifecycle": {
                "status": "active" if confirmed else "waiting_input",
                "updated_at": _iso_now(),
                "reason": None if confirmed else "Awaiting role confirmation.",
                "superseded_by": None,
            },
            "git": _git_info(project_root),
        }

        session_dir = _sessions_dir(project_root) / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(session_dir / "request.md", request.strip() + "\n")
        _write_json(session_dir / "session.json", entry)

        _upsert_memory_entry(project_root, entry, keep_entries=keep_entries)

        if prev_latest_session_id and str(prev_latest_session_id) != session_id:
            try:
                set_session_status(
                    project_root,
                    session_id=str(prev_latest_session_id),
                    status="superseded",
                    reason=f"Superseded by newer session {session_id}.",
                    superseded_by=session_id,
                )
            except FileNotFoundError:
                pass

        latest = {
            "version": 1,
            "updated_at": _iso_now(),
            "latest_session_id": session_id,
            "latest_mode": mode,
            "latest_roles": list(role_ids),
            "latest_request": request.strip(),
            "latest_confirmed_session_id": session_id if confirmed else prev_latest.get("latest_confirmed_session_id"),
            "latest_confirmed_roles": list(role_ids) if confirmed else prev_latest.get("latest_confirmed_roles", []),
            "latest_confirmed_request": request.strip() if confirmed else prev_latest.get("latest_confirmed_request"),
            "latest_skill": title if confirmed else prev_latest.get("latest_skill"),
            "latest_confirmation": {
                "status": "confirmed" if confirmed else "pending",
                "confirmed_at": entry["confirmation"]["confirmed_at"],
                "source": confirmation_source if confirmed else None,
            },
            "active_run_id": prev_latest.get("active_run_id"),
            "latest_run_id": prev_latest.get("latest_run_id"),
        }
        _write_json(_latest_path(project_root), latest)
        _rewrite_notepad(project_root)
        return entry


def confirm_session(project_root: Path, *, session_id: str | None = None) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        latest = _read_json(_latest_path(project_root), {"latest_session_id": None})
        resolved_session_id = session_id or latest.get("latest_session_id")
        if not resolved_session_id:
            raise ValueError("No OMC session exists yet. Run `scripts/omc.py \"...\"` first.")

        session_path = _session_path(project_root, str(resolved_session_id))
        if not session_path.exists():
            raise FileNotFoundError(session_path)

        session = _update_session_entry(
            project_root,
            session_id=str(resolved_session_id),
            mutate=lambda s: (
                s.update(
                    {
                        "confirmation": {
                            **dict(s.get("confirmation", {})),
                            "status": "confirmed",
                            "confirmed_at": _iso_now(),
                            "source": dict(s.get("confirmation", {})).get("source") or "state.confirm",
                        },
                        "lifecycle": {
                            **dict(s.get("lifecycle", {})),
                            "status": "active",
                            "updated_at": _iso_now(),
                            "reason": "Session confirmed and active.",
                            "superseded_by": None,
                        },
                    }
                )
            ),
        )
        confirmation = session.get("confirmation", {})

        latest["version"] = 1
        latest["updated_at"] = _iso_now()
        latest["latest_confirmed_session_id"] = resolved_session_id
        latest["latest_confirmed_roles"] = list(session.get("role_ids", []))
        latest["latest_confirmed_request"] = session.get("request")
        latest["latest_confirmation"] = dict(confirmation)
        _write_json(_latest_path(project_root), latest)
        _rewrite_notepad(project_root)
        return session


def set_session_status(
    project_root: Path,
    *,
    status: str,
    session_id: str | None = None,
    reason: str | None = None,
    superseded_by: str | None = None,
) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        latest = _read_json(_latest_path(project_root), {"latest_session_id": None})
        resolved_session_id = session_id or latest.get("latest_session_id")
        if not resolved_session_id:
            raise ValueError("No OMC session exists yet.")

        before = _read_json(_session_path(project_root, str(resolved_session_id)), {})
        prev_lifecycle = dict(before.get("lifecycle", {}))
        prev_status = str(prev_lifecycle.get("status", ""))
        prev_reason = prev_lifecycle.get("reason")

        session = _update_session_entry(
            project_root,
            session_id=str(resolved_session_id),
            mutate=lambda s: s.update(
                {
                    "lifecycle": {
                        **dict(s.get("lifecycle", {})),
                        "status": status,
                        "updated_at": _iso_now(),
                        "reason": reason,
                        "superseded_by": superseded_by,
                    }
                }
            ),
        )
        changed = (prev_status != status) or (prev_reason != reason)
        if changed and status == "blocked":
            append_note(
                project_root,
                note_kind="blocked_hint",
                text=_format_blocked_note(session, reason=reason),
            )
        if changed and status == "superseded":
            append_note(
                project_root,
                note_kind="handoff",
                text=_format_handoff_note(session, project_root=project_root),
            )
        _rewrite_notepad(project_root)
        return session


def start_run(
    project_root: Path,
    *,
    command_name: str,
    summary: str | None = None,
    keep_entries: int = 80,
) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        latest = _read_json(_latest_path(project_root), {"latest_confirmed_session_id": None})
        run_id = f"run-{_slug_now()}-{uuid.uuid4().hex[:8]}"
        run = {
            "kind": "run",
            "run_id": run_id,
            "session_id": latest.get("latest_confirmed_session_id"),
            "command_name": command_name,
            "summary": (summary or command_name).strip(),
            "status": "running",
            "phase": "starting",
            "created_at": _iso_now(),
            "started_at": _iso_now(),
            "updated_at": _iso_now(),
            "finished_at": None,
            "progress_message": "Command started.",
            "metrics": {},
            "result": None,
            "git": _git_info(project_root),
        }
        _write_json(_run_path(project_root, run_id), run)
        _upsert_memory_entry(project_root, run, keep_entries=keep_entries)
        latest["version"] = 1
        latest["updated_at"] = _iso_now()
        latest["active_run_id"] = run_id
        latest["latest_run_id"] = run_id
        _write_json(_latest_path(project_root), latest)
        _rewrite_notepad(project_root)
        return run


def update_run(
    project_root: Path,
    *,
    run_id: str,
    phase: str | None = None,
    message: str | None = None,
    metrics: dict[str, object] | None = None,
    keep_entries: int = 80,
) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        path = _run_path(project_root, run_id)
        if not path.exists():
            raise FileNotFoundError(path)
        run = _read_json(path, {})
        if phase is not None:
            run["phase"] = phase
        if message is not None:
            run["progress_message"] = message.strip()
        if metrics:
            merged = dict(run.get("metrics", {}))
            merged.update(metrics)
            run["metrics"] = merged
        run["updated_at"] = _iso_now()
        _write_json(path, run)
        _upsert_memory_entry(project_root, run, keep_entries=keep_entries)
        _rewrite_notepad(project_root)
        return run


def finish_run(
    project_root: Path,
    *,
    run_id: str,
    status: str,
    message: str | None = None,
    result: dict[str, object] | None = None,
    keep_entries: int = 80,
) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        path = _run_path(project_root, run_id)
        if not path.exists():
            raise FileNotFoundError(path)
        run = _read_json(path, {})
        run["status"] = status
        run["phase"] = "finished"
        if message is not None:
            run["progress_message"] = message.strip()
        run["result"] = dict(result or {})
        run["updated_at"] = _iso_now()
        run["finished_at"] = _iso_now()
        _write_json(path, run)
        _upsert_memory_entry(project_root, run, keep_entries=keep_entries)
        latest = _read_json(_latest_path(project_root), {"active_run_id": None})
        latest["version"] = 1
        latest["updated_at"] = _iso_now()
        if latest.get("active_run_id") == run_id:
            latest["active_run_id"] = None
        latest["latest_run_id"] = run_id
        _write_json(_latest_path(project_root), latest)
        _rewrite_notepad(project_root)
        return run


def append_note(project_root: Path, *, note_kind: str, text: str, keep_entries: int = 80) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        entry = {
            "kind": "note",
            "note_kind": note_kind,
            "created_at": _iso_now(),
            "text": text.strip(),
        }

        memory_path = _memory_path(project_root)
        memory = _read_json(
            memory_path,
            {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
        )
        entries = list(memory.get("entries", []))
        if _should_skip_note(entries, note_kind=note_kind, text=text):
            return entry
        entries.append(entry)
        if len(entries) > keep_entries:
            entries = entries[-keep_entries:]
        memory["entries"] = entries
        memory["updated_at"] = _iso_now()
        _write_json(memory_path, memory)
        _rewrite_notepad(project_root)
        return entry


def compact_state(project_root: Path, *, keep_entries: int = 25) -> dict[str, object]:
    with _omc_lock(project_root):
        _ensure_tree(project_root)
        memory_path = _memory_path(project_root)
        latest_meta = _read_json(_latest_path(project_root), {"latest_session_id": None, "latest_confirmed_session_id": None})
        memory = _read_json(
            memory_path,
            {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
        )
        entries = list(memory.get("entries", []))
        entries = _compact_entries_with_note_policy(entries, keep_entries=keep_entries)
        existing_session_ids = {
            str(entry.get("session_id"))
            for entry in entries
            if entry.get("kind") == "session" and entry.get("session_id")
        }
        essential_sessions: list[dict[str, object]] = []
        for session_id in [
            latest_meta.get("latest_confirmed_session_id"),
            latest_meta.get("latest_session_id"),
        ]:
            sid = str(session_id) if session_id else None
            if not sid or sid in existing_session_ids:
                continue
            loaded = _load_session_entry(project_root, sid)
            if loaded:
                essential_sessions.append(loaded)
                existing_session_ids.add(sid)
        entries.extend(essential_sessions)
        memory["entries"] = entries
        memory["updated_at"] = _iso_now()
        _write_json(memory_path, memory)
        snapshot = {
            "created_at": _iso_now(),
            "kept_entries": len(entries),
            "latest_session_id": _read_json(_latest_path(project_root), {}).get("latest_session_id"),
        }
        snapshot_path = _compact_dir(project_root) / f"compact-{_slug_now()}.json"
        _write_json(snapshot_path, snapshot)
        _rewrite_notepad(project_root)
        return {
            "kept_entries": len(entries),
            "notepad": str(_notepad_path(project_root)),
            "summary": str(_summary_path(project_root)),
            "snapshot": str(snapshot_path),
        }


def status(project_root: Path) -> str:
    _ensure_tree(project_root)
    # status 조회 시점에 notepad/summary를 강제 재생성해 stale 상태를 줄입니다.
    _rewrite_notepad(project_root)
    memory = _read_json(
        _memory_path(project_root),
        {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
    )
    entries = list(memory.get("entries", []))
    sessions = _session_entries(project_root)
    runs = [entry for entry in entries if entry.get("kind") == "run"]
    notes = [entry for entry in entries if entry.get("kind") == "note"]
    latest_pending = _latest_pending_session(sessions)
    latest_meta = _read_json(_latest_path(project_root), {"latest_session_id": None, "latest_confirmed_session_id": None})
    active_run = _load_run_entry(project_root, str(latest_meta.get("active_run_id")))
    if active_run is not None and str(active_run.get("status")) != "running":
        active_run = None
    latest_run = _load_run_entry(project_root, str(latest_meta.get("latest_run_id")))
    recent_finished_runs = [
        entry for entry in reversed(runs) if str(entry.get("status")) in {"completed", "failed", "aborted"}
    ]
    latest = _load_session_entry(project_root, str(latest_meta.get("latest_session_id")))
    if latest is None:
        latest = entries[-1] if entries else None
    git_scope = _git_scope_snapshot(project_root)
    pipeline_history_run_count = _pipeline_history_run_count(project_root)
    display_run_count = _display_run_count(project_root, runs)
    lines = [f"OMC state: {project_root}"]
    lines.append(f"- memory entries: {len(entries)}")
    lines.append(f"- sessions: {len(sessions)}")
    lines.append(f"- runs: {display_run_count}")
    if pipeline_history_run_count:
        lines.append(f"- pipeline_history_runs(.omc/runs): {pipeline_history_run_count}")
    lines.append(f"- notes: {len(notes)}")
    if latest:
        lines.append(f"- latest: {_entry_summary(latest)}")
        latest_lifecycle = dict(latest.get("lifecycle", {}))
        if latest_lifecycle.get("reason"):
            lines.append(f"- latest_session_reason: {_excerpt(str(latest_lifecycle.get('reason', '')), 140)}")
            if latest_lifecycle.get("status") == "active":
                lines.append("- latest_session_note: 정리 필요")
    policy = _read_json(_policy_path(project_root), {"enforce_confirm": True})
    lines.append(f"- latest_session_id: {latest_meta.get('latest_session_id')}")
    lines.append(f"- latest_confirmed_session_id: {latest_meta.get('latest_confirmed_session_id')}")
    lines.append(f"- latest_pending_session_id: {latest_pending.get('session_id') if latest_pending else None}")
    lines.append(f"- latest_pending_request: {_excerpt(str(latest_pending.get('request', '')), 120) if latest_pending else 'None'}")
    lines.append(f"- active_run_id: {latest_meta.get('active_run_id')}")
    lines.append(f"- latest_run_id: {latest_meta.get('latest_run_id')}")
    if active_run:
        lines.append(f"- active_run: `{active_run.get('command_name', '')}` ({active_run.get('status', '')})")
    elif latest_run:
        lines.append(f"- latest_run: `{latest_run.get('command_name', '')}` ({latest_run.get('status', '')})")
    if recent_finished_runs:
        lines.append(f"- recent_runs: {_format_recent_runs_bucket(recent_finished_runs)}")
    lines.append(f"- enforce_confirm: {policy.get('enforce_confirm', True)}")
    lines.append(f"- 현재 커밋 범위: {_format_scope_bucket(git_scope['staged'])}")
    lines.append(f"- 범위 밖 dirty 변경: {_format_scope_bucket(git_scope['unstaged'])}")
    lines.append(f"- .omc 실행 아티팩트: {_format_scope_bucket(git_scope['omc_artifacts'])}")
    lines.append(f"- untracked: {_format_scope_bucket(git_scope['untracked'])}")
    if not git_scope["staged"] and git_scope["unstaged"]:
        lines.append("- ship 차단 힌트: 현재 커밋 범위가 없어 ship 불가 — 범위 밖 dirty 변경만 존재")
        lines.append("- 다음 조치 힌트: ship 전에 먼저 현재 커밋 범위를 만들어야 함")
    lines.append(f"- summary: {_summary_path(project_root)}")
    lines.append(f"- notepad: {_notepad_path(project_root)}")
    lines.append(f"- project-memory: {_memory_path(project_root)}")
    lines.append(f"- sessions-dir: {_sessions_dir(project_root)}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Persist and compact OMC-style project state.")
    sub = ap.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the .omc state tree.")
    init.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    init.add_argument("--force", action="store_true", help="Rebuild derived files.")

    record = sub.add_parser("record", help="Record a prompt/session entry.")
    record.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    record.add_argument("--mode", required=True, help="OMC mode name.")
    record.add_argument("--title", required=True, help="Mode title.")
    record.add_argument("--request", required=True, help="Request text.")
    record.add_argument("--roles", required=True, help="Comma-separated role ids.")
    record.add_argument("--prompt-path", type=str, default=None, help="Prompt output path.")
    record.add_argument("--base", action="append", default=[], help="Base prompt path(s).")
    record.add_argument("--team", action="append", default=[], help="Team file path(s).")
    record.add_argument("--confirm", action="store_true", help="Record the session as already confirmed/active.")
    record.add_argument(
        "--confirmation-source",
        type=str,
        default=None,
        help="Optional confirmation source label when --confirm is used.",
    )
    record.add_argument("--keep", type=int, default=80, help="Maximum stored entries.")

    sync_session = sub.add_parser("sync-session", help="Record a skill-driven session as confirmed/active.")
    sync_session.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    sync_session.add_argument("--mode", required=True, help="OMC mode name.")
    sync_session.add_argument("--title", required=True, help="Mode title.")
    sync_session.add_argument("--request", required=True, help="Request text.")
    sync_session.add_argument("--roles", required=True, help="Comma-separated role ids.")
    sync_session.add_argument("--prompt-path", type=str, default=None, help="Prompt output path.")
    sync_session.add_argument("--base", action="append", default=[], help="Base prompt path(s).")
    sync_session.add_argument("--team", action="append", default=[], help="Team file path(s).")
    sync_session.add_argument("--keep", type=int, default=80, help="Maximum stored entries.")

    note = sub.add_parser("note", help="Append a persistent note.")
    note.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    note.add_argument("--kind", default="note", help="Note kind label.")
    note.add_argument("--text", required=True, help="Note text.")
    note.add_argument("--keep", type=int, default=80, help="Maximum stored entries.")

    compact = sub.add_parser("compact", help="Prune history and rewrite the notepad.")
    compact.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    compact.add_argument("--keep", type=int, default=25, help="Entries to preserve.")

    status_cmd = sub.add_parser("status", help="Show current state summary.")
    status_cmd.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")

    confirm = sub.add_parser("confirm", help="Mark the latest or a specific session as confirmed.")
    confirm.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    confirm.add_argument("--session-id", type=str, default=None, help="Specific session id to confirm.")

    session_status_cmd = sub.add_parser("session-status", help="Update session lifecycle status.")
    session_status_cmd.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    session_status_cmd.add_argument("--status", required=True, choices=["active", "waiting_input", "blocked", "superseded"])
    session_status_cmd.add_argument("--session-id", type=str, default=None, help="Specific session id to update.")
    session_status_cmd.add_argument("--reason", type=str, default=None, help="Short status reason.")
    session_status_cmd.add_argument("--superseded-by", type=str, default=None, help="Newer session id when superseded.")

    run_start = sub.add_parser("run-start", help="Mark a guarded command as running.")
    run_start.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    run_start.add_argument("--command-name", dest="command_name", required=True, help="Human-readable command name.")
    run_start.add_argument("--summary", default=None, help="Short description for the run.")

    run_update = sub.add_parser("run-update", help="Update active run progress.")
    run_update.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    run_update.add_argument("--run-id", required=True, help="Run id to update.")
    run_update.add_argument("--phase", default=None, help="Current phase label.")
    run_update.add_argument("--message", default=None, help="Progress message.")
    run_update.add_argument("--metrics-json", default=None, help="JSON object string for metrics.")

    run_finish = sub.add_parser("run-finish", help="Mark a guarded command as completed/failed.")
    run_finish.add_argument("--target", type=Path, default=Path.cwd(), help="Target repository root.")
    run_finish.add_argument("--run-id", required=True, help="Run id to finish.")
    run_finish.add_argument("--status", required=True, choices=["completed", "failed", "aborted"], help="Final run status.")
    run_finish.add_argument("--message", default=None, help="Final result message.")
    run_finish.add_argument("--result-json", default=None, help="JSON object string for result payload.")

    return ap


def main() -> int:
    ap = _parser()
    args = ap.parse_args()

    if args.command == "init":
        init_state(omc_utils.project_root(args.target), force=bool(args.force))
        print(f"Initialized: {_omc_root(omc_utils.project_root(args.target))}")
        return 0

    if args.command == "record":
        record_session(
            omc_utils.project_root(args.target),
            mode=args.mode,
            title=args.title,
            request=args.request,
            role_ids=[x.strip() for x in args.roles.split(",") if x.strip()],
            prompt_path=args.prompt_path,
            base_paths=[str(p) for p in args.base],
            team_paths=[str(p) for p in args.team],
            confirmed=bool(args.confirm),
            confirmation_source=(args.confirmation_source or "state.record") if args.confirm else None,
            keep_entries=int(args.keep),
        )
        print(status(omc_utils.project_root(args.target)))
        return 0

    if args.command == "sync-session":
        record_session(
            omc_utils.project_root(args.target),
            mode=args.mode,
            title=args.title,
            request=args.request,
            role_ids=[x.strip() for x in args.roles.split(",") if x.strip()],
            prompt_path=args.prompt_path,
            base_paths=[str(p) for p in args.base],
            team_paths=[str(p) for p in args.team],
            confirmed=True,
            confirmation_source="skill_sync",
            keep_entries=int(args.keep),
        )
        print(status(omc_utils.project_root(args.target)))
        return 0

    if args.command == "note":
        append_note(
            omc_utils.project_root(args.target),
            note_kind=args.kind,
            text=args.text,
            keep_entries=int(args.keep),
        )
        print(status(omc_utils.project_root(args.target)))
        return 0

    if args.command == "compact":
        result = compact_state(omc_utils.project_root(args.target), keep_entries=int(args.keep))
        print(f"Compacted to {result['kept_entries']} entries: {result['notepad']}")
        return 0

    if args.command == "status":
        print(status(omc_utils.project_root(args.target)))
        return 0

    if args.command == "confirm":
        session = confirm_session(omc_utils.project_root(args.target), session_id=args.session_id)
        print(f"Confirmed session: {session.get('session_id')}")
        print(status(omc_utils.project_root(args.target)))
        return 0

    if args.command == "session-status":
        session = set_session_status(
            omc_utils.project_root(args.target),
            status=args.status,
            session_id=args.session_id,
            reason=args.reason,
            superseded_by=args.superseded_by,
        )
        print(f"Updated session: {session.get('session_id')} status={session.get('lifecycle', {}).get('status')}")
        print(status(omc_utils.project_root(args.target)))
        return 0

    if args.command == "run-start":
        run = start_run(omc_utils.project_root(args.target), command_name=args.command_name, summary=args.summary)
        print(run["run_id"])
        return 0

    if args.command == "run-update":
        metrics = json.loads(args.metrics_json) if args.metrics_json else None
        run = update_run(
            omc_utils.project_root(args.target),
            run_id=args.run_id,
            phase=args.phase,
            message=args.message,
            metrics=metrics,
        )
        print(f"Updated run: {run.get('run_id')} phase={run.get('phase')}")
        return 0

    if args.command == "run-finish":
        result = json.loads(args.result_json) if args.result_json else None
        run = finish_run(
            omc_utils.project_root(args.target),
            run_id=args.run_id,
            status=args.status,
            message=args.message,
            result=result,
        )
        print(f"Finished run: {run.get('run_id')} status={run.get('status')}")
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


# ---------------------------------------------------------------------------
# 공개 API — omc_guard.py / omc_hooks.py 등 외부 모듈용
# private(_) 함수를 직접 호출하지 말고 여기서 정의된 함수를 사용하세요.
# ---------------------------------------------------------------------------

def read_latest(project_root: Path) -> dict:
    """최신 세션 메타(latest.json)를 반환합니다."""
    init_state(project_root)
    return _read_json(
        _latest_path(project_root),
        {"version": 1, "latest_session_id": None, "latest_confirmed_session_id": None},
    )


def read_policy(project_root: Path) -> dict:
    """policy.json을 반환합니다."""
    return _read_json(_policy_path(project_root), {})


def read_hooks(project_root: Path) -> dict:
    """hooks.json을 반환합니다."""
    init_state(project_root)
    return _read_json(
        _hooks_path(project_root),
        {"version": 1, "hooks": {}},
    )


def read_memory(project_root: Path) -> dict:
    """memory.json을 반환합니다."""
    return _read_json(
        _memory_path(project_root),
        {"version": 1, "created_at": _iso_now(), "updated_at": _iso_now(), "entries": []},
    )


def get_compact_dir(project_root: Path) -> Path:
    """compact 스냅샷 저장 디렉토리를 반환합니다."""
    return _compact_dir(project_root)


def make_slug_now() -> str:
    """현재 시각 기반 슬러그를 반환합니다."""
    return _slug_now()


def refresh_notepad(project_root: Path) -> Path:
    """notepad.md를 재생성하고 경로를 반환합니다."""
    _rewrite_notepad(project_root)
    return _notepad_path(project_root)


def get_session_entries(project_root: Path) -> list:
    """memory.json의 세션 엔트리 목록을 반환합니다."""
    return _session_entries(project_root)


if __name__ == "__main__":
    raise SystemExit(main())
