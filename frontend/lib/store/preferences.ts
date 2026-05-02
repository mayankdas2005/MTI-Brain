import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type ResponseTone = 'consultant' | 'operator' | 'brief';
export type DefaultDataView = 'sql' | 'table';

export type NotifyOnComplete = 'when-hidden' | 'off';

interface PreferencesState {
  responseTone: ResponseTone;
  showSQL: boolean;
  autoShowCharts: boolean;
  showFollowUps: boolean;
  defaultDataView: DefaultDataView;
  showReasoning: boolean;
  maxResultRows: number;
  /** Stream-completion notification policy. */
  notifyOnComplete: NotifyOnComplete;
  /** Play a soft ping alongside the notification. */
  notifySound: boolean;
  /** Set true once we've shown the soft permission prompt; prevents re-asks. */
  softPromptShown: boolean;
}

interface PreferencesActions {
  setResponseTone: (tone: ResponseTone) => void;
  setShowSQL: (show: boolean) => void;
  setAutoShowCharts: (show: boolean) => void;
  setShowFollowUps: (show: boolean) => void;
  setDefaultDataView: (view: DefaultDataView) => void;
  setShowReasoning: (show: boolean) => void;
  setMaxResultRows: (rows: number) => void;
  setNotifyOnComplete: (val: NotifyOnComplete) => void;
  setNotifySound: (val: boolean) => void;
  setSoftPromptShown: (val: boolean) => void;
  /** True after user-scoped preferences have been loaded. */
  hydrated: boolean;
  /** Re-load preferences for the current user from localStorage. */
  rehydrateForUser: (userId: string) => void;
}

type PreferencesStore = PreferencesState & PreferencesActions;

const DEFAULTS: PreferencesState = {
  responseTone: 'consultant',
  showSQL: true,
  autoShowCharts: true,
  showFollowUps: true,
  defaultDataView: 'table',
  showReasoning: true,
  maxResultRows: 100,
  notifyOnComplete: 'when-hidden',
  notifySound: true,
  softPromptShown: false,
};

const STORAGE_PREFIX = 'quest-prefs';

export const usePreferencesStore = create<PreferencesStore>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      hydrated: false,

      setResponseTone: (tone) => set({ responseTone: tone }),
      setShowSQL: (show) => set({ showSQL: show }),
      setAutoShowCharts: (show) => set({ autoShowCharts: show }),
      setShowFollowUps: (show) => set({ showFollowUps: show }),
      setDefaultDataView: (view) => set({ defaultDataView: view }),
      setShowReasoning: (show) => set({ showReasoning: show }),
      setMaxResultRows: (rows) => set({ maxResultRows: rows }),
      setNotifyOnComplete: (val) => set({ notifyOnComplete: val }),
      setNotifySound: (val) => set({ notifySound: val }),
      setSoftPromptShown: (val) => set({ softPromptShown: val }),

      rehydrateForUser: (userId: string) => {
        // Load this user's preferences from localStorage, fall back to defaults
        try {
          const raw = localStorage.getItem(`${STORAGE_PREFIX}:${userId}`);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (parsed?.state) {
              set({ ...DEFAULTS, ...parsed.state });
            }
          }
        } catch {
          // Corrupted data - use defaults
        }
        // Update the persist storage to write to this user's key going forward
        usePreferencesStore.persist.setOptions({
          name: `${STORAGE_PREFIX}:${userId}`,
        });
        // Re-trigger persist to save under the new key
        usePreferencesStore.persist.rehydrate();
        set({ hydrated: true });
      },
    }),
    {
      name: STORAGE_PREFIX, // initial key, updated per-user via rehydrateForUser
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
