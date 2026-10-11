/** The wire types of the client entity. */

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

/** Partial update for `PUT /clients/me/preferences`: present keys only; `null` clears. */
export type ClientPreferencesPatch = Partial<{
  transport: Transport | null;
  time_budget_minutes: number | null;
  party_adults: number | null;
  party_children: number | null;
  interests: string[] | null;
  language: ClientLanguage | null;
  visit_minutes_by_category: Record<string, number> | null;
}>;

/** One row of `GET /clients/me/routes` — the light list, deliberately without the plan. */
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
  /** `true` when the server never took this route and the copy exists only in this browser. */
  local_only: boolean;
}

/** The full saved record (`GET /clients/me/routes/{id}`), including the plan. */
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

/** Machine reason codes the agent answers with. */
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

/** One stop of a ready-made route (`GET /routes/itineraries`). */
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

/** A picture of a point, always with the credit the licence requires. */
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
  /** Secondary points the author put on the way (a toilet, a café). */
  services?: ItineraryStop[];
}

export interface ItineraryList {
  items: Itinerary[];
  /** Stop keys the dataset no longer holds. */
  missing: string[];
}

/** One point of the full catalogue (`GET /places`), for the «все точки» tab. */
export type Place = ItineraryStop;

export interface PlacesAnswer {
  items: Place[];
  total: number;
  /** True when the agent cut the list at its own cap (a backstop against a runaway query). */
  capped: boolean;
}

/** A route line as Valhalla and the agent both speak it (WGS84, GeoJSON). */
export interface RouteLine {
  type: 'LineString';
  coordinates: [number, number][];
}

/** One secondary point beside the route: a café, a toilet, a hotel. */
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

/** A user's authority. */
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

/** Machine reason codes the accounts API answers with. */
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
