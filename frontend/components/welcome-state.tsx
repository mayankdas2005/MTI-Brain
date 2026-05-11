'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore } from '@/lib/store/threads';
import { getStoredUser } from '@/lib/auth';
import { MessageSquare, ArrowRight } from 'lucide-react';
import { type Suggestion, pickSuggestions } from '@/lib/suggestions';
import { usePinnedMetricsStore } from '@/lib/store/pinned-metrics';
import { MetricPinCard } from './metric-pin-card';

// ─── Taglines: time-of-day pools ───
// Each time slot has its own pool so the vibe matches the moment.
// A universal pool mixes in 40% of the time for variety.

const TAGLINES_MORNING: string[] = [
  'Fresh day, fresh numbers',
  'Your morning briefing starts here',
  'Coffee\u2019s ready. So is your data',
  'Start with the question that matters most',
  'The markets opened. Time to catch up',
  'A new day. A sharper lens on your data',
  'Morning clarity, powered by data',
  'First light. First insights',
  'Before the day runs away from you',
  'The early ask gets the early answer',
  'Set the tone before the noise starts',
  'Your numbers don\u2019t sleep. Neither should your decisions',
  'Get ahead of today\u2019s first surprises',
  'What if your forecast is already wrong?',
  'What moved while you were away?',
  'Where does the money actually go?',
  'What would change if you had perfect visibility?',
  'What\u2019s worth knowing before anything else?',
  'What would you want to know before your first meeting?',
  'What\u2019s the first thing you\u2019d ask if you could ask anything?',
];

const TAGLINES_AFTERNOON: string[] = [
  'Turning data into decisions',
  'Time for a midday check-in',
  'Afternoon focus. Sharper answers',
  'Making this afternoon count',
  'Half the day down. Keep the momentum',
  'Decisions don\u2019t wait. Neither should you',
  'Clear thinking for the second half',
  'The afternoon is when decisions get made',
  'Stay sharp through the second half',
  'Your competitive edge is a question away',
  'Cut through the afternoon noise',
  'The data is up to date. Are your decisions?',
  'What does the data know that you don\u2019t yet?',
  'Where is risk hiding in plain sight?',
  'What\u2019s drifting from the plan?',
  'What\u2019s the real story behind the numbers?',
  'What would your CFO want to see right now?',
  'What\u2019s the one thing that could change your afternoon?',
  'What would you regret not checking today?',
];

const TAGLINES_EVENING: string[] = [
  'Wrapping up the day with clarity',
  'One last look before you go',
  'Closing the loop on today',
  'Tomorrow\u2019s answers, tonight',
  'End the day sharper than you started',
  'Evening review. No surprises tomorrow',
  'The day\u2019s almost done. Finish strong',
  'Tying up loose ends',
  'Leave nothing unexamined',
  'The best close-of-day is an informed one',
  'Tomorrow starts with what you know tonight',
  'Finish the day on your terms',
  'One question can change tomorrow\u2019s outcome',
  'What do you wish you\u2019d caught earlier?',
  'What is carrying over that shouldn\u2019t be?',
  'What would tomorrow look like without the guesswork?',
  'What\u2019s still unresolved?',
  'What does closing the day actually look like?',
  'What\u2019s the one thing worth double-checking before you leave?',
  'What would a fresh set of eyes see in today\u2019s numbers?',
];

const TAGLINES_NIGHT: string[] = [
  'Burning the midnight oil',
  'The quiet hours. The best thinking hours',
  'Late nights make early wins',
  'While the world sleeps, you\u2019re ahead',
  'Night owl mode. Full focus',
  'No rush. Take your time with this one',
  'The best insights come after midnight',
  'Working late never looked this smart',
  'The stillness is yours. Use it',
  'Some questions deserve the quiet',
  'Late nights, clear answers',
  'While others rest, you\u2019re building the edge',
  'What can\u2019t wait until morning?',
  'What would you want waiting for you at sunrise?',
  'What\u2019s worth losing sleep over?',
  'What if you had the answer before anyone else did?',
  'What would tomorrow look like if you answered this tonight?',
  'What\u2019s the question you keep putting off?',
];

