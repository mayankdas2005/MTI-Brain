import { describe, it, expect } from 'vitest';
import {
  setThreadCreationGate,
  getThreadCreationGate,
  isThreadCreationPending,
} from '../threads';

describe('thread creation gate', () => {
  it('starts with no gate', () => {
    setThreadCreationGate(null);
    expect(getThreadCreationGate()).toBeNull();
    expect(isThreadCreationPending()).toBe(false);
  });

  it('sets a gate promise', () => {
    const gate = new Promise<void>((resolve) => setTimeout(resolve, 100));
    setThreadCreationGate(gate);

    expect(getThreadCreationGate()).toBe(gate);
    expect(isThreadCreationPending()).toBe(true);

    // Clean up
    setThreadCreationGate(null);
  });

  it('clears the gate', () => {
    const gate = Promise.resolve();
    setThreadCreationGate(gate);
    expect(isThreadCreationPending()).toBe(true);

    setThreadCreationGate(null);
    expect(getThreadCreationGate()).toBeNull();
    expect(isThreadCreationPending()).toBe(false);
  });
});
