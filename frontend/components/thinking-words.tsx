'use client';

import { useState, useEffect, useRef } from 'react';

/**
 * Ambient phrases shown while the assistant is processing.
 * Treasury / text-to-SQL flavored - intentionally distinct from actual
 * pipeline step messages so they never conflict.
 */
const THINKING_PHRASES = [
  // Cognitive
  'Thinking',
  'Analyzing',
  'Evaluating',
  'Reasoning',
  'Processing',
  'Interpreting',
  'Synthesizing',
  'Considering',

  // Data & query
  'Querying the data',
  'Running the numbers',
  'Cross-referencing tables',
  'Mapping relationships',
  'Aggregating metrics',
  'Scanning for patterns',
  'Correlating data points',
  'Reconciling figures',
  'Crunching the numbers',
  'Digging into the data',
  'Connecting the dots',
  'Piecing it together',

  // Treasury-flavored ambient
  'Reviewing the portfolio',
  'Checking exposures',
  'Assessing positions',
  'Analyzing cash flows',
  'Surveying the landscape',
  'Tracing the flows',
  'Sizing up the numbers',
  'Scanning the books',

  // Process
  'Working on it',
  'Pulling it together',
  'Refining the approach',
  'Formulating insights',
  'Structuring the analysis',
  'Compiling results',
  'Almost there',
] as const;

// Rare easter egg phrases - ~3 % chance per cycle
const RARE_PHRASES = [
  'Checking the ledger...',
  'Consulting the treasury desk...',
  'Running the FX exposure...',
  'Asking the CFO...',
  'Stress-testing the forecast...',
  'Reconciling the books...',
  'Scanning the swap portfolio...',
  'Wiring the answer...',
  'Checking counterparty limits...',
  'Hedging for accuracy...',
  'Balancing the books...',
  'Calling the back office...',
  'Pinging the data warehouse...',
  'Double-checking the decimals...',
  'Auditing the numbers...',
] as const;

interface ThinkingWordsProps {
  /** Cycle interval in ms (default 2 200) */
  interval?: number;
}

export function ThinkingWords({ interval = 2200 }: ThinkingWordsProps) {
  const [phrase, setPhrase] = useState<string>(
    () => THINKING_PHRASES[Math.floor(Math.random() * THINKING_PHRASES.length)],
  );
  const [visible, setVisible] = useState(true);
  const indexRef = useRef(Math.floor(Math.random() * THINKING_PHRASES.length));
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    timerRef.current = setInterval(() => {
      // Fade out → pick next phrase → fade in
      setVisible(false);
      setTimeout(() => {
        indexRef.current = (indexRef.current + 1) % THINKING_PHRASES.length;
        const next =
          Math.random() < 0.03
            ? RARE_PHRASES[Math.floor(Math.random() * RARE_PHRASES.length)]
            : THINKING_PHRASES[indexRef.current];
        setPhrase(next);
        setVisible(true);
      }, 250);
    }, interval);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [interval]);

  return (
    <span className="inline-flex items-center gap-2 text-muted-foreground">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
      </span>
      <span
        className="text-sm transition-opacity duration-300 ease-in-out"
        style={{ opacity: visible ? 1 : 0 }}
      >
        {phrase}
      </span>
      <span className="inline-flex gap-0.5">
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.3s]" />
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.15s]" />
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce" />
      </span>
    </span>
  );
}
