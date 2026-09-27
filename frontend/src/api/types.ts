/**
 * The wire types of the client entity (docs/specs/003-client-entity, §2–§3).
 *
 * Honesty rule that runs through this file: `null` means «не указано» — a
 * field the tourist never filled in, or one the server did not send. It is
 * never a stand-in for a real value and never a defaulted number, so a party
 * of "0 adults" and an unstated party stay distinguishable all the way to the
 * UI.
 */

/** Transport the plan was built for (Valhalla costing). */
export type Transport = 'pedestrian' | 'bicycle' | 'auto';

/** Ui language the tourist reads the guide in. */
export type ClientLanguage = 'ru' | 'en';

/** `client_preferences` as it comes back from the agent. */
export interface ClientPreferences {
  transport: Transport | null;
  time_budget_minutes: number | null;
  party_adults: number | null;
  party_children: number | null;
  /** Codes of the canonical taxonomy — not human-readable labels. */
  interests: string[] | null;
  language: ClientLanguage | null;
  /** `{категория: минуты}` — the tourist's own usual pace. */
  visit_minutes_by_category: Record<string, number> | null;
  updated_at?: string | null;
}

/**
 * `PUT /clients/me/preferences` is a partial update: only the keys present are
 * written, and an explicit `null` clears a field. Do not send a key you do not
 * mean to change.
 */
export type ClientPreferencesPatch = Partial<{
  transport: Transport | null;
  time_budget_minutes: number | null;
  party_adults: number | null;
  party_children: number | null;
  interests: string[] | null;
  language: ClientLanguage | null;
  visit_minutes_by_category: Record<string, number> | null;
}>;

/**
 * One row of `GET /clients/me/routes` — the light list, deliberately without
 * the plan. `distance_m`/`duration_min` are `null` when the stored plan does
 * not state them; they are never filled in with a guess.
 */
export interface SavedRouteSummary {
  id: string;
  /** What the tourist called it; may be empty. */
  name: string | null;
  query: string;
  created_at: string | null;
  stop_count: number | null;
  distance_m: number | null;
  duration_min: number | null;
}

/** A saved route as the list needs it, plus where this copy actually lives. */
export interface SavedRouteListItem extends SavedRouteSummary {
  /**
   * `true` when the server never took this route and the copy exists only in
   * this browser. The UI shows it as such — a local fallback is not a fact
   * about the tourist's account.
   */
  local_only: boolean;
}

/**
 * The full saved record (`GET /clients/me/routes/{id}`), including the plan.
 * `plan` is the verified TripPlan as it was stored, passed through untouched:
 * re-reading a saved route must not rebuild it into something else.
 */
export interface SavedRoute {
  id: string;
  name: string | null;
  query: string;
  plan: unknown;
  visit_overrides: Record<string, number> | null;
  created_at: string | null;
  updated_at: string | null;
}

/** `POST /clients/me/routes` body. */
export interface CreateRouteInput {
  query: string;
  plan: unknown;
  name?: string | null;
  visit_overrides?: Record<string, number> | null;
}

/** `201` answer of `POST /clients/me/routes`. */
export interface CreatedRoute {
  id: string;
  created_at: string | null;
}

/**
 * Machine reason codes the agent answers with (spec §3). `network_unavailable`
 * is added on the client: a request that never reached the server is the same
 * kind of «saving does not work right now» as a `503 storage_unavailable`.
 */
export type ClientApiErrorCode =
  | 'storage_unavailable'
  | 'route_not_found'
  | 'invalid_client_id'
  | 'too_many_routes'
  | 'network_unavailable'
  | 'bad_response'
  | 'server_error';

/** Codes that mean «this request cannot be saved right now» — never «saved». */
export const SAVING_UNAVAILABLE_CODES: readonly ClientApiErrorCode[] = [
  'storage_unavailable',
  'network_unavailable',
];

/**
 * One stop of a ready-made route (`GET /routes/itineraries`).
 *
 * The agent reads these fields from the same places table the planner uses, so
 * a card can only print facts the dataset actually holds — `opening_hours` and
 * `visit_minutes` are `null` where nothing is known, never a default.
 */
export interface ItineraryStop {
  place_id: number;
  /** Provenance key the curated file is authored against (`city:old-castle`). */
  source_url: string;
  name: string;
  category: string | null;
  town: string | null;
  district: string | null;
  lat: number;
  lon: number;
  visit_minutes: number | null;
  opening_hours: string | null;
  blurb: string | null;
  fun_fact: string | null;
  /** Extra facts for the marker card; the agent parses them like the planner. */
  fun_facts: string[];
  /** Real source links (Wikipedia and friends) where the dataset has them. */
  links: { title: string; url: string }[];
  ticket_price: string | null;
}

/** A curated itinerary: an ordered set of real places, no model involved. */
export interface Itinerary {
  id: string;
  title: string;
  blurb: string;
  transport: Transport;
  stop_count: number;
  /** Curated visit time of the stops; travel time is added when it is drawn. */
  visit_minutes: number;
  stops: ItineraryStop[];
}

export interface ItineraryList {
  items: Itinerary[];
  /**
   * Stop keys the dataset no longer holds. An itinerary can be served shorter
   * than authored; this is how the client can tell that it happened.
   */
  missing: string[];
}
