# Automatic Model Routing Roadmap

## 목표

OMC를 `스킬 기반 규칙 라우팅`에서 `완전 자동 모델 전환 제품`으로 키운다.
핵심은 사용자가 모델을 직접 고르지 않아도, 요청 난이도와 실패 신호에 따라 적절한 모델 강도가 자동으로 선택되는 것이다.

## 현재 위치

현재 OMC는 다음 수준에 가깝다.

- 요청 또는 스킬 종류를 보고 `task kind`를 정한다.
- `balanced / cost_saver / quality_first` 정책을 읽는다.
- `mini_default / mini_high / full_default` 중 하나를 규칙 기반으로 고른다.

즉, 지금은 `rule-based orchestration v1`이다.

최근 Codex 실행 안정화 보강(2026-08-13): `omc_exec`의 interactive/headless/preflight/auth/retry 경로가 공통 Codex binary resolver를 사용하도록 통일했고, `OMC_CODEX_BIN`과 ChatGPT 앱 번들 경로를 지원한다. reasoning capability 캐시는 resolver가 반환한 바이너리 경로와 함께 저장해 실행 파일 전환 시 이전 capability를 재사용하지 않는다. 관련 회귀 테스트 `23 passed`, 통합 테스트 `145 passed, 1 skipped`, TDD gate를 확인했다.

Full critique/review 교정 예산 분리(2026-08-14): critique/review의 첫 `REVISE`는 같은 diff를 반복 검토하거나 plan으로 되돌리지 않고 지적 내용을 보존한 `task_retry`로 즉시 전환한다. 두 단계가 하나의 누적 retry 예산을 공유해 critique가 review의 교정 기회를 소진하던 문제를 없애고, `task_retry_counts`를 단계별로 저장·resume하도록 보강했다. legacy 실행은 마지막 `task_retry` 기록으로 안전하게 복원하며, critique 2회 교정 뒤에도 review 1회 교정이 독립적으로 가능하고 resume된 critique가 예산을 초과하면 추가 provider 호출 없이 `HOLD`하는 회귀를 고정했다. resolver maintenance 입력은 저장소의 `scripts/fixtures/omc_latency_benchmark_v2.json`으로 승격해 기준 커밋·범위·관련 테스트 명령을 고정했다. 직접 회귀 `100 passed`, py_compile, staged TDD gate, OMC review `APPROVE`를 확인했다. 이는 Full 파이프라인의 공유 예산 결함을 닫은 구현 완료 상태이며, 수정 커밋 기준 Lite/Full 성공 재측정 전까지 latency 라우팅 기준은 `NOT_PROVEN`이다.

Full task retry BLOCK 정합성 완료(2026-08-15): `task_retry` 출력 계약을 `REASON_CODE + VERDICT` 구조로 고정하고 execution·quality·orchestration 사유를 분리해 저장한다. 구조화된 `BLOCK`은 bounded preview·tail·SHA-256 evidence와 함께 `decision=hold`로 기록하며, critique 반복 탈출 경로와 `retry_exhausted` 복구 경로 모두 top-level `hold`와 반환 코드 `2`로 즉시 종료한다. reason code가 없는 legacy BLOCK만 `block_without_reason_code`로 승격해 기존 `plan_retry/retry_exhausted` fallback을 유지한다. 두 실제 runtime 분기와 fallback 통합 회귀를 포함해 autopilot 테스트 `255 passed, 1 skipped`, py_compile, staged TDD gate, OMC review `APPROVE`를 확인했다. 이로써 decision payload와 실제 종료 상태 불일치는 닫혔지만, latency 라우팅 기준은 성공 표본 재측정 전까지 계속 `NOT_PROVEN`이다.

Setup 설치 검증 완료(2026-08-18): `python3 scripts/omc.py verify-install --target <repo>` 명령을 추가해 install-source metadata와 schema v1 receipt의 target·source hash·entry policy/status를 검증하고, managed 파일의 존재 여부와 설치 시점 hash drift를 strict exit code로 판정한다. 절대·상위 경로와 direct/parent symlink를 통한 target 경계 우회, blocked·빈·손상 receipt, 읽기 오류를 모두 fail-close하며 실제 임시 저장소에서 `setup --force → verify-install` E2E 성공을 확인했다. 관련 회귀 `96 passed`, py_compile, staged TDD gate, OMC review `APPROVE`를 통과했다. 이로써 setup-force 이후 최신 원본이 정상 반영됐는지 기계적으로 확인하는 운영 경로는 구현 완료다.

Setup 원본 freshness 검증 보강 완료(2026-08-20): 설치된 managed 파일의 무결성과 현재 `omc_kit` 원본 freshness를 분리해 `installed_integrity_status`와 `source_freshness_status`로 노출한다. 원본이 변경되면 `update_available`, 경로·hash를 확인할 수 없으면 `unknown`으로 판정하며 둘 다 strict 검증에서 fail-close한다. installer와 audit는 단일 `omc_source_hash` 계약을 공유하고 `.coverage`, `htmlcov`, Python·mypy·ruff·tox cache, 가상환경과 `node_modules` 같은 비관리 산출물을 hash에서 제외해 실행만으로 발생하던 freshness 오탐을 차단한다. 임시 저장소에서 새 공통 모듈이 실제 설치되고 설치된 target CLI가 `up_to_date`를 반환하는 E2E를 확인했으며, 집중 회귀 `114 passed`, 전체 회귀 `2371 passed, 3 skipped`, py_compile, staged TDD gate, OMC review `APPROVE`를 통과했다.

Setup 배포 범위 freshness·상태 의미 분리 완료(2026-08-21): source freshness hash를 저장소 전체가 아니라 setup이 실제 배포하는 `templates`와 공통 allowlist의 scripts·prompts·docs·`VERSION` 입력으로 제한했다. 따라서 `automatic_model_routing_roadmap.md`처럼 사용처에 설치되지 않는 내부 문서 변경은 더 이상 `update_available`을 만들지 않지만, 실제 배포 문서·스크립트·스킬 변경은 계속 freshness를 갱신한다. 설치 감사에는 `core_usage_readiness`를 추가해 설치 무결성·영수증 유효성·업데이트 권장을 Quality Gate 설정과 분리했고, Quality Gate는 `delivery_validation` 범위이며 `does_not_block_core_usage`임을 명시했다. 잘못된 v2 버전 영수증은 핵심 사용 상태도 `blocked`로 fail-close한다. 실제 임시 `setup --force` 후 strict audit에서 core `ready`, source `up_to_date`, Quality Gate `missing`, verification `ok`를 확인했고, 집중 회귀 `147 passed`, 전체 회귀 `2440 passed, 3 skipped`, py_compile, staged TDD gate, OMC review `APPROVE`를 통과했다.

OMC 제품 버전 관리 완료(2026-08-21): source kit 루트의 `VERSION`을 안정 버전 `MAJOR.MINOR.PATCH` SSOT로 고정하고 target 루트에는 배포하지 않는다. 새 설치는 source hash·선택적 Git revision·최초 설치/갱신 시각을 포함한 install receipt schema v2를 기록하며, schema v1은 읽기 호환 후 다음 성공 setup 또는 충돌 없는 자동 갱신에서 v2로 승격한다. 설치 시작 전에 version·hash·revision을 불변 `SourceIdentity`로 한 번 캡처해 누락·손상된 `VERSION`은 target 변경 전에 차단하고 metadata와 receipt가 동일 source snapshot을 공유하도록 했다. `python3 scripts/omc.py version --target . [--json]`은 release 비교, source 변경, managed install 무결성을 독립 상태로 보고하며 `verify-install --strict`의 강한 freshness gate는 유지한다. 관련 회귀 `142 passed`, 전체 회귀 `2438 passed, 3 skipped`, py_compile, staged TDD gate, `git diff --cached --check`, OMC review `APPROVE`를 통과했다.

Agent Skills SSOT 및 호환 미러 정리 완료(2026-08-21): `.agents/skills`를 canonical source, `templates/.agents/skills`를 설치 template로 유지하고 중복 관리되던 `templates/.agent/skills`는 제거했다. Antigravity의 `.agent/workflows`·`.agent/rules`는 지원 surface로 유지하되 target의 `.agent/skills`는 setup 시 canonical skills에서 생성되는 호환 미러로 고정했다. SSOT sync와 hub push는 `omc-*`, `pr-create`, 루트 skill checklist/template만 관리 대상으로 허용해 프로젝트 로컬·서드파티 skill이 공용 kit 또는 hub로 역유입되지 않도록 fail-closed 처리했다. canonical mapping 복구와 비관리 skill 음성 회귀를 포함해 대상 테스트 `103 passed`, 전체 회귀 `2374 passed, 3 skipped`, staged TDD gate, OMC review `APPROVE`를 확인했다. 관련 구현 커밋은 `2b90f8d`, `3e0d699`, `bb22984`이며, 이후 새 비접두 관리 skill을 추가할 때는 명시적 allowlist 갱신이 필요하다.

Plan Batch B roadmap commit alias 보강(2026-08-18): 실제 운영에서 사용된 `roadmap-and-commit` directive도 기존 `roadmap-sync-commit`과 동일하게 pending implementation completion을 보존하도록 exact allowlist에 추가했다. 원래 task에만 receipt가 귀속되고 alias directive에는 생성되지 않는 양성 회귀와 unrelated directive가 pending을 제거하는 기존 음성 경계를 함께 확인했다. state/candidate 연계 회귀 `83 passed`, TDD gate, OMC review `APPROVE`를 통과했다. 과거 `f10e3df` 누락 receipt는 prospective 원칙상 소급 생성하지 않으며, 이 커밋부터 실제 구현 receipt를 정상 축적한다.

V5 exact 2-child sequential opt-in 및 acceptance adapter 완료(2026-08-19): 명시적으로 승인된 두 child의 graph·실행 순서·prompt·개별 execution grant를 hash로 결속하고, aggregate call·elapsed·output 예산 안에서 각 child를 순차로 정확히 한 번씩 실행하는 제한 경로를 추가했다. 별도 원자적 sequence ledger가 중복 claim을 막고 claim 시점에 sequence와 child expiry를 다시 검증하며, 실제 provider call 수·elapsed·output usage와 durability를 기록한다. `execute-sequence` adapter는 완료, 두 child의 호출 전·후 실패, 첫·두 번째 child 전 만료, 중복 claim의 6개 종료 시나리오를 사전 등록된 acceptance contract로 판정하고, 완료 상태라도 metric·reason·child evidence가 충돌하면 CLI exit code `2`로 fail-close한다. 첫 child가 실패·timeout·blocked·indeterminate이면 두 번째 child를 실행하지 않고 parent review로 전환하며 retry·재분배·fallback·자동 resume은 허용하지 않는다. 관련 회귀 `222 passed`, 전체 회귀 `2213 passed, 3 skipped`, py_compile, TDD gate, OMC review `APPROVE`를 통과했다. 이는 bounded 2-child 실제 실행과 운영 acceptance 판정 완료를 뜻하지만 일반 N-child 스케줄링, 자동 재분배, 자동 모델 전환은 아직 포함하지 않는다.

V5 bounded N-child DAG grant 계약 완료(2026-08-23): 승인된 `3–5`개 child의 DAG 구조·prompt·개별 execution grant·aggregate budget을 하나의 approval hash 집합에 결속하는 `build_n_child_dag_grant`를 추가했다. child ID·dependency 유효성, cycle, lexical scope 중첩, 필수 grant metadata·status·미래 expiry·고유 idempotency key, 정수형 call/attempt와 비음수 numeric budget, critical-path 최저 elapsed budget을 모두 fail-close 검증한다. 승인 뒤 graph·grant·prompt·budget 중 하나라도 변하면 실행 grant를 만들지 않으며, 현재 ready child만 계산하되 retry·재분배·fallback·resume은 명시적으로 비활성화한다. 관련 회귀 `154 passed`, 전체 회귀 `2490 passed, 3 skipped`, staged TDD gate, `git diff --cached --check`, OMC review `APPROVE WITH NOTES`를 통과했다. 이는 N-child 실행 전 계약 계층 완료를 뜻하며 scheduler·provider 호출·실패 재분배는 아직 구현하지 않았다. scheduler 진입 전 symlink·case·glob scope 정규화 정책과 child `approval_id` 고유성 정책을 확정해야 한다.

Operator Experience 로컬 커밋 승인 상속 완료(2026-08-19): 현재 세션에서 에이전트가 이미 제시한 로컬 커밋 범위와 선택지에 사용자가 `1`, `1번`, `확인`처럼 정확한 짧은 응답을 보냈을 때 같은 내용을 다시 묻지 않도록 `decision-open → decision-resolve → decision-consume` receipt 흐름을 추가했다. receipt는 현재 confirmed session, 최대 1시간 TTL, 선택 option의 명시적 path 집합, 파일별 Git blob·mode·content와 실제 commit tree를 결합하고 성공한 로컬 커밋에서 한 번만 소비된다. 여러 그룹은 option별 `paths`를 필수로 요구하며 선택하지 않은 파일, 승인 뒤 내용 변경, 세션·범위 drift, 정책 파일의 무단 동반 커밋은 fail-close한다. `.omc/state`, run log, install receipt 등 명시적 런타임 아티팩트만 범위 비교에서 제외하고 `.omc/policy.json` 같은 동작 설정은 승인 범위에 남긴다. 이 상속은 UX acknowledgement일 뿐 push·PR·deploy·delete·reset 권한으로 확장되지 않는다. local-commit 결정 회귀 `14 passed`, prompt hook `7 passed`, 관련 전체 `54 passed`, py_compile, root/template parity, TDD gate와 OMC review `APPROVE`를 확인했다.

Autopilot machine output contract 확대 완료(2026-08-20): `plan / task / review`에 이어 `critique / investigate / ship`까지 공통 `omc-output/v1` envelope과 최종 `OMC_OUTPUT`·`VERDICT` 두 줄로 정규화하고, stage·outcome·risk·next skill·사용자 선택 필요 여부·reason code를 기계 판독 가능한 단일 계약으로 고정했다. Critique의 `APPROVE WITH NOTES`도 성공 판정으로 소비하며, Investigate·Ship은 reason-aware routing으로 다음 스킬과 사용자 선택 필요 여부를 fail-close 검증한다. legacy verdict와 기존 평문 `OMC_OUTPUT` envelope은 호환하되 명시적으로 잘못되거나 중복·미종결된 envelope은 거부한다. 새 machine envelope은 Markdown HTML comment로 숨겨 사용자 화면에는 JSON 대신 최종 `VERDICT`만 보이며, payload 안의 HTML comment delimiter도 안전하게 escape하고 round-trip한다. provider stderr는 stdout 계약 판정에서 분리해 경고가 verdict를 오염하거나 위조하지 못하게 하고 보존 로그만 남긴다. canonical·live·template 지침도 같은 계약으로 동기화했다. 집중 회귀 `247 passed`, 전체 회귀 `2365 passed, 3 skipped`, py_compile, staged TDD gate, OMC review `APPROVE`를 확인했다. 이는 핵심 실행·판정 스킬 6종의 출력 형식과 라우팅 판정 통일 완료를 뜻하며, 나머지 선택 스킬과 외부 provider별 본문 자연어 형식까지 강제하는 범위는 포함하지 않는다.

Autopilot 조회·실행 문맥 경계 보강 완료(2026-08-22): `$omc-autopilot 다음 계획`, `뭐 해야 해`, `현재 상태` 같은 조회 요청은 동기화된 현재 문맥으로 바로 답하고 저장소·브랜치·작업을 다시 묻거나 pipeline 명령을 노출하지 않도록 query 경로와 execution-preparation 경로를 분리했다. 실행 준비는 등록된 자연어 트리거 또는 명시적 `$omc-autopilot` 호출에 실행 가능한 작업이 함께 있을 때만 진입하며, `active + confirmed + same repository + unfinished` 문맥만 상속한다. stale·completed·다른 저장소 문맥은 상속하지 않고, 원격 default branch와 default-branch 조회 실패는 모두 명시적 branch 입력을 요구하도록 fail-close했다. 실제 발생 요청 fixture와 계약 회귀를 추가하고 canonical·template skill을 동기화했다. 집중 회귀 `159 passed`, staged TDD gate, `git diff --cached --check`, OMC review `APPROVE`를 확인했다.

Autopilot 승인 후 실제 launch·시작 영수증 검증 완료(2026-08-22): 실행 전 지시문·브랜치·모드·dirty 조건을 확정하고 사용자가 명시 승인하면 명령만 반환하지 않고 pipeline을 백그라운드에서 정확히 한 번 시작하도록 canonical·template skill 계약을 교정했다. launch 직후 PID·승인된 요청 브랜치·지시문을 `pipeline-status`에 결속하고 최대 대기 시간 안에 같은 실행의 result receipt가 나타나지 않으면 이전 결과를 stale로 거부한다. 브랜치 충돌로 실제 이름에 suffix가 붙어도 `requested_branch`를 별도로 보존해 승인 문맥과 생성 브랜치를 함께 검증하며, 대안 옵션은 선택된 항목만 단일 launch에 추가한다. 집중 회귀 `142 passed`, 전체 Autopilot 회귀 `433 passed, 1 skipped`, py_compile, staged TDD gate, 셸 PID 캡처, canonical/template parity, `git diff --cached --check`, OMC review `APPROVE WITH NOTES`를 확인했다. 이어 지연된 receipt가 polling 중 생성되는 비동기 경로를 실제 CLI subprocess로 재현하고, 실제 저장 계약인 200자 instruction receipt와 전체 지시문 비교가 정상 수렴하는 회귀 테스트를 추가했다. Autopilot 집중 회귀 `143 passed`, `git diff --cached --check`, OMC review `APPROVE`를 통과해 해당 선택 보강도 완료했다.

도구 중립 품질 게이트 Ship 전환 완료(2026-08-21): 프로젝트 소유 `.omc/quality-gates.json`에서 argv 기반 검증 명령과 `changed / affected / full` 범위를 선언하고, config·evidence hash에 결속된 proposal과 명시적 approval receipt를 거쳐서만 실행하는 공통 runner를 추가했다. `full` 범위는 별도 승인을 요구하며, changed 범위는 기준 커밋 이후 변경과 staged·unstaged tracked 변경을 함께 계산하고 빈 변경 집합은 명령을 실행하지 않는다. shell token 차단, 단일 config snapshot, 선행 `-` 경로 정규화, timeout·비 UTF-8 출력의 JSON 보존까지 fail-close 경계를 고정했다. 손상된 설정도 raw file hash CAS를 통해 승인된 proposal로만 복구할 수 있고, 정상 설정은 canonical config hash CAS를 유지한다. Ship과 TDD 경로는 특정 Nx·Jest·Pytest 실행 추론을 제거하고 `omc_tdd_check.py`와 공통 quality gate runner만 사용하도록 전환했으며, Codex·Claude·Gemini surface와 설치 template도 같은 계약으로 동기화했다. setup은 사용처의 quality-gates 설정을 보존하고 install audit은 readiness를 보고한다. CI는 PR base·push before·remote default branch 순으로 비교 기준을 결정하고 clean checkout에서도 base를 찾지 못하면 fail-close한다. 집중 회귀 `75 passed`, 전체 회귀 `2416 passed, 3 skipped`, YAML parse, clean-checkout 미테스트 변경 차단 재현, staged TDD gate, `git diff --cached --check`, OMC review `APPROVE`를 확인했다. OMC 코어의 legacy 도구 종속 실행 경로 전환은 완료됐으며, 남은 작업은 각 사용처가 실제 프로젝트 명령을 `.omc/quality-gates.json`에 구성하고 운영 evidence를 축적하는 것이다.

이전 동기화 기준(2026-08-10, 아래 2026-08-11 보정 전): executor capability 관측은 `observed_candidate_only` 계약까지, 복잡 작업 위임 관측은 `delegation_observed`와 실행 전 검증용 `noop_shadow` 계약, 기존 handoff를 정규화하는 `child decision` 계약 1차까지 구현·리뷰 완료했다. Plan Quality는 repository-grounded runtime runner로 observed holdout 10건 1차 배치를 실행하고, blind adjudication의 동일 case 의미 판정 비대칭을 방지하는 계약까지 반영했다. 재판정 결과 OMC와 baseline의 weighted recall은 모두 `0.94`, critical omission은 각각 `1`, executable task rate는 모두 `1.0`이었고 OMC의 total token은 baseline 대비 `47.27%` 적었다. 두 provider가 같이 놓친 `observed-plan-08` 요구사항 때문에 기존 strict verdict는 `NOT_PROVEN`이었으며, 이후 provider 출력 없는 독립 evidence packet과 사용자 서명으로 교정 gold를 확정했다. 사후 gold 교정 결과가 대체 판정으로 승격되지 않도록 `evaluation_scope`를 runtime attestation 대상 provider batch에 결합하고, post-hoc amendment는 항상 `DIAGNOSTIC_ONLY`로 차단했다. 실제 대체 판정은 교정 gold를 실행 전에 동결한 신규 disjoint holdout에서만 수행한다. Fresh Batch A는 baseline-only transfer readiness에서 결정적으로 익명화한 exact corpus 10건을 gold author와 runtime이 공유하도록 교정했고 독립 gold author/reviewer/sign-off 및 confirmatory manifest 승인·봉인까지 완료했다. 실제 paired 실행과 별도 batch ID 전체 재실행은 각각 불필요한 context/plan 왕복으로 fail-stop됐으며, 두 실패 배치는 보존했지만 대체 판정에는 사용할 수 없다. 이후 runtime shell 정책을 교정해 안전한 read-only 명령은 hard failure 대신 효율 위반으로 측정하고 위험·미분류 명령만 차단하며, private raw event와 public hash evidence를 분리했다. retry 간 실행 ID 충돌과 `git`·`rg`·`sed`의 실행·쓰기 우회도 차단했다. 최신 runtime은 provider 실행부터 blind adjudication·finalize까지 E2E로 연결되고, adjudication timeout 시 검증된 성공 session을 보존한 채 미완료 session만 재개한다. 외부 호출 직전 attempt를 ledger에 먼저 기록해 강제 종료에서도 호출 예산을 누락하지 않으며 provenance·ledger 변조와 불완전 checkpoint는 재호출 전에 차단한다. 최신 변경은 workflow를 provider workspace에서 읽는 왕복을 제거하고 frozen context와 같은 provider 입력에 선주입하며, activation evidence의 prompt hash를 고정 probe와 workflow 원문으로 독립 재계산한다. 독립 요구사항의 ID·task·VERIFY 과병합도 계약으로 차단했다. 현재 Plan 회귀 `389 passed`, TDD gate, OMC review `APPROVE WITH NOTES`를 확인했다. 이제 이 구현 커밋을 기준으로 manifest를 재동결하고 새 batch ID에서 Fresh paired Batch A 10건 전체를 재실행해야 한다. Review Quality는 Codex exec structured review와 OMC review의 observed same-diff 10건 역사적 비교와 사용자 gold-label sign-off를 보존했지만, provider 원문을 현재 재검증할 수 없어 Codex native review-agent 대체 판정에는 사용하지 않는다. 두 관측 계층도 추천 근거와 승인 scope를 설명할 뿐, `eligible` 판정·실행 위임·자동 전환은 아직 구현하지 않는다.

