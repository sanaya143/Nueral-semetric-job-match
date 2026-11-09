import type { ReactNode } from "react";

const LEVEL: Record<string, [string, string]> = {
  demonstrated: ["Demonstrated", "b-good"],
  applied: ["Applied", "b-accent"],
  inferred: ["Inferred", "b-info"],
  listed_only: ["Listed only", "b-warn"],
  missing: ["Missing", "b-bad"],
  stated: ["Stated", "b-muted"],
  not_stated: ["Not stated", "b-muted"],
};
const STATUS: Record<string, [string, string]> = {
  met: ["Met", "b-good"], partial: ["Partial", "b-warn"], not_met: ["Not met", "b-bad"], unknown: ["Unknown", "b-muted"],
};
const SUIT: Record<string, [string, string]> = {
  strong_match: ["Strong evidence match", "b-good"], good_match: ["Good match", "b-accent"],
  partial_match: ["Partial match", "b-warn"], low_match: ["Low match", "b-muted"],
};

export function LevelBadge({ level }: { level: string }) {
  const [label, cls] = LEVEL[level] ?? [level, "b-muted"];
  return <span className={`badge ${cls}`} title={levelHelp(level)}>{label}</span>;
}
export function StatusBadge({ status }: { status: string }) {
  const [label, cls] = STATUS[status] ?? [status, "b-muted"];
  return <span className={`badge ${cls}`}>{label}</span>;
}
export function Suitability({ value }: { value: string }) {
  const [label, cls] = SUIT[value] ?? [value, "b-muted"];
  return <span className={`badge ${cls}`}>{label}</span>;
}
export function Kind({ kind }: { kind: string }) {
  const cls = kind === "must" ? "b-bad" : kind === "nice" ? "b-outline" : "b-info";
  return <span className={`badge ${cls}`}>{kind === "must" ? "Must" : kind === "nice" ? "Nice" : "Learnable"}</span>;
}
export function Confidence({ band }: { band: string }) {
  const cls = band === "high" ? "b-good" : band === "medium" ? "b-accent" : "b-warn";
  return <span className={`badge ${cls}`}>{band} confidence</span>;
}

export function levelHelp(level: string): string {
  return ({
    demonstrated: "The CV describes the person doing this work.",
    applied: "Named in a described role or project, without detail of what they did.",
    inferred: "Not stated; implied by related work. Verify in interview.",
    listed_only: "Only in a skills list or summary.",
    missing: "No evidence in the CV.",
  } as Record<string, string>)[level] ?? "";
}

export function ScoreBar({ score, gate }: { score: number; gate?: boolean }) {
  const cls = gate === false ? "warn" : score >= 75 ? "good" : "";
  return (
    <div className="score">
      <span className="score-num">{score.toFixed(1)}</span>
      <div className={`bar ${cls}`}><i style={{ width: `${Math.max(2, Math.min(100, score))}%` }} /></div>
    </div>
  );
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="spread" style={{ marginBottom: 12 }}>
          {title ? <h2 style={{ margin: 0 }}>{title}</h2> : <span />}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="empty">{what}…</div>;
}
export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="alert bad">{error}</div> : null;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Quote({ quote, context, inferred }: { quote: string; context?: string; inferred?: boolean }) {
  if (!quote) return null;
  return (
    <div className={`quote ${inferred ? "inferred" : ""}`}>
      “{quote}”{context ? <span className="ctx">{context}</span> : null}
    </div>
  );
}

const FLAG_TEXT: Record<string, string> = {
  hidden_text_removed: "Hidden text removed",
  possible_prompt_injection: "Prompt-injection attempt ignored",
  skills_list_without_evidence: "Skills list without evidence",
  senior_title_low_tenure: "Senior title, short tenure",
  overlapping_roles: "Overlapping roles",
  unstructured_cv: "Unstructured CV",
  duplicate: "Duplicate",
};
export function Flags({ flags }: { flags: string[] }) {
  const shown = flags.filter((f) => !f.startsWith("llm_") && !f.startsWith("escalated"));
  if (!shown.length) return null;
  return (
    <span className="chips">
      {shown.map((f) => {
        const key = f.split(":")[0];
        const label = key === "unsupported_expertise_claims" ? "Unsupported expertise claims" : FLAG_TEXT[key] ?? key;
        const bad = key.includes("injection") || key.includes("hidden");
        return <span key={f} className={`badge ${bad ? "b-bad" : "b-warn"}`} title={f}>{label}</span>;
      })}
    </span>
  );
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
export function usd(n: number | null | undefined): string {
  if (!n) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n >= 1) return `$${n.toFixed(2)}`;
  const s = n.toFixed(3); // keep a meaningful third decimal: $0.024, but $0.20 / $0.13
  return `$${s.endsWith("0") ? s.slice(0, -1) : s}`;
}
