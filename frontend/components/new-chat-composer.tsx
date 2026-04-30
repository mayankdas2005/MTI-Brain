'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore } from '@/lib/store/threads';
import { ArrowUp, Loader2 } from 'lucide-react';
import { GHOST_PROMPTS } from '@/lib/suggestions';

interface NewChatComposerProps {
  initialValue?: string;
  /** When true, removes outer padding (used when embedded in center layout). */
  centered?: boolean;
  /** Optional project ID to assign the new thread to. */
  projectId?: string;
}

export function NewChatComposer({ initialValue = '', centered = false, projectId }: NewChatComposerProps) {
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [input, setInput] = useState(initialValue);
  const [submitting, setSubmitting] = useState(false);
  // Start at index 0 so SSR and the first client render agree (no Math.random()
  // in the initializer — that would hydrate-mismatch since the server and the
  // browser pick different indices). Randomize once on mount, then rotate.
  const [ghostIdx, setGhostIdx] = useState(0);
  const [ghostVisible, setGhostVisible] = useState(true);

  const createThread = useThreadStore((s) => s.createThread);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);

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

  // Sync if parent changes initialValue (e.g. suggestion clicked)
  useEffect(() => {
    setInput(initialValue);
    if (initialValue) {
      textareaRef.current?.focus();
    }
  }, [initialValue]);

  // Auto-grow textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 240)}px`;
    }
  }, [input]);

  const handleSubmit = async (text?: string) => {
    const message = (text ?? input).trim();
    if (!message || submitting) return;

    setSubmitting(true);
    try {
      const threadId = await createThread(undefined, projectId);
      setPendingQuestion(message);
      router.push(`/chat/${threadId}`);
    } catch {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const canSend = input.trim().length > 0 && !submitting;

  return (
    <div className={centered ? '' : 'px-4 pb-4 pt-2'}>
      <div className={centered ? 'w-full' : 'max-w-3xl mx-auto'}>
        <div className="relative rounded-2xl border border-border bg-background shadow-lg shadow-black/5 overflow-hidden">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder=""
            autoFocus
            rows={1}
            className="w-full resize-none bg-transparent px-4 pt-4 pb-2 text-sm leading-relaxed focus:outline-none min-h-[52px]"
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

            <button
              type="button"
              onClick={() => handleSubmit()}
              disabled={!canSend}
              className="group/btn flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background shadow-sm transition-spring disabled:opacity-25 hover:bg-foreground/85 hover:shadow-md hover:scale-[1.06] active:scale-[0.85]"
            >
              {submitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ArrowUp className="w-4 h-4 transition-transform duration-150 group-hover/btn:-translate-y-0.5" />
              )}
            </button>
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground/60 mt-2">
          MTI Brain is AI and can make mistakes. Please double-check responses.
        </p>
      </div>
    </div>
  );
}
