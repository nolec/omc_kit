#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", text.strip()).strip("_").lower()
    return slug or "domain"


def _role_id(domain: str) -> str:
    return f"project_{_slug(domain)}"


def _role_filename(role_id: str) -> str:
    return f"ROLE_{role_id.upper()}_ASSISTANT.md"


def _role_title(domain: str) -> str:
    return f"프로젝트 전용: {domain} 비서"


def _role_prompt(domain: str) -> str:
    return f"""# Role Prompt: 프로젝트 전용 {domain} 비서

당신은 이 프로젝트의 `{domain}` 도메인 작업을 돕는 전용 비서다.

## 1) 범위

- 이 도메인의 용어, 입력/출력, 데이터 계약을 프로젝트 SSOT 기준으로 고정한다.
- 공통 OMC 규칙은 `AGENTS.md`와 `.omc/summary.md`를 따른다.
- 도메인 특화 판단은 추측하지 않고, 필요한 경우 확인 질문으로 범위를 좁힌다.

## 2) 산출물

1) 도메인 목표/범위/제약 정리
2) 입력/출력 계약
3) 실행 또는 검증 커맨드
4) 남은 리스크와 다음 액션 1개
"""


def _load_team(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"roles": [], "profiles": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _upsert_by_id(items: list[dict[str, object]], item: dict[str, object]) -> list[dict[str, object]]:
    out = [x for x in items if str(x.get("id")) != str(item.get("id"))]
    out.append(item)
    return out


def init_domain(project_root: Path, *, domain: str, force: bool = False) -> list[Path]:
    project_prompts = project_root / "project_prompts"
    project_prompts.mkdir(parents=True, exist_ok=True)

    role_id = _role_id(domain)
    role_file = _role_filename(role_id)
    role_path = project_prompts / role_file
    if force or not role_path.exists():
        role_path.write_text(_role_prompt(domain), encoding="utf-8")

    team_path = project_prompts / "team.local.json"
    team = _load_team(team_path)
    roles = team.get("roles")
    profiles = team.get("profiles")
    if not isinstance(roles, list):
        roles = []
    if not isinstance(profiles, list):
        profiles = []

    roles = _upsert_by_id(
        roles,
        {
            "id": role_id,
            "title": _role_title(domain),
            "path": role_file,
            "tags": ["project", "domain", _slug(domain)],
        },
    )
    profiles = _upsert_by_id(
        profiles,
        {
            "id": f"{_slug(domain)}_project",
            "title": f"{domain} 프로젝트 프로필",
            "role_ids": ["analysis", "senior_coding", "code_review", role_id],
        },
    )

    payload = {"roles": roles, "profiles": profiles}
    team_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readme_path = project_prompts / "README.md"
    if force or not readme_path.exists():
        readme_path.write_text(
            "# Project OMC Prompts\n\n"
            "- `team.local.json`: 프로젝트 전용 role/profile 오버레이\n"
            "- `ROLE_*_ASSISTANT.md`: 도메인별 전용 역할 프롬프트\n",
            encoding="utf-8",
        )

    return [team_path, role_path, readme_path]


def main() -> int:
    ap = argparse.ArgumentParser(description="Create a project-local OMC domain overlay.")
    sub = ap.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create or update project_prompts/team.local.json and a role prompt.")
    init.add_argument("domain", help="Domain name, e.g. crypto, ipo, legal, support.")
    init.add_argument("--target", type=Path, default=Path.cwd(), help="Target project root.")
    init.add_argument("--force", action="store_true", help="Overwrite the generated role prompt/readme.")
    args = ap.parse_args()

    if args.command == "init":
        written = init_domain(args.target.resolve(), domain=args.domain, force=args.force)
        for path in written:
            print(path)
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
