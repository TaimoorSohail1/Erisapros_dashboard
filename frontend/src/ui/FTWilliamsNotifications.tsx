import { Activity, AlertTriangle, Bell, CheckCircle2, Eye, X, XCircle } from "lucide-react";
import { useMemo, useRef, useState, type RefObject } from "react";
import { Link } from "../router";
import { classifyFTWilliamsFailure, failureTypeLabels, ftwFailureTypeClass } from "../ftwFailures";
import type { FTWilliamsFailureQueueItem, FTWilliamsHistoryItem } from "../types";
import { formatFilingDisplayName } from "../utils";
import { InlineLoader } from "./Loading";
import { useDialogFocus } from "./useDialogFocus";
import { refreshFTWilliamsFailures, useFTWilliamsFailures, useFTWilliamsHistory } from "./ftWilliamsNotificationStore";

type FTWPanelTab = "failures" | "activity";

export function FTWilliamsNotifications() {
  const [activeTab, setActiveTab] = useState<FTWPanelTab>("failures");
  const [isOpen, setIsOpen] = useState(false);
  const failuresState = useFTWilliamsFailures();
  const historyState = useFTWilliamsHistory(isOpen && activeTab === "activity");
  const failures = failuresState.data;
  const history = historyState.data;
  const failureMessage = failuresState.error;
  const historyMessage = historyState.error;
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const warningCount = useMemo(() => history.filter((item) => item.status === "warning").length, [history]);
  const issueCount = failures.length + warningCount;

  return (
    <>
      <button
        ref={triggerRef}
        className="ftw-notification-trigger topbar-ftw-notification"
        type="button"
        onClick={() => {
          setIsOpen(true);
          void refreshFTWilliamsFailures().catch(() => undefined);
        }}
        aria-expanded={isOpen}
        aria-label="Open FT Williams notifications"
      >
        <Bell size={18} />
        <span>FT Williams</span>
        {issueCount ? <strong title={`${failures.length} failures, ${warningCount} warnings`}>{issueCount}</strong> : <em>{history.length}</em>}
      </button>
      {isOpen ? <FTWilliamsSidePanel
        activeTab={activeTab}
        failureMessage={failureMessage}
        failures={failures}
        failuresLoading={failuresState.loading}
        failuresUpdatedAt={failuresState.updatedAt}
        history={history}
        historyLoading={historyState.loading}
        historyMessage={historyMessage}
        historyUpdatedAt={historyState.updatedAt}
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        onTabChange={setActiveTab}
        returnFocusRef={triggerRef}
      /> : null}
    </>
  );
}

function FTWilliamsSidePanel({
  activeTab,
  failureMessage,
  failures,
  failuresLoading,
  failuresUpdatedAt,
  history,
  historyLoading,
  historyMessage,
  historyUpdatedAt,
  isOpen,
  onClose,
  onTabChange,
  returnFocusRef,
}: {
  activeTab: FTWPanelTab;
  failureMessage: string;
  failures: FTWilliamsFailureQueueItem[];
  failuresLoading: boolean;
  failuresUpdatedAt: number;
  history: FTWilliamsHistoryItem[];
  historyLoading: boolean;
  historyMessage: string;
  historyUpdatedAt: number;
  isOpen: boolean;
  onClose: () => void;
  onTabChange: (tab: FTWPanelTab) => void;
  returnFocusRef: RefObject<HTMLElement | null>;
}) {
  const previewFailures = failures.slice(0, 3);
  const previewHistory = history.slice(0, 5);
  const failuresInitialLoad = failuresLoading && !failuresUpdatedAt;
  const historyInitialLoad = historyLoading && !historyUpdatedAt;
  const panelRef = useRef<HTMLElement | null>(null);
  useDialogFocus(isOpen, panelRef, onClose, returnFocusRef);
  return (
    <aside ref={panelRef} tabIndex={-1} className={`ftw-side-panel ftw-notification-drawer card ${isOpen ? "open" : ""}`} role="dialog" aria-modal="true" aria-label="FT Williams notifications">
      <div className="ftw-side-head">
        <div>
          <h2>FT Williams Notifications</h2>
          <p>Failures and latest FTW actions from this dashboard.</p>
        </div>
        <button className="ftw-drawer-close" type="button" onClick={onClose} aria-label="Close FT Williams notifications">
          <X size={18} />
        </button>
      </div>

      <div className="ftw-side-tabs">
        <button className={activeTab === "failures" ? "active" : ""} type="button" onClick={() => onTabChange("failures")}>
          Failures <span>{failures.length}</span>
        </button>
        <button className={activeTab === "activity" ? "active" : ""} type="button" onClick={() => onTabChange("activity")}>
          Activity <span>{history.length}</span>
        </button>
      </div>

      {activeTab === "failures" ? (
        <div className="ftw-side-content" aria-busy={failuresLoading}>
          <div className="ftw-side-scroll">
            {failureMessage ? <div className="ftw-side-message">{failureMessage}</div> : null}
            {failuresInitialLoad ? (
              <FTWilliamsDrawerLoading label="Loading active failures" />
            ) : previewFailures.length ? (
              <div className="ftw-side-list">
                {previewFailures.map((item) => (
                  <FTWilliamsFailureCard item={item} key={`${item.filing_id}-${item.failed_at}`} />
                ))}
              </div>
            ) : !failureMessage ? (
              <div className="ftw-side-empty">
                <CheckCircle2 size={22} />
                <strong>No FT Williams issues right now</strong>
                <small>Failed sends will appear here automatically.</small>
              </div>
            ) : null}
          </div>
          <div className="ftw-side-footer">
            {failures.length > previewFailures.length ? (
              <p className="ftw-side-more-count">+{failures.length - previewFailures.length} more active failures</p>
            ) : failuresLoading && failuresUpdatedAt ? <InlineLoader label="Refreshing" /> : null}
            <Link className="button secondary ftw-side-footer-action" to="/ftwilliams/failures">
              View all failures <Eye size={15} />
            </Link>
          </div>
        </div>
      ) : (
        <div className="ftw-side-content" aria-busy={historyLoading}>
          <div className="ftw-side-scroll">
            {historyMessage ? <div className="ftw-side-message">{historyMessage}</div> : null}
            {historyInitialLoad ? (
              <FTWilliamsDrawerLoading label="Loading recent activity" />
            ) : previewHistory.length ? (
              <div className="ftw-side-list ftw-side-activity-list">
                {previewHistory.map((item) => (
                  <FTWilliamsActivityItem item={item} key={item.id || `${item.filing_id}-${item.created_at}-${item.action}`} />
                ))}
              </div>
            ) : !historyMessage ? (
              <div className="ftw-side-empty">
                <Activity size={22} />
                <strong>No recent FT Williams activity</strong>
                <small>Updates, previews, and current-data queries will appear here.</small>
              </div>
            ) : null}
          </div>
          <div className="ftw-side-footer">
            {historyLoading && historyUpdatedAt ? <InlineLoader label="Refreshing" /> : null}
            <Link className="button secondary ftw-side-footer-action" to="/ftwilliams/activity">
              View all activity <Eye size={15} />
            </Link>
          </div>
        </div>
      )}
    </aside>
  );
}

