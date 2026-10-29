# OMC 비교 대시보드 · 최신 기준 2026-10-29

## 현재 · e3f34cbb main 반영·7개 파일 승인 재결속 및 반영 리뷰 완료 · 2026-10-29

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: e3f34cbb main 반영 완료 · 구현7개 승인 재결속 READY·반영 결과 리뷰 APPROVE · 파이프라인 완료 NOT_ASSESSED · 실제 비교 HOLD
- 목표 대비 진행: 검증된 동일 커밋 이관의 완료 판정을 구현·로컬 검증·리뷰·커밋·main 반영까지 연결했다. 불필요한 RED 차단을 분리하고 기존 스테이징 및 승인된 폴더 추가·삭제의 보존 검사를 보완했다. 실제 업무 완료·개입 감소·생산성·경쟁 우위는 아직 미입증이다.
- main·문서 기준: 통합 구현 커밋은 e3f34cbb27a940016491d202f90de06047c3d151이며 부모 순서는 문서287163358552bf208ad9c82f7c79d9c6ad8dd641, 구현dc65353a5d952d0e0c1cc7955eef8dfd5cb34c28이다. 문서4개의 내용·권한을 보존하고28716335 대비 구현7개만 반영했다. 두 커밋의 계보를 유지했으며 이번 문서4개 최신화·커밋은 구현 승인과 별도 범위다.
- 반영한 구현: 가드·수집기·직접 테스트2개·task 템플릿3개 등 승인7개는 dc65353의 blob·권한과 일치한다. 최초 승인 종류·이관 전 기준·원래 구현의 검증/RED·정확한 커밋/범위/보존 증거가 확인되는 새 이관만 별도 판정한다. 기존 구현 RED 경로는 유지한다. 범위 밖 스테이징과 무관한 파일·원래 빈 폴더·권한은 보존하고 승인 파일의 필요한 부모 추가와 승인 삭제로 비는 부모 제거만 구분한다.
- 검증: 기존 통합 회귀320개 통과·실패0·생략0, 독립 코드 리뷰의 직접 회귀7개 통과 기록을 유지한다. 두 수치를 합산하지 않는다. 이번 main 반영·반영 리뷰에서는 새 테스트·Claude·광고 비교를 실행하지 않았다. Git 계보·frozen diff·blob·권한·승인 범위·인덱스·원문 보존 대조를 수행했다.
- 승인과 반영 리뷰: 기존 승인 포인터·receipt·snapshot·리뷰 원문을 포함한 격리 예행연습 후 원본 main 반영과7개 승인 재결속 READY를 확인했다. 기준28716335·후보e3f34cbb의 scope는 a597fa5f1cb773cd87edfb85965f707c705ccc09e864402d51bcedd81154d6c3이다. 원본 c05344d13b324412aa857cf6c93bf118 receipt에 연결됐다. 후속 반영 리뷰는 APPROVE이며 원본 인덱스·포인터를 변경하지 않고 독립 후보에만 검토 승인을 추가했다. 이번 문서4개·원본 전체·출하/릴리스 승인은 포함하지 않는다. READY는 e3f34cbb 고정 구현 후보 기준의 확인 결과이며 이후 문서 커밋을 포함한 새 HEAD 전체의 출하 승인을 뜻하지 않는다.
- 종료 판정 경계: main 반영·7개 승인 재결속은 완료다. 파이프라인 완료는 NOT_ASSESSED로 별도 기록하며 complete-task·complete-transfer·이관 기준 등록·복구 발급은 호출하지 않았다. 이전 e0b9195 이관 당시 일반 완료 가드의 RED 부재 BLOCK 원문은 유지하고 소급 완료 처리하지 않는다.
- 보존 경계: 원본 인덱스 원문·이전 승인 포인터 원문을 별도 보관했다. 승인7개 엔트리 전환 외 인덱스 항목과 모든 플래그, 문서4개, 기존 receipt·snapshot·검증 원문·권한 및 다른 후보 스테이징을 확인했다. 당시 허용 변경을 제외한12509개 대조는 일치했다. 과거 임시 원문13개·16개 미확인과 설치 기존 실패1개는 미해결로 유지하며 모든 과거 자료의 완전 보존을 주장하지 않는다.
- 실제 검증 경계: 실제 홈 NOT_VERIFIED·비교 HOLD를 유지한다. 이번 반영·리뷰·문서 작업의 완료 가드0회·실제 이관 기능0회·실제 복구 발급0회·Claude0회·비교 재실행0회다. 과거 경쟁 도구1회·OMC0회,337.60초 중단·당시 자식 종료 unconfirmed,광고 추적4개 기존 실패·클릭/숨김 통합 미검증은 당시 이력으로 보존한다.
- 다음 작업: 남은 검수 실행기 호환성4개와 e3f34cbb 기반의 고정 버전·반영 범위 확정
- 후속 경계: 구현7개 커밋·main 반영·승인 재결속 단계는 종료했다. 별도 검수 실행기 호환성4개를 자동 채택하지 않는다. 이후 실제 Ad 비교 재개는 양쪽 고정 버전·공통 입력·필수7검증·시간 배치·실패 시 종료 조건을 새로 계획·검토·승인한 뒤 결정한다. 이전 모델 실행 승인과 시간 예산은 자동 승계하지 않는다.
- 배포·관찰: e3f34cbb 보완은 미릴리스·일반 사용처 미설치다. 정상11개 사용처의0.6.1 설치·엄격 감사는 과거 이력이며 이번 재감사는 없다. v3 DISABLED; OBSERVATION_INVALID / live_observation_not_enabled로 관찰은 미집계다. push·태그·릴리스·재설치 없음.
- 근거: /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-apply-dc65353-20261029/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-review-dc65353-20261029/audit-result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/verified-transfer-commit-20261029/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/verified-transfer-approved-20261029/review-result.json. 로컬 원문 보관 자료이며 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · e0b9195 main 반영·동일 커밋 이관 후보7개 리뷰 승인 · 2026-10-29

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: e0b9195 main 반영 완료 · 동일 커밋 이관 완료 판정 후보7개 리뷰 APPROVE·결속 READY · 구현 후보 미커밋·main 미반영 · 실제 비교 HOLD
- 목표 대비 진행: 검사 종료부터 최종 기록·보관까지 잇는 실행기를 main에 반영했다. 새 이관 판정 후보는 검증된 동일 커밋을 옮길 때 새 RED가 없다는 이유만으로 차단하지 않도록 구현·리뷰했다. 로컬 구현·증거 정합성 개선이며 실제 업무 완료·개입 감소·생산성·경쟁 우위는 아직 미입증이다.
- main 기준: 최신 구현 커밋은 e0b9195970bcf920f05f5a4d4c72741756077ee6이다. 실행기·직접 테스트·README 3개가 반영됐고 원본 리뷰 결속 READY를 확인한 이력이 있다. 동일 버퍼 복사·해시 연결, 부분 복사 실패 증거 보존, 검사 종료부터 최종 기록·보관과 마감 초과 시 가드 미호출을 다룬다. 당시 직접 회귀35개 통과·생략0이며 이번 문서 작업에서는 재실행하지 않았다.
- 별도 구현 후보: 동일 커밋 이관 완료 판정7개는 별도 후보에만 존재한다. 최초 승인 종류·이관 전 기준 등록·원래 구현의 검증/RED·정확한 커밋/승인 범위·내용/권한·보존 증거가 확인될 때만 새 이관의 RED 요구를 제외한다. 기존 구현 경로의 RED 요구는 유지한다. 무관한 기존 스테이징을 보존하고 승인 파일의 새 부모 폴더 및 승인 삭제로 비는 부모 폴더를 구분한다. 무관한 파일·원래 빈 폴더 삭제와 권한 변경은 계속 차단한다.
- 검증: 최종 통합 회귀320개 통과·실패0·생략0. 독립 리뷰에서 삭제 처리 직접 회귀7개를 재실행해 통과했다. 과거303개·313개 및 직접7개를320개와 합산하지 않는다. 구현 후보의 수정2개만 스테이징됐고 나머지 상속5개는 미스테이징 상태다. 최종 TDD·완료 가드는 통과했으며 이번 문서 최신화는 코드 회귀 재실행이 아니다.
- 리뷰와 승인 범위: 고정 snapshot d23da60c0a31b7d2475d4b3beddbe3f18b2152f5e8b98fac9da083c87440c096의7개 파일 전체 APPROVE·결속 READY다. scope b6532ed5652526f4d9de0123a97276ff6660ee95a1d1c7b30475e2c92f938601. 이번 문서4개·원본 전체·출하/릴리스 승인은 포함하지 않는다.
- 기존 중단 기록: e0b9195의 main 이관과 승인 재결속은 성공했지만 당시 운영 작업의 일반 complete-task는 새 RED 부재로 BLOCK이었다. 그 원문은 보존하며 이번 후보 구현으로 과거 완료 판정을 소급 변경하지 않는다. 새 이관 판정은 앞으로 사전 등록한 작업에만 적용된다.
- 보존 경계: 최신 구현·리뷰 당시 기존 변경·스테이징·기록8052개와 현재 구현 후보 인덱스 원문 보존을 확인했다. 이는 당시 명시된 보호 대상이며 모든 과거 자료의 완전 보존 주장이 아니다. 과거 임시 원문13개·16개 미확인, 기존 설치 실패1개는 유지한다.
- 실제 검증 경계: 이번 보완·문서 작업의 실제 Claude 호출0회·광고 비교 재실행0회·실제 복구 발급0회다. 실제 홈 NOT_VERIFIED와 비교 HOLD를 유지한다. 과거 경쟁 도구1회·OMC0회,337.60초 중단·당시 자식 종료 unconfirmed,광고 추적4개 기존 실패·클릭/숨김 통합 미검증은 당시 이력으로 남긴다.
- 다음 작업: 승인된 동일 커밋 이관 완료 판정7개 파일 커밋 및 커밋 후 승인 재결속
- 후속 경계: 7개 구현을 커밋할지 별도로 결정한다. 이후 main 반영·이관 검증과 남은 검수 실행기 호환성4개 채택 여부를 각각 확정한다. 실제 Ad 비교 재개는 고정 버전·공통 입력·필수7검증·시간 배치·종료 조건을 새로 계획·승인한 뒤 결정하며 이전 실행 승인·예산을 자동 승계하지 않는다.
- 배포·관찰: 이번 보완은 미릴리스·일반 사용처 미설치다. 정상11개 사용처의0.6.1 설치·엄격 감사는 과거 이력이며 이번 재감사는 없다. v3 DISABLED; OBSERVATION_INVALID / live_observation_not_enabled로 품질 관찰은 미집계다. push·태그·릴리스·재설치 없음.
- 근거: /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-apply-e0b9195-20261028/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/integration-buffer-fix-20261027/commit/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/verified-transfer-approved-20261029/review-result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/verified-transfer-approved-20261029/staged-gates.json. 원문 보관본과 후보7개 내용·권한을 별도 보존했다. 원래 후보 경로·당시 승인을 유지하며 보관 폴더 자체를 새 승인이나 main 반영으로 취급하지 않는다. 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 6a286d3 main 반영·3개 파일 승인 재결속 완료 · 2026-10-27

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 6a286d3의 승인된 3개 main 반영 완료 · 반영 후 코드 리뷰 APPROVE · 3개 파일 승인 재결속 READY · 실제 비교 HOLD
- 목표 대비 진행: 종료 권한 오류의 위치와 실패 결과를 남기고, 봉인 저장 후 오류·마감 초과가 정상 결과로 읽히는 경로를 보완했다. 구현·국소 회귀·main 반영·코드 승인까지 확인했으며 실제 업무 완료·개입 감소·생산성·경쟁 우위는 아직 미입증이다.
- 원본과 별도 후보: 원본 main에 포함된 최신 구현 커밋은 6a286d364911ceb0dcf9511d8112622be1af49af이며 기준 0470847abc2b0d90eaa774395c3770a0a134f0f1에서 fast-forward 반영됐다. 감독·감독 테스트·감독 README 3개만 포함한다. 별도 5개 호환성 후보의 겹치는 감독 테스트 상속 변경은 이 커밋에 포함됐지만, 검수 실행기·실행기 테스트·manifest·실행기 README 4개는 이번 반영 범위 밖이다. 기존 후보의 3개 스테이징과 5개 변경은 보존했다. 이번 문서 4개는 코드 승인과 별도 범위다.
- 이번 개선: 종료·회수 오류의 단계·연산·PID/PGID·errno·신호·원문·시각을 기록하고 실패 결과와 봉인을 남긴다. 새 결과는 pending 표시와 v2 봉인·finalized 기록을 함께 확인한다. 저장·닫기 오류나 감지된 마감 초과에서는 pending이 남아 소비자는 unknown으로 읽는다. 결과·봉인 해시는 각각 읽은 동일 버퍼에 연결하며 이전 v1 소비자도 새 v2 결과를 정상으로 오인하지 않는다. 기존 실행·정리·전체 마감 예산은 늘리지 않는다.
- 검증: 최종 감독 회귀 148개 통과·실패0·생략0(42.55초), 문법·TDD·완료 가드·커밋 훅을 확인했다. 제한 환경의 145개 통과·3개 실패는 프로세스 신원 조회 차단 때문에 발생한 실행 제약 기록으로 보존하고, 동일 소스를 조회가 허용된 환경에서 148개 통과한 결과와 구분한다. 이전 131개와 중복 합산하지 않는다. 이번 main 반영·리뷰·문서 최신화에서는 테스트·Claude·비교를 재실행하지 않았다.
- 리뷰와 승인 범위: 원본 main의 6a286d3와 동일한 별도 커밋 후보를 기준 0470847 대비 고정해 3개 파일을 새로 리뷰했다. frozen diff·커밋 blob·권한·요청 범위 일치, APPROVE와 결속 READY를 확인했다. 승인은 해당 3개 파일에만 적용하며 이번 문서4개·나머지 호환성4개·원본 전체·출하/릴리스 승인으로 확대하지 않는다.
- 중단과 후속 검토: 원본 반영 직후 기존 operator의 리뷰 역할 전환은 completion preserve does not match pending baseline으로 실패했다. 당시 중단·후속 스냅샷 기록을 보존했고, 이후 명시적 새 리뷰에서 독립 후보의 승인 재결속을 완료했다. 중단된 세션 자체를 초기화·복구하거나 원인 해결로 판정한 것은 아니다.
- 보존 경계: 반영 전 원본 인덱스 원문은 별도 보관했다. 원본 인덱스의 승인된3개 항목 교체와 플래그 유지를 확인했고, 해시 변경 예외는 원본 인덱스에만 적용했다. 다른 후보 인덱스·기존 스테이징·상속 테스트·5개 변경·기록은 보존했다. 원본 반영 대조626개와 후속 리뷰 대조736개는 각각 당시 보호 대상 수다. 과거 임시 원문13개·16개 미확인 및 기존 설치 실패1개는 그대로 남기며 모든 과거 자료의 완전 보존이나 실패 해결을 주장하지 않는다.
- 실제 재검증: 이번 보완 이후 실제 Claude 호출0회·광고 비교 재실행0회·실제 복구 발급0회다. 실제 홈 NOT_VERIFIED와 비교 HOLD를 유지한다. 과거 로그인 없는 실제 Ad 비교의 경쟁 도구1회·OMC0회,337.60초 중단·당시 자식 종료 unconfirmed,광고 추적4개 기존 실패·클릭/숨김 통합 미검증은 당시 이력으로 보존한다. 봉인·공개 성공은 실제 광고 업무나 경쟁 비교 성공을 뜻하지 않는다.
- 다음 작업: 남은 검수 실행기 호환성4개와 최신 감독의 고정 버전·호환성·반영 범위 확정
- 다음: 기존3개 코드의 main 반영·승인 재결속 단계는 종료했다. 별도 호환성4개를 자동 채택하지 않고, 비교 재개에 필요한 core·검수 실행기·감독 버전과 공통 입력·필수7검증·시간 배치·실패 시 종료 조건을 별도 계획에서 결정한다. 이전 모델 실행 승인이나 예산은 자동 승계하지 않으며 실제 홈 연동은 별도 미검증으로 유지한다.
- 배포: 6a286d3 보완은 미릴리스·일반 사용처 미설치다. 정상11개 사용처의0.6.1 설치·엄격 감사는 과거 이력이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: 새 고정 리뷰 후보의 v3 DISABLED. 완료 품질 관찰은 OBSERVATION_INVALID / live_observation_not_enabled다. 코드 승인·main 반영과 사용자 효과·경쟁 비교 판정을 구분한다.
- 근거: /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-apply-6a286d3-20261027/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-6a-review-20261027/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/seal-finalization-fix-20261027/evidence/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/seal-finalization-fix-20261027/staged-gates/result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/seal-finalization-fix-20261027/commit/result.json; 로컬 보관 자료이며 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 2abc549 main 반영·보존 검토 완료 · 2026-10-27

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 2abc549의 승인된 9개 main 반영 완료 · 원본 반영 리뷰 APPROVE · 기존 코드 승인 READY · 실제 비교 HOLD
- 목표 대비 진행: 파이프라인 작업 수명·완료 판정 재승인 계약(18bea62)과 공통 검증·절대 마감 감독 보완(2abc549)을 원본 main에 반영했다. 구현·로컬 회귀·반영 검증은 진행됐지만 실제 업무 완료·생산성·경쟁 우위는 아직 미입증이다.
- 원본과 별도 후보: 최신 구현 기준은 2abc549b16a2ca0c40484bd95828861c5cd432b5이며 원본 main에 fast-forward로 반영됐다. 기존 9개 스테이징은 해당 커밋에 포함돼 해소됐고 이전 내용과 인덱스 원문은 별도 보관했다. 새 구현 커밋은 만들지 않았다. 문서4개는 구현9개와 별도 범위로 관리한다.
- 이번 개선: 새 독립 작업에서만 시작 시각을 갱신하고 같은 작업의 역할 전환은 유지한다. 만료와 증거 누락을 구분하고 작업별1회·8시간의 완료 판정 전용 재승인 계약을 추가했다. 공통7검증의 실행 위치·필수 환경·원문 증거와 감독의 준비/실행/정리/최종 기록 마감을 연결했다. 준비 기록의 실행·worker 식별과 완료 시각을 확인하며 읽은 뒤 관측 시각으로 정상 공개의 오판을 막는다.
- 검증: 원본 반영의 격리 예행연습·직접 수용 검사1개·TDD·완료 가드를 통과했다. 독립 반영 리뷰에서 고정 diff와 실제 커밋,9개 내용·권한·승인 범위,이전 인덱스9개 항목,문서4개·잔존 기록·다른 인덱스를 재확인했다. 과거 감독113개·직접 경계8개,관련215개/미요청 외부 통합48개 생략,통합212개,18bea62 통합293개는 각각 당시 범위의 근거이며 중복 합산하지 않는다. 이번 반영·리뷰·문서 작업은 제품 회귀·브라우저 실제 홈·Claude 업무 성공의 재검증이 아니다.
- 리뷰와 승인 범위: 별도 고정 후보의 기존9개 코드 승인 연결 READY를 재확인했고 원본9개와 내용·권한이 일치한다. 이번 원본 반영 검토는 읽기 전용 APPROVE이며 기존 코드 승인 receipt를 덮어쓰거나 새로 발급하지 않았다. 승인 범위를 원본 전체·문서4개·출하/릴리스로 확대하지 않는다.
- 보존 경계: 원본 인덱스 원문·이전9개 파일·문서4개 보관본과 현재 잔존 기록·다른 인덱스는 대조했다. 이전 임시 폴더2곳의 원문은 각각13개·16개가 미확인으로 남는다. 최신9개 코드와 RED·회귀·TDD·완료 출력은 해시 일치 보관본 및 현재 승인 연결로 확인했다. 따라서 모든 과거 기록·임시 스테이징을 현재까지 완전히 보존했다는 주장은 하지 않으며 누락 상태를 소급 복원·발급하지 않는다.
- 실제 재검증: 이번 반영·리뷰·문서 작업의 실제 복구 발급0회·Claude 호출0회·광고 비교 재실행0회다. 실제 홈 NOT_VERIFIED와 비교 HOLD를 유지한다. 과거 로그인 없는 실제 Ad 비교의 경쟁 도구1회·OMC0회,337.60초 중단·당시 자식 종료 unconfirmed,기존 설치 실패1개·광고 추적4개 기존 실패·클릭/숨김 통합 미검증은 당시 이력으로 남긴다.
- 다음 작업: 통합된 후보로 로그인 없는 실제 Ad 동일 조건 비교를 재개할 범위와 종료 조건 확정
- 다음: 원본 반영 단계는 종료했다. 실제 Ad 비교 재개 여부를 별도 계획에서 결정하고 양쪽 도구의 고정 버전·공통 입력·필수7검증·시간 배치·실패 시 종료 조건을 확정한다. 이전 모델 실행 승인이나 시간 예산은 자동 승계하지 않는다. 실제 홈 연동은 별도 미검증으로 유지한다.
- 배포: 최신 보완은 미릴리스·일반 사용처 미설치다. 정상11개 사용처의0.6.1 설치·엄격 감사는 과거 이력이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: 고정 코드 후보의 v3 관찰 DISABLED. 코드 승인·원본 반영과 실제 사용자 효과·경쟁 비교 판정을 구분한다.
- 근거: /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-apply-2abc549-20261027/final-result.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-apply-2abc549-20261027/review-main-transfer.json; /Users/noseunglae/Downloads/dev/omc_kit/.omc/state/main-apply-2abc549-20261027/archive-custody.json; /Users/noseunglae/.codex/worktrees/seven-file-integration-r1r2/omc_kit/.omc/state/nine-final-review-18bea62-20261026/post-commit-result.json; 로컬 보관 자료이며 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 종료 기록·고정 원문 보완 및 원본 리뷰 재결속 · 2026-10-25

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 7bdbcb8 보완10개 커밋 · 원본 리뷰 APPROVE·결속 READY · 설치 기존 실패1개·비교 HOLD
- 목표 대비 진행: 로그인 없는 실제 Ad 독립 화면의 비교는 경쟁 도구1회·OMC0회에서 중단됐다. 요구 파일 경로 혼동과 300초 제한, 셸·Claude 종료 근거 혼동을 보완하고 고정 core 원문을 분리해 7bdbcb8로 커밋했다. 구현·로컬 회귀·원본 리뷰 재결속까지 완료했으며 실제 업무 완료·생산성·경쟁 우위는 아직 미입증이다.
- 실제 재검증: 비교 HOLD: 7bdbcb8 이전 로그인 없는 실제 Ad 비교에서 경쟁 도구1회·OMC0회. 경쟁 플러그인 활성화와 execute 호출·반환은 관측했지만 RED·코드 수정·최종7검증을 완료하지 못하고337.60초에 중단됐다. 당시 자식 종료 판정 unconfirmed 원문을 보존하며 이후 물리적 정리 확인을 정상 완료로 바꾸지 않는다. 7bdbcb8 이후 실제 Claude native0회·새 준비0회. 기존 설치 실패1개, 실제 홈 NOT_VERIFIED, 광고 추적4개 기존 실패·클릭/숨김 통합 미검증을 유지한다. 유효한 양쪽 비교 표본·업무 효과·경쟁 우위는 없다.
- 이번 개선: 요구 파일을 .omc/visual-requirement.json의 절대 경로·해시에 연결했다. 300초는 중단 기준에서 진행 점검으로 변경하고 900/1500/1680/1788/1800초와 전체7200초 시간표를 문서화했다. 시간표는 강제 집행이나 실제 성공 시간의 보장이 아니다. 직접 자식인 셸의 PID·종료 코드·signal·wait·그룹 소멸·출력 회수 기록을 보존하고 Claude PID·종료 코드는 미관측이면 null로 구분한다. 시각값 OverflowError 등 잘못된 기록은 unknown으로 보존한다. 996d80d 원문은 실행하지 않는 .fixture로 분리해 고정 해시와 현재 소스를 구분하며 query·6표본·픽셀 기준은 유지했다.
- 코드·범위: base26753b7d에서 리뷰 승인된 입력 템플릿·시간표·종료 관측·감독·테스트·고정 원문 fixture 등10개 파일만 main에 로컬 커밋7bdbcb8. 검토 후보와 커밋의 경로·내용·권한이 일치한다. 이번 문서4개는 별도 범위다.
- 검증: 최종 통합 리뷰162개 통과·실패0·생략0(91.80초, Git 밖 임시 검사 경로). 종료 기록 회귀77개는162개와 겹쳐 합산하지 않는다. 원본의 중첩 Git 검사 경로에서는151개 통과·설치 검사1개 기존 실패·생략0이며 변경 전후 동일 단계·정규화 원문 일치를 확인했다. 별도 공개 restore4개 통과. Git 밖 성공은 기존 설치 실패 해결을 뜻하지 않는다. 문법·diff·TDD·커밋 훅 확인은 실제 Claude 업무 성공·전체 출하 검증과 구분한다.
- 리뷰: 전체10개 격리 리뷰 APPROVE 뒤 커밋7bdbcb8을 base26753b7d와 고정해 원본 승인 기록을 재결속했다. 후보 범위 해시·frozen diff·커밋 blob/권한이 일치하고 결속 검사 READY다. 이는10개 구현 커밋의 승인 연결이며 이번 문서4개나 전체 출하·릴리스 승인이 아니다.
- 보존 경계: 기존10개 변경·과거 실행/중단 원문·설치 실패 전후 로그·리뷰·교훈을 보존했다. 셸 종료를 Claude 종료로 소급 기록하거나 과거 HOLD를 성공으로 변경하지 않는다.
- 다음 작업: 7bdbcb8 보완 후보의 출하·비교 재실행 여부와 고정 버전·시간 배치를 별도 결정
- 다음: 현재 구현·리뷰·커밋 단계는 종료했다. 후속 출하·재설치와 로그인 없는 실제 Ad 비교 재실행 여부는 별도로 결정한다. 재실행을 선택하면 새 core·검수 실행기·감독 버전과 입력 해시, 실행/기록/정리 시간 및 두 실행 사이 정리 조건을 다시 고정·검토·승인한다. 이전 승인·예산을 자동 승계하지 않고 이번 문서 최신화에서 준비·모델 호출·비교를 실행하지 않는다. 실제 홈 연동은 NOT_VERIFIED로 남긴다.
- 배포: 7bdbcb8 보완은 로컬 커밋 완료·미릴리스·일반 사용처 미설치다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 이력이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 코드 승인과 별개.
- 근거: .omc/state/termination-contract-v3-20261025/REPORT.md, .omc/state/termination-timestamp-fix-20261025/REPORT.md, .omc/state/termination-integrated-review.json, .omc/state/termination-commit-rebind-20261025/commit-check.json, .omc/state/termination-commit-rebind-20261025/installer-evidence/installer-comparison.json, .omc/state/review-snapshots/8663afcd0e554aafa6b6ff2ae898ac6d.json; 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 996d80d 검수 실행기 호환성 및 원본 리뷰 재결속 · 2026-10-25

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 996d80d 검수 실행기 호환성5개 커밋 · 원본 리뷰 APPROVE·결속 READY · 실제 비교 HOLD
- 목표 대비 진행: 996d80d의 home 증거·시간·종료 보완 뒤 남아 있던 검수 실행기의 이전 core 고정 문제를 해결했다. 03d6a70에서 실행기·manifest·테스트를 996d80d에 맞추고 공개 restore·고정 명령·HTTP·Chromium·영수증 및 감독 연결 회귀를 확인했다. 원본 커밋 리뷰 재결속도 완료했다. 실제 Claude 업무 완료·생산성·경쟁 우위는 아직 미입증이다.
- 실제 재검증: 03d6a70 호환성 보완 후 실제 Claude native 실행0회·새 B준비0회·비교 HOLD다. 과거 준비 NOT_STARTED(home unknown/시간 초과, controlled failed, wrapper0·시도소비0)와 이전 실제1회1778.16초 정상 종료·전체FAIL·compact 경계1회는 당시 이력으로 유지한다. 이번 A단계 통합의 환경 권한 실패는 제품 신규 실패와 구분하며2회차 통과로 과거 원문을 고치지 않는다. 기존 광고 추적4실패·클릭 추적/오늘 하루 숨김 통합 미검증·업무 효과 및 경쟁 우위 미입증을 유지한다.
- 이번 개선: 검수 복원 CLI·config·manifest의 지원 core를 996d80d 전체 SHA로 통일했다. 이전99773380·637a965·임의 core와 충돌 manifest는 출력 쓰기 전에 거부한다. home/controlled query·입력 해시·같은 버퍼 실행·기존6표본·픽셀 기준은 유지했다. 감독 제품e4557dd는 변경하지 않고 테스트가637a965와996d80d를 각각 복원해 정상·내부124·외부 시간 초과·최종result 누락의4사례와 실제 소스·승인 버전을 대조하도록 보완했다.
- 코드·범위: 리뷰 승인된 실행기·테스트·manifest·README 5개 파일만 main에 로컬 커밋03d6a70. base79e209a에서 검토한 frozen diff·스테이징·커밋 blob과 내용이 일치한다. 이번 로드맵·대시보드4파일은 별도 문서 범위다.
- 검증: 최종 공유 통합 A-FULL-2는302개 통과·생략0(534.16초). 1회차는 로컬 포트·Chromium 실행 권한 제한으로225개 통과·40개 실패·37개 설정 오류였고 원문을 보존했다. 동일 소스의 실행 환경 보정 후2회차로 통과했으며 전체 통합 시작은 총2회다. 대상별 GREEN13개·45개는302개와 중복돼 합산하지 않는다. Python3파일 문법·원본 보존·TDD·공식 완료 가드·커밋 훅을 확인했다. 로컬/합성 회귀이며 실제 로그인 홈이나 Claude 업무 성공 증거가 아니다.
- 리뷰: 원본 worktree capture는 candidate_directory_unsupported로 차단돼 기존 기록을 보존하고 동일5개 변경만 별도 후보에 구성해 APPROVE했다. 사용자 승인 커밋03d6a70을 reviewed base79e209a와 고정해 원본 snapshot·APPROVE receipt를 생성했다. 격리/원본 frozen diff와 후보 범위 해시가 일치하며 결속 검사 READY다. 이5개 파일 커밋 승인과 이번 문서4개·전체 출하 승인은 구분한다.
- 보존 경계: 원본 storefront HEAD·변경 내용·Git 인덱스, 과거 실행/오류/리뷰·교훈을 보존했다. 리뷰 재결속을 위해 기존 미추적 자료를 삭제·이동·자동 제외하지 않았다. 커밋은 승인5개만 포함하며 이번 문서4개와 분리한다.
- 다음 작업: 996d80d core·03d6a70 검수 실행기·e4557dd 감독을 고정한 실제 Claude 최대1회 재검증 계획 개정·검토
- 다음: A단계 호환성 구현·검증·리뷰·커밋은 종료한다. 996d80d core·03d6a70 검수 실행기·e4557dd 감독의 전체 SHA와 manifest/query 해시를 B계획에 고정하고 진입 조건·시간 배치·정상 종료 기준을 개정·검토한다. 별도 실행 승인 후에만 새 준비를 시작하며 실제 홈·광고2개·자동 전환·서버/후보 결속·필수8프로필·selection/verification/complete-task를 확인한다. 감독 호출·Claude native 실행은 각각 최대1회, 자동 재시도0회다. 내부 모델/API 요청 수는 별도 관측하고 미관측은 미확인으로 남긴다. 이전 승인·예산은 자동 승계하지 않는다. 유효 OMC 사례 이후 동등 경쟁 비교 진입을 판단한다.
- 배포: 996d80d core·03d6a70 검수 실행기·e4557dd 감독은 로컬 커밋 상태이며 미릴리스·일반 사용처 미설치다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 이력이고 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 코드 승인과 별개.
- 근거: .omc/state/compat-996d80d-A/REPORT.md, .omc/state/compat-996d80d-A/full-1.log, .omc/state/compat-996d80d-A/full-2.log, .omc/state/compat-996d80d-A/full-2.json, .omc/state/compat-996d80d-A/requirement-evidence.json, .omc/state/compat-996d80d-A/complete-after-staging.txt, .omc/state/compat-996d80d-review/REPORT.md, .omc/state/compat-996d80d-review/original-record-result.json, .omc/state/compat-996d80d-review/original-binding-result.json, .omc/state/review-snapshots/2957506e817541bab4a11c0dab540132.json; 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 고정 home 증거·시간 경계 보완 및 원본 리뷰 재결속 · 2026-10-24

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 고정 home 증거·시간·종료 연결11개 커밋 · 원본 리뷰 APPROVE·결속 READY · 실제 비교 HOLD
- 목표 대비 진행: 최근 준비는 고정 home 시간 초과로 NOT_STARTED·Claude0회였다. 원인을 조사해 단계별 시간·응답 원문·종료 상태를 기록하는 v2 계약과 실제 완료 가드 연결을 보완하고 996d80d의11파일로 커밋했다. 잘못된 JSON 응답과 정리 시간 경계도 수정했으며 원본 커밋 후보의 리뷰 결속을 확인했다. 보완 후 실제 Claude 업무 완료·생산성·경쟁 우위는 아직 미입증이다.
- 실제 재검증: 996d80d 보완 후 실제 Claude/provider 새 호출0회·비교 HOLD다. 최근 준비는 NOT_STARTED(준비 home unknown/시간 초과, controlled failed, wrapper0·시도소비0)이며 모델 실행의 신규 실패로 집계하지 않는다. 이전 실제1회1778.16초 정상 종료·전체FAIL·비교HOLD와 compact 경계1회, 기존 광고 추적4실패·클릭 추적/오늘 하루 숨김 통합 미검증은 당시 이력으로 유지한다. 이번 로컬 검사나 리뷰 결속으로 업무 효과·경쟁 우위를 입증하지 않는다.
- 이번 개선: 고정 home/controlled와 새 native 홈 관측에 단계 시작/종료·마감·HTTP 응답 원문·해시·완전성·작업/브라우저 ID·종료 상태를 연결했다. 내부15초·외부20초·전체120초, 정리12초·봉인5초를 적용하며 103초 작업 구간과 종료 확인을 분리한다. 본문·operation·result의 잘못된 JSON 구조는 unknown으로 처리하고 원문을 보존한다. 종료 미확인 채널은 재사용하지 않으며 고정 영수증·별도 native 홈 소비자·실제 완료 가드가 같은 증거를 확인한다. 기존 준비5초·동일 track6표본·800ms 간격·이미지2개·자동 전환과 세션/후보/서버 결속 기준은 유지한다.
- 코드·범위: 리뷰된 core·검수 실행기·테스트·문서11파일만 main에 로컬 커밋996d80d. 검토 snapshot·스테이징·커밋 blob/권한 및 frozen diff가 일치한다. 이번 로드맵·대시보드4파일은 구현 커밋과 분리된 별도 문서 범위다.
- 검증: 최종 관련 통합253개 통과·생략0(521.59초), 추가 호환136개 통과·생략0(58.05초), 독립 리뷰28개 통과(27.66초·148개 제외). 독립28개는 기존 범위와 겹치므로 합산하지 않는다. Python8파일·JS1파일 문법·manifest source 해시·diff·TDD·완료 가드·커밋 훅을 확인했다. 과거 기존 실패4개 기록은 소급 수정하지 않고 이번136개 통과와 구분한다. 합성/로컬 Chromium 회귀는 실제 Claude 광고 업무 성공 증거가 아니다.
- 리뷰: 격리11파일 리뷰 APPROVE 뒤 원본 worktree의 candidate_directory_unsupported를 조사했다. 과거 검사용 미커밋 Git 폴더가 전체 작업트리 후보에 포함된 것이 원인이었다. 996d80d 커밋을 base82010a1과 명시적으로 고정해 원본 snapshot·승인 receipt를 새로 생성했고 후보 범위 해시와 frozen diff가 기존 검토와 동일했다. 결속 검사 READY는 해당11파일 커밋 후보에 한정한다. 이번 문서4파일이나 전체 출하 검증·릴리스 승인을 뜻하지 않는다.
- 보존 경계: 원본 storefront·과거 실행/오류/리뷰·.omc 교훈을 보존했다. 리뷰 재결속을 위해 미추적 자료를 삭제·이동·자동 제외하지 않았으며 기존 커밋 단위 후보 수집 기능을 사용했다. 커밋은 승인11파일만 포함했고 이번 문서4파일과 분리한다.
- 다음 작업: 996d80d core·검수 실행기 호환성 확인을 포함한 실제 Claude 최대1회 재검증 계획·진입 조건 확정
- 다음: 검수 복원 CLI와 manifest의 현재 CORE 기준은 99773380이다. 새 후보996d80d의 core·검수 실행기·고정 query/manifest 해시와 감독e4557dd의 호환성을 먼저 확인하는 계획을 개정·검토·승인한다. 호환성 보완이 필요하면 별도 범위로 확정한다. 실제 홈·광고2개·자동 전환·서버/후보 결속·필수8프로필·selection/verification/complete-task·정상 종료와 최종 검증/기록 시간을 진입 조건으로 둔다. 조건 충족 및 새 실행 승인 뒤 Claude 최대1회·자동 재시도0회로 검증하며, 이전 실행 승인·예산은 자동 승계하지 않는다. 유효 OMC 사례가 확인된 뒤 동등 경쟁 비교 진입을 판단한다.
- 배포: 996d80d core·검수 실행기 보완은 로컬 커밋 완료·미릴리스·일반 사용처 미설치다. 감독e4557dd도 별도 미릴리스 도구다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 이력이며 이번에 재감사하지 않았다. 이번 변경의 push·태그·릴리스·재설치는 수행하지 않았다.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 코드 승인과 별개.
- 근거: .omc/home-evidence-review-fix-20261024/REPORT.md, .omc/home-evidence-review-fix-20261024/integration.txt, .omc/home-evidence-review-fix-20261024/compatibility.txt, .omc/home-evidence-review-fix-20261024/completion.txt, .omc/home-evidence-review-20261024/REVIEW.md, .omc/home-evidence-review-20261024/independent.txt, .omc/review-candidate-investigate-20261024/REPORT.md, .omc/review-commit-rebinding-996d80d/binding-result.json, .omc/review-commit-rebinding-996d80d/final-check.json, .omc/state/review-snapshots/27d7b7557e554e3fbdaed5cfae1814db.json; 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 테스트 영수증·공통 시각 정책 보완 커밋 · 2026-10-23

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 테스트 영수증·공통 시각 정책7개 커밋 · 리뷰 APPROVE · 실제 비교 HOLD
- 목표 대비 진행: 실제 광고 사례는 1778.16초에 정상 종료했지만 테스트 원문이 unknown인데 완료 가드가 통과하고 고정 픽셀 manifest 계약이 맞지 않아 전체 FAIL·비교 HOLD였다. 이 두 연결 문제와 경쟁 도구 호환성을 보완해 99773380에 커밋했다. 로컬 검증·리뷰는 완료했으며 보완 후 실제 Claude의 필수 검증·업무 완료와 경쟁 우위는 아직 미입증이다.
- 실제 재검증: 보완 전 실제1회는 정상 종료1778.16초이나 전체 FAIL·비교 HOLD다. 테스트 unknown(baseline_test_removed)을 passed로 기록한 충돌과 고정 픽셀 계약 불일치, 수정·최종 작업의 시간 경계 위반이 관측됐다. 원래4개 실패 이름은 동일해 신규 테스트 실패로 확정하지 않는다. 실제 compact 경계1회는 관측됐으나 전체 성공을 뜻하지 않는다. 99773380 보완 후 실제 Claude/provider 호출0회, 업무 효과·경쟁 우위 미입증이다.
- 이번 개선: 고정 테스트 정책에서 실제 영수증의 경로·해시·후보·실행·세션·판정을 완료 가드에 연결했다. unknown·실패·불일치를 passed로 적어도 완료로 통과시키지 않는다. 양쪽은 동일한 visual-requirement.json과 공통 시각 정책 해시를 사용한다. 요구 파일 변경·삭제·뒤늦은 추가를 거부하며 OMC에만 세션·승인 기준 결속을 추가 적용한다. 경쟁 도구에는 OMC 승인 파일을 요구하지 않는다. 기존 비고정 정책 계약은 유지한다.
- 코드·범위: 리뷰 승인된 실행기·가드·테스트·템플릿7개 파일만 main에 로컬 커밋99773380. 승인된 frozen 후보와 커밋 패치가 일치하며 TDD·커밋 가드 통과. 이번 로드맵·대시보드4파일은 별도 미커밋.
- 검증: 구현 회귀267개 통과·생략0(공개CLI·로컬 Jest/격리 Chromium·완료106개와 인접161개). 독립 리뷰73개 통과(영수증·완료·공통정책71개와 양쪽 실제 로컬 Jest/Chromium2개). 문법·diff·staged TDD·완료 가드·커밋 훅 통과. 267개와73개는 겹치므로 고유 검사 수나 개선율로 합산하지 않는다. 로컬 fixture 회귀는 실제 Claude 광고 업무 성공 증거가 아니다.
- 리뷰: 원본 스테이징과 동일한 격리7파일 frozen 후보 전체 APPROVE·승인 receipt 기록. 이후 main 커밋99773380의7개 경로·패치 일치 확인. 격리 승인 receipt를 전체 main 출하 승인으로 확대하지 않는다.
- 보존 경계: 원본 storefront·이전 실행·stdout/stderr·전체/staged/unstaged diff·인덱스 원문 및 .omc 교훈 보존. 보완 커밋은 승인7개만 포함. 이번 문서4개와 분리.
- 다음 작업: 99773380 보완 후보의 실제 Claude 광고 최대1회 재검증 계획·진입 조건 확정
- 다음: 99773380을 새 core 후보로 고정하고 runtimef7566cc2·감독e4557dd와의 결합 및 준비 경로를 확인하는 계획을 개정·검토·승인한다. 양쪽 동일 시각 요구 파일과 테스트 영수증 판정, 필수8프로필·selection/verification/complete-task·정상 종료·시간 조건을 성공 기준에 연결한다. 이전637a965 실행 승인·예산은 자동 승계하지 않는다. 새 계획 승인 뒤 준비 조건을 확인해 Claude 최대1회·자동 재시도0회만 수행한다. 유효 OMC 사례가 확인돼야 동등 경쟁 비교 진입을 판단한다.
- 배포: 99773380 보완은 로컬 main 커밋 완료·미릴리스·일반 사용처 미설치다. runtimef7566cc2·감독e4557dd도 별도 미릴리스 도구다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 이력이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / live_observation_not_enabled. 코드 승인과 별개.
- 근거: .omc/ad-single-absolute-records-20261023T041331Z/REPORT.md, .omc/ad-single-absolute-records-20261023T041331Z/summary.json, .omc/state/receipt-pixel-common-fix/REPORT.md, .omc/state/receipt-pixel-common-fix/final-command-completion.txt, .omc/state/receipt-pixel-common-fix/final-adjacent.txt, .omc/state/receipt-pixel-common-fix/completion.txt, .omc/state/receipt-pixel-common-review/REVIEW.md, .omc/state/receipt-pixel-common-review/record-review.json, .omc/state/receipt-pixel-common-review/independent-validation.txt, .omc/state/receipt-pixel-common-review/independent-both-arms.txt; 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 최소 감독 실행기 구현·커밋 · 2026-10-23

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 최소 감독3개 커밋 · 리뷰 APPROVE · 실제 비교 HOLD
- 목표 대비 진행: 637a965 core와 f7566cc2 검수 실행기로 실제 광고 재검증을 준비했지만 외부 감독의 실행 소스·명령 연결과 시간 근거를 확보하지 못해 호출 없이 중단했다. 이후 별도 최소 감독을 구현하고 입력 전체 전달·실행 파일 결속·PATH 실패 처리를 보완해 e4557dd에 커밋했다. 실행 기반의 구현·회귀·리뷰는 확인했지만 실제 Claude의 필수 검증 수행·업무 완료·생산성 및 경쟁 우위는 미입증이다.
- 실제 재검증: 이번 감독 구현·리뷰·커밋의 실제 Claude/provider 호출은0회다. f7566cc2 반영 준비는 감독 경로·시간 근거 미확인으로 NOT_STARTED(모델0·wrapper0·시도소비0)였고 기존 감독 탐색도 진입 불가로 종료했다. 감독 합성 회귀를 실제 광고 재검증으로 세지 않는다. 과거 임시 입력 소실 NOT_STARTED, c4864a3 실제1회·1800.17초·FAIL_TIMEOUT 및 비교 HOLD를 유지한다. 실제 compact UNOBSERVED, 광고 추적4실패·클릭 추적/오늘 하루 숨김 통합·업무 효과 및 경쟁 우위는 미검증이다.
- 이번 개선: 단일 시도 감독 CLI에 실행 마감·최대12초 별도 회수·stdout/stderr 원문 저장·JSON 판독 제한을 연결했다. 프롬프트 고정 바이트의 전체 pipe 전달·해시·EOF를 확인하며 미전달은 정상 판정을 금지한다. 실행 파일은 후보 기준의 같은 절대경로로 해시 계산·실행하고 PATH 검색 실패는 후보 동명 파일을 실행하지 않은 채 종료2·시도 미소비로 기록한다. 자식 정리 미확인·원문 불완전·최종 JSON 누락도 정상으로 처리하지 않는다.
- 코드·범위: 리뷰 승인된 감독 CLI·테스트·README3개 파일만 main에 로컬 커밋e4557dd. 커밋3개 경로·Git blob이 승인 후보와 동일하다. core637a965와 runtimef7566cc2는 수정하지 않았다. 이번 로드맵·대시보드4파일은 별도 미커밋.
- 검증: 최종 공개 CLI 회귀38개 통과·생략0(10.69초), 동일 후보의 독립 리뷰 회귀38개 통과·생략0(12.11초). 고정 core637a965 공개 run 경유 정상 JSON·내부124·외부 timeout·최종 JSON 없음의4사례와 B1~B6 종료/원문 경계, 입력 전달·교체·경로 결속·PATH 실패를 포함한다. Python2파일 문법·TDD·완료 가드·커밋 훅 통과. 두38개는 겹치는 같은 범위이므로 합산하지 않으며 과거158개·13개와도 합산하거나 개선율로 비교하지 않는다. 합성 자식 회귀는 실제 광고8프로필·로그인 홈·Claude 업무 완료 증거가 아니다.
- 리뷰: 원본 스테이징과 동일한 격리3파일 후보 전체 APPROVE·승인 receipt 기록. e4557dd의3개 경로·Git blob과 승인 후보의 일치를 확인했다. 승인 범위는 해당3파일이며 전체 main 릴리스 승인으로 확대하지 않는다.
- 보존 경계: 구현·리뷰·커밋에서 승인된3파일만 변경·커밋하고 .omc 근거·교훈과 과거 실행 기록은 보존했다. 감독 회귀와 고정 core 원문 보존을 확인했으며 실제 storefront·인증·브라우저 실행은 하지 않았다. 이번 문서4파일은 구현 커밋과 분리한다.
- 다음 작업: 637a965 core + f7566cc2 검수 실행기 + e4557dd 감독의 단일 재검증 계획·시간 배치 확정
- 다음: 감독 구현 보완은 종료한다. 새 감독e4557dd·core637a965·runtimef7566cc2를 별도 고정하고 기존 실제 Claude 최대1회 계획을 개정·검토·명시 승인한다. 설정된 제한·합성 실측·실제 모델 미관측 시간을 분리하고 최종8프로필·selection/verification/complete-task·정상 보고/종료의 경로와 시간 배치를 확정한다. 기존 준비60분·실행30분·사후30분 및600/960/120/120초 배치를 자동 승인으로 취급하지 않는다. 새 계획 승인 뒤에만 실제 홈 로그인·광고2개·자동 전환·후보 bundle 결속과 실행 진입 조건을 점검한다. 조건 미충족은 호출0·NOT_STARTED로 종료하고 자동 재시도하지 않는다. 유효 OMC 단일 사례 확인 뒤 동등 경쟁 비교를 결정한다.
- 배포: 새 감독e4557dd와 검수 실행기f7566cc2는 로컬 main 커밋 완료·미릴리스·일반 사용처 미설치다. 고정 core637a965와 별도 operator 도구를 구분한다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 결과이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 자동 재등록·재개 없음.
- 근거: 추적 문서 docs/comparison-dashboard/evidence/ad-native-supervisor-v1/README.md; 로컬 .omc/state/ad-supervisor-path-fix/REPORT.md, .omc/state/ad-supervisor-path-fix/final.txt, .omc/state/ad-supervisor-path-fix/complete-task.txt, .omc/state/ad-supervisor-review-3/raw-review.md, .omc/state/ad-supervisor-review-3/independent-tests.txt, .omc/state/ad-supervisor-review-3/approval-receipt.json, .omc/state/ad-supervisor-review-3/commit-receipt.json, .omc/ad-single-637a965-f7566cc2-20261023T013221Z/REPORT.md, .omc/state/supervision-readiness-20261023T014707Z/REPORT.md; 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · controlled query 호환성 보완 커밋 · 2026-10-23

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: controlled query 호환성3개 커밋 · 리뷰 APPROVE · 실제 비교 HOLD
- 목표 대비 진행: 임시 경로와 파일 교체 방어를 복원한 뒤, 필수 controlled 검증이 home 전용 허용 목록 때문에 막히는 호환성 누락을 확인했다. 두 query 해시를 고정하고 실제 core 명령→HTTP→Chromium→화면·receipt 판정 경로를 보완해 f7566cc2에 커밋했다. 실제 Claude가 필수 검증을 수행하고 업무를 정상 완료하는지와 생산성·경쟁 우위는 아직 미입증이다.
- 실제 재검증: 이번호환성 구현·리뷰·커밋 후 실제 Claude/provider 호출0회다. 기존637a965 준비 NOT_STARTED(임시 입력 소실,wrapper0회·예산소비0)와 c4864a3 실제1회·1800.17초·FAIL_TIMEOUT 및 비교 HOLD를 유지한다. 8e65e9f8 실행 계획은 controlled query 미허용으로 HOLD였으며 새 runtime으로 자동 재개하지 않는다. 실제 compact는 UNOBSERVED, 광고 추적4실패·클릭 추적/오늘 하루 숨김 통합 미검증·업무 효과와 경쟁 우위 미입증이다.
- 이번 개선: 허용 목록에 고정 core637a965의 CONTROLLED_QUERY 해시를 추가해 home·controlled 두 query만 복원한다. 실제 공개 freeze/run 명령으로 정상 passed·테두리/화살표 결함 failed·이미지 미로드/전환 정지 unknown을 확인한다. ID/고정 원문·두 읽기 시점의 파일 교체8개 회귀로 같은 버퍼 검증·실행과 변조 거부를 유지한다. 기존6개 홈 표본·픽셀 판정 기준은 바꾸지 않았다.
- 코드·범위: 리뷰 승인된 manifest·테스트·README3개 파일만 main에 로컬 커밋f7566cc2. Python 복원 CLI·Node 브라우저·core는 변경하지 않았다. 이번 로드맵·대시보드4파일은 별도 미커밋.
- 검증: 최종 관련 회귀3모듈158개 통과·생략0(226.60초): runtime·고정 입력·홈 관측, A/B각72셀/B1B2와 실제 baseline Ad 빌드 포함. 독립 리뷰13개 통과(통제 화면5개·파일 교체8개,48개 제외). 기본 환경 독립36개 통과·미요청 통합25개 생략은 실제 통합 완료 근거에서 제외한다. 독립 리뷰에서158개 전체를 다시 실행한 것은 아니며 리뷰 당시 실행 스냅샷과 해당3개 파일의 일치를 확인했다. 과거171개 및 중복 묶음과 합산하거나 개선율로 비교하지 않는다. 문법·TDD·완료 가드·커밋 훅 통과. 통제 fixture 검사는 실제 로그인 홈·전체8프로필·Claude 업무 완료 증거가 아니다.
- 리뷰: 원본 스테이징과 동일한 격리3개 후보 전체 APPROVE·승인 receipt 기록. main 커밋3개 경로·Git 객체·권한 일치 확인. 원본 전체 작업트리의 ship 승인으로 확대하지 않는다.
- 보존 경계: 원본 storefront의 외부 checkout/rebase로 HEAD·인덱스가 바뀌어 사용자 승인에 따라 읽기 전용 접근과 외부 변경 근거를 분리 보존했다. 원본 전체 동일성을 주장하지 않고 자동 복원하지 않았다. 증거용 소스 복사본은 표준 .omc/state 위치로 옮겨 해시를 재검증했으며 삭제·ignore 정책 변경은 없었다.
- 다음 작업: 637a965 core + f7566cc2 검수 실행기의 실제 Claude 최대1회 계획 개정·재검토·승인
- 다음: 호환성 보완 구현은 종료한다. 8e65e9f8에 고정됐던 실행 계획의 HOLD 이력은 보존하고, core637a965와 새 operator runtimef7566cc2·두 query·manifest 해시 및 검증 근거로 계획을 개정해 critique와 별도 실행 승인을 받는다. 준비60분·실행30분·사후30분과 수정600초/최종검증960초/기록120초/정상보고·종료120초 배치의 소요 근거를 재확인한다. 실제 홈 로그인·광고2개·자동 전환·후보 bundle 결속·필수8프로필 경로가 준비돼야 Claude 최대1회·자동 재시도0회에 진입한다. 준비 미충족은 호출0회로 종료하며 이전 승인·예외·예산은 승계하지 않는다. 최종 결과 선택·완료 기록·정상 종료를 확인한 유효 OMC 단일 사례 뒤 동등 경쟁 비교를 결정한다.
- 배포: 검수 실행기f7566cc2는 로컬 main 커밋 완료·미릴리스·일반 사용처 미설치다. 검증 대상 core는637a965이며 runtime과 구분한다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 결과이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 자동 재등록·재개 없음.
- 근거: 추적 문서 docs/comparison-dashboard/evidence/ad-validation-runtime-v1/README.md 및 manifest.json; 로컬 .omc/controlled-query-compat-task/REPORT.md, .omc/controlled-query-compat-task/final-strict-revision-2.txt, .omc/controlled-query-compat-task/complete-task-final.txt, .omc/controlled-query-compat-task/preservation-final.json, .omc/controlled-query-compat-task/storefront-external-reflog.txt, .omc/state/controlled-query-review-20261023/raw-review.md, .omc/state/controlled-query-review-20261023/approval-receipt.json, .omc/state/controlled-query-review-20261023/independent-browser.txt, .omc/plans/claude-ad-rerun-637a965-runtime-8e65e9f8-critique.json; 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 검수 실행기 복원·파일 교체 방어 커밋 · 2026-10-22

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 검수 실행기 복원5개 커밋 · 리뷰 APPROVE · 실제 비교 HOLD
- 목표 대비 진행: 시작 승인·세션 연결과 홈 관측을 보완했고, 사라진 임시 경로 때문에 막힌 광고 재검증의 실행기·입력을 복원했다. 복원 CLI·빌드·통제 화면 판정과 파일 교체 회귀를 확인해 8e65e9f8에 커밋했다. 실제 Claude 업무 완료·생산성 및 경쟁 우위는 아직 미입증이다.
- 실제 재검증: 637a965 개정2 준비는 검수 browser.cjs·manifest의 임시 경로 소실로 NOT_STARTED, Claude0회·wrapper0회·시도예산 소비0회다. 제품 결함이나 신규 테스트 실패로 집계하지 않는다. 이후 복원 작업과 리뷰에서도 Claude0회다. 기존 c4864a3의1회·1800.17초·FAIL_TIMEOUT, 테스트 이름 누락·홈 표본 부족·최종 기록 및 정상 종료 미완료와 비교 HOLD는 그대로 유지한다. 실제 compact는 UNOBSERVED, 광고 추적4실패·클릭 추적/오늘 하루 숨김 통합 미검증·생산성 및 경쟁 우위 미입증이다.
- 이번 개선: 해시 고정 입력에서 새 검수 폴더를 복원해 과거 임시 경로 의존을 제거했다. source·query는 검증한 바이트로 기록하고, 브라우저도 같은 query 버퍼를 검증·실행한다. 검사 중 파일이 교체되면 변조를 거부하거나 이미 검증한 원래 버퍼만 실행한다. 기본 CI와 실제 통합 환경을 구분하며 실제 검사를 요청했는데 필수 환경이 없으면 실패한다. 기존6개 홈 표본·픽셀 판정 기준은 유지한다.
- 코드·범위: 리뷰 승인된 검수 실행기5개 파일만 main에 로컬 커밋8e65e9f8. 이번 로드맵·대시보드4파일은 별도 미커밋.
- 검증: 최종 실제 환경 회귀171개 통과·생략0. 새 위치 A/B 각각72셀과 B1/B2 결속·실제 baseline 빌드·홈 표본 회귀를 포함한다. 독립 리뷰는 기본 환경30개 통과·미요청 통합16개 생략, 실제 Chromium 파일 교체4개와 경로1개 총5개 통과다. 묶음은 중복되므로 합산하지 않는다. 독립 리뷰 시171개를 다시 실행한 것은 아니며 실행 당시 코드와 현재 후보 일치를 확인했다. 문법·TDD·완료 가드·커밋 훅 통과. 통제 화면 검사는 실제 로그인 홈·Claude 완료 증거가 아니다.
- 리뷰: 원본 스테이징과 동일한 격리5개 후보 전체 APPROVE·승인 receipt 기록. main 커밋5개 경로·Git 객체·권한 일치 확인. 원본 전체 작업트리의 ship 승인으로 확대하지 않는다.
- 다음 작업: 637a965 core + 8e65e9f8 검수 실행기로 실제 Claude 최대1회 재검증 계획·승인
- 다음: 복원 구현 보완은 종료한다. core637a965와 별도 operator runtime8e65e9f8·manifest·query 해시를 고정한 새 계획을 검토·승인한 뒤 준비를 점검한다. 실제 홈 로그인·광고2개·자동 전환·후보 bundle 결속과 예산을 확인해야 실제 Claude 최대1회·자동 재시도0회에 진입한다. 준비 미충족 시 호출 없이 종료한다. 기존 테스트 이름, 필수8프로필·결과 선택·최종 완료 기록·정상 종료를 확인한다. 이전 승인·예외·시간 한도는 자동 승계하지 않는다. OMC 단일 사례가 유효해진 뒤 동등 경쟁 비교를 결정한다.
- 배포: 검수 실행기8e65e9f8은 로컬 main 커밋 완료·미릴리스·일반 사용처 미설치다. 검증 대상 core는637a965로 유지하며 복원 실행기와 구분한다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 결과이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 자동 재등록·재개 없음.
- 근거: 추적 문서 docs/comparison-dashboard/evidence/ad-validation-runtime-v1/README.md 및 manifest.json; 로컬 .omc/ad-single-637a965-20261022T054829Z/REPORT.md, .omc/runtime-query-consumer-fix/REPORT.md, .omc/runtime-query-consumer-fix/final-strict.txt, .omc/runtime-query-consumer-fix/complete-task.txt, .omc/state/review-20261022T165148-e5b64dc5/raw-review.md, .omc/state/review-20261022T165148-e5b64dc5/approval-receipt.json; 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 실제 광고 재검증 결과·홈 관측 보완 커밋 · 2026-10-22

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 광고 준비·표본 수집 보완3개 커밋 · 리뷰 APPROVE · 실제 비교 HOLD
- 목표 대비 진행: c4864a3 실제 Claude 1회에서 시작 승인·세션 연결과 RED 등록 이후 제품 수정을 확인했다. 업무는 테스트 이름 누락·홈 표본 부족·최종 기록 누락 및 시간 초과로 완료하지 못했다. 이후 637a965에서 홈 관측의 준비 대기·표본 수집과 기한 판정을 보완해 구현·리뷰·커밋했다. 새 후보의 실제 업무 완료·생산성 및 경쟁 우위는 미입증이다.
- 실제 재검증: c4864a3 실제 Claude1회·1800.17초·재시도0회. 시작 승인 연결과 RED 이후 수정은 확인됐지만 실행 FAIL_TIMEOUT·비교 HOLD다. tests는 baseline_test_removed와 최종 후보 불일치, home은 최초 timeout·후속2회 home_samples_missing, final-selection·verification·완료 성공·정상 terminal은 없었다. 실제 compact0회로 UNOBSERVED다. 637a965 보완 후 실제 Claude 호출0회. 기존 광고 추적4실패·클릭 추적/오늘 하루 숨김 통합 미검증·생산성 및 경쟁 우위 미입증을 유지한다.
- 이번 개선: 광고 DOM·트랙 연결과 이미지2개 로드를 최대5초 미만 동안 재탐색한 뒤 새 측정 창을 시작한다. 준비 경과가5000ms 이상이면 광고 발견보다 먼저 timeout으로 판정하고 표본을 수집하지 않는다. 동일 트랙6개 표본·800ms 이상 간격·4초 이상·연결 유지·이미지2개·변환 변화 기준은 유지한다. 준비 실패와 측정 중 요소 교체를 구분하며 서로 다른 요소의 표본을 합치지 않는다.
- 코드·범위: 리뷰 승인된3개 파일만 main에 로컬 커밋637a965. 이번 로드맵·대시보드4파일은 별도 미커밋.
- 검증: 최종 홈 관측·준비 회귀113개 통과·생략0(격리 Chromium 포함). 경계·표본 독립 리뷰23개 통과. 이전 보완 단계140개 통과 기록과 중복되므로 합산하지 않는다. 변경 없는 고정 명령 CLI 체인12개는 이번 재실행 범위에서 제외했다. Python 문법·TDD·완료 가드·커밋 훅 통과. 합성 브라우저 검사는 실제 로그인 홈·Claude 완료 증거가 아니다.
- 리뷰: 원본과 동일 diff의 격리3개 후보 전체 APPROVE·승인 receipt 기록. main 커밋3개 경로·내용 일치 확인. 원본 전체 ship 승인으로 확대하지 않는다.
- 다음 작업: 637a965 후보의 실제 Claude 광고 사례 최대1회 재검증 계획·승인
- 다음: 이번 관측 보완은 종료한다. 637a965를 격리 설치해 동일 광고 계약·입력·후보 서버·실제 홈 준비를 점검하는 단일 OMC 재검증을 별도로 계획·승인한다. 실제 Claude 최대1회·자동 재시도0회이며 준비 미충족 시 호출 없이 종료한다. 기존 테스트 이름 보존, 현재 후보의 필수8프로필·결과 선택·최종 완료 기록·정상 종료를 확인한다. 기존 실행 한도와 예외 승인은 자동 승계하지 않는다. OMC 단일 사례가 유효해진 뒤 동일 조건 경쟁 비교를 결정한다.
- 배포: 637a965는 로컬 main 커밋 완료·미릴리스·일반 사용처 미설치다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 결과이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 자동 재등록·재개 없음.
- 근거: 로컬 .omc/ad-single-c4864a3-20261022T011613Z/REPORT.md, .omc/ad-single-c4864a3-20261022T011613Z/summary.json, .omc/investigate-home-samples-20261022/REPORT.md, .omc/home-readiness-task/REPORT.md, .omc/home-readiness-deadline-task/REPORT.md, .omc/home-readiness-deadline-review/REPORT.md, .omc/home-readiness-deadline-review/receipt-result.json; 다른 checkout에 자동 제공되지 않는 자료다.

