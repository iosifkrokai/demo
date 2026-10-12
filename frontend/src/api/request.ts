/** The one place a request to the agent is shaped.
 *
 * Every `src/api/*` module used to build its own `AGENT_URL`, its own headers and
 * its own JSON handling — six copies of the same three lines, each free to drift.
 * They now share this module; a module that needs a typed error of its own (the
 * account codes, a services reason) catches `ApiError` and re-wraps it, which is
 * the only thing a shared layer cannot know.
 */

/** Same-origin by default; VITE_AGENT_URL only points at a remote agent. */
export const AGENT_URL =
  (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

/** A failed agent call: the status and the parsed body, still untyped. */
export class ApiError extends Error {
  readonly status: number | null;
  readonly body: unknown;

  constructor(
    message: string,
    status: number | null = null,
    body: unknown = null
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/**
 * `fetch` an agent path, carrying the session cookie, and read it back as JSON.
 *
 * Throws `ApiError` on a transport failure, a non-2xx answer or a body that is
 * not JSON. An `AbortError` is re-thrown untouched so a cancelled query stays a
 * cancellation rather than becoming a failure the UI would show.
 */
export async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body != null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(`${AGENT_URL}${path}`, {
      ...init,
      headers,
      credentials: 'include',
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    throw new ApiError('агент недоступен — попробуйте позже', null);
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new ApiError(
      `агент ответил ${response.status}`,
      response.status,
      body
    );
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError('агент ответил не JSON', response.status);
  }
}
