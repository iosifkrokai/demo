/**
 * The wire types of the client entity.
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
  /** The point's picture, or null when there is none (the usual case). */
  photo: Photo | null;
}

/**
 * A picture of a point, always with the credit the licence requires.
 *
 * An incomplete record never reaches the client: the backend drops a photo
 * whose author or licence is missing (see `parse_photo` there), so `author` and
 * `license` are always printable when this object is present.
 */
export interface Photo {
  url: string;
  author: string;
  license: string;
  /** The file page the credit was read from, for anyone re-checking it. */
  source: string | null;
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
  /**
   * Secondary points the author put on the way (a toilet, a café).
   *
   * They are NOT stops: not numbered, not counted in `stop_count`, not part of
   * `visit_minutes`. The server splits them by the taxonomy's role, so a toilet
   * can never stand where the guide promised a sight.
   */
  services?: ItineraryStop[];
}

export interface ItineraryList {
  items: Itinerary[];
  /**
   * Stop keys the dataset no longer holds. An itinerary can be served shorter
   * than authored; this is how the client can tell that it happened.
   */
  missing: string[];
}

/**
 * One point of the full catalogue (`GET /places`), for the «все точки» tab.
 *
 * Same shape as a ready-made route stop: the agent reads both from the same
 * `places` table through the same payload, so a card prints identical facts
 * whether the point came from a plan, an itinerary or the whole-map browse.
 */
export type Place = ItineraryStop;

export interface PlacesAnswer {
  items: Place[];
  total: number;
  /**
   * True when the agent cut the list at its own cap — a backstop against a
   * runaway query, never a feature. The Grodno dataset fits in one answer.
   */
  capped: boolean;
}

/**
 * A route line as Valhalla and the agent both speak it (WGS84, GeoJSON).
 *
 * The app already holds this for the line it draws, so asking «что есть по
 * пути» needs no recomputation.
 */
export interface RouteLine {
  type: 'LineString';
  coordinates: [number, number][];
}

/**
 * One secondary point beside the route: a café, a toilet, a hotel.
 *
 * `off_line_m` and `along_m` are measured (PostGIS). `detour_confirmed` is
 * always false here: the walk needed to reach the point is a real Valhalla
 * route, which this answer does not build — so no screen may print «+2 мин»
 * from it. `hours_known` is false for the ~55% of service points whose hours
 * the dataset does not have; unknown hours are never shown as «открыто».
 */
export interface ServiceAlong {
  id: number;
  source_url: string;
  name: string;
  category: string;
  town: string | null;
  lat: number;
  lon: number;
  opening_hours: string | null;
  hours_known: boolean;
  off_line_m: number;
  along_m: number;
  along_fraction: number;
  detour_confirmed: false;
}

export interface ServicesAlongAnswer {
  items: ServiceAlong[];
  /** What was measured, named for what it is. */
  measured: string;
  /** What was deliberately not measured, and why nothing here is a detour. */
  not_measured: string;
  detour_confirmed: boolean;
  profile: string;
  categories: string[];
  max_off_line_m: number;
  line_m: number;
  result_cap: number;
  capped: boolean;
  /** Set when the answer is empty on purpose (e.g. no service categories). */
  reason?: string;
}

// Accounts, visits and the admin panel (spec 005)
//
// Same honesty rule as everywhere else: `null` means «не указано» — a field the
// server did not send — never a stand-in for a real value.

/** A user's authority. Drives whether the admin panel is reachable at all. */
export type UserRole = 'user' | 'admin';

/** A signed-in account as every response carries it — never the password hash. */
export interface AccountUser {
  id: string;
  email: string;
  display_name: string | null;
  role: UserRole;
  created_at: string | null;
}

/** `GET /auth/me` — the honest state, anonymous included. */
export interface AuthState {
  authenticated: boolean;
  user: AccountUser | null;
}

/** One row of `GET /admin/users`, with the counts an admin asks about. */
export interface AdminUser extends AccountUser {
  client_id: string | null;
  last_login_at: string | null;
  saved_routes: number;
  visited: number;
}

export interface AdminUserList {
  items: AdminUser[];
  total: number;
}

export interface AdminPlaceList {
  items: Place[];
  total: number;
}

/** `GET /admin/stats` — the numbers in the admin header. */
export interface AdminStats {
  users: number;
  admins: number;
  places: number;
  visited: number;
  saved_routes: number;
}

/** A visited place: the whole catalogue payload plus *when* it was marked. */
export interface VisitedPlace extends Place {
  visited_at: string | null;
}

export interface VisitedList {
  items: VisitedPlace[];
  count: number;
}

/** `POST /admin/places` / `PATCH /admin/places/{id}` body. */
export interface AdminPlaceInput {
  name?: string;
  lat?: number;
  lon?: number;
  source_url?: string;
  category?: string | null;
  town?: string | null;
  district?: string | null;
  blurb?: string | null;
  fun_fact?: string | null;
  visit_minutes?: number | null;
  opening_hours?: string | null;
  ticket_price?: string | null;
}

/**
 * Machine reason codes the accounts API answers with (spec 005 §3).
 * `network_unavailable` / `bad_response` / `server_error` are added on the client,
 * the way `client.ts` does it — a request that never reached the server is the
 * same class of failure as a typed refusal.
 */
export type AccountApiErrorCode =
  | 'storage_unavailable'
  | 'not_authenticated'
  | 'not_admin'
  | 'invalid_credentials'
  | 'email_taken'
  | 'weak_password'
  | 'invalid_email'
  | 'user_not_found'
  | 'place_not_found'
  | 'source_taken'
  | 'last_admin'
  | 'self_role'
  | 'self_delete'
  | 'invalid_request'
  | 'network_unavailable'
  | 'bad_response'
  | 'server_error';

/** Codes that mean «the store is unreachable right now» — never a real answer. */
export const STORAGE_DOWN_CODES: readonly AccountApiErrorCode[] = [
  'storage_unavailable',
  'network_unavailable',
];
