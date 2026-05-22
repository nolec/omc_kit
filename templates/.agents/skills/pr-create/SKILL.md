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

```bash
# base 브랜치 자동 감지
BASE_BRANCH=$(git remote show origin 2>/dev/null | grep "HEAD branch" | awk '{print $NF}')
BASE_BRANCH=${BASE_BRANCH:-main}

git log ${BASE_BRANCH}..HEAD --oneline
git diff ${BASE_BRANCH}..HEAD --stat
gh auth status
```

gh가 설치되어 있지 않으면 `brew install gh` 후 `gh auth login --web` 실행을 안내합니다.

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

`.github/PULL_REQUEST_TEMPLATE.md`의 섹션을 **반드시** 유지합니다.

---

## Step 4 — PR 생성 명령

```bash
gh pr create \
  --title "제목" \
  --base "${BASE_BRANCH}" \
  --body "$(cat <<'BODY'
## 🔖 관련 문서

## ✏️ 작업 사항

(내용)

## 📷 스크린샷

## 💬 참고 자료

## 🎸 기타
BODY
)" \
  --assignee "$(gh api user --jq '.login')" \
  --label "[앱] XXX" \
  --label "[상태] 리뷰해주세요" \
  --label "[유형] XXX"
```

Draft PR 생성 시: `--draft` 플래그 추가

---

## Step 5 — 완료 보고

PR URL을 사용자에게 전달합니다.
