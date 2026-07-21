import { describe, it, expect, vi } from 'vitest';

describe('platform utils', () => {
  describe('isMac', () => {
    it('returns false in non-browser environment', async () => {
      const origNav = globalThis.navigator;
      Object.defineProperty(globalThis, 'navigator', { value: undefined, writable: true, configurable: true });

      vi.resetModules();
      const { isMac } = await import('@/lib/utils/platform');
      expect(isMac()).toBe(false);

      Object.defineProperty(globalThis, 'navigator', { value: origNav, writable: true, configurable: true });
    });

    it('detects macOS from platform string', async () => {
      vi.resetModules();
      Object.defineProperty(globalThis, 'navigator', {
        value: { platform: 'MacIntel', userAgent: '' },
        writable: true,
      });

      const { isMac } = await import('@/lib/utils/platform');
      expect(isMac()).toBe(true);
    });

    it('detects non-macOS from platform string', async () => {
      vi.resetModules();
      Object.defineProperty(globalThis, 'navigator', {
        value: { platform: 'Win32', userAgent: '' },
        writable: true,
      });

      const { isMac } = await import('@/lib/utils/platform');
      expect(isMac()).toBe(false);
    });

    it('detects macOS from userAgentData', async () => {
      vi.resetModules();
      Object.defineProperty(globalThis, 'navigator', {
        value: { userAgentData: { platform: 'macOS' }, platform: '', userAgent: '' },
        writable: true,
      });

      const { isMac } = await import('@/lib/utils/platform');
      expect(isMac()).toBe(true);
    });
  });

  describe('modifierLabel', () => {
    it('returns command symbol on macOS', async () => {
      vi.resetModules();
      Object.defineProperty(globalThis, 'navigator', {
        value: { platform: 'MacIntel', userAgent: '' },
        writable: true,
      });

      const { modifierLabel } = await import('@/lib/utils/platform');
      expect(modifierLabel()).toBe('\u2318'); // Command key
    });

    it('returns Ctrl on non-macOS', async () => {
      vi.resetModules();
      Object.defineProperty(globalThis, 'navigator', {
        value: { platform: 'Linux x86_64', userAgent: '' },
        writable: true,
      });

      const { modifierLabel } = await import('@/lib/utils/platform');
      expect(modifierLabel()).toBe('Ctrl');
    });
  });
});
