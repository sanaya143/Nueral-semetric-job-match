import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { blindQ, post, type ResultRow, type Run } from "../api";
import { useApi } from "../hooks";
import { Card, Confidence, Empty, ErrorBox, Flags, Loading, ScoreBar, Suitability, fmtDate, usd } from "../ui";

const DONE = (r: Run) => r.status === "done" || r.status === "failed";

export function FeedbackButtons({ runId, row, onChange }: { runId: string; row: { candidate: { id: string }; feedback?: string | null }; onChange: (a: string) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const send = async (action: string) => {
    try {
      await post("/feedback", { run_id: runId, candidate_id: row.candidate.id, action });
      onChange(action);
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <span className="row" style={{ gap: 4, flexWrap: "nowrap" }} title={err ?? "Your decision is logged for evaluation; it does not change the score."}>
      {(["shortlist", "maybe", "reject"] as const).map((a) => (
        <button key={a} className={`sm ${row.feedback === a ? "on" : ""}`} onClick={() => send(a)}>
          {a === "shortlist" ? "Shortlist" : a === "maybe" ? "Maybe" : "Reject"}</button>
      ))}
    </span>
  );
}

export default function RunResults() {
  const { rid = "" } = useParams();
  const nav = useNavigate();
  const { data: run, error, loading, setData } = useApi<Run>(`/runs/${rid}?${blindQ()}`, { poll: 800, until: DONE });
  const [picked, setPicked] = useState<string[]>([]);
  const [showFailed, setShowFailed] = useState(true);

  if (loading && !run) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!run) return null;

  const st = run.stats || {};
  const ver = st.verification || {};
  const deep = st.deep_eval || {};
  const t = st.timings_ms || {};
  const totalMs = Object.values(t as Record<string, number>).reduce((a, b) => a + b, 0);
  const rows = run.results.filter((r) => showFailed || r.gate_passed);
  const passed = run.results.filter((r) => r.gate_passed).length;

  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= 3 ? p : [...p, id]));
  const setFeedback = (row: ResultRow, action: string) =>
    setData({ ...run, results: run.results.map((r) => (r.candidate.id === row.candidate.id ? { ...r, feedback: action } : r)) });

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="small"><Link to={`/jobs/${run.job_id}`}>← {run.job_title}</Link></div>
          <h1>Ranking · {run.job_title}</h1>
          <div className="row small">
            <span className={`badge ${run.status === "done" ? "b-good" : run.status === "failed" ? "b-bad" : "b-accent"}`}>{run.status}</span>
            <span className="badge b-outline">{run.mode === "llm" ? "LLM-verified" : "offline verdicts"}</span>
            <span className="muted">job v{run.job_version} · {fmtDate(run.created_at)} · cost {usd(run.cost_usd)}{totalMs ? ` · ${(totalMs / 1000).toFixed(1)} s` : ""}</span>
          </div>
        </div>
        <div className="row">
          <button disabled={picked.length < 2} onClick={() => nav(`/runs/${rid}/compare?ids=${picked.join(",")}`)}>
            Compare selected ({picked.length}/3)</button>
        </div>
      </div>

      {run.stale && <div className="alert warn">The job's requirements were edited after this run. Re-run the match to rank against the current version.</div>}
      {run.error && <div className="alert bad">Run failed: {run.error}</div>}
      {ver.budget_stop && <div className="alert warn">LLM verification stopped early: {ver.budget_stop}. Remaining candidates keep offline verdicts.</div>}
      {deep.budget_stop && <div className="alert warn">Deep evaluation stopped early: {deep.budget_stop}</div>}

      {run.status !== "done" && run.status !== "failed" ? <Loading what="Ranking candidates" /> : (
        <>
          <Card title="Funnel">
            <div className="funnel">
              <div className="step"><b>{st.pool ?? "—"}</b>candidates ready</div><span className="arrow">→</span>
              <div className="step"><b>{st.after_filters ?? "—"}</b>after hard filters</div><span className="arrow">→</span>
              <div className="step"><b>{st.stage1 ?? "—"}</b>hybrid retrieval</div><span className="arrow">→</span>
              <div className="step"><b>{st.stage2 ?? "—"}</b>{st.cross_encoder ? "reranked shortlist" : "scored shortlist"}</div><span className="arrow">→</span>
              <div className="step"><b>{ver.verified ?? 0}</b>LLM-verified{ver.rejected_citations ? ` (${ver.rejected_citations} bad quotes dropped)` : ""}</div><span className="arrow">→</span>
              <div className="step"><b>{passed}</b>pass must-haves</div>
            </div>
            <div className="small muted" style={{ marginTop: 8 }}>
              Deep evaluations: {deep.llm ?? 0} by LLM, {deep.offline ?? 0} templated from verdicts.
              Retrieval works per requirement over evidence units, so wording differences ("document retrieval pipeline" vs "RAG") still match.
            </div>
          </Card>

          <Card title={`Ranked candidates (${run.results.length})`} actions={
            <label className="check"><input type="checkbox" checked={showFailed} onChange={(e) => setShowFailed(e.target.checked)} /> Show candidates missing must-haves</label>
          }>
            {rows.length === 0 ? <Empty>No candidates matched. Check filters, or upload more CVs.</Empty> : (
              <div className="table-wrap"><table>
                <thead><tr><th /><th>#</th><th>Candidate</th><th>Score</th><th>Assessment</th><th>Must-haves</th><th>Gaps &amp; flags</th><th>Your decision</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.candidate.id} className={r.gate_passed ? "" : "dim"}>
                      <td><input type="checkbox" aria-label="select for comparison" checked={picked.includes(r.candidate.id)}
                        disabled={!picked.includes(r.candidate.id) && picked.length >= 3} onChange={() => toggle(r.candidate.id)} /></td>
                      <td>{r.rank}</td>
                      <td style={{ minWidth: 200 }}>
                        <Link to={`/runs/${rid}/candidates/${r.candidate.id}`}><b>{r.candidate.display_name}</b></Link>
                        <div className="small muted">{r.candidate.headline || "—"} · {r.candidate.total_years.toFixed(1)} y</div>
                      </td>
                      <td><ScoreBar score={r.score} gate={r.gate_passed} /></td>
                      <td><div className="vstack"><Suitability value={r.suitability} /><Confidence band={r.confidence_band} /></div></td>
                      <td>
                        <b>{r.coverage.must_met}/{r.coverage.must_total}</b> met
                        <div className="small muted">{r.coverage.demonstrated} demonstrated</div>
                        {!r.gate_passed && <span className="badge b-bad">gate not passed</span>}
                      </td>
                      <td style={{ maxWidth: 300 }}>
                        {r.top_gaps.length > 0 && <div className="small">Missing: {r.top_gaps.join("; ")}</div>}
                        <Flags flags={r.candidate.flags} />
                        {r.concern_count > 0 && <div className="small muted">{r.concern_count} item(s) to verify</div>}
                      </td>
                      <td><FeedbackButtons runId={rid} row={r} onChange={(a) => setFeedback(r, a)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
            <p className="small muted" style={{ marginTop: 10 }}>
              Order: must-have gate first, then score. Scores are computed by fixed rules from cited verdicts. They estimate evidence strength, not candidate quality. You decide.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
