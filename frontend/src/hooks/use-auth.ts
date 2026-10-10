/** The signed-in account. */

import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import {
  AccountApiError,
  getAuthState,
  loginAccount,
  logoutAccount,
  registerAccount,
} from '@/api/account';
import type { AccountApiErrorCode, AccountUser, AuthState } from '@/api/types';

export const AUTH_QUERY_KEY = ['auth', 'me'] as const;

/** The session read as a reusable query option, so the router's `beforeLoad` guard and the `useAuth` hook share one cache entry and one request — the gate does not invent a second source of truth about who is signed in. */
export const authQueryOptions = queryOptions<AuthState>({
  queryKey: AUTH_QUERY_KEY,
  queryFn: getAuthState,
  staleTime: 5 * 60 * 1000,
  retry: false,
});

const ANONYMOUS: AuthState = { authenticated: false, user: null };

/** A Russian sentence for a machine code — the UI owns all human text. */
export function describeAccountError(error: unknown): string {
  if (error instanceof AccountApiError) {
    const byCode: Partial<Record<AccountApiErrorCode, string>> = {
      invalid_credentials: 'неверная почта или пароль',
      email_taken: 'такая почта уже занята',
      weak_password: 'пароль слишком короткий — минимум 8 символов',
      invalid_email: 'проверьте адрес почты',
      not_authenticated: 'нужно войти',
      not_admin: 'нужны права администратора',
      storage_unavailable: 'сервер не сохраняет данные — попробуйте позже',
      network_unavailable: 'нет связи с сервером — попробуйте позже',
      last_admin: 'нельзя оставить систему без администратора',
      self_role: 'нельзя менять собственную роль',
      self_delete: 'нельзя удалить самого себя',
      user_not_found: 'пользователь не найден',
      place_not_found: 'место не найдено',
      source_taken: 'такая ссылка-источник уже занята',
      invalid_request: 'нечего сохранять',
    };
    return byCode[error.code] ?? 'не получилось — попробуйте ещё раз';
  }
  return 'не получилось — попробуйте ещё раз';
}

export interface AuthApi {
  user: AccountUser | null;
  authenticated: boolean;
  isAdmin: boolean;
  isLoading: boolean;
  refetch: () => void;
}

export function useAuth(): AuthApi {
  const query = useQuery<AuthState>({
    queryKey: AUTH_QUERY_KEY,
    queryFn: getAuthState,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const state = query.data ?? ANONYMOUS;
  return {
    user: state.user,
    authenticated: state.authenticated,
    isAdmin: state.user?.role === 'admin',
    isLoading: query.isLoading,
    refetch: () => {
      void query.refetch();
    },
  };
}

function useSessionMutation<TInput>(
  fn: (input: TInput) => Promise<AccountUser>
) {
  const client = useQueryClient();
  return useMutation<AccountUser, unknown, TInput>({
    mutationFn: fn,
    onSuccess: (user) => {
      client.setQueryData<AuthState>(AUTH_QUERY_KEY, {
        authenticated: true,
        user,
      });
      void client.invalidateQueries({ queryKey: ['visited'] });
      void client.invalidateQueries({ queryKey: ['admin'] });
    },
  });
}

export const useLogin = () => useSessionMutation(loginAccount);

export const useRegister = () => useSessionMutation(registerAccount);

export function useLogout() {
  const client = useQueryClient();
  return useMutation<void, unknown, void>({
    mutationFn: logoutAccount,
    onSettled: () => {
      client.setQueryData<AuthState>(AUTH_QUERY_KEY, ANONYMOUS);
      client.removeQueries({ queryKey: ['visited'] });
      client.removeQueries({ queryKey: ['admin'] });
    },
  });
}
