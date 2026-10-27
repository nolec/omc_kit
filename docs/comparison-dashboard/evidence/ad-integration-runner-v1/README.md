# 단일 호출의 검사·종료·기록 실행기 v1

이 실행기는 기존 임시 제어기와 사후 기록 스크립트 사이의 대화·폴링 의존을 제거한다.
core·감독·완료 가드의 판정 규칙을 대체하지 않는다. 명령 실행 이력은 실제 서비스 성공의 증명이 아니다.

## 범위와 실행 경로

구현: `scripts/omc_ad_integration_runner.py`.
직접 회귀: `scripts/test_omc_ad_integration_runner.py`.
이 세 파일만 이번 범위다. 기존 호환성 4개, 316개 통과와 사후 기록 시간 초과 원문,
기존 승인·스테이징은 별도 보존한다. 실제 홈은 미검증, 비교는 HOLD다.

공개 실행:

```sh
python3 scripts/omc_ad_integration_runner.py --config /absolute/config.json --output /absolute/new-candidate/.omc/runs/unique-run
python3 scripts/omc_ad_integration_runner.py --read-result /absolute/new-candidate/.omc/runs/unique-run
```

설정은 이미 승인된 명령과 독립 후보를 지정한다. 이 문서는 기존 검사나 비교의 재실행 승인이 아니다.

```json
{
  "target": "/absolute/new-candidate",
  "session_id": "new-confirmed-session",
  "verification_file": ".omc/verification/new-confirmed-session.json",
  "test_argv": ["/absolute/python3", "approved-check.py"],
  "evidence_files": [".omc/state/check-output.txt"],
  "protected_roots": ["/absolute/original-repository", "/absolute/old-candidate"],
  "test_timeout_seconds": 900,
  "cleanup_seconds": 60
}
```

- 대상은 독립 `.git` 디렉터리를 가진 새 후보여야 한다. 공유 인덱스 worktree는 거부한다.
- 실행기 저장소와 protected_roots의 동일·상위·하위 대상은 거부한다. 기존 후보·원본·보관 폴더를 모두 보호 목록에 넣는다.
- 해당 대상의 지정 세션이 confirmed인지 확인하고, 검사 후와 가드 호출 직전에 같은 세션 원문인지 대조한다.
- 출력은 후보 `.omc` 안의 새 폴더만 허용하며 기존 폴더를 재사용하거나 복원하지 않는다.
- 실제 가드는 대상의 `scripts/omc_pipeline_guard.py complete-task --verification-file <고정 입력>`이다.
  임의 guard 명령을 설정으로 주입하지 않는다. 회귀는 별도의 작은 통제 후보에 가드 대역을 둔다.
- test_argv는 신뢰한 운영자 입력이며 셸을 거치지 않는다. 실행기 자체가 명령의 안전성이나 승인 진위를 인증하지 않는다.
- verification_file은 승인 기준·현재 snapshot과 연결된 입력이어야 한다. 검사 명령이 필요한 상태·근거를 작성해야 하며,
  실행기가 종료 코드 0만 보고 검증 항목을 passed로 채우지는 않는다. 가드 stdout의 공식 첫 상태·기계 outcome·VERDICT와 종료 코드를 함께 본다.

## 시간 계약

모든 판정은 같은 실행의 monotonic 시각을 사용한다.
검사 마감은 min(검사 시작+승인된 검사 제한, 작업 시작+1500초).
정리는 조기 종료에도 min(중단 시각+공유 정리 예산, 작업 시작+1560초) 이내에서만 수행한다.

기록 마감 D=min(정리 종료+60초, 작업 시작+1620초).
초기 증거 10초 / 가드 실행 최소 20초 / 가드 정리 10초 / 최종 기록 20초를 운영 예산으로 둔다.
이는 실측 성능 보장이 아니다. 고정 후와 실제 호출 직전에 R=D-now를 재계산한다.
R≤0은 deadline_exceeded, 0<R≤50은 insufficient_remaining_budget이며 가드는 호출하지 않는다.
가드 실행 마감 D-30초, 가드 정리 마감 D-20초. 늦은 정상 반환도 완료로 인정하지 않는다.
TERM은 최대 5초 또는 정리 잔여 예산의 절반만 사용하고 나머지를 KILL·종료 확인에 남긴다.
소유 그룹만 대상으로 삼으며, 종료 확인 불가는 unconfirmed다. 자동 재시도·예산 연장은 없다.

파일 읽기는 최대 8MiB의 유한 바이트 읽기다. 제한을 넘는 증거를 무시하고 완료로 처리하지 않는다.
시간 점검 사이의 파일 I/O·fsync·프로세스 생성 자체를 강제로 중단할 수는 없다.
부모 강제 종료·OS 정지·저장 장치 장애의 엄격한 실시간 종료나 crash durability는 보장하지 않는다.

