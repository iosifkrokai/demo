import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { RouteHistoryEntry } from '@/stores/directions-store';

import { HistoryTab } from './history-tab';

const ENTRY = (over: Partial<RouteHistoryEntry> = {}): RouteHistoryEntry => ({
  id: 'h1',
  query: 'два замка пешком',
  timeBudget: 120,
  createdAt: Date.parse('2026-09-20T10:00:00Z'),
  places: [
    {
      id: 1,
      name: 'Старый замок',
      category: 'замок',
      lat: 53.677,
      lon: 23.83,
    },
    {
      id: 2,
      name: 'Новый замок',
      category: 'замок',
      lat: 53.678,
      lon: 23.831,
    },
  ],
  ...over,
});

const renderTab = (entries: RouteHistoryEntry[]) =>
  render(
    <HistoryTab
      entries={entries}
      onRestore={vi.fn()}
      onRemove={vi.fn()}
      onClear={vi.fn()}
    />
  );

describe('вкладка «История»: что было пройдено', () => {
  it('называет пройденный маршрут пройденным', () => {
    renderTab([
      ENTRY({
        walk: {
          visited: 2,
          total: 2,
          at: Date.parse('2026-09-26T18:00:00Z'),
          completed: true,
        },
      }),
    ]);

    expect(screen.getByTestId('history-walk-h1')).toHaveTextContent(/пройден/i);
  });

  it('показывает, сколько пройдено, у незаконченного маршрута', () => {
    renderTab([
      ENTRY({
        walk: {
          visited: 1,
          total: 3,
          at: Date.parse('2026-09-26T18:00:00Z'),
          completed: false,
        },
      }),
    ]);

    const badge = screen.getByTestId('history-walk-h1');
    expect(badge).toHaveTextContent(/пройдено 1 из 3/i);
    // Не «пройден»: маршрут не закончен, и называть его пройденным — врать.
    expect(badge).not.toHaveTextContent(/^пройден$/i);
  });

  it('молчит о маршруте, который только планировали', () => {
    renderTab([ENTRY()]);

    expect(screen.queryByTestId('history-walk-h1')).toBeNull();
  });
});
