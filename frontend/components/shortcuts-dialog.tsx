'use client';

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from '@/components/ui/dialog';

const isMac = typeof navigator !== 'undefined' && navigator.platform.toUpperCase().indexOf('MAC') >= 0;
const mod = isMac ? '⌘' : 'Ctrl';

const SHORTCUTS = [
  { keys: `${mod} L`, label: 'New chat' },
  { keys: `${mod} K`, label: 'Search conversations' },
  { keys: `${mod} S`, label: 'Star / unstar thread' },
  { keys: `${mod} ⇧ C`, label: 'Copy last response' },
  { keys: `${mod} ⇧ P`, label: 'Open projects' },
  { keys: `${mod} ⇧ H`, label: 'Chat history' },
  { keys: `${mod} /`, label: 'Show this menu' },
  { keys: 'Enter', label: 'Send message' },
  { keys: 'Shift Enter', label: 'New line' },
  { keys: 'Esc', label: 'Close dialog' },
];

interface ShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShortcutsDialog({ open, onOpenChange }: ShortcutsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm p-0 gap-0 overflow-hidden" aria-describedby={undefined}>
        <DialogTitle className="px-4 pt-4 pb-2 text-sm font-semibold text-foreground">
          Keyboard shortcuts
        </DialogTitle>
        <div className="px-4 pb-4 space-y-1">
          {SHORTCUTS.map((s) => (
            <div key={s.keys} className="flex items-center justify-between py-1.5 text-sm">
              <span className="text-muted-foreground">{s.label}</span>
              <div className="flex items-center gap-1">
                {s.keys.split(' ').map((k) => (
                  <kbd
                    key={k}
                    className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded border border-border bg-muted text-[11px] font-mono text-muted-foreground"
                  >
                    {k}
                  </kbd>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
