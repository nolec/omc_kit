# OMC Next Skill Candidates

기준 상태
- 코드 worktree는 clean으로 간주
- 제외: `.omc/lessons`
- 다음 task 전 `git status -sb`로 코드 변경 0 재확인

지금 판단
- 지금은 스킬 최적화를 바로 진행하지 않음
- 최근 stale 의심 이슈는 스킬 결함보다 병렬 조회 해석 오차에 가까웠음
- 재현 없는 상태에서 더 줄이거나 바꾸면 없는 문제를 고칠 가능성이 큼

비교 대상
후보 1: omc-critique
후보 2: omc-retro

비교 기준
- 사용 빈도: `omc-critique`와 `omc-retro` 모두 반복 사용되지만, `omc-critique`가 이후 판단 품질에 주는 영향이 더 큼
- 현재 길이: `omc-critique`는 규칙과 verdict 구간이 길고, `omc-retro`는 출력 구조는 단순하지만 출처/충돌 설명이 반복됨
- 반복 설명량: `omc-critique`는 근거/대안/변경 비용 문장이 반복되고, `omc-retro`는 상태 출처와 stale 판정 안내가 반복됨
- 안전 리스크: `omc-critique`는 실패 탐지 품질 저하 리스크가 높고, `omc-retro`는 read-only라 중간 수준

후보 선정 이유
- `omc-critique`는 길이와 반복 설명량이 크지만 영향 범위도 커서, 다시 최적화할 때 가장 큰 절감 여지가 있음
- `omc-retro`는 read-only라 상대적으로 안전하고, 세션 종료/정리 UX를 다듬는 후속 후보로 적합함

지금은 바로 진행하지 않는 이유
- `omc-critique`는 재현 없는 상태에서 손대면 verdict 품질을 약화시킬 위험이 큼
- `omc-retro`는 지금 당장 반복 실패가 쌓인 상태가 아니라 효과 대비 긴급도가 낮음
- 현재는 개선보다 중단 기준을 지키는 편이 토큰과 판단 품질 면에서 더 이득임

다음 액션
1. `docs/omc_skill_optimization_stop_rules.md` 기준으로 재개 조건이 충족될 때만 재진입
2. 재개 시 `omc-critique`부터 contract test를 먼저 강화
3. `omc-retro`는 그다음 read-only 출력 구조만 좁게 다룸
