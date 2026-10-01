import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import type { MapGeoJSONFeature } from 'maplibre-gl';
import { useParams, useSearch, useNavigate } from '@tanstack/react-router';
import { Map, Marker, Popup, type MapRef } from 'react-map-gl/maplibre';
import type { MaplibreTerradrawControl } from '@watergis/maplibre-gl-terradraw';
import type maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

import type { Summary } from '@/components/types';
import { PanelToggle } from './parts/panel-toggle';

import { ToolButton } from './parts/tool-button';
import { getInitialMapStyle, getCustomStyle, getMapStyleUrl } from './utils';
import { CLICK_DELAY_MS, DOUBLE_TAP_THRESHOLD_MS } from './constants';
import type { MapStyleType } from './types';
import {
  isMissingVerifiedLine,
  routeProvenance,
  RouteLines,
} from './parts/route-lines';
import { HighlightSegment } from './parts/highlight-segment';
import { IsochronePolygons } from './parts/isochrone-polygons';
import { IsochroneLocations } from './parts/isochrone-locations';
import { RouteHoverPopup } from './parts/route-hover-popup';
import { MapContextMenu } from './parts/map-context-menu';
import { TilesInfoPopup } from './parts/tiles-info-popup';
import {
  VALHALLA_EDGES_LAYER_ID,
  VALHALLA_NODES_LAYER_ID,
  VALHALLA_SHORTCUTS_LAYER_ID,
  VALHALLA_ACCESS_RESTRICTIONS_PERMANENT_LAYER_ID,
  VALHALLA_ACCESS_RESTRICTIONS_TIMED_LAYER_ID,
} from '@/components/tiles/valhalla-layers';
import { MarkerIcon, type MarkerColor } from './parts/marker-icon';
import { ServicesLayer } from './parts/services-layer';
import { ServicesSummary } from './parts/services-summary';
import { PlaceCardPopup } from './parts/place-card-popup';
import { PlaceMarkerLabel } from './parts/place-marker-label';
import { maxBounds } from './constants';
import { getInitialMapPosition, LAST_CENTER_KEY } from './utils';
import { useCommonStore } from '@/stores/common-store';
import { useTranslation } from 'react-i18next';
import { Coffee, LocateFixed, Navigation } from 'lucide-react';
import { ME_WAYPOINT_ID, useDirectionsStore } from '@/stores/directions-store';
import { useServicesAlong } from '@/hooks/use-services-along';
import { useIsochronesStore } from '@/stores/isochrones-store';
import {
  useDirectionsQuery,
  useSetWaypointFromCoords,
} from '@/hooks/use-directions-queries';
import {
  useIsochronesQuery,
  useReverseGeocodeIsochrones,
} from '@/hooks/use-isochrones-queries';

const { center, zoom: zoom_initial } = getInitialMapPosition();

interface MarkerData {
  id: string;
  lng: number;
  lat: number;
  type: 'waypoint' | 'isocenter';
  index?: number;
  title?: string;
  color?: MarkerColor;
  shape?: string;
  number?: string;
  // Set for agent-generated stops: drives the label + card next to the marker.
  placeId?: number;
}

