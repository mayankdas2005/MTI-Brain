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

  return (
    <div>
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
          <ConversationTurn
            key={turn.versions.join(',')}
            turn={turn}
            threadId={threadId}
            activeIdx={getActiveIdx(turn)}
            onActiveIdxChange={(idx) => setActiveIdx(turn, idx)}
          />
        ) : null,
      )}
    </div>
  );
}
