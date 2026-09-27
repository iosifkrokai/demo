import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { RouteLines } from './route-lines';

const mockSource = vi.fn();
const mockLayer = vi.fn();

vi.mock('react-map-gl/maplibre', () => ({
  Source: (props: Record<string, unknown>) => {
    mockSource(props);
    return <div data-testid="source">{props.children as React.ReactNode}</div>;
  },
  Layer: (props: Record<string, unknown>) => {
    mockLayer(props);
    return <div data-testid="layer" />;
  },
}));

const mockUseCommonStore = vi.fn();

vi.mock('@/stores/common-store', () => ({
  useCommonStore: (selector: (state: unknown) => unknown) =>
    mockUseCommonStore(selector),
}));

const mockUseDirectionsStore = vi.fn();

vi.mock('@/stores/directions-store', () => ({
  useDirectionsStore: (selector: (state: unknown) => unknown) =>
    mockUseDirectionsStore(selector),
}));

const createMockState = (overrides = {}) => ({
  results: {
    data: {
      decodedGeometry: [
        [50, 10],
        [51, 11],
      ],
      trip: { summary: { length: 100, time: 3600 } },
      alternates: [],
    },
    show: { [-1]: true },
  },
  successful: true,
  activeRouteIndex: -1,
  ...overrides,
});