export const MapComponent = () => {
  const { activeTab } = useParams({ from: '/$activeTab' });
  const navigate = useNavigate({ from: '/$activeTab' });
  const coordinates = useCommonStore((state) => state.coordinates);
  const directionsPanelOpen = useCommonStore(
    (state) => state.directionsPanelOpen
  );
  const toggleDirections = useCommonStore((state) => state.toggleDirections);
  // On a wide screen the panel is docked at the top-left — exactly where the
  // planner entry pill sits — so rendering both paints the pill over the
  // panel's own title and tabs. The pill is the front door only while the
  // panel is out of the way; on a phone the panel is a bottom sheet, so the
  // pill must stay regardless.
  const setMapReady = useCommonStore((state) => state.setMapReady);
  const { style } = useSearch({ from: '/$activeTab' });
  const [showContextPopup, setShowContextPopup] = useState(false);
  const [popupLngLat, setPopupLngLat] = useState<{
    lng: number;
    lat: number;
  } | null>(null);
  const waypoints = useDirectionsStore((state) => state.waypoints);
  const placeDetails = useDirectionsStore((state) => state.placeDetails);
  const routeResult = useDirectionsStore((state) => state.results.data);
  // Off by default and asked for by hand: a guide that *marks* cafés uninvited
  // stops being a guide. The measurement itself runs whenever there is a route,
  // because the count is what the guide owes the tourist («по пути: 12 мест») —
  // it is the marks appearing unasked that would be pushy, not the number.
  const [showServices, setShowServices] = useState(false);
  const services = useServicesAlong(routeResult, {
    enabled: Boolean(routeResult),
  });
  const setActiveRouteIndex = useDirectionsStore(
    (state) => state.setActiveRouteIndex
  );

  // Which line is on screen, stated on the map: the plan the backend verified,
  // or the one this app routed for a hand-built route. An agent route without a
  // verified line says so instead of showing a substitute.
  const { t } = useTranslation();
  const provenance = routeProvenance(routeResult);
  const missingVerifiedLine = isMissingVerifiedLine(routeResult);

  const { refetch: refetchDirections } = useDirectionsQuery();
  const { refetch: refetchIsochrones } = useIsochronesQuery();
  const { setWaypointFromCoords } = useSetWaypointFromCoords();
  const { reverseGeocode: reverseGeocodeIsochrones } =
    useReverseGeocodeIsochrones();
  const [routeHoverPopup, setRouteHoverPopup] = useState<{
    lng: number;
    lat: number;
    summary: Summary;
  } | null>(null);
  const [tilesPopup, setTilesPopup] = useState<{
    lng: number;
    lat: number;
    features: MapGeoJSONFeature[];
  } | null>(null);
  // Which agent-generated stop currently shows its info card.
  const [activePlace, setActivePlace] = useState<{
    id: number;
    lng: number;
    lat: number;
  } | null>(null);
  const activeDetails =
    activePlace != null ? placeDetails[activePlace.id] : undefined;
  const [viewState, setViewState] = useState({
    longitude: center[0],
    latitude: center[1],
    zoom: zoom_initial,
  });
  const [currentMapStyle] = useState<MapStyleType>(getInitialMapStyle(style));
  // Selectable from the URL only: the map's own style switcher was one of the
  // controls in the top-right cluster the owner asked to remove, and a style is
  // chosen once, not while walking.
  const [customStyleData] = useState<maplibregl.StyleSpecification | null>(() =>
    getCustomStyle()
  );

  const resolvedMapStyle = useMemo(() => {
    if (currentMapStyle === 'custom') {
      return customStyleData ?? getMapStyleUrl('shortbread');
    }
    return getMapStyleUrl(currentMapStyle);
  }, [
    currentMapStyle,
    customStyleData,
  ]) as unknown as maplibregl.StyleSpecification;

  const mapRef = useRef<MapRef>(null);
  const focusRequest = useCommonStore((s) => s.focus);
  const guideFix = useCommonStore((s) => s.guideFix);
  /** Metres to the next turn — the guide publishes it, the camera acts on it. */
  const guideTurnDistanceM = useCommonStore((s) => s.guideTurnDistanceM);
  /** The bearing in force, so small course wobbles do not turn the map. */
  const bearingRef = useRef<number | null>(null);
  /** When the tourist last dragged the map away — following returns after a pause. */
  const panAwayRef = useRef(0);
  const ORIENTATION_STORAGE_KEY = 'grodno-map-orientation';
  type MapOrientation = 'heading' | 'north';

  /** Map orientation preference, persisted so the tourist's choice survives a reload. */
  const loadOrientation = (): MapOrientation => {
    try {
      const raw = localStorage.getItem(ORIENTATION_STORAGE_KEY);
      if (raw === 'north' || raw === 'heading') return raw;
      return 'heading';
    } catch {
      return 'heading';
    }
  };

  const saveOrientation = (orientation: MapOrientation) => {
    try {
      localStorage.setItem(ORIENTATION_STORAGE_KEY, orientation);
    } catch {
      // private mode / quota — the value stays in memory for this session
    }
  };

  const guiding = useCommonStore((s) => s.guiding);
  /** Navigator mode: the map keeps the tourist in view until a hand moves it. */
  const [follow, setFollow] = useState(true);
  /**
   * Map orientation: 'heading' = map rotates with the tourist's course,
   * 'north' = north is always up (bearing = 0). Persisted to localStorage.
   */
  const [orientation, setOrientation] =
    useState<MapOrientation>(loadOrientation);
  const drawRef = useRef<MaplibreTerradrawControl | null>(null);
  const touchStartTimeRef = useRef<number | null>(null);
  const touchLocationRef = useRef<{ x: number; y: number } | null>(null);
  const handledLongPressRef = useRef<boolean>(false);
  const clickStateRef = useRef<{
    timer: ReturnType<typeof setTimeout> | null;
    pendingLngLat: { lng: number; lat: number } | null;
    lastTapTime: number;
  }>({
    timer: null,
    pendingLngLat: null,
    lastTapTime: 0,
  });
  // Tracks whether the last click was on a route marker — if so, suppress the
  // generic map-info popup (avoid two modals at once).
  const markerClickRef = useRef(false);

  const cancelPendingClick = useCallback(() => {
    if (clickStateRef.current.timer) {
      clearTimeout(clickStateRef.current.timer);
      clickStateRef.current.timer = null;
      clickStateRef.current.pendingLngLat = null;
    }
  }, []);

  const updateWaypointPosition = useCallback(
    (object: { latLng: { lat: number; lng: number }; index: number }) => {
      setWaypointFromCoords(
        object.latLng.lng,
        object.latLng.lat,
        object.index
      ).then(() => {
        refetchDirections();
      });
    },
    [setWaypointFromCoords, refetchDirections]
  );

  const updateIsoPosition = useCallback(
    (lng: number, lat: number) => {
      reverseGeocodeIsochrones(lng, lat).then(() => {
        refetchIsochrones();
      });
    },
    [reverseGeocodeIsochrones, refetchIsochrones]
  );

  /** The panel's handle does both: it closes what is open, opens what is not. */
  const handlePanelToggle = useCallback(() => {
    toggleDirections();
    if (!directionsPanelOpen) {
      navigate({ params: { activeTab: 'directions' } });
    }
  }, [directionsPanelOpen, toggleDirections, navigate]);

  const handleAddWaypoint = useCallback(
    (index: number) => {
      if (!popupLngLat) return;
      setShowContextPopup(false);

      updateWaypointPosition({
        latLng: { lat: popupLngLat.lat, lng: popupLngLat.lng },
        index,
      });
    },
    [popupLngLat, updateWaypointPosition]
  );

  const handleAddIsoWaypoint = useCallback(() => {
    if (!popupLngLat) return;
    setShowContextPopup(false);
    updateIsoPosition(popupLngLat.lng, popupLngLat.lat);
  }, [popupLngLat, updateIsoPosition]);

  // Update markers when waypoints or isochrone centers change
  const geocodeResults = useIsochronesStore((state) => state.geocodeResults);
  const markers = useMemo(() => {
    const newMarkers: MarkerData[] = [];

    // Add waypoint markers. The "my location" waypoint (the route start the
    // sidebar pins) is drawn as its own blue pin and does not take a number, so
    // the tourist's stops stay numbered from 1.
    const poiWaypoints = waypoints.filter((w) => w.id !== ME_WAYPOINT_ID);
    waypoints
      .filter((w) => w.id === ME_WAYPOINT_ID)
      .forEach((waypoint) => {
        waypoint.geocodeResults.forEach((address) => {
          if (!address.selected) return;
          newMarkers.push({
            id: ME_WAYPOINT_ID,
            lng: address.displaylnglat[0],
            lat: address.displaylnglat[1],
            type: 'waypoint',
            index: 0,
            title: t('sidebar.ui.myLocation'),
            color: 'blue',
          });
        });
      });

    poiWaypoints.forEach((waypoint, index) => {
      // The store index (for dragging) differs from the displayed number once a
      // "my location" waypoint sits at the front.
      const sourceIndex = waypoints.indexOf(waypoint);
      const isOrigin = index === 0;
      const isDestination =
        index === poiWaypoints.length - 1 && poiWaypoints.length > 1;
      const color: MarkerColor = isOrigin
        ? 'green'
        : isDestination
          ? 'red'
          : 'grey';
      waypoint.geocodeResults.forEach((address) => {
        if (address.selected) {
          newMarkers.push({
            id: `waypoint-${sourceIndex}`,
            lng: address.displaylnglat[0],
            lat: address.displaylnglat[1],
            type: 'waypoint',
            index: sourceIndex,
            title: address.title,
            color,
            number: (index + 1).toString(),
            placeId: waypoint.placeId,
          });
        }
      });
    });

    // Add isochrone center marker
    geocodeResults.forEach((address) => {
      if (address.selected) {
        newMarkers.push({
          id: 'iso-center',
          lng: address.displaylnglat[0],
          lat: address.displaylnglat[1],
          type: 'isocenter',
          title: address.title,
          color: 'purple',
          shape: 'star',
          number: '1',
        });
      }
    });

    return newMarkers;
  }, [waypoints, geocodeResults]);

  //Stores the route content
  const lastZoomedCoordKeyRef = useRef<string | null>(null);

  useEffect(() => {
    //When a route is cleared resets the key to null
    if (!coordinates || coordinates.length === 0) {
      lastZoomedCoordKeyRef.current = null;
      return;
    }

    //If No Cordinates then return early
    if (!mapRef.current) return;

    //First Point
    const firstCoord = coordinates[0];
    if (!firstCoord || !firstCoord[0] || !firstCoord[1]) return;

    //Last Point
    const lastCoord = coordinates[coordinates.length - 1]!;

    const coordKey =
      coordinates.length +
      ':' +
      firstCoord[0] +
      ',' +
      firstCoord[1] +
      ':' +
      lastCoord[0] +
      ',' +
      lastCoord[1];
    //Compare with what was last zoomed
    if (coordKey === lastZoomedCoordKeyRef.current) return;
    //Store thr new Key
    lastZoomedCoordKeyRef.current = coordKey;

    const bounds: [[number, number], [number, number]] = coordinates.reduce<
      [[number, number], [number, number]]
    >(
      (acc, coord) => {
        if (!coord || !coord[0] || !coord[1]) return acc;
        return [
          [Math.min(acc[0][0], coord[1]), Math.min(acc[0][1], coord[0])],
          [Math.max(acc[1][0], coord[1]), Math.max(acc[1][1], coord[0])],
        ];
      },
      [
        [firstCoord[1], firstCoord[0]],
        [firstCoord[1], firstCoord[0]],
      ]
    );

    //Read panel from the store directly
    //avoids re-running the effect when panels open or close
    const state = useCommonStore.getState();
    const dpOpen = state.directionsPanelOpen;
    const spOpen = state.settingsPanelOpen;

    const paddingTopLeft = [
      window.innerWidth < 550 ? 80 : dpOpen ? 450 : 80,
      80,
    ];
    const paddingBottomRight = [
      window.innerWidth < 550 ? 80 : spOpen ? 450 : 80,
      80,
    ];

    mapRef.current.fitBounds(bounds, {
      padding: {
        top: paddingTopLeft[1] as number,
        bottom: paddingBottomRight[1] as number,
        left: paddingTopLeft[0] as number,
        right: paddingBottomRight[0] as number,
      },
      maxZoom: coordinates.length === 1 ? 11 : 18,
    });
    //only rerun when coordinates change
    //panel change no longer rerun this
  }, [coordinates]);

  // ── Panel → map: «покажи мне это место» ──────────────────────────────────
  // Tapping a place in the panel used to do nothing here: the tourist picked a
  // row and then had to find it on the map by hand. The row asks, the map looks.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusRequest) return;
    map.flyTo({
      center: [focusRequest.lng, focusRequest.lat],
      // Keep the tourist's own zoom when it is already closer than a street.
      zoom: Math.max(map.getZoom(), 15.5),
      duration: 900,
      essential: true,
    });
  }, [focusRequest]);

  // ── Guide → map: the navigator behaviour ─────────────────────────────────
  // While the guide runs the map follows the walk: centred on the tourist,
  // tilted, and turned to the heading when the device reports one. Without this
  // the tourist had to hunt for their own position on a still map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !guiding || !follow || !guideFix) return;
    // A new animation on every fix is what made the camera stutter: each one
    // started before the last had finished, so the view was pulled between two
    // targets and snapped. While one is still running, the next fix is simply
    // dropped — the camera is a second behind at worst, never fighting itself.
    if (map.isEasing?.()) return;
    // The panel covers the left of the canvas, so centring on the canvas would
    // park the tourist's dot behind it. The padding puts the dot in the middle
    // of the map the tourist can actually see.
    const panelWidth =
      Number.parseFloat(
        getComputedStyle(document.documentElement).getPropertyValue(
          '--panel-width'
        )
      ) || 0;
    // The heading is read once it has moved far enough to matter. A GPS course
    // wobbles by a few degrees every second and the simulated one jumps at every
    // corner; rotating for those turned the map into a spinning top. Below the
    // threshold the current bearing is kept, above it the map turns once, slowly.
    // In 'north' orientation the bearing is never applied — north stays up.
    const seen = bearingRef.current;
    // The route's own course first, the device's heading only as a fallback:
    // the phone's heading is the direction the handset points, the course is
    // the direction the walk goes.
    const steerTo = guideFix.course ?? guideFix.heading;
    // The turn threshold differs by source. The device's heading wobbles by a
    // few degrees per second, and turning for that made the map a spinning top;
    // the route's course is a clean number that changes only at a bend, so it is
    // followed closely — that is what makes the map turn with the path, segment
    // by segment, the way a navigator does.
    const turnThreshold = guideFix.course != null ? 4 : 15;
    const bearing =
      orientation === 'heading' &&
      steerTo != null &&
      (seen === null ||
        Math.abs(((steerTo - seen + 540) % 360) - 180) > turnThreshold)
        ? steerTo
        : undefined;
    if (bearing !== undefined) bearingRef.current = bearing;

    map.easeTo({
      center: [guideFix.lng, guideFix.lat],
      ...(bearing !== undefined ? { bearing } : {}),
      padding: { left: panelWidth, top: 0, right: 0, bottom: 0 },
      pitch: 45,
      // A navigator leans in at the turn: within 120 m the camera is at street
      // detail, otherwise at walking detail. Never back out — zooming out from
      // under a tourist who has just zoomed in is the camera fighting its user.
      zoom:
        guideTurnDistanceM != null && guideTurnDistanceM <= 120
          ? Math.max(map.getZoom(), 18)
          : Math.max(map.getZoom(), 16.5),
      // Linear, and as long as the gap between fixes: the camera then moves at
      // one steady speed for the whole walk instead of easing in and out on
      // every step, which is what read as jolting.
      duration: 900,
      // Identity easing: constant speed from the first millisecond to the last.
      // The default curve accelerates and brakes inside every step, which is
      // what read as the view lurching and snapping back.
      easing: (progress: number) => progress,
      essential: true,
    });
  }, [guiding, follow, guideFix, orientation, guideTurnDistanceM]);

  // Following starts again every time the guide is entered — and so does
  // heading-up. A navigator has to be aligned with the walk the moment it
  // starts: the stored preference belongs to browsing the map (planning a route,
  // reading a neighbourhood), and a tourist who had left it on «north up» walked
  // the whole route along a map that never turned. Only on entering, though: a
  // reload that already starts inside the guide keeps the choice the tourist
  // made there.
  const previousGuidingRef = useRef<boolean | null>(null);
  useEffect(() => {
    const previous = previousGuidingRef.current;
    previousGuidingRef.current = guiding;
    if (!guiding) return;
    setFollow(true);
    if (previous === false) {
      setOrientation('heading');
      saveOrientation('heading');
    }
  }, [guiding]);

  // When the tourist switches back to 'heading' while the guide is running,
  // the map snaps to the current heading immediately instead of waiting for
  // the next guideFix (which may not come if the tourist is standing still).
  useEffect(() => {
    if (orientation !== 'heading' || !guiding || !guideFix) return;
    const map = mapRef.current;
    if (!map || map.isEasing?.()) return;
    map.easeTo({
      bearing: guideFix.course ?? guideFix.heading ?? 0,
      duration: 300,
    });
  }, [orientation, guiding, guideFix]);

  // A hand on the map wins over the follow: dragging releases it.
  //
  // Only a real hand does. `easeTo` — the very call that makes the map follow —
  // emits dragstart/rotatestart of its own while it turns the camera to the
  // tourist's heading, so treating every such event as user input switched the
  // follow off on its first move: the map centred once and then stood still for
  // the rest of the walk. A user-driven event carries the DOM event with it.
  useEffect(() => {
    const map = mapRef.current?.getMap?.();
    if (!map || !guiding) return;
    const release = (event?: { originalEvent?: unknown }) => {
      if (event?.originalEvent) {
        panAwayRef.current = Date.now();
        setFollow(false);
      }
    };
    map.on('dragstart', release);
    map.on('rotatestart', release);
    return () => {
      map.off('dragstart', release);
      map.off('rotatestart', release);
    };
  }, [guiding]);

  /**
   * A navigator does not sulk. Panning away used to switch the follow off for
   * the rest of the walk, so the guide went on to «показывать маршрут» while the
   * tourist walked off the edge of it — the camera came back only if they found
   * the follow button. It comes back by itself now: eight seconds after the last
   * touch, or at once when a turn is within 60 m, which is the moment the screen
   * has to be showing the street rather than wherever the tourist was peering.
   */
  useEffect(() => {
    if (!guiding) return;
    const id = window.setInterval(() => {
      if (follow) return;
      const idleMs = Date.now() - panAwayRef.current;
      const turnIsHere =
        guideTurnDistanceM != null && guideTurnDistanceM <= 60 && idleMs > 2000;
      if (idleMs > 8000 || turnIsHere) setFollow(true);
    }, 1000);
    return () => window.clearInterval(id);
  }, [guiding, follow, guideTurnDistanceM]);

  const handleMapTilesClick = useCallback(
    (event: maplibregl.MapLayerMouseEvent) => {
      if (!mapRef.current) return;

      const map = mapRef.current.getMap();

      const availableLayers = [
        VALHALLA_EDGES_LAYER_ID,
        VALHALLA_NODES_LAYER_ID,
        VALHALLA_SHORTCUTS_LAYER_ID,
        VALHALLA_ACCESS_RESTRICTIONS_PERMANENT_LAYER_ID,
        VALHALLA_ACCESS_RESTRICTIONS_TIMED_LAYER_ID,
      ].filter((layerId) => map.getLayer(layerId));

      if (availableLayers.length === 0) return;

      const features = map.queryRenderedFeatures(event.point, {
        layers: availableLayers,
      });

      const { lng, lat } = event.lngLat;

      if (features && features.length > 0) {
        setTilesPopup({ lng, lat, features });
      }
    },
    []
  );

  const handleMapClick = useCallback(
    (event: maplibregl.MapLayerMouseEvent) => {
      // Prevent click if we just handled a long press
      if (handledLongPressRef.current) {
        handledLongPressRef.current = false;
        return;
      }

      if (showContextPopup) {
        setShowContextPopup(false);
        return;
      }

      // Check if TerraDraw is in an active drawing mode
      if (drawRef.current) {
        const terraDrawInstance = drawRef.current.getTerraDrawInstance();
        if (terraDrawInstance) {
          const mode = terraDrawInstance.getMode();
          if (
            mode === 'polygon' ||
            mode === 'select' ||
            mode === 'delete-selection'
          ) {
            return;
          }
        }
      }

      // Check if click is on a route line (hit-target layer is the wider,
      // transparent stand-in for routes-line — same source/properties).
      const routeFeature = event.features?.find(
        (f) =>
          f.layer?.id === 'routes-line' || f.layer?.id === 'routes-hit-target'
      );

      if (
        routeFeature &&
        typeof routeFeature.properties?.routeIndex === 'number'
      ) {
        setActiveRouteIndex(routeFeature.properties.routeIndex);
        return;
      }

      const { lngLat } = event;

      cancelPendingClick();

      // store the pending location in ref to avoid stale closure issues
      clickStateRef.current.pendingLngLat = lngLat;

      // delay showing popup to distinguish single click from double-click/double-tap
      clickStateRef.current.timer = setTimeout(() => {
        const pendingLngLat = clickStateRef.current.pendingLngLat;
        if (pendingLngLat) {
          if (activeTab === 'tiles') {
            handleMapTilesClick(event);
          } else if (markerClickRef.current) {
            // The click landed on a place marker: its own card (blurb, fun
            // facts, links) is already open — keep it.
            markerClickRef.current = false;
          } else {
            // Plain click on the tourist map: only clear the selection. Nothing
            // pops up — the coordinate / "Valhalla location JSON" popup was a
            // developer tool and has no place in a walk planner.
            setActivePlace(null);
          }
        }
        clickStateRef.current.timer = null;
        clickStateRef.current.pendingLngLat = null;
      }, CLICK_DELAY_MS);
    },
    [
      showContextPopup,
      cancelPendingClick,
      activeTab,
      handleMapTilesClick,
      setActiveRouteIndex,
      markerClickRef,
    ]
  );

  // handle double-click to cancel the pending single-click popup
  const handleMapDblClick = useCallback(() => {
    cancelPendingClick();
  }, [cancelPendingClick]);

  // cleanup timeout on unmount
  useEffect(() => {
    return () => {
      cancelPendingClick();
    };
  }, [cancelPendingClick]);

  // A new route means new places — drop a stale info card that would otherwise
  // stay pinned to coordinates the route no longer visits.
  useEffect(() => {
    setActivePlace(null);
  }, [placeDetails]);

  const handleMapContextMenu = useCallback(
    (event: { lngLat: { lng: number; lat: number } }) => {
      if (activeTab === 'tiles') return;

      const { lngLat } = event;
      setPopupLngLat(lngLat);
      setShowContextPopup(true);
    },
    [activeTab]
  );

  // Handle move end to save position
  const handleMoveEnd = useCallback(() => {
    if (!mapRef.current) return;
    const { lng, lat } = mapRef.current.getCenter();
    const zoom = mapRef.current.getZoom();

    const last_center = JSON.stringify({
      center: [lat, lng],
      zoom_level: zoom,
    });
    localStorage.setItem(LAST_CENTER_KEY, last_center);
  }, []);

  const handleTouchStart = useCallback(
    (event: maplibregl.MapTouchEvent) => {
      if (activeTab === 'tiles') return;

      const now = Date.now();
      const touchCount = event.originalEvent.touches.length;

      // multi-finger touch (pinch-to-zoom, etc.) - cancel any pending click popup
      if (touchCount > 1) {
        cancelPendingClick();
        return;
      }

      touchStartTimeRef.current = now;
      touchLocationRef.current = { x: event.point.x, y: event.point.y };
      handledLongPressRef.current = false;

      // detect double-tap: if two single-finger taps occur within threshold, cancel pending click
      if (now - clickStateRef.current.lastTapTime < DOUBLE_TAP_THRESHOLD_MS) {
        cancelPendingClick();
      }
      clickStateRef.current.lastTapTime = now;
    },
    [cancelPendingClick, activeTab]
  );

  const handleTouchEnd = useCallback(
    (event: maplibregl.MapTouchEvent) => {
      if (activeTab === 'tiles') return;

      const longTouchTimeMS = 100;
      const acceptableMoveDistance = 20;

      if (touchStartTimeRef.current && touchLocationRef.current) {
        const touchTime = new Date().getTime() - touchStartTimeRef.current;
        const didNotMoveMap =
          Math.abs(event.point.x - touchLocationRef.current.x) <
            acceptableMoveDistance &&
          Math.abs(event.point.y - touchLocationRef.current.y) <
            acceptableMoveDistance;

        if (touchTime > longTouchTimeMS && didNotMoveMap) {
          if (drawRef.current) {
            const terraDrawInstance = drawRef.current.getTerraDrawInstance();
            if (terraDrawInstance) {
              const mode = terraDrawInstance.getMode();
              if (
                mode === 'polygon' ||
                mode === 'select' ||
                mode === 'delete-selection'
              ) {
                touchStartTimeRef.current = null;
                touchLocationRef.current = null;
                return;
              }
            }
          }

          handledLongPressRef.current = true;
          handleMapContextMenu({ lngLat: event.lngLat });
        }
      }

      touchStartTimeRef.current = null;
      touchLocationRef.current = null;
    },
    [handleMapContextMenu, activeTab]
  );

  // Handle route line hover
  const onRouteLineHover = useCallback(
    (event: maplibregl.MapLayerMouseEvent) => {
      if (!mapRef.current) return;

      const map = mapRef.current.getMap();
      map.getCanvas().style.cursor = 'pointer';

      const feature = event.features?.[0];
      if (feature && feature.properties?.summary) {
        // Parse the summary if it's a string
        const summary =
          typeof feature.properties.summary === 'string'
            ? JSON.parse(feature.properties.summary)
            : feature.properties.summary;

        setRouteHoverPopup({
          lng: event.lngLat.lng,
          lat: event.lngLat.lat,
          summary: summary as Summary,
        });
      }
    },
    []
  );

  const handleMouseMove = useCallback(
    (event: maplibregl.MapLayerMouseEvent) => {
      if (!mapRef.current) return;

      const features = event.features;
      // Check if we're hovering over the routes-line / hit-target layer
      const topLayerId = features?.[0]?.layer?.id;
      const isOverRoute =
        topLayerId === 'routes-line' || topLayerId === 'routes-hit-target';

      const isOverTiles =
        features &&
        features.length > 0 &&
        (features[0]?.layer?.id === VALHALLA_EDGES_LAYER_ID ||
          features[0]?.layer?.id === VALHALLA_NODES_LAYER_ID ||
          features[0]?.layer?.id === VALHALLA_SHORTCUTS_LAYER_ID ||
          features[0]?.layer?.id ===
            VALHALLA_ACCESS_RESTRICTIONS_PERMANENT_LAYER_ID ||
          features[0]?.layer?.id ===
            VALHALLA_ACCESS_RESTRICTIONS_TIMED_LAYER_ID);

      if (isOverRoute) {
        onRouteLineHover(event);
      } else if (isOverTiles) {
        const map = mapRef.current.getMap();
        map.getCanvas().style.cursor = 'pointer';
      } else {
        // Clear popup and cursor when not over route
        if (routeHoverPopup) {
          setRouteHoverPopup(null);
        }
        const map = mapRef.current.getMap();
        if (map.getCanvas().style.cursor === 'pointer') {
          map.getCanvas().style.cursor = '';
        }
      }
    },
    [routeHoverPopup, onRouteLineHover]
  );

  const handleMouseLeave = useCallback(() => {
    if (!mapRef.current) return;
    const map = mapRef.current.getMap();
    map.getCanvas().style.cursor = '';
    setRouteHoverPopup(null);
  }, []);

  return (
    <>
      <Map
        ref={mapRef}
        {...viewState}
        onMove={(evt) => setViewState(evt.viewState)}
        onMoveEnd={handleMoveEnd}
        onLoad={() => setMapReady(true)}
        onClick={handleMapClick}
        onDblClick={handleMapDblClick}
        onContextMenu={handleMapContextMenu}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        interactiveLayerIds={
          activeTab === 'tiles'
            ? [
                VALHALLA_EDGES_LAYER_ID,
                VALHALLA_NODES_LAYER_ID,
                VALHALLA_SHORTCUTS_LAYER_ID,
                VALHALLA_ACCESS_RESTRICTIONS_PERMANENT_LAYER_ID,
                VALHALLA_ACCESS_RESTRICTIONS_TIMED_LAYER_ID,
              ]
            : ['routes-line', 'routes-hit-target']
        }
        mapStyle={resolvedMapStyle}
        style={{ width: '100%', height: '100vh' }}
        maxBounds={maxBounds}
        minZoom={2}
        maxZoom={18}
        data-testid="map"
        id="mainMap"
      >
        <RouteLines />
        <HighlightSegment />
        <IsochronePolygons />
        <IsochroneLocations />
        {/* The tourist's own position: an arrow, not a dot — it shows where the
            tourist is headed (course along the route), not just where they are.
            Falls back to the device heading if course is not yet available. */}
        {guiding && guideFix && (
          <Marker
            anchor="center"
            longitude={guideFix.lng}
            latitude={guideFix.lat}
            rotation={guideFix.course ?? guideFix.heading ?? 0}
            rotationAlignment="map"
          >
            <div
              data-testid="guide-position"
              className="relative flex h-10 w-10 items-center justify-center"
            >
              <span className="absolute flex h-10 w-10 items-center justify-center">
                <svg
                  viewBox="0 0 24 24"
                  width="40"
                  height="40"
                  className="drop-shadow-md"
                  aria-hidden="true"
                >
                  <circle
                    cx="12"
                    cy="12"
                    r="10"
                    fill="#3B82F6"
                    opacity="0.2"
                    className="motion-safe:animate-ping"
                  />
                  <path
                    d="M12 2 L20 19 L12 15 L4 19 Z"
                    fill="#2563EB"
                    stroke="white"
                    strokeWidth="1.5"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
            </div>
          </Marker>
        )}

        {markers.map((marker) => {
          const details =
            marker.placeId != null ? placeDetails[marker.placeId] : undefined;
          return (
            <Marker
              anchor="bottom"
              key={marker.id}
              longitude={marker.lng}
              latitude={marker.lat}
              draggable={true}
              onClick={() => {
                // Mark as marker click so handleMapClick doesn't open a second popup
                markerClickRef.current = true;
                if (details && marker.placeId != null) {
                  setActivePlace({
                    id: marker.placeId,
                    lng: marker.lng,
                    lat: marker.lat,
                  });
                }
              }}
              onDragEnd={(e) => {
                setActivePlace(null);
                if (marker.type === 'waypoint') {
                  updateWaypointPosition({
                    latLng: { lat: e.lngLat.lat, lng: e.lngLat.lng },
                    index: marker.index ?? 0,
                  });
                } else if (marker.type === 'isocenter') {
                  updateIsoPosition(e.lngLat.lng, e.lngLat.lat);
                }
              }}
            >
              <div className="relative">
                <MarkerIcon color={marker.color!} number={marker.number} />
                {details && <PlaceMarkerLabel details={details} />}
              </div>
            </Marker>
          );
        })}

        {activePlace && activeDetails && (
          <PlaceCardPopup
            lng={activePlace.lng}
            lat={activePlace.lat}
            details={activeDetails}
            onClose={() => setActivePlace(null)}
          />
        )}

        {showContextPopup && popupLngLat && (
          <Popup
            longitude={popupLngLat.lng}
            latitude={popupLngLat.lat}
            closeButton={false}
            closeOnClick={false}
            maxWidth="none"
          >
            <MapContextMenu
              activeTab={activeTab}
              onAddWaypoint={handleAddWaypoint}
              onAddIsoWaypoint={handleAddIsoWaypoint}
              popupLocation={popupLngLat}
            />
          </Popup>
        )}

        {routeHoverPopup && (
          <RouteHoverPopup
            lng={routeHoverPopup.lng}
            lat={routeHoverPopup.lat}
            summary={routeHoverPopup.summary}
          />
        )}

        {tilesPopup && (
          <Popup
            longitude={tilesPopup.lng}
            latitude={tilesPopup.lat}
            closeButton={false}
            maxWidth="none"
            onClose={() => setTilesPopup(null)}
          >
            <TilesInfoPopup
              features={tilesPopup.features}
              onClose={() => setTilesPopup(null)}
            />
          </Popup>
        )}

        {showServices && <ServicesLayer items={services.items} />}
      </Map>

      {(routeResult || guiding) && (
        // ONE column, not two independently pinned groups. As two groups
        // (bottom-40 and bottom-24) they overlapped each other — measured at
        // 390x844 the guide's compass sat at y=652..696 and the services button
        // at y=640..684, a 32px collision — and both sat BEHIND the mobile sheet,
        // which starts at y=464 and covers 45dvh. The sheet publishes its own
        // height in --sheet-h (see sidebar.tsx), so the column rides above it:
        // the tourist can reach the controls of the very mode they are in.
        <div
          data-testid="map-controls"
          className="absolute bottom-[calc(var(--sheet-h,0px)+0.75rem)] right-3 z-10 flex flex-col items-end gap-2 md:bottom-24 md:right-4"
        >
          {routeResult && (
            <ServicesSummary
              items={services.items}
              state={services.state}
              maxOffLineM={services.maxOffLineM}
              capped={services.capped}
              active={showServices}
              toggle={
                <ToolButton
                  data-testid="services-toggle"
                  title={t('map.servicesToggle')}
                  active={showServices}
                  icon={<Coffee className="h-4 w-4" />}
                  onClick={() => setShowServices((on) => !on)}
                />
              }
            />
          )}

          {guiding && (
            <>
              <ToolButton
                data-testid="guide-orientation"
                title={
                  orientation === 'heading'
                    ? t('map.northUp')
                    : t('map.headingUp')
                }
                icon={<Navigation className="h-4 w-4" />}
                onClick={() => {
                  const next: MapOrientation =
                    orientation === 'heading' ? 'north' : 'heading';
                  setOrientation(next);
                  saveOrientation(next);
                  const map = mapRef.current;
                  if (!map) return;
                  if (next === 'heading' && guideFix) {
                    // Nothing to steer by yet: leave the map's bearing alone
                    // rather than snapping it to due north, which is exactly
                    // what an untouched «по курсу» looks like from the outside.
                    const steerTo = guideFix.course ?? guideFix.heading;
                    if (steerTo != null) {
                      map.easeTo({ bearing: steerTo, duration: 300 });
                    }
                  } else if (next === 'north') {
                    map.easeTo({ bearing: 0, duration: 300 });
                  }
                }}
              />
              <ToolButton
                data-testid="guide-follow"
                title={follow ? t('map.following') : t('map.followMe')}
                active={follow}
                icon={<LocateFixed className="h-4 w-4" />}
                onClick={() => {
                  setFollow(true);
                  const map = mapRef.current;
                  if (map && guideFix) {
                    map.easeTo({
                      center: [guideFix.lng, guideFix.lat],
                      pitch: 45,
                      zoom: Math.max(map.getZoom(), 16.5),
                      duration: 600,
                    });
                  }
                }}
              />
            </>
          )}
        </div>
      )}

      {/* The panel's own handle, on the panel's own edge — the same line the
          resize grip sits on, so opening, closing and resizing read as one
          control instead of three buttons fighting for the map's corners. The
          label is the only thing said out loud, for a screen reader. */}
      <PanelToggle
        open={directionsPanelOpen}
        onToggle={handlePanelToggle}
        label={t('map.panelToggle')}
        className="left-[min(var(--panel-width,0px),calc(100vw-1.75rem))]"
      />

      {/* Only the two cases that carry information are shown. A line this app
          drew itself is the ordinary case; a badge saying so is noise a tourist
          has to read past (the owner read it as such). What must never be
          silent is an agent plan whose line is missing, and where a verified
          line came from. */}
      {provenance === 'agent' && (
        <div
          role="status"
          data-testid="route-provenance"
          data-provenance={provenance}
          data-verified-line={missingVerifiedLine ? 'false' : 'true'}
          className="absolute left-4 top-20 z-10 max-w-[calc(100vw-2rem)] rounded-full border border-border bg-card px-3 py-1.5 text-meta text-muted-foreground shadow-card md:left-[calc(var(--panel-width,0px)+1rem)]"
        >
          {missingVerifiedLine ? t('map.lineMissing') : t('map.lineFromAgent')}
        </div>
      )}
    </>
  );
};
