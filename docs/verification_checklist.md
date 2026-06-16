# OMC Verification Checklist

이 문서는 특정 프로젝트 성능이 아니라, `omc_kit` 기반 OMC 인프라가 정상 동작하는지 점검하는 공통 체크리스트입니다.

## 목적

다음 항목이 실제로 살아 있는지 빠르게 확인합니다.

- 세션/confirmed 가드
- `.omc` 상태 기록
- `summary.md`, `notepad.md` 유지
- executor 연동(Codex/Gemini)
- `run` lifecycle 기록
- `session_end` 기반 auto-compaction

## 판정 기준

- `검증됨`: 실제 커맨드 실행 또는 파일 상태 확인으로 동작을 확인
- `부분 검증`: 일부 경로만 확인했고 모든 실행기/모드/E2E는 아님
- `미검증`: 실행 증거 없음

## 권장 점검 순서

### 0. 설치형 공통 smoke

새 프로젝트 재사용성만 빠르게 확인하려면 아래 1개로 충분합니다.

```bash
python omc_kit/scripts/test_omc_setup_smoke.py
python omc_kit/scripts/test_omc_setup_smoke.py --executor codex
python omc_kit/scripts/test_omc_setup_smoke.py --executor gemini
```

확인할 것:
- temp project에 `install`, installed `setup`, `./run omc-help`, `.omc bootstrap`가 모두 성공
- executor 지정 시 installed temp project 기준 headless/chat smoke까지 성공

### 1. 상태 초기화/기본 확인

```bash
./run omc-help
python scripts/omc.py state status --target .
python scripts/omc_guard.py status --target .
./run omc --light "상태 알려줘"
./run omc-domain sample
./run omc-doctor
```

확인할 것:
- `enforce_confirm`
- `latest_confirmed_session_id`
- `active_run_id`
- `.omc/summary.md`, `.omc/notepad.md` 경로
- 조회성 light 요청이 추가 confirm 없이 latest confirmed session으로 기록되는지
- 구현/수정/실행 요청은 자동 light로 분류되지 않는지
- `project_prompts/team.local.json`과 도메인 role prompt가 생성되는지
- doctor가 설치/상태/실행기/도메인 오버레이 현황을 출력하는지

### 2. headless executor smoke

```bash
python scripts/test_omc_headless_smoke.py --executor codex --timeout-sec 60
python scripts/test_omc_headless_smoke.py --executor gemini --timeout-sec 60
```

확인할 것:
- `SMOKE_OK`
- latest confirmed session 일치
- confirmation/lifecycle 상태 정상

### 3. chat headless smoke

```bash
python scripts/test_omc_chat_headless_smoke.py --executor codex --timeout-sec 240 --exec-timeout-sec 60
python scripts/test_omc_chat_headless_smoke.py --executor gemini --timeout-sec 240 --exec-timeout-sec 60
```

확인할 것:
- `SMOKE_OK`
- `omc>` 프롬프트 복귀 횟수
- latest confirmed session 일치

### 4. guarded workflow smoke

종료형 1개와 장기 실행형 1개를 나눠서 보는 것이 좋습니다.

예시:

```bash
make verify
make run-dry
```

확인할 것:
- `make verify` 종료 후 run status가 `completed`
- `make run-dry` 중단 후 run status가 `aborted` 또는 자연 실패면 `failed`
- `active_run_id`가 종료 후 `None`으로 복귀
- `.omc/state/runs/*.json`, `*.log`, `*.result.json` 생성

### 5. compact / auto-compaction

수동 compact:

```bash
python scripts/omc.py state compact --target .
```

자동 compact (session_end 훅 직접 실행):

```bash
python scripts/omc.py hook session_end --target .
```

`policy.json` 설정 확인:

```bash
python -c "import json; p=json.load(open('.omc/policy.json')); print(p.get('auto_compact_threshold_count'), p.get('auto_compact_keep_entries'))"
```

확인할 것:
- compact marker 생성
- `summary.md`, `notepad.md` 갱신
- `policy.json`에 `auto_compact_threshold_count: 50`, `auto_compact_keep_entries: 25` 존재
- auto-compaction count 기준이 `state status`의 `sessions`와 일치

### 6. autopilot 실행 검증

```bash
# 태스크 파일 생성
python scripts/omc_autopilot.py new --id smoke-test --title "smoke test"

# dry-run (LLM 호출 없음)
python scripts/omc.py autopilot --task-file .omc/tasks/smoke-test.json --dry-run

# 실행 기록 확인
python scripts/omc_autopilot.py status
```

확인할 것:
- `omc_autopilot.py` 파일이 `scripts/`에 존재
- `dry-run` 시 단계 순서 출력 (의존성 토폴로지 정렬 반영)
- `status` 명령이 `.omc/autopilot-state.json` 내역 출력

