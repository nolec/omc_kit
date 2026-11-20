<!-- OMC:BEGIN -->
<!-- OMC:AGENTS:V1 -->
## Engineering Ethos (필독 — 모든 판단에 적용)

**모든 추천·구현·리뷰는 `ETHOS.md`의 원칙을 따릅니다.**
핵심 4원칙: **완성하라 / 찾아라 / 사람이 결정한다 / 지루한 것을 선택해라**
세부 내용: `ETHOS.md` 참조.

---

## OMC — Orchestrated Multi-agent Craft

이 저장소에는 OMC 구성요소가 직접 설치되어 있습니다.

## Codex 명시적 진입 — 자동 훅 비활성

<!-- OMC_CODEX_READ_ONLY_BOOTSTRAP -->
**Codex는 요청을 받기 전 원본에서 state init·session_start를 실행하지 않습니다.**
시작 훅이 미발화해도 수동 초기화로 돌아가지 않습니다. 상태/계획 조회는 읽기만 합니다.
일반 계획·구현·수정·리뷰는 Superpowers 흐름과 프로젝트 규칙을 따릅니다. 읽기 전용 조회나 QA 목록 작성만으로 OMC 구현 흐름에 진입하지 않는다.

명시적으로 선택한 OMC 작업에만 아래 진입·동기화·파이프라인 규칙을 적용합니다. `$omc-plan`, `$omc-task` 또는 OMC 작업 흐름을 직접 요청한 경우가 해당합니다.

직접 선택할 수 있는 스킬에는 `$omc-investigate`와 `$omc-review`도 포함됩니다. 각 작업에는 해당 스킬의 계약을 적용합니다.

새 OMC 구현 작업은 신뢰하는 kit의 `python3 -I -B scripts/omc.py workspace-entry --intent start --source <원본> --request <요청> [--parent <승인된외부부모>]`로 준비하며 실행기가 고유 경로를 고릅니다.
반환된 ticket 경로·SHA와 workspace/work/session binding을 보존합니다. 같은 요청의 재전달·조회는 같은 ticket을 사용합니다.
`WORKSPACE_READY`는 공간 준비이며 편집·설치·검증·전체 작업 완료가 아닙니다. 준비된 공간에서는 해당 Tier의 CONTRACT·RED 요건을 구현 전에 적용합니다.
기존 OMC 작업은 기존 작업 ID로 이어간다. 기존 ticket과 명시 workspace/work ID로 연결하고, 모호하면 작업 식별만 확인합니다. 원본 상태를 복구하거나 새 작업으로 몰래 바꾸지 않습니다.


---

## 필요한 기능을 직접 호출하기

- 상태는 `python3 scripts/omc.py state status --target .`로 조회합니다.
- 커밋 범위는 `scope-check`로 확인합니다. UI 확인 항목은 `$omc-qa`에 요청합니다.
- 관련 교훈은 `python3 scripts/omc_lesson.py --target . search "키워드"`로 조회합니다.

## 명시적 OMC 작업의 상세 규칙

OMC 구현 작업에서 파일을 수정하기 전에는 승인된 작업공간의 상태와 요청 연결을 확인합니다. 역할 확인·Tier·승인·CONTRACT·실제 RED 절차는 [OMC 운영 참조](docs/omc_explicit_workflow.md#명시적으로-선택한-omc-작업)와 선택한 스킬에서 확인합니다.

OMC 오케스트레이션을 명시적으로 요청했다면 위 운영 참조의 **오케스트레이션 사용 원칙**을 따릅니다.

### OMC Tier 1 요약

명시적으로 시작한 OMC 작업에서 동작이나 API를 변경할 때는 CONTRACT를 확인합니다. 신규 구현 파일은 승인된 CONTRACT와 실제 RED가 필요합니다. 버그 재현 등 기존 RED 의무와 적용 예외는 위 운영 참조에서 확인합니다.

## OMC 스킬 완료 후 자동 진행 금지

OMC 스킬이 완료되면 결과를 보고하고 다음 스킬로 자동 진입하지 않는다. 승인된 단일 작업 실행과 이미 제시한 로컬 커밋 선택의 예외는 명시적 OMC 작업의 상세 규칙을 따릅니다.
<!-- OMC:END -->
