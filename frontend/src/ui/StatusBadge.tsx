import type { FilingStatus } from "../types";

export function StatusBadge({ status }: { status: FilingStatus }) {
  const cls = status === "NEEDS_REVIEW" || status === "WAITING_FOR_WORKSHEET" || status === "WAITING_FOR_SCHEDULE_A" ? "warn" : status === "READY_FOR_APPROVAL" || status === "APPROVED" ? "ready" : status === "FAILED" || status === "REJECTED" || status === "DELETED" ? "fail" : status === "QUEUED" || status === "EXTRACTING" || status === "QUERYING_FTW_CURRENT" || status === "SUPERSEDED" ? "info" : "";
  return <span className={"badge " + cls}>{statusLabel(status)}</span>;
}

function statusLabel(status: FilingStatus) {
  if (status === "WAITING_FOR_WORKSHEET") return "WAITING FOR PLAN WORKSHEET";
  if (status === "WAITING_FOR_SCHEDULE_A") return "WAITING FOR SCHEDULE A";
  if (status === "QUERYING_FTW_CURRENT") return "QUERYING FTW CURRENT";
  if (status === "READY_FOR_APPROVAL") return "READY FOR APPROVAL";
  return status.replaceAll("_", " ");
}
