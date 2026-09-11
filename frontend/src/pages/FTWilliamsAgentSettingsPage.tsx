import {
  CheckCircle2,
  CircleAlert,
  Clipboard,
  Laptop,
  Link2,
  LoaderCircle,
  MonitorCog,
  RefreshCw,
  ShieldCheck,
  Unplug,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  createFTWLocalAgentPairingCode,
  getFTWLocalAgentStatus,
  listFTWLocalAgentDevices,
  revokeFTWLocalAgentDevice,
} from "../api";
import type { FTWLocalAgentDevice, FTWLocalAgentPairingCodeResponse, FTWLocalAgentStatus } from "../types";
import { InlineLoader, Skeleton } from "../ui/Loading";

type LoadState = "loading" | "ready" | "error";

export function FTWilliamsAgentSettingsPage() {
  const [status, setStatus] = useState<FTWLocalAgentStatus | null>(null);
  const [devices, setDevices] = useState<FTWLocalAgentDevice[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [message, setMessage] = useState("");
  const [pairing, setPairing] = useState<FTWLocalAgentPairingCodeResponse | null>(null);
  const [creatingCode, setCreatingCode] = useState(false);
  const [revokingId, setRevokingId] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = async (quiet = false) => {
    if (!quiet) setState("loading");
    setMessage("");
    try {
      const [nextStatus, nextDevices] = await Promise.all([
        getFTWLocalAgentStatus(),
        listFTWLocalAgentDevices(),
      ]);
      setStatus(nextStatus);
      setDevices(nextDevices.filter((device) => !device.revoked_at));
      setState("ready");
    } catch (error) {
      setState("error");
      setMessage(errorMessage(error, "Only ERISAPros administrators can manage the FT Williams Agent."));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const pairingExpired = useMemo(() => (
    pairing ? new Date(pairing.expires_at).getTime() <= Date.now() : false
  ), [pairing]);

  const createCode = async () => {
    setCreatingCode(true);
    setCopied(false);
    setMessage("");
    try {
      setPairing(await createFTWLocalAgentPairingCode());
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
            <div className="agent-status-meta">
              <span>Connected computers</span>
              <strong>{status?.device_count || 0}</strong>
            </div>
          </section>

          <section className="agent-setup-card card">
            <div className="agent-section-heading">
              <div>
                <span className="eyebrow">One-time setup</span>
                <h2>Connect this computer</h2>
                <p>Create a short-lived code, then enter it in the signed ERISAPros FT Williams Agent on the Windows computer that will run FT Williams.</p>
              </div>
              <button className="button" type="button" onClick={() => void createCode()} disabled={creatingCode || !status?.enabled}>
                {creatingCode ? <InlineLoader label="Creating code" /> : <><Link2 size={17} /> Connect this computer</>}
              </button>
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
              <li><span>1</span><div><strong>Install the signed agent</strong><small>Use the ERISAPros Agent installer supplied by your administrator on this Windows computer.</small></div></li>
              <li><span>2</span><div><strong>Enter the one-time code</strong><small>The agent saves its device token locally with Windows protection. Do not send the code by email or chat.</small></div></li>
              <li><span>3</span><div><strong>Sign in to FT Williams</strong><small>The agent opens a dedicated FT Williams browser. Complete any required MFA there; ERISAPros never receives the password or browser cookies.</small></div></li>
              <li><span>4</span><div><strong>Confirm Connected</strong><small>Return here and refresh. Plan mapping and automation are enabled separately after verification.</small></div></li>
            </ol>
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
                      <small>{device.expected_account} · Agent {device.agent_version || "version pending"}</small>
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
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "soon";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}
