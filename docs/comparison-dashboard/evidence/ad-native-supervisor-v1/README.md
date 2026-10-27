# 광고 native 감독 실행기 v1

별도 operator 도구다. 고정 core `637a9653759cfca9e070450139dfa56cdd67c5d1`과
runtime `f7566cc21348ecce36631f2c5748492269e3c7e3`를 변경하지 않는다.
감독의 정상 실행은 광고 업무 성공·화면 통과·비교 HOLD 해소를 뜻하지 않는다.
이 구현과 회귀에는 실제 Claude·provider·인증·브라우저 호출이 없다.

## 실행 계약

Python 표준 라이브러리와 POSIX 환경을 사용한다. 실행할 후보와 출력 폴더는
작업자가 소유해야 한다. 새 출력 폴더의 부모는 존재해야 하며, 출력 폴더는
없어야 한다. 재사용·덮어쓰기·자동 재시도는 없다. 실행 실패 후에도 폴더는 남는다.
PID/종료 기록은 서로 다른 새 파일이고 후보 내부의 일반 경로여야 한다.
환경변수 전체·자격 증명은 저장하지 않는다. argv·프롬프트·native 원문에는
민감한 내용이 포함될 수 있으므로 로컬 증거로 관리하고 자동 공유하지 않는다.

```text
python3 scripts/omc_ad_native_supervisor.py
  --cwd <후보 절대경로>
  --output <새 증거 폴더 절대경로>
  --prompt <일반 프롬프트 파일>
  --child-pid-file <후보 내부 새 PID 기록>
  --child-exit-file <후보 내부 새 종료 기록>
  --timeout 1800 --drain 12
  -- <고정 core의 공개 wrapper argv>
```

공개 wrapper는 `python3 <고정 core>/scripts/omc_home_observation.py run
--target <후보> --launch <종료 기록 shell과 자식 argv>`다. 기존 종료 기록 literal은
아래처럼 PID 관측 한 줄을 앞에 붙인다. 감독이 shell 명령을 추가 해석하지 않으며
호출자가 이 literal과 argv를 명시적으로 전달한다.

```sh
printf "%s\n" "$$" > "$OMC_CHILD_PID_RECORD";
"$@"; child_status=$?;
printf "%s\n" "$child_status" > "$OMC_CHILD_EXIT_RECORD";
exit "$child_status"
```

감독이 두 환경변수를 새 파일 경로로 설정한다. PID는 core가 별도 세션으로
실행하는 shell의 그룹 ID다. 감독은 그룹 존재 여부만 조회하고 그 그룹에 종료
신호를 보내지 않는다. 종료 신호는 직접 시작한 wrapper에만 보낸다.
PID/종료 기록은 신뢰한 로컬 recorder의 관측 근거이며 적대적 변조 방지가 아니다.

실행 파일은 후보 cwd를 기준으로 먼저 절대경로를 확정한다. bare 명령의 PATH 검색도
후보 기준이며 상대·빈 PATH 항목은 후보 내부에서 해석한다. 이 동일 절대경로로
해시를 계산하고 Popen을 실행한다. `requested_argv`는 요청 원문이고 `argv`는 실제
실행 경로다. 호출한 위치의 동명 실행 파일을 대신 해시하지 않는다.
bare 명령이 PATH에 없으면 후보 폴더의 동명 파일로 fallback하지 않는다.
종료 2와 `launch_error=executable_not_on_path`를 기록하며 `argv=[]`,
`wrapper_started=false`, `attempt_consumed=false`로 남긴다. 요청 argv는 보존한다.
후보 파일을 명시적으로 실행하려면 `./경로`를 주거나 PATH에 해당 위치를 넣어야 한다.

`invocation.json`에는 감독 소스·프롬프트·launch executable 및 interpreter 뒤
script가 존재하는 경우 그 파일 해시, cwd·실제/요청 argv·제한을 기록한다. 전체 core의
불변성이나 외부 의존성 무결성 증명은 별도 고정 소스 검사에 맡긴다.

## 시간·정리

