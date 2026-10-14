# C3 개선 재비교 v25

## 결론
- 동일 Markdown 2개 갱신 작업, 개선 전 OMC 0.4.0(154cb4d)과 현재 0.5.3(d3f3cb7), 조건별3회 총18회. 실제 AI 대신 결정적인 provider를 사용하고 실행 프로세스 그룹을 중단했다.
- 추가 호출18/18회0. 중복 실행 차단은 기존에도 있었으므로 이번 안전성 향상으로 집계하지 않는다.
- 호출 중단·유효 실행 기록 없음의 현재6회에서 작업/완료 단계/대상 파일/확인 불가/보류/별도 승인 안내가8줄800자 이내에 나타났다. 기존은 중복 실행 방지를 알리는1줄이었다. 표면 회귀33개 통과, 기존 public CLI 관련3개 통과. 이는 사용자 이해도 검증이 아니다.
- 완료 후 roadmap.md 내용 변경3회는 두 버전 모두 차단했지만, 현재도 receipt 불일치만 출력한다. 바뀐 파일 내용과 다음 대조 행동을 설명하지 못하는 경로가 남았다.

## 경쟁 비교의 경계
- 상대454bae(패키지5.6.2)의 graph 실행은 샌드박스 안/밖 모두 darwin contained directory-FD traversal unavailable로 시작하지 못했다. 실행 불가/구분 불가로 별도 보존하며 신규 실패·완료율 분모에서 제외한다.
- Ralph 재개 native 훅9회는 프로세스 확인 권한을 확보한 격리 환경에서 실행했다. 불완료 fixture6회는 다음 dashboard story를 안내했고, 파일 변경 fixture3회는 completed2/2를 유지했다. 파일 변경은 fileContains로 선언했지만 이미 완료된 story의 지속적 무효화를 입증하는 결과는 아니다. 실제 AI가 재실행했는지·중복 부작용 여부는 관측하지 않았다.
- Ralph에는 이 fixture의 OMC execution_observation 누락 상태를 같은 방식으로 표현하지 않았다. missing_record 명칭은 시나리오 의도만 같으며 대응 상태의 동등 비교가 아니다. hook의 shouldBlock은 계속 작업하라는 stop-hook 정책이며 실행 차단으로 번역하지 않는다.
- 따라서 경쟁 우열·전체 사용성·실제 AI 재개 개선 효과는 판정 보류한다. 초기 sandbox fixture write 실패9회는 환경 탐색이며 제품 실패나 유효 측정으로 집계하지 않는다.

## 요구사항별 증거
| ID | 확인 | 남은 검증 |
|---|---|---|
| R1 | OMC 완료된 roadmap 및 호출 이력 보존, 추가 호출0 | 상대 실제 실행 |
| R2 | OMC 요청 기록/유효 기록 없음 구분, 추가 호출0 | 실제 외부 부작용 및 상대 대응 상태 |
| R3 | OMC 파일 변경 시 완료 재사용 차단 | 불일치 안내 개선·상대 실제 재검증 |
| R4 | 현재 공개 CLI 출력6회 직접검사 | 파일 변경 경로와 실제 사용자 이해도 |

## 실행과 보존
원문·실행 전후 상태·작업 선언·호출 이력·실행 코드 텍스트·결과 JSON·파일 SHA256을 보존했다. 파일 변경 조건의 실제 현재/목표 Markdown은 후속 리뷰 보완 시 임시 측정 디렉터리에서 읽어 추가했다. artifact-observations.json에 사후 수집 시각과 SHA를 기록했으며, 실행 전 파일 스냅샷이 있었다고 주장하지 않는다. 실행 전 SHA는 완료 receipt에 기록된 값이다. provenance의 consumer 해시는 실행 후 캡처이며 사전 봉인으로 주장하지 않는다. 초기 기록 삭제 fixture가 observation을 잘못된 최상위 키에서 지운 측정은 제외하고, 현재 v2에서 dashboard step의 execution_observation을 지운 조건만 채택했다.
CONTRACT 등록 및 외부 표면검사 RED 후 측정/검사 GREEN. TDD staged 게이트 통과(신규 제품 코드 없음). 기존 연구 파일/사용처/제품 코드 변경 없음. 교훈: 차단의 존재와 사용자 안내 개선은 별도로 측정하고 정책 훅을 실제 실행 비교로 승격하지 않는다.

