import { useEffect } from 'react';

interface KeyboardShortcuts {
  'cmd-k'?: () => void;
  'cmd-shift-o'?: () => void;
  'cmd-/'?: () => void;
  'cmd-period'?: () => void;
  'cmd-s'?: () => void;
  'cmd-shift-c'?: () => void;
  'cmd-enter'?: () => void;
  'escape'?: () => void;
}

export function useKeyboardShortcuts(shortcuts: KeyboardShortcuts) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
      const isCmd = isMac ? e.metaKey : e.ctrlKey;

      // Cmd+K / Ctrl+K - Search (Claude-aligned)
      if (isCmd && !e.shiftKey && e.key === 'k') {
        e.preventDefault();
        shortcuts['cmd-k']?.();
        return;
      }

      // Cmd+Shift+O / Ctrl+Shift+O - New chat (Claude-aligned)
      if (isCmd && e.shiftKey && (e.key === 'O' || e.key === 'o')) {
        e.preventDefault();
        shortcuts['cmd-shift-o']?.();
        return;
      }

      // Cmd+/ - Show keyboard shortcuts (Claude-aligned)
      if (isCmd && e.key === '/') {
        e.preventDefault();
        shortcuts['cmd-/']?.();
        return;
      }

      // Cmd+. / Ctrl+. - Toggle sidebar (Claude-aligned)
      if (isCmd && e.key === '.') {
        e.preventDefault();
        shortcuts['cmd-period']?.();
        return;
      }

      // Cmd+S - Star current thread (custom)
      if (isCmd && !e.shiftKey && e.key === 's') {
        e.preventDefault();
        shortcuts['cmd-s']?.();
        return;
      }

      // Cmd+Shift+C - Copy last response (custom)
      if (isCmd && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
        e.preventDefault();
        shortcuts['cmd-shift-c']?.();
        return;
      }

      // Cmd+Enter - Send message
      if (isCmd && e.key === 'Enter') {
        e.preventDefault();
        shortcuts['cmd-enter']?.();
        return;
      }

      // Escape - Stop streaming (Claude-aligned). Don't preventDefault so
      // dialogs and other UI still get Esc for their own close handling.
      if (e.key === 'Escape') {
        shortcuts['escape']?.();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [shortcuts]);
}
