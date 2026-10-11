import { Link } from '@tanstack/react-router';
import { Loader2, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { describeAccountError, useAuth } from '@/hooks/use-auth';
import { useAdminStats } from '@/hooks/use-admin';

import { PlacesPanel } from './places-panel';
import { UsersPanel } from './users-panel';

/** The admin section: users and places, over one dashboard header. */

type Tab = 'users' | 'places';

const StatCard = ({ label, value }: { label: string; value: number }) => (
  <div className="flex flex-col rounded-2xl border border-border bg-card px-3 py-2 shadow-card">
    <span className="text-title font-semibold">{value}</span>
    <span className="text-meta text-muted-foreground">{label}</span>
  </div>
);

export function AdminPage() {
  const { t } = useTranslation();
  const { authenticated, isAdmin, user, isLoading } = useAuth();
  const stats = useAdminStats(isAdmin);
  const [tab, setTab] = useState<Tab>('users');

  if (isLoading) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-background p-4">
        <div
          data-testid="admin-loading"
          className="flex items-center gap-2 text-meta text-muted-foreground"
        >
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          {t('admin.loading')}
        </div>
      </main>
    );
  }

  if (!authenticated) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-background p-4">
        <div className="flex max-w-sm flex-col items-center gap-3 rounded-2xl border border-border bg-card p-6 text-center shadow-card">
          <ShieldCheck
            className="size-8 text-muted-foreground"
            aria-hidden="true"
          />
          <h1 className="text-title font-semibold">{t('admin.title')}</h1>
          <p className="text-meta text-muted-foreground">
            {t('admin.guestHint')}
          </p>
          <Button asChild>
            <Link to="/login">{t('account.signIn')}</Link>
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
            {t('admin.forbiddenTitle')}
          </h1>
          <p className="text-meta text-muted-foreground">
            {t('admin.forbiddenBody', { email: user?.email })}
          </p>
          <Button asChild variant="outline">
            <Link to="/$activeTab" params={{ activeTab: 'directions' }}>
              {t('admin.backToMap')}
            </Link>
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-4xl flex-col gap-4 bg-background p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-title font-semibold">{t('admin.title')}</h1>
        <Button asChild size="sm" variant="ghost">
          <Link to="/$activeTab" params={{ activeTab: 'directions' }}>
            {t('admin.backToMap')}
          </Link>
        </Button>
      </header>

      {stats.data && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatCard label={t('admin.stats.users')} value={stats.data.users} />
          <StatCard label={t('admin.stats.admins')} value={stats.data.admins} />
          <StatCard label={t('admin.stats.places')} value={stats.data.places} />
          <StatCard
            label={t('admin.stats.visited')}
            value={stats.data.visited}
          />
        </div>
      )}

      {stats.error && (
        <div
          data-testid="admin-stats-error"
          className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-destructive/40 bg-card px-3 py-2"
        >
          <span className="text-meta text-muted-foreground">
            {t(describeAccountError(stats.error))}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="admin-stats-retry"
            onClick={() => void stats.refetch()}
          >
            {t('admin.retry')}
          </Button>
        </div>
      )}

      <div
        role="tablist"
        aria-label={t('admin.tabsLabel')}
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
          {t('admin.tabs.users')}
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
          {t('admin.tabs.places')}
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
