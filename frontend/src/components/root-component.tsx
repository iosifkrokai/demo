import { Outlet, useRouterState } from '@tanstack/react-router';
import { lazy, Suspense } from 'react';

import { AccountBar } from './account/account-bar';

/**
 * Devtools are opt-in: they used to mount (and open a panel) over the map in
 * every dev run, which reads as a broken overlay for anyone looking at the app.
 * Start the dev server with VITE_DEVTOOLS=1 when you actually want them.
 */
const DEVTOOLS_ENABLED =
  import.meta.env.DEV && import.meta.env.VITE_DEVTOOLS === '1';

const TanStackDevtools = DEVTOOLS_ENABLED
  ? lazy(() =>
      import('@tanstack/react-devtools').then((mod) => ({
        default: mod.TanStackDevtools,
      }))
    )
  : () => null;

const ReactQueryDevtoolsPanel = DEVTOOLS_ENABLED
  ? lazy(() =>
      import('@tanstack/react-query-devtools').then((mod) => ({
        default: mod.ReactQueryDevtoolsPanel,
      }))
    )
  : () => null;

const TanStackRouterDevtoolsPanel = DEVTOOLS_ENABLED
  ? lazy(() =>
      import('@tanstack/react-router-devtools').then((mod) => ({
        default: mod.TanStackRouterDevtoolsPanel,
      }))
    )
  : () => null;

export const RootComponent = () => {
  // The account bar belongs over the app, not over the sign-in forms: on `/login`
  // and `/register` a «Войти» pill would be asking for the thing already on screen.
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const showAccountBar = pathname !== '/login' && pathname !== '/register';

  return (
    <>
      <Outlet />
      {showAccountBar && <AccountBar />}
      {DEVTOOLS_ENABLED && (
        <Suspense fallback={null}>
          <TanStackDevtools
            plugins={[
              {
                name: 'TanStack Query',
                render: (
                  <Suspense fallback={null}>
                    <ReactQueryDevtoolsPanel />
                  </Suspense>
                ),
                defaultOpen: false,
              },
              {
                name: 'TanStack Router',
                render: (
                  <Suspense fallback={null}>
                    <TanStackRouterDevtoolsPanel />
                  </Suspense>
                ),
                defaultOpen: false,
              },
            ]}
          />
        </Suspense>
      )}
    </>
  );
};
