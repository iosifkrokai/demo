import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchThread, ThreadError } from './threads';

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];
const mockFetch = (impl: (...args: FetchArgs) => Promise<Response>) =>
  vi.fn(impl);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the thread journal api', () => {
  it('reads the named run back', async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse({
        session_id: 'run-1',
        created_at: null,
        turns: [
          {
            revision: 0,
            query: 'погулять',
            submission: { ordered_ids: [1, 2] },
            measurement: {
              results: [{ id: 1, name: 'Старый замок', lat: 53.6, lon: 23.8 }],
            },
            created_at: '2026-10-12T10:00:00Z',
          },
        ],
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const journal = await fetchThread('run-1');

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/routes/threads/run-1');
    expect(journal.session_id).toBe('run-1');
    expect(journal.turns).toHaveLength(1);
    expect(journal.turns[0]?.query).toBe('погулять');
    expect(journal.turns[0]?.measurement.results?.[0]?.id).toBe(1);
  });

  it('encodes the run id into the path', async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse({ session_id: 'a b/c', turns: [] })
    );
    vi.stubGlobal('fetch', fetchMock);

    await fetchThread('a b/c');

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/routes/threads/a%20b%2Fc');
  });

  it('refuses a body that carries no turns', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse({}))
    );

    await expect(fetchThread('run-1')).rejects.toBeInstanceOf(ThreadError);
  });

  it('carries the status of a refused read', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse({ reason: 'not_authenticated' }, 401))
    );

    const error = await fetchThread('run-1').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ThreadError);
    expect((error as ThreadError).status).toBe(401);
  });
});
