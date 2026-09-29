import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { ServiceAlong } from '@/api/types';
import { ServicesSummary, categoryCounts } from './services-summary';

/**
 * What this file pins, and why each line is worth a test:
 *
 * * the count is the point of the summary — the guide knows there are places
 *   beside the walk, and a tourist who is not told will not press a small icon;
 * * «в пределах N м» is a measured distance, never a walking time;
 * * «не удалось проверить» and «ничего нет» stay different sentences: showing
 *   the second when the first is true would be a lie about the world;
 * * a trimmed list says it was trimmed.
 */
const service = (overrides: Partial<ServiceAlong> = {}): ServiceAlong => ({
  id: 1,
  source_url: 'osm:node/1',
  name: 'Ссобойка',
  category: 'кафе',
  town: 'Гродно',
  lat: 53.679,
  lon: 23.825,
  opening_hours: 'Mo-Su 09:00-19:00',
  hours_known: true,
  off_line_m: 1,
  along_m: 417,
  along_fraction: 0.4,
  detour_confirmed: false,
  ...overrides,
});

const renderSummary = (
  props: Partial<React.ComponentProps<typeof ServicesSummary>> = {}
) =>
  render(
    <ServicesSummary
      items={[service()]}
      state="ready"
      maxOffLineM={120}
      active={false}
      toggle={<button data-testid="services-toggle">toggle</button>}
      {...props}
    />
  );

describe('ServicesSummary', () => {
  it('говорит, сколько точек по маршруту и насколько они близко', () => {
    renderSummary({
      items: [
        service(),
        service({ id: 2, source_url: 'osm:node/2', category: 'кафе' }),
        service({ id: 3, source_url: 'osm:node/3', category: 'туалет' }),
      ],
      maxOffLineM: 120,
    });

    expect(screen.getByTestId('services-summary-count')).toHaveTextContent(
      'по пути: 3 места'
    );
    // The distance is measured; a walking time would be invented.
    expect(screen.getByTestId('services-summary-within')).toHaveTextContent(
      'в пределах 120 м от маршрута'
    );
  });

  it('разбивает по категориям и не теряет ни одной точки', () => {
    const items = [
      service(),
      service({ id: 2, source_url: 'osm:node/2', category: 'кафе' }),
      service({ id: 3, source_url: 'osm:node/3', category: 'туалет' }),
    ];
    renderSummary({ items });

    const rows = screen.getAllByTestId('services-summary-category');
    expect(rows).toHaveLength(2);
    // Biggest first: two cafés before one toilet.
    expect(rows[0]).toHaveAttribute('data-category', 'кафе');
    expect(rows[0]).toHaveTextContent('2');
    expect(rows[1]).toHaveAttribute('data-category', 'туалет');
    const counted = categoryCounts(items).reduce(
      (sum, entry) => sum + entry.count,
      0
    );
    expect(counted).toBe(items.length);
  });

  it('неудачную проверку не выдаёт за «ничего нет»', () => {
    renderSummary({ items: [], state: 'unavailable', maxOffLineM: null });

    expect(
      screen.getByTestId('services-summary-unavailable')
    ).toHaveTextContent('не удалось проверить, что рядом');
    // The empty-result sentence must NOT appear: we did not learn that.
    expect(screen.queryByTestId('services-summary-empty')).toBeNull();
    expect(screen.queryByTestId('services-summary-count')).toBeNull();
  });

  it('пустой результат говорит именно про пустой результат', () => {
    renderSummary({ items: [], state: 'ready', maxOffLineM: 90 });

    expect(screen.getByTestId('services-summary-empty')).toHaveTextContent(
      'рядом с маршрутом ничего не нашлось'
    );
    expect(screen.queryByTestId('services-summary-count')).toBeNull();
  });

  it('пока идёт измерение — так и говорит, а не молчит и не выдумывает ноль', () => {
    renderSummary({ items: [], state: 'loading', maxOffLineM: null });

    expect(screen.getByTestId('services-summary')).toHaveTextContent(
      'ищу, что рядом'
    );
    expect(screen.queryByTestId('services-summary-count')).toBeNull();
  });

  it('обрезанный список назван обрезанным', () => {
    renderSummary({ capped: true });
    expect(screen.getByTestId('services-summary-capped')).toBeInTheDocument();
  });

  it('до маршрута не говорит ничего — и тумблер остаётся на месте', () => {
    renderSummary({ state: 'idle', items: [], maxOffLineM: null });

    expect(screen.queryByTestId('services-summary')).toBeNull();
    expect(screen.getByTestId('services-toggle')).toBeInTheDocument();
  });

  it('подсказывает, что метки можно показать или скрыть', () => {
    const { rerender } = render(
      <ServicesSummary
        items={[service()]}
        state="ready"
        maxOffLineM={120}
        active={false}
        toggle={<button data-testid="services-toggle">toggle</button>}
      />
    );
    expect(screen.getByTestId('services-summary')).toHaveTextContent(
      'показать на карте'
    );

    rerender(
      <ServicesSummary
        items={[service()]}
        state="ready"
        maxOffLineM={120}
        active
        toggle={<button data-testid="services-toggle">toggle</button>}
      />
    );
    expect(screen.getByTestId('services-summary')).toHaveTextContent(
      'скрыть с карты'
    );
  });
});
