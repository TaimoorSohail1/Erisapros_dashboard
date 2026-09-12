import {
  BookOpen,
  CheckCircle2,
  CircleAlert,
  Clipboard,
  Download,
  Laptop,
  Link2,
  LoaderCircle,
  MonitorCog,
  Plus,
  RefreshCw,
  ShieldCheck,
  Unplug,
  Wifi,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import {
  createFTWLocalAgentPairingCode,
  getFTWLocalAgentStatus,
  listFTWClientWorkspaces,
  listFTWLocalAgentDevices,
  listFTWWorkspacePlanMappings,
  revokeFTWLocalAgentDevice,
  disableFTWWorkspacePlanMapping,
  verifyFTWWorkspacePlanMapping,
} from "../api";
import type {
  FTWClientWorkspace,
  FTWLocalAgentDevice,
  FTWLocalAgentPairingCodeResponse,
  FTWLocalAgentStatus,
  FTWWorkspacePlanMapping,
  FTWWorkspacePlanMappingInput,
} from "../types";
import { InlineLoader, Skeleton } from "../ui/Loading";

type LoadState = "loading" | "ready" | "error";

const FTW_AGENT_DOWNLOAD_URL = "https://github.com/TaimoorSohail1/Erisapros_dashboard/releases/latest/download/ERISAProsFTWAgentSetup.exe";

const emptyMapping: FTWWorkspacePlanMappingInput = {
  company_employer_id: "",
  plan_number: "",
  year: "",
  plan_name: "",
  ftw_customer_id: "",
  ftw_plan_id: "",
  ftw_browser_customer_id: "",
  ftw_browser_plan_id: "",
  verification_evidence: "",
};

export function FTWilliamsAgentSettingsPage() {
  const [status, setStatus] = useState<FTWLocalAgentStatus | null>(null);
  const [devices, setDevices] = useState<FTWLocalAgentDevice[]>([]);
  const [workspaces, setWorkspaces] = useState<FTWClientWorkspace[]>([]);
  const [mappings, setMappings] = useState<FTWWorkspacePlanMapping[]>([]);
  const [mappingInput, setMappingInput] = useState<FTWWorkspacePlanMappingInput>(emptyMapping);
  const [mappingBusy, setMappingBusy] = useState(false);
  const [workspaceId, setWorkspaceId] = useState("");
  const [state, setState] = useState<LoadState>("loading");
  const [message, setMessage] = useState("");
  const [pairing, setPairing] = useState<FTWLocalAgentPairingCodeResponse | null>(null);
  const [creatingCode, setCreatingCode] = useState(false);
  const [revokingId, setRevokingId] = useState("");
  const [copied, setCopied] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionResult, setConnectionResult] = useState<{ tone: "success" | "attention"; message: string } | null>(null);

  const refresh = async (quiet = false) => {
    if (!quiet) setState("loading");
    setMessage("");
    try {
      const [nextStatus, nextDevices, nextWorkspaces] = await Promise.all([
        getFTWLocalAgentStatus(),
        listFTWLocalAgentDevices(),
        listFTWClientWorkspaces(),
      ]);
      setStatus(nextStatus);
      setDevices(nextDevices.filter((device) => !device.revoked_at));
      setWorkspaces(nextWorkspaces.filter((workspace) => workspace.enabled));
      setWorkspaceId((current) => (
        nextWorkspaces.some((workspace) => workspace.id === current)
          ? current
          : nextWorkspaces.find((workspace) => workspace.enabled)?.id || ""
      ));
      setState("ready");
    } catch (error) {
      setState("error");
      setMessage(errorMessage(error, "Only ERISAPros administrators can manage the FT Williams Agent."));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (!workspaceId) {
      setMappings([]);
      return;
    }
    void listFTWWorkspacePlanMappings(workspaceId)
      .then(setMappings)
      .catch((error) => setMessage(errorMessage(error, "Verified plan mappings could not be loaded.")));
  }, [workspaceId]);

  const pairingExpired = useMemo(() => (
    pairing ? parseApiDateTime(pairing.expires_at).getTime() <= Date.now() : false
  ), [pairing]);

  const createCode = async () => {
    setCreatingCode(true);
    setCopied(false);
    setMessage("");
    try {
      setPairing(await createFTWLocalAgentPairingCode(workspaceId || undefined));
      await refresh(true);
    } catch (error) {
      setMessage(errorMessage(error, "The one-time connection code could not be created."));
    } finally {
      setCreatingCode(false);
    }
  };

  const copyCode = async () => {
    if (!pairing || pairingExpired) return;
    try {
      await navigator.clipboard.writeText(pairing.pairing_code);
      setCopied(true);
    } catch {
      setMessage("Copy the code manually. Your browser did not allow clipboard access.");
    }
  };

  const testAgentConnection = async () => {
    setTestingConnection(true);
    setConnectionResult(null);
    try {
      const [nextStatus, nextDevices] = await Promise.all([
        getFTWLocalAgentStatus(),
        listFTWLocalAgentDevices(),
      ]);
      const activeDevices = nextDevices.filter((device) => (
        !device.revoked_at && (!workspaceId || device.workspace_id === workspaceId)
      ));
      const readyDevice = activeDevices.find((device) => device.status === "CONNECTED" && device.browser_ready);
      const loginDevice = activeDevices.find((device) => device.status === "LOGIN_REQUIRED");
      setStatus(nextStatus);
      setDevices(nextDevices.filter((device) => !device.revoked_at));
      if (readyDevice) {
        setConnectionResult({
          tone: "success",
          message: `Connection test passed. ${readyDevice.name} is connected and FT Williams is ready.`,
        });
      } else if (loginDevice) {
        setConnectionResult({
          tone: "attention",
          message: `${loginDevice.name} is connected, but FT Williams needs login. Sign in there and test again; waiting work will resume automatically.`,
        });
      } else {
        setConnectionResult({
          tone: "attention",
          message: "No active computer was found for this workspace. Start the FT Williams Agent, then test again.",
        });
      }
    } catch (error) {
      setConnectionResult({
        tone: "attention",
        message: errorMessage(error, "The connection test could not be completed."),
      });
    } finally {
      setTestingConnection(false);
    }
  };

  const revokeDevice = async (device: FTWLocalAgentDevice) => {
    if (!window.confirm(`Disconnect ${device.name}? Its saved agent token will stop working immediately.`)) return;
    setRevokingId(device.id);
    setMessage("");
    try {
      await revokeFTWLocalAgentDevice(device.id);
      setDevices((current) => current.filter((item) => item.id !== device.id));
      await refresh(true);
    } catch (error) {
      setMessage(errorMessage(error, "This computer could not be disconnected."));
    } finally {
      setRevokingId("");
    }
  };

  const saveMapping = async (event: FormEvent) => {
    event.preventDefault();
    if (!workspaceId) return;
    setMappingBusy(true);
    setMessage("");
    try {
      await verifyFTWWorkspacePlanMapping(workspaceId, mappingInput);
      setMappings(await listFTWWorkspacePlanMappings(workspaceId));
      setMappingInput(emptyMapping);
    } catch (error) {
      setMessage(errorMessage(error, "The plan mapping could not be verified."));
    } finally {
      setMappingBusy(false);
    }
  };

  const disableMapping = async (mapping: FTWWorkspacePlanMapping) => {
    if (!window.confirm(`Disable the verified mapping for ${mapping.plan_name} (${mapping.year})?`)) return;
    setMappingBusy(true);
    setMessage("");
    try {
      await disableFTWWorkspacePlanMapping(mapping.workspace_id, mapping.id);
      setMappings(await listFTWWorkspacePlanMappings(mapping.workspace_id));
    } catch (error) {
      setMessage(errorMessage(error, "The plan mapping could not be disabled."));
    } finally {
      setMappingBusy(false);
    }
  };

  const setMappingField = (field: keyof FTWWorkspacePlanMappingInput, value: string) => {
    setMappingInput((current) => ({ ...current, [field]: value }));
  };

  return (
    <div className="agent-settings-page">
      <header className="agent-settings-hero">
        <div>
          <span className="eyebrow"><MonitorCog size={15} /> Connection settings</span>
          <h1 className="page-title">FT Williams Agent</h1>
          <p>Connect a trusted Windows computer so ERISAPros can safely use its local FT Williams browser session for verified Bring Forward work.</p>
        </div>
        <button className="button secondary" type="button" onClick={() => void refresh()} disabled={state === "loading"}>
          {state === "loading" ? <InlineLoader label="Refreshing" /> : <><RefreshCw size={16} /> Refresh</>}
        </button>
      </header>

      {message ? <div className="agent-settings-message" role="alert"><CircleAlert size={18} /> {message}</div> : null}

      {state === "loading" && !status ? <AgentSettingsSkeleton /> : null}

      {state !== "loading" || status ? (
        <>
          <section className="agent-status-card card" aria-label="FT Williams Agent status">
            <div className={`agent-status-icon ${status?.connected ? "connected" : status?.status === "LOGIN_REQUIRED" ? "attention" : "offline"}`}>
              {status?.connected ? <CheckCircle2 size={22} /> : <Laptop size={22} />}
            </div>
            <div>
              <span className="eyebrow">Connection status</span>
              <h2>{status?.connected ? "Connected and ready" : status?.status === "LOGIN_REQUIRED" ? "FT Williams login needed" : "No connected computer"}</h2>
              <p>{agentStatusMessage(status)}</p>
            </div>
            <div className="agent-status-controls">
              <div className="agent-status-meta">
                <span>Connected computers</span>
                <strong>{status?.device_count || 0}</strong>
              </div>
              <button className="button" type="button" onClick={() => void testAgentConnection()} disabled={testingConnection || !status?.enabled}>
                {testingConnection ? <InlineLoader label="Testing" /> : <><Wifi size={16} /> Test connection</>}
              </button>
            </div>
          </section>

          {connectionResult ? (
            <div className={`agent-connection-result ${connectionResult.tone}`} role="status" aria-live="polite">
              {connectionResult.tone === "success" ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}
              {connectionResult.message}
            </div>
          ) : null}

          <section className="agent-setup-card card">
            <div className="agent-section-heading">
              <div>
                <span className="eyebrow">One-time setup</span>
                <h2>Connect this computer</h2>
                <p>Download the FT Williams Agent, then connect this trusted Windows computer with a short-lived code.</p>
                {workspaces.length ? (
                  <label className="agent-workspace-select">
                    <span>Client workspace</span>
                    <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} disabled={creatingCode}>
                      {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.expected_account}</option>)}
                    </select>
                    <small>The computer and its future jobs stay inside this client workspace.</small>
                  </label>
                ) : null}
              </div>
              <div className="agent-setup-actions">
                <a className="button secondary" href="/ftw-agent-setup-guide.html" target="_blank" rel="noreferrer">
                  <BookOpen size={16} /> Open setup guide
                </a>
                <a className="button secondary" href={FTW_AGENT_DOWNLOAD_URL}>
                  <Download size={16} /> Download FTW Agent
                </a>
                <button className="button" type="button" onClick={() => void createCode()} disabled={creatingCode || !status?.enabled}>
                  {creatingCode ? <InlineLoader label="Creating code" /> : <><Link2 size={17} /> Connect this computer</>}
                </button>
              </div>
            </div>

            {!status?.enabled ? (
              <div className="agent-inline-warning"><CircleAlert size={17} /> The local agent is not enabled for this environment. Pairing is unavailable until an administrator enables the controlled rollout.</div>
            ) : null}

            {pairing ? (
              <div className="agent-pairing-code" aria-live="polite">
                <div>
                  <span>One-time connection code</span>
                  <strong>{pairingExpired ? "Expired" : pairing.pairing_code}</strong>
                  <small>{pairingExpired ? "Create a new code to continue." : `Expires ${formatDateTime(pairing.expires_at)}. This code can be used once.`}</small>
                </div>
                <button className="button secondary" type="button" onClick={() => void copyCode()} disabled={pairingExpired}>
                  <Clipboard size={16} /> {copied ? "Copied" : "Copy code"}
                </button>
              </div>
            ) : null}

            <ol className="agent-setup-steps">
              <li><span>1</span><div><strong>Download and open the agent</strong><small>Use the download above, then double-click the setup file. Windows may show “Unknown publisher” for this pilot build.</small></div></li>
              <li><span>2</span><div><strong>Enter the one-time code</strong><small>The agent saves its device token locally with Windows protection. Do not send the code by email or chat.</small></div></li>
              <li><span>3</span><div><strong>Sign in to FT Williams</strong><small>The agent opens a dedicated FT Williams browser. Complete any required MFA there; ERISAPros never receives the password or browser cookies.</small></div></li>
              <li><span>4</span><div><strong>Confirm Connected</strong><small>Return here and refresh. Plan mapping and automation are enabled separately after verification.</small></div></li>
            </ol>
          </section>

          <section className="agent-devices-card card">
            <div className="agent-section-heading">
              <div>
                <span className="eyebrow">Verified routing</span>
                <h2>Plan mappings</h2>
                <p>Only a verified client, plan, year, and FT Williams ID set can receive an automated Bring Forward job.</p>
              </div>
            </div>
            {mappings.length ? (
              <div className="agent-device-list">
                {mappings.map((mapping) => (
                  <article className="agent-device-row" key={mapping.id}>
                    <span className={`agent-device-state ${mapping.status === "VERIFIED" ? "connected" : "offline"}`}><ShieldCheck size={18} /></span>
                    <div className="agent-device-name">
                      <strong>{mapping.plan_name}</strong>
                      <small>EIN {mapping.company_employer_id} · Plan {mapping.plan_number} · {mapping.year}</small>
                    </div>
                    <div className="agent-device-detail">
                      <span className={`agent-device-badge ${mapping.status === "VERIFIED" ? "connected" : "offline"}`}>{mapping.status.replaceAll("_", " ")}</span>
                      <small>Verified {formatDateTime(mapping.verified_at)}</small>
                    </div>
                    {mapping.status === "VERIFIED" ? <button className="button danger agent-revoke-button" type="button" disabled={mappingBusy} onClick={() => void disableMapping(mapping)}>Disable</button> : null}
                  </article>
                ))}
              </div>
            ) : <div className="agent-empty-state"><ShieldCheck size={22} /><strong>No verified plan mappings</strong><span>Bring Forward stays blocked until the first plan is checked and recorded below.</span></div>}

            {workspaceId ? (
              <details className="agent-mapping-form-wrap">
                <summary><Plus size={15} /> Add verified plan mapping</summary>
                <form className="agent-mapping-form" onSubmit={(event) => void saveMapping(event)}>
                  <label>Plan name<input required value={mappingInput.plan_name} onChange={(event) => setMappingField("plan_name", event.target.value)} /></label>
                  <label>EIN<input required value={mappingInput.company_employer_id} onChange={(event) => setMappingField("company_employer_id", event.target.value)} /></label>
                  <label>Plan number<input required value={mappingInput.plan_number} onChange={(event) => setMappingField("plan_number", event.target.value)} /></label>
                  <label>Plan year<input required inputMode="numeric" maxLength={4} value={mappingInput.year} onChange={(event) => setMappingField("year", event.target.value)} /></label>
                  <label>ftwLink customer ID<input required value={mappingInput.ftw_customer_id} onChange={(event) => setMappingField("ftw_customer_id", event.target.value)} /></label>
                  <label>ftwLink plan ID<input required value={mappingInput.ftw_plan_id} onChange={(event) => setMappingField("ftw_plan_id", event.target.value)} /></label>
                  <label>Browser customer ID<input required value={mappingInput.ftw_browser_customer_id} onChange={(event) => setMappingField("ftw_browser_customer_id", event.target.value)} /></label>
                  <label>Browser plan ID<input required value={mappingInput.ftw_browser_plan_id} onChange={(event) => setMappingField("ftw_browser_plan_id", event.target.value)} /></label>
                  <label className="agent-mapping-evidence">Verification evidence<input required placeholder="Example: checked in Highland demo on Sep 11" value={mappingInput.verification_evidence} onChange={(event) => setMappingField("verification_evidence", event.target.value)} /></label>
                  <button className="button" type="submit" disabled={mappingBusy}>{mappingBusy ? <InlineLoader label="Saving" /> : "Save verified mapping"}</button>
                </form>
              </details>
            ) : null}
          </section>

          <section className="agent-devices-card card">
            <div className="agent-section-heading">
              <div>
                <span className="eyebrow">Trusted computers</span>
                <h2>Connected devices</h2>
                <p>Disconnect a computer immediately if it is lost, replaced, or should no longer run FT Williams.</p>
              </div>
            </div>
            {devices.length ? (
              <div className="agent-device-list">
                {devices.map((device) => (
                  <article className="agent-device-row" key={device.id}>
                    <span className={`agent-device-state ${deviceStateClass(device)}`}><Laptop size={18} /></span>
                    <div className="agent-device-name">
                      <strong>{device.name}</strong>
                      <small>{workspaceName(device.workspace_id, workspaces) || device.expected_account} · Agent {device.agent_version || "version pending"}</small>
                    </div>
                    <div className="agent-device-detail">
                      <span className={`agent-device-badge ${deviceStateClass(device)}`}>{deviceStateLabel(device)}</span>
                      <small>{device.last_seen_at ? `Last seen ${formatDateTime(device.last_seen_at)}` : "Waiting for first connection"}</small>
                      {device.last_error ? <em>{device.last_error}</em> : null}
                    </div>
                    <button className="button danger agent-revoke-button" type="button" disabled={revokingId === device.id} onClick={() => void revokeDevice(device)}>
                      {revokingId === device.id ? <InlineLoader label="Disconnecting" /> : <><Unplug size={15} /> Disconnect</>}
                    </button>
                  </article>
                ))}
              </div>
            ) : <div className="agent-empty-state"><ShieldCheck size={22} /><strong>No computer is connected yet</strong><span>Create a one-time code to connect the first trusted Windows computer.</span></div>}
          </section>

          <aside className="agent-safety-note">
            <ShieldCheck size={19} />
            <div><strong>Safety stays on.</strong> Connecting a computer does not enable automatic updates or sending. ERISAPros verifies the client, plan, year, and Schedule A first; uncertain work remains Action Needed.</div>
          </aside>
        </>
      ) : null}
    </div>
  );
}

