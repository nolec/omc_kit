# C3 native 중단·재개 진단 · 2026-10-11

제품 코드 수정 없이 공통3조건 × 두 도구6항목을 진단했다. 실제 AI 비교가 아니라 통제 fixture의 native CLI 진단이다. OMC는 fake Claude provider로 두 단계 산출물을 만들고 상대는 graph command 노드 경로를 사용했다. Ralph·Claude 대화 재개 동등성이나 제품 우월성을 주장하지 않는다.

| 조건 | OMC 관찰 | 상대 graph 관찰 |
|---|---|---|
| 선행 단계 완료·다음 단계 실행 중 강제 중단 후 재개 | provider 호출 중 종료 표시, 중복 실행 방지 위해 수동 대조 요구·exit1; 선행 단계 추가 실행 없음 | macOS contained directory-FD traversal 미지원, 초기 실행 불가·미관측 |
| 동일 중단 상태에서 요구사항 변경 후 재개 | task spec hash mismatch·exit1; 이전 상태 재사용 차단 | 같은 실행 환경 제한·미관측 |
| 동일 중단 상태에서 선행 완료 산출물 삭제 후 재개 | provider-inflight guard가 먼저 차단·exit1; 이 항목만으로 산출물 검사 도달을 주장하지 않음 | 같은 실행 환경 제한·미관측 |

별도 완료 상태 대조: 정상2단계 완료(exit0) 뒤 s1.json을 삭제하고 같은 태스크를 재실행하면 완료 receipt 불일치 안내·exit1. s1·s2 실행 로그는 각1회 유지. 이 대조는6항목 반복 표본에 합산하지 않는다.

관찰된 OMC 후속 후보: provider-inflight 차단 메시지는 수동 대조 필요만 알려준다. 중단된 단계·실제 산출물·확인할 근거·안전한 후속 절차를 직접 보여주는 재개 진단 개선을 검토할 수 있다. 자동 재실행을 허용하거나 in-flight 상태를 강제로 지우는 수정은 수행하지 않았다. 경쟁 도구 대비 부족점으로 확정한 것이 아니라 OMC 자체 사용성 후보다.

비교 미완료 경계: 상대3항목은 실패가 아니라 미관측. 지원 환경에서 같은 graph 계약을 재측정하거나 기존 C3의 Ralph 재개 consumer와 대응하는 별도 비교 계약을 마련해야 상대 대비 갭을 결정할 수 있다. 현재6항목으로 반복 안정성·사용자 개입 감소를 주장하지 않는다.

근거: 격리 c3-native-diagnostic-20261011/admitted/results.json, 각 arm-condition/result.json·initial.stdout/stderr·task.json, ours-missing-completed-control/result.json. manifest.json에 commit·실행 CLI 해시·수집 한계를 기록했다. 초기 preflight6항목은 OMC CLI flag 오류를 수정하기 전 준비 실행으로 제외·원문 보존했다.

측정 모듈 RED: ModuleNotFoundError: No module named probe → GREEN3개 테스트 통과. TDD --staged exit0(스테이징·신규 제품 구현 파일 없음). 제품 코드·기존 측정·대시보드·설치는 변경하지 않았다. 다음은 omc-review로 측정 경로와 결론 범위를 검토한다.
