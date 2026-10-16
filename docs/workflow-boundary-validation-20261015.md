# 최종 원문·검증 경로 경계 보완 · 2026-10-15

## 구현

- `omc_pipeline_guard.py verification-path`가 현재 작업 식별자에 연결된 `.omc/verification/<session>.json` 경로를 제공한다. 폴더 밖 경로와 외부 심볼릭 링크는 읽기 전에 거부한다. 이 검사는 이미 발생한 외부 파일 쓰기를 되돌리거나 차단하는 운영체제 격리가 아니다.
- 세션 상태와 검증 파일을 독립적으로 읽는다. 검증 파일이 없거나 JSON이 잘못돼도 정상 CONTRACT·RED 등록을 없다고 진단하지 않는다.
- `omc_workflow_validation.py`가 모델 종료 후 native events의 최종 result 원문 전체를 `final.txt`에 그대로 보존하고, 원문·이벤트 SHA-256과 `assessment.json`을 생성한다. 명시적 기계 판정을 보정하지 않는다.
- 현재 작업 등록, 관측된 등록·완료 명령의 호출·반환 순서, 최종 판정의 기대 결과 일치, 관측된 검증 인자·직접 Write/Edit 경로, native 종료 결과를 재비교 필수 조건으로 검사한다. 첫 상태 줄·상태 중복·다음 행동 중복은 별도 서식 품질이다. 필수 조건 실패·확인 불가는 hold, 서식만 위반하면 allow다.

## 수집 명령

`python3 scripts/omc_workflow_validation.py --target <작업 루트> --events-file <native JSONL> --session-id <OMC session ID> --verification-file <제공된 내부 JSON 경로> --expected-outcome blocked --output-dir <수집 폴더>`

이 명령은 실제 구현 요청과 브라우저 제공 여부에 따라 기대 판정을 실행 전에 고정해 사용한다. 모델에게 부족한 증거를 만들어 쓰게 하거나, 실패한 판정을 성공으로 바꾸지 않는다. 일반 Claude 실행 전체에 자동 설치되는 후처리 훅은 아니며 이번 격리 실행 runner가 모델 종료 직후 실제로 호출했다.

## 실제 실행 결과: 전체 재비교 보류

기존과 같은 합계 수정 요청을 새 복사본에서 실행했다. Sonnet 4.6 Bedrock / high / 600초 제한, 필수 브라우저 검증은 제공되지 않았다. 입력·설치 코드 해시·현재 작업 ID·기대 blocked 결과를 호출 전에 고정했다. 모델 실행은 60.86초에 정상 종료됐다.

현재 작업의 CONTRACT·RED 등록, 실제 실패 2개 → 제품 수정 → 단위 테스트 3개 통과, staged TDD 검사와 내부 검증 경로 요청·Write·complete-task 호출이 관측됐다. 게이트는 browser 미실행으로 BLOCK을 출력했다.

하지만 모델의 최종 원문은 `outcome: blocked` 등의 평문만 남기고 표준 OMC_OUTPUT envelope와 VERDICT를 누락했다. 수집기는 이를 조용히 보정하지 않고 truthful_final=unknown / recompare_entry=hold로 기록했다. 첫 줄과 다음 행동 서식도 별도 위반으로 기록했다. 모델이 전체 완료를 거짓 선언한 것은 아니지만, 기계 판정 계약의 충족을 확인할 수 없어 전체 광고 재비교는 보류다.

## 검증 범위와 한계

직접 CLI 회귀 테스트는 원문 보존·서식만 위반한 allow·필수 실패와 누락의 hold·본문/판정 충돌·중복·실제 보존 응답·잘못된 이벤트, 내부 경로·외부 절대/상위/심볼릭 링크, 세션과 검증 기록 읽기 오류 분리를 다룬다.

도구 호출 문자열과 반환값 및 현재 pipeline 상태를 관측하며 실행 진위를 인증하지 않는다. 일반 shell 명령 전체의 파일 쓰기를 추적하지 않으므로 범위 준수는 관측한 검증 인자와 직접 파일 도구 경로에 한정한다. 허위 로그 방지·운영체제 격리·모든 native 자동화 소비 경로의 강제 적용은 포함하지 않는다. 합성 사례 1회 결과를 일반 준수율이나 경쟁 도구 성능으로 확대하지 않는다.

## 근거

[실행 전 조건](comparison-dashboard/evidence/workflow-boundary-20261015/preregistration.json) / [최종 원문](comparison-dashboard/evidence/workflow-boundary-20261015/final.txt) / [자동 수집 판정](comparison-dashboard/evidence/workflow-boundary-20261015/assessment.json) / [native 호출·반환 원문](comparison-dashboard/evidence/workflow-boundary-20261015/native-events.jsonl)

이전 승인 대상 6개 파일에 대한 수정과 새 수집기·회귀 테스트가 있으므로 재리뷰가 필요하다. 전체 광고 재비교·커밋·릴리스·정상 사용처 재설치는 실행하지 않았다.
