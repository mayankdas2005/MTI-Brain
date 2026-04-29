'use client';

import { useState, useEffect } from 'react';
import { useThreadStore } from '@/lib/store/threads';
import { getStoredUser } from '@/lib/auth';
import { Landmark, TrendingUp, AlertTriangle, BarChart3 } from 'lucide-react';

const suggestions = [
  {
    icon: Landmark,
    label: 'Cash position',
    prompt: 'What is our total cash balance across all bank accounts as of yesterday?',
  },
  {
    icon: TrendingUp,
    label: 'Forecast variance',
    prompt: 'Compare our actual cash inflows and forecasts for the past 30 days by entity and highlight variances greater than 10%.',
  },
  {
    icon: AlertTriangle,
    label: 'FX exposure',
    prompt: 'List all FX exposures by currency pair for our international subsidiaries and show the net position vs hedged amount.',
  },
  {
    icon: BarChart3,
    label: 'Liquidity stress test',
    prompt: 'Run a stress test on our liquidity position assuming a 20% drop in daily receipts for 30 days and show which entities would breach minimum operating cash thresholds first.',
  },
];

const TAGLINES: string[] = [
  'What would you like to know',
  'Ask anything about your treasury data',
  'What are you looking into today',
  'What can I help you find',
  'Your data is ready when you are',
  'Go ahead, ask the hard question',
  'What should we look at first',
  'The numbers are in. What do you need',
  'Where would you like to start',
  'What decision can I help you make',
  'What matters most right now',
  'Curious about your cash position',
];

function getGreeting(): string {
  const now = new Date();
  const hour = now.getHours();
  const isFriday = now.getDay() === 5;
  if (hour < 3) return 'Working late';
  if (hour < 5) return 'You\'re up early';
  if (hour < 12) return isFriday ? 'Happy Friday morning' : 'Good morning';
  if (hour < 17) return isFriday ? 'Happy Friday afternoon' : 'Good afternoon';
  if (hour < 21) return isFriday ? 'Happy Friday evening' : 'Good evening';
  return 'Working late';
}

function pickTagline(): string {
  return TAGLINES[Math.floor(Math.random() * TAGLINES.length)];
}

interface WelcomeStateProps {
  onSuggestion?: (prompt: string) => void;
}

export function WelcomeState({ onSuggestion }: WelcomeStateProps = {}) {
  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const askQuestion = useThreadStore((s) => s.askQuestion);
  const isStreaming = useThreadStore((s) => s.isStreaming);

  const [firstName, setFirstName] = useState<string | undefined>(undefined);
  const [greeting, setGreeting] = useState('');
  const [tagline, setTagline] = useState('');

  useEffect(() => {
    const user = getStoredUser();
    setFirstName(user?.name?.split(' ')[0]);
    setGreeting(getGreeting());
    setTagline(pickTagline());
  }, []);

  const handleSuggestion = (prompt: string) => {
    if (onSuggestion) {
      onSuggestion(prompt);
      return;
    }
    if (!currentThreadId || isStreaming) return;
    askQuestion(currentThreadId, prompt);
  };

  return (
    <div className="flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-2xl space-y-6">
        {/* Greeting */}
        <div className="text-center space-y-2 animate-fade-up">
          {firstName && (
            <p className="text-sm text-muted-foreground" suppressHydrationWarning>
              {greeting || 'Hello'}, {firstName}
            </p>
          )}
          <h1 className="text-3xl font-semibold tracking-tight text-foreground" suppressHydrationWarning>
            {tagline || 'What would you like to know'}?
          </h1>
        </div>

        {/* Suggestion chips */}
        <div className="flex flex-wrap items-center justify-center gap-2">
          {suggestions.map((s, i) => {
            const Icon = s.icon;
            return (
              <button
                key={i}
                onClick={() => handleSuggestion(s.prompt)}
                disabled={isStreaming}
                className="animate-fade-up group inline-flex items-center gap-2 rounded-full border border-border bg-background px-4 py-2 text-sm transition-all duration-150 hover:bg-accent hover:border-primary/20 disabled:opacity-50"
                style={{ animationDelay: `${(i + 1) * 80}ms` }}
              >
                <Icon className="w-3.5 h-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
                <span className="text-foreground/80 group-hover:text-foreground transition-colors">
                  {s.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