최신 Plan 보정 기준(2026-08-11): workflow single-input 교정본의 Fresh paired Batch A 10건은 provider 20/20, retry `0`, efficiency violation `0`으로 완주했고 total token은 baseline `268,509` 대비 OMC `267,679`로 `830`(`0.31%`) 적었다. 그러나 OMC weighted requirement recall은 `0.976`(baseline `1.0`), evidence accuracy는 `0.9667`(baseline `1.0`)로 case 04의 독립 회귀 테스트 requirement를 구현 VERIFY에 흡수해 두 품질 gate를 통과하지 못했다. 최종 판정은 `NOT_PROVEN`이다. 원인을 별도 증거가 필요한 회귀 테스트 산출물의 ID 과병합으로 고정하고, 이를 독립 requirement ID 및 공유 task `supports`에 보존하는 계약과 canonical/template 동기화 테스트를 추가했다. Plan 전체 `391 passed`, TDD gate, OMC review `APPROVE WITH NOTES`를 확인했다. 이 교정은 Batch A 결과를 본 뒤 이루어졌으므로 해당 배치는 진단 전용이며, 다음 대체 claim은 교정 커밋을 고정한 신규 disjoint Batch B에서만 판정한다.

Plan Batch B 후보군 기반 완료(2026-08-11): 신규 disjoint Batch B를 사후 선택하지 못하도록 confirmatory anchor registry를 독립 서명·외부 고정 hash·세대/이전 hash 체인의 append-only 계약으로 고정했다. candidate universe는 관측 기간·저장소·선정 사유·provider 결과 미열람·이전 registry digest를 포함한 전체 envelope hash로 결합하고, universe 동결 뒤 발급한 signed seed receipt와 quota DP로 동일 입력의 shortlist를 결정적으로 재현한다. selection schema v2는 `batch_id`·universe hash·seed receipt·알고리즘·tie-break·전체 selection envelope를 묶으며 batch ID/selection hash replay, registry rollback·변조, provider leakage, quota 부족을 fail-close한다. 이전 배치 중복 후보는 universe에 보존하되 `prior_overlap`·`duplicate_context` 등 검증된 제외 사유로 shortlist에서 제거한다. Plan 전체 `409 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 이는 Batch B 선정 인프라 완료이며 실제 observed 후보군 10건의 구성·동결이나 대체 판정 완료를 의미하지 않는다.

Plan Batch B observed universe 신뢰 경계 보강(2026-08-11): 실제 세션 후보 수집은 explicit completion receipt와 baseline→follow-up Git ancestry·변경 경로·baseline context blob을 대조하고, Unicode 경로는 NUL 구분 Git diff로 검증한다. private inventory는 collector 서명과 외부 고정 hash를 요구하며 label receipt, append-only anchor에 결합된 signed prior commit snapshot, candidate universe draft를 순서대로 검증한다. seal 단계도 trusted prior snapshot에서 커밋 집합을 다시 구성해 runtime 전체 candidate 계약을 통과한 draft만 서명하고, malformed candidate·receipt·signoff는 예외 누출 없이 fail-close한다. 집중 회귀 `21 passed`, Plan 전체 `430 passed`, TDD gate와 OMC review `APPROVE`를 확인했다. 이는 실제 observed universe를 안전하게 만들고 동결할 코드 경로가 준비됐다는 의미이며, observed 10건 수집·독립 gold·paired 실행 전 대체 판정은 계속 `NOT_PROVEN`이다.

Plan Batch B completion receipt 자동 수집 보강(2026-08-11): confirmed `senior_coding` 세션을 단일 pending pointer로 기록하고, post-commit에서 HEAD의 first parent와 정확히 일치할 때만 collector-compatible `completion.json`을 O(1)로 생성하도록 연결했다. `task → review → ship → commit` 흐름은 task 귀속을 유지하되 일반 directive는 pending task를 지우며, 다중 commit·중복 baseline·요청 hash 불일치는 보수적으로 receipt 생성을 건너뛴다. setup은 기존 custom post-commit hook을 force 시 백업·체이닝하고 반복 설치에서도 원본을 보존한다. completion 회귀 `7 passed`, install/candidate universe 회귀 `79 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 따라서 실제 observed 후보를 자동 축적할 실행 경로까지 준비됐지만, prospective confirmatory receipt 수집·universe 동결·독립 gold·paired 실행은 아직 남아 있어 판정은 `NOT_PROVEN`이다.

Plan Batch B roadmap commit receipt 보존 보강(2026-08-17): 실제 운영 순서인 `task → review → roadmap-sync-commit → commit`에서 roadmap directive가 pending coding completion을 지워 구현 커밋 receipt가 누락되던 경계를 수정했다. `omc-ship`과 `roadmap-sync-commit`만 completion 보존 directive로 명시하고 다른 directive는 계속 pending task를 제거해 잘못된 후속 커밋 귀속을 막는다. 원래 task session에만 receipt가 생성되고 roadmap·일반 directive session에는 생성되지 않는 양·음성 회귀를 추가했으며 상태/candidate 연계 테스트 `63 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 기존 누락 receipt는 prospective 증거를 소급 제조하지 않기 위해 backfill하지 않는다. 변경 직전 실제 적격 구현 receipt 감사값은 `4/15`이며, 이후 live 수량의 SSOT는 계속 completion ledger와 관측 종료 후 source snapshot이다.

Plan Batch B prospective work-class lock 완료(2026-08-18): 구현 완료 뒤 receipt에 붙던 자기분류만으로 confirmatory 적격성을 판단하지 않도록, `omc-task` 진입 시 `implementation / synthetic / document_only / benchmark_maintenance`를 세션에 고정하고 외부 저장 private key와 pinned public key로 work-class lock을 즉시 봉인하는 opt-in 경로를 추가했다. 잠금 활성화 시 guard는 pending session을 먼저 기록하고 lock 봉인 성공 뒤에만 confirm해 실패가 확정 세션으로 남지 않으며, completion schema v2와 source snapshot schema v3가 session request hash·baseline full commit·lock 시각·독립 signer를 끝까지 결합한다. source snapshot signer와 inventory collector는 preregistration actor·registration authority·work-class lock signer의 키를 재사용할 수 없고, 기존 completion v1은 historical 호환만 유지하며 prospective Batch B에는 사용할 수 없다. 가변 Git short SHA 정규화, 문서/API 자연어 분류 경계, 기존 pending v1 호환을 포함한 집중 회귀 `100 passed`, 전체 회귀 `2165 passed, 3 skipped`, TDD gate와 OMC review `APPROVE`를 확인했다. 이는 prospective 후보 분류의 사후 조작 경로를 닫은 구현 완료이며, 실제 신규 lock-backed 구현 receipt 수집과 관측 종료 후 snapshot 동결 전 대체 판정은 계속 `NOT_PROVEN`이다.

Plan Batch B work-class lock 운영 전환(2026-08-18): 최초 session ledger 감사에서는 completion receipt `21건`, schema v2 receipt `1건`, cryptographic work-class lock `0건`이었고 유일한 v2 receipt도 `benchmark_maintenance`여서 confirmatory 적격 `implementation` receipt는 `0/15`였다. 이후 저장소 밖 사용자 custody(`~/.config/omc`)에 signer key와 pinned public-key config를 `0600`으로 생성하고, CLI·guard가 환경변수 없이도 이 설정을 fail-close로 읽도록 운영 경로를 추가했다. 잘못된 boolean 환경값, 부분 설정, 느슨한 key 권한은 모두 거부하며 `setup-force`는 custody를 복사하거나 덮어쓰지 않는다. 실제 격리 Git 저장소에서 `implementation` lock 봉인과 preflight `ready`를 확인했고 집중 회귀 `102 passed`, 전체 회귀 `2175 passed, 3 skipped`, TDD gate와 OMC review `APPROVE`를 통과했다. 과거 lock 없는 receipt는 계속 소급 승격하지 않으며, `0/15`는 운영 전환 전 기준선으로 보존한다. 다음 단계는 이 커밋 이후 완료되는 실제 구현 작업부터 lock-backed receipt를 수집하고 ledger를 재감사하는 것이다.

Plan Batch B prospective 수집 계약 보강(2026-08-12): setup-force 회귀 수정까지 생성된 기존 observed receipt 3건은 `pilot_observed` evidence로 보존하되 confirmatory 모집단에서는 제외한다. 신규 Batch B는 signer가 고정한 실제 Git anchor·정확한 관측 시작/종료·provider cutoff·pilot session 목록과 `prospective chronological first-N` 정책을 하나의 digest로 봉인한다. 서명된 preregistration hash는 Git registry commit의 정확한 record와 ancestry에 결합하되, 조작 가능한 Git commit 시각은 관측 전 등록 증거로 신뢰하지 않는다. 대신 사용자가 사전에 승인한 외부 timestamp authority 공개키를 preregistration digest에 고정하고, 그 authority가 registry commit·path와 preregistration hash를 관측 시작 전에 서명한 registration receipt만 검증한다. 호출 시 trusted key를 사후 추가해도 frozen authority와 다르면 거부하며 OMC 내부의 로컬 receipt 발급 API·CLI는 두지 않아 self-signed backdating 경로를 fail-close한다. 전용 v2 collector는 다시 별도의 독립 signer가 전체 source와 work class를 동결한 `complete-session-ledger snapshot`만 수용하고, registration receipt hash와 registry anchor를 source snapshot 및 inventory에 계속 결합한다. downstream label·universe 단계도 preregistration 서명, Git registry anchor, registration receipt와 source snapshot 결속을 전체 재검증하며 collector가 preregistration signer·timestamp authority·source signer 중 하나와 겹치면 거부한다. malformed receipt signoff는 예외를 누출하지 않고 `ValueError`로 fail-close한다. 이 계약을 통과한 구현 세션 중 시간순 첫 15건만 수락하며 나머지 제외 사유를 audit에 기록해 일반 v1 수집 결과로 바꿔치기할 수 없게 했다. confirmatory receipt는 `0/15`에서 시작하며 합성·문서 전용·benchmark maintenance 작업을 허용하지 않는다. 집중 회귀 `30 passed`, Plan 전체 `442 passed`, TDD gate와 OMC review `APPROVE`를 확인했다. 이는 선택 편향을 막는 수집 계약 구현이며, 실제 외부 authority 운영·15건 수집·독립 gold·paired 실행 전 판정은 계속 `NOT_PROVEN`이다.

Plan Batch B 로컬 authority 시도 무효화(2026-08-12): `omc-plan-batch-b-20260812` preregistration과 registry anchor는 보존하지만, timestamp authority private key를 preregistration signer와 같은 로컬 운영 문맥에서 생성·사용한 receipt는 외부 독립성을 증명하지 못하므로 `invalid_local_authority`로 폐기했다. 공개 HEAD에서는 해당 receipt를 제거하고 원본 receipt hash와 발급 커밋을 failure record에 남겼다. 이 배치는 confirmatory 모집단과 대체 claim에 사용할 수 없으며 실제 외부 운영 주체가 생성한 공개키를 먼저 전달하기 전에는 새 batch ID·관측 창을 만들지 않는다. confirmatory receipt는 계속 `0/15`, 판정은 `NOT_PROVEN`이다.

Plan Batch B Sigstore RFC 3161 신뢰 경계 전환 완료(2026-08-12): 로컬 또는 임의 외부 signer 공개키를 신뢰하는 방식을 제거하고 Sigstore Public Good TSA의 RFC 3161 응답을 검증하는 schema v2 경로를 구현했다. preregistration은 사용자가 사전에 승인한 전체 Sigstore TUF trusted-root snapshot digest와 TSA identity를 함께 동결하며, registration evidence는 canonical claim의 SHA-256 imprint·query/response DER digest·nonce·policy OID·serial·발급 시각·인증서 chain을 OpenSSL로 검증한다. registry commit·path·preregistration digest와 관측 시작 전 발급 조건은 registration receipt에 결합되고, 승인된 trusted-root digest는 source snapshot, inventory, label receipt, 최종 universe API와 CLI까지 fail-close로 재검증된다. 기존 schema v1은 호환 유지하며 관련 Plan 회귀 `455 passed`, TDD gate, OMC review `APPROVE WITH NOTES`를 확인했다. 실제 Sigstore TSA evidence 발급과 prospective confirmatory receipt 수집은 아직 시작하지 않았으므로 `0/15`, 판정은 계속 `NOT_PROVEN`이다.

Plan Batch B 실제 Sigstore registration receipt 고정(2026-08-12): `omc-plan-batch-b-sigstore-20260812-v1` preregistration을 registry commit `8a5393f`의 정확한 record에 결합하고, Sigstore Public Good TSA가 발급한 RFC 3161 response를 frozen trusted-root snapshot으로 검증했다. registration receipt는 claim hash `3c8cd049...59222`, response hash `98506604...9dfd4`, receipt hash `4f9366a3...c866`와 관측 시작 전 발급 시각을 보존하며, private artifact에는 원문 evidence와 TSA 응답을 보관한다. registry anchor·receipt validation과 관련 회귀 `46 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 이로써 prospective preregistration 신뢰 경계는 닫혔지만 실제 confirmatory receipt는 아직 `0/15`이며, 대체 판정은 계속 `NOT_PROVEN`이다. 다음 단계는 고정된 관측 창에서 실제 prospective receipt 15건을 시간순으로 수집하는 것이다.

Plan Batch B 다중 저장소 관측 창 활성화(2026-08-21): `omc-plan-batch-b-multirepo-20260820-v1` schema v3 preregistration을 registry commit `7b51960`에 고정하고 Sigstore Public Good TSA registration receipt `f7312f28...c127f`로 관측 시작 전에 등록했다. 관측 창은 `2026-08-20 00:00 KST`부터 `2026-09-18 23:59 KST`까지이며, 최소 3개 저장소·저장소당 최대 5건·시간순 최대 15건과 surface 5종 각 2건, ambiguity `low 3 / medium 4 / high 3`을 사전 고정했다. 8개 실제 사용처는 OMC `0.1.0` receipt v2와 최신 source hash로 setup-force 및 설치 검증을 마쳐 이후 구현 작업에서 lock-backed completion receipt를 생성할 수 있다. 현재 관측 시작 이후 확인된 schema v2 implementation receipt는 `research-auto` 1건뿐이고 source snapshot·coverage 검증 전이므로 공식 `validated_eligible`은 계속 `0`이다. 다음 단계는 계약을 다시 만들거나 benchmark 코드를 늘리는 것이 아니라, 관측 창 안에서 최소 3개 사용처의 실제 구현 receipt를 quota에 맞게 축적하는 것이다.

Plan Batch B completion hook backend 보강(2026-08-21): setup이 항상 `.git/hooks/post-commit`에 설치하던 경로를 실제 Git `core.hooksPath` 기준 resolver로 교체했다. native Git은 effective hooks directory, Husky는 생성 dispatcher인 `.husky/_`를 보존하고 public `.husky/post-commit`, 저장소 내부 custom path는 해당 hook directory에 설치한다. 외부 shared hooks path는 자동 수정하지 않고 `manual_integration_required`로 fail-close한다. 설치 audit은 OMC marker·실행 권한뿐 아니라 Husky dispatcher의 direct shell 호출 또는 표준 `post-commit → h → public hook` 위임 체인을 확인하며 빈 dispatcher와 `exec echo` 같은 비실행 명령을 `unreachable`로 판정한다. native·Husky 실제 commit E2E에서 work-class lock과 결합된 schema v2 receipt 생성을 확인했고 관련 회귀 `188 passed`, 전체 회귀 `2454 passed, 3 skipped`, TDD gate, py_compile, staged diff 검사와 OMC review `APPROVE`를 통과했다. 관측 시작 이후 raw lock-backed implementation receipt는 `research-auto`와 `sixshop3-storefront-fe` 각 1건, 합계 `2건/2개 저장소`로 확인했지만 source snapshot·coverage 검증 전이므로 공식 `validated_eligible=0`과 대체 판정 `NOT_PROVEN`은 유지한다.

Plan Batch B 저장소별 sampling cap 교정(2026-08-21): schema v3 preregistration에 봉인된 `저장소당 최대 5건`을 후보 분류 단계에서도 강제하도록 repository identity를 session ledger에서 끝까지 전달한다. 시간순으로 먼저 관측된 저장소가 5건을 초과하면 초과분은 `repository_limit_exceeded`로 제외하고, 다른 저장소의 후속 receipt가 전체 최대 15건을 계속 채우도록 바꿨다. 이로써 한 저장소가 전역 first-N을 선점해 종료 시 coverage 전체가 실패하는 경로를 닫았다. 현재 complete ledger pre-snapshot 감사는 classified receipt `26건`, provisional lock-backed implementation receipt `7건/3개 저장소`, 저장소 상한 제외 `3건`이며 관련 회귀 `58 passed`, 전체 회귀 `2455 passed, 3 skipped`를 확인했다. 이는 raw 후보 현황일 뿐 source snapshot·coverage 검증 전 공식 `validated_eligible=0`과 대체 판정 `NOT_PROVEN`은 유지한다.

Plan case별 input token 게이트 보강(2026-08-09): benchmark fast path만 root skill에 남기고 일반 workflow를 progressive-disclosure reference로 분리해 root 입력을 `541 bytes`로 줄였으며, runtime receipt를 포함한 실제 instrumented skill 입력은 `663 bytes`로 고정했다. runtime은 provider usage를 `case_id`로 짝지어 `paired_input_token_deltas`와 case별 최대 delta를 계산하고, 한 건이라도 baseline 대비 `100 tokens`를 초과하면 `input_token_overhead`로 실패시킨다. 평균값으로 outlier가 가려지거나 case pairing·합계가 변조된 경우는 `INVALID_RUN`으로 차단하며, usage 미측정은 통과값 `0`으로 간주하지 않는다. canonical skill과 설치 template 2종을 같은 구조로 정렬했고 관련 직접 회귀 `252 passed`, TDD gate, OMC review `APPROVE WITH NOTES`를 확인했다. protocol·skill hash가 바뀌었으므로 기존 manifest와 배치는 재사용하지 않으며, 현재 판정은 Fresh paired 재실행 전까지 `NOT_PROVEN`이다.

Plan native activation 비용 분리(2026-08-09): canonical benchmark skill을 `312 bytes`, 16자리 receipt를 포함한 instrumented 입력을 `333 bytes`로 더 줄이고 protocol v2에 최소 native skill control을 추가했다. raw native delta는 플랫폼의 skill activation floor를 포함하므로 `report_only`로 보존하고, `baseline → minimal native`를 platform floor, `minimal native → OMC Plan`을 controllable payload로 분리해 controllable delta만 `≤100 tokens`로 fail-close한다. 실제 동일 조건 probe 5회에서 raw `+179~180`, platform floor `+101~102`, controllable payload `+77~79`를 기록해 모두 통과했다. retry가 있으면 전체 attempt usage는 외부 호출·total token 예산에 유지하되 activation 비교는 성공 attempt usage로 계산하며, 음수 delta·합계 불일치·control hash drift·reported summary와 signed execution usage 불일치는 모두 거부한다. Runtime `163 passed`, Plan 전체 `371 passed`, TDD gate와 OMC review `APPROVE WITH NOTES`를 확인했다. 이는 activation 입력 측정 계약의 구현 완료일 뿐이며, total token·품질 대체 판정은 새 manifest 기반 Fresh paired Batch A 재실행 전까지 `NOT_PROVEN`이다.

Plan progressive-disclosure activation 재검증(2026-08-10): 일반 요청 workflow 경로를 bare text가 아닌 명시적 code-formatted reference로 고정해 provider가 root router만 읽고 멈추던 경로를 교정했고, canonical skill과 설치 template의 동기화 및 byte budget 회귀를 추가했다. staged skill SHA `a85876a5...4dd2`를 대상으로 동일 조건 fresh activation probe를 실행한 결과 raw native delta `+212`, platform floor `+170`, controllable payload `+42`를 기록해 `≤100 tokens` gate를 통과했다. baseline·minimal native·OMC Plan 3개 실행은 모두 단일 attempt, retry `0`, efficiency violation `0`이었고 관련 회귀 `205 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 이전 실패값 `+2424` 대비 controllable activation 입력은 `2382 tokens`(`98.3%`) 감소했다. 이 결과는 최신 skill의 activation 비용 계약 통과를 증명하지만 paired 품질·total token 판정을 대신하지 않으므로, 커밋 후 새 manifest를 동결해 Fresh paired Batch A를 재실행하기 전 일반 대체 판정은 승격하지 않는다.

Plan protocol v2 실행 계약 봉인(2026-08-10): confirmatory manifest를 schema v5로 올리고 `protocol_sha256`을 독립 signer의 서명 범위에 포함했다. prepare·seal·validate·run·finalize와 CLI가 동일 protocol content hash를 재계산해 비교하며, schema v4·protocol hash 누락·변조·다른 유효 protocol 교체는 provider 호출 전에 fail-close한다. 외부 전송 payload hash는 cases·gold·skill과 full OMC가 실제 읽는 workflow content hash를 결합하고, protocol·runner는 실행 계약에서 분리해 봉인한다. Plan 전체 `386 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 구현은 완료됐지만 고정 커밋 기준 새 manifest 승인·봉인과 Fresh paired Batch A 재실행 전까지 최신 runner의 대체 판정은 `NOT_PROVEN`이다.

Plan activation receipt JSON 계약 교정(2026-08-10): schema v5 Fresh paired Batch A 재실행은 5개 pair를 완료한 뒤 case 6 OMC가 activation receipt를 `R=<nonce>` 형태로 반환해 strict exact-read gate에서 중단됐다. 해당 실패 배치는 보존하되 대체 판정에는 사용하지 않는다. 원인은 skill의 축약 표기와 provider prompt가 값 복사 범위를 모호하게 만든 것이었으며, activation 입력을 `{"runtime_activation_receipt":"<nonce>"}` JSON으로 바꾸고 provider가 문자열 값만 복사하도록 계약을 명시했다. `R=<nonce>`를 계속 거부하는 회귀를 포함해 Plan 전체 `373 passed`, TDD gate, OMC review `APPROVE WITH NOTES`를 확인했다. 코드 계약 교정은 완료됐지만 실제 provider activation probe와 새 schema v5 manifest 기반 Fresh paired Batch A 재실행 전까지 판정은 `NOT_PROVEN`이다.

Plan surface 회귀 검증 분리(2026-08-10): 사용자 관찰 동작은 직접 surface 회귀 테스트 task/VERIFY로 연결하고 data-path 테스트가 이를 대체하지 못하도록 canonical skill·일반 workflow·설치 template 계약을 정렬했다. byte budget `<=420`과 source/template 동기화를 직접 회귀로 고정했으며 TDD gate와 OMC review `APPROVE WITH NOTES`를 확인했다. 스테이징된 `395 bytes` skill 원문(`c6f15856...e6519`)으로 외부 Codex 합성 activation probe를 실행해 receipt 활성화와 직접 surface 테스트 생성을 확인했고, 단일 v1 비점수 관측에서 input token은 baseline `13,366` 대비 OMC `11,213`(`-2,153`, `-16.11%`)이었다. 이 probe는 activation·sanity 근거일 뿐 native activation floor를 분리한 v2 비용 판정이나 10건 품질 대체 판정을 대신하지 않으므로, 변경된 skill hash 기준 Fresh paired Batch A/B 재검증 전 일반 대체 판정은 승격하지 않는다.

