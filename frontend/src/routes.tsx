import type { QueryClient } from '@tanstack/react-query';
import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
  retainSearchParams,
} from '@tanstack/react-router';
import { zodValidator } from '@tanstack/zod-adapter';
import { z } from 'zod';
import { App } from './app';
import { RootComponent } from './components/root-component';
import { authQueryOptions } from './hooks/use-auth';
import * as TanStackQueryProvider from './lib/tanstack-query/root-provider';
import { loginRedirectFor } from './utils/auth-guard';
import { searchParamsSchema, isValidTab } from './utils/route-schemas';
import type { Profile } from './stores/common-store';

const defaultProfile = ((import.meta.env
  .VITE_DEFAULT_COSTING_MODEL as string) || 'bicycle') as Profile;

/** While a lazy route's chunk is fetched: skeleton rows, per DESIGN.md — not a spinner. */
const RoutePending = () => (
  <div
    data-testid="route-pending"
    className="flex min-h-[100dvh] items-center justify-center bg-background p-6"
  >
    <div className="w-full max-w-sm space-y-3">
      <div className="h-12 animate-pulse rounded-xl bg-muted" />
      <div className="h-12 animate-pulse rounded-xl bg-muted" />
      <div className="h-12 animate-pulse rounded-xl bg-muted" />
    </div>
  </div>
);

export const rootRoute = createRootRoute({ component: RootComponent });

const TanStackQueryProviderContext = TanStackQueryProvider.getContext();

/** The mandatory-login gate: without a session there is no app. */
const requireAuth = async (args: {
  context: unknown;
  location: { pathname: string; searchStr: string; hash: string };
}): Promise<void> => {
  const queryClient = (args.context as { queryClient?: QueryClient })
    .queryClient;
  const authenticated = queryClient
    ? (await queryClient.ensureQueryData(authQueryOptions)).authenticated
    : false;
  const target = loginRedirectFor(
    {
      pathname: args.location.pathname,
      searchStr: args.location.searchStr,
      hash: args.location.hash,
    },
    authenticated
  );
  if (target) throw redirect(target);
};

/** `?redirect=/directions?profile=bicycle` — where to go back to after signing in. */
const loginSearchSchema = z.object({ redirect: z.string().optional() });

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: App,
  beforeLoad: () => {
    throw redirect({
      to: '/$activeTab',
      params: { activeTab: 'directions' },
      search: {
        profile: defaultProfile,
      },
    });
  },
});

/** The tab/profile half of the `/$activeTab` gate. */
export const activeTabBeforeLoad = ({
  params,
  search,
}: {
  params: { activeTab: string };
  search: { profile?: string };
}): void => {
  if (!isValidTab(params.activeTab)) {
    throw redirect({
      to: '/$activeTab',
      params: { activeTab: 'directions' },
      search: {
        profile: defaultProfile,
      },
    });
  }
  if (!search.profile) {
    throw redirect({
      to: '/$activeTab',
      params: { activeTab: params.activeTab },
      search: {
        ...search,
        profile: defaultProfile,
      },
    });
  }
};

const activeTabRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/$activeTab',
  component: App,
  validateSearch: zodValidator(searchParamsSchema),
  search: {
    middlewares: [
      retainSearchParams([
        'profile',
        'style',
        'use_ferry',
        'use_highways',
        'use_tolls',
        'alternates',
      ]),
    ],
  },
  beforeLoad: async (args) => {
    await requireAuth(args);
    activeTabBeforeLoad(args);
  },
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: lazyRouteComponent(
    () => import('./components/auth/login-page'),
    'LoginPage'
  ),
  validateSearch: zodValidator(loginSearchSchema),
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/register',
  component: lazyRouteComponent(
    () => import('./components/auth/register-page'),
    'RegisterPage'
  ),
  validateSearch: zodValidator(loginSearchSchema),
});

const visitedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/visited',
  component: lazyRouteComponent(
    () => import('./components/visited/visited-page'),
    'VisitedPage'
  ),
  beforeLoad: requireAuth,
});

const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin',
  component: lazyRouteComponent(
    () => import('./components/admin/admin-page'),
    'AdminPage'
  ),
  beforeLoad: requireAuth,
});

export const routeTree = rootRoute.addChildren([
  indexRoute,
  activeTabRoute,
  loginRoute,
  registerRoute,
  visitedRoute,
  adminRoute,
]);

export const router = createRouter({
  routeTree,
  context: { ...TanStackQueryProviderContext },
  defaultPreload: 'intent',
  scrollRestoration: true,
  defaultStructuralSharing: true,
  defaultPreloadStaleTime: 0,
  defaultPendingComponent: RoutePending,
  basepath: import.meta.env.BASE_URL,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
