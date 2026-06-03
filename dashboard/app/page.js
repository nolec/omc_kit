import { resolveDashboardRoot } from "../lib/omc-root.mjs";
import { buildOperationsConsoleSummary, listRecentRuns, readCurrentRun, readRunDetail } from "../lib/omc-runs.mjs";

function statusClass(status) {
  return `status-${String(status || "unknown").toLowerCase()}`;
}

function statusLabel(status) {
  const normalized = String(status || "unknown").toLowerCase();
  const map = {
    pending: "대기",
    running: "실행 중",
    completed: "완료",
    failed: "실패",
    cancelled: "취소됨",
    timeout: "시간 초과",
    held: "보류",
    invalid: "비정상",
    invalid_started_at: "시간값 오류",
    unknown: "알 수 없음",
  };
  return map[normalized] ?? normalized;
}

function formatKst(value) {
  if (!value) {
    return "N/A";
  }
  const raw = String(value);
  const compactUtc = /^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})Z$/;
  const matched = compactUtc.exec(raw);
  const normalized = matched ? `${matched[1]}T${matched[2]}:${matched[3]}:${matched[4]}Z` : raw;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) {
    return raw;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function renderVerdict(value) {
  return value ?? "해당 없음";
}

function renderReasonOrError(step) {
  const text = step?.reason ?? step?.error_message;
  if (text) {
    return text;
  }
  return step?.status === "completed" ? "-" : "해당 없음";
}

function freshnessLabel(value) {
  const map = {
    fresh: "최신",
    stale: "오래됨",
    idle: "유휴",
    unavailable: "없음",
    unknown: "판단 불가",
  };
  return map[value] ?? value ?? "해당 없음";
}

function freshnessHelpText(value) {
  const map = {
    fresh: "현재 실행의 최신 활동 시각이 최근 범위 안에 있습니다.",
    stale: "현재 실행의 최신 활동이 오래되어 확인이 필요합니다.",
    idle: "현재 실행이 진행 중 상태가 아니어서 최신성 판단이 중요하지 않습니다.",
    unavailable: "현재 실행 데이터가 없습니다.",
    unknown: "현재 실행은 있지만 최신 활동 시각이 없습니다.",
  };
  return map[value] ?? "최신성 정보를 해석할 수 없습니다.";
}

function nextActionLabel(action) {
  const map = {
    review_approval_required_run: "승인 대기 실행 확인",
    review_held_run: "보류 실행 확인",
    inspect_failed_run: "실패 실행 점검",
    check_session_freshness: "세션 최신성 확인",
    none: "즉시 조치 없음",
  };
  return map[action] ?? action ?? "해당 없음";
}

function manualGateReasonLabel(value) {
  const map = {
    plan_confirmation: "계획 확인 필요",
  };
  return map[value] ?? value ?? "해당 없음";
}

function sessionHealthLabel(value) {
  const map = {
    healthy: "정상",
    attention: "주의 필요",
    unknown: "판단 불가",
  };
  return map[value] ?? value ?? "해당 없음";
}

function sessionHealthReasonLabel(value) {
  const map = {
    running_or_idle: "현재 실행은 있으나 즉시 개입 신호가 없습니다.",
    approval_required: "사용자 승인 또는 수동 확인이 필요한 실행이 있습니다.",
    stale_current_run: "현재 실행의 최신 활동이 오래되어 확인이 필요합니다.",
    action_required_runs: "보류 또는 실패 실행이 남아 있습니다.",
    no_current_run: "현재 실행 데이터가 없습니다.",
    unknown: "세션 건강도를 계산할 입력이 부족합니다.",
    unavailable: "세션 건강도 데이터가 없습니다.",
  };
  return map[value] ?? value ?? "세션 건강도 사유가 없습니다.";
}

function availabilityLabel(value) {
  const map = {
    current_run: "현재 실행 상태",
    recent_runs: "최근 실행 목록",
    run_status_counts: "실행 상태 집계",
    known_reason_buckets: "알려진 원인 분류",
    next_action_rule: "고정 규칙 다음 액션",
    queue_depth: "큐 적체 수",
    worker_health: "워커 상태",
    parallel_agent_count: "병렬 에이전트 수",
    per_step_duration: "단계별 소요 시간",
  };
  return map[value] ?? value;
}

export default async function Home() {
  const root = resolveDashboardRoot();
  const current = await readCurrentRun(root);
  const runs = await listRecentRuns(root, 50);
  const selected = runs[0]?.run_id ? await readRunDetail(root, runs[0].run_id) : null;
  const steps = selected?.raw?.steps && typeof selected.raw.steps === "object" ? selected.raw.steps : {};
  const operationsSummary = buildOperationsConsoleSummary(current, runs, {
    currentUpdatedAt: current?.last_activity_at ?? null,
  });

  return (
    <main>
      <section className="panel">
        <h1>OMC 오토파일럿 대시보드</h1>
      </section>

      <section className="grid">
        <div className="panel">
          <h2>현재 상태</h2>
          {current ? <div className={statusClass(current.status)}>{statusLabel(current.status)}</div> : <div className="muted">현재 파이프라인 결과가 없습니다.</div>}
          <div className="muted">브랜치: {current?.branch ?? "N/A"}</div>
          <div className="muted">모드: {current?.mode ?? "N/A"}</div>
          <div className="muted">실행기: {current?.executor ?? "N/A"}</div>
        </div>
        <div className="panel">
          <h2>실행 건수</h2>
          <div>{runs.length}</div>
          <div className="muted">최근 run id 기준 50건 표시</div>
        </div>
      </section>

      <section className="grid">
        <div className="panel">
          <h2>운영 콘솔 요약</h2>
          <div className="muted">단일 executor 기준으로 동작하는 read-only 운영 콘솔입니다.</div>
          <div>액션 필요 실행: {operationsSummary.action_required_count}</div>
          <div>승인 필요: {operationsSummary.approval_required_count}</div>
          <div>복구 필요: {operationsSummary.recovery_required_count}</div>
          <div>보류: {operationsSummary.held_count}</div>
          <div>실패: {operationsSummary.failed_count}</div>
          <div className="muted">복구 필요 신호는 승인 필요 항목과 일부 겹칠 수 있습니다.</div>
          <div className="muted">세션 상태: {freshnessLabel(operationsSummary.freshness_status)}</div>
          <div className="muted">{freshnessHelpText(operationsSummary.freshness_status)}</div>
        </div>
        <div className="panel">
          <h2>다음 액션</h2>
          <div>{nextActionLabel(operationsSummary.next_action.action)}</div>
          <div className="muted">사유: {operationsSummary.next_action.reason ?? "해당 없음"}</div>
        </div>
      </section>

      <section className="grid">
        <div className="panel">
          <h2>운영 큐</h2>
          <div>승인 큐: {operationsSummary.approval_queue.length}</div>
          <div>복구 큐: {operationsSummary.recovery_queue.length}</div>
          <div>승인 사유 요약: {manualGateReasonLabel(operationsSummary.approval_queue[0]?.manual_gate_reason)}</div>
          <div className="muted">
            승인 큐는 manual gate 또는 보류 상태 실행을 포함합니다.
          </div>
        </div>
        <div className="panel">
          <h2>복구 요약</h2>
          <div>실행 중 멈춤: {operationsSummary.stale_run_count}</div>
          <div>실패 실행: {operationsSummary.failed_count}</div>
          <div className="muted">
            retry 소진, stale running, failed run을 우선적으로 확인하세요.
          </div>
        </div>
        <div className="panel">
          <h2>승인 대기 상세</h2>
          <div>수동 게이트 사유: {manualGateReasonLabel(current?.manual_gate_reason)}</div>
          <div>누적 재시도: {current?.retry_count ?? 0}</div>
          <div>재개 횟수: {current?.resume_count ?? 0}</div>
          <div className="muted">
            현재 실행이 승인 대기 상태가 아니면 참고용 운영 신호만 표시합니다.
          </div>
        </div>
      </section>

      <section className="grid">
        <div className="panel">
          <h2>단계 시간 요약</h2>
          <div>기록된 실행: {operationsSummary.duration_summary.total_runs_with_duration}</div>
          <div>누적 단계 시간: {operationsSummary.duration_summary.total_duration_sec}초</div>
          <div className="muted">
            최장 단계: {operationsSummary.duration_summary.longest_step
              ? `${operationsSummary.duration_summary.longest_step.run_id} / ${operationsSummary.duration_summary.longest_step.name} / ${operationsSummary.duration_summary.longest_step.duration_sec}초`
              : "해당 없음"}
          </div>
        </div>
        <div className="panel">
          <h2>세션 건강도</h2>
          <div>{sessionHealthLabel(operationsSummary.session_health.status)}</div>
          <div className="muted">{sessionHealthReasonLabel(operationsSummary.session_health.reason)}</div>
        </div>
      </section>

      <section className="panel">
        <h2>데이터 가용성</h2>
        <div className="muted">queue 적체와 worker 상태는 아직 수집하지 않습니다.</div>
        <div className="muted">현재 콘솔이 볼 수 있는 데이터</div>
        <ul>
          {operationsSummary.data_availability.available.map((item) => (
            <li key={item}>{availabilityLabel(item)}</li>
          ))}
        </ul>
        <div className="muted">아직 수집하지 않는 데이터</div>
        <ul>
          {operationsSummary.data_availability.unavailable.map((item) => (
            <li key={item}>{availabilityLabel(item)}</li>
          ))}
        </ul>
      </section>

      <section className="grid">
        <div className="panel">
          <h2>원인 분류</h2>
          {operationsSummary.reason_breakdown.length === 0 ? (
            <div className="muted">액션이 필요한 원인이 없습니다.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>분류</th>
                  <th>건수</th>
                </tr>
              </thead>
              <tbody>
                {operationsSummary.reason_breakdown.map((item) => (
                  <tr key={item.key}>
                    <td>{item.label}</td>
                    <td>{item.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="panel">
          <h2>액션 필요 실행</h2>
          {operationsSummary.action_required_count === 0 ? (
            <div className="muted">즉시 조치가 필요한 실행이 없습니다.</div>
          ) : (
            <div className="muted">보류 또는 실패 상태의 실행을 우선 확인하세요.</div>
          )}
        </div>
      </section>

      <section className="panel">
        <h2>최근 실행 이력</h2>
        {runs.length === 0 ? (
          <div className="muted">실행 이력이 없습니다.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Run ID</th>
                <th>상태</th>
                <th>브랜치</th>
                <th>모드</th>
                <th>시작 시간 (KST)</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td>{run.run_id}</td>
                  <td className={statusClass(run.status)}>{statusLabel(run.status)}</td>
                  <td>{run.branch ?? "N/A"}</td>
                  <td>{run.mode ?? "N/A"}</td>
                  <td>{formatKst(run.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>최신 실행 상세</h2>
        {!selected ? (
          <div className="muted">선택된 실행이 없습니다.</div>
        ) : (
          <>
            <div className="muted">Run ID: {selected.run_id}</div>
            <div className="muted">단계 타임라인</div>
            <table>
              <thead>
                <tr>
                  <th>단계</th>
                  <th>상태</th>
                  <th>시작 시간</th>
                  <th>종료 시간</th>
                  <th>소요 시간</th>
                  <th>판정</th>
                  <th>사유 / 오류</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(steps).map(([stepName, step]) => (
                  <tr key={stepName}>
                    <td>{stepName}</td>
                    <td className={statusClass(step?.status)}>{statusLabel(step?.status)}</td>
                    <td>{formatKst(step?.started_at)}</td>
                    <td>{formatKst(step?.finished_at)}</td>
                    <td>{step?.duration_sec ?? "N/A"}</td>
                    <td>{renderVerdict(step?.verdict)}</td>
                    <td>{renderReasonOrError(step)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 12 }}>
              <h2>원본 JSON</h2>
              <pre>{JSON.stringify(selected.raw, null, 2)}</pre>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
