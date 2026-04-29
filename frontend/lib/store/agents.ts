import { create } from 'zustand';

export interface Agent {
  id: string;
  name: string;
  description: string;
  model: string;
  systemPrompt: string;
}

interface AgentStore {
  agents: Agent[];
  currentAgentId: string | null;
  
  setCurrentAgent: (id: string) => void;
  getCurrentAgent: () => Agent | null;
}

const defaultAgents: Agent[] = [
  {
    id: 'gpt-4',
    name: 'GPT-4',
    description: 'Most capable model for complex reasoning',
    model: 'openai/gpt-4-turbo',
    systemPrompt: 'You are a helpful AI assistant.',
  },
  {
    id: 'claude',
    name: 'Claude',
    description: 'Thoughtful and nuanced responses',
    model: 'anthropic/claude-opus-4.6',
    systemPrompt: 'You are a helpful AI assistant.',
  },
];

export const useAgentStore = create<AgentStore>((set, get) => ({
  agents: defaultAgents,
  currentAgentId: 'gpt-4',

  setCurrentAgent: (id: string) => {
    set({ currentAgentId: id });
  },

  getCurrentAgent: () => {
    const state = get();
    return state.agents.find((a) => a.id === state.currentAgentId) || null;
  },
}));
