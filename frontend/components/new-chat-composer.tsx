'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore, setThreadCreationGate } from '@/lib/store/threads';
import { toast } from '@/lib/toast';
import { ArrowUp, Loader2, BrainCircuit } from 'lucide-react';
import { GHOST_PROMPTS } from '@/lib/suggestions';
import { loadDraft, saveDraft, clearDraft } from '@/lib/store/drafts';
import { useActivityStore } from '@/lib/store/activity';
import { track, Events } from '@/lib/analytics';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

const NEW_DRAFT_KEY = '__new__';

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
  // in the initializer - that would hydrate-mismatch since the server and the
  // browser pick different indices). Randomize once on mount, then rotate.
  const [ghostIdx, setGhostIdx] = useState(0);
  const [ghostVisible, setGhostVisible] = useState(true);
  const [deepAnalysis, setDeepAnalysis] = useState(false);

  const createThread = useThreadStore((s) => s.createThread);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);

  // Pick a random starting prompt after hydration so the carousel doesn't
  // always begin on the same one on every page load.
  useEffect(() => {
    setGhostIdx(Math.floor(Math.random() * GHOST_PROMPTS.length));
  }, []);

  // Restore /new draft on mount unless an explicit initialValue was provided
  // (e.g. clicking a suggestion which sets the input directly).
  useEffect(() => {
    if (initialValue) return;
    let cancelled = false;
    void loadDraft(NEW_DRAFT_KEY).then((draft) => {
      if (cancelled) return;
      if (draft) setInput(draft);
    });
    return () => {
      cancelled = true;
    };
  }, [initialValue]);

  // Debounced save while typing.
  useEffect(() => {
    const t = setTimeout(() => {
      void saveDraft(NEW_DRAFT_KEY, input);
    }, 400);
    return () => clearTimeout(t);
  }, [input]);

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

  const handleSubmit = (text?: string) => {
    const message = (text ?? input).trim();
    if (!message || submitting) return;

    setSubmitting(true);
    const threadId = crypto.randomUUID();

    // Gate blocks chat-composer from firing askQuestion until the thread
    // exists on the server. Resolves when creation succeeds; on failure,
    // navigate back to /new and clear the pending state.
    const gate = createThread(undefined, projectId, threadId)
      .then(() => {})
      .catch(() => {
        toast.error('Failed to create chat. Please try again.');
        setPendingQuestion(null);
        setThreadCreationGate(null);
        router.replace('/new');
      });
    setThreadCreationGate(gate);

    setPendingQuestion(message, deepAnalysis);
    void clearDraft(NEW_DRAFT_KEY);
    track(Events.ChatCreated, { thread_id: threadId, project_id: projectId ?? null });
    track(Events.QuestionAsked, {
      thread_id: threadId,
      is_followup: false,
      length: message.length,
      from: 'new',
    });
    useActivityStore.getState().recordQuestion();
    router.push(`/chat/${threadId}`);
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
        <div
          data-onboarding="composer"
          className="relative rounded-2xl border border-border bg-background shadow-lg shadow-black/5 overflow-hidden"
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder=""
            autoFocus
            rows={1}
            aria-label="Start a new conversation"
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
            <div className="flex items-center gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-pressed={deepAnalysis}
                    onClick={() => setDeepAnalysis((v) => !v)}
                    className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                      deepAnalysis
                        ? 'bg-primary/10 text-primary border border-primary/30'
                        : 'text-muted-foreground hover:text-foreground hover:bg-accent border border-transparent'
                    }`}
                  >
                    <BrainCircuit className="w-3.5 h-3.5 shrink-0" />
                    <span className="hidden sm:inline">Deep Analysis</span>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top">
                  {deepAnalysis
                    ? 'Deep Analysis on - extended reasoning, slower response'
                    : 'Deep Analysis - thorough multi-step reasoning for complex questions'}
                </TooltipContent>
              </Tooltip>
            </div>

            <button
              type="button"
              onClick={() => handleSubmit()}
              disabled={!canSend}
              aria-label={submitting ? 'Sending message' : 'Send message'}
              className="group/btn flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background shadow-sm transition-spring disabled:opacity-25 hover:bg-foreground/85 hover:shadow-md hover:scale-[1.06] active:scale-[0.85] outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              {submitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ArrowUp className="w-4 h-4 transition-transform duration-150 group-hover/btn:-translate-y-0.5" />
              )}
            </button>
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground/80 mt-2">
          MTI Brain is AI and can make mistakes. Please double-check responses.
        </p>
      </div>
    </div>
  );
}
