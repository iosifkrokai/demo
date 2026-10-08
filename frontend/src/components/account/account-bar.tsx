import { Link } from '@tanstack/react-router';
import { LogOut, MapPinned, ShieldCheck, UserRound } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { useAuth, useLogout } from '@/hooks/use-auth';

const menuLink =
  'flex items-center gap-2 rounded-xl px-2.5 py-2 text-meta transition-colors hover:bg-muted';

/**
 * The account control over the map (spec 005 §5).
 *
 * Fixed top-right, over both the desktop column and the mobile sheet, so there is
 * exactly one way in: «Войти» when anonymous, and a small menu — «Мои посещённые»,
 * «Админка» (only for an admin) and «Выйти» — when signed in.
 */
export function AccountBar() {
  const { user, authenticated, isAdmin, isLoading } = useAuth();
  const logout = useLogout();
  const [open, setOpen] = useState(false);

  if (isLoading) return null;

  return (
    <div
      data-testid="account-bar"
      className="pointer-events-auto fixed right-2 top-2 z-40 flex flex-col items-end gap-1"
    >
      {!authenticated ? (
        <Button asChild size="sm" variant="outline" data-testid="account-login">
          <Link to="/login">
            <UserRound className="size-4" aria-hidden="true" />
            Войти
          </Link>
        </Button>
      ) : (
        <>
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-expanded={open}
            data-testid="account-menu-button"
            onClick={() => setOpen((value) => !value)}
          >
            <UserRound className="size-4" aria-hidden="true" />
            <span className="max-w-[10rem] truncate">
              {user?.display_name || user?.email}
            </span>
          </Button>

          {open && (
            <div
              data-testid="account-menu"
              className="flex w-56 flex-col gap-0.5 rounded-2xl border border-border bg-card p-1.5 shadow-float"
            >
              <div className="truncate px-2.5 py-1 text-meta text-muted-foreground">
                {user?.email}
              </div>
              <Link
                to="/visited"
                className={menuLink}
                onClick={() => setOpen(false)}
                data-testid="account-visited"
              >
                <MapPinned className="size-4" aria-hidden="true" />
                Мои посещённые
              </Link>
              {isAdmin && (
                <Link
                  to="/admin"
                  className={menuLink}
                  onClick={() => setOpen(false)}
                  data-testid="account-admin"
                >
                  <ShieldCheck className="size-4" aria-hidden="true" />
                  Админка
                </Link>
              )}
              <button
                type="button"
                className={`${menuLink} text-left`}
                data-testid="account-logout"
                onClick={() => {
                  setOpen(false);
                  // Signing out must leave the gated app: without this the page
                  // the guard would refuse on the next navigation stays on screen.
                  logout.mutate(undefined, {
                    onSettled: () => window.location.assign('/login'),
                  });
                }}
              >
                <LogOut className="size-4" aria-hidden="true" />
                Выйти
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
