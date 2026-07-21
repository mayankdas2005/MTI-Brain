import { describe, it, expect, beforeEach } from 'vitest';
import { act } from '@testing-library/react';
import { usePreferencesStore, PREFERENCES_DEFAULTS } from '../preferences';

describe('usePreferencesStore', () => {
  beforeEach(() => {
    act(() => {
      usePreferencesStore.setState({
        ...PREFERENCES_DEFAULTS,
        hydrated: false,
      });
    });
  });

  describe('initial state / defaults', () => {
    it('has correct default values', () => {
      const state = usePreferencesStore.getState();
      expect(state.responseTone).toBe('executive');
      expect(state.showSQL).toBe(true);
      expect(state.showData).toBe(true);
      expect(state.autoShowCharts).toBe(true);
      expect(state.showFollowUps).toBe(true);
      expect(state.defaultDataView).toBe('table');
      expect(state.showReasoning).toBe(true);
      expect(state.maxResultRows).toBe(100);
      expect(state.density).toBe('comfortable');
      expect(state.ttsRate).toBe(1);
      expect(state.highContrast).toBe(false);
      expect(state.deepAnalysis).toBe(false);
      expect(state.conversationMode).toBe(false);
      expect(state.thinkingPlacement).toBe('sidebar');
    });
  });

  describe('setResponseTone', () => {
    it('updates response tone', () => {
      act(() => {
        usePreferencesStore.getState().setResponseTone('manager');
      });
      expect(usePreferencesStore.getState().responseTone).toBe('manager');
    });

    it('accepts all valid tones', () => {
      const tones = ['analyst', 'manager', 'director', 'executive'] as const;
      for (const tone of tones) {
        act(() => {
          usePreferencesStore.getState().setResponseTone(tone);
        });
        expect(usePreferencesStore.getState().responseTone).toBe(tone);
      }
    });
  });

  describe('boolean toggles', () => {
    it('toggles showSQL', () => {
      act(() => {
        usePreferencesStore.getState().setShowSQL(false);
      });
      expect(usePreferencesStore.getState().showSQL).toBe(false);
    });

    it('toggles showData', () => {
      act(() => {
        usePreferencesStore.getState().setShowData(false);
      });
      expect(usePreferencesStore.getState().showData).toBe(false);
    });

    it('toggles autoShowCharts', () => {
      act(() => {
        usePreferencesStore.getState().setAutoShowCharts(false);
      });
      expect(usePreferencesStore.getState().autoShowCharts).toBe(false);
    });

    it('toggles showFollowUps', () => {
      act(() => {
        usePreferencesStore.getState().setShowFollowUps(false);
      });
      expect(usePreferencesStore.getState().showFollowUps).toBe(false);
    });

    it('toggles showReasoning', () => {
      act(() => {
        usePreferencesStore.getState().setShowReasoning(false);
      });
      expect(usePreferencesStore.getState().showReasoning).toBe(false);
    });

    it('toggles highContrast', () => {
      act(() => {
        usePreferencesStore.getState().setHighContrast(true);
      });
      expect(usePreferencesStore.getState().highContrast).toBe(true);
    });

    it('toggles deepAnalysis', () => {
      act(() => {
        usePreferencesStore.getState().setDeepAnalysis(true);
      });
      expect(usePreferencesStore.getState().deepAnalysis).toBe(true);
    });

    it('toggles conversationMode', () => {
      act(() => {
        usePreferencesStore.getState().setConversationMode(true);
      });
      expect(usePreferencesStore.getState().conversationMode).toBe(true);
    });
  });

  describe('setMaxResultRows', () => {
    it('updates max result rows', () => {
      act(() => {
        usePreferencesStore.getState().setMaxResultRows(500);
      });
      expect(usePreferencesStore.getState().maxResultRows).toBe(500);
    });
  });

  describe('setDefaultDataView', () => {
    it('switches to sql view', () => {
      act(() => {
        usePreferencesStore.getState().setDefaultDataView('sql');
      });
      expect(usePreferencesStore.getState().defaultDataView).toBe('sql');
    });

    it('switches to table view', () => {
      act(() => {
        usePreferencesStore.getState().setDefaultDataView('table');
      });
      expect(usePreferencesStore.getState().defaultDataView).toBe('table');
    });
  });

  describe('setDensity', () => {
    it('sets compact density', () => {
      act(() => {
        usePreferencesStore.getState().setDensity('compact');
      });
      expect(usePreferencesStore.getState().density).toBe('compact');
    });
  });

  describe('setTTSRate', () => {
    it('updates TTS rate', () => {
      act(() => {
        usePreferencesStore.getState().setTTSRate(1.5);
      });
      expect(usePreferencesStore.getState().ttsRate).toBe(1.5);
    });
  });

  describe('setTTSVoiceURI', () => {
    it('sets a voice URI', () => {
      act(() => {
        usePreferencesStore.getState().setTTSVoiceURI('Microsoft David');
      });
      expect(usePreferencesStore.getState().ttsVoiceURI).toBe('Microsoft David');
    });
  });

  describe('setThinkingPlacement', () => {
    it('switches to inline placement', () => {
      act(() => {
        usePreferencesStore.getState().setThinkingPlacement('inline');
      });
      expect(usePreferencesStore.getState().thinkingPlacement).toBe('inline');
    });
  });

  describe('notification preferences', () => {
    it('sets notify on complete policy', () => {
      act(() => {
        usePreferencesStore.getState().setNotifyOnComplete('off');
      });
      expect(usePreferencesStore.getState().notifyOnComplete).toBe('off');
    });

    it('sets notify sound', () => {
      act(() => {
        usePreferencesStore.getState().setNotifySound(false);
      });
      expect(usePreferencesStore.getState().notifySound).toBe(false);
    });

    it('sets soft prompt shown', () => {
      act(() => {
        usePreferencesStore.getState().setSoftPromptShown(true);
      });
      expect(usePreferencesStore.getState().softPromptShown).toBe(true);
    });
  });

  describe('resetToDefaults', () => {
    it('resets all settings to defaults', () => {
      // Change several settings
      act(() => {
        const s = usePreferencesStore.getState();
        s.setResponseTone('analyst');
        s.setShowSQL(false);
        s.setMaxResultRows(500);
        s.setDensity('compact');
        s.setDeepAnalysis(true);
      });

      act(() => {
        usePreferencesStore.getState().resetToDefaults();
      });

      const state = usePreferencesStore.getState();
      expect(state.responseTone).toBe('executive');
      expect(state.showSQL).toBe(true);
      expect(state.maxResultRows).toBe(100);
      expect(state.density).toBe('comfortable');
      expect(state.deepAnalysis).toBe(false);
    });

    it('preserves softPromptShown flag', () => {
      act(() => {
        usePreferencesStore.getState().setSoftPromptShown(true);
      });

      act(() => {
        usePreferencesStore.getState().resetToDefaults();
      });

      expect(usePreferencesStore.getState().softPromptShown).toBe(true);
    });
  });
});