다음: 원문·측정 경계·남은 R3 설명 부족을 omc-review로 검토한다. 자동 수정·커밋·설치는 하지 않았다.

## 리뷰 보완: 보존된 증거 자체 검증
- verify.py는 기본적으로 자기 폴더를 입력으로 삼고 --root로 이동한 복사본을 검사한다. 임시 측정 디렉터리를 읽지 않는다.
- 반환: VALID/exit0 = 기록의 내부 일치 확인, INVALID/exit1 = 변조·모순·구조 오류, UNAVAILABLE/exit2 = 필수 근거 없음 또는 미관측. UNAVAILABLE은 신규 작업 실패로 집계하지 않는다. 해시 목록은 로컬 일치 검사이며 암호학적 진위 서명은 아니다.
- 검사: 원문 해시, 원문/요약/호출 로그/완료 상태 일치, 공개 안내, 사후 파일 관측/선언된 목표 대조, 정책 훅과 경쟁 실행의 분리. 보존된 테스트 원문도 자기 폴더를 읽도록 변경했다.
- 실행: `python3 docs/comparison-dashboard/evidence/c3-recompare-v25/verify.py`
- 회귀: `python3 -m pytest docs/comparison-dashboard/evidence/c3-recompare-v25/test_verify.py -q -p no:cacheprovider`
- surface-tests-source.txt를 같은 증거 폴더에서 Python 테스트로 읽으면 기존33개도 원본 임시 측정 없이 검사할 수 있다. 검증 코드와 테스트는 반복 측정/AI를 실행하지 않으며, 실제 사용자 이해도·동등 경쟁 비교는 계속 미관측이다.

이번 보완 검증: 누락·변조·요약/원문 불일치·경로 탈출·심볼릭 링크·이동한 폴더의 CLI 검사17개, 보존된 공개 출력 검사33개로 총50개 통과했다. RED14개 실패 출력과 GREEN50개 통과 출력은 archive-validation-red.txt/green.txt에 보존했다. 원본 측정18회와 Ralph 정책9회는 다시 실행하지 않았다.

## 두 번째 리뷰 보완: 필수 작업·완료 기록 계약
- 모든18개 측정에서 작업 선언의 필수 roadmap/dashboard 단계·순서·의존성·artifact_sha256 완료 조건·출력/목표 경로·목표 해시 형식을 확인한다. 빈 단계 목록은 INVALID다.
- 실행 전/후 상태의 작업 ID·정규 JSON 작업 해시·필수 단계 및 완료 단계 집합을 확인하고, 완료 receipt의 schema·작업·단계·validator·목표 해시·선행 receipt·receipt 자체 해시를 v1 형식으로 대조한다. 자체 해시를 다시 계산해도 목표/의존성 불일치를 통과시키지 않는다.
- 기존 native runner의 stale_recovery는 실제 작업 단계와 구분하며 auto_hold 제어 기록만 허용한다. 이 검사는 실행 전 파일을 직접 관측했다는 증명이 아니며, 사후 수집 경계와 사용자 이해도/동등 경쟁 비교 보류는 유지한다.
- 후속 RED:25개 실패·4개 통과(기존 검사에서 이미 탐지한4개)·17개 제외. GREEN: 계약·보존 회귀46개와 공개 출력33개, 총79개 통과. archive-binding-red.txt/green.txt에 실제 출력을 보존했다. 이전50개 통과 기록은 이전 보완 시점의 이력이다.
