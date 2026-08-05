import {
  Activity,
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  Eye,
  FileText,
  Search,
  SlidersHorizontal,
  XCircle,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "../router";
import { listFTWilliamsHistory } from "../api";
import type { FTWilliamsHistoryItem, FTWilliamsHistoryRange } from "../types";
import { formatFilingDisplayName } from "../utils";

type HistoryStatusFilter = "ALL" | "success" | "failed" | "info";
type HistoryActionFilter =
  | "ALL"
  | "PREVIEW"
  | "CURRENT_QUERY"
  | "UPDATE_SENT"
  | "UPDATE_FAILED"
  | "SCHEDULE_A_MATCHED"
  | "PLAN_MATCH_SAVED";

export function FTWilliamsActivityPage() {
  const [history, setHistory] = useState<FTWilliamsHistoryItem[]>([]);
  const [message, setMessage] = useState("");
  const [range, setRange] = useState<FTWilliamsHistoryRange>("7d");
  const [statusFilter, setStatusFilter] = useState<HistoryStatusFilter>("ALL");
  const [actionFilter, setActionFilter] = useState<HistoryActionFilter>("ALL");
  const [search, setSearch] = useState("");
  const [rowsLimit, setRowsLimit] = useState(10);
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const result = await listFTWilliamsHistory(range);
        if (!active) return;
        setHistory(result.items);
        setMessage("");
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : "Could not load FT Williams activity");
      }
    }
    load();
    return () => {
      active = false;
    };
  }, [range]);

  const filteredHistory = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return history.filter((item) => {
      const haystack = [
        item.filing_name,
        item.action_label,
        item.status,
        item.company_employer_id,
        item.plan_number,
        item.plan_name,
        item.sponsor_name,
        item.customer_id,
        item.plan_id,
        item.ftw_customer_id,
        item.ftw_plan_id,
        item.year,
      ].join(" ").toLowerCase();
      return (
        (!needle || haystack.includes(needle)) &&
        (statusFilter === "ALL" || item.status === statusFilter) &&
        (actionFilter === "ALL" || historyActionKey(item) === actionFilter)
      );
    });
  }, [actionFilter, history, search, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredHistory.length / rowsLimit));
  const pageStartIndex = filteredHistory.length ? (currentPage - 1) * rowsLimit : 0;
  const pageEndIndex = Math.min(pageStartIndex + rowsLimit, filteredHistory.length);
  const visibleHistory = filteredHistory.slice(pageStartIndex, pageEndIndex);

  useEffect(() => {
    setCurrentPage(1);
  }, [actionFilter, range, rowsLimit, search, statusFilter]);

  useEffect(() => {
    setCurrentPage((page) => Math.min(page, totalPages));
  }, [totalPages]);

  return (
    <div className="dashboard-page dashboard-v3 ftw-activity-page">
      <header className="dashboard-v2-hero">
        <div>
          <span className="eyebrow">FT Williams Activity</span>
          <h1 className="page-title">FT Williams Activity</h1>
          <p>Search, filter, and review FT Williams actions performed through this dashboard.</p>
        </div>
      </header>

      {message ? <div className="dashboard-message card">{message}</div> : null}

      <section className="dashboard-table-panel card">
        <div className="dashboard-table-head ftw-activity-head">
          <div>
            <h2>Activity History</h2>
            <p>{filteredHistory.length} matching action{filteredHistory.length === 1 ? "" : "s"} in the selected range.</p>
          </div>
          <div className="dashboard-table-controls">
            <label className="dashboard-search ftw-activity-search">
              <Search size={17} />
              <span>Search</span>
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filing, plan, EIN, PN..." />
            </label>
            <FilterDropdown
              icon={<Calendar size={16} />}
              label="Range"
              value={range}
              options={[
                { value: "1d", label: "1 Day" },
                { value: "7d", label: "7 Days" },
                { value: "30d", label: "1 Month" },
              ]}
              onChange={(value) => setRange(value as FTWilliamsHistoryRange)}
            />
            <FilterDropdown
              label="Status"
              value={statusFilter}
              options={[
                { value: "ALL", label: "All statuses" },
                { value: "success", label: "Success" },
                { value: "failed", label: "Failed" },
                { value: "info", label: "Info" },
              ]}
              onChange={(value) => setStatusFilter(value as HistoryStatusFilter)}
            />
            <FilterDropdown
              label="Action"
              value={actionFilter}
              options={[
                { value: "ALL", label: "All actions" },
                { value: "PREVIEW", label: "Preview" },
                { value: "CURRENT_QUERY", label: "Current query" },
                { value: "UPDATE_SENT", label: "Update sent" },
                { value: "UPDATE_FAILED", label: "Update failed" },
                { value: "SCHEDULE_A_MATCHED", label: "Schedule A matched" },
                { value: "PLAN_MATCH_SAVED", label: "Plan match saved" },
              ]}
              onChange={(value) => setActionFilter(value as HistoryActionFilter)}
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
              setStatusFilter("ALL");
              setActionFilter("ALL");
            }}>
              <SlidersHorizontal size={16} /> Reset
            </button>
          </div>
        </div>

        <div className="dashboard-table-wrap">
          <table className="dashboard-filings-table ftw-history-table ftw-activity-table">
            <thead>
              <tr>
                <th>Filing</th>
                <th>Action</th>
                <th>Plan</th>
                <th>FTW IDs</th>
                <th>Fields</th>
                <th>Date</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {visibleHistory.map((item) => (
                <FTWilliamsActivityRow item={item} key={item.id || `${item.filing_id}-${item.created_at}-${item.action}`} />
              ))}
            </tbody>
          </table>
        </div>

        {!visibleHistory.length ? (
          <div className="empty-state ftw-history-empty">
            <Activity size={18} /> No FT Williams activity matches these filters.
          </div>
        ) : null}

        <div className="dashboard-table-footer">
          <span>
            Showing {filteredHistory.length ? pageStartIndex + 1 : 0}-{pageEndIndex} of {filteredHistory.length} action{filteredHistory.length === 1 ? "" : "s"}
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

