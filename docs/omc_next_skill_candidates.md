# OMC Next Skill Candidates

기준 상태
- 코드 worktree는 clean으로 간주
- 제외: `.omc/lessons`
- 다음 task 전 `git status -sb`로 코드 변경 0 재확인

비교 대상
- `omc-status`
- `omc-critique`

비교 기준
- 사용 빈도: `omc-status`가 현재 상태 확인용으로 더 자주 호출됨
- 현재 길이: `omc-status` 39 non-empty lines, `omc-critique` 83 non-empty lines
- 반복 설명량: `omc-status`는 상태 분류/다음 액션 문장이 반복되고, `omc-critique`는 verdict/비판 규칙이 길게 반복됨
- 안전 리스크: `omc-status`는 read-only라 낮고, `omc-critique`는 실패 탐지 품질이 핵심이라 높음

추천 후보: omc-status

추천 이유
- 사용 빈도가 높아 작은 개선도 자주 회수됨
- read-only 스킬이라 압축 실패 리스크가 낮음
- 현재 길이는 짧지만 상태 출력 형식과 분류 규칙을 더 단단하게 묶어둘 가치가 있음

보류 이유
- `omc-critique`는 길이 절감 여지는 크지만, 비판 품질이 약해지면 이후 `plan/task` 판단 전체가 흔들릴 수 있음
- 다음 단계에서 다루려면 먼저 절대 줄이면 안 되는 비판 항목을 별도 고정해야 함

다음 액션
1. `omc-status` 비교 기준과 안전 항목을 테스트로 고정
2. read-only 유지, 상태 분류, 다음 액션 추천 계약을 압축 가능한 범위만 줄임
3. `omc-critique`는 이후 별도 critique/plan을 거쳐 재진입
