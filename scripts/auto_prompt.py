#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from omc_state import record_session
except ImportError:  # pragma: no cover - fallback when imported outside the kit path.
    record_session = None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_team(team_path: Path) -> dict:
    return json.loads(team_path.read_text(encoding="utf-8"))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _light_mode_decision(text: str) -> tuple[bool, str]:
    t = _normalize(text)
    if not t:
        return False, "empty_request"
    if len(t) > 80:
        return False, "too_long"
    if len(t.split()) > 12:
        return False, "too_many_words"
    heavy_markers = [
        "구현",
        "개발",
        "refactor",
        "리팩",
        "설계",
        "아키텍처",
        "backtest",
        "verify",
        "train",
        "rebuild",
        "deploy",
        "run-dry",
        "run-crypto",
        "deep-interview",
        "team",
        "ulw",
        "ralph",
        "원인",
        "분석",
        "debug",
        "버그",
        "수정",
        "고쳐",
        "바꿔",
        "추가",
        "삭제",
        "만들",
        "작성",
        "실행",
        "돌려",
        "테스트",
        "검증",
        "커밋",
        "commit",
        "push",
        "설치",
        "setup",
        "세팅",
    ]
    for marker in heavy_markers:
        if marker in t:
            return False, f"matched_strict_marker:{marker}"
    light_phrases = [
        "상태",
        "status",
        "요약",
        "summary",
        "뭐 남",
        "남은",
        "현재 모드",
        "omc 모드",
        "모드야",
        "도움말",
        "help",
        "어디까지",
        "진행상황",
        "진행 상황",
        "확인만",
        "보여줘",
        "알려줘",
    ]
    for phrase in light_phrases:
        if phrase in t:
            return True, f"matched_light_phrase:{phrase}"
    return False, "no_light_phrase"


def _should_use_light_mode(text: str) -> bool:
    return _light_mode_decision(text)[0]


@dataclass(frozen=True)
class ModeSpec:
    prompt_file: str
    title: str
    summary: str
    default_roles: tuple[str, ...]
    default_top: int


MODE_SPECS: dict[str, ModeSpec] = {
    "autopilot": ModeSpec(
        prompt_file="MODE_AUTOPILOT.md",
        title="AUTOPILOT",
        summary="역할 제안 -> 컨펌 -> 계획/구현/검증의 기본 자동 조종 모드.",
        default_roles=("analysis", "senior_coding", "code_review"),
        default_top=4,
    ),
    "team": ModeSpec(
        prompt_file="MODE_TEAM.md",
        title="TEAM",
        summary="공동 작업용 팀 모드. 역할을 분담하고 결과를 합칩니다.",
        default_roles=("search", "analysis", "senior_coding", "code_review"),
        default_top=5,
    ),
    "ulw": ModeSpec(
        prompt_file="MODE_ULTRAWORK.md",
        title="ULTRAWORK",
        summary="최대 병렬화 모드. 파일 소유권을 나누고 동시에 진행합니다.",
        default_roles=("search", "analysis", "senior_coding", "code_review"),
        default_top=6,
    ),
    "ralph": ModeSpec(
        prompt_file="MODE_RALPH.md",
        title="RALPH",
        summary="완료될 때까지 멈추지 않는 검증 루프 모드.",
        default_roles=("search", "analysis", "senior_coding", "code_review"),
        default_top=4,
    ),
    "deep-interview": ModeSpec(
        prompt_file="MODE_DEEP_INTERVIEW.md",
        title="DEEP INTERVIEW",
        summary="먼저 요구사항을 깊게 캐묻고 불확실성을 줄이는 모드.",
        default_roles=("search", "analysis"),
        default_top=3,
    ),
}


def _mode_spec(mode: str) -> ModeSpec:
    if mode not in MODE_SPECS:
        known = ", ".join(sorted(MODE_SPECS))
        raise ValueError(f"Unknown mode: {mode}. Known: {known}")
    return MODE_SPECS[mode]