function FTWilliamsActivityRow({ item }: { item: FTWilliamsHistoryItem }) {
  const planIdentity = item.company_employer_id && item.plan_number
    ? `${item.company_employer_id} / ${item.plan_number}`
    : item.customer_id && item.plan_id
      ? `${item.customer_id} / ${item.plan_id}`
      : "Plan pending";
  const ftwIdentity = item.ftw_customer_id && item.ftw_plan_id ? `${item.ftw_customer_id} / ${item.ftw_plan_id}` : "Pending";
  const displayName = formatFilingDisplayName(item.filing_name);
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
        <span className={`ftw-history-status status-${item.status}`}>
          {item.status === "failed" ? <XCircle size={14} /> : item.status === "success" ? <CheckCircle2 size={14} /> : <Activity size={14} />}
          {item.action_label}
        </span>
        {item.error_message ? <small className="ftw-history-error">{item.error_message}</small> : null}
      </td>
      <td>
        <div className="ftw-history-plan">
          <strong>{planIdentity}</strong>
          <small>{item.plan_name || item.sponsor_name || "FT Williams plan details"}</small>
        </div>
      </td>
      <td>
        <div className="ftw-history-plan">
          <strong>{ftwIdentity}</strong>
          <small>FTW customer / plan</small>
        </div>
      </td>
      <td>
        <div className="ftw-history-fields">
          <strong>{typeof item.updated_field_count === "number" ? item.updated_field_count : "-"}</strong>
          <small>{item.action.includes("UPDATE") ? "sent / attempted" : "prepared"}</small>
        </div>
      </td>
      <td>
        <div className="dashboard-uploaded-on">
          <span>{shortDate(item.created_at)}</span>
          <small>{shortTime(item.created_at)}</small>
        </div>
      </td>
      <td>
        <Link className="button secondary ftw-history-detail-button" to={`/filings/${item.filing_id}`}>
          View <Eye size={15} />
        </Link>
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

function historyActionKey(item: FTWilliamsHistoryItem): HistoryActionFilter {
  if (item.action === "FTWILLIAMS_UPDATE_SENT") return "UPDATE_SENT";
  if (item.action === "FTWILLIAMS_UPDATE_FAILED") return "UPDATE_FAILED";
  if (item.action === "FTWILLIAMS_SCHEDULE_A_MATCH_SELECTED") return "SCHEDULE_A_MATCHED";
  if (item.action === "FTWILLIAMS_MANUAL_MATCH_SAVED") return "PLAN_MATCH_SAVED";
  if (item.action === "FTWILLIAMS_REVIEW_PREPARED" && item.action_label === "Current data queried") return "CURRENT_QUERY";
  if (item.action === "FTWILLIAMS_REVIEW_PREPARED") return "PREVIEW";
  return "ALL";
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
