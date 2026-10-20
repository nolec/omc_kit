# OMC Kit Quality Gate Contract

이 문서는 OMC Kit 자체의 ship 품질 게이트가 무엇을 실행하고 왜 그 범위를
선택하는지 고정한다. 일반 제품 설명과 사용 안내를 담는 `README.md`는 이
계약의 evidence가 아니다.

## Local ship gate

```text
python3 -m pytest scripts -q -m "not slow"
```

- 목적: OMC Python 기능의 빠른 회귀 검증
- 범위: `full`
- required: `true`
- timeout: 3,600초
- 제외: `slow` marker가 붙은 health 검증. 이는 일상 ship gate가 아니라
  별도 장시간 검증으로 실행한다.

## CI 관계

`.github/workflows/omc-ci.yml`은 이 계약의 두 번째 evidence다. CI는 Linux에서
shell-independent 전체 suite를, macOS에서 shell-dependent hook tests를 실행한다.
따라서 CI 명령은 local ship 명령과 문자열까지 동일하지 않으며, 둘 중 하나를
다른 하나의 대체 근거로 취급하지 않는다.

## Evidence 정책

명시적 migration을 적용한 OMC Kit의 `.omc/quality-gates.json`은 다음 두 파일만
evidence로 결속한다. migration 전의 기존 config는 그 기존 evidence와 approval
상태를 유지한다.

1. `docs/quality_gate_contract.md`
2. `.github/workflows/omc-ci.yml`

README의 제품 상태, roadmap, 예시, 일반 문서 변경은 local ship 검증 계약을
변경하지 않는다. 반대로 위 두 evidence 중 하나가 바뀌면 승인 receipt는 stale이
되어야 한다.

## 기존 설정 migration

`setup --force`는 사용처가 소유한 `.omc/quality-gates.json`을 변경하지 않는다.
README를 evidence로 가진 기존 설정도 자동으로 바꾸지 않는다. 각 저장소에서
현재 config SHA를 확인한 뒤, 위 두 evidence SHA와 동일한 gate 명령을 포함한
proposal을 작성해 CAS로 적용하고 별도로 승인한다.

```text
python3 scripts/omc_quality_gate.py --target . status
python3 scripts/omc_quality_gate.py --target . proposal-validate <proposal.json>
python3 scripts/omc_quality_gate.py --target . proposal-apply <proposal.json> \
  --expected-current-sha256 <current-config-sha256>
python3 scripts/omc_quality_gate.py --target . approve --config-sha256 <new-config-sha256>
```

proposal·approval 이전에는 기존 설정을 삭제하거나 README evidence만 제거하지
않는다. 손상되었거나 parse할 수 없는 config의 복구는
`docs/omc_quality_gates.md`의 raw-file-hash 절차를 따른다.

## 이번 최종 후보에 한정한 생략 예외

2026-10-20 사용자는 아래 3개 테스트의 생략 예외와 다음 절의 보존 기준을
선택했다. 적용 대상은 기준 `edae3c9`에 이 계약 문서 변경을 반영하고 리뷰·커밋한
뒤 고정하는 **최종 후보 1개와 전체 검사 최대 1회**다. 실행 전 별도 승인 기준에
전체 후보 SHA, 문서·설정 해시, 실제 실행 환경과 예외 목록을 연결한다.
최종 SHA가 연결되지 않았거나 후보가 바뀌면 실행할 수 없으며 자동 승계하지 않는다.
이 문서의 정책 선택은 커밋·설정 적용·품질 명령 실행·릴리스 승인을 대신하지 않는다.

| 정확한 pytest nodeid | 허용되는 생략 사유 | 결과의 의미 |
| --- | --- | --- |
| `scripts/test_omc_review_compare.py::test_observed_candidate_hashes_match_recorded_manifest` | `observed candidate inputs are external; set OMC_REVIEW_OBSERVED_INPUT_ROOT to verify their hashes` | 외부 과거 입력 원본 미확보로 재현성 미검증 |
| `scripts/test_omc_review_compare.py::test_observed_candidate_hash_verification_reports_manifest_mismatch` | `observed candidate inputs are external; set OMC_REVIEW_OBSERVED_INPUT_ROOT to verify their hashes` | 과거 실제 입력의 불일치 검증 미실행 |
| `scripts/test_omc_autopilot.py::test_module_missing` | `모듈 없음 — RED 단계 확인용` | 모듈 존재 시 생략하는 의도적 RED 표식; 실제 모듈 import 성공은 확인 |

