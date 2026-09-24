import type { FTWilliamsFailureQueueSummary, FTWilliamsFailureType } from "./types";

export type { FTWilliamsFailureType } from "./types";

export const failureTypeLabels: Record<FTWilliamsFailureType, string> = {
  NEEDS_RETRY: "Needs retry",
  NEEDS_DATA_FIX: "Needs data fix",
  NEEDS_PLAN_MATCH: "Needs plan match",
  NEEDS_SERVICE_CHECK: "Needs service check",
};

export function classifyFTWilliamsFailure(item: FTWilliamsFailureQueueSummary): FTWilliamsFailureType {
  if (item.failure_type) return item.failure_type;
  const text = `${item.short_reason || ""} ${item.next_action || ""} ${item.review_status || ""}`.toLowerCase();
  if (/(plan|mapping|customer|identifier|match|ein|pn|ftw id|plan id|customer id)/.test(text)) return "NEEDS_PLAN_MATCH";
  if (/(field|xml|form|checkbox|edit check|value|line|schedule|payload|invalid)/.test(text)) return "NEEDS_DATA_FIX";
  if (/(login|session|credential|auth|unauthorized|forbidden|token|permission|network|timeout|connection|service unavailable|gateway|rate limit)/.test(text)) return "NEEDS_SERVICE_CHECK";
  return "NEEDS_RETRY";
}

export function ftwFailureTypeClass(type: FTWilliamsFailureType) {
  return type.toLowerCase().replaceAll("_", "-");
}

export function countFTWilliamsFailureTypes(items: FTWilliamsFailureQueueSummary[]) {
  return items.reduce<Record<FTWilliamsFailureType, number>>(
    (counts, item) => {
      counts[classifyFTWilliamsFailure(item)] += 1;
      return counts;
    },
    {
      NEEDS_RETRY: 0,
      NEEDS_DATA_FIX: 0,
      NEEDS_PLAN_MATCH: 0,
      NEEDS_SERVICE_CHECK: 0,
    },
  );
}
