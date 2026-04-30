'use client';

import { useThreadStore } from '@/lib/store/threads';
import { getLastVisibleAssistantConvId } from '@/lib/utils/conversation-tree';

interface FollowUpChipsProps {
  threadId: string;
  followUps: string[];
  conversationId: string;
}

export function FollowUpChips({ threadId, followUps, conversationId }: FollowUpChipsProps) {
  const askQuestion = useThreadStore((s) => s.askQuestion);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const currentMessages = useThreadStore((s) => s.currentMessages);
  const activeVersionsForThread = useThreadStore((s) => s.activeVersions[threadId]);

  if (!followUps.length) return null;

  const handleClick = (q: string) => {
    const linearPrior = getLastVisibleAssistantConvId(currentMessages, activeVersionsForThread);
    askQuestion(threadId, q, linearPrior ?? conversationId);
  };

  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {followUps.map((q, i) => (
        <button
          key={i}
          disabled={isStreaming}
          onClick={() => handleClick(q)}
          className="text-xs px-3.5 py-2 rounded-2xl border border-border bg-background hover:bg-accent hover:border-primary/20 transition-all duration-150 text-foreground/80 hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed text-left"
        >
          {q}
        </button>
      ))}
    </div>
  );
}
