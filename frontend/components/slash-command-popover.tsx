'use client';

import { motion } from 'framer-motion';
import { type LucideIcon, Eraser, RotateCcw, Copy, Keyboard, Plus } from 'lucide-react';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';

export interface SlashCommand {
  id: string;
  /** What the user types: e.g. "clear" → triggered by `/clear` */
  trigger: string;
  /** Short label shown in the menu */
  label: string;
  /** One-line description */
  description: string;
  icon: LucideIcon;
  /** Whether this command needs a current thread / a last assistant message */
  requires?: 'thread' | 'last-assistant';
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    id: 'clear',
    trigger: 'clear',
    label: '/clear',
    description: 'Clear the composer',
    icon: Eraser,
  },
  {
    id: 'retry',
    trigger: 'retry',
    label: '/retry',
    description: 'Regenerate the last response',
    icon: RotateCcw,
    requires: 'last-assistant',
  },
  {
    id: 'copy',
    trigger: 'copy',
    label: '/copy',
    description: 'Copy the last response',
    icon: Copy,
    requires: 'last-assistant',
  },
  {
    id: 'new',
    trigger: 'new',
    label: '/new',
    description: 'Start a fresh conversation',
    icon: Plus,
  },
  {
    id: 'help',
    trigger: 'help',
    label: '/help',
    description: 'Show keyboard shortcuts',
    icon: Keyboard,
  },
];

/** Returns the matching commands for the current `/foo` input. */
export function matchSlashCommands(input: string): SlashCommand[] {
  if (!input.startsWith('/')) return [];
  // Disable on multiline drafts so power users can quote a code path that
  // happens to start with `/` without triggering the menu.
  if (input.includes('\n')) return [];
  const query = input.slice(1).toLowerCase().trim();
  if (!query) return SLASH_COMMANDS;
  return SLASH_COMMANDS.filter((cmd) => cmd.trigger.startsWith(query));
}

interface SlashCommandPopoverProps {
  commands: SlashCommand[];
  activeIndex: number;
  onSelect: (cmd: SlashCommand) => void;
  onHover: (index: number) => void;
}

export function SlashCommandPopover({
  commands,
  activeIndex,
  onSelect,
  onHover,
}: SlashCommandPopoverProps) {
  const reduced = usePrefersReducedMotion();
  if (commands.length === 0) return null;
  return (
    <motion.div
      role="listbox"
      aria-label="Slash commands"
      initial={reduced ? false : { opacity: 0, y: 4, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.14, ease: 'easeOut' }}
      className="absolute bottom-full left-0 right-0 mb-2 rounded-xl border border-border bg-popover text-popover-foreground shadow-lg overflow-hidden origin-bottom"
    >
      <p className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-widest text-muted-foreground/60 font-medium">
        Slash commands
      </p>
      <ul className="py-1 max-h-64 overflow-y-auto">
        {commands.map((cmd, i) => {
          const Icon = cmd.icon;
          const active = i === activeIndex;
          return (
            <li key={cmd.id}>
              <button
                type="button"
                role="option"
                aria-selected={active}
                onMouseEnter={() => onHover(i)}
                onClick={() => onSelect(cmd)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors ${
                  active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="font-medium">{cmd.label}</span>
                <span className="ml-auto text-xs text-muted-foreground/70 truncate">
                  {cmd.description}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </motion.div>
  );
}
