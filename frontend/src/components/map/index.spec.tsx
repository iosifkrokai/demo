import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  waitFor,
  fireEvent,
  act,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MapComponent } from './index';
import type { ParsedDirectionsGeometry } from '@/components/types';

const mockToast = vi.hoisted(() => ({
  error: vi.fn(),
}));

const mockQueryRenderedFeatures = vi.hoisted(() =>
  vi.fn(() => [] as unknown[])
);
const mockGetLayer = vi.hoisted(() => vi.fn((): unknown => true));
const mockMapRef = vi.hoisted(() => ({
  getCenter: vi.fn(() => ({ lng: 13.4, lat: 52.5 })),
  getZoom: vi.fn(() => 10),
  fitBounds: vi.fn(),
  getMap: vi.fn(() => ({
    getCanvas: vi.fn(() => ({ style: { cursor: '' } })),
    queryRenderedFeatures: mockQueryRenderedFeatures,
    getLayer: mockGetLayer,
  })),
}));

vi.mock('react-map-gl/maplibre', async () => {
  const React = await import('react');
  return {
    // eslint-disable-next-line react/display-name
    Map: React.forwardRef(
      (
        {
          children,
          onClick,
          onDblClick,
          onContextMenu,
          onTouchStart,
          ...props
        }: {
          children?: React.ReactNode;
          onClick?: (e: unknown) => void;
          onDblClick?: (e: unknown) => void;
          onContextMenu?: (e: unknown) => void;
          onTouchStart?: (e: unknown) => void;
          longitude?: number;
          latitude?: number;
          zoom?: number;
        },
        ref: React.Ref<typeof mockMapRef>
      ) => {
        // Set up the ref to return our mock map
        React.useImperativeHandle(ref, () => mockMapRef);

        return (
          <div
            data-testid="map"
            data-longitude={props.longitude}
            data-latitude={props.latitude}
            data-zoom={props.zoom}
            onClick={() => {
              onClick?.({
                lngLat: { lng: 13.4, lat: 52.5 },
                point: { x: 100, y: 100 },
              });
            }}
            onDoubleClick={() => {
              onDblClick?.({
                lngLat: { lng: 13.4, lat: 52.5 },
                point: { x: 100, y: 100 },
              });
            }}
            onContextMenu={(e: React.MouseEvent) => {
              e.preventDefault();
              onContextMenu?.({
                lngLat: { lng: 13.4, lat: 52.5 },
                point: { x: 100, y: 100 },
              });
            }}
            onTouchStart={(e: React.TouchEvent) => {
              onTouchStart?.({
                lngLat: { lng: 13.4, lat: 52.5 },
                point: { x: 100, y: 100 },
                originalEvent: e,
              });
            }}
          >
            {children}
          </div>
        );
      }
    ),
    Marker: vi.fn(({ children, longitude, latitude }) => (
      <div data-testid="marker" data-lng={longitude} data-lat={latitude}>
        {children}
      </div>
    )),
    Popup: vi.fn(({ children }) => <div data-testid="popup">{children}</div>),
    NavigationControl: vi.fn(() => (
      <div data-testid="navigation-control">Nav</div>
    )),
    GeolocateControl: vi.fn(({ onError }) => (
      <div data-testid="geolocate-control">
        <button
          data-testid="trigger-geolocate-error"
          onClick={() => onError?.({ PERMISSION_DENIED: false })}
        >
          Trigger Error
        </button>
        <button
          data-testid="trigger-geolocate-permission-denied"
          onClick={() => onError?.({ PERMISSION_DENIED: true })}
        >
          Trigger Permission Denied
        </button>
      </div>
    )),
    useMap: vi.fn(() => ({ current: mockMapRef })),
  };
});

const mockUseParams = vi.hoisted(() =>
  vi.fn(() => ({ activeTab: 'directions' }))
);

vi.mock('@tanstack/react-router', () => ({
  useParams: mockUseParams,
  useSearch: vi.fn(() => ({ profile: 'bicycle', style: undefined })),
  useNavigate: vi.fn(() => vi.fn()),
}));

vi.mock('@/stores/common-store', () => ({
  useCommonStore: vi.fn((selector) => {
    const state = {
      coordinates: [],
      directionsPanelOpen: true,
      settingsPanelOpen: false,
      updateSettings: vi.fn(),
    };
    return selector(state);
  }),
}));

