import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AccountBar } from './account-bar';

const authState = vi.hoisted(() => ({
  current: {
    user: null as { display_name?: string | null; email?: string } | null,
    authenticated: false,
    isAdmin: false,
    isLoading: false,
  },
}));

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string;
    children: React.ReactNode;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('@/hooks/use-auth', () => ({
  useAuth: () => authState.current,
  useLogout: () => ({ mutate: vi.fn(), isPending: false }),
}));

beforeEach(() => {
  authState.current = {
    user: null,
    authenticated: false,
    isAdmin: false,
    isLoading: false,
  };
});

describe('AccountBar', () => {
  it('shows a spinner instead of nothing while the session resolves', () => {
    authState.current.isLoading = true;
    render(<AccountBar />);

    expect(screen.getByTestId('account-bar-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('account-bar')).not.toBeInTheDocument();
  });

  it('offers «Войти» when anonymous', () => {
    render(<AccountBar />);

    expect(screen.getByTestId('account-login')).toBeInTheDocument();
  });

  it('shows the signed-in name', () => {
    authState.current = {
      user: { display_name: 'Аня', email: 'anya@example.com' },
      authenticated: true,
      isAdmin: true,
      isLoading: false,
    };
    render(<AccountBar />);

    expect(screen.getByTestId('account-menu-button')).toHaveTextContent('Аня');
  });
});
