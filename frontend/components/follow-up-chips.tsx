'use client';

import { useThreadStore } from '@/lib/store/threads';

interface FollowUpChipsProps {
  threadId: string;
  followUps: string[];
  conversationId: string;
}

export function FollowUpChips({ threadId, followUps, conversationId }: FollowUpChipsProps) {
  const askQuestion = useThreadStore((s) => s.askQuestion);
  const isStreaming = useThreadStore((s) => s.isStreaming);

  if (!followUps.length) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {followUps.map((q, i) => (
        <button
          key={i}
          disabled={isStreaming}
          onClick={() => askQuestion(threadId, q, conversationId)}
          className="text-xs px-3.5 py-2 rounded-2xl border border-border bg-background hover:bg-accent hover:border-primary/20 transition-all duration-150 text-foreground/80 hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed text-left"
        >
          {q}
        </button>
      ))}
    </div>
  );
}
