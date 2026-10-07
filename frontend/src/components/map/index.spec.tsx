import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  waitFor,
  fireEvent,
  act,
  cleanup,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MapComponent } from './index';
import type { ParsedDirectionsGeometry } from '@/components/types';

const mockToast = vi.hoisted(() => ({
  error: vi.fn(),
}));

/** Panel state the mocked common store reports; individual tests flip it. */
const mockCommonState = {
  directionsPanelOpen: true,
  guiding: false,
  guideVoiceMuted: false,
  guideFix: null as {
    lng: number;
    lat: number;
    heading: number;
    course?: number;
  } | null,
};

/** The panel's handle toggles the store, so the toggle is a spy here. */
const mockToggleDirections = vi.hoisted(() => vi.fn());
const mockSetGuideVoiceMuted = vi.hoisted(() =>
  vi.fn((muted: boolean) => {
    mockCommonState.guideVoiceMuted = muted;
  })
);

const mockQueryRenderedFeatures = vi.hoisted(() =>
  vi.fn(() => [] as unknown[])
);
const mockGetLayer = vi.hoisted(() => vi.fn((): unknown => true));
const mockGetMap = vi.hoisted(() =>
  vi.fn(() => ({
    getCanvas: vi.fn(() => ({ style: { cursor: '' } })),
    queryRenderedFeatures: mockQueryRenderedFeatures,
    getLayer: mockGetLayer,
    on: vi.fn(),
    off: vi.fn(),
  }))
);
const mockMapRef = vi.hoisted(() => ({
  getCenter: vi.fn(() => ({ lng: 13.4, lat: 52.5 })),
  getZoom: vi.fn(() => 10),
  fitBounds: vi.fn(),
  easeTo: vi.fn(),
  isEasing: vi.fn(() => false),
  getMap: mockGetMap,
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
          attributionControl?: boolean | { compact?: boolean };
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
            data-attribution-control={JSON.stringify(props.attributionControl)}
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
    // `onClick` is forwarded so a test can tap a marker the way the tourist
    // does. Dropping it made every marker inert, which is exactly the
    // interaction the place card hangs off.
    Marker: vi.fn(({ children, longitude, latitude, onClick }) => (
      <div
        data-testid="marker"
        data-lng={longitude}
        data-lat={latitude}
        onClick={onClick}
      >
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
      coordinates: [] as unknown[],
      get directionsPanelOpen() {
        return mockCommonState.directionsPanelOpen;
      },
      settingsPanelOpen: false,
      toggleDirections: mockToggleDirections,
      updateSettings: vi.fn(),
      focus: null,
      get guiding() {
        return mockCommonState.guiding;
      },
      get guideFix() {
        return mockCommonState.guideFix;
      },
      get guideVoiceMuted() {
        return mockCommonState.guideVoiceMuted;
      },
      focusOn: vi.fn(),
      setGuideFix: vi.fn(),
      setGuiding: vi.fn(),
      setGuideVoiceMuted: mockSetGuideVoiceMuted,
      placesVisible: false,
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
      data: null as
        | import('@/components/types').ParsedDirectionsGeometry
        | null,
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

vi.mock('@/hooks/use-places', () => ({
  usePlaces: vi.fn(() => ({
    places: [],
    total: 0,
    capped: false,
    isLoading: false,
    error: null,
    reload: vi.fn(),
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
  const actual = await vi.importActual<typeof import('./parts/route-lines')>(
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
        title={title}
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
    mockCommonState.guiding = false;
    mockCommonState.guideVoiceMuted = false;
    mockCommonState.guideFix = null;
    mockDirectionsState.current = {
      waypoints: [],
      results: { data: null, show: {} },
      successful: false,
      placeDetails: {},
      activeRouteIndex: 0,
    };
  });

  afterEach(() => {
    // `cleanup()` explicitly: vitest runs here without `globals: true`, so
    // Testing Library's automatic afterEach never registers, and this file's
    // markers and popups would otherwise be left in the document shared by the
    // rest of the worker.
    cleanup();
    vi.restoreAllMocks();
  });

  it('should render without crashing', () => {
    expect(() => render(<MapComponent />)).not.toThrow();
  });

  it('should render the map container', () => {
    render(<MapComponent />);
    expect(screen.getByTestId('map')).toBeInTheDocument();
    expect(screen.getByTestId('map')).toHaveAttribute(
      'data-attribution-control',
      JSON.stringify({ compact: false })
    );
  });

  // The four controls that used to sit in the map's top-right corner are gone.
  // They were chrome borrowed from a route planner — zoom, geolocate, a polygon
  // excluder, a style switcher — and the owner read them as a second, native row
  // of buttons competing with the panel's own controls in the same corner. The
  // map keeps gestures (pinch, scroll, drag) and the panel keeps its own «use my
  // location», so nothing became unreachable. This test keeps the corner empty:
  // one of them quietly coming back is the regression to catch.
  it('держит верхний правый угол карты без кнопок', () => {
    render(<MapComponent />);

    for (const id of [
      'navigation-control',
      'geolocate-control',
      'draw-control',
      'map-style-control',
    ]) {
      expect(screen.queryByTestId(id)).not.toBeInTheDocument();
    }
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

  // The panel's handle lives on the panel's own edge — the line the resize grip
  // sits on — not in a corner of the map. It says what it does out loud only to
  // a screen reader; visually it is a chevron pointing the way the panel moves.
  it('держит ручку панели на её собственном крае', () => {
    render(<MapComponent />);

    const handle = screen.getByTestId('panel-toggle');
    expect(handle).toHaveAttribute(
      'aria-label',
      'открыть или закрыть панель маршрута'
    );
    expect(handle.className).toContain('top-1/2');
    expect(handle.className).toContain('--panel-width');
  });

  // On a phone the panel is a sheet across the bottom, so it has no vertical
  // left edge for an edge-handle to stand on — and the clamped position parked
  // it in the middle of the map as a floating tab. The way in and out there is
  // the sheet's own grab bar plus a flick down to dismiss (MobileShell); this
  // test pins that the desktop control stays off a phone.
  it('на телефоне ручка панели не рисуется: у шторки нет левого края', () => {
    render(<MapComponent />);

    const handle = screen.getByTestId('panel-toggle');
    expect(handle.className).toContain('hidden');
    expect(handle.className).toContain('md:flex');
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

    // The panel's handle is the only entry point the map keeps, and it is not
    // in this corner: it is on the panel's edge (see the test above).
    expect(screen.queryByTestId('heightgraph-toggle')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('heightgraph-hover-marker')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Open on osm.org' })
    ).not.toBeInTheDocument();
  });

  it('держит ручку панели на месте в обоих состояниях', () => {
    // The pill it replaced disappeared once the panel was open, which is why the
    // header needed a second control to close it. A toggle stays put and means
    // the same thing either way — that is the point of tying it to the panel's
    // edge rather than to the map's corner. jsdom answers every media query with
    // `matches: false`, so the wide case is stated explicitly.
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;

    try {
      mockCommonState.directionsPanelOpen = true;
      render(<MapComponent />);
      expect(screen.getByTestId('panel-toggle')).toHaveAttribute(
        'aria-expanded',
        'true'
      );

      cleanup();

      mockCommonState.directionsPanelOpen = false;
      render(<MapComponent />);
      expect(screen.getByTestId('panel-toggle')).toHaveAttribute(
        'aria-expanded',
        'false'
      );
    } finally {
      mockCommonState.directionsPanelOpen = true;
      window.matchMedia = original;
    }
  });

  it('ручка открывает панель, когда её нет, и закрывает, когда она есть', async () => {
    const mockNavigate = vi.fn();
    const router = await import('@tanstack/react-router');
    vi.mocked(router.useNavigate).mockReturnValue(mockNavigate);
    const user = userEvent.setup();

    // Open: the handle closes it and does not lead anywhere — closing is the
    // whole action, and wandering to the directions tab would be a side effect.
    mockCommonState.directionsPanelOpen = true;
    render(<MapComponent />);
    await user.click(screen.getByTestId('panel-toggle'));
    expect(mockToggleDirections).toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();

    cleanup();

    // Closed: the handle opens it and brings the panel's own tab into view.
    mockToggleDirections.mockClear();
    mockCommonState.directionsPanelOpen = false;
    try {
      render(<MapComponent />);
      await user.click(screen.getByTestId('panel-toggle'));
      expect(mockToggleDirections).toHaveBeenCalled();
      expect(mockNavigate).toHaveBeenCalledWith({
        params: { activeTab: 'directions' },
      });
    } finally {
      mockCommonState.directionsPanelOpen = true;
    }
  });

  // ── Reading about a point ────────────────────────────────────────────────
  //
  // One place has exactly one reader on screen: the popup by the pin on a wide
  // screen, the card at the bottom of the map on a phone. Rendering both would
  // put the same text about the same point on the display twice.

  const withAPlace = (placeId: number) => {
    mockDirectionsState.current.placeDetails = {
      [placeId]: {
        name: 'Старый замок',
        category: 'замок',
        blurb: 'Резиденция великих князей.',
        funFact: 'Дошла лишь одна башня.',
        funFacts: [],
        links: [],
        visitMinutes: 30,
      },
    };
    mockDirectionsState.current.waypoints = [
      {
        id: 'wp-1',
        placeId,
        userInput: 'Старый замок',
        geocodeResults: [
          {
            selected: true,
            sourcelnglat: [23.83, 53.69],
            displaylnglat: [23.83, 53.69],
            title: 'Старый замок',
          },
        ],
      },
    ];
  };

  // The owner read «по пути: 12 мест» on a phone as noise to get past: it began
  // as a 176x130 card over the map and was already down to one line by the time
  // it was removed. On a phone the map now carries nothing but the map.
  //
  // What stays is the marks themselves — `/routes/services` still runs and a
  // café still opens its own card when tapped. Only the chrome about them went.
  // The alternative was marks that could not be reached at all.
  it('на телефоне не показывает ничего о местах рядом, но сами метки рисует', async () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes('767'),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;

    try {
      render(<MapComponent />);
      const isPhone = () => window.matchMedia('(max-width: 767px)').matches;

      // No count block, no card, no button to reveal anything.
      expect(
        screen.queryByTestId('mobile-services-chip')
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId('services-summary')).not.toBeInTheDocument();
      expect(screen.queryByTestId('services-toggle')).not.toBeInTheDocument();
      expect(isPhone()).toBe(true);
    } finally {
      window.matchMedia = original;
    }
  });

  // The «по пути: 12 мест» block is gone from EVERY screen, the wide one too:
  // it sat above the guide's own compass, and the count it printed is not lost —
  // `/routes/services` still runs and the marks still say their distance and
  // hours when tapped. What remains is the bare button that turns the marks on,
  // which is what it was before the card grew around it.
  it('не рисует сводку «по пути» ни на телефоне, ни на десктопе', async () => {
    render(<MapComponent />);

    expect(screen.queryByTestId('services-summary')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('services-summary-count')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('services-summary-category')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('services-summary-capped')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('services-summary-empty')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('mobile-services-chip')
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('mobile-services-capped')
    ).not.toBeInTheDocument();
  });

  it('оставляет кнопку меток: без неё они недостижимы', async () => {
    // The card is gone, but the marks it used to reveal still need a door.
    // It rides with the route, so the test has to have one.
    mockDirectionsState.current.results.data = {
      legs: [],
      summary: { length: 1000, time: 600 },
      shape: {
        type: 'LineString',
        coordinates: [
          [23.8, 53.6],
          [23.9, 53.7],
        ],
      },
      waypoints: [],
    } as unknown as typeof mockDirectionsState.current.results.data;

    render(<MapComponent />);

    const toggle = screen.queryByTestId('services-toggle');
    expect(toggle).not.toBeNull();
    expect(toggle).toHaveAttribute('title', 'что рядом по пути');
  });

  it('на широком экране о месте читает всплывающая карточка у метки', async () => {
    const user = userEvent.setup();
    withAPlace(7);
    render(<MapComponent />);

    await user.click(screen.getByTestId('marker'));

    expect(screen.getByTestId('popup')).toBeInTheDocument();
    expect(screen.queryByTestId('mobile-place-card')).not.toBeInTheDocument();
  });

  it('на телефоне о месте читает карточка у нижнего края карты', async () => {
    // A 340px popup anchored to a pin is 87 % of a 390px screen, sits wherever
    // the pin happens to be — often under the map's own controls — and is read
    // with a thumb over its bottom. Every phone map app puts the place card on
    // the bottom of the map instead, and so does this one.
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes('767'),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;

    try {
      const user = userEvent.setup();
      withAPlace(7);
      render(<MapComponent />);

      await user.click(screen.getByTestId('marker'));

      const card = screen.getByTestId('mobile-place-card');
      expect(card).toHaveTextContent('Старый замок');
      expect(card).toHaveTextContent('Дошла лишь одна башня.');
      // And not the popup as well.
      expect(screen.queryByTestId('popup')).not.toBeInTheDocument();
    } finally {
      window.matchMedia = original;
    }
  });

  it('название выбранной точки остаётся на карте, остальные уходят сами', async () => {
    const user = userEvent.setup();
    withAPlace(7);
    render(<MapComponent />);

    await user.click(screen.getByTestId('marker'));

    // The tapped point's caption is being read on purpose, so it opts out of
    // the 6s auto-hide; nothing else on the map changes.
    expect(screen.getByTestId('place-marker-label')).toHaveAttribute(
      'data-active',
      'true'
    );
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

  // The map's own GeolocateControl is gone with the rest of the corner. The
  // tourist still has geolocation — the panel's «use my location» races the
  // browser's answer against its own timer and reports failure inside the panel
  // (see the sidebar specs) — so no user-facing feedback was lost with the
  // control, and the toasts it used to raise are gone with it.

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

  describe('map orientation (the map always turns with the walk)', () => {
    // The mock store holds `mockCommonState` at module level; individual tests
    // flip its `guiding` / `guideFix` fields so the helpers reuse the same
    // pattern as the rest of the suite.
    const withGuiding = () => {
      mockCommonState.guiding = true;
      mockCommonState.guideFix = { lng: 23.8, lat: 53.9, heading: 90 };
    };

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
      mockCommonState.guiding = false;
      mockCommonState.guideFix = null;
    });

    it('больше не предлагает переключать ориентацию', () => {
      withGuiding();
      render(<MapComponent />);

      // The compass button is gone: while walking there is nothing to choose
      // between — the map turns with the route or it is not a navigator.
      expect(screen.queryByTestId('guide-orientation')).not.toBeInTheDocument();
    });

    it('кнопка слежения остаётся на своём месте', () => {
      withGuiding();
      render(<MapComponent />);

      expect(screen.getByTestId('guide-follow')).toBeInTheDocument();
    });

    it('держит переключатель звука рядом с остальными кнопками навигатора', () => {
      withGuiding();
      render(<MapComponent />);

      const sound = screen.getByTestId('guide-voice-toggle-map');
      expect(sound).toHaveAttribute('aria-label', 'выключить звук');
      fireEvent.click(sound);
      expect(mockSetGuideVoiceMuted).toHaveBeenCalledWith(true);
    });

    it('keeps location and nearby controls clear of the phone navigation HUD', () => {
      const originalWidth = window.innerWidth;
      Object.defineProperty(window, 'innerWidth', {
        configurable: true,
        value: 390,
      });
      withGuiding();
      mockDirectionsState.current.results.data = {
        legs: [],
        summary: { length: 1000, time: 600 },
        shape: {
          type: 'LineString',
          coordinates: [
            [23.8, 53.6],
            [23.9, 53.7],
          ],
        },
        waypoints: [],
      } as unknown as typeof mockDirectionsState.current.results.data;

      try {
        render(<MapComponent />);

        const controls = screen.getByTestId('map-controls');
        expect(controls.className).toContain('z-10');
        expect(controls.className).toContain('left-3');
        expect(screen.getByTestId('guide-follow')).toBeInTheDocument();
        expect(screen.getByTestId('services-toggle')).toBeInTheDocument();
        expect(controls).toContainElement(screen.getByTestId('guide-follow'));
        expect(controls).toContainElement(
          screen.getByTestId('services-toggle')
        );
      } finally {
        Object.defineProperty(window, 'innerWidth', {
          configurable: true,
          value: originalWidth,
        });
      }
    });

    it('easeTo ведёт карту по курсу маршрута', () => {
      withGuiding();
      // The route's own course wins over the device's heading.
      mockCommonState.guideFix = {
        lng: 23.8,
        lat: 53.9,
        heading: 10,
        course: 90,
      };
      mockMapRef.easeTo = vi.fn();
      render(<MapComponent />);

      const call = mockMapRef.easeTo.mock.calls
        .map((c) => c[0])
        .find((o) => o && 'bearing' in o);
      expect(call).toBeDefined();
      expect(call!.bearing).toBe(90);
    });

    it('без курса берёт направление устройства', () => {
      withGuiding();
      mockMapRef.easeTo = vi.fn();
      render(<MapComponent />);

      const call = mockMapRef.easeTo.mock.calls
        .map((c) => c[0])
        .find((o) => o && 'bearing' in o);
      expect(call).toBeDefined();
      expect(call!.bearing).toBe(90);
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

    it('молчит про линию, которую приложение нарисовало само', () => {
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

      // A line this app drew itself is the ordinary case, and the owner read the
      // badge saying so as noise to scroll past. Only the two cases that carry
      // information speak: where a verified line came from, and an agent plan
      // whose line is missing.
      expect(screen.queryByTestId('route-provenance')).not.toBeInTheDocument();
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
