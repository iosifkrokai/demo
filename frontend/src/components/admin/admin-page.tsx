import { Link } from '@tanstack/react-router';
import { ShieldCheck } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { useAuth } from '@/hooks/use-auth';
import { useAdminStats } from '@/hooks/use-admin';

import { PlacesPanel } from './places-panel';
import { UsersPanel } from './users-panel';

/**
 * The admin section (spec 005 §5): users and places, over one dashboard header.
 *
 * Access is decided by the server, not by this page: a plain user is refused with
 * `403 not_admin` regardless of what the UI renders. So the page does not pretend
 * the section is secret — it says plainly that admin rights are needed, and never
 * fires an `/admin/*` request for a non-admin.
 */

type Tab = 'users' | 'places';

const StatCard = ({ label, value }: { label: string; value: number }) => (
  <div className="flex flex-col rounded-2xl border border-border bg-card px-3 py-2 shadow-card">
    <span className="text-title font-semibold">{value}</span>
    <span className="text-meta text-muted-foreground">{label}</span>
  </div>
);

export function AdminPage() {
  const { authenticated, isAdmin, user, isLoading } = useAuth();
  const stats = useAdminStats(isAdmin);
  const [tab, setTab] = useState<Tab>('users');

  if (isLoading) return null;

  if (!authenticated) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-background p-4">
        <div className="flex max-w-sm flex-col items-center gap-3 rounded-2xl border border-border bg-card p-6 text-center shadow-card">
          <ShieldCheck
            className="size-8 text-muted-foreground"
            aria-hidden="true"
          />
          <h1 className="text-title font-semibold">Админка</h1>
          <p className="text-meta text-muted-foreground">
            Раздел доступен только администраторам. Войдите под аккаунтом с
            правами админа.
          </p>
          <Button asChild>
            <Link to="/login">Войти</Link>
          </Button>
        </div>
      </main>
    );
  }

  if (!isAdmin) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-background p-4">
        <div
          data-testid="admin-forbidden"
          className="flex max-w-sm flex-col items-center gap-3 rounded-2xl border border-border bg-card p-6 text-center shadow-card"
        >
          <ShieldCheck
            className="size-8 text-muted-foreground"
            aria-hidden="true"
          />
          <h1 className="text-title font-semibold">
            Нужны права администратора
          </h1>
          <p className="text-meta text-muted-foreground">
            Вы вошли как {user?.email}, но у этого аккаунта роль «пользователь».
          </p>
          <Button asChild variant="outline">
            <Link to="/$activeTab" params={{ activeTab: 'directions' }}>
              к карте
            </Link>
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-4xl flex-col gap-4 bg-background p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-title font-semibold">Админка</h1>
        <Button asChild size="sm" variant="ghost">
          <Link to="/$activeTab" params={{ activeTab: 'directions' }}>
            к карте
          </Link>
        </Button>
      </header>

      {stats.data && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <StatCard label="пользователей" value={stats.data.users} />
          <StatCard label="админов" value={stats.data.admins} />
          <StatCard label="мест" value={stats.data.places} />
          <StatCard label="посещений" value={stats.data.visited} />
          <StatCard label="маршрутов" value={stats.data.saved_routes} />
        </div>
      )}

      <div
        role="tablist"
        aria-label="Разделы админки"
        className="flex gap-1 rounded-2xl border border-border bg-card p-1"
      >
        <button
          type="button"
          role="tab"
          id="admin-tab-users"
          aria-selected={tab === 'users'}
          aria-controls="admin-panel"
          data-testid="admin-tab-users"
          onClick={() => setTab('users')}
          className={`flex-1 rounded-xl px-3 py-1.5 text-meta font-medium transition-colors max-md:min-h-10 ${
            tab === 'users'
              ? 'bg-primary text-primary-foreground'
              : 'hover:bg-muted'
          }`}
        >
          Пользователи
        </button>
        <button
          type="button"
          role="tab"
          id="admin-tab-places"
          aria-selected={tab === 'places'}
          aria-controls="admin-panel"
          data-testid="admin-tab-places"
          onClick={() => setTab('places')}
          className={`flex-1 rounded-xl px-3 py-1.5 text-meta font-medium transition-colors max-md:min-h-10 ${
            tab === 'places'
              ? 'bg-primary text-primary-foreground'
              : 'hover:bg-muted'
          }`}
        >
          Места
        </button>
      </div>

      <div
        role="tabpanel"
        id="admin-panel"
        aria-labelledby={`admin-tab-${tab}`}
      >
        {tab === 'users' ? (
          <UsersPanel enabled={isAdmin} currentUserId={user?.id ?? null} />
        ) : (
          <PlacesPanel enabled={isAdmin} />
        )}
      </div>
    </main>
  );
}
