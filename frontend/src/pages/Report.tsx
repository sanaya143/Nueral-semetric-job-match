import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { blindQ, post, type DeepEval, type Report } from "../api";
import { useApi } from "../hooks";
import { Card, Confidence, ErrorBox, Flags, Kind, LevelBadge, Loading, Quote, ScoreBar, StatusBadge, Suitability } from "../ui";
import { FeedbackButtons } from "./RunResults";

const DIM_LABEL: Record<string, string> = {
  technical: "Technical competency", practical: "Practical experience", domain: "Project / domain relevance",
  nice: "Nice-to-have coverage", seniority: "Seniority fit", logistics: "Logistics", education: "Education",
};

export default function ReportPage() {
  const { rid = "", cid = "" } = useParams();
  const { data, error, loading, setData } = useApi<Report>(`/runs/${rid}/candidates/${cid}?${blindQ()}`);
  const { data: health } = useApi<{ llm_enabled: boolean }>("/health");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const runDeep = async () => {
    setBusy(true); setErr(null);
    try {
      const de = await post<DeepEval>(`/runs/${rid}/candidates/${cid}/deep-eval`);
      setData({ ...data, deep_eval: de });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const de = data.deep_eval;
  const order = { must: 0, nice: 1, learnable: 2 } as Record<string, number>;
  const verdicts = [...data.verdicts].sort((a, b) => (order[a.kind] ?? 3) - (order[b.kind] ?? 3));
  const inferred = data.verdicts.filter((v) => v.level === "inferred");
  const prov = data.provenance || {};

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="small"><Link to={`/runs/${rid}`}>← Ranking · {data.job.title}</Link></div>
          <h1>#{data.rank} · {data.candidate.display_name}</h1>
          <p className="muted">{data.candidate.headline || "—"} · {data.candidate.total_years.toFixed(1)} years · <Link to={`/candidates/${cid}`}>profile</Link></p>
          <div className="row"><Suitability value={data.suitability} /><Confidence band={data.confidence_band} />
            {!data.gate_passed && <span className="badge b-bad">Must-have gate not passed</span>}<Flags flags={data.candidate.flags} /></div>
        </div>
        <div className="vstack end" style={{ gap: 10 }}>
          <div style={{ width: 200 }}><ScoreBar score={data.score} gate={data.gate_passed} /></div>
          <FeedbackButtons runId={rid} row={data} onChange={(a) => setData({ ...data, feedback: a })} />
        </div>
      </div>

      <div className="alert info">{data.disclaimer}</div>
      {data.job.stale && <div className="alert warn">This report uses job version {data.job.run_version}; the job is now at version {data.job.version}.</div>}

      <div className="grid cols-2">
        <Card title="Summary">
          {de ? (
            <div className="stack">
              <p>{de.summary}</p>
              <div className="small muted">
                {de.mode === "llm" ? "Written by the LLM from verified verdicts only." : "Generated from the verdicts by template (no LLM)."}
                {de.note ? ` ${de.note}` : ""}
              </div>
              {de.mode !== "llm" && health?.llm_enabled && (
                <div><button disabled={busy} onClick={runDeep} title="One mid-tier call: about 4K input and up to 1.8K output tokens">
                  {busy ? "Evaluating…" : "Run LLM deep evaluation (≈ $0.02)"}</button></div>
              )}
              <ErrorBox error={err} />
            </div>
          ) : <span className="muted">No evaluation stored.</span>}
        </Card>
        <Card title="Score breakdown">
          <div className="stack" style={{ gap: 8 }}>
            {Object.entries(data.dimension_scores).map(([k, v]) => (
              <div key={k} className="spread small"><span style={{ width: 170 }}>{DIM_LABEL[k] ?? k}</span>
                <div className="bar" style={{ flex: 1 }}><i style={{ width: `${Math.max(2, Math.min(100, v))}%` }} /></div>
                <span style={{ width: 36, textAlign: "right" }}>{Math.round(v)}</span></div>
            ))}
            <div className="small muted">Weights: technical 30, practical 25, domain 15, nice-to-have 10, seniority 10, logistics 10, education 0 unless required, renormalised over what this job uses.</div>
          </div>
        </Card>
      </div>

      <Card title="Requirement by requirement">
        <div className="table-wrap"><table>
          <thead><tr><th>Requirement</th><th>Evidence</th><th>Status</th><th>What the CV shows</th></tr></thead>
          <tbody>
            {verdicts.map((v) => (
              <tr key={v.atom_id}>
                <td style={{ minWidth: 220 }}><div className="row" style={{ gap: 6 }}><Kind kind={v.kind} /><span className="small muted">{v.dimension}</span></div>
                  <div style={{ marginTop: 4 }}>{v.atom_text}</div></td>
                <td><LevelBadge level={v.level} /><div className="small muted">{v.verified_by === "llm" ? "LLM-verified" : "rule-based"}</div></td>
                <td><StatusBadge status={v.status} /></td>
                <td style={{ minWidth: 320 }}>
                  {v.evidence.filter((e, i, all) => all.findIndex((x) => x.quote === e.quote) === i).slice(0, 2)
                    .map((e, i) => <Quote key={i} quote={e.quote} context={e.context} inferred={v.level === "inferred"} />)}
                  {v.note && <div className="small muted">{v.note}</div>}
                  {v.skills.length > 1 && <div className="chips" style={{ marginTop: 4 }}>{v.skills.map((s) => (
                    <span key={s.skill_id} className="chip">{s.name}: {s.level.replace("_", " ")}</span>))}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>

      <div className="grid cols-2">
        <Card title="Strengths (cited)">
          {de && de.strengths.length ? de.strengths.map((s, i) => (
            <div key={i} style={{ marginBottom: 8 }}><div>{s.text}</div><Quote quote={s.quote} context={s.context} /></div>
          )) : <span className="muted small">No demonstrated strengths recorded.</span>}
          {inferred.length > 0 && (
            <><hr /><h3>Inferred, not stated</h3>
              <div className="small muted">Implied by related work. Treat as questions, not facts.</div>
              <ul className="small">{inferred.map((v) => <li key={v.atom_id}>{v.atom_text}</li>)}</ul></>
          )}
        </Card>
        <Card title="Gaps and things to verify">
          {data.concerns.length === 0 && (!de || de.gaps.length === 0) ? <span className="muted small">None.</span> : (
            <ul className="small">
              {data.concerns.map((c, i) => <li key={`c${i}`}>{c.text}</li>)}
              {de?.gaps.filter((g) => !data.concerns.some((c) => c.text.includes(g))).map((g, i) => <li key={`g${i}`}>{g}</li>)}
            </ul>
          )}
          {de && de.interview_questions.length > 0 && (
            <><hr /><h3>Suggested interview questions</h3><ol className="small">{de.interview_questions.map((q, i) => <li key={i}>{q}</li>)}</ol></>
          )}
        </Card>
      </div>

      <details><summary>Evidence units from the CV ({data.evidence_units.length})</summary>
        <div className="stack" style={{ marginTop: 8 }}>
          {data.evidence_units.map((u) => (
            <div key={u.id} className="card"><div className="small muted">{u.kind} · {u.context}</div><div className="unit">{u.text}</div></div>
          ))}
        </div>
      </details>
      <details><summary>Provenance (for audit and reproduction)</summary>
        <div className="card mono" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
          {JSON.stringify({ verifier: data.verifier, stage1_score: data.stage1_score, ...prov }, null, 2)}
        </div>
      </details>
    </div>
  );
}
