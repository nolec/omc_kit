# OMC Quickstart KR

## 핵심 원칙

- 세션 시작 시 훅이 자동으로 컨텍스트 수집 + BM25 교훈 주입 + auto_compact를 처리합니다.
- 기본 정책은 `.omc/policy.json`의 `enforce_confirm=true`입니다.
- 최신 OMC 세션이 confirmed 되기 전에는 실행 가드가 상태 변경 명령을 차단합니다.
- 세션이 50개 이상 쌓이면 `auto_compact`가 자동으로 최근 25개만 보존합니다.
- 교훈이 쌓일수록 BM25가 현재 작업과 관련된 교훈을 자동 주입합니다.

## 팀 운영 루틴

1. **요청 시작** — 역할 추천 + 컨펌

```bash
python3 scripts/omc.py "기능 구현 요청"
```

2. **역할 확인**
   - `추천 역할: ...`가 뜨면 `Enter`로 확정 또는 `+추가,-삭제` / `a,b,c`로 수정
   - 컨펌 전에는 파일 수정/명령 실행 없음

3. **상태 확인**

```bash
python3 scripts/omc.py state status
python3 scripts/omc_doctor.py --target .
```

4. **교훈 기록 (30초)**

```bash
python3 scripts/omc_lesson.py add -i
```

---

## 시나리오 1. 단일 작업 요청

```bash
python3 scripts/omc.py "로그인 기능 구현해줘"
```

내부 동작:
- `session_start` 훅 실행 (컨텍스트 수집 → BM25 관련 교훈 주입 → auto_compact)
- 역할/모드 자동 선택 → 컨펌 UI
- 프롬프트 합성 → LLM 실행

---

## 시나리오 2. 자율 루프 (Autopilot)

여러 스텝을 자동 실행하고 `expect` 검증으로 품질을 확인합니다.

```bash
# 태스크 파일 생성
python3 scripts/omc_autopilot.py new --id feat-login --title "로그인 기능"

# .omc/tasks/feat-login.json 편집 후 실행
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-login.json

# 계획 확인 (LLM 호출 없음)
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-login.json --dry-run

# 실행 기록
python3 scripts/omc_autopilot.py status
```

---

## 시나리오 3. 교훈 검색 및 기록

```bash
# BM25 유사도 검색
python3 scripts/omc_lesson.py search "타입 오류" --top 3

# 교훈 추가
python3 scripts/omc_lesson.py add -i

# 최근 목록
python3 scripts/omc_lesson.py list
```

---

## 시나리오 4. 현재 상태 확인

```bash
python3 scripts/omc.py state status
python3 scripts/omc_doctor.py --target .
cat .omc/context.md    # 마지막 세션 컨텍스트
```

---

## 시나리오 5. 수동 compact

자동 compact(threshold=50)가 있지만 수동으로도 가능합니다:

```bash
python3 scripts/omc.py state compact
```

내부 동작:
- `pre_compact` 훅 → 메모리 스냅샷 저장
- 상태 압축 (최근 25개 보존)
- `post_compact` 훅 → notepad 재생성

---

## 시나리오 6. 새 프로젝트에 설치

```bash
python omc_kit/scripts/omc.py setup --target /path/to/project
cd /path/to/project
python scripts/omc_doctor.py --target .
```

---

## 시나리오 7. 실행기 선택

```bash
OMC_EXECUTOR=gemini python3 scripts/omc.py "작업 요청"
OMC_EXECUTOR=codex  python3 scripts/omc.py "작업 요청"
python3 scripts/omc_chat.py --executor gemini "작업 요청"
```

기본값: `OMC_EXECUTOR` env → codex 탐지 → gemini 탐지 → codex fallback

---

## 시나리오 8. 훅 커스터마이징

파일: `.omc/hooks.json`

```json
{
  "hooks": {
    "session_start": [
      {"type": "shell", "command": "python3 scripts/omc_context.py --target ."},
      {"type": "builtin", "name": "auto_compact"},
      {"type": "builtin", "name": "refresh_notepad"}
    ],
    "session_end": [
      {"type": "builtin", "name": "auto_compact"}
    ]
  }
}
```

---

## 시나리오 9. TDD 파이프라인

```bash
# 테스트 먼저 작성 후 RED 등록
python3 scripts/omc_pipeline_guard.py red-done path/to/test_file.py

# 구현 파일 생성 (RED 등록 후에만 허용)
# ... 구현 ...

# 예외 허용
python3 scripts/omc_pipeline_guard.py allow path/to/config.py --reason "설정 파일"

# 상태 확인
python3 scripts/omc_pipeline_guard.py status
```

---

## 비용 추적

```bash
# 작업 후 비용 기록
OMC_EXECUTOR=claude python3 scripts/omc_cost.py record \
  --model claude-sonnet-4-5 --task "작업명"

# 현황
python3 scripts/omc_cost.py report
```
