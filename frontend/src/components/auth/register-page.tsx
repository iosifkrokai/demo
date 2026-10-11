import { Link, useSearch } from '@tanstack/react-router';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { describeAccountError, useRegister } from '@/hooks/use-auth';
import { appPath } from '@/utils/app-path';

/** Create an account. */
export function RegisterPage() {
  const { redirect: returnTo } = useSearch({ from: '/register' });
  const register = useRegister();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    register.mutate(
      {
        email,
        password,
        display_name: displayName.trim() || null,
      },
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
        data-testid="register-form"
        className="flex w-full max-w-sm flex-col gap-3 rounded-2xl border border-border bg-card p-5 shadow-card"
      >
        <h1 className="text-title font-semibold">Регистрация</h1>
        <p className="text-meta text-muted-foreground">
          Сохранённые маршруты и посещённые места будут доступны с любого
          устройства.
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
            data-testid="register-email"
          />
        </label>

        <label className="flex flex-col gap-1 text-meta font-medium">
          Как вас зовут{' '}
          <span className="font-normal text-muted-foreground">
            (необязательно)
          </span>
          <Input
            name="display_name"
            autoComplete="nickname"
            maxLength={120}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            data-testid="register-name"
          />
        </label>

        <label className="flex flex-col gap-1 text-meta font-medium">
          Пароль{' '}
          <span className="font-normal text-muted-foreground">
            (минимум 8 символов)
          </span>
          <Input
            type="password"
            name="password"
            autoComplete="new-password"
            minLength={8}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            data-testid="register-password"
          />
        </label>

        {error && (
          <p
            role="alert"
            data-testid="register-error"
            className="text-meta text-destructive"
          >
            {error}
          </p>
        )}

        <Button
          type="submit"
          disabled={register.isPending}
          data-testid="register-submit"
        >
          {register.isPending ? 'создаю…' : 'создать аккаунт'}
        </Button>

        <p className="text-meta text-muted-foreground">
          Уже есть аккаунт?{' '}
          <Link
            to="/login"
            className="text-primary underline-offset-4 hover:underline"
          >
            войти
          </Link>
        </p>
      </form>
    </main>
  );
}