function FTWilliamsFailureCard({ item }: { item: FTWilliamsFailureQueueItem }) {
  const displayName = formatFilingDisplayName(item.filing_name);
  const planIdentity = item.company_employer_id && item.plan_number
    ? `${item.company_employer_id} / ${item.plan_number}`
    : item.ftw_customer_id && item.ftw_plan_id
      ? `FTW ${item.ftw_customer_id} / ${item.ftw_plan_id}`
      : "Plan pending";
  const failureType = classifyFTWilliamsFailure(item);
  return (
    <article className="ftw-side-failure-card">
      <div className="ftw-side-card-top">
        <AlertTriangle size={17} />
        <div>
          <strong>{displayName}</strong>
          <small>{planIdentity}{item.year ? ` / Plan year ${item.year}` : ""}</small>
        </div>
        <time>{shortDate(item.failed_at)}<small>{shortTime(item.failed_at)}</small></time>
      </div>
      <span className={`ftw-side-status type-${ftwFailureTypeClass(failureType)}`}>{failureTypeLabels[failureType]}</span>
      <p title={plainFailureReason(item.failure_reason)}>{plainFailureReason(item.failure_reason)}</p>
      {item.error_code ? <small className="ftw-side-error-code">{item.error_code}</small> : null}
      <div className="ftw-side-card-bottom">
        <span>{item.attempted_field_count} fields attempted</span>
        <Link className="button danger" to={`/filings/${item.filing_id}`}>
          Review / Retry <Eye size={14} />
        </Link>
      </div>
    </article>
  );
}

function FTWilliamsDrawerLoading({ label }: { label: string }) {
  return (
    <div className="ftw-side-loading" role="status" aria-live="polite">
      <InlineLoader label={label} />
      <span className="ftw-side-loading-line" />
      <span className="ftw-side-loading-line short" />
    </div>
  );
}

function FTWilliamsActivityItem({ item }: { item: FTWilliamsHistoryItem }) {
  const displayName = formatFilingDisplayName(item.filing_name);
  const planIdentity = item.company_employer_id && item.plan_number
    ? `${item.company_employer_id} / ${item.plan_number}`
    : item.ftw_customer_id && item.ftw_plan_id
      ? `FTW ${item.ftw_customer_id} / ${item.ftw_plan_id}`
      : "Plan pending";
  return (
    <Link className="ftw-side-activity-item" to={`/filings/${item.filing_id}`}>
      <span className={`ftw-side-activity-icon status-${item.status}`}>
        {item.status === "failed" ? <XCircle size={16} /> : item.status === "warning" ? <AlertTriangle size={16} /> : item.status === "success" ? <CheckCircle2 size={16} /> : <Activity size={16} />}
      </span>
      <div>
        <strong>{item.action_label}</strong>
        <small>{displayName} / {planIdentity}</small>
        {typeof item.updated_field_count === "number" ? <em>{item.updated_field_count} fields {item.action.includes("UPDATE") ? "updated" : "analyzed"}</em> : null}
      </div>
      <time>{shortDate(item.created_at)}<small>{shortTime(item.created_at)}</small></time>
    </Link>
  );
}

function plainFailureReason(reason: string) {
  if (/invalid field reg/i.test(reason)) return "One or more generated XML tags are not accepted by ftwLink for this update.";
  if (/InsCarrierNAICCode/i.test(reason)) return "Carrier NAIC needs a numeric value before FT Williams can accept this Schedule A.";
  return reason;
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}

function shortTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));
}
