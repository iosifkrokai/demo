import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { Profile } from '@/stores/common-store';
import type {
  ActiveWaypoint,
  ActiveWaypoints,
  ValhallaRouteResponse,
} from '@/components/types';
import {
  buildDirectionsRequest,
  parseDirectionsGeometry,
  makeLocations,
  chunkWaypoints,
  VALHALLA_MAX_LOCATIONS,
  WAYPOINT_SNAP_RADIUS_M,
} from './valhalla';

vi.mock('./polyline', () => ({
  decode: vi.fn(),
}));

import { decode } from './polyline';

describe('chunkWaypoints', () => {
  const wp = (i: number): ActiveWaypoint => ({
    title: `stop ${i}`,
    description: '',
    selected: true,
    displaylnglat: [23.8 + i / 100, 53.6 + i / 100],
    sourcelnglat: [23.8 + i / 100, 53.6 + i / 100],
    key: i,
    addressindex: 0,
  });
  const points = (n: number) => Array.from({ length: n }, (_, i) => wp(i));

  it('keeps a short list in a single request', () => {
    expect(chunkWaypoints(points(5))).toHaveLength(1);
    expect(chunkWaypoints(points(20))).toHaveLength(1);
  });

  it('splits longer lists into chained groups of at most 20 locations', () => {
    for (const total of [21, 29, 40]) {
      const chunks = chunkWaypoints(points(total));
      expect(chunks.length).toBeGreaterThan(1);
      for (const chunk of chunks) {
        expect(chunk.length).toBeLessThanOrEqual(VALHALLA_MAX_LOCATIONS);
      }
      const seen = new Set(chunks.flat().map((w) => w.title));
      expect(seen.size).toBe(total);
      for (let i = 1; i < chunks.length; i += 1) {
        const previousLast = chunks[i - 1]!.at(-1)?.title;
        expect(chunks[i]![0]?.title).toBe(previousLast);
      }
    }
  });

  it('returns nothing for an empty list', () => {
    expect(chunkWaypoints([])).toEqual([]);
  });
});

