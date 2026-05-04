import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type ResponseTone = 'consultant' | 'operator' | 'brief';
export type DefaultDataView = 'sql' | 'table';
export type Density = 'comfortable' | 'compact';

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
  /** Display density — applied to <html data-density> via a Providers-level
   *  effect. Drives the --density-* CSS variables in globals.css. */
  density: Density;
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
  setDensity: (density: Density) => void;
  /** Reset every persisted preference back to its DEFAULT value. The
   *  `softPromptShown` flag is preserved — it tracks whether we've ever
   *  shown the notification permission soft-prompt and resetting it
   *  would re-trigger that prompt for no reason. */
  resetToDefaults: () => void;
  /** True after user-scoped preferences have been loaded. */
  hydrated: boolean;
  /** Re-load preferences for the current user from localStorage. */
  rehydrateForUser: (userId: string) => void;
}

type PreferencesStore = PreferencesState & PreferencesActions;

export const PREFERENCES_DEFAULTS: PreferencesState = {
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
  density: 'comfortable',
};

const STORAGE_PREFIX = 'mti-brain-prefs';

export const usePreferencesStore = create<PreferencesStore>()(
  persist(
    (set) => ({
      ...PREFERENCES_DEFAULTS,
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
      setDensity: (density) => set({ density }),

      resetToDefaults: () =>
        set((state) => ({
          ...PREFERENCES_DEFAULTS,
          // preserve flags that aren't user-facing settings
          softPromptShown: state.softPromptShown,
          hydrated: state.hydrated,
        })),

      rehydrateForUser: (userId: string) => {
        // Load this user's preferences from localStorage, fall back to defaults
        try {
          const raw = localStorage.getItem(`${STORAGE_PREFIX}:${userId}`);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (parsed?.state) {
              set({ ...PREFERENCES_DEFAULTS, ...parsed.state });
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
