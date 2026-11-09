import { Link } from "react-router-dom";
import type { RunSummary } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Loading, fmtDate, usd } from "../ui";

interface Stats {
  candidates: Record<string, number>; candidates_total: number; jobs: number; runs: number;
  spend_month_usd: number; cap_monthly_usd: number; spend_alert: boolean; recent_runs: RunSummary[];
}

export default function Dashboard() {
  const { data, error, loading } = useApi<Stats>("/stats");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const spendPct = data.cap_monthly_usd ? (100 * data.spend_month_usd) / data.cap_monthly_usd : 0;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="muted">Rank candidates by evidence of work, not keyword density.</p>
        </div>
        <div className="row">
          <Link to="/candidates"><button>Upload CVs</button></Link>
          <Link to="/jobs"><button className="primary">New job</button></Link>
        </div>
      </div>

      <div className="grid cols-4">
        <div className="card stat"><div className="label">Candidates ready</div>
          <div className="value">{data.candidates.ready ?? 0}</div>
          <div className="small muted">{data.candidates_total} total
            {data.candidates.processing ? ` · ${data.candidates.processing} processing` : ""}
            {data.candidates.duplicate ? ` · ${data.candidates.duplicate} duplicate` : ""}
            {data.candidates.failed ? ` · ${data.candidates.failed} failed` : ""}</div></div>
        <div className="card stat"><div className="label">Jobs</div><div className="value">{data.jobs}</div></div>
        <div className="card stat"><div className="label">Match runs</div><div className="value">{data.runs}</div></div>
        <div className="card stat"><div className="label">Model spend this month</div>
          <div className="value">{usd(data.spend_month_usd)}</div>
          <div className={`bar ${data.spend_alert ? "warn" : "good"}`} style={{ marginTop: 6 }}>
            <i style={{ width: `${Math.max(1, Math.min(100, spendPct))}%` }} /></div>
          <div className="small muted">cap {usd(data.cap_monthly_usd)} / month</div></div>
      </div>

      <Card title="Recent match runs">
        {data.recent_runs.length === 0 ? (
          <Empty>No runs yet. Create a job, confirm its requirements, then run a match.</Empty>
        ) : (
          <div className="table-wrap"><table>
            <thead><tr><th>Job</th><th>Status</th><th>Mode</th><th>Cost</th><th>Started</th></tr></thead>
            <tbody>
              {data.recent_runs.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/runs/${r.id}`}>{r.job_title}</Link>{r.stale && <span className="badge b-warn" style={{ marginLeft: 6 }}>job edited since</span>}</td>
                  <td>{r.status}</td><td>{r.mode}</td><td>{usd(r.cost_usd)}</td><td>{fmtDate(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>

      <Card title="How rankings work">
        <div className="grid cols-3 small">
          <div><b>1 · Evidence, not keywords.</b> Each CV is split into roles and projects. Skills are graded
            <i> demonstrated</i>, <i>applied</i>, <i>inferred</i> or <i>listed only</i>. Job titles never count as evidence.</div>
          <div><b>2 · Requirements you confirm.</b> Job descriptions become must-have / nice-to-have / learnable
            requirements. You edit and confirm them before any ranking.</div>
          <div><b>3 · Deterministic, cited scores.</b> Every verdict quotes the CV. The score is computed by fixed
            rules, so the same evidence always gives the same score. You make the decision.</div>
        </div>
      </Card>
    </div>
  );
}
