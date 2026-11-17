# Codex-only SSOT Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-subagent-driven-development (recommended) or superpowers-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수동 `omc_sync_ssot.py`에서 비Codex 구성의 동기화 경로만 제거하고 지원되는 경로·읽기 전용 모드·보존 경계를 회귀 검사로 고정한다.

**Architecture:** 기존 `SYNC_MAP`에서 Cursor 규칙, Claude/Gemini 최상위 문서, Gemini 구성 분기를 제거한다. 공통 `scripts/omc_*.py`, AGENTS/CODEX 문서, 관리 대상 `.agents/skills` Markdown, `.codex/commands` 매핑과 공개 함수·CLI 계약은 유지한다. 제품 소스와 기록을 읽거나 쓰는 실제 동기화 대신 복사된 CLI와 합성 프로젝트로 검증한다.

**Tech Stack:** Python 표준 라이브러리, pytest, subprocess.

**Spec:** `docs/superpowers/specs/2026-11-16-codex-only-design.md` 및 2026-11-17 사용자 요청: “남은 omc_sync_ssot.py의 비Codex 처리 경로도 격리 공간에서 정리하고 회귀 테스트”. 이 계획은 종전 범위 밖이던 수동 동기화 도구의 IDE 분기만 추가로 다룬다.

## Global Constraints

- “Codex용 `.agents/skills`, 공통 `scripts`, `prompts`, 호출 가능한 공통 훅 스크립트는 보존한다.”
- “기존 미커밋 변경, 기록 삭제 상태, 백업, 실패 원문, 테스트 fixture의 과거 계약은 보존한다.”
- “제품 코드·Git 원장·기존 정리 백업은 변경하지 않는다.”
- “공통 엔진의 외부 모델 provider 지원과 과거 연구·벤치마크 자료는 이번 제거 범위가 아니다.”
- “원본 반영·설치·훅 활성화·실제 복구·커밋·푸시는 하지 마.”
- 현재 `/Users/noseunglae/.codex/worktrees/f9d2/omc_kit` 안에서만 구현한다. 계획 단계에서는 아래 런타임·검사 코드를 수정하지 않는다.
- 실제 workspace의 `omc_sync_ssot.py` 기본 쓰기 명령을 실행하지 않는다. 합성 프로젝트의 파일 복사는 설치·승인·완료 처리가 아니다.
- `ROOT`, `KIT=ROOT/"omc_kit"`와 live→kit 복사 방향은 유지한다. external source 등록 해석·목적지 재설계·receipt 발급·자동 동기화는 추가하지 않는다. 레거시 목적지를 실제 운영에 사용해도 된다는 승인이 아니다.
- `.codex/hooks.json`, `.omc`, 백업, 다른 IDE의 기존 파일은 수정·삭제하지 않는다. 관리 대상 확장이나 원문 재생성은 하지 않는다.

## Review Focus

1. 비지원 구성이 실제로 모두 존재할 때도 dry-run/check/sync가 이를 열거·복사·삭제하지 않음 — 테스트 A.
2. `omc_claude_*.py` 같은 공통 provider 파일은 이름 때문에 제외하지 않음; Codex 중첩 문서·pr-create도 기존 매핑 유지 — 테스트 B.
3. 타사 스킬·임의 최상위 Markdown을 OMC 소유 파일로 확대하지 않음 — 테스트 C.
4. 차이 없음·변경·대상 누락에서 모드별 종료 코드가 기존 계약과 같고 check/dry-run은 쓰지 않음 — 테스트 D.
5. 비지원 디렉터리가 외부 파일을 향한 symlink여도 외부 원문·기록·훅·백업을 건드리지 않음 — 테스트 E.

---

## File Structure

- Modify: `scripts/omc_sync_ssot.py:1–75` — 설명과 `SYNC_MAP`의 비Codex 구성 분기만 제거. 공통 동기화 엔진은 유지.
- Modify/Test: `scripts/test_omc_sync_ssot.py` — 기존3검사 유지, 합성 CLI와 모드·보존 회귀 검사 추가.
- 다른 엔진·스킬 본문·설치기·기존 문서/기록은 변경하지 않는다. 도구 설명에 유지되는 레거시 목적지와 수동 사용 범위를 명시한다.

