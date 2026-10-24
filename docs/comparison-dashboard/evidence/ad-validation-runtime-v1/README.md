# 광고 검수 실행기 복원 v1

삭제된 실행 폴더를 source로 사용하지 않고, 이 묶음의 해시 고정 입력에서 새 검수 run을 만든다. OMC core는 `996d80d539d7245443f3a539f1a9fa333b450770`, 업무 기준은 `34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc`다. 이 실행기는 별도 operator runtime이며 원본 OMC의 모델 검증 성공이나 실제 홈 준비를 증명하지 않는다.

## 입력과 보존

`manifest.json`은 기존 Home·픽셀 빌더·통제 빌더·serve 원문, 과거 업무 요청·승인 기준·baseline 실패·등록·출처 기록의 경로와 SHA-256을 보존한다. 원본을 수정하지 않는다. 기존 browser.cjs의 절대 경로 의존 부분은 새 `scripts/omc_ad_validation_browser.cjs`로 대체한다. 과거 runtime.json·인증·사용자 데이터·환경변수는 복사하거나 영구 보관하지 않는다.

새 run은 **이미 준비한 새 baseline 후보의 바로 위 폴더**여야 하며 후보 외 기존 파일이 없어야 한다. baseline 후보의 Ad·foundation·Home 및 JSX 설정(`tsconfig.base.json`, `libs/sellerhub-store/tsconfig.json`) 바이트를 원본 Git 기준과 대조한다. Git 후보는 baseline HEAD와 clean 상태가 필요하고, 최소 추출 후보는 검증한 파일과 명시적 node_modules 의존성 링크만 허용한다. Home의 고정 fixture 복사만 baseline 바이트 교체를 허용한다. 사용 중인 원본 저장소·기존 runtime·추가 작업·폴더 링크·덮어쓰기는 거부한다.

## 고정 query 호환성

허용 목록은 `home`과 `controlled` 두 개다. 누락·추가 query나 해시 불일치는 복원 전에 거부한다. 복원된 브라우저는 query ID 또는 고정 원문의 해시가 일치하는 호출만 허용한다. `996d80d`의 실제 `run --profile controlled`는 CONTROLLED_QUERY 원문을 전송하며, 새 wrapper ID 호출만 검증해서 이 경로를 대신하지 않는다. 각각 검증한 동일 바이트로 복원하고 최종 브라우저 소비 지점도 같은 버퍼를 해시 검증·실행한다. 입력 경로를 재독해 바뀐 파일을 실행하거나 허용 해시를 재계산하지 않는다.

통합 회귀는 고정 core의 공개 `freeze`로 테스트 전용 정책을 등록한 뒤 실제 `run --target <owned-candidate> --profile controlled`를 subprocess로 호출한다. 이 명령과 `_browser`·`_judge`·HTTP 반환은 대체하지 않는다. 테스트 소유 정적 화면을 복원 runtime의 실제 HTTP/Chromium에서 읽어 observation·screenshot·policy/candidate/receipt pin과 제공 bundle을 대조한다. Jest·lint 등 실행하지 않은 프로필의 도구 pin은 정책 형식에 필요할 뿐 해당 검사의 실행 증거가 아니다.

| 통제 화면 | 고정 core status | CLI exit |
| --- | --- | --- |
| S1: 6종·각 이미지2개·전환·화살표0·정상 border/shadow | passed | 0 |
| S2: S1에서 border만 오류 | failed | 1 |
| S3: S1에서 화살표만 추가 | failed | 1 |
| S4: S1에서 이미지1개 미로드 | unknown / controlled_surface_not_observed | 2 |
| S5: S1에서 전환만 정지 | unknown / controlled_surface_not_observed | 2 |

두 query 각각 ID/고정 원문과 읽기 교체 시점 두 개를 조합해 회귀한다. 검사 전 교체는 거부하고, 최종 읽기 뒤 교체는 검증한 기존 버퍼만 실행해야 한다. query 교체가 실제 발생했는지 파일 해시·시점 기록도 남긴다. 위 HTML은 테스트 fixture이며 실제 baseline Ad 빌드·픽셀 matrix, 로그인 실제 홈, 실제 Claude 업무 성공과 구분한다.

## 새 run 준비

개인 경로를 source에 넣지 말고 다음 JSON을 **실행별 외부 설정 파일**로 작성한다. 모든 경로는 절대 경로다. 두 포트는 서로 다르고 사용 중이지 않아야 한다.

