import { useEffect, useSyncExternalStore } from "react";
import { listFTWilliamsFailureQueue, listFTWilliamsHistory } from "../api";
import type { FTWilliamsFailureQueueItem, FTWilliamsHistoryItem } from "../types";
import { createSharedPollingResource } from "./sharedPollingResource";

const failuresResource = createSharedPollingResource<FTWilliamsFailureQueueItem[]>({
  initialData: [],
  load: async () => (await listFTWilliamsFailureQueue()).items,
  pollMs: 60_000,
});

const historyResource = createSharedPollingResource<FTWilliamsHistoryItem[]>({
  initialData: [],
  load: async () => (await listFTWilliamsHistory("30d")).items,
  pollMs: 5 * 60_000,
});

export function useFTWilliamsFailures() {
  const snapshot = useSyncExternalStore(failuresResource.subscribe, failuresResource.getSnapshot);
  useEffect(() => failuresResource.acquirePolling(), []);
  return snapshot;
}

export function useFTWilliamsHistory(enabled: boolean) {
  const snapshot = useSyncExternalStore(historyResource.subscribe, historyResource.getSnapshot);
  useEffect(() => {
    if (enabled) void historyResource.refresh().catch(() => undefined);
  }, [enabled]);
  return snapshot;
}