## 이전 이력 · Claude 시작·compact 승인 연결 보완 커밋 · 2026-10-22

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 시작·compact 승인 연결 보완8개 커밋 · 리뷰 APPROVE · 실제 비교 HOLD
- 목표 대비 진행: 771976c 실제 Claude 1회에서 시작 훅의 승인 초기화를 확인했다. c4864a3로 최초 시작 인계와 같은 세션 compact의 승인·진행 보존을 구현·리뷰·커밋했다. 새 후보의 실제 업무 완료와 동등 경쟁 비교는 아직 확인하지 않았다.
- 실제 재검증: 771976c 실제 Claude 단일 재검증은1회·33.00초·child/wrapper exit0·재시도0회였지만 시작 훅이 CONTRACT와 pipeline session ID를 초기화해 첫 task-context exit2로 중단했다. 제품 수정0·최종8프로필검증0·업무 및 비교 HOLD다. c4864a3 보완 후 실제 Claude 호출0회다. 과거 양쪽 비교 HOLD·광고 추적4실패·클릭 추적/오늘 하루 숨김 통합 미검증·생산성 및 경쟁 우위 미입증을 유지한다.
- 이번 개선: 최초 startup은 승인·작업·검증 기준·후보를 결속한 일회용 인계만 유지한다. 같은 native session의 compact는 현재 승인과 진행을 보존하되 유효기간을 연장하거나 과거 승인을 복원하지 않는다. 다른 세션·변경/만료 승인·손상 기록은 거부한다. 컨텍스트 생성의 중복 초기화를 제거하고 공개 훅은 현재 source/session_id만 전달한다.
- 코드·범위: 리뷰 승인된8개 파일만 main에 로컬 커밋. 이번 로드맵·대시보드4파일은 별도 미커밋.
- 검증: 최종 관련 회귀303개 통과·UI query2개 제외. 독립 리뷰134개 통과·동일 UI query2개 제외. 공개 shell startup→제품 수정/진행→반복 compact 및 승인 변경 차단 확인. 두 묶음은 중복되므로 합산하지 않는다. Python·shell 문법, TDD·완료 가드·커밋 훅 통과. 실제 Claude 실행 검증과는 구분한다.
- 리뷰: 원본과 동일 내용의 격리8개 후보 전체 APPROVE·승인 receipt 기록. main 커밋8개 경로·내용 일치 확인. 원본 전체 ship 승인으로 확대하지 않는다.
- 다음 작업: c4864a3 후보의 실제 Claude 광고 사례 최대1회 재검증 계획·승인
- 다음: 이번 시작·compact 보완은 종료한다. c4864a3를 격리 설치해 동일 광고 계약·입력·후보 서버·실제 홈 준비를 확인하는 단일 OMC 재검증을 별도로 계획·승인한다. 실제 Claude 최대1회·자동 재시도0회, 준비 미충족 시 호출 없이 종료한다. 최초 명령의 승인 연결부터 필수 검증·화면 자동 판정·최종 완료 기록·정상 종료까지 확인한다. 이전 실행 한도·예외 승인은 자동 승계하지 않는다.
- 배포: c4864a3는 로컬 main 커밋 완료·미릴리스·일반 사용처 미설치다. 정상11개 사용처의 e618fca/0.6.1 설치·엄격 감사는 당시 결과이며 이번에 재감사하지 않았다. push·태그·릴리스·재설치 없음.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / live_observation_not_enabled. 자동 재등록·재개 없음.
- 근거: 로컬 .omc/ad-single-771976c-20261021T160747Z/REPORT.md, .omc/ad-single-771976c-20261021T160747Z/summary.json, .omc/tasks/session-start-handoff/REPORT.md, .omc/tasks/session-start-handoff/review/REPORT.md, .omc/tasks/session-start-compact/REPORT.md, .omc/tasks/session-start-compact/review/REPORT.md, .omc/tasks/session-start-compact/review/record-result.txt; 다른 checkout에 자동 제공되지 않는 자료다.

