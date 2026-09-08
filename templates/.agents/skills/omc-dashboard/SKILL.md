---
name: omc-dashboard
description: "Codex-only V0. JSON 또는 CSV로 격리된 단일 페이지 로컬 대시보드를 만들고 데이터·build·interaction·responsive render를 검증할 때 사용한다. 범용 앱·문서 생성이나 배포에는 사용하지 않는다."
---

# OMC Dashboard V0

목적은 JSON 또는 CSV 입력에서 사용자가 직접 확인할 수 있는 로컬 단일 페이지 대시보드를 완성하는 것이다. Claude Artifact와 동등하다고 주장하지 않는다. 이 스킬 구현만으로 제품 효과가 증명되지 않으며 초기 상태는 `SKILL_IMPLEMENTED_NOT_FORWARD_VALIDATED`다.

## 범위

- Codex-only V0이며 화면은 단일 페이지, interaction은 없음 또는 필터 최대 1개다.
- 출력 기본값은 새 격리 디렉터리다. 기존 저장소 변경은 정확한 경로와 명시적 승인이 있을 때만 허용한다.
- 자동 배포·push·PR·commit 금지. 외부 공개와 mock data 사용도 각각 별도 승인이 필요하다.
- 범용 미니 앱, 인증, 서버 API, 문서·슬라이드 생성은 범위 밖이다.
- 이 스킬은 OMC output inspection support surface다. 특정 도메인의 우선순위 엔진이나 의사결정 모델은 범위 밖이다.

## 입력 계약

작업 전에 아래 값을 고정한다. 필수값이 없거나 데이터 의미가 불명확하면 구현하지 않고 `INPUT_CONTRACT_REQUIRED`로 종료한다.

```text
data_source: JSON 또는 CSV regular file 경로
audience: 대시보드 사용자
questions:
  - id: 질문 식별자
    question: 화면이 답해야 할 질문
    source_fields: 답의 근거가 되는 원본 필드 목록
    calculation: 선언된 필드만 사용하는 계산식과 정렬 시 tie-break
    missing_policy: BLOCK | N/A
    surface: 결과를 표시할 화면 영역
output_root: 선택; 없으면 격리 디렉터리 생성
interaction: 없음 또는 필터 1개
```

원본이 symlink가 아닌 regular file인지 확인한 뒤 수정하지 않는다. 변환 전에 격리 output의 evidence 디렉터리로 한 번 복사해 `source_snapshot`을 만들고 읽기 전용으로 설정한다. artifact root 기준 상대 `path`, 최초 `sha256`, `size_bytes`, `row_count`를 기록한다. schema·집계 기준·null 처리·단위도 이 snapshot에서 고정하며 이후 build와 데이터 검증은 모두 같은 snapshot만 사용한다. 각 build·data 검증 직전과 handoff 직전에 snapshot의 hash·크기·행 수를 다시 계산해 최초 sha256과 일치하고 최초 크기·행 수와 같은지 대조한다. 하나라도 다르면 이후 판정을 중단하고 `DATA_VERIFICATION_FAILED`로 종료한다. credential, 개인정보, 내부 URL 또는 민감도가 불명확하면 `DATA_CLASSIFICATION_REQUIRED`로 중단한다. source 절대경로, credential, 원문 개인정보를 client bundle·화면·로그에 노출하지 않는다.

각 질문은 구현 전에 `question → source_fields → calculation → missing_policy → surface`로 결속한다. 이 전체 목록을 key 정렬·공백 없는 UTF-8 JSON으로 직렬화해 격리 evidence 디렉터리의 `question_contract_snapshot` regular file로 한 번만 쓰고 read-only로 설정한다. 구현 시작 전에 그 파일의 initial sha256과 `size_bytes`를 실행 로그에 남기고 `question_contract_sha256`으로 고정한다. handoff 직전에 같은 snapshot file을 다시 읽어 `handoff_sha256`과 `handoff_size_bytes`를 계산하고 initial 값 및 독립 실행 로그의 digest와 모두 일치해야 한다. 현재 계약과 self-hash를 함께 다시 계산한 값은 최초 결속 증거로 인정하지 않는다. question contract ID와 evidence `question_id`의 exact set equality도 확인한다. 누락·추가·duplicate question_id 또는 source_fields·calculation·missing_policy·surface 변경은 `QUESTION_EVIDENCE_REQUIRED`로 차단한다. `source_fields`가 비어 있으면 결론·추천·순위를 렌더링하지 않는다. `missing_policy: BLOCK`에서 근거가 없으면 handoff와 `USER_ACCEPTANCE_PENDING`을 모두 차단하며 `N/A` status로 바꿀 수 없다. evidence의 `status: N/A`는 결속된 계약이 `missing_policy: N/A`일 때만 허용하고 `surface: omitted`로 기록한다. 의미가 불명확하거나 필드 결속을 만들 수 없어도 `QUESTION_EVIDENCE_REQUIRED`로 차단한다. 선언하지 않은 필드를 undeclared proxy로 사용하거나 계산식 밖의 의미를 부여하면 `UNSUPPORTED_INFERENCE`로 차단한다. 정렬 계산은 동률을 재현할 tie-break까지 선언한다. N/A는 추천·순위 surface를 렌더링하지 않는다.

