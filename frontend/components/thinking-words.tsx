'use client';

import { useState, useEffect, useRef } from 'react';

/**
 * 55 professional ambient phrases — shown while the assistant is processing.
 * Intentionally generic so they never conflict with actual pipeline step messages
 * (e.g., "Resolving entities", "Validating SQL syntax", "Fetching results").
 */
const THINKING_PHRASES = [
  // Core analytical
  'Analyzing',
  'Evaluating',
  'Assessing',
  'Examining',
  'Interpreting',
  'Investigating',
  'Synthesizing',
  'Determining',
  'Calculating',
  'Consolidating',
  // Data-specific
  'Querying the data',
  'Running the numbers',
  'Cross-referencing tables',
  'Mapping relationships',
  'Validating results',
  'Aggregating metrics',
  'Filtering records',
  'Scanning for patterns',
  'Correlating data points',
  'Reconciling figures',
  // Process-oriented
  'Building the query',
  'Structuring the analysis',
  'Resolving entities',
  'Identifying key metrics',
  'Applying business rules',
  'Verifying data integrity',
  'Refining the approach',
  'Preparing the summary',
  'Compiling the results',
  'Formulating insights',
] as const;

// Rare easter egg phrases — ~3% chance per cycle
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
] as const;

interface ThinkingWordsProps {
  /** Cycle interval in ms (default 2200) */
  interval?: number;
}

export function ThinkingWords({ interval = 2200 }: ThinkingWordsProps) {
  const [index, setIndex] = useState(() =>
    Math.floor(Math.random() * THINKING_PHRASES.length),
  );
  const [visible, setVisible] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    timerRef.current = setInterval(() => {
      // Fade out → swap word → fade in
      setVisible(false);
      setTimeout(() => {
        setIndex((prev) => (prev + 1) % THINKING_PHRASES.length);
        setVisible(true);
      }, 250); // half of the CSS transition duration
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
        {Math.random() < 0.03
        ? RARE_PHRASES[Math.floor(Math.random() * RARE_PHRASES.length)]
        : THINKING_PHRASES[index]}
      </span>
      <span className="inline-flex gap-0.5">
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.3s]" />
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.15s]" />
        <span className="w-1 h-1 rounded-full bg-muted-foreground/50 animate-bounce" />
      </span>
    </span>
  );
}
