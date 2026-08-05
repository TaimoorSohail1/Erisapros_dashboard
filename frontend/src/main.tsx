import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./ui/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { FTWilliamsActivityPage } from "./pages/FTWilliamsActivityPage";
import { FTWilliamsFailuresPage } from "./pages/FTWilliamsFailuresPage";
import { FilingReviewPage } from "./pages/FilingReviewPage";
import { FieldRulesPage } from "./pages/FieldRulesPage";
import { ShareFilePage } from "./pages/ShareFilePage";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/ftwilliams/failures" element={<FTWilliamsFailuresPage />} />
          <Route path="/ftwilliams/activity" element={<FTWilliamsActivityPage />} />
          <Route path="/filings/:id" element={<FilingReviewPage />} />
          <Route path="/field-rules" element={<FieldRulesPage />} />
          <Route path="/sharefile" element={<ShareFilePage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  </React.StrictMode>
);
