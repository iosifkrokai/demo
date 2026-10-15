import type { Photo } from '@/api/types';

export interface ActiveWaypoint {
  title: string;
  description?: string;
  selected?: boolean;
  addresslnglat?: [number, number];
  sourcelnglat?: [number, number];
  displaylnglat: [number, number];
  key: number;
  addressindex: number;
}

/** `result_mode` for /routes/generate: `route` a ready itinerary, `catalogue` grouped. */
export type ResultMode = 'route' | 'catalogue';

/** How strongly an amenity is wanted. */
export type AmenityStrength = 'hard' | 'soft';

export type ActiveWaypoints = ActiveWaypoint[];

export interface ParsedDirectionsGeometry {
  trip: Trip;
  id: string;
  decodedGeometry: number[][];
  alternates?: ValhallaRouteResponse[];
  /** Which line is on screen. */
  source?: 'agent' | 'client';
  /** False when an agent plan has no usable geometry: the map says so, no substitution. */
  hasVerifiedLine?: boolean;
}

export interface Trip {
  locations: Location[];
  legs: Leg[];
  summary: Summary;
  status_message: string;
  status: number;
  units: string;
  language: string;
  warnings?: ValhallaWarning[];
}

export interface Location {
  type: string;
  lat: number;
  lon: number;
  side_of_street: string;
  original_index: number;
}

/** One maneuver for the guide UI: Valhalla `type` (drives the icon) and position in metres. */
export interface GuideManeuver {
  key: string;
  type: number;
  instruction: string;
  /** Metres from the start of the line to the manoeuvre's begin point. */
  along: number;
}

export interface Leg {
  maneuvers: Maneuver[];
  summary: Summary;
  shape: string;
}

export interface Maneuver {
  type: number;
  instruction: string;
  verbal_succinct_transition_instruction?: string;
  verbal_pre_transition_instruction: string;
  verbal_post_transition_instruction?: string;
  street_names?: string[];
  bearing_after?: number;
  time: number;
  length: number;
  cost: number;
  begin_shape_index: number;
  end_shape_index: number;
  verbal_multi_cue?: boolean;
  travel_mode: string;
  travel_type: string;
  verbal_transition_alert_instruction?: string;
  bearing_before?: number;
  roundabout_exit_count?: number;
  begin_street_names?: string[];
  rough?: boolean;
  toll?: boolean;
  ferry?: boolean;
}

export interface Summary {
  has_time_restrictions: boolean;
  has_toll: boolean;
  has_highway: boolean;
  has_ferry: boolean;
  min_lat: number;
  min_lon: number;
  max_lat: number;
  max_lon: number;
  time: number;
  length: number;
  cost: number;
}

export interface NominationResponse {
  place_id: number;
  licence: string;
  osm_type: string;
  osm_id: number;
  lat: string;
  lon: string;
  class: string;
  type: string;
  place_rank: number;
  importance: number;
  addresstype: string;
  name: string;
  display_name: string;
  address: Address;
  boundingbox: string[];
}

export interface Address {
  suburb: string;
  town: string;
  state: string;
  'ISO3166-2-lvl4'?: string;
  region: string;
  postcode: string;
  country: string;
  country_code: string;
}

export interface ValhallaWarning {
  code: number;
  text: string;
}

export interface ValhallaRouteResponse {
  id: 'valhalla_directions';
  trip: Trip;
  alternates?: ValhallaRouteResponse[];
}

/** One stop of an agent-planned route, as POST /routes/generate returns it. */
/** A line as GeoJSON spells it: coordinates are `[lon, lat]`. */
export interface GeoLineString {
  type?: string;
  coordinates?: number[][];
}

export interface AgentPoint {
  id: number;
  /** The client's own name for the stop, echoed back; absent for a catalogued stop. */
  key?: string | null;
  name: string;
  category?: string | null;
  lat: number;
  lon: number;
  blurb?: string | null;
  fun_fact?: string | null;
  fun_facts?: string[];
  photo?: Photo | null;
  links?: Array<{ title: string; url: string }>;
  visit_minutes?: number | null;
  opening_hours?: string | null;
  ticket_price?: string | null;
  /** `true` when admission is free; `false`/absent when paid or unknown. */
  is_free?: boolean | null;
  town?: string | null;
  district?: string | null;
}

export interface AgentBudget {
  budget_minutes: number | null;
  total_minutes: number;
  walk_minutes: number;
  visit_minutes: number;
  fits: boolean;
}

/** What the planner managed with the request — or that it is still working. */
export type RoutePlanStatus = 'ready' | 'degraded' | 'infeasible' | 'pending';

/** What a step-free walk cost, measured against the same stops taken directly. */
export interface StepFreeInfo {
  walk_minutes: number;
  without_steps_minutes: number;
  detour_minutes: number;
  length_km?: number | null;
  without_steps_length_km?: number | null;
}

/** Why a requirement went unmet. */
export type PlanRequirementReason =
  | 'must_visit_on_route'
  | 'must_visit_absent'
  | 'must_visit_unroutable'
  | 'must_visit_outside_coverage'
  | 'service_on_route'
  | 'service_along_route'
  | 'hard_service_absent'
  | 'soft_service_absent'
  | 'service_not_measured'
  | 'interest_absent'
  | 'avoid_honoured'
  | 'avoid_violated'
  | 'free_entry_proven'
  | 'paid_stop_on_route'
  | 'admission_unknown'
  | 'open_at_visit'
  | 'closed_at_visit'
  | 'hours_unknown'
  | 'visit_time_unknown'
  | 'step_free_proven'
  | 'steps_on_route'
  | 'steps_unknown'
  | 'route_missing'
  | 'geometry_missing';

/** One requirement from the agent's interpretation of the request. */
export interface PlanRequirement {
  kind: string;
  strength: string;
  code: string | null;
  name: string;
  origin: string;
  status: string;
  reason: PlanRequirementReason | null;
  place_ids: number[];
}

/** The agent's account of the plan: requirements honoured, missed, or not understood. */
export interface PlanInterpretation {
  status: RoutePlanStatus;
  requirements: PlanRequirement[];
  unmet: PlanRequirement[];
  unknowns: string[];
}

/** POST /routes/generate — either a ready plan or the honest reason there is none. */
export interface AgentRouteResponse {
  /** `route` a walkable itinerary, `catalogue` a list of places to choose from. */
  result_mode?: ResultMode;
  points?: AgentPoint[];
  budget?: AgentBudget;
  costing?: string | null;
  /** The drawn line, when the answer carries one. */
  shape?: GeoLineString | null;
  summary?: { length_km?: number | null; time_seconds?: number | null };
  status?: RoutePlanStatus;
  /** The measured price of a step-free detour, present only when one was taken. */
  step_free?: StepFreeInfo | null;
  interpretation?: PlanInterpretation;
}

export interface ValhallaOptimizedRouteResponse {
  trip: Trip;
  id?: string;
}
