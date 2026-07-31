---
skill_name: omc-review
description: "코드 변경사항·diff·PR 리뷰. 트리거: 리뷰해줘, 코드 확인, 뭐가 문제야, 코드 봐줘, 이거 괜찮아. 치명/중대/경미/제안 4단계로 분류, 파일:라인 근거 필수. 요청 범위 외 리뷰 금지."
---

# OMC 코드 리뷰

이 스킬의 목적은 변경 승인 전 버그, 회귀, 테스트 누락을 찾는 것입니다. 요약보다 이슈를 먼저 씁니다. 기본 속도는 normal이고, diff가 작고 단순한 경우에만 빠르게 읽습니다.

## Step 0. 리뷰 범위 수집

blind/read-only 비교 평가를 명시적으로 요청받은 경우에는 아래 state/session 명령과 변경 가능한 검증은 실행하지 않는다. 해당 평가는 `git diff HEAD`와 읽기 전용 파일 확인만 수행하며, 최종 형식 계약은 그대로 적용한다.

```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-review" --request "<현재 작업 한 줄 요약>" --roles code_review
git status -sb
git diff HEAD
git ls-files --others --exclude-standard 2>/dev/null | head -20
find . -newer .git/index -not -path './.git/*' \( -name '*.ts' -o -name '*.tsx' -o -name '*.py' -o -name '*.md' \) 2>/dev/null | head -15
python3 scripts/omc.py state status --target . 2>/dev/null
```

## 필수 체크

- 범위 확정 / 파일:라인 근거 / 검증 커맨드는 항상 기록, 출력이 길어져도 마지막 `검증 커맨드 / 판정 / VERDICT / 다음 추천`은 생략하지 않습니다.

리뷰어가 사용자에게 바로 보여줄 것: 파일:라인 근거 이슈 / 검증 커맨드 / 판정 / VERDICT | 시스템이 암묵적으로 처리: 자명한 요약 / 대용량 diff 분할 / 범위 밖 제외

안전 필수 항목: 파일:라인 / VERDICT / [치명] [중대] [경미] [제안]

리뷰 범위
- `git diff HEAD`가 있으면 staged/unstaged 변경 전체를 보고, diff에 없는 untracked/ignored 대상은 파일을 직접 읽고 `.omc/runs` `.omc/lessons` `pipeline_run_result`는 제외합니다.
- diff 200줄 이상이면 파일 단위로 나누고, 요청 범위 밖은 리뷰하지 않습니다.

## Step 1. REVIEW CHECKLIST

C1. 정확성/정합성: null, 빈 배열, 타임존, 인덱스, 정렬 오류는? / C2. 조용한 실패: 예외 무시, 빈 catch, undefined 반환은? / C3. 안전성: 에러 처리와 복구 경로가 명시적인가?
C4. API/계약: 입력·출력 변경이 consumer와 호환되는가? / C5. 테스트/검증: 새 로직에 대응하는 테스트가 있는가? / C6. 성능: O(N²), 메모리 폭증, 불필요한 리렌더링은?
C7. 유지보수: 6개월 뒤 이해하기 어려운 부분은?
C8. 외부 계약 경계: optional/null/빈 값의 fallback·guard가 있는가? API 필드 존재·타입과 순서 보장이 확인됐는가? 누락·unknown 값이 조용히 잘못된 성공으로 이어지지 않는가?

모르면 `N/A — 이유`로 적습니다.

### Evidence classification gate

강한 finding(P1/P2)은 파일·라인뿐 아니라 변경된 동작과의 직접 연결이 있어야 합니다.

| evidence_class | 조건 | 출력 처리 |
|---|---|---|
| `behavioral_direct` | diff에서 사용자·시스템 동작 회귀가 직접 확인됨 | P1/P2 finding 가능 |
| `non_behavioral` | 공백·포맷처럼 동작 영향이 없음 | P1/P2 finding으로 출력하지 않는다 |
| `context_needed` | alias·호출 경로·런타임 계약 확인이 더 필요함 | finding 제외, `확인 필요`로 기록 |
| `test_quality_only` | 테스트 강도만 우려됨 | 제안으로 기록 |
| `unresolved` | 근거가 상충하거나 부족함 | finding 제외, 필요한 검증을 기록 |

`context_needed`와 `unresolved`는 버그가 없다는 뜻이 아니라, 현재 diff만으로 강한 finding을 확정하지 않는다는 뜻입니다.

### Direct-impact pass (APPROVE 전 1회)

변경된 외부 입력, fallback, lookup/key, 조건 분기, side effect마다 `값의 출처 → 변경된 변환/분기 → 관찰 가능한 결과`를 한 줄로 추적합니다. 이 세 단계가 모두 diff 안에서 보이면 `context_needed`로 미루지 말고 `behavioral_direct` finding으로 기록합니다. 세 단계 중 하나라도 diff 밖 계약에 의존할 때만 `[확인 필요]`를 사용합니다.

### Diff-local P1 pass (APPROVE 전 1회)

아래 패턴은 외부 서비스의 세부 계약을 추측하는 용도가 아니다. diff 안에서 기존의 안정 장치가 제거되고 관찰 가능한 잘못된 상태가 만들어지는지 확인한다. 그 인과가 diff 안에 있으면 외부 계약 확인만을 이유로 `context_needed`로 내리지 않는다.

