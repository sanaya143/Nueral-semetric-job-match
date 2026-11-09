import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { post, type Job, type RunSummary } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Loading, fmtDate } from "../ui";

interface JobRow {
  id: string; title: string; status: string; version: number; atoms: number; must: number;
  last_run: RunSummary | null; created_at: string | null;
}

const SAMPLE = {
  title: "AI Engineer (LLM applications)",
  description: `We are hiring an AI Engineer to build LLM-powered products for enterprise clients.

Must have:
- 3+ years of professional Python development
- Hands-on experience building LLM applications
- Retrieval-augmented generation (RAG) pipelines
- Experience with a vector database (pgvector, Qdrant or similar)
- Deploying services to production

Nice to have:
- FastAPI
- Docker and Kubernetes
- Evaluation of LLM outputs

Can learn on the job:
- LangGraph

Location: Bengaluru, hybrid. Notice period up to 60 days.`,
};

export default function Jobs() {
  const nav = useNavigate();
  const { data, error, loading } = useApi<{ items: JobRow[] }>("/jobs");
  const { data: health } = useApi<{ llm_enabled: boolean }>("/health");
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [useLlm, setUseLlm] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    setBusy(true); setErr(null);
    try {
      const j = await post<Job>("/jobs", { title, description: desc, use_llm: useLlm });
      nav(`/jobs/${j.id}`, { state: { notes: j.notes } });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Jobs</h1>
          <p className="muted">Paste a job description. It becomes requirements that you review and confirm before any ranking.</p>
        </div>
      </div>

      <div className="grid cols-2">
        <Card title="New job" actions={<button className="sm ghost" onClick={() => { setTitle(SAMPLE.title); setDesc(SAMPLE.description); }}>Use sample JD</button>}>
          <div className="stack">
            <label className="field">Title<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Backend Engineer (Python)" /></label>
            <label className="field">Job description<textarea value={desc} onChange={(e) => setDesc(e.target.value)}
              placeholder="Paste the full JD. Headings like 'Must have', 'Nice to have' and 'Can learn' help." /></label>
            {health?.llm_enabled && (
              <label className="check" title="Uses the mid-tier model once per job (about $0.03, capped at $0.04)">
                <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
                Parse with LLM (≈ $0.03 per job)
              </label>
            )}
            <ErrorBox error={err} />
            <div><button className="primary" disabled={busy || title.trim().length < 2 || desc.trim().length < 20} onClick={create}>
              {busy ? "Extracting requirements…" : "Extract requirements"}</button></div>
          </div>
        </Card>

        <Card title="All jobs">
          <ErrorBox error={error} />
          {loading && !data ? <Loading /> : !data || data.items.length === 0 ? <Empty>No jobs yet.</Empty> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Job</th><th>Status</th><th>Requirements</th><th>Last run</th></tr></thead>
              <tbody>
                {data.items.map((j) => (
                  <tr key={j.id}>
                    <td><Link to={`/jobs/${j.id}`}>{j.title}</Link><div className="small muted">v{j.version} · {fmtDate(j.created_at)}</div></td>
                    <td><span className={`badge ${j.status === "confirmed" ? "b-good" : "b-warn"}`}>{j.status}</span></td>
                    <td>{j.atoms} <span className="muted small">({j.must} must)</span></td>
                    <td>{j.last_run ? <Link to={`/runs/${j.last_run.id}`}>{j.last_run.status}</Link> : <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </Card>
      </div>
    </div>
  );
}
