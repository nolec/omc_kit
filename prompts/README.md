# Role Prompts (비서 프롬프트)

이 폴더는 프로젝트의 base prompt(예: `PROMPT_COMMON.md` / `PROMPT_PROJECT_<NAME>.md`) 위에
**역할(비서)별로 추가로 얹는 프롬프트 조각**을 모아둡니다.

## 사용 순서(추천)

1) base prompt(프로젝트 공통 규칙/런북)
2) 역할 프롬프트(아래 중 1개 이상)
3) 오늘의 작업 요청(목표/범위/DoD/제약 포함)

## 역할 프롬프트 목록

- `prompts/ROLE_SEARCH_ASSISTANT.md`: 웹/레포/문서 기반 “검색 비서”
- `prompts/ROLE_ANALYSIS_ASSISTANT.md`: 로그/코드/데이터 근거로 가설-검증하는 “분석 비서”
- `prompts/ROLE_CODE_REVIEW_ASSISTANT.md`: PR/diff 중심의 “코드리뷰 비서”
- `prompts/ROLE_SENIOR_CODING_ASSISTANT.md`: 설계/구현/검증을 주도하는 “시니어 코딩 비서”

## 합성(추천)

역할/프로필의 SSOT는 `prompts/team.json`입니다.

```bash
# 고정 조합(profile)
python scripts/compose_prompt.py --profile debugging
python scripts/compose_prompt.py --profile code_review --out /tmp/prompt.md

# 규칙 기반 자동 추천(요청 텍스트 -> roles)
roles=$(python scripts/suggest_roles.py --text "버그 원인 분석하고 수정해줘")
python scripts/compose_prompt.py --roles "$roles"
```

## 자동 역할 선택(추천)

요청 문장만 주면, 역할을 자동 선택하고 오케스트레이터 프롬프트까지 포함해 합성합니다.

```bash
python scripts/auto_prompt.py --base PROMPT_COMMON.md --request "로그인 기능 구현해줘" --out /tmp/prompt.md
```

## 자동 선택 + 컨펌(추천)

```bash
python scripts/auto_prompt.py --confirm --base PROMPT_COMMON.md --request "인증 기능 만들고 싶어" --out /tmp/prompt.md
```

## OMC-style 단일 진입점

```bash
# 단일 작업 요청 (역할 추천 + 컨펌)
python scripts/omc.py "로그인 기능 만들고 싶어"

# Autopilot: 태스크 파일 기반 멀티 LLM 자율 루프
python scripts/omc_autopilot.py new --id feat-x --title "기능 X 구현"
python scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json
python scripts/omc.py autopilot --task-file .omc/tasks/feat-x.json --dry-run

# 팀/인터뷰/상태
python scripts/omc.py team "여러 역할로 나눠서 진행해줘"
python scripts/omc.py deep-interview "요구사항이 아직 애매해"
python scripts/omc.py state status
python scripts/omc.py state compact
```
