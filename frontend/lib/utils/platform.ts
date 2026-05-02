/**
 * Tiny helpers for platform-aware UI affordances.
 * `isMac()` is SSR-safe (returns false on the server) so it can be called
 * during render without guards. The result is stable per-session, so it's
 * safe to compute lazily.
 */
export function isMac(): boolean {
  if (typeof navigator === 'undefined') return false;
  // userAgentData is the modern API; navigator.platform is the wide fallback.
  // Casting because TS doesn't yet ship userAgentData in lib.dom.
  const uaData = (navigator as unknown as { userAgentData?: { platform?: string } })
    .userAgentData;
  if (uaData?.platform) return /mac/i.test(uaData.platform);
  return /mac/i.test(navigator.platform || navigator.userAgent || '');
}

/** Returns "⌘" on macOS, "Ctrl" elsewhere. */
export function modifierLabel(): string {
  return isMac() ? '⌘' : 'Ctrl';
}