describe('RouteLines', () => {
  beforeEach(() => {
    mockSource.mockClear();
    // По умолчанию проводник не ведёт: линия рисуется целиком.
    mockUseCommonStore.mockReset();
    mockUseCommonStore.mockImplementation((selector) => selector({}));
    mockLayer.mockClear();
    mockUseDirectionsStore.mockClear();
  });

  it('should render nothing when results data is null', () => {
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = { results: { data: null, show: {} }, successful: false };
      return selector(state);
    });

    const { container } = render(<RouteLines />);

    expect(container.firstChild).toBeNull();
  });

  it('should render nothing when not successful', () => {
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = createMockState({ successful: false });
      return selector(state);
    });

    const { container } = render(<RouteLines />);

    expect(container.firstChild).toBeNull();
  });

  it('should render Source when data is valid', () => {
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = createMockState();
      return selector(state);
    });

    render(<RouteLines />);

    expect(mockSource).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'routes', type: 'geojson' })
    );
  });

  it('should render three layers (outline, line, hit-target)', () => {
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = createMockState();
      return selector(state);
    });

    render(<RouteLines />);

    expect(mockLayer).toHaveBeenCalledTimes(3);
    expect(mockLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'routes-hit-target' })
    );
  });

  it('should render outline layer with white color', () => {
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = createMockState();
      return selector(state);
    });

    render(<RouteLines />);

    expect(mockLayer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'routes-outline',
        type: 'line',
        paint: { 'line-color': '#FFF', 'line-width': 9, 'line-opacity': 1 },
      })
    );
  });

  it('should render line layer with dynamic color', () => {
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = createMockState();
      return selector(state);
    });

    render(<RouteLines />);

    expect(mockLayer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'routes-line',
        type: 'line',
        paint: {
          // Пройденная половина линии гаснет: цвет выбирается по флагу walked.
          'line-color': [
            'case',
            ['==', ['get', 'walked'], true],
            '#9CA3AF',
            ['get', 'color'],
          ],
          'line-width': 5,
          'line-opacity': ['case', ['==', ['get', 'routeIndex'], -1], 1, 0.5],
        },
      })
    );
  });

  it('гасит пройденную половину линии, пока ведёт проводник', () => {
    // Навигатор не рисует весь маршрут за спиной: пройденное тускнеет, впереди
    // остаётся акцентный цвет.
    // Основной маршрут активен (индекс 0), а положение — его начало.
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = createMockState({ activeRouteIndex: 0, show: { 0: true } });
      return selector(state);
    });
    mockUseCommonStore.mockImplementation((selector) =>
      selector({ guiding: true, guideFix: { lat: 50, lng: 10 } })
    );

    render(<RouteLines />);

    const data = mockSource.mock.calls.at(-1)?.[0]?.data as {
      features: { properties: { walked?: boolean } }[];
    };
    const walkedParts = data.features.filter((f) => f.properties.walked === true);
    const aheadParts = data.features.filter((f) => f.properties.walked === false);

    expect(walkedParts.length).toBeGreaterThan(0);
    expect(aheadParts.length).toBeGreaterThan(0);
  });

  it('should convert lat/lng to lng/lat format', () => {
    mockUseDirectionsStore.mockImplementation((selector) => {
      const state = createMockState();
      return selector(state);
    });

    render(<RouteLines />);

    const sourceCall = mockSource.mock.calls[0]?.[0];
    const coords = sourceCall?.data.features[0].geometry.coordinates;
    expect(coords[0]).toEqual([10, 50]);
    expect(coords[1]).toEqual([11, 51]);
  });

  describe('route provenance (spec 002 §7 — one route, one source)', () => {
    const agentRoute = {
      decodedGeometry: [
        [53.9, 23.8],
        [53.91, 23.81],
      ],
      trip: {
        legs: [],
        summary: { length: 7.5, time: 1800 },
      },
      source: 'agent' as const,
      hasVerifiedLine: true,
    };

    it('draws the agent line and states it as the source', () => {
      mockUseDirectionsStore.mockImplementation((selector) =>
        selector(createMockState({ results: { data: agentRoute, show: {} } }))
      );

      render(<RouteLines />);

      const feature = mockSource.mock.calls[0]?.[0]?.data.features[0];
      expect(feature.properties.provenance).toBe('agent');
      expect(feature.geometry.coordinates[0]).toEqual([23.8, 53.9]);
    });

    it('draws a hand-built route as a client line', () => {
      mockUseDirectionsStore.mockImplementation((selector) =>
        selector(
          createMockState({
            results: {
              data: {
                decodedGeometry: [
                  [50, 10],
                  [51, 11],
                ],
                trip: { summary: { length: 100, time: 3600 } },
                source: 'client' as const,
                hasVerifiedLine: true,
              },
              show: {},
            },
          })
        )
      );

      render(<RouteLines />);

      expect(
        mockSource.mock.calls[0]?.[0]?.data.features[0].properties.provenance
      ).toBe('client');
    });

    it('treats a result without provenance as a client route', () => {
      mockUseDirectionsStore.mockImplementation((selector) => {
        const state = createMockState();
        return selector(state);
      });

      render(<RouteLines />);

      expect(
        mockSource.mock.calls[0]?.[0]?.data.features[0].properties.provenance
      ).toBe('client');
    });

    it('draws nothing when an agent route carries no usable geometry', () => {
      mockUseDirectionsStore.mockImplementation((selector) =>
        selector(
          createMockState({
            results: {
              data: {
                decodedGeometry: [],
                trip: { legs: [], summary: { length: 0, time: 0 } },
                source: 'agent' as const,
                hasVerifiedLine: false,
              },
              show: { '0': true },
            },
          })
        )
      );

      const { container } = render(<RouteLines />);

      // No substituted client line: the stops stay on the map, the line does not.
      expect(container.firstChild).toBeNull();
      expect(mockSource).not.toHaveBeenCalled();
    });

    it('reports the summary of the line it actually draws', () => {
      mockUseDirectionsStore.mockImplementation((selector) =>
        selector(createMockState({ results: { data: agentRoute, show: {} } }))
      );

      render(<RouteLines />);

      // The hover popup and the route strip read this: the verified line's own
      // numbers, not the ones a client-side request would have produced.
      expect(
        mockSource.mock.calls[0]?.[0]?.data.features[0].properties.summary
      ).toEqual({ length: 7.5, time: 1800 });
    });
  });
});