실행 시계는 wrapper 시작 요청 전 monotonic에서 시작한다. 입력 검증·출력 폴더
확보·입력 snapshot 복사·해시 계산은 그 이전이다. 실행 제한은 0초 초과~1800초,
회수 제한은 0초 초과~12초다. 시간 초과나 처리 가능한 SIGINT/SIGTERM에서
wrapper에 TERM을 요청하고, 회수 창의 60%(최대 10초) 뒤에도 살아 있으면 wrapper를
KILL한다. 회수 마감에는 남은 파이프를 닫는다. 정상 wrapper 종료 후 자손이 EOF를
막는 경우에도 같은 회수 한도가 적용된다. 무기한 EOF·입력 쓰기 대기는 없다.

core가 소유한 자식을 정리한다. 정상 판정에는 wrapper 회수, 자식 종료 기록,
해당 PID 그룹 부재, 양쪽 출력 EOF가 모두 필요하다. 기록 부재는 `unconfirmed`,
관측된 그룹 잔류는 `incomplete`다. 테스트가 나중에 자식을 정리해도 감독 결과를
수정하지 않는다. 그룹 부재 관측은 같은 PID의 재사용·동시 로컬 변조나 그룹 밖으로
탈출한 모든 프로세스에 대한 완전한 인증이 아니다.

SIGKILL·전원 상실·OS 자체의 저장 장치 정지는 정상 정리 보장 범위 밖이다.
이 경우 부분 기록이 남을 수 있고 성공으로 추정하지 않는다. 시간 제한은 OS의
정상 스케줄링·로컬 일반 파일 I/O를 전제로 하며 실시간 운영체제 수준 보장은 아니다.

## 원문·판정

프롬프트 원본은 한 번 읽어 메모리를 무제한 사용하지 않는 private 임시 파일로
복사한다. 복사한 동일 바이트에서 기대 길이·해시를 계산하고 그 파일을 stdin으로
전달한다. 원본 경로를 교체해도 다시 읽지 않으며 임시 파일은 실행 종료 때 닫힌다.
결과의 `input`에는 실제 pipe 쓰기 바이트·해시, 기대 바이트·해시, EOF 전송,
입력 오류·전달 완전성을 기록한다. 조기 BrokenPipe·미전달·불일치이면 정상 금지다.
빈 입력도 EOF를 보내고 빈 SHA-256을 기록한다. 여기서 전달 완료는 OS pipe 쓰기와
EOF를 확인한 것이며 수신 프로그램의 전체 읽기·내용 이해를 인증하지 않는다.

`stdout.raw`·`stderr.raw`에 64KiB 이하 단위로 바이트를 연속 저장한다.
JSONL 판독 버퍼는 레코드당 1MiB다. 초과 레코드는 판독을 중단하지만 원문은 계속
저장한다. JSON 객체가 아닌 줄·손상·부분 마지막 줄·크기 초과는 판독 불완전이다.
정상 terminal은 `type=result`, `subtype=success`, `is_error=false`인 객체다.
전체 result와 정상 result가 각각 정확히 1개여야 한다. 정상 이외의 result,
중복 result, 최종 result 없음은 정상 판정을 금지한다.

원문 저장 오류 시 종료 처리를 시작한다. 이미 저장한 바이트 수·SHA-256은 유지하고
원문 회수 불완전을 기록한다. 정상 마지막 EOF까지 읽었더라도 저장 오류를 완화하지
않는다. `result.json` 저장 실패는 감독 stderr의 `result_record_failed`와 종료 1로
알린다. 저장 장치 오류가 발생한 뒤 전체 원문 보존을 주장하지 않는다.

| CLI 종료 | 의미 |
| --- | --- |
| 0 | wrapper0·자식0·정상 result1개·입력 전달/해시 일치·정리 확인·원문/판독 완전·timeout/중단/강제종료/정리오류 없음 |
| 1 | 실행했지만 실패·시간 초과·중단·결과/정리 불완전, 또는 결과 기록 실패 |
| 2 | 입력·준비 오류 또는 wrapper 시작 실패 |

`result.json`에는 자식 내부124와 외부 timeout을 별개로 남긴다. 원인 단정 없이
실제 wrapper/자식 코드·시각·회수·저장 오류를 기록한다. 출력 폴더는 spawn 실패에도
소비되지만 `attempt_consumed`는 wrapper가 실제 시작된 경우만 true다.

