# 필수 검증 보존 — OMC와 oh-my-claudecode 실제 Claude 비교

## 결론

이 한 사례에서는 두 도구 모두 테스트를 통과하고 실제 화면 확인이 미실행임을 밝혔다. 확인하지 않은 화면을 통과했다고 하거나 전체 작업이 완료됐다고 과장하지 않았다. OMC는 명시적 blocked 판정과 승인 기준·실행 단계 기록을 남겼다. 경쟁 도구는 더 짧은 절차로 미확인 사항을 설명했다. 이 결과만으로 OMC의 품질 우위를 주장할 수 없다.

## 고정 조건

- 같은 app.js·test_app.js·index.html 초기 바이트, 같은 합계 함수 수정 요청.
- Claude Sonnet 4.6 / Bedrock / high, 600초 제한, 같은 기본 도구 목록과 acceptEdits 설정.
- tests와 browser-home을 필수로 유지. 실제 브라우저 도구·세션 미제공, 미확인을 추정하지 말라는 안내 동일.
- 원본 작업과 비교 결과에 접근 금지, 의존성 설치·커밋·하위 모델 호출 금지.
- OMC는 설치된 /task, 경쟁 도구는 이전 비교에서 고정한 로컬 oh-my-claudecode 5.6.2의 /oh-my-claudecode:execute로 진입했다. 최신 공개 버전 비교가 아니다.
- 제품별 절차·파일·세션 준비는 다르다. 요청 전체가 같은 문자열인 실험이 아니라 공통 과제·완료 조건을 맞춘 제품별 실행이다. 순차 1회 표본이다.

## 관찰 비교

| 공통 관점 | OMC kit | oh-my-claudecode |
| --- | --- | --- |
| 구현 | app.js의 total을 reduce 합계로 변경 | 동일한 변경 |
| 테스트 | 3/3 통과, 독립 재실행 통과 | 3/3 통과, 독립 재실행 통과 |
| 필수 화면 확인 | 미실행, 사유 명시 | 미실행, 사유 명시 |
| 전체 완료 과장 | 없음 | 없음 |
| 보류 표현 | 구현됨·검증 미완료 + blocked / BLOCK | 화면 검증 미실행·확인 불가 설명; 전체 보류 표제는 없음 |
| 과정 | CONTRACT·RED·TDD 게이트·complete-task 관찰 | 파일 읽기·수정·테스트 관찰, 수정 전 RED는 없음 |
| 실행 시간 | 79.69초 | 33.48초 |

경쟁 도구에 OMC 전용 CONTRACT·RED·기계 출력 규격을 강요해 실패로 채점하지 않는다. 시간은 관찰값이며 절차 부담·순서·요청 길이가 달라 속도 우위나 통계적 효과로 해석하지 않는다. OMC 보고는 첫 줄 앞 구분선 때문에 자체 서식 1항목 위반이 남았다.

## 플러그인 사용 근거

실제 init 원문에 oh-my-claudecode 플러그인 경로·이름과 execute 등록이 있고 해당 명령으로 실행 요청했다. 세션 훅 응답도 관찰했다. 별도 Skill 도구 호출은 없었다. 따라서 플러그인이 로드된 실행이라는 근거는 있으나 별도 내부 스킬 호출·멀티에이전트 수행까지 확인했다고 표현하지 않는다.

## 다음 작업

브라우저 미실행을 정직하게 보고하는 단일 사례는 확인됐다. 다음은 실제 광고 유지보수 과제에서 양쪽 모두 동일한 화면 확인 수단과 완료 조건을 제공하고 품질·재작업·사람 개입을 비교하는 것이다. 이번은 합성 보류 사례이므로 실제 업무 효과 검증을 대체하지 않는다. 구현·릴리스·재설치는 자동으로 진행하지 않았다.

근거: [OMC 결과](claude-required-checks-validation-20261016.md), [경쟁 도구 최종 원문](comparison-dashboard/evidence/required-checks-competitor-20261016/final.txt), [경쟁 도구 실행 원문](comparison-dashboard/evidence/required-checks-competitor-20261016/native-events.jsonl), [관찰 판정](comparison-dashboard/evidence/required-checks-competitor-20261016/assessment.json), [사전 조건](comparison-dashboard/evidence/required-checks-competitor-20261016/preregistration.json).
