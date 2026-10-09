import { Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { describeAccountError } from '@/hooks/use-auth';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useAdminUsers, useDeleteUser, usePatchUser } from '@/hooks/use-admin';
import type { AdminUser, UserRole } from '@/api/types';

/**
 * The users half of the admin panel (spec 005 §3).
 *
 * The guards live on the server (never demote/delete the last admin, never touch
 * your own role) — this panel only offers the actions and shows the reason code's
 * sentence when one is refused. It does not pre-hide a button to hide the rule.
 */

const fmtDate = (value: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleDateString('ru-RU', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      });
};

interface UsersPanelProps {
  enabled: boolean;
  currentUserId: string | null;
}

export function UsersPanel({ enabled, currentUserId }: UsersPanelProps) {
  const [query, setQuery] = useState('');
  // The input stays instant; only the debounced value reaches the query key, so
  // a burst of typing is one request rather than one per character.
  const debouncedQuery = useDebouncedValue(query);
  const users = useAdminUsers(enabled, debouncedQuery);
  const patchUser = usePatchUser();
  const deleteUser = useDeleteUser();

  const changeRole = (user: AdminUser, role: UserRole) => {
    patchUser.mutate(
      { userId: user.id, patch: { role } },
      { onError: (error) => toast.error(describeAccountError(error)) }
    );
  };

  const remove = (user: AdminUser) => {
    if (!window.confirm(`Удалить аккаунт ${user.email}?`)) return;
    deleteUser.mutate(user.id, {
      onError: (error) => toast.error(describeAccountError(error)),
    });
  };

  return (
    <section className="flex flex-col gap-3" data-testid="admin-users">
      <div className="flex items-center justify-between gap-2">
        <Input
          data-testid="admin-users-search"
          placeholder="поиск по почте или имени"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="max-w-xs"
        />
        <span className="text-meta text-muted-foreground">
          всего: {users.total}
        </span>
      </div>

      {users.isLoading && !users.error && (
        <p className="text-meta text-muted-foreground">загружаю…</p>
      )}

      {users.error && (
        <div
          data-testid="admin-users-error"
          className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-destructive/40 bg-card px-3 py-2"
        >
          <span className="text-meta text-muted-foreground">
            {describeAccountError(users.error)}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="admin-users-retry"
            onClick={() => void users.refetch()}
          >
            повторить
          </Button>
        </div>
      )}

      <ul className="flex flex-col gap-2">
        {users.items.map((user) => {
          const isSelf = user.id === currentUserId;
          return (
            <li
              key={user.id}
              data-testid={`admin-user-${user.id}`}
              className="flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-card p-3 shadow-card"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-body font-semibold">
                  {user.display_name || user.email}
                  {isSelf && (
                    <span className="ml-2 text-meta text-muted-foreground">
                      это вы
                    </span>
                  )}
                </div>
                <div className="truncate text-meta text-muted-foreground">
                  {user.email}
                </div>
                <div className="text-meta text-muted-foreground">
                  посещённых: {user.visited} · маршрутов: {user.saved_routes} ·
                  вход: {fmtDate(user.last_login_at)}
                </div>
              </div>

              <label className="flex items-center gap-2 text-meta">
                роль
                <select
                  data-testid={`admin-user-role-${user.id}`}
                  value={user.role}
                  disabled={patchUser.isPending}
                  onChange={(event) =>
                    changeRole(user, event.target.value as UserRole)
                  }
                  className="h-9 rounded-xl border border-border bg-background px-2 text-meta outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="user">пользователь</option>
                  <option value="admin">админ</option>
                </select>
              </label>

              <Button
                type="button"
                variant="ghost"
                size="sm"
                data-testid={`admin-user-delete-${user.id}`}
                disabled={deleteUser.isPending}
                onClick={() => remove(user)}
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="size-4" aria-hidden="true" />
                удалить
              </Button>
            </li>
          );
        })}
      </ul>

      {!users.isLoading && !users.error && users.items.length === 0 && (
        <p className="text-meta text-muted-foreground">ничего не найдено</p>
      )}
    </section>
  );
}
