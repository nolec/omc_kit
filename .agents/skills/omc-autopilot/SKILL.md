---
skill_name: omc-autopilot
description: "지시문 하나로 plan→task→review→PR 전체 파이프라인을 자동 실행. 트리거: 자동으로 해줘, 자동화, autopilot, 잘 때 돌려줘, pipeline 실행. 반드시 지시문과 브랜치명을 확정한 뒤 실행한다."
---

# OMC Autopilot

지시문 하나로 전체 개발 파이프라인(plan → task → review → PR)을 자동 실행합니다.

> **이 스킬을 쓰면 안 되는 상황**:
> - 지시문이 모호한 경우 → `/omc-office-hours` 또는 `/omc-brainstorm` 먼저
> - 치명/중대 이슈가 열려있는 경우 → 해결 후 실행

---

## Step 0: 컨텍스트 수집

> **AI는 아래 커맨드를 직접 실행하고 결과를 확인한 후 다음 단계로 진입한다. 건너뛰지 않는다.**

```bash
git branch --show-current 2>/dev/null
git status --porcelain 2>/dev/null | head -10
git log --oneline -3 2>/dev/null
python3 scripts/omc.py state status --target . 2>/dev/null | head -5
```

수집 결과 연결:
- 현재 브랜치 → 새 브랜치 이름 제안 시 참고
- uncommitted 변경 → Step 1에서 dirty 상태 여부 판단
- 세션 상태 → 기존 작업 중인 세션 있으면 사용자에게 알림

---

## Step 1: 지시문 확정 체크

수집 결과를 바탕으로 아래 항목을 확인한다.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AUTOPILOT 실행 전 확인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지시문   : [사용자가 말한 내용 그대로 또는 보완]
브랜치   : [제안: feat/xxx 또는 fix/xxx]
모드     : [LITE / FULL] — 근거: [브랜치 prefix / 지시문 길이 / 명시]
dirty    : [clean / dirty N개] — 처리: [clean이면 없음 / dirty면 아래 안내]
실행방법 : 백그라운드 실행 권장 (작업 시간: ~수 분~수십 분)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**모드 자동 결정 기준:**
- fix/, hotfix/, chore/, docs/ 브랜치 prefix → **LITE** (plan/critique 스킵)
- 지시문 50자 이하 → **LITE**
- feat/ + 지시문 50자 초과 → **FULL** (plan→critique→task→review)
- `--mode lite/full` 로 언제든 override 가능

**uncommitted 변경이 있으면 반드시 아래 중 하나를 선택하도록 안내:**
```
❌ uncommitted 변경이 감지됐습니다. (dry-run 아닌 실제 실행 시 차단됩니다)

선택:
  A) git commit -am "wip" 또는 git stash 후 실행 (권장)
  B) --allow-dirty 플래그 추가하여 강제 실행
```

---

## Step 2: 실행 명령 출력

확인 완료 후 아래 명령을 출력하고 **백그라운드 실행**을 안내한다.

### clean 상태 (권장)

```bash
# 백그라운드 실행
nohup python3 scripts/omc_autopilot.py pipeline \
  --instruction "[지시문]" \
  --branch "[브랜치명]" \
  --mode [auto|lite|full] \
  > .omc/pipeline.log 2>&1 &

echo "PID: $!  |  로그: .omc/pipeline.log"
```

### dirty 상태 + --allow-dirty

```bash
nohup python3 scripts/omc_autopilot.py pipeline \
  --instruction "[지시문]" \
  --branch "[브랜치명]" \
  --allow-dirty \
  > .omc/pipeline.log 2>&1 &

echo "PID: $!  |  로그: .omc/pipeline.log"
```

### 흐름만 먼저 확인 (dry-run — dirty 상태에서도 가능)

```bash
python3 scripts/omc_autopilot.py pipeline \
  --instruction "[지시문]" \
  --branch "[브랜치명]" \
  --dry-run
```

### 기타 옵션

```bash
# 지시문 10자 미만 강제 실행
python3 scripts/omc_autopilot.py pipeline \
  --instruction "[짧은 지시문]" \
  --branch "[브랜치명]" \
  --force

# dirty + 짧은 지시문 동시 강제
python3 scripts/omc_autopilot.py pipeline \
  --instruction "[짧은 지시문]" \
  --branch "[브랜치명]" \
  --force --allow-dirty
```

---



### 실패한 파이프라인 재개 (--resume)

```bash
python3 scripts/omc_autopilot.py pipeline \\
  --instruction "[이전과 동일한 지시문]" \\
  --branch "[이전 브랜치명]" \\
  --resume
```

| 상태 | 동작 |
|---|---|
| result 파일 없음 | exit 1 + 안내 메시지 |
| 이미 completed | exit 0 + PR URL 출력 |
| 이전 단계 일부 completed | 완료 단계 건너뜀, 실패 단계부터 재실행 |
## Step 3: 실행 후 결과 확인

```bash
python3 -c "
import json
d = json.load(open('.omc/pipeline_run_result.json'))
print('상태:', d['status'], '| 모드:', d['mode'])
print('PR:', d.get('pr_url') or '없음')
"
```

| 결과 | 다음 단계 |
|---|---|
| status: completed | PR 확인 후 머지 |
| status: failed | `cat .omc/pipeline.log` → `/omc-investigate` |
| PR 없음 | `gh pr create` 수동 실행 |
| ❌ dirty 차단 | `git stash` 또는 `--allow-dirty` 추가 |
