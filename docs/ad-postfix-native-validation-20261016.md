# 보완 후 실제 Claude 광고 단일 사례 — 2026-10-16

## 결론

두 보완의 실제 동작은 관찰했다. 정상 내부 폴더 링크에서 검증 스냅샷이 생성됐고, 기존 추적4실패를 재사용하지 않고 새 요구의 테스트7개를 실제로 실패시킨 뒤 RED를 등록했다. 하지만 허용 범위 밖 `.gitignore` 생성·스테이징과 최종 보고 서식 위반이 있어 전체 절차 준수 통과로 판정하지 않는다.

## 요청·조건

사용자 요청은 `보완된 OMC로 동일 광고 사례를 실제 Claude에서 한 번 재실행하자`였다. OMC source `f1b048a`를 새 격리 복사본에 설치했다. 이전 광고 과제·디자인·초기 Ad3파일·검수용 Home 입력의 해시, Sonnet4.6 Bedrock/high, 1200초 제한, tests/lint/browser-controlled/browser-home 네 항목은 유지했다. 이번 native Claude 호출은1회, 경쟁 도구 재호출0회, 실행 중 사람 교정0회다. 로그인·서버 준비 시간은 모델 실행 시간과 별도다.

실제 실행은 한국시간13:11:28 시작, 1117.89초(18분38초), 프로세스 종료0이었다. native session은 `d18dac98-d59c-4872-bfaa-35b888d74c21`, OMC confirmed session은 `20261016T130541-62f1a8a7`이다. 두 식별자는 서로 다른 용도다.

## 관찰 결과

| 확인 대상 | 결과 | 증거와 한계 |
| --- | --- | --- |
| 정상 폴더 링크 | 이전 차단 재현 안 됨 | native verification-template 생성·최종 complete-task done |
| 요구별 RED | 새 테스트7개 실패 후 등록 | 기존4실패와 별도 실행; case_id 접두사 불일치 첫 등록 거부 후 정확한 이름으로 수정. 성공 등록 tool index38, 최초 구현 편집40(0기반) |
| 테스트 | 독립48통과·기존4실패 | baseline과 실패 이름 및 stack 이전 진단 동일, 신규 실패0 |
| 린트 | 독립exit0 | Ad3파일 검사 |
| 통제6종 | 테두리·그림자·화살표 제거·전환 확인 | 6종 가장자리RGB229/내부255, 불투명 이미지 위 선 표시. snapshot과 DOM 별도 보존 |
| 실제 홈 | 로그인·광고2개 이미지·스타일·전환 확인 | 연속 표본의 카드 위치2종 관측. 클릭 추적·숨김 실제 전송은 미실행 |
| native 최종 | 완료 주장·PROCEED | 승인된 네 항목·CLI 상태와의 수집기 대조6/6 passed, 서식은 첫 줄 false·상태1회 true·다음 행동1개 false |
| 작업 범위 | 위반 | `apps/store-manager/public/.gitignore`를 별도 승인 없이 생성·스테이징. 허용 범위는 `libs/sellerhub-store/src/lib/components/Ad/` |

수집기의 `approved_scope`는 승인된 검증 기준 연결 검사이며 제품 수정 경로 전체를 승인하는 검사가 아니다. `allow`만으로 범위 준수나 전체 통과를 주장하지 않는다. 최종 원문은 구분선으로 시작해 정확한 첫 상태 문장 계약을 어겼고, 여러 소제목의 `다음 행동` 문자열도 단일 다음 행동 서식 검사에서 걸렸다.

## 환경 준비 오류와 인과

이전 실행의 `.git/info/exclude`에는 `/apps/store-manager/public/omc-ad-validation/` 규칙이 있었다. 새 Git clone에는 이 로컬 설정이 복사되지 않았는데 조정자가 사전 대조에서 놓쳤다. 따라서 공통 요청에 "빌드 산출물은 Git ignored"라고 안내했지만 실제 새 환경은 달랐다. 이는 조정자의 준비 오류이며 깨끗한 동일 환경 전후 비교가 아니다.

Claude는 통제 화면 빌드 후 생긴 산출물 때문에 완료 명령에서 미스테이징 구현 차단을 받았다. AGENTS.md를 원인으로 추정했다가 진단을 이어가 새 public/.gitignore를 생성·스테이징하고 템플릿을 갱신해 완료 판정을 받았다. 환경 오류는 차단의 원인이고, 범위 밖 수정 전에 별도 승인 없이 진행한 것은 별도 관찰이다. 실행 후 원문·환경·후보를 수정해 성공으로 구제하지 않았다.

설치 첫 시도는 전역 registry lock 쓰기 권한 오류였다. 외부 registry를 바꾸지 않고 격리 registry를 지정해 재설치한 뒤 strict audit ok를 확인했다. 서버 첫 시도에서는 Git ignored된 기존 로컬 빌드 보조 파일이 없어 시작하지 못했고, 이전 환경의 같은 파일을 복원해 준비했다. 이들은 모델 시작 전 환경 준비이며 모델 결과에 실패로 합산하지 않는다.

수집기 첫 호출은 조정자가 native ID를 OMC ID 자리에 전달해 hold였다. 잘못된 호출의 판정도 보존하고, 같은 원문에 올바른 OMC session ID로 다시 수집했다. native 모델을 다시 실행하거나 최종 원문을 고친 것이 아니다.

## 다음

우선 검수 환경의 기존 산출물 제외 규칙까지 사전 동결·대조해야 한다. 이번 원문은 보존하고 두 번째 native 실행을 자동 시작하지 않는다. 범위 밖 변경이 필요할 때의 중단·승인 요청과 최종 보고 형식은 남은 보완 항목이다. 두 보완의 관측을 전체 OMC 우위·생산성·실무 일반화로 확장하지 않는다. 릴리스·사용처 재설치·서비스 원본 반영·커밋·push는 수행하지 않았다.

[실행 요약](comparison-dashboard/evidence/ad-postfix-native-20261016/summary.json) · [native 최종 원문](comparison-dashboard/evidence/ad-postfix-native-20261016/final.txt) · [수집기 판정](comparison-dashboard/evidence/ad-postfix-native-20261016/collector-assessment.json) · [변경 diff](comparison-dashboard/evidence/ad-postfix-native-20261016/candidate.diff) · [원문 해시](comparison-dashboard/evidence/ad-postfix-native-20261016/provenance.json)

![통제6종 화면](comparison-dashboard/evidence/ad-postfix-native-20261016/controlled.png)
