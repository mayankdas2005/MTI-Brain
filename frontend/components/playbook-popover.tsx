'use client';

import { type SavedQuery } from '@/lib/store/playbook';
import { BookOpen } from 'lucide-react';

interface PlaybookPopoverProps {
  queries: SavedQuery[];
  activeIndex: number;
  onSelect: (q: SavedQuery) => void;
  onHover: (index: number) => void;
}

export function PlaybookPopover({ queries, activeIndex, onSelect, onHover }: PlaybookPopoverProps) {
  if (queries.length === 0) return null;
  return (
    <div
      role="listbox"
      aria-label="Saved queries"
      className="absolute bottom-full left-0 right-0 mb-2 rounded-xl border border-border bg-popover text-popover-foreground shadow-lg overflow-hidden origin-bottom animate-in fade-in-0 zoom-in-95 duration-100"
    >
      <p className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-widest text-muted-foreground/60 font-medium flex items-center gap-1.5">
        <BookOpen className="w-3 h-3" />
        Playbook
      </p>
      <ul className="py-1 max-h-[40vh] md:max-h-56 overflow-y-auto">
        {queries.map((q, i) => (
          <li key={q.id}>
            <button
              type="button"
              role="option"
              aria-selected={i === activeIndex}
              onMouseEnter={() => onHover(i)}
              onClick={() => onSelect(q)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors ${
                i === activeIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
              }`}
            >
              <BookOpen className="w-4 h-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="font-medium truncate">{q.name}</span>
              <span className="ml-auto text-xs text-muted-foreground/60 truncate hidden sm:inline max-w-[180px]">
                {q.query_text.slice(0, 60)}{q.query_text.length > 60 ? '…' : ''}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