describe('valhalla.ts', () => {
  const mockDecode = vi.mocked(decode);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('buildDirectionsRequest', () => {
    const mockActiveWaypoints: ActiveWaypoints = [
      {
        title: 'Start',
        description: 'Starting point',
        selected: true,
        addresslnglat: [-74.006, 40.7128],
        sourcelnglat: [-74.006, 40.7128],
        displaylnglat: [-74.006, 40.7128],
        key: 1,
        addressindex: 0,
      },
      {
        title: 'Via',
        description: 'Via point',
        selected: true,
        addresslnglat: [-118.2437, 34.0522],
        sourcelnglat: [-118.2437, 34.0522],
        displaylnglat: [-118.2437, 34.0522],
        key: 2,
        addressindex: 1,
      },
      {
        title: 'End',
        description: 'Ending point',
        selected: true,
        addresslnglat: [-87.6298, 41.8781],
        sourcelnglat: [-87.6298, 41.8781],
        displaylnglat: [-87.6298, 41.8781],
        key: 3,
        addressindex: 2,
      },
    ];

    const mockDateTime = { type: 1, value: '2023-01-01T12:00' };

    const expectedLocations = [
      {
        lon: -74.006,
        lat: 40.7128,
        type: 'break',
        radius: WAYPOINT_SNAP_RADIUS_M,
      },
      {
        lon: -118.2437,
        lat: 34.0522,
        type: 'via',
        radius: WAYPOINT_SNAP_RADIUS_M,
      },
      {
        lon: -87.6298,
        lat: 41.8781,
        type: 'break',
        radius: WAYPOINT_SNAP_RADIUS_M,
      },
    ];

    it('should create a directions request with car profile (mapped to auto)', () => {
      const profile: Profile = 'car';

      const result = buildDirectionsRequest({
        profile,
        activeWaypoints: mockActiveWaypoints,
        dateTime: mockDateTime,
        language: 'en-US',
      });

      expect(result).toEqual({
        json: {
          costing: 'auto',
          costing_options: { auto: {} },
          exclude_polygons: [],
          locations: expectedLocations,
          units: 'kilometers',
          alternates: 2,
          id: 'valhalla_directions',
          language: 'en-US',
          date_time: mockDateTime,
        },
      });
    });

    it('should create a directions request with bicycle profile and its costing tune', () => {
      const profile: Profile = 'bicycle';

      const result = buildDirectionsRequest({
        profile,
        activeWaypoints: mockActiveWaypoints,
        dateTime: mockDateTime,
        language: 'de-DE',
      });

      expect(result).toEqual({
        json: {
          costing: 'bicycle',
          costing_options: { bicycle: { cycling_speed: 20, use_roads: 0.5 } },
          exclude_polygons: [],
          locations: expectedLocations,
          units: 'kilometers',
          alternates: 2,
          id: 'valhalla_directions',
          language: 'de-DE',
          date_time: mockDateTime,
        },
      });
    });

    it('should create a directions request with pedestrian profile and its costing tune', () => {
      const profile: Profile = 'pedestrian';

      const result = buildDirectionsRequest({
        profile,
        activeWaypoints: mockActiveWaypoints,
        dateTime: mockDateTime,
        language: 'en-US',
      });

      expect(result.json.costing).toBe('pedestrian');
      expect(result.json.costing_options).toEqual({
        pedestrian: {
          step_penalty: 0,
          service_penalty: 15,
          use_tracks: 0,
          use_living_streets: 0.5,
          destination_only_penalty: 300,
        },
      });
    });

    it('should create a directions request without date_time when type is -1', () => {
      const profile: Profile = 'car';
      const dateTimeNoType = { type: -1, value: '2023-01-01T12:00' };

      const result = buildDirectionsRequest({
        profile,
        activeWaypoints: mockActiveWaypoints,
        dateTime: dateTimeNoType,
        language: 'en-US',
      });

      expect(result).toEqual({
        json: {
          costing: 'auto',
          costing_options: { auto: {} },
          exclude_polygons: [],
          locations: expectedLocations,
          units: 'kilometers',
          alternates: 2,
          id: 'valhalla_directions',
          language: 'en-US',
        },
      });
      expect(result.json).not.toHaveProperty('date_time');
    });

    it('should create a directions request with single waypoint', () => {
      const singleWaypoint: ActiveWaypoints = [mockActiveWaypoints[0]!];
      const profile: Profile = 'car';

      const result = buildDirectionsRequest({
        profile,
        activeWaypoints: singleWaypoint,
        dateTime: mockDateTime,
        language: 'en-US',
      });

      expect(result).toEqual({
        json: {
          costing: 'auto',
          costing_options: { auto: {} },
          exclude_polygons: [],
          locations: [expectedLocations[0]],
          units: 'kilometers',
          alternates: 2,
          id: 'valhalla_directions',
          language: 'en-US',
          date_time: mockDateTime,
        },
      });
    });
  });

  describe('parseDirectionsGeometry', () => {
    it('should parse directions geometry with single leg', () => {
      const mockData: ValhallaRouteResponse = {
        trip: {
          legs: [
            {
              shape: 'encoded_shape_1',
              summary: {
                has_time_restrictions: false,
                has_toll: false,
                has_highway: false,
                has_ferry: false,
                min_lat: 40.0,
                min_lon: -75.0,
                max_lat: 41.0,
                max_lon: -74.0,
                time: 300,
                length: 10.5,
                cost: 100,
              },
              maneuvers: [],
            },
          ],
          locations: [],
          summary: {
            has_time_restrictions: false,
            has_toll: false,
            has_highway: false,
            has_ferry: false,
            min_lat: 40.0,
            min_lon: -75.0,
            max_lat: 41.0,
            max_lon: -74.0,
            time: 300,
            length: 10.5,
            cost: 100,
          },
          status_message: 'Found route',
          status: 0,
          units: 'kilometers',
          language: 'en-US',
        },
        id: 'valhalla_directions',
      };

      const mockDecodedCoordinates = [
        [40.7128, -74.006],
        [40.713, -74.005],
        [40.714, -74.004],
      ];
      mockDecode.mockReturnValue(mockDecodedCoordinates);

      const result = parseDirectionsGeometry(mockData);

      expect(mockDecode).toHaveBeenCalledWith('encoded_shape_1', 6);
      expect(result).toEqual(mockDecodedCoordinates);
    });

    it('should parse directions geometry with multiple legs', () => {
      const mockData: ValhallaRouteResponse = {
        trip: {
          legs: [
            {
              shape: 'encoded_shape_1',
              summary: {
                has_time_restrictions: false,
                has_toll: false,
                has_highway: false,
                has_ferry: false,
                min_lat: 40.0,
                min_lon: -75.0,
                max_lat: 41.0,
                max_lon: -74.0,
                time: 150,
                length: 5.0,
                cost: 50,
              },
              maneuvers: [],
            },
            {
              shape: 'encoded_shape_2',
              summary: {
                has_time_restrictions: false,
                has_toll: false,
                has_highway: false,
                has_ferry: false,
                min_lat: 41.0,
                min_lon: -74.0,
                max_lat: 42.0,
                max_lon: -73.0,
                time: 150,
                length: 5.5,
                cost: 50,
              },
              maneuvers: [],
            },
          ],
          locations: [],
          summary: {
            has_time_restrictions: false,
            has_toll: false,
            has_highway: false,
            has_ferry: false,
            min_lat: 40.0,
            min_lon: -75.0,
            max_lat: 42.0,
            max_lon: -73.0,
            time: 300,
            length: 10.5,
            cost: 100,
          },
          status_message: 'Found route',
          status: 0,
          units: 'kilometers',
          language: 'en-US',
        },
        id: 'valhalla_directions',
      };

      const mockDecodedCoordinates1 = [
        [40.7128, -74.006],
        [40.713, -74.005],
      ];
      const mockDecodedCoordinates2 = [
        [40.714, -74.004],
        [40.715, -74.003],
      ];
      mockDecode
        .mockReturnValueOnce(mockDecodedCoordinates1)
        .mockReturnValueOnce(mockDecodedCoordinates2);

      const result = parseDirectionsGeometry(mockData);

      expect(mockDecode).toHaveBeenCalledWith('encoded_shape_1', 6);
      expect(mockDecode).toHaveBeenCalledWith('encoded_shape_2', 6);
      expect(result).toEqual([
        ...mockDecodedCoordinates1,
        ...mockDecodedCoordinates2,
      ]);
    });

    it('should handle empty legs array', () => {
      const mockData: ValhallaRouteResponse = {
        trip: {
          legs: [],
          locations: [],
          summary: {
            has_time_restrictions: false,
            has_toll: false,
            has_highway: false,
            has_ferry: false,
            min_lat: 40.0,
            min_lon: -75.0,
            max_lat: 41.0,
            max_lon: -74.0,
            time: 0,
            length: 0,
            cost: 0,
          },
          status_message: 'No route found',
          status: 1,
          units: 'kilometers',
          language: 'en-US',
        },
        id: 'valhalla_directions',
      };

      const result = parseDirectionsGeometry(mockData);

      expect(mockDecode).not.toHaveBeenCalled();
      expect(result).toEqual([]);
    });
  });

  describe('makeLocations', () => {
    const mockWaypoints: ActiveWaypoints = [
      {
        title: 'Start',
        description: 'Starting point',
        selected: true,
        addresslnglat: [-74.006, 40.7128],
        sourcelnglat: [-74.006, 40.7128],
        displaylnglat: [-74.006, 40.7128],
        key: 1,
        addressindex: 0,
      },
      {
        title: 'Via 1',
        description: 'Via point 1',
        selected: true,
        addresslnglat: [-118.2437, 34.0522],
        sourcelnglat: [-118.2437, 34.0522],
        displaylnglat: [-118.2437, 34.0522],
        key: 2,
        addressindex: 1,
      },
      {
        title: 'Via 2',
        description: 'Via point 2',
        selected: true,
        addresslnglat: [-87.6298, 41.8781],
        sourcelnglat: [-87.6298, 41.8781],
        displaylnglat: [-87.6298, 41.8781],
        key: 3,
        addressindex: 2,
      },
      {
        title: 'End',
        description: 'Ending point',
        selected: true,
        addresslnglat: [-122.4194, 37.7749],
        sourcelnglat: [-122.4194, 37.7749],
        displaylnglat: [-122.4194, 37.7749],
        key: 4,
        addressindex: 3,
      },
    ];

    it('should create locations from waypoints with correct types', () => {
      const result = makeLocations(mockWaypoints);

      expect(result).toEqual([
        {
          lon: -74.006,
          lat: 40.7128,
          type: 'break',
          radius: WAYPOINT_SNAP_RADIUS_M,
        },
        {
          lon: -118.2437,
          lat: 34.0522,
          type: 'via',
          radius: WAYPOINT_SNAP_RADIUS_M,
        },
        {
          lon: -87.6298,
          lat: 41.8781,
          type: 'via',
          radius: WAYPOINT_SNAP_RADIUS_M,
        },
        {
          lon: -122.4194,
          lat: 37.7749,
          type: 'break',
          radius: WAYPOINT_SNAP_RADIUS_M,
        },
      ]);
    });

    it('should create locations from single waypoint', () => {
      const singleWaypoint: ActiveWaypoints = [mockWaypoints[0]!];

      const result = makeLocations(singleWaypoint);

      expect(result).toEqual([
        {
          lon: -74.006,
          lat: 40.7128,
          type: 'break',
          radius: WAYPOINT_SNAP_RADIUS_M,
        },
      ]);
    });

    it('should create locations from two waypoints', () => {
      const twoWaypoints: ActiveWaypoints = mockWaypoints.slice(0, 2);

      const result = makeLocations(twoWaypoints);

      expect(result).toEqual([
        {
          lon: -74.006,
          lat: 40.7128,
          type: 'break',
          radius: WAYPOINT_SNAP_RADIUS_M,
        },
        {
          lon: -118.2437,
          lat: 34.0522,
          type: 'break',
          radius: WAYPOINT_SNAP_RADIUS_M,
        },
      ]);
    });

    it('should create locations from empty waypoints array', () => {
      const result = makeLocations([]);

      expect(result).toEqual([]);
    });
  });
});