```json
{
  "schema_version": "omc-ad-runtime-config/v1",
  "run_root": "/absolute/new-run",
  "candidate": "/absolute/new-run/fresh-candidate",
  "baseline_repo": "/absolute/local-storefront-with-baseline-git",
  "baseline_commit": "34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc",
  "core_commit": "996d80d539d7245443f3a539f1a9fa333b450770",
  "port": 3050,
  "control_port": 3150,
  "headless": false,
  "playwright_module": "/absolute/node_modules/playwright",
  "playwright_package_sha256": "SHA256_OF_PACKAGE_JSON",
  "queries": {
    "home": {"path": "/absolute/home-query.js", "sha256": "6b6c3ab4e82beefd0c1b48f49087d6ded68f24e88dfd0e4c4e76ec9b25ea03da"},
    "controlled": {"path": "/absolute/controlled-query.js", "sha256": "78dd0132daeeaa51302f701e87801c84dabfee6df813d5b44dd79d5774fd5d09"}
  }
}
```

home-query.js는 고정 core의 `omc_workflow_preflight.BROWSER_QUERY`, controlled-query.js는 같은 core의 `omc_validation_command.CONTROLLED_QUERY` 문자열을 UTF-8 바이트 그대로 저장한다. 최신 main을 대신 가져오지 않는다. manifest의 query SHA와 다르면 거부한다. Playwright package 해시는 의존성 식별이며 설치 전체의 무결성 증명은 아니다. Node·Playwright·Chromium 및 storefront 빌드 의존성은 별도 제공한다.

저장소 루트에서:

```bash
python3 scripts/omc_ad_validation_runtime.py restore --config /absolute/config.json
```

생성되는 파일:

- `<run>/runtime.json`, `restore-receipt.json`, `browser.cjs`, `queries/home.js`, `queries/controlled.js`
- `<run>/build-controlled.cjs`, `serve.cjs`
- `<candidate>/.omc/visual-input/build-pixels.cjs`
- `<candidate>/libs/store-manager/components/src/lib/home/Home.tsx`

복원은 모든 입력·출력 경로를 먼저 검사한다. source·query·설정·manifest는 검사한 바이트와 해시를 고정한다. 기록할 때 입력 경로를 다시 읽지 않으며, 복원 파일도 고정 기대 해시와 대조한 뒤 runtime을 기록한다. 검사 후 입력이 교체되어도 새 바이트를 허용값으로 재계산하지 않는다. 쓰기 중 OS 오류는 부분 산출물을 남길 수 있으며 자동 삭제·재시도하지 않는다. 원문 오류를 보존하고 새 run을 준비한다. receipt는 해시 연결 기록이며 외부 인증이나 서명된 실행 증거가 아니다.

후보 디렉터리에서 두 빌더를 실행한다:

```bash
node ../build-controlled.cjs
node .omc/visual-input/build-pixels.cjs
```

실제 서비스 서버는 별도 준비한다. `node <run>/serve.cjs <candidate-directory-name>`는 기존 서버 원문이며 로컬 환경·의존성·인증이 필요하다. 이 명령을 성공한 빌드만으로 실행 준비 완료로 표시하지 않는다.

브라우저를 별도 프로세스로 실행하고 명령을 전달한다:

```bash
node /absolute/new-run/browser.cjs launch --config /absolute/new-run/runtime.json
python3 scripts/omc_ad_validation_runtime.py command --runtime /absolute/new-run/runtime.json --op goto --args-json '["/sixshoptest01"]'
python3 scripts/omc_ad_validation_runtime.py command --runtime /absolute/new-run/runtime.json --op snapshot
python3 scripts/omc_ad_validation_runtime.py command --runtime /absolute/new-run/runtime.json --op evaluate --args-json '["home"]'
```

명령은 localhost의 해당 origin으로 제한한다. evaluate는 고정 query ID 또는 그와 바이트·해시가 일치하는 원문만 허용한다. 후자는 고정 996d80d 준비 검사의 원문 호출과 호환되는 경로이며 임의 JavaScript·click은 허용하지 않는다. 최종 실행에서도 query 파일을 한 번 읽고 그 버퍼의 해시를 확인한 뒤 같은 버퍼를 평가한다. 검사 후 파일이 교체돼도 새 경로 내용을 재독해 실행하지 않는다. 예전 `node ../browser.cjs snapshot` 호출은 위 Python command 형식으로 대체한다. snapshot은 URL·후보·스크린샷 해시·관측 JS 응답 해시를 기록한다. 서버/후보 결속은 별도 요구사항의 bundle 해시와 대조해야 하며 URL만으로 확정하지 않는다. 프로세스는 호출자가 소유·정리하고 다른 서버를 종료하지 않는다.

## 검증 및 종료 조건

일반 CI의 `python3 -m pytest scripts/ -q`에서는 로컬 합성 Git baseline·패키지로 입력/경로/해시/오류의 단위 회귀를 수행한다. 외부 storefront·Chromium이 필요한 실제 검사는 요청이 없을 때만 명시적으로 생략하며, 이 일반 회귀 결과를 복원 완료 근거로 사용하지 않는다. 단위 fixture의 합성 commit을 실제 업무 baseline으로 주장하지 않는다.