def _detect_mode(text: str) -> str:
    t = _normalize(text)
    if any(k in t for k in ["deep-interview", "deep interview", "심층면담", "심층 인터뷰", "깊게 물어", "질문부터"]):
        return "deep-interview"
    if any(k in t for k in ["ulw", "ultrawork", "ultra work", "최대 병렬", "병렬로"]):
        return "ulw"
    if "ralph" in t or "멈추지 말고" in t or "끝날 때까지" in t:
        return "ralph"
    if "team" in t or "팀 모드" in t or "협업" in t or "공동 작업" in t:
        return "team"
    if "autopilot" in t or "/autopilot" in t:
        return "autopilot"
    return "autopilot"


def _score_roles(text: str) -> dict[str, int]:
    t = _normalize(text)
    scores: dict[str, int] = {}

    def bump(role_id: str, n: int = 1) -> None:
        scores[role_id] = scores.get(role_id, 0) + n

    # Project safety: when the request smells like trading/crypto, bias towards risk.
    # Safe in non-trading repos because unknown role ids are filtered out later.
    if any(
        k in t
        for k in [
            "코인",
            "크립토",
            "crypto",
            "trading",
            "트레이딩",
            "선물",
            "perp",
            "swap",
            "okx",
            "binance",
            "거래소",
            "매매",
        ]
    ):
        bump("risk", 3)

    if any(k in t for k in ["리뷰", "code review", "review", "diff", "pr", "머지", "merge", "검토", "점검"]):
        bump("code_review", 3)

    if any(k in t for k in ["디버", "debug", "버그", "error", "exception", "trace", "stack", "재현", "원인", "분석", "파악", "확인", "조사"]):
        bump("analysis", 3)
        bump("senior_coding", 1)

    if any(k in t for k in ["구현", "개발", "refactor", "리팩", "feature", "추가", "설계", "api", "모듈", "기능"]):
        bump("senior_coding", 3)

    if any(k in t for k in ["체결", "maker", "taker", "스프레드", "spread", "슬리피지", "slippage", "호가", "latency", "rate limit", "수수료"]):
        bump("microstructure", 3)
        bump("risk", 2)

    if any(k in t for k in ["레짐", "regime", "심리", "군중", "뉴스", "news", "이벤트", "흐름", "flow", "state", "시장"]):
        bump("behavioral", 3)
        bump("analysis", 1)

    if any(k in t for k in ["리스크", "risk", "한도", "limit", "kill", "킬", "중단", "폭주", "손실", "dd", "drawdown"]):
        bump("risk", 3)

    if any(k in t for k in ["문서", "docs", "레퍼", "reference", "찾아", "검색", "spec", "스펙", "조사", "탐색", "알아봐"]):
        bump("search", 3)

    return scores


def _score_role_metadata(text: str, roles: dict[str, dict[str, object]]) -> dict[str, int]:
    t = _normalize(text)
    scores: dict[str, int] = {}
    if not t:
        return scores
    for role_id, meta in roles.items():
        tokens: set[str] = set()
        tokens.add(_normalize(role_id))
        tokens.add(_normalize(role_id.removeprefix("project_")))
        title = str(meta.get("title", ""))
        if title:
            tokens.add(_normalize(title))
        path = Path(str(meta.get("path", ""))).stem
        if path:
            tokens.add(_normalize(path))
            tokens.add(_normalize(path.removeprefix("ROLE_PROJECT_").removesuffix("_ASSISTANT")))
        tags = meta.get("tags", [])
        if isinstance(tags, list):
            tokens.update(_normalize(str(tag)) for tag in tags)
        for token in sorted(x for x in tokens if len(x) >= 3):
            if token in t:
                scores[role_id] = max(scores.get(role_id, 0), 4 if role_id.startswith("project_") else 2)
    return scores


