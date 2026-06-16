'use client';

import { useState, useRef, useEffect, useCallback, use } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore, isThreadCreationPending } from '@/lib/store/threads';
import { usePreferencesStore } from '@/lib/store/preferences';
import { useUIStore } from '@/lib/store/ui';
import { toast } from '@/lib/toast';
import { MessageList } from '@/components/message-list';
import { ChatComposer } from '@/components/chat-composer';
import { ThinkingSidePanel } from '@/components/thinking-side-panel';
import { ArrowDown } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';

interface ChatPageProps {
  params: Promise<{ chatId: string }>;
}

export default function ChatPage({ params }: ChatPageProps) {
  const { chatId } = use(params);
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const autoScrollRef = useRef(true);
  const [hasNewResponse, setHasNewResponse] = useState(false);
  const prevStreamingRef = useRef(false);
  const streamJustEndedRef = useRef(false);

  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const currentMessages = useThreadStore((s) => s.currentMessages);
  const streamingMessages = useThreadStore((s) => s.streamingMessages);
  const messagesLoading = useThreadStore((s) => s.messagesLoading);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const streamingThreadId = useThreadStore((s) => s.streamingThreadId);
  const fetchThread = useThreadStore((s) => s.fetchThread);
  const setCurrentThread = useThreadStore((s) => s.setCurrentThread);
  const pendingQuestion = useThreadStore((s) => s.pendingQuestion);

  // Authoritative source for rendering. If this page is showing the
  // currently-streaming thread, render from the dedicated streamingMessages
  // slot - it's immune to setCurrentThread clearing it during navigation,
  // so returning to the thread keeps the live stream visible.
  const useStreamingSlot =
    streamingThreadId === chatId && streamingMessages.length > 0;
  const displayedMessages = useStreamingSlot ? streamingMessages : currentMessages;

  // Load thread from backend on mount / chatId change
  useEffect(() => {
    setCurrentThread(chatId);
    const store = useThreadStore.getState();
    const streamingHere = isStreaming && streamingThreadId === chatId;
    const streamSlotHas = store.streamingMessages.length > 0;
    const currentHas = store.currentThreadId === chatId && store.currentMessages.length > 0;
    // Skip fetch only when the stream is for THIS thread AND we already
    // have its messages in EITHER slot (live SSE keeps them fresh in
    // streamingMessages even if currentMessages was cleared).
    // Detect streaming → not-streaming transition for THIS thread.
    // When a stream just finished, onDone already populated currentMessages
    // with the complete data - skip the background-refresh to avoid a race
    // with the backend's async message save.
    const justEnded = streamJustEndedRef.current;
    streamJustEndedRef.current = false;
    if (!isStreaming && store.streamingThreadId === null && currentHas && justEnded) {
      // Mark for next transition detection
      return;
    }
    // Track when streaming is active so we know when it ends
    if (streamingHere) {
      streamJustEndedRef.current = true;
    }
    const decide = () => {
      if (pendingQuestion) return 'skip-pending';
      if (isThreadCreationPending()) return 'skip-pending';
      if (streamingHere && (streamSlotHas || currentHas)) return 'skip-streaming';
      if (currentHas) return 'background-refresh';
      return 'fetch';
    };
    const decision = decide();
    if (process.env.NODE_ENV !== 'production') {
      // eslint-disable-next-line no-console
      console.debug('[chat-page] effect', {
        chatId,
        decision,
        currentThreadId: store.currentThreadId,
        isStreaming,
        streamingThreadId,
        currentMessages: store.currentMessages.length,
        streamingMessages: store.streamingMessages.length,
        pendingQuestion: !!pendingQuestion,
      });
    }
    if (decision === 'skip-pending' || decision === 'skip-streaming') return;
    if (decision === 'background-refresh') {
      fetchThread(chatId).catch(() => {});
      return;
    }
    fetchThread(chatId).catch(() => {
      toast.error('Chat not found.');
      router.replace('/new');
    });
  }, [chatId, fetchThread, setCurrentThread, router, pendingQuestion, isStreaming, streamingThreadId]);

  // Hash-based scroll: when URL has #msg-<uuid>, scroll to that element.
  // Two triggers:
  //  1. Effect: fires on mount / chatId change / messages load (handles async load on fresh navigation)
  //  2. hashchange listener: handles same-thread re-searches from Ctrl+K (no page reload, no message count change)
  const lastHashScrollRef = useRef<string | null>(null);
  useEffect(() => { lastHashScrollRef.current = null; }, [chatId]);

  const scrollToHash = useCallback(() => {
    const hash = window.location.hash;
    if (!hash || hash === lastHashScrollRef.current) return;
    const el = document.getElementById(hash.slice(1));
    if (el) {
      lastHashScrollRef.current = hash;
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, []);

  useEffect(() => { scrollToHash(); }, [displayedMessages.length, chatId, scrollToHash]);

  useEffect(() => {
    window.addEventListener('hashchange', scrollToHash);
    return () => window.removeEventListener('hashchange', scrollToHash);
  }, [scrollToHash]);

  // Cmd+K is handled at the layout level (opens search modal)

  const streamingOrigin = useThreadStore((s) => s.streamingOrigin);
  const streamingMessageId = useThreadStore((s) => s.streamingMessageId);

  // Auto-scroll to bottom when messages change.
  // For new questions / follow-ups → scroll to bottom.
  // For retry / edit → scroll to the retried/edited turn (not bottom).
  // Use 'instant' during streaming deltas to avoid jittery smooth-scroll restarts.
  const prevLenRef = useRef(displayedMessages.length);
  useEffect(() => {
    const isNewMessage = displayedMessages.length !== prevLenRef.current;
    prevLenRef.current = displayedMessages.length;

    // New message added → decide scroll target based on origin
    if (isNewMessage && scrollRef.current) {
      // Hash navigation takes priority — the hash-scroll effect handles positioning.
      if (window.location.hash) return;

      const origin = useThreadStore.getState().streamingOrigin;
      // Only honour retry/edit scroll when streaming is actively for THIS thread.
      // stale streamingOrigin from a previous operation must not hijack a fresh load.
      const isActiveStreamHere = isStreaming && useThreadStore.getState().streamingThreadId === chatId;

      if (isActiveStreamHere && (origin === 'retry' || origin === 'edit')) {
        // Scroll to the streaming assistant message (near the retried/edited question)
        const msgId = useThreadStore.getState().streamingMessageId;
        if (msgId) {
          const el = document.getElementById(`msg-${msgId}`);
          if (el) {
            setAutoScroll(true);
            setHasNewResponse(false);
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return;
          }
        }
      }

      // Default: scroll to bottom (new question / follow-up)
      setAutoScroll(true);
      setHasNewResponse(false);
      scrollRef.current.scrollIntoView({ behavior: 'smooth' });
      return;
    }

    // Streaming delta → keep content in view only if user hasn't scrolled away.
    // Use 'instant' (not 'smooth') — smooth on every delta creates continuous
    // animation that fights user scroll input and makes the outer scroll uncontrollable.
    if (isStreaming) {
      const origin = useThreadStore.getState().streamingOrigin;
      if (origin === 'retry' || origin === 'edit') {
        // For retry/edit: only follow if user hasn't manually scrolled away.
        // (Initial scroll-to-message on isNewMessage already positioned them there.)
        if (autoScrollRef.current) {
          const msgId = useThreadStore.getState().streamingMessageId;
          if (msgId) {
            const el = document.getElementById(`msg-${msgId}`);
            if (el) {
              el.scrollIntoView({ behavior: 'instant' as ScrollBehavior, block: 'end' });
              return;
            }
          }
        }
        return;
      }
    }

    // Normal: scroll to bottom only if already following.
    // Guard with hash: don't snap to bottom while viewing a search-targeted message.
    if (autoScrollRef.current && scrollRef.current && !window.location.hash) {
      scrollRef.current.scrollIntoView({ behavior: 'instant' as ScrollBehavior });
    }
  }, [displayedMessages, autoScroll, isStreaming, chatId]);

  // Detect response completed while scrolled up
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      const origin = useThreadStore.getState().streamingOrigin;
      if (origin === 'retry' || origin === 'edit') {
        // Final scroll so the completed response isn't cut off mid-screen
        const msgId = useThreadStore.getState().streamingMessageId;
        if (msgId) {
          const el = document.getElementById(`msg-${msgId}`);
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'end' });
        }
      } else if (!autoScroll) {
        setHasNewResponse(true);
      }
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, autoScroll]);

  // Visual-viewport keyboard avoidance: when the iOS soft keyboard slides up,
  // window.visualViewport.height shrinks. We expose the difference as a CSS
  // variable so the composer can lift above the keyboard. The fallback (0)
  // means desktop / Android-without-vv-resize is unaffected.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const vv = window.visualViewport;
    if (!vv) return;
    const onResize = () => {
      const inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      document.documentElement.style.setProperty('--vv-bottom-inset', `${inset}px`);
    };
    onResize();
    vv.addEventListener('resize', onResize);
    vv.addEventListener('scroll', onResize);
    return () => {
      vv.removeEventListener('resize', onResize);
      vv.removeEventListener('scroll', onResize);
      document.documentElement.style.removeProperty('--vv-bottom-inset');
    };
  }, []);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    // Tight threshold: only consider "at bottom" when truly within 40px.
    // The previous 120px was too wide — as streaming adds content the user
    // could scroll up 80px and still be pulled back down.
    const isAtBottom = distFromBottom < 40;
    autoScrollRef.current = isAtBottom;
    setAutoScroll(isAtBottom);
    if (isAtBottom) setHasNewResponse(false);
  };

  const scrollToBottom = () => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' });
    autoScrollRef.current = true;
    setAutoScroll(true);
    setHasNewResponse(false);
  };

  // Thinking side panel: auto-open during streaming when in sidebar mode
  const thinkingPlacement = usePreferencesStore((s) => s.thinkingPlacement);
  const openThinkingPanel = useUIStore((s) => s.openThinkingPanel);

  useEffect(() => {
    if (
      thinkingPlacement === 'sidebar' &&
      isStreaming &&
      streamingThreadId === chatId &&
      streamingMessageId
    ) {
      openThinkingPanel(streamingMessageId);
    }
  }, [thinkingPlacement, isStreaming, streamingThreadId, chatId, streamingMessageId, openThinkingPanel]);

  const hasMessages = displayedMessages.length > 0;

  // Show loading when thread data hasn't arrived yet:
  // - messagesLoading is true (fetch in progress), OR
  // - currentThreadId doesn't match (first render before useEffect)
  // Skip when there's a pendingQuestion or an active stream for this
  // thread - streaming populates messages directly via the streaming slot.
  // Also check threadMessageMap directly - if we have cached data for this
  // chatId, don't flash a skeleton even if currentMessages hasn't been
  // populated yet (setCurrentThread in the effect will pick it up).
  const hasCachedData = useThreadStore((s) => (s.threadMessageMap[chatId]?.length ?? 0) > 0);
  const needsLoad = currentThreadId !== chatId && !useStreamingSlot && !hasCachedData;
  if ((messagesLoading || needsLoad) && !hasMessages && !pendingQuestion) {
    return (
      <div className="flex flex-col h-full bg-background">
        <div className="flex-1 py-6">
          <div className="max-w-3xl lg:max-w-[900px] mx-auto space-y-6 px-4 md:px-6">
            <div className="flex justify-end">
              <Skeleton className="h-12 w-3/5 rounded-2xl" />
            </div>
            <div className="space-y-3">
              <Skeleton className="h-4 w-4/5" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-2/5" />
            </div>
            <div className="flex justify-end">
              <Skeleton className="h-12 w-2/5 rounded-2xl" />
            </div>
            <div className="space-y-3">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-3/5" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex flex-col flex-1 min-w-0 bg-background relative">
        <div
          ref={containerRef}
          className="flex-1 overflow-y-auto"
          onScroll={handleScroll}
        >
          <div className="min-h-full flex flex-col">
            {!hasMessages ? (
              <div className="flex-1" />
            ) : (
              <div className="flex-1 py-6">
                <div className="max-w-3xl lg:max-w-[900px] xl:max-w-[1100px] 2xl:max-w-[1200px] mx-auto px-4 md:px-6">
                  <MessageList messages={displayedMessages} threadId={chatId} />
                </div>
              </div>
            )}
            <div ref={scrollRef} className="h-px shrink-0" />
          </div>
        </div>

        {/* Scroll-to-bottom button */}
        {!autoScroll && (
          <div className="absolute bottom-24 left-1/2 -translate-x-1/2 z-20 animate-fade-in">
            <button
              onClick={scrollToBottom}
              className="flex items-center gap-1.5 rounded-full bg-foreground text-background px-3 py-1.5 text-xs font-medium shadow-lg hover:opacity-90 transition-opacity"
            >
              <ArrowDown className="w-3.5 h-3.5" />
              {hasNewResponse ? 'New response' : 'Scroll to bottom'}
            </button>
          </div>
        )}

        <ChatComposer />
      </div>

      {/* Thinking side panel (right side) */}
      <ThinkingSidePanel />
    </div>
  );
}
