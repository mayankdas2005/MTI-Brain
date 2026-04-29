'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useThreadStore } from '@/lib/store/threads';
import { getStoredUser } from '@/lib/auth';
import { Landmark, TrendingUp, AlertTriangle, BarChart3, MessageSquare, ArrowRight, Wallet, ArrowLeftRight, ShieldAlert, PieChart, Banknote, Search, Activity, Scale, LineChart, Layers, Building2, DollarSign, Globe, FileText, ArrowUpDown, RefreshCw, Percent, Clock, Calculator, GitBranch, Shield } from 'lucide-react';

// ─── Curated prompt pool ───
// Each visit surfaces 4 chips - one simple, two complex, one advanced -
// so the product feels simultaneously approachable and powerful.

interface Suggestion {
  icon: typeof Landmark;
  label: string;
  prompt: string;
}

const SIMPLE: Suggestion[] = [
  { icon: Landmark, label: 'Cash position', prompt: 'What is our total cash balance across all bank accounts as of yesterday?' },
  { icon: Building2, label: 'Account balance', prompt: 'Show me the closing balance of our JPMorgan operating account for the last 7 days.' },
  { icon: Banknote, label: 'Large wires today', prompt: 'List all wire transfers over 1 mil processed today.' },
  { icon: Landmark, label: 'Concentration balance', prompt: 'What is the current balance in our Wells Fargo concentration account?' },
  { icon: AlertTriangle, label: 'Negative balances', prompt: 'Show me all bank accounts that have a negative balance this week.' },
  { icon: DollarSign, label: 'ACH receipts', prompt: 'What were our total ACH receipts yesterday?' },
  { icon: Layers, label: 'Accounts by currency', prompt: 'List all bank accounts grouped by currency.' },
  { icon: Globe, label: 'Accounts in Canada', prompt: 'How many bank accounts do we have open in Canada?' },
  { icon: FileText, label: 'Cash position report', prompt: 'Show me the daily cash position report for last Friday.' },
  { icon: ArrowLeftRight, label: 'FX rates', prompt: 'What is the current FX rate for USD to CAD in our system?' },
];

const COMPLEX: Suggestion[] = [
  { icon: TrendingUp, label: 'Forecast variance', prompt: 'Compare our actual cash inflows and forecasts for the past 30 days by entity and highlight variances greater than 10%.' },
  { icon: Wallet, label: 'Idle cash analysis', prompt: 'Show me the idle cash balances by region over the last 90 days and identify accounts that have been consistently above 5 mil USD. Also show a trend.' },
  { icon: Scale, label: 'Reconciliation gaps', prompt: "Match yesterday's bank balances against our ERP ledger balances and list all discrepancies above $10K." },
  { icon: BarChart3, label: 'Collection float', prompt: 'Calculate the daily avg. float for our top 10 collection accounts over the last quarter.' },
  { icon: ArrowUpDown, label: 'Intercompany funding', prompt: 'Show me all intercompany funding transactions over $500K in the last month along with source and destination.' },
  { icon: RefreshCw, label: 'Sweep activity', prompt: 'Generate a cash concentration report showing sweep activity, target balances, and unswept residuals across all subsidiary accounts.' },
  { icon: PieChart, label: 'FX exposure', prompt: 'List all FX exposures by currency pair for our international subsidiaries and show the net position vs hedged amount.' },
  { icon: Percent, label: 'Investment yield', prompt: 'What is our weighted average yield on short-term investments this month vs last month. Split by instrument type.' },
  { icon: Search, label: 'Bank fee audit', prompt: 'Show me all bank fees charged in the last 90 days. Group by bank and service type, and flag fees that exceed our negotiated rate cards.' },
  { icon: Clock, label: 'Dormant accounts', prompt: 'Identify all bank accounts with no transaction activity in the past 60 days and provide their balances and account purpose.' },
];

const ADVANCED: Suggestion[] = [
  { icon: LineChart, label: 'Cash forecast', prompt: 'Build a 4 week and 3 month cash forecast using historical inflows and outflows. Factor in seasonality from the same period last year, and highlight any week where projected liquidity falls below our $200M minimum threshold.' },
  { icon: TrendingUp, label: 'Cash flow drivers', prompt: 'Analyze the variance between our forecasted and actual operating cash flow over the past 6 months. Identify the reasons for the variance from PoV of AR collections, AP disbursements, payroll, and capex. Identify the top 3 drivers of forecast inaccuracy.' },
  { icon: BarChart3, label: 'Liquidity stress test', prompt: 'Run a stress test on our liquidity position assuming a 20% drop in daily receipts for 30 days and show which entities would breach minimum operating cash thresholds first.' },
  { icon: Activity, label: 'Payment anomalies', prompt: "Detect vendors with anomalous payment patterns in the last 30 days using historical baselines. Flag any disbursements that deviate more than 3 standard deviations from the vendor's typical payment size." },
  { icon: DollarSign, label: 'True cost of cash', prompt: 'Calculate our true cost of cash by entity, factoring in idle balances, borrowing costs on credit facilities, and opportunity cost vs our investment portfolio yield.' },
  { icon: ShieldAlert, label: 'Counterparty risk', prompt: 'Build a counterparty exposure dashboard showing total deposits, investments, and derivative MTM by bank, and flag any counterparty exceeding 25% of total cash plus investments.' },
  { icon: Calculator, label: 'DSO & DPO outlook', prompt: 'Project our Day Sales Outstanding and Day Payables Outstanding for the next quarter based on current trends and show the impact on working capital and free cash flow.' },
  { icon: Percent, label: 'Interest income', prompt: 'Analyze our interest income over the trailing 12 months by investment instrument. Calculate yield to maturity vs benchmark rates, and recommend reallocation opportunities for underperforming positions.' },
  { icon: GitBranch, label: 'Cash flow mapping', prompt: 'Map all cash movements from collection accounts through concentration accounts to disbursement accounts for last month, and identify inefficiencies, redundant hops, or trapped cash.' },
  { icon: Shield, label: 'Hedge effectiveness', prompt: 'Build a hedge effectiveness analysis for our FX forward portfolio, comparing realized vs forecasted exposure coverage, and quantify under- or over-hedged positions by currency and tenor.' },
];

