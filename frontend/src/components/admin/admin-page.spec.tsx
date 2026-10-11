import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { AdminPage } from './admin-page';

const authState = vi.hoisted(() => ({
  current: {
    authenticated: true,
    isAdmin: true,
    user: { id: 'u1', email: 'admin@example.com' },
    isLoading: false,
  },
}));

const statsState = vi.hoisted(() => ({
  current: {
    data: undefined as Record<string, number> | undefined,
    error: null as Error | null,
    refetch: vi.fn(),
  },
}));

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));

vi.mock('@/hooks/use-auth', () => ({
  useAuth: () => authState.current,
  describeAccountError: () => 'нет связи с сервером — попробуйте позже',
}));

vi.mock('@/hooks/use-admin', () => ({
  useAdminStats: () => statsState.current,
}));

vi.mock('./places-panel', () => ({
  PlacesPanel: () => <div data-testid="places-panel" />,
}));
vi.mock('./users-panel', () => ({
  UsersPanel: () => <div data-testid="users-panel" />,
}));

beforeEach(() => {
  statsState.current = {
    data: undefined,
    error: null,
    refetch: vi.fn(),
  };
  authState.current.isLoading = false;
});

describe('AdminPage — the dashboard header admits failure', () => {
  it('shows a loading state instead of a blank screen while auth resolves', () => {
    authState.current.isLoading = true;
    render(<AdminPage />);

    expect(screen.getByTestId('admin-loading')).toBeInTheDocument();
  });

  it('renders the stat cards when the stats call succeeds', () => {
    statsState.current.data = {
      users: 3,
      admins: 1,
      places: 42,
      visited: 7,
      saved_routes: 2,
    };
    render(<AdminPage />);

    expect(screen.getByText('пользователей')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-stats-error')).not.toBeInTheDocument();
  });

  it('shows a retry instead of silently dropping the header when stats fail', () => {
    statsState.current.error = new Error('offline');
    render(<AdminPage />);

    expect(screen.getByTestId('admin-stats-error')).toBeInTheDocument();
    expect(
      screen.getByText('нет связи с сервером — попробуйте позже')
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('admin-stats-retry'));
    expect(statsState.current.refetch).toHaveBeenCalledTimes(1);
  });
});