### Task 1: 비Codex 수동 동기화 분기 제거와 보존 회귀

**Files:** 위 두 파일만 구현 변경 대상. 이 계획 파일은 설계 산출물이다.

**Interfaces:**
- Preserve: `SYNC_MAP: list[tuple[str, str]]`, `_is_managed_agent_skill(relative_path: Path) -> bool`, `_pairs(root: Path, kit: Path) -> list[tuple[Path, Path]]`.
- Preserve: `check(root: Path = ROOT, kit: Path = KIT) -> list[tuple[Path, Path]]`, `sync(root: Path = ROOT, kit: Path = KIT) -> int`, `main() -> int`.
- Preserve modes: default copies changed/missing supported pairs and exits0; `--check` exits1 iff supported differences exist; `--dry-run` exits0 and lists supported differences without copying.
- Test fixture: copy the actual candidate script to `tmp_path/project/scripts/omc_sync_ssot.py`; CLI derives its own `ROOT` and static map there. Set synthetic kit to `project/omc_kit`. Seed paths before process import. Do not monkeypatch SYNC_MAP to hide a retained branch, and do not add a discovery refactor solely for tests.

- [ ] **Step 1: Preserve baseline and write the failing tests**

  Save initial hashes/file sets of source, skills, `.omc` and prior evidence outside the repository. Preserve the two before files. Add tests below; existing3 tests must remain.

  | ID / pytest name | Concrete input and assertions |
  |---|---|
  | A `test_non_codex_surfaces_are_never_sync_targets` | Parametrize `default`, `--check`, `--dry-run`. Seed `.cursor/rules/omc-local.mdc`, `bm-local.mdc`, `.gemini/settings.json`, `.gemini/commands/local.md`, `.claude/commands/local.md`, `.agent/skills/omc-local/SKILL.md`, `CLAUDE.md`, `GEMINI.md`, with differing existing destinations. First make all supported pairs equal. Assert exit0, no unsupported paths in reported pairs, no file changes anywhere. Snapshot source and destination trees before/after. |
  | B `test_supported_codex_and_common_pairs_remain` | Seed `scripts/omc_engine.py`, `scripts/omc_claude_provider.py`, AGENTS.md, CODEX.md, `.agents/skills/omc-example/SKILL.md`, nested `references/workflow.md`, `pr-create/SKILL.md`, SKILL_CHECKLIST.md, SKILL_TEMPLATE.md, `.codex/commands/omc-commands.md`. Assert all expected pairs are present exactly once. In default mode, each missing/different destination equals source bytes afterward. Do not use provider name filtering. |
  | C `test_project_owned_skills_are_not_adopted` | Seed `.agents/skills/project-local/SKILL.md` and `.agents/skills/NOTES.md`, plus existing destination sentinels. Assert neither enters pairs/output and all original bytes remain. Existing `_is_managed_agent_skill` assertions retain their values. |
  | D `test_mode_exit_codes_and_write_boundaries` | Parametrize modes × `equal/changed/missing` supported destination. Assert check exit is `0/1/1`, dry-run is always0, default is always0. Read-only modes leave complete file sets/bytes unchanged. Default changes only enumerated supported destinations; a second default run is no-op, with hashes and mtimes unchanged. Source files, `.omc` sentinels, hooks and backup sentinels remain identical in every case. |
  | E `test_foreign_symlink_trees_and_evidence_are_untouched` | Point synthetic `.gemini` and `.cursor/rules` at separate temp-owned external trees. Keep unrelated destination backup/record/hook sentinels. Assert all modes leave external bytes, link targets and sentinel hashes unchanged, no unsupported destination is added. No symlink into real user files. |

  Literal assertion contracts for A–E (fixture snapshots cover file sets and bytes; mtime/link snapshots are additional):

  ```python
  # A: unsupported-only differences do not count as sync work.
  assert result.returncode == 0
  assert after == before
  assert not any(prefix in result.stdout for prefix in
                 (".cursor/", ".gemini/", ".claude/", ".agent/", "CLAUDE.md", "GEMINI.md"))
  # B: compare to the explicit supported input→destination pairs listed above.
  assert len(pairs) == len(set(pairs))
  assert expected_supported_pairs <= set(pairs)
  assert all(destination.read_bytes() == source.read_bytes() for source, destination in pairs)
  # C: neither third-party skill nor arbitrary root file is adopted.
  assert ".agents/skills/project-local/SKILL.md" not in reported_sources
  assert ".agents/skills/NOTES.md" not in reported_sources
  # D: exit semantics and repeat-run metadata are observable, not inferred.
  assert result.returncode == (int(case != "equal") if mode == "--check" else 0)
  assert second_after == second_before  # includes mtimes for no-op second sync
  # E: snapshots include link targets and the temp-owned external trees.
  assert external_after == external_before
  assert links_after == links_before
  assert protected_after == protected_before
  ```

