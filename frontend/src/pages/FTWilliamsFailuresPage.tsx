import {
  AlertTriangle,
  Calendar,
  Check,
  ChevronDown,
  Eye,
  FileText,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "../router";
import { dismissFTWilliamsFailure } from "../api";
import {
  classifyFTWilliamsFailure,
  countFTWilliamsFailureTypes,
  failureTypeLabels,
  ftwFailureTypeClass,
  type FTWilliamsFailureType,
} from "../ftwFailures";
import type { FTWilliamsFailureQueueItem } from "../types";
import { formatFilingDisplayName } from "../utils";
import { FTWilliamsDiagnostic } from "../ui/FTWilliamsDiagnostic";
import { InlineLoader, Skeleton } from "../ui/Loading";
import { refreshFTWilliamsFailures, useFTWilliamsFailures } from "../ui/ftWilliamsNotificationStore";

type FailureTypeFilter = "ALL" | FTWilliamsFailureType;
type DateFilter = "ALL" | "TODAY" | "LAST_7" | "LAST_30";

export function FTWilliamsFailuresPage() {
  const failuresState = useFTWilliamsFailures();
  const failures = failuresState.data;
  const initialLoad = failuresState.loading && !failuresState.updatedAt;
  const [actionMessage, setActionMessage] = useState("");
  const message = actionMessage || failuresState.error;
  const [dismissingId, setDismissingId] = useState("");
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<FailureTypeFilter>("ALL");
  const [dateFilter, setDateFilter] = useState<DateFilter>("ALL");
  const [rowsLimit, setRowsLimit] = useState(10);
  const [currentPage, setCurrentPage] = useState(1);

  async function dismissFailure(item: FTWilliamsFailureQueueItem) {
    if (!window.confirm("Dismiss this failure from the active queue? The failure remains in Activity for auditing.")) return;
    setActionMessage("");
    setDismissingId(item.filing_id);
    try {
      await dismissFTWilliamsFailure(item.filing_id);
      await refreshFTWilliamsFailures();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Could not dismiss the FT Williams failure.");
    } finally {
      setDismissingId("");
    }
  }

  const filteredFailures = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return failures
      .filter((item) => {
        const failureType = classifyFTWilliamsFailure(item);
        const haystack = [
          item.filing_name,
          item.failure_reason,
          item.next_action,
          item.plan_name,
          item.sponsor_name,
          item.company_employer_id,
          item.plan_number,
          item.customer_id,
          item.plan_id,
          item.ftw_customer_id,
          item.ftw_plan_id,
          item.year,
          failureTypeLabels[failureType],
        ].join(" ").toLowerCase();
        return (
          (!needle || haystack.includes(needle)) &&
          (typeFilter === "ALL" || failureType === typeFilter) &&
          matchesDateFilter(item.failed_at, dateFilter)
        );
      })
      .sort((a, b) => {
        const dateDelta = new Date(b.failed_at).getTime() - new Date(a.failed_at).getTime();
        if (dateDelta !== 0) return dateDelta;
        return b.attempted_field_count - a.attempted_field_count;
      });
  }, [dateFilter, failures, search, typeFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredFailures.length / rowsLimit));
  const pageStartIndex = filteredFailures.length ? (currentPage - 1) * rowsLimit : 0;
  const pageEndIndex = Math.min(pageStartIndex + rowsLimit, filteredFailures.length);
  const visibleFailures = filteredFailures.slice(pageStartIndex, pageEndIndex);
  const failureCounts = countFTWilliamsFailureTypes(failures);
  const summaryCards = [
    { label: "Active failures", value: failures.length, tone: "danger" as const },
    { label: failureTypeLabels.NEEDS_RETRY, value: failureCounts.NEEDS_RETRY, tone: "warn" as const },
    { label: failureTypeLabels.NEEDS_DATA_FIX, value: failureCounts.NEEDS_DATA_FIX, tone: "warn" as const },
    { label: failureTypeLabels.NEEDS_PLAN_MATCH, value: failureCounts.NEEDS_PLAN_MATCH, tone: "ready" as const },
    { label: failureTypeLabels.NEEDS_SERVICE_CHECK, value: failureCounts.NEEDS_SERVICE_CHECK, tone: "info" as const },
  ].filter((card) => card.value > 0);

  useEffect(() => {
    setCurrentPage(1);
  }, [dateFilter, rowsLimit, search, typeFilter]);

  useEffect(() => {
    setCurrentPage((page) => Math.min(page, totalPages));
  }, [totalPages]);

  return (
    <div className="dashboard-page dashboard-v3 ftw-failures-page">
      <header className="dashboard-v2-hero">
        <div>
          <span className="eyebrow">FT Williams Operator Queue</span>
          <h1 className="page-title">FT Williams Failures</h1>
          <p>Active unresolved FT Williams send failures that need operator review before retrying.</p>
        </div>
      </header>

      {initialLoad ? (
        <section className="ftw-failure-summary-grid ftw-summary-loading" aria-label="Loading failure summary">
          {Array.from({ length: 4 }, (_, index) => (
            <div className="ftw-failure-summary-card" key={index}>
              <Skeleton className="ftw-summary-label-skeleton" />
              <Skeleton className="ftw-summary-value-skeleton" />
            </div>
          ))}
        </section>
      ) : failures.length ? (
        <section className="ftw-failure-summary-grid">
          {summaryCards.map((card) => (
            <FailureSummaryCard key={card.label} label={card.label} value={card.value} tone={card.tone} />
          ))}
        </section>
      ) : !message ? (
        <section className="ftw-failure-clear-state card">
          <span><Check size={22} /></span>
          <div>
            <h2>No active FT Williams failures</h2>
            <p>New failed sends will appear here automatically when operator review is needed.</p>
          </div>
        </section>
      ) : null}

      {message ? <div className="dashboard-message card">{message}</div> : null}

      <section className="dashboard-table-panel card">
        <div className="dashboard-table-head ftw-failures-head">
          <div>
            <h2>Active Failure Queue</h2>
            <p>
              {initialLoad
                ? "Loading active failures..."
                : `${filteredFailures.length} unresolved failure${filteredFailures.length === 1 ? "" : "s"} match this view.`}
              {failuresState.loading && failuresState.updatedAt ? <InlineLoader label="Refreshing" /> : null}
            </p>
          </div>
          <div className="dashboard-table-controls">
            <label className="dashboard-search ftw-failures-search">
              <Search size={17} />
              <span>Search</span>
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filing, plan, EIN, reason..." />
            </label>
            <FilterDropdown
              label="Type"
              value={typeFilter}
              options={[
                { value: "ALL", label: "All types" },
                { value: "NEEDS_RETRY", label: "Needs retry" },
                { value: "NEEDS_DATA_FIX", label: "Needs data fix" },
                { value: "NEEDS_PLAN_MATCH", label: "Needs plan match" },
                { value: "NEEDS_SERVICE_CHECK", label: "Needs service check" },
              ]}
              onChange={(value) => setTypeFilter(value as FailureTypeFilter)}
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
            <button className="button secondary table-filter-button" type="button" onClick={() => {
              setSearch("");
              setTypeFilter("ALL");
              setDateFilter("ALL");
            }}>
              <SlidersHorizontal size={16} /> Reset
            </button>
          </div>
        </div>

        <div className="dashboard-table-wrap" aria-busy={failuresState.loading}>
          <table className="dashboard-filings-table ftw-failures-table">
            <thead>
              <tr>
                <th>Filing</th>
                <th>Failure Type</th>
                <th>Reason</th>
                <th>Plan</th>
                <th>Fields</th>
                <th>Failed On</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {initialLoad ? <FailureTableLoadingRows /> : null}
              {visibleFailures.map((item) => (
                <FTWilliamsFailureQueueRow
                  item={item}
                  key={`${item.filing_id}-${item.failed_at}`}
                  dismissing={dismissingId === item.filing_id}
                  onDismiss={dismissFailure}
                />
              ))}
            </tbody>
          </table>
        </div>

        {!initialLoad && !visibleFailures.length ? (
          <div className="empty-state ftw-history-empty">
            <AlertTriangle size={18} /> No unresolved FT Williams failures match these filters.
          </div>
        ) : null}

        <div className="dashboard-table-footer">
          <span>
            Showing {filteredFailures.length ? pageStartIndex + 1 : 0}-{pageEndIndex} of {filteredFailures.length} failure{filteredFailures.length === 1 ? "" : "s"}
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
  );
}

