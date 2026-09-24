import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PlaceMarkerLabel } from './place-marker-label';
import type { PlaceDetails } from '@/stores/directions-store';

const details = (overrides: Partial<PlaceDetails> = {}): PlaceDetails => ({
  name: 'Старый замок',
  category: 'castle',
  blurb: 'Резиденция великих князей на левом берегу Немана.',
  funFact: 'От прежних стен до наших дней дошла лишь одна башня.',
  visitMinutes: 30,
  ...overrides,
});

describe('PlaceMarkerLabel', () => {
  it('should render without crashing', () => {
    expect(() =>
      render(<PlaceMarkerLabel details={details()} />)
    ).not.toThrow();
  });

  it('should show the place name and its blurb', () => {
    render(<PlaceMarkerLabel details={details()} />);

    expect(screen.getByTestId('place-marker-label')).toBeInTheDocument();
    expect(screen.getByText('Старый замок')).toBeInTheDocument();
    expect(
      screen.getByText('Резиденция великих князей на левом берегу Немана.')
    ).toBeInTheDocument();
  });

  it('should render only the name when blurb is missing', () => {
    render(<PlaceMarkerLabel details={details({ blurb: null })} />);

    expect(screen.getByText('Старый замок')).toBeInTheDocument();
    expect(
      screen.queryByText('Резиденция великих князей на левом берегу Немана.')
    ).not.toBeInTheDocument();
  });
});
