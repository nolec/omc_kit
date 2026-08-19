---
skill_name: omc-review
description: "코드 변경사항·diff·PR 리뷰. 치명/중대/경미/제안으로 분류하고 파일:라인 근거를 요구한다."
---
# OMC 코드 리뷰
목적은 승인 전 버그·회귀·테스트 누락 탐지다. 이슈를 요약보다 먼저 쓴다.
## Step 0. 리뷰 범위 수집
blind/read-only 비교 평가는 state/session 명령과 변경 가능한 검증은 실행하지 않는다. `git diff HEAD`와 읽기 전용 확인만 하며 최종 계약은 유지한다.
```bash
python3 scripts/omc.py state sync-session --target . --mode autopilot --title "omc-review" --request "<현재 작업 한 줄 요약>" --roles code_review
git status -sb
git diff HEAD
git ls-files --others --exclude-standard
find . -newer .git/index
python3 scripts/omc.py state status --target .
```
## 필수 체크
- 범위 확정 / 파일:라인 근거 / 검증 커맨드를 기록한다. 출력이 길어져도 마지막 `검증 커맨드 / 판정 / VERDICT / 다음 추천`은 생략하지 않습니다. | 리뷰어가 사용자에게 바로 보여줄 것: 근거 이슈·검증·판정 | 시스템이 암묵적으로 처리: 분할·요약·범위 밖 제외
- 안전 필수 항목: 파일:라인 / VERDICT / [치명] [중대] [경미] [제안]
리뷰 범위: `git diff HEAD` 전체와 필요한 untracked/ignored 파일을 직접 읽고, `.omc/runs` `.omc/lessons` `pipeline_run_result`는 제외한다. 200줄 이상은 파일별로 나눈다.
## Step 1. REVIEW CHECKLIST
C1 정확성/정합성: null·빈 배열·타임존·인덱스·정렬 / C2 조용한 실패 / C3 안전성·복구
C4 API·consumer 계약 / C5 새 로직 테스트·검증 / C6 성능·메모리·불필요한 반복
C7 유지보수·책임·이름 / C8 외부 계약: optional/null/unknown fallback과 필드·타입·순서. 모르면 `N/A — 이유`.
## Evidence gate
- `behavioral_direct`: diff 안의 원인→분기→관찰 결과가 직접 연결될 때만 P1/P2 finding 가능.
- `non_behavioral`: 동작 영향 없음, `context_needed`: 외부 계약 필요, `test_quality_only`: 테스트 강도만 우려, `unresolved`: 근거 상충. 이들은 P1/P2 finding으로 출력하지 않는다.
- Direct-impact: 값의 출처→변환/분기→결과가 모두 diff 안이면 승격하고, 외부 계약 확인만을 이유로 `context_needed`로 내리지 않는다.
- Diff-local P1: 동적 값 이중 읽기 / 식별자→순서 매핑 / 측정값 없는 정책 성공 / 필수 메타데이터 조건부 검증을 확인한다.
## Step 2. REVIEW RESULT
```text
[치명]
- 없음 또는 `[파일:라인] evidence_class: behavioral_direct | evidence: 직접 인과와 관찰 영향 | 수정 방향`
[중대]
- 없음 또는 `[파일:라인] evidence_class: behavioral_direct | evidence: 직접 인과와 관찰 영향 | 수정 방향`
[경미]
- 없음 또는 `[파일:라인] evidence_class: behavioral_direct | evidence: 직접 인과와 관찰 영향 | 개선 방향`
[제안]
- 없음 또는 `[파일:라인] evidence_class: test_quality_only | evidence: 선택 개선 근거`
[확인 필요]
- 없음 또는 `[파일:라인] evidence_class: context_needed|unresolved | evidence: 필요한 검증`
검증 커맨드: ...
판정: BLOCK / REVISE / APPROVE WITH NOTES / APPROVE
VERDICT: 판정과 동일하며 한 번만 출력
decision: REVISE / APPROVE (판정 결과) | risk: HIGH / MED / LOW (리스크 요약) | next_action: 다음 스킬 1개
공통 결정표: stage=review / outcome=approved|blocked / user_selection_needed=yes|no / ship_intent_explicit=yes|no
```
- 강한 finding은 `evidence_class: behavioral_direct`와 비어 있지 않은 `evidence:`가 필수다. 가설은 `[확인 필요]`로 내린다.
- 판정 규칙: 치명=BLOCK, 중대=REVISE, 경미/제안만=APPROVE WITH NOTES, 없음=APPROVE. REVISE/BLOCK면 수정 방향 포함.
## Machine output contract — 마지막 두 줄은 `OMC_OUTPUT: {JSON}`과 `VERDICT: <VALUE>`; JSON은 `schema_version=omc-output/v1`, `stage`, `outcome`, `risk`, `next_skill`, `user_selection_needed`, `reason_code`; `next_skill`은 canonical `omc-*` 또는 null; unresolved/blocked는 `reason_code` 필수; legacy 정규화는 표시하고 명시적 오류는 보정하지 않습니다.

## 다음 추천
- 우선순위는 `현재 병목 > 기본 파이프라인`, 주추천 1개만 제시한다.
- REVISE/BLOCK면 `$omc-task`
- APPROVE/APPROVE WITH NOTES + 배포 준비 명시 + ship_intent_explicit=yes면 `$omc-ship`
- APPROVE/APPROVE WITH NOTES + 배포 준비 미명시 또는 user_selection_needed=yes면 사용자 선택 대기
- 자동으로 진행하지는 않습니다.
