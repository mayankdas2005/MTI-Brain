import type { LucideIcon } from 'lucide-react';
import {
  Landmark,
  TrendingUp,
  AlertTriangle,
  BarChart3,
  Wallet,
  ArrowLeftRight,
  ShieldAlert,
  PieChart,
  Banknote,
  Search,
  Activity,
  Scale,
  LineChart,
  Layers,
  Building2,
  DollarSign,
  Globe,
  FileText,
  ArrowUpDown,
  RefreshCw,
  Percent,
  Clock,
  Calculator,
  GitBranch,
  Shield,
} from 'lucide-react';

export interface Suggestion {
  icon: LucideIcon;
  label: string;
  prompt: string;
}

export const SIMPLE: Suggestion[] = [
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

export const COMPLEX: Suggestion[] = [
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

export const ADVANCED: Suggestion[] = [
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

export function pickRandom<T>(arr: T[], n: number): T[] {
  const shuffled = [...arr].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, n);
}

export function pickSuggestions(): Suggestion[] {
  return [
    ...pickRandom(SIMPLE, 1),
    ...pickRandom(COMPLEX, 2),
    ...pickRandom(ADVANCED, 1),
  ].sort(() => Math.random() - 0.5);
}

export const GHOST_PROMPTS: string[] = [
  'What were our total ACH receipts yesterday?',
  'List all bank accounts grouped by currency.',
  'How many bank accounts do we have open in Canada?',
  'List all wire transfers over 1 mil processed today.',
  'What is the current FX rate for USD to CAD?',
  'Show me the daily cash position for last Friday.',
  'List FX exposures by currency pair.',
  'Show idle cash balances by region this quarter.',
  'Compare actuals vs forecast for the last 30 days.',
  'Match bank balances against ERP ledger balances.',
];
