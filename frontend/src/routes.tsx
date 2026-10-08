import type { QueryClient } from '@tanstack/react-query';
import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  retainSearchParams,
} from '@tanstack/react-router';
import { zodValidator } from '@tanstack/zod-adapter';
import { z } from 'zod';
import { App } from './app';
import { AdminPage } from './components/admin/admin-page';
import { LoginPage } from './components/auth/login-page';
import { RegisterPage } from './components/auth/register-page';
import { RootComponent } from './components/root-component';
import { VisitedPage } from './components/visited/visited-page';
import { authQueryOptions } from './hooks/use-auth';
import * as TanStackQueryProvider from './lib/tanstack-query/root-provider';
import { loginRedirectFor } from './utils/auth-guard';
import { searchParamsSchema, isValidTab } from './utils/route-schemas';
import type { Profile } from './stores/common-store';

const defaultProfile = ((import.meta.env
  .VITE_DEFAULT_COSTING_MODEL as string) || 'bicycle') as Profile;

export const rootRoute = createRootRoute({ component: RootComponent });

const TanStackQueryProviderContext = TanStackQueryProvider.getContext();

/**
 * The mandatory-login gate (spec 005 §5): without a session there is no app.
 *
 * It reads the *same* `authQueryOptions` entry `useAuth` uses, so the gate and the
 * UI can never disagree about who is signed in. The visitor is sent to `/login`
 * with the path they asked for, so signing in returns them to it.
 */
const requireAuth = async (args: {
  context: unknown;
  location: { pathname: string; searchStr: string; hash: string };
}): Promise<void> => {
  // `context` is not statically typed at route-definition time (the router's
  // `Register` is declared at the bottom of this file), so it is read defensively.
  // «Unknown» fails closed: an unverifiable session is treated as no session.
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
        'lang',
      ]),
    ],
  },
  beforeLoad: async ({ context, location, params, search }) => {
    // Login first: an anonymous visitor is not asked to pick a tab.
    await requireAuth({ context, location });
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
  },
});

// Account pages (spec 005). `/login` and `/register` are the only public routes;
// everything else is behind `requireAuth`.
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
  validateSearch: zodValidator(loginSearchSchema),
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/register',
  component: RegisterPage,
  validateSearch: zodValidator(loginSearchSchema),
});

const visitedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/visited',
  component: VisitedPage,
  beforeLoad: requireAuth,
});

const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin',
  component: AdminPage,
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
  basepath: import.meta.env.BASE_URL,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
