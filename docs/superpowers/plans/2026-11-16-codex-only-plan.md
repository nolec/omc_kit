# Codex-only OMC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Codex 스킬과 공통 엔진을 보존하고 다른 IDE 전용 지원을 제거하며 자동 훅은 비활성화한다.

**Architecture:** 설치 manifest·source hash·doctor가 동일한 Codex 전용 계약을 사용한다. 기존 타깃의 사용자 설정은 손대지 않고 원본 kit의 소유권이 확인된 도구별 구성만 보존 후 제거한다. 공통 엔진·명시 호출 가드는 유지한다.

**Tech Stack:** Python, pytest/unittest, JSON, shell hook templates.

**Spec:** `docs/superpowers/specs/2026-11-16-codex-only-design.md`

## Global Constraints

- Codex 스킬·공통 엔진 유지, Claude·Gemini·Cursor 지원 제거, 자동 훅 비활성화 유지.
- `.agent` 중복 배포 제외. 기존 파일은 OMC 소유가 확인된 것만 정리한다.
- 기존 기록·실패·백업·미커밋 변경과 제품 코드·Git 이력을 보존한다.
- 실제 프로젝트 설치·복구·storefront 변경·commit/push는 하지 않는다.
- 공통 모델 provider와 과거 연구·증거 fixture는 범위 밖이다.
- 삭제 후보별 경로·SHA256·파일 메타데이터를 고정하고 새 보존본의 복원 바이트 대조를 통과해야 제거한다.
- 원본 kit의 OMC 세션·자동 훅을 이번 계획 작성이나 구현 준비 때문에 다시 활성화하지 않는다. 적용 가능한 필수 가드가 변경을 막으면 우회하지 말고 차단 사실을 보고한다.

## Review Focus

1. 재설치가 다른 도구 구성 또는 자동 훅을 다시 생성하지 않는가: Task 1·2.
2. 사용자 고유 설정이나 무관한 `.agent` 자료를 삭제하지 않는가: Task 1·4.
3. doctor가 disabled를 작업 승인·완료로 오인하지 않는가: Task 3.
4. source hash·설치 receipt·검사 파일 목록이 서로 어긋나지 않는가: Task 1·3.
5. 문서·스킬이 자동 초기화·다른 IDE 경로를 다시 강제하지 않는가: Task 2·5.

### Task 1: Codex 전용 설치 계약

**Files:** Modify `scripts/install.py`, `scripts/omc_source_hash.py`; Test `scripts/test_install.py`, `scripts/test_install_whitelist.py`; Create test `scripts/test_codex_only_install.py`.

**Interfaces:** 기존 installer `_main() -> int`, `_build_install_manifest(...)`, `source_sha256(source_kit: Path) -> str`와 설치 receipt 형식을 유지한다. 지원하지 않는 IDE의 배포 항목만 제외한다.

- [x] 새 테스트 `test_fresh_install_is_codex_only`를 작성한다. 임시 타깃에 `.agents/skills/omc-task/SKILL.md`와 공통 엔진이 존재하고 `.claude`, `.gemini`, `.cursor`, `.agent`, `.cursorignore`, `CLAUDE.md`, `GEMINI.md`가 생성되지 않음을 단언한다.
- [x] `test_reinstall_preserves_non_codex_user_files`에서 다른 IDE 폴더의 사용자 sentinel 파일을 설치 전후 바이트 대조한다. `--force`도 포함한다.
- [x] `python3 -m pytest scripts/test_codex_only_install.py -q`를 실행해 실제 RED 원문을 기록한다.
- [x] 복사 분기·생성 문서·manifest·source hash의 다른 IDE 항목을 제거한다. 설치기의 다른 IDE 사용자 파일 자동 삭제 기능은 추가하지 않는다.
- [x] `python3 -m pytest scripts/test_codex_only_install.py scripts/test_install.py scripts/test_install_whitelist.py -q`를 실행한다. 현재 지원 계약 테스트를 갱신하되 과거 증거 fixture는 변경하지 않는다.

### Task 2: 자동 실행을 꺼진 상태로 유지

**Files:** Modify `templates/.codex/hooks.json`, `templates/AGENTS.md`, `templates/CODEX.md`, `CODEX.md`, `scripts/install.py`, `scripts/omc.py`; Test `scripts/test_codex_only_install.py`, `scripts/test_omc_setup_smoke_contract.py`.

**Interfaces:** `setup --target`와 직접 installer 호출을 유지한다. `.agent-hooks` 공통 스크립트는 명시 호출용으로 남긴다. 새 활성화 옵션을 만들지 않는다.

