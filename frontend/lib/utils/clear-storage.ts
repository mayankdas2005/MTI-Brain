/**
 * Nuclear local-state reset for this origin.
 *
 * Wipes localStorage, sessionStorage, every IndexedDB database (covers
 * Dexie-backed composer drafts), and every Cache Storage entry (covers the
 * service-worker app-shell cache). Effectively "log out + forget everything
 * the browser remembered about MTI Brain on this device."
 *
 * Resolves once the wipe is complete; the caller is expected to reload
 * (or navigate to /) so the app boots into a clean state.
 */
export async function clearAllLocalData(): Promise<void> {
  if (typeof window === 'undefined') return;

  try {
    localStorage.clear();
  } catch {
    // ignore
  }
  try {
    sessionStorage.clear();
  } catch {
    // ignore
  }

  // IndexedDB — list every DB this origin has and delete each one.
  // `databases()` is supported by Chrome/Edge/Firefox; Safari (older) may
  // omit it, in which case we delete the known ones explicitly.
  try {
    if (indexedDB.databases) {
      const dbs = await indexedDB.databases();
      await Promise.all(
        dbs.map(
          (d) =>
            new Promise<void>((resolve) => {
              if (!d.name) return resolve();
              const req = indexedDB.deleteDatabase(d.name);
              req.onsuccess = () => resolve();
              req.onerror = () => resolve();
              req.onblocked = () => resolve();
            }),
        ),
      );
    } else {
      // Fallback: delete known databases by name. Add new ones here as we
      // introduce them.
      await new Promise<void>((resolve) => {
        const req = indexedDB.deleteDatabase('quest-drafts');
        req.onsuccess = () => resolve();
        req.onerror = () => resolve();
        req.onblocked = () => resolve();
      });
    }
  } catch {
    // ignore
  }

  // Cache Storage — service-worker caches (mti-brain-vN, etc).
  try {
    if ('caches' in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch {
    // ignore
  }

  // Unregister any active service worker so the next load registers fresh.
  try {
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    }
  } catch {
    // ignore
  }
}
