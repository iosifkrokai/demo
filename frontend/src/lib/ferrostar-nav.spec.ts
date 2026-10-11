/** Ferrostar bridge: the pure half. */

import { describe, expect, it } from 'vitest';
import type {
  RouteDeviation,
  RouteStep,
  SpokenInstruction,
  TripState,
  UserLocation,
  VisualInstruction,
} from '@stadiamaps/ferrostar';

import type { ParsedDirectionsGeometry } from '@/components/types';
import { metresBetween } from '@/components/parts/guide-format';

import {
  buildFerrostarRoute,
  extractCourse,
  extractDistanceRemaining,
  extractDistanceToNextManeuver,
  extractRemainingSteps,
  extractSpokenInstruction,
  extractVisualInstruction,
  isCompletelyOffRoute,
} from './ferrostar-nav';

/** An L-shaped walk: north 111 m, east 67 m, north 111 m, east 67 m. */
const SHAPE: number[][] = [
  [53.0, 23.0],
  [53.001, 23.0],
  [53.001, 23.001],
  [53.002, 23.001],
  [53.002, 23.002],
];

/** Length of the shape's segments, in metres. */
const SEGMENTS_M = SHAPE.slice(0, -1).map((point, i) => {
  const next = SHAPE[i + 1]!;
  return metresBetween(
    { lat: point[0]!, lon: point[1]! },
    { lat: next[0]!, lon: next[1]! }
  );
});

const maneuver = (
  over: Partial<ParsedDirectionsGeometry['trip']['legs'][0]['maneuvers'][0]>
) => ({
  type: 1,
  instruction: 'Идите прямо',
  verbal_pre_transition_instruction: '',
  time: 60,
  length: 0.1,
  cost: 0,
  begin_shape_index: 0,
  end_shape_index: 0,
  travel_mode: 'pedestrian',
  travel_type: 'street',
  ...over,
});

/** Two legs, two maneuvers each — the shape of every plan the sidebar builds. */
const VALHALLA = {
  id: 'valhalla_directions',
  decodedGeometry: SHAPE,
  trip: {
    locations: [
      {
        type: 'break',
        lat: 53.0,
        lon: 23.0,
        side_of_street: '',
        original_index: 0,
      },
      {
        type: 'break',
        lat: 53.001,
        lon: 23.001,
        side_of_street: '',
        original_index: 1,
      },
      {
        type: 'break',
        lat: 53.002,
        lon: 23.002,
        side_of_street: '',
        original_index: 2,
      },
    ],
    status_message: 'ok',
    status: 0,
    units: 'kilometers',
    language: 'ru-RU',
    summary: {
      length: 0.35,
      time: 500,
      cost: 0,
      has_time_restrictions: false,
      has_toll: false,
      has_highway: false,
      has_ferry: false,
      min_lat: 53,
      min_lon: 23,
      max_lat: 53.002,
      max_lon: 23.002,
    },
    legs: [
      {
        shape: '',
        summary: { length: 0.18, time: 250, cost: 0 },
        maneuvers: [
          maneuver({
            begin_shape_index: 0,
            end_shape_index: 1,
            instruction: 'Идите на север по Советской',
            street_names: ['Советская'],
          }),
          maneuver({
            type: 10,
            begin_shape_index: 1,
            end_shape_index: 2,
            instruction: 'Поверните направо',
          }),
        ],
      },
      {
        shape: '',
        summary: { length: 0.17, time: 250, cost: 0 },
        maneuvers: [
          maneuver({
            type: 2,
            begin_shape_index: 0,
            end_shape_index: 1,
            instruction: 'Идите на север по Замковой',
            street_names: ['Замковая'],
          }),
          maneuver({
            type: 4,
            begin_shape_index: 1,
            end_shape_index: 2,
            instruction: 'Вы прибыли в пункт назначения',
          }),
        ],
      },
    ],
  },
} as unknown as ParsedDirectionsGeometry;

