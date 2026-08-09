---
skill_name: omc-plan
description: "계획·설계·TDD 분해. 트리거: 계획해줘, 설계해줘, 태스크 나눠줘. 범위·DoD 확정."
---

# OMC 설계·계획

`docs/orchestration_usage.md`: 고정 범위는 fast, 불명확하면 normal.

## Phase 0. 상태 확인
주입된 OMC 세션 문맥 우선. 동기화되어 있으면 상태 명령을 실행하지 않습니다. `scripts/omc.py`가 제공된 정확한 context path에 없으면 격리 입력으로 보고 탐색 생략. 문맥이 없거나 오래됐고 경로가 명시된 경우만 실행한다.
```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-plan" --request "<현재 작업 한 줄 요약>" --roles analysis
```
AGENTS.md Tier 1 요구사항은 CONTRACT 입력으로 사용한다.

## Phase 1. CONTRACT
목표 / 범위 (포함) / 범위 (제외) / DoD / 제약 / 사용자 컨펌을 고정한다. 사용자에게 보여줄 단계: CONTRACT·최소 설계·TDD 태스크·handoff | 시스템이 암묵적으로 처리: 재안내·반복 코칭.

## Phase 2. 최소 설계
입력 / 출력 / 성공 지표 / 실패 정책 / 영향받는 파일. `decision / risk / next_action / policy_profile`: 진행 가능 여부 / 변경 위험도 / 다음 스킬 1개 / cost-quality 기본 추천. `policy_reason_summary / policy_confidence`도 출력한다.
공통 결정표: stage=plan / outcome=unresolved|ready / user_selection_needed=yes|no | confidence=low → balanced + user_selection_needed=yes.
- `plan full`: CONTRACT + 최소 설계 + 다중 TDD 태스크. 새 파일·신규 파일/API·시그니처 변경/3개 이상 파일/검증 명령 축약 불가/범위 불명확.
- `plan lite`: CONTRACT + 최소 설계 + 태스크 2개 이하. 기존 파일 중심, 검증 명령 1개, 범위를 한 문장으로 설명 가능할 때만 사용한다. 애매하거나 설명이 약하면 full 재계획한다.
dirty 변경과 계획 범위는 분리한다.

## Phase 3. TDD 태스크 분해·근거 추출
- 격리 benchmark는 runner가 주입한 frozen context만 사용, shell·추가 tool 호출 없이 즉시 구조화 결과 작성. 구조화 출력 중 todo_list·planning tool 생성 금지. CONTRACT·handoff 없이 schema 필드만 반환하며 요구사항·scope·task 중복 금지, 항목별 한 문장·짧은 ID를 쓴다.
- 일반 실행은 근거 파일은 한 번의 명령으로 직접 읽고 진행 메시지·pwd·재탐색 금지. 코드 symbol·사용자 관찰 동작·실패 경로를 매핑하며 근거 매핑 완료 전 lite 금지.
- 선택된 객체의 동작은 ID·수신자 전달을 상태·payload까지 추적해 supports·VERIFY에 연결한다. 요구사항은 짧은 ID로 고정하고 supports에는 ID만 재사용한다.
- 직접 인접한 사용자 관찰 가능 비대상 동작은 surface당 1개 보존한다. 포괄 표현 금지·확인하지 않은 예시 명령 금지. 같은 target과 검증 목적이고 의존성 경계를 넘지 않을 때만 병합하며 검증 경계가 다르면 요구사항 ID 분리, assumption이 필수 데이터 경로 검증을 대체 금지.
- assumptions는 요청·frozen context가 명시적으로 허용한 근거만 기록한다. 비차단 불확실성은 생략하고 구현을 막는 불확실성만 decisions_required. assumptions·decisions는 구현을 막는 항목만 남기고 반복 설명 금지·후속 제안 금지.
```text
태스크 N: [기능] / RED: [실패 테스트 파일+케이스] / GREEN: [최소 구현 파일] / VERIFY: [검증 커맨드]
```

## Phase 4. 세션 기록
사용자 컨펌 완료 전에는 `python3 scripts/omc.py state confirm --target .`를 실행하지 않으며, confirm 후에만 `$omc-task`로 넘긴다.

## 다음 추천
- 우선순위는 현재 병목 > 기본 파이프라인이며 주추천 1개만 제시한다. 새 파일/API 변경/3개 이상 파일 같은 고위험이면 먼저 `$omc-critique`.
- outcome=ready + user_selection_needed=no + 범위 고정 + 컨펌 완료면 `$omc-task`.
- outcome=unresolved + risk=high 또는 범위 불명확이면 `$omc-critique`.
- outcome=unresolved + risk=low + user_selection_needed=yes면 `$omc-office-hours`.
- 사용자가 설계만 확인 중이거나 다음 단계를 아직 고르지 않음 → 사용자 선택 대기. 자동으로 진행하지는 않습니다.

## ⛔ 자동 진입 금지
완료 후 사용자가 다음 스킬을 명시할 때까지 멈춘다.
