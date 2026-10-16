# 광고 비교 준비 검사와 엄격 수집

이 도구는 광고 benchmark의 다음 실행을 위한 선택형 진입점이다. 현재 사용처의 Claude 실행을 전역으로 차단하지 않는다. 기존 `omc_workflow_validation.py` 호출은 과거 의미를 유지하며, 이번 비교에서는 아래 준비 검사와 `--scope-report`를 함께 사용한다.

## 실행 전 동결

운영자가 별도 파일에 manifest를 작성하고 바이트 SHA-256을 실행 인자로 고정한다. 초기 Ad3파일·Home 검수 입력·요청·디자인·browser/helper/builder·공통 검증 기준을 `inputs`에 모두 포함한다. 아래는 구조 예시이며 해시·경로·포트는 다음 격리 후보에서 확인한 실제 값으로 채워야 한다.

```json
{
  "schema_version": "omc-workflow-preflight/v1",
  "inputs": {
    "task.md": "실제 SHA-256",
    "design-reference.md": "실제 SHA-256",
    "design-section.png": "실제 SHA-256",
    "libs/sellerhub-store/src/lib/components/Ad/Ad.tsx": "실제 SHA-256",
    "libs/sellerhub-store/src/lib/components/Ad/Ad.styled.ts": "실제 SHA-256",
    "libs/sellerhub-store/src/lib/components/Ad/Ad.test.tsx": "실제 SHA-256",
    "libs/store-manager/components/src/lib/home/Home.tsx": "실제 SHA-256",
    "../browser.cjs": "실제 SHA-256",
    "../build-controlled.cjs": "실제 SHA-256",
    "../common-required-checks.json": "실제 SHA-256"
  },
  "required_checks_file": "../common-required-checks.json",
  "required_check_ids": ["tests", "lint", "browser-controlled", "browser-home"],
  "ignored_artifacts": [
    {"path":"apps/store-manager/public/omc-ad-validation/index.html", "source":".git/info/exclude", "pattern":"/apps/store-manager/public/omc-ad-validation/"},
    {"path":"apps/store-manager/public/omc-ad-validation/bundle.js", "source":".git/info/exclude", "pattern":"/apps/store-manager/public/omc-ad-validation/"}
  ],
  "work_paths": ["libs/sellerhub-store/src/lib/components/Ad/"],
  "browser": {"control_url":"http://127.0.0.1:9423/command", "origin":"http://localhost:3023", "home_path":"/sixshoptest01", "min_images":2}
}
```

조정자가 모델 시작 전에 기존 로컬 제외 규칙을 격리 후보에 구성하고 통제 화면을 한 번 빌드한다. 일반 프로젝트 `.gitignore`를 추가해 누락을 숨기지 않는다. `git check-ignore`의 실제 일치 파일·패턴까지 대조하므로 다른 `.gitignore`가 같은 결과를 내도 실패한다. 준비 검사는 파일·해시·네 기준 목록과 로컬 브라우저 제어 서버를 확인한다. 정해진 읽기 전용 DOM 관찰로 실제 홈 URL, 로드된 카드 이미지2개 이상, 약4.8초 동안의 전환을 검사한다. 준비 검사 자체는 모델을 호출하지 않는다.

## 공개 실행 진입점

```bash
python3 scripts/omc_workflow_preflight.py --target <후보> \
  --manifest <동결 JSON> --manifest-sha256 <manifest SHA-256> \
  --report <저장소 밖 새 report.json>
```

준비가 안 됐으면 종료2·status=blocked·reason_codes를 남긴다. 정상은 종료0·status=ready이며 working 파일과 index 기준을 report에 보존한다. report는 write-once이고 저장소 밖 또는 `.omc/` 안에만 쓸 수 있다. 화면 준비 실패나 hash 불일치를 수정한 뒤에는 새 manifest 승인·새 report 경로를 사용한다. 기존 실행·기록은 덮어쓰지 않는다.

**실제 모델 호출이 별도로 승인된 뒤에만** 위 명령 끝에 `--launch <executor argv...>`를 추가한다. 준비 실패 시 후속 프로세스는 호출하지 않고, ready report를 기록한 경우에만 해당 argv를 shell 없이 실행한다. launch의 stdout은 자식 출력 그대로이며 준비 요약은 stderr로 분리한다. 자식 종료값을 반환하고 이를 완료·품질 판정으로 바꾸지 않는다. 직접 다른 runner를 호출하면 이 gate를 거치지 않으므로 다음 광고 재실행은 이 진입점을 사용한다.

## 범위 변경과 최종 보고

모델 요청에 다음 조건을 명시한다. 준비 문제 때문에 허용 범위 밖 설정을 고쳐야 하면 수정하지 말고 멈춰서 원인·필요한 변경·승인 대상을 설명한다. 범위 변경은 별도 승인과 새 준비 기록으로만 진행한다. 최종 첫 줄은 complete-task의 정확한 상태 문장이고 `다음 행동: ...`은 한 번만 쓴다. 도구 출력 뒤 구분선·별도 완료 제목·중복 요약으로 감싸지 않는다.

```bash
python3 scripts/omc_workflow_validation.py --target <같은 후보> \
  --events-file <native 원문> --session-id <OMC confirmed session ID> \
  --verification-file <검증 JSON> --expected-outcome <done|blocked> \
  --output-dir <새 수집 폴더> \
  --scope-report <실행 전 report.json> --scope-report-sha256 <report SHA-256>
```

`--session-id`는 Claude transcript UUID와 다르다. 엄격 모드는 report의 target·기준 ID·해시를 확인하고, Git-visible 파일과 index의 변경을 실행 전 기준에 대조한다. `.omc/` runtime은 허용하며 그 밖에는 work_paths의 정확한 파일 또는 `/`로 끝나는 디렉터리 범위만 허용한다. 기존 dirty 작업은 변하지 않았을 때 보존한다. Write/Edit의 성공 결과가 확인된 범위 밖 쓰기는 복원돼도 실패, 도구가 거부한 쓰기는 실제 변경으로 집계하지 않고, 결과 미확인은 unknown으로 보류한다. 범위 검사·서식 검사도 required에 포함하므로 전체 allow로 집계하지 않는다. 서식이나 원문을 고쳐 통과시키지 않는다.

## 검증 경계

해시·report·baseline·검증 항목은 운영자가 제출한 명세에 연결할 뿐 사람 승인 자체나 실행 진위를 인증하지 않는다. 이 도구는 일반 셸 명령의 모든 일시적 쓰기·ignored 파일 변경을 감시하는 샌드박스가 아니다. 반환됐다는 브라우저 상태도 독립 UI·서버 코드 동일성 인증은 아니다. 프로세스 실행 전 검사와 실제 모델이 절차를 준수했는지는 별개다. 회귀의 HTTP 서비스·stand-in executor 성공을 실제 Claude 재실행 성공으로 승격하지 않는다. 과거 실행의 범위 위반·서식 위반·준비 오류는 그대로 보존한다.
