# OMC Plan Workflow

## Phase 0
주입된 OMC 세션 문맥 우선. 동기화되어 있으면 상태 명령을 실행하지 않습니다. `scripts/omc.py`가 제공된 정확한 context path 밖은 탐색 생략. 문맥 없거나 오래되면:
```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-plan" --request "<현재 작업 한 줄 요약>" --roles analysis
```
AGENTS.md Tier 1 → CONTRACT 입력.

## Phase 1. CONTRACT
목표/범위 (포함)/범위 (제외)/DoD/제약/사용자 컨펌. 사용자에게 보여줄 단계. 시스템이 암묵적으로 처리.

## Phase 2. 설계
입력/출력/성공 지표/실패 정책/영향받는 파일.
`decision / risk / next_action / policy_profile` = 진행 가능 여부 / 변경 위험도 / 다음 스킬 1개 / cost-quality 기본 추천. `policy_reason_summary / policy_confidence`.
공통 결정표: stage=plan / outcome=unresolved|ready / user_selection_needed=yes|no; confidence=low → balanced + user_selection_needed=yes.
- `plan full`: 새 파일·신규 파일/API·시그니처 변경/3개 이상 파일/검증 명령 축약 불가/범위 불명확.
- `plan lite`: 기존 파일·검증 명령 1개·범위 한 문장·태스크 2개 이하만; 애매하면 full 재계획.
dirty 변경과 계획 범위 분리.

## Phase 3. TDD 태스크·근거 추출
- 압축 전에 원자 requirement ledger를 고정하고 이후 ID를 다시 정의하거나 병합하지 않는다. 데이터 공급, 소비자 동작, 회귀 검증처럼 증거가 다른 항목은 검증 경계별 독립 ID로 유지한다.
- 출력 전 모든 requirement ID가 task.supports와 VERIFY에 연결됐는지 확인한다. request/response 계약, member/guest 분기, filter query처럼 독립적으로 실패할 수 있는 경계를 포괄 표현에 흡수하지 않는다.
- 요청·문맥에서 별도 증거로 확인되는 회귀 테스트 산출물도 독립 requirement ID로 보존하고 구현 requirement의 VERIFY로 흡수하지 않는다. 구현과 테스트가 task를 공유해도 공유 task의 supports에 각 ID를 모두 유지한다.
- 구조화 출력은 schema 필드만 사용하고 요구사항·scope·task 중복 금지, todo_list·planning tool 생성 금지. 반복 설명 금지, 항목별 한 문장.
- 근거 파일은 한 번의 명령으로 직접 읽고 진행 메시지·pwd·재탐색 금지. 코드 symbol·사용자 관찰 동작·실패 경로 근거 매핑 완료 전 lite 금지.
- 선택된 객체: ID·수신자 → 상태·payload → supports·VERIFY. 요구사항은 짧은 ID; supports에는 ID만.
- 직접 인접 사용자 관찰 가능 동작은 surface당 1개. 포괄 표현 금지·확인하지 않은 예시 명령 금지. 같은 target과 검증 목적이고 의존성 경계를 넘지 않을 때만 병합.
- 필수 데이터 경로: source/model → adapter/state → consumer/UI. 병합 전 각 requirement는 최소 하나의 task와 VERIFY 연결; 검증 경계가 다르면 요구사항 ID 분리; assumption이 필수 데이터 경로 검증을 대체 금지.
- 사용자 관찰 동작은 직접 surface 회귀 테스트 task/VERIFY로 연결한다. 데이터 경로 테스트는 surface 회귀 테스트를 대체하지 않는다.
- assumptions는 명시적으로 허용한 근거만. 비차단 불확실성은 생략; 구현을 막는 불확실성만 decisions_required, 구현을 막는 항목만.
```text
태스크 N: [기능] / RED: [실패 테스트 파일+케이스] / GREEN: [최소 구현 파일] / VERIFY: [검증 커맨드]
```

## Phase 4
사용자 컨펌 완료 전 `python3 scripts/omc.py state confirm --target .` 금지. confirm 후 `$omc-task`.

## Machine output contract — 마지막 두 줄은 `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT: <VALUE>`; JSON은 `schema_version=omc-output/v1`, `stage`, `outcome`, `risk`, `next_skill`, `user_selection_needed`, `reason_code`; `next_skill`은 canonical `omc-*` 또는 null; unresolved/blocked는 `reason_code` 필수; legacy 평문 입력은 허용하되 새 출력은 숨김 형식만 사용하고 명시적 오류는 보정하지 않습니다.

## 다음 추천
- 주추천 1개; 우선순위: 현재 병목 > 기본 파이프라인.
- 새 파일/API 변경/3개 이상 파일 같은 고위험이면 먼저 `$omc-critique`.
- outcome=ready + user_selection_needed=no + 범위 고정 + 컨펌 완료면 `$omc-task`.
- outcome=unresolved + risk=high 또는 범위 불명확이면 `$omc-critique`.
- outcome=unresolved + risk=low + user_selection_needed=yes면 `$omc-office-hours`.
- 사용자가 설계만 확인 중이거나 다음 단계를 아직 고르지 않음 → 사용자 선택 대기. 자동으로 진행하지는 않습니다.
