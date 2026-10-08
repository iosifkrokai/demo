import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

import type { PlaceDetails } from '@/stores/directions-store';

import { PlaceCardBody } from './place-card-body';

// The «посещено» toggle (spec 005) has its own spec; here it is mocked so a pure
// layout test does not need a QueryClient. Anonymous: the card shows the hint.
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
  name: 'Костел Франциска Ассизского',
  category: 'place_of_worship',
  blurb: 'Барочный храм на площади Советской.',
  funFact: 'В башне костёла до сих пор идут часы работы 1750 года.',
  funFacts: ['Осмотр занимает около 20 минут.', 'Вход свободный.'],
  links: [{ title: 'История костёла', url: 'https://example.org/1' }],
  visitMinutes: 20,
  ...overrides,
});

// jsdom shares one document across the files of a worker: without this, the
// card this spec leaves behind answers the next spec's queries.
afterEach(cleanup);

describe('PlaceCardBody', () => {
  it('says the essentials on both surfaces', () => {
    for (const mobile of [false, true]) {
      const view = render(
        <PlaceCardBody details={details()} onClose={() => {}} mobile={mobile} />
      );
      expect(
        screen.getByText('Костел Франциска Ассизского')
      ).toBeInTheDocument();
      expect(
        screen.getByText('Барочный храм на площади Советской.')
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          'В башне костёла до сих пор идут часы работы 1750 года.'
        )
      ).toBeInTheDocument();
      expect(screen.getByText('~20 мин')).toBeInTheDocument();
      view.unmount();
    }
  });

  it('is what the two readers share, so they cannot say different things', () => {
    const { unmount } = render(
      <PlaceCardBody details={details()} onClose={() => {}} />
    );
    expect(screen.getByTestId('place-card-body')).toHaveAttribute(
      'data-variant',
      'popup'
    );
    unmount();

    render(<PlaceCardBody details={details()} onClose={() => {}} mobile />);
    expect(screen.getByTestId('place-card-body')).toHaveAttribute(
      'data-variant',
      'mobile'
    );
  });

  it('keeps a tap on the card from reaching the map underneath', () => {
    // Otherwise the map's own click handler reads the tap as a miss and closes
    // the card mid-sentence — and on a phone the card sits exactly where the
    // thumb reaches for the map. The listener here stands in for the map's:
    // it is on an ancestor, so only `stopPropagation` on the card stops it.
    const onMapClick = vi.fn();
    render(
      <div onClick={onMapClick}>
        <span data-testid="the-map" />
        <PlaceCardBody details={details()} onClose={() => {}} mobile />
      </div>
    );

    fireEvent.click(screen.getByTestId('place-card-body'));
    expect(onMapClick).not.toHaveBeenCalled();

    // A tap on the map beside the card still gets through — the card swallows
    // its own events, not the map's.
    fireEvent.click(screen.getByTestId('the-map'));
    expect(onMapClick).toHaveBeenCalledTimes(1);
  });

  it('puts the extra facts and the links behind one tap on a phone', () => {
    // Measured on 390x844 before: eight stacked blocks at 12px, so the point's
    // name sat above the fold and the reason to visit was a scroll away.
    render(<PlaceCardBody details={details()} onClose={() => {}} mobile />);

    expect(screen.queryByText('Ещё факты')).not.toBeInTheDocument();
    expect(screen.queryByText('История костёла')).not.toBeInTheDocument();

    const more = screen.getByTestId('place-card-more');
    expect(more).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(more);

    expect(more).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Ещё факты')).toBeInTheDocument();
    expect(screen.getByText('История костёла')).toBeInTheDocument();

    // And it closes again — the card must not grow permanently.
    fireEvent.click(more);
    expect(screen.queryByText('Ещё факты')).not.toBeInTheDocument();
  });

  it('has no disclosure at all when there is nothing behind it', () => {
    // A «More» that opens onto nothing is a control that lies.
    render(
      <PlaceCardBody
        details={details({ funFacts: [], links: [] })}
        onClose={() => {}}
        mobile
      />
    );
    expect(screen.queryByTestId('place-card-more')).not.toBeInTheDocument();
  });

  it('lays the extras out inline on a wide screen, where there is room', () => {
    render(<PlaceCardBody details={details()} onClose={() => {}} />);

    expect(screen.getByText('Ещё факты')).toBeInTheDocument();
    expect(screen.getByText('История костёла')).toBeInTheDocument();
    expect(screen.queryByTestId('place-card-more')).not.toBeInTheDocument();
  });

  it('leaves out what the dataset does not have, without breaking the layout', () => {
    render(
      <PlaceCardBody
        details={details({
          category: null,
          blurb: null,
          funFact: null,
          visitMinutes: null,
          openingHours: undefined,
          ticketPrice: undefined,
          town: undefined,
          district: undefined,
        })}
        onClose={() => {}}
        mobile
      />
    );

    expect(screen.getByText('Костел Франциска Ассизского')).toBeInTheDocument();
    expect(screen.queryByText('~20 мин')).not.toBeInTheDocument();
  });

  it('names itself in the interface language, not in Russian only', () => {
    // The headings used to be hardcoded strings in the popup, so an
    // English-language tourist read «Ещё факты» on an otherwise English card.
    render(<PlaceCardBody details={details()} onClose={() => {}} />);
    expect(screen.getByLabelText('Закрыть')).toBeInTheDocument();
  });

  // The close button's 40px comes from `size-10`, and `size-10` is referenced
  // only inside src/components/ui/button.tsx. Tailwind does not scan that
  // directory on its own, so before index.css named it as a source the class was
  // dropped from the build with no error at all — and this button measured
  // 16x16 on the phone while every test was green. Both halves are pinned here:
  // the button asks for a real utility, and the stylesheet really scans the
  // directory that defines it.
  it('берёт мишень закрытия из реальной утилиты, а не из воздуха', () => {
    render(<PlaceCardBody details={details()} onClose={() => {}} mobile />);
    expect(screen.getByLabelText('Закрыть').className).toContain('size-10');

    const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
    expect(css).toMatch(/@source\s+['\"]\.\/components\/ui['\"]/);
  });

  it('closes on the close button', () => {
    const onClose = vi.fn();
    render(<PlaceCardBody details={details()} onClose={onClose} mobile />);

    fireEvent.click(screen.getByLabelText('Закрыть'));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
