# C3 Ralph 재개 경로 사전 확인 · 2026-10-11

이 문서는 제한적 정책 진단 승인 전 사전 확인 이력이다. 최신 상태는 [후속 진단 보고서](c3_ralph_policy_diagnostic_20261011.md)를 따른다.

T1 사전 확인만 수행했다. Ralph public CLI help는 macOS에서 exit0이며 afk·verify·from-map을 제공한다. 앞선 graph의 darwin 미지원은 Ralph 실행 미지원 근거가 아니다. 독립 resume 명령은 해당 CLI 목록에 없지만, 이것이 Ralph 재개 기능 부재를 뜻하지 않는다.

| 경계 | 확인한 실제 consumer |
|---|---|
| headless 시작 | src/cli/commands/ralph.ts:82 ralphAfkArgv는 /ralph 새 세션 prompt와 factory profile을 생성 |
| 시작 시 PRD | src/hooks/ralph/loop.ts:265 ensurePrdForStartup → :281 reconcileStalePrdForStartup → 경고 표면 |
| stale PRD 복구 | src/hooks/ralph/stale-prd.ts:658; observable content check에 따른 reconciliation이며 리뷰 승인을 대체하지 않는 계약 |
| 지속·완료 표시 | src/hooks/persistent-mode/index.ts:2294 checkPersistentModes → :2624 createHookOutput; 실제 Stop hook·세션 문맥 필요 |
| 별도 background resume tool | src/tools/resume-session.ts:68 resumeSession은 background manager context를 continuation prompt로 반환; Ralph 전체 완료 재검증 consumer로 대체할 근거 없음 |
| OMC 대응 후보 | scripts/omc_autopilot.py cmd_run의 task hash·provider-inflight·completion receipt guard; Ralph PRD/Stop hook과 동일 runtime 경계는 아님 |

현재 대응 계약은 미확정이다. Ralph native hook/PRD 표면과 OMC CLI 표면을 각각 고정하는 제한된 정책 진단, 또는 실제 Claude session/hook 전체 경로 비교 중 어느 수준을 측정할지 정해야 한다. 임의 adapter로 최종 native 완료를 합성하지 않는다. afk 새 세션 시작을 기존 세션 재개로 간주하지 않는다.

승인 계획의 T1 중단 조건에 따라 T2 공통6항목·T3 경쟁 갭 선정은 NOT_RUN으로 남겼다. 상대 실패·개선 완료로 집계하지 않는다. 다음 계획은 두 측정 경계와 관찰 항목을 먼저 고정해야 한다.

격리 근거: c3-ralph-preflight-20261011/ralph-help.stdout, ralph-help.stderr, manifest.json. 제품 코드·기존 graph6항목·대시보드·설치·원장은 변경하지 않았다. 문서만 생성했다. RED/GREEN은 새 코드가 없어 해당 없음; TDD staged 검사는 별도 수행한다.
