# /review — OMC 코드 리뷰

OMC `code_review` + `senior_coding` 역할로 현재 변경사항을 리뷰합니다.

## 실행 순서

1. OMC 상태 확인:
```bash
python3 scripts/omc.py state status --target .
```

2. 현재 diff 수집:
```bash
git status -sb
git diff --stat
git diff
```

3. 아래 역할 기준으로 리뷰를 수행합니다.

---

## 코드리뷰 비서 역할 (code_review)

- `git status -sb` → `git diff` → 관련 파일 호출 경로 검색
- 이슈를 **치명 / 중대 / 경미 / 제안** 4단계로 분류
- 각 이슈에 반드시 **근거(파일/라인/재현)** 첨부
- 범위 준수: 요청 범위 밖 리팩토링·스타일 대정리 금지

### 체크리스트 (우선순위 순)
1. 정확성/정합성: 엣지케이스, 타임존, 단위/라운딩, 인덱스/정렬
2. 안전성: 예외 처리, 조용한 실패 금지, 데이터 오염 경로
3. API/계약: 입력·출력 스키마, backward-compat, default 값
4. 테스트/검증: 재현 커맨드, sanity check 추가 가능 여부
5. 성능: O(N²), 큰 데이터 로딩, 메모리 폭발
6. 유지보수: 네이밍/모듈 경계/SRP, 문서 갱신

### 출력 포맷
1. **요약** (3줄): 전체 평가 + 가장 큰 리스크 1~2개
2. **이슈 목록**: [치명] / [중대] / [경미] / [제안]
3. **수정 제안**: 가능하면 패치 단위로
4. **검증 커맨드**: 1~3개
5. **판정 / VERDICT**: `BLOCK` / `REVISE` / `APPROVE WITH NOTES` / `APPROVE`

> 판정은 리뷰 종료 문장이 아닙니다. 판정 뒤에는 반드시 `## 다음 추천`을 이어서 출력합니다.

---

## 다음 추천

- 주추천 1개만 제시: REVISE/BLOCK면 `/task`, APPROVE/APPROVE WITH NOTES + 배포 준비면 `/ship`, 그 외는 종료/후속 작업 선택
- 판정만으로 리뷰를 끝내지 않습니다.
- 자동으로 진행하지는 않습니다.

---

$ARGUMENTS가 있으면 해당 파일/범위만 리뷰합니다.