## 이전 이력 · 세션·원문·완료 증거 연결 보완 커밋 · 2026-10-22

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 세션·원문 보완14개 커밋 · 완료 가드 통과 · 실제 비교 HOLD
- 하려던 일과 결과: 동일 광고 사례로 실제 업무 효과를 비교하려 했고 양쪽 각1회 실행을 종료했다. OMC는 세션·승인 연결 단절로 제품 수정 전에 멈췄다. 경쟁은 execute 성공 반환·제품 수정이 있었지만 최종 증거가 유효하지 않아 양쪽 HOLD다. 이 차단 원인을 보완한14개 파일을 771976c에 커밋했다.
- 이번 개선: 사전 결속 비교의 세션·승인 기준을 유지하고 native 실행 중 문맥 변경을 감지한다. 오류·중단 때 원문과 진단을 분리 보존하고 하위 프로세스 종료·출력 회수에 시간제한을 둔다. 새 결과 선택 경로는 .omc/comparisons/<run_id>/final-selection.json으로 고정한다. 리뷰·조사 뒤에는 작업 ID·계보·기준 커밋을 대조해 같은 구현의 완료 증거만 연결한다.
- 검증: 초기 세션·경로 보완249개, 종료 처리 보완154개, 최종 완료 소유 작업 연결226개가 각각 통과했다. 독립 리뷰 종료5개·연결14개 통과. 서로 다른 후보·중복 범위이므로 합산하지 않는다. main 최종 TDD·complete-task 및 커밋 훅 통과, 14개 경로·내용 대조 완료.
- 리뷰·범위: 초기11개 리뷰 지적은 종료 처리2개 후속 APPROVE로 보완했고, 완료 소유 작업 연결3개는 별도 APPROVE다. 격리 부분 후보 승인과 원본 전체 후보 승인은 구분한다. 커밋은 승인한14개 파일만 포함한다.
- 다음 작업: 771976c 후보의 실제 Claude 광고 사례 1회 재검증 계획·승인
- 다음: 준비 보완은 이번 범위에서 종료한다. 771976c 후보를 격리 설치하고 동일 광고 계약·입력·후보 서버·실제 홈 준비를 확인하는 단일 OMC 재검증을 별도로 계획·승인한다. 실제 Claude 호출은 최대1회·자동 재시도0회로 제한하고, 준비가 충족되지 않으면 실행하지 않고 원인을 기록한다. 기존 양쪽 비교 호출 한도는 소비됐으며 이전 승인·예외를 자동 승계하지 않는다.
- 배포: 이번 771976c는 로컬 main 커밋 완료·미릴리스·일반 사용처 미설치다. 기존 정상11개는 e618fca/0.6.1 설치·감사 당시 결과로 보존하며 현재 재감사했다고 주장하지 않는다. 이번 작업에서 push·태그·릴리스·재설치 없음.
- 경계: 광고 비교는 양쪽 각1회 실행했으나 모두 HOLD다. OMC는 세션 전환·승인 연결 문제로 제품 수정 전 종료(exit1·159.98초), 경쟁은 execute 호출·성공 반환과 제품 수정 뒤 테스트명 누락·실제 홈 unknown·선택 receipt8개 stale로 HOLD(exit0·784.93초)다. 진행 단계가 달라 속도 우열을 매기지 않는다. 771976c 보완 후 실제 Claude 호출0회이며 경쟁 우위·시간/개입/재작업 효과는 미입증이다. 기존 광고 추적4실패·클릭 추적/오늘 하루 숨김 통합 미검증을 유지한다.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / live_observation_not_enabled. 자동 재등록·재개 없음.
- 근거: 로컬 .omc/ad-pair-061-20261021/REPORT.md, .omc/session-custody-fix/REPORT.md, .omc/native-drain-review-fix/REPORT.md, .omc/completion-owner-fix/REPORT.md 및 complete-final.txt, .omc/completion-owner-review/REPORT.md. 다른 checkout에 자동 제공되지 않는 로컬 자료다.

