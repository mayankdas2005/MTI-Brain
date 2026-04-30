'use client';

import { useState, useRef, useEffect, use } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore } from '@/lib/store/threads';
import { MessageList } from '@/components/message-list';
import { ChatComposer } from '@/components/chat-composer';
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
      router.replace('/new');
    });
  }, [chatId, fetchThread, setCurrentThread, router, pendingQuestion, isStreaming, streamingThreadId]);

  // Cmd+K is handled at the layout level (opens search modal)

  // Auto-scroll to bottom when messages change.
  // Force auto-scroll when new messages are added (follow-up, new question).
  // Use 'instant' during streaming deltas to avoid jittery smooth-scroll restarts.
  const prevLenRef = useRef(displayedMessages.length);
  useEffect(() => {
    const isNewMessage = displayedMessages.length !== prevLenRef.current;
    prevLenRef.current = displayedMessages.length;

    // New message added → force scroll regardless of current position
    if (isNewMessage && scrollRef.current) {
      setAutoScroll(true);
      setHasNewResponse(false);
      scrollRef.current.scrollIntoView({ behavior: 'smooth' });
      return;
    }

    // Streaming delta → scroll only if already following
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: 'instant' });
    }
  }, [displayedMessages, autoScroll, isStreaming]);

  // Detect response completed while scrolled up
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming && !autoScroll) {
      setHasNewResponse(true);
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, autoScroll]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    setAutoScroll(isNearBottom);
    if (isNearBottom) setHasNewResponse(false);
  };

  const scrollToBottom = () => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' });
    setAutoScroll(true);
    setHasNewResponse(false);
  };

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
          <div className="max-w-3xl mx-auto space-y-6 px-4">
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
    <div className="flex flex-col h-full bg-background relative">
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
              <div className="max-w-3xl mx-auto">
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
  );
}
