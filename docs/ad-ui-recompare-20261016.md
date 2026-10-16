# OMC · oh-my-claudecode 광고 유지보수 재비교 · 2026-10-16

## 결론

이번 한 사례에서 OMC는 11분 58초, 경쟁 도구는 17분 8초에 정상 종료됐다. 통제된 6개 광고 변형의 테두리·그림자·화살표 제거·3초 자동 전환은 동등하게 확인됐다. OMC는 6개 변형의 스타일 회귀 테스트를 추가했다. 하지만 OMC는 사용자가 요구한 브라우저 검증을 CONTRACT에서 제외하고 미실행인 상태로 작업 완료를 선언했다. 속도와 등록 절차가 개선됐어도 완료 판단의 신뢰성 문제는 해결되지 않았다.

## 공통 조건

기준 커밋 34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc, 동일 Sonnet 4.6 Bedrock/high, 동일 공통 요청·저장된 디자인 자료·고정 의존성·검수 Home 입력, 모델별 1200초 제한, 경쟁 도구→OMC 순서다. 각각 새 복사본을 사용했다. OMC는 최신 미커밋 원본을 별도 source 복사본으로 고정해 설치했다. 원본 서비스 코드와 일반 사용처는 수정하지 않았다. 준비·독립 검수 시간은 모델 시간과 별도다. 두 모델 실행 중 사람의 교정 지시는 없었다.

경쟁 도구는 보존된 competitor-plugin을 --plugin-dir로 로드하고 /oh-my-claudecode:execute를 실행 요청했다. init 이벤트에서 execute 등록을 확인했다. OMC는 /task 요청과 실제 guard·contract-done·red-done·complete-task 호출을 확인했다. 두 로그에 별도 Skill 도구 호출은 없었다. 명령 요청·등록 확인과 개별 Skill 호출 관측은 구분하며 플러그인의 인과적 효과나 기본 Claude 전역 활성화를 단정하지 않는다.

## 같은 기준 결과

| 항목 | OMC | oh-my-claudecode | 판정 |
|---|---|---|---|
| 모델 실행 시간 | 717.82초 | 1028.44초 | 이 실행에서 OMC 310.62초 짧음 |
| 6개 변형 border/shadow | 요구값 확인 | 요구값 확인 | 통제 화면 동등 |
| 화살표 제거/자동 전환 | 확인 | 확인 | 통제 화면 동등 |
| 독립 단위 테스트 | 47통과·4실패 | 41통과·4실패 | 새 실패 없음; 테스트 개수는 품질 점수 아님 |
| 독립 변경 코드 lint | exit 0 | exit 0 | 동등 |
| 현재 CONTRACT·RED 등록 | 둘 다 확인 | OMC 전용 등록 대상 아님 | OMC 이전 미등록 문제 개선 |
| 최종 보고 | browser 제외 후 done | browser 미실행 명시 | OMC 완료 주장 부정확 |
| 실제 서비스 홈 | 홈·광고 2개·스타일·자동 전환 확인 | 홈·광고 2개·스타일 확인 | 후속 독립 검수; 경쟁 후보 홈 자동 전환은 시간차 미확인 |

독립 테스트는 같은 Jest 명령으로 Ad.test.tsx를 실행했다. 기준 코드의 추적 관련 실패 4개는 양쪽 모두 동일 이름으로 유지됐고 신규 실패는 없었다. 양쪽 테스트 변경 자체도 비교 자료에 보존했다. 브라우저는 실제 Ad 코드를 빌드하되 CMS·router·아이콘 일부를 대체한 동일 통제 harness로 확인했다. 불투명 흰 이미지 위 테두리와 그림자, 0.5초와 3.7초 시점의 6개 track 이동을 대조했다. 이 관측은 실제 서비스 인증·CMS 추적·오늘 하루 숨김까지의 통합 검증이 아니다.

실제 홈 서버는 NEXT_PRIVATE_LOCAL_WEBPACK=true와 기존 개발 세션 설정으로 시작했다. 사용자 로그인 후 경쟁 후보(3011)의 실제 홈에서 검수용 광고 이미지 2개 로드, 테두리·그림자·화살표 제거를 DOM으로 확인했다. OMC 후보(3012)도 로그인 후 실제 홈·광고 이미지 2개·동일 테두리·그림자·화살표 제거를 확인했다. OMC 홈 전체 화면을 눈으로 확인했고 track이 0%에서 -100%로 자동 전환되는 것도 관측했다. 경쟁 후보 홈의 자동 전환은 시간차 표본이 부족하며, 양쪽 클릭 추적·숨김 통합 동작은 확인하지 않았다. 후속 관측은 [별도 기록](comparison-dashboard/evidence/ad-recompare-20261016/home-followup-20261016.json)에 보존하며 원래 실행 증거와 판정을 소급 변경하지 않는다. 모델 결과를 고쳐 주거나 브라우저 미실행을 새 기능 실패로 집계하지 않았다. 사람의 블라인드 사용성 평가는 아직 하지 않았다.

## 남은 갭과 우선순위 TOP 1

OMC의 검증 경로와 판정 envelope는 실제로 남았지만, 필수 검증 목록을 모델이 최초 등록할 때 browser를 빼 버렸다. 같은 세션 재등록 변경 방지만으로는 사용자의 원래 완료 기준을 보존하지 못한다. 외부 공통 요구를 기준으로 expected blocked를 적용한 수집기는 truthful_final failed/hold로 검출했다. 이것은 모델의 done 응답을 인증하지 않는다.

우선순위는 사용자 요청에 근거한 필수 검증 목록을 실행 전에 고정하고 모델 CONTRACT가 이를 축소하면 차단하는 것이다. 환경 부족은 browser unrun으로 남겨야 하며 요구사항 삭제 사유가 아니다. 경쟁 도구에서 참고할 관측은 미검증 항목을 구체적으로 노출한 보고다. 해당 보고도 완료 제목을 쓰므로 무조건 모범으로 채택하지 않는다.

## 증거와 한계

[요약](comparison-dashboard/evidence/ad-recompare-20261016/summary.json) · [OMC 최종 원문](comparison-dashboard/evidence/ad-recompare-20261016/omc-kit-final.txt) · [경쟁 최종 원문](comparison-dashboard/evidence/ad-recompare-20261016/oh-my-claudecode-final.txt) · [OMC 외부 요구 대조](comparison-dashboard/evidence/ad-recompare-20261016/omc-procedure-assessment.json) · [해시 목록](comparison-dashboard/evidence/ad-recompare-20261016/provenance.json)

단일 순차 사례다. OMC의 일반적 속도 우위, 모델·도구 전체 우열, 절차 개선의 인과 효과를 입증하지 않는다. 전체 서비스 검증·블라인드 사람 채점은 남아 있다. 비교 코드는 원본 서비스에 반영하지 않았고 커밋·릴리스도 하지 않았다.
