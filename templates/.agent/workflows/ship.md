# /ship — OMC 배포 준비

OMC 가드 확인 후 배포 전 체크리스트를 실행합니다.

## 실행 순서

### 1. OMC 가드 확인 (미컨펌 세션 차단)
```bash
python3 scripts/omc_guard.py require --target . --for "ship"
```

### 2. TDD 강제 체크 (MANDATORY)

```bash
python3 scripts/omc_tdd_check.py --run-tests
```

- 신규/수정된 구현 파일 중 **테스트 파일이 없으면 즉시 종료 코드 1** 로 차단
- 예외 허용: `python3 scripts/omc_tdd_check.py --run-tests --report-only`

### 3. 린트 / 타입 체크 / 전체 테스트
```bash
npx nx affected --target=test
```

### 4. Git 상태 확인
```bash
git status -sb
git log --oneline -5
```

---

## 배포 전 체크리스트

- [ ] OMC 가드 통과 (미확정 세션 없음)
- [ ] **TDD 체크 통과** (`omc_tdd_check.py --run-tests` 반환값 0)
- [ ] 타입 에러 0개
- [ ] 린트 에러 0개
- [ ] 테스트 전부 통과
- [ ] `.env` / 비밀값 커밋 없음

---

## 배포 후 — Compound Engineering (30초)

배포 완료 후 아래 질문에 답하세요.

```
이번 배포에서 배운 점이 있나요?
  - 왜 처음부터 제대로 못 했나?
  - 어떤 요구사항을 놓쳤나?
  - 다음에 추가할 규칙은?

교훈이 있으면:
  python3 scripts/omc_lesson.py add -i
```

---

배포 대상: $ARGUMENTS (미지정 시 전체)
