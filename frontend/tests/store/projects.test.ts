import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';

vi.mock('@/lib/api', () => ({
  listProjects: vi.fn().mockResolvedValue([]),
  createProject: vi.fn().mockResolvedValue({
    id: 'proj-new',
    name: 'New Project',
    description: null,
    starred: false,
    thread_count: 0,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  }),
  getProject: vi.fn().mockResolvedValue({
    id: 'proj-1',
    name: 'Project 1',
    description: 'Desc',
    starred: false,
    threads: [],
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  }),
  updateProject: vi.fn().mockResolvedValue(undefined),
  deleteProject: vi.fn().mockResolvedValue(undefined),
  starProject: vi.fn().mockResolvedValue({ starred: true }),
}));

import { useProjectStore } from '@/lib/store/projects';
import * as api from '@/lib/api';

describe('useProjectStore', () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.setState({
        projects: [],
        searchResults: [],
        searchLoading: false,
        loading: false,
        fetched: false,
        lastFetched: 0,
        searchQuery: '',
        currentProject: null,
        currentProjectLoading: false,
        projectDetailMap: {},
      });
    });
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('starts with empty projects list', () => {
      expect(useProjectStore.getState().projects).toEqual([]);
    });

    it('starts with no current project', () => {
      expect(useProjectStore.getState().currentProject).toBeNull();
    });

    it('starts with fetched as false', () => {
      expect(useProjectStore.getState().fetched).toBe(false);
    });
  });

  describe('createProject', () => {
    it('optimistically adds project to list', async () => {
      await act(async () => {
        await useProjectStore.getState().createProject('New Project');
      });

      const projects = useProjectStore.getState().projects;
      expect(projects).toHaveLength(1);
      expect(projects[0].name).toBe('New Project');
      expect(projects[0].id).toBe('proj-new');
    });

    it('passes description through', async () => {
      await act(async () => {
        await useProjectStore.getState().createProject('With Desc', 'A description');
      });

      expect(api.createProject).toHaveBeenCalledWith('With Desc', 'A description');
    });

    it('rolls back on API error', async () => {
      vi.mocked(api.createProject).mockRejectedValueOnce(new Error('Create failed'));

      await expect(
        act(async () => {
          await useProjectStore.getState().createProject('Fail Project');
        }),
      ).rejects.toThrow('Create failed');

      expect(useProjectStore.getState().projects).toEqual([]);
    });
  });

  describe('deleteProject', () => {
    it('removes project from list', async () => {
      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
            { id: 'proj-2', name: 'P2', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
        });
      });

      await act(async () => {
        await useProjectStore.getState().deleteProject('proj-1');
      });

      const projects = useProjectStore.getState().projects;
      expect(projects).toHaveLength(1);
      expect(projects[0].id).toBe('proj-2');
    });

    it('clears currentProject if it was deleted', async () => {
      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
          currentProject: { id: 'proj-1', name: 'P1', description: null, starred: false, threads: [], created_at: '', updated_at: '' },
        });
      });

      await act(async () => {
        await useProjectStore.getState().deleteProject('proj-1');
      });

      expect(useProjectStore.getState().currentProject).toBeNull();
    });

    it('evicts project from detail cache', async () => {
      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
          projectDetailMap: {
            'proj-1': { id: 'proj-1', name: 'P1', description: null, starred: false, threads: [], created_at: '', updated_at: '' },
          },
        });
      });

      await act(async () => {
        await useProjectStore.getState().deleteProject('proj-1');
      });

      expect(useProjectStore.getState().projectDetailMap['proj-1']).toBeUndefined();
    });

    it('rolls back on API error', async () => {
      vi.mocked(api.deleteProject).mockRejectedValueOnce(new Error('Delete failed'));

      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
        });
      });

      await expect(
        act(async () => {
          await useProjectStore.getState().deleteProject('proj-1');
        }),
      ).rejects.toThrow('Delete failed');

      expect(useProjectStore.getState().projects).toHaveLength(1);
    });
  });

  describe('updateProject', () => {
    it('optimistically updates name', async () => {
      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'Old Name', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
          currentProject: { id: 'proj-1', name: 'Old Name', description: null, starred: false, threads: [], created_at: '', updated_at: '' },
        });
      });

      await act(async () => {
        await useProjectStore.getState().updateProject('proj-1', 'New Name');
      });

      expect(useProjectStore.getState().projects[0].name).toBe('New Name');
      expect(useProjectStore.getState().currentProject?.name).toBe('New Name');
    });

    it('rolls back on API error', async () => {
      vi.mocked(api.updateProject).mockRejectedValueOnce(new Error('Update failed'));

      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'Original', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
        });
      });

      await expect(
        act(async () => {
          await useProjectStore.getState().updateProject('proj-1', 'Changed');
        }),
      ).rejects.toThrow('Update failed');

      expect(useProjectStore.getState().projects[0].name).toBe('Original');
    });
  });

  describe('starProject', () => {
    it('optimistically toggles starred state', async () => {
      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
        });
      });

      await act(async () => {
        await useProjectStore.getState().starProject('proj-1');
      });

      // After API resolves with { starred: true }, the final state is true
      expect(useProjectStore.getState().projects[0].starred).toBe(true);
    });

    it('updates currentProject starred state', async () => {
      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
          currentProject: { id: 'proj-1', name: 'P1', description: null, starred: false, threads: [], created_at: '', updated_at: '' },
        });
      });

      await act(async () => {
        await useProjectStore.getState().starProject('proj-1');
      });

      expect(useProjectStore.getState().currentProject?.starred).toBe(true);
    });

    it('rolls back on error', async () => {
      vi.mocked(api.starProject).mockRejectedValueOnce(new Error('Star failed'));

      act(() => {
        useProjectStore.setState({
          projects: [
            { id: 'proj-1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' },
          ],
        });
      });

      await expect(
        act(async () => {
          await useProjectStore.getState().starProject('proj-1');
        }),
      ).rejects.toThrow('Star failed');

      expect(useProjectStore.getState().projects[0].starred).toBe(false);
    });
  });

  describe('mutateProjectDetail', () => {
    it('applies updater to currentProject when matching', () => {
      act(() => {
        useProjectStore.setState({
          currentProject: { id: 'proj-1', name: 'P1', description: null, starred: false, threads: [], created_at: '', updated_at: '' },
          projectDetailMap: {
            'proj-1': { id: 'proj-1', name: 'P1', description: null, starred: false, threads: [], created_at: '', updated_at: '' },
          },
        });
      });

      act(() => {
        useProjectStore.getState().mutateProjectDetail('proj-1', (p) => ({
          ...p,
          name: 'Mutated',
        }));
      });

      expect(useProjectStore.getState().currentProject?.name).toBe('Mutated');
      expect(useProjectStore.getState().projectDetailMap['proj-1'].name).toBe('Mutated');
    });
  });

  describe('invalidateProjectDetail', () => {
    it('removes project from detail cache', () => {
      act(() => {
        useProjectStore.setState({
          projectDetailMap: {
            'proj-1': { id: 'proj-1', name: 'P1', description: null, starred: false, threads: [], created_at: '', updated_at: '' },
          },
        });
      });

      act(() => {
        useProjectStore.getState().invalidateProjectDetail('proj-1');
      });

      expect(useProjectStore.getState().projectDetailMap['proj-1']).toBeUndefined();
    });

    it('no-ops when project not in cache', () => {
      act(() => {
        useProjectStore.setState({ projectDetailMap: {} });
      });

      act(() => {
        useProjectStore.getState().invalidateProjectDetail('nonexistent');
      });

      expect(useProjectStore.getState().projectDetailMap).toEqual({});
    });
  });

  describe('setSearchQuery', () => {
    it('updates the search query', () => {
      act(() => {
        useProjectStore.getState().setSearchQuery('search term');
      });

      expect(useProjectStore.getState().searchQuery).toBe('search term');
    });

    it('clears searchResults when query is empty', () => {
      act(() => {
        useProjectStore.setState({
          searchResults: [{ id: 'p1', name: 'P1', description: null, starred: false, thread_count: 0, created_at: '', updated_at: '' }],
        });
      });

      act(() => {
        useProjectStore.getState().setSearchQuery('');
      });

      expect(useProjectStore.getState().searchResults).toEqual([]);
      expect(useProjectStore.getState().searchLoading).toBe(false);
    });
  });
});
