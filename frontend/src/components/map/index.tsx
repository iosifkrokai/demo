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
import {
  PHONE_MAX_MARKS,
  PHONE_MIN_GAP_PX,
  ServicesLayer,
} from './parts/services-layer';
import { PlacesLayer, PLACES_POINTS_LAYER_ID } from './parts/places-layer';
import { PlaceCardPopup } from './parts/place-card-popup';
import { PlaceMarkerLabel } from './parts/place-marker-label';
import { MobilePlaceCard } from '@/components/mobile/mobile-place-card';
import { useIsMobile } from '@/components/mobile/use-is-mobile';
import { maxBounds } from './constants';
import { getInitialMapPosition, LAST_CENTER_KEY } from './utils';
import { useCommonStore } from '@/stores/common-store';
import { useTranslation } from 'react-i18next';
import { Coffee, LocateFixed, Volume2, VolumeX } from 'lucide-react';
import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type PlaceDetails,
} from '@/stores/directions-store';
import { useServicesAlong } from '@/hooks/use-services-along';
import { usePlaces } from '@/hooks/use-places';
import type { Place } from '@/api/types';
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

/** A catalogue row → the card the map already knows how to draw. The «все
 * точки» circles reuse the waypoint card, so the raw row is shaped into the
 * same `PlaceDetails` the agent route publishes. */
const placeToDetails = (place: Place): PlaceDetails => ({
  name: place.name,
  category: place.category,
  blurb: place.blurb,
  funFact: place.fun_fact,
  funFacts: place.fun_facts,
  links: place.links,
  visitMinutes: place.visit_minutes,
  openingHours: place.opening_hours,
  ticketPrice: place.ticket_price,
  town: place.town,
  district: place.district,
  photo: place.photo,
});

