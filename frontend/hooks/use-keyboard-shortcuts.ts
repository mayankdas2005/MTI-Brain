import { useHotkeys } from 'react-hotkeys-hook';
import { useRef } from 'react';

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

/**
 * App-wide keyboard shortcuts. Backed by `react-hotkeys-hook` for
 * - platform-aware modifier handling (`mod` = cmd on macOS, ctrl elsewhere)
 * - automatic input/textarea handling (we opt-in via `enableOnFormTags`
 *   so global shortcuts still fire while the user is typing)
 * - conflict detection across nested scopes
 *
 * The handler ref pattern keeps the per-shortcut bindings stable across
 * renders even when the parent passes inline-arrow callbacks.
 */
export function useKeyboardShortcuts(shortcuts: KeyboardShortcuts) {
  const ref = useRef(shortcuts);
  ref.current = shortcuts;

  // All shortcuts opt into firing from form fields — these are global
  // navigation/action bindings the user expects to work regardless of
  // focus context.
  const opts = { enableOnFormTags: true, preventDefault: true } as const;

  useHotkeys('mod+k', () => ref.current['cmd-k']?.(), opts);
  useHotkeys('mod+shift+o', () => ref.current['cmd-shift-o']?.(), opts);
  useHotkeys('mod+/', () => ref.current['cmd-/']?.(), opts);
  useHotkeys('mod+period', () => ref.current['cmd-period']?.(), opts);
  useHotkeys('mod+s', () => ref.current['cmd-s']?.(), opts);
  useHotkeys('mod+shift+c', () => ref.current['cmd-shift-c']?.(), opts);
  useHotkeys('mod+enter', () => ref.current['cmd-enter']?.(), opts);
  // Escape intentionally does NOT preventDefault — Radix dialogs rely on
  // Esc to close themselves, and we want that to keep working.
  useHotkeys(
    'escape',
    () => ref.current['escape']?.(),
    { enableOnFormTags: true, preventDefault: false },
  );
}
