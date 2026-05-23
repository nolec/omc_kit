---
skill_name: pr-create
description: "GitHub PR 생성 — 프로젝트 표준 템플릿·라벨·Assignee 자동 적용. 트리거: PR 올려줘, PR 생성, pull request 만들어줘, PR 열어줘."
---

# PR 생성 스킬

`.github/PULL_REQUEST_TEMPLATE.md`를 기반으로 라벨·Assignee를 자동 설정해 PR을 생성합니다.

> **이 스킬을 쓰면 안 되는 상황**:
> - `$omc-ship` 체크리스트 미완료 상태 → ship 먼저

---

## Step 1 — 사전 확인

> **AI는 아래 커맨드를 직접 실행하고 결과를 확인한 후 Step 2로 진입한다. 건너뛰지 않는다.**

```bash
# base 브랜치 자동 감지
BASE_BRANCH=$(git remote show origin 2>/dev/null | grep "HEAD branch" | awk '{print $NF}')
BASE_BRANCH=${BASE_BRANCH:-main}

# 커밋·변경 내용 확인
git log ${BASE_BRANCH}..HEAD --oneline 2>/dev/null
git diff ${BASE_BRANCH}..HEAD --stat 2>/dev/null

# push 여부 확인 (원격에 없으면 PR 생성 불가)
git status -sb 2>/dev/null

# 중복 PR 감지
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null)
gh pr list --head "${CURRENT_BRANCH}" 2>/dev/null

# gh 인증 확인
gh auth status 2>/dev/null

# PR 템플릿 확인
cat .github/PULL_REQUEST_TEMPLATE.md 2>/dev/null
```

수집 결과 연결:
- `git log` 결과 → **Step 3 ✏️ 작업 사항** 섹션 내용 작성에 반영
- `git diff --stat` 결과 → **Step 3 ✏️ 작업 사항** 변경 파일 요약에 반영
- `PULL_REQUEST_TEMPLATE.md` 내용 → **Step 4 본문**의 섹션 구조로 사용 (하드코딩 금지)

### 사전 확인 게이트

아래 조건 중 하나라도 해당하면 멈추고 사용자에게 안내한다:

| 조건 | 처리 |
|---|---|
| 원격에 push 안 됨 (`git status`에 `ahead` 표시 + 원격 없음) | `git push -u origin HEAD` 실행 후 재시도 |
| 이미 PR 존재 (`gh pr list` 결과 있음) | PR URL을 사용자에게 전달하고 중단 |
| `gh auth status` 실패 | `gh auth login --web` 안내 후 중단 |
| `gh`가 설치 안 됨 | `brew install gh` 안내 후 중단 |

---

## Step 2 — 라벨 결정

> **라벨이 없을 때**: `gh label list` 로 확인 후 없으면
> `gh label create "[라벨명]" --color "#색상코드"` 로 먼저 생성합니다.

### 앱 라벨 (변경 경로 기준)

| 경로 | 라벨 |
|---|---|
| `apps/editor/`, `libs/block-maker/`, `libs/editor-lib/` | `[앱] 에디터` |
| `apps/storefront/`, `libs/storefront-*/` | `[앱] 웹사이트` |
| `apps/store-manager/`, `libs/sellerhub-*/` (sellerhub-store 제외) | `[앱] 스토어매니저` |
| `libs/shared/`, `libs/sellerhub-store/` | `[공통] 프론트엔드` |
| `theme-*/`, `libs/theme-*/` | `[테마] 공통` |

※ 변경 경로가 여러 앱에 걸치면 해당하는 앱 라벨을 모두 추가한다.

### 유형 라벨

| 작업 내용 | 라벨 |
|---|---|
| 새 파일·컴포넌트·기능 추가 | `[유형] 신규 기능` |
| 기존 기능 수정·개선 | `[유형] 기능 개선` |
| 버그 수정 | `[유형] 버그` |
| 기능 변경 없는 코드 구조 변경 | `[유형] 리팩토링` |
| 긴급 수정 | `[유형] 핫픽스` |

### 상태 라벨
- 작업 완료 + 리뷰 요청 → `[상태] 리뷰해주세요`
- 아직 작업 중 → `[상태] 작업 중`

---

## Step 3 — 본문 작성

> `.github/PULL_REQUEST_TEMPLATE.md` 의 섹션 구조를 **그대로** 유지한다.
> Step 1에서 수집한 `git log` + `git diff --stat` 결과를 `✏️ 작업 사항` 에 반영한다.

본문 예시 (섹션 구조는 템플릿 기준):

```
## 🔖 관련 문서
(Jira, Notion, 이슈 링크 등 — 없으면 생략)

## ✏️ 작업 사항
(git log 커밋 요약 + 변경된 주요 파일 설명)

## 📷 스크린샷
(UI 변경이 있으면 첨부, 없으면 생략)

## 💬 참고 자료
(관련 PR, 문서, 라이브러리 링크 등 — 없으면 생략)

## 🎸 기타
_후속작업, 관련된 epic, 해당 PR 전 먼저 리뷰가 되어야하는 PR, 도움이 필요한 부분, 고민되는 부분, 설명 등_
```

---

## Step 4 — PR 생성 명령

> **아래 커맨드를 실행하기 전에 Step 3에서 작성한 본문 내용으로 BODY를 채운다.**

```bash
gh pr create \
  --title "제목" \
  --base "${BASE_BRANCH}" \
  --body "$(cat <<'BODY'
## 🔖 관련 문서

## ✏️ 작업 사항

(여기에 Step 3에서 작성한 내용)

## 📷 스크린샷

## 💬 참고 자료

## 🎸 기타

_후속작업, 관련된 epic, 해당 PR 전 먼저 리뷰가 되어야하는 PR, 도움이 필요한 부분, 고민되는 부분, 설명 등_
BODY
)" \
  --assignee "$(gh api user --jq '.login')" \
  --label "[앱] XXX" \
  --label "[상태] 리뷰해주세요" \
  --label "[유형] XXX"
```

Draft PR 생성 시: `--draft` 플래그 추가

---

☐ Step 4 PR 생성 완료

## Step 5 — 완료 보고

PR 생성 후 URL을 확인하고 사용자에게 전달한다.

```bash
gh pr view --web 2>/dev/null || gh pr view 2>/dev/null | head -5
```


> 답하기 어려운 항목은 `N/A — [이유]` 형식으로 기재한다. 빈칸으로 두지 않는다.

## 이후 액션

| 결과 | 다음 단계 |
|---|---|
| PR 생성 완료 | PR URL을 사용자에게 전달 |
| 중복 PR 발견 | 기존 PR URL 전달 후 중단 |
| gh 미인증 | `gh auth login --web` 안내 |
