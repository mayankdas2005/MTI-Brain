'use client';

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore, getThreadCreationGate, setThreadCreationGate } from '@/lib/store/threads';
import { useUIStore } from '@/lib/store/ui';
import { usePreferencesStore } from '@/lib/store/preferences';
import { ArrowUp, Square, BrainCircuit, AudioLines, Bookmark } from 'lucide-react';
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
import { VoiceInputButton } from './voice-input-button';
import { usePlaybookStore } from '@/lib/store/playbook';
import { PlaybookPopover } from './playbook-popover';

function escHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildEmailHTML(markdown: string): string {
  const lines = markdown.split('\n');
  const htmlLines = lines.map((line) => {
    if (line.startsWith('### ')) return `<h3 style="margin:12px 0 4px;font-size:14px;">${escHtml(line.slice(4))}</h3>`;
    if (line.startsWith('## ')) return `<h2 style="margin:16px 0 6px;font-size:16px;">${escHtml(line.slice(3))}</h2>`;
    if (line.startsWith('# ')) return `<h1 style="margin:0 0 12px;font-size:20px;">${escHtml(line.slice(2))}</h1>`;
    if (line.startsWith('- ') || line.startsWith('* ')) return `<li style="margin:2px 0;">${escHtml(line.slice(2))}</li>`;
    if (line.match(/^\d+\. /)) return `<li style="margin:2px 0;">${escHtml(line.replace(/^\d+\. /, ''))}</li>`;
    if (line.trim() === '') return '<br/>';
    return `<p style="margin:4px 0;">${escHtml(line).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\*(.+?)\*/g, '<em>$1</em>')}</p>`;
  });
  return `<div style="font-family:Arial,sans-serif;font-size:13px;line-height:1.6;color:#1a1a1a;">${htmlLines.join('')}<hr style="margin:16px 0;border:none;border-top:1px solid #e5e5e5;"/><p style="font-size:11px;color:#888;">Shared from MTI Brain</p></div>`;
}

