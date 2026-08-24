import { useEffect, useSyncExternalStore } from "react";
import { listFTWilliamsFailureQueue, listFTWilliamsHistory } from "../api";
import type { FTWilliamsFailureQueueItem, FTWilliamsHistoryItem, FTWilliamsHistoryRange } from "../types";
import { createSharedPollingResource } from "./sharedPollingResource";

const failuresResource = createSharedPollingResource<FTWilliamsFailureQueueItem[]>({
  initialData: [],
  load: async () => (await listFTWilliamsFailureQueue()).items,
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
