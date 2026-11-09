import { Link, useParams, useSearchParams } from "react-router-dom";
import { blindQ, type ResultRow } from "../api";
import { useApi } from "../hooks";
import { Card, ErrorBox, Kind, LevelBadge, Loading, ScoreBar, StatusBadge, Suitability } from "../ui";

interface Cell { candidate_id: string; level: string; status: string; quote: string; note: string }
interface CompareData {
  candidates: ResultRow[];
  atoms: { atom_text: string; kind: string; dimension: string; cells: Cell[]; differs: boolean }[];
  differentiators: string[];
}

export default function Compare() {
  const { rid = "" } = useParams();
  const [sp] = useSearchParams();
  const ids = sp.get("ids") ?? "";
  const { data, error, loading } = useApi<CompareData>(`/runs/${rid}/compare?ids=${encodeURIComponent(ids)}&${blindQ()}`);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="small"><Link to={`/runs/${rid}`}>← Ranking</Link></div>
          <h1>Side-by-side comparison</h1>
          <p className="muted">Rows where evidence levels differ are highlighted; those are the requirements that separate these candidates.</p>
        </div>
      </div>

      {data.differentiators.length > 0 && (
        <div className="alert info"><b>Differentiators:</b> {data.differentiators.join(" · ")}</div>
      )}

      <Card>
        <div className="table-wrap"><table>
          <thead>
            <tr><th style={{ minWidth: 200 }}>Requirement</th>
              {data.candidates.map((c) => (
                <th key={c.candidate.id} style={{ minWidth: 240 }}>
                  <Link to={`/runs/${rid}/candidates/${c.candidate.id}`}>#{c.rank} {c.candidate.display_name}</Link>
                  <div style={{ marginTop: 6 }}><ScoreBar score={c.score} gate={c.gate_passed} /></div>
                  <div style={{ marginTop: 4 }}><Suitability value={c.suitability} /></div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.atoms.map((a, i) => (
              <tr key={i} style={a.differs ? { background: "var(--accent-soft)" } : undefined}>
                <td><Kind kind={a.kind} /> <span className="small muted">{a.dimension}</span><div>{a.atom_text}</div></td>
                {a.cells.map((c) => (
                  <td key={c.candidate_id}>
                    <div className="row" style={{ gap: 4 }}><LevelBadge level={c.level} /><StatusBadge status={c.status} /></div>
                    {c.quote ? <div className="quote">“{c.quote}”</div> : c.note ? <div className="small muted">{c.note}</div> : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>
    </div>
  );
}