// The map reads waypoints, place details and the stored route result from the
// directions store; a test swaps the whole state through `mockDirectionsState`.
const mockDirectionsState = vi.hoisted(() => ({
  current: {
    waypoints: [] as unknown[],
    // Typed through the contract so a case can install an agent or client route
    // with its provenance fields (spec 002).
    results: {
      data: null as import('@/components/types').ParsedDirectionsGeometry | null,
      show: {} as Record<string, boolean>,
    },
    successful: false,
    placeDetails: {} as Record<number, unknown>,
    activeRouteIndex: 0,
  },
}));

vi.mock('@/stores/directions-store', () => ({
  ME_WAYPOINT_ID: 'me',
  useDirectionsStore: vi.fn((selector) =>
    selector(mockDirectionsState.current)
  ),
}));

vi.mock('@/stores/isochrones-store', () => ({
  useIsochronesStore: vi.fn((selector) => {
    const state = {
      geocodeResults: [],
    };
    return selector(state);
  }),
}));

vi.mock('@/hooks/use-directions-queries', () => ({
  useDirectionsQuery: vi.fn(() => ({
    refetch: vi.fn(),
  })),
  useSetWaypointFromCoords: vi.fn(() => ({
    setWaypointFromCoords: vi.fn().mockResolvedValue([]),
  })),
}));

vi.mock('@/hooks/use-isochrones-queries', () => ({
  useIsochronesQuery: vi.fn(() => ({
    refetch: vi.fn(),
  })),
  useReverseGeocodeIsochrones: vi.fn(() => ({
    reverseGeocode: vi.fn().mockResolvedValue([]),
  })),
}));

vi.mock('./map-style-control', () => ({
  MapStyleControl: vi.fn(() => (
    <div data-testid="map-style-control">Style Control</div>
  )),
}));

vi.mock('./draw-control', () => ({
  DrawControl: vi.fn(() => <div data-testid="draw-control">Draw Control</div>),
}));

vi.mock('./parts/route-lines', async () => {
  const actual =
    await vi.importActual<typeof import('./parts/route-lines')>(
      './parts/route-lines'
    );
  return {
    ...actual,
    RouteLines: vi.fn(() => <div data-testid="route-lines">Route Lines</div>),
  };
});

vi.mock('./parts/highlight-segment', () => ({
  HighlightSegment: vi.fn(() => (
    <div data-testid="highlight-segment">Highlight</div>
  )),
}));

vi.mock('./parts/isochrone-polygons', () => ({
  IsochronePolygons: vi.fn(() => (
    <div data-testid="isochrone-polygons">Isochrone</div>
  )),
}));

vi.mock('./parts/isochrone-locations', () => ({
  IsochroneLocations: vi.fn(() => (
    <div data-testid="isochrone-locations">Locations</div>
  )),
}));

vi.mock('./parts/tool-button', () => ({
  ToolButton: vi.fn(
    ({
      title,
      onClick,
      disabled,
      'data-testid': testId,
    }: {
      title: string;
      onClick: () => void;
      disabled?: boolean;
      'data-testid'?: string;
    }) => (
      <button
        aria-label={title}
        onClick={onClick}
        disabled={disabled}
        data-testid={testId}
      >
        {title}
      </button>
    )
  ),
}));

vi.mock('./parts/map-info-popup', () => ({
  MapInfoPopup: vi.fn(({ onClose }) => (
    <div data-testid="map-info-popup">
      <button onClick={onClose}>Close</button>
    </div>
  )),
}));

vi.mock('./parts/map-context-menu', () => ({
  MapContextMenu: vi.fn(({ onAddWaypoint }) => (
    <div data-testid="map-context-menu">
      <button onClick={() => onAddWaypoint(0)}>Add Waypoint</button>
    </div>
  )),
}));

vi.mock('./parts/tiles-info-popup', () => ({
  TilesInfoPopup: vi.fn(({ onClose }) => (
    <div data-testid="tiles-info-popup">
      <button onClick={onClose}>Close Tiles Popup</button>
    </div>
  )),
}));

vi.mock('./utils', () => ({
  getInitialMapStyle: vi.fn(() => 'shortbread'),
  getCustomStyle: vi.fn(() => null),
  getMapStyleUrl: vi.fn((style: string) => `https://example.com/${style}.json`),
  getInitialMapPosition: vi.fn(() => ({
    center: [13.4, 52.5],
    zoom: 10,
  })),
  LAST_CENTER_KEY: 'last_center',
}));

