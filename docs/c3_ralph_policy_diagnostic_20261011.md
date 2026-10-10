# C3 Ralph hook·PRD / OMC CLI 제한적 정책 진단 · 2026-10-11

승인된 3조건 × 2도구의 native 정책 표면 6항목을 실행·보존했다. 실제 AI 호출은 0회다. 제품 개선 효과, 동일 runtime 비교, 경쟁 우열을 입증한 결과는 아니다. 기존 graph 진단과 합산하지 않는다.

## 관찰 결과

| 조건 | OMC 실제 CLI | Ralph 실제 hook·PRD | 해석 범위 |
|---|---|---|---|
| 중단 후 재진입 | s1 완료·s2 provider 호출 중 자체 프로세스 그룹 SIGKILL. 재실행 exit 1, provider 호출 중 종료·중복 방지를 위한 수동 대조 안내. s1/s2 각각 1호출 유지 | 완료 s1·미완료 s2의 persisted fixture에서 startLoop=true. s1 완료 상태 유지, Stop hook shouldBlock=true / continue=false, iteration 2와 계속 수행·검증 안내 | OMC 실제 강제 중단과 Ralph persisted 상태 fixture는 서로 다른 경계. Ralph 프로세스 crash recovery 자체는 미검증 |
| 요청 변경 | 두 스텝 완료 exit 0 후 s1 prompt 변경. 재실행 exit 1, task spec hash mismatch. 추가 provider 호출 없음 | 완료 PRD 유지, startup prompt만 변경. startLoop=true, 기존 PRD description·criteria와 완료 표시 유지. allComplete=true이나 Stop hook은 최종 Architect 검증을 요구하며 차단 | Ralph prompt 변경과 PRD criterion 변경은 다른 계약. 완료 표시 유지가 stale 최종 승인·자동 완료를 뜻하지 않음 |
| 완료 근거 삭제 | 두 스텝 완료 exit 0 후 s1.json 삭제. exit 1, 완료 receipt 부재/현재 workflow 불일치 안내. 추가 provider 호출 없음 | 완료 PRD fixture의 s1.json 삭제. 기존 passes/architectVerified 유지, allComplete=true. Stop hook은 최종 Architect 검증을 요구하며 차단 | 이 hook 단계에서 완료 표시를 자동 철회하지 않음. 실제 reviewer가 삭제를 발견하는지, 최종 승인하는지는 미실행 |

Ralph wrapper exit 0은 native API 호출이 정상 종료됐다는 뜻이다. `startLoop=true`, `allComplete=true`, hook `continue`는 전체 작업 완료 승인과 구분한다. 이번 Ralph 3항목의 Stop hook은 모두 `shouldBlock=true`, `continue=false`였다. Architect 요청은 원문으로만 수집했으며 실제 agent나 승인 응답을 실행·합성하지 않았다.

## 실제 consumer와 입력 계약

- OMC: `scripts/omc.py autopilot --task-file <격리경로>/task.json` → v2 durable state·task hash·provider-inflight·completion receipt guard. controlled provider는 파일만 생산한다. provider의 APPROVE 문자열은 실제 모델 판단이 아니다.
- Ralph: 설치된 dist의 `createRalphLoopHook(...).startLoop(...)` → PRD startup/reconciliation → `checkPersistentModes(...)` → `createHookOutput(...)`. public CLI afk 새 세션을 resume로 대체하지 않았다.
- 각 도구의 2스텝/2story·완료 근거 s1.json/s2.json을 격리했다. Ralph의 선행 완료·review 표시와 revision은 **입력 fixture**이며 실제 reviewer 실행 증거가 아니다. native writePrd/readPrd로 완료 표시가 유효하게 유지되는지 실행 전에 확인했다.
- Ralph는 R1에서 s1 완료·s2 미완료, R2/R3에서 두 story 완료를 준비했다. OMC는 R2/R3 선행 CLI 완료를 실제 관찰했다. Ralph startup은 iteration을 1로 초기화하고 이어진 Stop hook이 iteration 2를 기록했다.
- 전체 rival dist JS·kit scripts PY·rival package/lock hash를 실행 전 manifest로 고정했다. 재진입 직전 각 입력/상태 파일 hash, before/after 상태 원문, stdout/stderr/exit를 보존했다. dist와 source의 빌드 일치 여부나 모든 외부 dependency binding을 인증한 것은 아니다.

