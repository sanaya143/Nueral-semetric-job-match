import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { del, prefs } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, ErrorBox, Flags, LevelBadge, Loading, Quote } from "../ui";
import { blindName, type CandRow } from "./Candidates";

interface Profile extends CandRow {
  contact: { full_name: string | null; email: string | null; phone: string | null; redacted_fields: string[] } | null;
  consent_basis: string;
  units: { id: string; kind: string; title: string; context: string; text: string; start_year: number | null; end_year: number | null }[];
  capabilities: { skill_id: string; name: string; category: string; level: string; quote: string; via: string; confidence: number; context: string }[];
  hidden_text_removed: boolean;
}

const LEVEL_ORDER = ["demonstrated", "applied", "inferred", "listed_only"];
const LEVEL_TITLE: Record<string, string> = {
  demonstrated: "Demonstrated", applied: "Applied", inferred: "Inferred (not stated)", listed_only: "Listed only",
};

export default function CandidateProfile() {
  const { cid = "" } = useParams();
  const nav = useNavigate();
  const { data, error, loading } = useApi<Profile>(`/candidates/${cid}`);
  const [reveal, setReveal] = useState(!prefs.blind());
  const [err, setErr] = useState<string | null>(null);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const erase = async () => {
    if (!window.confirm("Permanently erase this candidate? This deletes the stored file, extracted evidence, vectors, " +
      "match results and feedback for this person. It cannot be undone.")) return;
    try {
      const r = await del<{ deleted: boolean; verified: boolean }>(`/candidates/${cid}`);
      window.alert(r.verified ? "Erased. Verification found no remaining rows or files." : "Erased, but verification found leftovers. Check the audit log.");
      nav("/candidates");
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const download = async () => {
    try {
      const r = await fetch(`/api/v1/candidates/${cid}/file`, { headers: { "X-Tenant-ID": prefs.tenant() } });
      if (!r.ok) throw new Error(`Download failed (${r.status})`);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url; a.download = data.filename || "cv"; a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const byLevel = LEVEL_ORDER.map((lvl) => [lvl, data.capabilities.filter((c) => c.level === lvl)] as const);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="small"><Link to="/candidates">← Candidates</Link></div>
          <h1>{blindName(data)}</h1>
          <p className="muted">{data.headline || "No headline"} · {data.total_years.toFixed(1)} years of dated experience</p>
          <Flags flags={data.flags} />
        </div>
        <div className="row">
          <button onClick={download}>Download original</button>
          <button className="danger" onClick={erase}>Erase candidate</button>
        </div>
      </div>
      <ErrorBox error={err} />
      {data.hidden_text_removed && (
        <div className="alert warn">Hidden or near-invisible text was found in this file and removed before analysis.</div>
      )}

      <div className="grid cols-3">
        <Card title="Logistics (as stated)">
          <div className="small">Location: {data.location || <span className="muted">not stated</span>}</div>
          <div className="small">Work mode: {data.work_mode || <span className="muted">not stated</span>}</div>
          <div className="small">Notice: {data.notice_period_days != null ? `${data.notice_period_days} days` : <span className="muted">not stated</span>}</div>
        </Card>
        <Card title="Contact" actions={!reveal && data.contact ? <button className="sm" onClick={() => setReveal(true)}>Reveal</button> : undefined}>
          {!data.contact ? <span className="muted small">None found</span> : reveal ? (
            <div className="small">
              <div>{data.contact.full_name || "—"}</div><div>{data.contact.email || "—"}</div><div>{data.contact.phone || "—"}</div>
              <div className="muted">Stored separately and removed from text sent to any model.</div>
            </div>
          ) : <span className="muted small">Hidden during blind review.</span>}
        </Card>
        <Card title="Source">
          <div className="small">Source: {data.source}</div>
          <div className="small">Lawful basis: {data.consent_basis}</div>
          <div className="small">Extraction: {data.extraction_mode}</div>
        </Card>
      </div>

      <Card title="Capabilities by evidence strength">
        {data.capabilities.length === 0 ? <Empty>No capabilities extracted.</Empty> : (
          <div className="stack">
            {byLevel.filter(([, items]) => items.length).map(([lvl, items]) => (
              <div key={lvl}>
                <h3>{LEVEL_TITLE[lvl]} <span className="muted small">({items.length})</span></h3>
                <div className="table-wrap"><table>
                  <tbody>
                    {items.map((c) => (
                      <tr key={c.skill_id}>
                        <td style={{ width: 220 }}><b>{c.name}</b><div className="small muted">{c.category}</div></td>
                        <td style={{ width: 120 }}><LevelBadge level={c.level} /></td>
                        <td><Quote quote={c.quote} context={c.context} inferred={c.level === "inferred"} />
                          {c.via !== "direct" && c.via !== "llm" && <div className="small muted">via {c.via}</div>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Evidence units">
        <div className="stack">
          {data.units.map((u) => (
            <details key={u.id} open={u.kind === "experience" || u.kind === "project"}>
              <summary><span className="badge b-outline">{u.kind}</span> {u.context}</summary>
              <div className="unit" style={{ marginTop: 6 }}>{u.text}</div>
            </details>
          ))}
        </div>
      </Card>
    </div>
  );
}