def _merge_teams(
    team_paths: list[Path],
) -> tuple[dict[str, dict[str, object]], set[str], dict[str, list[str]]]:
    roles: dict[str, dict[str, object]] = {}
    known_roles: set[str] = set()
    profiles: dict[str, list[str]] = {}

    for team_path in team_paths:
        team_path = team_path.resolve()
        team = _load_team(team_path)
        base_dir = team_path.parent

        for r in team.get("roles", []):
            rid = r.get("id")
            p = r.get("path")
            if not rid or not p:
                continue
            rid_s = str(rid)
            known_roles.add(rid_s)
            rp = Path(p)
            roles[rid_s] = {
                "path": (rp if rp.is_absolute() else (base_dir / rp).resolve()),
                "title": r.get("title", rid_s),
                "tags": r.get("tags", []),
            }

        for p in team.get("profiles", []):
            pid = p.get("id")
            role_ids = p.get("role_ids")
            if not pid or not isinstance(role_ids, list):
                continue
            profiles[str(pid)] = [str(x) for x in role_ids]

    return roles, known_roles, profiles


def _role_paths(roles: dict[str, dict[str, object]], role_ids: list[str]) -> list[Path]:
    unknown = [r for r in role_ids if r not in roles]
    if unknown:
        known = ", ".join(sorted(roles.keys()))
        raise ValueError(f"Unknown role id(s): {unknown}. Known: {known}")
    return [Path(str(roles[r]["path"])) for r in role_ids]


def _profile_role_ids(profiles: dict[str, list[str]], profile_id: str) -> list[str]:
    if profile_id in profiles:
        return list(profiles[profile_id])
    known = ", ".join(sorted(profiles.keys()))
    raise ValueError(f"Unknown profile id: {profile_id}. Known: {known}")


def _kit_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _default_team_paths() -> list[Path]:
    """
    Prefer project-local installed team.json if present, otherwise fall back to kit's team.json.
    """
    cwd = Path.cwd()
    project_team = cwd / "prompts" / "team.json"
    if project_team.exists():
        return [project_team.resolve()]
    kit = _kit_root()
    return [(kit / "prompts" / "team.json").resolve()]


def _default_base_paths(context_mode: str = "full") -> list[Path]:
    """
    Prefer project root (cwd) base prompt files when present.
    """
    cwd = Path.cwd()
    out: list[Path] = []
    common_name = "PROMPT_COMMON_LEAN.md" if context_mode == "lean" else "PROMPT_COMMON.md"
    common = cwd / common_name
    if not common.exists() and context_mode == "lean":
        common = cwd / "PROMPT_COMMON.md"
    if common.exists():
        out.append(common.resolve())
    else:
        kit_common = _kit_root() / "templates" / common_name
        if kit_common.exists():
            out.append(kit_common.resolve())
        elif context_mode == "lean":
            kit_common_fb = _kit_root() / "templates" / "PROMPT_COMMON.md"
            if kit_common_fb.exists():
                out.append(kit_common_fb.resolve())
    project_candidates = sorted(cwd.glob("PROMPT_PROJECT*.md"))
    project = project_candidates[0] if project_candidates else (cwd / "PROMPT_1.md")
    if project.exists():
        out.append(project.resolve())
    omc_summary = cwd / ".omc" / "summary.md"
    omc_notepad = cwd / ".omc" / "notepad.md"
    if omc_summary.exists():
        out.append(omc_summary.resolve())
    elif omc_notepad.exists():
        out.append(omc_notepad.resolve())
    return out


def _split_base_paths(base_paths: list[Path]) -> tuple[list[Path], list[Path]]:
    summary_like: list[Path] = []
    other: list[Path] = []
    for path in base_paths:
        if path.name in {"summary.md", "notepad.md"} and ".omc" in str(path):
            summary_like.append(path)
        else:
            other.append(path)
    return other, summary_like


def _clean_markdown_block(text: str) -> str:
    lines = text.splitlines()
    cleaned: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            continue
        if stripped == "":
            if prev_blank:
                continue
            prev_blank = True
            cleaned.append("")
            continue
        prev_blank = False
        cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip()


