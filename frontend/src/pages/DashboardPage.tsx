import {
  AlertTriangle,
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  Eye,
  FileText,
  HelpCircle,
  Search,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { deleteFiling, listFilings, listFTWilliamsFailureQueue } from "../api";
import type { Filing, FilingStatus, FTWilliamsFailureQueueItem, ScheduleAContractType } from "../types";
import { StatusBadge } from "../ui/StatusBadge";
import { formatFilingDisplayName, percent } from "../utils";

type StatusFilter = "ALL" | FilingStatus;
type DateFilter = "ALL" | "TODAY" | "LAST_7" | "LAST_30";
type ContractTypeFilter = "ALL" | ScheduleAContractType;
type DashboardToast = {
  message: string;
  title: string;
  tone: "error" | "success";
} | null;
const DASHBOARD_REVIEW_FIELD_TOTAL = 61;
const DASHBOARD_POLL_MS = 30000;

export function DashboardPage() {
  const [filings, setFilings] = useState<Filing[]>([]);
  const [message, setMessage] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("ALL");
  const [dateFilter, setDateFilter] = useState<DateFilter>("ALL");
  const [contractTypeFilter, setContractTypeFilter] = useState<ContractTypeFilter>("ALL");
  const [ftwFailures, setFtwFailures] = useState<FTWilliamsFailureQueueItem[]>([]);
  const [rowsLimit, setRowsLimit] = useState(25);
  const [currentPage, setCurrentPage] = useState(1);
  const [toast, setToast] = useState<DashboardToast>(null);
  const [deleteTarget, setDeleteTarget] = useState<Filing | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [deletingFilingId, setDeletingFilingId] = useState<string | null>(null);
  const previousFilingsRef = useRef<Filing[] | null>(null);

  useEffect(() => {
    let active = true;
    let requestInFlight = false;

    async function load({ announceChanges = false }: { announceChanges?: boolean } = {}) {
      if (requestInFlight) return;
      requestInFlight = true;
      try {
        const rows = await listFilings();
        if (!active) return;
        const shareFileRows = rows.filter((item) => item.intake_source !== "MANUAL");
        const toastMessage = announceChanges ? dashboardChangeToast(previousFilingsRef.current, shareFileRows) : null;
        previousFilingsRef.current = shareFileRows;
        setFilings(shareFileRows);
        if (toastMessage) setToast(toastMessage);
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : "Could not load filings");
      }

      try {
        const failureQueue = await listFTWilliamsFailureQueue();
        if (!active) return;
        setFtwFailures(failureQueue.items);
      } catch (error) {
        if (active) {
          setFtwFailures([]);
        }
      } finally {
        requestInFlight = false;
      }
    }

    load();
    const interval = window.setInterval(() => load({ announceChanges: true }), DASHBOARD_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const sortedFilings = useMemo(
    () => [...filings].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [filings],
  );
  const needsReview = filings.filter((item) => item.status === "NEEDS_REVIEW");
  const readyToSend = filings.filter((item) => item.status === "APPROVED");

  const filteredFilings = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return sortedFilings
      .filter((filing) => {
        const displayName = formatFilingDisplayName(filing.file_name);
        const haystack = [
          filing.file_name,
          displayName,
          filingClientName(filing),
          filingPlanIdentity(filing),
          xmlValue(filing.proposed_xml, "PlanName"),
        ].join(" ").toLowerCase();
        const matchesSearch = !needle || haystack.includes(needle);
        const matchesStatus = statusFilter === "ALL" || filing.status === statusFilter;
        const matchesDate = matchesDateFilter(filing.created_at, dateFilter);
        const matchesContractType = contractTypeFilter === "ALL" || (filing.schedule_a_contract_type || "UNKNOWN") === contractTypeFilter;
        return matchesSearch && matchesStatus && matchesDate && matchesContractType;
      });
  }, [contractTypeFilter, dateFilter, search, sortedFilings, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredFilings.length / rowsLimit));
  const pageStartIndex = filteredFilings.length ? (currentPage - 1) * rowsLimit : 0;
  const pageEndIndex = Math.min(pageStartIndex + rowsLimit, filteredFilings.length);
  const visibleFilings = filteredFilings.slice(pageStartIndex, pageEndIndex);

  useEffect(() => {
    setCurrentPage(1);
  }, [search, statusFilter, dateFilter, contractTypeFilter, rowsLimit]);

  useEffect(() => {
    setCurrentPage((page) => Math.min(page, totalPages));
  }, [totalPages]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 6500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function handleDeleteFiling() {
    if (!deleteTarget?.id) return;
    const target = deleteTarget;
    setDeletingFilingId(target.id);
    setDeleteError("");
    try {
      await deleteFiling(target.id);
      setFilings((current) => {
        const next = current.filter((filing) => filing.id !== target.id);
        previousFilingsRef.current = next;
        return next;
      });
      setFtwFailures((current) => current.filter((item) => item.filing_id !== target.id));
      setToast({
        tone: "success",
        title: "Filing removed",
        message: `${formatFilingDisplayName(target.file_name)} was removed from ERISAPros only. ShareFile was not changed.`,
      });
      setDeleteTarget(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not remove this filing from the dashboard.";
      setDeleteError(message);
      setToast({ tone: "error", title: "Delete failed", message });
    } finally {
      setDeletingFilingId(null);
    }
  }

  return (
    <div className="dashboard-page dashboard-v3 dashboard-ops">
      {toast ? <DashboardToastMessage toast={toast} onClose={() => setToast(null)} /> : null}
      {deleteTarget ? (
        <DeleteFilingModal
          error={deleteError}
          filing={deleteTarget}
          isDeleting={deletingFilingId === deleteTarget.id}
          onCancel={() => {
            if (deletingFilingId) return;
            setDeleteError("");
            setDeleteTarget(null);
          }}
          onConfirm={handleDeleteFiling}
        />
      ) : null}
      <section className="dashboard-kpi-grid">
        <DashboardKpi icon={<FileText size={24} />} value={filings.length} label="Total Filings" tone="info" note="Tracked packages" />
        <DashboardKpi icon={<HelpCircle size={24} />} value={needsReview.length} label="Needs Review" tone="warn" note={filings.length ? `${Math.round((needsReview.length / filings.length) * 100)}% of total` : "0% of total"} />
        <DashboardKpi icon={<CheckCircle2 size={24} />} value={readyToSend.length} label="Ready to Send" tone="ready" note={filings.length ? `${Math.round((readyToSend.length / filings.length) * 100)}% of total` : "0% of total"} />
        <DashboardKpi icon={<XCircle size={24} />} value={ftwFailures.length} label="FTW Failed" tone="danger" featured={ftwFailures.length > 0} note={ftwFailures.length ? "Needs attention" : "Clear"} />
      </section>

      {message ? <div className="dashboard-message card">{message}</div> : null}

      <div className="dashboard-workbench">
        <section className="dashboard-table-panel card">
          <div className="dashboard-table-head">
            <div>
              <h2>All Filings</h2>
              <p>{filings.length} ShareFile package{filings.length === 1 ? "" : "s"} tracked for review.</p>
            </div>
            <div className="dashboard-table-controls">
              <label className="dashboard-search">
                <Search size={17} />
                <span>Search</span>
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search filings, plan, EIN, client..." />
              </label>
              <FilterDropdown
                label="Status"
                value={statusFilter}
                options={[
                  { value: "ALL", label: "All statuses" },
                  { value: "NEEDS_REVIEW", label: "Needs review" },
                  { value: "READY_FOR_APPROVAL", label: "Ready" },
                  { value: "APPROVED", label: "Approved" },
                  { value: "FAILED", label: "Failed" },
                  { value: "REJECTED", label: "Rejected" },
                ]}
                onChange={(value) => setStatusFilter(value as StatusFilter)}
              />
              <FilterDropdown
                icon={<Calendar size={16} />}
                label="Date"
                value={dateFilter}
                options={[
                  { value: "ALL", label: "All time" },
                  { value: "TODAY", label: "Today" },
                  { value: "LAST_7", label: "Last 7 days" },
                  { value: "LAST_30", label: "Last 30 days" },
                ]}
                onChange={(value) => setDateFilter(value as DateFilter)}
              />
              <FilterDropdown
                label="Contract"
                value={contractTypeFilter}
                options={[
                  { value: "ALL", label: "All contract types" },
                  { value: "EXPERIENCE_RATED", label: "Experience rated" },
                  { value: "NONEXPERIENCE_RATED", label: "Nonexperience rated" },
                  { value: "NEEDS_REVIEW", label: "Needs review" },
                  { value: "UNKNOWN", label: "Unknown" },
                ]}
                onChange={(value) => setContractTypeFilter(value as ContractTypeFilter)}
              />
              <FilterDropdown
                label="Rows"
                value={String(rowsLimit)}
                options={[
                  { value: "10", label: "10" },
                  { value: "25", label: "25" },
                  { value: "50", label: "50" },
                ]}
                compact
                onChange={(value) => setRowsLimit(Number(value))}
              />
            </div>
          </div>

          <div className="dashboard-table-wrap">
            <table className="dashboard-filings-table">
              <thead>
                <tr>
                  <th>File / Client</th>
                  <th>Plan / EIN</th>
                  <th>Status</th>
                  <th>Coverage</th>
                  <th>Issues</th>
                  <th>Uploaded</th>
                  <th>Action</th>
                  <th className="more-col" />
                </tr>
              </thead>
              <tbody>
                {visibleFilings.map((filing) => (
                  <DashboardFilingRow key={filing.id} filing={filing} onDeleteRequest={setDeleteTarget} />
                ))}
              </tbody>
            </table>
          </div>

          {!visibleFilings.length ? (
            <div className="empty-state"><FileText size={18} /> No filings match this view.</div>
          ) : null}

          <div className="dashboard-table-footer">
            <span>
              Showing {filteredFilings.length ? pageStartIndex + 1 : 0}-{pageEndIndex} of {filteredFilings.length} filing{filteredFilings.length === 1 ? "" : "s"}
            </span>
            <div>
              <button
                className="button secondary"
                disabled={currentPage === 1}
                onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
              >
                Prev
              </button>
              {paginationItems(currentPage, totalPages).map((item, index) =>
                item === "..." ? (
                  <span key={`ellipsis-${index}`} className="page-pill">...</span>
                ) : (
                  <button
                    key={item}
                    className={`pagination-page ${item === currentPage ? "active" : ""}`}
                    onClick={() => setCurrentPage(item)}
                    type="button"
                  >
                    {item}
                  </button>
                ),
              )}
              <button
                className="button secondary"
                disabled={currentPage === totalPages}
                onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))}
              >
                Next
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function DashboardToastMessage({ onClose, toast }: { onClose: () => void; toast: NonNullable<DashboardToast> }) {
  const Icon = toast.tone === "success" ? CheckCircle2 : AlertTriangle;
  return (
    <div className={`review-toast toast-${toast.tone}`} role="status" aria-live="polite">
      <Icon size={20} />
      <div>
        <strong>{toast.title}</strong>
        <small>{toast.message}</small>
      </div>
      <button type="button" onClick={onClose} aria-label="Dismiss notification">
        <X size={14} />
      </button>
    </div>
  );
}

function DeleteFilingModal({
  error,
  filing,
  isDeleting,
  onCancel,
  onConfirm,
}: {
  error: string;
  filing: Filing;
  isDeleting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="dashboard-delete-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        aria-labelledby="delete-filing-title"
        aria-modal="true"
        className="dashboard-delete-modal"
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dashboard-delete-modal-head">
          <span className="dashboard-delete-icon"><Trash2 size={22} /></span>
          <div>
            <p>Remove from ERISAPros</p>
            <h2 id="delete-filing-title">Delete filing from dashboard?</h2>
          </div>
        </div>
        <p className="dashboard-delete-copy">
          This removes the filing from the ERISAPros dashboard and review queue only. The ShareFile document and source folder will remain unchanged.
        </p>
        <div className="dashboard-delete-summary">
          <span>
            <small>Filing</small>
            <strong>{formatFilingDisplayName(filing.file_name)}</strong>
          </span>
          <span>
            <small>Client</small>
            <strong>{filingClientName(filing)}</strong>
          </span>
          <span>
            <small>Plan / EIN</small>
            <strong>{filingPlanIdentity(filing)}</strong>
          </span>
        </div>
        {error ? <div className="dashboard-delete-error">{error}</div> : null}
        <div className="dashboard-delete-actions">
          <button className="button secondary" type="button" disabled={isDeleting} onClick={onCancel}>
            Cancel
          </button>
          <button className="button dashboard-delete-confirm" type="button" disabled={isDeleting} onClick={onConfirm}>
            <Trash2 size={17} />
            {isDeleting ? "Deleting..." : "Delete from dashboard"}
          </button>
        </div>
      </section>
    </div>
  );
}

function DashboardKpi({
  icon,
  value,
  label,
  tone,
  featured,
  note,
}: {
  icon: ReactNode;
  value: number;
  label: string;
  tone: "ready" | "warn" | "danger" | "info";
  featured?: boolean;
  note?: string;
}) {
  return (
    <div className={`dashboard-kpi card dashboard-kpi-${tone} ${featured ? "featured" : ""}`}>
      <span>{icon}</span>
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
        {note ? <em>{note}</em> : null}
      </div>
      <i />
    </div>
  );
}

function FilterDropdown({
  compact,
  icon,
  label,
  onChange,
  options,
  value,
}: {
  compact?: boolean;
  icon?: ReactNode;
  label: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  value: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const selected = options.find((option) => option.value === value) || options[0];

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  return (
    <div className={`filter-dropdown ${compact ? "compact" : ""}`} ref={ref}>
      <button className="filter-dropdown-trigger" type="button" onClick={() => setOpen((current) => !current)} aria-expanded={open}>
        {icon ? <span className="filter-dropdown-icon">{icon}</span> : null}
        <span className="filter-dropdown-label">{label}</span>
        <strong>{selected.label}</strong>
        <ChevronDown size={16} />
      </button>
      {open ? (
        <div className="filter-dropdown-menu">
          {options.map((option) => (
            <button
              className={option.value === value ? "selected" : ""}
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              {option.value === value ? <Check size={16} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function DashboardFilingRow({ filing, onDeleteRequest }: { filing: Filing; onDeleteRequest: (filing: Filing) => void }) {
  const missingOther = (filing.missing_medium_priority_count || 0) + (filing.missing_low_priority_count || 0);
  const problemCount = (filing.missing_high_priority_count || 0) + missingOther + (filing.low_confidence_count || 0) + (filing.unmapped_count || 0);
  const totalFields = DASHBOARD_REVIEW_FIELD_TOTAL;
  const missingFields = (filing.missing_high_priority_count || 0) + missingOther;
  const foundFields = Math.max(0, totalFields - missingFields);
  const pipelineStage = dashboardPipelineStage(filing);
  const fieldMetrics = dashboardFieldMetrics(filing, foundFields, totalFields, pipelineStage);
  const issueMetrics = dashboardIssueMetrics(filing, problemCount, missingOther, pipelineStage);
  const displayName = formatFilingDisplayName(filing.file_name);
  const clientName = filingClientName(filing);
  const planIdentity = filingPlanIdentity(filing);
  const planName = filingPlanName(filing, pipelineStage);
  const readyAction = filing.status === "APPROVED" || filing.status === "READY_FOR_APPROVAL";
  const statusDotTone = filing.status === "FAILED" || filing.status === "REJECTED"
    ? "danger"
    : filing.status === "APPROVED"
      ? "ready"
      : pipelineStage.tone === "warn"
        ? "warn"
        : "info";

  return (
    <tr>
      <td>
        <Link className="dashboard-filing-cell" to={`/filings/${filing.id}`}>
          <i className={`dashboard-status-dot ${statusDotTone}`} />
          <FileText size={22} />
          <span>
            <strong>{displayName}</strong>
            <small>{clientName}</small>
          </span>
        </Link>
      </td>
      <td>
        <div className="dashboard-plan-cell">
          <strong>{planIdentity}</strong>
          <small>{planName}</small>
        </div>
      </td>
      <td>
        <div className="dashboard-stage-cell">
          <StatusBadge status={filing.status} />
          <ScheduleAContractBadge type={filing.schedule_a_contract_type || "UNKNOWN"} compact />
          <small>{pipelineStage.detail}</small>
        </div>
      </td>
      <td>
        <div className={`dashboard-confidence ${fieldMetrics.pending ? "pending" : ""}`}>
          <strong>{fieldMetrics.headline}</strong>
          <small>{fieldMetrics.detail}</small>
          <div className="confidence-track">
            <i className={fieldMetrics.barClass} style={{ width: fieldMetrics.width }} />
          </div>
        </div>
      </td>
      <td>
        <div className="dashboard-issues">
          <strong>{issueMetrics.headline}</strong>
          <small><i className="issue-dot high" /> {issueMetrics.highDetail}</small>
          <small><i className="issue-dot medium" /> {issueMetrics.otherDetail}</small>
        </div>
      </td>
      <td>
        <div className="dashboard-uploaded-on">
          <span>{shortDate(filing.created_at)}</span>
          <small>{shortTime(filing.created_at)}</small>
        </div>
      </td>
      <td>
        <div className="dashboard-row-actions">
          <Link className="button dashboard-review-button" to={`/filings/${filing.id}`} aria-label={`Review ${displayName}`}>
            {readyAction ? "View" : "Review"} <Eye size={16} />
          </Link>
        </div>
      </td>
      <td className="more-col">
        <button
          className="dashboard-delete-row-button"
          type="button"
          aria-label={`Delete ${displayName} from dashboard`}
          title="Delete from dashboard"
          onClick={() => onDeleteRequest(filing)}
        >
          <Trash2 size={17} />
        </button>
      </td>
    </tr>
  );
}

function ScheduleAContractBadge({ type, compact = false }: { type: ScheduleAContractType; compact?: boolean }) {
  const tone = type === "EXPERIENCE_RATED"
    ? "experience"
    : type === "NONEXPERIENCE_RATED"
      ? "nonexperience"
      : type === "NEEDS_REVIEW"
        ? "review"
        : "unknown";
  return <span className={`contract-type-badge ${tone} ${compact ? "compact" : ""}`}>{contractTypeLabel(type)}</span>;
}

function contractTypeLabel(type: ScheduleAContractType) {
  if (type === "EXPERIENCE_RATED") return "Experience rated";
  if (type === "NONEXPERIENCE_RATED") return "Nonexperience rated";
  if (type === "NEEDS_REVIEW") return "Needs type review";
  return "Type unknown";
}

function filingClientName(filing: Filing) {
  const clientName = firstStringFromPackageDocuments(filing, ["client_name", "client"]);
  return clientName || xmlValue(filing.proposed_xml, "SponsorName") || "Client pending";
}

function filingPlanIdentity(filing: Filing) {
  const ein = firstXmlValue(filing.proposed_xml, ["EIN", "EmployerEIN", "SponsorEIN", "SponsEIN", "SponsDfeEIN"]);
  const planNumber = firstXmlValue(filing.proposed_xml, ["PlanNum", "PN", "PlanNumber", "SponsDfePlanNum"]);
  if (ein && planNumber) return `${ein} / ${planNumber}`;
  if (ein) return ein;
  const docEin = firstStringFromPackageDocuments(filing, ["ein", "company_employer_id", "customer_id"]);
  const docPlanNumber = firstStringFromPackageDocuments(filing, ["plan_number", "plan_num", "pn"]);
  if (docEin && docPlanNumber) return `${docEin} / ${docPlanNumber}`;
  if (isWaitingForFiles(filing.status)) return "Waiting for package";
  if (isProcessingStatus(filing.status)) return "Plan details loading";
  return "Plan pending";
}

function filingPlanName(filing: Filing, stage: DashboardPipelineStage) {
  const name = firstXmlValue(filing.proposed_xml, ["PlanName", "PlanNm"]) || firstStringFromPackageDocuments(filing, ["plan_name"]);
  if (name) return name;
  if (isWaitingForFiles(filing.status)) return stage.detail;
  if (isProcessingStatus(filing.status)) return "Plan details will appear after extraction";
  return "Plan details pending";
}

type DashboardPipelineStage = {
  detail: string;
  pendingMetrics: boolean;
  tone: "info" | "warn" | "ready" | "danger";
};

function dashboardPipelineStage(filing: Filing): DashboardPipelineStage {
  if (filing.status === "WAITING_FOR_WORKSHEET") {
    return {
      detail: "Schedule A received. Waiting for the matching Plan Worksheet.",
      pendingMetrics: true,
      tone: "warn",
    };
  }
  if (filing.status === "WAITING_FOR_SCHEDULE_A") {
    return {
      detail: "Plan Worksheet received. Waiting for the matching Schedule A PDF.",
      pendingMetrics: true,
      tone: "warn",
    };
  }
  if (filing.status === "UPLOADED" || filing.status === "QUEUED") {
    return {
      detail: "Both files found. Extraction is queued.",
      pendingMetrics: true,
      tone: "info",
    };
  }
  if (filing.status === "EXTRACTING") {
    return {
      detail: "Extracting with EyeLevel, then mapping fields.",
      pendingMetrics: true,
      tone: "info",
    };
  }
  if (filing.status === "EXTRACTED" || filing.status === "MAPPED") {
    return {
      detail: "Mapping complete. Loading FT Williams current values.",
      pendingMetrics: true,
      tone: "info",
    };
  }
  if (filing.status === "QUERYING_FTW_CURRENT") {
    return {
      detail: "Fetching current Form 5500 and Schedule A values from FT Williams.",
      pendingMetrics: true,
      tone: "info",
    };
  }
  if (filing.status === "NEEDS_REVIEW") {
    return {
      detail: "FTW current values loaded. Review field decisions.",
      pendingMetrics: false,
      tone: "warn",
    };
  }
  if (filing.status === "READY_FOR_APPROVAL") {
    return {
      detail: "Fields reviewed. Approval is required before sending.",
      pendingMetrics: false,
      tone: "ready",
    };
  }
  if (filing.status === "APPROVED") {
    return {
      detail: "Approved and ready to send to FT Williams.",
      pendingMetrics: false,
      tone: "ready",
    };
  }
  if (filing.status === "FAILED" || filing.status === "REJECTED") {
    return {
      detail: filing.error_message || "Needs operator review before continuing.",
      pendingMetrics: false,
      tone: "danger",
    };
  }
  return {
    detail: "Package is being prepared for review.",
    pendingMetrics: false,
    tone: "info",
  };
}

function dashboardFieldMetrics(
  filing: Filing,
  foundFields: number,
  totalFields: number,
  stage: DashboardPipelineStage,
) {
  if (stage.pendingMetrics) {
    const queued = filing.status === "UPLOADED" || filing.status === "QUEUED";
    const queryingFtw = filing.status === "EXTRACTED" || filing.status === "MAPPED" || filing.status === "QUERYING_FTW_CURRENT";
    const width = queryingFtw ? "72%" : filing.status === "EXTRACTING" ? "46%" : queued ? "22%" : "0%";
    return {
      headline: isWaitingForFiles(filing.status)
        ? "Waiting for files"
        : queryingFtw
          ? "Fetching FTW current data"
          : filing.status === "EXTRACTING"
            ? "Extraction in progress"
            : "Queued",
      detail: isWaitingForFiles(filing.status)
        ? "Coverage starts after both files arrive"
        : queryingFtw
          ? "Comparison starts after FTW values load"
          : "Field coverage pending",
      pending: true,
      barClass: queryingFtw || filing.status === "EXTRACTING" ? "confidence-low" : "confidence-missing",
      width,
    };
  }

  const coverage = foundFields / totalFields;
  return {
    headline: `${foundFields} of ${totalFields} fields`,
    detail: `${percent(coverage)} complete`,
    pending: false,
    barClass: coverage >= 0.8 ? "confidence-medium" : coverage > 0 ? "confidence-low" : "confidence-missing",
    width: percent(coverage),
  };
}

function dashboardIssueMetrics(
  filing: Filing,
  problemCount: number,
  missingOther: number,
  stage: DashboardPipelineStage,
) {
  if (stage.pendingMetrics) {
    return {
      headline: "Pending",
      highDetail: "High-priority issues not calculated yet",
      otherDetail: "Medium / low issues pending",
    };
  }

  return {
    headline: `${problemCount} issues`,
    highDetail: `${filing.missing_high_priority_count || 0} high priority`,
    otherDetail: `${missingOther} medium / low`,
  };
}

function isWaitingForFiles(status: Filing["status"]) {
  return status === "WAITING_FOR_WORKSHEET" || status === "WAITING_FOR_SCHEDULE_A";
}

function dashboardChangeToast(previous: Filing[] | null, next: Filing[]): DashboardToast {
  if (!previous) return null;
  const previousById = new Map(previous.map((filing) => [filing.id, filing]));
  const newFilings = next.filter((filing) => !previousById.has(filing.id));
  const changedFilings = next.filter((filing) => {
    const old = previousById.get(filing.id);
    return old ? filingChangeSignature(old) !== filingChangeSignature(filing) : false;
  });
  const events = [...newFilings, ...changedFilings];
  if (!events.length) return null;
  if (events.length > 1) {
    return {
      tone: events.some((filing) => filing.status === "FAILED" || filing.status === "REJECTED") ? "error" : "success",
      title: "ShareFile activity updated",
      message: `${events.length} filing${events.length === 1 ? "" : "s"} updated from ShareFile.`,
    };
  }
  const filing = events[0];
  const displayName = formatFilingDisplayName(filing.file_name);
  const old = previousById.get(filing.id);
  if (!old) return filingStatusToast(filing, displayName);
  if (old.status !== filing.status) return filingStatusToast(filing, displayName);
  return {
    tone: "success",
    title: "Filing package updated",
    message: `${displayName} was updated from ShareFile.`,
  };
}

function filingChangeSignature(filing: Filing) {
  return [
    filing.status,
    filing.updated_at,
    filing.package_document_count,
    filing.missing_high_priority_count,
    filing.missing_medium_priority_count,
    filing.missing_low_priority_count,
    filing.low_confidence_count,
    filing.unmapped_count,
  ].join("|");
}

function filingStatusToast(filing: Filing, displayName: string): NonNullable<DashboardToast> {
  if (filing.status === "WAITING_FOR_WORKSHEET") {
    return {
      tone: "success",
      title: "Schedule A received",
      message: `${displayName} is waiting for the matching Plan Worksheet.`,
    };
  }
  if (filing.status === "WAITING_FOR_SCHEDULE_A") {
    return {
      tone: "success",
      title: "Plan Worksheet received",
      message: "Waiting for the matching Schedule A PDF.",
    };
  }
  if (filing.status === "QUEUED" || filing.status === "UPLOADED") {
    return {
      tone: "success",
      title: "Filing package ready",
      message: `${displayName} has both files and extraction is queued.`,
    };
  }
  if (filing.status === "EXTRACTING") {
    return {
      tone: "success",
      title: "Extraction started",
      message: `${displayName} is being extracted and mapped.`,
    };
  }
  if (filing.status === "QUERYING_FTW_CURRENT" || filing.status === "EXTRACTED" || filing.status === "MAPPED") {
    return {
      tone: "success",
      title: "Loading FT Williams current data",
      message: `${displayName} is loading current Form 5500 and Schedule A values.`,
    };
  }
  if (filing.status === "NEEDS_REVIEW" || filing.status === "READY_FOR_APPROVAL") {
    return {
      tone: "success",
      title: "Filing ready for review",
      message: `${displayName} is ready for field review.`,
    };
  }
  if (filing.status === "FAILED" || filing.status === "REJECTED") {
    return {
      tone: "error",
      title: "Filing needs attention",
      message: `${displayName} needs review before it can continue.`,
    };
  }
  return {
    tone: "success",
    title: "Filing updated",
    message: `${displayName} was updated from ShareFile.`,
  };
}

function firstStringFromPackageDocuments(filing: Filing, keys: string[]) {
  for (const document of filing.package_documents || []) {
    for (const key of keys) {
      const value = stringFromRecord(document, key);
      if (value) return value;
    }
  }
  return "";
}

function stringFromRecord(record: Record<string, unknown> | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function firstXmlValue(xml: string | null | undefined, tags: string[]) {
  for (const tag of tags) {
    const value = xmlValue(xml, tag);
    if (value) return value;
  }
  return "";
}

function xmlValue(xml: string | null | undefined, tag: string) {
  if (!xml) return "";
  const match = xml.match(new RegExp(`<${tag}>([^<]*)</${tag}>`, "i"));
  return match?.[1]?.trim() || "";
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}

function shortTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function matchesDateFilter(value: string, filter: DateFilter) {
  if (filter === "ALL") return true;
  const date = new Date(value);
  const now = new Date();
  if (Number.isNaN(date.getTime())) return false;

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (filter === "TODAY") {
    return date >= startOfToday;
  }

  const days = filter === "LAST_7" ? 7 : 30;
  const threshold = new Date(startOfToday);
  threshold.setDate(startOfToday.getDate() - (days - 1));
  return date >= threshold;
}

function isProcessingStatus(status: Filing["status"]) {
  return [
    "WAITING_FOR_WORKSHEET",
    "WAITING_FOR_SCHEDULE_A",
    "QUEUED",
    "UPLOADED",
    "EXTRACTING",
    "EXTRACTED",
    "MAPPED",
    "QUERYING_FTW_CURRENT",
  ].includes(status);
}

function paginationItems(currentPage: number, totalPages: number) {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const pages = new Set([1, totalPages, currentPage, currentPage - 1, currentPage + 1]);
  return [...pages]
    .filter((page) => page >= 1 && page <= totalPages)
    .sort((a, b) => a - b)
    .reduce<(number | "...")[]>((items, page) => {
      const previous = items[items.length - 1];
      if (typeof previous === "number" && page - previous > 1) items.push("...");
      items.push(page);
      return items;
    }, []);
}
