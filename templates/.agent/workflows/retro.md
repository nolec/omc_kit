# /retro — OMC 주간 회고

`.omc/` 세션 히스토리를 분석해서 주간 회고를 생성합니다.

## 실행

```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
ls -lt .omc/state/sessions/ | head -20
python3 scripts/omc_lesson.py list
```

---

## 회고 포맷

### 1. 이번 주 완료된 작업
최근 세션 중 `active` 또는 `confirmed` 상태인 것들을 목록으로 정리합니다.

### 2. 반복되는 문제 패턴
여러 세션에서 비슷한 이슈가 반복됐는지 확인합니다.

### 3. 완료되지 못한 작업
`superseded` 또는 pending 상태로 남은 세션을 확인합니다.

### 4. 다음 주 우선순위
notepad의 `pending_request`와 `handoff 포인트`를 기반으로 제안합니다.

### 5. OMC 인프라 건강 지표
- 세션 총 수:
- confirmed 비율:
- 가장 많이 쓰인 역할:

---

## 6. Compound Engineering — 교훈 캡처 (MANDATORY)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
왜 처음부터 제대로 하지 못한 작업이 있었나?
  _______________________________________________

다음에 추가할 규칙 (있으면):
  _______________________________________________
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

```bash
python3 scripts/omc_lesson.py add -i
```

---

$ARGUMENTS 기간이 지정되면 해당 기간 세션만 분석합니다. 미지정 시 최근 7일.