const TAGLINES_ANYTIME: string[] = [
  'Your treasury advisor is ready',
  'Answers your spreadsheet can\u2019t give you',
  'Your data is ready when you are',
  'Every answer starts with a question',
  'Your real-time financial co-pilot',
  'One question away from clarity',
  'Skip the report. Just ask',
  'Real-time answers. No waiting',
  'Your data, distilled',
  'Smarter decisions start here',
  'From raw data to real insight',
  'Built for the questions that matter',
  'Clarity at the speed of thought',
  'Intelligence on demand',
  'The signal in your data, surfaced',
  'Precision answers. Zero fluff',
  'Trusted intelligence. Every time',
  'The advisor that never clocks out',
  'Enterprise data. Human decisions',
  'From signals to strategy',
  'What does your treasury actually know?',
  'What if the forecast is the problem?',
  'What would perfect liquidity feel like?',
  'What\u2019s the cost of not knowing?',
  'What is your data trying to tell you?',
  'Where is the decision hiding?',
  'What if the risk isn\u2019t where you think it is?',
  'What would change if you could see everything at once?',
  'What\u2019s the question behind the question?',
  'What does your exposure really look like?',
  'What would you do with one more hour of runway?',
  'What does good look like in your cash position?',
  'What would your data say if it could speak?',
  'What\u2019s the most important number you haven\u2019t looked at today?',
];



// ─── Greetings: day + time aware with multiple options per slot ───

const GREETINGS: Record<string, string[]> = {
  'mon_early':     ['You\u2019re up before the market', 'Early bird, Monday edition', 'The week starts early for you', 'Monday hasn\u2019t woken up yet', 'First one in, as usual'],
  'mon_morning':   ['Let\u2019s start the week strong', 'Monday morning. Let\u2019s go', 'New week, new insights', 'Monday\u2019s yours to shape', 'Fresh week. Fresh lens'],
  'mon_afternoon': ['Monday\u2019s moving fast', 'Good afternoon', 'Halfway through Monday', 'The week is already in motion', 'Monday afternoon, still going strong'],
  'mon_evening':   ['Still going strong on Monday', 'Burning the Monday oil', 'You made it through Monday', 'One day down, four to go', 'Monday\u2019s behind you'],
  'mon_night':     ['Monday night deep dive', 'Working late on Monday', 'The week starts now', 'You don\u2019t stop on Mondays', 'Late Monday, early edge'],

  'tue_early':     ['You\u2019re up early', 'Early start to Tuesday', 'Tuesday\u2019s just beginning', 'Two days in, already ahead', 'Up before the market again'],
  'tue_morning':   ['Good morning', 'Tuesday\u2019s off to a good start', 'Morning check-in', 'Tuesday momentum building', 'Another day, sharper focus'],
  'tue_afternoon': ['Good afternoon', 'Tuesday afternoon', 'Tuesday\u2019s in full swing', 'Midweek energy kicking in', 'Still pushing on Tuesday'],
  'tue_evening':   ['Good evening', 'Wrapping up Tuesday', 'Tuesday done well', 'Another solid day behind you', 'Tuesday\u2019s almost yours'],
  'tue_night':     ['Working late', 'Burning the midnight oil', 'Tuesday night focus', 'The quiet hours are yours', 'Late night, clear head'],

  'wed_early':     ['Midweek, early start', 'You\u2019re up before the sun', 'Hump day hasn\u2019t started yet', 'Midweek, early mover', 'Wednesday before the world wakes'],
  'wed_morning':   ['Good morning', 'Halfway through the week', 'Happy hump day', 'Wednesday. The pivot point', 'Midweek and moving'],
  'wed_afternoon': ['Good afternoon', 'Midweek momentum', 'Wednesday afternoon', 'Over the hump and going', 'Midweek clarity setting in'],
  'wed_evening':   ['Good evening', 'Wednesday evening', 'Halfway home', 'Midweek wind-down', 'Two more days to make it count'],
  'wed_night':     ['Midweek midnight oil', 'Working late', 'Wednesday night deep work', 'Quiet midweek hours', 'The middle of the week, the middle of the night'],

  'thu_early':     ['Almost Friday. Almost', 'You\u2019re up early', 'Thursday before the sun', 'One day from Friday', 'Close enough to almost taste it'],
  'thu_morning':   ['Good morning', 'Thursday morning', 'One more day after this', 'Thursday. The final push', 'Almost at the finish line'],
  'thu_afternoon': ['Good afternoon', 'Thursday\u2019s flying by', 'Weekend almost in sight', 'Thursday afternoon focus', 'One day left to make it count'],
  'thu_evening':   ['Good evening', 'Thursday evening wind-down', 'Almost there', 'Friday is just hours away', 'Thursday done. One more to go'],
  'thu_night':     ['Working late on Thursday', 'Late night', 'Thursday night. Last push of the week', 'Almost Friday', 'Finishing strong before the weekend'],

  'fri_early':     ['Friday already', 'Up early on a Friday', 'Friday before the world wakes up', 'The last early morning of the week', 'Friday. Earned'],
  'fri_morning':   ['Happy Friday', 'TGIF morning', 'Friday. Let\u2019s finish strong', 'Last day. Make it count', 'Friday energy. Full send'],
  'fri_afternoon': ['Happy Friday afternoon', 'Weekend\u2019s almost here', 'Friday afternoon clarity', 'Almost at the finish line', 'Close the week strong'],
  'fri_evening':   ['Still at it on Friday', 'Friday evening', 'Dedicated. Even on Fridays', 'The weekend can wait a little longer', 'Wrapping up the week right'],
  'fri_night':     ['Friday night and still working', 'Weekend can wait', 'Friday night deep work', 'The committed ones don\u2019t stop on Fridays', 'The week ends when you say it does'],

  'weekend_morning':   ['Weekend morning', 'Taking the weekend shift', 'Even weekends need clarity', 'The weekend doesn\u2019t stop the work', 'Early bird, weekend edition'],
  'weekend_afternoon': ['Weekend warrior mode', 'Working the weekend', 'Weekend dedication', 'The work doesn\u2019t take weekends off', 'Weekend momentum'],
  'weekend_evening':   ['Weekend evening', 'Quiet weekend session', 'Weekend wind-down', 'Sunday clarity', 'Weekend reflection time'],
  'weekend_night':     ['Weekend late night', 'Burning weekend midnight oil', 'Weekend dedication runs deep', 'No days off for the focused', 'Weekend nights build weekday edges'],
};