복원 완료를 위한 실제 검사는 아래처럼 명시 실행한다. `OMC_AD_RUNTIME_INTEGRATION=1` 또는 외부 경로 환경변수 중 하나라도 지정하면 실제 검사 모드다. 필수 경로가 빠졌거나 잘못됐으면 오류로 실패하며 생략으로 통과시키지 않는다:

```bash
OMC_AD_RUNTIME_INTEGRATION=1 \
OMC_AD_RESTORE_STOREFRONT=/absolute/local-storefront \
OMC_PREFLIGHT_PLAYWRIGHT_MODULE=/absolute/node_modules/playwright \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest scripts/test_omc_ad_validation_runtime.py scripts/test_omc_ad_fixture_archive.py scripts/test_omc_home_observation.py -q
```

새 위치 A/B에서 생성 CLI → config → localhost HTTP → Chromium → 고정 판정기를 검사한다. matrix/v2는 각 위치에서 V1~V6의 72셀과 B1/B2 결속을 대조한다. V6의 기존 unaligned fixture는 대상 자체 transform 때문에 `unknown/unsupported_ancestor_transform`, CLI 2, aggregate `visual_surface_unknown`을 기대한다. 이것을 `unaligned_geometry` 분기 검사로 세지 않는다. 정상 12셀·알려진 결함·불확실성을 정확히 구분한 회귀 통과와 실제 업무 통과는 별개다.

복원 종료는 필수 matrix 일치·누락/생략 0·경로/원본 보존·빌드/홈 표본 회귀·리뷰 및 provider_calls=0이다. 실제 Claude 재개는 이 runtime과 matrix 근거의 동일 해시, 새 실행 계획 검토·명시 승인·실제 홈 로그인/광고 2개/자동 전환/후보 bundle 결속/예산 준비가 필요하다. 새 runtime/fixture/oracle 변경 시 영향 검증과 리뷰를 다시 수행한다. 과거 NOT_STARTED·FAIL_TIMEOUT·HOLD는 변경하지 않는다.


## 996d80d 후보 호환성 · A단계

현재 실행기는 단일 core `996d80d539d7245443f3a539f1a9fa333b450770`만 지원한다.
config의 core와 실행기 CORE를 먼저 대조하고, manifest.core_commit도 출력 쓰기 전에 대조한다.
이전 core99773380·637a965 및 임의 core는 `candidate_version_mismatch`, manifest core의 누락·불일치는
`manifest_core_mismatch`로 종료1이며 기존 후보와 입력을 보존한다.
과거 core637a965 재현은 당시 runtime 커밋 `f7566cc2`와 당시 설정을 함께 사용하며,
새 실행기에 과거 core를 지정하거나 새 후보를 과거 버전으로 표시하지 않는다.

복원 순서는 clean 업무 baseline → restore → 고정 core996d80d 설치 → 설치 관리 소스의
Git blob/해시 대조 → 서비스 준비다. restore는 clean baseline을 검사하므로 설치 순서를 뒤집지 않는다.
복원 receipt의 config/runtime/manifest 해시와 runtime.json의 core를 함께 보존한다.
home/controlled query 원문 해시, 입력 원문, acceptance_matrix와 픽셀·6표본 기준은 변경하지 않는다.

검증은 공개 restore subprocess의 성공·거부·무쓰기, 고정 core996d80d archive의 공개 검증 명령,
실제 로컬 HTTP/Chromium controlled/home·DPR 픽셀·baseline Ad 빌드 및 파일 교체 회귀를 포함한다.
통제 화면의 정상은 passed, 테두리·화살표 결함은 failed, 이미지 미로드·전환 정지는 unknown이며
각 실제 종료 상태도 따로 대조한다. 기존 baseline Ad 픽셀 결함을 성공으로 바꾸지 않는다.
외부 통합을 요청하지 않은 기본 CI 검사와 명시적으로 수행한 통합 결과는 별도 보고한다.
명시 통합의 환경 누락은 실패이며, 미요청 통합의 생략을 완료 근거에 합산하지 않는다.

A단계는 실제 Claude·AWS 로그인·실제 홈 계정 로그인 없이 호환성을 검증한다.
리뷰·별도 승인 커밋 후 새 runtime의 전체 커밋 SHA와 manifest/CLI/browser/query 해시를
후행 계획에 고정하기 전까지 실제 Claude 재검증 B단계는 차단한다.
A의 로컬 회귀·리뷰·커밋은 실제 업무 성공·릴리스·사용처 설치 또는 B 실행 승인이 아니다.
# Home 검증 증거 계약 v2

