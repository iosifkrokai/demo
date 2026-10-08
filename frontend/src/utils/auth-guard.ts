/**
 * The mandatory-login decision (spec 005 §5).
 *
 * Kept as a pure function — no router, no query client, no fetch — so the rule
 * «no session, no app» is unit-testable on its own, and `routes.tsx` only has to
 * turn the answer into a `redirect()`.
 *
 * The `redirect` it returns is a **relative** path (`pathname + search + hash`),
 * never an absolute href: after login the app navigates back to it with the
 * router, and an absolute URL would leave the app's own origin/base path.
 */

export interface GuardLocation {
  pathname: string;
  /** Query string including the leading `?`, as TanStack Router reports it. */
  searchStr?: string;
  hash?: string;
}

export interface LoginRedirect {
  to: '/login';
  search: { redirect: string };
}

/** The path to come back to after signing in. */
export const returnPathOf = (location: GuardLocation): string =>
  `${location.pathname}${location.searchStr ?? ''}${location.hash ?? ''}`;

/**
 * Where to send a visitor who is not signed in, or `null` when they may stay.
 */
export const loginRedirectFor = (
  location: GuardLocation,
  authenticated: boolean
): LoginRedirect | null =>
  authenticated
    ? null
    : { to: '/login', search: { redirect: returnPathOf(location) } };