- [x] `test_setup_and_force_reinstall_leave_hooks_disabled`를 추가한다. Codex 이벤트 배열은 비어 있고 Git pre/post-commit 자동 훅과 세션 시작 부작용이 없어야 한다. 기존 사용자 훅 sentinel의 바이트·모드도 유지한다.
- [x] `test_generated_codex_guidance_does_not_require_auto_session_start`를 추가하고 실제 RED를 기록한다.
- [x] setup의 기본 session_start 실행, 자동 Git 훅 등록·업데이트, 자동 세션 시작 지시를 제거한다. 수동 공통 가드의 검증 조건은 완화하지 않는다.
- [x] 두 테스트 모듈과 `scripts/test_install.py`를 실행한다. 원본에 설치하지 않고 임시 타깃에서만 검증한다.

### Task 3: doctor·설치 감사 일치

**Files:** Modify `scripts/omc_doctor.py`, `scripts/omc_hook_contract.py`; 필요시 동일 배포 계약을 읽는 `scripts/omc_install_audit.py`; Test `scripts/test_omc_doctor_install_contract.py`, `scripts/test_omc_hook_contract.py`, `scripts/test_omc_install_audit.py`, `scripts/test_codex_only_install.py`.

**Interfaces:** `run_checks(root: Path) -> list[Check]`, 기존 검사 결과 구조 유지. 지원 범위와 자동 실행 정책에 대한 검사를 Codex 계약에 맞춘다.

- [x] `test_doctor_accepts_absent_non_codex_integrations`와 `test_disabled_hooks_do_not_imply_completed_work`를 추가한다. disabled는 의도된 자동 실행 정책이며 작업 완료·승인 상태를 생성하거나 덮어쓰지 않아야 한다.
- [x] `test_receipt_and_source_hash_follow_codex_manifest`에서 배포 파일·receipt·doctor가 같은 경로 집합을 검사하도록 검증한다.
- [x] RED 원문을 기록한 뒤 다른 IDE 필수 검사·미사용 계약 함수를 제거하고 기존 공통 상태 검증은 유지한다.
- [x] 위 네 모듈과 `scripts/test_omc_doctor_hook_block.py`, `scripts/test_omc_generated_install_isolation.py`를 실행한다.

### Task 4: 원본 kit의 도구별 생성물 정리

**Files:** 정리 후보 `templates/.claude/**`, `templates/.gemini/**`, `templates/.cursor/**`, `templates/CLAUDE.md`, `templates/GEMINI.md`, `templates/.cursorignore`; 원본 kit의 `.claude`, `.gemini`, `.cursor`, `.agent`, `.cursorignore` 안에서 소유권이 확인된 OMC 파일. 공통 `.agent-hooks`와 `.agents`는 보존.

**Interfaces:** 새 정리 CLI나 migration을 만들지 않는다. 고정 경로 manifest와 저장소 밖 보존본으로 일회성 정리를 수행한다.

- [x] tracked/untracked/ignored·symlink 여부와 사용자 고유 내용까지 조사해 삭제 파일 목록을 고정한다. 모호한 파일은 남긴다.
- [x] 고정 파일을 메타데이터 manifest와 함께 새 압축본에 저장하고 별도 복원 사본의 모든 파일 SHA256을 대조한다. 원본 Git 객체·기존 백업은 변경하지 않는다.
- [x] Task 1–3 검사가 통과하고 실행 직전 후보 바이트·메타데이터가 여전히 같을 때만 고정 파일을 제거한다. 실패·변경 발견 시 해당 파일은 유지한다.
- [x] 제거 전후 기존 사용자 파일과 최초 미커밋 변경 목록을 대조한다. 상위 폴더 일괄 삭제나 원장 변경은 하지 않는다.

### Task 5: 문서·스킬과 최종 회귀

**Files:** Modify `README.md`, `CODEX.md`, `docs/orchestration_usage.md`, `docs/kit_map.md`, `docs/quickstart_kr.md`, `.agents/skills/**`, `templates/.agents/skills/**`의 현재 지원 안내만. 공통 역할·품질 기준은 유지.

**Interfaces:** Codex `$omc-*` 및 명시적 공통 CLI 호출을 남긴다. 현재 비활성 정책과 과거 실행 증거를 구분한다.

- [x] 현재 안내의 다른 IDE 실행 지시·자동 초기화 강제를 제거하고 Codex 사용 예시로 맞춘다. 스킬 SSOT와 실제 복사본을 동기화한다.
- [x] `python3 -m pytest scripts/test_install.py scripts/test_install_whitelist.py scripts/test_codex_only_install.py scripts/test_omc_setup_smoke_contract.py scripts/test_omc_doctor_install_contract.py scripts/test_omc_hook_contract.py scripts/test_omc_install_audit.py scripts/test_omc_generated_install_isolation.py -q`를 실행한다.
- [x] 변경한 스킬에 해당하는 `scripts/test_omc_*_skill_contract.py`도 실행한다. 실패를 정책 제외로 숨기지 않는다.
- [x] `git diff --check`와 기존 미커밋 변경 보존 대조를 수행한다. 새 설치·재설치 결과, 비활성 훅, 남은 미분류 파일과 미검증 범위를 최종 보고한다. 실제 프로젝트 설치와 commit은 하지 않는다.
