# Claude Code + OMC Incremental Value Feasibility

Study ID는 `claude-code-omc-incremental-value-20260908-v1`이다. 현재 상태는 `FEASIBILITY_CONTRACT_IMPLEMENTED_NOT_STARTED`이며 feasibility 결과의 claim은 항상 `NO_SUPERIORITY_CLAIM`이다.

## 비교 범위

- baseline `raw_claude_code`: Claude Code의 동결된 기본 기능을 유지하고 OMC managed configuration만 끈다.
- treatment `claude_code_with_omc`: 같은 Claude Code 조건에 OMC managed configuration만 추가한다.
- 기존 Codex Persona Pilot evidence를 재사용하지 않는다. 기존 study ID, T0, case, receipt, key와 custody도 재사용하지 않는다.
- 비교 claim은 Claude Code에 OMC를 추가한 incremental value로 제한한다. Claude Code 대체, 모델 우위, Artifact 동등성 또는 전체 OMC 우위로 확대하지 않는다.

## 현재 허용된 작업

등록 계약의 정적 검증만 허용한다.

```bash
python3 scripts/omc_claude_incremental_value.py validate-registration \
  --registration docs/claude_code_omc_incremental_value_preregistration_v1.json
```

실제 Claude Code 실행은 별도 승인 전 금지한다. 현재 registration의 `execution_authorized=false`를 변경하거나 10건을 소급 수집하지 않는다.

## 실행 전 동결 조건

각 pair는 같은 request, repository commit, source tree, Claude Code version, model, reasoning, permission, timeout, verification command와 native skill·hook·plugin·MCP inventory를 사용한다. `repository_commit`은 저장소 object format에 따른 40자리 SHA-1 또는 64자리 SHA-256 Git object ID다. intervention·approval·token count는 비음수 정수이며 시간·비용은 유한한 비음수 숫자다. 두 arm의 허용된 차이는 OMC managed configuration뿐이다.

현재 `case_roster_state=NOT_FROZEN`이며 roster는 비어 있다. 실행 승인 전 정확히 10개 case의 case ID, 표시용 repository ID, canonical repository identity subject, 그 SHA-256, request hash, 전체 common configuration SHA-256, arm order를 채워 `FROZEN`으로 전환한다. identity subject는 독립 authorization authority가 실제 remote를 확인해 소문자 `{scheme: git_remote, host, owner, repository}`로 정규화하며 `.git` suffix나 임의 alias를 허용하지 않는다. validator는 subject의 canonical JSON에서 SHA-256을 직접 재계산한다. common configuration hash는 request·repository commit·source tree와 Claude Code version·model·reasoning·permission·timeout·verification commands·native skill/hook/plugin/MCP inventory·environment를 모두 결속한다. roster는 표시용 라벨이나 caller 제공 digest가 아니라 검증된 canonical identity 기준으로 최소 2개 repository를 포함하고 각 arm이 정확히 5번 먼저 실행되도록 균형화한다. 각 arm은 별도 workspace, session과 cache identity를 사용한다. 첫 arm의 파일·대화·cache·학습을 두 번째 arm에 전달하지 않는다.

실행 승인은 승인 receipt hash와 저장소 밖 custody의 독립 authorization authority가 서명한 receipt로 증명한다. authority 공개키는 registration이 아니라 `OMC_CLAUDE_INCREMENTAL_VALUE_TRUSTED_AUTHORIZATION_PUBLIC_KEY` 환경변수에서 주입한다. 승인 subject는 study ID, 실행 허용 여부, trusted Ed25519 execution public key와 frozen case roster SHA-256을 함께 결속하므로 승인 뒤 repository snapshot이나 공통 실행 구성을 바꾸고 execution key로 재서명해도 거부한다. raw provider output, final diff, verification output과 운영 지표는 각각 서명된 arm receipt에 결속하고, case ID·repository ID·불변 repository identity·arm order와 fatal/provider/blind/metric 상태를 포함한 pair envelope도 별도로 서명한다. arm identity를 제거한 packet의 blind evaluation이 끝나기 전 mapping을 공개하지 않는다.

## Feasibility 판정

10쌍 feasibility는 실행 가능성과 측정 완전성만 판정한다. registration과 receipt 무결성을 먼저 검증한 뒤, 유효하게 서명된 fatal violation은 설정·환경 불일치보다 우선해 `STOP`한다. 설정·환경 불일치와 provider 부재·blinding 실패·10쌍 미달은 `INCONCLUSIVE`, receipt binding 실패는 `BLOCKED`, metric 누락은 `FEASIBILITY_FAIL`이다. 모든 gate가 통과해도 결과는 `FEASIBILITY_PASS`이며 `NO_SUPERIORITY_CLAIM`을 유지한다.

30쌍 confirmatory는 feasibility와 별도 registration·T0·evidence root로 시작한다. 최소 2개 저장소와 baseline correction event 10건을 충족하기 전 30% 감소율을 판정하지 않는다.
