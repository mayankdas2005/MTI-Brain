'use client';

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useThreadStore } from '@/lib/store/threads';
import { ArrowUp, Square } from 'lucide-react';
import { getLastVisibleAssistantConvId } from '@/lib/utils/conversation-tree';
import { GHOST_PROMPTS } from '@/lib/suggestions';

export function ChatComposer() {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [input, setInput] = useState('');
  // Start at index 0 so SSR and the first client render agree (Math.random()
  // in the initializer hydrate-mismatches — server and browser pick different
  // indices). Randomize once after mount in the effect below.
  const [ghostIdx, setGhostIdx] = useState(0);
  const [ghostVisible, setGhostVisible] = useState(true);

  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const pendingQuestion = useThreadStore((s) => s.pendingQuestion);
  const askQuestion = useThreadStore((s) => s.askQuestion);
  const stopGeneration = useThreadStore((s) => s.stopGeneration);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);
  const currentMessages = useThreadStore((s) => s.currentMessages);
  const streamingMessageId = useThreadStore((s) => s.streamingMessageId);
  const activeVersionsForThread = useThreadStore(
    (s) => (currentThreadId ? s.activeVersions[currentThreadId] : undefined),
  );

  // Pipeline progress (0..0.95) for the stop-button ring during streaming.
  // Caps at 0.95 until onDone fires so the ring never claims complete prematurely.
  const pipelineProgress = useMemo(() => {
    if (!isStreaming || !streamingMessageId) return 0;
    const msg = currentMessages.find((m) => m.id === streamingMessageId);
    const steps = msg?.streamingSteps ?? [];
    if (steps.length === 0) return 0;
    const done = steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
    return Math.min(done / Math.max(steps.length, done + 1), 0.95);
  }, [isStreaming, streamingMessageId, currentMessages]);

  // Source for cascading visibility: the conversation_id of the assistant
  // message in the LAST VISIBLE turn's active version. Walks the same turn
  // structure / visibility rules as MessageList so the new question's
  // source_conversation_id matches what the user is actually viewing.
  const lastAssistantConvId = useMemo(
    () => getLastVisibleAssistantConvId(currentMessages, activeVersionsForThread),
    [currentMessages, activeVersionsForThread],
  );

  // Auto-grow textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 240)}px`;
    }
  }, [input]);

  // Pick a random starting prompt after hydration so the carousel doesn't
  // always begin on the same one on every page load.
  useEffect(() => {
    setGhostIdx(Math.floor(Math.random() * GHOST_PROMPTS.length));
  }, []);

  // Rotate ghost-text starters while the textarea is empty.
  useEffect(() => {
    if (input.length > 0) return;
    const id = setInterval(() => {
      setGhostVisible(false);
      setTimeout(() => {
        setGhostIdx((i) => (i + 1) % GHOST_PROMPTS.length);
        setGhostVisible(true);
      }, 220);
    }, 4000);
    return () => clearInterval(id);
  }, [input.length]);

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
      await askQuestion(currentThreadId, question, lastAssistantConvId);
    },
    [input, currentThreadId, isStreaming, askQuestion, lastAssistantConvId],
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
            placeholder=""
            disabled={isStreaming && !input}
            rows={1}
            className="w-full resize-none bg-transparent px-4 pt-4 pb-2 text-sm leading-relaxed focus:outline-none disabled:opacity-50 min-h-[52px]"
          />
          {input.length === 0 && (
            <span
              aria-hidden
              className="pointer-events-none absolute left-4 top-4 text-sm leading-relaxed text-muted-foreground transition-opacity duration-200 ease-in-out"
              style={{ opacity: ghostVisible ? 1 : 0 }}
            >
              {GHOST_PROMPTS[ghostIdx]}
            </span>
          )}

          <div className="flex items-center justify-between px-3 pb-3">
            <div className="flex items-center gap-1" />

            <div className="relative h-9 w-9 flex items-center justify-center">
              {isStreaming && (
                <svg
                  aria-hidden
                  viewBox="0 0 36 36"
                  className="absolute inset-0 -rotate-90 pointer-events-none"
                >
                  <circle
                    cx="18"
                    cy="18"
                    r="17"
                    fill="none"
                    strokeWidth="2"
                    className="stroke-foreground/10"
                  />
                  <circle
                    cx="18"
                    cy="18"
                    r="17"
                    fill="none"
                    strokeWidth="2"
                    strokeLinecap="round"
                    className="stroke-primary transition-[stroke-dashoffset] duration-500 ease-out"
                    strokeDasharray={2 * Math.PI * 17}
                    strokeDashoffset={2 * Math.PI * 17 * (1 - pipelineProgress)}
                  />
                </svg>
              )}
              {isStreaming ? (
                <button
                  type="button"
                  onClick={handleStop}
                  className="relative flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background shadow-sm transition-spring hover:bg-foreground/85 hover:shadow-md hover:scale-[1.06] active:scale-[0.85]"
                >
                  <Square className="w-3.5 h-3.5 fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleSubmit()}
                  disabled={!canSend}
                  className="group/btn relative flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background shadow-sm transition-spring disabled:opacity-25 hover:bg-foreground/85 hover:shadow-md hover:scale-[1.06] active:scale-[0.85]"
                >
                  <ArrowUp className="w-4 h-4 transition-transform duration-150 group-hover/btn:-translate-y-0.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground/60 mt-2">
          MTI Brain is AI and can make mistakes. Please double-check responses.
        </p>
      </div>
    </div>
  );
}