const DAY_NAMES = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const;

function getGreeting(): string {
  const now = new Date();
  const hour = now.getHours();
  const day = now.getDay();

  let timeSlot: string;
  if (hour < 4) timeSlot = 'night';       // midnight - 3:59 AM
  else if (hour < 7) timeSlot = 'early';   // 4 AM - 6:59 AM
  else if (hour < 12) timeSlot = 'morning'; // 7 AM - 11:59 AM
  else if (hour < 17) timeSlot = 'afternoon'; // noon - 4:59 PM
  else if (hour < 21) timeSlot = 'evening'; // 5 PM - 8:59 PM
  else timeSlot = 'night';                 // 9 PM - 11:59 PM

  let key: string;
  if (day === 0 || day === 6) {
    key = `weekend_${timeSlot === 'early' ? 'night' : timeSlot}`;
  } else {
    key = `${DAY_NAMES[day]}_${timeSlot}`;
  }

  const pool = GREETINGS[key] || ['Hello'];
  return pool[Math.floor(Math.random() * pool.length)];
}

function pickTagline(): string {
  const now = new Date();
  const hour = now.getHours();

  let timePool: string[];
  if (hour < 4) timePool = TAGLINES_NIGHT;           // midnight - 3:59 AM
  else if (hour < 7) timePool = TAGLINES_MORNING;     // 4 AM - 6:59 AM (early birds get morning energy)
  else if (hour < 12) timePool = TAGLINES_MORNING;    // 7 AM - 11:59 AM
  else if (hour < 17) timePool = TAGLINES_AFTERNOON;   // noon - 4:59 PM
  else if (hour < 21) timePool = TAGLINES_EVENING;     // 5 PM - 8:59 PM
  else timePool = TAGLINES_NIGHT;                     // 9 PM - 11:59 PM

  // 60% chance time-specific, 40% chance universal - keeps it varied
  const pool = Math.random() < 0.6 ? timePool : TAGLINES_ANYTIME;
  return pool[Math.floor(Math.random() * pool.length)];
}

interface WelcomeStateProps {
  onSuggestion?: (prompt: string) => void;
}

