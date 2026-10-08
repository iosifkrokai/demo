import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  AccountApiError,
  adminListUsers,
  getAuthState,
  isStorageDown,
  listVisited,
  registerAccount,
} from './account';

const CLIENT_ID = '33333333-3333-4333-8333-333333333333';

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];
const mockFetch = (impl: (...args: FetchArgs) => Promise<Response>) =>
  vi.fn(impl);

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('grodno-client-id', CLIENT_ID);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('accounts api', () => {
  it('reads /auth/me, anonymous included', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse({ authenticated: false, user: null }))
    );
    await expect(getAuthState()).resolves.toEqual({
      authenticated: false,
      user: null,
    });
  });

  it('registers, sends the client id and the session cookie, returns the user', async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse(
        {
          id: 'u1',
          email: 'a@b.co',
          display_name: 'Турист',
          role: 'user',
          created_at: '2026-10-07T00:00:00Z',
        },
        201
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = await registerAccount({
      email: 'a@b.co',
      password: 'secret123',
    });

    expect(user.role).toBe('user');
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/auth/register');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('include');
    expect((init?.headers as Headers).get('X-Client-Id')).toBe(CLIENT_ID);
  });

  it('turns a reason code into a typed error', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse({ reason: 'email_taken' }, 409))
    );

    await expect(
      registerAccount({ email: 'a@b.co', password: 'secret123' })
    ).rejects.toMatchObject({
      name: 'AccountApiError',
      code: 'email_taken',
      status: 409,
    });
  });

  it('reads a storage outage as «not a real answer»', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () =>
        jsonResponse({ reason: 'storage_unavailable' }, 503)
      )
    );

    const error = await getAuthState().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AccountApiError);
    expect(isStorageDown(error)).toBe(true);
  });

  it('keeps visited_at and the place payload', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () =>
        jsonResponse({
          items: [
            {
              place_id: 7,
              name: 'Старый замок',
              category: 'замок',
              town: 'Гродно',
              lat: 53.67,
              lon: 23.82,
              visit_minutes: 90,
              visited_at: '2026-10-07T10:00:00Z',
            },
          ],
          count: 1,
        })
      )
    );

    const visited = await listVisited();
    expect(visited.count).toBe(1);
    expect(visited.items[0]).toMatchObject({
      place_id: 7,
      name: 'Старый замок',
      visited_at: '2026-10-07T10:00:00Z',
    });
  });

  it('builds the admin users query and reads the counts', async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse({
        items: [
          {
            id: 'u1',
            email: 'admin@x.y',
            display_name: null,
            role: 'admin',
            created_at: '2026-10-07T00:00:00Z',
            client_id: null,
            last_login_at: null,
            saved_routes: 2,
            visited: 5,
          },
        ],
        total: 1,
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const list = await adminListUsers({ q: 'admin', limit: 10 });
    expect(list.total).toBe(1);
    expect(list.items[0]).toMatchObject({
      role: 'admin',
      saved_routes: 2,
      visited: 5,
    });

    const [url] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain('/admin/users?');
    expect(String(url)).toContain('q=admin');
    expect(String(url)).toContain('limit=10');
  });
});