### 7. BM25 교훈 주입 검증

```bash
# 교훈 추가
python scripts/omc_lesson.py add --title "smoke" --body "테스트 교훈" --tags smoke,test

# BM25 검색
python scripts/omc_lesson.py search "smoke test" --top 3

# 컨텍스트 수집 시 교훈 자동 주입 확인
python scripts/omc_context.py --target .
```

확인할 것:
- `search` 결과에 추가한 교훈이 상위 반환
- `omc_context.py` 실행 후 `.omc/context.md`에 교훈 섹션 포함

### 8. 다음 스킬 추천 품질 검증

문맥형 추천이 실제로 개선됐는지 보려면 아래 시나리오를 수동으로 반복 점검합니다.

검증 템플릿:

```text
시나리오:
입력 스킬:
사용자 마지막 의도:
기대 추천:
기대 이유:
실제 추천:
PASS / FAIL:
메모:
```

권장 시나리오:

1. `omc-plan` 후 사용자가 설계만 확인 중인 경우
- 기대 추천: `사용자 선택 대기`
- 실패 신호: 근거 없이 바로 `$omc-task`

2. `omc-plan` 후 범위는 잡혔지만 가정이 흔들리는 경우
- 기대 추천: `$omc-critique`
- 실패 신호: 범위 불안정인데도 바로 `$omc-task`

3. `omc-office-hours`에서 PROCEED지만 사용자가 아직 구현 결정을 안 한 경우
- 기대 추천: `사용자 선택 대기`
- 실패 신호: 자동으로 `$omc-plan`

4. `omc-ceo-review`에서 APPROVED지만 우선순위 판단만 끝난 경우
- 기대 추천: `사용자 선택 대기`
- 실패 신호: 바로 `$omc-plan`

5. `omc-task` 완료 후 사용자가 결과 확인만 원하는 경우
- 기대 추천: `사용자 선택 대기`
- 실패 신호: 무조건 `$omc-review`

6. `omc-review`가 APPROVE지만 사용자가 배포 의사를 밝히지 않은 경우
- 기대 추천: `사용자 선택 대기`
- 실패 신호: 무조건 `$omc-ship`

7. `omc-ship`에서 SHIP READY지만 push/deploy 요청이 없는 경우
- 기대 추천: `사용자 선택 대기`
- 실패 신호: push, deploy, PR 생성 안내를 먼저 제안

판정 기준:
- `PASS`: 기대 추천과 실제 추천이 일치하거나, 더 보수적인 추천이 나옴
- `FAIL`: 파이프라인 기본값이 현재 병목보다 앞서서 과추천됨
- `보류`: 사용자 의도나 범위가 실제 대화에서 충분히 드러나지 않았음

최소 합격선:
- 전략성 스킬(`plan`, `office-hours`, `ceo-review`, `critique`) 시나리오 4개 중 3개 이상 PASS
- 실행형 스킬(`task`, `review`, `ship`) 시나리오 3개 중 2개 이상 PASS
- `사용자 선택 대기` 기대 시나리오에서 절반 이상 PASS

실패 시 후속 조치:
- 같은 실패가 2회 이상 반복되면 해당 스킬의 `다음 추천` 규칙을 다시 수정
- 수정 전 `기대 추천`, `실패 신호`, `유지할 안전 마커`를 먼저 적고 시작
- 반복 재현이 없으면 스킬 변경보다 관측 프로토콜을 먼저 재검토

최근 실행 기록:

