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
      <span
        className="text-sm transition-opacity duration-200 ease-in-out"
        style={{ opacity: visible ? 1 : 0 }}
      >
        {display}
      </span>
      <span className="inline-flex items-center gap-[3px]">
        <span className="w-[5px] h-[5px] rounded-full bg-foreground/35 [animation:thinking-dot_1.4s_ease-in-out_0s_infinite]" />
        <span className="w-[5px] h-[5px] rounded-full bg-foreground/35 [animation:thinking-dot_1.4s_ease-in-out_0.2s_infinite]" />
        <span className="w-[5px] h-[5px] rounded-full bg-foreground/35 [animation:thinking-dot_1.4s_ease-in-out_0.4s_infinite]" />
      </span>
    </span>
  );
}