Plan Fresh paired Batch A 조건부 대체 판정 및 adjudication 선검증 보강(2026-08-10): 고정 manifest와 동일 `gpt-5.4-mini`·low 조건에서 provider 20회, activation 3회, blind adjudication 7회를 합친 외부 호출 `30/30`으로 10건을 완주했다. baseline과 OMC의 weighted recall은 모두 `1.0`, critical omission은 `0`, executable task rate는 `1.0`이었다. OMC는 evidence accuracy `0.9667 → 1.0`, unsupported assumption `7 → 6`, decision proxy `33 → 26`, total token `260,840 → 260,527`(`-0.12%`)을 기록해 단일 confirmatory corpus 판정은 `PROVISIONALLY_REPLACEABLE`이다. primary gain은 `0.0`이므로 우월성 gate는 통과하지 않았고 `SUPERIOR_CANDIDATE`가 아니다. blind session 2의 중복 edge 출력이 두 번 success로 기록돼 수동 복구가 필요했던 문제는 success ledger 기록 전에 전체 의미 정규화를 수행하도록 교정했으며, `decisions_required` 수를 실제 사용자 개입으로 오인하지 않도록 최종 metrics에 `user_intervention.measurement_status=proxy_only`와 실제 관측값 `null`을 명시했다. 관련 회귀 `233 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 이 변경으로 runtime runner hash가 달라져 기존 manifest의 소급 재최종화는 의도대로 차단되므로, 현재 변경을 커밋한 뒤 새 manifest를 승인·봉인하고 Fresh paired Batch A를 재실행해야 최신 runner에 대한 조건부 대체 판정을 갱신할 수 있다.

Plan paired workflow surface·승인 provenance 보강(2026-08-10): protocol v2 activation을 baseline·minimal native·router native·full OMC 네 surface로 분리하고, router에는 root skill만 제공하며 full OMC에만 `references/workflow.md`를 제공하도록 workspace parity를 고정했다. full OMC는 정확히 한 번의 `cat -- .agents/skills/omc-plan/references/workflow.md`만 허용하고 path·content·command hash receipt를 provider input과 runtime provenance에 결합한다. 외부 전송 승인 digest도 cases·gold·skill뿐 아니라 workflow content hash를 포함하도록 schema를 분리했으며 prepare·seal·run·finalize와 CLI가 같은 값을 재계산한다. 따라서 승인 후 workflow만 교체하거나 exact-read evidence를 위조하면 provider 실행 또는 최종 판정 전에 fail-close한다. Plan 전체 `386 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 구현은 완료됐지만 runner/protocol hash가 바뀌었으므로 기존 manifest는 재사용하지 않으며, 이 커밋 기준 새 manifest와 Fresh paired Batch A/B 재검증 전 일반 `REPLACEABLE` 판정으로 승격하지 않는다.

Plan workflow single-input 전달·요구사항 원자성 보강(2026-08-10): full OMC workflow를 provider workspace 파일과 `cat` exact-read 왕복으로 전달하지 않고 runner가 frozen context와 같은 provider prompt에 직접 선주입하도록 전환했다. root skill의 reference trigger는 실행 projection에서 비활성화하고 workflow content hash·delivery prompt hash·provider input hash를 provenance에 결합한다. activation evidence validator는 고정 probe request와 workflow 원문으로 기대 prompt hash를 독립 재계산하므로 provider가 같은 임의 hash를 execution·delivery 양쪽에 자기 보고해도 거부한다. 독립 요구사항은 별도 ID를 유지하고 각 ID가 최소 하나의 task `supports`와 `VERIFY`에 연결되도록 canonical·template workflow 계약도 정렬했다. Plan 전체 `389 passed`, TDD gate, OMC review `APPROVE WITH NOTES`를 확인했다. 이는 구현·검증 계약 완료 상태이며 요구사항 보존과 token 효과는 이 커밋 기준 Fresh paired 재실행 전까지 `NOT_PROVEN`이다.

Auto-update 결과 계약 보강(2026-08-12): session-start 자동 갱신이 `up_to_date`를 정상 `ok`로, `local_conflict`와 `source_missing`을 각각 `blocked` 사유로 구분하도록 종료 코드와 Hook 매핑을 고정했다. 상태별 회귀 테스트를 추가해 설치 테스트 `83 passed`, Hook 계약 `6 passed`, Codex Hook 설정 `6 passed`, TDD gate 통과를 확인했다. 현재 변경은 커밋 준비가 완료됐으며, 중간 실패 시 부분 설치를 막는 원자적 staging은 별도 후속 작업이다.

Plan case 04 독립 회귀 requirement 과병합 교정(2026-08-11): Fresh paired Batch A에서 OMC가 상세 캐시 무효화 구현과 회귀 검증 task는 제시했지만 테스트 산출물을 별도 requirement ID로 유지하지 않아 weighted recall과 evidence accuracy가 각각 baseline보다 낮아졌다. workflow에 별도 증거로 확인되는 회귀 테스트 산출물을 구현 VERIFY로 흡수하지 않고 독립 ID로 보존하며, task를 공유할 때도 모든 ID를 `supports`에 유지하는 계약을 추가했다. canonical과 두 설치 template의 동기화 및 provider prompt 전달을 직접 회귀로 고정했고 Plan 전체 `391 passed`, TDD gate, 두 차례 OMC review `APPROVE WITH NOTES`를 확인했다. 동일 Batch A는 교정에 사용되어 confirmatory 재사용 금지이며, 실제 효과는 신규 disjoint Batch B에서만 검증한다.

Plan 품질 development pilot 완료(2026-08-02): development 4건에서 OMC Plan과 기준 Plan을 같은 `gpt-5.4-mini`·medium reasoning 설정으로 실제 8회 실행했고, exact-hash strict gold를 독립 키로 승인한 뒤 fresh disjoint 2-session adjudication을 완주했다. signed gold와 pilot report는 서명·hash·provenance 검증을 통과했고, 관련 회귀 `70 passed`와 최종 OMC review `APPROVE WITH NOTES`를 확인했다. 양쪽 모두 요구사항 coverage `1.0`, critical omission `0`, executable step rate `1.0`으로 품질은 동률이었다. OMC는 총 `50,700` tokens로 기준 `48,162`보다 약 `5.27%`, 출력 문자는 `8,533`으로 기준 `7,601`보다 약 `12.26%` 많았다. 따라서 개발 pilot 범위에서는 우월성 주장을 차단하고, 다음 판단은 OMC Plan 출력·비용 최적화 후 holdout 확대 여부를 사람이 결정한다.

Plan runtime 대체·우월성 검증 1차 배치 완료(2026-08-03): 실제 Agent Skill 활성화를 nonce receipt로 증명하고 baseline에서는 이를 금지하는 activation gate, observed anonymized holdout 10건, 동일 모델 설정의 paired execution, 5개 blind adjudication session, usage·품질 acceptance, signed gold·provider batch·runtime attestation 검증을 하나의 runner에 연결했다. 저장소 밖 artifact root 강제, protocol/corpus/gold/skill/prompt hash 교차검증, 빈 task plan 거부와 run-to-finalize 회귀까지 구현했다. 이어 weighted requirement recall `+0.05`, one-sided bootstrap 95% lower bound `> 0`, 고정 seed·10,000회 bootstrap, 독립 confirmation 2회를 우월성 계약으로 고정했다. 1차 실행은 OMC와 baseline 모두 weighted recall `0.94`, critical omission `1`, executable task rate `1.0`으로 품질이 동률이었고, OMC는 evidence accuracy `1.0`(기준 `0.975`), output token `13.44%` 감소, total token `47.27%` 감소를 기록했다. 다만 동일 case 내 동등 주장을 비대칭적으로 채점하던 adjudicator 문구를 보강해 5개 session을 재실행했으며, 이후 10건 paired primary delta는 모두 `0.0`으로 정렬됐다. strict verdict는 두 provider의 공통 critical omission 때문에 `NOT_PROVEN`이므로, 1차 배치 완료를 대체·우월 판정으로 해석하지 않는다.

Plan confirmatory 실행 계약 보강(2026-08-03): 신규 disjoint holdout 실행 전에 sampling frame, prior fingerprint registry, selected case fingerprint, gold 독립성, 승인된 외부 payload, signer를 하나의 manifest로 동결하고 검증하도록 runner를 확장했다. activation probe와 provider 실행에는 `maximum_external_calls` 사전 차단을 적용했고, Codex CLI가 요청별 토큰 hard cap을 제공하지 않는 한계를 반영해 토큰 계약은 `observed_total_token_stop_threshold`로 명시했다. 임계치를 넘긴 호출은 실제 누적 usage와 execution id를 `confirmatory-budget-failure.json`에 남긴 뒤 다음 호출 전에 배치를 중단한다. 사후 gold amendment는 계속 `DIAGNOSTIC_ONLY`이며, 이 구현 완료 자체는 대체 판정이 아니다. 관련 전체 회귀 `1700 passed, 3 skipped`와 OMC review `APPROVE`를 확인했다.

Plan 인접 동작 보존 진단 보강(2026-08-03): 좁은 변경에서 제공된 근거에 직접 인접한 사용자 관찰 가능 비대상 동작을 surface당 최대 1개의 보존 요구사항으로 고정하고, 실행 가능한 task의 `supports`와 `VERIFY`에 연결하도록 `omc-plan` 계약을 보강했다. 암묵적 보존 요구사항을 포함한 development 진단 4건, `preservation_task_link_rate`, 품질·출력 token·사용자 개입 회귀 gate를 pilot에 추가했으며 development 결과가 대체·우월 주장에 사용되지 않도록 `replacement_claim_eligible=false`를 강제했다. 관련 회귀 `117 passed`를 확인했다. 진단을 반영한 실제 holdout 1차 10건은 수집·판정했고, 남은 보존 요구사항 1건의 gold-label은 독립 재판정·서명을 마쳤다. 이제 2차 독립 disjoint 배치 실행만 남았다.

Plan 상태 왕복 최적화(2026-08-04): 주입된 세션 문맥이 현재 요청과 동기화됐거나 격리 입력에 `scripts/omc.py`가 없으면 Phase 0의 상태 명령을 생략하고, 문맥이 없거나 오래됐으며 정확한 script context가 제공된 경우에만 `sync-session` 1회를 허용하도록 canonical skill과 설치 template을 정렬했다. worst 3-case 진단에서는 total token이 `339,204 → 104,638`로 `69.15%` 감소했고 weighted recall `1.0`, critical omission `0`, unsupported assumption `0`을 유지했다. runtime runner는 `state status`, script context 없는 sync, 단일 명령·activation retry를 포함한 반복 sync를 failure receipt로 차단한다. 이 수치는 원인 진단 결과이며 신규 disjoint confirmatory 대체 판정을 대신하지 않는다.

Plan 신규 disjoint confirmatory 2차 중단(2026-08-04): 승인·서명된 신규 10건과 현재 `omc-plan` skill hash를 고정해 동일 `gpt-5.4-mini`·medium 조건으로 실행했다. activation probe와 9개 paired case, 10번째 OMC 실행까지 총 21회 외부 호출은 정상 완료됐지만 누적 observed token이 `1,324,818`에 도달해 사전 stop threshold `1,200,000`을 `124,818`(`10.40%`) 초과했다. runner는 `confirmatory-budget-failure.json`을 남기고 10번째 baseline 호출 전에 fail-stop했으므로 blind adjudication과 품질 acceptance는 수행하지 않았다. 임계치를 사후 완화하거나 9건 부분 결과로 대체 판정을 내리지 않으며, 현재 판정은 `NOT_PROVEN_COST_BUDGET`이다.

Plan confirmatory token 폭증 방지 보강(2026-08-04): 큰 context를 파일별 shell turn으로 반복 읽으며 누적 input usage가 커지는 경로를 차단하기 위해 두 provider prompt가 모든 context를 단일 shell command로 읽도록 정렬하고, 둘 이상의 context read command를 runtime failure로 기록한다. provider별 최대 observed call을 다음 호출의 token reserve로 사용해 남은 예산이 부족하면 호출 전에 `projected_token_stop_threshold_exceeded`로 차단한다. Plan 관련 회귀 `193 passed`와 TDD gate 통과를 확인했으며, 임계치·모델·gold·corpus는 변경하지 않았다. 실제 대체 판정은 수정본으로 신규 disjoint 10건을 처음부터 재실행한 뒤에만 갱신한다.

Plan 신규 disjoint confirmatory 재실행 완료(2026-08-04): 동일 corpus·gold·manifest·`gpt-5.4-mini`·medium 조건에서 10개 pair와 5개 blind adjudication을 모두 완료했고, 총 observed usage `865,871`로 실행 상한 `1,200,000` 안에 완주했다. 두 provider의 weighted recall `0.94`, critical omission `1`, executable task rate `1.0`, evidence accuracy `1.0`은 동률이었고 OMC는 unsupported assumption을 `6 → 3`으로 줄였다. 반면 OMC output token은 `51,978` 대 `21,273`으로 `144.34%`, total token은 `499,604` 대 `324,386`으로 `54.02%` 많았다. 최종 verdict는 `NOT_PROVEN`, 실패 gate는 `critical_omissions`, `output_bloat`, `total_tokens`다. 따라서 reserve/context guard의 완주 효과는 입증됐지만 OMC Plan의 대체 가능성은 입증되지 않았다.

Plan critical omission·output bloat 최소 교정(2026-08-04): 선택된 객체에서 동작이 시작되면 ID·수신자를 상태·payload까지 추적하도록 canonical skill과 설치 template을 보강하고, 제공된 skill/context를 한 명령으로 읽은 뒤 `pwd`·진행 전용 명령 없이 즉시 결과를 작성하도록 runtime prompt·failure gate를 추가했다. 실패 원인 4건 진단에서 output token은 `28,469 → 11,909`로 `58.17%`, total token은 `239,676 → 164,668`로 `31.30%` 감소했다. case 10 blind 재판정은 기존에 누락된 `REQ-selected-recipient`를 포함해 3개 요구사항을 모두 hit했고 unsupported assumption은 없었다. 다만 이전 baseline과의 진단 비교에서는 output ratio 약 `1.26`, total ratio 약 `1.23`으로 acceptance `1.25`·`1.05`를 모두 통과하지 못했으며, fresh paired confirmatory가 아니므로 대체 claim에는 사용하지 않는다.

Plan confirmatory runtime 후속 교정(2026-08-04): 코드 리뷰에서 activation retry의 정상적인 attempt별 context read를 배치 전체 중복으로 오인하는 문제와 `rg "pwd"`처럼 `pwd`가 인자·검색어인 정상 명령까지 차단하는 문제를 발견했다. context 중복 검사를 현재 attempt로 제한하고 shell token상 실제 실행 명령이 `pwd`일 때만 차단하도록 수정했으며, 재현 테스트 2건을 포함한 Plan 회귀 `199 passed`와 OMC review `APPROVE`를 확인했다. 이 교정은 runner 신뢰도 보강이며 대체 claim 자체를 갱신하지 않는다.

Plan confirmatory semantic quota 강제(2026-08-04): 신규 Batch A가 편향된 사례 10건으로도 형식상 통과하는 것을 막기 위해 confirmatory manifest를 v2로 올렸다. runner는 서명된 case label의 surface 분포(UI/state, API/payload, data/indexing, backend/rules, multi-file/legacy 각 2건), ambiguity 분포(low 3, medium 4, high 3), selected-object 사례 최대 2건을 고정 계약과 대조한다. quota 누락·완화·불일치와 legacy v1 manifest는 실행 전에 차단하며 Plan 전체 회귀 `203 passed`를 확인했다. 다음 단계는 이 v2 계약을 만족하는 신규 disjoint Batch A 후보 선정과 사전 등록이다.

Plan confirmatory Batch A 후보 사전 등록(2026-08-04): 다섯 저장소에서 prior confirmatory 20건과 follow-up commit이 겹치지 않는 신규 10건을 선정했다. surface 5종 각 2건, ambiguity low/medium/high `3/4/3`, selected-object 2건을 고정했고 후보끼리 baseline→follow-up으로 이어지는 연쇄 commit과 동일 저장소의 context 경로 재사용도 차단했다. 중복 경로가 있던 API/payload 후보는 독립 commit으로 교체했으며, 각 후보의 first parent와 context 후보 경로가 실제 Git diff에 존재함을 로컬 교차 검증했다. selection digest는 `7d930544...a65b`, Plan 전체 회귀는 `204 passed`다. 이는 후보 사전 등록 완료를 뜻하며, 익명 corpus·독립 gold·manifest 서명·외부 전송 승인은 아직 남아 있다.

Plan confirmatory baseline context 독립 선택 기반(2026-08-04): selection은 commit `3ecfd98`과 digest `7d930544...a65b`로 동결하고 follow-up commit·변경 경로·private repo mapping을 selector packet에서 제외했다. packet은 baseline tree의 파일 수·digest만 포함하며 실제 10건 dry-run에서 `8,184,015 bytes → 3,987 bytes`로 축소됐다. selector는 baseline-only 격리 workspace에서 경로를 고르고, 독립 session·provider output 비노출·Ed25519 서명·packet/tree/blob hash를 통과해야 immutable context manifest가 생성된다. 리뷰 보강으로 prepare 단계가 기존 10건 semantic/prior-overlap 계약과 frozen selection digest를 재검증하고 selection author session을 신뢰 키로 서명된 provenance에서만 가져오도록 고정했다. provenance 원문·서명은 packet에 보존되며 materialize/apply/manifest 검증에서도 trusted author key로 재검증된다. downstream 소비자는 frozen selection과 prior registry로 packet projection 전체를 재구성해 완전 일치를 확인하므로 공격자가 case·repo·commit을 바꾸고 packet hash를 다시 계산해도 거부한다. case id도 단일 canonical path segment로 제한한다. workspace는 허용된 baseline blob만 임시 루트에 추출한 뒤 전체 성공 시 원자적으로 공개해 경로 탈출·제외 경로 노출·부분 산출물 잔존을 차단했다. 실제 독립 selector 실행, 외부 전송 승인, corpus 익명화, gold 작성은 아직 수행하지 않았다.

Plan baseline retrieval 현실성 보강(2026-08-05): 한국어 요청과 영어 코드 식별자가 분리된 synthetic development v2 5건을 별도 frozen corpus로 추가하고, request-only bilingual term expansion·path 우선 점수·문서/test 보정·민감 파일 사전 제외를 baseline-only shortlist에 반영했다. 개발 측정은 25개 후보 중 10개를 선택해 critical/weighted path recall `1.0`, 파일 수 `60%` 절감, credential/local-path 형태 민감 후보 1개 제외를 기록했고 기존 v1 및 Plan 회귀를 유지했다. 반면 기존 Batch A를 사후 정답 경로와 대조한 진단에서는 path recall이 `1/42`에 그쳐 해당 배치는 retrieval 개선 후 confirmatory 재실행에 재사용하지 않는다. 다음 claim 대상은 이 10건을 prior registry로 이동한 뒤 새로 선정·사전 등록한 disjoint Batch A 10건이다.

Plan fresh Batch A 로컬 preflight(2026-08-05): prior registry를 30건으로 확장하고 semantic quota `2/2/2/2/2`, ambiguity `3/4/3`, selected-object 2건을 만족하는 신규 10건을 selection commit `337f00d`, digest `71c8c7ae...6ea5`로 동결했다. signed baseline context manifest가 선택한 blob만 transfer readiness에 포함되도록 연결해 260KB 무관 파일이 payload에서 제외되고 기존 full-baseline 경로와 privacy/tamper 회귀를 유지함을 확인했다. 혼합 언어 저장소에서 한국어 파일 하나 때문에 bilingual expansion 전체가 꺼지던 결함도 파일별 path/content 언어 판정과 source/test 결합 규칙으로 교정하고 ranking algorithm version `3`을 retrieval policy digest에 포함했다. 로컬 packet·workspace·privacy·token gate는 통과했지만, 실제 10건 unlabeled sanity에서 문자 n-gram shortlist가 긴 일반 경로를 과대평가해 QNA/Grid 등 일부 관련 영역을 12개 후보 안에 안정적으로 포함하지 못했다. provider/gold 실행은 시작하지 않았으며, fresh paired Batch A는 BM25 또는 개념 커버리지 기반 후보 생성이 별도 development fixture에서 통과하고 새 policy digest로 다시 동결될 때까지 차단한다.

Plan BM25·개념 커버리지 shortlist 보강(2026-08-05): ranking algorithm version `4`에서 요청·경로·본문을 단어 단위로 분리하고 후보 집합의 희소도를 반영하는 BM25를 적용했다. 한국어 요청의 번역 확장어는 개념 그룹으로 묶어 아직 선택되지 않은 개념을 다음 후보에서 우대하고, 구현 파일 우선·동일 basename 테스트 결합·민감 파일 제외는 유지했다. fresh Batch A와 분리한 synthetic development v3 5건은 30개 후보 중 10개를 선택해 critical/weighted path recall `1.0`, 파일 수 `66.7%` 절감, 고정 prompt reserve를 포함한 보수적 입력 token upper bound `54,926 → 52,385`(`4.63%`) 절감을 기록했으며 v1/v2 회귀도 유지했다. 기존 fresh Batch A baseline만 사용한 unlabeled sanity에서는 QNA 주문 다이얼로그, 다음 날 상승 패턴 스캐너, Grid 문의 완료 흐름이 상위 후보로 복구됐다. 이는 로컬 retrieval gate 통과이지 provider 품질 판정이 아니므로, 새 policy digest 동결과 독립 selector·gold 확보 전까지 provider 실행 및 대체 claim은 계속 차단한다.

Plan BM25 v4 사전등록 anchor·readiness 강제(2026-08-05): commit `a0f68dc`에서 ranking v4 source commit, retrieval policy, fresh Batch A selection, canonical `omc-plan` skill, runtime protocol을 하나의 Ed25519 서명 manifest로 동결했다. commit `e756c9b`에서는 이 anchor를 local transfer readiness API와 CLI의 필수 입력으로 연결하고 검증된 manifest digest를 readiness hash에 포함했다. manifest·selection·skill·protocol drift와 신뢰하지 않은 signer는 readiness 생성 전에 차단하며, 사후 readiness 검증도 같은 anchor로 전체 계약을 재구성한다. provider 실행 허용과 대체 claim은 계속 `false`다. 관련 context-selection·runtime-pilot 회귀와 TDD gate를 통과했고 OMC review는 `APPROVE`였다. 다음 단계는 동결된 baseline-only packet으로 독립 selector를 실행하고 독립 gold를 확보하는 것이다.

