import { Link, useSearch } from '@tanstack/react-router';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { describeAccountError, useLogin } from '@/hooks/use-auth';

/**
 * Enter an existing account (spec 005 §3).
 *
 * Failures are shown as the machine code's Russian sentence, never a raw status:
 * «неверная почта или пароль» is what the tourist needs, and the agent
 * deliberately does not say which of the two was wrong.
 */
export function LoginPage() {
  // Where the guard sent us from (`?redirect=…`); default is the map.
  const { redirect: returnTo } = useSearch({ from: '/login' });
  const login = useLogin();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    login.mutate(
      { email, password },
      {
        onSuccess: () =>
          // A full navigation, not a router push: the app re-reads the session on
          // load, so the guard sees the fresh cookie instead of a stale cache.
          window.location.assign(
            returnTo && returnTo.startsWith('/') ? returnTo : '/directions'
          ),
        onError: (err) => setError(describeAccountError(err)),
      }
    );
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-background p-4">
      <form
        onSubmit={submit}
        data-testid="login-form"
        className="flex w-full max-w-sm flex-col gap-3 rounded-2xl border border-border bg-card p-5 shadow-card"
      >
        <h1 className="text-title font-semibold">Вход</h1>
        <p className="text-meta text-muted-foreground">
          Маршруты, предпочтения и посещённые места останутся на сервере.
        </p>

        <label className="flex flex-col gap-1 text-meta font-medium">
          Почта
          <Input
            type="email"
            name="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            data-testid="login-email"
          />
        </label>

        <label className="flex flex-col gap-1 text-meta font-medium">
          Пароль
          <Input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            data-testid="login-password"
          />
        </label>

        {error && (
          <p data-testid="login-error" className="text-meta text-destructive">
            {error}
          </p>
        )}

        <Button
          type="submit"
          disabled={login.isPending}
          data-testid="login-submit"
        >
          {login.isPending ? 'вхожу…' : 'войти'}
        </Button>

        <p className="text-meta text-muted-foreground">
          Нет аккаунта?{' '}
          <Link
            to="/register"
            className="text-primary underline-offset-4 hover:underline"
          >
            зарегистрироваться
          </Link>
        </p>
      </form>
    </main>
  );
}
