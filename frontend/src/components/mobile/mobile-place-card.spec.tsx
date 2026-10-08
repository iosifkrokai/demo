import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

import type { PlaceDetails } from '@/stores/directions-store';

import { MobilePlaceCard } from './mobile-place-card';

// The «посещено» toggle (spec 005) has its own spec; mocked so a layout test needs
// no QueryClient, and anonymous, which is what the hint renders for.
vi.mock('@/hooks/use-auth', () => ({
  useAuth: () => ({
    user: null,
    authenticated: false,
    isAdmin: false,
    isLoading: false,
    refetch: () => {},
  }),
  describeAccountError: () => '',
}));
vi.mock('@/hooks/use-visited', () => ({
  useVisitedIds: () => new Set<number>(),
  useToggleVisited: () => ({ mutate: () => {}, isPending: false }),
}));

const details = (overrides: Partial<PlaceDetails> = {}): PlaceDetails => ({
  name: 'Старый замок',
  category: 'замок',
  blurb: 'Резиденция великих князей на левом берегу Немана.',
  funFact: 'От прежних стен до наших дней дошла лишь одна башня.',
  funFacts: [],
  links: [],
  visitMinutes: 30,
  ...overrides,
});

// jsdom shares one document across the files of a worker: without this, the
// card this spec leaves behind answers the next spec's queries.
afterEach(cleanup);

describe('MobilePlaceCard', () => {
  it('says what the point is', () => {
    render(<MobilePlaceCard details={details()} onClose={() => {}} />);

    expect(screen.getByText('Старый замок')).toBeInTheDocument();
    expect(
      screen.getByText('Резиденция великих князей на левом берегу Немана.')
    ).toBeInTheDocument();
  });

  it('stands on the bottom of the map, above the panel sheet', () => {
    // A card that follows the pin inherits the pin's screen position: it moves
    // when the map is panned, can land under the map's own controls, and has to
    // be fought with by the pan handler to stay put. Docking it on the same
    // `--sheet-h` every floating control rides on gives it neither problem.
    render(<MobilePlaceCard details={details()} onClose={() => {}} />);

    expect(screen.getByTestId('mobile-place-card').className).toContain(
      '--sheet-h'
    );
  });

  it('cannot grow over the map it is describing', () => {
    // Three extra facts and four links must not be able to swallow the map.
    render(
      <MobilePlaceCard
        details={details({
          funFacts: ['Размер башни — 30 метров.'],
          links: [{ title: 'История', url: 'https://example.org/1' }],
        })}
        onClose={() => {}}
      />
    );

    // The bounds are on the card itself, inside the full-width wrapper.
    const card = screen.getByTestId('mobile-place-card').firstElementChild!;
    expect(card.className).toContain('max-h-');
    expect(card.className).toContain('overflow-y-auto');
  });

  it('is never on a wide screen', () => {
    // The desktop reader is the popup by the pin; two cards for one point would
    // put the same text on screen twice.
    render(<MobilePlaceCard details={details()} onClose={() => {}} />);
    expect(screen.getByTestId('mobile-place-card').className).toContain(
      'md:hidden'
    );
  });

  it('does not eat taps meant for the map around it', () => {
    // The wrapper covers the full width, so without pointer-events-none the
    // strip above the card would be a strip of dead map.
    render(<MobilePlaceCard details={details()} onClose={() => {}} />);
    expect(screen.getByTestId('mobile-place-card').className).toContain(
      'pointer-events-none'
    );
  });

  it('closes', () => {
    const onClose = vi.fn();
    render(<MobilePlaceCard details={details()} onClose={onClose} />);

    fireEvent.click(screen.getByLabelText('Закрыть'));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