## 실행

1. 입력과 허용된 write scope를 요약하고 사용자가 이미 지정하지 않은 중요한 해석만 확인한다.
2. 격리 output에 현재 환경에서 재현 가능한 가장 단순한 로컬 web stack을 사용한다. dependency download가 필요하면 승인 없이 진행하지 않는다.
3. 질문 계약에 직접 답하는 KPI와 비교만 구현한다. 근거 없는 지표·카피·mock row, undeclared proxy를 만들지 않는다.
4. 같은 snapshot의 기준 집계와 화면 소비 값을 독립적으로 대조한다.
5. frozen build command와 interaction check를 실행한다.
6. 실제 브라우저에서 1440px와 390px를 각각 확인한다. 데이터 경로 테스트는 화면 검증을 대체하지 않는다.
7. bundle·화면·로그에서 민감 데이터와 source path 노출을 검사한다.
8. 결과물을 다시 실행할 수 있는 명령과 한계를 함께 전달하고 사용자 판정을 기다린다.

## Fail-closed 완료 gate

다음 evidence는 각각 독립적이며 모든 항목을 `status: PASS | FAIL | NOT_RUN`, 비어 있지 않은 `command_or_method`, 비어 있지 않은 `evidence`로 기록한다. render 항목에는 실제 `viewport`와 screenshot 또는 동등한 render artifact 경로도 남긴다. `interaction_verification`의 `N/A`는 고정된 입력 계약이 `interaction: 없음`일 때만 허용한다.

- `data_verification`: source 집계와 화면 값 equality
- `build_verification`: frozen build command exit 0
- `interaction_verification`: 없음이면 명시적 N/A, 있으면 필터 동작 PASS
- `desktop_render_verification`: 1440px 직접 확인
- `mobile_render_verification`: 390px 직접 확인
- `sensitive_data_check`: credential·개인정보·내부 경로 미노출

검증 중 하나라도 실패하거나 실행하지 못하면 완료로 보고하지 않는다. 즉 `FAIL` 또는 `NOT_RUN`이거나 필수 evidence가 비어 있으면 차단하고, 허용되지 않은 `N/A`도 실패다. snapshot·데이터 불일치는 `DATA_VERIFICATION_FAILED`, build 실패는 `BUILD_FAILED`, 브라우저 미확인은 `RENDER_NOT_VERIFIED`, clipping·interaction 실패는 `SURFACE_VERIFICATION_FAILED`, 민감정보 검출 또는 검사 실패는 `SENSITIVE_DATA_CHECK_FAILED`다. 자동 보정하거나 PASS로 추정하지 않는다. `acceptance_state`는 이 결과에서 파생하며, 모든 기술 검증이 PASS이거나 허용된 interaction N/A일 때만 `USER_ACCEPTANCE_PENDING`으로 사용자에게 제시한다.

## Handoff

```text
artifact_root:
run_command:
interaction:
source_snapshot:
  path:
  sha256:
  size_bytes:
  row_count:
  handoff_sha256:
  handoff_size_bytes:
  handoff_row_count:
question_contract:
  - id:
    question:
    source_fields:
    calculation:
    missing_policy: BLOCK | N/A
    surface:
question_contract_sha256:
question_contract_snapshot:
  path:
  sha256:
  size_bytes:
  handoff_sha256:
  handoff_size_bytes:
question_evidence:
  - question_id:
    source_fields:
    calculation:
    missing_policy: BLOCK | N/A
    result:
    surface:
    status: PASS | N/A
preview_status:
verification:
  data_verification:
    status: PASS | FAIL | NOT_RUN
    command_or_method:
    evidence:
  build_verification:
    status: PASS | FAIL | NOT_RUN
    command_or_method:
    evidence:
  interaction_verification:
    status: PASS | FAIL | NOT_RUN | N/A
    command_or_method:
    evidence:
  desktop_render_verification:
    status: PASS | FAIL | NOT_RUN
    viewport: 1440px
    command_or_method:
    evidence:
  mobile_render_verification:
    status: PASS | FAIL | NOT_RUN
    viewport: 390px
    command_or_method:
    evidence:
  sensitive_data_check:
    status: PASS | FAIL | NOT_RUN
    command_or_method:
    evidence:
limitations:
acceptance_state: <파생 상태>
```

사용자 판정은 `USER_ACCEPTED`, `REVISION_REQUIRED`, `REJECTED` 중 하나다. 다른 실제 데이터로 별도 실행을 완료하기 전에는 `WORKFLOW_REPEATABILITY_OBSERVED`로 승격하지 않고 제품 효과는 `NOT_YET_PROVEN`으로 유지한다.

## 다음 추천

결과물을 제시한 뒤 자동으로 다른 스킬, 배포 또는 커밋으로 진행하지 않는다. 사용자 판정을 기다린다.