Plan confirmatory runtime bridge 완료(2026-08-05): local transfer readiness의 exact context를 signed public corpus·reviewed gold와 연결하고 `prepare-confirmatory` → 승인 hash → 독립 manifest signature → `seal-confirmatory` execution-ready receipt 흐름을 구현했다. schema v3의 source/runtime corpus anchor, prior disjointness, semantic quota, payload·corpus·skill drift, materialized context digest, self-hash 재계산을 함께 검증해 context substitution과 위조된 hash chain을 차단한다. runtime/context-selection 통합 회귀 `138 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 이는 외부 실행 입력 계약이 준비됐다는 뜻이며 대체 판정은 아니다. 다음 단계는 새 외부 signer의 독립 sign-off와 Fresh Batch A provider 실행이다.

Plan baseline-only exact-corpus 교정(2026-08-06): 기존 reviewed gold의 context가 follow-up diff 증거를 포함해 실제 baseline-only runtime context 10건과 모두 불일치하는 것을 확인하고 해당 gold의 Fresh Batch A 재사용을 차단했다. local transfer readiness의 baseline 파일만 고정 정책으로 익명화해 public corpus·runtime corpus·독립 gold author packet이 동일 request와 context를 공유하도록 `prepare-gold-author` 경로를 추가했다. 경로는 확장자만 보존한 순서형 alias로 바꾸고 긴 경로부터 치환하며, 로컬 경로·URL·이메일·제품 식별자·AWS access key를 고정 규칙으로 제거한다. 정책 hash drift, 중복 파일, request 타입, context substitution을 차단하고 실제 10건 exact-corpus 일치와 provider 실행 금지 상태를 확인했다. runtime pilot `85 passed`, context-selection `57 passed`, OMC review `APPROVE`를 통과했다. 외부 검토 대상 payload hash는 `695c5e7b...e05d3`, bundle hash는 `0f6be782...8dac6`이며 아직 독립 gold 작성·검토·서명 전이므로 대체 판정은 계속 `NOT_PROVEN`이다.

Plan gold evidence 실행 준비 보강(2026-08-06): 승인된 baseline-only packet을 durable artifact root에 원자적으로 기록하고, 외부 author와 reviewer의 원문 출력·표준 JSON·session metadata를 분리 보존하는 receipt ledger를 구현했다. 모든 receipt는 사전 승인된 manifest digest와 정확히 결합되며 author/reviewer 순서, payload hash, raw-output schema, evidence root 이탈을 검증한다. privacy gate는 private key, credential assignment, bearer token, AWS key, 로컬 사용자 경로, URL, 이메일, 제품 식별자를 quoted/unquoted·prefix·특수문자 변형까지 차단한다. 실제 10건 packet privacy scan은 finding `0`, runtime pilot `101 passed`, context-selection `57 passed`, TDD gate와 OMC review `APPROVE`를 확인했다. 이는 독립 실행 결과를 신뢰 가능하게 수집할 로컬 계약이 완료됐다는 뜻이며, 외부 author/reviewer 실행과 gold 서명은 아직 수행하지 않았으므로 provider 실행 및 대체 판정은 계속 차단한다.

Plan shell 관측·안전 경계 교정(2026-08-07): confirmatory 실행에서 안전한 repository 탐색까지 `unexpected_shell_command`로 fail-stop하던 과잉 차단을 분리했다. `cat`, `rg`, `sed`, read-only `git` 등 허용된 읽기 명령은 attempt별 `efficiency_violations`로 계수하되 실행을 계속하고, unknown executable·동적 shell·pipe/redirection·compound command와 `git`/`rg`/`sed`의 실행·쓰기 우회 옵션은 `unsafe_shell_command`로 차단한다. raw event JSONL은 private artifact에만 저장하고 public provider batch에는 hash만 남기며, activation retry가 execution id를 재사용해도 `(execution_id, command)` 기준으로 안전·위험 명령을 각각 평가한다. 관련 Plan 회귀 `164 passed`, TDD gate, OMC review `APPROVE`를 확인했다. 이는 runner의 측정 정확도와 안전 경계를 보강한 것이며 실제 대체 판정은 갱신하지 않는다.

Plan Fresh paired adjudication canonicalization 보강(2026-08-07): compact Plan 계약과 todo-list 왕복 차단을 적용한 Fresh paired Batch A 10건 provider 실행은 완주했고 baseline `259,352`, OMC `274,737` total token으로 OMC overhead는 `15,385`(`5.9%`)였다. 다만 blind adjudicator가 같은 task를 여러 requirement link로 반복해 `semantic task link duplicates task`가 발생했으므로 이 실행의 품질 판정은 무효이며 대체 claim에 사용하지 않는다. 원인은 indexed normalizer가 legacy 경로와 달리 중복 task link를 canonicalize하지 않던 계약 불일치로 확정했다. task별 requirement union·순서 보존 병합, task 수 schema 상한, 단일 task-link prompt 계약과 회귀 테스트를 추가했고 Plan 관련 `336 passed`, 전체 빠른 회귀 `1858 passed, 3 skipped, 12 deselected`, OMC review `APPROVE`, ship gate `SHIP READY`를 확인했다. preregistration v5 anchor도 현재 skill·protocol 계약으로 갱신했지만 runner hash가 바뀌었으므로 기존 confirmatory manifest와 실패 배치는 재사용하지 않는다.

Plan Fresh paired Batch A token overhead 재검증(2026-08-08): indexed canonicalization 교정본의 10건 유효 배치에서 baseline과 OMC는 weighted recall·executable task rate·task evidence accuracy가 모두 `1.0`, critical omission이 모두 `0`이었고 unsupported assumption은 `8 → 6`으로 줄었다. indexed canonicalization 교정 후 Fresh Batch A 유효 판정 완료 상태다. output token은 `14,026 → 17,033`으로 허용 비율 안이었지만 total token은 `260,081 → 277,678`, overhead `17,597`(`6.77%`)로 `≤5%` 기준을 넘었으며 실패 gate는 `total_tokens` 하나다. 최종 decision은 `NOT_PROVEN`이다. 이후 canonical skill 입력과 treatment prompt를 압축하고 실행 가능한 RED·GREEN·VERIFY 구체성을 복원해 관련 회귀 `240 passed`와 OMC review `APPROVE WITH NOTES`를 확인했지만, 이 후보는 아직 Fresh paired로 재측정하지 않았다. reasoning token이 provider usage에 직접 노출되지 않으면 hidden reasoning 감소를 별도 claim하지 않으며, Batch A 통과 판정의 상한은 `PROVISIONALLY_REPLACEABLE`이다.

Plan runtime adjudication E2E 연결 완료(2026-08-09): `run-batch`가 만든 provider batch·blind session·private mapping을 검증한 뒤 독립 adjudicator 5회를 실행하고, 표준 결과·attempt ledger·execution id를 `finalize` 입력으로 직접 넘기는 `run-adjudication` 단계를 추가했다. runtime attestation과 protocol hash를 외부 호출 전에 검증하고, 실패·timeout attempt도 ledger에 남기며, provider 호출과 adjudicator 시도를 합친 frozen end-to-end external call budget을 강제한다. decision proxy도 provider별 count/mean과 delta로 집계해 최종 gate에서 검증한다. 관련 직접 회귀 `214 passed`, TDD gate와 OMC review `APPROVE WITH NOTES`를 확인했다. 확장 Plan 테스트의 기존 preregistration skill hash 불일치 2건은 이번 변경과 독립된 잔여 정합성 이슈로 분리하며, 이 구현만으로 대체 판정을 갱신하지 않는다.

Plan runtime adjudication checkpoint 안전 재개 완료(2026-08-09): session timeout이나 프로세스 중단 후 전체 adjudication을 버리지 않고, 저장된 session 결과의 provenance와 attempt ledger 결합을 재검증해 성공 session은 건너뛰고 실패·미실행 session만 재개하도록 보강했다. 예상 밖 결과 파일, 알 수 없는 session, 결과와 맞지 않는 success attempt, 불완전 checkpoint는 외부 재호출 전에 차단하며 retry attempt까지 frozen end-to-end call budget에 포함한다. 외부 Codex 호출 직전에 failed attempt를 먼저 영속화하고 정상 완료 시 같은 attempt를 success로 갱신해 강제 종료에서도 실제 호출 횟수가 누락되지 않는다. 빈 결과 디렉터리만 남은 호출 전 중단은 안전하게 재개하되 ledger 없는 결과 artifact는 차단한다. Plan·runtime 통합 회귀 `214 passed`, TDD gate, OMC review `APPROVE`를 확인했으며 대체 판정은 계속 `NOT_PROVEN`이다.

Plan confirmatory 로컬 전송 경계(2026-08-04): 기준 구현을 commit `55752f4`로 고정한 뒤, 독립 selector에 전달될 baseline-only payload의 파일·요청·byte·content hash를 로컬 transfer manifest로 고정하고 UTF-8 일반 파일만 허용하는 privacy audit를 추가했다. private key·credential assignment·AWS access key·로컬 사용자 경로와 symlink를 실행 전에 차단한다. 실제 baseline의 PNG·ICO 같은 binary blob은 전체 실행을 막거나 조용히 사라지지 않고 hash·크기·`omitted_binary` 사유를 manifest에 남긴 뒤 payload와 token accounting에서 제외하며, 판별 사유도 `invalid_utf8`과 `control_character`로 구분해 실제 분기와 감사 증거를 일치시켰다. 10개 case별 입력 token은 실제 canonical JSON byte 수와 고정 prompt reserve를 합친 보수적 상한으로 계산해 호출·timeout·입출력 token 상한과 연결했다. source가 포함된 readiness 출력은 OMC 저장소와 모든 원본 저장소 내부 경로를 거부한다. provider-native receipt verifier가 없는 현재 단계는 `operator_attested`, `replacement_claim_eligible=false`, `provider_execution_allowed=false`로 강제해 로컬 서명을 독립 provider 증거로 승격할 수 없다. Plan 회귀 `224 passed`, TDD gate, 최종 OMC review `APPROVE`를 확인했다. 이 readiness는 정확한 외부 전송 payload 승인 전 로컬 검토용이며 실제 외부 selector 실행·독립성 증명·대체 판정은 아직 수행하지 않았다.

V5 단일 child pilot 보강(2026-07-15): `noop_shadow` gate가 operator approval, plan/scope fingerprint, child/dependency readiness, sensitive scope, 단일 시도·시간·출력 예산, idempotency를 검증하도록 구현·리뷰 완료했다. 누락된 안전 메타데이터도 명시적으로 차단하며, orchestrator 위임 surface를 통한 통합 회귀까지 확인했다. 실제 executor 호출과 자동 재분배는 여전히 열지 않는다.

V5 delegation contract hardening(2026-07-15): domain order의 malformed 입력, 불완전한 `execution_order`/`recovery_action`, 모순된 ready order와 누락된 `recommendation_only`를 명시적으로 거부하도록 보강했다. 관련 회귀 테스트와 TDD gate를 통과했으며, 실제 executor 호출·자동 retry·자동 재분배는 계속 비활성 상태다.

V5 단일 child execution grant(2026-08-16): 기존 `noop_shadow`의 operator approval, scope, dependency, budget, idempotency 검증을 그대로 재사용해 `execution_requested=true`와 `execution_mode=single_child_opt_in`이 함께 명시된 단일 child에만 bounded execution grant를 발급한다. grant는 scope hash·approval expiry·단일 시도·시간·출력 예산과 parent review fallback을 보존하지만 프로세스나 외부 LLM을 직접 호출하지 않는다. 기본 shadow 동작과 실제 호출 차단은 유지하며 관련 executor/orchestrator 회귀 `129 passed`를 확인했다. 다음 경계는 grant 소비 ledger와 실제 executor adapter를 별도 opt-in으로 연결하는 것이다.

V5 execution grant 예약 전이(2026-08-16): 발급된 single-child grant를 실제 호출 전에 idempotency key와 ledger revision 기준으로 예약하는 CAS-ready 순수 전이 계약을 추가했다. scope hash·approval expiry·단일 시도·시간·출력 예산을 다시 검증하고 중복 예약, stale revision, 만료, scope 불일치, malformed ledger·grant는 예외 누출 없이 차단한다. 입력 ledger는 변경하지 않고 revision이 증가한 새 ledger와 reservation을 반환하며 실제 파일 잠금·저장과 executor 호출은 여전히 수행하지 않는다. 관련 executor/orchestrator 회귀 `136 passed`를 확인했다.

V5 execution grant 원자적 파일 ledger(2026-08-16): CAS-ready reservation 전이를 ledger별 배타 파일 잠금 아래 최신 revision에 적용하고 `fsync → atomic replace → directory fsync`로 저장하는 adapter를 추가했다. 손상된 JSON과 symlink 경로는 원본을 수정하지 않고 차단하며, 동일 grant를 두 프로세스가 동시에 예약해도 한 건만 성공하는 회귀를 고정했다. 실제 executor 호출과 자동 retry는 계속 비활성 상태이며 관련 executor/orchestrator 회귀 `139 passed`를 확인했다.

V5 opt-in single-child executor adapter(2026-08-16): 승인·예약된 grant를 executor·scope·approval expiry·단일 시도·시간·출력 예산에 다시 결속한 뒤 `reserved → running`을 원자적으로 claim한 호출자만 기존 headless provider 경로를 1회 실행하도록 연결했다. 이 경계에서는 Codex 네트워크 fallback과 모든 retry를 끄고, 성공·실패·timeout·runner 예외를 terminal ledger에 기록하며 출력은 grant 상한에서 잘라낸다. claim lock 획득 뒤 approval 만료를 다시 검증하고, claim 또는 terminal 파일 교체 뒤 durability 확인이 불확실하면 최상위 `indeterminate`로 중단해 성공 위장과 이중 실행을 막는다. sub-second 실행 예산은 절삭 없이 provider에 전달하고 malformed runner output도 예외 누출 없이 terminal 실패로 귀결한다. strict Codex의 실제 네트워크 timeout에서도 다른 provider를 호출하지 않는 회귀를 포함해 직접 관련 executor/orchestrator·roadmap 회귀 `196 passed`, TDD gate, OMC review `APPROVE`를 확인했으며, 자동 재분배와 무인 retry는 계속 비활성 상태다.

V5 terminal parent-review recovery 계약 완료(2026-08-17): single-child executor의 `failed`·`timeout`, claim/ledger `indeterminate`, 실행 후 finalization `blocked`를 하나의 bounded `parent_review` surface로 정규화했다. 결과에는 실제 execution status/reason과 recovery reason/action을 분리해 보존하고 `automatic_retry_allowed=false`, `automatic_redistribution_allowed=false`를 강제한다. 성공 결과와 실행 전 입력·binding 차단에는 recovery를 붙이지 않아 실행 실패 복구와 사전 gate 거부를 섞지 않는다. 동시에 Full critique 정책의 stale 회귀 테스트를 현재 계약인 quality `REVISE → task_retry`, quality `BLOCK → HOLD`, task orchestration failure → `plan_retry`로 정렬했다. 관련 executor·critique·autopilot 회귀 `200 passed`, 전체 빠른 회귀 `2100 passed, 3 skipped, 12 deselected`, TDD gate와 OMC review `APPROVE`를 확인했다. parent-review 자동 실행이나 child 재분배는 열지 않았으며, 다음 단계는 opt-in 운영 표본에서 recovery surface가 실제 parent 판단으로 소비되는지 검증하는 것이다.

V5 parent-review ledger 증거 영속화(2026-08-17): terminal `failed`·`timeout` 결과를 원자적 execution ledger에 확정할 때 검증된 bounded `parent_review`를 자동 파생해 같은 outcome에 저장하도록 연결했다. 호출자가 recovery payload를 주입하지 못하며 `succeeded` outcome에는 기록하지 않아 성공과 복구 증거를 분리한다. 반환 surface와 실제 파일 ledger가 같은 recovery 계약을 보존하는 회귀를 고정했고 executor·orchestrator·autopilot 관련 회귀 `180 passed`, TDD gate와 OMC review `APPROVE`를 확인했다. 자동 retry·재분배는 계속 비활성 상태이며, 다음 단계는 opt-in 운영 표본에서 ledger의 `parent_review`가 실제 parent 판단과 후속 승인 기록에 소비되는지 검증하는 것이다.

V5 parent-review operator 판단 기록(2026-08-18): terminal `failed`·`timeout` ledger의 검증된 `parent_review`를 대상으로 scope·parent·child·idempotency key·recovery action·승인 만료에 결속된 사람의 `hold|acknowledge` 판단을 revision 기반 단일 전이로 기록하도록 연결했다. 중복 판단, terminal status 불일치, malformed decision·approval, stale revision은 원본 ledger를 변경하지 않고 차단하며 파일 adapter는 배타 잠금과 `fsync → atomic replace → directory fsync`를 유지한다. 임시 파일 생성 실패도 예외 누출 없이 write failure로 닫고, replace 이후 durability 실패는 `indeterminate`로 분리한다. 단위 회귀 `84 passed`, executor·orchestrator·autopilot 관련 회귀 `190 passed`, TDD gate와 OMC review `APPROVE`를 확인했다. 이 단계는 parent 판단의 감사 증거만 남기며 `automatic_retry_allowed=false`, `automatic_redistribution_allowed=false`를 계속 강제한다. 다음 단계는 opt-in 운영 표본에서 판단 기록의 실제 소비와 후속 수동 조치 결과를 관측하는 것이다.

V5 parent-review 수동 후속 결과 기록(2026-08-18): 기록된 `hold|acknowledge` 판단 이후 사람의 실제 조치 결과를 `resolved|still_blocked|escalated`로 한 번만 ledger에 영속화하는 revision 기반 전이를 추가했다. follow-up은 approval·parent·child·scope·idempotency key에 다시 결속하고 terminal outcome과 recovery action을 재검증하며, 자동 retry와 자동 재분배가 수행됐다는 입력은 명시적으로 차단한다. 파일 adapter는 배타 잠금과 `fsync → atomic replace → directory fsync`를 유지하고, 잠금 획득·임시 파일 생성 실패를 구조화된 fail-close 결과로 반환하며 replace 이후 durability 실패는 `indeterminate`로 분리한다. 단위 회귀 `97 passed`, TDD gate와 OMC review `APPROVE`를 확인했다. 다음 단계는 코드 계약 확장이 아니라 opt-in 운영 표본에서 decision→manual action→follow-up 결과가 실제로 누적되는지 관측하는 것이다.

V4 운영 검증 보강(2026-07-13): `cost_per_successful_task` 집계에서 simulated/dry-run 성공 비용을 제외하고 실제 observed run 비용만 반영하도록 수정했으며, 해당 오염 반례를 overview 회귀 테스트로 고정했다. overview 테스트 33건과 TDD gate를 통과했다. 기존 pipeline readiness 출력 불일치 2건은 이번 변경 범위 밖의 잔여 리스크로 남긴다.

V4 실행 안정성 보강(2026-07-16): Codex headless 실행에 지원되는 비대화형 `approval_policy="never"` 사전검증을 추가하고, 지원되지 않는 설정은 모델 호출 전에 fail-fast하도록 정리했다. DNS 장애는 provider fallback 대상과 구분해 `network_unavailable`로 기록하며 autopilot이 같은 장애를 재시도하지 않도록 연결했다. partial timeout output과 provider 종료 상태도 runtime에 보존한다. 관련 회귀 테스트와 quickstart 문서 계약 보강까지 staged 상태이며, 직접 관련 테스트 `138 passed, 1 skipped`, health 제외 전체 회귀 `1112 passed, 1 skipped`를 확인했다. health 테스트는 외부 실행 경로 때문에 개별 테스트가 약 60초 걸리므로 운영 검증에서 별도 느린 테스트군으로 관리한다.

Health 검증 실행 경로 정리(2026-07-16): 외부 품질 검사를 호출하는 `scripts/test_omc_health.py` 전체에 `slow` marker를 부여하고, `scripts/conftest.py`에 marker를 등록했다. README와 quickstart에 빠른 회귀(`-m "not slow"`)와 느린 health 검증(`-m slow`) 명령을 분리해, 일상 개발에서 불필요하게 외부 검사를 기다리지 않도록 했다. marker 수집·제외 경계와 기존 빠른 회귀군을 확인했으며, 전체 slow health 실행은 운영 검증 시점에 별도로 수행한다.

Health 저장소 정합성 보강(2026-07-16): `omc_kit`에 맞춰 health를 Python 문법 검사와 OMC scripts 테스트 수집 기준으로 단순화했다. 외부 패키지 설치나 무관한 프런트엔드 도구 실행 없이 health 테스트 `11 passed`, 기존 scripts 회귀 `1112 passed, 1 skipped`를 확인했다.

Cost-Quality Policy Layer 1차 구현(2026-07-16): 정책 비교 acceptance를 `hit / over_conservative / over_aggressive / pending`으로 분류하고, 실패 outcome과 비용 근거 부족을 `hit`로 오판하지 않도록 보수적으로 처리했다. Policy → Executor handoff 계약에 정책 프로필·이유·확신도·사용자 선택 여부·executor fallback을 고정하고, recommendation-only와 `execution_allowed=false`를 validator로 검증한다. 관련 routing/orchestrator 회귀 `134 passed`, staged TDD gate 통과.

Policy comparison observed 연결 완료(2026-07-18): observed run을 `policy_profile / outcome / quality_failure / cost_delta / eligible / exclusion_reason` 입력으로 정규화하고, 정책 프로필·품질 증거·비용 증거가 없으면 `pending`으로 보수적으로 차단한다. `policy_comparison` 결과를 benchmark report, autopilot overview, collected summary에서 동일하게 집계하며 status/reason counter와 빈 summary 기본 schema를 맞췄다. 관련 회귀 `388 passed, 1 skipped`, TDD gate 통과.

현재 이 작업의 구현 범위는 완료됐다. 남은 것은 실제 운영 observed 데이터에서 정책별 `hit / pending / over_aggressive / over_conservative` 비율과 비용·품질 상관관계를 축적해 threshold와 정책 선택의 타당성을 검증하는 일이다. 비용 증거가 없는 데이터는 계속 `pending`으로 유지하며, 자동 executor 전환·무인 reroute는 이 검증이 끝날 때까지 열지 않는다.

## 제품 목표 상태

최종 목표는 아래 다섯 가지를 만족하는 것이다.

1. 사용자가 모델을 직접 고르지 않아도 된다.
2. 한 요청 안에서도 step별로 모델 강도가 다를 수 있다.
3. 실패가 반복되면 더 강한 모델로 자동 승격된다.
4. 성공/실패/비용 로그가 쌓여 정책이 계속 개선된다.
5. 필요하면 사용자가 override할 수 있고, 선택 이유를 설명할 수 있다.

## 로드맵 상태판

현재 구현 기준으로 보면 상태는 아래에 가깝다.

<details>
<summary>세부 상태판</summary>

| 트랙 | 상태 | 근거 | 다음 빈칸 |
|---|---|---|---|
| V1. Skill-based Routing | 완료 | `resolve_task_routing()`, `OMC_ROUTING_POLICY`, `role_suggest`의 `task_kind_hint`, `autopilot`의 `task_kind` 전달이 이미 연결되어 있다. | 운영값 미세조정 |
| V2. Step-level Routing | 완료 | `autopilot` step metadata schema와 normalization이 있고, `complexity/risk/preferred_profile/sensitive_paths`가 실제 profile 선택에 반영된다. V3 승격 규칙 연결도 완료되어, 최근에는 `routing_reason_codes`, `routing_reason_summary`가 step state와 overview surface까지 연결돼 step별 선택 근거를 바로 읽을 수 있게 됐다. 이는 기존 `선택 근거 기록 고도화` 빈칸을 구현 측면에서 닫은 상태다. | 운영 surface 미세조정 |
| V3. Failure-driven Escalation | 완료 | V3-1 수준의 failure class 분리와 persistence/report 반영은 들어갔고, `retry_exhausted`와 `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 및 critique 경로 runtime consumption이 완료됐다. 추가로 `task_retry` / `plan_retry` 성공 경로, `timeout` 경로, `failed` 계열 주요 경로의 decision payload shape 일반화와 `orchestration_failure` decision policy 연결이 반영됐다. 복구 경로도 `_recovery_target_from_decision` 헬퍼로 공통화했다. task 단계 orchestration failure(`bad_entry_skill`/`metadata_missing`)는 `plan_retry`, critique/review의 revisable quality failure는 첫 판정부터 `task_retry`로 수렴하며, blocking quality failure는 `HOLD`한다. `task_retry`의 구조화된 `REASON_CODE + VERDICT: BLOCK`은 bounded output evidence와 함께 `decision=hold`로 저장되고 실제 runtime도 즉시 top-level `hold`로 종료한다. reason code가 비어 있을 때만 `block_without_reason_code`로 승격해 기존 fallback을 유지하므로 silent completion과 payload/runtime 불일치를 함께 막는다. resume 이후 `task_stage_plan_retry_count`도 더 이상 중복 소비되지 않도록 정리됐고 `timeout` 경로도 공통 decision 엔진(`_decision_policy_entry`)으로 직접 소비한다. 남은 축은 telemetry 기반 정책 비교와 multi-run tuning이다. | telemetry report MVP + multi-run KPI 고도화 |
| V4. Telemetry-driven Tuning | 완료 | step state에 `token_usage`, `cost_estimate` 저장은 들어갔고, `benchmark-report`에 `had_reroute`, `recovered_after_retry`, `total_cost_usd`, `total_tokens` 같은 single-run telemetry가 반영됐다. 추가로 `.omc/runs/` 기준 `reroute_rate`, `retry_to_success_rate`, `cost_per_successful_task` multi-run KPI summary와 current-path 중복 제거까지 반영됐다. 정책 비교 리포트 자동화 1차와 telemetry report 정리 2차까지 들어갔고, V4 KPI 2차의 baseline/timebox 기준도 고정됐다. 최근에는 neutral observed seed를 readiness 입력에서 제외하고, observed_output run도 별도 case로 수집하되 `mode_accuracy`/`task_start_delay` decision metric은 왜곡하지 않도록 exclusion 규칙을 넣었다. 추가로 observed_output producer도 partial metadata를 그대로 저장하지 않도록 schema/backfill 규칙을 고정해, 실제 샘플이 benchmark 입력으로 들어가기 전 shape을 먼저 안정화했다. 이어서 readiness coverage 1차로 overview KPI에 `readiness_same_surface`가 노출되고, 2차로 benchmark report payload에 `readiness_status_line`, `baseline_comparison_status`, `next_kpi_blocker`, `baseline_comparison_line`까지 실려 baseline 비교 가능 여부를 바로 읽을 수 있게 됐다. 최근에는 autopilot overview에도 `readiness_status` / `next_kpi_blocker`를 노출해 샘플 부족 이유를 콘솔에서 바로 읽게 했고, observed sample contract 1차로 partial metadata observed_output이 버려질 때도 rejection count와 reason map이 summary에 남도록 보강했다. 이어서 policy comparison summary 1차를 넣어 deferred/ready 상태를 decision payload 한 줄로 바로 읽게 만들었다. 추가로 response-mode threshold 정책 검증 1차로 collection summary와 comparison summary의 ready/pending 의미를 브리지 테스트로 고정했고, threshold taxonomy(`ready / pending / ambiguous`)와 candidate 비교도 count-aware benchmark check로 보강했다. 여기에 더해 `state status`가 `.omc/state/runs`와 `.omc/runs`를 함께 집계하도록 복구해 실제 observed run 축적량이 status 화면에서 축소 표기되지 않게 했고, `pipeline_history_runs(.omc/runs)` 라벨까지 노출해 어떤 저장소에서 온 관측치인지 바로 읽게 만들었다. 또 `observed-collect` clean-scope 전제조건이 로컬 `.omc/tasks` 수정에만 머무르지 않도록 `templates/shared_tasks` 설치 경로를 추가해 `setup --force` 대상 저장소까지 동일 정책이 전파되게 맞췄다. 최근 진행분으로 collected observed summary와 comparison summary 양쪽에 `reroute_rate`, `retry_to_success_rate`, `cost_per_successful_task` 3개 KPI가 같은 형식으로 실리고, 이를 run-based fixture 회귀 테스트로 고정했다. 추가로 baseline flag drift, pending/ready 경로의 rejection count-only suffix, ready 경로 count-only suffix까지 핵심 반례 보강이 닫혔다. 최근에는 decision surface에 `next_priority_recommendation`, `next_priority_reason`를 추가해 observed sample 부족, same-surface 부족, policy pair 부족, baseline 입력 drift, ready 후 operator 병목 검증까지 다음 우선순위 1개를 직접 추천하도록 보강했고, 관련 경계 케이스 회귀 테스트도 함께 고정했다. 이어서 collected observed summary에도 `observed_reason_signals_present`를 노출해 ready 상태에서 operator 병목 검증 추천이 왜 나왔는지 summary surface에서 바로 읽을 수 있게 맞췄다. 이번 배치에서는 invalid observed_output이 여러 필드를 동시에 놓쳐도 rejection case 수와 reason map이 summary/report에 그대로 남도록 explicit rejection metadata 집계를 보강했고, 이와 함께 `로드맵 최신화 + 현재 진행 상태 체크`처럼 혼합 의도의 실제 observed_request를 fixture에 추가해 `$omc-plan` 추천 정밀도를 별도 회귀 테스트로 고정했다. 이어서 autopilot overview에도 collected summary surface(`baseline_line`, `policy_summary`, `reason_signal`)를 직접 노출해 report/decision을 열지 않아도 운영 화면 한 장에서 readiness 의미를 읽게 했고, sample 부족 deferred 경계와 ready reason-signal 경계를 overview fixture로 별도 고정했다. 최근에는 overview에도 `next_priority_recommendation`, `next_priority_reason`가 직접 surfaced되도록 연결해, 운영자가 콘솔 overview만 보고도 다음 1개 액션을 바로 판단할 수 있게 맞췄다. 추가로 `cmd_run` 계열 observed task가 `.omc/runs` 기록을 남기고 `completion_requires_real_runs=true`를 실제 observed 증가 조건으로 묶이도록 보강해, 태스크 완료와 dataset 증가가 어긋나지 않게 맞췄다. 이어서 `observed-collect-reverse`, `observed-ready-surface`, `omc_observed_collect_batch.py`를 추가해 reverse/same-surface observed 축적을 반복 실행할 수 있게 했고, unique runtime task copy 전략으로 중복 fingerprint 없이 샘플을 누적하도록 조정했다. 현재 overview 기준 `observed_samples=21`, `readiness_same_surface=8`, `distinct_policy_pairs=2`, `baseline_comparison_status=ready`가 충족됐고, `v4b-operational-validation` 실행도 completed로 남아 overview / collected summary / decision surface 정렬이 확인됐다. 이번에는 benchmark decision surface가 `recommended_executor`, `executor_reason_summary`, `executor_fallback`까지 직접 노출하고, 해당 summary 계약도 `resolve_policy_summary()` 기준 회귀 테스트로 잠겨 decision/report/contract alignment가 한 번 더 닫혔다. 구현 반례 추가보다 운영 observed 기준 충족과 완료 판정이 더 중요한 단계였고, 그 1차 기준까지 닫혔다. 실패·타임아웃 경로에서도 executor, task kind, 정책, 환경 fingerprint, `failed_at`을 보존해 capability observation이 실패 실행을 누락하지 않도록 보강했으며 관련 회귀 테스트를 추가했다. | 운영 신뢰도 유지 및 후속 기준 미세조정 |
| V5. Learned Orchestrator | 부분 반영 | `decompose-only`, 추천-only executor handoff, `noop_shadow`, child decision contract와 `delegation_observed.child_decisions` surface까지 구현·검증됐다. 명시적 `single_child_opt_in`의 bounded 실제 실행과 exact 2-child sequential opt-in까지 열렸다. 2-child 경로는 별도 원자적 ledger와 acceptance adapter로 실제 순차 실행을 검증한다. 추가로 `3–5` child DAG의 graph·prompt·개별 grant·aggregate budget을 승인 hash에 결속하고 dependency·cycle·scope·expiry·idempotency·numeric budget을 fail-close하는 bounded N-child grant 계약까지 완료했다. N-child 경로는 현재 ready child 계산까지만 수행하며 scheduler/provider 호출과 retry·재분배·fallback·resume은 금지된다. | scope 정규화·approval ID 정책을 확정한 뒤 N-child scheduler와 제한적 자동 재분배 gate를 별도 검증 |
| Operator Experience | 진행중 | `plan/task/review` 진입점과 추천은 있으나, 실제 사용감은 아직 “더 똑똑한 흐름 제어”까지는 아니다. 다만 `plan / task / review / critique / investigate / ship`의 machine output을 공통 `omc-output/v1`으로 통일하고 envelope을 사용자 화면에서 숨겨, 사람이 읽는 본문과 자동 라우팅 데이터가 섞이던 문제를 닫았다. 기존 평문 envelope 호환, malformed envelope fail-close, reason-aware routing과 Critique의 `APPROVE WITH NOTES` 성공 소비까지 회귀 테스트로 고정했다. 기존 `decision / risk / next_action` 의미 보강과 `2-1 next_action 공통화`도 유지되며, `2-2 reroute / delay UX`와 `role_suggest` 시작 스킬 오판 패턴 보강까지 반영됐다. 최근에는 `top-expensive-flows` benchmark CLI와 `verification_checklist`의 고비용 흐름 커버 맵도 추가돼, 병목 흐름을 더 직접적으로 보게 됐다. response-mode 벤치마크에는 실제 review/plan/status/reentry 요청 케이스를 추가했고, next-action 파서는 한글/영문 라벨과 구분자 변형까지 흡수하도록 정리했다. 최근에는 `로드맵 최신화 + 현재 진행 상태 체크`, `로드맵 기준 어떤 작업들이 남은거야` 같은 roadmap-sync observed_request도 fixture에 편입해, status성 표현이 섞여도 `$omc-plan`으로 정렬돼야 하는 경계를 별도 케이스로 잠갔다. 이어서 expensive flow report에 `reroute_reason / reroute_signal / output_bloat_reason / compression_signal`을 직접 노출해, reroute와 과출력 병목을 한 화면에서 읽을 수 있게 맞췄다. 이번에는 `추천이 task야 critique야`, `plan full 이면 critique 해야하나`, `방금 거는 task 해도 되나`, `이거 review 해야해?` 같은 설명/허용 여부 질문도 observed_request fixture로 고정해, 스킬 추천 질문을 곧바로 task 진행으로 오해하지 않도록 wrong_next_step 경계를 한 단계 더 잠갔다. 추가로 `우리 방금 추천은 plan 이었는데 critique 하니까 치명이 나왔어`, `plan 을 했을 때 추천은 task 가 나온 상태고 내가 혹시 몰라서 critique 했더니 구멍을 발견한거잖아`, `critique 활용해서 진행하자` 같은 실제 요청문도 fixture에 넣어, 설명 질문이 아니라 실제 critique reroute가 맞는 경계를 observed_request 기준으로 더 직접 잠갔다. 여기에 더해 현재 구현 라운드를 언제 닫을지 판단할 수 있도록 `Operator Experience 마감 기준`을 문서에 명시했고, `.omc/tasks/operator-experience-validation.json` autopilot completed 증빙까지 확보해 수동 설명력 검증과 자동 실행 완료가 같은 결론으로 수렴하도록 맞췄다. 최근에는 failed status follow-up도 shared decision input으로 이관해 review/debug/build 후속 안내가 status surface와 같은 규칙으로 수렴하도록 맞췄고, autopilot overview follow-up도 같은 shared decision input으로 옮겨 overview surface까지 후속 액션 규칙이 일관되게 수렴하도록 맞췄다. observed_request neutral seed가 candidate 규칙으로 `expected_next_action`을 스스로 만들지 않게 다시 분리해 benchmark 자기검증도 제거했다. 이번에는 `ready` 입력 기준 `overview / collected_summary / report_decision`가 같은 `next_priority_recommendation`과 reason을 유지하는 정합성 회귀 테스트도 추가해, surface별 설명 문구 drift를 다시 바로 잡을 수 있게 했다. 현재 세션의 로컬 커밋 선택은 scope·blob·mode·TTL에 결속된 단일 소비 receipt로 상속해 반복 확인을 줄이되, 선택되지 않은 파일과 push·PR·deploy 같은 권한은 계속 분리한다. | 운영 observed 검증 + 마감 기준 유지 점검 |

