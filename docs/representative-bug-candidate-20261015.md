# 대표 버그 후보: 리뷰 안내 범위와 승인 기록 범위의 불일치

## 결과

실제 리뷰 과정에서 발견한 범위 불일치를 격리 저장소의 공개 명령으로 재현했다. 제품 코드는 수정하지 않았다. 이는 평가 후보 발굴 결과이며 OMC 효과 입증이나 수정 완료가 아니다.

## R1 — 사용자 문제와 원인 경로

요청은 관측 점검 문서 한 파일의 리뷰였다. 리뷰 보고는 기존 .omc 자료를 제외한다고 했으나, capture-review는 Git이 추적하지 않는 일반 파일까지 후보에 포함했다. 이후 record-review는 문서만 분리하지 않고 그 전체 candidate_scope에 승인 receipt를 만들었다. 결과적으로 사람이 읽는 승인 범위와 기계가 기록한 후보 범위가 달랐다.

- .agents/skills/omc-review/SKILL.md의 리뷰 범위 규칙은 .omc/runs와 .omc/lessons 등을 제외한다.
- scripts/omc_review_snapshot.py:240의 build_worktree_candidate는 tracked 및 untracked 경로를 합친다.
- scripts/omc_review_snapshot.py:297의 build_review_diff는 같은 후보의 신규 파일 내용을 추가한다.
- scripts/omc_review_snapshot.py의 record-review는 전달된 snapshot 후보에 결속하며, 리뷰 본문의 “제외” 설명으로 후보 경로를 축소하지 않는다.
- 기존 scripts/test_omc_review_snapshot.py의 test_worktree_review_still_blocks_new_untracked_file는 untracked 감지가 의도된 안전장치임을 보여준다. 따라서 모든 untracked 파일을 무시하는 변경은 해결책으로 확정하지 않는다.

앞선 승인 receipt: .omc/state/review-snapshots/a26418e67e2b458388d872f6a3b0f8a6.json.
그 snapshot: .omc/state/review-snapshots/peer-inputs/80a41906345246ac88404e915f3b7815.json.
문서에 대한 내용 검토 결과와 별개로, 이 receipt를 문서 한 파일만 승인한 근거로 사용해서는 안 된다. 기존 증거는 삭제·변경하지 않았다. 이 문제만으로 실제 무관 파일 커밋·출하가 발생했다고 주장하지 않는다.

## R2 — 직접 실행 재현

제품 저장소 밖 임시 Git 저장소를 만들고 README.md 하나를 기준 커밋으로 고정했다. 신규 report.md와 기존 작업을 모사한 .omc/lessons/unrelated.md를 만들었다. 이 내용은 민감 정보 없는 합성 입력이며, 발견 계기는 실제 앞선 리뷰다.

실행 명령(임시 저장소 루트 및 기준 커밋을 각각 TARGET, BASE로 대입):

```text
python3 /Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_review_snapshot.py capture-review --target TARGET --base-commit BASE
python3 /Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_review_snapshot.py show-review-diff --review-snapshot CAPTURE_PATH --review-snapshot-sha256 CAPTURE_SHA
```

실제 결과: report.md와 .omc/lessons/unrelated.md 둘 다 frozen diff에 포함됐다. 명령은 정상 종료했다. 프로그램 충돌이 아니라 안내상 제외 범위와 도구 출력의 불일치다. 이 격리 재현에서는 승인 receipt를 추가로 만들지 않았다.

재현 실행 결과: /private/tmp/omc-review-scope-reproduction.json.
재현 snapshot SHA-256: c8abb3e3b1aadbc5a09740aec664efde5594ebeabb2e3e9f9d31a933c2586b97.
임시 자료는 삭제될 수 있으므로 이 문서에 입력·명령·관찰을 남겼다. 구현 시 이 과정을 직접 CLI 회귀 테스트로 고정해야 한다.

## R3 — 수정 과제와 평가 기준

완료 조건: 사용자가 승인한다고 읽는 파일 범위와 기계 승인 receipt의 범위가 같아야 한다. 범위 밖 파일을 보존하면서 실제 코드·테스트 변경 누락 및 리뷰 이후 변경에 대한 stale 차단은 유지해야 한다.

선택지는 명시적 리뷰 범위를 도구 전체 수명주기에 결속하는 방식 또는 승인 전에 격리 후보를 만드는 절차다. 현재 도구의 전체 변경 감지를 무조건 축소하거나 receipt 내용을 수동 편집하지 않는다. 어느 방식이 더 작은 안전한 변경인지는 별도 계획에서 결정한다.

| 지표 | 집계 기준 |
| --- | --- |
| 결과 품질 | 공개 CLI에서 표시·snapshot·receipt의 범위 일치, 범위 밖 파일 보존, 관련 stale/코드 누락 방지 회귀 통과 |
| 시간 | 다음 수정 과제의 시작·첫 통과·리뷰 완료 시각 기록, 작업 시간과 사람 응답 대기를 구분 |
| 재작업 | 최초 구현 후보 이후 결함·요구 누락으로 수정한 회차, 단순 검증 재실행과 구분 |
| 사람 개입 | 시작 요청 이후 사람이 제공한 필수 판단·추가 정보·승인 횟수, 종류와 이유 기록 |

이번 탐색은 수정 시간 측정에 포함하지 않는다. 경쟁 도구 또는 개선 전 동일 조건 실행이 없으므로 시간 절감률·우월성·인과 효과를 계산하지 않는다. 현재 결과는 측정 방법을 적용할 한 사례의 확보다.

## 검증 및 경계

CONTRACT: 후보 탐색·재현·보고서 작성. RED/GREEN: 제품 구현이 없으므로 해당 없음. 신규 테스트·제품 수정은 다음 수정 과제의 TDD 단계에서 수행한다. 기존 관측 점검 문서와 .omc 자료는 이번 변경 범위에서 제외한다.

교훈: 자연어로 제외한 파일과 승인 receipt의 실제 경로 목록을 함께 대조해야 한다. 이번에는 후보 근거로만 기록하며 전역 규칙이나 설치 파일은 바꾸지 않는다.
