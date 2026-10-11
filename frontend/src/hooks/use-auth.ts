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

/** Session query option shared by the router guard and `useAuth`, so they share one cache. */
export const authQueryOptions = queryOptions<AuthState>({
  queryKey: AUTH_QUERY_KEY,
  queryFn: getAuthState,
  staleTime: 5 * 60 * 1000,
  retry: false,
});

const ANONYMOUS: AuthState = { authenticated: false, user: null };

/** A translation key for a machine code — the dictionary owns the human text. */
export function describeAccountError(error: unknown): string {
  if (error instanceof AccountApiError) {
    const byCode: Partial<Record<AccountApiErrorCode, string>> = {
      invalid_credentials: 'account.errors.invalid_credentials',
      email_taken: 'account.errors.email_taken',
      weak_password: 'account.errors.weak_password',
      invalid_email: 'account.errors.invalid_email',
      not_authenticated: 'account.errors.not_authenticated',
      not_admin: 'account.errors.not_admin',
      storage_unavailable: 'account.errors.storage_unavailable',
      network_unavailable: 'account.errors.network_unavailable',
      last_admin: 'account.errors.last_admin',
      self_role: 'account.errors.self_role',
      self_delete: 'account.errors.self_delete',
      user_not_found: 'account.errors.user_not_found',
      place_not_found: 'account.errors.place_not_found',
      source_taken: 'account.errors.source_taken',
      invalid_request: 'account.errors.invalid_request',
    };
    return byCode[error.code] ?? 'account.errors.generic';
  }
  return 'account.errors.generic';
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
