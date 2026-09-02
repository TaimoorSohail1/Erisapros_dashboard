import { useEffect, useSyncExternalStore } from "react";
import { listFTWilliamsFailureNotifications, listFTWilliamsHistory } from "../api";
import type { FTWilliamsFailureNotificationResponse, FTWilliamsHistoryItem, FTWilliamsHistoryRange } from "../types";
import { createSharedPollingResource } from "./sharedPollingResource";

const failuresResource = createSharedPollingResource<FTWilliamsFailureNotificationResponse>({
  initialData: {
    total: 0,
    counts: { active: 0, needs_retry: 0, needs_data_fix: 0, needs_plan_match: 0, needs_service_check: 0 },
    items: [],
  },
  load: listFTWilliamsFailureNotifications,
  pollMs: 60_000,
});

const historyResources = new Map<FTWilliamsHistoryRange, ReturnType<typeof createSharedPollingResource<FTWilliamsHistoryItem[]>>>();

function getHistoryResource(range: FTWilliamsHistoryRange) {
  const existing = historyResources.get(range);
  if (existing) return existing;
  const resource = createSharedPollingResource<FTWilliamsHistoryItem[]>({
    initialData: [],
    load: async () => (await listFTWilliamsHistory(range)).items,
    pollMs: 5 * 60_000,
  });
  historyResources.set(range, resource);
  return resource;
}

export function useFTWilliamsFailures() {
  const snapshot = useSyncExternalStore(failuresResource.subscribe, failuresResource.getSnapshot);
  useEffect(() => failuresResource.acquirePolling(), []);
  return snapshot;
}

export function refreshFTWilliamsFailures() {
  return failuresResource.refresh({ force: true });
}

export function useFTWilliamsHistory(enabled: boolean, range: FTWilliamsHistoryRange = "30d") {
  const resource = getHistoryResource(range);
  const snapshot = useSyncExternalStore(resource.subscribe, resource.getSnapshot);
  useEffect(() => {
    if (!enabled) return;
    return resource.acquirePolling();
  }, [enabled, resource]);
  return snapshot;
}

export const useFTWilliamsFailureNotifications = useFTWilliamsFailures;
