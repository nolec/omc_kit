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

- 주추천 1개만 제시: REVISE/BLOCK면 `/task` (수동 제안; 기계 라우팅의 BLOCK은 null,true 유지), APPROVE/APPROVE WITH NOTES + 배포 준비면 `/ship`, 그 외는 종료/후속 작업 선택
- 판정만으로 리뷰를 끝내지 않습니다.
- 자동으로 진행하지는 않습니다.

---

$ARGUMENTS가 있으면 해당 파일/범위만 리뷰합니다.

## 완료 보고와 검증 경계

완료 판정 계약: CONTRACT에서 필수 검증의 대상·방법·통과 조건을 확정한다. 통제된 컴포넌트 검증과 실제 서비스 검증은 별개이며 필수 대상 대신 다른 화면·버전의 증거를 쓰면 증거 불일치다. 필수 검증 통과일 때만 전체 완료를 보고한다. 미실행·확인 불가는 구현됨·검증 미완료, 실패는 수정 필요, 증거 불일치는 재검증 필요로 보고한다. 브라우저 불필요가 계약상 명확하면 브라우저 미실행만으로 보류하지 않는다. 기존 실패는 동일 기준의 비교 근거가 있을 때만 신규 실패와 구분한다. 코드 승인과 전체 완료를 구분하며 미충족 검증·사유·다음 행동 한 가지를 명시한다. 이 안내는 물리적 차단을 보장하지 않는다. review는 코드 승인 여부와 필수 검증 충족 여부를 각각 보고한다. 코드에 이슈가 없어도 전체 완료를 추정하지 않으며 필수 검증이 남으면 자동 ship 추천을 하지 않는다. 검증 미실행만으로 코드 결함을 만들어내거나 코드 승인 판정을 강제로 낮추지 않는다. 기존 기계 계약: APPROVE/APPROVE WITH NOTES=>approved,null; REVISE=>blocked,omc-task,false; BLOCK=>blocked,null,true. 코드 승인 보고에 outcome=blocked를 함께 쓰지 않는다. risk는 low/medium/high이고 user_selection_needed는 boolean으로 출력한다. 수동 제안과 기계 라우팅은 별개다. 본문의 수정 요청으로 /task를 제안해도 BLOCK의 next_skill은 null, user_selection_needed는 true다. REVISE일 때만 next_skill=omc-task,false를 쓴다. 아래 다음 추천은 수동 제안이며 기계 라우팅 표를 덮어쓰지 않는다.

기계 기록의 단계: stage=review. 아래 별도 기록 규칙이 이전 출력 예시보다 우선한다.

## Machine output contract
별도 기록 모드 `detached_v1`을 명시적으로 사용한다. `omc-output/v1`의 stage/outcome/risk/next_skill/user_selection_needed/reason_code·VERDICT·기존 라우팅 의미는 바꾸지 않는다. `<!-- OMC_OUTPUT: {JSON} -->`과 `VERDICT`는 내부 판정 원문에만 두고 사용자 최종 답변에 붙이지 않는다. 한글 판정·이유·검증·다음 행동만 표시한다.
먼저 `python3 scripts/omc_output_contract.py capabilities`와 `python3 scripts/omc_workflow_validation.py capabilities`로 발행자·소비자의 지원을 각각 확인한다. 실행 전에 모드·현재 작업/세션·단계·보고 ID·마감과 명시 기록 경로를 고정한다. 문맥이 없으면 `prepare --target . --stage <단계> --report-id <새ID> --budget-sec <승인된남은초>`로 현재 상태에서 새 문맥을 만들고 반환된 context/path·SHA를 호출자 실행 메타데이터에 보존한다. 숫자 예산을 임의 연장하거나 이전 보고 ID를 재사용하지 않는다.
일반 plan/review/critique/investigate/ship 선언은 내부 원문을 작업의 .omc 안에 저장한 뒤 `python3 scripts/omc_output_contract.py publish --target . --output-context <문맥경로> --output-context-sha256 <해시> --source-file <내부원문> --source-sha256 <해시>`로 발행한다. 이는 모델 선언이며 해시 일치가 검증 통과나 사용자 승인 진위를 인증하지 않는다.
실제 task 완료는 기존 `complete-task --verification-file <내부검증>`에 `--output-mode detached_v1 --output-context <문맥경로> --output-context-sha256 <해시>`를 추가한다. 이관은 승인된 complete-transfer 경로를 유지한다. 일반 publish로 완료 가드의 발행 주체를 지정할 수 없다.
사용자 최종 답변은 발행 CLI의 한글 stdout과 같은 바이트로 제공한다. 경로/해시는 사용자 답변 꼬리표 대신 호출 인자와 실행 메타데이터로 전달한다. 수집기는 명시 manifest 경로/해시·현재 상태·native 호출/반환·실제 최종 원문을 독립 대조한다. CLI 표시 검사·수집기 fixture·실제 모델의 자유 응답 준수는 별도 증거다.
업무 차단 종료 코드1도 정상 판정으로 기록한다. 전달 실패 코드2는 업무 실패와 다르다. 저장·봉인·출력 실패 시 한글 오류만 알리고 완료/승인·자동 후속·구형 fallback을 금지한다. 실패 기록도 저장할 수 없으면 stderr·종료 원문만 보존한다. 봉인 후 표시 실패를 이유로 기존 manifest나 verdict를 수정하지 않는다.
구형 legacy_inline으로 시작한 입력만 기존 parser로 읽는다. 신형 기록 누락·변조·다른 세션·모드 혼합·표시 불일치는 보류하며 구형으로 우회하지 않는다. 구형 도구에서 신형 안내를 실행할 수 없으면 호출 전에 한글 지원 불가를 알린다. 이 안내는 플랫폼 화면 숨김이나 모델 준수를 물리적으로 보장하지 않는다.
