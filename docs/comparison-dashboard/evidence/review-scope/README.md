# 리뷰 승인 범위 대조 구현 기록

## 요청과 결과

요청은 리뷰에서 설명한 승인 파일과 실제 승인 기록의 후보 범위가 달라지는 일을 막는 것이다. 요청에서 정한 저장소 상대 경로를 record-review의 반복 가능한 --expected-path로 전달한다. 목록을 후보에서 자동 추출하는 기능은 제공하지 않는다.

명시적 목록과 snapshot 전체 후보가 같아야 승인 기록이 생성된다. 누락은 review_scope_required, 잘못된 경로·중복은 review_scope_invalid, 차이는 review_scope_mismatch로 거부한다. 불일치 출력에는 요청 목록·실제 목록·추가/누락 경로·격리 재리뷰 안내가 나온다. 성공 출력에도 두 목록을 표시한다. 내부 기록 함수 두 경계에도 같은 대조를 적용했다.

목록은 필터가 아니다. 기존 untracked 감지와 stale 검사는 유지된다. 기존 receipt 스키마와 과거 기록을 변경하지 않는다. 목록을 전달하지 않는 이전 호출은 새 승인 기록 생성이 차단되며, 관련 호출 테스트를 명시적 목록으로 갱신했다. 함수에서 명시적 빈 목록은 빈 후보에만 일치하며 CLI에서 옵션 생략은 허용하지 않는다.

리뷰 지침 원본과 이 저장소의 스킬 복사본을 동기화했다. 외부 사용처 설치·릴리스·커밋은 수행하지 않았다. 기존 관측 점검 문서와 후보 보고서는 이번 수정에서 변경하지 않았다.

## R1–R4 검증

- R1: 일치 성공 및 불일치 시 추가/누락 목록과 승인 포인터 보존을 공개 CLI로 확인.
- R2: 생략 CLI·snapshot 기반 내부 함수·하위 기록 함수의 승인 우회 차단 확인.
- R3: 실제 문제와 같은 기존 교훈 파일 혼입 사례에서 거부 및 원문 보존 확인; 격리 후 재리뷰 절차는 지침으로 제공하며 자동 격리 기능은 없다.
- R4: 중복·절대·상위·비정규 경로 거부와 삭제·이름 변경 후보 처리 확인; 기존 stale 및 변경 감지 회귀와 함께 실행.

RED 원문: /private/tmp/omc-scope-red.txt — 8 failed, 62 deselected.
새 직접 회귀 테스트: scripts/test_omc_review_snapshot.py의 explicit_scope 테스트군.
관련 회귀 명령: python3 -m pytest scripts/test_omc_review_snapshot.py scripts/test_omc_peer_review.py scripts/test_omc_skill_effectiveness_cohort.py scripts/test_omc_review_skill_contract.py -q
최종 실행 결과 원문: /private/tmp/omc-scope-verified.txt.

## 첫 사례 측정의 경계

- 구현 시작: 2026-10-15T13:00:17+09:00 (OMC 세션 생성 시각).
- 새 직접 테스트 12개 첫 통과 로그 시각: 2026-10-15T13:03:46.039199+09:00 (로컬 파일 수정 시각으로 기록한 근사치).
- 구현 도중 추가 사용자 판단·정보 요청: 0회. 시작 요청은 제외한다.
- 구현 중 수정 반복은 발생했다: 잘못 넓게 적용한 치환 수정, 테스트 호출 구문 수정, 기존 fixture의 명시적 범위 보완, 스킬 복사본 동기화. 이는 리뷰 후 재작업이 아니라 구현 중 실패 수정이며 재작업 0회로 보고하지 않는다.
- 실제 실행 시간과 응답 대기를 별도 계측하지 못했으므로 순수 작업 시간·대기 절감률은 산출하지 않는다. 독립 리뷰 완료 시간과 리뷰 후 수정 회차는 아직 없다.
- 이 한 사례만으로 생산성 향상·경쟁 우위·기존 관측 복구를 주장하지 않는다. 실무 평가의 첫 사례이며 비교군은 없다.

## 최종 확인

관련 4개 파일 통합 실행은 145 passed와 지침 길이 제한 1 failed였다. 지침의 내용은 유지하며 형식을 줄이고 저장소 복사본을 동기화한 뒤, 지침 계약 테스트를 다시 실행해 통과했다. 최종 지침 테스트 원문은 /private/tmp/omc-scope-skill-final.txt에 있다. 제품 코드 최종 변경을 포함한 새 직접 테스트 12개는 /private/tmp/omc-scope-final-new-tests.txt에서 통과했다. 전체 프로젝트 회귀는 실행하지 않았다. staged 및 HEAD 기준 TDD 게이트와 diff 공백 검사도 통과했다.


## 0.5.9 릴리스·설치 결과 — 2026-10-15

명시적 승인 범위 대조·기존 차단 유지. 격리 구현 리뷰146개 통과. 전체 품질 게이트: 4324 passed, 3 skipped, 12 deselected in 550.36s (0:09:10). 첫 실행의0.5.8 기대값 고정 테스트1개 실패는 기록 보존 후0.5.9로 수정했고 관련32개 및 전체 게이트 재검증 통과.

0.5.9 로컬 패치 릴리스 6acbf14. 정상11개 사용처 installed_version0.5.9·clean·source unchanged·엄격 감사 통과. 업데이트 직전 대비 업무 파일34023개·관측 파일114개 변경0. 최초 백업 이후 업데이트 전 변경 관측0개는 별도 기록하고 보존. marketing-project는 기존 제외 유지. 원격 push·tag·GitHub release 없음.

source SHA-256: `992f4f7279e9e9eed1b9bac7e84f65c8f7e378f941dd59692112c27539be8333`.

| 사용처 | 설치·감사 |
| --- | --- |
| ai-cs | 0.5.9 / clean / strict PASS |
| codex-chat | 0.5.9 / clean / strict PASS |
| marketing | 0.5.9 / clean / strict PASS |
| research-auto | 0.5.9 / clean / strict PASS |
| market-reasoning-engine | 0.5.9 / clean / strict PASS |
| okx-maker-grid-bot-wind | 0.5.9 / clean / strict PASS |
| sixshop3-storefront-fe | 0.5.9 / clean / strict PASS |
| sixshop2 | 0.5.9 / clean / strict PASS |
| sixshop2-landing | 0.5.9 / clean / strict PASS |
| sixshop3-partner-center-fe | 0.5.9 / clean / strict PASS |
| sellerhub-accounts-fe-restored | 0.5.9 / clean / strict PASS |

백업·검증 원문: /private/tmp/omc-059-install-backups 및 /private/tmp/omc-059-install-results. ai-cs 사용자 중단과 기존 관측 설정·장부는 유지한다.
