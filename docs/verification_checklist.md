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
