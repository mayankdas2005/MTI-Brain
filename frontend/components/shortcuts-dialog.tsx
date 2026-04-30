'use client';

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from '@/components/ui/dialog';

const isMac = typeof navigator !== 'undefined' && navigator.platform.toUpperCase().indexOf('MAC') >= 0;
const mod = isMac ? '⌘' : 'Ctrl';

// Order: Claude-aligned bindings first (most-used at top), then custom
// shortcuts not present in Claude. Two sections: General and In chats.
const SHORTCUTS: Array<{ keys: string; label: string; section: 'general' | 'chat' }> = [
  // ─── General — Claude-aligned ───
  { keys: `${mod} K`, label: 'Search conversations', section: 'general' },
  { keys: `${mod} ⇧ O`, label: 'New chat', section: 'general' },
  { keys: `${mod} .`, label: 'Toggle sidebar', section: 'general' },
  { keys: `${mod} /`, label: 'Keyboard shortcuts', section: 'general' },
  // ─── General — Custom ───
  { keys: `${mod} ⇧ P`, label: 'Open projects', section: 'general' },
  { keys: `${mod} ⇧ H`, label: 'Chat history', section: 'general' },

  // ─── In chats — Claude-aligned ───
  { keys: 'Enter', label: 'Send message', section: 'chat' },
  { keys: '⇧ Enter', label: 'New line in message', section: 'chat' },
  { keys: 'Esc', label: 'Stop response', section: 'chat' },
  // ─── In chats — Custom ───
  { keys: `${mod} S`, label: 'Star / unstar thread', section: 'chat' },
  { keys: `${mod} ⇧ C`, label: 'Copy last response', section: 'chat' },
];

interface ShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShortcutsDialog({ open, onOpenChange }: ShortcutsDialogProps) {
  const general = SHORTCUTS.filter((s) => s.section === 'general');
  const chat = SHORTCUTS.filter((s) => s.section === 'chat');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md p-0 gap-0 overflow-hidden" aria-describedby={undefined}>
        <DialogTitle className="px-5 pt-5 pb-3 text-base font-semibold text-foreground">
          Keyboard shortcuts
        </DialogTitle>
        <div className="px-5 pb-5 space-y-5">
          <ShortcutSection title="General" shortcuts={general} />
          <ShortcutSection title="In chats" shortcuts={chat} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ShortcutSection({
  title,
  shortcuts,
}: {
  title: string;
  shortcuts: Array<{ keys: string; label: string }>;
}) {
  return (
    <div>
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70 mb-2">
        {title}
      </h3>
      <div className="space-y-1">
        {shortcuts.map((s) => (
          <div key={s.keys + s.label} className="flex items-center justify-between py-1.5 text-sm">
            <span className="text-foreground/85">{s.label}</span>
            <div className="flex items-center gap-1">
              {s.keys.split(' ').map((k, i) => (
                <kbd
                  key={`${k}-${i}`}
                  className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded border border-border bg-muted text-[11px] font-mono text-muted-foreground"
                >
                  {k}
                </kbd>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
