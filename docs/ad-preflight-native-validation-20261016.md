# 준비 검사 보완 후 실제 Claude 광고 사례 — 2026-10-16

OMC `9255a00`을 새 격리 작업 폴더에 설치한 뒤 같은 광고 유지보수를 Claude Sonnet 4.6 Bedrock/high로 1회 실행했다. 이번 사례에서는 승인 범위와 필수 검증·보고 형식 준수를 확인했다. 추적 관련 기존 테스트 4실패는 해결되지 않았다.

## 무엇을 확인했나

이전 실행은 새 clone의 로컬 제외 규칙 누락으로 준비 조건이 달랐고, 모델이 승인 범위 밖 public/.gitignore를 생성·스테이징했다. 최종 보고도 첫 상태 문장·단일 다음 행동 조건을 어겼다. 이번에는 산출물 제외 규칙과 입력을 동결한 준비 검사로 실제 홈의 카드 이미지2개·카드 자체 전환을 확인한 뒤 모델을 호출했다. 준비 오류가 있으면 호출하지 않는 진입점을 사용했다.

| 확인 대상 | 이번 결과 |
| --- | --- |
| 입력·환경 | 이전 초기 Ad14파일·Home 검수 입력·디자인·공통 네 검증 해시 일치; 소스 설치 audit ok |
| 실제 실행 | 14:54:40 KST 시작, 486.24초(8분6초), 프로세스 종료0, 실행 중 사람 교정0회 |
| 요구별 RED | 기존4실패와 새9실패 구분; 새 RED 등록 후 최초 구현 편집 |
| 작업 범위 | Ad 폴더 내 구현·테스트3파일 변경; 기존 준비 상태는 보존; 범위 위반0 |
| 테스트·린트 | 독립49통과·기존4실패, 실패 이름·stack 경로/라인을 제외한 진단 동일, 신규 실패0; eslint exit0 |
| 통제6종 | DOM·스크린샷·시간차 확인; 가장자리RGB229/내부255로 불투명 이미지 위 선 확인, 그림자·화살표 제거·자동전환 확인 |
| 실제 홈 | 로그인된 /sixshoptest01, 카드 이미지2개, 현재 스타일·스크린샷 및 두 시점의 카드 전환 확인 |
| 엄격 수집 | required8항목 passed, work_scope passed, report_format passed; first_line/unique_status/single_next_action 모두 true |

## 해석과 한계

이 사례에서 이전 범위·보고 형식 위반은 재발하지 않았다. 수집기 allow와 독립 화면·테스트 확인은 별도로 기록했다. 기존 추적4실패를 새 실패나 전체 테스트 통과로 바꾸지 않았다. 외부 광고 클릭·오늘 하루 숨김의 실제 전송은 수행하지 않았다.

동일 초기 코드·홈·네 검증과 같은 모델을 썼지만, 이번 요청에는 범위 밖 변경 필요 시 중단 및 정확한 보고 형식 지시를 명시적으로 보강했다. 준비 오류도 바로잡았다. 따라서 1회의 시간 차이를 도구만의 생산성 향상으로 해석하거나 일반 우위를 주장하지 않는다. 경쟁 도구는 이번에 재실행하지 않았다. 원본 서비스·이전 실행·기존 스테이징12개는 보존했고, 제품 광고 후보를 storefront에 반영하지 않았다.

## 기록

커밋은 준비 도구·수집기·테스트·사용 문서4개만 포함한 `9255a002d170f1096a809decc21db59ce2ce7eea`이다. 이 보고와 실행 근거는 새 미커밋 기록이며 기존 로드맵·대시보드 변경을 덮어쓰지 않았다.

[실행 요약](comparison-dashboard/evidence/ad-preflight-native-20261016/summary.json) · [최종 원문](comparison-dashboard/evidence/ad-preflight-native-20261016/final.txt) · [엄격 수집](comparison-dashboard/evidence/ad-preflight-native-20261016/collector-assessment.json) · [변경 diff](comparison-dashboard/evidence/ad-preflight-native-20261016/candidate.diff) · [해시 목록](comparison-dashboard/evidence/ad-preflight-native-20261016/provenance.json)

![통제6종](comparison-dashboard/evidence/ad-preflight-native-20261016/controlled.png)