## 검증과 남은 조건

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest scripts/test_omc_ad_native_supervisor.py -q
```

테스트는 Git 객체에서 고정 core를 복원하고 실제 승인·home requirement·CONTRACT를
등록한다. 공개 `run` 경유 S1 정상/S2 내부124/S3 외부timeout/S4 최종JSON없음을
실제 subprocess로 검증한다. 임의 내부 함수나 mock wrapper로 이를 대체하지 않는다.

B1~B6의 의도적으로 고장난 wrapper·대량/무개행 출력·입력 정체·저장 오류·신호·상속
스트림은 별도 감독 경계 검사다. 저장 오류는 테스트 소유 프로세스의 POSIX
RLIMIT_FSIZE로 유발한다. 테스트의 잔류 자식 정리는 감독 성공과 분리한다.
짧은 제한과 1초 이내 스케줄링 허용 오차를 사용하며 원문 시각도 대조한다.

설정된 1800/12초 제한, 합성 회귀의 실측, 실제 모델의 실행 시간은 다르다.
실제 Claude·광고 8프로필·완료 기록·보고 시간은 이번에 관측하지 않았다.
실제 광고 재검증에는 별도 리뷰·후보 확정·시간 배치 및 준비 조건·명시 요청이 필요하다.

## 절대 마감 모드의 종료 오류와 최종 기록

`--origin-monotonic`과 `--deadline-monotonic`으로 실행하는 부모 감독에 적용한다.
legacy 모드의 판정과 고정 core는 바꾸지 않는다. `span=deadline-origin`일 때 실행
마감은 `origin+1760*span/1800`, 정리 마감은
`min(stop+12*span/1800, origin+1772*span/1800)`이다. 모든 대상과 TERM/KILL은
같은 정리 예산을 공유하며 오류가 나도 새 시간을 부여하지 않는다. 정리 뒤부터
전체 마감까지는 최종 기록 구간이다. 신원 조회 후에도 남은 정리 시간을 다시
확인하고, 마감이 지난 신호 요청·추가 대기·저장 재시도를 하지 않는다.

부모의 종료 요청·회수 오류는 `runtime_errors`에 단계(`stage`), 연산(`operation`),
대상 종류와 PID/PGID/경로, 예외 종류, `errno`, 원문 메시지, UTC/KST/monotonic을
저장한다. 같은 대상·연산·오류가 반복되면 최초 원문을 유지하고 `occurrences`만
증가시킨다. 각 최초 오류는 stderr의 `supervisor_runtime_error:` JSON으로도 남긴다.
TERM/KILL은 `termination`, waitpid는 `reap`, 최종 저장은 `final_record` 단계다.
이 기록은 원래 OS 권한 거부의 정책·발생 원인까지 인증하지 않는다.

실행 중 오류는 입력·준비 오류로 바꾸지 않는다. 오류가 있으면 프로세스 결과는
`incomplete_or_failed`, 정리는 `unconfirmed`, `full_contract_pass=false`다. 자식을
나중에 확인하거나 테스트가 정리해도 원래 실패 결과를 성공으로 바꾸지 않는다.
기존 결과 schema와 소비자 `read_absolute_result`는 유지한다. 이 소비자가 실패
결과를 읽었다는 사실은 성공이 아니며, 호출자는 process/cleanup/full_contract_pass를
별도로 확인해야 한다. 실제 홈 `NOT_VERIFIED`와 비교 `HOLD`는 유지한다.

결과·봉인은 새 실행 전용 폴더에 exclusive 생성한다. 봉인은 실제 저장된 결과의
바이트 해시와 run_id를 연결한다. 봉인은 실행·정리·업무 성공을 증명하지 않는다.
결과 저장 실패 시 봉인을 만들지 않고 종료 1을 반환한다. 봉인 저장 실패 시 이미
저장된 결과는 유지하고 종료 1과 오류 원문을 남긴다. 불완전 JSON·봉인 누락·다른
실행 ID·해시 불일치·늦은 봉인은 기존 소비자에서 unknown이다. 최초 실행 오류는
후속 저장 오류로 덮어쓰지 않는다. 저장 완료 전 프로세스 사망 또는 쓰기 자체가
막히는 상황에서 결과·봉인 보존을 보장하지 않는다. SIGKILL·저장 장치 정지와 OS
스케줄링은 실시간 마감 보장 밖이며 stderr도 실패하면 내구성 있는 기록은 미확인이다.

직접 회귀는 별도 Python 프로세스에서 실제 공개 main·fork·감독·결과·봉인·소비자를
사용한다. 오류 주입은 테스트 소유 OS 연산만 교체한다. 운영용 오류 주입 옵션을
추가하거나 감독의 분기·저장·봉인 함수를 가짜로 바꾸지 않는다. 시간 경계 사례는
저장 연산이 반환하면서 시계가 마감을 넘는 상황을 결정적으로 만든다. 주입 회귀와
고정 core의 네 실제 subprocess 표면은 서로 다른 증거이며 합쳐서 실제 광고 성공으로
보고하지 않는다.

이번 보완 후보는 기존 5개 호환성 변경을 상속하지만 수정 범위는 감독·감독 테스트·
이 문서 3개다. 기존 5개가 있는 폴더와 인덱스·기록은 별도로 보존한다. 겹치는 감독
테스트는 상속 기준 대비 이번 차이로 검토한다. 기존 288통과/1실패의 전체 통합 결과는
보존하며 이번 국소 회귀로 대체하지 않는다. Claude·광고 비교·릴리스·재설치는 없다.

### 봉인 저장 후 실패의 소비 계약 보완

절대 마감 모드의 새 결과는 `finalization_protocol=pending-publication/v2`다.
기존 결과 schema는 유지하되, 결과·봉인 파일이 있다는 사실만으로 공개하지 않는다.

1. 새 출력 폴더에 `finalization.pending`을 exclusive 생성한다. 실패하면 결과·봉인을
   쓰지 않고 종료 1과 `write_pending` 원문을 남긴다.
2. 결과와 `omc-ad-absolute-seal/v2` 봉인을 저장·닫는다. 쓰기·flush·close 오류 또는
   감지된 마감 초과이면 pending을 남긴다. 이미 저장된 JSON은 삭제·덮어쓰기하지 않는다.
3. 실제 봉인 close가 반환한 뒤의 monotonic과 결과·봉인 원문 해시·run_id를
   `finalized.json`에 저장·닫는다. 이 단계의 오류·마감 초과도 pending을 남긴다.
4. 전체 마감 이내임을 다시 확인한 뒤 pending을 한 번만 unlink하여 공개한다.
   unlink 오류도 실패이며 재시도하지 않는다. 모든 단계가 기존 전체 예산을 공유한다.

새 `read_absolute_result`는 결과·봉인·finalized를 각각 한 번 읽은 동일 버퍼의 해시로
검증한다. pending이 남음(끊긴 링크 포함), finalized 누락·부분 JSON·링크·다른 run_id·
해시 불일치·완료 시각 범위 불일치는 unknown이다. finalized가 완전한 JSON이어도
close 오류가 나면 pending이 남아 unknown이다. 공개 이후에도 실행 실패 결과는
`incomplete_or_failed`이며 봉인·공개 성공을 업무 성공으로 승격하지 않는다.

v2 seal의 기존 `completed_monotonic` 필드는 null이다. 이전 v1 소비자는 새 결과를
unknown으로 읽으므로 새 봉인을 오래된 판독기로 정상 취급하지 않는다. 새 소비자는
과거 v1 결과·봉인은 기존 기준으로 읽는다. 새 결과를 사용하려면 이 감독과 함께
제공되는 소비자를 사용해야 하며 외부 소비자를 자동 교체하지 않는다.

마감 초과는 저장 뒤에도 확인한다. 마지막 공개 unlink는 마감 확인 직후 호출하는
원자적 OS 연산이다. 그 마지막 연산 자체의 무기한 지연·OS 정지·SIGKILL·외부 파일
변조까지 성공/종료 시간을 인증하는 프로토콜은 아니다. 기록 보존의 보장 한계는
그대로이며 새 마감이나 저장 재시도 예산을 추가하지 않는다.

직접 회귀는 실제 공개 main과 파일 쓰기·닫기를 통과한 뒤 close 오류/시계 경과를
주입한다. 감독의 판정·JSON 바이트·소비자는 대체하지 않는다. 봉인 close 오류·늦은
close, finalized close 오류·늦은 close, pending 생성·공개 실패 및 정상 공개를
구분한다. 고정 2abc549의 v1 소비자도 실제로 불러 새 결과 거부를 확인하며,
finalized 원문/시각/해시/실행 ID/링크 결함과 과거 v1 호환성은 별도로 확인한다.
기존 131개 통과 기록은 보존하고 이번 소스의 회귀 결과와 구분한다.
