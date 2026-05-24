---
skill_name: omc-review
description: "코드 변경사항·diff·PR 리뷰. 트리거: 리뷰해줘, 코드 확인, 뭐가 문제야, 코드 봐줘, 이거 괜찮아. 치명/중대/경미/제안 4단계로 분류, 파일+라인 근거 첨부. 요청 범위 외 리뷰 금지."
---

# OMC 코드 리뷰

이슈를 **치명 / 중대 / 경미 / 제안** 4단계로 분류하고 근거를 첨부합니다.

## 실행

```bash
git status -sb
git diff --cached
git diff
python3 scripts/omc.py state status --target .
```

## diff 소스 규칙 (필수)
- 먼저 `git diff --cached`를 기준으로 리뷰합니다.
- 이어서 `git diff`(unstaged)까지 확인해 누락 여부를 점검합니다.
- 출력 첫 줄에 반드시 범위를 명시합니다: `범위: staged only` 또는 `범위: staged + unstaged`.

## 체크리스트
1. 정확성/정합성: 엣지케이스, 타임존, 인덱스/정렬
2. 안전성: 예외 처리, 조용한 실패 금지
3. API/계약: 입력·출력 스키마, backward-compat
4. 테스트/검증
5. 성능: O(N²), 메모리 폭발
6. 유지보수: 네이밍/모듈 경계/SRP

## 출력 포맷
1. **요약** (3줄)
2. **이슈 목록**: [치명] / [중대] / [경미] / [제안]
3. **수정 제안**: 패치 단위로
4. **검증 커맨드**
