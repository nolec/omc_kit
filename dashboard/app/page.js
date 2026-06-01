import { resolveDashboardRoot } from "../lib/omc-root.mjs";
import { listRecentRuns, readCurrentRun, readRunDetail } from "../lib/omc-runs.mjs";

function statusClass(status) {
  return `status-${String(status || "unknown").toLowerCase()}`;
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
        <h1>OMC Autopilot Dashboard</h1>
      </section>

      <section className="grid">
        <div className="panel">
          <h2>Current Status</h2>
          {current ? <div className={statusClass(current.status)}>{current.status}</div> : <div className="muted">No current pipeline result</div>}
          <div className="muted">Branch: {current?.branch ?? "N/A"}</div>
          <div className="muted">Mode: {current?.mode ?? "N/A"}</div>
          <div className="muted">Executor: {current?.executor ?? "N/A"}</div>
        </div>
        <div className="panel">
          <h2>Run Count</h2>
          <div>{runs.length}</div>
          <div className="muted">Showing latest 50 by run id</div>
        </div>
      </section>

      <section className="panel">
        <h2>Recent Runs</h2>
        {runs.length === 0 ? (
          <div className="muted">No run history</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Status</th>
                <th>Branch</th>
                <th>Mode</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td>{run.run_id}</td>
                  <td className={statusClass(run.status)}>{run.status}</td>
                  <td>{run.branch ?? "N/A"}</td>
                  <td>{run.mode ?? "N/A"}</td>
                  <td>{run.started_at ?? "N/A"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>Latest Run Detail</h2>
        {!selected ? (
          <div className="muted">No run selected</div>
        ) : (
          <>
            <div className="muted">Run ID: {selected.run_id}</div>
            <table>
              <thead>
                <tr>
                  <th>Step</th>
                  <th>Status</th>
                  <th>Verdict</th>
                  <th>Reason / Error</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(steps).map(([stepName, step]) => (
                  <tr key={stepName}>
                    <td>{stepName}</td>
                    <td className={statusClass(step?.status)}>{step?.status ?? "unknown"}</td>
                    <td>{step?.verdict ?? "N/A"}</td>
                    <td>{step?.reason ?? step?.error_message ?? "N/A"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 12 }}>
              <h2>Raw JSON</h2>
              <pre>{JSON.stringify(selected.raw, null, 2)}</pre>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
