import { useEffect } from 'react';

interface KeyboardShortcuts {
  'cmd-k'?: () => void;
  'cmd-n'?: () => void;
  'cmd-/'?: () => void;
  'cmd-s'?: () => void;
  'cmd-shift-c'?: () => void;
  'cmd-enter'?: () => void;
  'cmd-l'?: () => void;
  'escape'?: () => void;
}

export function useKeyboardShortcuts(shortcuts: KeyboardShortcuts) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
      const isCmd = isMac ? e.metaKey : e.ctrlKey;

      // Cmd+K / Ctrl+K - New chat
      if (isCmd && e.key === 'k') {
        e.preventDefault();
        shortcuts['cmd-k']?.();
      }

      // Cmd+N / Ctrl+N - New chat
      if (isCmd && e.key === 'n') {
        e.preventDefault();
        shortcuts['cmd-n']?.();
      }

      // Cmd+/ — Show keyboard shortcuts
      if (isCmd && e.key === '/') {
        e.preventDefault();
        shortcuts['cmd-/']?.();
      }

      // Cmd+S — Star current thread
      if (isCmd && e.key === 's') {
        e.preventDefault();
        shortcuts['cmd-s']?.();
      }

      // Cmd+Shift+C — Copy last response
      if (isCmd && e.shiftKey && e.key === 'C') {
        e.preventDefault();
        shortcuts['cmd-shift-c']?.();
      }

      // Cmd+Enter / Ctrl+Enter - Send message
      if (isCmd && e.key === 'Enter') {
        e.preventDefault();
        shortcuts['cmd-enter']?.();
      }

      // Cmd+L / Ctrl+L - Focus input
      if (isCmd && e.key === 'l') {
        e.preventDefault();
        shortcuts['cmd-l']?.();
      }

      // Escape - Close modals / clear
      if (e.key === 'Escape') {
        e.preventDefault();
        shortcuts['escape']?.();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [shortcuts]);
}
