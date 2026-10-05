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

// jsdom shares one document across the files of a worker, so a card left over
// from another spec would answer these queries too.
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

  // The complaint this answers: on 390x844 a two-line 210px plate on every pin
  // of a 12-stop route overlapped its neighbours, and a name cut in half is
  // worse than no name — the card it opens has the blurb anyway.
  it('shows one line on a phone, with the blurb left to the card', () => {
    render(<PlaceMarkerLabel details={details()} />);

    // The blurb is in the DOM but not on a phone: the card carries it.
    expect(
      screen.getByText('Резиденция великих князей на левом берегу Немана.')
        .className
    ).toContain('max-md:hidden');

    // And the name is bounded, so it truncates instead of sprawling.
    expect(label().className).toContain('max-md:max-w-[40vw]');
    expect(screen.getByText('Старый замок').className).toContain('truncate');
  });

  it('gets out of the way by itself on a phone', () => {
    // Labels for every stop at once answer «what is here» and then keep
    // answering it, over the map, for as long as the route is open.
    render(<PlaceMarkerLabel details={details()} />);
    expect(label().className).toContain('max-md:animate-place-label-out');
  });

  it('stays up for the point that was tapped', () => {
    // That one is being read on purpose — the card it opened is above it — so
    // the 6s auto-hide must not take the name away mid-sentence.
    render(<PlaceMarkerLabel details={details()} active />);

    expect(label()).toHaveAttribute('data-active', 'true');
    expect(label().className).not.toContain('animate-place-label-out');
  });

  it('is left alone on a wide screen', () => {
    // Both behaviours are `max-md:`, so a desktop map is unchanged: the plate
    // keeps its blurb and never fades.
    render(<PlaceMarkerLabel details={details()} />);

    expect(label().className).toContain('max-w-[210px]');
    expect(label().className).not.toContain('md:hidden');
  });
});
