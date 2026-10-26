# 독립 Ad 입력·종료 관측 계약

이 묶음은 영구 공통 입력과 시간·종료 증거 계약이다. 경로 치환은 검수 runtime의 contract 명령이 제공하며 감독의 절대 마감 모드와 연결한다. 자동 편집 중단 기능은 제공하지 않는다. 실제 Claude 실행·비교 재개·로그인은 별도 결정 사항이다.

## 입력 작성과 확인

`common-input.template.json`과 `prompt.template.md`의 `{{CANDIDATE_ROOT}}`를 후보의
실제 절대 경로로, `{{REQUIREMENT_SHA256}}`을 그 후보의
`.omc/visual-requirement.json` 원본 바이트 SHA256으로 치환한다. JSON 경로는 JSON
문자열 규칙으로 인코딩한다. 미치환 변수가 없어야 하며 공통 입력·프롬프트의
상대/절대 경로·해시와 실제 일반 파일을 대조한다. 파일이 없거나 내용이 바뀌면
준비 실패다. `.omc/visual-input/visual-requirement.json` 별칭·복사본을 만들지 않는다.
양쪽의 공통 정책은 동일하게 유지하고 도구별 세션·승인·실행 지시는 별도 부록으로
분리한다. 문서 일관성 검사로 모델의 실제 읽기·절차 준수를 인증하지 않는다.

300초는 진행 확인점이다. 수정 종료 900초부터 원문 회수 1800초까지의 누적 목표는
제안 예산이며 성공 시간의 실측 보장이 아니다. 실제 호출 전 E0·사전처리·회수 예약을
포함한 감독 인자와 절대 마감을 별도 확정한다. 전체 7200초와 기존 정리 기한은
유지한다. 이 변경이 기존 감독의 timeout을 바꾸거나 900초 이후 편집을 차단하지 않는다.

## 선택적 종료 관측

기존 `tee_native`의 `termination_record`(후보 기준 `.omc` 내부 상대 경로)와
`termination_run_id`를 함께 지정한다. 공개 감독에는 같은 파일의 절대 경로를
`--termination-record`, 같은 식별자를 `--termination-run-id`로 함께 지정한다.
실제 부모 PID와 직접 자식 셸 PID는 감독 관측과 대조한다. 경로는 서로 다른 새
일반 파일이어야 하며 기존 기록을 덮어쓰지 않는다. 기록 모드를 생략한 호출은
기존 판정 의미를 유지한다. 실제 비교를 위한 호출 조합은 이 문서에서 실행하지 않는다.

| 역할 | 관측 범위 |
| --- | --- |
| 감독 | 실행 부모 PID·반환 코드, 원문, 직접 자식 그룹 부재 |
| 실행 부모 | 자신이 Popen한 셸 PID·wait 결과·신호·회수 상태 |
| 직접 자식 셸 | 기존 PID/종료 파일을 쓰고 Claude를 호출 |
| Claude | 현재 구조에서 PID·자체 종료 코드는 별도 관측하지 않으므로 null |

새 JSON의 schema는 `omc-native-termination/v1`이다. `run_id`, `writer_pid`,
`direct_child_pid`, `direct_child_pgid`, `stop_reason`, `signals_sent`,
`direct_child_returncode`, `direct_child_signal`, `wait_completed`,
`process_group_absent`, `drain_complete`, `claude_pid`, `claude_returncode`,
`recorded_at`(Unix 초)를 보존한다. 신호 목록은 전송 시도의 signal/scope/outcome을
기록하며 전송 실패나 이미 사라진 그룹을 성공 전송으로 표시하지 않는다.

부모가 관측한 셸 반환 코드 -9는 셸이 SIGKILL로 종료됐다는 뜻이다. 기존 셸 기록의
137과 의미를 대조할 수 있지만 기존 파일을 대신 작성하지 않는다. 셸 결과를
Claude 자체의 종료 결과로 해석하지 않는다. 기록 파일은 로컬 결속 근거이며 동시
악의적 변조를 인증하는 서명이나 보안 경계가 아니다.

감독은 새 모드에서 기록·신원·회수·원문 drain·그룹 부재를 확인한다. 기록 누락·불일치·
회수 실패·그룹 잔류·저장 실패는 성공으로 추정하지 않는다. 부모까지 SIGKILL되거나
저장 장치가 실패하면 기록을 보장할 수 없다. 정리가 확인된 강제 종료도 정상 실행이나
업무 성공이 아니다. 종료 결과·정리·원문 보존·업무 성공은 별개로 판정한다.