describe('buildFerrostarRoute', () => {
  const built = buildFerrostarRoute(VALHALLA);

  it('turns every maneuver of every leg into a step', () => {
    expect(built).not.toBeNull();
    expect(built?.route.steps).toHaveLength(4);
    expect(built?.route.geometry).toHaveLength(SHAPE.length);
    expect(built?.route.steps.map((s) => s.instruction)).toEqual([
      'Идите на север по Советской',
      'Поверните направо',
      'Идите на север по Замковой',
      'Вы прибыли в пункт назначения',
    ]);
  });

  it('carries the whole line and its length onto the route', () => {
    const total = SEGMENTS_M.reduce((sum, m) => sum + m, 0);
    expect(built?.route.distance).toBeCloseTo(total, 3);
    expect(built?.route.geometry[0]).toEqual({ lat: 53.0, lng: 23.0 });
    expect(built?.route.bbox.sw).toEqual({ lat: 53.0, lng: 23.0 });
    expect(built?.route.bbox.ne).toEqual({ lat: 53.002, lng: 23.002 });
  });

  it('slices each step out of the line, in metres, not Valhalla units', () => {
    const steps = built!.route.steps;
    steps.forEach((step, i) => {
      expect(step.distance).toBeCloseTo(SEGMENTS_M[i]!, 3);
      expect(step.geometry.length).toBeGreaterThanOrEqual(2);
    });
    expect(steps[0]!.geometry[0]).toEqual({ lat: 53.0, lng: 23.0 });
    expect(steps[1]!.geometry.at(-1)).toEqual({ lat: 53.001, lng: 23.001 });
    expect(steps[2]!.geometry[0]).toEqual({ lat: 53.001, lng: 23.001 });
    expect(steps[3]!.geometry.at(-1)).toEqual({ lat: 53.002, lng: 23.002 });
    expect(steps[0]!.duration).toBe(60);
  });

  it('names the road when Valhalla named it', () => {
    expect(built?.route.steps[0]?.roadName).toBe('Советская');
    expect(built?.route.steps[2]?.roadName).toBe('Замковая');
    expect(built?.route.steps[1]?.roadName).toBeUndefined();
  });

  it('speaks each step at the four trigger distances with valid UUIDs', () => {
    const step = built!.route.steps[1]!;
    expect(
      step.spokenInstructions.map((s) => s.triggerDistanceBeforeManeuver)
    ).toEqual([400, 200, 50, 0]);
    expect(
      step.spokenInstructions.every((s) =>
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
          s.utteranceId
        )
      )
    ).toBe(true);
    expect(
      new Set(step.spokenInstructions.map((s) => s.utteranceId)).size
    ).toBe(4);
    expect(
      step.spokenInstructions.every((s) => s.text === 'Поверните направо')
    ).toBe(true);
    expect(step.spokenInstructions.every((s) => s.ssml === undefined)).toBe(
      true
    );
  });

  it('keeps the UI list parallel to the steps', () => {
    const along = SEGMENTS_M.reduce<number[]>(
      (acc, m) => [...acc, (acc[acc.length - 1] ?? 0) + m],
      [0]
    );
    expect(built?.maneuvers.map((m) => m.key)).toEqual([
      '0-0',
      '0-1',
      '1-0',
      '1-1',
    ]);
    expect(built?.maneuvers.map((m) => m.type)).toEqual([1, 10, 2, 4]);
    expect(built?.maneuvers.map((m) => Math.round(m.along))).toEqual(
      along.slice(0, 4).map(Math.round)
    );
    expect(built?.stepKeyToIndex.get('1-1')).toBe(3);
  });

  it('carries the stops as waypoints', () => {
    expect(built?.route.waypoints).toHaveLength(3);
    expect(built?.route.waypoints[0]?.coordinate).toEqual({
      lat: 53.0,
      lng: 23.0,
    });
    expect(built?.route.waypoints[2]?.coordinate).toEqual({
      lat: 53.002,
      lng: 23.002,
    });
    expect(built?.route.waypoints.every((w) => w.kind === 'Break')).toBe(true);
  });

  it('falls back to the ends of the line when Valhalla sent no locations', () => {
    const noLocations = {
      ...VALHALLA,
      trip: { ...VALHALLA.trip, locations: [] },
    } as ParsedDirectionsGeometry;
    const result = buildFerrostarRoute(noLocations);
    expect(result?.route.waypoints).toHaveLength(2);
    expect(result?.route.waypoints[0]?.coordinate).toEqual({
      lat: 53.0,
      lng: 23.0,
    });
    expect(result?.route.waypoints[1]?.coordinate).toEqual({
      lat: 53.002,
      lng: 23.002,
    });
  });

  it('says no to a route it cannot navigate', () => {
    expect(buildFerrostarRoute(null)).toBeNull();
    expect(
      buildFerrostarRoute({
        ...VALHALLA,
        decodedGeometry: [[53, 23]],
      } as ParsedDirectionsGeometry)
    ).toBeNull();
    expect(
      buildFerrostarRoute({
        ...VALHALLA,
        trip: { ...VALHALLA.trip, legs: [] },
      } as ParsedDirectionsGeometry)
    ).toBeNull();
    expect(
      buildFerrostarRoute({
        ...VALHALLA,
        trip: {
          ...VALHALLA.trip,
          legs: [{ shape: '', summary: VALHALLA.trip.summary, maneuvers: [] }],
        },
      } as unknown as ParsedDirectionsGeometry)
    ).toBeNull();
  });
});