/** Pick n random items from an array without repeating. */
function pickRandom<T>(arr: T[], n: number): T[] {
  const shuffled = [...arr].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, n);
}

/** Surface 4 suggestions: 1 simple + 2 complex + 1 advanced. */
function pickSuggestions(): Suggestion[] {
  return [
    ...pickRandom(SIMPLE, 1),
    ...pickRandom(COMPLEX, 2),
    ...pickRandom(ADVANCED, 1),
  ].sort(() => Math.random() - 0.5);
}

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
];

const TAGLINES_AFTERNOON: string[] = [
  'Turning data into decisions',
  'Time for a midday check-in',
  'Afternoon focus. Sharper answers',
  'Making this afternoon count',
  'Half the day down. Keep the momentum',
  'Decisions don\u2019t wait. Neither should you',
  'Your afternoon edge, powered by data',
  'Clear thinking for the second half',
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
];

// ─── Greetings: day + time aware with multiple options per slot ───

const GREETINGS: Record<string, string[]> = {
  'mon_early':     ['You\u2019re up before the market', 'Early bird, Monday edition'],
  'mon_morning':   ['Let\u2019s start the week strong', 'Monday morning. Let\u2019s go', 'New week, new insights'],
  'mon_afternoon': ['Monday\u2019s moving fast', 'Good afternoon', 'Halfway through Monday'],
  'mon_evening':   ['Still going strong on Monday', 'Burning the Monday oil'],
  'mon_night':     ['Monday night deep dive', 'Working late on Monday'],

  'tue_early':     ['You\u2019re up early', 'Early start to Tuesday'],
  'tue_morning':   ['Good morning', 'Tuesday\u2019s off to a good start', 'Morning check-in'],
  'tue_afternoon': ['Good afternoon', 'Tuesday afternoon'],
  'tue_evening':   ['Good evening', 'Wrapping up Tuesday'],
  'tue_night':     ['Working late', 'Burning the midnight oil'],

  'wed_early':     ['Midweek, early start', 'You\u2019re up before the sun'],
  'wed_morning':   ['Good morning', 'Halfway through the week', 'Happy hump day'],
  'wed_afternoon': ['Good afternoon', 'Midweek momentum'],
  'wed_evening':   ['Good evening', 'Wednesday evening'],
  'wed_night':     ['Midweek midnight oil', 'Working late'],

  'thu_early':     ['Almost Friday. Almost', 'You\u2019re up early'],
  'thu_morning':   ['Good morning', 'Thursday morning', 'One more day after this'],
  'thu_afternoon': ['Good afternoon', 'Thursday\u2019s flying by'],
  'thu_evening':   ['Good evening', 'Thursday evening wind-down'],
  'thu_night':     ['Working late on Thursday', 'Late night'],

  'fri_early':     ['Friday already', 'Up early on a Friday'],
  'fri_morning':   ['Happy Friday', 'TGIF morning', 'Friday. Let\u2019s finish strong'],
  'fri_afternoon': ['Happy Friday afternoon', 'Weekend\u2019s almost here'],
  'fri_evening':   ['Still at it on Friday', 'Friday evening'],
  'fri_night':     ['Friday night and still working', 'Weekend can wait'],

  'weekend_morning':   ['Weekend morning', 'Taking the weekend shift'],
  'weekend_afternoon': ['Weekend warrior mode', 'Working the weekend'],
  'weekend_evening':   ['Weekend evening', 'Quiet weekend session'],
  'weekend_night':     ['Weekend late night', 'Burning weekend midnight oil'],
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

  const [firstName, setFirstName] = useState<string | undefined>(undefined);
  const [greeting, setGreeting] = useState('');
  const [tagline, setTagline] = useState('');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);

  useEffect(() => {
    const user = getStoredUser();
    setFirstName(user?.name?.split(' ')[0]);
    setGreeting(getGreeting());
    setTagline(pickTagline());
    setSuggestions(pickSuggestions());
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
          {firstName && (
            <p className="text-sm text-muted-foreground" suppressHydrationWarning>
              {greeting || 'Hello'}, {firstName}
            </p>
          )}
          <h1 className="text-5xl font-light tracking-[-0.03em] text-foreground" suppressHydrationWarning>
            {tagline || 'Your treasury advisor is ready'}
          </h1>
        </div>

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

        {/* Suggestion chips */}
        <div className="flex flex-wrap items-center justify-center gap-2">
          {suggestions.map((s, i) => {
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
      </div>
    </div>
  );
}
