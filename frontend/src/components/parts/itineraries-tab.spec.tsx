import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ItinerariesTab } from './itineraries-tab';
import type { Itinerary } from '@/api/types';

const stop = (over: Partial<Itinerary['stops'][number]>) => ({
  place_id: 1,
  source_url: 'city:old-castle',
  name: 'Старый замок (Гродно)',
  category: 'замок',
  town: 'Гродно',
  district: null,
  lat: 53.6791,
  lon: 23.8216,
  visit_minutes: 90,
  opening_hours: 'вт–вс 10:00–18:00',
  blurb: 'Королевский замок Витовта',
  fun_fact: null,
  fun_facts: [],
  links: [],
  ticket_price: null,
  // The API always sends this field; most points have no photo, so the default
  // is the common case and a test that wants one says so explicitly.
  photo: null,
  ...over,
});

const ITINERARY: Itinerary = {
  id: 'old-town-castles',
  title: 'Два замка и Советская',
  blurb: 'Сердце старого города',
  transport: 'pedestrian',
  stop_count: 2,
  visit_minutes: 150,
  stops: [
    stop({}),
    stop({
      place_id: 2,
      source_url: 'city:new-castle',
      name: 'Новый замок',
      category: 'дворец',
      visit_minutes: 60,
      opening_hours: null,
    }),
  ],
};

describe('ItinerariesTab', () => {
  it('prints what the dataset holds: the authored stops and their real facts', () => {
    render(<ItinerariesTab itineraries={[ITINERARY]} onOpen={vi.fn()} />);

    expect(screen.getByText('Два замка и Советская')).toBeInTheDocument();
    expect(screen.getByText('2 остановки')).toBeInTheDocument();
    // Curated visit time of the stops, not an invented duration.
    expect(screen.getByText(/осмотр ~/)).toBeInTheDocument();

    // Stops are behind the disclosure until it is opened.
    expect(screen.queryByText('Старый замок (Гродно)')).toBeNull();
  });

  it('lists the stops in the authored order when the disclosure is opened', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ItinerariesTab itineraries={[ITINERARY]} onOpen={vi.fn()} />);

    await user.click(screen.getByTestId('itinerary-stops-old-town-castles'));

    const rows = screen
      .getByTestId('itinerary-stops-list-old-town-castles')
      .querySelectorAll('li');
    expect([...rows].map((li) => li.textContent)).toEqual([
      expect.stringContaining('Старый замок (Гродно)'),
      expect.stringContaining('Новый замок'),
    ]);
    // A real fact from the row, and nothing invented for the stop that has none.
    expect(screen.getByText(/вт–вс 10:00–18:00/)).toBeInTheDocument();
  });

  it('hands the route over without asking anything', async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup({ delay: null });
    render(<ItinerariesTab itineraries={[ITINERARY]} onOpen={onOpen} />);

    await user.click(screen.getByTestId('itinerary-open-old-town-castles'));
    expect(onOpen).toHaveBeenCalledWith(ITINERARY);
  });

  it('refuses to open a route while a plan is in flight', async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup({ delay: null });
    render(
      <ItinerariesTab itineraries={[ITINERARY]} onOpen={onOpen} disabled />
    );

    await user.click(screen.getByTestId('itinerary-open-old-town-castles'));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('states an incomplete route instead of passing it off as whole', () => {
    render(
      <ItinerariesTab
        itineraries={[ITINERARY]}
        missing={['city:gone']}
        onOpen={vi.fn()}
      />
    );
    expect(screen.getByRole('status')).toHaveTextContent(/не нашлась в данных/);
  });

  it('says the list could not be loaded rather than showing it empty', () => {
    render(
      <ItinerariesTab
        itineraries={[]}
        error={new Error('boom')}
        onReload={vi.fn()}
        onOpen={vi.fn()}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/не удалось загрузить/i);
    expect(screen.queryByTestId('itineraries-empty')).toBeNull();
  });

  it('waits with a statement, not an empty list', () => {
    render(<ItinerariesTab itineraries={[]} isLoading onOpen={vi.fn()} />);
    expect(screen.getByTestId('itineraries-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('itineraries-empty')).toBeNull();
  });

  it('shows a stop photo with its credit, and no image for stops without one', async () => {
    const user = userEvent.setup({ delay: null });
    const withPhoto: Itinerary = {
      ...ITINERARY,
      stops: [
        stop({
          name: 'Старый замок (Гродно)',
          photo: {
            url: 'https://upload.wikimedia.org/wikipedia/commons/6/6a/castle.jpg',
            author: 'Александр Липилин',
            license: 'CC BY-SA 3.0',
            source: 'https://commons.wikimedia.org/wiki/File:castle.jpg',
          },
        }),
        stop({ place_id: 3, source_url: 'city:kolozha', name: 'Коложская церковь' }),
      ],
    };

    render(<ItinerariesTab itineraries={[withPhoto]} onOpen={vi.fn()} />);
    await user.click(screen.getByTestId('itinerary-stops-old-town-castles'));

    // Печать на строке — тоже печать: подпись обязана быть рядом с картинкой.
    expect(screen.getByAltText('Старый замок (Гродно)')).toHaveAttribute(
      'src',
      'https://upload.wikimedia.org/wikipedia/commons/6/6a/castle.jpg'
    );
    expect(screen.getByText(/Александр Липилин/)).toBeInTheDocument();
    // У второй остановки фото нет — значит и картинки быть не должно.
    expect(screen.queryByAltText('Коложская церковь')).toBeNull();
  });
});
