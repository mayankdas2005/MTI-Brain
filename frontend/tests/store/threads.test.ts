import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';

// Mock API + SSE modules
vi.mock('@/lib/api', () => ({
  getRecents: vi.fn().mockResolvedValue([]),
  getThread: vi.fn().mockResolvedValue({ messages: [], title: 'Test', starred: false, project_id: null }),
  createThread: vi.fn().mockResolvedValue({ thread_id: 'new-thread-id', title: 'New Thread' }),
  deleteThread: vi.fn().mockResolvedValue(undefined),
  starThread: vi.fn().mockResolvedValue(undefined),
  renameThread: vi.fn().mockResolvedValue(undefined),
  moveThread: vi.fn().mockResolvedValue(undefined),
  bulkDeleteThreads: vi.fn().mockResolvedValue(undefined),
  bulkMoveThreads: vi.fn().mockResolvedValue(undefined),
  submitFeedback: vi.fn().mockResolvedValue({ liked: true, comment: null }),
  stopGeneration: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('@/lib/api/sse', () => ({
  streamSSE: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('@/lib/utils', () => ({
  randomId: vi.fn(() => 'mock-random-id'),
}));

vi.mock('@/lib/store/projects', () => ({
  useProjectStore: {
    getState: () => ({
      mutateProjectDetail: vi.fn(),
      fetchProjects: vi.fn(),
      invalidateProjectDetail: vi.fn(),
      refreshCurrentProjectIfMatches: vi.fn(),
    }),
  },
}));

vi.mock('@/lib/store/preferences', () => ({
  usePreferencesStore: {
    getState: () => ({
      responseTone: 'executive',
      maxResultRows: 100,
      deepAnalysis: false,
    }),
  },
}));

import { useThreadStore } from '@/lib/store/threads';
import * as api from '@/lib/api';

describe('useThreadStore', () => {
  beforeEach(() => {
    act(() => {
      useThreadStore.setState({
        threads: [],
        searchResults: [],
        isSearching: false,
        threadsLoading: false,
        searchQuery: '',
        threadsOffset: 0,
        hasMore: true,
        threadsLastFetched: 0,
        currentThreadId: null,
        currentMessages: [],
        currentThreadTitle: null,
        currentThreadStarred: false,
        currentThreadProjectId: null,
        messagesLoading: false,
        isStreaming: false,
        isStopping: false,
        streamingMessageId: null,
        streamingThreadId: null,
        streamingMessages: [],
        abortController: null,
        streamingOrigin: null,
        pendingQuestion: null,
        pendingDeepAnalysis: false,
        selectedThreadIds: new Set(),
        threadMessageMap: {},
        activeVersions: {},
      });
    });
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('starts with empty threads list', () => {
      expect(useThreadStore.getState().threads).toEqual([]);
    });

    it('starts with no current thread', () => {
      expect(useThreadStore.getState().currentThreadId).toBeNull();
    });

    it('starts with isStreaming as false', () => {
      expect(useThreadStore.getState().isStreaming).toBe(false);
    });
  });

  describe('setCurrentThread', () => {
    it('sets currentThreadId', () => {
      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 'thread-1', title: 'Thread 1', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
        });
      });

      act(() => {
        useThreadStore.getState().setCurrentThread('thread-1');
      });

      expect(useThreadStore.getState().currentThreadId).toBe('thread-1');
    });

    it('clears messages when switching to null', () => {
      act(() => {
        useThreadStore.setState({
          currentThreadId: 'thread-1',
          currentMessages: [{ id: 'msg-1', conversation_id: 'c1', role: 'user', content: 'hello', created_at: '' }],
          currentThreadTitle: 'Test',
          currentThreadStarred: true,
          currentThreadProjectId: 'proj-1',
        });
      });

      act(() => {
        useThreadStore.getState().setCurrentThread(null);
      });

      const state = useThreadStore.getState();
      expect(state.currentThreadId).toBeNull();
      expect(state.currentMessages).toEqual([]);
      expect(state.currentThreadTitle).toBeNull();
    });

    it('no-ops when setting same thread', () => {
      act(() => {
        useThreadStore.setState({
          currentThreadId: 'thread-1',
          currentMessages: [{ id: 'msg-1', conversation_id: 'c1', role: 'user', content: 'hello', created_at: '' }],
        });
      });

      act(() => {
        useThreadStore.getState().setCurrentThread('thread-1');
      });

      // Messages should remain unchanged (no-op)
      expect(useThreadStore.getState().currentMessages).toHaveLength(1);
    });

    it('restores messages from threadMessageMap', () => {
      const cachedMessages = [
        { id: 'msg-1', conversation_id: 'c1', role: 'user' as const, content: 'cached msg', created_at: '' },
      ];
      act(() => {
        useThreadStore.setState({
          currentThreadId: null,
          currentMessages: [],
          threadMessageMap: { 'thread-2': cachedMessages },
          threads: [{ id: 'thread-2', title: 'Cached', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' }],
        });
      });

      act(() => {
        useThreadStore.getState().setCurrentThread('thread-2');
      });

      expect(useThreadStore.getState().currentMessages).toEqual(cachedMessages);
    });
  });

  describe('setPendingQuestion', () => {
    it('stores a pending question', () => {
      act(() => {
        useThreadStore.getState().setPendingQuestion('What is revenue?');
      });

      expect(useThreadStore.getState().pendingQuestion).toBe('What is revenue?');
      expect(useThreadStore.getState().pendingDeepAnalysis).toBe(false);
    });

    it('stores deep analysis flag', () => {
      act(() => {
        useThreadStore.getState().setPendingQuestion('Complex query', true);
      });

      expect(useThreadStore.getState().pendingQuestion).toBe('Complex query');
      expect(useThreadStore.getState().pendingDeepAnalysis).toBe(true);
    });

    it('clears pending question with null', () => {
      act(() => {
        useThreadStore.getState().setPendingQuestion('temp');
      });
      act(() => {
        useThreadStore.getState().setPendingQuestion(null);
      });

      expect(useThreadStore.getState().pendingQuestion).toBeNull();
    });
  });

  describe('thread selection', () => {
    beforeEach(() => {
      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'T1', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
            { id: 't2', title: 'T2', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
            { id: 't3', title: 'T3', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
        });
      });
    });

    it('toggles thread selection on', () => {
      act(() => {
        useThreadStore.getState().toggleThreadSelection('t1');
      });

      expect(useThreadStore.getState().selectedThreadIds.has('t1')).toBe(true);
    });

    it('toggles thread selection off', () => {
      act(() => {
        useThreadStore.getState().toggleThreadSelection('t1');
      });
      act(() => {
        useThreadStore.getState().toggleThreadSelection('t1');
      });

      expect(useThreadStore.getState().selectedThreadIds.has('t1')).toBe(false);
    });

    it('selects all threads', () => {
      act(() => {
        useThreadStore.getState().selectAllThreads();
      });

      const selected = useThreadStore.getState().selectedThreadIds;
      expect(selected.size).toBe(3);
      expect(selected.has('t1')).toBe(true);
      expect(selected.has('t2')).toBe(true);
      expect(selected.has('t3')).toBe(true);
    });

    it('clears selection', () => {
      act(() => {
        useThreadStore.getState().selectAllThreads();
      });
      act(() => {
        useThreadStore.getState().clearSelection();
      });

      expect(useThreadStore.getState().selectedThreadIds.size).toBe(0);
    });
  });

  describe('activeVersions', () => {
    it('sets active version for a thread', () => {
      act(() => {
        useThreadStore.getState().setActiveVersion('thread-1', 'v1,v2', 1);
      });

      expect(useThreadStore.getState().activeVersions['thread-1']?.['v1,v2']).toBe(1);
    });

    it('clears active versions for a thread', () => {
      act(() => {
        useThreadStore.getState().setActiveVersion('thread-1', 'v1,v2', 1);
      });
      act(() => {
        useThreadStore.getState().clearActiveVersionsForThread('thread-1');
      });

      expect(useThreadStore.getState().activeVersions['thread-1']).toBeUndefined();
    });

    it('no-ops clear when thread has no versions', () => {
      const before = useThreadStore.getState().activeVersions;
      act(() => {
        useThreadStore.getState().clearActiveVersionsForThread('nonexistent');
      });

      expect(useThreadStore.getState().activeVersions).toBe(before);
    });
  });

  describe('deleteThread', () => {
    it('removes thread from list optimistically', async () => {
      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'T1', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
            { id: 't2', title: 'T2', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
        });
      });

      await act(async () => {
        await useThreadStore.getState().deleteThread('t1');
      });

      const threads = useThreadStore.getState().threads;
      expect(threads).toHaveLength(1);
      expect(threads[0].id).toBe('t2');
    });

    it('clears currentThread if deleting active thread', async () => {
      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'T1', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
          currentThreadId: 't1',
          currentMessages: [{ id: 'msg', conversation_id: 'c', role: 'user', content: 'hi', created_at: '' }],
        });
      });

      const wasCurrent = await act(async () => {
        return await useThreadStore.getState().deleteThread('t1');
      });

      expect(wasCurrent).toBe(true);
      expect(useThreadStore.getState().currentThreadId).toBeNull();
      expect(useThreadStore.getState().currentMessages).toEqual([]);
    });

    it('rolls back on API error', async () => {
      vi.mocked(api.deleteThread).mockRejectedValueOnce(new Error('Network error'));

      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'T1', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
        });
      });

      await expect(
        act(async () => {
          await useThreadStore.getState().deleteThread('t1');
        }),
      ).rejects.toThrow('Network error');

      // Thread should be back
      expect(useThreadStore.getState().threads).toHaveLength(1);
    });
  });

  describe('starThread', () => {
    it('optimistically toggles starred state', async () => {
      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'T1', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
        });
      });

      await act(async () => {
        await useThreadStore.getState().starThread('t1');
      });

      expect(useThreadStore.getState().threads[0].starred).toBe(true);
    });

    it('also updates currentThreadStarred when thread is active', async () => {
      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'T1', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
          currentThreadId: 't1',
          currentThreadStarred: false,
        });
      });

      await act(async () => {
        await useThreadStore.getState().starThread('t1');
      });

      expect(useThreadStore.getState().currentThreadStarred).toBe(true);
    });
  });

  describe('renameThread', () => {
    it('optimistically renames thread in list', async () => {
      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'Old Title', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
          currentThreadId: 't1',
          currentThreadTitle: 'Old Title',
        });
      });

      await act(async () => {
        await useThreadStore.getState().renameThread('t1', 'New Title');
      });

      expect(useThreadStore.getState().threads[0].title).toBe('New Title');
      expect(useThreadStore.getState().currentThreadTitle).toBe('New Title');
    });

    it('rolls back on API failure', async () => {
      vi.mocked(api.renameThread).mockRejectedValueOnce(new Error('fail'));

      act(() => {
        useThreadStore.setState({
          threads: [
            { id: 't1', title: 'Original', starred: false, project_id: null, last_message: null, created_at: '', updated_at: '' },
          ],
          currentThreadId: 't1',
          currentThreadTitle: 'Original',
        });
      });

      await expect(
        act(async () => {
          await useThreadStore.getState().renameThread('t1', 'New Name');
        }),
      ).rejects.toThrow('fail');

      expect(useThreadStore.getState().threads[0].title).toBe('Original');
      expect(useThreadStore.getState().currentThreadTitle).toBe('Original');
    });
  });

  describe('setSearchQuery', () => {
    it('updates the search query', () => {
      act(() => {
        useThreadStore.getState().setSearchQuery('test query');
      });

      expect(useThreadStore.getState().searchQuery).toBe('test query');
    });
  });

  describe('createThread', () => {
    it('calls API and returns thread ID', async () => {
      let threadId: string = '';
      await act(async () => {
        threadId = await useThreadStore.getState().createThread('My Thread');
      });

      expect(threadId).toBe('new-thread-id');
      expect(api.createThread).toHaveBeenCalledWith({
        thread_id: undefined,
        title: 'My Thread',
        project_id: undefined,
      });
    });
  });
});
