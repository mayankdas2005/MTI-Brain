'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore } from '@/lib/store/threads';
import { ArrowUp, Loader2 } from 'lucide-react';

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

  const createThread = useThreadStore((s) => s.createThread);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);

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
            placeholder="Ask a question about your data..."
            autoFocus
            rows={1}
            className="w-full resize-none bg-transparent px-4 pt-4 pb-2 text-sm leading-relaxed placeholder:text-muted-foreground focus:outline-none min-h-[52px]"
          />

          <div className="flex items-center justify-between px-3 pb-3">
            <div className="flex items-center gap-1" />

            <button
              type="button"
              onClick={() => handleSubmit()}
              disabled={!canSend}
              className="flex items-center justify-center h-8 w-8 rounded-lg bg-foreground text-background transition-opacity disabled:opacity-25 hover:opacity-80"
              title="Send (Enter)"
            >
              {submitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ArrowUp className="w-4 h-4" />
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
