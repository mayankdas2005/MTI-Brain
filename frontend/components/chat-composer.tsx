'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { useThreadStore } from '@/lib/store/threads';
import { ArrowUp, Square } from 'lucide-react';

export function ChatComposer() {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [input, setInput] = useState('');

  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const pendingQuestion = useThreadStore((s) => s.pendingQuestion);
  const askQuestion = useThreadStore((s) => s.askQuestion);
  const stopGeneration = useThreadStore((s) => s.stopGeneration);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);

  // Auto-grow textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 240)}px`;
    }
  }, [input]);

  // Auto-stream pending question (from /new page)
  const pendingHandled = useRef<string | null>(null);
  useEffect(() => {
    if (!currentThreadId || !pendingQuestion) return;
    if (pendingHandled.current === `${currentThreadId}:${pendingQuestion}`) return;

    pendingHandled.current = `${currentThreadId}:${pendingQuestion}`;
    askQuestion(currentThreadId, pendingQuestion);
  }, [currentThreadId, pendingQuestion, askQuestion]);

  const handleStop = () => {
    if (currentThreadId) {
      stopGeneration(currentThreadId);
    }
  };

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      if (!input.trim() || !currentThreadId || isStreaming) return;

      const question = input.trim();
      setInput('');
      await askQuestion(currentThreadId, question);
    },
    [input, currentThreadId, isStreaming, askQuestion],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const canSend = input.trim().length > 0 && !!currentThreadId && !isStreaming;

  return (
    <div className="px-4 pb-4 pt-2">
      <div className="max-w-3xl mx-auto">
        <div className="relative rounded-2xl border border-border bg-background shadow-lg shadow-black/5 overflow-hidden">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about your data..."
            disabled={isStreaming && !input}
            rows={1}
            className="w-full resize-none bg-transparent px-4 pt-4 pb-2 text-sm leading-relaxed placeholder:text-muted-foreground focus:outline-none disabled:opacity-50 min-h-[52px]"
          />

          <div className="flex items-center justify-between px-3 pb-3">
            <div className="flex items-center gap-1" />

            {isStreaming ? (
              <button
                type="button"
                onClick={handleStop}
                className="flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background hover:opacity-90 transition-spring active:scale-[0.85]"
                title="Stop generation"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => handleSubmit()}
                disabled={!canSend}
                className="flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background transition-spring disabled:opacity-25 hover:opacity-90 active:scale-[0.85]"
                title="Send (Enter)"
              >
                <ArrowUp className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground/60 mt-2">
          MTI Brain is AI and can make mistakes. Please double-check responses.
        </p>
      </div>
    </div>
  );
}
