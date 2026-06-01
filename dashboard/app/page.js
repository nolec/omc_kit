import { resolveDashboardRoot } from "../lib/omc-root.mjs";
import { listRecentRuns, readCurrentRun, readRunDetail } from "../lib/omc-runs.mjs";

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

export default async function Home() {
  const root = resolveDashboardRoot();
  const current = await readCurrentRun(root);
  const runs = await listRecentRuns(root, 50);
  const selected = runs[0]?.run_id ? await readRunDetail(root, runs[0].run_id) : null;
  const steps = selected?.raw?.steps && typeof selected.raw.steps === "object" ? selected.raw.steps : {};

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
            <table>
              <thead>
                <tr>
                  <th>단계</th>
                  <th>상태</th>
                  <th>판정</th>
                  <th>사유 / 오류</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(steps).map(([stepName, step]) => (
                  <tr key={stepName}>
                    <td>{stepName}</td>
                    <td className={statusClass(step?.status)}>{statusLabel(step?.status)}</td>
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
