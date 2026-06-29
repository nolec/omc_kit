#!/usr/bin/env python3
"""
omc_role_suggest.py — 요청 텍스트를 분석해 OMC 역할을 추천하고
AI 대화 흐름에서 바로 붙여넣을 수 있는 출력을 생성합니다.

사용:
  python3 scripts/omc_role_suggest.py "기능 추가하고 싶어"
  python3 scripts/omc_role_suggest.py --text "버그 수정" --top 3 --format plain
  python3 scripts/omc_role_suggest.py --text "배포 준비" --format json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# 역할 정의 (team.json 없이도 동작하는 내장 기본값)
# ---------------------------------------------------------------------------
_BUILTIN_ROLES: list[dict] = [
    {
        "id": "senior_coding",
        "title": "시니어 코딩 비서",
        "description": "구현·설계·리팩터링 전반을 담당합니다.",
        "tags": ["구현", "개발", "feature", "refactor", "리팩", "추가", "설계", "api", "모듈", "implement", "create", "build"],
    },
    {
        "id": "analysis",
        "title": "분석 비서",
        "description": "버그·이슈 근본 원인 분석 및 조사를 담당합니다.",
        "tags": ["디버", "debug", "버그", "bug", "error", "exception", "trace", "stack", "재현", "원인", "분석", "investigate", "diagnose", "왜", "이유"],
    },
    {
        "id": "code_review",
        "title": "코드리뷰 비서",
        "description": "코드 품질·안전성·패턴을 검토합니다.",
        "tags": ["리뷰", "review", "diff", "pr", "머지", "merge", "검토", "확인"],
    },
    {
        "id": "directive",
        "title": "실행 지시 비서",
        "description": "명령 실행·배포·파일 수정 등 상태 변경 작업을 담당합니다.",
        "tags": ["실행", "생성", "배포", "deploy", "run", "make", "command", "명령", "설치", "install", "ship", "release"],
    },
    {
        "id": "search",
        "title": "검색 비서",
        "description": "문서·레퍼런스·스펙 검색을 담당합니다.",
        "tags": ["문서", "docs", "레퍼", "reference", "찾아", "검색", "spec", "스펙", "search", "find"],
    },
    {
        "id": "tdd",
        "title": "TDD 비서",
        "description": "테스트 작성 및 RED→GREEN→REFACTOR 사이클을 안내합니다.",
        "tags": ["테스트", "test", "tdd", "unit", "커버리지", "coverage", "jest", "pytest", "검증"],
    },
]

# 복합 패턴: 여러 키워드가 동시에 등장할 때 추가 점수
_PATTERN_BONUSES: list[tuple[list[str], str, int]] = [
    (["버그", "수정"], "analysis", 2),
    (["버그", "fix"], "analysis", 2),
    (["버그", "수정"], "tdd", 1),         # 버그 수정 → TDD도 권장
    (["테스트", "작성"], "tdd", 4),
    (["테스트", "추가"], "tdd", 4),
    (["코드", "리뷰"], "code_review", 4),
    (["pr", "리뷰"], "code_review", 4),
    (["기능", "추가"], "senior_coding", 3),
    (["기능", "구현"], "senior_coding", 3),
    (["리팩터", "링"], "senior_coding", 3),
    (["배포", "준비"], "directive", 4),
    (["배포", "해줘"], "directive", 4),
    (["새", "기능"], "senior_coding", 3),
    (["새", "기능"], "tdd", 1),           # 새 기능 → TDD 권장
    (["수정", "작성"], "senior_coding", 1),
]


class RoleSuggestion(NamedTuple):
    role_id: str
    title: str
    description: str
    score: int
    matched_keywords: list[str]


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_all(text: str, keywords: tuple[str, ...]) -> bool:
    return all(keyword in text for keyword in keywords)


def _load_team_roles(target: Path) -> list[dict]:
    """team.json 또는 team.local.json에서 역할 로드. 없으면 내장 기본값 사용."""
    paths_to_try = [
        target / "project_prompts" / "team.local.json",
        target / "prompts" / "team.json",
        Path(__file__).parent.parent / "prompts" / "team.json",
    ]
    for p in paths_to_try:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                roles = data.get("roles", [])
                if roles:
                    # tags 필드가 없으면 내장 roles에서 보완
                    builtin_map = {r["id"]: r for r in _BUILTIN_ROLES}
                    result = []
                    for r in roles:
                        merged = dict(builtin_map.get(r["id"], {}))
                        merged.update(r)
                        result.append(merged)
                    # tdd는 기본 team.json에 없을 수 있으므로 항상 포함
                    existing_ids = {r["id"] for r in result}
                    for br in _BUILTIN_ROLES:
                        if br["id"] not in existing_ids:
                            result.append(dict(br))
                    return result
            except Exception:
                pass
    return list(_BUILTIN_ROLES)


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _resolve_routing_policy() -> str:
    policy = _normalize(os.environ.get("OMC_ROUTING_POLICY"))
    if policy in {"balanced", "cost_saver", "quality_first"}:
        return policy
    return "balanced"


def _build_orchestration(
    *,
    response_mode: str,
    recommended_skill: str,
    primary_role: str,
    task_kind_hint: str,
) -> dict[str, str]:
    return {
        "response_mode": response_mode,
        "recommended_skill": recommended_skill,
        "primary_role": primary_role,
        "task_kind_hint": task_kind_hint,
        "routing_policy": _resolve_routing_policy(),
    }


def _score(text: str, roles: list[dict]) -> list[RoleSuggestion]:
    t = _normalize(text)
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    role_map = {r["id"]: r for r in roles}

    # 단순 태그 매칭
    for role in roles:
        rid = role.get("id", "")
        tags: list[str] = role.get("tags", [])
        for tag in tags:
            if tag.lower() in t:
                scores[rid] = scores.get(rid, 0) + 1
                matched.setdefault(rid, []).append(tag)

    # 복합 패턴 보너스 (역할이 scores에 없어도 추가)
    for keywords, rid, bonus in _PATTERN_BONUSES:
        if rid in role_map and all(k in t for k in keywords):
            scores[rid] = scores.get(rid, 0) + bonus

    results: list[RoleSuggestion] = []
    for rid, score in sorted(scores.items(), key=lambda x: (-x[1], x[0])):
        if score > 0 and rid in role_map:
            r = role_map[rid]
            results.append(
                RoleSuggestion(
                    role_id=rid,
                    title=r.get("title", rid),
                    description=r.get("description", ""),
                    score=score,
                    matched_keywords=list(dict.fromkeys(matched.get(rid, []))),
                )
            )

    # 아무것도 안 맞으면 기본값
    if not results:
        fallback_id = "senior_coding"
        r = role_map.get(fallback_id, {"title": "시니어 코딩 비서", "description": "기본 추천"})
        results = [RoleSuggestion(fallback_id, r.get("title", fallback_id), r.get("description", ""), 0, [])]

    return results


def suggest(text: str, top: int = 3, target: Path | None = None) -> list[RoleSuggestion]:
    roles = _load_team_roles(target or Path.cwd())
    return _score(text, roles)[:top]


def suggest_orchestration(text: str, *, target: Path | None = None) -> dict[str, str]:
    normalized = _normalize(text)
    suggestions = suggest(text, top=3, target=target)
    primary_role = suggestions[0].role_id if suggestions else "senior_coding"

    plan_keywords = ("계획", "plan", "설계", "분해", "어떻게 구현", "태스크")
    review_keywords = ("리뷰", "review", "diff", "pr", "코드 봐줘", "검토")
    investigate_keywords = ("버그", "debug", "에러", "error", "원인", "왜 실패")
    ship_keywords = ("배포", "ship", "release", "푸시 준비", "deploy")
    brainstorm_keywords = ("브레인스토밍", "아이디어", "막막", "요구사항 정리")
    benchmark_keywords = ("벤치마크", "benchmark", "비교")
    critique_keywords = ("비판", "critique", "약점", "냉정하게")
    status_keywords = ("상태", "뭐하고 있었", "어디까지", "status")
    lesson_keywords = ("교훈", "lesson", "배운")
    retro_keywords = ("회고", "retro")
    qa_keywords = ("qa", "검수", "체크리스트")
    reentry_keywords = ("오랜만", "복귀", "뭐였지", "reentry", "구조 파악")
    task_keywords = ("구현", "개발", "만들어", "추가", "수정", "커밋", "빌드", "테스트 추가")
    work_breakdown_intent = (
        ("작업" in normalized or "태스크" in normalized)
        and _contains_any(normalized, ("쪼개", "잘게", "우선순위", "진행 순서", "정리"))
    )
    progress_summary_intent = _contains_any(
        normalized,
        ("지금까지 뭐 했", "뭐 했는지 정리", "현재 어떤점이 개선", "어떤점이 개선", "개선된거야"),
    )
    explicit_planning_intent = _contains_any(normalized, plan_keywords)
    explicit_review_intent = _contains_any(normalized, review_keywords)
    explicit_critique_intent = _contains_any(normalized, critique_keywords)
    review_validation_intent = (
        _contains_any(normalized, ("git changes", "변경 상태", "git diff", "현재 변경"))
        and _contains_any(normalized, ("괜찮", "체크", "맞아", "정말", "확인"))
    )
    plan_validation_intent = (
        _contains_any(normalized, ("계획", "plan"))
        and _contains_any(normalized, ("맞아", "맞는지", "제대로", "괜찮", "검증", "확인"))
    )
    explicit_fix_intent = (
        _contains_all(normalized, ("버그", "수정"))
        or _contains_all(normalized, ("bug", "fix"))
        or ("에러" in normalized and "수정" in normalized)
    )
    root_cause_intent = _contains_any(normalized, ("원인", "왜 실패", "재현", "추적", "debug", "디버"))

    if explicit_critique_intent or plan_validation_intent:
        return _build_orchestration(
            response_mode="review-first",
            recommended_skill="$omc-critique",
            primary_role="code_review",
            task_kind_hint="review",
        )
    if explicit_review_intent or review_validation_intent:
        return _build_orchestration(
            response_mode="review-first",
            recommended_skill="$omc-review",
            primary_role="code_review",
            task_kind_hint="review",
        )
    if _contains_any(normalized, ship_keywords):
        return _build_orchestration(
            response_mode="execute-first",
            recommended_skill="$omc-ship",
            primary_role="directive",
            task_kind_hint="ship",
        )
    if explicit_fix_intent and not root_cause_intent:
        return _build_orchestration(
            response_mode="execute-first",
            recommended_skill="$omc-task",
            primary_role="senior_coding",
            task_kind_hint="task",
        )
    if _contains_any(normalized, investigate_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-investigate",
            primary_role="analysis",
            task_kind_hint="investigate",
        )
    if progress_summary_intent and not (
        explicit_planning_intent
        or explicit_review_intent
        or explicit_critique_intent
        or work_breakdown_intent
        or explicit_fix_intent
        or root_cause_intent
    ):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-status",
            primary_role="analysis",
            task_kind_hint="task",
        )
    if work_breakdown_intent:
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-plan",
            primary_role="analysis",
            task_kind_hint="plan",
        )
    if explicit_planning_intent:
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-plan",
            primary_role="analysis",
            task_kind_hint="plan",
        )
    if _contains_any(normalized, brainstorm_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-brainstorm",
            primary_role="analysis",
            task_kind_hint="plan",
        )
    if _contains_any(normalized, benchmark_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-benchmark",
            primary_role="analysis",
            task_kind_hint="plan",
        )
    if _contains_any(normalized, status_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-status",
            primary_role="analysis",
            task_kind_hint="task",
        )
    if _contains_any(normalized, lesson_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-lesson",
            primary_role="analysis",
            task_kind_hint="task",
        )
    if _contains_any(normalized, retro_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-retro",
            primary_role="analysis",
            task_kind_hint="review",
        )
    if _contains_any(normalized, qa_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-qa",
            primary_role="analysis",
            task_kind_hint="review",
        )
    if _contains_any(normalized, reentry_keywords):
        return _build_orchestration(
            response_mode="answer-first",
            recommended_skill="$omc-reentry",
            primary_role="analysis",
            task_kind_hint="plan",
        )
    if _contains_any(normalized, task_keywords):
        return _build_orchestration(
            response_mode="execute-first",
            recommended_skill="$omc-task",
            primary_role="senior_coding",
            task_kind_hint="task",
        )

    fallback_task_kind = "task" if primary_role == "senior_coding" else "plan"
    return _build_orchestration(
        response_mode="answer-first",
        recommended_skill="$omc-plan",
        primary_role=primary_role,
        task_kind_hint=fallback_task_kind,
    )


# ---------------------------------------------------------------------------
# 출력 포맷터
# ---------------------------------------------------------------------------

def _fmt_plain(suggestions: list[RoleSuggestion], text: str) -> str:
    orchestration = suggest_orchestration(text)
    lines = [f'📌 요청 분석: "{text[:60]}{"..." if len(text) > 60 else ""}"', ""]
    lines.append("🤖 추천 역할:")
    for i, s in enumerate(suggestions, 1):
        kws = f"  (매칭: {', '.join(s.matched_keywords[:3])})" if s.matched_keywords else ""
        lines.append(f"  {i}. [{s.role_id}] {s.title}{kws}")
        lines.append(f"     → {s.description}")
    lines += [
        "",
        f"🧭 추천 모드: {orchestration['response_mode']}",
        f"🧩 추천 시작 스킬: {orchestration['recommended_skill']}",
        f"🎯 주역할: {orchestration['primary_role']}",
        f"🧠 task kind 힌트: {orchestration['task_kind_hint']}",
        f"⚖️ 라우팅 정책: {orchestration['routing_policy']}",
        "",
        "─" * 48,
        "확인하려면: 확인 (또는 +role_id, -role_id 로 조정)",
        "역할 변경:  senior_coding,analysis  (쉼표로 직접 지정)",
    ]
    return "\n".join(lines)


def _fmt_json(suggestions: list[RoleSuggestion], text: str) -> str:
    orchestration = suggest_orchestration(text)
    return json.dumps(
        {
            "suggestions": [
                {"role_id": s.role_id, "title": s.title, "score": s.score, "matched": s.matched_keywords}
                for s in suggestions
            ],
            "response_mode": orchestration["response_mode"],
            "recommended_skill": orchestration["recommended_skill"],
            "primary_role": orchestration["primary_role"],
            "task_kind_hint": orchestration["task_kind_hint"],
            "routing_policy": orchestration["routing_policy"],
        },
        ensure_ascii=False,
        indent=2,
    )


def _fmt_ids(suggestions: list[RoleSuggestion]) -> str:
    return ",".join(s.role_id for s in suggestions)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="OMC 역할 자동 추천")
    ap.add_argument("text", nargs="?", default=None, help="분석할 요청 텍스트 (위치 인수)")
    ap.add_argument("--text", dest="text_flag", default=None, help="분석할 요청 텍스트 (플래그)")
    ap.add_argument("--top", type=int, default=3, help="추천 역할 수 (기본: 3)")
    ap.add_argument("--target", type=Path, default=Path.cwd(), help="프로젝트 루트 (team.json 탐색 기준)")
    ap.add_argument(
        "--format",
        choices=["plain", "json", "ids"],
        default="plain",
        help="출력 포맷: plain(기본) | json | ids(쉼표 구분 role_id 목록)",
    )
    args = ap.parse_args()

    text = args.text_flag or args.text
    if not text:
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        else:
            ap.print_help()
            return 1

    if not text:
        print("오류: 분석할 텍스트가 없습니다.", file=sys.stderr)
        return 1

    results = suggest(text, top=args.top, target=args.target)

    if args.format == "json":
        print(_fmt_json(results, text))
    elif args.format == "ids":
        print(_fmt_ids(results))
    else:
        print(_fmt_plain(results, text))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
