# 절차 준수 개선 및 Claude 플러그인 소비 검증 · 2026-10-15

## 결론

합성 작업 1개씩의 실제 Sonnet 실행에서 OMC의 현재 작업 CONTRACT·RED 등록과 완료 보고 게이트 호출이 개선됐다. 필수 브라우저 검증이 미실행인 상태를 유지하고 최종 기계 판정도 BLOCK으로 보고했다. 단, 보고 형식과 작업 범위 준수까지 완전히 해결됐다고 볼 수 없다.

별도 Claude 실행에서는 Skill 도구로 `oh-my-claudecode:execute`를 호출하고 execute 지침 본문을 로드한 뒤 구현·테스트를 수행했다. 이번 실행의 실제 플러그인 사용은 확인됐다. 이것을 이전 모든 비교나 일반 Claude 세션의 자동 활성화 증거로 확대하지 않는다.

## 실행 전 고정한 조건

같은 기존 app.js 합계 오류, 기존 테스트와 index.html을 새 복사본에 구성했다. 기준 구현은 항상 0을 반환하며, 요구 결과는 빈 배열 0 / [1,2,3] 6 / [-2,5] 3이다. node 테스트와 실제 브라우저 표시 확인을 필수로 두되 모델 실행에는 브라우저 도구·세션을 제공하지 않았다. 브라우저 항목을 임의로 제거하지 않고 미완료로 보고할지 관측한다.

개선 전은 현재 HEAD의 OMC 설치본, 개선 후는 리뷰한 guard와 task 템플릿을 반영한 설치본이다. 설치와 승인된 세션 준비만 수행했으며 CONTRACT·RED를 미리 등록하지 않았다. Sonnet 4.6 Bedrock / high / 실행별 600초 제한, 순서는 개선 전 → 개선 후 → 경쟁 도구 사용 확인이다. 사용자 승인을 받은 범위의 복사본 수정과 해당 파일 스테이징만 허용했다. 실행 전 조건은 근거 폴더의 validation-contract.json에 보존했다.

경쟁 도구 사용 확인은 같은 합성 작업에 더해 Skill 도구로 execute를 로드하라는 명시적 지시가 있다. 따라서 성능 비교나 자연 선택률 비교가 아닌 기능 호출·소비 확인이다. 각 조건은 1회뿐이며 일반 준수율·시간 개선·인과 효과를 주장하지 않는다.

## 개선 전후 관측

| 항목 | 개선 전 | 개선 후 |
|---|---|---|
| 실제 모델 실행 | 60.06초, 정상 종료 | 59.96초, 정상 종료 |
| CONTRACT 등록 | 호출 없음, confirmed=false | 제품 수정 전 호출, confirmed=true |
| RED | 실패 2개 관측 후 등록 호출, 세션 식별자 비어 있음 | 실패 2개 관측 후 등록, 현재 세션과 일치 |
| GREEN | 모델 테스트 3개 통과 | 모델 테스트 3개 통과 |
| 독립 함수 검증 | 3개 통과 | 3개 통과 |
| staged TDD 검사 | 실제 호출 | 실제 호출 |
| complete-task | 명령 없음 | 실제 호출, browser 미실행으로 BLOCK |
| 최종 본문 | 구현됨·검증 미완료라고 안내 | 구현됨·검증 미완료라고 안내 |
| 기계 판정 | 표준 envelope 없음 | 파서 검증된 blocked / verification_incomplete / BLOCK |

두 실행 모두 기존 Edit 훅의 일반 파일 수정 우회가 가능한 환경이다. 이번 관측은 안내를 따라 등록 명령을 실제 호출한 개선 후 사례이지, 기존 파일 편집의 물리적 강제가 해결됐다는 뜻이 아니다. TDD 검사에는 JS 대응 테스트 파일명 권장 경고가 있었으며 독립 함수 검사로 실제 동작을 별도로 대조했다. 실행 시간 차이 약 0.1초는 개선 효과로 해석하지 않는다.

## 남은 미준수·진단 문제

- 개선 후 최종 응답의 첫 비어 있지 않은 줄은 상태 문장이 아닌 `---`이다. 상태 설명도 반복돼 첫 줄·중복 제한을 충족하지 못했다.
- 작업 폴더 밖 수정 금지 지시가 있었지만 개선 후 검증 JSON을 `/tmp/verify_task.json`에 만들었다. 이 파일을 작업 폴더 아래에 두도록 할 필요가 있다.
- 개선 후 검증 JSON 생성 전 complete-task를 먼저 호출했다가 실패했고 이후 재시도했다. 최초 오류에서 검증 파일을 읽지 못하자 sid도 비워져, 실제로 등록돼 있던 CONTRACT·RED까지 없다고 출력했다. 읽기 오류와 실제 등록 상태의 진단을 분리할 필요가 있다.
- 검증 상태·근거는 모델이 기록한 선언이며 이 명령이 실행 진위를 인증하지 않는다. 실제 브라우저 검증은 하지 않았다.

## Claude의 oh-my-claudecode 사용 여부

별도 probe에서 `Skill {skill: oh-my-claudecode:execute}` 호출과 반환된 `skills/execute` 지침을 확인했다. 이후 app.js·test_app.js 수정과 node 테스트가 실행됐고 독립 3개 함수 검사도 통과했다. 브라우저 미검증을 보고했다. 모델 실행은 33.93초였지만 앞의 두 실행과 같은 비교 조건으로 순위를 매기지 않는다.

이전 광고 비교 실행도 inline plugin 로드와 execute 명령 등록은 확인됐다. 다만 보존된 상위 로그에는 명시적 Skill 호출·지침 본문 소비가 없어 그 로그만으로 절차 소비까지 확정하지 않는다. 이번 probe의 확인을 과거 결과에 소급 적용하지 않는다.

확인한 기본 `~/.claude/settings.json`과 현재 저장소 `.claude/settings.json`의 enabledPlugins는 모두 비어 있다. 이번처럼 `--plugin-dir`를 지정한 실행과 일반 실행은 다르며, 별도 프로필·다른 실행 인자는 조사하지 않았다. 사용자 기본 설정은 변경하지 않았다.

## 근거와 보존

- [정규화 결과](comparison-dashboard/evidence/workflow-adherence-20261015/summary.json)
- [실제 도구 호출·반환 순서](comparison-dashboard/evidence/workflow-adherence-20261015/selected-traces.json)
- [실행 전 조건](comparison-dashboard/evidence/workflow-adherence-20261015/validation-contract.json)
- [공통 요청](comparison-dashboard/evidence/workflow-adherence-20261015/request.md)
- [개선 전 보고](comparison-dashboard/evidence/workflow-adherence-20261015/before-final.txt) · [개선 후 보고](comparison-dashboard/evidence/workflow-adherence-20261015/after-final.txt) · [경쟁 도구 보고](comparison-dashboard/evidence/workflow-adherence-20261015/competitor-probe-final.txt)

입력과 최종 합성 코드를 근거 폴더에 보존했다. 원시 이벤트 로그와 설치 로그는 `/private/tmp/omc-workflow-validation-20261015-215958/`에 남겼다. 임시 원시 파일의 장기 보존은 보장하지 않는다. 환경값·쿠키·자격 증명을 보고서에 복사하지 않았다. 이번 검증은 앞서 리뷰된 제품 6개 파일을 바꾸지 않았고, 커밋·릴리스·정상 사용처 재설치도 하지 않았다.

다음 우선순위: 등록·차단 개선은 유지하고, 최종 보고 첫 줄·중복과 임시 검증 파일 위치 및 읽기 오류 진단을 좁은 회귀 사례로 보완한다.
