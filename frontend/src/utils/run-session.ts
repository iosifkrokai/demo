/** The id of the current guide run («полный прогон»). */

const newRunId = (): string =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

let current: string | null = null;

/** Begin a new run — a first query on a new topic — and return its id. */
export const startRunSession = (): string => {
  current = newRunId();
  return current;
};

/** The run in progress, minting one on first use (a reload, a restored route). */
export const currentRunSession = (): string => {
  current ??= newRunId();
  return current;
};