def _extract_heading_block(text: str, *, heading: str, stop_at: list[str] | None = None) -> str:
    lines = text.splitlines()
    start_idx: int | None = None
    level = 0
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start_idx = i
            level = len(heading) - len(heading.lstrip("#"))
            break
    if start_idx is None:
        return ""
    stop_set = set(stop_at or [])
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped in stop_set:
            end_idx = i
            break
        if stripped.startswith("#"):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                end_idx = i
                break
    return _clean_markdown_block("\n".join(lines[start_idx:end_idx]))


def _document_preface(text: str) -> str:
    lines = text.splitlines()
    preface: list[str] = []
    for line in lines:
        if line.startswith("## "):
            break
        preface.append(line)
    return _clean_markdown_block("\n".join(preface))


def _join_markdown_blocks(blocks: list[str]) -> str:
    return "\n\n---\n\n".join(block for block in blocks if block.strip()).strip()


def _lean_common_block(path: Path) -> str:
    text = _read_text(path)
    return _join_markdown_blocks(
        [
            _extract_heading_block(
                text,
                heading="## 1) 작업 요청 템플릿",
                stop_at=["### 1.2 정밀 입력"],
            ),
            _extract_heading_block(text, heading="## 2) 프로젝트 계약"),
            _extract_heading_block(text, heading="## 3) 질문 트리거"),
            _extract_heading_block(text, heading="## 4) 실행 원칙"),
            _extract_heading_block(text, heading="## 5) 결과 보고 포맷"),
        ],
    )


def _lean_project_block(path: Path) -> str:
    text = _read_text(path)
    return _join_markdown_blocks(
        [
            _extract_heading_block(text, heading="## 1) Role (이 작업에서의 정체성)"),
            _extract_heading_block(text, heading="## 2) 비협상 원칙(특히 중요한 것만)"),
            _extract_heading_block(
                text,
                heading="## 3) 저장소 계약(구조/커맨드/검증)",
                stop_at=["### 3.2 실행/검증 런북(기본)"],
            ),
            _extract_heading_block(text, heading="## 5) 트레이딩/데이터 무결성 규칙(도메인 핵심)"),
            _extract_heading_block(text, heading="## 6) 컨텍스트 절약(세션 운영)"),
        ],
    )


def _lean_mode_block(path: Path, *, confirmed: bool) -> str:
    text = _read_text(path)
    blocks = [
        _extract_heading_block(text, heading="## 0) 기본 언어"),
        _extract_heading_block(text, heading="## 1) Gate 0 — 입력 충족 체크(필수)"),
    ]
    if not confirmed:
        blocks.append(_extract_heading_block(text, heading="## 2) Gate 1 — 역할 제안 및 컨펌(필수)"))
    blocks.extend(
        [
            _extract_heading_block(text, heading="## 3) Gate 2 — 설계/계획(필수)"),
            _extract_heading_block(text, heading="## 4) Gate 3 — 구현(필수)"),
            _extract_heading_block(text, heading="## 5) Gate 4 — 검증(필수)"),
            _extract_heading_block(text, heading="## 6) Gate 5 — 리뷰/리스크 점검(권장)"),
            _extract_heading_block(text, heading="## 7) 최종 보고 포맷(고정)"),
        ]
    )
    return _join_markdown_blocks(blocks)


def _lean_orchestrator_block(path: Path) -> str:
    text = _read_text(path)
    return _join_markdown_blocks(
        [
            _extract_heading_block(text, heading="## 1) 입력이 부족하면 멈춰야 하는 조건"),
            _extract_heading_block(text, heading="## 1.1) 기본 언어(중요)"),
            _extract_heading_block(text, heading="## 2) 역할 선택 규칙(자동)"),
            _extract_heading_block(text, heading="## 3) 출력 포맷(고정)"),
        ],
    )


