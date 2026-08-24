export interface SharedPollingSnapshot<T> {
  data: T;
  error: string;
  loading: boolean;
  updatedAt: number;
}

interface SharedPollingOptions<T> {
  initialData: T;
  load: () => Promise<T>;
  pollMs: number;
  freshMs?: number;
  now?: () => number;
  setIntervalFn?: (callback: () => void, timeout: number) => unknown;
  clearIntervalFn?: (handle: unknown) => void;
}

export function createSharedPollingResource<T>({
  initialData,
  load,
  pollMs,
  freshMs = 5_000,
  now = Date.now,
  setIntervalFn = (callback, timeout) => globalThis.setInterval(callback, timeout),
  clearIntervalFn = (handle) => globalThis.clearInterval(handle as ReturnType<typeof setInterval>),
}: SharedPollingOptions<T>) {
  // A resource with updatedAt === 0 has never completed a request. Keep it in a
  // loading state so consumers cannot mistake initialData for a real empty response.
  let snapshot: SharedPollingSnapshot<T> = { data: initialData, error: "", loading: true, updatedAt: 0 };
  let inFlight: Promise<T> | null = null;
  let pollingConsumers = 0;
  let timer: unknown = null;
  const listeners = new Set<() => void>();

  function publish(next: Partial<SharedPollingSnapshot<T>>) {
    snapshot = { ...snapshot, ...next };
    listeners.forEach((listener) => listener());
  }

  async function refresh({ force = false }: { force?: boolean } = {}): Promise<T> {
    if (inFlight) return inFlight;
    if (!force && snapshot.updatedAt && now() - snapshot.updatedAt < freshMs) return snapshot.data;
    publish({ loading: true });
    inFlight = load()
      .then((data) => {
        publish({ data, error: "", loading: false, updatedAt: now() });
        return data;
      })
      .catch((error) => {
        publish({
          error: error instanceof Error ? error.message : "Request failed",
          loading: false,
        });
        throw error;
      })
      .finally(() => {
        inFlight = null;
      });
    return inFlight;
  }

  function acquirePolling() {
    pollingConsumers += 1;
    if (pollingConsumers === 1) {
      void refresh().catch(() => undefined);
      timer = setIntervalFn(() => void refresh({ force: true }).catch(() => undefined), pollMs);
    }
    let released = false;
    return () => {
      if (released) return;
      released = true;
      pollingConsumers = Math.max(0, pollingConsumers - 1);
      if (!pollingConsumers && timer !== null) {
        clearIntervalFn(timer);
        timer = null;
      }
    };
  }

  return {
    acquirePolling,
    getSnapshot: () => snapshot,
    refresh,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
