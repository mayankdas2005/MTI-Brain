import { create } from 'zustand';

interface ThinkingStore {
  enableDeepThinking: boolean;
  isThinking: boolean;
  thinkingContent: string;
  
  setEnableDeepThinking: (enabled: boolean) => void;
  setIsThinking: (thinking: boolean) => void;
  setThinkingContent: (content: string) => void;
  clearThinking: () => void;
}

export const useThinkingStore = create<ThinkingStore>((set) => ({
  enableDeepThinking: false,
  isThinking: false,
  thinkingContent: '',

  setEnableDeepThinking: (enabled: boolean) => {
    set({ enableDeepThinking: enabled });
  },

  setIsThinking: (thinking: boolean) => {
    set({ isThinking: thinking });
  },

  setThinkingContent: (content: string) => {
    set({ thinkingContent: content });
  },

  clearThinking: () => {
    set({ thinkingContent: '', isThinking: false });
  },
}));