const LOCATION: UserLocation = {
  coordinates: { lat: 53.0, lng: 23.0 },
  horizontalAccuracy: 8,
  courseOverGround: undefined,
  timestamp: { secs_since_epoch: 1_700_000_000, nanos_since_epoch: 0 },
  speed: undefined,
};

const step = (instruction: string): RouteStep => ({
  geometry: [
    { lat: 53.0, lng: 23.0 },
    { lat: 53.001, lng: 23.0 },
  ],
  distance: 111,
  duration: 60,
  roadName: undefined,
  exits: [],
  instruction,
  spokenInstructions: [],
  visualInstructions: [],
  annotations: undefined,
  incidents: [],
  drivingSide: undefined,
  roundaboutExitNumber: undefined,
});

const SPOKEN: SpokenInstruction = {
  text: 'Поверните направо',
  ssml: undefined,
  triggerDistanceBeforeManeuver: 200,
  utteranceId: '0-1-200',
};

const VISUAL: VisualInstruction = {
  primaryContent: {
    text: 'Поверните направо',
    maneuverType: 'turn',
    maneuverModifier: 'right',
    roundaboutExitDegrees: undefined,
    laneInfo: undefined,
    exitNumbers: [],
  },
  secondaryContent: undefined,
  subContent: undefined,
  triggerDistanceBeforeManeuver: 200,
};

interface NavigatingOverrides {
  deviation?: RouteDeviation;
  progress?: { distanceToNextManeuver?: number; distanceRemaining?: number };
  /** `undefined` means «Ferrostar said nothing» — a real value, so overrides are read by key. */
  spokenInstruction?: SpokenInstruction | undefined;
  visualInstruction?: VisualInstruction | undefined;
  remainingSteps?: RouteStep[];
  course?: { degrees: number; accuracy: number | undefined } | undefined;
}

