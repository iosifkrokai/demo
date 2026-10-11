/** The anonymous client id. */

export const CLIENT_ID_STORAGE_KEY = 'grodno-client-id';

/** A v4 UUID, which is what `crypto.randomUUID()` produces. */
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Whether a value can be sent as `X-Client-Id` without the server rejecting it. */
export const isClientId = (value: unknown): value is string =>
  typeof value === 'string' && UUID_V4.test(value);

const toHex = (bytes: Uint8Array): string =>
  [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');

/** Mint a UUIDv4, preferring the platform's own implementation. */
const newClientId = (): string => {
  const c = globalThis.crypto;

  if (typeof c?.randomUUID === 'function') {
    return c.randomUUID();
  }

  if (typeof c?.getRandomValues === 'function') {
    const bytes = c.getRandomValues(new Uint8Array(16));
    bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
    bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
    const hex = toHex(bytes);
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  const random = () =>
    Math.floor(Math.random() * 0x10000)
      .toString(16)
      .padStart(4, '0');
  return `${random()}${random()}-${random()}-4${random().slice(1)}-a${random().slice(1)}-${random()}${random()}${random()}`;
};

/** The id for this browser, generated on first ask if there is none. */
let sessionClientId: string | null = null;

export const getClientId = (): string => {
  if (typeof localStorage !== 'undefined') {
    try {
      const stored = localStorage.getItem(CLIENT_ID_STORAGE_KEY);
      if (isClientId(stored)) return stored;

      const fresh = newClientId();
      localStorage.setItem(CLIENT_ID_STORAGE_KEY, fresh);
      sessionClientId = fresh;
      return fresh;
    } catch {}
  }

  if (!sessionClientId) sessionClientId = newClientId();
  return sessionClientId;
};

/** The current id without creating one — for reads that must not mint data. */
export const peekClientId = (): string | null => {
  if (typeof localStorage !== 'undefined') {
    try {
      const stored = localStorage.getItem(CLIENT_ID_STORAGE_KEY);
      if (isClientId(stored)) return stored;
    } catch {}
  }
  return sessionClientId;
};

/** Forget the id in this browser. */
export const clearClientId = (): void => {
  sessionClientId = null;
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.removeItem(CLIENT_ID_STORAGE_KEY);
  } catch {}
};