## 회귀 범위

템플릿을 서로 다른 후보 경로에 치환해 실제 문서와 파일의 연결을 검사한다.
공개 감독 → 변경한 실행 부모 → 실제 셸 recorder → 합성 프로그램 경로로 정상·비정상·
신호 중단·기록 손상·작성 부모 종료·회수/저장 실패와 기존 호출 호환성을 검사한다.
기존 고정 core의 회귀를 새 core의 검증으로 대체하지 않는다. 실제 모델 호출은 0회다.

## 개정 3 구현 계약

공통 원본은 common-input.template.json이며 tests/lint/build-controlled/build-pixels/controlled/pixels-dpr1/pixels-dpr2의 정확한 명령·환경·증거 위치·기준과 manifest 생성 코드를 포함한다. 과거 원문 출처 SHA와 원래 버전을 보존하며 임시 경로는 @NAME@ 슬롯으로만 바꾼다. 영구 데이터의 무결성은 runtime manifest의 common_contract 해시로 고정한다. `{{CANDIDATE_ROOT}}`, `{{REQUIREMENT_SHA256}}`은 요구 파일을 설명하는 기존 슬롯이고 실제 실행 경로 슬롯은 runtime contract 명령이 함께 치환한다.

공개 감독의 새 선택 인자는 --origin-monotonic E0 --deadline-monotonic D --run-id ID --phase-evidence 후보내부파일이다. D-E0는0초 초과1800초 이하이며 다른 시계·미래origin·NaN·무한대는 허용하지 않는다. 기본1800초 구간의 경계는 사전처리30/편집900/검증1680/완료1740/정상종료1760/정리1772/최종기록1800초다. 짧은 합성 회귀에서는 구간 비율을 축소하고 실제시각 허용 오차와 결정적 시계 경계 검사를 분리한다. 실제 비교 예산으로 짧은 회귀 설정을 사용하지 않는다. 기존 timeout/drain 모드는 유지한다.

감시 부모는 입력을 읽기 전에 worker를 분리해 사전처리 정지도 관측한다. worker는 직접 생성한 그룹의 PID·PGID·시작 식별을 인계한다. 부모는 살아 있는 동일 신원을 확인한 그룹만 정리한다. 그룹 선두가 먼저 사라져도 이미 확인한 구성원의 신원을 재확인한다. 인계 공백·신원 불일치는 정리 성공으로 추정하지 않는다.

중단 시 정리 마감은 min(stop+12,E0+1772)다. worker 정지·그룹 종료·회수는 공유 예산을 사용하고 직렬로12초씩 연장하지 않는다. 정상 종료·조기 실패·신호 중단 모두 같은 식을 적용한다. 정리 시각이 늦었으면 나중에 그룹이 사라져도 cleanup_unconfirmed를 유지한다. 최종 기록 예약은 E0+1800까지이며 조기 종료 시 즉시 기록한다.

부모 SIGKILL·OS 정지·저장 장치 무응답에서는 최종 기록을 보장하지 않는다. 외부 소비자는 같은 run_id의 result.json과해시seal.json이 모두 있고 기한 조건에 맞는지 확인한다. 누락·부분 기록·다른 실행은 unknown이며 별도 복구 감독은 추가하지 않는다.

phase-evidence는 omc-ad-phase-evidence/v1로 run_id/candidate/origin_monotonic/source_sha256/last_edit_monotonic/checks/completion_monotonic을 연결한다. checks에는 고정7개 ID와 시작·종료·source_snapshot_sha256·stdout/stderr/result의 경로·SHA가 필요하다. 각 결과는 공통 계약의 실제 argv/bindings/exit_code/timed_out/artifacts를 포함하며 원문 재평가를 통과해야 한다. 전체 마감 준수만으로 단계 unknown을 통과시키지 않는다. 로컬 기록은 실행 진위나 적대적 변조를 인증하지 않는다.

전체7200초의 비교 실행 자체는 별도 계획·검토·요청 대상이다. 실제 홈 NOT_VERIFIED·비교 HOLD·기존 설치 실패는 유지한다.
