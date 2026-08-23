import { summarizeFTWilliamsMessage } from "../utils";
import type { FTWilliamsOperationDiagnostic } from "../types";

export function FTWilliamsDiagnostic({
  errorCode,
  message,
  operations = [],
  technicalDetails,
}: {
  errorCode?: string | null;
  message?: string | null;
  operations?: FTWilliamsOperationDiagnostic[];
  technicalDetails?: string | null;
}) {
  if (!message?.trim()) return null;
  const summary = summarizeFTWilliamsMessage(message);
  const hasMore = summary !== message.replace(/\s+/g, " ").trim() || Boolean(errorCode || technicalDetails || operations.length);

  return (
    <div className="ftw-diagnostic">
      <p>{summary}</p>
      {hasMore ? (
        <details>
          <summary>Technical details</summary>
          <div className="ftw-diagnostic-details">
            {errorCode ? <p><strong>Error code:</strong> {errorCode}</p> : null}
            {technicalDetails ? <p>{technicalDetails}</p> : null}
            <p>{message}</p>
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
