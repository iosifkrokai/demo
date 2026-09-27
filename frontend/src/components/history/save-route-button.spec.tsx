import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';

import type { SaveRouteOutcome } from '@/hooks/use-client-routes';

import { SaveRouteButton } from './save-route-button';

afterEach(cleanup);

describe('SaveRouteButton', () => {
  it('saves nothing until the tourist clicks', async () => {
    const onSave = vi.fn(
      async (): Promise<SaveRouteOutcome> => ({
        ok: true,
        storage: 'server',
        id: 'r1',
      })
    );

    render(<SaveRouteButton onSave={onSave} />);
    expect(onSave).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByTestId('save-route-button'));
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(null);
    expect(screen.getByTestId('save-route-status')).toHaveTextContent(
      'маршрут сохранён на сервере'
    );
  });

  it('passes the name the tourist typed', async () => {
    const onSave = vi.fn(
      async (): Promise<SaveRouteOutcome> => ({
        ok: true,
        storage: 'server',
        id: 'r1',
      })
    );

    render(<SaveRouteButton onSave={onSave} />);
    fireEvent.change(screen.getByTestId('save-route-name'), {
      target: { value: 'Вечерняя прогулка' },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('save-route-button'));
    });

    expect(onSave).toHaveBeenCalledWith('Вечерняя прогулка');
  });

  it('reports 503 honestly and never claims the route was saved', async () => {
    const onSave = vi.fn(
      async (): Promise<SaveRouteOutcome> => ({
        ok: false,
        storage: 'local',
        storageUnavailable: true,
        localId: 'local-1',
        message:
          'сохранение недоступно — маршрут останется только в этом браузере',
      })
    );

    render(<SaveRouteButton onSave={onSave} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('save-route-button'));
    });

    const status = screen.getByTestId('save-route-status');
    expect(status).toHaveTextContent('сохранение недоступно');
    expect(status).not.toHaveTextContent('сохранён');
    expect(screen.queryByText(/маршрут сохранён/i)).toBeNull();
  });
});
