import { useState } from "react";
import { Link } from "react-router-dom";
import { api, prefs, type CandidateRef, type Verdict } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Kind, LevelBadge, ScoreBar, StatusBadge, Suitability } from "../ui";

interface Result {
  rank: number; candidate: CandidateRef; score: number; gate_passed: boolean; confidence_band: string;
  suitability: string; verdicts: Verdict[]; concerns: { type: string; text: string }[];
}
interface SearchOut { query: string; mode?: string; atoms: { text: string; kind: string }[]; results: Result[]; note?: string }

const EXAMPLES = [
  "Python engineer who has built RAG pipelines with a vector database, in Bengaluru",
  "data engineer with Spark and Airflow, 4+ years",
  "QA automation with Selenium or Playwright, remote",
];

export default function Search() {
  const { data: health } = useApi<{ llm_enabled: boolean }>("/health");
  const [query, setQuery] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [out, setOut] = useState<SearchOut | null>(null);

  const run = async (q = query) => {
    if (q.trim().length < 3) return;
    setBusy(true); setErr(null);
    try {
      setOut(await api<SearchOut>("POST", `/search?blind=${prefs.blind()}`, { query: q, use_llm: useLlm, limit: 20 }));
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
          <h1>Search candidates</h1>
          <p className="muted">Describe who you need in plain language. The query runs through the same evidence funnel as a job (without LLM verification).</p>
        </div>
      </div>
      <Card>
        <div className="stack">
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="e.g. backend engineer who has deployed Python services to production" />
            <button className="primary" disabled={busy || query.trim().length < 3} onClick={() => run()}>{busy ? "Searching…" : "Search"}</button>
          </div>
          <div className="row small">
            <span className="muted">Try:</span>
            {EXAMPLES.map((x) => <button key={x} className="sm ghost" onClick={() => { setQuery(x); run(x); }}>{x}</button>)}
          </div>
          {health?.llm_enabled && (
            <label className="check" title="Small model, about 1K input / 0.5K output tokens">
              <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} /> Interpret the query with an LLM (≈ $0.0005 per search)
            </label>
          )}
        </div>
      </Card>
      <ErrorBox error={err} />

      {out && (
        <Card title={`Results (${out.results.length})`}>
          {out.atoms.length > 0 && (
            <div className="row small" style={{ marginBottom: 10 }}>
              <span className="muted">Understood as:</span>
              {out.atoms.map((a, i) => <span key={i} className="chip"><Kind kind={a.kind} /> {a.text}</span>)}
            </div>
          )}
          {out.note && <div className="alert warn">{out.note}</div>}
          {out.results.length === 0 ? <Empty>No candidates matched.</Empty> : (
            <div className="table-wrap"><table>
              <thead><tr><th>#</th><th>Candidate</th><th>Score</th><th>Assessment</th><th>Evidence per requirement</th></tr></thead>
              <tbody>
                {out.results.map((r) => (
                  <tr key={r.candidate.id} className={r.gate_passed ? "" : "dim"}>
                    <td>{r.rank}</td>
                    <td><Link to={`/candidates/${r.candidate.id}`}><b>{r.candidate.display_name}</b></Link>
                      <div className="small muted">{r.candidate.headline || "—"} · {r.candidate.total_years.toFixed(1)} y</div></td>
                    <td><ScoreBar score={r.score} gate={r.gate_passed} /></td>
                    <td><Suitability value={r.suitability} /></td>
                    <td style={{ minWidth: 300 }}>
                      {r.verdicts.map((v) => (
                        <div key={v.atom_id} className="spread small" style={{ marginBottom: 4 }}>
                          <span title={v.evidence[0]?.quote ?? v.note}>{v.atom_text}</span>
                          {v.dimension === "logistics" ? <StatusBadge status={v.status} /> : <LevelBadge level={v.level} />}
                        </div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </Card>
      )}
    </div>
  );
}
