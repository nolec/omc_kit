# /autopilot — 전체 파이프라인 자동 실행

지시문 하나로 plan → task → review → PR 전체를 자동 실행합니다.

## 실행 전 확인

```bash
git branch --show-current
git status --porcelain | head -5
```

## 모드 자동 결정 기준

| 조건 | 모드 | 내용 |
|---|---|---|
| fix/, hotfix/, chore/, docs/ 브랜치 | LITE | task + review만 실행 |
| 지시문 50자 이하 | LITE | task + review만 실행 |
| feat/ + 지시문 50자 초과 | FULL | plan → critique → task → review |

## 흐름 먼저 확인 (dry-run, 즉시 완료)

```bash
python3 scripts/omc_autopilot.py pipeline \
  --instruction "$ARGUMENTS" \
  --branch "feat/autopilot-$(date +%s)" \
  --dry-run
```

## 실제 실행 (백그라운드 권장 — 수십 분 소요)

```bash
# 브랜치명은 작업에 맞게 변경
nohup python3 scripts/omc_autopilot.py pipeline \
  --instruction "$ARGUMENTS" \
  --branch "feat/autopilot-$(date +%s)" \
  --mode auto \
  --allow-dirty \
  > .omc/pipeline.log 2>&1 &

echo "PID: $!  |  로그: .omc/pipeline.log"
```

## 진행 상황 및 결과 확인

```bash
# 실시간 로그
tail -f .omc/pipeline.log

# 최종 결과
python3 -c "
import json
d = json.load(open('.omc/pipeline_run_result.json'))
print('상태:', d['status'], '| 모드:', d['mode'])
print('PR:', d.get('pr_url') or '없음')
"
```

## 옵션

```bash
# 모드 명시
python3 scripts/omc_autopilot.py pipeline \
  --instruction "$ARGUMENTS" --branch "fix/..." --mode lite

# 지시문 10자 미만 강제 실행
python3 scripts/omc_autopilot.py pipeline \
  --instruction "$ARGUMENTS" --branch "fix/..." --force
```


# 실패한 파이프라인 재개
python3 scripts/omc_autopilot.py pipeline \
  --instruction "[이전과 동일한 지시문]" \
  --branch "[이전 브랜치명]" \
  --resume
## 고급 사용 — task 파일 기반 방식

복잡한 멀티 스텝 자동화가 필요하면 task 파일을 직접 구성할 수 있습니다.

```bash
python3 scripts/omc_autopilot.py new --id feat-login --title "로그인 기능 구현"
# .omc/tasks/feat-login.json 편집 후
python3 scripts/omc.py autopilot --task-file .omc/tasks/feat-login.json --dry-run
```
