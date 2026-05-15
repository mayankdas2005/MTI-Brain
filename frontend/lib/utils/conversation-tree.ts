import type { Message } from '@/lib/store/threads';

export interface ConversationTurnData {
  versions: string[];
  allMessages: Map<string, Message[]>;
  sourceConversationId?: string;
  turnKey: string;
}

export function groupConversationTurns(messages: Message[]): ConversationTurnData[] {
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

    const rootMsgs = byConvId.get(cid) || [];
    const sourceUserMsg = rootMsgs.find((m) => m.role === 'user');
    const sourceConversationId = sourceUserMsg?.source_conversation_id;

    const allMsgsFlat = [...allMessages.values()].flat();
    const userMsg = allMsgsFlat.find((m) => m.role === 'user');
    const turnKey = userMsg?.id ?? versions.join(',');

    turns.push({ versions, allMessages, sourceConversationId, turnKey });
  }

  if (orphanStreaming.length > 0) {
    const tempId = 'streaming:new';
    const streamingUserMsg = orphanStreaming.find((m) => m.role === 'user');
    turns.push({
      versions: [tempId],
      allMessages: new Map([[tempId, orphanStreaming]]),
      sourceConversationId: streamingUserMsg?.source_conversation_id,
      turnKey: streamingUserMsg?.id ?? tempId,
    });
  }

  return turns;
}

export function getActiveIdx(
  turn: ConversationTurnData,
  activeVersions: Record<string, number> | undefined,
): number {
  const key = turn.versions.join(',');
  if (activeVersions && key in activeVersions) return activeVersions[key];
  const streamingIdx = turn.versions.findIndex((v) =>
    turn.allMessages.get(v)?.some((m) => m.isStreaming),
  );
  return streamingIdx >= 0 ? streamingIdx : turn.versions.length - 1;
}

export function computeVisibility(
  turns: ConversationTurnData[],
  activeVersions: Record<string, number> | undefined,
): boolean[] {
  const activeConvIds = new Set<string>();
  const visible: boolean[] = [];
  for (let i = 0; i < turns.length; i++) {
    const turn = turns[i];
    if (turn.sourceConversationId) {
      visible[i] = activeConvIds.has(turn.sourceConversationId);
    } else {
      visible[i] = true;
    }
    if (visible[i]) {
      const idx = getActiveIdx(turn, activeVersions);
      const activeConvId = turn.versions[idx];
      if (activeConvId) activeConvIds.add(activeConvId);
    }
  }
  return visible;
}

export function getLastVisibleAssistantConvId(
  messages: Message[],
  activeVersions: Record<string, number> | undefined,
): string | undefined {
  const turns = groupConversationTurns(messages);
  const visible = computeVisibility(turns, activeVersions);
  for (let i = turns.length - 1; i >= 0; i--) {
    if (!visible[i]) continue;
    const turn = turns[i];
    const idx = getActiveIdx(turn, activeVersions);
    const activeConvId = turn.versions[idx];
    const msgs = turn.allMessages.get(activeConvId) || [];
    const assistant = msgs.find(
      (m) => m.role === 'assistant' && m.conversation_id && !m.isStreaming,
    );
    if (assistant?.conversation_id) return assistant.conversation_id;
  }
  return undefined;
}