</details>

### Plan Quality Validation

`omc-plan`이 기준 Plan보다 요구사항 보존, 범위 통제, 의존성 표현, 실행 가능성에서 실제로 나은지 동일 조건으로 검증하는 품질 트랙이다.

| 상태 | 현재 근거 | 다음 마일스톤 | 종료 기준 |
|---|---|---|---|
| 진행중 | 최신 Fresh paired Batch A는 단일 corpus에서 `PROVISIONALLY_REPLACEABLE`을 기록했지만 runner 교정 뒤 다중 저장소 일반화 claim으로 승격하지 않는다. Batch B의 signed inventory·label receipt·prior snapshot·append-only anchor·post-freeze seed receipt·deterministic quota shortlist, prospective work-class lock, completion schema v2, source snapshot schema v3와 actor key independence는 구현·검증했다. 다중 저장소 schema v3 preregistration `omc-plan-batch-b-multirepo-20260820-v1`은 registry와 Sigstore TSA에 등록됐고 `2026-08-20`부터 `2026-09-18`까지의 관측 창이 활성 상태다. 8개 사용처도 OMC `0.1.0` receipt v2로 최신화했다. complete ledger pre-snapshot 감사 기준 raw provisional lock-backed schema v2 implementation receipt는 `7건/3개 저장소`이며 저장소별 5건 cap 초과 `3건`은 제외됐다. source snapshot·coverage 검증 전 공식 `validated_eligible=0`이다. 기존 단일 저장소 후보는 repository-scoped pilot으로만 보존한다. | 고정 관측 창에서 lock-backed implementation receipt를 사전 quota에 맞춰 저장소당 최대 5건·전체 최대 15건까지 계속 수집한다. 관측 종료 후 complete source snapshot을 동결하고 coverage 계약을 통과한 새 universe·shortlist 10건, 독립 gold sign-off·paired 실행·blind adjudication을 완료한다. | 신규 disjoint Batch B에서 모든 replacement gate를 통과하면 `PROVISIONALLY_REPLACEABLE`; 이후 별도 독립 confirmatory batch에서 재현해야 `REPLACEABLE`; 두 독립 배치가 primary gain·confidence gate까지 통과하면 `BENCHMARK_SUPERIOR` |

- 현재 다중 저장소 대체 판정은 `NOT_PROVEN`이다. 단일 Fresh Batch A의 `PROVISIONALLY_REPLACEABLE`은 진단·조건부 증거로만 유지하고, 기존 `omc-plan-batch-b-sigstore-20260812-v1`과 단일 저장소 후보 3건도 repository-scoped pilot으로만 보존한다. 신규 다중 저장소 claim은 complete ledger pre-snapshot 감사에서 raw provisional lock-backed schema v2 implementation receipt `7건/3개 저장소`와 저장소 상한 제외 `3건`을 확인했지만 source snapshot 검증 전 공식 `validated_eligible=0`이며, 어떤 후보도 적격으로 부르지 않는다. 로컬-authority 배치 `omc-plan-batch-b-20260812`와 그 receipt는 계속 무효다.
- runtime runner는 claim에 영향을 주는 입력과 결과를 서명·hash로 묶고 provider→blind adjudication→finalize를 실행할 수 있지만, 실제 Fresh paired provider 실행 결과를 대신하지 않는다.
- 다음 병목은 등록이나 코드 구현이 아니라 lock-backed 운영 표본 수집이다. 활성 관측 창 안에서 사전에 `implementation`으로 봉인한 completion receipt를 시간순으로 최대 15건 수집하되, 최소 3개 저장소와 저장소별 최대 5건, 고정 surface·ambiguity quota를 사후 변경하지 않는다. 합성·문서·benchmark maintenance, lock 없는 historical receipt, 기존 단일 저장소 pilot 후보는 계속 다중 저장소 claim에서 제외한다. 고정 window 종료 후 quota가 부족해도 사후 연장하지 않고 해당 batch를 무효화한다. Batch B가 모든 acceptance gate를 통과해도 판정은 `PROVISIONALLY_REPLACEABLE`까지만 허용한다.

### Review Quality Validation

`omc-review`가 Codex native review-agent와 비교해 실제 변경의 결함을 더 안정적으로 찾는지 검증하는 별도 품질 트랙이다. 이 트랙은 모델 우열을 문서로 선언하는 작업이 아니라, 같은 diff와 독립 실행 결과를 영구 보존해 교체 가능 여부를 판정하는 작업이다.

| 상태 | 현재 근거 | 다음 마일스톤 | 종료 기준 |
|---|---|---|---|
| 진행중 | 실사용 anonymized diff 10건의 V5 historical same-diff batch와 gold-label sign-off를 기록했지만, Codex exec structured review 원문이 외부 임시 경로에만 있었고 현재 보존돼 있지 않다. 따라서 Codex `3/8 hit, 3 FP`, OMC `6/8 hit, 6 FP`는 참고 수치일 뿐 대체 판정 근거가 아니다. | durable raw output을 남긴 native review-agent 동일 10건 재실행 후 blind gold-label·false-positive 재측정 | OMC가 Codex보다 핵심 탐지율·evidence 정확도가 높고 false-positive가 같거나 낮아야 `Codex 대체 가능` |

- synthetic 또는 historical pilot 결과만으로 OMC review의 대체 가능성을 주장하지 않는다.
- provenance 오염, 경로 불일치, 수동 기록 결과는 최종 비교 모수에서 제외한다.
- 전역 로드맵은 `수집 게이트 준비`, `10건 실행 완료`, `blind gold-label 완료`, `실사용 비교 결론` 마일스톤 완료 때만 갱신한다. 개별 fixture 수정과 재실행 기록은 상세 실험 문서에서 관리한다.
- 상세 계약과 현재 제한은 [OMC Review Synthetic Comparison](omc_review_synthetic_comparison.md)을 따른다.

### 상태판 압축 뷰

- 완료: V1 Skill-based Routing, V2 Step-level Routing, V3 Failure-driven Escalation, V4 Telemetry-driven Tuning
- 진행중: Operator Experience, Plan Quality Validation, Review Quality Validation
- 부분 반영: V5 Learned Orchestrator의 명시적 single-child·exact 2-child sequential 실제 실행과 bounded `3–5` child DAG grant 계약
- 미착수: N-child dependency scheduler·provider 실행·자동 재분배·자동 모델 전환·자동 ship

최근 구현 반영:

- 제한 자동 실행 pilot 1차를 구현했다. `simple_task`이면서 low-risk·high-confidence·scope-fixed 조건을 모두 만족하고 사용자가 `--execute-simple`을 명시한 경우에만 기존 autopilot의 `task -> review`를 실행한다.
- 기존 `auto_execution_allowed` 정책은 유지하고, `simple_auto_execute_allowed`를 별도 gate로 분리했다.
- verdict 미감지 또는 `REVISE/BLOCK`이면 자동 실행을 실패 처리하며, Git 상태 조회 실패·dirty scope·신규 파일·API 변경·삭제·민감 경로도 실행을 차단한다.
- 현재 상태는 `완전 자동 오케스트레이션`이 아니라 `안전한 opt-in pilot`이며, 운영 비용·성공률 검증 전까지 복잡 작업 자동 실행과 자동 ship은 보류한다.

이 압축 뷰의 목적은 상태판을 매번 긴 문장으로 해석하지 않도록 하고,
지금 손대야 할 축만 바로 읽게 만드는 것이다.

## V1 ~ V5 로드맵

### V1. Skill-based Routing

현재 단계다.

- 입력: 스킬명, 자연어 키워드, `task_kind`
- 출력: `mini_default / mini_high / full_default`
- 장점: 단순하고 예측 가능함
- 한계: 요청 전체를 하나의 강도로 보는 경향이 강함

현재 포함 항목:

- `role_suggest`의 `task_kind_hint`
- `autopilot`의 `task_kind` 기반 실행
- `resolve_task_routing()` 공통 helper
- `OMC_ROUTING_POLICY` 프리셋

### V2. Step-level Routing

한 요청을 step으로 나누고, step마다 별도 프로필을 고른다.

예시:

- `plan`: `mini_high`
- `implementation draft`: `mini_default`
- `retry`: `mini_high`
- `final review`: `full_default`

현재 반영된 변화:

- autopilot step schema에 라우팅 메타데이터 추가
- `step.task_kind` 외에 `complexity`, `risk`, `sensitive_paths`, `escalation_policy` 지원
- `preferred_profile`, `complexity`, `risk`, `sensitive_paths`가 실제 profile 선택에 반영된다
- `preferred_profile`도 `ship`/high-risk safety guard는 우회하지 못한다

남은 변화:

- V4 telemetry와 연결된 profile 선택 근거 고도화

메타데이터 책임은 아래처럼 나눈다.

1. `role_suggest`
   요청 단위 초안 메타데이터를 만든다.
   예: `task_kind_hint`, 초기 `risk`, 초기 `complexity`

2. `plan` 또는 task file 생성 단계
   사람 또는 스킬이 step별 메타데이터를 보정한다.
   예: `sensitive_paths`, `preferred_profile`, `escalation_policy`

3. `autopilot` runtime
   최종 normalization과 fallback 책임을 진다.
   누락값 보정, 허용 task kind 정규화, 안전 기본값 강제를 맡는다.

즉, metadata producer는 단일 주체가 아니라
`초안 생성 -> 설계 보정 -> 런타임 정규화` 3단계 파이프라인으로 본다.

### V3. Failure-driven Escalation

자동 전환의 핵심 단계다.

처음에는 싸게 시작하고, 실패 신호가 누적되면 더 강한 모델로 올린다.

현재 상태는 `V3 완료 + V4 multi-run KPI summary 1차 완료`에 가깝다.

- V3-1에서 들어간 것:
  - `failure_class` / `reason_codes` persistence
  - `quality_success`, `failure_class_breakdown` report 반영
  - `completed + BLOCK/HOLD/REVISE`까지 포함한 failure 집계
