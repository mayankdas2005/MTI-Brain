'use client';

import { useState, useEffect, useCallback } from 'react';

function stripToPlainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]+`/g, '')
    .replace(/^\|.+\|$/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function isTTSSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window;
}

// ─── Curated voice list ──────────────────────────────────────────────────────
// Ordered by quality. We pick the best available on this device, up to
// MAX_PER_GENDER per gender - keeps the settings dropdown short and clean.

const MAX_PER_GENDER = 3;

interface CuratedVoice {
  label: string;          // friendly display name shown in settings
  gender: 'female' | 'male';
  match: string[];        // substrings to match against SpeechSynthesisVoice.name (case-insensitive)
}

const CURATED: CuratedVoice[] = [
  // ── Female ──
  { label: 'Aria',    gender: 'female', match: ['microsoft aria', 'aria online'] },
  { label: 'Jenny',   gender: 'female', match: ['microsoft jenny', 'jenny online'] },
  { label: 'Samantha',gender: 'female', match: ['samantha'] },
  { label: 'Google Female', gender: 'female', match: ['google us english female', 'google uk english female'] },
  { label: 'Karen',   gender: 'female', match: ['karen'] },
  { label: 'Natasha', gender: 'female', match: ['microsoft natasha', 'natasha online'] },
  // ── Male ──
  { label: 'Guy',     gender: 'male',   match: ['microsoft guy', 'guy online'] },
  { label: 'Ryan',    gender: 'male',   match: ['microsoft ryan', 'ryan online'] },
  { label: 'Daniel',  gender: 'male',   match: ['daniel'] },
  { label: 'Google Male', gender: 'male', match: ['google us english male', 'google uk english male'] },
  { label: 'Alex',    gender: 'male',   match: ['alex'] },
  { label: 'James',   gender: 'male',   match: ['microsoft james', 'james online'] },
];

export type VoiceGender = 'female' | 'male';

export interface ResolvedVoice {
  label: string;
  gender: VoiceGender;
  voice: SpeechSynthesisVoice;
}

/** Returns up to MAX_PER_GENDER female + MAX_PER_GENDER male curated voices
 *  that are actually available on this device/browser. */
export function getCuratedVoices(): ResolvedVoice[] {
  if (!isTTSSupported()) return [];
  const available = speechSynthesis.getVoices();
  const result: ResolvedVoice[] = [];
  const countByGender: Record<VoiceGender, number> = { female: 0, male: 0 };

  for (const entry of CURATED) {
    if (countByGender[entry.gender] >= MAX_PER_GENDER) continue;
    const found = available.find((v) =>
      entry.match.some((m) => v.name.toLowerCase().includes(m)),
    );
    if (found) {
      result.push({ label: entry.label, gender: entry.gender, voice: found });
      countByGender[entry.gender]++;
    }
  }
  return result;
}

/** Pick the best available voice for auto mode. */
export function pickDefaultVoice(): SpeechSynthesisVoice | null {
  const curated = getCuratedVoices();
  // Prefer first female, then first male, then any English voice
  const female = curated.find((v) => v.gender === 'female');
  if (female) return female.voice;
  const male = curated.find((v) => v.gender === 'male');
  if (male) return male.voice;
  // Last resort: any English voice
  return speechSynthesis.getVoices().find((v) => v.lang.startsWith('en')) ?? null;
}

/** Kept for backward compat with settings page import. */
export function detectGender(voice: SpeechSynthesisVoice): VoiceGender | 'unknown' {
  const name = voice.name.toLowerCase();
  const entry = CURATED.find((c) => c.match.some((m) => name.includes(m)));
  return entry?.gender ?? 'unknown';
}

// ─── Hook ────────────────────────────────────────────────────────────────────

export function useTTS(rate: number = 1, voiceURI: string = '') {
  const [isSpeaking, setIsSpeaking] = useState(false);

  useEffect(() => {
    if (!isTTSSupported()) return;
    return () => { speechSynthesis.cancel(); };
  }, []);

  const speak = useCallback(
    (text: string, onEnd?: () => void) => {
      if (!isTTSSupported()) return;
      speechSynthesis.cancel();
      const plain = stripToPlainText(text);
      if (!plain) return;
      const utterance = new SpeechSynthesisUtterance(plain);
      utterance.rate = rate;

      const voices = speechSynthesis.getVoices();
      const selected = voiceURI
        ? voices.find((v) => v.voiceURI === voiceURI) ?? pickDefaultVoice()
        : pickDefaultVoice();
      if (selected) utterance.voice = selected;

      utterance.onstart = () => setIsSpeaking(true);
      utterance.onend = () => { setIsSpeaking(false); onEnd?.(); };
      utterance.onerror = () => { setIsSpeaking(false); onEnd?.(); };
      speechSynthesis.speak(utterance);
    },
    [rate, voiceURI],
  );

  const stop = useCallback(() => {
    if (!isTTSSupported()) return;
    speechSynthesis.cancel();
    setIsSpeaking(false);
  }, []);

  return { speak, stop, isSpeaking };
}

/** Hook that reactively returns the curated voice list (fires on voiceschanged). */
export function useAvailableVoices(): ResolvedVoice[] {
  const [voices, setVoices] = useState<ResolvedVoice[]>([]);

  useEffect(() => {
    if (!isTTSSupported()) return;
    const load = () => setVoices(getCuratedVoices());
    load();
    speechSynthesis.addEventListener('voiceschanged', load);
    return () => speechSynthesis.removeEventListener('voiceschanged', load);
  }, []);

  return voices;
}