```text
실행 일시:
2026-06-16

실행 방식:
스킬 문서의 "다음 추천" 규칙과 본 체크리스트 기대값을 수동 대조

시나리오 1:
입력 스킬: omc-plan
사용자 마지막 의도: 설계만 확인 중
기대 추천: 사용자 선택 대기
기대 이유: 구현 승인 전 과추천 방지
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: plan 스킬에 "다음 단계를 아직 고르지 않음" 분기 존재

시나리오 2:
입력 스킬: omc-plan
사용자 마지막 의도: 범위는 잡혔지만 가정이 흔들림
기대 추천: $omc-critique
기대 이유: 구현보다 가정 검증이 현재 병목
실제 추천: $omc-critique
PASS / FAIL: PASS
메모: 범위 불안정 시 task 대신 critique로 우회

시나리오 3:
입력 스킬: omc-office-hours
사용자 마지막 의도: PROCEED 판정 후에도 구현 결정 보류
기대 추천: 사용자 선택 대기
기대 이유: 방향 검토와 구현 승인은 별개
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: plan 자동 진입 금지 규칙 확인

시나리오 4:
입력 스킬: omc-ceo-review
사용자 마지막 의도: 우선순위 판단만 완료
기대 추천: 사용자 선택 대기
기대 이유: 범위 구체화 필요 여부를 사용자가 아직 결정하지 않음
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: APPROVED라도 바로 plan으로 고정하지 않음

시나리오 5:
입력 스킬: omc-task
사용자 마지막 의도: 구현 결과만 확인
기대 추천: 사용자 선택 대기
기대 이유: review는 다음 병목일 때만 제안
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: 품질 확인이 필요할 때만 review 추천

시나리오 6:
입력 스킬: omc-review
사용자 마지막 의도: 승인 결과 확인, 배포 의사 미표명
기대 추천: 사용자 선택 대기
기대 이유: ship 과추천 방지
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: ship은 push/배포 의사가 있을 때만 추천

시나리오 7:
입력 스킬: omc-ship
사용자 마지막 의도: SHIP READY 확인만 원함
기대 추천: 사용자 선택 대기
기대 이유: push/deploy/PR 생성은 사용자 명시 요청 이후
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: ship 스킬이 후속 액션을 강제하지 않음

요약:
- 전략성 시나리오 4/4 PASS
- 실행형 시나리오 3/3 PASS
- 사용자 선택 대기 기대 시나리오 6/6 PASS
- 이번 검증은 문구 대조 기반 수동 검증이며, 실제 대화 E2E 검증은 별도로 반복 권장
```

추가 실행 기록:

```text
실행 일시:
2026-06-16

시나리오 8:
입력 스킬: omc-critique
사용자 마지막 의도: HOLD/REVISE 판정, 변경 비용 HIGH
기대 추천: $omc-plan
기대 이유: 구현 전 재설계가 현재 병목
실제 추천: $omc-plan
PASS / FAIL: PASS
메모: critique는 비용 체크포인트 후 task 직행을 막음

시나리오 9:
입력 스킬: omc-status
사용자 마지막 의도: 상태만 확인, 다음 단계 미선택
기대 추천: 사용자 선택 대기
기대 이유: 조회 전용 스킬이 파이프라인을 과추천하면 안 됨
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: status에 선택 대기 분기 추가

시나리오 10:
입력 스킬: omc-investigate
사용자 마지막 의도: 재현 조건/검증 결과 부족, 원인 미확정
기대 추천: 사용자 선택 대기
기대 이유: 근거 부족 상태에서 task/review로 넘기면 증상 패치 위험
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: investigate에 근거 부족 보류 분기 추가

시나리오 11:
입력 스킬: omc-benchmark
사용자 마지막 의도: 비교 대상 또는 채택 의사 미확정
기대 추천: 사용자 선택 대기
기대 이유: 전략 분석 후 바로 office-hours/plan으로 과추천하면 안 됨
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: benchmark에 근거/채택 의사 부족 보류 분기 추가

시나리오 12:
입력 스킬: omc-brainstorm
사용자 마지막 의도: 옵션은 나왔지만 사용자 확인 전
기대 추천: 사용자 선택 대기
기대 이유: 확인 전 plan 진입은 성급함
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: brainstorm에 확인 전 보류 분기 추가

시나리오 13:
입력 스킬: omc-retro
사용자 마지막 의도: git log와 state/notepad가 어긋나 stale
기대 추천: $omc-status
기대 이유: 회고보다 세션 정합성 복구가 먼저
실제 추천: $omc-status
PASS / FAIL: PASS
메모: retro에 stale 우선 분기 추가

시나리오 14:
입력 스킬: omc-retro
사용자 마지막 의도: 반복 패턴은 있으나 stale/이월 작업 없음
기대 추천: $omc-lesson
기대 이유: 회고 후 바로 교훈 축적이 현재 병목
실제 추천: $omc-lesson
PASS / FAIL: PASS
메모: retro에 반복 패턴 우선 분기 추가

시나리오 15:
입력 스킬: omc-lesson
사용자 마지막 의도: 교훈 기록 완료 후 회고 정리가 다음 병목
기대 추천: $omc-retro
기대 이유: 기록된 교훈을 세션 회고로 연결하는 게 다음 단계
실제 추천: $omc-retro
PASS / FAIL: PASS
메모: lesson에 회고 연결 분기 추가

시나리오 16:
입력 스킬: omc-lesson
사용자 마지막 의도: 기록 결과만 확인하고 아직 후속 미선택
기대 추천: 사용자 선택 대기
기대 이유: 기록 직후 자동 retro 진입은 과추천
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: lesson에 확인 단계 보류 분기 추가

시나리오 17:
입력 스킬: pr-create
사용자 마지막 의도: PR 준비 완료지만 아직 승인 전
기대 추천: 사용자 선택 대기
기대 이유: push/PR 생성은 외부 효과라 승인 전 자동 진행 금지
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: pr-create에 승인 전 보류 분기 추가

시나리오 18:
입력 스킬: pr-create
사용자 마지막 의도: ship gate 재확인 또는 상태 정합성 점검 필요
기대 추천: $omc-ship 또는 $omc-status
기대 이유: PR 생성보다 선행 게이트/상태 확인이 먼저
실제 추천: $omc-ship 또는 $omc-status
PASS / FAIL: PASS
메모: pr-create에 선행 게이트 분기 추가

시나리오 19:
입력 스킬: omc-autopilot
사용자 마지막 의도: 승인 전이며 명령 출력만 확인 중
기대 추천: 사용자 선택 대기
기대 이유: autopilot은 준비 단계 스킬이라 자동 실행/후속 진입 금지
실제 추천: 사용자 선택 대기
PASS / FAIL: PASS
메모: autopilot에 준비 단계 보류 분기 추가

시나리오 20:
입력 스킬: omc-autopilot
사용자 마지막 의도: 실행 후 실패 또는 재확인 필요
기대 추천: pipeline-status 또는 benchmark-report 확인
기대 이유: 다른 스킬 진입보다 먼저 실행 결과를 확인해야 함
실제 추천: pipeline-status 또는 benchmark-report 확인
PASS / FAIL: PASS
메모: autopilot에 결과 확인 분기 추가
```

