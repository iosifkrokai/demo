import { Link, useSearch } from '@tanstack/react-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { describeAccountError, useLogin } from '@/hooks/use-auth';
import { appPath } from '@/utils/app-path';

/** Enter an existing account. */
export function LoginPage() {
  const { t } = useTranslation();
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
          window.location.assign(
            appPath(
              returnTo && returnTo.startsWith('/') ? returnTo : '/directions'
            )
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
        <h1 className="text-title font-semibold">{t('auth.login.title')}</h1>
        <p className="text-meta text-muted-foreground">
          {t('auth.login.subtitle')}
        </p>

        <label className="flex flex-col gap-1 text-meta font-medium">
          {t('auth.email')}
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
          {t('auth.password')}
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
          <p
            role="alert"
            data-testid="login-error"
            className="text-meta text-destructive"
          >
            {t(error)}
          </p>
        )}

        <Button
          type="submit"
          disabled={login.isPending}
          data-testid="login-submit"
        >
          {t(login.isPending ? 'auth.login.submitting' : 'auth.login.submit')}
        </Button>

        <p className="text-meta text-muted-foreground">
          {t('auth.login.noAccount')}{' '}
          <Link
            to="/register"
            className="text-primary underline-offset-4 hover:underline"
          >
            {t('auth.login.registerLink')}
          </Link>
        </p>
      </form>
    </main>
  );
}
