import { describe, it, expect } from 'vitest';
import type { Message } from '@/lib/store/threads';
import {
  groupConversationTurns,
  getActiveIdx,
  computeVisibility,
  getLastVisibleAssistantConvId,
} from '@/lib/utils/conversation-tree';

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-' + Math.random().toString(36).slice(2),
    conversation_id: '',
    role: 'user',
    content: 'hello',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

describe('groupConversationTurns', () => {
  it('groups a simple user+assistant pair into one turn', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user', content: 'Hi' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant', content: 'Hello!' }),
    ];

    const turns = groupConversationTurns(messages);
    expect(turns).toHaveLength(1);
    expect(turns[0].versions).toEqual(['c1']);
    expect(turns[0].allMessages.get('c1')).toHaveLength(2);
  });

  it('creates separate turns for distinct conversation IDs', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user', content: 'Q1' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant', content: 'A1' }),
      makeMessage({ conversation_id: 'c2', role: 'user', content: 'Q2' }),
      makeMessage({ conversation_id: 'c2', role: 'assistant', content: 'A2' }),
    ];

    const turns = groupConversationTurns(messages);
    expect(turns).toHaveLength(2);
    expect(turns[0].versions).toEqual(['c1']);
    expect(turns[1].versions).toEqual(['c2']);
  });

  it('groups version branches under the root conversation', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user', content: 'Q1' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant', content: 'A1' }),
      makeMessage({ conversation_id: 'c1-retry', parent_conversation_id: 'c1', role: 'user', content: 'Q1' }),
      makeMessage({ conversation_id: 'c1-retry', parent_conversation_id: 'c1', role: 'assistant', content: 'A1 v2' }),
    ];

    const turns = groupConversationTurns(messages);
    expect(turns).toHaveLength(1);
    expect(turns[0].versions).toEqual(['c1', 'c1-retry']);
  });

  it('handles streaming messages without conversation_id', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user', content: 'Q1' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant', content: 'A1' }),
      makeMessage({ conversation_id: '', role: 'user', content: 'Q2' }),
      makeMessage({ conversation_id: '', role: 'assistant', content: '', isStreaming: true }),
    ];

    const turns = groupConversationTurns(messages);
    // The streaming messages without conv id form an orphan turn
    expect(turns).toHaveLength(2);
    expect(turns[1].versions).toEqual(['streaming:new']);
  });

  it('handles empty messages array', () => {
    const turns = groupConversationTurns([]);
    expect(turns).toEqual([]);
  });

  it('extracts sourceConversationId from root user message', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user', content: 'Q1', source_conversation_id: 'parent-c' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant', content: 'A1' }),
    ];

    const turns = groupConversationTurns(messages);
    expect(turns[0].sourceConversationId).toBe('parent-c');
  });
});

describe('getActiveIdx', () => {
  it('returns the user-selected index from activeVersions', () => {
    const turn = {
      versions: ['c1', 'c2', 'c3'],
      allMessages: new Map([
        ['c1', [makeMessage({ conversation_id: 'c1' })]],
        ['c2', [makeMessage({ conversation_id: 'c2' })]],
        ['c3', [makeMessage({ conversation_id: 'c3' })]],
      ]),
      turnKey: 'k1',
    };

    const activeVersions = { 'c1,c2,c3': 1 };
    expect(getActiveIdx(turn, activeVersions)).toBe(1);
  });

  it('returns the streaming index when no activeVersions entry exists', () => {
    const turn = {
      versions: ['c1', 'c2'],
      allMessages: new Map([
        ['c1', [makeMessage({ conversation_id: 'c1' })]],
        ['c2', [makeMessage({ conversation_id: 'c2', isStreaming: true })]],
      ]),
      turnKey: 'k1',
    };

    expect(getActiveIdx(turn, undefined)).toBe(1);
  });

  it('returns last index when no streaming and no activeVersions', () => {
    const turn = {
      versions: ['c1', 'c2', 'c3'],
      allMessages: new Map([
        ['c1', [makeMessage({ conversation_id: 'c1' })]],
        ['c2', [makeMessage({ conversation_id: 'c2' })]],
        ['c3', [makeMessage({ conversation_id: 'c3' })]],
      ]),
      turnKey: 'k1',
    };

    expect(getActiveIdx(turn, undefined)).toBe(2);
  });
});

describe('computeVisibility', () => {
  it('marks all turns visible when none have sourceConversationId', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant' }),
      makeMessage({ conversation_id: 'c2', role: 'user' }),
      makeMessage({ conversation_id: 'c2', role: 'assistant' }),
    ];

    const turns = groupConversationTurns(messages);
    const visible = computeVisibility(turns, undefined);
    expect(visible).toEqual([true, true]);
  });

  it('hides turns whose sourceConversationId is not active', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant' }),
      makeMessage({ conversation_id: 'c2', role: 'user', source_conversation_id: 'nonexistent' }),
      makeMessage({ conversation_id: 'c2', role: 'assistant' }),
    ];

    const turns = groupConversationTurns(messages);
    const visible = computeVisibility(turns, undefined);
    expect(visible[0]).toBe(true);
    expect(visible[1]).toBe(false);
  });

  it('shows follow-up turns whose source is active', () => {
    const messages: Message[] = [
      makeMessage({ conversation_id: 'c1', role: 'user' }),
      makeMessage({ conversation_id: 'c1', role: 'assistant' }),
      makeMessage({ conversation_id: 'c2', role: 'user', source_conversation_id: 'c1' }),
      makeMessage({ conversation_id: 'c2', role: 'assistant' }),
    ];

    const turns = groupConversationTurns(messages);
    const visible = computeVisibility(turns, undefined);
    expect(visible).toEqual([true, true]);
  });
});

describe('getLastVisibleAssistantConvId', () => {
  it('returns the last visible assistant conversation id', () => {
    const messages: Message[] = [
      makeMessage({ id: 'u1', conversation_id: 'c1', role: 'user' }),
      makeMessage({ id: 'a1', conversation_id: 'c1', role: 'assistant' }),
      makeMessage({ id: 'u2', conversation_id: 'c2', role: 'user' }),
      makeMessage({ id: 'a2', conversation_id: 'c2', role: 'assistant' }),
    ];

    const result = getLastVisibleAssistantConvId(messages, undefined);
    expect(result).toBe('c2');
  });

  it('returns undefined for empty messages', () => {
    expect(getLastVisibleAssistantConvId([], undefined)).toBeUndefined();
  });

  it('skips streaming messages', () => {
    const messages: Message[] = [
      makeMessage({ id: 'u1', conversation_id: 'c1', role: 'user' }),
      makeMessage({ id: 'a1', conversation_id: 'c1', role: 'assistant' }),
      makeMessage({ id: 'u2', conversation_id: '', role: 'user' }),
      makeMessage({ id: 'a2', conversation_id: '', role: 'assistant', isStreaming: true }),
    ];

    const result = getLastVisibleAssistantConvId(messages, undefined);
    expect(result).toBe('c1');
  });
});
