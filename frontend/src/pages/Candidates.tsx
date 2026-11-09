import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, prefs } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Flags, Loading, fmtDate } from "../ui";

export interface CandRow {
  id: string; display_name: string; headline: string; status: string; error: string | null;
  total_years: number; location: string | null; work_mode: string | null; notice_period_days: number | null;
  flags: string[]; extraction_mode: string; source: string; filename: string | null; created_at: string | null;
}
interface List { total: number; items: CandRow[] }

const DONE = new Set(["ready", "duplicate", "failed"]);
const STATUS_CLS: Record<string, string> = { ready: "b-good", duplicate: "b-muted", failed: "b-bad" };

export function blindName(c: { id: string; display_name: string }): string {
  return prefs.blind() ? `Candidate ${c.id.slice(0, 6).toUpperCase()}` : c.display_name;
}

export default function Candidates() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const params = new URLSearchParams({ limit: "500" });
  if (status) params.set("status", status);
  if (q.trim()) params.set("q", q.trim());
  const { data, error, loading, reload } = useApi<List>(`/candidates?${params}`, {
    poll: 1500, until: (d) => d.items.every((c) => DONE.has(c.status)),
  });

  const upload = async (files: FileList | null) => {
    if (!files || !files.length) return;
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await api<{ uploaded: { filename: string; status: string; error?: string }[] }>("POST", "/candidates/upload", fd);
      const rejected = r.uploaded.filter((u) => u.status === "rejected");
      setMsg(`${r.uploaded.length - rejected.length} file(s) queued for parsing and extraction.` +
        (rejected.length ? ` ${rejected.length} rejected: ${rejected.map((u) => `${u.filename} (${u.error})`).join(", ")}` : ""));
      reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Candidates</h1>
          <p className="muted">Upload CVs you are allowed to process. Each one is parsed, cleaned of hidden text, and split into evidence units.</p>
        </div>
      </div>

      <div
        className={`drop ${over ? "over" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}
        role="button" tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      >
        <b>{busy ? "Uploading…" : "Drop CVs here or click to choose"}</b>
        <div className="small muted">PDF, DOCX, TXT, MD, or a ZIP of them · up to 10 MB per file</div>
        <input ref={input} type="file" multiple accept=".pdf,.docx,.txt,.md,.zip" hidden onChange={(e) => upload(e.target.files)} />
      </div>
      {msg && <div className="alert info">{msg}</div>}
      <ErrorBox error={err} />

      <Card
        title={`Candidates${data ? ` (${data.total})` : ""}`}
        actions={
          <div className="row">
            <input placeholder="Filter by headline" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 200 }} />
            <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 140 }}>
              <option value="">All statuses</option>
              <option value="ready">Ready</option>
              <option value="processing">Processing</option>
              <option value="duplicate">Duplicate</option>
              <option value="failed">Failed</option>
            </select>
          </div>
        }
      >
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !data || data.items.length === 0 ? (
          <Empty>No candidates yet. Upload CVs above, or run <span className="mono">python scripts/seed_demo.py</span> for the synthetic demo set.</Empty>
        ) : (
          <div className="table-wrap"><table>
            <thead><tr><th>Candidate</th><th>Headline</th><th>Years</th><th>Location / mode</th><th>Notice</th><th>Status</th><th>Flags</th><th>Added</th></tr></thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.id}>
                  <td><Link to={`/candidates/${c.id}`}>{blindName(c)}</Link>
                    <div className="small muted">{c.filename}</div></td>
                  <td>{c.headline || <span className="muted">—</span>}</td>
                  <td>{c.status === "ready" ? c.total_years.toFixed(1) : "—"}</td>
                  <td>{c.location || <span className="muted">not stated</span>}{c.work_mode ? ` · ${c.work_mode}` : ""}</td>
                  <td>{c.notice_period_days != null ? `${c.notice_period_days} d` : <span className="muted">—</span>}</td>
                  <td><span className={`badge ${STATUS_CLS[c.status] ?? "b-accent"}`} title={c.error ?? ""}>{c.status}</span>
                    {c.status === "ready" && <div className="small muted">{c.extraction_mode}</div>}</td>
                  <td><Flags flags={c.flags} /></td>
                  <td className="small">{fmtDate(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
