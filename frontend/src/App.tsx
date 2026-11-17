import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { AUTH_EVENT, prefs } from "./api";
import { useApi } from "./hooks";
import Dashboard from "./pages/Dashboard";
import Candidates from "./pages/Candidates";
import CandidateProfile from "./pages/CandidateProfile";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import RunResults from "./pages/RunResults";
import ReportPage from "./pages/Report";
import Compare from "./pages/Compare";
import Search from "./pages/Search";
import Admin from "./pages/Admin";

export default function App() {
  const [blind, setBlind] = useState(prefs.blind());
  const [tenant, setTenant] = useState(prefs.tenant());
  const { data: health } = useApi<{ llm_enabled: boolean; embedding_model: string; auth_mode?: string }>("/health");
  const [needKey, setNeedKey] = useState(false);
  const signedIn = !!prefs.apiKey();
  const localMode = !health || health.auth_mode === "none";

  useEffect(() => {
    const on = () => setNeedKey(true);
    window.addEventListener(AUTH_EVENT, on);
    return () => window.removeEventListener(AUTH_EVENT, on);
  }, []);
  useEffect(() => {
    if (health && health.auth_mode === "api_key" && !prefs.apiKey()) setNeedKey(true);
  }, [health]);

  const saveKey = (k: string) => {
    if (!k.trim()) return;
    prefs.setApiKey(k.trim());
    window.location.reload();
  };
  const signOut = () => {
    prefs.setApiKey(null);
    window.location.reload();
  };

  const toggleBlind = () => {
    prefs.setBlind(!blind);
    setBlind(!blind);
  };
  const changeTenant = (t: string) => {
    const clean = t.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64) || "demo";
    prefs.setTenant(clean);
    setTenant(clean);
    window.location.reload();
  };

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">Evidence Matching<span>Recruiter workspace{localMode ? " · local build" : ""}</span></div>
        <nav className="nav">
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
          <NavLink to="/candidates">Candidates</NavLink>
          <NavLink to="/search">Search</NavLink>
          <NavLink to="/admin">Costs &amp; audit</NavLink>
        </nav>
        <div className="side-foot">
          <label className="check" title="Hide names in rankings and reports to reduce bias">
            <input type="checkbox" checked={blind} onChange={toggleBlind} /> Blind review
          </label>
          {localMode ? (
            <label className="field">Workspace (tenant)
              <input defaultValue={tenant} onBlur={(e) => e.target.value !== tenant && changeTenant(e.target.value)} />
            </label>
          ) : (
            <div>
              Workspace: <span className="badge b-muted">{signedIn ? "from your key" : "not signed in"}</span>{" "}
              {signedIn && <button className="sm" onClick={signOut}>Sign out</button>}
            </div>
          )}
          <div>
            Mode:{" "}
            <span className={`badge ${health?.llm_enabled ? "b-accent" : "b-muted"}`}>
              {health ? (health.llm_enabled ? "OpenRouter" : "Offline ($0)") : "…"}
            </span>
          </div>
        </div>
      </aside>
      {needKey && (
        <div className="modal-back">
          <form className="card modal" onSubmit={(e) => {
            e.preventDefault();
            saveKey(new FormData(e.currentTarget).get("key") as string);
          }}>
            <h2>Sign in to your workspace</h2>
            <p className="muted small">This server needs a workspace key. Ask your administrator for one. It is kept
              only for this browser tab.</p>
            <input name="key" type="password" autoFocus placeholder="nsm_..." autoComplete="off" />
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" type="submit">Sign in</button>
            </div>
          </form>
        </div>
      )}
      <main className="main" key={`${tenant}-${blind}`}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/jobs/:jid" element={<JobDetail />} />
          <Route path="/runs/:rid" element={<RunResults />} />
          <Route path="/runs/:rid/candidates/:cid" element={<ReportPage />} />
          <Route path="/runs/:rid/compare" element={<Compare />} />
          <Route path="/candidates" element={<Candidates />} />
          <Route path="/candidates/:cid" element={<CandidateProfile />} />
          <Route path="/search" element={<Search />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<div className="empty">Page not found.</div>} />
        </Routes>
      </main>
    </div>
  );
}
