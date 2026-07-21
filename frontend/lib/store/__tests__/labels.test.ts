import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';

vi.mock('@/lib/api/client', () => ({
  apiFetch: vi.fn(),
}));

import { useLabelsStore, LABEL_COLORS } from '../labels';
import { apiFetch } from '@/lib/api/client';

describe('useLabelsStore', () => {
  beforeEach(() => {
    act(() => {
      useLabelsStore.setState({
        byThread: {},
        fetched: false,
      });
    });
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('starts with empty labels', () => {
      expect(useLabelsStore.getState().byThread).toEqual({});
    });

    it('starts with fetched as false', () => {
      expect(useLabelsStore.getState().fetched).toBe(false);
    });
  });

  describe('fetchAllLabels', () => {
    it('fetches and groups labels by thread', async () => {
      vi.mocked(apiFetch).mockResolvedValueOnce([
        { id: 'l1', thread_id: 't1', label: 'Priority', color: 'red', created_at: '2024-01-01' },
        { id: 'l2', thread_id: 't1', label: 'Review', color: 'blue', created_at: '2024-01-01' },
        { id: 'l3', thread_id: 't2', label: 'Done', color: 'green', created_at: '2024-01-01' },
      ]);

      await act(async () => {
        await useLabelsStore.getState().fetchAllLabels();
      });

      const state = useLabelsStore.getState();
      expect(state.fetched).toBe(true);
      expect(state.byThread['t1']).toHaveLength(2);
      expect(state.byThread['t2']).toHaveLength(1);
    });

    it('does not refetch when already fetched', async () => {
      act(() => {
        useLabelsStore.setState({ fetched: true });
      });

      await act(async () => {
        await useLabelsStore.getState().fetchAllLabels();
      });

      expect(apiFetch).not.toHaveBeenCalled();
    });
  });

  describe('addLabel', () => {
    it('adds label to the thread', async () => {
      const newLabel = { id: 'l-new', thread_id: 't1', label: 'Important', color: 'red', created_at: '2024-01-01' };
      vi.mocked(apiFetch).mockResolvedValueOnce(newLabel);

      await act(async () => {
        await useLabelsStore.getState().addLabel('t1', 'Important', 'red');
      });

      expect(useLabelsStore.getState().byThread['t1']).toContainEqual(newLabel);
    });

    it('appends to existing labels for a thread', async () => {
      act(() => {
        useLabelsStore.setState({
          byThread: {
            't1': [{ id: 'l1', thread_id: 't1', label: 'Existing', color: 'blue', created_at: '2024-01-01' }],
          },
        });
      });

      const newLabel = { id: 'l2', thread_id: 't1', label: 'New', color: 'green', created_at: '2024-01-02' };
      vi.mocked(apiFetch).mockResolvedValueOnce(newLabel);

      await act(async () => {
        await useLabelsStore.getState().addLabel('t1', 'New', 'green');
      });

      expect(useLabelsStore.getState().byThread['t1']).toHaveLength(2);
    });
  });

  describe('removeLabel', () => {
    it('removes label from thread optimistically', async () => {
      act(() => {
        useLabelsStore.setState({
          byThread: {
            't1': [
              { id: 'l1', thread_id: 't1', label: 'Keep', color: 'blue', created_at: '2024-01-01' },
              { id: 'l2', thread_id: 't1', label: 'Remove', color: 'red', created_at: '2024-01-01' },
            ],
          },
        });
      });

      vi.mocked(apiFetch).mockResolvedValueOnce(undefined);

      await act(async () => {
        await useLabelsStore.getState().removeLabel('l2', 't1');
      });

      const labels = useLabelsStore.getState().byThread['t1'];
      expect(labels).toHaveLength(1);
      expect(labels[0].id).toBe('l1');
    });

    it('rolls back on API error', async () => {
      act(() => {
        useLabelsStore.setState({
          byThread: {
            't1': [
              { id: 'l1', thread_id: 't1', label: 'Keep', color: 'blue', created_at: '2024-01-01' },
            ],
          },
        });
      });

      vi.mocked(apiFetch).mockRejectedValueOnce(new Error('Delete failed'));

      await act(async () => {
        await useLabelsStore.getState().removeLabel('l1', 't1');
      });

      // Should roll back
      expect(useLabelsStore.getState().byThread['t1']).toHaveLength(1);
    });
  });
});

describe('LABEL_COLORS', () => {
  it('has 6 color options', () => {
    expect(LABEL_COLORS).toHaveLength(6);
  });

  it('each color has name, bg, text, and dot', () => {
    for (const color of LABEL_COLORS) {
      expect(color.name).toBeTruthy();
      expect(color.bg).toBeTruthy();
      expect(color.text).toBeTruthy();
      expect(color.dot).toBeTruthy();
    }
  });
});
