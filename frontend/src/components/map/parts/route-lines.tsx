import { useMemo } from 'react';
import { Source, Layer } from 'react-map-gl/maplibre';
import { useDirectionsStore } from '@/stores/directions-store';
import { routeObjects } from '../constants';
import type { Feature, FeatureCollection, LineString } from 'geojson';

import { useCommonStore } from '@/stores/common-store';

import { splitAtPosition, type LineCoords } from './route-walk';
import type { ParsedDirectionsGeometry } from '@/components/types';

/** Where the line on screen came from. */
export type RouteProvenance = 'agent' | 'client';

/** A route result plus the provenance of the geometry it carries. */
export interface ProvenancedRoute extends ParsedDirectionsGeometry {
  source: RouteProvenance;
  /** False when an agent route has no usable geometry: the line stays absent. */
  hasVerifiedLine: boolean;
}

/** Provenance of a stored result. */
export const routeProvenance = (
  data: ParsedDirectionsGeometry | null | undefined
): RouteProvenance | null => {
  if (data == null) return null;
  return (data as Partial<ProvenancedRoute>).source ?? 'client';
};

/** True when there is a drawable line: ≥2 points, and for an agent route, backend geometry. */
export const hasUsableLine = (
  data: ParsedDirectionsGeometry | null | undefined
): boolean => {
  if (data == null) return false;
  if ((data as Partial<ProvenancedRoute>).hasVerifiedLine === false) {
    return false;
  }
  return (data.decodedGeometry?.length ?? 0) > 1;
};

/** An agent route is on screen but its verified line is not: the honest "no line" state. */
export const isMissingVerifiedLine = (
  data: ParsedDirectionsGeometry | null | undefined
): boolean => routeProvenance(data) === 'agent' && !hasUsableLine(data);

export function RouteLines() {
  const directionResults = useDirectionsStore((state) => state.results);
  const directionsSuccessful = useDirectionsStore((state) => state.successful);
  const activeRouteIndex = useDirectionsStore(
    (state) => state.activeRouteIndex
  );
  const guiding = useCommonStore((state) => state.guiding);
  const guideFix = useCommonStore((state) => state.guideFix);

  const data = useMemo(() => {
    if (!directionResults.data || !directionsSuccessful) return null;

    const hasNoData = Object.keys(directionResults.data).length === 0;
    if (hasNoData) return null;

    if (!hasUsableLine(directionResults.data)) return null;

    const response = directionResults.data;
    const provenance = routeProvenance(response);
    const showRoutes = directionResults.show || {};
    const features: Feature<LineString>[] = [];

    if (response.alternates) {
      response.alternates.forEach((alternate, i) => {
        if (!showRoutes[i + 1]) return;
        const coords = (alternate! as ParsedDirectionsGeometry)!
          .decodedGeometry;
        const summary = alternate!.trip.summary;
        const isActive = activeRouteIndex === i + 1;

        features.push({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: coords.map((c) => [c[1] ?? 0, c[0] ?? 0]),
          },
          properties: {
            color: isActive ? routeObjects.color : routeObjects.inactiveColor,
            type: 'alternate',
            routeIndex: i + 1,
            summary,
            provenance,
          },
        });
      });
    }

    if (showRoutes[0] !== false) {
      const coords = response.decodedGeometry;
      const summary = response.trip.summary;
      const isActive = activeRouteIndex === 0;

      const line: LineCoords = coords.map((c) => [c[1] ?? 0, c[0] ?? 0]);
      const color = isActive ? routeObjects.color : routeObjects.inactiveColor;
      const split =
        guiding && guideFix && isActive
          ? splitAtPosition(line, { lat: guideFix.lat, lon: guideFix.lng })
          : null;

      if (split) {
        features.push({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: split.walked },
          properties: {
            color,
            type: 'main',
            routeIndex: 0,
            summary,
            provenance,
            walked: true,
          },
        });
        features.push({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: split.remaining },
          properties: {
            color,
            type: 'main',
            routeIndex: 0,
            summary,
            provenance,
            walked: false,
          },
        });
      } else {
        features.push({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: line },
          properties: {
            color,
            type: 'main',
            routeIndex: 0,
            summary,
            provenance,
            walked: false,
          },
        });
      }
    }

    features.sort((a, b) => {
      const aActive = a.properties?.routeIndex === activeRouteIndex ? 1 : 0;
      const bActive = b.properties?.routeIndex === activeRouteIndex ? 1 : 0;
      return aActive - bActive;
    });

    return {
      type: 'FeatureCollection',
      features,
    } as FeatureCollection;
  }, [
    directionResults,
    directionsSuccessful,
    activeRouteIndex,
    guiding,
    guideFix,
  ]);

  if (!data) return null;

  return (
    <Source id="routes" type="geojson" data={data}>
      <Layer
        id="routes-outline"
        type="line"
        paint={{
          'line-color': '#FFF',
          'line-width': 9,
          'line-opacity': 1,
        }}
      />
      <Layer
        id="routes-line"
        type="line"
        paint={{
          'line-color': [
            'case',
            ['==', ['get', 'walked'], true],
            '#9CA3AF',
            ['get', 'color'],
          ],
          'line-width': 5,
          'line-opacity': [
            'case',
            ['==', ['get', 'routeIndex'], activeRouteIndex],
            1,
            0.5,
          ],
        }}
      />
      <Layer
        id="routes-hit-target"
        type="line"
        paint={{
          'line-color': '#000',
          'line-width': 15,
          'line-opacity': 0,
        }}
      />
    </Source>
  );
}
