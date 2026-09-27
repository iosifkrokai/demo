import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { ServiceAlong } from '@/api/types';
import { ServicesLayer, serviceIcon } from './services-layer';

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
    <div data-testid="marker" data-longitude={longitude} data-latitude={latitude}>
      {children}
    </div>
  ),
  Popup: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popup">{children}</div>
  ),
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
        items={[service(), service({ id: 2, source_url: 'osm:node/2', name: 'Туалет', category: 'туалет' })]}
      />
    );
    expect(screen.getAllByTestId('service-marker')).toHaveLength(2);
  });

  it('значки различают категории, а незнакомую не выдумывают', () => {
    expect(serviceIcon('кафе')).not.toBe(serviceIcon('туалет'));
    expect(serviceIcon('ресторан')).not.toBe(serviceIcon('кафе'));
    // An unknown category gets the neutral dot rather than a café icon.
    expect(serviceIcon('вертолётная площадка')).toBe(serviceIcon('что-то новое'));
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
      <ServicesLayer items={[service({ opening_hours: null, hours_known: false })]} />
    );
    fireEvent.click(screen.getByTestId('service-marker'));
    expect(screen.getByTestId('service-card')).toHaveTextContent('часы неизвестны');
  });

  it('на метке нет номера: услуга не становится остановкой маршрута', () => {
    render(<ServicesLayer items={[service()]} />);
    expect(screen.getByTestId('service-marker').textContent).toBe('');
  });
});
