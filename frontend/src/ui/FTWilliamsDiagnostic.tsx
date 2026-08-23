import { summarizeFTWilliamsMessage } from "../utils";
import type { FTWilliamsEditCheckIssue, FTWilliamsOperationDiagnostic } from "../types";

export function FTWilliamsDiagnostic({
  errorCode,
  editCheckIssues = [],
  message,
  operations = [],
  technicalDetails,
}: {
  errorCode?: string | null;
  editCheckIssues?: FTWilliamsEditCheckIssue[];
  message?: string | null;
  operations?: FTWilliamsOperationDiagnostic[];
  technicalDetails?: string | null;
}) {
  if (!message?.trim() && !editCheckIssues.length) return null;
  const normalizedMessage = message?.trim() || "FT Williams Edit Checks require corrections.";
  const summary = summarizeFTWilliamsMessage(normalizedMessage);
  const hasMore = summary !== normalizedMessage.replace(/\s+/g, " ").trim() || Boolean(errorCode || technicalDetails || operations.length);

  return (
    <div className="ftw-diagnostic">
      <p>{summary}</p>
      {editCheckIssues.length ? (
        <section className="ftw-edit-check-summary" aria-label="FT Williams Edit Check issues">
          <strong>What needs fixing ({editCheckIssues.length})</strong>
          <div className="ftw-edit-check-list">
            {editCheckIssues.map((issue, index) => {
              const scheduleName = [
                issue.schedule_desc,
                issue.schedule_seq_no ? `Schedule A #${issue.schedule_seq_no}` : null,
              ].filter(Boolean).join(" · ");
              return (
                <article className="ftw-edit-check-issue" key={`${issue.code}-${issue.schedule_seq_no || "form"}-${index}`}>
                  <div>
                    <span className="ftw-edit-check-code">{issue.code}</span>
                    <strong>{issue.field_label || "FT Williams field"}</strong>
                  </div>
                  {scheduleName ? <small>{scheduleName}</small> : null}
                  <p>{issue.message}</p>
                  {issue.current_value ? <small><b>Current value:</b> {issue.current_value}</small> : null}
                  {issue.correction ? <small><b>Fix:</b> {issue.correction}</small> : null}
                </article>
              );
            })}
          </div>
        </section>
      ) : null}
      {hasMore ? (
        <details>
          <summary>Technical details</summary>
          <div className="ftw-diagnostic-details">
            {errorCode ? <p><strong>Error code:</strong> {errorCode}</p> : null}
            {technicalDetails ? <p>{technicalDetails}</p> : null}
            <p>{normalizedMessage}</p>
            {operations.map((operation, index) => (
              <div className="ftw-operation-diagnostic" key={`${operation.operation}-${index}`}>
                <strong>{operation.operation}</strong>
                <span>{operation.outcome_code.replaceAll("_", " ")}</span>
                <small>
                  HTTP {operation.http_status ?? "none"}
                  {operation.elapsed_ms != null ? ` · ${operation.elapsed_ms} ms` : ""}
                  {operation.request_id ? ` · Request ${operation.request_id}` : ""}
                </small>
                {operation.error_code || operation.error_description ? (
                  <p>{[operation.error_code, operation.error_description].filter(Boolean).join(": ")}</p>
                ) : null}
                {operation.response_excerpt ? <pre>{operation.response_excerpt}</pre> : null}
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
