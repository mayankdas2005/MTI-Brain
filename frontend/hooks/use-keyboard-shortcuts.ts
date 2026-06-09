import { useHotkeys } from 'react-hotkeys-hook';
import { useRef } from 'react';

interface KeyboardShortcuts {
  'cmd-k'?: () => void;
  'cmd-shift-o'?: () => void;
  'cmd-shift-p'?: () => void;
  'cmd-shift-h'?: () => void;
  'cmd-/'?: () => void;
  'cmd-comma'?: () => void;
  'cmd-period'?: () => void;
  'cmd-s'?: () => void;
  'cmd-shift-c'?: () => void;
  'cmd-shift-v'?: () => void;
  'cmd-shift-e'?: () => void;
  'cmd-shift-l'?: () => void;
  'cmd-shift-m'?: () => void;
  'cmd-r'?: () => void;
  'cmd-enter'?: () => void;
  'escape'?: () => void;
  /** Plain `?` opens the keyboard cheat sheet. Does NOT fire from form
   *  tags - the user typing "?" in the composer should not pop a dialog. */
  'question-mark'?: () => void;
  /** Plain `/` focuses search/palette. Same form-tag rule as `?`. */
  'slash'?: () => void;
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

  // All modified shortcuts opt into firing from form fields - these are
  // global navigation/action bindings the user expects to work regardless
  // of focus context.
  const opts = { enableOnFormTags: true, preventDefault: true } as const;
  // Single-key shortcuts (?, /) deliberately exclude form fields so they
  // don't fight the user typing literal "?" or "/" in the composer.
  const singleKeyOpts = { enableOnFormTags: false, preventDefault: true } as const;

  useHotkeys('mod+k', () => ref.current['cmd-k']?.(), opts);
  useHotkeys('mod+shift+o', () => ref.current['cmd-shift-o']?.(), opts);
  useHotkeys('mod+shift+p', () => ref.current['cmd-shift-p']?.(), opts);
  useHotkeys('mod+shift+h', () => ref.current['cmd-shift-h']?.(), opts);
  useHotkeys('mod+/', () => ref.current['cmd-/']?.(), opts);
  useHotkeys('mod+comma', () => ref.current['cmd-comma']?.(), opts);
  useHotkeys('mod+period', () => ref.current['cmd-period']?.(), opts);
  useHotkeys('mod+s', () => ref.current['cmd-s']?.(), opts);
  useHotkeys('mod+shift+c', () => ref.current['cmd-shift-c']?.(), opts);
  useHotkeys('mod+shift+v', () => ref.current['cmd-shift-v']?.(), opts);
  useHotkeys('mod+shift+e', () => ref.current['cmd-shift-e']?.(), opts);
  useHotkeys('mod+shift+l', () => ref.current['cmd-shift-l']?.(), opts);
  useHotkeys('mod+shift+m', () => ref.current['cmd-shift-m']?.(), opts);
  useHotkeys('mod+r', () => ref.current['cmd-r']?.(), opts);
  useHotkeys('mod+enter', () => ref.current['cmd-enter']?.(), opts);
  useHotkeys('shift+/', () => ref.current['question-mark']?.(), singleKeyOpts);
  useHotkeys('/', () => ref.current['slash']?.(), singleKeyOpts);
  // Escape intentionally does NOT preventDefault - Radix dialogs rely on
  // Esc to close themselves, and we want that to keep working.
  useHotkeys(
    'escape',
    () => ref.current['escape']?.(),
    { enableOnFormTags: true, preventDefault: false },
  );
}
