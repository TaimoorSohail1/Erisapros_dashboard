// Local-only visual fixture. No remote requests, real credentials, or FTW writes.
import ReactDOM from "react-dom/client";
import { FTWilliamsAgentSettingsPage } from "../src/pages/FTWilliamsAgentSettingsPage";
import { AppShell } from "../src/ui/AppShell";
import type { FTWLocalAgentDevice } from "../src/types";
import "../src/styles.css";

const scenario = new URLSearchParams(location.search).get("scenario") || "ready";
const device: FTWLocalAgentDevice = {
  id: "qa-agent", name: "Synthetic Windows agent", expected_account: "QA Account",
  status: "CONNECTED", agent_version: "0.4.0", browser_ready: true,
  last_seen_at: new Date().toISOString(), created_at: new Date().toISOString(), pause_requested: false,
};
if (scenario === "old") device.agent_version = "0.3.2";
if (scenario === "offline") device.status = "OFFLINE";
if (scenario === "login") { device.status = "LOGIN_REQUIRED"; device.browser_ready = false; device.last_error = "Complete MFA in the dedicated browser."; }
if (scenario === "active") device.active_job_id = "synthetic-active-job";
window.fetch = async (input, options = {}) => {
  const url = String(input);
  if (url.endsWith("/control") && options.method === "POST") {
    if (scenario === "error") return new Response(JSON.stringify({ detail: "Synthetic network failure: pause was not confirmed. Retry when connected." }), { status: 503 });
    device.pause_requested = JSON.parse(String(options.body)).paused;
    device.status = device.pause_requested ? "PAUSING" : "RESUMING";
    device.browser_ready = false;
    if (scenario !== "offline") window.setTimeout(() => {
      device.status = device.pause_requested ? "PAUSED" : scenario === "login" ? "LOGIN_REQUIRED" : "CONNECTED";
      device.browser_ready = !device.pause_requested && scenario !== "login";
      device.active_job_id = null;
    }, 1000);
    return new Response(JSON.stringify({ device_id: device.id, status: device.status, pause_requested: device.pause_requested }), { headers: { "Content-Type": "application/json" } });
  }
  if (options.method && options.method !== "GET") return new Response(JSON.stringify({ detail: "This local fixture only supports synthetic Pause/Resume." }), { status: 400 });
  const data = url.includes("local-agent/status") ? {
    enabled: true, connected: device.status === "CONNECTED" && device.browser_ready,
    status: device.status, pause_requested: device.pause_requested, device_count: 1, device_name: device.name,
  } : url.includes("local-agent/devices") ? { devices: [device] }
    : url.includes("local-agent/workspaces") ? { workspaces: [] }
    : url.includes("failure-notifications") ? { notifications: [], total: 0, unread_count: 0 }
    : {};
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
};
ReactDOM.createRoot(document.getElementById("root")!).render(<AppShell>
  <div role="note" className="agent-settings-message">Local QA · synthetic responses only · no live FTW activity</div>
  <FTWilliamsAgentSettingsPage />
</AppShell>);
