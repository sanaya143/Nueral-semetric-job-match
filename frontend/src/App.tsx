import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { prefs } from "./api";
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
  const { data: health } = useApi<{ llm_enabled: boolean; embedding_model: string }>("/health");

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
        <div className="brand">Evidence Matching<span>Recruiter workspace · local build</span></div>
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
          <label className="field">Workspace (tenant)
            <input defaultValue={tenant} onBlur={(e) => e.target.value !== tenant && changeTenant(e.target.value)} />
          </label>
          <div>
            Mode:{" "}
            <span className={`badge ${health?.llm_enabled ? "b-accent" : "b-muted"}`}>
              {health ? (health.llm_enabled ? "OpenRouter" : "Offline ($0)") : "…"}
            </span>
          </div>
        </div>
      </aside>
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
