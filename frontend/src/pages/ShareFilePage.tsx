import { FolderSync } from "lucide-react";
import { useEffect, useState } from "react";
import { getShareFileAuthorizationUrl, getShareFileStatus, syncShareFileFolder } from "../api";
import { InlineLoader, Skeleton } from "../ui/Loading";

type ShareFileStatusView = Awaited<ReturnType<typeof getShareFileStatus>>;

export function ShareFilePage() {
  const [status, setStatus] = useState<ShareFileStatusView | null>(null);
  const [message, setMessage] = useState("");
  const [syncResult, setSyncResult] = useState<{
    message: string;
    roots?: Array<{ id: string; name: string; source: string; path: string }>;
    errors?: Array<{ folder_id: string; path: string; status_code: number; response: string }>;
  } | null>(null);
  const [authorizationUrl, setAuthorizationUrl] = useState("");
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    getShareFileStatus().then((nextStatus) => {
      setStatus(nextStatus);
      if (!nextStatus.connected) {
        getShareFileAuthorizationUrl()
          .then((payload) => setAuthorizationUrl(payload.authorization_url || ""))
          .catch(() => setAuthorizationUrl(""));
      }
    }).catch((error) => setMessage(error.message));
  }, []);

  const handleSync = async () => {
    setMessage("");
    setSyncResult(null);
    setSyncing(true);
    try {
      const result = await syncShareFileFolder();
      setSyncResult(
        {
          message: result.queued
            ? "ShareFile scan started. New filings will appear automatically when background processing finishes."
            : result.message,
          roots: result.scan_roots,
          errors: result.scan_errors,
        }
      );
      const refreshed = await getShareFileStatus();
      setStatus(refreshed);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "ShareFile sync failed.");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">ShareFile Intake</h1>
          <div className="subtle">Live intake scans shared client folders, classifies filing documents, and queues extraction for Schedule A PDFs and 5500 Plan Worksheets.</div>
        </div>
      </div>
      {message ? <div className="card card-pad">{message}</div> : null}
      <div className="card card-pad">
        <h2 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: 10 }}><FolderSync size={22} /> Connector Status</h2>
        {!status ? <ShareFileStatusSkeleton /> : (
          <>
            <p style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <span className={status.configured ? "badge ready" : "badge warn"}>{status.configured ? "Configured" : "Not configured"}</span>
              <span className={status.connected ? "badge ready" : "badge warn"}>{status.connected ? "OAuth connected" : "OAuth needed"}</span>
            </p>
            <p>{status.message}</p>
            <dl>
              <dt className="subtle">Subdomain</dt>
              <dd>{status.subdomain || "-"}</dd>
              <dt className="subtle">Scan scope</dt>
              <dd>{status.scan_scope || "-"}</dd>
              <dt className="subtle">Shared folder discovery</dt>
              <dd>{status.discover_shared_folders ? "Enabled" : "Disabled"}</dd>
              <dt className="subtle">Configured fallback folder IDs</dt>
              <dd>{status.configured_folder_ids?.length ? status.configured_folder_ids.join(", ") : "-"}</dd>
              <dt className="subtle">Configured shared root folder ID</dt>
              <dd>{status.shared_root_folder_id || "-"}</dd>
            </dl>
            <button className="button" disabled={!status.configured || !status.connected || syncing} onClick={handleSync}>
              {syncing ? <InlineLoader label="Syncing ShareFile" /> : <><FolderSync size={18} /> Sync ShareFile</>}
            </button>
            {!status.connected && authorizationUrl ? (
              <p>
                <a className="button secondary" href={authorizationUrl} target="_blank" rel="noreferrer">
                  Connect ShareFile OAuth
                </a>
              </p>
            ) : null}
            {syncResult ? (
              <div className="subtle" style={{ marginTop: 18 }}>
                <p>{syncResult.message}</p>
                {syncResult.roots?.length ? (
                  <div>
                    <strong>Scan roots:</strong>
                    <ul>
                      {syncResult.roots.slice(0, 12).map((root) => (
                        <li key={root.id}>{root.path}</li>
                      ))}
                    </ul>
                    {syncResult.roots.length > 12 ? <p>+ {syncResult.roots.length - 12} more roots.</p> : null}
                  </div>
                ) : null}
                {syncResult.errors?.length ? (
                  <div>
                    <strong>Folders skipped because ShareFile denied access:</strong>
                    <ul>
                      {syncResult.errors.slice(0, 6).map((error) => (
                        <li key={`${error.folder_id}-${error.status_code}`}>{error.path} ({error.status_code})</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </div>
    </>
  );
}

function ShareFileStatusSkeleton() {
  return (
    <div className="sharefile-status-skeleton" role="status" aria-live="polite" aria-label="Loading ShareFile connector status">
      <div className="sharefile-skeleton-badges"><Skeleton className="skeleton-pill" /><Skeleton className="skeleton-pill" /></div>
      <Skeleton className="skeleton-line skeleton-line-wide" />
      <div className="sharefile-skeleton-details">
        {Array.from({ length: 5 }, (_, index) => (
          <div key={index}><Skeleton className="skeleton-line skeleton-line-short" /><Skeleton className="skeleton-line skeleton-line-medium" /></div>
        ))}
      </div>
      <Skeleton className="skeleton-button skeleton-button-wide" />
    </div>
  );
}