- V3-2에서 최근 반영된 것:
  - `retry_exhausted`, `failed_critique_loop` 경로의 `escalation_policy` decision persistence 연결 완료
  - critique 경로에서 `decision / reroute_target` 실제 소비까지 연결 완료
  - `task_retry`, `plan_retry` 성공 경로의 `decision / decision_reason / reroute_target` shape 일반화 1차 완료
  - `timeout` 경로의 `decision / decision_reason / reroute_target` shape 일반화 완료
  - `failed`, `failed_branch`, `failed_ambiguous_response` 경로의 decision contract 정규화 완료
  - `bad_entry_skill`, `metadata_missing`, `reroute_loop`에 대한 `orchestration_failure` 1차 decision policy 연결 완료
  - `VERDICT: BLOCK`만 있고 reason code가 없는 task 경로를 `block_without_reason_code`로 승격해 silent completion을 차단했다.
  - `decision_policy_entry` helper 추출로 failure-class별 decision 규칙을 공통 엔진으로 옮길 준비를 마쳤다.
  - critique/review failure step runtime 소비가 `_failure_step_decision` helper를 통해 공통 decision 엔진을 직접 사용하도록 정리됐다.
  - `task_retry`, `plan_retry` 실패 payload도 `_retry_step_payload` helper로 정리돼 retry runtime decision 하드코딩이 제거됐다.
  - `ambiguous_response`, `branch_setup_failed`도 `orchestration_failure`로 승격돼 persisted decision이 explicit hold로 수렴하도록 정리됐다.
- V3-2에서 남은 것:
  - 없음 — `orchestration_failure` runtime 소비 경로 전반이 `decision_policy_entry`를 직접 사용하도록 정리됐다.

실패 신호 예:

- 같은 step 재시도 2회 이상
- 테스트 실패 반복
- review에서 `major` 또는 `critical`
- output format mismatch
- 같은 파일 반복 수정

실패는 최소 3개 클래스로 나눈다.

1. execution failure
   테스트 실패, 명령 실패, timeout, 파일 미생성

2. quality failure
   review `major/critical`, 회귀 발견, DoD 미달

3. orchestration failure
   잘못된 시작 스킬, reroute 반복, output format mismatch,
   step metadata 부족으로 인한 잘못된 선택

같은 retry라도 failure class가 다르면 승격 규칙도 다르게 가져간다.

필수 변화:

- 승격 규칙 엔진 도입
- retry 사유 분류
- `same / escalate / reroute` 선택 로직 추가

### V4. Telemetry-driven Tuning

정책을 감이 아니라 로그로 튜닝한다.

수집 대상:

- 요청 유형
- 선택 모델
- 토큰/비용
- retry 횟수
- 최종 결과
- review severity
- reroute 횟수
- start delay

필수 변화:

- `.omc/` 내 실행 메타데이터 구조화 저장
- benchmark 리포트에 single-run telemetry 추가
- `.omc/runs/` 기준 aggregate KPI summary 추가
- 정책별 비교 리포트 자동화

최소 KPI는 세 가지로 고정한다.

1. reroute rate
   처음 선택한 시작 스킬 또는 경로가 중간에 얼마나 자주 바뀌는지

2. retry-to-success rate
   retry 이후 실제 성공으로 회복되는 비율이 얼마나 되는지

3. cost per successful task
   성공한 작업 1건당 평균 비용이 얼마인지

이 세 가지가 있어야 `품질 개선`과 `비용 증가`를 같은 화면에서 같이 본다.

### V5. Learned Orchestrator

복잡한 작업 자동 분해·위임의 첫 구현 단위로 `decompose-only`를 추가했다.

- `needs_delegation` 요청을 backend/frontend/verification 도메인 child로 분리한다
- 각 child에 scope, dependency, expected output, handoff contract를 부여한다
- 모든 child가 완료된 뒤 cross-scope 영향 검토를 수행하는 integration-review child를 둔다
- 현재는 `execution_allowed=false`로 고정해 분해 결과와 handoff contract만 검증한다
- 10개 복합 요청 fixture와 dependency-cycle 회귀 테스트로 기본 경계를 잠근다
- child contract의 타입·enum·빈 값 검증을 `expected_output`, handoff 필드, confidence 일관성까지 1차 강화했고 malformed fixture와 `144 passed, 1 skipped` 회귀 증거를 확보했다
- domain boundary fixture 7개로 다중 도메인, 단일 도메인 불명확, contextual marker 경계를 고정했고 `145 passed, 1 skipped`까지 회귀 검증했다
- `build_delegation_observed_record()`가 복합 요청 분해와 blocked/scope-mismatch handoff를 `delegation_observed`로 정규화한다
- `build_delegation_shadow_record()`가 승인·scope·expiry·guard metadata를 검증하지만 실제 executor를 호출하지 않는 `noop_shadow` 기록을 만든다
- 단일 child pilot은 operator approval, plan/scope fingerprint, child/dependency readiness, sensitive scope, 단일 시도·시간·출력 예산, idempotency를 모두 검증하며, 누락된 scope/dependency metadata도 차단한다
- 단일 child pilot의 정상·차단·orchestrator 경유 surface를 포함한 회귀 테스트와 TDD gate를 통과했다(`286 passed, 1 skipped`)
- malformed identifier, timezone-less expiry, 비유한/초대형 숫자, 비boolean 실행 플래그는 명시적 rejection record로 남긴다
- `build_child_decision()`이 기존 handoff를 `ready / blocked / hold / rejected`로 정규화하고 canonical `next_action`, `decision_reason`, retry budget hold를 기록한다
- `execution_order`와 `recovery_action`의 내부 계약을 검증해 malformed surface, ready 상태의 blocked dependency, 안전 플래그 누락을 명시적으로 rejected 처리한다
- child decision은 recommendation-only이며 실제 executor/autopilot retry 연결 없이 `execution_allowed=false`를 유지한다
- `delegation_observed`에 `child_decisions`를 연결해 handoff와 decision을 같은 observed record에서 비교할 수 있게 했다. malformed top-level handoff는 기존 rejection과 빈 decision 목록을 유지한다
- autopilot task의 `delegation_case(s)`를 실행 결과에 별도 저장하며, 모든 기록은 `recommendation_only=true`, `execution_allowed=false`로 고정한다
- malformed case, invalid evidence status, execution permission 요청을 rejected record로 남겨 silent acceptance를 차단한다
- `templates/shared_tasks/complex-delegation-evidence-pilot.json`을 read-only fixture pilot로 제공해 설치 대상 저장소에도 같은 관측 케이스를 전파한다
- shared task 설치는 기본 `setup --force`에서 marker가 있는 OMC task만 갱신하며, legacy markerless task는 `--migrate-shared-tasks`를 명시한 경우에만 동일 ID를 확인해 승격한다

현재 판정은 `부분 반영`이다. 단일 child와 exact 2-child sequential opt-in의 bounded 실제 실행, `3–5` child DAG의 실행 전 grant 계약까지는 안전하게 열렸지만 일반 자동 위임 전까지 남은 구현 갭은 다음과 같다.

- 실제 observed 오탐/누락이 다시 발생할 때 classifier 규칙과 fixture를 함께 확장
- symlink·case·glob scope 정규화와 child approval ID 고유성 정책 확정
- bounded N-child dependency scheduling·provider 실행과 실패 시 자동 재분배 정책의 설계 및 검증
- 운영 데이터 축적 후에만 execution gate를 단계적으로 해제

가장 마지막 단계다.

규칙은 여전히 안전망으로 남기되, 실제 선택은 축적된 데이터 기반 점수화 또는 학습형 분류기로 보조한다.

가능한 형태:

- 정책 추천기
- step 난이도 추정기
- “처음부터 Full로 갈 요청” 예측기

단, 이 단계는 V2~V4 로그 품질이 확보된 뒤에만 의미가 있다.

초기 진입 게이트는 아래처럼 둔다.

- step telemetry 300건 이상
- 정책별 비교 가능 케이스 100건 이상
- retry reason 분류 정확도 85% 이상

이 수치를 만족하기 전에는 V5를 연구 단계로만 두고,
운영 기본값은 계속 rule-based 계층을 사용한다.

## Operator Experience

자동 모델 전환과 별개로, 지금 사용 중인 `plan / task / review` 경험 자체를 더 똑똑하게 만드는 축이 필요하다.

이 트랙은 단순한 UX 문구 개선이 아니라,
사용자 요청을 더 정확히 해석하고 다음 액션을 더 잘 추천하는
`사람이 체감하는 오케스트레이션 품질`을 다룬다.

이 구간의 속도 기준은 `plan / task` fast, `critique / review` normal을 기본으로 두고 읽는다.
즉, 빠르게 끝낼 수 있는 건 빨리 끝내되, 실패 탐지가 중요한 구간은 normal 안전장치를 유지한다.

핵심 목표는 다섯 가지다.

1. 시작 스킬 정확도
   `plan`으로 가야 할 요청과 `critique`나 `review`가 먼저 필요한 요청을 더 잘 구분한다.

2. next-action 추천 품질
   파이프라인 기본 순서가 아니라
   현재 병목과 사용자 의도를 우선해 다음 스킬 1개를 추천한다.

3. stop / proceed 경계 명확화
   스킬 완료 후 자동 진입 없이,
   왜 여기서 멈췄는지와 다음 선택지를 짧게 설명한다.

4. reroute / delay UX
   잘못 시작한 경우 “왜 경로를 바꿔야 하는지”를 설명하고
   바로 `critique`, `investigate`, `review`로 reroute할 수 있어야 한다.

5. plan/task/review 결과 구조화
   각 스킬의 출력이 길어져도
   결정, 리스크, 다음 액션, 게이트 상태가 끝까지 보존되어야 한다.

### Operator Experience 1차 통합안

방금 만든 플랜은 아래 4개 축으로 로드맵에 합쳐진다.

1. `next-action` 추천 품질
   현재 병목과 사용자 의도를 우선해 다음 스킬 1개만 추천한다.

2. `reroute / delay` 표준화
   confidence가 낮거나 범위가 흔들리면 reroute/delay로 분기하고, 그 이유를 짧게 설명한다.

3. `output bloat` 억제
   plan/task/review/critique 모두 한 화면에서 다음 액션을 판단할 수 있을 만큼만 출력한다.

4. 스킬별 `fast / normal` 기준 정렬
   `plan / task`는 범위가 고정되면 fast, `critique / review`는 기본 normal을 유지한다.

이 통합안의 목적은 “더 많은 단계”가 아니라,
**사용자가 입력하거나 스킬을 실행했을 때 원하는 바를 더 정확히 취하고, 다음 행동이 즉시 보이게 만드는 것**이다.

### Operator Experience 마감 기준

아래 조건을 만족하면 Operator Experience의 현재 구현 라운드는 일단 마감 가능으로 본다.

- expensive-flow summary에서 `dominant_flow_kind=wrong_next_step`가 유지되더라도, 최근 observed 질문 보강이 실제 explanation-first 경계를 대표적으로 덮고 있다
- `operator_next_priority=tighten_next_action_routing`와 `operator_validation_status=ready_to_close`가 함께 유지된다
- explanation/permission 질문 축에서 최소한 아래 observed_request 케이스가 fixture로 고정돼 있다
  - `추천이 task야 critique야`
  - `plan full 이면 critique 해야하나`
  - `방금 거는 task 해도 되나`
  - `이거 review 해야해?`
- 새 observed case를 1건 더 넣더라도 현재 우선순위 판단이 바뀌지 않는다

즉, 지금 단계에서는 무한히 케이스를 더 넣는 것보다 현재 마감선을 명시하고, 다음 라운드에서 다른 병목이 실제로 떠오르는지 보는 편이 토큰 대비 효율이 높다.

현재 기준 즉시 넣을 만한 작업은 아래다.

- `role_suggest`에 시작 스킬 오판 패턴 보강
- next-skill 추천 규칙에 “현재 병목 > 기본 파이프라인” 원칙 고정
- `plan/task/review` 출력 contract에 `decision`, `risk`, `next_action` 필수화
- `reroute`와 `delay`를 오케스트레이션 이벤트로 기록

2026-07-16 기준 다음 작업은 위 구현을 다시 확장하는 것이 아니라, ship 후 실제 네트워크 환경에서 DNS/timeout 분류가 의도대로 관측되는지 확인하고 health 느린 테스트군을 별도 실행하는 운영 검증이다.

최근 반영된 1차 변화:

- `omc-plan` 출력 contract에 `decision / risk / next_action` 의미를 plan 문맥으로 고정
- `omc-review` 출력 contract에 `decision / risk / next_action` 의미를 review 문맥으로 고정
- `omc-task` 출력 contract에 `decision / risk / next_action` 의미를 task 문맥으로 고정
- 관련 contract regression test 추가로 누락 시 즉시 실패하도록 보강

최근 반영된 2차 변화:

- `2-1 next_action 공통화`: `plan / task / review`가 공통 결정표(`stage / outcome / user_selection_needed`, review는 `ship_intent_explicit` 추가) 기준으로 주추천 1개만 남기도록 정리
- `plan`의 unresolved 경로를 `critique`와 `office-hours`로 의미 분리
- `review`의 approve 경로를 `ship_intent_explicit=yes`일 때만 `$omc-ship`, 그 외에는 사용자 선택 대기로 단일화
- 관련 contract test와 benchmark check로 “한 상태당 next_action 1개” 회귀를 고정
- `2-2 reroute / delay UX`: `omc-critique`, `omc-status` 출력 contract에 조건부 `reroute 이유 / delay 이유 / 재개 조건` 설명 규칙 반영
- `role_suggest`가 `변경 상태 검토`와 `plan 검증` 요청을 각각 `review`, `critique`로 더 정확히 라우팅
- `omc-status`가 `현재 병목 > 기본 파이프라인` 우선순위를 직접 따르도록 추천 규칙을 보강
- `omc-plan`, `omc-review`도 같은 우선순위를 다음 추천 규칙에 직접 반영해 plan/review/status 3축을 맞췄다.
- `top-expensive-flows` benchmark CLI와 `verification_checklist` 커버 맵으로 고비용 흐름 상위 5개를 바로 읽을 수 있게 정리했다.

남은 2차 변화:

- `omc-plan`, `omc-review`, `omc-task` 1차 계약 보강은 끝났다.
- `2-1 next_action 공통화`도 끝났다.
- `2-2 reroute / delay UX`도 1차 완료됐다.
- next-action 품질 보강 3차의 plan/review/status 병목 우선 추천은 반영 완료다.
- response-mode 벤치마크에 실제 review/plan/status/reentry 요청 케이스를 넣어 next-action 정밀도를 더 직접 검증한다.
- next-action 파서는 한글/영문 라벨과 여러 구분자 변형을 흡수하도록 정리됐다.
- expensive flow report도 이제 top 5를 단일 `wrong_next_step`만으로 채우지 않고 `wrong_next_step / reroute_loop / output_bloat / over_stage_entry`를 함께 surface하도록 보강됐다.
- summary에도 `dominant_flow_kind`, `operator_next_priority`, `operator_next_priority_reason`를 추가해 어떤 병목을 먼저 줄여야 하는지 한 화면에서 바로 읽게 맞췄다.
- `role_suggest`도 Operator Experience 정리 요청을 일반 review/task가 아니라 `$omc-plan`으로 먼저 정렬하도록 보강됐다.
- 다음 남은 조각은 fixture/benchmark에도 이 우선순위를 더 직접 반영해 실제 기대 추천과의 오차를 줄이는 것이 아니라, 운영 observed 데이터에서 이 summary 신호의 설명력이 충분한지 검증하는 일이다.

이 트랙은 V2~V4와 연결된다.

- V2가 step metadata를 더 잘 가지면 시작 스킬 판단이 좋아진다.
- V3가 failure class를 가지면 reroute 품질이 좋아진다.
- V4가 telemetry를 쌓으면 어떤 추천이 실제로 유효했는지 측정할 수 있다.

## 제품 아키텍처

완전 자동 모델 전환 제품은 아래 계층으로 본다.

1. Classifier
   요청과 컨텍스트를 읽고 복잡도/리스크/범위를 추정한다.

2. Planner
   요청을 step으로 나누고 각 step의 목적을 정한다.

3. Router
   각 step에 맞는 모델 강도를 고른다.

4. Evaluator
   테스트, review, 실행 결과를 읽고 실패 신호를 수집한다.

5. Escalator
   실패 시 같은 모델 유지 / 상향 / reroute를 결정한다.

6. Telemetry
   비용, 성공률, retry율, 품질 지표를 남긴다.

## 운영 원칙

- 기본은 자동 선택
- 필요하면 사용자 override 가능
- 선택 이유는 설명 가능해야 함
- 고가 모델은 처음부터 남발하지 않고 실패 신호 기반으로 승격
- `ship` 같은 고위험 단계는 보수적 유지

## 현재 구현 연결점

현재 V1의 실제 SSOT는 아래 파일들이다.

- [scripts/omc_role_suggest.py](/Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_role_suggest.py)
- [scripts/omc_autopilot.py](/Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_autopilot.py)
- [scripts/omc_exec.py](/Users/noseunglae/Downloads/dev/omc_kit/scripts/omc_exec.py)

관련 전략/사용 문서는 아래를 함께 본다.

- [docs/fugu_benchmark.md](/Users/noseunglae/Downloads/dev/omc_kit/docs/fugu_benchmark.md)
- [docs/orchestration_usage.md](/Users/noseunglae/Downloads/dev/omc_kit/docs/orchestration_usage.md)

## 초기 구현 우선순위 4개 (완료된 역사 기록)

이 절은 V1 초기 구현 순서를 보존하는 역사 기록이며 현재 착수 목록이 아니다. 현재 상태와 다음 작업은 상단의 품질 검증 상태표와 `바로 다음 작업 계획`을 기준으로 한다.

### 1. Step metadata 확장

가장 먼저 해야 한다.

추가 후보:

- `complexity`
- `risk`
- `sensitive_paths`
- `preferred_profile`
- `escalation_policy`

이유:

- 지금은 `task_kind` 하나로 너무 많은 판단을 대신한다.
- V2, V3, V4 전부의 기반이 된다.

### 2. Retry escalation engine

두 번째 우선순위다.

최소 규칙 예:

- 첫 실패: same profile
- 두 번째 실패: `mini_high`
- 세 번째 실패 또는 review major: `full_default`

이유:

- 자동 전환 제품의 체감 가치는 여기서 가장 크게 생긴다.
- 사용자는 “처음엔 싸게, 막히면 알아서 세게”를 원한다.

### 3. Execution telemetry 저장

세 번째 우선순위다.

최소 저장 항목:

- chosen profile
- task kind
- retry count
- final result
- token usage
- cost estimate

이유:

- 이후 benchmark와 learned orchestrator의 재료가 된다.
- 지금 단계부터 로그가 없으면 다음 단계는 계속 감으로 가게 된다.

### 4. Plan / Task / Review experience 고도화

네 번째 우선순위다.

최소 작업:

- 시작 스킬 판정 정확도 개선
- next-action 추천 단일화
- `decision / risk / next_action` 구조 강제
- reroute / delay 기록 추가

이유:

- 사용자는 모델 라우팅보다 먼저 “지금 이 흐름이 똑똑한가”를 체감한다.
- plan, task, review 경험이 좋아져야 자동 모델 전환도 신뢰받는다.

## 이번 분기 현실적 목표

이번 분기 목표는 V5가 아니라 V3 완료 + V4 multi-run KPI summary 1차 완료까지였다.
현재 구현은 이미 그 선을 넘었고, 이제는 V4 데이터 축적과 Operator Experience 정교화가 다음 우선순위다.

이번 분기 핵심 목표는 아래 3개다.

- multi-run telemetry 축적
- 정책 비교 리포트 정교화
- Plan / Task / Review experience 고도화

`Plan / Task / Review experience 고도화`는 이번 분기에도 병행할 수 있지만,
핵심 엔진 로드맵을 흐리지 않도록 `병행 UX 트랙`으로 취급한다.

즉, 분기 운영 기준은 아래처럼 본다.

- 핵심 엔진 트랙: telemetry / policy comparison / data accumulation
- 병행 UX 트랙: 시작 스킬 정확도 / next-action 품질 / reroute UX

## 작업 운영 원칙

- 코드 작업과 설치/정리를 분리한다.
- plan -> task -> review 왕복은 위험할 때만 반복한다.
- 같은 저장소 안에서 한 번에 끝내는 것을 우선한다.
- 설치/동기화 작업은 setup / gitignore / hook 같은 운영성 변경에 한해 예외적으로 묶는다.
- 저장소를 넘는 작업은 코드와 설치를 분리하고, setup은 대상별로 끝낸다.

### OMC latency baseline 및 reroute 억제 (2026-08-13)

- 실행 이력 409건을 점검했지만 기존 기록에는 단계별 `execution_metrics`가 없어 과거 p50/p95를 소급 산출하지 않는다.
- 신규 관측에서는 Lite 경로(`omc-task -> omc-review`)와 Full 경로(`omc-plan -> omc-critique -> omc-task -> omc-review`)를 분리해 실행 시간과 실제 skill path를 기록한다.
- 실제 baseline 1건 기준 Lite는 약 106초, Full은 약 552초로 관측됐으며 Full은 반복 critique와 plan 재시도에서 HOLD로 종료됐다. 이 값은 표본 1건씩이므로 일반 성능 결론이 아니라 병목 확인용 baseline이다.
- critique 반복이 해결되지 않은 상태에서 plan을 자동 재진입하면 같은 준비 왕복이 반복되므로, task retry 소진 후 자동 plan reroute를 중단하고 사용자 HOLD로 승격하도록 `_CRITIQUE_AUTO_RETRY_MAX=0`을 고정했다.
- 단계별 `duration_ms`, `mode`, `skill_path`는 실행 결과의 `execution_metrics`로 보존한다. provider token metadata가 없는 실행은 token p50/p95로 해석하지 않으며, 충분한 신규 표본이 쌓인 뒤 별도로 산출한다.
- `latency-summary --runs-dir` CLI가 완료된 실행만 `mode`별 nearest-rank p50/p95로 집계하고, legacy·실패·미완료·telemetry 누락 실행의 제외 건수를 함께 출력한다.
- 현재 `.omc/runs` 413건 중 유효 telemetry 표본은 Lite 1건뿐이다. Lite p50/p95는 `106,462ms`이며 Full은 표본 부족으로 산출하지 않는다. 최신 커밋 `48a9782` 기준으로도 작업 디렉터리 diff는 없고, 이 결과는 구현 회귀가 아닌 표본 부족 상태다.
- 따라서 latency baseline 측정 경로는 구현됐지만, 운영 결론과 전후 비교를 위해서는 Lite/Full 각각의 신규 표본 축적이 남아 있다.
- Full critique/review의 첫 `REVISE`는 동일 diff 재검토나 plan 왕복 없이 지적 내용을 포함한 `task_retry`로 즉시 전환한다. `task_retry` 예산이 소진되면 자동 plan fallback 없이 `HOLD`하며, `BLOCK`은 처음부터 `HOLD`한다.
- 최신 격리 fixture v2 실행 4건(Lite 1, Full 3)은 Lite 실패 1건과 Full HOLD 3건으로 끝나 성공 표본에 포함하지 않는다. 이 진단에서 Full critique의 누적 `task_retry`가 review 교정 예산까지 소진하는 공유 예산 결함을 확인했다.
- critique와 review의 교정 예산을 단계별 `task_retry_counts`로 분리하고 실행 결과와 resume에 보존했다. critique 2회와 review 1회가 독립적으로 소비되는 실제 resume 회귀를 포함해 직접 테스트 `100 passed`, py_compile, staged TDD gate, OMC review `APPROVE`를 확인했다.
- `task_retry`의 구조화된 `BLOCK`은 실패 분류·bounded evidence·`decision=hold`를 함께 보존하고, 일반 critique 복구와 retry 소진 복구 모두 실제 top-level `hold`로 종료하도록 정렬했다. 무사유 legacy BLOCK fallback은 유지했으며 전체 autopilot 회귀 `255 passed, 1 skipped`와 OMC review `APPROVE`를 확인했다.
- 구현 결함은 닫혔지만 수정 커밋 기준의 격리 재실행 전이므로 병목 해결 효과와 Full latency 개선은 `NOT_PROVEN`으로 유지한다. 재실행에서도 실패/HOLD한 결과는 p50/p95 성공 표본으로 승격하지 않는다.
- 고정 benchmark 입력 v2를 `scripts/fixtures/omc_latency_benchmark_v2.json`에 보존했다. 기준 커밋 `48a9782`, resolver 수정 범위, 신규 resolution test, 전체 관련 테스트 명령을 저장소 안에서 검증하며 임시 디렉터리 입력은 기준 계약으로 사용하지 않는다.
- 고정 입력 기준 1차 재측정은 Lite 성공 4건·실패 1건, Full 성공 2건·quality HOLD 2건·수동 gate 중단 1건으로 끝났다. Full 5건 성공 표본과 token telemetry가 아직 부족하므로 p50/p95 라우팅 기준은 계속 `NOT_PROVEN`이다.
- Lite benchmark의 장기 정체를 제한하도록 task `300초`, review `180초` 상한을 추가하고, 각 단계 timeout을 pipeline 전체 `max_time` 잔여 예산으로 다시 제한했다. 잔여 시간이 1초 미만이면 provider를 호출하지 않고 `pipeline_deadline_exhausted`로 종료하며, 실제 provider timeout은 `status=timeout`, `rc=124`, `timeout_sec`, monotonic `duration_ms`로 보존한다. 완료된 task 뒤 review timeout이 난 실행은 resume에서 task를 건너뛰고 review만 재시도한다. 일반 Lite의 기존 task `1200초`, review `600초` 계약은 유지한다. sub-second 경계 회귀를 포함한 autopilot 테스트 `128 passed, 1 skipped`, TDD gate, OMC review `APPROVE`를 확인했다.
- 스킬 진입 훅 오버헤드와 중복 컨텍스트를 줄이기 위해 순수 Codex skill 링크 호출은 UserPromptSubmit에서 `0 byte`로 종료하고, 설명이 붙은 호출은 링크를 제거한 실제 설명만 BM25 검색에 사용하도록 정리했다. SessionStart는 executor·stable `event_id`·저장소 root에 결속된 receipt로 중복 lifecycle과 context 출력을 억제하며, 매 실행에서 공통 summary를 강제 재생성해 executor overlay와 stale summary가 섞이지 않게 했다. 동일 이벤트의 순차·동시 중복, PID 기록 전 lock 경쟁, state/session/output 실패 재시도, 동시 선행 실패 후 후행 인계, 비식별 diagnostics를 포함한 hook 회귀 `30 passed`, 셸 문법·template/install 정합성·TDD gate, OMC review `APPROVE WITH NOTES`를 확인했다. 이 변경은 진입 중복과 실패 복구 경로의 구현 완료를 뜻하지만 Lite/Full p50/p95 개선 증거는 아니므로 운영 latency 판정은 계속 `NOT_PROVEN`으로 유지한다.