## 실행 불가·제외 기록

최종 채택 결과는 `c3-ralph-policy-diagnostic-20261011-v4`의 6항목이다. 실행 불가 0, native 관찰 6이다. 차단은 도구의 정책 출력이며 신규 제품 실패로 집계하지 않는다.

1. suffix 없는 첫 시도: harness의 빈 .git을 사용했고 Ralph 준비 실패. fixture/환경 경계 미확정으로 제외.
2. v2: 유효한 격리 git init 사용 후에도 sandbox에서 process start identity가 null. optional better-sqlite3 미설치 상태의 native file-lock fallback도 검증 가능한 identity가 없어 준비 실패. environment unavailable로 제외. macOS 전체 미지원으로 해석하지 않는다.
3. v3: 제한 밖 실행으로 잠금 준비는 성공했으나 입력 완료 표시에 native governing revision이 없어 완료 값이 정규화되어 사라졌다. R2/R3의 완료 baseline이 유효하지 않아 비교 전체를 제외.
4. v4: 유효한 revision-bound 완료 입력과 사전 완료 개수 assertion을 적용, native process identity 조회가 가능한 실행 환경에서 6항목을 수집했다. 제품 소스·dependency 설치·native 판단 로직을 수정하지 않았다.

이전 시도 원문은 그대로 보존한다. `run.py`는 새 루트가 이미 존재하면 실패하도록 구성돼 덮어쓰지 않는다. 새 반복 실행은 별도의 루트 이름이 필요하다.

## 개선 후보와 다음 판단

우선 후보는 **OMC 중단 차단 뒤의 복구 안내 구체화**다. OMC는 안전하게 차단하지만 현재 안내는 수동 대조가 필요하다는 한 문장이다. Ralph는 현재 story, acceptance criteria, 검증 단계와 다음 행동을 제공했다. OMC에 완료 단계·불확실 provider 단계·대조할 근거 위치·중복 실행 없이 확인할 절차를 제시하는 방향을 검토할 수 있다.

이는 제한된 정책 진단에서 관찰한 안내 차이이며 안전한 자동 resume 구현이나 사용자 복구 시간 단축의 검증은 아니다. 차단을 제거하거나 자동 재시도하는 변경은 이번 범위에 없다. 최종 reviewer 경로의 효과나 경쟁 우열은 아직 판단할 수 없다.

## 검증·인계

CONTRACT 등록, 실제 RED `ModuleNotFoundError: No module named 'capture'`, red-done 등록을 완료했다. 캡처 도구 4테스트 PASS. audit.py는 6항목 원문·전후 snapshot·sealed input 존재, prior completion 상태, hook 차단, OMC 추가 호출 없음, consumer hash 불변을 확인했다. TDD staged gate exit 0(저장소 신규 구현 파일 없음). Completion observation은 `OBSERVATION_INVALID / pending_completion_invalid`로 유지하며 자연 작업 개선 근거에 사용하지 않는다.

교훈: 완료 fixture는 boolean만으로 유효하지 않을 수 있다. native revision 계약과 read-back 완료 상태를 검증한 후에만 채택한다. 환경 제한과 harness 입력 오류를 제품 실패로 승격하지 않는다.

analysis → senior_coding 완료. 다음 역할은 code_review: `$omc-review`에서 fixture의 대응 범위, 원문 해석, excluded/admitted 분리를 검토한다. 자동 리뷰·커밋·대시보드 최신화는 수행하지 않았다. 기존 미커밋 preflight 문서와 이 보고서는 별개다.

## 근거 위치

외부 루트: `/Users/noseunglae/.codex/visualizations/2026/10/06/01a1108d-b710-7001-903a-c639eaf94c27/c3-ralph-policy-diagnostic-20261011-v4`

- `manifest-before.json`, `consumer-integrity.json`, `results.json`
- `ours-{resume,changed,missing}/execution.json`, `initial.json`, `calls.txt`, native `.omc/state/autopilot/c3-policy.json`
- `ralph-{resume,changed,missing}/prepared.json`, `native-output.json`, `execution.json`, `mutation.json`
- `{arm}-{condition}-before.json`, `-sealed.json`, `-after.json`
- `capture.py`, `test_capture.py`, `ralph.mjs`, `run.py`, `audit.py`, `verification.txt`
