import { Outlet, useRouterState } from '@tanstack/react-router';
import { lazy, Suspense } from 'react';

import { AccountBar } from './account/account-bar';

/** Devtools are opt-in, so they never mount over the map unless enabled. */
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
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const ACCOUNT_BAR_HIDDEN_ROUTES = [
    '/login',
    '/register',
    '/admin',
    '/visited',
  ];
  const showAccountBar = !ACCOUNT_BAR_HIDDEN_ROUTES.includes(pathname);

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