def _light_summary_block(path: Path) -> str:
    text = _read_text(path)
    return _join_markdown_blocks(
        [
            _document_preface(text),
            _extract_heading_block(text, heading="## Key Context"),
            _extract_heading_block(text, heading="## Recent Outcomes"),
            _extract_heading_block(text, heading="## Notes"),
            _extract_heading_block(text, heading="## Token Policy"),
        ],
    )


def _light_runtime_block(*, mode_cfg: ModeSpec, routing_reason: str) -> str:
    return _join_markdown_blocks(
        [
            "## Light Mode\n\n"
            "- 현재 요청은 짧고 단순한 OMC 요청으로 분류한다.\n"
            + f"- routing_reason: `{routing_reason}`\n"
            "- 역할 컨펌은 이미 완료된 것으로 간주하고 다시 요청하지 않는다.\n"
            "- 과도한 계획/역할 설명보다 바로 답하거나 필요한 최소 실행만 제안한다.\n"
            "- 요청이 구현/실행/상태 변경으로 확장되면 그 시점에만 OMC 게이트를 다시 강화한다.",
            "## Response Contract\n\n"
            "- 사용자에게는 한글로 짧고 직접적으로 답한다.\n"
            "- 불필요한 역할 열거, 장문 계획, 중복 컨텍스트 요약을 생략한다.\n"
            "- 상태 변경이 필요하면 OMC guard/confirmed 규칙은 유지한다.",
            "# Auto-Selected Mode\n\n"
            + f"- mode: {mode_cfg.title}\n"
            + f"- summary: {mode_cfg.summary}\n"
            + "- friction: light\n",
        ],
    )


def _default_project_overlay_team() -> list[Path]:
    cwd = Path.cwd()
    p = cwd / "project_prompts" / "team.local.json"
    return [p.resolve()] if p.exists() else []


