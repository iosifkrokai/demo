import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import { RootComponent } from './root-component';

/** The pathname the mocked router reports; each test sets it. */
const pathname = vi.hoisted(() => ({ current: '/directions' }));

vi.mock('@tanstack/react-router', () => ({
  Outlet: () => <div data-testid="outlet" />,
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { pathname: string } }) => unknown;
  }) => select({ location: { pathname: pathname.current } }),
}));

vi.mock('./account/account-bar', () => ({
  AccountBar: () => <div data-testid="account-bar" />,
}));

beforeEach(() => {
  pathname.current = '/directions';
});

describe('RootComponent — where the account bar belongs', () => {
  it('keeps the bar over the map', () => {
    pathname.current = '/directions';
    render(<RootComponent />);

    expect(screen.getByTestId('account-bar')).toBeInTheDocument();
  });

  it.each(['/login', '/register'])(
    'hides the bar on the auth route %s',
    (route) => {
      pathname.current = route;
      render(<RootComponent />);

      expect(screen.queryByTestId('account-bar')).not.toBeInTheDocument();
    }
  );

  it.each(['/admin', '/visited'])(
    'hides the bar on the full page %s',
    (route) => {
      pathname.current = route;
      render(<RootComponent />);

      expect(screen.queryByTestId('account-bar')).not.toBeInTheDocument();
    }
  );
});