아래 이전 이력의 현재·다음·설치 상태는 기록 당시의 값이며 이번 실행 승인이나 현행 상태로 재사용하지 않는다.

## 이전 이력 · 0.6.1 릴리스·11개 재설치 완료 · 2026-10-21

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 0.6.1 로컬 패치 릴리스·정상11개 재설치 완료 · 전체 출하 검증 통과 · 실제 비교 HOLD 유지
- 코드·커밋: 고정 검증68411f7·계약 테스트 격리edae3c9·계약 문서e41c03e·설치 검사 격리d844457·인덱스 증거 보존904c58d를 포함해 e618fca로0.6.1 메타데이터3개를 커밋했다.
- 검증: 최종0.6.1 후보e618fca 전체 출하 검사1회: 4790 passed·0 failed·0 error·승인된3 skipped·slow12 제외, 1063.28초. 실제 광고 baseline 빌드·산출물 보존 통과. 내부 인덱스 기록5건 전후 raw 동일. 버전 회귀32개는 별도 범위로 통과했으며 전체 수치와 합산하지 않는다.
- 생략 경계: 외부 과거 입력 검증2개·의도적 RED표식1개만 최종 후보1개·전체 검사1회에 명시 승인했다. 이 승인은 소비됐으며 다음 실행에 자동 승계하지 않는다. 과거 원본 자료 미확보·재현성 미검증 상태는 유지한다.
- 보존: 원본 HEAD·파일 내용/권한·인덱스 원문 보존. 실행 clone의 스테이징 정보·플래그 보존, raw 차이는 .omc/wip/latest.json 시간 캐시뿐이다. 대표 재현의 플러그인 원인과 과거 원인UNKNOWN을 구분한다.
- 배포: 0.6.1 로컬 패치 릴리스e618fca·정상11개 재설치·엄격 감사 완료. 모두 installed_version0.6.1·install_integrity clean·source_status unchanged·source_revision e618fca. 설치 직전 업무 파일34912개·관찰/설정163개 변경0. 설치본·receipt 백업 보존. marketing-project 기존 제외, 원격 push·태그·GitHub release 없음.
- 다음: 출하 준비 보완은 종료했다. 0.6.1을 기준으로 advertising-comparison/v2 계약·동일 baseline/모델/입력·경쟁 플러그인·후보 서버·실제 홈 준비를 고정한 뒤, 양쪽 각 최대1회·자동 재시도0회의 실제 비교를 별도 승인 후 진행한다. 기존 HOLD·원본 증거를 소급 변경하거나 릴리스·설치 결과로 비교 성공을 대신하지 않는다.
- 다음 작업: 0.6.1 동일 광고 사례의 실제 비교 재실행 여부 결정
- 경계: 현재 보완 후 실제 Claude·oh-my-claudecode 새 실행0회. 기존9e21b70 단일 실행464.48초의 필수5검증·픽셀12조건·실제 홈 통과와 native_procedure unknown·전체 HOLD는 당시 결과로 보존한다. 기존 광고 추적4실패와 클릭 추적·오늘 하루 숨김 통합 미검증, 동등 비교·생산성 효과 미입증을 유지한다. Chromium 로컬 렌더 성공은 실제 광고 화면 통과가 아니다. OMC Kit 전체 회귀 통과와 광고 업무의 기존 추적4실패는 서로 다른 검증 범위다. 0.6.1 릴리스·설치는 실제 Claude 재비교나 생산성 효과의 증거가 아니다.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 관찰 설정·기록은 보존하며 재개·재등록하지 않았다.
- 근거: [0.6.1 릴리스 노트](../releases/0.6.1.md). 로컬 .omc/release-061/REPORT.md, rollout-summary.json, install-results.json, final-quality-summary.json 및 .omc/ship-final-e618fca/assessment.json. 로컬 자료는 다른 checkout에 자동 제공되지 않는다.

## 이전 이력 · 출하 재검증 준비와 검증 정책 정리 · 2026-10-20

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 출하 재검증 준비 · 코드 커밋 완료 · 계약 문서 승인·미커밋 · 실제 비교 HOLD 유지
- 코드·커밋: 68411f7 고정 검증 코드6개·문서4개 커밋 완료. edae3c9 계약 해시 테스트2개 격리 보완 커밋 완료.
- 계약 정책: 예외는 과거 외부자료 검사2개와 의도적 RED표식1개, 최종 후보1개·전체 검사1회에 한정한다. 실제 광고 baseline 빌드는 필수다. 정책 선택과 문서·JSON 리뷰 APPROVE는 완료됐으나 계약 문서1개는 미커밋, 최종 후보 미고정·설정 stale·실행 승인 전이다.
- 검증: 68411f7 전체 품질 검사 이력: 4777 passed·2 failed·4 skipped·slow12 제외, 17분24초. 계약 해시 테스트의 실제 작업 상태 간섭은 기준 버전과 후보에서 동일 재현됐고 edae3c9로 격리 보완했다. 관련56개와 독립10개는 별도 범위 통과이며 합산하거나 전체 검사 재통과로 취급하지 않는다. 생략 후보4개 진단1회는4skipped이며 통과가 아니다. 고정 검증 경로의88개·독립3개는 이전 후보 이력이다.
- 진단: 원본을 복사한 격리 index에서 Git 변경 조회만으로 raw hash와 stat 캐시가 달라지고 entry·flags는 같은 현상을 재현했다. 당시 원인은 UNKNOWN. 외부 비교 원본7개는 조사 범위에서 기대 해시 일치 자료0개다. Chromium은 샌드박스에서 권한 오류, 권한 허용 환경의 로컬 HTML 렌더는 성공했으나 실제 광고 화면은 미검증이다.
- 기록 정정: 진단 기록 충족과 출하 준비 blocked를 분리하고, TDD 증거는 기준3da399b→후보edae3c9의 격리12파일 검사로 명시했다. 원본 기록은 보존했다.
- 다음: 리뷰 승인된 계약 문서 1개를 별도 커밋하고 이번 로드맵·대시보드 변경까지 포함한 최종 후보를 고정한다. 최종 범위 리뷰·실제 범위 TDD·품질 설정 근거 갱신과 정식 승인·권한 허용 환경을 확인한 뒤 별도 omc-ship 요청으로 전체 검사를 최대1회·60분·재시도0회 수행한다. 이번 최신화는 출하 검사나 모델 비교 실행이 아니다.
- 다음 작업: 계약 문서 1개 커밋 후 최종 후보 고정
- 배포: 68411f7 고정 검증 경로와 edae3c9 테스트 격리 보완은 로컬 커밋 완료·미릴리스·일반 사용처 미설치다. 계약 문서는 리뷰 승인·미커밋이며 이번 문서4파일 커밋 범위에서는 제외한다. 기존0.6.0 정상11개 설치는 과거 이력이고 새 push·설치·재감사는 수행하지 않았다.
- 경계: 현재 보완 후 실제 Claude·oh-my-claudecode 새 실행0회. 기존9e21b70 단일 실행464.48초의 필수5검증·픽셀12조건·실제 홈 통과와 native_procedure unknown·전체 HOLD는 당시 결과로 보존한다. 기존 광고 추적4실패와 클릭 추적·오늘 하루 숨김 통합 미검증, 동등 비교·생산성 효과 미입증을 유지한다. Chromium 로컬 렌더 성공은 실제 광고 화면 통과가 아니다.
- 관찰: v3 DISABLED · OBSERVATION_INVALID / pending_completion_invalid. 문서 승인과 별개로 작업 완료 게이트의 RED 등록 요건 미충족은 유지한다.
- 근거: 로컬 .omc/committed-ship-check-68411f7/REPORT.md, .omc/contract-hash-isolation-task/REPORT.md, .omc/ship-diagnostic-edae3c9/correction-v2/REPORT.md, .omc/external-input-browser-investigation/REPORT.json, .omc/ship-contract-document-task/review/review.json. 다른 checkout에서 자동 제공되지 않는 로컬 자료다.

## 이전 이력 · 고정 검증 실행 경로 구현·리뷰 승인 · 2026-10-20

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 고정 검증 경로 6파일 구현·리뷰 승인 · 미커밋 · 실제 비교 HOLD 유지
- 이번 보완: 고정8개 공통 검증 프로필의 실행·종료 코드·출력·후보·산출물을 결속하고, 공통 업무·도구 내부 절차·비교 진입을 분리했다. 신규 RED 실패 케이스가 최종 결과에 같은 이름으로 모두 남아 통과해야 비교 진입을 허용한다. 삭제·이름 변경·미해결은 HOLD이며 실제 실패 failed와 실행 불가 unknown을 구분해 필수5개 결과를 보존한다.
- 코드·리뷰: scripts/omc_validation_command.py, scripts/test_omc_validation_command.py, scripts/omc_native_command.py, scripts/test_omc_native_command.py, scripts/omc_workflow_validation.py, scripts/test_omc_workflow_validation.py 총6파일. 원본과 바이트가 같은 격리 후보 APPROVE·요청/실제 승인 범위6개 일치. 미커밋 상태이며 기존 fb57afa 진단 보완 커밋과 구분한다.
- 검증: 최종 보완 후보의 실행기·수집기 회귀88개 통과·skip0, 독립 리뷰 정상/실패/실행 불가3개 통과. 동일 후보 해시 확인·TDD exit0·실제 index 보존. 앞선 RED 결속 보완 후보의286개 통과는 이전 후보 이력이며88개·3개와 합산하지 않는다. 홈/DOM bridge는 합성 입력으로 실제 서비스 검수나 provider 실행 성공이 아니다.
- 실제 비교: 새 계약 실행0회. 기존9e21b70 실제 Claude 필수5검증·픽셀12조건·실제 홈 통과와 절차 unknown·전체 HOLD는 당시 결과로 보존한다.
- 다음: 고정 검증 준비 보완은 종료한다. 승인된 코드6파일과 이번 문서4파일의 커밋 범위를 구분해 결정한다. 이후 새 advertising-comparison/v2 계약·동일 baseline/모델/입력·경쟁 플러그인·후보 서버·실제 홈 준비를 확인하고, 양쪽 각 최대1회·자동 재시도0회의 실제 비교는 별도 승인 후 진행한다. 기존 비교 HOLD와 원본 증거를 소급 변경하지 않는다.
- 다음 작업: 리뷰 승인 코드 6파일 커밋 여부 결정
- 배포: 고정 검증 경로6파일은 미커밋·미릴리스·일반 사용처 미설치다. 기존 fb57afa 진단 보완은 로컬 커밋 완료·미릴리스·미설치 이력으로 유지한다. 기존0.6.0 정상11개 설치는 과거 이력이며 이번에 push·재설치·재감사하지 않았다.
- 경계: 이번 고정 검증 경로로 실제 Claude·oh-my-claudecode를 실행한 횟수는0회다. 새 계약의 로컬 allow는 단일 arm의 로컬 증거 판정이며 실제 동등 비교 성공·전체 임의 Bash 효과·경쟁 우위·일반 생산성을 입증하지 않는다. 과거9e21b70 단일 실행464.48초의 native_procedure unknown·전체 HOLD, 기존 광고 추적4실패와 클릭 추적·오늘 하루 숨김 통합 미검증을 유지한다.
- 관찰: v3 DISABLED, 완료 품질 OBSERVATION_INVALID / pending_completion_invalid. 코드 승인과 별도이며 자동 재등록하지 않는다.
- 근거: 로컬 `.omc/failure-state-task/REPORT.md`, `green-result.json`, `review/REPORT.md`, `review/record-result.txt`; 이전 후보는 `.omc/red-case-binding-task/REPORT.md`. 로컬 미추적 자료로 다른 checkout에 자동 제공되지 않는다.

아래 이전 이력의 ‘현재·다음’과 수치는 기록 당시의 상태이며, 이번 현행 상태나 실행 승인으로 재사용하지 않는다.

## 이전 이력 · 실제 Claude 검증 결과와 진단 보완 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 실제 Claude 필수 검증 통과 · 절차 비교 HOLD · 진단 보완 커밋 완료
- 하려던 작업·실제 결과: 9e21b70 후보에서 광고 Ad 테두리·그림자·카드 화살표 유지보수의 실제 Claude를 1회 실행했다. Sonnet4.6/high·464.48초·정상 종료·자동 재시도0회. CONTRACT 성공→실제 신규 RED 실패·영수증→RED 등록 성공→제품 수정 순서와 최종 필수5검증·픽셀12조건·실제 홈·완료 기록·모델 보고 일치를 확인했다. 수집기10/11항목 통과, native_procedure만 unsupported_bash_command로 unknown이므로 전체 비교 HOLD다.
- 이번 보완: 제한된 cat·sha256sum 인식과 호출 위치별 미지원 사유를 추가했다. 과거 상태를 현재 파일로 대신 검사하지 않으며 임의 실행·복합 셸은 확인 불가로 유지한다.
- 코드·리뷰: 진단 보완4파일만 fb57afa에 커밋했다. 격리 후보 APPROVE WITH NOTES이며 제안은 필수 검증이 모두 유효하고 절차만 unknown인 보고서 테스트 추가다.
- 검증: 진단 보완 관련 테스트123개 통과, 독립 리뷰 재실행123개 통과(동일 묶음, 합산하지 않음). TDD·커밋 훅 통과. 실제 광고 후보의 독립 테스트44개 통과·기존 추적4실패 유지·신규 실패0개, 독립 eslint 통과. 코드 로컬 검증과 실제 Claude 결과를 구분한다.
- 다음: 미지원 명령의 설명 보완은 종료했다. 기존 비교 HOLD와 원본 증거를 유지하고, 후속 비교의 필요성과 당시 명령 효과를 입증할 증거 수집 방식을 별도 결정한다. 준비 보완·Claude 재호출·경쟁 비교·릴리스를 자동 진행하지 않는다.
- 다음 작업: 기존 비교 HOLD 유지 · 후속 비교 방식 결정
- 배포: fb57afa 진단 보완4파일과 앞선9e21b70 등은 로컬 커밋 완료·미릴리스·일반 사용처 미설치 상태다. 이번 작업에서 원격 push·재설치·재감사를 하지 않았다. 기존0.6.0 정상11개 설치는 과거 이력이다.
- 경계: 464.48초 실행은 9e21b70의 단일 사례이며 fb57afa 이후 Claude 재호출은0회다. 알려진 성공 반환 순서는 확인했지만 임의 Bash 전체 효과를 입증하지 않았다. 기존46건은 명령 미지원21·구문 미지원14·당시 경로 증거 없음11로 분류됐고 모두 unknown을 유지한다. CLI의 과거 기록 재평가에서는 cat·sha256sum도 당시 경로 증거가 없으면 통과시키지 않는다. 최신 동등 경쟁 비교·속도 우열·일반 생산성·C1/C2/C3 효과는 미입증, 기존 광고 추적4실패와 실제 클릭 추적·오늘 하루 숨김 서비스 통합 미검증은 유지한다.
- 관찰: v3 DISABLED, 완료 품질 OBSERVATION_INVALID / pending_completion_invalid. 자동 재등록하지 않는다.
- 근거: 로컬 `.omc/command-native-rerun-20261019/REPORT.md`, `summary.json`, `collected/assessment.json`, `.omc/bounded-read-task/historical-projection.json`, `review-record-output.txt`. 실행 근거는 로컬 미추적 자료이며 다른 checkout에 자동 제공되지 않는다. 원본·이전 판정은 보존한다.

## 이전 이력 · 명령·진단·판정 보완 커밋 완료 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 명령·진단·판정 보완 커밋 완료 · 새 보완 후 실제 Claude 재검증 전
- 하려던 작업·실제 결과: 광고 Ad의 테두리·그림자와 카드 화살표를 고치려던 실제 Claude1회는 421.02초·정상 종료·자동 재시도0회였다. 테스트 신규7실패를 실행했지만 observe 명령의 2>&1이 고정 명령과 달라 호출 영수증이 생성되지 않았다. RED 등록과 제품 편집이 차단돼 전체 hold로 종료했다. 최종 필수5검증·픽셀12조건·native 홈 관측은 미실행이다.
- 확인된 안전 동작: CONTRACT 이후 테스트만 수정했고 제품 Ad.tsx·Ad.styled.ts는 수정하지 않았다. 실제 신규 실패가 있어도 호출 영수증·RED 등록이 없으면 제품 편집을 차단했다. 기존 추적4실패는 이름·진단 동일로 구분했다.
- 이번 보완: 단일 명령 끝의 2>&1을 제한적으로 인식하고 복합 명령은 계속 거부한다. 읽기 전용 diagnose로 명령·반환·영수증 누락을 구분하며, 반환 미확인 시 이전 영수증 유무와 무관하게 기존 호출부터 확인하도록 안내한다. 공식 판정과 모델 원문 상태를 별도 표시하고 불일치는 계속 보류한다.
- 코드·리뷰: 최종8파일을 격리 후보에서 APPROVE한 뒤 9e21b70으로 로컬 커밋했다. 경미했던 반환 대기 안내도 보완·재리뷰했다.
- 검증: 명령·진단 보완 당시 관련240개 통과·skip0. 반환 대기 안내 후속 보완 후 관련118개 통과·skip0, 최종 격리 후보 독립 재리뷰44개 통과·APPROVE. 묶음은 중복되므로 합산하지 않는다. 실제 임시 설치본·구문·TDD·커밋 훅 확인. 최종8파일을 9e21b70에 로컬 커밋했다.
- 다음: 9e21b70을 포함한 새 격리 후보에서 고정 baseline·Sonnet4.6/high·필수5검증·픽셀12조건을 유지하고 실제 Claude를 최대1회·자동 재시도0으로 실행한다. 실제 RED 실행→호출 영수증→등록 성공→제품 수정, 최종 실제 홈과 완료 기록·모델 보고 일치를 확인한다. 이번 문서 최신화로 모델 호출을 시작하지 않는다.
- 다음 작업: 9e21b70 후보로 실제 Claude 광고 사례 1회 재검증
- 배포: 9e21b70 보완8파일은 로컬 커밋 완료·미릴리스·일반 사용처 미설치·원격 push 전이다. 앞선 df4adbb 등도 미릴리스 이력을 유지한다. 기존0.6.0 정상11개 설치는 과거 이력이며 이번에 재설치·재감사하지 않았다.
- 경계: 확인된 개선은 유효한 RED가 없을 때 제품 편집을 차단한 것이다. 421.02초 실행은 새 가드 df4adbb 이후지만 명령·진단 보완9e21b70 이전이다. 9e21b70 이후 실제 Claude 호출0회이며 정상 완료 경로의 실제 모델 성공은 미검증이다. 보고 형식은 통과했지만 모델 상태 문장은 공식 판정과 달랐다. native_procedure는 unknown이며 실제 제품 선행 편집으로 단정하지 않는다. 최신 동등 경쟁 비교·속도 우열·일반 생산성·C1/C2/C3 효과는 미입증, 기존 광고 추적4실패 유지. 로컬 해시·수집 검사는 의미 적합성이나 악의적 변조 방어를 인증하지 않는다.
- 관찰: v3 DISABLED, 완료 품질 관찰 OBSERVATION_INVALID / pending_completion_invalid. 자동 재등록하지 않는다.
- 근거: 로컬 `.omc/postfix-native-rerun-20261019/REPORT.md`, `.omc/native-command-fix/REPORT.md`, `.omc/native-command-pending-fix/REPORT.md`, `review/REPORT.md`, `review/approval.json`. 이전 원문·판정은 보존한다.

