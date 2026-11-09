// Thin API client for the local FastAPI backend (/api/v1). Tenant comes from the header selector.

export type Level =
  | "demonstrated" | "applied" | "inferred" | "listed_only" | "missing"
  | "stated" | "not_stated";
export type Status = "met" | "partial" | "not_met" | "unknown";

export interface Evidence { unit_id: string; context: string; quote: string; level: string; skill_id: string | null; via: string }
export interface Verdict {
  atom_id: string; atom_text: string; kind: "must" | "nice" | "learnable"; dimension: string;
  level: Level; status: Status; score: number; evidence: Evidence[];
  skills: { skill_id: string; name: string; level: string }[];
  source: string; note: string; confidence: number; verified_by: string;
}
export interface CandidateRef {
  id: string; display_name: string; headline: string; total_years: number; location: string | null;
  work_mode: string | null; notice_period_days: number | null; flags: string[]; extraction_mode: string; source: string;
}
export interface ResultRow {
  candidate: CandidateRef; rank: number; score: number; gate_passed: boolean; confidence: number;
  confidence_band: string; suitability: string; stage1_score: number; dimension_scores: Record<string, number>;
  verifier: string; coverage: { must_total: number; must_met: number; demonstrated: number };
  top_gaps: string[]; concern_count: number; feedback?: string | null;
}
export interface RunSummary {
  id: string; job_id: string; job_title: string | null; job_version: number; stale: boolean; status: string;
  mode: string; error: string | null; cost_usd: number; created_at: string | null; finished_at: string | null;
}
export interface Run extends RunSummary { stats: Record<string, any>; results: ResultRow[] }
export interface DeepEval {
  mode: string; summary: string; strengths: { text: string; quote: string; unit_id: string; context: string }[];
  gaps: string[]; interview_questions: string[]; note?: string;
}
export interface Report extends ResultRow {
  verdicts: Verdict[]; concerns: { type: string; text: string }[]; deep_eval: DeepEval | null;
  job: { id: string; title: string; version: number; run_version: number; stale: boolean };
  evidence_units: { id: string; kind: string; context: string; text: string }[];
  provenance: Record<string, any>; disclaimer: string;
}
export interface Atom {
  id?: string; text: string; kind: string; dimension: string; skill_ids: string[];
  skills?: { id: string; name: string }[]; logic: string; min_years: number | null; weight: number;
}
export interface Job {
  id: string; title: string; description: string; status: string; version: number;
  seniority_years: number | null; location: string | null; work_mode: string | null; extraction_mode: string;
  atoms: Atom[]; runs: RunSummary[]; notes: string[];
}

const TENANT_KEY = "nsm.tenant";
const BLIND_KEY = "nsm.blind";

function store(key: string, value?: string): string | null {
  try {
    if (value !== undefined) localStorage.setItem(key, value);
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export const prefs = {
  tenant: () => store(TENANT_KEY) || "demo",
  setTenant: (t: string) => store(TENANT_KEY, t),
  blind: () => store(BLIND_KEY) !== "false",
  setBlind: (b: boolean) => store(BLIND_KEY, String(b)),
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Tenant-ID": prefs.tenant() };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const r = await fetch(`/api/v1${path}`, { method, headers, body: payload });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not JSON */ }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

export const get = <T = any>(p: string) => api<T>("GET", p);
export const post = <T = any>(p: string, b?: unknown) => api<T>("POST", p, b ?? {});
export const put = <T = any>(p: string, b?: unknown) => api<T>("PUT", p, b ?? {});
export const del = <T = any>(p: string) => api<T>("DELETE", p);
export const blindQ = () => `blind=${prefs.blind()}`;
