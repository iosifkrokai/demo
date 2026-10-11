import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import type { SavedRouteListItem } from '@/api/types';

import { SavedRoutesList } from './saved-routes-list';

afterEach(cleanup);

const item = (
  overrides: Partial<SavedRouteListItem> = {}
): SavedRouteListItem => ({
  id: 'r1',
  name: 'Старый город',
  query: 'что посмотреть',
  created_at: '2026-09-01T00:00:00Z',
  stop_count: 5,
  distance_m: 3200,
  duration_min: 120,
  local_only: false,
  ...overrides,
});

describe('SavedRoutesList', () => {
  it('renders the summary of a route', () => {
    render(
      <SavedRoutesList
        routes={[item()]}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    expect(screen.getByTestId('saved-route-r1')).toBeInTheDocument();
    expect(screen.getByText('Старый город')).toBeInTheDocument();
    expect(screen.getByTestId('saved-route-meta-r1')).toHaveTextContent(
      '5 остановок'
    );
    expect(screen.getByTestId('saved-route-meta-r1')).toHaveTextContent(
      '3,2 км'
    );
    expect(screen.getByTestId('saved-route-meta-r1')).toHaveTextContent('2 ч');
  });

  it('never renders geometry it did not receive', () => {
    const route = {
      ...item(),
      plan: { shape: { coordinates: [[53.68, 23.83]] } },
    } as SavedRouteListItem;

    render(
      <SavedRoutesList
        routes={[route]}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    const html = document.body.innerHTML;
    expect(html).not.toContain('53.68');
    expect(html).not.toContain('23.83');
    expect(html).not.toContain('coordinates');
    expect(html).not.toContain('plan');
  });

  it('shows a local-only copy without inventing numbers', () => {
    render(
      <SavedRoutesList
        routes={[
          item({
            id: 'l1',
            name: null,
            query: '',
            stop_count: null,
            distance_m: null,
            duration_min: null,
            local_only: true,
          }),
        ]}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    expect(screen.getByTestId('saved-route-local-l1')).toHaveTextContent(
      'только в этом браузере'
    );
    expect(screen.getByTestId('saved-route-l1')).toHaveTextContent(
      'маршрут без названия'
    );

    const meta = screen.getByTestId('saved-route-meta-l1').textContent ?? '';
    expect(meta).not.toMatch(/км|останов|мин/);
  });

  it('calls back for open, rename and delete', () => {
    const onRestore = vi.fn();
    const onRename = vi.fn();
    const onDelete = vi.fn();
    render(
      <SavedRoutesList
        routes={[item()]}
        onRestore={onRestore}
        onRename={onRename}
        onDelete={onDelete}
      />
    );

    fireEvent.click(screen.getByTestId('saved-route-open-r1'));
    expect(onRestore).toHaveBeenCalledWith('r1');

    fireEvent.click(screen.getByTestId('saved-route-rename-r1'));
    fireEvent.change(screen.getByTestId('saved-route-rename-input-r1'), {
      target: { value: 'Новое имя' },
    });
    fireEvent.click(screen.getByTestId('saved-route-rename-save-r1'));
    expect(onRename).toHaveBeenCalledWith('r1', 'Новое имя');

    fireEvent.click(screen.getByTestId('saved-route-delete-r1'));
    expect(onDelete).toHaveBeenCalledWith('r1');
  });

  it('says plainly when there is nothing saved yet', () => {
    render(
      <SavedRoutesList routes={[]} onRestore={vi.fn()} onDelete={vi.fn()} />
    );
    expect(screen.getByTestId('saved-routes-empty')).toHaveTextContent(
      'пока ничего не сохранено'
    );
  });

  it('says saving is unavailable when storage is down', () => {
    render(
      <SavedRoutesList
        routes={[]}
        storageUnavailable
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );
    expect(screen.getByTestId('saved-routes-unavailable')).toHaveTextContent(
      'сохранение недоступно'
    );
  });
});
