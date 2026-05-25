# OMC Data — sixshop3-storefront-fe

sixshop3-storefront-fe 프로젝트에서 수집된 OMC 세션 기록과 교훈 모음.

## 구조

```
data/
├── lessons/    # .omc/lessons/ — BM25 기반 자동 주입 교훈 (14개)
└── sessions/   # .omc/state/sessions/ — 세션 레코드 (44개)
```

## 교훈 목록 (lessons/)

| 날짜 | 제목 | 태그 |
|------|------|------|
| 2026-05-15 | async/await 누락 + 미선언 변수 | async-await, syntax-error |
| 2026-05-15 | bm.call URL 조합 버그 | api, bm-call, url |
| 2026-05-15 | bm.onDestroy 체이닝 누적 | memory-leak, onDestroy |
| 2026-05-15 | Motion.animate keyframe 1개 미작동 | animation, keyframe |
| 2026-05-15 | MutationObserver this 미저장 | memory-leak, dispose |
| 2026-05-15 | OMC 인프라 미동기화 | omc-infra, ssot |
| 2026-05-15 | 타이머 정리 누락 | timer, cleanup |
| 2026-05-18 | session-start CONTRACT 초기화 | session, contract, tdd |
| 2026-05-18 | edit_file에 CONTRACT 강제 | omc, tdd, pipeline-guard |
| 2026-05-18 | 수정 파일(M)도 TDD 체크 | omc, tdd, modified-files |
| 2026-05-18 | allow --reason 필수 | allow, reason, audit |
| 2026-05-18 | SSOT 자동화 + 교훈 콘솔 출력 | ssot, sync, lesson |
| 2026-05-18 | 구현 요청 즉시 파이프라인 진입 실패 | tdd, pipeline, hooks |
| 2026-05-20 | install.py 수동 등록 누락 | omc-infra, install |

## 활용 방법

다른 프로젝트에서 이 교훈을 재활용하려면:

```bash
# 교훈을 현재 프로젝트 .omc/lessons/로 복사
cp /path/to/omc_kit/data/lessons/*.md .omc/lessons/

# BM25 검색
python3 scripts/omc_lesson.py search "키워드"
```
