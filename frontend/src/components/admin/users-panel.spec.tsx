import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { UsersPanel } from './users-panel';

const refetch = vi.hoisted(() => vi.fn());

/** When set, `useAdminUsers` reports failure — exercising the error path. */
const errorOverride = vi.hoisted(() => ({ value: null as Error | null }));

vi.mock('@/hooks/use-admin', () => ({
  useAdminUsers: () =>
    errorOverride.value
      ? {
          items: [],
          total: 0,
          isLoading: false,
          error: errorOverride.value,
          refetch,
        }
      : {
          items: [],
          total: 0,
          isLoading: false,
          error: null,
          refetch,
        },
  usePatchUser: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteUser: () => ({ mutate: vi.fn(), isPending: false }),
}));

beforeEach(() => {
  refetch.mockClear();
  errorOverride.value = null;
});

describe('UsersPanel — errors are not empty results', () => {
  it('renders the empty state when the request succeeds with no rows', () => {
    render(<UsersPanel enabled currentUserId={null} />);

    expect(screen.getByText('ничего не найдено')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-users-error')).not.toBeInTheDocument();
  });

  it('shows an error with a retry instead of «ничего не найдено»', () => {
    errorOverride.value = new Error('offline');
    render(<UsersPanel enabled currentUserId={null} />);

    expect(screen.getByTestId('admin-users-error')).toBeInTheDocument();
    expect(screen.queryByText('ничего не найдено')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('admin-users-retry'));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
