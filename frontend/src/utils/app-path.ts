/**
 * In-app path that respects the deploy base.
 *
 * The app can be served from a sub-path (PR previews are built with
 * `homepage` rewritten to `<host>/<PR#>/`, and the router uses
 * `import.meta.env.BASE_URL`). A raw `window.location.assign('/login')` ignores
 * that base and sends the browser to the origin root, leaving the app — so
 * full-page navigations (the login redirect, sign-out) go through this helper.
 */
export function appPath(path: string): string {
  const base = import.meta.env.BASE_URL || '/';
  const trimmedBase = base.endsWith('/') ? base.slice(0, -1) : base;
  const trimmedPath = path.startsWith('/') ? path : `/${path}`;
  return `${trimmedBase}${trimmedPath}` || '/';
}
