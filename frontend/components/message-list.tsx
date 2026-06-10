'use client';

import { useMemo, useEffect, useCallback, useRef } from 'react';
import { Message, useThreadStore } from '@/lib/store/threads';
import { MessageBubble } from './message-bubble';
import {
  ConversationTurnData,
  groupConversationTurns,
  getActiveIdx as getActiveIdxFromVersions,
  computeVisibility,
} from '@/lib/utils/conversation-tree';

interface MessageListProps {
  messages: Message[];
  threadId: string;
}

function ConversationTurn({
  turn,
  threadId,
  activeIdx,
  onActiveIdxChange,
}: {
  turn: ConversationTurnData;
  threadId: string;
  activeIdx: number;
  onActiveIdxChange: (idx: number) => void;
}) {
  const { versions, allMessages } = turn;
  const streamingIdx = versions.findIndex((v) =>
    allMessages.get(v)?.some((m) => m.isStreaming),
  );

  const onChangeRef = useRef(onActiveIdxChange);
  onChangeRef.current = onActiveIdxChange;

  useEffect(() => {
    if (streamingIdx >= 0) {
      onChangeRef.current(streamingIdx);
    }
  }, [streamingIdx, versions.length]);

  const safeIdx = Math.min(activeIdx, versions.length - 1);
  const activeConvId = versions[safeIdx];
  let msgs = allMessages.get(activeConvId) || [];
  const hasMultipleVersions = versions.length > 1;

  if (hasMultipleVersions && !msgs.some((m) => m.role === 'user')) {
    const rootMsgs = allMessages.get(versions[0]) || [];
    const rootUser = rootMsgs.find((m) => m.role === 'user');
    if (rootUser) {
      msgs = [rootUser, ...msgs];
    }
  }

  const versionNav = hasMultipleVersions ? {
    current: safeIdx + 1,
    total: versions.length,
    onPrev: () => onActiveIdxChange(Math.max(0, safeIdx - 1)),
    onNext: () => onActiveIdxChange(Math.min(versions.length - 1, safeIdx + 1)),
    hasPrev: safeIdx > 0,
    hasNext: safeIdx < versions.length - 1,
  } : undefined;

  // Stream-active turns get aria-live="polite" so screen readers announce
  // new chunks as they arrive. Idle turns omit the attribute to avoid
  // noisy re-announcements during virtual scroll.
  const turnIsStreaming = msgs.some((m) => m.isStreaming);

  // Stable scroll anchor: always the first user message in the oldest version.
  // Search results return the original message id (oldest version), so this
  // anchor works even when the user has edited the message and switched to v2+.
  const anchorId = (allMessages.get(versions[0]) || []).find((m) => m.role === 'user')?.id;

  return (
    <div
      id={anchorId ? `msg-${anchorId}` : undefined}
      aria-live={turnIsStreaming ? 'polite' : undefined}
      aria-atomic="false"
    >
      {msgs.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          threadId={threadId}
          versionNav={message.role === 'user' ? versionNav : undefined}
        />
      ))}
    </div>
  );
}

export function MessageList({ messages, threadId }: MessageListProps) {
  const turns = useMemo(() => groupConversationTurns(messages), [messages]);

  const activeVersionsForThread = useThreadStore(
    (s) => s.activeVersions[threadId],
  );
  const setActiveVersion = useThreadStore((s) => s.setActiveVersion);

  const getActiveIdx = useCallback(
    (turn: ConversationTurnData) => getActiveIdxFromVersions(turn, activeVersionsForThread),
    [activeVersionsForThread],
  );

  const setActiveIdx = useCallback(
    (turn: ConversationTurnData, idx: number) => {
      setActiveVersion(threadId, turn.versions.join(','), idx);
    },
    [setActiveVersion, threadId],
  );

  const visibleTurns = useMemo(
    () => computeVisibility(turns, activeVersionsForThread),
    [turns, activeVersionsForThread],
  );

  return (
    <div className="space-y-4">
      {turns.map((turn, i) =>
        visibleTurns[i] ? (
          <div key={turn.turnKey}>
            <ConversationTurn
              turn={turn}
              threadId={threadId}
              activeIdx={getActiveIdx(turn)}
              onActiveIdxChange={(idx) => setActiveIdx(turn, idx)}
            />
          </div>
        ) : null,
      )}
    </div>
  );
}
