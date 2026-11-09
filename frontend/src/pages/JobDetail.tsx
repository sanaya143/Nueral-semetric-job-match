import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { post, put, type Atom, type Job, type RunSummary } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Kind, Loading, fmtDate, usd } from "../ui";

interface Row extends Atom { key: number; rederive: boolean; touched: boolean }
interface Skill { id: string; name: string; category: string }

const KINDS = [["must", "Must have"], ["nice", "Nice to have"], ["learnable", "Learnable"]];
const DIMS = ["technical", "domain", "experience", "logistics", "education"];
let nextKey = 1;
const toRow = (a: Atom): Row => ({ ...a, key: nextKey++, rederive: false, touched: false });

export default function JobDetail() {
  const { jid = "" } = useParams();
  const nav = useNavigate();
  const loc = useLocation();
  const notes = ((loc.state as { notes?: string[] } | null)?.notes) ?? [];
  const { data: job, error, loading, setData } = useApi<Job>(`/jobs/${jid}`);
  const { data: health } = useApi<{ llm_enabled: boolean }>("/health");
  const { data: tax } = useApi<{ skills: Skill[] }>("/taxonomy");
  const [rows, setRows] = useState<Row[]>([]);
  const [meta, setMeta] = useState({ seniority_years: "", location: "", work_mode: "" });
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [verify, setVerify] = useState(true);
  const [filters, setFilters] = useState({ location: "", work_mode: "", max_notice_days: "" });

  useEffect(() => {
    if (!job) return;
    setRows(job.atoms.map(toRow));
    setMeta({ seniority_years: job.seniority_years?.toString() ?? "", location: job.location ?? "", work_mode: job.work_mode ?? "" });
    setDirty(false);
  }, [job]);

  const byName = useMemo(() => new Map((tax?.skills ?? []).map((s) => [s.name.toLowerCase(), s])), [tax]);

  if (loading && !job) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!job) return null;

  const edit = (key: number, patch: Partial<Row>) => {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
    setDirty(true);
  };
  const addSkill = (row: Row, name: string) => {
    const s = byName.get(name.trim().toLowerCase());
    if (!s || row.skill_ids.includes(s.id)) return false;
    edit(row.key, { skill_ids: [...row.skill_ids, s.id], skills: [...(row.skills ?? []), { id: s.id, name: s.name }], rederive: false, touched: true });
    return true;
  };
  const removeSkill = (row: Row, id: string) =>
    edit(row.key, { skill_ids: row.skill_ids.filter((x) => x !== id), skills: (row.skills ?? []).filter((x) => x.id !== id), rederive: false, touched: true });

  const payload = () => ({
    atoms: rows.filter((r) => r.text.trim()).map((r) => ({
      text: r.text.trim(), kind: r.kind, dimension: r.dimension, logic: r.logic, weight: r.weight,
      min_years: r.min_years, skill_ids: r.rederive ? null : r.skill_ids,
    })),
    seniority_years: meta.seniority_years ? Number(meta.seniority_years) : null,
    location: meta.location.trim() || null,
    work_mode: meta.work_mode || null,
  });

  const act = async (what: string, fn: () => Promise<void>) => {
    setBusy(what); setErr(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const save = () => act("save", async () => setData(await put<Job>(`/jobs/${jid}/requirements`, payload())));
  const confirm = () => act("confirm", async () => {
    if (dirty) await put<Job>(`/jobs/${jid}/requirements`, payload());
    setData(await post<Job>(`/jobs/${jid}/confirm`));
  });
  const run = () => act("run", async () => {
    const f: Record<string, unknown> = {};
    if (filters.location.trim()) f.location = filters.location.trim();
    if (filters.work_mode) f.work_mode = filters.work_mode;
    if (filters.max_notice_days) f.max_notice_days = Number(filters.max_notice_days);
    const r = await post<RunSummary>(`/jobs/${jid}/match`, { verify, filters: f });
    nav(`/runs/${r.id}`);
  });

  const confirmed = job.status === "confirmed" && !dirty;
  const llm = !!health?.llm_enabled;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="small"><Link to="/jobs">← Jobs</Link></div>
          <h1>{job.title}</h1>
          <div className="row small">
            <span className={`badge ${confirmed ? "b-good" : "b-warn"}`}>{confirmed ? "confirmed" : dirty ? "unsaved edits" : "draft"}</span>
            <span className="muted">version {job.version} · requirements by {job.extraction_mode}</span>
          </div>
        </div>
        <div className="row">
          <button disabled={!dirty || !!busy} onClick={save}>{busy === "save" ? "Saving…" : "Save draft"}</button>
          <button className="primary" disabled={confirmed || !!busy || rows.length === 0} onClick={confirm}>
            {busy === "confirm" ? "Confirming…" : "Confirm requirements"}</button>
        </div>
      </div>
      <ErrorBox error={err} />
      {notes.length > 0 && <div className="alert info">{notes.join(" ")}</div>}
      {job.status === "confirmed" && dirty && (
        <div className="alert warn">Saving edits creates version {job.version + 1} as a draft. Earlier runs stay as they were and are marked "job edited since".</div>
      )}

      <Card title={`Requirements (${rows.length})`} actions={
        <button className="sm" onClick={() => { setRows((r) => [...r, toRow({ text: "", kind: "must", dimension: "technical", skill_ids: [], skills: [], logic: "all", min_years: null, weight: 1 })]); setDirty(true); }}>+ Add requirement</button>
      }>
        <p className="small muted">Titles are not requirements. Mark only what the role truly needs as <b>Must</b>; anything a capable hire can pick up in weeks is <b>Learnable</b>. Skills decide what evidence counts; edit them if the match looks wrong.</p>
        <datalist id="skills">{(tax?.skills ?? []).map((s) => <option key={s.id} value={s.name} />)}</datalist>
        {rows.length === 0 ? <Empty>No requirements. Add at least one.</Empty> : (
          <div className="table-wrap"><table style={{ minWidth: 980 }}>
            <thead><tr><th>Requirement</th><th>Type</th><th>Dimension</th><th>Skills (evidence that counts)</th><th>Min years</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td style={{ minWidth: 260 }}>
                    <input value={r.text} onChange={(e) => edit(r.key, { text: e.target.value, rederive: !r.touched })} />
                    {r.rederive && <div className="small muted">Skills will be re-detected from the text on save.</div>}
                  </td>
                  <td style={{ width: 130 }}>
                    <select value={r.kind} onChange={(e) => edit(r.key, { kind: e.target.value })}>
                      {KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                    <div style={{ marginTop: 4 }}><Kind kind={r.kind} /></div>
                  </td>
                  <td style={{ width: 130 }}>
                    <select value={r.dimension} onChange={(e) => edit(r.key, { dimension: e.target.value })}>
                      {DIMS.map((d) => <option key={d} value={d}>{d}</option>)}</select>
                  </td>
                  <td style={{ minWidth: 240 }}>
                    <div className="chips" style={{ marginBottom: 4 }}>
                      {(r.skills ?? []).map((s) => <span className="chip" key={s.id}>{s.name}
                        <button aria-label={`remove ${s.name}`} onClick={() => removeSkill(r, s.id)}>×</button></span>)}
                      {r.skill_ids.length > 1 && (
                        <select value={r.logic} onChange={(e) => edit(r.key, { logic: e.target.value })} style={{ width: "auto", padding: "1px 6px" }}
                          title="'all' = every skill needs evidence; 'any' = one is enough">
                          <option value="all">all of</option><option value="any">any of</option></select>
                      )}
                    </div>
                    <input list="skills" placeholder="add skill…" onKeyDown={(e) => {
                      if (e.key === "Enter" && addSkill(r, e.currentTarget.value)) e.currentTarget.value = "";
                    }} onChange={(e) => { if (addSkill(r, e.target.value)) e.target.value = ""; }} />
                  </td>
                  <td style={{ width: 90 }}>
                    <input type="number" min={0} step={0.5} value={r.min_years ?? ""}
                      onChange={(e) => edit(r.key, { min_years: e.target.value === "" ? null : Number(e.target.value) })} />
                  </td>
                  <td><button className="sm ghost" aria-label="delete requirement" onClick={() => { setRows((rs) => rs.filter((x) => x.key !== r.key)); setDirty(true); }}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        <hr />
        <div className="grid cols-3">
          <label className="field">Overall years expected (seniority)
            <input type="number" min={0} step={0.5} value={meta.seniority_years}
              onChange={(e) => { setMeta({ ...meta, seniority_years: e.target.value }); setDirty(true); }} /></label>
          <label className="field">Job location
            <input value={meta.location} onChange={(e) => { setMeta({ ...meta, location: e.target.value }); setDirty(true); }} /></label>
          <label className="field">Work mode
            <select value={meta.work_mode} onChange={(e) => { setMeta({ ...meta, work_mode: e.target.value }); setDirty(true); }}>
              <option value="">not specified</option><option value="onsite">onsite</option><option value="hybrid">hybrid</option><option value="remote">remote</option>
            </select></label>
        </div>
      </Card>

      <Card title="Run a match">
        {!confirmed ? <div className="alert warn">Confirm the requirements first. Rankings only run against a confirmed version.</div> : (
          <div className="stack">
            <div className="grid cols-3">
              <label className="field">Hard filter: location (optional)
                <input value={filters.location} onChange={(e) => setFilters({ ...filters, location: e.target.value })} placeholder="e.g. Bengaluru" /></label>
              <label className="field">Hard filter: work mode
                <select value={filters.work_mode} onChange={(e) => setFilters({ ...filters, work_mode: e.target.value })}>
                  <option value="">any</option><option value="onsite">onsite</option><option value="hybrid">hybrid</option><option value="remote">remote</option>
                </select></label>
              <label className="field">Hard filter: max notice (days)
                <input type="number" min={0} value={filters.max_notice_days} onChange={(e) => setFilters({ ...filters, max_notice_days: e.target.value })} /></label>
            </div>
            <div className="small muted">Filters only drop candidates whose CV states a conflicting value. Candidates who say nothing are kept.</div>
            {llm && (
              <label className="check" title="Haiku 5.5 verifies the top 50 (≈$0.0007 each); Sonnet 5.5 deep-evaluates the top 5 (≈$0.018 each). Capped at $0.20 per run.">
                <input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)} />
                LLM verification + deep evaluation (≈ $0.06 per run, capped at $0.20)
              </label>
            )}
            {!llm && <div className="small muted">Offline mode: deterministic verdicts, $0. Add an OpenRouter key in <span className="mono">.env</span> to enable LLM verification.</div>}
            <div><button className="primary" disabled={!!busy} onClick={run}>{busy === "run" ? "Starting…" : "Run match"}</button></div>
          </div>
        )}
      </Card>

      <Card title="Runs for this job">
        {job.runs.length === 0 ? <Empty>No runs yet.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Run</th><th>Version</th><th>Status</th><th>Mode</th><th>Cost</th></tr></thead>
            <tbody>
              {job.runs.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/runs/${r.id}`}>{fmtDate(r.created_at)}</Link></td>
                  <td>v{r.job_version}{r.stale && <span className="badge b-warn" style={{ marginLeft: 6 }}>older version</span>}</td>
                  <td>{r.status}</td><td>{r.mode}</td><td>{usd(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>

      <details><summary>Original job description</summary><div className="unit card" style={{ marginTop: 8 }}>{job.description}</div></details>
    </div>
  );
}
