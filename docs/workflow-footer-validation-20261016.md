# Claude 최종 출력 계약 통일 검증 · 2026-10-16

Claude task 원본에 Codex와 동일한 마지막 OMC_OUTPUT·VERDICT 계약을 명시했다. 양쪽 지침에서 PHASE 7 작업 기록과 사용자 최종 보고를 분리하고, 게이트의 기계 판정 두 줄을 끝에 보존하도록 했다. Claude 단계 양식의 별도 자기 리뷰 블록을 제거했다. 설치 도우미로 현재 저장소 복사본을 동기화했다.

## 코드 검증

지침 회귀 RED 2개 실패·13개 통과 후 보완했다. 관련 8개 테스트 파일 187개 통과, staged TDD 게이트 통과. 원본과 실제 설치 지침의 bytes 및 실행 전 source hash 일치를 확인했다.

## 실제 단일 실행

같은 숫자 합계 수정 요청을 새 복사본에서 Sonnet 4.6 Bedrock/high로 1회 실행했다. 필수 브라우저는 제공하지 않았고 기대 blocked를 사전 고정했다. 58.06초 정상 종료. CONTRACT·RED 등록, 단위 테스트 실패 후 3개 통과, 내부 검증 경로 사용과 완료 게이트 BLOCK을 관측했다. 최종 원문은 상태 문장으로 시작하고 OMC_OUTPUT·VERDICT: BLOCK을 유지했다.

수집 결과 필수 5조건 모두 passed, recompare_entry allow. 첫 줄·단일 상태 서식도 통과. single_next_action은 false로 기록됐다. 원문의 다음 행동은 불릿으로 표기되어 있으며 수집기는 해당 라벨을 줄 시작에서만 인식한다. 이 서식 지표를 조용히 통과로 바꾸지 않았다.

이번 결과는 의도적으로 미실행인 브라우저를 정직하게 보고하고 기계 계약을 유지했는지에 대한 검증이다. 브라우저 검증 완료, 일반 모델 준수율, 경쟁 도구 대비 우위 또는 전체 광고 재비교 완료를 뜻하지 않는다. 전체 비교 실행, 커밋, 릴리스, 정상 사용처 설치는 하지 않았다. 변경 지침과 새 증거는 리뷰 대상이다.

[실행 전 조건](comparison-dashboard/evidence/workflow-footer-20261016/preregistration.json) · [최종 원문](comparison-dashboard/evidence/workflow-footer-20261016/final.txt) · [수집 판정](comparison-dashboard/evidence/workflow-footer-20261016/assessment.json) · [native 기록](comparison-dashboard/evidence/workflow-footer-20261016/native-events.jsonl)
