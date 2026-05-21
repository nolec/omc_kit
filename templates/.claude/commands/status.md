# /status — OMC 현재 상태 확인

현재 OMC 세션 상태, 최근 작업 맥락, notepad를 출력합니다.

## 실행

```bash
python3 scripts/omc.py state status --target .
cat .omc/notepad.md
```

출력 후 아래 항목을 요약해서 보고합니다:

1. **현재 확정된 작업**: `latest_confirmed_session_id` 기준 요청·역할
2. **미처리 pending**: `latest_pending_request` 있으면 표시
3. **최근 5개 세션**: 어떤 작업들을 했는지 흐름 파악
4. **다음 1액션 제안**: notepad의 handoff 포인트 기준