export function WelcomeState({ onSuggestion }: WelcomeStateProps = {}) {
  const router = useRouter();
  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const askQuestion = useThreadStore((s) => s.askQuestion);
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const recentThreads = useThreadStore((s) => s.threads.filter((t) => !t.starred).slice(0, 3));
  const recentTitles = useThreadStore((s) =>
    s.threads.slice(0, 10).map((t) => t.title).filter(Boolean) as string[]
  );
  const pinnedMetrics = usePinnedMetricsStore((s) => s.metrics);
  const fetchMetrics = usePinnedMetricsStore((s) => s.fetchMetrics);
  useEffect(() => { fetchMetrics(); }, [fetchMetrics]);

  const [firstName, setFirstName] = useState<string | undefined>(undefined);
  const [greeting, setGreeting] = useState('');
  const [tagline, setTagline] = useState('');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const user = getStoredUser();
    setFirstName(user?.name?.split(' ')[0]);
    setGreeting(getGreeting());
    setTagline(pickTagline());
    setSuggestions(pickSuggestions(recentTitles));
    setMounted(true);
  // recentTitles intentionally excluded: suggestions are picked once on mount
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
      <div className="w-full max-w-2xl space-y-8">
        {/* Greeting */}
        <div className="text-center space-y-2 animate-fade-up">
          {!mounted ? (
            <>
              <div className="h-4 w-40 rounded-full bg-muted animate-pulse mx-auto" />
              <div className="h-14 w-3/4 rounded-xl bg-muted animate-pulse mx-auto mt-2" />
            </>
          ) : (
            <>
              {firstName && (
                <p className="text-sm text-muted-foreground">
                  {greeting}, {firstName}
                </p>
              )}
              <h1 className="text-5xl font-light tracking-[-0.03em] text-foreground">
                {tagline}
              </h1>
            </>
          )}
        </div>

        {/* Pinned metrics - max 6, capped so they never push the composer off screen */}
        {pinnedMetrics.length > 0 && (
          <div className="w-full animate-fade-up" style={{ animationDelay: '20ms' }}>
            <p className="text-[10px] text-muted-foreground/50 uppercase tracking-widest font-medium text-center mb-2">Pinned metrics</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {pinnedMetrics.slice(0, 6).map((m) => (
                <MetricPinCard key={m.id} metric={m} />
              ))}
            </div>
            {pinnedMetrics.length > 6 && (
              <p className="text-[11px] text-muted-foreground/60 text-center mt-2">
                +{pinnedMetrics.length - 6} more - unpin some to see them here
              </p>
            )}
          </div>
        )}

        {/* Continue where you left off */}
        {recentThreads.length > 0 && (
          <div className="flex flex-col items-center gap-2 animate-fade-up" style={{ animationDelay: '40ms' }}>
            <p className="text-[10px] text-muted-foreground/50 uppercase tracking-widest font-medium">Continue</p>
            <div className="flex flex-col w-full gap-1">
              {recentThreads.map((t) => (
                <button
                  key={t.id}
                  onClick={() => router.push(`/chat/${t.id}`)}
                  onMouseEnter={() => router.prefetch(`/chat/${t.id}`)}
                  className="flex items-center gap-3 px-4 py-2.5 rounded-xl border border-border bg-background hover:bg-accent hover:border-primary/20 transition-all duration-150 text-left group"
                >
                  <MessageSquare className="w-3.5 h-3.5 text-muted-foreground group-hover:text-primary transition-colors shrink-0" />
                  <span className="text-sm text-foreground/80 group-hover:text-foreground truncate transition-colors flex-1">
                    {t.title || 'Untitled chat'}
                  </span>
                  <ArrowRight className="w-3 h-3 text-transparent group-hover:text-muted-foreground ml-auto transition-colors shrink-0" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Suggestion chips - row 1: first 3, row 2: last 1 centered */}
        <div className="flex flex-col items-center gap-2">
          {!mounted ? (
            <>
              <div className="flex items-center justify-center gap-2">
                {[88, 112, 96].map((w, i) => (
                  <div key={i} className="h-9 rounded-full bg-muted animate-pulse" style={{ width: `${w}px`, animationDelay: `${i * 60}ms` }} />
                ))}
              </div>
              <div className="flex items-center justify-center gap-2">
                <div className="h-9 w-28 rounded-full bg-muted animate-pulse" style={{ animationDelay: '180ms' }} />
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center justify-center gap-2">
                {suggestions.slice(0, 3).map((s, i) => {
                  const Icon = s.icon;
                  return (
                    <button
                      key={i}
                      onClick={() => handleSuggestion(s.prompt)}
                      disabled={isStreaming}
                      className="group inline-flex items-center gap-2 rounded-full border border-border bg-background px-4 py-2 text-sm transition-all duration-150 hover:bg-accent hover:border-primary/20 disabled:opacity-50"
                      style={{
                        animation: `fade-up 0.4s ease-out ${(i + 1) * 80}ms both, chip-breathe 4s ${1.5 + i * 0.3}s ease-in-out infinite`,
                      }}
                    >
                      <Icon className="w-3.5 h-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
                      <span className="text-foreground/80 group-hover:text-foreground transition-colors">
                        {s.label}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="flex items-center justify-center gap-2">
                {suggestions.slice(3).map((s, i) => {
                  const Icon = s.icon;
                  return (
                    <button
                      key={i + 3}
                      onClick={() => handleSuggestion(s.prompt)}
                      disabled={isStreaming}
                      className="group inline-flex items-center gap-2 rounded-full border border-border bg-background px-4 py-2 text-sm transition-all duration-150 hover:bg-accent hover:border-primary/20 disabled:opacity-50"
                      style={{
                        animation: `fade-up 0.4s ease-out ${(i + 4) * 80}ms both, chip-breathe 4s ${1.5 + (i + 3) * 0.3}s ease-in-out infinite`,
                      }}
                    >
                      <Icon className="w-3.5 h-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
                      <span className="text-foreground/80 group-hover:text-foreground transition-colors">
                        {s.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
