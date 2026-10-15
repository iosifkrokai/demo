const BASE_URL_STORAGE_KEY = 'valhalla_base_url';

const DEFAULT_BASE_URL =
  import.meta.env.VITE_VALHALLA_URL ||
  (typeof window !== 'undefined'
    ? window.location.origin
    : 'http://localhost:8002');

export function getBaseUrl(): string {
  if (typeof window === 'undefined') {
    return DEFAULT_BASE_URL;
  }

  const stored = localStorage.getItem(BASE_URL_STORAGE_KEY);

  if (stored) {
    return stored;
  }

  return DEFAULT_BASE_URL;
}
