import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { VisitedToggle } from './visited-toggle';

const CLIENT_ID = '55555555-5555-4555-8555-555555555555';

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

const wrap = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return Wrapper;
};

const AUTH_ON = {
  authenticated: true,
  user: {
    id: 'u1',
    email: 'tourist@example.com',
    display_name: 'Турист',
    role: 'user',
    created_at: '2026-10-07T00:00:00Z',
  },
};

const PLACE = {
  place_id: 7,
  source_url: 'city:old-castle',
  name: 'Старый замок',
  category: 'замок',
  town: 'Гродно',
  district: null,
  lat: 53.67,
  lon: 23.82,
  visit_minutes: 90,
  opening_hours: null,
  blurb: null,
  fun_fact: null,
  fun_facts: [],
  links: [],
  photo: null,
  ticket_price: null,
};

const stubFetch = (authState: unknown, visited: unknown) => {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url === '/auth/me') return jsonResponse(authState);
      if (url === '/me/visited' && method === 'GET')
        return jsonResponse(visited);
      if (url.startsWith('/me/visited/') && method === 'PUT') {
        return jsonResponse({ ...PLACE, visited_at: '2026-10-07T11:00:00Z' });
      }
      if (url.startsWith('/me/visited/') && method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      return jsonResponse({}, 404);
    }
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
};

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('grodno-client-id', CLIENT_ID);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('VisitedToggle', () => {
  it('tells an anonymous tourist to sign in instead of failing a write', async () => {
    stubFetch({ authenticated: false, user: null }, { items: [], count: 0 });

    render(<VisitedToggle placeId={7} />, { wrapper: wrap() });

    expect(await screen.findByTestId('visited-login-hint')).toBeInTheDocument();
    expect(screen.queryByTestId('visited-toggle-7')).not.toBeInTheDocument();
  });

  it('marks a place visited for a signed-in tourist', async () => {
    const fetchMock = stubFetch(AUTH_ON, { items: [], count: 0 });

    render(<VisitedToggle placeId={7} />, { wrapper: wrap() });

    const button = await screen.findByTestId('visited-toggle-7');
    expect(button).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(button);

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url) === '/me/visited/7' && init?.method === 'PUT'
      );
      expect(put).toBeTruthy();
    });
  });

  it('shows a place as visited when the registry already holds it', async () => {
    stubFetch(AUTH_ON, {
      items: [{ ...PLACE, visited_at: '2026-10-01T00:00:00Z' }],
      count: 1,
    });

    render(<VisitedToggle placeId={7} />, { wrapper: wrap() });

    const button = await screen.findByTestId('visited-toggle-7');
    await waitFor(() => expect(button).toHaveAttribute('aria-pressed', 'true'));
    expect(button).toHaveTextContent('посещено');
  });
});
