import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import { guideModeFor } from './guide-mode';
import { GuideNextStop } from './guide-next-stop';

afterEach(cleanup);

const walk = guideModeFor('pedestrian');
const drive = guideModeFor('auto');

const base = {
  number: 2,
  name: 'Кафе Немо',
  category: 'кафе',
  visitMinutes: 40,
  mode: walk,
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

  it('adds the travel time and the ETA when the route can carry them', () => {
    render(
      <GuideNextStop
        {...base}
        distance={240}
        travelMinutes={4}
        etaLabel="≈ 14:35"
      />
    );

    expect(screen.getByTestId('guide-next-travel')).toHaveTextContent(
      'идти ~4 мин'
    );
    expect(screen.getByTestId('guide-next-eta')).toHaveTextContent(
      'прибытие ≈ 14:35'
    );
  });

  it('keeps the ETA row out when there is no estimate to give', () => {
    render(<GuideNextStop {...base} distance={240} />);

    expect(screen.queryByTestId('guide-next-eta')).toBeNull();
    expect(screen.queryByTestId('guide-next-travel')).toBeNull();
  });

  it('speaks of driving and parking on a car route', () => {
    render(
      <GuideNextStop {...base} mode={drive} distance={2400} travelMinutes={9} />
    );

    expect(screen.getByTestId('guide-next-travel')).toHaveTextContent(
      'ехать ~9 мин'
    );
    expect(screen.getByTestId('guide-next-arrival-hint')).toHaveTextContent(
      'припаркуйтесь у остановки'
    );
  });

  it('has nothing to park on foot or by bike', () => {
    render(<GuideNextStop {...base} distance={240} travelMinutes={4} />);

    expect(screen.queryByTestId('guide-next-arrival-hint')).toBeNull();
  });

  it('reads the estimate as approximate and lets the tourist set their own time', () => {
    const changes: (number | null)[] = [];
    render(
      <GuideNextStop
        {...base}
        distance={240}
        estimateMinutes={40}
        onVisitMinutesChange={(minutes) => changes.push(minutes)}
      />
    );

    // The estimate is a hint, not a claim: it is shown with «≈».
    expect(screen.getByTestId('visit-time-chip')).toHaveTextContent('≈ 40 мин');

    fireEvent.click(screen.getByTestId('visit-time-chip'));
    fireEvent.click(screen.getByTestId('visit-time-plus'));
    expect(changes).toEqual([50]);
  });

  it('offers the estimate back once the tourist has changed it', () => {
    const changes: (number | null)[] = [];
    render(
      <GuideNextStop
        {...base}
        visitMinutes={70}
        visitOverride={70}
        estimateMinutes={40}
        distance={240}
        onVisitMinutesChange={(minutes) => changes.push(minutes)}
      />
    );

    // Their own number is shown without «≈» — it is not an estimate any more.
    expect(screen.getByTestId('visit-time-chip')).toHaveTextContent('70 мин');

    fireEvent.click(screen.getByTestId('visit-time-chip'));
    fireEvent.click(screen.getByTestId('visit-time-minus'));
    expect(changes).toEqual([60]);

    fireEvent.click(screen.getByTestId('visit-time-reset'));
    expect(changes).toEqual([60, null]);
  });
});