## 이전 이력 · 절차·RED 근거 보완 커밋 완료 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 절차·RED 근거 보완 커밋 완료 · 보완 후 실제 Claude 재검증 전
- 실제 실행: 검수 입력 복구 후 Sonnet4.6/high 실제 Claude1회·851.18초·정상 종료·자동 재시도0회. 호출별 홈 관측 영수증 명시 선택과 픽셀12/12는 통과했다. 제품 편집이 CONTRACT·RED 등록보다 먼저였고 실제 통과한 케이스가 RED 실패로 등록됐다. 보고 형식 failed·최종 실제 홈 결속 unknown으로 전체 판정 hold를 유지한다.
- 이번 보완: 현재 CONTRACT 전 Tier1 편집 차단, 고정 Jest의 실제 신규 실패와 native 호출 영수증에 연결한 RED 등록, 등록 성공 반환 이후 제품 수정 순서 대조를 추가했다. 복합 셸은 unknown으로 보류하며 기존 Tier0 예외와 명시 native RED 차단을 함께 유지한다.
- 코드·리뷰: 전체11개 후보 APPROVE 후 `df4adbb`에 해당11개 파일만 로컬 커밋했다. 리뷰 승인 receipt는 원본과 동일한 격리 후보11개에 결속됐다.
- 검증: 구현 보완 회귀321개 통과(핵심115·홈/픽셀/설치206), skip0. 격리 후보 전체11개 재리뷰 APPROVE·핵심115개 재실행 통과(321과 중복). 실제 Git 훅·TDD·구문·템플릿/배포본 일치 확인. 승인된11개 파일만 df4adbb로 로컬 커밋.
- 다음: df4adbb를 포함한 새 격리 후보에서 고정 baseline·모델·필수5검증·픽셀12조건을 유지하고 native RED 실행·영수증 선택·등록 성공 반환 후 제품 수정을 확인한다. 보고 형식·최종 실제 홈 결속의 미해결 조건도 사전에 고정한 뒤 최대1회 실행한다. 이번 최신화로 모델 호출을 시작하지 않는다.
- 다음 작업: df4adbb 후보로 실제 Claude 광고 사례1회 재검증
- 배포: df4adbb 절차·RED 보완11파일은 로컬 커밋 완료·미릴리스·일반 사용처 미설치·원격 push 전. d4496e8·cf38f24 역시 미릴리스 이력 유지. 기존0.6.0 정상11개 설치는 과거 이력이며 이번에 재감사하지 않았다.
- 경계: 홈 관측·픽셀 통과와 전체 절차 준수는 별개다. 보완 후 실제 Claude 실행0회. 보고 첫 줄/길이와 최종 실제 홈 결속 문제는 별도 미해결이다. 최신 양쪽 동등 비교·속도 우열·일반 생산성·C1/C2/C3 효과는 미입증이며 기존 광고 추적4실패는 유지한다. 로컬 근거 일관성 검사이며 테스트 의미·악의적 동일 계정 변조·Codex 물리 훅을 인증하지 않는다.
- 관찰: v3 DISABLED, 완료 품질 관찰 OBSERVATION_INVALID / live_observation_not_enabled. 자동 재등록하지 않는다.
- 근거: 로컬 `.omc/home-native-rerun-20261019/REPORT.md`, `procedure-audit.json`, `.omc/native-red-review-fix/REPORT.md`, `review/REPORT.md`, `review/approval.json`. 기존 실행 원문·이전 판정은 보존한다.

## 이전 이력 · 검수 입력 복구·커밋 완료 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 홈 관측 보완 커밋·검수 입력 복구 완료 · 실제 Claude 재검증 전
- 코드·리뷰: 홈 관측 보완5파일은 `d4496e8`, 복구 입력·안내·테스트5파일은 `cf38f24`로 로컬 커밋했다. 복구5파일은 원본과 바이트가 같은 격리 후보에서 리뷰 APPROVE이며 승인 범위도5개로 일치한다.
- 중단과 복구: 실제 Claude 재검증을 준비했으나 이전 임시 폴더의 고정 입력이 없어 호출0회로 중단했다. 이후 픽셀 builder와 홈 광고 입력을 원본 해시 그대로 복구해 `docs/comparison-dashboard/evidence/ad-validation-inputs-v1/`에 보존했다. 실행 불가를 제품·모델의 신규 실패로 계산하지 않는다.
- 검증: 검수 입력 복구 격리 후보 리뷰 APPROVE·12개 검사 통과. 원본 해시2개·과거 픽셀 정책 일치, 새 폴더 복원·손상/누락·덮어쓰기/외부 링크 거부·실제 baseline Ad 빌드 확인. 홈 관측 보완의 기존182개/92개 회귀 결과는 별도 이력(중복 포함). 실제 Claude 새 호출0회.
- 다음: 복구한 고정 입력으로 새 격리 후보를 준비하고 서버·로그인·광고·후보 결속을 확인한 뒤 실제 Claude 광고 사례를 최대1회 재검증한다. 기존 필수5검증·픽셀12조건·관측 증거 명시 선택·최종 완료 기록 일치를 유지한다. 이번 문서 커밋은 모델 호출을 시작하지 않는다.
- 다음 작업: 복구 입력으로 새 후보 준비 후 실제 Claude 광고 사례1회 재검증
- 배포: d4496e8 홈 관측 보완과 cf38f24 검수 입력 복구는 로컬 커밋 완료·미릴리스·일반 사용처 미설치. 기존0.6.0 정상11개 설치는 과거 이력이며 이번에 재감사하지 않았다.
- 경계: 최신 경쟁 실행은 execute 호출·성공 반환 후 수정이 확인됐고 항목별 결과를 채택했다. native 홈 자동 전환의 두 표본은 같아 확인 불가이며 UI 실패로 단정하지 않는다. 운영자 사후 관측으로 대체하지 않는다. 최신 양쪽 동시 비교·속도 우열·일반 생산성·C1/C2/C3 효과는 미입증이며 기존 광고 추적4실패는 유지한다. 복구 입력 확인은 실제 홈·화면12개·native Claude 재검증 통과를 뜻하지 않는다. 환경·인증·서버 설정은 보존 묶음에 포함하지 않는다.
- 관찰: v3 DISABLED, 완료 품질 관찰 OBSERVATION_INVALID / pending_completion_invalid. 리뷰 승인과 별개이며 자동 재등록하지 않는다.
- 근거: `docs/comparison-dashboard/evidence/ad-validation-inputs-v1/README.md`, `manifest.json`, 로컬 `.omc/ad-input-recovery-20261019/review/REVIEW.md`. 기존 실행 원문은 소급 수정하지 않는다.

## 이전 이력 · 홈 관측 증거 연결 구현·리뷰 승인 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 홈 관측 증거 연결 구현·리뷰 승인 · 실제 Claude 재검증 전
- 개선: 기존6회 시간 관측을 재사용해 호출별 증거를 보존하고 home_motion_result의 경로·해시로 완료 검증에 쓸 관측을 명시적으로 선택한다. 미선택·증거 부족은 보류한다. 기존 준비 판정·마지막 대기는 유지한다.
- 코드·리뷰: 홈 관측 모듈·가드·준비·수집기·테스트5파일은 APPROVE·스테이징 상태이며 미커밋이다. 원본과 바이트가 같은 격리 후보5파일에만 승인 receipt를 기록했다.
- 검증: 리뷰 보완 회귀182개 통과. 마지막 호출별 분리 경계 및 격리 후보 재리뷰에서 홈·완료92개 통과(묶음 간 중복). 실제 격리 브라우저·문법·TDD 게이트 통과. 실제 Claude 재실행은 미수행.
- 최신 경쟁 실행: Sonnet4.6/high1회·686.72초. execute 호출13·성공 반환14·첫 수정58을 확인했다. 코드·테스트·린트·통제 화면·픽셀12/12는 항목별 채택했다. native 홈 자동 전환은 두 표본이 같아 확인 불가이며 전체 검증 완료로 승격하지 않는다.
- OMC 선행 참조: 1070.8초·필수5검증·픽셀12/12·수집9/9는 기존 단일 사례 근거다. 최신 경쟁 실행과 속도·우열을 비교하지 않는다.
- 다음: 리뷰 승인된 구현5파일을 커밋한 뒤 보완된 OMC로 실제 Claude 광고 사례를1회 재검증한다. 새 관측 선택·후보 결속·완료 기록 일치를 확인하며, 이 문서 커밋으로 모델 호출을 시작하지 않는다.
- 다음 작업: 구현5파일 커밋 후 실제 Claude 광고 사례1회 재검증
- 배포: 이번 홈 관측 보완은 미커밋·미릴리스·일반 사용처 미설치. 앞선7e417dc·1a3585f도 미릴리스 이력 유지. 기존0.6.0 정상11개 설치는 과거 이력이며 재감사하지 않았다.
- 경계: 최신 경쟁 실행은 execute 호출·성공 반환 후 수정이 확인됐고 항목별 결과를 채택했다. native 홈 자동 전환의 두 표본은 같아 확인 불가이며 UI 실패로 단정하지 않는다. 운영자 사후 관측으로 대체하지 않는다. 최신 양쪽 동시 비교·속도 우열·일반 생산성·C1/C2/C3 효과는 미입증이며 기존 광고 추적4실패는 유지한다.
- 관찰: v3 DISABLED, 완료 품질 관찰 OBSERVATION_INVALID / live_observation_not_enabled. 자동 재등록하지 않는다.
- 근거: `.omc/peer-entry-first-20261019/correction-003/REPORT.md`, `.omc/home-observation-analysis-20261019/REPORT.md`, `.omc/home-motion-review-fix/REPORT.md`, `.omc/home-motion-review-fix/review/REVIEW.md`, `.omc/home-motion-review-fix/review/approval-receipt.json` (로컬 실행 기록; 이번4문서 범위에 포함하지 않음).

## 이전 이력 · 실행 기록 판정 개선 커밋 · 경쟁 비교 표본 부적격 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 실행 기록 판정 도구 구현·리뷰·커밋 완료 · 경쟁 비교 표본 부적격
- 개선: 도구 진입·수정 순서·공통5검증을 분리한다. 호출ID와 성공 반환을 연결하고, 해석 불가 Bash·불완전 기록은 확인 불가로 둔다. 합성 테스트와 실제 실행·운영자 사후 검증을 구분한다. 실행을 차단하는 장치는 아니다.
- 코드·리뷰: `7e417dc`에 분석기·테스트2파일만 커밋했다. 원본과 동일한 격리 후보2파일 리뷰 APPROVE이며 승인 receipt는 해당 격리 후보에만 결속된다.
- 검증: 신규 CLI 보고 회귀15개 재검증 통과. 인접 회귀 포함81개 통과·2개 건너뜀, TDD 게이트 통과. 원문246개 이벤트에서 execute 호출0회와 성공 반환 없는 코드 수정을 관찰했다. 원문·보존56개 해시 불변.
- 경쟁 실행: Sonnet4.6/high 실제 Claude1회·740.57초. 플러그인5.6.2 로드·execute 등록 확인, 실제 execute 호출0회로 비교 표본 부적격. 실행 중 픽셀 검사는 모듈 상대 경로 오류로 unknown이며 브라우저 충돌로 확정하지 않는다. 사후 운영자 픽셀12/12·독립 테스트41통과/동일 기존4실패·신규0·린트 통과는 별도 사실이다. 자동 재실행0회.
- OMC 선행 참조: 1070.8초·필수5검증·픽셀12/12·수집9/9 통과는 기존 단일 사례 근거로 유지한다. 이번 경쟁 실행과 속도·도구 우열을 판단하지 않는다.
- 다음: 7e417dc 분석 결과를 기준으로 실제 execute 호출·성공 반환을 확인할 재실행 방식과 횟수를 별도 계획에서 확정한다. 현재 분석기는 읽기 전용 관찰이며 실행 생략을 예방하지 않는다. 추가 모델 호출은 별도 승인 전 시작하지 않는다.
- 다음 작업: 실제 execute 진입을 확인할 재실행 방식·횟수 확정
- 배포: 7e417dc 분석기와 앞선1a3585f 보완은 미릴리스·일반 사용처 미설치. 기존0.6.0 정상11개 설치는 과거 이력이며 재감사하지 않았다.
- 경계: 공통5개 검증은 분석기에서 자동 평가하지 않고 unknown으로 남긴다. Skill 성공 반환은 절차 준수나 품질 통과가 아니다. 이번 경쟁 실행의 플러그인 로드는 확인됐지만 execute 호출0회로 비교 표본 부적격이다. 사후 운영자 픽셀12/12 통과는 실행 중 픽셀unknown을 대체하지 않는다. 도구 우열·속도·일반 생산성 효과는 미입증이다. 기존 광고 추적4실패와 C1/C2/C3 효과 미검증은 유지한다.
- 관찰: v3 DISABLED, 완료 품질 관찰 OBSERVATION_INVALID / pending_completion_invalid. 리뷰 판정과 별개이며 자동 재등록하지 않는다.
- 근거: `.omc/peer-native-20261019/REPORT.md`, `.omc/peer-trace-analysis/REPORT.md`, `historical-analysis.json`, `review/approval-receipt.json`. 로컬 실행 원문은 이번4개 문서 범위에 포함하지 않는다.

## 이전 이력 · 실제 Claude 광고 검증·완료 기록 연결 통과 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 실제 Claude 광고 단일 사례 필수 검증·완료 기록 연결 통과 · 리뷰 APPROVE WITH NOTES
- 목표 도달 수준: 필수 검증을 생략하지 않고 최종 코드와 완료 기록을 연결하는 실제 단일 사례를 확인했다. 일반 생산성·경쟁 우위 입증이나 전체 목표 완료율을 뜻하지 않는다.
- 실제 실행 결과: Sonnet4.6/high 실제 Claude1회·1070.8초 정상 종료. 필수5검증·화면12/12·독립 수집9/9 통과, 최종 코드 snapshot·verification·complete-task·원문 일치. 테스트48통과·기존 추적4실패 이름/진단 동일·신규0, 린트0·실제 홈·광고2이미지·자동 전환·후보 서버 결속 통과. 광고3파일 고정 리뷰 APPROVE WITH NOTES.
- 무엇이 달라졌나: 실행 전 브라우저 모듈 절대 경로를 실제 로드해 확인하고, 검사마다 새 결과 폴더를 보존했다. 마지막 수정 뒤 새 verification-template과12개 결과를 연결해 complete-task와 최종 원문까지 남겼다. 제품 코드 수정 없이 준비 안내를 보완한 재검증이다.
- 이전 시도: 1201.21초 시간 초과 사례는 화면12개 통과 후 최종 기록 갱신·원문 생성 전에 종료돼 hold로 보존한다. 더 이전612.04초 사례의 독립 픽셀12개 실패·수집8항목 통과도 당시 이력으로 유지한다.
- 리뷰: 요청·실제 범위 Ad.styled.ts, Ad.tsx, Ad.test.tsx 3개 일치. APPROVE WITH NOTES. 원문은 기계 계약 제외22물리행으로20행 제한을2행 초과한다. 수집기의 서식 통과는 보고 길이 준수를 뜻하지 않는다.
- 실행 기준: b67f092(필수 화면 검증 보완1a3585f 포함). 이번4개 문서 최신화는 제품 코드 변경·새 릴리스가 아니다.
- 다음: 동일 광고 baseline·모델·예산·필수 화면12조건·실제 홈 환경을 제공하는 oh-my-claudecode 비교를 준비한다. OMC 전용 명령·기계 출력 형식을 상대에 강요하지 않고 품질·재작업·사람 개입을 독립 평가한다. 실제 실행은 별도 요청 후 진행한다.
- 다음 작업: 동일 화면 검증 계약으로 oh-my-claudecode 비교 준비
- 배포: 0.6.0 로컬 릴리스2f0f521·정상11개 설치는 이전 이력이다. 1a3585f 보완은 미릴리스·일반 사용처 미설치이며 이번에 재감사하지 않았다.
- 경계: 단일 사례에서 필수 검증과 최종 완료 기록의 연결을 확인했으며 최신 양쪽 비교·일반 생산성·경쟁 우위는 미입증이다. 픽셀 검사는 정의한 직선 테두리와 이미지 표식에 한정된다. 기존 광고 추적4실패·클릭/숨김 외부 전송·C1 사용자 효과·C2 유지보수 이점·C3 사용자 이해는 미해결 또는 미검증이다. 원문은 기계 계약 제외22물리행으로20행 제한을2행 초과했고 수집기의 report_format은 길이를 검사하지 않는다. 이번 보완은 미릴리스·일반 사용처 미설치이며 기존0.6.0 정상11개 설치 이력은 재감사하지 않았다.
- 관찰: v3 DISABLED, 완료 품질 관찰 OBSERVATION_INVALID / pending_completion_invalid. 리뷰 승인·화면 판정과 별개이며 관찰 원장을 수정하거나 자동 재등록하지 않았다.
- 근거: 로컬 .omc/final-native-20261019/REPORT.md, summary.json, collected/assessment.json, verification.json, review/summary.json. 직전 시간 초과는 .omc/visual-native-20261019/REPORT.md. 원문·이미지·스냅샷은 로컬 기록으로 보존하며 이번4개 문서 범위에는 포함하지 않는다.

## 이전 이력 · 필수 화면 검증·완료 판정 결속 보완 커밋 · 2026-10-19

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 필수 화면 검증·완료 판정 결속 보완 커밋·리뷰 승인 · 새 보완 후 Claude 재실행 전
- 목표 도달 수준: 잘못된 완료 판단을 막는 기반 구현은 진전됐으나 실제 업무 효과 입증은 미완료다. 공통 분모가 없는 완료율을 임의 퍼센트로 표시하지 않는다.
- 실제 실행 결과: 올바른 후보 서버에서 Sonnet4.6/high를1회 실행했다(612.04초). 서버·실제 홈·광고 이미지2개·자동 전환과 native 절차·범위·서식8항목은 통과했지만, 독립 픽셀6종×DPR1/2=12개는 모두 실패했다. 모델의 완료 보고와 수집allow를 전체 성공으로 취급하지 않으며 종합 판정은failed/hold다.
- 발견 원인: 실행 전 별도 필수 평가였던 픽셀 검사가 native의 네 승인 항목에 포함되지 않았다. 이전 승인 기준·native 원문·수집allow를 사후 수정하지 않고 독립 판정과 구분해 보존했다.
- 이번 개선: 실행 전 필수 광고 유형·정책·builder를 고정하고 승인 후 필수 여부 하향을 차단한다. 수정 후 같은 실행·정책·현재 후보에 속하는12개 화면 결과를 모두 재판정한다. 완료 명령과 수집기가 같은 함수를 사용하고, 보고서별 오류가 있어도 나머지를 검사한 뒤 실패>증거 불일치>미검증 순으로 결정한다.
- 검증: 화면·완료·수집 회귀118개 통과, 가드 회귀101개 통과, 고정7파일 독립 리뷰의 실제 Chromium 정·역순·중복·정상 화면12개 통과·APPROVE. 문법·변경 형식·TDD 게이트 통과. 승인본과 동일한7파일만1a3585f로 로컬 커밋했다. 전체 저장소 품질 게이트·실제 Claude 새 실행의 통과를 뜻하지 않는다.
- 커밋 범위: docs/visual-controlled-check.md, scripts/omc_pipeline_guard.py, scripts/omc_visual_check.py, scripts/omc_workflow_preflight.py, scripts/omc_workflow_validation.py, scripts/test_omc_visual_check.py, scripts/test_omc_workflow_preflight.py 총7개.
- 다음: 1a3585f 보완을 포함한 새 격리 후보에서 advertising-visual/v1 필수 유형·고정 정책·기존 필수 검증과 12개 화면 결과를 승인 계약에 연결한다. 실제 홈·광고·자동 전환·서버·후보 결속을 확인한 뒤 실제 Claude 광고 사례를 1회 재검증한다. 이 검증 통과 후 동일 조건의 oh-my-claudecode 비교를 별도로 진행한다.
- 배포: 0.6.0 로컬 릴리스2f0f521·정상11개 설치는 이전 이력이다. 이번1a3585f 보완은 버전 변경·릴리스·재설치·원격push 전이다.
- 경계: 1a3585f 보완 후 실제 Claude 재실행·최신 양쪽 비교·일반 생산성 및 경쟁 우위는 미입증. 이번 자동 검사는 정의한 테두리 직선 구간과 이미지 표식에 한정된다. C1 사용자 효과·C2 유지보수 이점·C3 사용자 이해·기존 광고 추적4실패와 클릭/숨김 외부 전송은 미검증 또는 미해결이다. 이번 보완은 미릴리스·사용처 미설치이며 기존0.6.0 정상11개 설치 이력과 구분한다.
- 근거: [화면 검증 계약 사용법](../visual-controlled-check.md). 실행·검증·리뷰 원문은 로컬 .omc/bound-native-20261018/ 및 .omc/visual-order-fix/에 보존한다. 이번 문서 커밋에는 원문·별도 교훈·제품 코드를 추가하지 않는다.