고정 home/controlled 실행과 새 native home 관측은 `omc-browser-execution/v2`를
사용한다. 프로필 시작부터 정책·snapshot·응답·사후 검사·봉인까지120초이며
내부 작업15초, 외부 응답20초, 정리12초·봉인5초를 예약한다. 단계 진입에는
37초가 필요하다. 단조 시계의 절대 마감을 사용하므로 응답 조각 수신이
기한을 연장하지 않는다. POSIX 주 스레드의 타이머를 사용하며 지원되지
않는 호출 환경은 통과로 추정하지 않는다.

103초 작업 마감은 정책·후보 확인과 관측 후 검사 구간에만 적용한다.
브라우저 단계는 자체 작업 마감 안에서 실행하며, 실패 후 종료 확인과 봉인은
그 작업 구간을 벗어나 전체120초 마감 아래 수행한다. 따라서 늦은 단계의
시간 초과도 예약한 정리 시간을 사용할 수 있으며 전체 기한을 연장하지 않는다.
JSON 본문·operation·result가 객체가 아니거나 result가 누락된 응답은
unknown으로 남기고 원문 보존·종료 확인 경로로 연결한다.

각 단계의 operation_id·브라우저 식별자·시작/종료·마감·HTTP 상태·오류 유형과
수신 원문 참조를 후보 `.omc` 아래 보존한다. 원문은 응답당1MiB, 프로필당8MiB로
제한한다. 부분 수신·상한 도달·저장 실패는 완전한 성공 근거가 아니다.
로그인 입력·쿠키·인증 헤더는 수집하지 않는다. 기존 조회용 runtime command는
검수 조작용이며, 그 출력만으로 v2 실행 영수증이나 완료 증거를 대신하지 않는다.

오류 또는 시간 초과 후 driver는 채널을 재사용 불가로 만들고 소유 컨텍스트를
닫는다. `operation-status`는 해당 operation_id의 종료 확인만 제공하며
`close-context`도 등록된 요청의 소유 컨텍스트에 한정한다. 응답 대기 종료를
작업 종료로 간주하지 않는다. 정리 확인이 없으면 `cleanup_unconfirmed`를
보존하고 후속 검증을 거부한다. 임의 서버나 프로세스를 종료하지 않는다.

고정 영수증과 수집기는 단계 원문·순서·마감·종료 상태를 재검증한다.
별도 native 홈 요구도 계약을 고정하며 `assess_completion_home`이 이를
검증한 뒤 기존6개 표본·서버·세션·후보 결속을 판정한다. 실제 완료 가드는
이 결과를 소비하므로 verification에 passed만 적어 우회할 수 없다.
과거 계약의 기록은 원문과 당시 의미를 유지해 읽으며 새 계약으로 승격하지 않는다.

새 준비 manifest에는 runtime manifest와 같은 `browser_evidence_contract`를
선언해 v2 준비 관측을 선택한다. 구 계약 manifest의 읽기 호환 경로는 유지되지만
그 결과를 새 native 홈 관측이나 고정 profile의 v2 영수증으로 대신 사용할 수 없다.

이 보완과 로컬 합성 회귀는 지난 실제 홈의 지연 자원을 확정하거나 실제
Claude 광고 사례의 성공을 입증하지 않는다. 새 실행 전에는 변경한 core와
runtime의 버전·파일 해시를 별도 고정해야 한다.

## A단계 공유 검증·감독 연결

996d80d core의 공개 restore·고정 명령·HTTP·Chromium·영수증 경로를 검증한다.
감독 제품 소스는 e4557dd로 보존하며, 테스트는 637a965와 996d80d를 각각 Git archive로
복원해 선택된 소스·승인 target_version·실행 경로 해시를 대조한다. 두 core 모두
정상 종료·내부 124·외부 시간 초과·최종 result 누락의 4개 공개 실행 사례를 유지한다.
이 자식은 합성 프로세스이며 Claude나 내부 모델/API를 호출하지 않는다.

개정 2의 A-FULL 명령은 runtime·supervisor·validation_command·home_observation
4개 테스트 파일의 공유 전체 실행이다. A01~A05는 같은 최종 실행 ID·소스 snapshot·
원문 로그·각 nodeid 결과를 참조하며 테스트 수를 중복 합산하지 않는다.
모든 GREEN 뒤 최종 1회, 확인된 동일 5파일 보정 뒤에만 추가 1회로 제한한다.
실패·시간 초과·생략·중단도 실행 횟수를 소비하고 첫 통과 후 확인용 재실행은 하지 않는다.
대상별 RED/GREEN은 별도 기록하지만 모두 A의 3600초 안에서 수행한다.
A 통과는 B 실행 승인이 아니며 실제 Claude 횟수·내부 API 수의 관측은 후행 B 계약을 따른다.
