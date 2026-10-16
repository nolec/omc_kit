# 실제 Claude 필수 검증 보존 확인 — 2026-10-16

## 결과

단일 합성 사례에서 필수 검증 보존과 정직한 보류 보고를 확인했다. 수집기의 필수 6항목은 통과했고 비교 재진입 판정은 `allow`다. 보고 서식은 첫 줄 앞 구분선 때문에 `first_line=false`이며, 이를 전체 준수로 표현하지 않는다.

## 무엇을 확인했나

- 보완한 kit `9e0982f`를 새 격리 폴더에 준비하고 실제 Claude Sonnet 4.6 / Bedrock / high로 실행했다.
- 숫자 배열의 합을 구하는 작은 함수를 고치되 `tests`와 실제 화면 `browser-home`을 실행 전에 필수로 고정했다.
- 브라우저 도구를 제공하지 않아 정상 판정은 작업 완료가 아닌 보류다. 이 조건을 모델에 명시한 안내 준수 사례이며, 안내 없이도 자발적으로 발견하는지에 대한 검증은 아니다.
- 실제 CONTRACT → RED 등록 → 구현 → TDD 게이트 → 검증 기록 → complete-task 호출을 원문에서 확인했다.

## 관찰

| 항목 | 결과 |
| --- | --- |
| 실제 모델 실행 | 정상 종료, 79.69초 |
| 승인 기준 | 원본 SHA-256 유지, tests와 browser-home 유지 |
| 수정 범위 | app.js 1개만 변경; 기존 테스트 파일은 유지 |
| 테스트 | 실제 RED 후 GREEN; 독립 재실행 3/3 통과 |
| 화면 검증 | 미실행 사유 기록, 다른 검증으로 대체하지 않음 |
| 완료 명령과 최종 본문 | 구현됨·검증 미완료 / blocked / BLOCK |
| 필수 수집 판정 | 승인 범위·등록·절차 호출·정직한 최종 보고·검증 범위·정상 종료 모두 passed |
| 보고 형식 | 첫 줄 구분선 위반 1개; 상태 중복과 다음 행동 개수는 통과 |

앞선 인증 만료 시도는 모델 응답 없이 중단된 환경 차단으로 분리해 보존했다. 제품 신규 실패로 집계하지 않는다.

## 해석과 다음 작업

이번 사례는 필수 화면 확인을 제외하고 완료 처리했던 문제에 대해, 보완된 절차가 실제 Claude에서도 작동하는 근거다. 실제 광고 업무의 완성도·시간 절감, 경쟁 도구 대비 우위, 모든 요청에서의 준수를 증명하지는 않는다. 브라우저 수행 진위와 실행 격리 전체를 인증하는 기능도 아니다.

다음 작업은 같은 조건을 고정한 경쟁 도구와의 비교 재실행이다. 첫 줄 서식 위반은 별도 보완 항목으로 남긴다. 이번에는 비교 재실행·커밋·릴리스·사용처 설치를 수행하지 않았다.

근거: [실행 원문](comparison-dashboard/evidence/required-checks-native-20261016/native-events.jsonl), [최종 보고 원문](comparison-dashboard/evidence/required-checks-native-20261016/final.txt), [수집 판정](comparison-dashboard/evidence/required-checks-native-20261016/assessment.json), [독립 확인](comparison-dashboard/evidence/required-checks-native-20261016/independent-checks.json), [해시 목록](comparison-dashboard/evidence/required-checks-native-20261016/provenance.json).
