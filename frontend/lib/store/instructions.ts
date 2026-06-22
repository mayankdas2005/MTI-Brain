import { create } from 'zustand';
import {
  listInstructions,
  createInstruction,
  updateInstruction,
  deleteInstruction,
  type UserInstruction,
  type CreateInstructionPayload,
  type UpdateInstructionPayload,
} from '@/lib/api/instructions';

interface InstructionsStore {
  instructions: UserInstruction[];
  loading: boolean;
  lastFetched: number | null;

  fetchInstructions: () => Promise<void>;
  addInstruction: (payload: CreateInstructionPayload) => Promise<UserInstruction>;
  updateInstruction: (id: string, patch: UpdateInstructionPayload) => Promise<void>;
  removeInstruction: (id: string) => Promise<void>;
}

export const useInstructionsStore = create<InstructionsStore>()((set, get) => ({
  instructions: [],
  loading: false,
  lastFetched: null,

  fetchInstructions: async () => {
    set({ loading: true });
    try {
      const data = await listInstructions();
      set({ instructions: data, lastFetched: Date.now(), loading: false });
    } catch {
      set({ loading: false });
    }
  },

  addInstruction: async (payload) => {
    const created = await createInstruction(payload);
    set((s) => ({ instructions: [...s.instructions, created] }));
    return created;
  },

  updateInstruction: async (id, patch) => {
    const prev = get().instructions;
    set((s) => ({
      instructions: s.instructions.map((i) => (i.id === id ? { ...i, ...patch } : i)),
    }));
    try {
      const updated = await updateInstruction(id, patch);
      set((s) => ({
        instructions: s.instructions.map((i) => (i.id === id ? updated : i)),
      }));
    } catch (err) {
      set({ instructions: prev });
      throw err;
    }
  },

  removeInstruction: async (id) => {
    const prev = get().instructions;
    set((s) => ({ instructions: s.instructions.filter((i) => i.id !== id) }));
    try {
      await deleteInstruction(id);
    } catch (err) {
      set({ instructions: prev });
      throw err;
    }
  },
}));
