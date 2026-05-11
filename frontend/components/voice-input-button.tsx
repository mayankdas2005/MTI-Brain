'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { Mic, MicOff } from 'lucide-react';
import { toast } from '@/lib/toast';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface VoiceInputButtonProps {
  onTranscript: (text: string, isFinal: boolean) => void;
  disabled?: boolean;
  className?: string;
}

function isSpeechRecognitionSupported(): boolean {
  if (typeof window === 'undefined') return false;
  if (!('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)) return false;
  return true;
}

function isSecureContext(): boolean {
  if (typeof window === 'undefined') return false;
  return window.isSecureContext;
}

interface SpeechRecognitionResultItem {
  transcript: string;
  confidence: number;
}
interface SpeechRecognitionResult {
  readonly length: number;
  readonly isFinal: boolean;
  item(index: number): SpeechRecognitionResultItem;
  [index: number]: SpeechRecognitionResultItem;
}
interface SpeechRecognitionResultList {
  readonly length: number;
  item(index: number): SpeechRecognitionResult;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEventLocal extends Event {
  readonly results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEventLocal extends Event {
  readonly error: string;
}

type SpeechRecognitionInstance = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((e: SpeechRecognitionEventLocal) => void) | null;
  onerror: ((e: SpeechRecognitionErrorEventLocal) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};
type SpeechRecognitionCtor = new () => SpeechRecognitionInstance;

function getSpeechRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  return (
    (window as unknown as { SpeechRecognition?: SpeechRecognitionCtor }).SpeechRecognition ??
    (window as unknown as { webkitSpeechRecognition?: SpeechRecognitionCtor }).webkitSpeechRecognition ??
    null
  );
}

function showSpeechError(error: string) {
  switch (error) {
    case 'not-allowed':
      toast.error('Microphone access is blocked. Check your browser permissions for this site and make sure the microphone is allowed.');
      break;
    case 'service-not-allowed':
      toast.error(
        window.location.protocol === 'http:'
          ? 'Voice input is not available over HTTP. It will work once the app is deployed on a secure connection.'
          : 'Voice input is currently unavailable. Make sure your microphone is allowed and speech recognition is enabled on your device.',
        { duration: 7000 },
      );
      break;
    case 'network':
      toast.error('Could not reach the voice service. Check your internet connection and try again.');
      break;
    case 'audio-capture':
      toast.error('No microphone found. Make sure one is connected and not being used by another app.');
      break;
    case 'no-speech':
      // silent - user just didn't say anything
      break;
    case 'aborted':
      // silent - user cancelled
      break;
    default:
      toast.error('Voice recognition failed. Please try again.');
  }
}

export function VoiceInputButton({ onTranscript, disabled, className = '' }: VoiceInputButtonProps) {
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const supported = isSpeechRecognitionSupported();

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
  }, []);

  const start = useCallback(() => {
    const SpeechRecognitionCtor = getSpeechRecognition();
    if (!SpeechRecognitionCtor) return;

    const rec = new SpeechRecognitionCtor();
    rec.lang = 'en-US';
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onstart = () => setListening(true);

    rec.onresult = (e: SpeechRecognitionEventLocal) => {
      const transcript = Array.from(e.results)
        .map((r) => r[0].transcript)
        .join('');
      const isFinal = e.results[e.results.length - 1].isFinal;
      onTranscript(transcript, isFinal);
    };

    rec.onerror = (e: SpeechRecognitionErrorEventLocal) => {
      setListening(false);
      showSpeechError(e.error);
    };

    rec.onend = () => setListening(false);

    recognitionRef.current = rec;
    rec.start();
  }, [onTranscript]);

  const toggle = useCallback(() => {
    if (listening) { stop(); } else { start(); }
  }, [listening, start, stop]);

  useEffect(() => {
    const onToggle = () => { toggle(); };
    const onStart = () => { if (!listening) start(); };
    const onStop = () => { if (listening) stop(); };
    window.addEventListener('mti-brain:toggle-voice', onToggle);
    window.addEventListener('mti-brain:start-voice', onStart);
    window.addEventListener('mti-brain:stop-voice', onStop);
    return () => {
      window.removeEventListener('mti-brain:toggle-voice', onToggle);
      window.removeEventListener('mti-brain:start-voice', onStart);
      window.removeEventListener('mti-brain:stop-voice', onStop);
      recognitionRef.current?.abort();
    };
  }, [toggle, listening, start, stop]);

  if (!supported) return null;

  const httpsRequired = !isSecureContext();

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={httpsRequired ? undefined : toggle}
          disabled={disabled || httpsRequired}
          aria-label={listening ? 'Stop recording' : 'Start voice input'}
          aria-pressed={listening}
          className={`relative flex items-center justify-center h-8 w-8 rounded-xl transition-spring disabled:opacity-30 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
            listening
              ? 'bg-destructive/15 text-destructive hover:bg-destructive/25'
              : 'text-muted-foreground hover:text-foreground hover:bg-accent'
          } ${className}`}
        >
          {listening ? (
            <>
              <MicOff className="w-4 h-4 relative z-10" />
              <span className="absolute inset-0 rounded-xl animate-ping bg-destructive/20" />
            </>
          ) : (
            <Mic className="w-4 h-4" />
          )}
        </button>
      </TooltipTrigger>
      {!listening && (
        <TooltipContent side="top" align="start">
          {httpsRequired
            ? 'Voice input requires a secure connection - will work when deployed'
            : 'Voice input'}
        </TooltipContent>
      )}
    </Tooltip>
  );
}
