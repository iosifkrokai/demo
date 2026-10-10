import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { PlaceMarkerLabel } from './place-marker-label';
import type { PlaceDetails } from '@/stores/directions-store';

const details = (overrides: Partial<PlaceDetails> = {}): PlaceDetails => ({
  name: 'Старый замок',
  category: 'castle',
  blurb: 'Резиденция великих князей на левом берегу Немана.',
  funFact: 'От прежних стен до наших дней дошла лишь одна башня.',
  funFacts: [],
  links: [],
  visitMinutes: 30,
  ...overrides,
});

const label = () => screen.getByTestId('place-marker-label');

afterEach(cleanup);

describe('PlaceMarkerLabel', () => {
  it('should render without crashing', () => {
    expect(() =>
      render(<PlaceMarkerLabel details={details()} />)
    ).not.toThrow();
  });

  it('should show the place name and its blurb', () => {
    render(<PlaceMarkerLabel details={details()} />);

    expect(label()).toBeInTheDocument();
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

  it('shows one line on a phone, with the blurb left to the card', () => {
    render(<PlaceMarkerLabel details={details()} />);

    expect(
      screen.getByText('Резиденция великих князей на левом берегу Немана.')
        .className
    ).toContain('max-md:hidden');

    expect(label().className).toContain('max-md:max-w-[40vw]');
    expect(screen.getByText('Старый замок').className).toContain('truncate');
  });

  it('gets out of the way by itself on a phone', () => {
    render(<PlaceMarkerLabel details={details()} />);
    expect(label().className).toContain('max-md:animate-place-label-out');
  });

  it('stays up for the point that was tapped', () => {
    render(<PlaceMarkerLabel details={details()} active />);

    expect(label()).toHaveAttribute('data-active', 'true');
    expect(label().className).not.toContain('animate-place-label-out');
  });

  it('is left alone on a wide screen', () => {
    render(<PlaceMarkerLabel details={details()} />);

    expect(label().className).toContain('max-w-[210px]');
    expect(label().className).not.toContain('md:hidden');
  });
});