function AgentSettingsSkeleton() {
  return <div className="agent-settings-skeleton" role="status" aria-live="polite" aria-label="Loading FT Williams Agent settings">
    <Skeleton className="agent-skeleton-status" />
    <Skeleton className="agent-skeleton-card" />
    <Skeleton className="agent-skeleton-card" />
  </div>;
}

function agentStatusMessage(status: FTWLocalAgentStatus | null) {
  if (!status?.enabled) return "The local agent rollout is currently disabled for this environment.";
  if (status.connected) return `${status.device_name || "A trusted computer"} is signed in and ready for verified FT Williams work.`;
  if (status.status === "LOGIN_REQUIRED") return "Open the ERISAPros Agent on the connected computer and sign in to FT Williams again.";
  return "Connect a trusted Windows computer, then sign in to FT Williams once in its dedicated browser.";
}

function deviceStateClass(device: FTWLocalAgentDevice) {
  if (device.status === "CONNECTED" && device.browser_ready) return "connected";
  if (device.status === "LOGIN_REQUIRED") return "attention";
  return "offline";
}

function deviceStateLabel(device: FTWLocalAgentDevice) {
  if (device.status === "CONNECTED" && device.browser_ready) return "Connected";
  if (device.status === "LOGIN_REQUIRED") return "Login needed";
  return "Offline";
}

function formatDateTime(value: string) {
  const date = parseApiDateTime(value);
  if (Number.isNaN(date.getTime())) return "soon";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

function parseApiDateTime(value: string) {
  // The API currently serializes UTC datetimes without a timezone suffix.
  // Treat those values as UTC so client timezone differences cannot expire a
  // valid pairing code early or display an incorrect last-seen time.
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

function workspaceName(workspaceId: string | null | undefined, workspaces: FTWClientWorkspace[]) {
  const workspace = workspaces.find((item) => item.id === workspaceId);
  return workspace ? `${workspace.name} · ${workspace.expected_account}` : "";
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}
