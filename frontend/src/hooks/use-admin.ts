/**
 * The admin panel's data layer (spec 005 §3).
 *
 * Every hook is gated on `enabled`, which callers set to `auth.isAdmin`: a plain
 * user must never fire an `/admin/*` request just to be told 403 — the panel is
 * simply not reachable for them.
 *
 * Writes invalidate the catalogue (`['places']`) as well as the admin lists: an
 * edited place is the same row the map and the «все точки» tab show, so a stale
 * catalogue would print the old blurb.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  adminCreatePlace,
  adminDeletePlace,
  adminDeleteUser,
  adminListPlaces,
  adminListUsers,
  adminPatchUser,
  adminStats,
  adminUpdatePlace,
} from '@/api/account';
import type {
  AdminPlaceInput,
  AdminPlaceList,
  AdminStats,
  AdminUser,
  AdminUserList,
  UserRole,
} from '@/api/types';

export const adminUsersKey = (q: string) => ['admin', 'users', q] as const;
export const adminPlacesKey = (q: string, category: string) =>
  ['admin', 'places', q, category] as const;
export const ADMIN_STATS_KEY = ['admin', 'stats'] as const;

function useAdminInvalidate() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: ['admin'] });
    void client.invalidateQueries({ queryKey: ['places'] });
    void client.invalidateQueries({ queryKey: ['visited'] });
  };
}

export function useAdminUsers(enabled: boolean, q = '') {
  const query = useQuery<AdminUserList>({
    queryKey: adminUsersKey(q),
    queryFn: () => adminListUsers({ q, limit: 100 }),
    enabled,
    retry: false,
  });
  return {
    items: query.data?.items ?? [],
    total: query.data?.total ?? 0,
    isLoading: query.isLoading,
    error: query.error,
  };
}

export function useAdminPlaces(enabled: boolean, q = '', category = '') {
  const query = useQuery<AdminPlaceList>({
    queryKey: adminPlacesKey(q, category),
    queryFn: () => adminListPlaces({ q, category, limit: 100 }),
    enabled,
    retry: false,
  });
  return {
    items: query.data?.items ?? [],
    total: query.data?.total ?? 0,
    isLoading: query.isLoading,
    error: query.error,
  };
}

export function useAdminStats(enabled: boolean) {
  return useQuery<AdminStats>({
    queryKey: ADMIN_STATS_KEY,
    queryFn: adminStats,
    enabled,
    retry: false,
  });
}

export function usePatchUser() {
  const invalidate = useAdminInvalidate();
  return useMutation<
    unknown,
    unknown,
    { userId: string; patch: { role?: UserRole; display_name?: string | null } }
  >({
    mutationFn: ({ userId, patch }) => adminPatchUser(userId, patch),
    onSuccess: invalidate,
  });
}

export function useDeleteUser() {
  const invalidate = useAdminInvalidate();
  return useMutation<unknown, unknown, string>({
    mutationFn: (userId) => adminDeleteUser(userId),
    onSuccess: invalidate,
  });
}

export function useCreatePlace() {
  const invalidate = useAdminInvalidate();
  return useMutation<unknown, unknown, AdminPlaceInput>({
    mutationFn: (input) => adminCreatePlace(input),
    onSuccess: invalidate,
  });
}

export function useUpdatePlace() {
  const invalidate = useAdminInvalidate();
  return useMutation<
    unknown,
    unknown,
    { placeId: number; patch: AdminPlaceInput }
  >({
    mutationFn: ({ placeId, patch }) => adminUpdatePlace(placeId, patch),
    onSuccess: invalidate,
  });
}

export function useDeletePlace() {
  const invalidate = useAdminInvalidate();
  return useMutation<unknown, unknown, number>({
    mutationFn: (placeId) => adminDeletePlace(placeId),
    onSuccess: invalidate,
  });
}

export type { AdminUser };