## 증거 고정과 최종 기록 분리

원문은 처음부터 새 보관 폴더 raw/에 직접 기록한다. 최소 결과 minimum.json은 가드 전에 저장한다.
작성자 종료가 확인되면 verification과 명시한 증거를 frozen/에 고정한다.
가드 호출 전후 고정 입력과 원본 입력 해시를 비교한다. 이후 final.json을 추가해도 frozen/을 다시 쓰지 않는다.

최종 기록은 검사 결과, 가드 상태, 종료 확인, 원문 크기·해시, 입력 해시와 시간 경계를 연결한다.
가드 예외·시간 초과·증거 변경은 별도 사유이며 검사 통과와 섞지 않는다.
최종 기록은 final.tmp 작성·닫기 후 final.json으로 원자 게시한다.
publication.json이 최종 기록 해시·실행 ID·게시 시각을 연결하고, 닫힘 후 마감 재확인을 통과해야 pending을 제거한다.
`--read-result`는 pending 부재·게시 연결·시간·파일 크기/해시·최종 상태를 다시 확인한다.
가드 성공 반환만으로 전체 완료가 되지 않는다. final.json 단독 읽기는 공식 완료 판정이 아니다.

## 종료 미확인과 부분 증거

검사 작성자 종료가 미확인이면 가드를 호출하지 않는다. 가드 종료 미확인도 전체 완료를 차단한다.
원문 최종 해시는 null이며 archive_complete와 overall_complete는 false다.
원문은 계속 변할 수 있으므로 원문 위치를 보존하고 별도 partial/ 복사본만 만든다.
복사 시작 시 확인한 크기 이하, 파일별 1MiB·합계 8MiB 이하, 기록 마감 이내에서만 읽는다.
복사본에는 실제 바이트의 크기·해시·수집 시각·원문 위치를 기록한다. 원문의 완전한 복사라는 주장은 하지 않는다.
복사 또는 최종 저장 실패 시 가능한 원문 위치와 원문 오류를 stdout으로 알린다.
최종 목록이 없거나 부분 저장·늦은 게시·해시 불일치가 있으면 소비자는 incomplete로 판정한다.
성공 종료 코드는 0, 미완료·실패는 1이다. 상태와 종료 코드를 함께 확인한다.

## 검증 경계

이번 직접 회귀는 공개 CLI의 통제된 작은 자식·가드 대역과 경계 시계를 사용한다.
실제 TERM/KILL·출력 보존은 실제 자식으로, 종료 확인 불가·디스크 오류는 결정적 주입으로 검증한다.
경계 시계와 주입은 테스트 진입 코드에서만 전달하며 공개 설정으로 제공하지 않는다.
기존 316개 전체 통합·실제 가드의 기존 작업 완료 복구·Claude·비교·로그인 홈은 실행하지 않는다.
직접 회귀 결과는 새 실행기의 로컬 계약 근거이며 실제 광고 업무 전체 완료의 증거가 아니다.


## 리뷰 후 입력 결속 보완

입력마다 처음 읽은 단일 바이트 버퍼로 frozen/ 복사본과 원본·복사본 기준 SHA-256을 만든다.
verification JSON은 세션 확인을 위해 해석하지만 다시 직렬화하지 않는다. 공백·줄바꿈도 원문 그대로 보존한다.
원본을 다시 읽는 것은 최초 버퍼의 기준 해시와 현재 상태를 대조할 때만 한다.
같은 원본을 중복 지정해 경로별 기준 해시를 덮어쓰는 입력은 duplicate input source로 거부한다.
가드 호출 전·실제 호출 경계·반환 후 원본이나 복사본이 달라지면 evidence_changed로 차단한다.
첫 읽기 후 원본 교체를 두 종류(verification·추가 evidence)에 주입해 가드 미호출과 완료 차단을 확인한다.
부분 복사 오류는 verification 저장 오류와 구분한다. 실제 partial/ 복사본의 열기·쓰기·닫기 실패를 각각 주입하고,
raw/ 원문 유지·오류 원문·원문 최종 해시 null·보관 및 전체 완료 차단을 공개 CLI에서 확인한다.
기존 후보의 스테이징과 기록은 그대로 두고, 검토 후보에는 실행기·테스트·이 문서의 세 파일만 복사한다.
승인 기준·검증 파일·교훈 기록은 검토 후보로 옮기거나 숨기지 않고 원래 작업 후보와 별도 증거 보관에 유지한다.
검토 snapshot의 경로 목록과 내용·권한을 요청한 세 파일 및 검증한 소스와 대조한다.
