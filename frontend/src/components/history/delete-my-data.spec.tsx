import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';

import type { DeleteMyDataOutcome } from '@/hooks/use-client-routes';

import { DeleteMyData } from './delete-my-data';

afterEach(cleanup);

describe('DeleteMyData', () => {
  it('asks for confirmation before deleting anything', async () => {
    const onDelete = vi.fn(
      async (): Promise<DeleteMyDataOutcome> => ({
        ok: true,
        message: 'данные удалены',
      })
    );

    render(<DeleteMyData onDelete={onDelete} />);

    // The first click only opens the consequence; nothing is deleted yet.
    fireEvent.click(screen.getByTestId('delete-my-data-start'));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/Отменить нельзя/)).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-my-data-confirm'));
    });

    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('delete-my-data-status')).toHaveTextContent(
      'данные удалены'
    );
  });

  it('can be cancelled without deleting', () => {
    const onDelete = vi.fn();
    render(<DeleteMyData onDelete={onDelete} />);

    fireEvent.click(screen.getByTestId('delete-my-data-start'));
    fireEvent.click(screen.getByTestId('delete-my-data-cancel'));

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByTestId('delete-my-data-confirm')).toBeNull();
  });

  it('does not claim deletion when the server cannot be reached', async () => {
    const onDelete = vi.fn(
      async (): Promise<DeleteMyDataOutcome> => ({
        ok: false,
        storageUnavailable: true,
        serverDeleted: false,
        message:
          'сервер недоступен — данные на сервере могли остаться; локальные копии удалены',
      })
    );

    render(<DeleteMyData onDelete={onDelete} />);
    fireEvent.click(screen.getByTestId('delete-my-data-start'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-my-data-confirm'));
    });

    const status = screen.getByTestId('delete-my-data-status');
    expect(status).toHaveTextContent('данные на сервере могли остаться');
    expect(status).not.toHaveTextContent('данные удалены');
  });
});