export function copyAsEmail(markdown: string): void {
  const html = buildEmailHTML(markdown);
  const el = document.createElement('div');
  el.innerHTML = html;
  el.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;pointer-events:none;';
  document.body.appendChild(el);
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
  document.execCommand('copy');
  sel?.removeAllRanges();
  document.body.removeChild(el);
}
import { useTTS } from '@/lib/hooks/use-tts';
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
  // Deep Analysis persists in preferences across conversations.
  const deepAnalysis = usePreferencesStore((s) => s.deepAnalysis ?? false);
  const setDeepAnalysis = usePreferencesStore((s) => s.setDeepAnalysis);

  const ttsRate = usePreferencesStore((s) => s.ttsRate ?? 1);
  const ttsVoiceURI = usePreferencesStore((s) => s.ttsVoiceURI ?? '');
  const setResponseTone = usePreferencesStore((s) => s.setResponseTone);
  const setMaxResultRows = usePreferencesStore((s) => s.setMaxResultRows);
  const conversationMode = usePreferencesStore((s) => s.conversationMode ?? false);
  const setConversationMode = usePreferencesStore((s) => s.setConversationMode);
  const { speak: ttsSpeakFn, stop: ttsStop } = useTTS(ttsRate, ttsVoiceURI);

  const prevStreamingRef = useRef(false);

  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const isStopping = useThreadStore((s) => s.isStopping);
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

  // Conversation mode: when streaming ends, auto-read the response aloud,
  // then restart the mic so the user can speak again hands-free.
  useEffect(() => {
    const justFinished = prevStreamingRef.current && !isStreaming;
    prevStreamingRef.current = isStreaming;
    if (!justFinished || !conversationMode) return;
    const last = [...currentMessages].reverse().find((m) => m.role === 'assistant' && m.content);
    if (!last) return;
    ttsSpeakFn(last.content, () => {
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent('mti-brain:start-voice'));
      }, 600);
    });
  }, [isStreaming, conversationMode, currentMessages, ttsSpeakFn]);

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
    const gate = getThreadCreationGate();
    if (gate) {
      setThreadCreationGate(null);
      gate.then(() => {
        askQuestion(currentThreadId, pendingQuestion, undefined, undefined, pendingDeepAnalysis);
      });
    } else {
      askQuestion(currentThreadId, pendingQuestion, undefined, undefined, pendingDeepAnalysis);
    }
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

  // Playbook @ trigger
  const playbookQueries = usePlaybookStore((s) => s.queries);
  const fetchPlaybookQueries = usePlaybookStore((s) => s.fetchQueries);
  const atMatches = useMemo(() => {
    if (!input.startsWith('@')) return [];
    const q = input.slice(1).toLowerCase();
    return q
      ? playbookQueries.filter((pq) => pq.name.toLowerCase().includes(q))
      : playbookQueries;
  }, [input, playbookQueries]);
  const atOpen = atMatches.length > 0 && input.startsWith('@');
  const [atIndex, setAtIndex] = useState(0);
  useEffect(() => { setAtIndex(0); }, [atMatches.length]);
  useEffect(() => { if (currentThreadId) fetchPlaybookQueries(); }, [currentThreadId, fetchPlaybookQueries]);

  // Save-to-playbook dialog
  const createPlaybookQuery = usePlaybookStore((s) => s.createQuery);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveQueryName, setSaveQueryName] = useState('');
  const saveSubmittingRef = useRef(false);

  const handleSavePlaybook = () => {
    if (!saveQueryName.trim() || saveSubmittingRef.current) return;
    saveSubmittingRef.current = true;
    void createPlaybookQuery(saveQueryName.trim(), input.trim())
      .then(() => {
        toast.success(`"${saveQueryName.trim()}" saved to Playbook`);
        setSaveDialogOpen(false);
      })
      .catch(() => toast.error('Failed to save.'))
      .finally(() => { saveSubmittingRef.current = false; });
  };

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
      setInput('');
      if (currentThreadId) void clearDraft(currentThreadId);

      switch (cmd.id) {
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
        case 'speak':
          if (lastAssistant) {
            ttsSpeakFn(lastAssistant.content);
          }
          break;
        case 'share':
          if (lastAssistant) {
            copyAsEmail(lastAssistant.content);
            toast.success('Email content copied - paste into Outlook, Gmail, or Teams.');
          }
          break;
        case 'export':
          window.dispatchEvent(new CustomEvent('mti-brain:export-pdf'));
          break;
        case 'tone-analyst':
          setResponseTone('analyst');
          toast.success('Tone set to Analyst');
          break;
        case 'tone-manager':
          setResponseTone('manager');
          toast.success('Tone set to Manager');
          break;
        case 'tone-director':
          setResponseTone('director');
          toast.success('Tone set to Director');
          break;
        case 'tone-executive':
          setResponseTone('executive');
          toast.success('Tone set to Executive');
          break;
        case 'rows-50':
          setMaxResultRows(50);
          toast.success('Max rows set to 50');
          break;
        case 'rows-100':
          setMaxResultRows(100);
          toast.success('Max rows set to 100');
          break;
        case 'rows-200':
          setMaxResultRows(200);
          toast.success('Max rows set to 200');
          break;
        case 'rows-500':
          setMaxResultRows(500);
          toast.success('Max rows set to 500');
          break;
      }
    },
    [currentMessages, currentThreadId, retryResponse, router, setShortcutsOpen, ttsSpeakFn, setResponseTone, setMaxResultRows],
  );

  const handleSubmit = useCallback(
    async (e?: React.FormEvent, overrideText?: string) => {
      e?.preventDefault();
      // Slash command menu only applies when typing manually, not voice auto-send.
      if (!overrideText && slashOpen) {
        const cmd = slashMatches[slashIndex];
        if (cmd) { runSlashCommand(cmd); return; }
      }
      const question = (overrideText ?? input).trim();
      if (!question || !currentThreadId || isStreaming) return;

      setInput('');
      if (currentThreadId) void clearDraft(currentThreadId);
      const wasDeepAnalysis = deepAnalysis;
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
    // @ menu (Playbook) navigation
    if (atOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setAtIndex((i) => (i + 1) % atMatches.length); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setAtIndex((i) => (i - 1 + atMatches.length) % atMatches.length); return; }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const q = atMatches[atIndex];
        if (q) { setInput(q.query_text); textareaRef.current?.focus(); }
        return;
      }
      if (e.key === 'Escape') { e.preventDefault(); setInput(''); return; }
    }
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
      <div className="max-w-3xl lg:max-w-[900px] mx-auto relative">
        {atOpen && (
          <PlaybookPopover
            queries={atMatches}
            activeIndex={Math.min(atIndex, atMatches.length - 1)}
            onSelect={(q) => { setInput(q.query_text); textareaRef.current?.focus(); }}
            onHover={setAtIndex}
          />
        )}
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
            {/* Left toolbar: Deep Analysis + Voice */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-pressed={deepAnalysis}
                onClick={() => setDeepAnalysis(!deepAnalysis)}
                className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                  deepAnalysis
                    ? 'bg-primary/10 text-primary border border-primary/30'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent border border-transparent'
                }`}
              >
                <BrainCircuit className="w-3.5 h-3.5 shrink-0" />
                <span className="hidden sm:inline">Deep Analysis</span>
              </button>
              {/* VoiceInputButton - hidden, conversation mode drives it via window events */}
              {/* <span className="hidden" aria-hidden>
                <VoiceInputButton
                  onTranscript={(text, isFinal) => {
                    setInput(text);
                    if (isFinal && text.trim()) {
                      void handleSubmit(undefined, text.trim());
                    }
                  }}
                  disabled={isStreaming}
                />
              </span> */}
              {/* Conversation mode button - hidden */}
              {/* <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-pressed={conversationMode}
                    onClick={() => {
                      const next = !conversationMode;
                      setConversationMode(next);
                      if (next) {
                        window.dispatchEvent(new CustomEvent('mti-brain:start-voice'));
                      } else {
                        ttsStop();
                        window.dispatchEvent(new CustomEvent('mti-brain:stop-voice'));
                      }
                    }}
                    className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                      conversationMode
                        ? 'bg-primary/10 text-primary border border-primary/30'
                        : 'text-muted-foreground hover:text-foreground hover:bg-accent border border-transparent'
                    }`}
                  >
                    <AudioLines className={`w-3.5 h-3.5 shrink-0 ${conversationMode ? 'animate-pulse' : ''}`} />
                    <span className="hidden sm:inline">Conversation</span>
                  </button>
                </TooltipTrigger>
                {!conversationMode && (
                  <TooltipContent side="top" align="start">
                    Conversation mode - hands-free
                  </TooltipContent>
                )}
              </Tooltip> */}

              {/* Save to Playbook */}
              {input.trim() && !slashOpen && !atOpen && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => { setSaveQueryName(''); setSaveDialogOpen(true); }}
                      className="flex items-center justify-center h-8 w-8 rounded-xl text-muted-foreground hover:text-foreground hover:bg-accent transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      aria-label="Save to Playbook"
                    >
                      <Bookmark className="w-3.5 h-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" align="start">Save to Playbook</TooltipContent>
                </Tooltip>
              )}
            </div>

            {/* Save-to-Playbook dialog */}
            {saveDialogOpen && (
              <div className="absolute inset-x-0 bottom-full mb-2 z-50">
                <div className="mx-auto max-w-sm rounded-xl border border-border bg-popover shadow-lg p-4 space-y-3">
                  <p className="text-sm font-medium">Save to Playbook</p>
                  <input
                    autoFocus
                    value={saveQueryName}
                    onChange={(e) => setSaveQueryName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); handleSavePlaybook(); }
                      if (e.key === 'Escape') setSaveDialogOpen(false);
                    }}
                    placeholder="Name this query..."
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                  <div className="flex justify-end gap-2">
                    <button onClick={() => setSaveDialogOpen(false)} className="text-xs text-muted-foreground hover:text-foreground px-2 py-1">Cancel</button>
                    <button
                      onClick={handleSavePlaybook}
                      className="rounded-lg bg-primary text-primary-foreground text-xs px-3 py-1.5 hover:bg-primary/90"
                    >
                      Save
                    </button>
                  </div>
                </div>
              </div>
            )}

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
                  disabled={isStopping}
                  aria-label={isStopping ? 'Stopping…' : 'Stop generation'}
                  className={`tap-44 relative flex items-center justify-center h-8 w-8 rounded-xl bg-foreground text-background shadow-sm transition-spring hover:bg-foreground/85 hover:shadow-md hover:scale-[1.06] active:scale-[0.85] ${isStopping ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  {isStopping
                    ? <span className="w-3.5 h-3.5 rounded-full border-2 border-background/60 border-t-background animate-spin" />
                    : <Square className="w-3.5 h-3.5 fill-current" />
                  }
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