function FailureTableLoadingRows() {
  return (
    <>
      {Array.from({ length: 4 }, (_, row) => (
        <tr className="ftw-table-skeleton-row" key={row}>
          {Array.from({ length: 7 }, (_, column) => (
            <td key={column}><Skeleton className={column === 2 ? "wide" : ""} /></td>
          ))}
        </tr>
      ))}
    </>
  );
}

function FailureSummaryCard({
  label,
  tone,
  value,
}: {
  label: string;
  tone: "danger" | "warn" | "info" | "ready";
  value: number;
}) {
  return (
    <div className={`ftw-failure-summary-card summary-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function FTWilliamsFailureQueueRow({
  dismissing,
  item,
  onDismiss,
}: {
  dismissing: boolean;
  item: FTWilliamsFailureQueueItem;
  onDismiss: (item: FTWilliamsFailureQueueItem) => void;
}) {
  const displayName = formatFilingDisplayName(item.filing_name);
  const failureType = classifyFTWilliamsFailure(item);
  const planIdentity = item.company_employer_id && item.plan_number
    ? `${item.company_employer_id} / ${item.plan_number}`
    : item.customer_id && item.plan_id
      ? `${item.customer_id} / ${item.plan_id}`
      : item.ftw_customer_id && item.ftw_plan_id
        ? `FTW ${item.ftw_customer_id} / ${item.ftw_plan_id}`
        : "Plan pending";
  return (
    <tr>
      <td>
        <Link className="dashboard-filing-cell" to={`/filings/${item.filing_id}`}>
          <FileText size={22} />
          <span>
            <strong>{displayName}</strong>
            <small>{item.year ? `Plan year ${item.year}` : "Plan year pending"}</small>
          </span>
        </Link>
      </td>
      <td>
        <span className={`ftw-failure-type type-${ftwFailureTypeClass(failureType)}`}>
          {failureTypeLabels[failureType]}
        </span>
      </td>
      <td>
        <div className="ftw-failure-reason-cell">
          <FTWilliamsDiagnostic
            errorCode={item.error_code}
            editCheckIssues={item.edit_check_issues}
            message={item.failure_reason}
            operations={item.operation_diagnostics}
            technicalDetails={item.technical_details}
          />
          {item.next_action ? <small>Next: {item.next_action}</small> : null}
        </div>
      </td>
      <td>
        <div className="ftw-history-plan">
          <strong>{planIdentity}</strong>
          <small>{item.plan_name || item.sponsor_name || "FT Williams plan details"}</small>
        </div>
      </td>
      <td>
        <div className="ftw-history-fields">
          <strong>{item.attempted_field_count}</strong>
          <small>attempted</small>
        </div>
      </td>
      <td>
        <div className="dashboard-uploaded-on">
          <span>{shortDate(item.failed_at)}</span>
          <small>{shortTime(item.failed_at)}</small>
        </div>
      </td>
      <td>
        <div className="ftw-failure-actions">
          <Link className="button danger ftw-failure-review-button" to={`/filings/${item.filing_id}`}>
            Review / Retry <Eye size={15} />
          </Link>
          {item.can_dismiss !== false ? (
            <button className="button secondary" disabled={dismissing} onClick={() => onDismiss(item)} type="button">
              {dismissing ? "Dismissing..." : "Dismiss"}
            </button>
          ) : null}
        </div>
      </td>
    </tr>
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

function shortDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}

function shortTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));
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