## 이전 이력 · 서버·후보 결속 보완 커밋 · 2026-10-18

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 서버·후보 결속 보완 커밋·리뷰 승인 · 보완 후 실제 재실행 전
- 목표 도달 수준: 잘못된 완료 판단을 막는 기반 구현은 진전됐으나 실제 업무 효과 입증은 미완료다. 공통 분모가 없는 완료율을 임의 퍼센트로 표시하지 않는다.
- 실제 실행 결과: 0.6.0 실제 Claude 광고 사례1회는 실행됐다(968.44초). 실제 홈이 이전 경쟁 후보 서버에 연결돼 홈 검증은 유효하지 않았다. 기존blocked·첫 줄 서식 실패는 보존하며, 후보의 통제 픽셀12조합 통과와 절차 일부 준수 관측을 전체 성공으로 승격하지 않는다.
- 원인: 조정자가 점유된3025 포트를 재사용했고 새 후보 시작은EADDRINUSE로 실패했다. URL·이미지·자동 전환만 확인해 이전 경쟁 도구 서버를 새 후보로 오인했다.
- 이번 개선: 실제 수신 프로세스의 시작 시각·계보·작업 폴더와 홈이 참조한 번들의 응답을 후보 파일에 연결한다. 실제 스크립트 쿼리의 모든 버전을 대조하고, 서버 확인 불가와 확인된 범위 위반을 독립적으로 보존한다. 잘못된 서버로 효과를 판정하는 오류를 막기 위한 보완이다.
- 검증: 서버 결속 관련 회귀117개 통과(103.12초), 독립 리뷰 재검증28개 통과(23.12초), TDD·문법 검사 통과·격리4개 파일 APPROVE. 승인본과 원본의 바이트 일치 확인 후91c8d4e로 로컬 커밋했다.
- 커밋 범위: docs/workflow-preflight.md, scripts/omc_workflow_preflight.py, scripts/test_omc_workflow_preflight.py, scripts/test_omc_workflow_server_binding.py 총4개. 기존 실행 자료·교훈·다른 변경은 제외·보존.
- 다음: 91c8d4e 후보와 새 manifest를 고정하고, 가용 포트·실제 수신 프로세스·작업 폴더·홈 번들 일치를 확인한 뒤 실제 Claude 광고 사례 1회를 재검증한다. 통과 후 동일 조건의 oh-my-claudecode 비교를 별도로 진행한다.
- 배포: 0.6.0 로컬 릴리스2f0f521·정상11개 설치는 이전 이력이다. 이번91c8d4e 보완의 릴리스·재설치·원격push·태그·GitHub release는 수행하지 않았다.
- 경계: 보완 후 실제 Claude 재실행·최신 양쪽 비교·일반 생산성 및 경쟁 우위는 미입증. C1 사용자 효과·C2 유지보수 이점·C3 사용자 이해·광고 추적 기존4실패와 클릭/숨김 외부 전송은 미검증 또는 미해결이다. 이번 보완은 미릴리스·사용처 미설치이며, 기존0.6.0 정상11개 설치 이력과 구분한다.
- 근거: 커밋91c8d4e의 코드·회귀·사용법 문서는 저장소에 보존된다. 실행 원문은 로컬 .omc/release-060/native-validation-summary.json, .omc/release-060/home-mismatch-investigation.md, .omc/server-binding-fixes/regression.txt, .omc/server-binding-fixes/approval-record.txt에 보존하며 이번 문서 커밋에는 포함하지 않는다.

## 이전 이력 · 0.6.0 릴리스·설치 완료 · 2026-10-16

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 통제 화면 자동 판정 V0 · 0.6.0 로컬 릴리스·정상11개 재설치 완료.
- 커밋·반영: 자동 화면 판정6개 파일은9160448로 main에 반영됐다. 기존 대기10개는 f2cc2ef로, 비교 보고서·근거286개는3d96423으로 별도 커밋했다. 업무 파일·로컬 실행 기록·설정·별도 교훈은 보존했다.
- 이번 개선: CSS 선언이나 모델 보고 대신 실제 캡처 픽셀로 이미지 위 테두리·표식을 판정한다. 승인된 필수 검사에서 결과 누락·증거 불일치·실패·판정 불가는 전체 통과를 막는다.
- 검증: 0.6.0 전체 품질4474개 통과·28개 생략·12개 제외(595.24초). 별도 실제 Chromium 화면·준비 검사49개 통과(61.95초), 버전32개 통과. main 후보352파일 리뷰249개 통과·APPROVE 후 릴리스 메타데이터3파일 검토·355파일 승인 결속READY. 설치11개 엄격 감사·0.6.0·clean·source unchanged; 설치 직전 업무 파일34224개·관찰 기록114개 변경0.
- 환경 복구: 초기 전체 검사의3건은 Git에서 제외된 Claude 명령·스킬 설치 파일 부재로 실행 불가였다. 변경 전 기준본에서도 재현했고, 후보 자체 templates에서 기존 설치기를 비강제로 실행한 뒤 전체 검사를 통과했다. 제품6파일·승인·품질 설정은 바뀌지 않았으며 이전 실패 기록은 보존했다.
- 다음: 0.6.0 릴리스2f0f521을 기준으로 고정 manifest·후보·화면 검사 기준을 승인 조건에 포함해 실제 Claude 광고 사례 1회를 재검증한다. 같은 자동 판정을 적용한 양쪽 새 비교는 후속으로 남긴다.
- 릴리스·설치: `2f0f521`로0.6.0 로컬 릴리스, 정상11개 재설치·엄격 감사 완료. 원격 push·태그·GitHub release 없음.
- 경계: 자동 판정은 정의한 직선 구간과 이미지 표식에 한정된다. 자동 판정을 적용한 실제 Claude 광고 재실행·새 양쪽 비교는 미실행이며, 기존 광고 추적4실패·클릭/숨김 외부 전송·C3 사용자 이해·일반 생산성 및 경쟁 우위는 미해결 또는 미입증이다. 0.6.0 로컬 릴리스·정상11개 설치는 완료했으며 원격 push·태그·GitHub release는 하지 않았다.
- 관찰: v3 `DISABLED`, 완료 품질 `OBSERVATION_INVALID / pending_completion_invalid`는 코드 승인·품질검사와 별개다.
- 근거: 로컬 브랜치 `codex/visual-release-candidate`의 `9160448f07f09c0904a566828aa0d4f9985cb4c2`(6파일), `.omc/release-candidate-six/commit-result.json`, `.omc/release-candidate-six/environment-task/environment-full-quality.txt`, `.omc/release-candidate-six/environment-task/review-result.json`. 실행 원문은 로컬 기록이며 이번4개 문서 변경에 포함되지 않는다. 아래 현재·다음 표현은 각 시점의 이력이다.

- 자료 정리: 비교 보고서·근거286개는 `3d96423`, 완료 보고 회귀·경계 근거10개는 `f2cc2ef`로 커밋했다. 관련 회귀80개·합성 Node 테스트9개 통과.
- 보관 범위: 로컬 보관 근거265개 해시 일치. `ad-paired-recompare-20261015`의14개 원문은 과거 임시 경로 참조만 남아 있어 전체 원문 보관을 뜻하지 않는다.

- 설치 보존: 설치본·receipt 백업 `/private/tmp/omc-060-install-backups-v2`, 검증 `/private/tmp/omc-060-install-results/verified.json`. 설치 직전 업무34224개·관찰114개 변경0. marketing-project 제외 유지.

## 당시 이력 · 통제 화면 자동 판정 V0 구현·리뷰 승인 · 2026-10-16

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 통제 화면 자동 판정 V0 구현·리뷰 승인 · 실제 Claude 재검증 전. 준비 검사·범위/보고 수집기 보완은 `9255a00` 커밋 완료. 자동 화면 판정 구현 4개 파일은 리뷰 승인됐으나 아직 미커밋이다.
- 실제 Claude·경쟁 비교: 동일 공통 입력·Sonnet4.6/high 순차 단일 사례: OMC486.24초·49통과/기존4실패·엄격8항목 통과, 상대611.43초·41통과/기존4실패·테두리 가림. 양쪽 실제 브라우저 검증·범위 준수·사람 교정0회. 상대 플러그인5.6.2 로드와 execute 호출 확인. 검증 수행과 요구 충족을 구분하며 속도 우위로 일반화하지 않는다.
- 이번 개선: CSS 선언이나 모델의 완료 주장 대신 실제 캡처 픽셀로 이미지 위 테두리를 판정한다. 승인 기준에 화면 검사 ID가 있으면 결과 생략으로 통과할 수 없다.
- 독립 검증: 구현 회귀299개 통과, 격리 4파일 독립 리뷰297개 통과·APPROVE. 두 정상 방식×6종×DPR1/2=24개 모두 passed. 기존 실제 Ad 코드의 별도 통제 fixture 캡처 재판정: 정상12개 passed·테두리 가림12개 failed. 결과 누락·후보/manifest/증거 불일치 및 failed/unknown은 수집기 hold.
- 다음: 리뷰 승인된 자동 판정 구현 4개 파일을 별도 커밋한 뒤, 고정 manifest·후보·화면 검사 기준을 승인 조건에 포함해 실제 Claude 광고 사례 1회를 재검증한다. 동일 자동 판정을 적용한 양쪽 새 비교는 별도 실행으로 남긴다.
- 경계: 자동 판정은 정의한 직선 구간과 이미지 표식만 검사한다. 실제 홈·Claude 새 실행은 이번 구현·리뷰에서 미실행. 기존 추적 테스트4실패·클릭/숨김 외부 전송·C3 사용자 이해·일반 생산성 및 경쟁 우위는 미해결 또는 미입증. 이번 보완 미릴리스·일반 사용처 미설치; 기존0.5.9 설치 이력은 별도다.
- 기록: 이번 커밋은 로드맵·대시보드 4개 문서만 포함한다. 자동 판정 코드·새 비교 보고서/원문 근거는 로컬 미커밋 자료이며 이번 문서 커밋에 포함되지 않는다. 기존 스테이징12개는 보존한다. 완료 품질 관찰은 `OBSERVATION_INVALID / pending_completion_invalid`, v3는 `DISABLED`로 코드 승인과 별개다.
- 로컬 참고(이번 커밋 미포함): `docs/ad-preflight-native-validation-20261016.md`, `docs/ad-competitor-parity-validation-20261016.md`, `docs/visual-controlled-check.md`, `.omc/visual-task/summary.md`. 아래 현재·다음 표현은 각 시점의 이력이다.

## 당시 이력 · 보완 후 실제 Claude 단일 사례 관측 · 2026-10-16

- 목표: oh-my-claudecode의 강점을 흡수해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 현재 판정: 실제 Claude 재실행 완료 · 두 보완 동작 확인 · 범위·서식 전체 미통과. 코드 보완 커밋 `f1b048a`·9개 파일 리뷰 APPROVE 이력은 유지한다.
- 확인: 실제 Sonnet4.6/high 1회·1117.89초. 새 요구7개 RED 후 구현, 정상 폴더 링크 처리 관측. 독립48통과·기존4실패 동일·린트0·통제6종/실제 홈 확인. 수집6/6 passed이나 범위 밖 public/.gitignore 추가 및 첫 줄·단일 다음 행동 서식 위반. 조정자의 기존 로컬 제외 설정 누락으로 동일 환경 전후 비교 전체 통과 아님.
- 환경 경위: 새 복사본에 이전 Git 로컬 산출물 제외 규칙을 조정자가 누락했다. 모델은 차단 해결을 위해 허용 범위 밖 `.gitignore`를 생성·스테이징했다. 환경 오류와 범위 위반을 분리하며 실행 후 성공으로 구제하지 않는다.
- 다음: 기존 검수 빌드 산출물 제외 설정까지 환경을 사전 동결·대조한다. 범위 밖 변경 시 승인 요청과 최종 보고 서식은 보완 항목으로 남기며 추가 native 실행은 자동 시작하지 않는다.
- 경계: 두 보완 동작 관측과 전체 절차 준수는 별개. 준비 환경 차이·범위 위반·서식 위반 보존. 일반 경쟁 우위·생산성·클릭/숨김 실제 전송·C3 사용자 이해 효과 미입증. 미릴리스·일반 사용처 미설치. 기존 비교·12개 staged 변경·로컬 작업물은 보존, 추가 모델·경쟁 도구 재호출0.
- 근거: [실제 Claude 재실행 결과](../ad-postfix-native-validation-20261016.md). 아래 현재·다음은 당시 이력이다.

## 당시 이력 · 폴더 링크·RED 보완 커밋 완료 · 2026-10-16

- 목표: oh-my-claudecode의 강점을 흡수하고 부족한 부분을 개선해 실제 업무를 정확하고 적은 개입으로 끝내는 OMC를 만든다.
- 실제 광고 비교: 같은 Sonnet4.6/high·공통 화면 검증 환경에서 양쪽 실행. OMC 테두리 요구 충족, 상대는 이미지에 선이 가려져 미충족. 양쪽 실제 홈 이미지·자동 전환 확인. OMC975.17초 / 상대651.83초, 실행 중 사람 교정 지시 각0회. 단일 순차 사례이며 준비·로그인 비용은 별도다.
- 발견과 보완: 당시 OMC는 정상 폴더 링크 때문에 BLOCK했고 기존 추적 실패를 새 요구의 RED로 사용했다. 내부 폴더 링크의 대상 변경 감지·외부/순환 차단과 승인 기준에 연결된 새 실패 근거 검사를 보완했다. 과거 BLOCK·서식 위반·기존 광고 false completion은 소급 통과시키지 않는다.
- 구현·리뷰·커밋: 9개 파일 APPROVE, 로컬 커밋 `f1b048a`. 구현 단계 관련250개 통과; 격리 9개 파일 리뷰248개 통과·APPROVE; 중첩 링크 변경 감지·순환 차단 확인; 커밋 가드·TDD 통과. 신규 문서2개 EOF 빈 줄 경고 유지. CLI 복사본 검증은 native Claude 재실행이 아니다.
- 검증 경계: 광고 후보 복사본에서 완료 CLI·수집기 통과와 화면 관찰을 확인했지만 새 절차를 실제 Claude가 수행한 증거는 아니다. RED 기록의 진위·요구의 의미 관련성을 가드가 인증하지 않는다.
- 배포: 이번 보완 미릴리스·일반 사용처 미설치. 기존0.5.9 설치 이력은 별개이며 재감사하지 않았다. 나머지 staged12개와 로컬 자료는 보존했고 push는 하지 않았다.
- 다음: 보완한 OMC로 실제 Claude 단일 광고 사례를 재실행해 정상 폴더 링크 처리·요구별 RED 근거·네 필수 검증·정확한 완료 보고를 확인한다. 릴리스·일반 사용처 재설치는 별도 승인 대상으로 남긴다.
- 한계: 동일 광고 사례1회 비교이며 도구 전체 우열·생산성 효과는 미입증. 보완 후 실제 Claude 재실행·릴리스·일반 사용처 재설치 미실행. 클릭 추적·숨김 실제 이벤트 전송과 C3 사용자 이해 효과는 미검증.
- 근거: [실제 광고 비교](../ad-ui-browser-pair-20261016.md), [보완 검증](../task-link-red-hardening-20261016.md). 아래 현재·다음 표현은 각 시점의 이력이다.

## 당시 이력 · 실제 Claude 단일 사례 양쪽 확인 · 2026-10-16

- 목표: oh-my-claudecode의 강점을 흡수해 OMC의 잘못된 완료 판단을 줄이고 실제 업무 효과를 확인한다.
- 확인: 실제 Sonnet 4.6/high 합계 단일 사례에서 양쪽 모두 테스트 3/3 통과·화면 미실행 명시·전체 완료 과장 없음. OMC는 필수 수집 6항목 통과·명시적 blocked 판정, 첫 줄 서식 1항목 위반. 경쟁 도구는 플러그인 로드 확인·짧은 미확인 설명.
- 비교 한계: 브라우저 부재를 명시한 합성 1사례씩이며 제품별 절차는 다름. 실제 광고 업무 효과·경쟁 우위·C3 사용자 이해 효과 미입증. 기존 광고 false completion 판정 보존.
- 보완 구현: 8개 파일 리뷰 APPROVE·145개 테스트 통과·3e4190c 커밋 이력 유지. 이번에는 제품 코드를 수정하지 않았다.
- 배포: 이번 보완 미릴리스·일반 사용처 미설치. 기존 0.5.9 설치 이력은 별개이며 재감사하지 않았다.
- 다음: 실제 광고 유지보수 과제에서 양쪽에 동일한 화면 검증 환경·완료 조건을 제공하고 품질·재작업·사람 개입을 비교한다. 릴리스·정상 사용처 재설치는 별도 승인 대상으로 남긴다.
- 근거: [양쪽 단일 사례 비교](../required-checks-comparison-20261016.md). 아래 현재·다음 표현은 각 시점의 이력이다.

## 당시 이력 · 필수 검증 결속 보완 커밋 · 2026-10-16

- 목표: oh-my-claudecode와의 대표 업무 비교에서 드러난 OMC의 잘못된 완료 판단을 줄인다.
- 구현·검증: 승인된 필수 검증 기준·실제 후보 스냅샷·정확한 완료 상태를 연결하는 보완을 구현하고, 격리 8개 파일 리뷰 APPROVE·145개 테스트 통과 후 3e4190c에 로컬 커밋했다.
- 개선: 최초 CONTRACT의 필수 항목 누락 차단, 통제 화면·실제 홈 구분, 검증 후 코드·설정 변경 시 이전 증거 거부, 실패·불일치·미실행의 정확한 보고 대조.
- 비교 이력: 동일 Sonnet 4.6/high 단일 광고 업무에서 OMC 717.82초, 상대 1028.44초. 통제 6종 UI는 동등하게 확인했으나 당시 OMC는 browser를 제외하고 완료를 선언했다. 이 판정은 후속 보완으로 소급 통과시키지 않는다.
- 실제 홈 후속: 양쪽 광고 이미지·스타일 표시 확인; OMC 자동 전환 확인. 상대 실제 홈 자동 전환의 시간차 관측·양쪽 클릭 추적·숨김 통합 검증은 미완료다.
- 배포: 이번 보완은 미릴리스·일반 사용처 미설치. 기존 정상 11개 사용처의 0.5.9 설치 이력은 별개이며 이번에는 재감사하지 않았다.
- 다음: 보완된 기준으로 실제 Claude 단일 시나리오를 재검증한 뒤, 통과하면 같은 조건의 도구 비교를 진행한다. 릴리스·정상 사용처 재설치는 별도 승인 대상으로 남긴다.
- 한계: 보완 후 실제 Claude 재검증·실무 효과·경쟁 우위·C3 사용자 이해 효과는 미입증. 원본의 나머지 12개 staged 변경과 로컬 자료는 보존했고 push는 하지 않았다.
- 근거: [동일 조건 비교 기록](../ad-ui-recompare-20261016.md), 커밋 `3e4190c`, 격리 후보 리뷰·145개 테스트. 아래 내용의 현재·다음 표현은 각 시점의 이력이다.

## 당시 이력 · 완료 보고 안내 후속 · 2026-10-15

- 목표: 구현·코드 승인과 필수 검증 완료를 구분해 잘못된 완료 보고를 줄인다.
- 확인: 필수 검증이 남으면 전체 완료를 주장하지 않도록 안내를 보완했다. 관련 테스트 131개 통과, 리뷰 APPROVE WITH NOTES. 후속 응답의 기계 계약 12/12 통과, 첫 문장 정확 일치 5/12(마침표 차이 4개·상태 구분 차이 3개). 기존 두 오류는 이번 표본에서 재현되지 않았으나 일반 업무 재발 방지 효과는 미입증이다.
- 설치: 기존 사용처는 0.5.9 설치 이력 유지. 이번 안내 변경은 버전 변경·릴리스·재설치 전이다.
- 다음: 실패·증거 불일치 사례의 첫 문장 구분을 재검증한다. 실제 업무 효과·경쟁 우열·C3 이해 효과는 미입증이며 관측 자동 재개는 하지 않는다.
- 근거: [완료 보고 평가 기록](../completion-reporting-pilot-20261015.md)

## 당시 이력 · 리뷰 승인 범위 대조 · 2026-10-15

- 완료: 0.5.9 패치 릴리스·정상11개 재설치·엄격 감사.
- 품질: 4324 passed, 3 skipped, 12 deselected in 550.36s (0:09:10). 버전 기대값1개 실패 수정 후 재검증 통과.
- 보존: 업데이트 직전 대비 업무34023개·관측114개 변경0; 업데이트 전 변경0개 별도 기록.
- 다음: 대표 업무의 비교 기준·완료 조건을 정해 실제 효과 평가를 준비한다. C3 사용자 이해 재검증은 별도 미완료이며 관측 자동 재등록·재개는 하지 않는다.
- 미입증: 생산성·경쟁 우위·C3 사용자 이해 효과.

## 당시 이력 · 복구 판독 요약 · 2026-10-15

C3 다섯 항목 복구 안내 0.5.8 릴리스·정상11개 사용처 설치 완료 · 형식 동의와 이해 효과 구분 · 새 안내 이해 재검증 전

0.5.7 재검증에서 사용자가 요청받은 작업·하려던 일·작업 위치·중단 이유·해결 제안 형식을 제시했고 “좋아 저렇게 하자”로 동의했다. 비교 기준 질문은 요청과 목표 파일을 구분할 필요를 확인한 것이다. 이는 형식 요구·선호 확인이며 새 제품의 이해 검증 통과가 아니다. 기본 출력은 다섯 항목, 기술 근거는 상세로 이동. 파일 불일치 재사용 차단과 이전 실행 여부 불확실 차단의 공개 CLI 모두 적용. 선언된 목표 해시를 사용자 요청의 의미 일치로 취급하지 않으며 요청 확인 뒤 수정 요청을 안내한다. 목표 검증 불가에서는 수정 제안 없이 보류. 관련352개 통과·1개 생략; 합성 사례의 기본5항목·상세 원문/경로·종료1·파일 변경0·추가 호출0 확인. 독립 리뷰132개 통과·APPROVE. 사용자 이해 재검증 전. 0.5.8 고정 전체 품질 게이트4312개 통과·3개 생략·12개 제외. OMC 가드·TDD·리뷰 결속·비밀값 검사 통과. 타입·린트 별도 게이트는 계약에 없음. 설치11개 엄격 검증 통과. 사용자 이해 효과 재검증은 미실행. 0.5.8 고정 전체 품질 게이트4312개 통과·3개 생략·12개 제외. OMC 가드·TDD·리뷰 결속·비밀값 검사 통과. 타입·린트 별도 게이트는 계약에 없음. 설치11개 엄격 검증 통과. 사용자 이해 효과 재검증은 미실행. 0.5.7 고정 전체 품질 게이트4303개 통과·3개 생략·12개 제외. 별도 타입·린트 게이트는 계약에 없음. 11개 사용처 설치·엄격 감사 통과. 새 안내 사용자 이해 관측 없음.