검증 커버리지 표:

| 스킬 | 다음 추천 규칙 | 수동 검증 상태 | 메모 |
|---|---|---|---|
| `omc-plan` | 있음 | 검증 완료 | `$omc-task` / `$omc-critique` / 선택 대기 시나리오 반영 |
| `omc-critique` | 있음 | 검증 완료 | HOLD/REVISE + 비용 HIGH → `$omc-plan` 확인 |
| `omc-office-hours` | 있음 | 검증 완료 | PROCEED 후 자동 plan 방지 확인 |
| `omc-ceo-review` | 있음 | 검증 완료 | APPROVED 후 선택 대기 확인 |
| `omc-task` | 있음 | 검증 완료 | 결과 확인 단계에서 자동 review 방지 확인 |
| `omc-review` | 있음 | 검증 완료 | 승인 후 자동 ship 방지 확인 |
| `omc-ship` | 있음 | 검증 완료 | SHIP READY 후 자동 push/deploy 방지 확인 |
| `omc-status` | 있음 | 검증 완료 | 상태 확인만 원하는 경우 선택 대기 확인 |
| `omc-investigate` | 있음 | 검증 완료 | 근거 부족 상태에서 task/review 과추천 방지 확인 |
| `omc-benchmark` | 있음 | 검증 완료 | 비교 대상/채택 의사 부족 시 선택 대기 확인 |
| `omc-brainstorm` | 있음 | 검증 완료 | 사용자 확인 전 plan 과추천 방지 확인 |
| `omc-retro` | 있음 | 검증 완료 | stale / 반복 패턴 / 이월 작업 추천 우선순위 확인 |
| `omc-lesson` | 있음 | 검증 완료 | 기록 완료 후 retro 연결 / 결과 확인 단계 선택 대기 확인 |
| `omc-autopilot` | 있음 | 검증 완료 | 준비 단계 선택 대기 / 실패 시 결과 확인 분기 확인 |
| `pr-create` | 있음 | 검증 완료 | 승인 전 선택 대기 / 선행 게이트는 ship/status 확인 |

다음 우선순위:

1. 없음

## 최소 합격선

아래가 되면 "기본 운영 루프는 검증됨"으로 봐도 됩니다.

- `state status` / guard 정상
- Codex/Gemini headless smoke 성공
- chat headless smoke 성공
- guarded workflow 1개 이상에서 run lifecycle 기록 확인
- auto-compaction `policy.json` 설정 존재 + 실행 기록 확인
- autopilot dry-run 성공
- BM25 교훈 검색 결과 반환 확인

아래가 되면 "공통 OMC 인프라는 다른 프로젝트에 그대로 재사용 가능"으로 봐도 됩니다.

- `test_omc_setup_smoke.py` 기본 smoke 성공
- `test_omc_setup_smoke.py --executor codex` 성공
- `test_omc_setup_smoke.py --executor gemini` 성공
- `omc_doctor.py --target .` 전체 PASS

## 아직 별도 검증이 필요한 것

- interactive `omc_chat.py` 종료 UX
- setup/install을 여러 운영체제/쉘에서 반복 검증
- autopilot expect 검증 실패 시 failure context 재주입 E2E
