'use client';

import { useEffect, useState } from 'react';

interface ThinkingWordsProps {
  /** Active pipeline node label, e.g. "Generating SQL". */
  label?: string;
}

export function ThinkingWords({ label }: ThinkingWordsProps) {
  const display = (label && label.trim()) || 'Thinking';
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    setVisible(false);
    const id = setTimeout(() => setVisible(true), 60);
    return () => clearTimeout(id);
  }, [display]);

  return (
    <span className="inline-flex items-center gap-2 text-muted-foreground">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
      </span>
      <span
        className="text-sm transition-opacity duration-200 ease-in-out"
        style={{ opacity: visible ? 1 : 0 }}
      >
        {display}
      </span>
      <span className="inline-flex gap-0.5">
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.3s]" />
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.15s]" />
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce" />
      </span>
    </span>
  );
}
