import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { AgentPoint } from '@/components/types';

const mockFocusOn = vi.hoisted(() => vi.fn());

vi.mock('@/stores/common-store', () => ({
  useCommonStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ focusOn: mockFocusOn }),
}));

import { CatalogueList } from './catalogue-list';

const churches: AgentPoint[] = [
  {
    id: 101,
    name: 'Костёл Святого Франциска Ксаверия',
    category: 'костёл',
    town: 'Гродно',
    lat: 53.6789,
    lon: 23.8312,
    blurb: 'Самый старый костёл города',
  },
  {
    id: 102,
    name: 'Церковь Бориса и Глеба',
    category: 'церковь',
    town: 'Гродно',
    lat: 53.6785,
    lon: 23.8275,
  },
  {
    id: 103,
    name: 'Костёл Девы Марии',
    category: 'костёл',
    town: 'Островец',
    lat: 54.6121,
    lon: 25.9554,
  },
];

describe('CatalogueList', () => {
  beforeEach(() => {
    mockFocusOn.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('lists every place with its name and where it is', () => {
    render(<CatalogueList places={churches} />);

    expect(screen.getByTestId('catalogue-list')).toBeInTheDocument();
    for (const place of churches) {
      expect(
        screen.getByTestId(`catalogue-place-${place.id}`)
      ).toHaveTextContent(place.name);
    }
    expect(
      screen.getByText('Костёл Святого Франциска Ксаверия')
    ).toBeInTheDocument();
    expect(screen.getByText('Самый старый костёл города')).toBeInTheDocument();
  });

  it('says how many places there are, in the language’s plural', () => {
    render(<CatalogueList places={churches} />);
    expect(screen.getByText('3 места')).toBeInTheDocument();
  });

  it('honestly calls itself a choice of places, not a walk', () => {
    render(<CatalogueList places={churches} />);
    expect(screen.getByText('Каталог мест')).toBeInTheDocument();
    expect(
      screen.getByText(/выбор мест, а не готовая прогулка/i)
    ).toBeInTheDocument();
  });

  it('shows the place on the map when its card is tapped', async () => {
    const user = userEvent.setup({ delay: null });
    render(<CatalogueList places={churches} />);

    await user.click(screen.getByTestId('catalogue-place-102'));
    expect(mockFocusOn).toHaveBeenCalledWith(23.8275, 53.6785);
  });
});
