/**
 * Service worker registration utilities.
 *
 * Public API:
 * - register()         — registers sw.js (no-op in dev or if unsupported)
 * - applyPendingUpdate — activates a waiting SW and reloads the page
 *
 * Registration is also handled inline in index.html (no-JS fallback path).
 * This module exists to support the update UX (prompting the user to reload).
 *
 * Usage (in src/index.tsx):
 *   import { register } from './lib/service-worker-registry';
 *   register();
 *
 * Dev mode: register() returns early — no SW in dev, no HMR interference.
 *
 * Update flow (production):
 * 1. New sw.js (new CACHE_NAME) installs → old SW keeps serving the tourist
 * 2. 'updatefound' fires on the registration → 'sw-update-available' on window
 * 3. App shows "Обновить" prompt
 * 4. User taps it → applyPendingUpdate() → SKIP_WAITING → page reloads
 */

export interface SWRegistrationResult {
  registered: boolean;
  alreadyRegistered: boolean;
}

/**
 * Registers sw.js (found at the app root scope).
 * Safe to call on every app init — register() is idempotent per URL/scope.
 *
 * Dev mode: skips entirely. SW lives in public/ and is processed by
 * sw-vite-plugin only during production builds.
 */
export async function register(): Promise<SWRegistrationResult> {
  if (import.meta.env.DEV) {
    return { registered: false, alreadyRegistered: false };
  }

  if (!('serviceWorker' in navigator)) {
    return { registered: false, alreadyRegistered: false };
  }

  try {
    const scope = import.meta.env.BASE_URL;
    const swUrl = `${scope}sw.js`;

    const reg = await navigator.serviceWorker.register(swUrl, { scope });

    // New SW installed but not yet activated → will activate on next visit
    reg.addEventListener('updatefound', () => {
      const worker = reg.installing;
      if (!worker) return;

      worker.addEventListener('statechange', () => {
        if (
          worker.state === 'installed' &&
          navigator.serviceWorker.controller
        ) {
          window.dispatchEvent(
            new CustomEvent('sw-update-available', {
              detail: { waitingWorker: worker },
            })
          );
        }
      });
    });

    await reg.update(); // check for updates immediately
    return { registered: true, alreadyRegistered: false };
  } catch (err) {
    console.warn('[SW] Registration failed:', err);
    return { registered: false, alreadyRegistered: false };
  }
}

/**
 * Sends SKIP_WAITING to the waiting SW, then reloads the page.
 * Call this when the user confirms "apply update" in the UI.
 */
export async function applyPendingUpdate(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration();
  const worker = reg?.waiting;

  if (!worker) return;

  worker.postMessage({ type: 'SKIP_WAITING' });

  await new Promise<void>((resolve) => {
    navigator.serviceWorker.addEventListener(
      'controllerchange',
      () => resolve(),
      {
        once: true,
      }
    );
  });

  window.location.reload();
}
