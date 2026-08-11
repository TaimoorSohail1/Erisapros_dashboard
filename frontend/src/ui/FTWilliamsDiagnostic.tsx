import { summarizeFTWilliamsMessage } from "../utils";

export function FTWilliamsDiagnostic({ message }: { message?: string | null }) {
  if (!message?.trim()) return null;
  const summary = summarizeFTWilliamsMessage(message);
  const hasMore = summary !== message.replace(/\s+/g, " ").trim();

  return (
    <div className="ftw-diagnostic">
      <p>{summary}</p>
      {hasMore ? (
        <details>
          <summary>View details</summary>
          <div className="ftw-diagnostic-details">{message}</div>
        </details>
      ) : null}
    </div>
  );
}