0.5.8 패치 릴리스c047947·정상11개 사용처 재설치·엄격 검증 완료(sellerhub·OKX 포함). 설치본0.5.8·무결성clean·원본일치. 설치본·receipt 백업 보존. 비교34011개 중34009개 해시 일치. storefront BankdaHistory.tsx·BankdaHistory.styled.ts는 설치 전11:27경 수정 관측(설치11:33, OMC 관리 항목0개)으로 보존하고 전체 불변 주장 제외. 수정 원인은 추정하지 않음. marketing-project는 기존0.3.4 보존. 이번 요청 근거·변경 이유·수정 제안 추가 안내는 구현·리뷰 완료 상태이며 버전 변경 전·미설치다. 기존0.5.6 설치 이력과 구분한다.

설치된0.5.8의 기본 다섯 항목 안내만 제공한 새 격리 사례에서 사용자 이해를 확인한다. 형식 동의·코드 검증·설치를 실제 이해 효과로 집계하지 않으며 실무·다중 사례 효과·경쟁 우열은 미검증으로 유지한다.

V0: 미실행 근거·별도 승인 단계 하나만 실행. 완료 단계 보존·공통 잠금·승인 소비·재중단 차단. 자동 재시도·불확실 완료 승격·후속 자동 실행 없음.

현재 우선순위: 설치된0.5.8의 다섯 항목 안내로 사용자 이해 재검증. 릴리스·11개 설치 완료와 이해 효과 미검증을 구분한다.

사용자 효과·경쟁 우열은 미입증. 아래 내용은 당시 이력입니다.

## 당시 이력 · 복구 판단 안내 보완 · 2026-10-11

복구 판단 안내 보완·리뷰 완료. 실행 응답 진단 수집 여부, 외부 provider 기록 미수집, 산출물 존재 여부와 재개 미지원 한계를 표시한다. workflow18테스트 통과·APPROVE WITH NOTES. 사용자 효과·경쟁 우열은 미입증이다.

0.4.1 릴리스 1d1e96e: 정상 11개 사용처 설치·엄격 검증 완료. marketing-project는 drift로 보존. 이번 추가 안내 보완은 이번 변경 묶음에 포함하며 사용처에는 미설치. 별도 릴리스 판단 대상.

다음: 격리 사례 안내만으로 완료·불확실 단계, 확인할 근거, 차단 유지 이유를 찾을 수 있는지 사용자 확인.

아래 v25 이하 내용은 당시 이력이다.


복구 안내 구현·리뷰 완료를 반영했다. CLI가 완료·불확실 단계, 상태·산출물 위치와 안전한 대조 순서를 안내하며 기존 차단을 유지한다. 관련195테스트 통과·1개 건너뜀, 독립 리뷰18개 통과·APPROVE WITH NOTES. 실제 강제 중단 후 재실행에서 provider 추가 호출 없음을 확인했다.

v25 화면 검증: 데이터16테스트·질문18개 결속, Chrome 1440px/390px·가로 넘침 없음·클릭/키보드·근거 해시 검증 PASS.

다음은 버전·사용처 업데이트 범위 확인과 실제 사용 검증 계획이다. 버전 변경·재설치·push와 사용자 효과 검증은 아직 수행하지 않았다. 아래 v24 이하의 현재·다음 표현은 당시 기록이다.

## 이전 기록 · v24 / C3 제한적 정책 진단

OMC CLI와 Ralph hook·PRD 6항목 원문을 별도로 추가했다. 양쪽 정책 차단과 최종 승인 경계, 유효 완료 fixture, 제외 시도를 분리한다. 리뷰 APPROVE WITH NOTES·캡처4테스트 PASS·6항목 audit PASS·consumer1667개 해시 일치. 실제 AI·전체 runtime 동등 비교·사용자 효과·경쟁 우열은 미검증이다.

다음 계획 후보는 OMC 중단 차단 뒤 복구 안내 구체화이며 제품 구현은 아직 시작하지 않았다. 이전 graph3항목 미관측은 이력으로 보존했다. [최신 보고서](../c3_ralph_policy_diagnostic_20261011.md) · [로드맵](../automatic_model_routing_roadmap.md).

v24 검증: 데이터16테스트 PASS·질문18개 결속·Chrome 1440px/390px 화면 직접 확인·가로 넘침 없음·펼침 클릭/키보드 및 신규6개 원문 해시 일치.

아래 v23 이하의 현재·다음 표현은 당시 기록이다. 제품 코드·버전·설치는 변경하지 않았다.

C3 통제 native CLI 진단6항목과 별도 완료 상태 근거 삭제 대조, 리뷰 APPROVE WITH NOTES까지 완료했다. OMC3항목은 차단을 관측했고 상대 graph3항목은 darwin 실행 미지원으로 미관측이다. 비교 미완료이며 상대 실패·경쟁 우위·제품 효과로 집계하지 않는다. 실제 AI 호출 없이 OMC fake provider와 상대 graph command 경로를 사용했으며 Ralph·Claude 대화 재개 동등 비교가 아니다.

OMC 강제 중단 후 재개는 provider-inflight guard가 수동 대조를 요구하며 차단한다. 요청 변경은 task spec hash mismatch로 차단한다. 중단 상태의 산출물 삭제는 앞선 guard에서 차단되므로 별도 완료 상태 대조로 산출물 누락 시 receipt 불일치 차단을 확인했다. [진단 보고서](../c3_resume_diagnostic_20261011.md) · 원문6항목은 evidence/c3-diagnostic-v23/에 있으며 로컬 절대경로만 가린 표시용 사본이다.

C1 후보 제외·회귀 검증 정리는 c369d8e에 커밋 완료. 기존 C1 이력·원문은 보존하고 현재 선택은 C3 비교 경로 확인으로 이동했다. 다음은 대응 Ralph 재개 consumer 확인 또는 graph 지원 환경 확보 후 재비교다. 재개 안내 개선은 OMC 자체 후보이며 경쟁 갭으로 확정하지 않았다.

제품 코드·사용처 설치 변경 없음. 이번 로컬 커밋 범위는 진단 문서·로드맵·대시보드 기록이다. 측정 테스트3개는 분류 함수 검사이며 리뷰에서 원문·state·receipt·calls를 독립 대조했다. 해당 대조의 자동화는 보강 제안으로 남긴다. 현재 관찰 v3 DISABLED / OBSERVATION_INVALID(pending_completion_invalid).

이하 내용은 각 시점의 이력이다.

# 현재 상태 · 2026-10-11 · v22

응답 지침 후보는 실제12회에서 오류 감소 효과가 입증되지 않아 제외했다. 실행기 사실 보고는 기존 제품 기능(67d66f0)으로 유지한다. 신규 제품 기능 채택이 아니라 회귀 테스트 보강이다. 최종 상태·출력·분류와 AI 참고 판정 분리, 재시도·시간 초과·의존 단계 차단 표시를 검증했다.

후보 실험: 실행기 사실12/12 정확, 구조화 응답10/12, 원문 완전 일치3/12, 채널 역전4회, 형식 누락2회. 나머지 원문 불일치3회는 줄바꿈 정밀도 차이이며 자유 설명 분류 문제1회는 채널 역전 사례와 중복이다. 이전24회와 검수 기준이 달라 오류 감소·악화를 추정하지 않는다. 후보 제외 뒤 실제 AI 재실행은 없다.

로컬 회귀332개 통과·1개 제외, 마지막 표시 단언5개 통과, 리뷰 독립12개 통과·APPROVE WITH NOTES. 완료 파일 없는 public CLI fixture의 exit1만으로 검증 실패 차단을 증명할 수 없다는 테스트 보강 제안은 남긴다. 사용자 효과·경쟁 우위는 미입증이다.

v21 비교 기록은219c328에 커밋 완료. 이번 테스트·대시보드·로드맵은 함께 로컬 커밋하는 범위이며 사용처 설치 변경은 없다. 다음 후보 C2·C3의 사용자 문제와 공통 비교 시나리오를 검토해 우선순위를 결정한다(아직 미선택). C1 후속 구현은 보류한다.

