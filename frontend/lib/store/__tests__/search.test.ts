import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';

vi.mock('@/lib/api/threads', () => ({
  getRecents: vi.fn().mockResolvedValue([]),
}));

vi.mock('@/lib/api/projects', () => ({
  listProjects: vi.fn().mockResolvedValue([]),
}));

import { useSearchStore } from '../search';
import { getRecents } from '@/lib/api/threads';
import { listProjects } from '@/lib/api/projects';

describe('useSearchStore', () => {
  beforeEach(() => {
    act(() => {
      useSearchStore.setState({
        open: false,
        query: '',
        chatResults: [],
        projectResults: [],
        recentChats: [],
        loading: false,
      });
    });
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('starts closed', () => {
      expect(useSearchStore.getState().open).toBe(false);
    });

    it('starts with empty query', () => {
      expect(useSearchStore.getState().query).toBe('');
    });

    it('starts with empty results', () => {
      const state = useSearchStore.getState();
      expect(state.chatResults).toEqual([]);
      expect(state.projectResults).toEqual([]);
      expect(state.recentChats).toEqual([]);
    });
  });

  describe('openModal', () => {
    it('sets open to true and resets query', () => {
      act(() => {
        useSearchStore.setState({ query: 'old query' });
      });

      act(() => {
        useSearchStore.getState().openModal();
      });

      const state = useSearchStore.getState();
      expect(state.open).toBe(true);
      expect(state.query).toBe('');
      expect(state.chatResults).toEqual([]);
      expect(state.projectResults).toEqual([]);
    });

    it('loads recent chats on open', () => {
      act(() => {
        useSearchStore.getState().openModal();
      });

      expect(getRecents).toHaveBeenCalledWith({ limit: 8 });
    });
  });

  describe('closeModal', () => {
    it('closes the modal and clears results', () => {
      act(() => {
        useSearchStore.setState({
          open: true,
          query: 'test',
          chatResults: [{ thread_id: 't1', title: 'result', snippet: 'abc' }] as any,
          projectResults: [{ id: 'p1', name: 'proj' }] as any,
        });
      });

      act(() => {
        useSearchStore.getState().closeModal();
      });

      const state = useSearchStore.getState();
      expect(state.open).toBe(false);
      expect(state.query).toBe('');
      expect(state.chatResults).toEqual([]);
      expect(state.projectResults).toEqual([]);
    });
  });

  describe('search', () => {
    it('updates query', () => {
      act(() => {
        useSearchStore.getState().search('revenue');
      });

      expect(useSearchStore.getState().query).toBe('revenue');
    });

    it('clears results for queries shorter than 2 chars', () => {
      act(() => {
        useSearchStore.setState({
          chatResults: [{ thread_id: 't1' }] as any,
          projectResults: [{ id: 'p1' }] as any,
        });
      });

      act(() => {
        useSearchStore.getState().search('a');
      });

      const state = useSearchStore.getState();
      expect(state.chatResults).toEqual([]);
      expect(state.projectResults).toEqual([]);
      expect(state.loading).toBe(false);
    });

    it('sets loading state for valid queries', () => {
      act(() => {
        useSearchStore.getState().search('valid query');
      });

      expect(useSearchStore.getState().loading).toBe(true);
    });
  });
});