## 바로 다음 작업 계획

현재 기준 상태는 아래와 같다.

decision engine 잔여 예외 감사는 완료됐고, 추가 코드 gap은 발견되지 않았다.
`next-action 품질 보강 3차`는 완료됐고, 이제 다음 우선순위는 아래와 같다.

1. next-action 품질 보강 3차 - 완료
   `2-2 reroute / delay UX`, 시작 스킬 정확도 보강, `omc-status`, `omc-plan`, `omc-review`의 병목 우선 추천은 반영됐다.
   다음은 fixture/benchmark에도 같은 우선순위를 더 직접 반영해, 같은 상태에서도 사용자 의도 차이를 더 잘 구분하는 것이다. 현재는 top-expensive-flows CLI가 생겨서, 이 작업의 병목 후보를 훨씬 빨리 찾을 수 있고, review intent 실제 요청 케이스와 next-action gap/next_action_incomplete도 함께 드러난다. response-mode fixture는 29 cases까지 늘었고 observed_request / expected_next_action 케이스도 더 촘촘히 고정됐다.

2. telemetry report 정리 2차 - 완료
   정책 비교 리포트는 1차 자동화가 들어갔고, benchmark/report 출력도 비교 가능한 형태로 정리됐다.
   readiness와 baseline comparison 상태는 `policy_comparison_summary`까지 포함해 deferred/ready 판정을 한 줄로 바로 읽을 수 있게 됐다.
   collected observed summary에도 `observed_data_bottleneck_summary`를 넣어 샘플 부족과 rejected observed_output reason을 함께 읽게 만들었다.
   이제 V4의 다음 목표는 더 많은 실제 실행 데이터를 쌓고 정책 기준을 더 정교하게 만드는 것이다.

3. slow health 운영 검증 - 준비 완료
   빠른 회귀 경로와 느린 health 경로를 분리했다. 다음 운영 검증 시 `python3 -m pytest scripts/test_omc_health.py -q -m slow`를 실행해 실제 OMC scripts 문법·테스트 수집 결과를 확인한다.

4. Lite/Full latency 표본 축적 - 다음 작업
   단계별 retry 예산 분리와 구조화 BLOCK/HOLD 정합성 커밋을 기준으로 격리 fixture v2를 먼저 재실행한다. 이후 Lite 성공 표본을 최소 `5건`까지 채우고 Full 성공 표본도 최소 `5건`을 확보한 뒤, 각 결과의 단계별 시간·input/output/total token·retry를 수집한다. 현재 유효 표본은 Lite 성공 `4건`, Full 성공 `2건`이며 최신 진단의 Lite 실패 1건과 Full HOLD 3건은 성공 표본에서 제외해 보존한다. benchmark Lite의 단계별 timeout과 전체 deadline cap은 구현·검증 완료했지만 운영 성능 개선을 증명하는 표본은 아니다. token telemetry가 없는 결과는 0으로 대체하지 않고 `미측정`으로 남긴다. 각 모드 성공 표본이 최소 5건이 되기 전에는 p50/p95 기반 라우팅 경계를 확정하지 않는다.

최근 보강:
- `Executor Recommendation Surface`의 추천-only acceptance line과 handoff acceptance binding을 문서/테스트로 고정해, executor surface가 어디까지 설명하고 어디서 reroute layer로 넘기는지 경계를 명시했다.
- 추가로 fallback은 executor 대체안 제시, reroute는 다음 경로 결정 소유라는 책임 분리를 문서/테스트로 더 직접 고정했다.
- 자동 추천·자동 라우팅 1차를 반영해, 범위가 고정된 단순 task는 `task + cost_saver`를 추천하고 복잡·고위험 task는 `plan + user_selection_needed=yes`로 멈추도록 공통 decision surface를 맞췄다. 파일 수정·커밋·배포 자동 실행은 계속 금지한다.
- benchmark pair report에 `skill_count`, 모델 profile, 사용자 확인 횟수, input/output tokens, elapsed time의 선택적 before/after delta를 추가했고, 부분 토큰 메타데이터는 `total_tokens_delta`를 산출하지 않도록 검증한다.
- latency baseline 측정을 위해 `pipeline --benchmark --skip-pr` 경로를 추가했다. PR 생성은 건너뛰되 PLAN/TASK/REVIEW telemetry는 완료로 기록하고, 결과에 `benchmark=true` provenance를 남긴다. 일반 pipeline에서 `--skip-pr` 단독 사용은 차단하며, resume 시에도 benchmark provenance를 보존한다. 이 경로는 실행 지연 p50/p95와 토큰 지표 측정 전용이며 실제 PR 완료를 의미하지 않는다.
- Lite 경로도 `--benchmark --skip-pr`에서 push/PR 생성 없이 `completed`로 종료되도록 보강했고, `cmd_pipeline()` 기반 dry-run 통합 테스트로 `benchmark` provenance와 PR skip 상태를 검증한다. 이후 benchmark 전용 task/review timeout, pipeline 잔여 deadline 상한, timeout telemetry, resume 계약까지 보강했으며 관련 autopilot 테스트는 `128 passed, 1 skipped`다. 운영 성공 표본은 Lite `4건`, Full `2건`으로 아직 부족하므로 라우팅 기준은 확정하지 않는다.

## 다음 순환 목표

현재 기준 요약은 아래와 같다.

- V1 완료
- V2 완료
- V3 완료
- V4 완료
- Operator Experience는 후속 운영 UX 정리 단계

즉, 구현 로드맵 기준으로는 `V4-A 구현 마감`뿐 아니라 `V4-B 운영 완료 판정 1차`까지 닫혔고, 다음 순환부터는 구현 잔여가 아니라 운영 유지·검증과 Operator Experience 후속 정리로 본다.

다음 순환에서 볼 후보는 아래와 같다.

1. V4 multi-run KPI summary 2차
   이 트랙은 `V4-A 구현 마감`과 `V4-B 운영 완료 판정`으로 나눠서 봤고, 현재는 1차 완료 판정까지 도달했다.
   `V4-B 운영 검증 실행 준비`를 넘어서 실제 `v4b-operational-validation` 실행이 completed로 남아 있고, overview 기준 `operational_validation_readiness=start-ready`, `observed_samples=21`, `same-surface=8`, `policy pair=2`가 함께 확인됐다.
   실제 observed run 축적 20회 이상 조건은 충족됐고, overview / collected summary / decision 정렬도 운영 검증 태스크로 다시 확인했다.
   최근에는 autopilot 운영 유지 측면에서도 stale `running` state를 재실행 전에 자동 복구하도록 보강했다. live PID가 남아 있으면 재실행을 막고, dead PID와 missing PID는 서로 다른 `stale_reason`으로 step/task state에 남겨 운영자가 재시도 전후 맥락을 같은 surface에서 읽을 수 있게 맞췄다.
   핵심 반례 보강은 사실상 완료됐고, 이제는 실제 실행 케이스를 더 쌓아 정책 비교의 신뢰도를 높이는 운영 검증 단계다.
   이번에는 observed-cost pilot fixture가 baseline/candidate별 `skill_path`를 보존하고, benchmark report가 경로 길이로 `skill_count`와 `skill_count_delta`를 계산하도록 보강해 plan/task/review 라운드 감소를 실제 비교 지표로 남긴다.
   추가로 실제 observed 실행 저장 시 `observed_metrics`에 모델 profile, skill path, elapsed time, token/retry/reroute/success 지표를 정규화해 이후 운영 비교가 원본 step 구조에 의존하지 않도록 맞췄다.
   `reroute rate`, `retry-to-success rate`, `cost per successful task` 기준 샘플을 더 모은다.
   neutral observed seed와 observed_output 실데이터를 구분한 상태에서 same-surface observed evidence를 더 누적한다.
   readiness/baseline 상태 문구는 이미 report와 overview에 실리므로, threshold taxonomy와 candidate count-aware check이 들어간 현재 기준에서는 다음 단계가 deferred/ready 판정이 실제 observed dataset 누적에서 얼마나 안정적인지 검증하는 일이다.
   최근에는 collected observed summary도 `policy pair 2/2` 부족을 별도 병목으로 표시하도록 맞췄고, same-surface observed evidence가 `0 → 1 → 2`로 변할 때 collected summary / report / taxonomy가 함께 기대값을 유지하는 회귀 테스트도 추가했다.
   추가로 multi-run KPI 3종은 이제 collected observed summary와 comparison summary 양쪽에 함께 노출되고, run fixture 기반 회귀 테스트로 고정됐다. 최근에는 neutral observed_request seed가 readiness용 policy pair를 부풀려 `가짜 ready`를 만들지 않도록, 전체 관측 분포와 readiness 입력 분포도 분리했다.
   여기서의 threshold taxonomy/candidate 비교는 구현 정합성 확인용이며, threshold 숫자 자체의 정책 타당성은 실제 observed multi-run 비교로만 판단한다.
   2026-07-14 운영 evidence 점검에서는 observed 25개, same-surface 8개, distinct policy pair 2개로 readiness는 유지됐지만, `cost_evidence=0`, `quality_evidence=0`, `paired_evidence=0`으로 cost-quality validation은 `pending / cost_evidence_missing`이다. Codex cost log에는 token usage는 남지만 현재 비용 추정 경로가 Codex 금액 산출을 지원하지 않아, 임의 환산이나 `ready` 승격은 하지 않는다. 현재 overview는 `measurement_basis=token_only`와 완전한 step별 input/output token 합계만 운영 지표로 노출하며, partial token evidence와 USD 비용 검증은 계속 pending으로 남긴다.
   invalid observed_output이 조용히 사라지지 않도록 rejection summary는 들어갔고, 1차로 collected observed summary에서도 rejection reason 병목을 함께 읽게 만들었다. 최근에는 이 rejection context가 comparison decision의 pending 경로뿐 아니라 ready 경로의 `policy_comparison_summary`에도 남도록 맞춰, readiness 달성 후에도 버려진 실데이터 수를 한 줄에서 같이 읽게 했다. 추가로 ready mixed fixture에 neutral observed_request를 함께 섞어도 readiness sample/policy-pair count가 부풀지 않고 ready 판단이 유지되는 회귀 테스트를 고정했고, 이어서 accumulated observed dataset fixture에서도 collected summary와 report summary가 같은 ready 결론으로 수렴하는지 별도 케이스로 잠갔다. 이번에는 count 기준이 모두 충족돼도 `baseline_comparison_ready=false`이면 decision이 ready처럼 보이지 않도록 drift guard를 추가해, collected/report와 decision 문구의 비정상 어긋남도 별도 회귀 테스트로 잠갔다. 이어서 rejection reason map이 비어도 `rejected observed_output` count 자체는 decision bottleneck에서 사라지지 않도록 count-only suffix 보강까지 넣었고, 마지막으로 collected observed summary도 `readiness_blocker_line`, sample/same-surface gap, `baseline_comparison_ready`를 함께 노출해 report/decision과 같은 readiness 경계 신호를 직접 읽게 맞췄다. 이어서 ready mixed / accumulated observed fixture에서도 이 gap과 `baseline_comparison_ready`가 그대로 유지되는지 직접 검증하도록 회귀 테스트를 보강했다. ready 경로의 `policy_comparison_summary`에서도 같은 count-only suffix가 유지됨을 별도 테스트로 확인했다. 최근에는 observed request에서 포착된 reroute/과출력 신호도 최종 decision surface인 `policy_comparison_summary`에 `reason signals observed`로 최소 연결해, 병목 문구와 설명 신호 존재를 한 줄에서 같이 읽게 했다. 이어서 `next_priority_recommendation`, `next_priority_reason`를 decision payload에 추가해 sample/same-surface/policy-pair/baseline drift/ready 후 operator 병목 중 무엇을 다음 우선순위로 볼지 직접 surfaced하도록 맞췄고, 해당 경계 분기 회귀 테스트도 함께 고정했다. 최근에는 invalid same-surface noise가 있어도 valid same-surface evidence가 `0 → 1`로 바뀔 때 deferred/ready 전이가 정확히 유지되는 fixture를 추가했고, 이어서 same-surface는 이미 충족된 상태에서 readiness용 policy pair가 `1 → 2`로 바뀌는 혼합 fixture도 별도 회귀 테스트로 잠가 threshold가 노이즈에 흔들리지 않게 했다. 추가로 accumulated observed dataset에서 reason signal이 summary와 final decision 양쪽에 같은 우선순위 추천으로 유지되는 정렬 회귀 테스트도 고정했다. 이번에는 collected observed summary도 `baseline_comparison_status`, `next_kpi_blocker`를 직접 surface하도록 맞춰 decision을 내려가지 않아도 운영 화면에서 ready/deferred와 blocker를 바로 읽게 했고, sample 부족 deferred 경계도 별도 운영형 fixture로 잠가 collected summary / final decision이 같은 `insufficient_observed_samples` 판단을 유지하는지 고정했다. 이어서 `completion_requires_real_runs=true` 태스크가 dry-run만으로 실제 observed 완료처럼 보이지 않도록 `simulated` 메타데이터, legacy dry-run heuristic, `cmd_status`의 `completed (dry-run)` 표기, overview readiness 집계 제외까지 묶어 read/write/status 호환을 닫았다. 이제 V4 2차는 새 반례를 더 추가하는 구현 단계가 아니라, 현재 완료 기준을 유지하면서 운영 데이터 drift를 감시하는 유지·검증 단계로 넘어간다.

   추가로 task-file autopilot 실행 결과도 `instruction`과 `mode`를 보존하도록 일반 pipeline과 result schema를 맞췄다. 기존 기록은 소급하지 않고, 새 실행부터 복잡도 분류와 자동 분해 근거로 사용한다.

2. Operator Experience 4차
   구현 1차는 반영됐다. `plan / task / review`의 next-action 품질과 response-mode fixture 보강, Operator Experience 정리 요청의 `$omc-plan` 정렬, expensive flow report의 다양성 surface와 우선순위 summary가 들어갔다.
   최근에는 expensive-flow summary에도 `operator_validation_status`, `output_bloat_followup_needed`, `output_bloat_status_line`를 추가해, `output_bloat`가 관측되더라도 주 병목이 아닐 때는 follow-up 구현보다 `wrong_next_step` 축을 계속 우선해야 한다는 운영 판정을 한 화면에서 읽게 맞췄다.
   추가로 `"plan으로 계획 세우고 task 했는데 왜 작업을 선언하라는거지"` observed 요청을 wrong-next-step 회귀 테스트로 별도 고정해, 재선언 혼란 케이스에서도 baseline이 `$omc-task`로 과수렴하고 candidate는 `사용자 선택 대기`로 멈춰야 한다는 경계를 explicit하게 잠갔다.
   현재 기준으로는 `dominant_flow_kind=wrong_next_step`, `operator_next_priority=tighten_next_action_routing`, `operator_validation_status=ready_to_close`가 수동 검증과 autopilot 검증에서 함께 일관되게 확인됐다.
   `.omc/tasks/operator-experience-validation.json`은 이제 `completed`로 남고, `benchmark-report`에도 `operational_validation_stage=operator_experience_validation` 메타데이터가 보존돼 자동 실행 완료 증빙까지 확보됐다.
   즉, 이 트랙은 구현 중심 다음 우선순위가 아니라 운영 검증 중심 후속 정리 단계로 이동했고, 현재 판정은 `조건부 통과`가 아니라 `1차 완료 후 유지·검증 단계`에 가깝다.

3. Learned orchestrator 진입 조건 정리
   데이터가 충분한지 판단하는 gate를 더 명시적으로 만든다.
   telemetry 축적 기준과 정책 비교 가능 케이스 기준을 함께 고정한다.

Codex cost metadata 경로 조사와 token-only 운영 지표 정책 결정은 완료했다. 현재 실행 큐는 아래 순서로 유지한다.

1. `.omc/tasks/v4b-operational-maintenance.json` 기준 운영 observed 유지 검증
2. `.omc/tasks/operator-experience-validation.json` 기준 Operator Experience 설명력 검증
3. 실제 executor capability evidence 축적 및 freshness/환경 기준 검증

lesson 2개(`비대화형 Codex wrapper`, `setup force dirty 범위 분리`)는 기록 완료했으며, 이번 정리 범위에서 추적 대상으로 커밋한다. 외부 사용처의 setup force dirty 변경은 각 저장소별로 별도 리뷰·커밋한다.

### 최소 KPI 기준

`V4 multi-run KPI summary 2차`는 아래 조건을 만족해야 완료로 본다.

- observed_request / observed_output 기준 multi-run 실행 샘플 20회 이상
- neutral observed seed는 수집량으로만 보이고 readiness 입력에서는 제외된다
- observed_output은 `comparison_scope`, response sample을 보존하되 `mode_accuracy` / `task_start_delay` decision metric을 공짜로 밀어 올리지 않는다
- observed_output producer는 partial metadata를 허용하지 않고, task metadata backfill 후에도 필수 schema가 비면 benchmark payload를 남기지 않는다
- threshold taxonomy(`ready / pending / ambiguous`)와 candidate 비교가 같은 observed fixture 기준으로 재현 가능하다
- distinct policy pair 2개 이상
- `reroute rate`, `retry-to-success rate`, `cost per successful task` 3개 KPI가 모두 표에 노출
- 한 번의 실행 요약이 아니라, 반복 실행에서 같은 형식으로 재현 가능
- baseline은 직전 정책 또는 고정 기준값 대비로 정의된다
- baseline 대비 개선/악화 판단 문구가 함께 표시된다
- baseline/timebox 예외 허용은 운영 검증 목적에서 timebox로만 허용한다
- `Operator Experience 4차`는 구현 우선순위의 다음 단계로 올리되, V4 2차의 남은 운영 검증과는 분리해서 다룬다

### 운영 유지 체크포인트

- dry-run completion은 운영 완료 샘플에 포함하지 않는다
- `operational_validation_readiness=start-ready`가 overview / collected summary / decision surface에서 같이 보여야 한다
- `next_priority_recommendation`과 `next_priority_reason`은 ready 이후에도 operator follow-up 문맥을 잃지 않아야 한다
- `wrong_next_step`이 주 병목이 아닌 경우에만 output_bloat follow-up을 다음 구현 후보로 올린다
- `wrong_next_step`이 주 병목이 아니면 `next_priority_recommendation`은 `compress_operator_outputs`로 바로 바뀌지 않는다
- autopilot 재실행 시 기존 task state가 `running`이면 live PID는 차단하고, dead/missing PID는 `stale_recovery` 이력과 분리된 `stale_reason`으로 복구돼야 한다
- `.omc/tasks/v4b-operational-maintenance.json`은 `resume_failed=true`, observed metadata contract, `expect_only` step 구성을 갖춰 headless 자유응답 때문에 유지 검증이 멈추지 않도록 고정돼야 한다

`Operator Experience 4차`는 아래 조건을 만족해야 한다.

- `plan / task / review` next-action이 각기 1개로 수렴
- 시작 스킬 오판 패턴을 benchmark로 재현 가능
- reroute / delay 이유와 재개 조건이 한 화면에서 읽힘
- 첫 구현 배치는 `response-mode fixture 기반 next-action 의도 분기 정밀화`로 시작한다

## V5 후보 트랙 구체화

Fugu식 오케스트레이션에서 실제로 가져올 가치가 높은 다음 3개 트랙은 아래와 같다.

### 1. Next-step Decision Engine 일반화

- 목표: `plan / task / review / ship / status` 전반의 다음 액션 추천을 공통 decision engine으로 수렴
- 문제: 일부 surface는 정렬됐지만 스킬별 추천 규칙이 아직 부분적으로 흩어져 있다
- 완료 기준:
  - 같은 입력 상태면 어떤 surface에서도 같은 `next_action / next_priority`가 나온다
  - `wrong_next_step / reroute / output_bloat / over_stage_entry` 우선순위가 같은 규칙표로 설명된다
  - 대표 반례가 fixture와 benchmark 회귀 테스트로 고정된다
- 산출물:
  - 공통 decision input schema
  - 스킬별 adapter
  - regression fixture set
