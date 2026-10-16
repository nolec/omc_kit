# 통제 광고 화면의 실제 표시 검사 V0

이 검사는 **정의한 직선 구간의 테두리와 같은 캡처의 이미지 표식**만 판정한다.
일반 화면 품질, 전체 테두리 연속성, 실제 홈 검수 성공을 뜻하지 않는다.
실제 홈의 기존 검수는 별도 필수 항목으로 유지한다. 모델 호출·릴리스는 수행하지 않는다.

## 실행 전 고정 조건

- Chromium viewport 1440×1000 CSS px, DPR 1 또는 2.
- `ad-card-large`, `ad-card-medium`, `ad-card-small`, `ad-banner-small`,
  `ad-banner-large`, `ad-banner-landing` data-testid가 각각 정확히 하나 존재한다.
- 각 요소는 완전히 화면 안에 있고, x/y/width/height×DPR이 정수 device pixel에서
  0.01 이내다. fixture 배치를 정렬하며 제품 스타일을 바꿔 성공으로 구제하지 않는다.
- 검사 요소와 상위 요소의 CSS transform은 지원하지 않는다. transform으로 좌표만
  맞추면 기존 래스터화의 흐림이 남을 수 있으므로 fixture를 직접 정수 위치에 배치한다.
  내부 슬라이드 track의 전환은 캡처 전후 안정성 검사로 구분한다.
- 요소의 이미지 개수를 미리 선언한다. 가장자리는 불투명 흰색이고 중앙에는
  RGB(200,20,80) 표식이 최소 5×5 device pixel 영역으로 보인다.
  표식이 없는 기존 광고 이미지는 이 V0 입력으로 사용할 수 없다.
- 전환이 안정된 현재 이미지·위치가 캡처 전후 같아야 한다.
- 번들은 동일한 로컬 HTTP origin에서 정확히 한 번 로드된다.

각 변 중앙 60%의 바깥 1 CSS px 띠를 RGB226~233으로, 인접 안쪽 1 CSS px 띠를
RGB252 이상으로 검사한다. 각 변에서 각각 95% 이상 충족해야 한다. 모서리와 바깥
그림자는 검사하지 않는다. 내부 표식은 중앙 3×3 표본에서 채널별 오차3 이내여야 한다.
이 숫자는 이번 계약에서 고정한 값이며 결과를 보고 넓혀 통과시키지 않는다.

## Manifest와 실행

운영자가 승인할 manifest를 대상 저장소 밖에 쓰고 원본 바이트 SHA-256을 고정한다.
빌드 입력에는 현재 Ad 코드, fixture/builder 및 HTML을 포함하고, 통제 번들은
해당 입력으로 빌드한다. 아래 해시·경로는 실제 빌드 값으로 채운다.

```json
{
  "schema_version": "omc-visual-manifest/v1",
  "url": "http://127.0.0.1:8800/index.html",
  "bundle_url": "http://127.0.0.1:8800/bundle.js",
  "bundle_path": ".omc/visual-fixture/bundle.js",
  "bundle_sha256": "실제 번들 SHA256",
  "build_inputs": {
    "libs/example/Ad.tsx": "실제 입력 SHA256",
    ".omc/visual-fixture/build.cjs": "실제 builder SHA256",
    ".omc/visual-fixture/index.html": "실제 HTML SHA256"
  },
  "dpr": 1,
  "images_per_variant": 2
}
```

```bash
python3 scripts/omc_visual_check.py --target <후보> \
  --manifest <manifest.json> --manifest-sha256 <승인한 해시> \
  --output-dir <존재하지 않는 외부 폴더 또는 .omc 하위 폴더> \
  --playwright-module <이미 설치된 Playwright 모듈 절대 경로>
```

Node.js·Playwright·Chromium과 Python Pillow가 필요하다. 자동 설치하지 않는다.
출력은 원본 screen.png, 브라우저 관측 capture.json, report.json과 실행 출력이다.
종료값은 passed=0, failed=1, unknown=2다. 이미지 숨김/누락/표식 가림과 선 결함은
failed, 로딩 실패·비정렬·불안정·다른 번들은 unknown이다. 기존 결과 폴더를 덮어쓰지 않는다.

후보 스냅샷과 빌드 입력·번들 파일을 캡처 전후 대조하고, 브라우저가 실제 받은 번들의
해시를 비교한다. 이 연결은 운영자가 제출한 빌드 명세의 결속이며 올바른 빌드 수행,
전체 의존성 추적 또는 악의적인 로컬 파일 작성자를 인증하는 장치는 아니다.

## 필수 검사 연결

새 비교의 승인 기준 checks에 `id: browser-visual-controlled`를 추가하고 같은 항목에
`visual_manifest_sha256`으로 승인한 manifest 해시를 고정한다. 이는 기존 browser-home과
별개이며 과거 승인 개정을 직접 바꾸지 않는다. DPR1·2 두 정상 방식의 24개 회귀 조합은
실제 passed여야 하며 unknown을 정상 회귀 통과로 계산하지 않는다.

```bash
python3 scripts/omc_workflow_validation.py <기존 필수 인자> \
  --visual-report <새 결과 폴더/report.json> \
  --visual-report-sha256 <report 원본 SHA256>
```

승인 기준에 해당 ID가 있으면 인자를 생략해도 visual_controlled=unknown, 전체 hold다.
수집기는 후보·manifest·고정 기준·PNG/관측 해시를 대조하고 픽셀 판정을 다시 실행한다.
모델의 완료 주장과 관계없이 failed/unknown은 전체 통과를 막는다. 기존 범위/서식 검사와
실제 홈 검사도 유지한다. 해당 ID가 없는 과거 호출은 기존 의미를 유지한다. 이 선택형
비교 도구가 모든 Claude 실행이나 임의 셸 우회를 전역 차단하는 것은 아니다.

## 회귀 실행

```bash
OMC_PREFLIGHT_PLAYWRIGHT_MODULE=<로컬 Playwright 모듈> \
python3 -m pytest -q scripts/test_omc_visual_check.py
```

환경변수를 생략한 일반 Python 실행은 실제 브라우저 회귀를 skip한다. 그러한 실행은
이 기능의 완료 증거가 아니다. 합성 fixture는 실제 Chromium 픽셀을 사용하지만
그 자체가 실제 제품 Ad 코드나 로그인된 서비스 홈을 검증한 증거는 아니다.
