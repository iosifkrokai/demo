/**
 * The id of the current guide run («полный прогон»).
 *
 * Every request the agent serves for one topic — the first generate, each
 * refinement, and «что по пути» — carries the same id, and the backend forwards
 * it to Langfuse as the session, so one run reads as one thread in the trace UI
 * instead of a scatter of separate traces.
 *
 * The boundary is the topic, not the browser: a generate that is not a
 * refinement starts a new run (see `submitPrompt` in `components/sidebar.tsx`),
 * so clearing the route and asking something else begins a fresh session. Kept
 * in memory rather than localStorage — a run lives for the page, and a reload is
 * honestly a new one.
 */

/**
 * An id for one run. `crypto.randomUUID` needs a secure context; the demo also
 * runs over plain http on a LAN, where it is missing — the fallback keeps the id
 * unique per run without pretending to be a UUID. Mirrors `newProgressId`.
 */
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
