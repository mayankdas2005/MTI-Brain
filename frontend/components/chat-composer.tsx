'use client';

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore } from '@/lib/store/threads';
import { useUIStore } from '@/lib/store/ui';
import { ArrowUp, Square, BrainCircuit } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { getLastVisibleAssistantConvId } from '@/lib/utils/conversation-tree';
import { GHOST_PROMPTS } from '@/lib/suggestions';
import { loadDraft, saveDraft, clearDraft } from '@/lib/store/drafts';
import { useActivityStore } from '@/lib/store/activity';
import { track, Events } from '@/lib/analytics';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/utils';
import {
  SlashCommandPopover,
  matchSlashCommands,
  type SlashCommand,
} from './slash-command-popover';

export function ChatComposer() {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [input, setInput] = useState('');
  // Start at index 0 so SSR and the first client render agree (Math.random()
  // in the initializer hydrate-mismatches - server and browser pick different
  // indices). Randomize once after mount in the effect below.
  const [ghostIdx, setGhostIdx] = useState(0);
  const [ghostVisible, setGhostVisible] = useState(true);
  // Seed from pendingDeepAnalysis so the toggle stays ON when the user
  // enabled Deep Analysis on /new and the first message was auto-fired.
  const [deepAnalysis, setDeepAnalysis] = useState(
    () => useThreadStore.getState().pendingDeepAnalysis,
  );

  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const pendingQuestion = useThreadStore((s) => s.pendingQuestion);
  const pendingDeepAnalysis = useThreadStore((s) => s.pendingDeepAnalysis);
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

  // Restore any draft saved for this thread on mount / thread change.
  useEffect(() => {
    if (!currentThreadId) return;
    let cancelled = false;
    void loadDraft(currentThreadId).then((draft) => {
      if (cancelled) return;
      if (draft && !input) setInput(draft);
    });
    return () => {
      cancelled = true;
    };
    // input intentionally omitted: we only restore on thread switch, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentThreadId]);

  // Debounced save while typing. Clears on send (handled in handleSubmit).
  useEffect(() => {
    if (!currentThreadId) return;
    const t = setTimeout(() => {
      void saveDraft(currentThreadId, input);
    }, 400);
    return () => clearTimeout(t);
  }, [currentThreadId, input]);

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

  // Auto-stream pending question (from /new page). The dedup ref prevents a
  // single (thread, question) pair from auto-firing twice if the effect
  // re-runs in the same mount. We reset the ref whenever threadId changes so
  // navigating away and back lets a fresh pending question fire even if it
  // happens to match the one we previously handled.
  const pendingHandled = useRef<string | null>(null);
  useEffect(() => {
    pendingHandled.current = null;
  }, [currentThreadId]);
  useEffect(() => {
    if (!currentThreadId || !pendingQuestion) return;
    const key = `${currentThreadId}:${pendingQuestion}`;
    if (pendingHandled.current === key) return;
    pendingHandled.current = key;
    askQuestion(currentThreadId, pendingQuestion, undefined, undefined, pendingDeepAnalysis);
  }, [currentThreadId, pendingQuestion, pendingDeepAnalysis, askQuestion]);

  const handleStop = () => {
    if (currentThreadId) {
      stopGeneration(currentThreadId);
    }
  };

  // ─── Slash commands ───
  const router = useRouter();
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen);
  const retryResponse = useThreadStore((s) => s.retryResponse);

  const slashMatches = useMemo(() => matchSlashCommands(input), [input]);
  const slashOpen = slashMatches.length > 0 && input.startsWith('/');
  const [slashIndex, setSlashIndex] = useState(0);

  // Reset highlight when the candidate set changes.
  useEffect(() => {
    setSlashIndex(0);
  }, [slashMatches.length]);

  const runSlashCommand = useCallback(
    (cmd: SlashCommand) => {
      track(Events.SlashCommandUsed, { command: cmd.id });
      const lastAssistant = [...currentMessages]
        .reverse()
        .find((m) => m.role === 'assistant' && m.content);
      // Validate prerequisites; if unmet, surface a toast and bail.
      if (cmd.requires === 'last-assistant' && !lastAssistant) {
        toast.warning('No assistant response yet for that command.', {
          id: 'slash-no-assistant',
        });
        setInput('');
        return;
      }
      // Capture the prior input length so /clear can surface visible
      // feedback only when the user actually had a draft worth wiping
      // (saying "Draft cleared" with an empty composer would be noise).
      const hadDraft = input.trim().length > cmd.label.length;
      setInput('');
      if (currentThreadId) void clearDraft(currentThreadId);

      switch (cmd.id) {
        case 'clear':
          toast.success(hadDraft ? 'Draft cleared' : 'Composer cleared', {
            id: 'slash-cleared',
          });
          break;
        case 'retry':
          if (currentThreadId && lastAssistant?.conversation_id) {
            void retryResponse(currentThreadId, lastAssistant.conversation_id);
          }
          break;
        case 'copy':
          if (lastAssistant) {
            void copyText(lastAssistant.content).then((ok) => {
              if (ok) toast.success('Last response copied');
              else toast.error('Copy failed', { id: 'copy-failed' });
            });
          }
          break;
        case 'new':
          router.push('/new');
          break;
        case 'help':
          setShortcutsOpen(true);
          break;
      }
    },
    [currentMessages, currentThreadId, retryResponse, router, setShortcutsOpen],
  );

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      // If a slash command is open, Enter selects the highlighted command
      // instead of sending the literal "/foo" as a chat message.
      if (slashOpen) {
        const cmd = slashMatches[slashIndex];
        if (cmd) {
          runSlashCommand(cmd);
          return;
        }
      }
      if (!input.trim() || !currentThreadId || isStreaming) return;

      const question = input.trim();
      const wasDeepAnalysis = deepAnalysis;
      setInput('');
      void clearDraft(currentThreadId);
      // Silently track active days - gates the install-prompt eligibility.
      useActivityStore.getState().recordQuestion();
      track(Events.QuestionAsked, {
        thread_id: currentThreadId,
        is_followup: !!lastAssistantConvId,
        length: question.length,
        deep_analysis: wasDeepAnalysis,
      });
      await askQuestion(currentThreadId, question, lastAssistantConvId, undefined, wasDeepAnalysis);
    },
    [
      input,
      currentThreadId,
      isStreaming,
      askQuestion,
      lastAssistantConvId,
      deepAnalysis,
      slashOpen,
      slashMatches,
      slashIndex,
      runSlashCommand,
    ],
  );

  // Paste / drag-drop affordance: backend doesn't accept attachments yet,
  // but silently ignoring a paste-image makes the app feel broken.
  // Surface a one-time toast (sonner dedupes via id) so the user knows
  // it's recognized.
  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items ?? []);
    if (items.some((it) => it.kind === 'file' && it.type.startsWith('image/'))) {
      e.preventDefault();
      toast.info('File attachments are coming soon - paste text only for now.', {
        id: 'paste-attachment',
      });
    }
  }, []);
  const handleDrop = useCallback((e: React.DragEvent<HTMLTextAreaElement>) => {
    if (e.dataTransfer?.files?.length) {
      e.preventDefault();
      toast.info('File attachments are coming soon.', { id: 'drop-attachment' });
    }
  }, []);
  const handleDragOver = useCallback((e: React.DragEvent<HTMLTextAreaElement>) => {
    if (e.dataTransfer?.types?.includes('Files')) e.preventDefault();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Slash menu navigation takes priority while it's open.
    if (slashOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSlashIndex((i) => (i + 1) % slashMatches.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSlashIndex((i) => (i - 1 + slashMatches.length) % slashMatches.length);
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        const cmd = slashMatches[slashIndex];
        if (cmd) setInput(`/${cmd.trigger} `);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setInput('');
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const canSend = input.trim().length > 0 && !!currentThreadId && !isStreaming;

  return (
    <div
      className="px-4 pt-2"
      style={{
        paddingBottom: 'max(1rem, env(safe-area-inset-bottom), var(--vv-bottom-inset, 0px))',
      }}
    >
      <div className="max-w-3xl mx-auto relative">
        {slashOpen && (
          <SlashCommandPopover
            commands={slashMatches}
            activeIndex={Math.min(slashIndex, slashMatches.length - 1)}
            onSelect={runSlashCommand}
            onHover={setSlashIndex}
          />
        )}
        <div
          data-onboarding="composer"
          className="relative rounded-2xl border border-border bg-background shadow-lg shadow-black/5 overflow-hidden"
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            placeholder=""
            rows={1}
            aria-label="Message"
            aria-expanded={slashOpen}
            aria-haspopup="listbox"
            className="w-full resize-none bg-transparent px-4 pt-4 pb-2 text-base md:text-sm leading-relaxed focus:outline-none disabled:opacity-50 min-h-[52px]"
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
            {/* Deep Analysis toggle */}
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
                  className="tap-44 relative flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background shadow-sm transition-spring hover:bg-foreground/85 hover:shadow-md hover:scale-[1.06] active:scale-[0.85]"
                >
                  <Square className="w-3.5 h-3.5 fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleSubmit()}
                  disabled={!canSend}
                  className="tap-44 group/btn relative flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background shadow-sm transition-spring disabled:opacity-25 hover:bg-foreground/85 hover:shadow-md hover:scale-[1.06] active:scale-[0.85]"
                >
                  <ArrowUp className="w-4 h-4 transition-transform duration-150 group-hover/btn:-translate-y-0.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground/80 mt-2">
          MTI Brain is AI and can make mistakes. Please double-check responses.
        </p>
      </div>
    </div>
  );
}
