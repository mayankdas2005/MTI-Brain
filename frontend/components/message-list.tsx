'use client';

import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { Message } from '@/lib/store/threads';
import { MessageBubble } from './message-bubble';

interface MessageListProps {
  messages: Message[];
  threadId: string;
}

interface ConversationTurnData {
  versions: string[];
  allMessages: Map<string, Message[]>;
  /** If set, this turn is only visible when this conversation_id is the active version. */
  sourceConversationId?: string;
}

/**
 * Group messages into conversation turns with version branches.
 * Backend guarantees: parent_conversation_id always points to the ROOT.
 */
function groupConversationTurns(messages: Message[]): ConversationTurnData[] {
  const byConvId = new Map<string, Message[]>();
  const versionMap = new Map<string, string[]>();
  const isChildVersion = new Set<string>();
  const streamingMessages: Message[] = [];

  for (const msg of messages) {
    const cid = msg.conversation_id;
    if (!cid) {
      streamingMessages.push(msg);
      continue;
    }
    if (!byConvId.has(cid)) byConvId.set(cid, []);
    byConvId.get(cid)!.push(msg);

    if (msg.parent_conversation_id) {
      const root = msg.parent_conversation_id;
      if (!versionMap.has(root)) versionMap.set(root, [root]);
      const versions = versionMap.get(root)!;
      if (!versions.includes(cid)) versions.push(cid);
      isChildVersion.add(cid);
    }
  }

  // Group streaming messages by parent_conversation_id so user+assistant
  // from the same edit/retry share a single version slot
  const streamingByParent = new Map<string, Message[]>();
  const orphanStreaming: Message[] = [];

  for (const msg of streamingMessages) {
    if (msg.parent_conversation_id) {
      const root = msg.parent_conversation_id;
      if (!streamingByParent.has(root)) streamingByParent.set(root, []);
      streamingByParent.get(root)!.push(msg);
    } else {
      orphanStreaming.push(msg);
    }
  }

  for (const [root, msgs] of streamingByParent) {
    const tempId = `streaming:${root}`;
    if (!versionMap.has(root)) versionMap.set(root, [root]);
    const versions = versionMap.get(root)!;
    if (!versions.includes(tempId)) versions.push(tempId);
    byConvId.set(tempId, msgs);
    isChildVersion.add(tempId);
  }

  const turns: ConversationTurnData[] = [];
  const seen = new Set<string>();

  for (const msg of messages) {
    const cid = msg.conversation_id;
    if (!cid || seen.has(cid)) continue;
    seen.add(cid);
    if (isChildVersion.has(cid)) continue;

    const versions = versionMap.get(cid) || [cid];
    for (const v of versions) seen.add(v);

    const allMessages = new Map<string, Message[]>();
    for (const v of versions) {
      allMessages.set(v, byConvId.get(v) || []);
    }

    // Check if this turn was spawned from a specific version (follow-up branching)
    const rootMsgs = byConvId.get(cid) || [];
    const sourceUserMsg = rootMsgs.find((m) => m.role === 'user');
    const sourceConversationId = sourceUserMsg?.source_conversation_id;

    turns.push({ versions, allMessages, sourceConversationId });
  }

  // Streaming messages without a parent (new question) → single turn
  if (orphanStreaming.length > 0) {
    const tempId = 'streaming:new';
    const streamingUserMsg = orphanStreaming.find((m) => m.role === 'user');
    turns.push({
      versions: [tempId],
      allMessages: new Map([[tempId, orphanStreaming]]),
      sourceConversationId: streamingUserMsg?.source_conversation_id,
    });
  }

  return turns;
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

  // Stable ref for the callback to avoid re-triggering the effect
  const onChangeRef = useRef(onActiveIdxChange);
  onChangeRef.current = onActiveIdxChange;

  // Auto-switch to the streaming version when retry/edit starts
  useEffect(() => {
    if (streamingIdx >= 0) {
      onChangeRef.current(streamingIdx);
    }
  }, [streamingIdx, versions.length]);

  const safeIdx = Math.min(activeIdx, versions.length - 1);
  const activeConvId = versions[safeIdx];
  let msgs = allMessages.get(activeConvId) || [];
  const hasMultipleVersions = versions.length > 1;

  // For retry versions that only contain an assistant message, prepend the
  // user message from the root version so the question stays visible.
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

  // Track active version index per turn (keyed by turn's versions signature)
  const [activeVersions, setActiveVersions] = useState<Record<string, number>>({});

  const getActiveIdx = useCallback((turn: ConversationTurnData) => {
    const key = turn.versions.join(',');
    if (key in activeVersions) return activeVersions[key];
    // Default: streaming version or latest
    const streamingIdx = turn.versions.findIndex((v) =>
      turn.allMessages.get(v)?.some((m) => m.isStreaming),
    );
    return streamingIdx >= 0 ? streamingIdx : turn.versions.length - 1;
  }, [activeVersions]);

  const setActiveIdx = useCallback((turn: ConversationTurnData, idx: number) => {
    const key = turn.versions.join(',');
    setActiveVersions((prev) => ({ ...prev, [key]: idx }));
  }, []);

  // Cascading visibility: turns with a sourceConversationId are only visible
  // when that conversation_id is the active version of a prior turn.
  // Turns without a source stay visible unless a prior branched turn is set
  // to a non-latest version (backward compat truncation).
  const visibleTurns = useMemo(() => {
    const activeConvIds = new Set<string>();
    const visible: boolean[] = [];
    let truncated = false;

    for (let i = 0; i < turns.length; i++) {
      const turn = turns[i];

      if (turn.sourceConversationId) {
        // Source-linked: only visible if source version is active
        visible[i] = activeConvIds.has(turn.sourceConversationId);
      } else {
        // No source: visible unless truncated by an older-version selection
        visible[i] = !truncated;
      }

      if (visible[i]) {
        const idx = getActiveIdx(turn);
        const activeConvId = turn.versions[idx];
        if (activeConvId) activeConvIds.add(activeConvId);

        // Truncate downstream turns when:
        // 1. A multi-version turn shows a non-latest version (user navigated back), OR
        // 2. A turn has a streaming version (retry/edit in progress — old downstream is stale)
        if (!truncated && turn.versions.length > 1) {
          const isStreamingVersion = turn.versions.some((v) =>
            turn.allMessages.get(v)?.some((m) => m.isStreaming),
          );
          if (isStreamingVersion || idx < turn.versions.length - 1) {
            truncated = true;
          }
        }
      }
    }
    return visible;
  }, [turns, getActiveIdx]);

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
