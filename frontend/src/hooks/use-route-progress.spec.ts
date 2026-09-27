import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, renderHook, waitFor } from '@testing-library/react';

import { POLL_INTERVAL_MS, useRouteProgress } from './use-route-progress';

/**
 * The panel asks the pipeline where it got to, and shows what comes back.
 *
 * What this file pins:
 *
 * 1. **A stage comes from the server or not at all.** An unknown id (404) or a
 *    failed tick yields `null`, and the panel keeps its own truthful sentence;
 *    the client never invents a stage to fill the silence.
 * 2. **The codes are passed through, not guessed.** An unexpected code is not
 *    rendered as some neighbouring stage.
 * 3. **Polling stops.** A finished request must not leave a timer running.
 */

const answer = (stage: string, done = false) => ({
  ok: true,
  status: 200,
  json: async () => ({ stage, done, failed: false, elapsed_ms: 1234 }),
});

const notFound = { ok: false, status: 404, json: async () => ({ detail: { reason: 'unknown_progress_id' } }) };

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('useRouteProgress', () => {
  it('показывает стадию, которую назвал конвейер', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => answer('searching_places')));

    const { result } = renderHook(() => useRouteProgress('id-1', { enabled: true }));

    await waitFor(() => expect(result.current).toBe('searching_places'));
  });

  it('ведёт стадии по мере продвижения конвейера', async () => {
    // Real timers here: the point is that the panel follows the pipeline over
    // time, and `waitFor` is the honest way to observe that.
    const stages = ['interpreting_request', 'measuring_legs', 'done'] as const;
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => answer(stages[Math.min(call++, stages.length - 1)] ?? 'done'))
    );

    const { result } = renderHook(() => useRouteProgress('id-2', { enabled: true }));

    await waitFor(() => expect(result.current).toBe('interpreting_request'));
    await waitFor(() => expect(result.current).toBe('measuring_legs'), { timeout: 5000 });
    await waitFor(() => expect(result.current).toBe('done'), { timeout: 5000 });
  });

  it('после «готово» больше не опрашивает сервер', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => answer('done', true));
    vi.stubGlobal('fetch', fetchMock);

    renderHook(() => useRouteProgress('id-3', { enabled: true }));

    await vi.advanceTimersByTimeAsync(0);
    const afterFirst = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 4);

    expect(fetchMock.mock.calls.length).toBe(afterFirst);
  });

  it('незнакомый id — это молчание, а не выдуманная стадия', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => notFound));

    const { result } = renderHook(() => useRouteProgress('id-4', { enabled: true }));

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });

  it('неизвестный код стадии не превращается в соседнюю стадию', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => answer('polishing_the_spires')));

    const { result } = renderHook(() => useRouteProgress('id-5', { enabled: true }));

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });

  it('сбой опроса не притворяется пустой стадией', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('network');
    }));

    const { result } = renderHook(() => useRouteProgress('id-6', { enabled: true }));

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });

  it('без запроса не опрашивает вовсе', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useRouteProgress(null, { enabled: false }));

    expect(result.current).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