/** A `Navigating` TripState, assembled by hand — the shape Ferrostar returns. */
const navigating = (over: NavigatingOverrides = {}): TripState => {
  const deviation = over.deviation ?? 'NoDeviation';
  const progress = over.progress ?? {};
  const spokenInstruction =
    'spokenInstruction' in over ? over.spokenInstruction : SPOKEN;
  const visualInstruction =
    'visualInstruction' in over ? over.visualInstruction : VISUAL;
  const remainingSteps = over.remainingSteps ?? [
    step('Поверните направо'),
    step('Вы прибыли'),
  ];
  const course =
    'course' in over
      ? over.course
      : { degrees: 12.5, accuracy: undefined as number | undefined };
  return {
    Navigating: {
      currentStepGeometryIndex: 0,
      userLocation: LOCATION,
      snappedUserLocation: { ...LOCATION, courseOverGround: course },
      remainingSteps,
      remainingWaypoints: [],
      progress: {
        distanceToNextManeuver: progress.distanceToNextManeuver ?? 214,
        distanceRemaining: progress.distanceRemaining ?? 812,
        durationRemaining: 300,
      },
      summary: {
        distanceTraveled: 100,
        snappedDistanceTraveled: 98,
        startedAt: new Date(0),
        endedAt: null,
      },
      deviation,
      visualInstruction,
      spokenInstruction,
      annotationJson: undefined,
    },
  };
};

const OFF_STEP: RouteDeviation = {
  Deviation: { kind: { OffStepOnRoute: { deviationFromStepLine: 12 } } },
};
const COMPLETELY_OFF: RouteDeviation = {
  Deviation: { kind: { CompletelyOffRoute: { deviationFromRouteLine: 340 } } },
};

describe('Ferrostar state extractors', () => {
  it('reads the course of the snapped position', () => {
    expect(extractCourse(navigating())).toBe(12.5);
    expect(extractCourse(navigating({ course: undefined }))).toBeNull();
  });

  it('reads the progress Ferrostar computed', () => {
    expect(extractDistanceToNextManeuver(navigating())).toBe(214);
    expect(extractDistanceRemaining(navigating())).toBe(812);
  });

  it('separates «off the line» from «off the route»', () => {
    expect(isCompletelyOffRoute(navigating())).toBe(false);
    expect(isCompletelyOffRoute(navigating({ deviation: OFF_STEP }))).toBe(
      false
    );
    expect(
      isCompletelyOffRoute(navigating({ deviation: COMPLETELY_OFF }))
    ).toBe(true);
  });

  it('reads the announcement and the banner', () => {
    expect(extractSpokenInstruction(navigating())).toEqual(SPOKEN);
    expect(extractVisualInstruction(navigating())).toEqual(VISUAL);
    expect(extractRemainingSteps(navigating())).toHaveLength(2);
  });

  it('has nothing to say before or after the trip', () => {
    const idle: TripState = { Idle: { user_location: undefined } };
    const nav = navigating();
    if (!('Navigating' in nav)) throw new Error('fixture is not Navigating');
    const complete: TripState = {
      Complete: { user_location: LOCATION, summary: nav.Navigating.summary },
    };

    for (const state of [idle, complete] as TripState[]) {
      expect(extractCourse(state)).toBeNull();
      expect(extractDistanceToNextManeuver(state)).toBeNull();
      expect(extractDistanceRemaining(state)).toBeNull();
      expect(extractSpokenInstruction(state)).toBeNull();
      expect(extractVisualInstruction(state)).toBeNull();
      expect(extractRemainingSteps(state)).toEqual([]);
      expect(isCompletelyOffRoute(state)).toBeNull();
    }
  });

  it('tolerates a fix with nothing to announce', () => {
    const quiet = navigating({
      spokenInstruction: undefined,
      visualInstruction: undefined,
      remainingSteps: [],
    });
    expect(extractSpokenInstruction(quiet)).toBeNull();
    expect(extractVisualInstruction(quiet)).toBeNull();
    expect(extractRemainingSteps(quiet)).toEqual([]);
  });

  it('never throws on a state that is not there at all', () => {
    for (const state of [null, undefined] as unknown as TripState[]) {
      expect(extractCourse(state)).toBeNull();
      expect(extractDistanceToNextManeuver(state)).toBeNull();
      expect(extractDistanceRemaining(state)).toBeNull();
      expect(extractSpokenInstruction(state)).toBeNull();
      expect(extractVisualInstruction(state)).toBeNull();
      expect(extractRemainingSteps(state)).toEqual([]);
      expect(isCompletelyOffRoute(state)).toBeNull();
    }
  });
});