vi.mock('sonner', () => ({
  toast: mockToast,
}));

describe('MapComponent', () => {
  let localStorageMock: Record<string, string>;

  beforeEach(() => {
    localStorageMock = {};
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(
      (key: string, value: string) => {
        localStorageMock[key] = value;
      }
    );
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(
      (key: string) => localStorageMock[key] ?? null
    );

    vi.clearAllMocks();
    mockDirectionsState.current = {
      waypoints: [],
      results: { data: null, show: {} },
      successful: false,
      placeDetails: {},
      activeRouteIndex: 0,
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should render without crashing', () => {
    expect(() => render(<MapComponent />)).not.toThrow();
  });

  it('should render the map container', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('map')).toBeInTheDocument();
  });

  it('should render navigation control', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('navigation-control')).toBeInTheDocument();
  });

  it('should render geolocate control', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('geolocate-control')).toBeInTheDocument();
  });

  it('should render draw control', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('draw-control')).toBeInTheDocument();
  });

  it('should render map style control', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('map-style-control')).toBeInTheDocument();
  });

  it('should render route lines component', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('route-lines')).toBeInTheDocument();
  });

  it('should render highlight segment component', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('highlight-segment')).toBeInTheDocument();
  });

  it('should render isochrone polygons component', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('isochrone-polygons')).toBeInTheDocument();
  });

  it('should render isochrone locations component', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('isochrone-locations')).toBeInTheDocument();
  });

  it('should render left-side Directions shortcut button', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('tab-directions-button')).toBeInTheDocument();
  });

  // The upstream map put four more controls on the canvas: an elevation
  // profile overlay (HeightGraph + its hover marker, fed by a Valhalla /height
  // request), an "Open on osm.org" button, and Isochrones/Tiles shortcuts that
  // jumped to the three-panel RoutePlanner. This fork replaced that planner
  // with the tourist Sidebar — one planning panel, no tab strip — and dropped
  // the map chrome that only made sense with it. Nothing renders
  // HeightGraph/HeightgraphHoverMarker any more, and the two remaining tabs
  // have no panel to open, so the map keeps a single shortcut to the live
  // Directions panel. This test pins that contract.
  it('should render no map controls beyond the Directions panel shortcut', () => {
    render(<MapComponent />);

    const shortcuts = screen.getByLabelText('Panel shortcuts');
    expect(within(shortcuts).getAllByRole('button')).toHaveLength(1);

    expect(screen.queryByTestId('heightgraph-toggle')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('heightgraph-hover-marker')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Open on osm.org' })
    ).not.toBeInTheDocument();
  });

  it('should call navigate when Directions shortcut button is clicked', async () => {
    const mockNavigate = vi.fn();
    const router = await import('@tanstack/react-router');
    vi.mocked(router.useNavigate).mockReturnValue(mockNavigate);

    const user = userEvent.setup();
    render(<MapComponent />);

    await user.click(screen.getByTestId('tab-directions-button'));

    expect(mockNavigate).toHaveBeenCalledWith({
      params: { activeTab: 'directions' },
    });
  });

  it('should NOT open the Valhalla coordinate popup on a plain map click', async () => {
    vi.useFakeTimers();
    render(<MapComponent />);

    fireEvent.click(screen.getByTestId('map'));

    // advance timers past the click delay (200ms)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });

    // The tourist view only clears the selection: the coordinate /
    // "Valhalla location JSON" popup is a developer tool (tiles tab).
    expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it('should show context menu on right click', async () => {
    render(<MapComponent />);

    fireEvent.contextMenu(screen.getByTestId('map'));

    await waitFor(() => {
      expect(screen.getByTestId('popup')).toBeInTheDocument();
      expect(screen.getByTestId('map-context-menu')).toBeInTheDocument();
    });
  });

  it('should set initial view state from getInitialMapPosition', () => {
    render(<MapComponent />);
    const map = screen.getByTestId('map');

    expect(map).toHaveAttribute('data-longitude', '13.4');
    expect(map).toHaveAttribute('data-latitude', '52.5');
    expect(map).toHaveAttribute('data-zoom', '10');
  });

  it('should close context popup when clicking on map again', async () => {
    render(<MapComponent />);

    fireEvent.contextMenu(screen.getByTestId('map'));

    await waitFor(() => {
      expect(screen.getByTestId('map-context-menu')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('map'));

    await waitFor(() => {
      expect(screen.queryByTestId('map-context-menu')).not.toBeInTheDocument();
    });
  });

  it('should never show the coordinate popup on the tourist map', async () => {
    vi.useFakeTimers();
    render(<MapComponent />);

    const map = screen.getByTestId('map');

    fireEvent.click(map);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

    // a second click stays clean as well — no popup flash, and no fake timer
    // left behind for the next test
    fireEvent.click(map);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  describe('GeolocateControl error handling', () => {
    it('should show default error toast when geolocate fails', async () => {
      const user = userEvent.setup();
      render(<MapComponent />);

      await user.click(screen.getByTestId('trigger-geolocate-error'));

      expect(mockToast.error).toHaveBeenCalledWith(
        'Не удалось определить ваше местоположение. Попробуйте ещё раз.'
      );
    });

    it('should show permission denied error toast when location permission is denied', async () => {
      const user = userEvent.setup();
      render(<MapComponent />);

      await user.click(
        screen.getByTestId('trigger-geolocate-permission-denied')
      );

      expect(mockToast.error).toHaveBeenCalledWith(
        'Не удалось определить ваше местоположение. Проверьте настройки браузера и разрешите доступ к геолокации.'
      );
    });
  });

  describe('double-click and double-tap behavior', () => {
    it('should not show popup on double-click (zoom only)', async () => {
      vi.useFakeTimers();
      render(<MapComponent />);

      const map = screen.getByTestId('map');

      // simulate double-click: click followed quickly by dblclick
      fireEvent.click(map);
      fireEvent.doubleClick(map);

      // advance timers past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      // popup should not appear because double-click cancelled it
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });

    it('should cancel pending popup when double-click occurs before delay expires', async () => {
      vi.useFakeTimers();
      render(<MapComponent />);

      const map = screen.getByTestId('map');

      // first click starts the timer
      fireEvent.click(map);

      // popup should not be visible yet
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      // advance only 100ms (less than 200ms delay)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });

      // double-click cancels the pending popup
      fireEvent.doubleClick(map);

      // advance past the original delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200);
      });

      // popup should still not appear
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });

    it('should cancel pending popup on double-tap (mobile)', async () => {
      vi.useFakeTimers();
      render(<MapComponent />);

      const map = screen.getByTestId('map');

      // create a mock touch event with single finger
      const createTouchEvent = () => ({
        touches: [{ identifier: 0, target: map }],
      });

      // first tap
      fireEvent.touchStart(map, createTouchEvent());
      fireEvent.click(map);

      // popup should not be visible yet
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      // second tap within 300ms threshold (simulating double-tap)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });
      fireEvent.touchStart(map, createTouchEvent());

      // advance past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      // popup should not appear because double-tap cancelled it
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });

    it('should cancel pending popup on multi-finger touch (pinch-to-zoom)', async () => {
      vi.useFakeTimers();
      render(<MapComponent />);

      const map = screen.getByTestId('map');

      // first single-finger tap starts click timer
      fireEvent.touchStart(map, {
        touches: [{ identifier: 0, target: map }],
      });
      fireEvent.click(map);

      // popup should not be visible yet
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      // multi-finger touch (pinch gesture) should cancel pending popup
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      fireEvent.touchStart(map, {
        touches: [
          { identifier: 0, target: map },
          { identifier: 1, target: map },
        ],
      });

      // advance past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      // popup should not appear because multi-touch cancelled it
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });

    it('should not leave a coordinate popup behind after a single tap', async () => {
      vi.useFakeTimers();
      render(<MapComponent />);

      const map = screen.getByTestId('map');

      // first tap
      fireEvent.touchStart(map, {
        touches: [{ identifier: 0, target: map }],
      });
      fireEvent.click(map);

      // wait longer than double-tap threshold (300ms) + click delay (200ms)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      // no second tap occurred: the tourist view stays clean (the coordinate
      // popup only exists for the tiles tab)
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });
  });

  describe('route provenance (spec 002 §7 — one route, one source)', () => {
    /**
     * The map only reads the geometry, the summary and the provenance fields, so
     * these fixtures carry a deliberately partial Valhalla trip: the single cast
     * inside this helper is preferred over fabricating ten trip fields the
     * assertion never touches.
     */
    const routeFixture = (over: {
      decodedGeometry: number[][];
      summary?: { length: number; time: number };
      source: 'agent' | 'client';
      hasVerifiedLine: boolean;
    }): ParsedDirectionsGeometry =>
      ({
        id: 'test-route',
        decodedGeometry: over.decodedGeometry,
        trip: { legs: [], summary: over.summary ?? { length: 0, time: 0 } },
        source: over.source,
        hasVerifiedLine: over.hasVerifiedLine,
      }) as unknown as ParsedDirectionsGeometry;

    const agentRoute = routeFixture({
      decodedGeometry: [
        [53.9, 23.8],
        [53.91, 23.81],
      ],
      summary: { length: 7.5, time: 1800 },
      source: 'agent',
      hasVerifiedLine: true,
    });

    it('states that the line on screen is the verified agent plan', () => {
      mockDirectionsState.current = {
        ...mockDirectionsState.current,
        results: { data: agentRoute, show: { '0': true } },
        successful: true,
      };

      render(<MapComponent />);

      const chip = screen.getByTestId('route-provenance');
      expect(chip).toHaveAttribute('data-provenance', 'agent');
      expect(chip).toHaveAttribute('data-verified-line', 'true');
      expect(chip).toHaveTextContent('проверенного плана агента');
    });

    it('states that a hand-built route is drawn by the app itself', () => {
      mockDirectionsState.current = {
        ...mockDirectionsState.current,
        results: {
          data: routeFixture({
            decodedGeometry: [
              [53.9, 23.8],
              [53.91, 23.81],
            ],
            summary: { length: 3, time: 600 },
            source: 'client',
            hasVerifiedLine: true,
          }),
          show: { '0': true },
        },
        successful: true,
      };

      render(<MapComponent />);

      const chip = screen.getByTestId('route-provenance');
      expect(chip).toHaveAttribute('data-provenance', 'client');
      expect(chip).toHaveTextContent('построена в приложении');
    });

    it('says "no verified line" instead of showing a different one', () => {
      mockDirectionsState.current = {
        ...mockDirectionsState.current,
        results: {
          data: routeFixture({
            decodedGeometry: [],
            summary: { length: 0, time: 0 },
            source: 'agent',
            hasVerifiedLine: false,
          }),
          show: { '0': true },
        },
        successful: true,
      };

      render(<MapComponent />);

      const chip = screen.getByTestId('route-provenance');
      expect(chip).toHaveAttribute('data-provenance', 'agent');
      expect(chip).toHaveAttribute('data-verified-line', 'false');
      expect(chip).toHaveTextContent('без проверенной линии');
    });

    it('shows no provenance while there is no route', () => {
      render(<MapComponent />);

      expect(screen.queryByTestId('route-provenance')).not.toBeInTheDocument();
    });
  });

  describe('route stop numbering', () => {
    const stop = (id: string, name: string, lng: number, lat: number) => ({
      id,
      userInput: name,
      placeId: 42,
      geocodeResults: [
        {
          title: name,
          selected: true,
          displaylnglat: [lng, lat],
          sourcelnglat: [lng, lat],
          key: 0,
          addressindex: 0,
        },
      ],
    });

    it('numbers the stops from 1 and leaves "my location" unnumbered', () => {
      mockDirectionsState.current = {
        ...mockDirectionsState.current,
        waypoints: [
          {
            id: 'me',
            userInput: 'Моё местоположение',
            geocodeResults: [
              {
                title: 'Моё местоположение',
                selected: true,
                displaylnglat: [23.8, 53.9],
                sourcelnglat: [23.8, 53.9],
                key: 0,
                addressindex: 0,
              },
            ],
          },
          stop('0', 'Костёл', 23.81, 53.91),
          stop('1', 'Замок', 23.82, 53.92),
        ],
      };

      render(<MapComponent />);

      expect(screen.getAllByTestId('marker')).toHaveLength(3);
      expect(screen.getByLabelText('Старт')).toBeInTheDocument();
      expect(screen.getByLabelText('Точка 1')).toBeInTheDocument();
      expect(screen.getByLabelText('Точка 2')).toBeInTheDocument();
      // The route start never takes a stop number.
      expect(screen.queryByLabelText('Точка 3')).not.toBeInTheDocument();
    });
  });

  describe('tiles tab behavior', () => {
    beforeEach(() => {
      mockUseParams.mockReturnValue({ activeTab: 'tiles' });
      mockQueryRenderedFeatures.mockClear();
      mockGetLayer.mockClear();
    });

    afterEach(() => {
      mockUseParams.mockReturnValue({ activeTab: 'directions' });
    });

    it('should show tiles info popup when clicking on tiles with features', async () => {
      vi.useFakeTimers();
      mockGetLayer.mockReturnValue(true);
      mockQueryRenderedFeatures.mockReturnValue([
        {
          type: 'Feature',
          sourceLayer: 'edges',
          properties: { id: '123', speed: 50 },
          geometry: {
            type: 'LineString',
            coordinates: [
              [0, 0],
              [1, 1],
            ],
          },
          layer: { id: 'valhalla-edges' },
        },
      ]);

      render(<MapComponent />);

      fireEvent.click(screen.getByTestId('map'));

      // advance timers past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      expect(screen.getByTestId('tiles-info-popup')).toBeInTheDocument();

      vi.useRealTimers();
    });

    it('should not show tiles info popup when no features are found', async () => {
      vi.useFakeTimers();
      mockGetLayer.mockReturnValue(true);
      mockQueryRenderedFeatures.mockReturnValue([]);

      render(<MapComponent />);

      fireEvent.click(screen.getByTestId('map'));

      // advance timers past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      expect(screen.queryByTestId('tiles-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });

    it('should not query features when valhalla layers do not exist', async () => {
      vi.useFakeTimers();
      mockGetLayer.mockReturnValue(undefined);

      render(<MapComponent />);

      fireEvent.click(screen.getByTestId('map'));

      // advance timers past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      expect(mockQueryRenderedFeatures).not.toHaveBeenCalled();
      expect(screen.queryByTestId('tiles-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });

    it('should only query available layers when some layers exist', async () => {
      vi.useFakeTimers();
      // Only edges layer exists, nodes layer does not
      mockGetLayer.mockImplementation((layerId?: string) =>
        layerId === 'valhalla-edges' ? { id: 'valhalla-edges' } : undefined
      );
      mockQueryRenderedFeatures.mockReturnValue([
        {
          type: 'Feature',
          sourceLayer: 'edges',
          properties: { id: '123' },
          geometry: {
            type: 'LineString',
            coordinates: [
              [0, 0],
              [1, 1],
            ],
          },
          layer: { id: 'valhalla-edges' },
        },
      ]);

      render(<MapComponent />);

      fireEvent.click(screen.getByTestId('map'));

      // advance timers past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      expect(mockQueryRenderedFeatures).toHaveBeenCalledWith(
        { x: 100, y: 100 },
        { layers: ['valhalla-edges'] }
      );

      vi.useRealTimers();
    });

    it('should close tiles info popup when close button is clicked', async () => {
      vi.useFakeTimers();
      mockGetLayer.mockReturnValue(true);
      mockQueryRenderedFeatures.mockReturnValue([
        {
          type: 'Feature',
          sourceLayer: 'edges',
          properties: { id: '123' },
          geometry: {
            type: 'LineString',
            coordinates: [
              [0, 0],
              [1, 1],
            ],
          },
          layer: { id: 'valhalla-edges' },
        },
      ]);

      render(<MapComponent />);

      fireEvent.click(screen.getByTestId('map'));

      // advance timers past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      expect(screen.getByTestId('tiles-info-popup')).toBeInTheDocument();

      vi.useRealTimers();

      const user = userEvent.setup();
      await user.click(
        screen.getByRole('button', { name: 'Close Tiles Popup' })
      );

      await waitFor(() => {
        expect(
          screen.queryByTestId('tiles-info-popup')
        ).not.toBeInTheDocument();
      });
    });

    it('should not show context menu on right click in tiles tab', () => {
      render(<MapComponent />);

      fireEvent.contextMenu(screen.getByTestId('map'));

      // Context menu should not appear in tiles tab
      expect(screen.queryByTestId('map-context-menu')).not.toBeInTheDocument();
    });

    it('should not show info popup on click in tiles tab', async () => {
      vi.useFakeTimers();
      mockGetLayer.mockReturnValue(undefined);
      mockQueryRenderedFeatures.mockReturnValue([]);

      render(<MapComponent />);

      fireEvent.click(screen.getByTestId('map'));

      // advance timers past the click delay
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      // should not show info popup (only tiles popup behavior)
      expect(screen.queryByTestId('map-info-popup')).not.toBeInTheDocument();

      vi.useRealTimers();
    });
  });
});
