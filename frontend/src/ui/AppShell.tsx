import { FolderSync, LayoutDashboard, ListChecks, ShieldCheck } from "lucide-react";
import { NavLink } from "react-router-dom";
import { FTWilliamsNotifications } from "./FTWilliamsNotifications";

export function AppShell({ children }: { children: React.ReactNode }) {
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
          <NavLink to="/field-rules"><ListChecks size={18} /> Field Rules</NavLink>
          <NavLink to="/sharefile"><FolderSync size={18} /> ShareFile Intake</NavLink>
        </nav>
        <div className="topbar-actions">
          <FTWilliamsNotifications />
          <div className="topbar-status">
            <i />
            Review workspace
          </div>
        </div>
      </header>
      <main className="main">{children}</main>
    </div>
  );
}
