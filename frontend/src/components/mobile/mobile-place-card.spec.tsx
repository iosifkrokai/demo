import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

import type { PlaceDetails } from '@/stores/directions-store';

import { MobilePlaceCard } from './mobile-place-card';

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
    render(<MobilePlaceCard details={details()} onClose={() => {}} />);

    expect(screen.getByTestId('mobile-place-card').className).toContain(
      '--sheet-h'
    );
  });

  it('cannot grow over the map it is describing', () => {
    render(
      <MobilePlaceCard
        details={details({
          funFacts: ['Размер башни — 30 метров.'],
          links: [{ title: 'История', url: 'https://example.org/1' }],
        })}
        onClose={() => {}}
      />
    );

    const card = screen.getByTestId('mobile-place-card').firstElementChild!;
    expect(card.className).toContain('max-h-');
    expect(card.className).toContain('overflow-y-auto');
  });

  it('is never on a wide screen', () => {
    render(<MobilePlaceCard details={details()} onClose={() => {}} />);
    expect(screen.getByTestId('mobile-place-card').className).toContain(
      'md:hidden'
    );
  });

  it('does not eat taps meant for the map around it', () => {
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
