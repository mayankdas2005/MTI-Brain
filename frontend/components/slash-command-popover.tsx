'use client';

import { useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { type LucideIcon, RotateCcw, Copy, Keyboard, Plus, BarChart3, Table2, Sun, Moon } from 'lucide-react';
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
    id: 'light',
    trigger: 'light',
    label: '/light',
    description: 'Switch to light theme',
    icon: Sun,
  },
  {
    id: 'dark',
    trigger: 'dark',
    label: '/dark',
    description: 'Switch to dark theme',
    icon: Moon,
  },
  {
    id: 'help',
    trigger: 'help',
    label: '/help',
    description: 'Show keyboard shortcuts',
    icon: Keyboard,
  },
  {
    id: 'tone-analyst',
    trigger: 'tone analyst',
    label: '/tone analyst',
    description: 'Technical, data-first tone',
    icon: BarChart3,
  },
  {
    id: 'tone-manager',
    trigger: 'tone manager',
    label: '/tone manager',
    description: 'Clear, operational tone',
    icon: BarChart3,
  },
  {
    id: 'tone-director',
    trigger: 'tone director',
    label: '/tone director',
    description: 'Strategic, concise tone',
    icon: BarChart3,
  },
  {
    id: 'tone-executive',
    trigger: 'tone executive',
    label: '/tone executive',
    description: 'High-level, board-ready tone',
    icon: BarChart3,
  },
  {
    id: 'rows-50',
    trigger: 'rows 50',
    label: '/rows 50',
    description: 'Return max 50 rows',
    icon: Table2,
  },
  {
    id: 'rows-100',
    trigger: 'rows 100',
    label: '/rows 100',
    description: 'Return max 100 rows',
    icon: Table2,
  },
  {
    id: 'rows-200',
    trigger: 'rows 200',
    label: '/rows 200',
    description: 'Return max 200 rows',
    icon: Table2,
  },
  {
    id: 'rows-500',
    trigger: 'rows 500',
    label: '/rows 500',
    description: 'Return max 500 rows',
    icon: Table2,
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
  const listRef = useRef<HTMLUListElement>(null);
  const activeItemRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    activeItemRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

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
      <ul ref={listRef} className="py-1 max-h-[40vh] md:max-h-64 overflow-y-auto">
        {commands.map((cmd, i) => {
          const Icon = cmd.icon;
          const active = i === activeIndex;
          return (
            <li key={cmd.id}>
              <button
                ref={active ? activeItemRef : undefined}
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
                <span className="ml-auto text-xs text-muted-foreground/70 truncate hidden sm:inline">
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
