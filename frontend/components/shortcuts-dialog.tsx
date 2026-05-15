'use client';

import {
  ResponsiveDialog,
  ResponsiveDialogContent,
  ResponsiveDialogTitle,
  ResponsiveDialogDescription,
} from '@/components/ui/responsive-dialog';

const isMac = typeof navigator !== 'undefined' && navigator.platform.toUpperCase().indexOf('MAC') >= 0;
const mod = isMac ? '⌘' : 'Ctrl';

/**
 * Each shortcut row is one action with one or more keybindings. When an
 * action has multiple bindings (e.g. Cmd+K AND `/` both open search),
 * the variants are rendered side-by-side separated by "or" - one row,
 * not duplicate rows.
 */
type Shortcut = {
  /** One or more keybinding variants, each as a space-delimited string. */
  variants: string[];
  label: string;
  section: 'general' | 'chat' | 'voice';
};

const SHORTCUTS: Shortcut[] = [
  // ─── General ───
  { variants: [`${mod} K`, '/'], label: 'Search', section: 'general' },
  { variants: [`${mod} ⇧ O`], label: 'New chat', section: 'general' },
  { variants: [`${mod} ⇧ P`], label: 'Open projects', section: 'general' },
  { variants: [`${mod} ⇧ H`], label: 'Chat history', section: 'general' },
  { variants: [`${mod} .`], label: 'Toggle sidebar', section: 'general' },
  { variants: [`${mod} /`, '?'], label: 'Keyboard shortcuts', section: 'general' },

  // ─── In chats ───
  { variants: ['Enter'], label: 'Send message', section: 'chat' },
  { variants: ['⇧ Enter'], label: 'New line in message', section: 'chat' },
  { variants: ['Esc'], label: 'Stop response', section: 'chat' },
  { variants: [`${mod} R`], label: 'Retry last response', section: 'chat' },
  { variants: [`${mod} S`], label: 'Star / unstar thread', section: 'chat' },
  { variants: [`${mod} ⇧ C`], label: 'Copy last response', section: 'chat' },
  // { variants: [`${mod} ⇧ E`], label: 'Export conversation to PDF', section: 'chat' },
  // { variants: [`${mod} ⇧ L`], label: 'Copy share link', section: 'chat' },

  // ─── Voice ─── (hidden)
  // { variants: [`${mod} ⇧ V`], label: 'Toggle voice input', section: 'voice' },
];

interface ShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShortcutsDialog({ open, onOpenChange }: ShortcutsDialogProps) {
  const general = SHORTCUTS.filter((s) => s.section === 'general');
  const chat = SHORTCUTS.filter((s) => s.section === 'chat');
  const voice = SHORTCUTS.filter((s) => s.section === 'voice');

  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange}>
      <ResponsiveDialogContent className="sm:max-w-md p-0 gap-0 overflow-hidden">
        <ResponsiveDialogTitle className="px-5 pt-5 pb-3 text-base font-semibold text-foreground">
          Keyboard shortcuts
        </ResponsiveDialogTitle>
        <ResponsiveDialogDescription className="sr-only">
          All available keyboard shortcuts for MTI Brain
        </ResponsiveDialogDescription>
        <div className="px-5 pb-5 space-y-5 max-h-[70vh] overflow-y-auto">
          <ShortcutSection title="General" shortcuts={general} />
          <ShortcutSection title="In chats" shortcuts={chat} />
          {/* Voice section - hidden */}
          {/* <ShortcutSection title="Voice" shortcuts={voice} /> */}
        </div>
      </ResponsiveDialogContent>
    </ResponsiveDialog>
  );
}

function KeyChord({ chord }: { chord: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {chord.split(' ').map((k, i) => (
        <kbd
          key={`${k}-${i}`}
          className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded border border-border bg-muted text-[11px] font-mono text-muted-foreground"
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}

function ShortcutSection({
  title,
  shortcuts,
}: {
  title: string;
  shortcuts: Shortcut[];
}) {
  return (
    <div>
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70 mb-2">
        {title}
      </h3>
      <div className="space-y-1">
        {shortcuts.map((s) => (
          <div key={s.label} className="flex items-center justify-between py-1.5 text-sm">
            <span className="text-foreground/85">{s.label}</span>
            <div className="flex items-center gap-2">
              {s.variants.map((variant, i) => (
                <span key={variant} className="inline-flex items-center gap-2">
                  {i > 0 && (
                    <span className="text-[11px] text-muted-foreground/60">or</span>
                  )}
                  <KeyChord chord={variant} />
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