- 최근 반영:
  - readiness 쪽에 먼저 들어가 있던 input-builder 패턴을 operator priority 경로에도 적용해, `wrong_next_step / reroute / output_bloat / over_stage_entry` 판단 입력이 `core + extension` shape로 한 번 감싸지도록 1차 정렬했다.
  - 이어서 `next_priority`도 `report_decision / collected_summary` 양쪽에서 같은 surface adapter builder를 타도록 이관해, `source_surface + extension` 조립 규칙을 한 곳으로 모았다.
  - 추가로 `resolve_next_priority / resolve_next_priority_from_input`를 공통 모듈로 올려 `benchmark / autopilot`이 같은 priority rule을 공유하도록 2차 공통화를 마쳤다.
  - overview도 이제 `shared input -> shared resolver` 경로를 직접 타도록 보강했고, 해당 경로는 전용 회귀 테스트로 잠가 local unpacking drift를 막았다.
  - 최근에는 `operator priority`에 이어 `output_bloat validation`과 `operator explanation`도 shared decision input contract로 이관해, benchmark 쪽이 thin wrapper만 남기고 같은 resolver를 직접 타도록 정리했다.
  - 이어서 `overview_summary`의 `next_priority` adapter도 `source_surface=overview_summary`까지 포함한 공통 input shape로 잠가, surface별 adapter drift를 fixture 수준에서 바로 감지할 수 있게 맞췄다.
  - 추가로 `operator explanation`도 ready flow 기준 shared resolver와 benchmark adapter가 같은 설명 라인을 유지하는 parity fixture를 넣어, explanation surface drift까지 같은 방식으로 잠갔다.
  - autopilot은 `complexity_class_required=true`인 명시적 분석 스텝에만 `complexity_class / complexity_reason`을 저장하고, 일반 task/review/ship 스텝 오염을 회귀 테스트로 차단한다.
- 구현 순서:
  1. decision input schema 고정 - 완료
  2. priority rule 공통화 - 완료
  3. skill adapter 이관 - 사실상 완료 (operator priority / output_bloat validation / operator explanation / overview next_priority parity 고정)
  4. fixture 확대 - 대표 반례 마감 단계 (새 observed failure가 다시 잡힐 때만 추가 확장)

### 2. Cost-Quality Policy Layer

- 목표: 작업 난이도와 실패 비용에 따라 `cost_saver / balanced / quality_first`를 추천하거나 반자동 결정
- 문제: 지금은 사용자가 모델 강도와 thinking 강도를 직접 판단해야 하는 비중이 크다
- 완료 기준:
  - 요청 난이도, 실패 비용, 범위, ambiguity를 보고 policy profile을 추천한다
  - 선택 근거가 `reason summary`로 남고 benchmark에서 비용/품질 차이를 재현 가능하다
  - 토큰 낭비 케이스와 품질 실패 케이스를 같은 비교 리포트에서 읽을 수 있다
- 산출물:
  - policy profile 3종 정의
  - 선택 규칙표
  - observed 비교 리포트
- 구현 순서:
  1. profile 정의
  2. trigger 조건 정의
  3. benchmark case 연결
  4. summary surface 노출

policy decision input SSOT:
- `failure_cost`
- `ambiguity`
- `operator_goal`

입력 축은 3개로 시작한다.
`failure_cost / ambiguity / operator_goal`만 Cost-Quality Policy Layer의 SSOT로 쓰고,
`task_kind / risk / review_severity / retry_count / sensitive_path` 같은 신호는
Decision Engine 또는 runtime routing에서 파생 입력으로만 사용한다.

- `cost_saver`: low failure cost + low ambiguity + speed goal
- `balanced`: 기본값 및 low-confidence fallback
- `quality_first`: high failure cost 또는 quality goal 우선
- `confidence=low`이면 `balanced + user_selection_needed=yes`로 고정한다.

Layer boundary:
- Cost-Quality Policy Layer: 정책 프로필 추천과 설명만 담당
- Executor Recommendation Surface: 실행기/모델 매핑만 담당
- Reroute Layer: 실패 후 fallback / retry / delay만 담당

최근 반영:
- policy helper 1차는 위 3축 기준으로 축소 정렬됐다.
- 기본 반환은 `balanced`로 보수화했고, `cost_saver`는 `low failure cost + low ambiguity + speed goal`의 명시적 lightweight 조건에서만 선택되게 제한했다.
- low-confidence 경계는 `balanced + user_selection_needed=yes` output contract로 고정했다.
- `omc-plan` surface 1차도 연결되어 `policy_profile / policy_reason_summary / policy_confidence`와 low-confidence fallback 규칙을 plan 계약에서 직접 읽을 수 있게 맞췄다.
- benchmark/report surface에도 `recommended_policy_profile / policy_reason_summary / policy_confidence / user_selection_needed`가 직접 노출되고, 관련 회귀 테스트로 summary 계약이 고정됐다.
- policy comparison acceptance helper가 observed outcome·retry·quality failure·cost evidence를 받아 `hit / over_conservative / over_aggressive / pending`으로 보수적으로 분류한다. 실패 outcome 또는 비용 근거 부족은 `pending`으로 남겨 임의의 품질·비용 승격을 막는다.
- Executor Recommendation Surface handoff는 policy 필드와 executor 이유/fallback을 한 계약으로 묶고, validator가 recommendation-only 및 실행 차단 상태를 확인한다. `resolve_policy_summary()`가 handoff 오류 목록까지 반환해 누락을 조용히 통과시키지 않는다.

설계상 남은 갭:
- policy comparison 결과를 실제 observed benchmark/report summary에 직접 연결해 운영 데이터에서 반복 집계하는 작업이 남아 있다.
- 실제 USD 비용 evidence가 없는 동안에는 token-only/pending 정책을 유지하며, 임의 비용 환산은 하지 않는다.
- Executor Recommendation Surface의 capability evidence가 충분해질 때까지 승인 기반 reroute와 auto-switch는 보류한다.

confidence threshold 표:

| failure_cost | ambiguity | operator_goal | recommended_policy_profile | confidence |
|---|---|---|---|---|
| low | low | speed | cost_saver | high |
| high | high | quality | quality_first | high |
| medium | high | balanced | balanced | low |

policy comparison acceptance line:
- 적중(hit): observed outcome과 policy recommendation이 같은 방향으로 수렴
- 과보수(over-conservative): balanced/quality_first가 반복되지만 실패 비용 대비 과도한 비용 증가가 확인됨
- 과공격(over-aggressive): cost_saver가 선택됐지만 retry/review failure로 곧바로 상향 필요가 확인됨

executor handoff summary fields:
- `recommended_policy_profile`
- `policy_reason_summary`
- `policy_confidence`
- `user_selection_needed`

후속 구현 순서:
1. confidence threshold 표 문서화
2. policy comparison acceptance line 추가
3. summary surface handoff contract 고정

### 3. Executor Recommendation Surface

- 목표: Codex / Claude / Gemini 또는 모델 강도를 작업 성격에 따라 추천
- 문제: 현재는 실행기와 강도를 사람이 자주 직접 고른다
- 완료 기준:
  - `추천 실행기 + 이유 + fallback`이 자동 산출된다
  - 실패 클래스별 reroute rule이 있다
  - 사람 승인 하에서만 executor 전환이 가능하다
- 산출물:
  - executor capability matrix
  - routing rule table
  - fallback / reroute rule
- 구현 순서:
  1. 추천-only read mode
  2. 승인 기반 reroute
  3. 제한적 auto-switch

executor recommendation input contract:
- `task_kind`
- `recommended_policy_profile`
- `risk`
- `sensitive_paths`
- `operator_goal`

executor recommendation output contract:
- `recommended_executor`
- `executor_reason_code`
- `executor_reason_summary`
- `executor_fallback`
- `user_selection_needed`
- `recommended_policy_profile`
- `policy_confidence`
- `recommendation_only=true`
- `evidence_status=unverified`
- `capability_evidence_status=unverified|insufficient|observed|rejected`
- `capability_evidence_source=none|fixture|observed`
- `capability_evidence_sample_count`
- `capability_evidence_reason_codes`
- `execution_allowed=false`

executor 설계상 남은 갭:
- capability evidence 관측 schema와 malformed/partial/fixture/observed 경계는 구현했지만, 실제 운영 데이터 기반 추천 품질은 아직 검증되지 않았다.
- `eligible` threshold, 비용 환산 정책, stale evidence 정책은 아직 확정하지 않았다.
- 승인 기반 reroute와 제한적 auto-switch는 아직 구현하지 않았다.
- child scope 기반 capability routing은 아직 추천 근거로 사용하지 않는다.

현재 반영:
- domain child와 integration-review child에 추천-only executor handoff를 연결했다.
- parent/child 모두 `recommendation_only`, `evidence_status`, policy profile/confidence contract를 검증한다.
- capability evidence 관측 계층을 추가해 `source_type`, `observed_at`, `sample_count`, `environment_fingerprint`와 상태·reason code를 보존한다.
- fixture/observed 데이터 모두 `execution_allowed=false`로 고정해 관측과 실행 허가를 분리했다.
- 실제 `.omc/runs` record를 `executor + task_kind + domain + policy_profile` 키로 집계하고, stale/environment mismatch/insufficient 상태를 관측 surface에 반영한다.
- freshness·환경 경계 fixture와 실제 run aggregation 회귀 테스트를 확보했다.
- `.omc/runs/*/result.json` loader가 persisted run만 읽고 malformed/non-object 결과는 건너뛰도록 고정했다.
- `running/pending/in_progress` run은 실패 표본에서 제외하고 `in_progress_count`로만 기록하며, fresh/stale sample count를 분리해 최신 관측값이 과거 stale 표본에 오염되지 않게 한다.
- current environment 표본과 mismatch 표본을 별도 집계해 현재 환경의 fresh evidence가 있으면 과거 환경 표본이 전체 판정을 덮지 않게 한다.
- malformed/non-object/read-error run은 `rejected_run_count`와 `rejected_run_reasons` summary로 보존하고, observed timestamp는 timezone 포함 ISO-8601만 허용한다.
- 추천-only acceptance fixture와 capability evidence 경계 회귀 테스트를 확보했다.
- executor candidate contract 1차를 추가해 capability 관측을 `observed_candidate_only | insufficient_data | blocked_data_quality`로 분리하고, `quality_success`와 `final_success`를 별도 필드로 보존한다.
- 비용은 `cost_status=known|unknown`만 기록하며 비용 미상이나 품질 미검증을 eligibility로 승격하지 않는다. 모든 결과는 `execution_allowed=false`로 유지한다.
- 승인 placeholder(`approval_required`, `approval_id`, `approved_executor`, `approved_scope_fingerprint`, `approved_at`, `expires_at`)와 canonical scope fingerprint를 추가해 scope 변경 시 승인 근거가 재사용되지 않도록 했다.
- candidate 상태별 단일 `next_action`(`collect_capability_evidence`, `repair_capability_evidence`, `review_executor_candidate`, `compare_executor_cost`)과 fixture 회귀 테스트를 고정했다.
- `running/pending/in_progress` aggregation도 동일한 candidate-only schema와 실제 `sensitive_paths` 기반 scope fingerprint를 반환하도록 맞췄고, `NaN`/무한대 비용은 `known`으로 승격되지 않게 방어했다.
- delegation handoff 1차로 parent-child scope subset, dependency status, cycle/missing dependency, malformed metadata rejection, `blocked_by`, deterministic topological order를 추천-only contract로 고정했다. 승인 lifecycle과 실제 executor 실행은 여전히 제외하며 `execution_allowed=false`를 유지한다.
- 실제 복잡 요청(`결제 API + 프론트 + 백엔드 테스트`)과 blocked dependency·scope mismatch 운영 fixture를 추가해, child handoff의 `next_action`과 추천-only 경계를 운영형 입력에서도 확인했다.
- overview KPI summary에 observed cost/quality/paired evidence count와 `cost_quality_validation_status`/blocker를 노출해 비용·품질 검증을 별도 게이트로 분리했다. 현재 실제 `.omc/runs`는 비용·품질 evidence가 부족해 `pending`으로 유지된다.
- paired evidence는 `same_surface`이면서 유효한 `baseline->candidate` 또는 `candidate->baseline`일 때만 인정하고, cross-surface·invalid policy pair 회귀 테스트로 오판정을 차단했다.

executor acceptance line:
- pass: `recommended_executor / executor_reason_summary / executor_fallback / user_selection_needed` 4개 필드가 한 surface에서 함께 설명된다.
- handoff pass: 위 4개 필드와 `recommended_policy_profile / policy_confidence / recommendation_only / evidence_status`가 같은 child contract에서 함께 검증된다.
- hold: 추천은 나왔지만 `executor_reason_summary` 또는 `user_selection_needed`가 비어 사람이 바로 선택 근거를 읽을 수 없다.
- fallback: 추천 실행기가 막혀도 `executor_fallback`이 같은 task_kind / policy_profile 문맥에서 바로 제시된다.
- reroute: fallback으로도 해결되지 않는 실패만 reroute layer로 넘기며, executor surface는 실패 이후 경로 결정을 직접 소유하지 않는다.

fallback vs reroute 책임 분리:
- fallback은 추천 실행기 선택 이후의 대체안 제시에 한정되며, task 재분해나 policy 재선택을 트리거하지 않는다.
- reroute는 fallback 실패 또는 executor surface 바깥 신호(`retry_exhausted`, `quality_failure`, `orchestration_failure`)가 확인됐을 때만 열린다.
- executor surface는 `어떤 실행기를 먼저 쓸지`와 `막혔을 때 어떤 실행기로 한 번 더 시도할지`까지만 답한다.
- reroute layer는 `같은 실행기군 재시도`가 아니라 `plan_retry / critique / delay / hold` 같은 다음 경로 결정을 소유한다.

executor handoff acceptance binding:
- `recommended_policy_profile`와 `policy_confidence`는 executor 선택 강도의 근거로 읽혀야 한다.
- `policy_reason_summary`는 `executor_reason_summary`와 서로 모순 없이 이어져야 한다.
- `user_selection_needed=yes`면 executor surface도 추천-only로 멈추고 자동 전환을 시도하지 않는다.

executor 후속 구현 순서:
1. 실제 observed capability evidence 축적 및 freshness/환경 기준 검증
2. threshold·비용 환산 정책을 별도 확정한 뒤 eligibility 판정 설계 (candidate-only contract와 분리)
3. 승인 기반 child reroute와 dependency 실행 gate 설계
4. budget/retry/timeout guard 검증 후 제한적 auto-switch 검토

## Learned Orchestrator 진입 게이트

V5는 "바로 구현해볼 만한 다음 기능"이 아니라, 아래 gate를 통과했을 때만 여는 연구/제품화 단계로 본다.

### 진입 조건

- V4 운영 observed 유지 검증이 계속 `ready`를 유지한다
- Operator Experience가 `ready_to_close` 상태를 유지하고, 새 observed 케이스 1건 추가에도 주 우선순위가 흔들리지 않는다
- `Decision Engine 일반화`, `Cost-Quality Policy Layer`, `Executor Recommendation Surface`의 추천-only surface가 먼저 정리된다
- 사람 승인 없는 자동 executor 전환 없이도 policy/executor 추천 품질을 설명 가능하게 유지한다

### 보류 조건

- observed run은 충분하지만 policy drift 설명이 아직 약하다
- wrong_next_step가 여전히 주 병목인데 learned layer로 덮으려 한다
- executor recommendation이 추천-only 단계도 닫히지 않았다
- 운영 검증보다 구현 욕심이 앞서서 fallback/guard 설명력이 약해진다

### 시작 전 금지선

- learned orchestrator를 runtime closed-loop auto-switch로 바로 연결하지 않는다
- benchmark/fixture 없이 learned policy를 넣지 않는다
- 기존 decision engine 설명 가능성을 희생하면서 black-box 점수를 올리지 않는다

### 진입 체크리스트

- telemetry 300건 이상
- 정책 비교 가능 케이스 100건 이상
- retry reason 분류 정확도 85% 이상
- V4 운영 유지 검증이 최근 기준에서도 `ready`를 유지한다
- Operator Experience가 새 observed 케이스 1건 추가에도 `wrong_next_step` 우선순위를 흔들지 않는다

### 진입 산출물

- learned candidate scorecard 1차
- rule-based baseline comparison report
- shadow recommendation audit log
- learned 후보가 기존 추천을 뒤집는 대표 케이스 10개 이상

### 첫 구현 범위

- 추천-only shadow mode로 시작한다
- 기존 rule-based decision은 primary, learned score는 secondary로 병렬 기록한다
- executor 자동 전환이 아니라 `추천 차이 감지`와 `설명 품질 비교`만 먼저 다룬다
- 기존 `next_priority_recommendation` surface와 충돌하면 learned 결과는 참고 정보로만 남긴다

한 줄 기준:

- `추천 엔진 3축이 먼저, learned layer는 맨 마지막`

## Fugu식 기능 MVP 설계

## 로드맵 검증 매트릭스

지금 단계에서는 "많이 수정했다"와 "실제로 반영됐다"를 같은 말로 쓰지 않는다.
로드맵 완료 항목은 아래 기준으로 다시 확인한다.

| 로드맵 완료 항목 | 실제 반영 증거 | Fugu 비교에 쓰는 축 | 판정 규칙 |
|---|---|---|---|
| V4. Telemetry-driven Tuning | roadmap 문구 + 관련 테스트 + overview/summary/autopilot completed 상태 | feedback loop / policy tuning | 문서만 반영이면 `부분 반영`, 테스트/실행 증거까지 있으면 `반영 확인` |
| Operator Experience | roadmap 문구 + response-mode fixture + expensive-flow summary + validation task 상태 | next-action quality / operator control | 문서+fixture만 있으면 `반영 확인`, 운영 판정까지 닫히면 `체감 개선 확인` |
| Fugu식 MVP 설계 | benchmark 문서 + roadmap MVP 섹션 + 후속 설계 태스크 | single-entry runtime orchestration | 설계만 있으면 `문서만 반영`, 구현/검증이 붙어야 `반영 확인` |

Fugu 비교 문구는 `현재 상태 참조`와 `반영 검증 완료`를 구분한다.

완전 자동 전환보다 먼저 넣을 최소 제품 단위는 아래 3개다.

### MVP 1. Decision Engine Core

- 입력:
  - `task_kind`
  - `ambiguity_level`
  - `failure_cost`
  - `scope_size`
  - `observed_bottleneck`
  - `ship_intent`
- 출력:
  - `recommended_next_skill`
  - `recommended_policy_profile`
  - `recommended_executor`
  - `reason_summary`
  - `confidence`
- 원칙:
  - 초기에 자동 실행은 하지 않고 추천 엔진으로만 시작한다
  - 스킬은 편의성 레이어이고 강제 진입점은 기존 CLI 가드를 유지한다

### MVP 2. Policy Profile 3종

- `cost_saver`: 짧은 조회, 작은 수정, low-risk 작업
- `balanced`: 일반 개발 기본값
- `quality_first`: 설계, 리팩터링, 교차 영향 큰 작업
- 각 profile은 권장 model / thinking / executor 전략을 가진다

### MVP 3. Executor Recommendation Surface

- 출력 예시:
  - `추천 실행기: Codex`
  - `추천 프로필: balanced`
  - `이유: 범위 고정, 코드 수정 중심, 교차 시스템 리스크 중간`
  - `fallback: Claude Code quality_first`

### MVP 제외 범위

- 자동 executor 전환
- 정책 자동 학습
- 무인 closed-loop reroute

위 3개는 구현비와 운영 리스크가 커서 V5 초기 MVP에서는 제외한다.

## 토큰 대비 효과 점수표

점수 기준은 아래처럼 본다.

- 효과: 5 높음
- 구현비: 5 큼
- 토큰절감: 5 큼
- 리스크: 5 큼

| 항목 | 효과 | 구현비 | 토큰절감 | 리스크 | 총평 |
|---|---:|---:|---:|---:|---|
| Decision Engine 일반화 | 5 | 3 | 4 | 2 | 가장 먼저 |
| Cost-Quality Policy Layer | 5 | 3 | 5 | 2 | 두 번째 |
| Executor Recommendation Surface | 4 | 2 | 3 | 1 | 빠른 승리 |
| 승인 기반 reroute | 4 | 4 | 3 | 3 | 중기 |
| 자동 executor switch | 4 | 5 | 4 | 4 | 나중 |
| learned orchestrator | 5 | 5 | 4 | 5 | 맨 나중 |

현재 기준 우선순위는 아래 3개로 고정한다.

1. `Decision Engine 일반화`
2. `Cost-Quality Policy Layer`
3. `Executor Recommendation Surface`

## Decision Engine Spec

runtime decision은 `failure_class / escalation_policy / retry_count / reason_codes` 조합으로 결정한다.
핵심은 failure path마다 분기문을 따로 늘리는 것이 아니라, 같은 입력 shape에서 같은 decision을 내리게 하는 것이다.

기본 상태 전이표는 아래처럼 고정한다.

- `execution_failure` + default policy + threshold 미만
  - decision: `same`
  - reroute_target: 없음
  - 의미: 현재 경로를 한 번 더 유지한다.

- `execution_failure` + default policy + threshold 이상 또는 `retry_exhausted`
  - decision: `reroute`
  - reroute_target: `task_retry`
  - 의미: 구현 경로 재시도로 올린다.

- `execution_failure` + aggressive policy
  - decision: `reroute`
  - reroute_target: `task_retry`
  - 의미: threshold를 기다리지 않고 빠르게 재시도 경로로 보낸다.

- `quality_failure` + default policy
  - decision: `reroute`
  - reroute_target: `task_retry`
  - 의미: 첫 REVISE 지적을 구현에 반영한 뒤 새 diff를 다시 검토한다.

- `quality_failure` + conservative policy
  - decision: `hold`
  - reroute_target: 없음
  - 의미: 자동 우회보다 명시적 재설계를 우선한다.

- `contract_failure`
  - decision: `hold`
  - reroute_target: 없음
  - 의미: 사용자의 명시적 확인 없이는 진행하지 않는다.

- `orchestration_failure`
  - decision: 기본 `hold`, 필요한 경우만 `plan_retry`
  - reroute_target: 상황별
  - 의미: 잘못된 시작 스킬, 잘못된 reroute, metadata 부족은 엔진 자동 보정보다 경로 재설계를 우선한다.

이 표를 먼저 고정한 뒤 runtime이 이를 소비해야 한다.
반대로 runtime 분기부터 늘리면 failure path마다 예외 규칙이 다시 생긴다.

failure path 일반화에서 최소 orchestration failure shape와 single-run telemetry가 안정된 뒤 multi-run KPI summary를 붙였다.
이제 KPI는 정책 비교와 next-action 품질 개선 근거로 연결돼야 한다.

여기까지 가면 OMC는 `규칙 기반 스킬 오케스트레이터`에서 `초기 자동 모델 전환 엔진`으로 넘어가기 시작한다.

## 하지 말아야 할 것

- learned orchestrator를 너무 일찍 도입하기
- 로그 없이 정책만 계속 늘리기
- 모델 선택을 완전히 블랙박스로 만들기
- step 메타데이터 없이 예외 규칙만 쌓기

## 한 줄 결론

OMC가 완전 자동 모델 전환 제품으로 가려면,
`task_kind 기반 규칙 라우팅`을
`step 메타데이터 -> 실패 기반 승격 -> 실행 telemetry -> 데이터 기반 튜닝`
구조로 확장해야 한다.
