import { Columns3, FolderSync, LayoutDashboard, ListChecks, ShieldCheck } from "lucide-react";
import { NavLink } from "../router";
import { FTWilliamsNotifications } from "./FTWilliamsNotifications";
import { authenticationEnabled, signOut } from "../auth";

export function AppShell({ children }: { children: React.ReactNode }) {
  const filingReviewPath = typeof window !== "undefined" && window.location.pathname.startsWith("/filings/")
    ? window.location.pathname
    : null;
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-brand-mark"><ShieldCheck size={20} /></span>
          <span>
            <div className="brand">ERISAPros</div>
            <div className="subtle">Schedule A / 5500 Review</div>
          </span>
        </div>
        <nav className="nav">
          <NavLink to="/" end><LayoutDashboard size={18} /> Dashboard</NavLink>
          {filingReviewPath ? <NavLink to={filingReviewPath}><Columns3 size={18} /> FTW Review</NavLink> : null}
          <NavLink to="/field-rules"><ListChecks size={18} /> Field Rules</NavLink>
          <NavLink to="/sharefile"><FolderSync size={18} /> ShareFile Intake</NavLink>
        </nav>
        <div className="topbar-actions">
          <FTWilliamsNotifications />
          <div className="topbar-status">
            <i />
            Review workspace
          </div>
          {authenticationEnabled() && (
            <button className="button secondary" type="button" onClick={() => { signOut(); window.location.reload(); }}>
              Sign out
            </button>
          )}
        </div>
      </header>
      <main className="main">{children}</main>
    </div>
  );
}
