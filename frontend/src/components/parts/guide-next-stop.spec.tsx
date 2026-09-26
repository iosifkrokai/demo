import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { GuideNextStop } from './guide-next-stop';

afterEach(cleanup);

const base = {
  number: 2,
  name: 'Кафе Немо',
  category: 'кафе',
  visitMinutes: 40,
  mapsHref: 'https://www.google.com/maps/search/?api=1&query=53.68,23.83',
};

describe('GuideNextStop', () => {
  it('shows the stop without a distance until the browser says where we are', () => {
    render(<GuideNextStop {...base} distance={null} />);

    expect(screen.queryByTestId('guide-next-distance')).toBeNull();
    expect(screen.getByText('Кафе Немо')).toBeInTheDocument();
  });

  it('shows the distance to the stop', () => {
    render(<GuideNextStop {...base} distance={240} />);

    expect(screen.getByTestId('guide-next-distance')).toHaveTextContent(
      'до неё 240 м'
    );
  });

  it('adds the walking time and the ETA when the route can carry them', () => {
    render(
      <GuideNextStop
        {...base}
        distance={240}
        walkMinutes={4}
        etaLabel="≈ 14:35"
      />
    );

    expect(screen.getByTestId('guide-next-walk')).toHaveTextContent(
      'идти ~4 мин'
    );
    expect(screen.getByTestId('guide-next-eta')).toHaveTextContent(
      'прибытие ≈ 14:35'
    );
  });

  it('keeps the ETA row out when there is no estimate to give', () => {
    render(<GuideNextStop {...base} distance={240} />);

    expect(screen.queryByTestId('guide-next-eta')).toBeNull();
    expect(screen.queryByTestId('guide-next-walk')).toBeNull();
  });
});
