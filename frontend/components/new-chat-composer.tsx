'use client';

import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useTheme } from 'next-themes';
import { useThreadStore, setThreadCreationGate } from '@/lib/store/threads';
import { toast } from '@/lib/toast';
import { ArrowUp, Loader2, BrainCircuit, BookOpen, Bookmark } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import { usePreferencesStore } from '@/lib/store/preferences';
import { GHOST_PROMPTS } from '@/lib/suggestions';
import { loadDraft, saveDraft, clearDraft } from '@/lib/store/drafts';
import { useActivityStore } from '@/lib/store/activity';
import { useUIStore } from '@/lib/store/ui';
import { track, Events } from '@/lib/analytics';
import {
  SlashCommandPopover,
  matchSlashCommands,
  type SlashCommand,
} from './slash-command-popover';
import { usePlaybookStore } from '@/lib/store/playbook';
import { PlaybookPopover } from './playbook-popover';

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
  const { setTheme } = useTheme();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [input, setInput] = useState(initialValue);
  const [submitting, setSubmitting] = useState(false);
  const [ghostIdx, setGhostIdx] = useState(() => Math.floor(Math.random() * GHOST_PROMPTS.length));
  const [ghostVisible, setGhostVisible] = useState(true);

  const responseTone = usePreferencesStore((s) => s.responseTone);
  const setResponseTone = usePreferencesStore((s) => s.setResponseTone);
  const setMaxResultRows = usePreferencesStore((s) => s.setMaxResultRows);
  const ghostPrompts = GHOST_PROMPTS;
  const deepAnalysis = usePreferencesStore((s) => s.deepAnalysis ?? false);
  const setDeepAnalysis = usePreferencesStore((s) => s.setDeepAnalysis);

  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen);

  const createThread = useThreadStore((s) => s.createThread);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);

  // ─── Slash commands (filter out thread/last-assistant commands — no thread on /new) ───
  const slashMatches = useMemo(
    () => matchSlashCommands(input).filter((c) => !c.requires && c.id !== 'new'),
    [input],
  );
  const slashOpen = slashMatches.length > 0 && input.startsWith('/');
  const [slashIndex, setSlashIndex] = useState(0);
  useEffect(() => { setSlashIndex(0); }, [slashMatches.length]);

  // ─── Playbook @ trigger ───
  const playbookQueries = usePlaybookStore((s) => s.queries);
  const fetchPlaybookQueries = usePlaybookStore((s) => s.fetchQueries);
  const createPlaybookQuery = usePlaybookStore((s) => s.createQuery);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveQueryName, setSaveQueryName] = useState('');
  const saveSubmittingRef = useRef(false);

  const handleSavePlaybook = () => {
    if (!saveQueryName.trim() || saveSubmittingRef.current) return;
    const name = saveQueryName.trim();
    const queryText = input.trim();
    saveSubmittingRef.current = true;
    setSaveDialogOpen(false);
    void createPlaybookQuery(name, queryText)
      .then(() => { toast.success(`"${name}" saved — type @ to use it`); })
      .catch(() => toast.error('Failed to save.'))
      .finally(() => { saveSubmittingRef.current = false; });
  };
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
  useEffect(() => { void fetchPlaybookQueries(); }, [fetchPlaybookQueries]);

  const runSlashCommand = useCallback(
    (cmd: SlashCommand) => {
      // On /new we skip commands that require a thread or last-assistant response.
      if (cmd.requires) return;
      track(Events.SlashCommandUsed, { command: cmd.id });
      setInput('');
      void clearDraft(NEW_DRAFT_KEY);
      switch (cmd.id) {
        case 'new':
          break; // already on /new
        case 'help':
          setShortcutsOpen(true);
          break;
        case 'light':
          setTheme('light');
          break;
        case 'dark':
          setTheme('dark');
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
    [setTheme, setShortcutsOpen, setResponseTone, setMaxResultRows],
  );

  // Restore /new draft on mount
  useEffect(() => {
    if (initialValue) return;
    let cancelled = false;
    void loadDraft(NEW_DRAFT_KEY).then((draft) => {
      if (cancelled) return;
      if (draft) setInput(draft);
    });
    return () => { cancelled = true; };
  }, [initialValue]);

  // Debounced draft save
  useEffect(() => {
    const t = setTimeout(() => { void saveDraft(NEW_DRAFT_KEY, input); }, 400);
    return () => clearTimeout(t);
  }, [input]);

  // Ghost text carousel
  useEffect(() => {
    if (input.length > 0) return;
    const id = setInterval(() => {
      setGhostVisible(false);
      setTimeout(() => {
        setGhostIdx((i) => (i + 1) % ghostPrompts.length);
        setGhostVisible(true);
      }, 220);
    }, 4000);
    return () => clearInterval(id);
  }, [input.length, ghostPrompts.length]);

  // Sync initialValue from parent
  useEffect(() => {
    setInput(initialValue);
    if (initialValue) textareaRef.current?.focus();
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
    // Navigate slash command menu
    if (slashOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSlashIndex((i) => Math.min(i + 1, slashMatches.length - 1)); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSlashIndex((i) => Math.max(i - 1, 0)); return; }
      if (e.key === 'Escape') { setInput(''); return; }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        const cmd = slashMatches[slashIndex];
        if (cmd) runSlashCommand(cmd);
        return;
      }
    }
    // Navigate playbook menu
    if (atOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setAtIndex((i) => Math.min(i + 1, atMatches.length - 1)); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setAtIndex((i) => Math.max(i - 1, 0)); return; }
      if (e.key === 'Escape') { setInput(''); return; }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        const q = atMatches[atIndex];
        if (q) { setInput(q.query_text); setTimeout(() => textareaRef.current?.focus(), 0); }
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const canSend = input.trim().length > 0 && !submitting;

  return (
    <div className={centered ? '' : 'px-4 pb-4 pt-2'}>
      <div className={centered ? 'w-full' : 'max-w-3xl lg:max-w-[900px] mx-auto'}>
        <div className="relative">
          {/* Slash command popover */}
          {slashOpen && (
            <SlashCommandPopover
              commands={slashMatches}
              activeIndex={slashIndex}
              onSelect={runSlashCommand}
              onHover={setSlashIndex}
            />
          )}
          {/* Playbook popover */}
          {atOpen && (
            <PlaybookPopover
              queries={atMatches}
              activeIndex={atIndex}
              onSelect={(q) => { setInput(q.query_text); setTimeout(() => textareaRef.current?.focus(), 0); }}
              onHover={setAtIndex}
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
              placeholder=""
              autoFocus
              rows={1}
              aria-label="Start a new conversation"
              className="w-full resize-none bg-transparent px-4 pt-4 pb-2 text-base md:text-sm leading-relaxed focus:outline-none min-h-[52px]"
            />
            {input.length === 0 && (
              <span
                aria-hidden
                className="pointer-events-none absolute left-4 top-4 text-sm leading-relaxed text-muted-foreground transition-opacity duration-200 ease-in-out"
                style={{ opacity: ghostVisible ? 1 : 0 }}
              >
                {ghostPrompts[ghostIdx % ghostPrompts.length]}
              </span>
            )}

            <div className="flex items-center justify-between px-3 pb-3">
              <div className="flex items-center gap-1">
                {/* Deep Analysis */}
                <button
                  type="button"
                  aria-pressed={deepAnalysis}
                  onClick={() => setDeepAnalysis(!deepAnalysis)}
                  className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors border outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                    deepAnalysis
                      ? 'chip-deep-analysis'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent border-transparent'
                  }`}
                >
                  <BrainCircuit className="w-3.5 h-3.5 shrink-0" />
                  <span className="hidden sm:inline">Deep Analysis</span>
                </button>

                {/* Playbook — only shown when entries exist */}
                {playbookQueries.length > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      setInput('@');
                      setTimeout(() => textareaRef.current?.focus(), 0);
                    }}
                    className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors border border-transparent text-muted-foreground hover:text-foreground hover:bg-accent outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  >
                    <BookOpen className="w-3.5 h-3.5 shrink-0" />
                    <span className="hidden sm:inline">Playbook</span>
                  </button>
                )}

                {/* Save to Playbook — shown when text is typed */}
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
                    <TooltipContent side="right">Save to Playbook</TooltipContent>
                  </Tooltip>
                )}
              </div>

              <div className="relative h-9 w-9 flex items-center justify-center">
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
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground/80 mt-2">
          MTI Brain is AI and can make mistakes. Please double-check responses.
        </p>
      </div>

      <Dialog open={saveDialogOpen} onOpenChange={setSaveDialogOpen}>
        <DialogContent className="sm:max-w-md p-6 gap-0">
          <DialogTitle className="text-lg font-semibold text-foreground mb-1">
            Save to Playbook
          </DialogTitle>
          <p className="text-sm text-muted-foreground mb-4">Give this query a name so you can reuse it later.</p>
          <input
            autoFocus
            value={saveQueryName}
            onChange={(e) => setSaveQueryName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); handleSavePlaybook(); }
              if (e.key === 'Escape') setSaveDialogOpen(false);
            }}
            placeholder="Name this query…"
            className="w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground"
          />
          <DialogFooter className="mt-4">
            <Button variant="ghost" onClick={() => setSaveDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleSavePlaybook}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