빌드·데이터·화면 근거는 격리 comparison-roadmap-refresh-20261008의 evidence/*-v22 및 v22-*.png에 보존한다. 이번 시점의 관찰은 v3 DISABLED / OBSERVATION_INVALID(live_observation_not_enabled)이다.

이하 기록은 당시 시점의 이력이다.

# 현재 비교 · 2026-10-10 · v21

제품 보고 분리 코드는 `67d66f0` 로컬 커밋 완료. 공통 4조건 × 두 도구 × 각3회 = 유효24회에서 최종 AI 보고23회·독립 코드 검증23회·시간 초과0회를 확인했다. 우리 실행기 표시12/12 대조 통과·성공3회 완료·필수 실패9회 차단. 우리 AI 설명은3개 실행에서 스트림·분류·종료 상태 오류가 각각 관찰됐다. 상대는 최종 응답1회 없음과 독립 코드 검증1회 실패가 같은 실행에서 관찰됐다. 상대 native 완료 상태는12회 모두 미관측이며 실패로 계산하지 않는다.

Native 권한·리뷰·완료 정책이 달라 엄밀한 동등성·전체 우열·사용자 효과를 주장하지 않는다. 개선 전 AI 대조군과 사용자 진단 시간은 미측정이다. 초기 준비 산출물2개 및 r3 조기 수집1회를 제외하고 같은조건1회로 보충했다. 이전 측정 원문은 변경하지 않았다.

현재 상단·C1 카드·로드맵은 일치한다. 비교표와 원문24개는 `evidence/c1-paired-v21/`에 보존했다. 데이터13개 검사·15개 근거 연결·1440/390px 실제 화면·클릭/키보드·보고 해시 검증 통과. 현재 비교·대시보드 변경은 미커밋, 사용처 재설치는 진행하지 않았다. 다음 작업은 `$omc-review`로 측정 경로·원문 대조·결론의 범위를 리뷰하는 것이다.

빌드 및 UI 검수 원본: 격리 `comparison-roadmap-refresh-20261008/evidence/source-v21.json`, `initial-v21.json`, `render-v21.json`, `handoff-v21.json`. 독립 측정 원본: `c1-paired-report-comparison-r3-20261010/admitted-results.json`, `independent-audit.json`, `provenance.json`; 보충 `c1-paired-report-supplement-20261010/`. 현재 natural completion observation은 `OBSERVATION_INVALID(pending_completion_invalid)`이며 이 비교를 해당 표본으로 계산하지 않는다.

이하 기록은 당시 시점의 이력이다.

# 상태 정합성 보완 · v20

상단·구현 현황·C1 현재 상태를 새 코드12회 재측정 완료로 통일했다. 재측정 전 문구는 구현 당시 이력으로 구분했다. 현재 상태 일치·시점 구분·실제 표시 회귀 포함12개 검사 통과. 기본 build.py 및 test_dashboard.py도 v20 고정 입력에 연결했다. 이전 원본·측정은 보존한다. 미설치·결과 재리뷰 전.

# 새 코드 12회 재검증 · 2026-10-10 · v19

공통4조건 각3회 실제 AI 실행: 실행기 최종 보고 표시 대조12/12·시간 초과0·성공3회 완료·필수 실패 및 검증 불가9회 차단. 독립 코드 검증12/12·원본 보존 확인. AI 자유 응답 오류1건(S1-existing-ours-2)은 별도다. 이전12회와 합산 금지. 미커밋 파일 해시로 코드 고정; 경쟁 우위·사용자 효과·오류 감소 효과 미입증.

근거: 격리 c1-report-separation-validation-20261010의 results.json, surface-audit.json, summary.json, code-binding.json. 대시보드 데이터11개·1440px/390px 화면·클릭/키보드·보고 해시12개 검증. 다음은 결과 재리뷰. 제품 미설치.

이하 시점별 이력:

# 최신 상태 · 2026-10-10 · 대시보드 v18

검증 사실과 AI 참고 응답 분리 로컬 구현·재리뷰 APPROVE. 제품 회귀 325개 통과·1개 건너뜀. 이번 변경은 사용처 미설치·실제 AI 재측정 전이다. 이전 12회 보고 12/12·시간 초과 0·AI 출력 서술 오류4건은 개선 전 측정으로 보존한다.

상단 상태·C1 로드맵·다음 작업·구현 이력을 일치시켰다. 다음은 승인된 변경 커밋 후 새 코드 공통 4조건 보고 재검증이며 경쟁 도구 동등 조건 비교는 별도다. 대시보드 데이터 검사11개 통과, 1440px·390px 화면 및 클릭·키보드 펼치기 확인. 사용자 수용 전 상태.

근거: 격리 comparison-roadmap-refresh-20261008/evidence/source-v18.json, initial-v18.json, render-v18.json, handoff-v18.json. 이하 기록은 당시 시점의 이력이다.

# OMC 비교 개선 로드맵

2026-10-10 갱신. `index.html`은 로컬 단일 페이지 대시보드이며 `view.json`은 화면이 소비하는 표시 데이터입니다. 원본 실행 기록과 격리 빌드·화면 검수 근거는 별도로 보존합니다.

## 최신 상태 — 종료 지시 보완 후 12회 반복 검증·자유 응답 한계

새 12회에서 보고 12/12, 시간 초과 0회. 성공 3회 완료, 기존 실패·신규 실패·파일 부재 각 3회에서 분류·사유·조치와 완료 차단을 확인했다. 독립 검증·원본 보존·설치 구현·격리 확인 12/12. 리뷰 APPROVE WITH NOTES.

AI 자유 응답 4건(existing-1·existing-2·new-2·new-3)은 stdout/stderr를 뒤바꿔 서술했다. 실행기가 저장한 실제 출력·진단은 정확하다. 보고 종료와 자유 응답 정확성을 별도로 표시하며 원문은 `evidence/c1-repeat/`에서 대조한다. 이 12회에서 반복 동작을 확인했지만 일반 안정성·경쟁 우위·사용자 효과는 미검증이다. 다음은 자유 응답 정확성 보완 범위 결정 후 경쟁 도구와 동등 조건 재비교다. 아래 기존 기록은 별도 보존했다.

## 이전 진단 — 실패 보고 종료 지시 보완·4회 진단 검증

항상 실패하는 검증기에 통과만 요구하던 지시에 실패 시 실제 출력·미검증 항목을 보고하고 종료하는 규칙을 추가했다. 제품 코드·120초 제한·필수 실패 차단은 유지했다. 조건별 1회 실행에서 최종 보고 4/4, 시간 초과 0/4, 독립 코드 검증 4/4를 확인했다. 성공은 완료, 기존 실패·신규 실패·파일 부재는 분류·사유·조치 보고 후 완료 표시 없이 차단했다. 리뷰 APPROVE WITH NOTES(정책 테스트의 불필요 반복 정리 제안).

반복 안정성·경쟁 우위·사용자 효과는 미검증이다. 다음은 동일 종료 지시로 반복 안정성을 확인한 뒤 경쟁 도구와 동등 조건으로 재비교하는 것이다. 아래 12회와 24회 기록은 합산하거나 덮어쓰지 않는다. 새 원문은 `evidence/c1-exit-policy/`에서 확인한다.

## 보완 후 실제 AI 재검증 — 12회 수집 완료, 전체 검증 미충족

d143637의 4조건 × 3회 실제 AI 실행을 수집했다. 최종 보고 8회·시간 초과 4회이며 독립 코드 검증은 11/12 통과했다. 성공 3회 완료, 신규 실패 2회에서 분류·사유·조치·차단 확인(1회 시간 초과), 파일 부재 3회에서 구분 불가·파일 확인 안내·차단 확인. 기존 실패 3회는 시간 초과로 최종 보고 미확인이다. 다음은 기존 실패 시간 초과 원인 조사와 보고 수집 경로 확인이다. 사용처 재설치와 사용자 효과는 미검증이다.

원문·독립 audit: `/Users/noseunglae/.codex/visualizations/2026/10/06/01a1108d-b710-7001-903a-c639eaf94c27/c1-report-revalidation-r2-20261010/`. 검증기·기준 기록·설치 구현·oracle 원본은 12회 모두 보존했고 접근 차단 probes를 확인했다. 첫 준비 실패는 AI 미시작으로 제외했다. 아래는 재검증 전 시점의 기록이다.

## 재검증 전 — 최종 보고 연결 로컬 구현·리뷰 승인

C1 실패 분류·사유·남은 조치를 autopilot 실행기 최종 보고에 연결했다. 잘못된 ID·label은 구분 불가로 처리하며 검증 결과·완료 차단을 유지한다. 관련 테스트 316개 통과·1개 건너뜀, 재리뷰 34개 통과·APPROVE. 현재 `d143637` 로컬 커밋 완료·사용처 미설치이며 모델 자유 응답과 사용자 효과는 미검증이다. 다음은 실제 AI 4조건의 최종 보고 재검증이다.

## 보완 전 실제 AI 비교 수집 이력

공통 4조건 × 도구 2종 × 조건별 3회로 최종 24회 기록을 확보했다. 초기 24회 중 격리 위반 6회를 제외하고 보충했으며, AI가 시작되지 않은 실행기 실패 2회도 별도 제외했다. 최종 기록은 초기 실행 18회와 접근 차단을 적용한 보충 6회로 구성된다.

- 최종 응답 수집: omc_kit 8/12, oh-my-claudecode 3/12. 시간 초과: 각각 4회, 9회. 응답 수집률은 작업 완료율이 아니다.
- 독립 코드 검증 통과는 각각 10/12이며, 필수 검증 명령의 성공 및 최종 보고 여부와 구분한다.
- 주 모델 Sonnet 4.6은 최종 24회 모두 실행 기록으로 확인했다. 사용자 진단 시간은 미측정이다.
- 도구별 기본 실행 권한 차이, 초기·보충 격리 방식 차이, 실제 AI 개선 전 대조군 부재 때문에 C1 효과와 경쟁 우열 판정은 보류한다.
- 다음 보완 후보: autopilot 실제 최종 보고에 C1 실패 분류·사유·남은 조치를 직접 연결한다. 현재 작업 게이트 출력 통합 커밋은 `95a88be`이며, 이전 사용처 설치 기록과 별개다.
- 대시보드 입력 v11 및 조건별 보고 24개를 반영했다. 보고가 없으면 그 사실을 명시한 기록을 제공한다. 이전 비교 기록은 보존한다.

최종 원문: `/Users/noseunglae/.codex/visualizations/2026/10/06/01a1108d-b710-7001-903a-c639eaf94c27/c1-native-comparison-reauth-r5-20261008/admitted-results.json` 및 같은 폴더의 `admitted-audit.json`, `summary.json`. 아래 내용은 시점별 이력이다.

## C1 — 기존·신규 실패 진단

1. 개선 전 OMC·개선 후 OMC·경쟁 도구에 성공·기존 실패·신규 실패·검증 파일 없음 조건을 각각 3회 적용해 총 36회 실행했다.
2. 기존·신규 실패 분류와 필수 실패 차단을 확인했으며, 검증 파일 없음이 신규 실패로 분류되는 문제를 발견했다.
3. 직접 `python check.py`의 파일 부재를 `missing_verification_file` 근거로 구분 불가 처리했다. 일반 테스트의 종료 코드 2는 기존·신규 분류를 유지한다. 래퍼·옵션·다른 인터프리터의 파일 부재 판별은 이 보완의 범위 밖이다.
4. 관련 테스트 151개 통과와 리뷰 승인을 확인하고 C1 코드·테스트·문서 3개 파일을 `c742b4e`로 로컬 커밋했다.

5. 당시 HEAD `6093e54`에서 기존 실패·신규 실패·검증 파일 없음 3조건을 직접 CLI와 실제 TDD 작업 게이트로 각각 실행했다. 분류는 `existing`·`new`·`unknown`이며 모든 필수 실패는 종료 상태 1로 차단했다.
6. 실제 작업 게이트는 품질 게이트 실패만 출력하고 분류·사유를 전달하지 않는 통합 누락을 재현했다.

**당시 다음 작업:** 작업 게이트 최종 보고에 실패 분류·사유·남은 조치를 전달하는 보완 계획. 기존 36회는 파일 부재 보완 전 코드 결과로 보존한다. 이번 검증은 최종 코드의 세 조건 집중 확인이며 경쟁 도구 재실행이나 실제 LLM 작업 비교가 아니다.

실행 원문: `/Users/noseunglae/.codex/visualizations/2026/10/06/01a1108d-b710-7001-903a-c639eaf94c27/c1-final-focus-20261008/results.json`. 코드 기준: 같은 폴더의 `provenance.json`.

## 검증 기록의 구분

- 304개 통과·1개 건너뜀: 이전 quality gate·autopilot 합동 검증.
- 151개 통과: 초기 C1 품질 진단 검증.
- 36회 비교: 로컬 검증 명령 실행. 경쟁 도구는 `ralphVerify` CLI action을 호출했다. 실제 AI 작업 실행 비교가 아니다.
- 명령 실행 시간은 사람의 진단 시간이나 생산성 효과를 대체하지 않는다. 사용자 개입·제품 효과는 미측정이다.
- autopilot 보고 개선은 별도 로컬 커밋 `6093e54`로 완료했다. 이번 작업은 해당 변경을 수정하지 않았다.
- 이번 집중 회귀 테스트 3개 통과는 기존 151개 중 일부의 재검증이다. 합산하지 않는다.
- 대시보드 표시·데이터 검증 9개 통과 및 1440px·390px 실제 화면과 클릭·키보드 펼치기 검수 완료.
- 원본 v2는 보존하고 v3 입력·계약 해시를 고정했다. `comparison-roadmap-refresh-20261008/evidence/initial-v3.json`과 `question-evidence-v3.json`에 연결 근거를 보존한다.

## 이후 후보

| 후보 | 상태 | 필요한 비교 |
|---|---|---|
| C2 프롬프트 관리 | 비교 검증 필요 | 동일 정책 변경의 수정량·생성 불일치 |
| C3 중단·재개 | 비교 검증 필요 | 동일 중단·요청 변경의 중복 작업·과거 완료 재사용 |

로컬 커밋과 제품 효과 검증은 별개다. C1 결과를 확인한 뒤 C2·C3 진행 여부를 사람이 결정한다. 푸시는 수행하지 않았다.

## 0.4.0 버전·사용처 설치 상태

원본 VERSION과 버전 테스트를 0.4.0으로 변경하고 ebf5c67로 커밋했다. 버전 테스트 32개 통과. 릴리스 당시 C1·autopilot 코드 개선은 기존 커밋에 포함됐으며 작업 보고 통합 보완은 미구현이었다. 이후 통합 커밋 95a88be는 이 설치 기록에 포함되지 않는다.

- 재설치 10곳: sixshop2-landing, research-auto, sixshop3-partner-center-fe, sixshop3-storefront-fe, marketing, market-reasoning-engine, okx-maker-grid-bot-wind, ai-cs, codex-chat, sixshop2.
- 추가 설치: sellerhub-accounts-fe-restored. 기본 사용 준비 완료. 프로젝트 품질 검증 명령은 미설정. 공통 프롬프트 2개는 Git 로컬 제외 목록으로 보조 파일 노출을 해소했다.
- 총 11곳: 0.4.0·설치 정상·원본 기능 파일 일치 확인. 설치 완료와 사용자 효과 입증은 별개다.
- marketing-project: AGENTS.md 프로젝트 지침 변경으로 재설치 보류. 기존 지침 보존.
- 설치 원문은 격리 대시보드 evidence/omc-040-install-results.json 및 omc-040-install-verification.json에 보존(앞선 10곳). sellerhub는 별도 설치·검증 결과로 확인했다.
- 대시보드 입력 v4 고정. 이전 v3 및 이전 화면을 보존했다.

이후 아래 작업 게이트 통합을 로컬 구현·검증했다.

## C1 작업 게이트 출력 통합 · 커밋 전 검증 이력

`omc_tdd_check.py --run-tests`가 기존 진단 함수를 사용해 gate별 분류·사유 코드·남은 조치를 표시한다. 실제 CLI 성공·기존 실패·신규 실패·검증 파일 없음·손상 기준 기록 7조건 회귀를 검증했다. 성공은 0, 필수 실패는 기존 실패여도 1을 유지한다.

- 관련 테스트 173개 통과(품질 진단 151개 및 작업 게이트 22개); 이전 테스트 수와 합산하지 않는다.
- 이번 통합은 로컬 구현·리뷰 승인·커밋 예정 상태다. 이전 11곳 0.4.0 설치에는 포함되지 않는다.
- 실제 AI 최종 응답 전달·사용자 시간·경쟁 도구 우열은 미검증이다.
- 대시보드 입력 v7 고정, v4 화면·원본 보존.
- 리뷰 APPROVE 완료. 승인된 7개 파일 로컬 커밋 예정(이 문서는 커밋 직전 상태의 기록).

리뷰 보완: 선택 검증 실패·실행 불가에는 참고 조치를 안내하며 필수 실패로 오인시키지 않는다. 실행 결과의 required 필드는 해당 실행의 설정에서 전달한다.

## 실제 AI 24회 비교 · 인증 복구 전 사전 점검 이력

C1 통합 커밋 95a88be, 상대 커밋 454bae017356ed1d056182cd929b6eb6cf6143ad를 고정했다. 기본 Claude 경로는 모델 사용량 없는 실행 오류로 종료됐고, 이전 공통 Bedrock 환경은 40초 안에 응답하지 않았다. 24회 비교는 시작 0회다. 원인 미확인이며 작업 실패·완료율 분모에 집계하지 않는다.

근거: 격리 c1-native-comparison-20261008/preflight.json, plan-binding.json. 공통 실행 환경 응답 복구 전 제품 효과·경쟁 도구 우열을 판단하지 않는다. 이 기록은 인증 복구 전 상태다. 이후 AWS 세션 만료를 확인했고 재로그인 후 공통 모델의 실제 응답을 확인해 비교를 재개했다.

## v25 최신화 전 검증 이력 (d3f3cb7)

관련 회귀266개 통과·1개 생략. 출력 순서 정리 후 요약8개 통과. 후속 리뷰 workflow89개 통과·APPROVE (2026-10-14). 요청·반환·유효 기록 없음과 완료 검증을 구분하고 현재/목표 비교·기록 위치·보류 이유를 8줄·800자 이내에 표시. 이전 릴리스 품질 검증은 당시 이력이며 이번 후보 검증을 대신하지 않음. 0.5.1 릴리스 당시 사전 review_stale 차단을 놓친 실행 순서 오류는 보존하며, 사후 READY를 사전 승인으로 간주하지 않음. 0.5.2 릴리스 전체 품질 게이트4243개 통과·3개 생략·12개 제외, 버전·설치136개 통과, 커밋 전 리뷰 결속 READY. 격리 문서 작업 관련100개 통과, 후속 독립 리뷰16개 통과·APPROVE. 실제 임시 저장소의 설치·세션·커밋 훅·완료 기록과 원본 보존 확인. 기존 커밋 Markdown만 입력 허용, 원본 내용 변경 시 확인 실패; 자동 반영·실제 OKX 적용은 미실행. 0.5.3 전체 품질4259개 통과·3개 생략·12개 제외. 설치·가드·버전182개 통과, 로드맵25개 통과. 커밋 전 리뷰 결속 READY.

## R3 구현 전 비교 이력 (611e844)

C3 개선 전후 재검증·증거 검증기 리뷰 완료 · 경쟁 동등 비교 보류 · 사용자 이해도 미검증

v25: OMC 0.4.0→0.5.3 공통 문서 작업3조건×각3회, 총18회 추가 호출0. 차단은 기존에도 있었으며 안내가1줄→8줄로 개선됨. 완료 후 파일 변경 경로는 receipt 불일치 안내만 남아 보완 필요. 증거 검증기는 저장소 원문·필수 단계·완료 기록·목표/의존 해시를 대조하며79개 검사 통과·최종 리뷰 APPROVE. Ralph native 정책 훅9회는 별도 fixture 관측, 실제 AI·동등 경쟁 비교 아님. graph는 macOS 실행 불가로 실패 분모에서 제외. 사용자 판단 시간·개입 효과 미측정.

0.5.3 c971455 정상11개 사용처 설치·엄격 검증 완료 (2026-10-14, sellerhub·OKX 포함). OKX의 로컬 --work-path 의존 테스트를 기존 문서 격리 절차 검증으로 전환: 복사본·실제 설치 각각16개 통과, 설치 무결성 clean. 기존 가드·테스트·receipt 백업 보존, 연구 코드·평가 문서·인덱스·HEAD 불변 확인. marketing-project는 기존 drift로 0.3.4 보존. 자동 문서 반영·전체 사용성은 미검증. OMC 결과 문서 d3f3cb7·OKX 테스트148e452 커밋 완료. 이번 v25는 비교 검증 도구·근거 변경으로 추가 릴리스·재설치 대상 아님.

## 추가 작업 맥락 안내 보완 전 이력 (0.5.4)

C3 R3 구현·최종 리뷰·0.5.4 릴리스 및 정상11개 사용처 설치 완료 · 사용자 이해도 미검증

0.5.4 릴리스0d4b569 및 정상11개 사용처 재설치·엄격 검증 완료(2026-10-14, sellerhub·OKX 포함). 모든 설치본0.5.4·무결성clean·소스일치. 설치본·receipt 백업 보존, 관리 범위 밖33,628개 파일과 소비자HEAD 불변 확인. storefront PaymentSettingRepository.ts 1개는 백업 이후·설치 이전 수정이 관측되어 전체 파일 불변 주장에서는 제외했으며 OMC 설치 관리 대상이 아니다. marketing-project는 기존drift로0.3.4 보존. 전체 제품 회귀4279개 통과·3개 생략·12개 제외(버전 변경 전), 버전 변경 후 버전·설치181개 통과. 타입·린트 별도 게이트는 품질 계약에 없음. 실제 사용자 이해도·경쟁 효과 미검증.

## 차이·의도 안내 보완 전 설치 이력 (0.5.5)

0.5.5 릴리스6cb39bb 및 정상11개 사용처 재설치·엄격 검증 완료(2026-10-14, sellerhub·OKX 포함). 모든 설치본0.5.5·무결성clean·소스일치. 설치본·receipt 백업 보존. 범위 밖33,627개 파일 해시 일치; storefront Bankda 파일2개와HEAD는 별도 작업 변경이 관측되어 불변 주장에서는 제외하고 보존했다. marketing-project는 기존drift로0.3.4 유지. 전체 품질 재검증4293개 통과·3개 생략·12개 제외, 버전/설치181개 통과. 첫 전체 실행은 테스트 시작 후 버전 변경으로 기대값이 충돌해1개 실패했으며,0.5.5 고정 후 재실행한 결과를 최종 근거로 사용한다. 타입·린트 별도 게이트는 계약에 없음.

## 합성 판단 이해 검증 정정 (2026-10-14)

검증용 roadmap.md의 “새로운 내용”은 AI가 의도적으로 만든 변경이며 실무 변경이 아니다. 사용자는 안내를 재진술한 뒤 “그걸 모르겠는데”라고 응답했다. AI가 만든 변경 경위를 사용자에게 찾게 한 검증은 부적절했고 판단 이해 통과로 계산하지 않는다. 이전 목적·위치 인식 기록은 당시의 제한적 관측이며 실제 판단 수행·전체 사용성 근거가 아니다.

## 배경 제공 합성 사례의 제한적 이해 확인 (2026-10-14)

검증용 복사본에는 AI가 의도적으로 “통과”라고 기록했고, 실제 확인 결과와 원래 요청은 “미통과”임을 먼저 공개했다. 표와 설명을 함께 제공한 뒤 사용자 응답 “유지하면 안되지 다른 기록은 정합성이 깨지잖아”에서 현재 유지가 부적절하다는 선택과 정합성 이유를 확인했다. 이는 배경 제공 조건의 합성1건 이해 확인이며 CLI 출력만의 효과·실제 업무 사용성·다중 사례 성공이 아니다. 이전 합성 판단 검증 미통과는 변경하지 않는다.


## 0.5.6 패치 릴리스·설치 완료 (2026-10-14)

0.5.7 패치 릴리스b54e4a1·정상11개 사용처 재설치·엄격 감사 완료(sellerhub·OKX 포함). 설치본0.5.7·무결성clean·원본일치. 설치본·receipt 백업 보존. 비교33512개 중33269개 해시 일치. storefront243개는 별도rebase 관측으로 전체 불변 주장 제외(OMC 관리 항목0개, 기존 파일211개 수정 시각은 설치 전·32개 부재). 자동 복원하지 않음. marketing-project는 기존0.3.4 보존. 이번 요청 근거·변경 이유·수정 제안 추가 안내는 구현·리뷰 완료 상태이며 버전 변경 전·미설치다. 기존0.5.6 설치 이력과 구분한다. 전체 품질 게이트 최종4300개 통과·3개 생략·12개 제외, 버전·설치181개 통과. 최초 실행은 버전 테스트의0.5.5 기대값 때문에1개 실패했으며 기대값 수정 후 전체를 재실행했다. 별도 타입·린트 게이트는 계약에 없음. CLI 단독 이해·실무 효과는 미검증이다.


## 현재 정합성 확인 (2026-10-15)

C3의 구현·리뷰·0.5.6 릴리스·정상11개 사용처 설치는 완료됐다. C1의 다음 작업에서 남아 있던 릴리스 대기 문구와 C3 완료 기준의 설치 미완료 문구를 정정했다. 다음 우선 작업은 설치된 CLI 안내만으로 상황과 판단을 이해할 수 있는지 확인하는 것이다. 배경 제공 합성1건 이해 확인과 CLI 단독·실무 효과 미검증은 구분해 유지한다. C2 비교 검증은 남아 있으며 과거 이력은 현재 완료 상태로 바꾸지 않는다.


## C3-UX-01 · CLI 단독 판단 이해 검증 계획 (2026-10-15)

**상태: 원본 안내 보완 요구 보존 · 다섯 항목 형식 동의 · 0.5.8 릴리스·정상11개 설치 완료 · 새 안내 이해 재검증 전.** C3의 남은 CLI 단독 이해 검증에 연결한다.

- 목표: 요청과 설치된0.5.6 CLI 안내만 보고 작업·중단 이유·현재 결과·다음 행동과 이유를 이해하는지 확인.
- 범위: 격리 문서 반영 사례1건. 제품 수정·실제 업무 파일 변경·경쟁 도구 비교 제외.
- 조건: 사용처 경로·설치 무결성·입력·기대 결과 고정. 요청에 배경·목표 명시, 이전 사례와 다른 문구 사용. 별도 해설·표 없이 요청과 원본 안내만 제공하며 AI가 만든 변경 경위를 사용자에게 조사시키지 않음.

| 태스크 | 요구사항 | 확인 근거 |
|---|---|---|
| T1 격리 사례 준비 | R1 입력·환경 고정 | 버전·무결성·입력·기대 결과 원문 |
| T2 CLI 실행 | R2 안전 차단 | 파일 변경0·추가 호출0·후속 실행0·실제 종료 상태 |
| T3 사용자 판단 확인 | R3 CLI 단독 이해 | 작업·이유·결과·다음 행동과 이유의 사용자 원문 |
| T4 결과 기록 | R4 근거 경계 보존 | 안전성·이해 결과 분리, 이해 확인/보완 필요/실행 불가 |

완료 기준: 안전성 관찰과 사용자 판단 원문을 모두 기록한다. 침묵·문구 재진술만으로 통과 처리하지 않는다. 추가 설명 후 이해한 경우 CLI 단독 이해로 계산하지 않으며 실행 불가는 이해 실패와 구분한다.

결과 반영: JSON의 C3 confirmed/effect와 current_update.verification, 로드맵 현재 요약·검증 이력, 대시보드 C3 상태를 함께 갱신한다. 합성1건은 실무·다중 사례 효과나 경쟁 우열의 근거가 아니다. C1 후속 구현·C2 비교는 별도 작업으로 유지한다.


C3-UX-01 실행 근거: `docs/comparison-dashboard/evidence/c3-ux-01/`. 설치된0.5.6 CLI의 종료1·파일 변경0·추가 호출0·단계 기록 유지 확인. 준비된 합성 사례이며 실제 업무/과거 실행 관측이 아니다. 사용자 판단 결과는 아직 없음.


## C3-UX-01 관측·후속 보완 (2026-10-15)

C3-UX-01 원본 안내 뒤 사용자는 “점검 결과를 점검·요약 문서 복사본에 기록 작업을 하려다가 파일 내용 불일치로 보류했고 재검증?”이라고 응답했다. 후속 선택2에서 무엇을 수정할지 몰랐다고 확인했고 “왜도”로 이유 설명도 요구했다. 목적·중단 이유 재진술 관측과 수정 내용·이유 보완 요구를 기록하며 CLI 단독 이해 통과로 계산하지 않는다.

C3-UX-01 원본 안내 뒤 사용자는 “점검 결과를 점검·요약 문서 복사본에 기록 작업을 하려다가 파일 내용 불일치로 보류했고 재검증?”이라고 응답했다. 후속 선택2에서 무엇을 수정할지 몰랐다고 확인했고 “왜도”로 이유 설명도 요구했다. 목적·중단 이유 재진술 관측과 수정 내용·이유 보완 요구를 기록하며 CLI 단독 이해 통과로 계산하지 않는다. 추가 안내는 검증된 목표 불일치에만 요청 원문(사실 검증 아님)·변경 이유·수정 제안을 표시한다. 요약 최대11줄/1100자 회귀, 긴 내용 생략 표시·상세 경로·전체 파일 교체 지시 아님 안내 유지. 관련343개 통과·1개 생략, 최종workflow123개 통과, 초기 독립 리뷰123개 통과·APPROVE WITH NOTES 이후 관측 문서 정리 재리뷰 APPROVE. 자동 수정·추가 호출·후속 실행 차단 유지. 새 안내의 사용자 이해 재검증은 미실행. 0.5.7 고정 전체 품질 게이트4303개 통과·3개 생략·12개 제외. 별도 타입·린트 게이트는 계약에 없음. 11개 사용처 설치·엄격 감사 통과. 새 안내 사용자 이해 관측 없음. 0.5.7 고정 전체 품질 게이트4303개 통과·3개 생략·12개 제외. 별도 타입·린트 게이트는 계약에 없음. 11개 사용처 설치·엄격 감사 통과. 새 안내 사용자 이해 관측 없음.

0.5.7 패치 릴리스b54e4a1·정상11개 사용처 재설치·엄격 감사 완료(sellerhub·OKX 포함). 설치본0.5.7·무결성clean·원본일치. 설치본·receipt 백업 보존. 비교33512개 중33269개 해시 일치. storefront243개는 별도rebase 관측으로 전체 불변 주장 제외(OMC 관리 항목0개, 기존 파일211개 수정 시각은 설치 전·32개 부재). 자동 복원하지 않음. marketing-project는 기존0.3.4 보존.

다음: 설치된0.5.7의 원본 안내로 새 격리 사례1건의 수정 내용·이유 이해를 재검증한다. 기존 사례는 보완 요구 관측으로 유지하며 실무·다중 사례 효과·경쟁 우열은 미검증으로 남긴다.


## 0.5.7 패치 릴리스·설치 완료 (2026-10-15)

0.5.7 패치 릴리스b54e4a1·정상11개 사용처 재설치·엄격 감사 완료(sellerhub·OKX 포함). 설치본0.5.7·무결성clean·원본일치. 설치본·receipt 백업 보존. 비교33512개 중33269개 해시 일치. storefront243개는 별도rebase 관측으로 전체 불변 주장 제외(OMC 관리 항목0개, 기존 파일211개 수정 시각은 설치 전·32개 부재). 자동 복원하지 않음. marketing-project는 기존0.3.4 보존. 전체 품질 게이트4303개 통과·3개 생략·12개 제외. OMC 가드·TDD·리뷰 결속·비밀값 검사 통과. 타입·린트 별도 게이트는 계약에 없음. 원격push·tag는 수행하지 않음. 새 안내의 사용자 이해 재검증·실무 효과는 미검증.


## 다섯 항목 복구 안내 구현·검증 (2026-10-15)

0.5.7 재검증에서 사용자가 요청받은 작업·하려던 일·작업 위치·중단 이유·해결 제안 형식을 제시했고 “좋아 저렇게 하자”로 동의했다. 비교 기준 질문은 요청과 목표 파일을 구분할 필요를 확인한 것이다. 이는 형식 요구·선호 확인이며 새 제품의 이해 검증 통과가 아니다. 기본 출력은 다섯 항목, 기술 근거는 상세로 이동. 파일 불일치 재사용 차단과 이전 실행 여부 불확실 차단의 공개 CLI 모두 적용. 선언된 목표 해시를 사용자 요청의 의미 일치로 취급하지 않으며 요청 확인 뒤 수정 요청을 안내한다. 목표 검증 불가에서는 수정 제안 없이 보류. 관련352개 통과·1개 생략; 합성 사례의 기본5항목·상세 원문/경로·종료1·파일 변경0·추가 호출0 확인. 독립 리뷰132개 통과·APPROVE. 사용자 이해 재검증 전. 0.5.8 고정 전체 품질 게이트4312개 통과·3개 생략·12개 제외. OMC 가드·TDD·리뷰 결속·비밀값 검사 통과. 타입·린트 별도 게이트는 계약에 없음. 설치11개 엄격 검증 통과. 사용자 이해 효과 재검증은 미실행. 0.5.8 고정 전체 품질 게이트4312개 통과·3개 생략·12개 제외. OMC 가드·TDD·리뷰 결속·비밀값 검사 통과. 타입·린트 별도 게이트는 계약에 없음. 설치11개 엄격 검증 통과. 사용자 이해 효과 재검증은 미실행.

기본 형식: 요청받은 작업 → 하려던 일 → 작업 위치 → 중단 이유 → 중단된 문제를 해결하기 위한 제안. 기본에 내부 목표 파일명·해시·완료 증빙 경로를 노출하지 않고 상세에서 전체 요청 원문·관련 파일 경로·변경 근거를 확인한다. 확인할 내용을 먼저 안내하며 자동 수정·실행을 하지 않는다.

0.5.8 패치 릴리스c047947·정상11개 사용처 재설치·엄격 검증 완료(sellerhub·OKX 포함). 설치본0.5.8·무결성clean·원본일치. 설치본·receipt 백업 보존. 비교34011개 중34009개 해시 일치. storefront BankdaHistory.tsx·BankdaHistory.styled.ts는 설치 전11:27경 수정 관측(설치11:33, OMC 관리 항목0개)으로 보존하고 전체 불변 주장 제외. 수정 원인은 추정하지 않음. marketing-project는 기존0.3.4 보존.

근거: `docs/comparison-dashboard/evidence/c3-five-part/`. 다음: 설치된0.5.8의 기본 다섯 항목 안내만 제공한 새 격리 사례에서 사용자 이해를 확인한다. 형식 동의·코드 검증·설치를 실제 이해 효과로 집계하지 않으며 실무·다중 사례 효과·경쟁 우열은 미검증으로 유지한다.


## 다섯 항목 안내 최종 리뷰·커밋 정리 (2026-10-15)

기본 다섯 항목·상세 근거 분리 보완은 독립workflow132개 통과·APPROVE. 관련352개 통과·1개 생략 이력 유지. 승인된 코드·테스트·합성 근거·현재 문서를 이번 커밋 범위로 정리한다. 릴리스·사용처 설치·새 안내 사용자 이해 검증은 아직 수행하지 않았다.


## 0.5.8 다섯 항목 안내 릴리스·설치 완료 (2026-10-15)

0.5.8 패치 릴리스c047947·정상11개 사용처 재설치·엄격 검증 완료(sellerhub·OKX 포함). 설치본0.5.8·무결성clean·원본일치. 설치본·receipt 백업 보존. 비교34011개 중34009개 해시 일치. storefront BankdaHistory.tsx·BankdaHistory.styled.ts는 설치 전11:27경 수정 관측(설치11:33, OMC 관리 항목0개)으로 보존하고 전체 불변 주장 제외. 수정 원인은 추정하지 않음. marketing-project는 기존0.3.4 보존. 전체 품질 게이트4312개 통과·3개 생략·12개 제외. 타입·린트 별도 게이트는 계약에 없음. 원격push·tag는 수행하지 않음. 사용자 이해 재검증과 실무 효과는 미검증.

다음: 설치된0.5.8의 기본 다섯 항목 안내만 제공한 새 격리 사례에서 사용자 이해를 확인한다. 형식 동의·코드 검증·설치를 실제 이해 효과로 집계하지 않으며 실무·다중 사례 효과·경쟁 우열은 미검증으로 유지한다.
