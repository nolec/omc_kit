# confirmed 상태 훅 차단 버그

## 상황
`omc-pipeline-check.sh` 세션 체크에서 `status == "confirmed"` → 무조건 exit 1 차단.
`allow` 등록을 해도 이 블록에서 먼저 막혀서 우회 불가.

## 근본 원인
`confirmed`는 "이전 세션 완료"를 뜻하지만 "현재 파이프라인 작업 중"도 같은 값.
`contract_confirmed`는 `.omc/pipeline_session.json`에 있는데 훅이 `latest.json`만 읽었음.

## 적용된 규칙
`pipeline-check.sh`는 `latest.json`(status)과 `pipeline_session.json`(contract_confirmed)을 함께 읽어야 한다. confirmed+contract_confirmed=True → 허용, confirmed+contract_confirmed 없음 → 차단.

태그: 훅,pipeline,confirmed,버그
