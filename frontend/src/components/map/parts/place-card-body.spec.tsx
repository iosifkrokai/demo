import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

import type { PlaceDetails } from '@/stores/directions-store';

import { PlaceCardBody } from './place-card-body';

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
    const onMapClick = vi.fn();
    render(
      <div onClick={onMapClick}>
        <span data-testid="the-map" />
        <PlaceCardBody details={details()} onClose={() => {}} mobile />
      </div>
    );

    fireEvent.click(screen.getByTestId('place-card-body'));
    expect(onMapClick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('the-map'));
    expect(onMapClick).toHaveBeenCalledTimes(1);
  });

  it('puts the extra facts and the links behind one tap on a phone', () => {
    render(<PlaceCardBody details={details()} onClose={() => {}} mobile />);

    expect(screen.queryByText('Ещё факты')).not.toBeInTheDocument();
    expect(screen.queryByText('История костёла')).not.toBeInTheDocument();

    const more = screen.getByTestId('place-card-more');
    expect(more).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(more);

    expect(more).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Ещё факты')).toBeInTheDocument();
    expect(screen.getByText('История костёла')).toBeInTheDocument();

    fireEvent.click(more);
    expect(screen.queryByText('Ещё факты')).not.toBeInTheDocument();
  });

  it('has no disclosure at all when there is nothing behind it', () => {
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
    render(<PlaceCardBody details={details()} onClose={() => {}} />);
    expect(screen.getByLabelText('Закрыть')).toBeInTheDocument();
  });

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
