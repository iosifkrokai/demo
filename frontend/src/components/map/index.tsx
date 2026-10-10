import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
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
  placeId?: number;
}

/** A catalogue row → the card the map already knows how to draw. */
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
  const isMobile = useIsMobile();

  const coordinates = useCommonStore((state) => state.coordinates);
  const directionsPanelOpen = useCommonStore(
    (state) => state.directionsPanelOpen
  );
  const toggleDirections = useCommonStore((state) => state.toggleDirections);
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
  const { places } = usePlaces({ enabled: placesVisible });
  const routeResult = useDirectionsStore((state) => state.results.data);
  /** Whether the places beside the route are drawn. */
  const [showServices, setShowServices] = useState(false);
  const servicesVisible = showServices || isMobile;
  const services = useServicesAlong(routeResult, {
    enabled: Boolean(routeResult),
  });
  const setActiveRouteIndex = useDirectionsStore(
    (state) => state.setActiveRouteIndex
  );

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
  const [activePlace, setActivePlace] = useState<{
    id: number;
    lng: number;
    lat: number;
  } | null>(null);
  const placesDetails = useMemo<Record<number, PlaceDetails>>(() => {
    const byId: Record<number, PlaceDetails> = {};
    for (const place of places) {
      byId[place.place_id] = placeToDetails(place);
    }
    return byId;
  }, [places]);

  const activeDetails =
    activePlace != null
      ? (placesDetails[activePlace.id] ?? placeDetails[activePlace.id])
      : undefined;
  const [initialViewState] = useState(() => ({
    longitude: center[0],
    latitude: center[1],
    zoom: zoom_initial,
  }));
  const [currentMapStyle] = useState<MapStyleType>(getInitialMapStyle(style));
  const [customStyleData] = useState<maplibregl.StyleSpecification | null>(() =>
    getCustomStyle()
  );

  const resolvedMapStyle =
    currentMapStyle === 'custom'
      ? (customStyleData ?? getMapStyleUrl('shortbread'))
      : getMapStyleUrl(currentMapStyle);

  const mapRef = useRef<MapRef>(null);

  /** How much of the canvas's left edge the docked panel covers, right now. */
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
  /** While the guide runs, the map always turns with the walk (never north-up). */
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

  const geocodeResults = useIsochronesStore((state) => state.geocodeResults);
  const markers = useMemo<MarkerData[]>(() => {
    const next: MarkerData[] = [];

    const poiWaypoints = waypoints.filter((w) => w.id !== ME_WAYPOINT_ID);
    waypoints
      .filter((w) => w.id === ME_WAYPOINT_ID)
      .forEach((waypoint) => {
        waypoint.geocodeResults.forEach((address) => {
          if (!address.selected) return;
          next.push({
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
          next.push({
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

    geocodeResults.forEach((address) => {
      if (address.selected) {
        next.push({
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

    return next;
  }, [waypoints, geocodeResults, t]);

  /** The bounding box of the line the map draws, or null when there is none. */
  const routeBounds = useMemo(() => {
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
  }, [coordinates]);

  /** Fit the whole route into the room the panel actually leaves. */
  const lastZoomedCoordKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!coordinates || coordinates.length === 0) {
      lastZoomedCoordKeyRef.current = null;
      return;
    }

    if (!mapRef.current) return;

    const firstCoord = coordinates[0];
    if (!firstCoord || !firstCoord[0] || !firstCoord[1]) return;

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
    if (coordKey === lastZoomedCoordKeyRef.current) return;
    lastZoomedCoordKeyRef.current = coordKey;

    const bounds = routeBounds;
    if (!bounds || !mapRef.current) return;
    const left = panelWidthPx();
    mapRef.current.fitBounds(bounds, {
      padding: {
        top: 80,
        bottom: 80,
        left: left > 0 ? left + 40 : 80,
        right: 80,
      },
      maxZoom: coordinates.length === 1 ? 11 : 18,
    });
  }, [coordinates, routeBounds]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusRequest) return;
    map.flyTo({
      center: [focusRequest.lng, focusRequest.lat],
      zoom: Math.max(map.getZoom(), 15.5),
      duration: 900,
      essential: true,
    });
  }, [focusRequest]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !guiding || !follow || !guideFix) return;
    if (map.isEasing?.()) return;
    const panelWidth = panelWidthPx();
    const seen = bearingRef.current;
    const steerTo = guideFix.course ?? guideFix.heading;
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
      zoom:
        guideTurnDistanceM != null && guideTurnDistanceM <= 120
          ? Math.max(map.getZoom(), 18)
          : Math.max(map.getZoom(), 16.5),
      duration: 900,
      easing: (progress: number) => progress,
      essential: true,
    });
  }, [guiding, follow, guideFix, guideTurnDistanceM]);

  const [autoFollowOnGuideStart, setAutoFollowOnGuideStart] = useState(false);
  useEffect(() => {
    if (guiding) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAutoFollowOnGuideStart(true);
    }
  }, [guiding]);
  useEffect(() => {
    if (autoFollowOnGuideStart) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFollow(true);
      setAutoFollowOnGuideStart(false);
    }
  }, [autoFollowOnGuideStart]);

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

  /** A navigator does not sulk. */
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

  /** Leaving the walk hands the map back in the state a planner expects. */
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
    const bounds = routeBounds;
    if (!bounds || !mapRef.current) return;
    const left = panelWidthPx();
    const id = window.setTimeout(() => {
      mapRef.current?.fitBounds(bounds, {
        padding: {
          top: 80,
          bottom: 80,
          left: left > 0 ? left + 40 : 80,
          right: 80,
        },
        maxZoom: 17,
      });
    }, 550);
    return () => window.clearTimeout(id);
  }, [guiding, routeBounds]);

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
      if (handledLongPressRef.current) {
        handledLongPressRef.current = false;
        return;
      }

      if (showContextPopup) {
        setShowContextPopup(false);
        return;
      }

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

      clickStateRef.current.pendingLngLat = lngLat;

      clickStateRef.current.timer = setTimeout(() => {
        const pendingLngLat = clickStateRef.current.pendingLngLat;
        if (pendingLngLat) {
          if (activeTab === 'tiles') {
            handleMapTilesClick(event);
          } else if (markerClickRef.current) {
            markerClickRef.current = false;
          } else {
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

  const handleMapDblClick = useCallback(() => {
    cancelPendingClick();
  }, [cancelPendingClick]);

  useEffect(() => {
    return () => {
      cancelPendingClick();
    };
  }, [cancelPendingClick]);

  const [activePlaceResetToken, setActivePlaceResetToken] = useState(0);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActivePlaceResetToken((value) => value + 1);
  }, [placeDetails]);
  useEffect(() => {
    if (activePlaceResetToken > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActivePlace(null);
    }
  }, [activePlaceResetToken]);

  const handleMapContextMenu = useCallback(
    (event: { lngLat: { lng: number; lat: number } }) => {
      if (activeTab === 'tiles') return;

      const { lngLat } = event;
      setPopupLngLat(lngLat);
      setShowContextPopup(true);
    },
    [activeTab]
  );

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

      if (touchCount > 1) {
        cancelPendingClick();
        return;
      }

      touchStartTimeRef.current = now;
      touchLocationRef.current = { x: event.point.x, y: event.point.y };
      handledLongPressRef.current = false;

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

  const onRouteLineHover = useCallback(
    (event: maplibregl.MapLayerMouseEvent) => {
      if (!mapRef.current) return;

      const map = mapRef.current.getMap();
      map.getCanvas().style.cursor = 'pointer';

      const feature = event.features?.[0];
      if (feature && feature.properties?.summary) {
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
  const isTouch =
    typeof window !== 'undefined' &&
    (window.matchMedia?.('(pointer: coarse)')?.matches ?? false);

  const handleMouseMove = useCallback(
    (event: maplibregl.MapLayerMouseEvent) => {
      if (!mapRef.current) return;

      const features = event.features;
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

      if (isOverRoute && !isTouch) {
        onRouteLineHover(event);
      } else if (isOverTiles || isOverPlaces) {
        const map = mapRef.current.getMap();
        map.getCanvas().style.cursor = 'pointer';
      } else {
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
        initialViewState={initialViewState}
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
        style={{ width: '100%', height: '100dvh' }}
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

        {activePlace && activeDetails && !isMobile && (
          <PlaceCardPopup
            lng={activePlace.lng}
            lat={activePlace.lat}
            details={activeDetails}
            placeId={activePlace.id}
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

        {servicesVisible && (
          <ServicesLayer
            items={services.items}
            thin={
              isMobile
                ? {
                    max: PHONE_MAX_MARKS,
                    minGapPx: PHONE_MIN_GAP_PX,
                    avoidLabels: true,
                  }
                : undefined
            }
          />
        )}
      </Map>

      {(routeResult || guiding) && (
        <div
          data-testid="map-controls"
          className={`absolute z-10 flex flex-col gap-2 ${
            isMobile
              ? guiding
                ? 'left-3 top-[calc(max(env(safe-area-inset-top),0.75rem)+10rem)] items-start'
                : 'bottom-[calc(env(safe-area-inset-bottom)+var(--sheet-h,0px)+0.75rem)] right-3 items-end'
              : guiding
                ? 'right-4 top-[calc(max(env(safe-area-inset-top),0.75rem)+13.5rem)] items-end'
                : 'bottom-24 right-4 items-end'
          }`}
        >
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

      {!guiding && (
        <PanelToggle
          open={directionsPanelOpen}
          onToggle={handlePanelToggle}
          label={t('map.panelToggle')}
          className="left-[min(var(--panel-width,0px),calc(100vw-1.75rem))]"
        />
      )}

      {isMobile && activePlace && activeDetails && (
        <MobilePlaceCard
          details={activeDetails}
          placeId={activePlace.id}
          onClose={() => setActivePlace(null)}
        />
      )}

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
