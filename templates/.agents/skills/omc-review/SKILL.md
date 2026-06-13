---
skill_name: omc-review
description: "코드 변경사항·diff·PR 리뷰. 트리거: 리뷰해줘, 코드 확인, 뭐가 문제야, 코드 봐줘, 이거 괜찮아. 치명/중대/경미/제안 4단계로 분류, 파일:라인 근거 필수. 요청 범위 외 리뷰 금지."
---

# OMC 코드 리뷰

이 스킬의 목적은 변경 승인 전 버그, 회귀, 테스트 누락을 찾는 것입니다. 요약보다 이슈를 먼저 씁니다.

## Step 0. 리뷰 범위 수집

```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-review" --request "<현재 작업 한 줄 요약>" --roles code_review
git status -sb
git diff HEAD
git ls-files --others --exclude-standard 2>/dev/null | head -20
find . -newer .git/index -not -path './.git/*' \( -name '*.ts' -o -name '*.tsx' -o -name '*.py' -o -name '*.md' \) 2>/dev/null | head -15
python3 scripts/omc.py state status --target . 2>/dev/null
```

## 필수 체크

- 범위 확정 / 파일:라인 근거 / 검증 커맨드는 항상 기록

리뷰어가 사용자에게 바로 보여줄 것: 파일:라인 근거 이슈 / 검증 커맨드 / 판정 / VERDICT

시스템이 암묵적으로 처리
- 자명한 요약 / 대용량 diff 분할 / 범위 밖 제외

안전 필수 항목: 파일:라인 / VERDICT / [치명] [중대] [경미] [제안]

리뷰 범위
- `git diff HEAD`가 있으면 staged/unstaged 변경 전체를 봅니다.
- diff에 없는 untracked/ignored 대상은 파일을 직접 읽고, `.omc/runs` `.omc/lessons` `pipeline_run_result`는 제외합니다.
- diff 200줄 이상이면 파일 단위로 나누고, 요청 범위 밖은 리뷰하지 않습니다.

## Step 1. REVIEW CHECKLIST

C1. 정확성/정합성: null, 빈 배열, 타임존, 인덱스, 정렬 오류는? / C2. 조용한 실패: 예외 무시, 빈 catch, undefined 반환은?
C3. 안전성: 에러 처리와 복구 경로가 명시적인가? / C4. API/계약: 입력·출력 변경이 consumer와 호환되는가?
C5. 테스트/검증: 새 로직에 대응하는 테스트가 있는가? / C6. 성능: O(N²), 메모리 폭증, 불필요한 리렌더링은?
C7. 유지보수: 6개월 뒤 이해하기 어려운 부분은?

모르면 `N/A — 이유`로 적습니다.

## Step 2. REVIEW RESULT

파일:라인 근거 없는 이슈는 쓰지 않습니다. 신규 파일 전체 이슈는 `[파일경로 전체]`를 씁니다.

```text
[치명] — 장애, 데이터 손실, 보안 구멍
  - [파일:라인] 재현 경로 + 수정 방향

[중대] — 기능 오동작, 테스트/타입 위반
  - [파일:라인] 문제 + 수정 방향

[경미] — 품질, 성능 우려, 네이밍
  - [파일:라인] 개선 제안

[제안] — 선택 개선
  - [파일:라인] 제안

검증 커맨드:
  - ...

판정: BLOCK / REVISE / APPROVE WITH NOTES / APPROVE
VERDICT: BLOCK / REVISE / APPROVE WITH NOTES / APPROVE
```

## 판정 기준

- `[치명]` 있음: `BLOCK`
- `[중대]` 있음: `REVISE`
- `[경미]` 또는 `[제안]`만 있음: `APPROVE WITH NOTES`
- 이슈 없음: `APPROVE`

## 규칙

- 범위 준수, 파일:라인 근거, 수정 방향은 필수입니다.
- `BLOCK`/`REVISE`면 수정 후 다시 `$omc-review`를 실행합니다.