def _dedupe_keep_order(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _apply_role_edits(role_ids: list[str], edit: str, *, known: set[str]) -> list[str]:
    """
    Edit format:
      - full replace: "a,b,c"
      - incremental: "+a,-b,+c"
    """
    edit = edit.strip()
    if not edit:
        return role_ids

    tokens = [t.strip() for t in edit.split(",") if t.strip()]
    if any(t.startswith(("+", "-")) for t in tokens):
        cur = list(role_ids)
        cur_set = set(cur)
        for t in tokens:
            if len(t) < 2:
                continue
            op = t[0]
            rid = t[1:].strip()
            if rid and rid not in known:
                raise ValueError(f"Unknown role id: {rid}")
            if op == "+" and rid and rid not in cur_set:
                cur.append(rid)
                cur_set.add(rid)
            if op == "-" and rid and rid in cur_set:
                cur = [x for x in cur if x != rid]
                cur_set = set(cur)
        return _dedupe_keep_order(cur)

    # full replace
    out = []
    for rid in tokens:
        if rid not in known:
            raise ValueError(f"Unknown role id: {rid}")
        out.append(rid)
    return _dedupe_keep_order(out)


def _confirm_roles(
    role_ids: list[str],
    *,
    roles: dict[str, dict[str, object]],
    known: set[str],
    style: str = "full",
) -> tuple[list[str], bool]:
    compact = style == "compact"
    print("")
    if compact:
        print(f"추천 역할: {', '.join(role_ids)}")
        print("Enter=그대로 | +role,-role | role1,role2")
    else:
        print("추천 비서(역할) 목록:")
        for rid in role_ids:
            title = str(roles.get(rid, {}).get("title", rid))
            print(f"- {rid}: {title}")
        print("")
        print("수정 방법:")
        print("- 그대로 진행: Enter")
        print("- 전체 교체: role1,role2,role3")
        print("- 추가/삭제: +추가할_role_id,-삭제할_role_id")
        print("")

    while True:
        try:
            raw = input("Roles> ").strip()
        except EOFError:
            # Non-interactive environment: fall back to defaults.
            print("[경고] --confirm 입력을 받을 수 없어(비대화형 환경) 추천 역할 그대로 진행합니다.")
            return role_ids, False
        if raw == "":
            return role_ids, True
        try:
            edited = _apply_role_edits(role_ids, raw, known=known)
        except ValueError as e:
            print(f"[오류] {e}")
            continue
        if not edited:
            print("[오류] 역할 목록은 비워둘 수 없습니다.")
            continue
        print("")
        if compact:
            print(f"적용 역할: {', '.join(edited)}")
        else:
            print("최종 역할:")
            for rid in edited:
                title = str(roles.get(rid, {}).get("title", rid))
                print(f"- {rid}: {title}")
        try:
            ok = input("확정 [Y/n]> " if compact else "확정할까요? [Y/n] ").strip().lower()
        except EOFError:
            print("[경고] 컨펌 입력을 받을 수 없어 현재 역할로 진행합니다.")
            return edited, False
        if ok in {"", "y", "yes"}:
            return edited, True


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-select roles and compose a single prompt for LLM usage.")
    ap.add_argument(
        "--team",
        type=Path,
        action="append",
        default=[],
        help="Team JSON file path (repeatable). Later files override earlier ones by id.",
    )
    ap.add_argument(
        "--base",
        type=Path,
        action="append",
        default=[],
        help="Base prompt markdown file(s) to prepend (repeatable).",
    )
    ap.add_argument("--profile", type=str, default=None, help="Profile id in team.json.")
    ap.add_argument("--roles", type=str, default=None, help="Comma-separated role ids in team.json.")
    ap.add_argument("--top", type=int, default=None, help="When auto-selecting, how many roles to include.")
    ap.add_argument(
        "--mode",
        type=str,
        default="auto",
        choices=["auto", "autopilot", "team", "ulw", "ralph", "deep-interview"],
        help="Execution mode. `auto` detects the mode from request text.",
    )
    ap.add_argument("--request", type=str, default=None, help="User request text to append.")
    ap.add_argument("--request-file", type=Path, default=None, help="Read user request from file.")
    ap.add_argument("--confirm", action="store_true", help="Ask to confirm/edit suggested roles before composing.")
    ap.add_argument("--assume-confirm", action="store_true", help="Confirm the recommended roles automatically.")
    ap.add_argument(
        "--friction-mode",
        choices=["auto", "strict", "light"],
        default="auto",
        help="Control OMC overhead. `light` skips confirm and shrinks prompt for short/simple requests.",
    )
    ap.add_argument(
        "--context-mode",
        type=str,
        choices=["full", "lean"],
        default="full",
        help="How much static prompt context to include.",
    )
    ap.add_argument(
        "--confirm-style",
        type=str,
        choices=["full", "compact"],
        default="full",
        help="How to render the interactive role confirmation UI.",
    )
    ap.add_argument("--out", type=Path, default=None, help="Write composed prompt to this path (optional).")
    ap.add_argument("--quiet-write", action="store_true", help="Do not print the output path when --out is used.")
    args = ap.parse_args()

    if args.request is None and args.request_file is None:
        raise SystemExit("Provide --request or --request-file")
    request_text = args.request if args.request is not None else args.request_file.read_text(encoding="utf-8")
    auto_light, routing_reason = _light_mode_decision(request_text)
    if args.friction_mode == "light":
        routing_reason = "forced_light"
    elif args.friction_mode == "strict":
        routing_reason = "forced_strict"
    light_mode = args.friction_mode == "light" or (
        args.friction_mode == "auto"
        and not args.roles
        and args.profile is None
        and auto_light
    )
    resolved_friction = "light" if light_mode else "strict"
    active_mode = _detect_mode(request_text) if args.mode == "auto" else args.mode
    mode_cfg = _mode_spec(active_mode)

    team_paths = list(args.team) if args.team else _default_team_paths()
    # Auto-include project overlay if present (unless user explicitly managed --team list)
    if not args.team:
        team_paths.extend(_default_project_overlay_team())

    for tp in team_paths:
        if not tp.exists():
            raise FileNotFoundError(tp)
    roles, known_roles, profiles = _merge_teams(team_paths)

    base_paths = [Path(p).resolve() for p in (list(args.base) if args.base else _default_base_paths(args.context_mode))]
    omc_summary = Path.cwd() / ".omc" / "summary.md"
    omc_notepad = Path.cwd() / ".omc" / "notepad.md"
    if omc_summary.exists():
        if omc_summary.resolve() not in base_paths:
            base_paths.append(omc_summary.resolve())
    elif omc_notepad.exists() and omc_notepad.resolve() not in base_paths:
        base_paths.append(omc_notepad.resolve())

    role_ids: list[str] = []
    if args.profile is not None:
        role_ids.extend(_profile_role_ids(profiles, args.profile))
    if args.roles is not None:
        role_ids.extend([r.strip() for r in args.roles.split(",") if r.strip()])

    if not role_ids:
        scores = _score_roles(request_text)
        for rid, score in _score_role_metadata(request_text, roles).items():
            scores[rid] = scores.get(rid, 0) + score
        scored = [(rid, s) for rid, s in scores.items() if rid in known_roles and s > 0]
        scored.sort(key=lambda x: (-x[1], x[0]))
        top_n = args.top if args.top is not None else mode_cfg.default_top
        if light_mode:
            top_n = min(int(top_n), 2)
        if scored:
            if light_mode:
                role_ids = [rid for rid, _ in scored[: max(1, int(top_n))]]
            else:
                role_ids = list(mode_cfg.default_roles)
                role_ids.extend([rid for rid, _ in scored[: max(1, int(top_n))]])
        else:
            if light_mode:
                role_ids = list(mode_cfg.default_roles[:1]) or ["analysis"]
            else:
                role_ids = list(mode_cfg.default_roles) or ["senior_coding", "code_review"]

    role_ids = _dedupe_keep_order(role_ids)
    confirmed = False
    effective_context_mode = "lean" if light_mode and args.context_mode == "full" else args.context_mode
    if light_mode:
        confirmed = True
    elif args.assume_confirm:
        confirmed = True
    elif args.confirm:
        role_ids, confirmed = _confirm_roles(
            role_ids,
            roles=roles,
            known=known_roles,
            style=args.confirm_style,
        )

    # Mode prompt (pipeline / gates)
    kit = _kit_root()
    role_paths = _role_paths(roles, role_ids)
    mode_prompt = kit / "prompts" / _mode_spec(active_mode).prompt_file
    if not mode_prompt.exists():
        raise FileNotFoundError(mode_prompt)
    orchestrator = kit / "prompts" / "ROLE_ORCHESTRATOR.md"
    if not orchestrator.exists():
        raise FileNotFoundError(orchestrator)

    parts: list[str] = []
    if light_mode:
        base_docs, summary_docs = _split_base_paths(base_paths)
        if summary_docs:
            for p in summary_docs:
                if not p.exists():
                    raise FileNotFoundError(p)
                parts.append(_light_summary_block(p).rstrip())
        else:
            for p in base_docs[-1:]:
                if not p.exists():
                    raise FileNotFoundError(p)
                parts.append(_read_text(p).rstrip())
        parts.append(_light_runtime_block(mode_cfg=mode_cfg, routing_reason=routing_reason).rstrip())
    elif effective_context_mode == "lean":
        base_docs, summary_docs = _split_base_paths(base_paths)
        common_doc = base_docs[0] if base_docs else None
        project_doc = base_docs[1] if len(base_docs) > 1 else None
        if summary_docs:
            for p in summary_docs:
                if not p.exists():
                    raise FileNotFoundError(p)
                parts.append(_read_text(p).rstrip())
        else:
            for p in base_docs[-1:]:
                if not p.exists():
                    raise FileNotFoundError(p)
                parts.append(_read_text(p).rstrip())
        if common_doc is not None and common_doc.exists():
            if common_doc.name == "PROMPT_COMMON_LEAN.md":
                parts.append(_read_text(common_doc).rstrip())
            else:
                parts.append(_lean_common_block(common_doc).rstrip())
        if project_doc is not None and project_doc.exists():
            parts.append(_lean_project_block(project_doc).rstrip())
        parts.extend(
            [
                _lean_mode_block(mode_prompt, confirmed=confirmed).rstrip(),
                _lean_orchestrator_block(orchestrator).rstrip(),
            ]
        )
    else:
        for p in [*base_paths, mode_prompt, orchestrator]:
            if not p.exists():
                raise FileNotFoundError(p)
            parts.append(_read_text(p).rstrip())

    for p in role_paths:
        if not p.exists():
            raise FileNotFoundError(p)
        parts.append(_read_text(p).rstrip())

    if not light_mode:
        parts.append(
            "# Auto-Selected Mode\n\n"
            + f"- mode: {active_mode}\n"
            + f"- title: {mode_cfg.title}\n"
            + f"- summary: {mode_cfg.summary}\n"
        )
    parts.append(
        "# Auto-Selected Roles\n\n"
        + f"- role_ids: {','.join(role_ids)}\n"
        + "- note: role prompts for these ids are included in this prompt.\n"
    )
    parts.append(
        "# Routing Decision\n\n"
        + f"- requested_friction: {args.friction_mode}\n"
        + f"- resolved_friction: {resolved_friction}\n"
        + f"- reason: {routing_reason}\n"
    )
    if confirmed:
        parts.append(
            "# Runtime Override\n\n"
            + "중요: OMC 로컬 역할 컨펌이 이미 완료되었다. 아래 지시가 이전 모드/오케스트레이터 지시보다 우선한다.\n\n"
            + "- Gate 1(역할 제안 및 컨펌)은 이미 충족되었다.\n"
            + "- 사용자에게 역할 컨펌을 다시 요청하지 않는다.\n"
            + "- `추천 비서(초안):`, `컨펌 요청:` 섹션은 다시 출력하지 않는다.\n"
            + "- 바로 `컨펌된 비서:`와 `통합 실행 계획(게이트):`부터 시작한다.\n"
            + "- 사용자가 추가 입력을 주지 않은 이상 `그대로`, `+추가,-삭제`, `전체 교체` 같은 응답을 다시 요구하지 않는다.\n"
        )
    parts.append("# Task Request\n\n" + request_text.strip())
    composed = "\n\n---\n\n".join(parts).rstrip() + "\n"

    if args.out is None:
        print(composed, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(composed, encoding="utf-8")
        if not args.quiet_write:
            print(f"Wrote: {args.out}")

    if record_session is not None:
        try:
            project_root = Path.cwd()
            session = record_session(
                project_root,
                mode=active_mode,
                title=mode_cfg.title,
                request=request_text,
                role_ids=role_ids,
                prompt_path=str(args.out) if args.out is not None else None,
                base_paths=[str(p) for p in base_paths],
                team_paths=[str(p) for p in team_paths],
                confirmed=confirmed,
                confirmation_source=(
                    "light_mode"
                    if light_mode
                    else ("assume_confirm" if args.assume_confirm else ("interactive_confirm" if confirmed else None))
                ),
                routing={
                    "requested_friction": args.friction_mode,
                    "resolved_friction": resolved_friction,
                    "reason": routing_reason,
                    "light_mode": light_mode,
                },
            )
            print(
                "[OMC] session recorded: "
                + str(session.get("session_id", "unknown"))
                + f" (confirmed={session.get('confirmation', {}).get('status') == 'confirmed'})"
            )
        except Exception as exc:  # pragma: no cover - state recording should not break prompt composition.
            print(f"[warn] omc state record skipped: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
