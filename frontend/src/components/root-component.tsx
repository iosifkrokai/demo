import { Outlet, useRouterState } from '@tanstack/react-router';

import { AccountBar } from './account/account-bar';

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
    </>
  );
};
