/** The mandatory-login decision. */

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

/** Where to send a visitor who is not signed in, or `null` when they may stay. */
export const loginRedirectFor = (
  location: GuardLocation,
  authenticated: boolean
): LoginRedirect | null =>
  authenticated
    ? null
    : { to: '/login', search: { redirect: returnPathOf(location) } };
