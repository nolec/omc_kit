# 서명 corpus와 runtime corpus는 manifest bridge로 연결한다
날짜: 2026-08-05
태그: plan-benchmark,signature,runtime-bridge

## 증상
독립 gold는 공개 corpus hash에 서명됐지만 runtime validator는 context 포함 corpus hash를 요구했다

## 원인
서명 원본과 실행 입력이 서로 다른 표현이라는 경계를 계약에 명시하지 않았다

## 적용된 규칙
서명 원본은 변경하지 않고 confirmatory manifest가 source_corpus_sha256과 runtime corpus hash를 함께 서명해야 한다

## 검증 커맨드
pytest scripts/test_omc_plan_runtime_pilot.py -q
