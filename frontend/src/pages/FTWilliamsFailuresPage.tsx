import {
  AlertTriangle,
  Calendar,
  Check,
  ChevronDown,
  ChevronUp,
  Eye,
  FileText,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { dismissFTWilliamsFailure, getFTWilliamsFailureDetail, listFTWilliamsFailureQueue } from "../api";
import { failureTypeLabels, ftwFailureTypeClass, type FTWilliamsFailureType } from "../ftwFailures";
import { Link } from "../router";
import type {
  FTWilliamsFailureCounts,
  FTWilliamsFailureQueueItem,
  FTWilliamsFailureQueueResponse,
  FTWilliamsFailureQueueSummary,
} from "../types";
import { FTWilliamsDiagnostic } from "../ui/FTWilliamsDiagnostic";
import { InlineLoader, Skeleton } from "../ui/Loading";
import { refreshFTWilliamsFailures } from "../ui/ftWilliamsNotificationStore";
import { formatFilingDisplayName } from "../utils";

type FailureTypeFilter = "ALL" | FTWilliamsFailureType;
type DateFilter = "ALL" | "TODAY" | "LAST_7" | "LAST_30";

const PAGE_SIZE = 10;
const EMPTY_COUNTS: FTWilliamsFailureCounts = {
  active: 0,
  needs_retry: 0,
  needs_data_fix: 0,
  needs_plan_match: 0,
  needs_service_check: 0,
};

const EMPTY_QUEUE: FTWilliamsFailureQueueResponse = {
  total: 0,
  page: 1,
  page_size: PAGE_SIZE,
  total_pages: 1,
  counts: EMPTY_COUNTS,
  items: [],
};

export function FTWilliamsFailuresPage() {
  const [queue, setQueue] = useState<FTWilliamsFailureQueueResponse>(EMPTY_QUEUE);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [dismissingId, setDismissingId] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<FailureTypeFilter>("ALL");
  const [dateFilter, setDateFilter] = useState<DateFilter>("ALL");
  const [currentPage, setCurrentPage] = useState(1);
  const [refreshKey, setRefreshKey] = useState(0);
  const [expandedId, setExpandedId] = useState("");
  const [detailById, setDetailById] = useState<Record<string, FTWilliamsFailureQueueItem>>({});
  const [detailLoadingId, setDetailLoadingId] = useState("");
  const [detailError, setDetailError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setCurrentPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void listFTWilliamsFailureQueue({
      page: currentPage,
      pageSize: PAGE_SIZE,
      search: debouncedSearch,
      failureType: typeFilter,
      date: dateFilter,
      signal: controller.signal,
    })
      .then((response) => {
        setQueue(response);
        setLoaded(true);
        if (currentPage > response.total_pages) setCurrentPage(response.total_pages);
      })
      .catch((requestError) => {
        if (controller.signal.aborted) return;
        setError(requestError instanceof Error ? requestError.message : "Could not load FT Williams failures.");
        setLoaded(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [currentPage, dateFilter, debouncedSearch, refreshKey, typeFilter]);

  async function dismissFailure(item: FTWilliamsFailureQueueSummary) {
    if (!window.confirm("Dismiss this failure from the active queue? The failure remains in Activity for auditing.")) return;
    setActionMessage("");
    setDismissingId(item.filing_id);
    try {
      await dismissFTWilliamsFailure(item.filing_id);
      setExpandedId("");
      setRefreshKey((value) => value + 1);
      await refreshFTWilliamsFailures();
    } catch (requestError) {
      setActionMessage(requestError instanceof Error ? requestError.message : "Could not dismiss the FT Williams failure.");
    } finally {
      setDismissingId("");
    }
  }

  async function toggleDetails(item: FTWilliamsFailureQueueSummary) {
    if (expandedId === item.filing_id) {
      setExpandedId("");
      return;
    }
    setExpandedId(item.filing_id);
    setDetailError("");
    if (detailById[item.filing_id]) return;
    setDetailLoadingId(item.filing_id);
    try {
      const detail = await getFTWilliamsFailureDetail(item.filing_id);
      setDetailById((current) => ({ ...current, [item.filing_id]: detail }));
    } catch (requestError) {
      setDetailError(requestError instanceof Error ? requestError.message : "Could not load failure details.");
    } finally {
      setDetailLoadingId("");
    }
  }

  const initialLoad = loading && !loaded;
  const message = actionMessage || error;
  const counts = queue.counts;
  const summaryCards = [
    { label: "Active failures", value: counts.active, tone: "danger" as const },
    { label: failureTypeLabels.NEEDS_RETRY, value: counts.needs_retry, tone: "warn" as const },
    { label: failureTypeLabels.NEEDS_DATA_FIX, value: counts.needs_data_fix, tone: "warn" as const },
    { label: failureTypeLabels.NEEDS_PLAN_MATCH, value: counts.needs_plan_match, tone: "ready" as const },
    { label: failureTypeLabels.NEEDS_SERVICE_CHECK, value: counts.needs_service_check, tone: "info" as const },
  ].filter((card) => card.value > 0);
  const pageStart = queue.total ? (queue.page - 1) * queue.page_size + 1 : 0;
  const pageEnd = Math.min(queue.page * queue.page_size, queue.total);

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
      ) : counts.active ? (
        <section className="ftw-failure-summary-grid">
          {summaryCards.map((card) => <FailureSummaryCard key={card.label} {...card} />)}
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

      {message ? (
        <div className="dashboard-message card ftw-failure-error-state">
          <span>{message}</span>
          <button className="button secondary" type="button" onClick={() => setRefreshKey((value) => value + 1)}>Retry</button>
        </div>
      ) : null}

      <section className="dashboard-table-panel card">
        <div className="dashboard-table-head ftw-failures-head">
          <div>
            <h2>Active Failure Queue</h2>
            <p>
              {initialLoad ? "Loading active failures..." : `${queue.total} unresolved failure${queue.total === 1 ? "" : "s"} match this view.`}
              {loading && loaded ? <InlineLoader label="Refreshing" /> : null}
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
              onChange={(value) => {
                setTypeFilter(value as FailureTypeFilter);
                setCurrentPage(1);
              }}
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
              onChange={(value) => {
                setDateFilter(value as DateFilter);
                setCurrentPage(1);
              }}
            />
            <div className="ftw-page-size-label" aria-label="Ten failures per page">10 per page</div>
            <button className="button secondary table-filter-button" type="button" onClick={() => {
              setSearch("");
              setDebouncedSearch("");
              setTypeFilter("ALL");
              setDateFilter("ALL");
              setCurrentPage(1);
            }}>
              <SlidersHorizontal size={16} /> Reset
            </button>
          </div>
        </div>

        <div className="dashboard-table-wrap" aria-busy={loading}>
          <table className="dashboard-filings-table ftw-failures-table">
            <thead>
              <tr>
                <th>Filing</th>
                <th>Failure Type</th>
                <th>Reason</th>
                <th>Plan</th>
                <th>Issues</th>
                <th>Failed On</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {initialLoad ? <FailureTableLoadingRows /> : null}
              {!initialLoad && queue.items.map((item) => {
                const detail = detailById[item.filing_id];
                const expanded = expandedId === item.filing_id;
                return (
                  <Fragment key={`${item.filing_id}-${item.failed_at}`}>
                    <FTWilliamsFailureQueueRow
                      item={item}
                      expanded={expanded}
                      dismissing={dismissingId === item.filing_id}
                      detailsLoading={detailLoadingId === item.filing_id}
                      onDismiss={dismissFailure}
                      onToggleDetails={toggleDetails}
                    />
                    {expanded ? (
                      <tr className="ftw-failure-detail-row">
                        <td colSpan={7}>
                          {detailLoadingId === item.filing_id ? <InlineLoader label="Loading failure details" /> : detail ? (
                            <div className="ftw-failure-detail-panel">
                              <FTWilliamsDiagnostic
                                errorCode={detail.error_code}
                                editCheckIssues={detail.edit_check_issues}
                                message={detail.failure_reason}
                                operations={detail.operation_diagnostics}
                                technicalDetails={detail.technical_details}
                              />
                              {detail.next_action ? <p><strong>Next:</strong> {detail.next_action}</p> : null}
                            </div>
                          ) : <div className="ftw-side-message">{detailError || "Failure details are unavailable."}</div>}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>

        {!initialLoad && !loading && !queue.items.length && !message ? (
          <div className="empty-state ftw-history-empty">
            <AlertTriangle size={18} /> No unresolved FT Williams failures match these filters.
          </div>
        ) : null}

        <div className="dashboard-table-footer">
          <span>Showing {pageStart}-{pageEnd} of {queue.total} failure{queue.total === 1 ? "" : "s"}</span>
          <div>
            <button className="button secondary" disabled={currentPage === 1 || loading} onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}>Prev</button>
            {paginationItems(currentPage, queue.total_pages).map((item, index) => item === "..." ? (
              <span key={`ellipsis-${index}`} className="page-pill">...</span>
            ) : (
              <button key={item} className={`pagination-page ${item === currentPage ? "active" : ""}`} disabled={loading} onClick={() => setCurrentPage(item)} type="button">{item}</button>
            ))}
            <button className="button secondary" disabled={currentPage >= queue.total_pages || loading} onClick={() => setCurrentPage((page) => Math.min(queue.total_pages, page + 1))}>Next</button>
          </div>
        </div>
      </section>
    </div>
  );
}

function FailureTableLoadingRows() {
  return <>{Array.from({ length: 4 }, (_, row) => (
    <tr className="ftw-table-skeleton-row" key={row}>
      {Array.from({ length: 7 }, (_, column) => <td key={column}><Skeleton className={column === 2 ? "wide" : ""} /></td>)}
    </tr>
  ))}</>;
}

function FailureSummaryCard({ label, tone, value }: { label: string; tone: "danger" | "warn" | "info" | "ready"; value: number }) {
  return <div className={`ftw-failure-summary-card summary-${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

function FTWilliamsFailureQueueRow({
  detailsLoading,
  dismissing,
  expanded,
  item,
  onDismiss,
  onToggleDetails,
}: {
  detailsLoading: boolean;
  dismissing: boolean;
  expanded: boolean;
  item: FTWilliamsFailureQueueSummary;
  onDismiss: (item: FTWilliamsFailureQueueSummary) => void;
  onToggleDetails: (item: FTWilliamsFailureQueueSummary) => void;
}) {
  const displayName = formatFilingDisplayName(item.filing_name);
  const planIdentity = item.company_employer_id && item.plan_number
    ? `${item.company_employer_id} / ${item.plan_number}`
    : item.customer_id && item.plan_id
      ? `${item.customer_id} / ${item.plan_id}`
      : item.ftw_customer_id && item.ftw_plan_id
        ? `FTW ${item.ftw_customer_id} / ${item.ftw_plan_id}`
        : "Plan pending";
  return (
    <tr className="ftw-failure-compact-row">
      <td>
        <Link className="dashboard-filing-cell" to={`/filings/${item.filing_id}`}>
          <FileText size={22} />
          <span><strong>{displayName}</strong><small>{item.year ? `Plan year ${item.year}` : "Plan year pending"}</small></span>
        </Link>
      </td>
      <td><span className={`ftw-failure-type type-${ftwFailureTypeClass(item.failure_type)}`}>{failureTypeLabels[item.failure_type]}</span></td>
      <td>
        <div className="ftw-failure-reason-cell">
          <strong>{item.short_reason}</strong>
          {item.issue_groups.length ? <div className="ftw-failure-groups">{item.issue_groups.map((group) => <span key={group.label}>{group.label} × {group.count}</span>)}</div> : null}
        </div>
      </td>
      <td><div className="ftw-history-plan"><strong>{planIdentity}</strong><small>{item.plan_name || item.sponsor_name || "FT Williams plan details"}</small></div></td>
      <td><div className="ftw-history-fields"><strong>{item.issue_count}</strong><small>{item.issue_count === 1 ? "issue" : "issues"} · {item.attempted_field_count} attempted</small></div></td>
      <td><div className="dashboard-uploaded-on"><span>{shortDate(item.failed_at)}</span><small>{shortTime(item.failed_at)}</small></div></td>
      <td>
        <div className="ftw-failure-actions">
          <button className="button secondary ftw-failure-detail-button" disabled={detailsLoading} onClick={() => onToggleDetails(item)} type="button">
            {detailsLoading ? "Loading..." : expanded ? "Hide details" : "View details"} {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
          <Link className="button danger ftw-failure-review-button" to={`/filings/${item.filing_id}`}>Review / Retry <Eye size={15} /></Link>
          {item.can_dismiss !== false ? <button className="button secondary" disabled={dismissing} onClick={() => onDismiss(item)} type="button">{dismissing ? "Dismissing..." : "Dismiss"}</button> : null}
        </div>
      </td>
    </tr>
  );
}

function FilterDropdown({ icon, label, onChange, options, value }: {
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
    <div className="filter-dropdown" ref={ref}>
      <button className="filter-dropdown-trigger" type="button" onClick={() => setOpen((current) => !current)} aria-expanded={open}>
        {icon ? <span className="filter-dropdown-icon">{icon}</span> : null}
        <span className="filter-dropdown-label">{label}</span><strong>{selected.label}</strong><ChevronDown size={16} />
      </button>
      {open ? <div className="filter-dropdown-menu">{options.map((option) => (
        <button className={option.value === value ? "selected" : ""} key={option.value} type="button" onClick={() => { onChange(option.value); setOpen(false); }}>
          <span>{option.label}</span>{option.value === value ? <Check size={16} /> : null}
        </button>
      ))}</div> : null}
    </div>
  );
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
