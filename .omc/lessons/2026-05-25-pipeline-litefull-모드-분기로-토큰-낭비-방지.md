# pipeline LITE/FULL 모드 분기로 토큰 낭비 방지
날짜: 2026-05-25
태그: pipeline,token,lite,full,mode

## 증상
fix/ 브랜치 단순 수정에도 plan+critique 스텝을 실행해 토큰을 낭비했다

## 원인
cmd_pipeline에 모드 분기 없이 무조건 FULL 경로를 탔다

## 적용된 규칙
브랜치 prefix(fix/hotfix/chore/docs) 또는 짧은 지시문은 LITE, feat+긴지시문은 FULL, --mode로 override 가능

## 검증 커맨드
pytest scripts/test_omc_pipeline_mode.py -v
