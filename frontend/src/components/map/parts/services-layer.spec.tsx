import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { ServiceAlong } from '@/api/types';
import {
  PHONE_MAX_MARKS,
  PHONE_MIN_GAP_PX,
  ServicesLayer,
  serviceIcon,
  visibleServices,
} from './services-layer';

vi.mock('react-map-gl/maplibre', () => ({
  Marker: ({
    children,
    longitude,
    latitude,
  }: {
    children: React.ReactNode;
    longitude: number;
    latitude: number;
  }) => (
    <div
      data-testid="marker"
      data-longitude={longitude}
      data-latitude={latitude}
    >
      {children}
    </div>
  ),
  Popup: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popup">{children}</div>
  ),
  // The layer reads the camera to thin the marks for a phone. Outside a real
  // map there is none, so `current` is null and the layer draws everything it is
  // given — which is what the desktop path and these cases do.
  useMap: () => ({ current: null }),
}));

/**
 * What this file pins, and why each line is worth a test:
 *
 * * the marks sit on the service's own coordinates (lon/lat, not swapped);
 * * the card is honest twice over — «часы неизвестны» when the dataset has no
 *   hours, and «время на заход не рассчитано» because the distance on screen is
 *   not a walking time;
 * * a service is never a route stop: nothing here is numbered.
 */
const service = (overrides: Partial<ServiceAlong> = {}): ServiceAlong => ({
  id: 1,
  source_url: 'osm:node/1',
  name: 'Ссобойка',
  category: 'кафе',
  town: 'Гродно',
  lat: 53.679,
  lon: 23.825,
  opening_hours: 'Mo-Su 09:00-19:00',
  hours_known: true,
  off_line_m: 1,
  along_m: 417,
  along_fraction: 0.4,
  detour_confirmed: false,
  ...overrides,
});

describe('ServicesLayer', () => {
  it('ставит метку на координаты самой точки', () => {
    render(<ServicesLayer items={[service()]} />);
    const marker = screen.getByTestId('marker');
    expect(marker).toHaveAttribute('data-longitude', '23.825');
    expect(marker).toHaveAttribute('data-latitude', '53.679');
  });

  it('рисует по метке на каждую точку и ни одной лишней', () => {
    render(
      <ServicesLayer
        items={[
          service(),
          service({
            id: 2,
            source_url: 'osm:node/2',
            name: 'Туалет',
            category: 'туалет',
          }),
        ]}
      />
    );
    expect(screen.getAllByTestId('service-marker')).toHaveLength(2);
  });

  it('значки различают категории, а незнакомую не выдумывают', () => {
    expect(serviceIcon('кафе')).not.toBe(serviceIcon('туалет'));
    expect(serviceIcon('ресторан')).not.toBe(serviceIcon('кафе'));
    // A transit stop is a boarding point, not a café: it gets its own glyph.
    expect(serviceIcon('остановка транспорта')).not.toBe(serviceIcon('кафе'));
    // An unknown category gets the neutral dot rather than a café icon.
    expect(serviceIcon('вертолётная площадка')).toBe(
      serviceIcon('что-то новое')
    );
  });

  it('карточка показывает часы как есть и говорит, что время на заход не считали', () => {
    render(<ServicesLayer items={[service()]} />);
    fireEvent.click(screen.getByTestId('service-marker'));

    const card = screen.getByTestId('service-card');
    expect(card).toHaveTextContent('Ссобойка');
    expect(card).toHaveTextContent('кафе · Гродно');
    expect(card).toHaveTextContent('1 м в сторону от маршрута');
    expect(card).toHaveTextContent('Mo-Su 09:00-19:00');
    // The number is a distance; the card refuses to present it as a walk.
    expect(card).toHaveTextContent('время на заход не рассчитано');
    expect(card).not.toHaveTextContent('мин');
  });

  it('без часов в данных пишет «часы неизвестны», а не молчит', () => {
    render(
      <ServicesLayer
        items={[service({ opening_hours: null, hours_known: false })]}
      />
    );
    fireEvent.click(screen.getByTestId('service-marker'));
    expect(screen.getByTestId('service-card')).toHaveTextContent(
      'часы неизвестны'
    );
  });

  it('на метке нет номера: услуга не становится остановкой маршрута', () => {
    render(<ServicesLayer items={[service()]} />);
    expect(screen.getByTestId('service-marker').textContent).toBe('');
  });

  // On a phone the marks are thinned, because drawn all at once they made 29
  // overlapping pairs on a 12-place route: clumps of four to six icons, ten of
  // them lying across the route's own place names. These pin the rule.
  describe('прореживание меток на телефоне', () => {
    /** A place `dLat` degrees north of the origin — roughly 111km per degree. */
    const near = (i: number, dLat: number) =>
      service({
        id: i,
        source_url: `https://x/${i}`,
        lon: 23.8,
        lat: 53.68 + dLat,
      });

    const centre = { lat: 53.68, lon: 23.8 };
    const thin = { max: PHONE_MAX_MARKS, minGapPx: PHONE_MIN_GAP_PX };

    it('рисует всё, когда прореживание не просят (десктоп)', () => {
      const items = [near(1, 0), near(2, 0.001), near(3, 0.002)];
      expect(visibleServices(items, centre, null)).toHaveLength(3);
    });

    it('не превышает четыре метки, даже когда мест двенадцать', () => {
      const items = Array.from({ length: 12 }, (_, i) => near(i, i * 0.02));
      const shown = visibleServices(items, centre, thin);
      expect(shown).toHaveLength(PHONE_MAX_MARKS);
    });

    it('разносит метки по экрану, а не по метрам', () => {
      // Three in a row within 40 m of each other: they must not clump in either
      // case. Without screen coordinates it falls back to the geographic gap.
      const items = [near(1, 0), near(2, 0.0002), near(3, 0.0004)];
      expect(visibleServices(items, centre, thin)).toHaveLength(1);

      // Two marks far apart on the map but close together on screen — the second
      // is still dropped.
      const two = [near(1, 0), near(2, 0.0003)];
      const screen = [
        { x: 200, y: 300 },
        { x: 210, y: 305 },
      ];
      const pos = new Map(two.map((s, i) => [s.source_url, screen[i]]));
      expect(
        visibleServices(
          two,
          centre,
          thin,
          (item) => pos.get(item.source_url) ?? null
        )
      ).toHaveLength(1);
    });

    it('берёт ближайшие к центру карты, а не первые попавшиеся', () => {
      // The distant mark (55 km) does not pass the proximity threshold to the
      // marks already kept, but the distance between the kept ones must still decrease.
      const items = [near(1, 0.5), near(2, 0.001), near(3, 0.002)];
      const shown = visibleServices(items, centre, thin).map((s) => s.id);
      expect(shown).toEqual([2, 3, 1]);

      // And when there are more marks than the limit, the furthest are dropped first.
      const many = Array.from({ length: 8 }, (_, i) =>
        near(i, 0.001 * (i + 1))
      );
      const four = visibleServices(many, centre, { ...thin, minGapPx: 10 });
      expect(four).toHaveLength(PHONE_MAX_MARKS);
      // `many[0]` is the closest to the centre, and the ids run 0..7.
      expect(four.map((s) => s.id)).toEqual([0, 1, 2, 3]);
    });

    it('без центра (карта ещё не готова) рисует все — иначе мигнут пустой карты', () => {
      const items = Array.from({ length: 12 }, (_, i) => near(i, i * 0.02));
      expect(visibleServices(items, null, thin)).toHaveLength(12);
    });
  });
});