export const MapComponent = () => {
  const { activeTab } = useParams({ from: '/$activeTab' });
  const navigate = useNavigate({ from: '/$activeTab' });
  // Which surface reads about a point: a popup by the pin on a wide screen, a
  // card on the bottom of the map on a phone. The one viewport predicate, used
  // here for a presentation choice rather than for a second shell — the shells
  // themselves are still chosen in app.tsx alone.
  const isMobile = useIsMobile();

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
  const placesVisible = useCommonStore((state) => state.placesVisible);
  // The whole catalogue, fetched once and shared with the panel list through
  // the ['places'] query key. `placesVisible` gates the fetch, so a tourist who
  // never opens the «Все точки» tab never downloads ~2.5k rows.
  const { places } = usePlaces({ enabled: placesVisible });
  const routeResult = useDirectionsStore((state) => state.results.data);
  /**
   * Whether the places beside the route are drawn.
   *
   * Asked for by hand on a wide screen, where the summary card offers the
   * button: a guide that *marks* cafés uninvited stops being a guide.
   *
   * On a phone there is no card and no button — the owner read the count block
   * as noise to get past — so the marks are simply always on. The alternative
   * was marks that cannot be reached at all, which is worse than marks the
   * tourist did not ask for: on a phone the map is the whole screen, a café
   * icon on it reads as a thing that is there, and tapping it still says what
   * it is and that its hours are unknown.
   */
  const [showServices, setShowServices] = useState(false);
  const servicesVisible = showServices || isMobile;
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
  // The card shows whichever source knows this stop: the agent route's curated
  // `placeDetails`, or the raw catalogue when the tourist tapped a circle.
  const placesDetails = useMemo<Record<number, PlaceDetails>>(() => {
    const map: Record<number, PlaceDetails> = {};
    for (const place of places) map[place.place_id] = placeToDetails(place);
    return map;
  }, [places]);

  const activeDetails =
    activePlace != null
      ? (placesDetails[activePlace.id] ?? placeDetails[activePlace.id])
      : undefined;
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

  /**
   * How much of the canvas's left edge the docked panel covers, right now.
   *
   * The panel publishes it as `--panel-width` and zeroes it whenever it is not
   * actually there — closed, on a phone (where the panel is a bottom sheet), and
   * while the guide runs (where the column hides itself). Read from the property
   * rather than from the store so the camera never reserves space for a panel
   * nobody can see.
   */
  const panelWidthPx = () => {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(
      '--panel-width'
    );
    const parsed = Number.parseFloat(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  };

  const focusRequest = useCommonStore((s) => s.focus);
  const guideFix = useCommonStore((s) => s.guideFix);
  /** Metres to the next turn — the guide publishes it, the camera acts on it. */
  const guideTurnDistanceM = useCommonStore((s) => s.guideTurnDistanceM);
  /** The bearing in force, so small course wobbles do not turn the map. */
  const bearingRef = useRef<number | null>(null);
  /** When the tourist last dragged the map away — following returns after a pause. */
  const panAwayRef = useRef(0);
  const guiding = useCommonStore((s) => s.guiding);
  const guideVoiceMuted = useCommonStore((s) => s.guideVoiceMuted);
  const setGuideVoiceMuted = useCommonStore((s) => s.setGuideVoiceMuted);
  /** Navigator mode: the map keeps the tourist in view until a hand moves it. */
  const [follow, setFollow] = useState(true);
  /**
   * While the guide runs the map is always north-up-free: it turns with the walk,
   * because a navigator that can be talked out of turning is not one. There used
   * to be a «по курсу / север сверху» button here with the choice remembered in
   * localStorage; it was a control nobody pressed mid-walk and one more thing
   * between the tourist and the map, so the bearing is now applied
   * unconditionally and there is nothing to remember.
   */
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

  /**
   * The bounding box of the line the map draws, or null when there is none.
   *
   * Shared by the two things that frame a route: the effect that reacts to the
   * coordinates changing, and the one that reframes it when the guide is left.
   */
  const routeBounds = (): [[number, number], [number, number]] | null => {
    if (!coordinates || coordinates.length === 0) return null;
    const first = coordinates[0];
    if (!first || !first[0] || !first[1]) return null;
    return coordinates.reduce<[[number, number], [number, number]]>(
      (acc, coord) => {
        if (!coord || !coord[0] || !coord[1]) return acc;
        return [
          [Math.min(acc[0][0], coord[1]), Math.min(acc[0][1], coord[0])],
          [Math.max(acc[1][0], coord[1]), Math.max(acc[1][1], coord[0])],
        ];
      },
      [
        [first[1], first[0]],
        [first[1], first[0]],
      ]
    );
  };

  /**
   * Fit the whole route into the room the panel actually leaves.
   *
   * The padding is read from `--panel-width`, not from a constant: it used to be
   * a hardcoded 450px whenever the panel was open, wrong at both ends of its own
   * range — the panel is resizable from 340 to 720px, so at 720 the route's east
   * end landed under the panel and at 340 the map kept 110px of nothing. The
   * property is 0 whenever there is no column to make room for.
   */
  const fitRoute = (maxZoom: number) => {
    const bounds = routeBounds();
    if (!bounds || !mapRef.current) return false;
    const left = panelWidthPx();
    mapRef.current.fitBounds(bounds, {
      padding: {
        top: 80,
        bottom: 80,
        left: left > 0 ? left + 40 : 80,
        right: 80,
      },
      maxZoom,
    });
    return true;
  };

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

    fitRoute(coordinates.length === 1 ? 11 : 18);
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
    // of the map the tourist can actually see. `--panel-width` is 0 while the
    // panel hides itself (sidebar.tsx), so the whole canvas is used then — which
    // is what stopped the dot from sitting 210px right of centre on a 1440px
    // screen.
    const panelWidth = panelWidthPx();
    // The heading is read once it has moved far enough to matter. A GPS course
    // wobbles by a few degrees every second and the simulated one jumps at every
    // corner; rotating for those turned the map into a spinning top. Below the
    // threshold the current bearing is kept, above it the map turns once, slowly.
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
  }, [guiding, follow, guideFix, guideTurnDistanceM]);

  // Following starts again every time the guide is entered: a navigator has to
  // re-centre the tourist the moment the walk starts.
  useEffect(() => {
    if (guiding) setFollow(true);
  }, [guiding]);

  // When the tourist switches back to 'heading' while the guide is running,
  // the map snaps to the current heading immediately instead of waiting for
  // the next guideFix (which may not come if the tourist is standing still).
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

  /**
   * Leaving the walk hands the map back in the state a planner expects.
   *
   * The navigator leaves the camera tilted 45°, turned to the course and at
   * street zoom — and nothing undid that. Measured after «выйти»: the tourist is
   * left looking at one park from 18 zoom while the route they just walked is
   * somewhere off screen, with no control that frames it again (the fit-bounds
   * effect only fires when the coordinates themselves change).
   *
   * So the exit is an event the map can see: level the camera, forget the
   * bearing, and frame the whole route in whatever room the panel leaves.
   */
  const wasGuiding = useRef(false);
  useEffect(() => {
    const map = mapRef.current?.getMap?.();
    if (guiding) {
      wasGuiding.current = true;
      return;
    }
    if (!wasGuiding.current || !map) return;
    wasGuiding.current = false;
    map.easeTo({ pitch: 0, bearing: 0, duration: 500, essential: true });
    // Refit once the panel is back: it publishes its width from an effect of its
    // own, so on the same tick the property is still 0 and the route would be
    // framed for a screen the panel is about to take a third of.
    const id = window.setTimeout(() => fitRoute(17), 550);
    return () => window.clearTimeout(id);
  }, [guiding]);

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

      // A tap on a «все точки» circle: open the same card a waypoint marker
      // opens, keyed by the feature's own placeId. Any pending plain-click
      // timer is dropped so it cannot close the card that just opened.
      const placeFeature = event.features?.find(
        (f) => f.layer?.id === PLACES_POINTS_LAYER_ID
      );
      if (
        placeFeature &&
        typeof placeFeature.properties?.placeId === 'number'
      ) {
        cancelPendingClick();
        markerClickRef.current = true;
        const geometry = placeFeature.geometry;
        const [lng, lat] =
          geometry != null && geometry.type === 'Point'
            ? (geometry.coordinates as [number, number])
            : [event.lngLat.lng, event.lngLat.lat];
        setActivePlace({ id: placeFeature.properties.placeId, lng, lat });
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

  /** A real finger rather than a mouse: no hover, so no hover popup. */
  const isTouch = useMemo(
    () =>
      typeof window !== 'undefined' &&
      (window.matchMedia?.('(pointer: coarse)')?.matches ?? false),
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
      const isOverPlaces = topLayerId === PLACES_POINTS_LAYER_ID;

      // A finger cannot hover, so on a touch screen there is nothing to hover
      // with — and the popup it leaves behind (measured on 390x844: «Route
      // Summary 7.2 km 1h 25m» sitting on the map under a synthetic pointer
      // move) is a stale card the tourist cannot dismiss. The summary is on the
      // panel and on the stops list anyway, so it is the mouse's job alone.
      if (isOverRoute && !isTouch) {
        onRouteLineHover(event);
      } else if (isOverTiles || isOverPlaces) {
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
    [routeHoverPopup, onRouteLineHover, isTouch]
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
                PLACES_POINTS_LAYER_ID,
              ]
            : ['routes-line', 'routes-hit-target', PLACES_POINTS_LAYER_ID]
        }
        mapStyle={resolvedMapStyle}
        attributionControl={false}
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
                {details && (
                  <PlaceMarkerLabel
                    details={details}
                    active={activePlace?.id === marker.placeId}
                  />
                )}
              </div>
            </Marker>
          );
        })}

        {/* One place, one reader. A popup and a bottom card for the same point
            would put two copies of the same text on the screen at once. */}
        {activePlace && activeDetails && !isMobile && (
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

        {placesVisible && <PlacesLayer places={places} />}

        {/* On a phone only a handful of marks are drawn — see ServicesLayer for
            the measurement that set it. A wide screen has the room for all. */}
        {servicesVisible && (
          <ServicesLayer
            items={services.items}
            thin={
              isMobile
                ? {
                    max: PHONE_MAX_MARKS,
                    minGapPx: PHONE_MIN_GAP_PX,
                    // The route's own place names are the widest thing on the
                    // map — 156px on a phone — and a service mark that covers
                    // one hides the name of the stop it sits next to. The layer
                    // reads their boxes itself, at the moment it thins: passed
                    // in from here they would be a frame stale, because the
                    // captions are re-laid-out by the same camera move.
                    avoidLabels: true,
                  }
                : undefined
            }
          />
        )}
      </Map>

      {(routeResult || guiding) && (
        // The planner controls ride above the mobile sheet. During navigation the
        // HUD owns the bottom of the screen, so the controls get out of its way:
        // on a phone the open top-left corner, on a monitor the column under the
        // HUD's own exit/voice cluster.
        //
        // They used to sit at `md:bottom-24 md:right-4` while guiding too, which
        // put «следовать» (y 760-804) underneath the HUD's bottom stack
        // (y 777-892) — measured 1188px² of overlap, the button half hidden
        // behind the progress bar.
        <div
          data-testid="map-controls"
          className={`absolute z-10 flex flex-col gap-2 ${
            isMobile
              ? guiding
                ? 'left-3 top-[calc(max(env(safe-area-inset-top),0.75rem)+10rem)] items-start'
                : 'bottom-[calc(var(--sheet-h,0px)+0.75rem)] right-3 items-end'
              : guiding
                ? // Under the HUD cluster (exit + sound, 2x44 + 8 gap = 96px),
                  // which starts 7rem below the same top inset.
                  'right-4 top-[calc(max(env(safe-area-inset-top),0.75rem)+13.5rem)] items-end'
                : 'bottom-24 right-4 items-end'
          }`}
        >
          {/*
            The «по пути: N мест» block is gone from every screen.

            It began as a 176x130 card over the map and was already down to one
            line on a phone by the time the owner asked for it to go — and it
            was still the first thing between the tourist and the map, on the
            wide screen too, where it sits above the guide's own compass. What
            it said is not lost, only relocated: `/routes/services` still runs,
            the marks still carry their names, distance and hours when tapped,
            and the count a tourist wants is on the panel's stops list.

            The button that turned the marks on stays, because without it they
            could not be reached at all. It is a bare icon with the same
            «что рядом по пути» label, which is what it was before the card grew
            around it.
          */}
          {routeResult && (
            <ToolButton
              data-testid="services-toggle"
              title={t('map.servicesToggle')}
              active={showServices}
              icon={<Coffee className="h-4 w-4" />}
              onClick={() => setShowServices((on) => !on)}
            />
          )}

          {guiding && (
            <>
              {/* Sound is `md:hidden` here: from md up the HUD's own cluster
                  already carries a mute button (guide-voice-toggle-hud), and two
                  of them on one screen is one too many. On a phone this column is
                  the only place the switch exists, so it stays. */}
              <ToolButton
                data-testid="guide-voice-toggle-map"
                title={
                  guideVoiceMuted
                    ? t('guide.enableSound')
                    : t('guide.disableSound')
                }
                active={guideVoiceMuted}
                className="md:hidden"
                icon={
                  guideVoiceMuted ? (
                    <VolumeX className="h-4 w-4" />
                  ) : (
                    <Volume2 className="h-4 w-4" />
                  )
                }
                onClick={() => setGuideVoiceMuted(!guideVoiceMuted)}
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
          label is the only thing said out loud, for a screen reader.

          Desktop only (see PanelToggle): on a phone the panel is a sheet across
          the bottom, so it has no left edge to stand on, and the way in and out
          belongs to the sheet itself.

          Hidden while walking: the panel hides itself then (`GUIDE_SHEET_CLASS`),
          so the chevron would sit on the map's far edge pointing at nothing, and
          it measured — parked on the left edge at x=0 in the middle of the map.
          The way out of navigation is the HUD's exit button. */}
      {!guiding && (
        <PanelToggle
          open={directionsPanelOpen}
          onToggle={handlePanelToggle}
          label={t('map.panelToggle')}
          className="left-[min(var(--panel-width,0px),calc(100vw-1.75rem))]"
        />
      )}

      {/* About a point, on a phone: a card at the bottom of the map rather than
          a popup over it. Rendered here, next to the map's other floating
          chrome, because it is chrome over the map — and `md:hidden`, so it is
          never in the DOM on a wide screen. */}
      {isMobile && activePlace && activeDetails && (
        <MobilePlaceCard
          details={activeDetails}
          onClose={() => setActivePlace(null)}
        />
      )}

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
