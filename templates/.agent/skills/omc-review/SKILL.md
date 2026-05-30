---
skill_name: omc-review
description: "코드 변경사항·diff·PR 리뷰. 트리거: 리뷰해줘, 코드 확인, 뭐가 문제야, 코드 봐줘, 이거 괜찮아. 치명/중대/경미/제안 4단계로 분류, 파일:라인 근거 필수. 요청 범위 외 리뷰 금지."
---

# OMC 코드 리뷰

목표는 버그, 회귀, 누락 테스트를 찾는 것입니다. 요약보다 이슈를 먼저 씁니다.

## Step 0. 리뷰 범위 수집

아래 명령을 실행하고 결과로 리뷰 범위를 확정합니다.

```bash
git status -sb
git diff HEAD
git ls-files --others --exclude-standard 2>/dev/null | head -20
find . -newer .git/index -not -path './.git/*' \( -name '*.ts' -o -name '*.tsx' -o -name '*.py' -o -name '*.md' \) 2>/dev/null | head -15
python3 scripts/omc.py state status --target . 2>/dev/null
```

## 필수 체크

- 범위 확정: `git diff HEAD` + untracked/최근 파일 포함 여부 명시
- 파일:라인 근거: 근거 없는 이슈 금지
- 검증 커맨드: 최종 판정 전 실행 목록 기록

리뷰 범위는 위 출력으로 확정합니다.
- `git diff HEAD`가 있으면 staged/unstaged 변경 전체를 봅니다.
- untracked 파일과 ignored 최근 파일도 범위에 포함할지 판단합니다.
- diff에 없는 untracked/ignored 대상은 파일을 직접 읽고 리뷰합니다.
- `.omc/runs`, `.omc/lessons`, `.omc/pipeline_run_result.json`은 실행 산출물로 기본 제외합니다.
- diff 200줄 이상이면 파일 단위로 나눠 리뷰합니다.
- 요청 범위 밖은 리뷰하지 않습니다.

## Step 1. REVIEW CHECKLIST

C1. 정확성/정합성: null, 빈 배열, 타임존, 인덱스, 정렬 오류는?
C2. 조용한 실패: 예외 무시, 빈 catch, undefined 반환은?
C3. 안전성: 에러 처리와 복구 경로가 명시적인가?
C4. API/계약: 입력·출력 변경이 consumer와 호환되는가?
C5. 테스트/검증: 새 로직에 대응하는 테스트가 있는가?
C6. 성능: O(N²), 메모리 폭증, 불필요한 리렌더링은?
C7. 유지보수: 6개월 뒤 이해하기 어려운 부분은?

C1~C7을 모두 채운 후에만 Step 2로 갑니다. 모르면 `N/A — 이유`로 씁니다.

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
- 대용량 diff는 분할하고 최종 판정은 가장 높은 등급을 따릅니다.
- `BLOCK`/`REVISE`면 수정 후 다시 `$omc-review`를 실행합니다.
