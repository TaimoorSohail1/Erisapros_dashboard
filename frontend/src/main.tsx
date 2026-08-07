import React from "react";
import ReactDOM from "react-dom/client";
import { AppShell } from "./ui/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { FTWilliamsActivityPage } from "./pages/FTWilliamsActivityPage";
import { FTWilliamsFailuresPage } from "./pages/FTWilliamsFailuresPage";
import { FilingReviewPage } from "./pages/FilingReviewPage";
import { FieldRulesPage } from "./pages/FieldRulesPage";
import { ShareFilePage } from "./pages/ShareFilePage";
import { AuthGate } from "./ui/AuthGate";
import "./styles.css";

function CurrentPage() {
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  if (path === "/ftwilliams/failures") return <FTWilliamsFailuresPage />;
  if (path === "/ftwilliams/activity") return <FTWilliamsActivityPage />;
  if (path.startsWith("/filings/")) return <FilingReviewPage />;
  if (path === "/field-rules") return <FieldRulesPage />;
  if (path === "/sharefile") return <ShareFilePage />;
  return <DashboardPage />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate>
      <AppShell><CurrentPage /></AppShell>
    </AuthGate>
  </React.StrictMode>
);