- 예외는 정확한 nodeid와 사유가 모두 일치하는 `skipped`에만 적용한다.
  해당 테스트의 실패·오류·xfail을 면제하거나 생략을 통과 개수에 포함하지 않는다.
- 전체 명령과 `not slow` 선택을 유지한다. 테스트 삭제, marker 변경,
  `-k`·`--ignore` 등으로 검사 대상을 줄이지 않는다. 기존 `slow` 제외 외에는
  위 목록에 없는 생략이나 예상하지 못한 상태가 하나라도 있으면 차단한다.
- 종료 코드뿐 아니라 JUnit/XML의 개별 결과와 생략 목록을 대조한다.
  실행 출력과 실행 환경을 보존하고 출력 확장 옵션만 허용한다.
- 과거 manifest와 기대 해시는 보존한다. 다른 diff나 합성 자료로 원본을 대체하거나
  과거 결과를 소급 성공 처리하지 않는다. 원본을 확보하면 기대 해시를 확인한 뒤
  별도 역사적 입력 검증으로 수행한다.

### 실제 광고 빌드는 필수

`scripts/test_omc_ad_fixture_archive.py::test_recovered_builder_bundles_actual_baseline_ad`
는 생략 예외가 아니다. `OMC_AD_RESTORE_STOREFRONT`에 실제 의존성이 있는 저장소를
지정하고, 테스트가 고정한 `34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc`의 원본,
보존된 빌더 입력과 esbuild를 사용한다. 전체 검사 안에서 해당 테스트의 통과와
산출물 생성을 확인한다. 생략·실행 불가·실패면 차단한다.
별도 광고 해소 검사를 반복하지 않으며, 빌드 통과를 실제 홈·광고 화면 검수나
Claude 비교 성능 검증으로 확대 해석하지 않는다.

### 최종 후보 고정 전후의 보존 구간

1. **고정 전 준비:** 명시적으로 요청된 문서 수정·리뷰·커밋을 수행하고 변경 내역을
   기록한다. 이때 승인된 HEAD·문서·index 변경을 검증 중 보존 위반으로 취급하지 않는다.
2. **고정 시점:** 최종 커밋을 결정한 직후, 실행용 복사본 준비를 시작하기 전에
   원본의 HEAD·파일 내용·권한·index 바이트 기준값을 저장한다.
3. **고정 후 원본:** 이후 실행 준비부터 전체 검사 종료까지 원본 기준값을 유지한다.
   Git 상태 조회도 index 캐시를 갱신할 수 있으므로 원본 검사는 직접 바이트 읽기를
   우선하며, 원본 index를 복원·reset해 변경을 숨기지 않는다.
4. **고정 후 검수 복사본:** 동일 최종 후보로 준비하고, 실행 직전 별도 기준값을 저장한다.
   HEAD·파일 내용·권한·index의 mode/oid/path/stage·플래그 변화는 차단한다.
   내부 해시가 달라졌을 때 stat 캐시·확장 정보 차이를 설명할 수 있는 경우만
   원시 바이트와 해석 근거를 보존한 뒤 허용한다. 미해석 변경은 차단한다.

과거 index 변경 원인은 `UNKNOWN`으로 유지한다. 대표 재현을 당시 인과의 증명으로
사용하지 않으며, 실행 결과를 본 뒤 보존 기준을 완화하지 않는다.

### 실행 진입과 종료

- 최종 후보 리뷰, 실제 변경 범위 TDD, 비밀값 검사, 품질 설정의 근거 해시와
  정식 승인을 확인한다. 이 문서가 바뀌면 기존 설정의 근거는 stale이므로 위의
  migration 절차로 갱신하고 승인한다. 이전 승인 파일을 편집해 통과시키지 않는다.
- Node/Jest/Playwright와 실제 광고 빌드 입력을 고정하고, Chromium은 로컬 렌더
  성공을 확인한 권한 허용 환경에서 실행한다. 누락된 준비를 통과로 추정하지 않는다.
- 별도 출하 검증 요청 후 전체 게이트를 최대 1회, 3,600초 한도로 실행한다.
  자동 수정·재시도는 0회다. 실패·오류·예상 밖 생략·시간 초과·보존 위반이면 중단한다.
- 보고는 실제 통과, 위의 허용된 생략, 미실행·실패를 구분한다. 진단 작업의 RED
  차단 기록은 보존하고 이번 출하 결과로 성공 처리하지 않는다.
  push·릴리스·재설치는 이 검증 범위에 포함하지 않는다.
