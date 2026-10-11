/** Service worker registration utilities. */

export interface SWRegistrationResult {
  registered: boolean;
  alreadyRegistered: boolean;
}

/** Registers sw.js (found at the app root scope). */
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

    await reg.update();
    return { registered: true, alreadyRegistered: false };
  } catch (err) {
    console.warn('[SW] Registration failed:', err);
    return { registered: false, alreadyRegistered: false };
  }
}

/** Sends SKIP_WAITING to the waiting SW, then reloads the page. */
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