| 패턴 | 직접 확인할 인과 | finding 기준 |
|---|---|---|
| 동적 값 이중 읽기 | 변경 가능한 값이 key와 request, 권한과 조회처럼 서로 다른 소비처에서 각각 다시 읽힘 | 두 읽기 사이 값 변경 시 key/요청 또는 권한/결과가 불일치하면 `behavioral_direct` |
| 식별자→순서 매핑 | 이름·ID 기반 lookup이 배열 위치·enumerate 기반 매핑으로 대체됨 | 항목 누락·순서 변경 시 이름별 결과가 다른 슬롯에 기록되면 `behavioral_direct` |
| 측정값 없는 정책 성공 | policy/validation 성공 분기가 필수 측정값의 `None`·누락을 허용함 | 측정되지 않은 결과가 hit/success/ready로 기록되면 `behavioral_direct` |
| 필수 메타데이터 조건부 검증 | 강한 상태(confirmed 등)의 필수 필드 검증이 optional guard 아래에 있음 | 필드 누락 입력이 강한 상태로 통과하면 `behavioral_direct` |

각 패턴은 diff에서 제거된 안정 장치, 새 분기, 관찰 가능한 결과를 모두 적을 수 있을 때만 finding으로 승격한다. 하나라도 diff 밖에 있으면 `[확인 필요]`로 남긴다.

## Step 2. REVIEW RESULT

파일:라인 근거 없는 이슈는 쓰지 않습니다. 신규 파일 전체 이슈는 `[파일경로 전체]`를 씁니다.

```text
[치명] — 장애, 데이터 손실, 보안 구멍
  - [파일:라인] evidence_class: behavioral_direct | evidence: diff 안의 인과 경로 + 관찰 가능한 동작 영향 | 수정 방향

[중대] — 기능 오동작, 테스트/타입 위반
  - [파일:라인] evidence_class: behavioral_direct | evidence: diff 안의 인과 경로 + 관찰 가능한 동작 영향 | 수정 방향

[경미] — 품질, 성능 우려, 네이밍
  - [파일:라인] evidence_class: behavioral_direct | evidence: diff 안의 인과 경로 + 관찰 가능한 동작 영향 | 개선 제안

[제안] — 선택 개선
  - [파일:라인] evidence_class: test_quality_only | 제안

[확인 필요] — 현재 diff만으로 finding 확정 불가
  - [파일:라인] evidence_class: context_needed | 확인할 호출 경로·계약·재현 커맨드
  - [파일:라인] evidence_class: unresolved | 상충 근거와 필요한 검증

검증 커맨드:
  - ...

판정: BLOCK / REVISE / APPROVE WITH NOTES / APPROVE
VERDICT: BLOCK / REVISE / APPROVE WITH NOTES / APPROVE
decision: REVISE / APPROVE (판정 결과) | risk: HIGH / MED / LOW (리스크 요약) | next_action: $omc-task / $omc-ship / 사용자 선택 대기 (다음 스킬 1개)
공통 결정표: stage=review / outcome=revise|done / user_selection_needed=yes|no / ship_intent_explicit=yes|no
```

`[치명]`/`[중대]`/`[경미]`는 반드시 `behavioral_direct`와 비어 있지 않은 `evidence:`를 기록합니다. `context_needed`/`unresolved`는 판정 근거에서 제외하고 `[확인 필요]`로만 남깁니다.

### Machine-readable evidence contract

- 각 finding은 등급 제목 바로 아래의 한 bullet로만 쓴다: `- [파일:라인] evidence_class: <ASCII token> | evidence: <직접 인과>`.
- `evidence_class` 값에는 백틱, 따옴표, 번역어를 붙이지 않는다. 허용 값은 표의 5개뿐이다.
- `[치명]`/`[중대]`/`[경미]`의 `evidence:`에는 "가능할 수 있음" 대신 diff에서 확인한 원인과 결과를 모두 쓴다.
- 형식을 지킬 수 없는 가설은 finding으로 승격하지 말고 `[확인 필요]`에 남긴다. 최종 `VERDICT:` 줄은 반드시 한 번만 쓴다.

## 판정 기준 / 규칙

- `[치명]` 있음: `BLOCK`
- `[중대]` 있음: `REVISE`
- `[경미]` 또는 `[제안]`만 있음: `APPROVE WITH NOTES`
- 이슈 없음: `APPROVE`
- 범위 준수 / 파일:라인 근거 / 수정 방향은 필수이며, `BLOCK`/`REVISE`면 수정 방향 포함 후 다시 `$omc-review`를 실행합니다. (`REVISE/BLOCK면 수정 방향 포함`)

## 다음 추천

- 우선순위는 항상 `현재 병목 > 기본 파이프라인`
- 주추천 1개, 우선순위: REVISE/BLOCK면 `$omc-task`
- APPROVE/APPROVE WITH NOTES + 배포 준비 명시 + ship_intent_explicit=yes면 `$omc-ship`
- APPROVE/APPROVE WITH NOTES + 배포 준비 미명시 또는 user_selection_needed=yes면 사용자 선택 대기
- 자동으로 진행하지는 않습니다.
