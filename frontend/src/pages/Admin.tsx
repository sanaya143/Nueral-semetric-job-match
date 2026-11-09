import { Link } from "react-router-dom";
import type { RunSummary } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Loading, fmtDate, usd } from "../ui";

interface Config {
  llm_mode: string; llm_enabled: boolean; api_key_configured: boolean; embedding_model: string; rerank_enabled: boolean;
  models: Record<string, string>; caps: Record<string, number>; funnel: Record<string, number>;
  storage: { database: string; data_dir: string };
}
interface Costs {
  caps: Record<string, number>; month_to_date_usd: number; tenant_total_usd: number; avg_cost_per_cv_usd: number;
  by_task: { task: string; model: string; calls: number; input_tokens: number; cached_tokens: number; output_tokens: number;
    reasoning_tokens: number; cost_usd: number; failed: number; avg_latency_ms: number }[];
  runs: RunSummary[]; note: string;
}
interface Audit { items: { at: string; actor: string; action: string; entity: string; entity_id: string | null; detail: Record<string, unknown> }[] }

const n = (x: number) => x.toLocaleString();

export default function Admin() {
  const cfg = useApi<Config>("/config");
  const costs = useApi<Costs>("/admin/costs");
  const audit = useApi<Audit>("/admin/audit?limit=200");

  if ((cfg.loading && !cfg.data) || (costs.loading && !costs.data)) return <Loading />;
  const c = cfg.data;
  const k = costs.data;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Costs &amp; audit</h1>
          <p className="muted">Every model call is metered. Budgets are checked before each call, against its worst-case cost.</p>
        </div>
      </div>
      <ErrorBox error={cfg.error || costs.error || audit.error} />

      {k && (
        <div className="grid cols-4">
          <div className="card stat"><div className="label">Month to date</div><div className="value">{usd(k.month_to_date_usd)}</div>
            <div className="small muted">cap {usd(k.caps.monthly_usd)}</div></div>
          <div className="card stat"><div className="label">Avg cost per CV</div><div className="value">{usd(k.avg_cost_per_cv_usd)}</div>
            <div className="small muted">target ≤ {usd(k.caps.per_cv_usd)} · hard stop {usd(k.caps.per_cv_hard_usd)} per CV</div></div>
          <div className="card stat"><div className="label">Per-run cap</div><div className="value">{usd(k.caps.per_run_usd)}</div></div>
          <div className="card stat"><div className="label">Workspace total</div><div className="value">{usd(k.tenant_total_usd)}</div></div>
        </div>
      )}

      {c && (
        <div className="grid cols-2">
          <Card title="Mode and models">
            <div className="small stack" style={{ gap: 4 }}>
              <div>LLM mode: <b>{c.llm_mode}</b> {c.llm_enabled ? <span className="badge b-accent">active</span> :
                <span className="badge b-muted">{c.llm_mode === "openrouter" && !c.api_key_configured ? "no API key set" : "no model calls, $0"}</span>}</div>
              <div>Embeddings: <span className="mono">{c.embedding_model}</span></div>
              <div>Cross-encoder rerank: {c.rerank_enabled ? "on" : "off (blended rule-based rerank)"}</div>
              <hr />
              {Object.entries(c.models).map(([task, m]) => <div key={task}>{task}: <span className="mono">{m}</span></div>)}
              {!c.llm_enabled && <div className="muted">Models are listed for reference; none is called in offline mode.</div>}
            </div>
          </Card>
          <Card title="Funnel and storage">
            <div className="small stack" style={{ gap: 4 }}>
              <div>Stage 1 (retrieval) keeps top {c.funnel.stage1_top_k}</div>
              <div>Stage 2 (rerank) keeps top {c.funnel.stage2_top_k} for verification</div>
              <div>LLM deep evaluation: top {c.funnel.deep_eval_eager_k} automatically, up to rank {c.funnel.deep_eval_lazy_k} on request</div>
              <hr />
              <div>Database: {c.storage.database}</div>
              <div>Data folder: <span className="mono">{c.storage.data_dir}</span></div>
            </div>
          </Card>
        </div>
      )}

      {k && (
        <Card title="Model usage by task">
          {k.by_task.length === 0 ? <Empty>No model calls yet.</Empty> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Task</th><th>Model</th><th>Calls</th><th>Input</th><th>Cached</th><th>Output</th><th title="Hidden reasoning, billed as output. Should stay 0: thinking is switched off.">Thinking</th><th>Cost</th><th>Failed</th><th>Avg latency</th></tr></thead>
              <tbody>
                {k.by_task.map((r) => (
                  <tr key={`${r.task}-${r.model}`}>
                    <td>{r.task}</td><td className="mono">{r.model}</td><td>{n(r.calls)}</td><td>{n(r.input_tokens)}</td>
                    <td>{n(r.cached_tokens)}</td><td>{n(r.output_tokens)}</td><td>{n(r.reasoning_tokens ?? 0)}</td><td>{usd(r.cost_usd)}</td><td>{r.failed}</td><td>{n(r.avg_latency_ms)} ms</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
          <p className="small muted" style={{ marginTop: 8 }}>{k.note}</p>
        </Card>
      )}

      {k && k.runs.length > 0 && (
        <Card title="Recent runs">
          <div className="table-wrap"><table>
            <thead><tr><th>Job</th><th>Mode</th><th>Status</th><th>Cost</th><th>Started</th></tr></thead>
            <tbody>{k.runs.map((r) => (
              <tr key={r.id}><td><Link to={`/runs/${r.id}`}>{r.job_title}</Link></td><td>{r.mode}</td><td>{r.status}</td>
                <td>{usd(r.cost_usd)}</td><td>{fmtDate(r.created_at)}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}

      <Card title="Audit log (latest 200)">
        {!audit.data ? <Loading /> : audit.data.items.length === 0 ? <Empty>No events.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead>
            <tbody>{audit.data.items.map((a, i) => (
              <tr key={i}><td className="small">{fmtDate(a.at)}</td><td>{a.actor}</td><td>{a.action}</td>
                <td className="small">{a.entity}{a.entity_id ? ` · ${a.entity_id.slice(0, 8)}` : ""}</td>
                <td className="mono">{Object.keys(a.detail || {}).length ? JSON.stringify(a.detail) : ""}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