- [ ] **Step 2: Run RED and verify the cause**

  Run: `python3 -m pytest -q scripts/test_omc_sync_ssot.py`.
  Expected: A/E fail because the existing CLI still enumerates/copies seeded `.cursor`/`.gemini`/CLAUDE/GEMINI configuration. Preserve stdout/stderr and exit. B/C/D may already pass; do not alter working engine behavior to manufacture a RED.

- [ ] **Step 3: Make the minimal production edit**

  Remove only Cursor `omc-*.mdc`/`bm-*.mdc` comprehensions, CLAUDE/GEMINI top-level mappings and `.gemini` recursive mapping from `SYNC_MAP`. Keep the existing Codex/common mapping rules, public interfaces, destination defaults, comparison and copy behavior. Update the module description to identify Codex/common scope and unchanged legacy `ROOT/omc_kit` destination. No new CLI options, dependencies, receipt handling, deletion or installation.

- [ ] **Step 4: Run GREEN and meaningful neighboring regression**

  Run: `python3 -m pytest -q scripts/test_omc_sync_ssot.py scripts/test_install_whitelist.py scripts/test_omc_source_hash.py`.
  Expected: all pass, no new skip/xfail. CLI remains in deployment whitelist and source hashing includes the edited tool. Validate `git diff --check` and compare the saved source/record/evidence hash sets: only the two implementation/test files and this new plan may differ. Common engine files and actual skill contents must be unchanged. Keep both failed and passed logs.

- [ ] **Step 5: Record the result without committing**

  Save the final diff, commands/exit codes, preservation comparison and explicit limitations outside the repository. Do not repeat the whole40-minute suite merely for the two-file map removal if scoped checks pass; broaden only for a failure, unexpected interface effect or further user request. Do not claim a new whole-suite result. Leave the existing detached worktree and all prior changes intact. No git add/commit/push, original reflection, installation or real sync.

## Self-Review

- Coverage: nonCodex exclusion→A/E; Codex/common preservation→B; ownership→C; CLI semantics/read-only/idempotence→D; records/backups/no prohibited operations→D/E and Step4/5.
- Types and signatures match the current module; no new production API or undefined inter-task dependency.
- One independently testable task; all five Review Focus conditions have named tests.
- Deliberate boundary: this removes IDE-specific mappings, not common provider modules or the legacy destination model. External-source migration and transactional sync hardening are separate work.
- Execution choice: recommend Native/inline because this is one mapping change with one regression module, and per-task delegation adds no useful boundary.

## Execution Handoff

Plan review precedes implementation under writing-plans. The existing earlier inline choice was for the preceding R1–R3 plan; do not silently treat it as selection for this new plan. Confirm that this narrow plan captures the request and choose Native/inline or Subagent-driven before implementation. The no-commit/no-install constraints remain in either method.
