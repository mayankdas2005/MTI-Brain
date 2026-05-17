import type { LucideIcon } from 'lucide-react';
import {
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
  CreditCard,
  Target,
  Network,
  Landmark,
} from 'lucide-react';

export interface Suggestion {
  icon: LucideIcon;
  label: string;
  prompt: string;
}

// ─── Simple ───────────────────────────────────────────────────────────────────

const TREASURY_SIMPLE: Suggestion[] = [
  { icon: Banknote,      label: 'Total cash balance yesterday',    prompt: 'What is our total cash balance across all bank accounts as of yesterday?' },
  { icon: LineChart,     label: 'JPMorgan 7-day balance',          prompt: 'Show me the closing balance of our JPMorgan operating account for the last 7 days.' },
  { icon: ArrowLeftRight,label: 'Large wires today',               prompt: 'List all wire transfers over 1 million processed today.' },
  { icon: Wallet,        label: 'Wells Fargo balance',             prompt: 'What is the current balance in our Wells Fargo concentration account?' },
  { icon: DollarSign,    label: 'ACH receipts yesterday',          prompt: 'What were our total ACH receipts yesterday?' },
  { icon: Layers,        label: 'Accounts by currency',            prompt: 'List all bank accounts grouped by currency.' },
  { icon: Building2,     label: 'Canada bank accounts',            prompt: 'How many bank accounts do we have open in Canada?' },
  { icon: BarChart3,     label: 'Friday cash position',            prompt: 'Show me the daily cash position report for last Friday.' },
  { icon: Globe,         label: 'USD to CAD FX rate',              prompt: 'What is the current FX rate for USD to CAD in our system?' },
  { icon: Landmark,      label: 'Debt by facility',                prompt: 'What is our current debt outstanding by facility?' },
  { icon: Clock,         label: 'Debt maturities 90 days',         prompt: 'Show me upcoming debt maturities in the next 90 days.' },
  { icon: FileText,      label: 'Letters of credit',               prompt: 'List all open letters of credit and their expiration dates.' },
  { icon: DollarSign,    label: 'Revolver availability',           prompt: 'What is our available borrowing capacity under the revolving credit facility?' },
  { icon: Globe,         label: 'Net cash by region',              prompt: 'Show me this week\'s net cash position by region.' },
  { icon: Shield,        label: 'FX hedge notional',               prompt: 'What is the total notional of our outstanding FX hedges?' },
  { icon: Clock,         label: 'Investments maturing 30 days',    prompt: 'List all investment positions maturing in the next 30 days.' },
  { icon: ShieldAlert,   label: 'Facility compliance status',      prompt: 'Show me current compliance status across all credit facilities.' },
  { icon: Percent,       label: 'Commercial paper outstanding',    prompt: 'What is our current commercial paper outstanding and weighted average rate?' },
  { icon: FileText,      label: 'Signatory recertification',       prompt: 'List all bank account signatories that need annual recertification this quarter.' },
  { icon: Globe,         label: 'Global cash & investments',       prompt: 'What is our consolidated cash and short-term investment balance globally?' },
  { icon: Percent,       label: 'Weighted avg cost of debt',       prompt: 'What is the weighted average cost of debt for our portfolio?' },
  { icon: Wallet,        label: 'Liquidity by region',             prompt: 'Show me total liquidity (cash & undrawn committed facilities) by region.' },
  { icon: Scale,         label: 'Credit ratings by agency',        prompt: 'What is our current credit rating from each agency?' },
  { icon: Building2,     label: 'Bank wallet share',               prompt: 'List all bank relationships with their share of wallet over the last 12 months.' },
  { icon: TrendingUp,    label: 'YoY net debt change',             prompt: 'Show me the year-over-year change in our net debt position.' },
  { icon: Percent,       label: 'EUR FX hedge ratio',              prompt: 'What is our current FX hedge ratio for forecasted EUR exposures?' },
  { icon: Wallet,        label: 'Total liquidity today',           prompt: 'What is our total liquidity available today?' },
  { icon: Scale,         label: 'Credit rating & outlook',         prompt: 'What is our current credit rating and outlook from each agency?' },
  { icon: TrendingUp,    label: 'Shareholder returns YTD',         prompt: 'How much have we returned to shareholders YTD via dividends and buybacks?' },
  { icon: Globe,         label: 'Global cash by region',           prompt: 'Show me global cash by major region.' },
  { icon: BarChart3,     label: 'Debt maturity summary',           prompt: 'What is our current debt maturity profile in summary?' },
  { icon: CreditCard,    label: 'Card volume yesterday',           prompt: 'Show me total card payment volume processed across all our acquirers yesterday.' },
  { icon: Banknote,      label: 'Chase settlement today',          prompt: 'What was the total settlement amount received from Chase Paymentech into our concentration account today?' },
  { icon: Layers,        label: 'Processors last week',            prompt: 'List all payment processors we used last week with total volume by processor.' },
  { icon: PieChart,      label: 'Card sales by network',           prompt: 'Show me the breakdown of yesterday\'s card sales by network — Visa, Mastercard, Amex, Discover, and Costco-branded.' },
  { icon: ArrowLeftRight,label: 'ACH debit volume',                prompt: 'What is the total ACH debit volume initiated for vendor payments this week?' },
  { icon: DollarSign,    label: 'Large wires yesterday',           prompt: 'Display all wire payments over USD 5 million sent through our payment hub yesterday.' },
  { icon: AlertTriangle, label: 'Bank file rejections',            prompt: 'List all payment file rejections from our banks in the last 24 hours with reason codes.' },
  { icon: Clock,         label: 'Pending settlements today',       prompt: 'What is the total amount of pending settlements in our merchant acquirer holding accounts as of today?' },
  { icon: ShieldAlert,   label: 'Chargebacks last week',           prompt: 'Show me total chargebacks initiated against our card receivables last week.' },
  { icon: BarChart3,     label: 'Total volume this month',         prompt: 'What is our total payment volume processed this month across all channels?' },
  { icon: DollarSign,    label: 'Cost YTD vs budget',              prompt: 'Show me total payment processing cost year-to-date versus budget.' },
  { icon: Layers,        label: 'Top 10 vendors by volume',        prompt: 'List our top 10 vendors by outbound payment volume this quarter.' },
  { icon: Clock,         label: 'Average days-to-pay',             prompt: 'What is our current average days-to-pay across all suppliers?' },
  { icon: CreditCard,    label: 'Card receivables in transit',     prompt: 'Show me total card receivables in transit between authorization and bank deposit as of today.' },
  { icon: PieChart,      label: 'Outbound payments by method',     prompt: 'Display the breakdown of outbound payments by method — ACH, wire, RTP, virtual card, check — for the current month.' },
  { icon: Percent,       label: 'Interchange last quarter',        prompt: 'What was total interchange paid to card networks last quarter?' },
  { icon: ShieldAlert,   label: 'Fraud losses YTD',                prompt: 'Show me total payment fraud losses booked this year-to-date by channel.' },
  { icon: DollarSign,    label: 'Annual processing spend',         prompt: 'What is our total annual payments processing spend across all acquirers and processors?' },
  { icon: BarChart3,     label: 'Top 5 payment partners',          prompt: 'Show me our top 5 payment partners ranked by volume and by spend.' },
  { icon: Percent,       label: 'Blended interchange rate',        prompt: 'What is our blended effective interchange rate year-to-date?' },
  { icon: DollarSign,    label: 'Processing cost % of revenue',    prompt: 'Display our payment processing cost as a percentage of card-payment revenue for the trailing 12 months.' },
  { icon: ShieldAlert,   label: 'Fraud loss rate',                 prompt: 'Show me total fraud loss as a percentage of payment volume for the current year.' },
  { icon: Activity,      label: 'Global STP rate',                 prompt: 'What is our straight-through-processing rate across the global payment hub?' },
  { icon: Globe,         label: 'Volume by region & method',       prompt: 'Show me total payment volume by region and by payment method for the trailing 12 months.' },
  { icon: CreditCard,    label: 'Acquirer concentration',          prompt: 'Display our acquirer concentration and share of total card volume by top 3 acquirers.' },
  { icon: Clock,         label: 'DPO vs benchmark',                prompt: 'What is our average days-payable-outstanding versus the industry benchmark?' },
  { icon: Percent,       label: 'Cost of payments % revenue',      prompt: 'What is our total cost of payments as a percentage of revenue?' },
  { icon: DollarSign,    label: 'Processing spend last year',      prompt: 'How much did we spend on payment processing last year?' },
  { icon: PieChart,      label: 'Top 3 partner share',             prompt: 'Show me our top 3 payment partners and the percentage of volume each handles.' },
  { icon: Banknote,      label: 'Interchange spend last year',     prompt: 'What was our total interchange spend last year?' },
  { icon: Activity,      label: 'Payment hub STP rate',            prompt: 'What is our payment-hub straight-through-processing rate?' },
  { icon: Globe,         label: 'E-com vs in-warehouse',           prompt: 'What is our share of e-commerce versus in-warehouse payment volume?' },
  { icon: ShieldAlert,   label: 'Member fraud absorbed',           prompt: 'How much member-facing fraud did we absorb last year?' },
];

// ─── Complex ──────────────────────────────────────────────────────────────────

const TREASURY_COMPLEX: Suggestion[] = [
  { icon: TrendingUp,    label: 'Cash inflow vs forecast',         prompt: 'Compare our actual cash inflows and forecasts for the past 30 days by entity and highlight variances greater than 10%.' },
  { icon: Scale,         label: 'Bank vs ERP recon',               prompt: 'Match yesterday\'s bank balances against our ERP ledger balances and list all discrepancies above $10K.' },
  { icon: Calculator,    label: 'Daily float analysis',            prompt: 'Calculate the daily average float for our top 10 collection accounts over the last quarter.' },
  { icon: Network,       label: 'Intercompany funding',            prompt: 'Show me all intercompany funding transactions over $500K in the last month along with source and destination.' },
  { icon: Layers,        label: 'Cash concentration report',       prompt: 'Generate a cash concentration report showing sweep activity, target balances, and unswept residuals across all subsidiary accounts.' },
  { icon: Globe,         label: 'FX exposure vs hedge',            prompt: 'List all FX exposures by currency pair for our international subsidiaries and show the net position vs hedged amount.' },
  { icon: Percent,       label: 'Investment yield by type',        prompt: 'What is our weighted average yield on short-term investments this month vs last month, split by instrument type?' },
  { icon: AlertTriangle, label: 'Bank fees vs rate cards',         prompt: 'Show me all bank fees charged in the last 90 days, grouped by bank and service type, and flag fees that exceed our negotiated rate cards.' },
  { icon: Search,        label: 'Dormant bank accounts',           prompt: 'Identify all bank accounts with no transaction activity in the past 60 days and provide their balances and account purpose.' },
  { icon: PieChart,      label: 'Portfolio vs policy',             prompt: 'Show me our investment portfolio mix by instrument type, credit rating, and maturity bucket, and flag any positions outside of our investment policy guidelines.' },
  { icon: Scale,         label: 'Borrowing cost vs benchmark',     prompt: 'Compare our actual borrowing costs vs benchmark rates across all facilities and calculate the all-in cost of debt for the quarter.' },
  { icon: Wallet,        label: 'Liquidity coverage report',       prompt: 'Generate a liquidity coverage report showing total available liquidity, committed facilities, uncommitted lines, and cash on hand by currency.' },
  { icon: TrendingUp,    label: 'Working capital trends',          prompt: 'Show working capital trends over the last 6 quarters including DSO, DPO, DIO, and benchmark against our internal targets.' },
  { icon: DollarSign,    label: 'Bank fee savings',                prompt: 'Analyze bank fees by service category over the last 12 months and identify the top 5 cost-saving opportunities through service consolidation or renegotiation.' },
  { icon: ArrowUpDown,   label: 'Derivative positions',            prompt: 'Show me all derivative positions by counterparty with current MTM, collateral posted, and remaining capacity.' },
  { icon: BarChart3,     label: 'Capex vs forecast by entity',     prompt: 'Compare our forecasted vs actual capital expenditure cash outflows by business unit for the quarter and identify entities trending over budget.' },
  { icon: BarChart3,     label: 'Capex vs forecast by dept',       prompt: 'Compare our forecasted vs actual capital expenditure cash outflows by business unit for the quarter and identify departments trending over budget.' },
  { icon: Landmark,      label: 'Treasury dashboard',              prompt: 'Provide a treasury dashboard showing liquidity, debt profile, investment performance, FX and interest rate hedging, and counterparty exposure.' },
  { icon: Scale,         label: 'Capital structure vs peers',      prompt: 'Analyze our capital structure vs peer companies (Walmart, Target, Home Depot, Kroger) on key metrics: leverage, liquidity, cost of capital, and shareholder returns.' },
  { icon: TrendingUp,    label: 'Working capital efficiency',      prompt: 'Show me the trend in working capital efficiency over 8 quarters and decompose changes into AR, inventory, and AP drivers by business segment.' },
  { icon: PieChart,      label: 'Capital allocation vs framework', prompt: 'Compare our actual capital allocation (capex, dividends, buybacks, M&A, debt paydown) vs our stated capital allocation framework over the last 3 years.' },
  { icon: Globe,         label: 'FX risk report',                  prompt: 'Generate an FX risk report showing translation exposure by currency, transaction exposure by entity, hedge coverage, and YTD FX impact on earnings.' },
  { icon: FileText,      label: 'CFO treasury briefing',           prompt: 'Give me a one-page CFO briefing on treasury health: liquidity, debt, FX, interest rate exposure, and key risks.' },
  { icon: ShieldAlert,   label: 'Counterparty risk summary',       prompt: 'Provide a counterparty risk summary showing our largest exposures across all banks and the actions we are taking to manage concentration risk.' },
  { icon: Scale,         label: 'POS vs acquirer recon',           prompt: 'Reconcile yesterday\'s gross card sales reported by our point-of-sale system against the net settlements deposited by each acquirer, and break out the variance by interchange, assessments, processor fees, and chargebacks.' },
  { icon: AlertTriangle, label: 'ACH returns by reason',           prompt: 'Pull all ACH returns from the past 60 days, categorize by return reason code, and show me the dollar impact and trend by originating business unit.' },
  { icon: Network,       label: 'Payment hub throughput',          prompt: 'Generate a daily payment hub throughput report for the last 30 days showing volume and value by payment type — wire, ACH, RTP, FedNow, check, virtual card — along with success, rejection, and repair rates.' },
  { icon: Percent,       label: 'Acquirer fee breakdown',          prompt: 'Show me total payment processing fees paid to each acquirer for the past quarter as a percentage of processed amount, split by interchange, network assessments, and processor margin.' },
  { icon: Search,        label: 'Duplicate payment exceptions',    prompt: 'List all duplicate payment exceptions flagged by our payment hub in the last 90 days, and show resolution status, dollar amount, and recovery rate.' },
  { icon: ShieldAlert,   label: 'Chargeback ratios vs threshold',  prompt: 'Show me chargeback ratios by warehouse, acquirer, and reason code for the last 6 months and flag any combination that exceeds the 1% Visa monitoring threshold.' },
  { icon: TrendingUp,    label: 'Cost per transaction trend',      prompt: 'Compare our blended cost-per-transaction across acquirers for the past 4 quarters, normalized for ticket size and card mix, and explain variances driven by interchange, network fees, processor margin, and incentive tier achievements.' },
  { icon: BarChart3,     label: 'Acquirer scorecard',              prompt: 'Pull our acquirer scorecard for the last 12 months covering authorization rate, settlement timeliness, fee competitiveness, dispute win rate, technical uptime, and innovation roadmap progress.' },
  { icon: AlertTriangle, label: 'Hub exceptions report',           prompt: 'Generate an exceptions and repairs report for our payment hub for the past quarter showing volume by exception type, average resolution time, and manpower effort to fix.' },
  { icon: Target,        label: 'Supplier on-time rates',          prompt: 'Compare on-time payment rates to suppliers across business units for the past 6 months, broken down by payment method, and highlight units operating outside our 95% on-time policy.' },
  { icon: RefreshCw,     label: 'Chargeback settlement recon',     prompt: 'Pull all payment-related chargebacks, refunds, and merchandise returns for the past 4 quarters and reconcile to the corresponding card settlement activity to validate net receivable position.' },
  { icon: Globe,         label: 'Cross-border cost by corridor',   prompt: 'Show me the breakdown of cross-border payment volume by corridor, currency, and method for the past 12 months, with end-to-end cost including FX spread, lifting fees, and correspondent charges.' },
  { icon: ShieldAlert,   label: 'Operational losses trend',        prompt: 'Pull all payment-related operational losses, fraud losses, and write-offs over the past 24 months, categorize by root cause, and show the trend versus our internal loss tolerance.' },
  { icon: TrendingUp,    label: '8-quarter KPI trend',             prompt: 'Show me an 8-quarter trend of payment KPIs — cost as percent of volume, authorization rate, STP rate, fraud loss basis points, chargeback ratio, and supplier on-time rate — with policy thresholds overlaid.' },
  { icon: AlertTriangle, label: 'Material payment incidents',      prompt: 'Pull all material payment incidents over the past 12 months — outages, settlement delays, fraud events, sanctions hits, large reconciliation breaks — with root cause, financial impact, and remediation status.' },
  { icon: LineChart,     label: '5-year cost evolution',           prompt: 'Show me how our payments cost evolved over the past 5 years versus card mix shift, ticket-size changes, channel mix shift (in-warehouse vs e-commerce vs membership), and contract renewals.' },
  { icon: BarChart3,     label: 'Quarterly exec scorecard',        prompt: 'Generate a quarterly payments scorecard for the executive committee covering volume, cost, fraud, resilience, supplier experience, and member experience, with traffic-light status against targets.' },
  { icon: LineChart,     label: 'Decade cost trajectory',          prompt: 'Show me the trajectory of our payments cost as a percentage of revenue over the last decade, decomposed into mix, rate, and operating-leverage effects.' },
  { icon: FileText,      label: 'Audit committee summary',         prompt: 'Summarize all material payment incidents and regulatory matters over the past 24 months in a format suitable for the audit committee.' },
];

// ─── Advanced ─────────────────────────────────────────────────────────────────

const TREASURY_ADVANCED: Suggestion[] = [
  { icon: LineChart,     label: 'Cash forecast with seasonality',  prompt: 'Build a 4-week and 3-month cash forecast using historical inflows and outflows. Factor in seasonality from the same period last year, and highlight any week where projected liquidity falls below our $200M minimum threshold.' },
  { icon: Activity,      label: 'OCF variance analysis',           prompt: 'Analyze the variance between our forecasted and actual operating cash flow over the past 6 months. Identify the reasons from the perspective of AR collections, AP disbursements, payroll, and capex. Identify the top 3 drivers of forecast inaccuracy.' },
  { icon: ShieldAlert,   label: 'Liquidity stress test',           prompt: 'Run a stress test on our liquidity position assuming a 20% drop in daily receipts for 30 days and show which entities would breach minimum operating cash thresholds first.' },
  { icon: AlertTriangle, label: 'Vendor anomaly detection',        prompt: 'Detect vendors with anomalous payment patterns in the last 30 days using historical baselines. Flag any disbursements that deviate more than 3 standard deviations from the vendor\'s typical payment size.' },
  { icon: Calculator,    label: 'True cost of cash',               prompt: 'Calculate our true cost of cash by entity, factoring in idle balances, borrowing costs on credit facilities, and opportunity cost vs our investment portfolio yield.' },
  { icon: Network,       label: 'Counterparty exposure dashboard',  prompt: 'Build a counterparty exposure dashboard showing total deposits, investments, and derivative MTM by bank, and flag any counterparty exceeding 25% of total cash plus investments.' },
  { icon: TrendingUp,    label: 'DSO & DPO projection',            prompt: 'Project our Day Sales Outstanding and Day Payables Outstanding for the next quarter based on current trends and show the impact on working capital and free cash flow.' },
  { icon: Percent,       label: 'Interest income & reallocation',  prompt: 'Analyze our interest income over the trailing 12 months by investment instrument. Calculate yield to maturity vs benchmark rates, and recommend reallocation opportunities for underperforming positions.' },
  { icon: GitBranch,     label: 'Cash movement mapping',           prompt: 'Map all cash movements from collection accounts through concentration accounts to disbursement accounts for last month, and identify inefficiencies, redundant hops, or trapped cash.' },
  { icon: Scale,         label: 'FX hedge effectiveness',          prompt: 'Build a hedge effectiveness analysis for our FX forward portfolio, comparing realized vs forecasted exposure coverage, and quantify under- or over-hedged positions by currency and tenor.' },
  { icon: Shield,        label: 'Counterparty credit risk',        prompt: 'Build a counterparty credit risk dashboard aggregating exposure across deposits, investments, derivatives, and trade payables, and recommend rebalancing actions to stay within board-approved concentration limits.' },
  { icon: Calculator,    label: 'Payment terms impact model',      prompt: 'Model the working capital impact of changing our standard supplier payment terms from Net 30 to Net 45 across our top 100 vendors and estimate the cash flow benefit net of any pricing concessions or supply chain finance program changes.' },
  { icon: ShieldAlert,   label: 'Fraud risk assessment',           prompt: 'Conduct a fraud risk assessment by analyzing payment patterns across all disbursement channels for the last 90 days, identifying any single-approver high-value payments, or payments to newly added vendors.' },
  { icon: LineChart,     label: 'Rate impact on pension',          prompt: 'Analyze the impact of recent rate moves on our pension funded status, OCI, and projected contribution requirements over the next 3 years.' },
  { icon: ShieldAlert,   label: 'Enterprise liquidity stress test', prompt: 'Conduct an enterprise-wide liquidity stress test modeling a severe scenario (recession, supply chain disruption, oil price increase, 30% same-store sales decline) and quantify minimum liquidity needs, facility utilization, and contingent funding actions over 12 months.' },
  { icon: Globe,         label: 'FX hedging optimization',         prompt: 'Develop a multi-year FX hedging strategy optimization comparing layered, static, and dynamic hedging approaches across our top 8 currency exposures, factoring in cost, P&L volatility, and budget rate protection.' },
  { icon: DollarSign,    label: '$3B maturity funding strategy',   prompt: 'Analyze the optimal funding strategy for $3B in upcoming maturities over 18 months across bonds, term loans, commercial paper, and revolver draws, factoring in market windows, investor demand, covenant flexibility, and rating impact.' },
  { icon: ArrowLeftRight,label: 'ACH & wire rejection fixes',      prompt: 'Analyze ACH and wire rejections over the past 12 months and classify them into bank validation issues, file format errors, beneficiary data problems, and OFAC holds, and recommend the top 5 process fixes ranked by dollar impact.' },
  { icon: ShieldAlert,   label: 'Chargeback fraud clusters',       prompt: 'Analyze chargeback patterns over the past 24 months using transaction-level data to identify clusters of friendly fraud, true fraud, and merchant error, and recommend representment strategies with expected win rates by reason code.' },
  { icon: Target,        label: 'STP scorecard',                   prompt: 'Build a payment hub straight-through-processing scorecard that shows STP rate by payment type, country, and originating system over the past 12 months. Identify the top 10 exception drivers, and quantify the cost of each repair touch.' },
];

// ─── Ghost prompts ────────────────────────────────────────────────────────────

export const GHOST_PROMPTS: string[] = [
  'What is our total cash balance across all bank accounts as of yesterday?',
  'Show me upcoming debt maturities in the next 90 days.',
  'What is our current credit rating from each agency?',
  'Show me total card payment volume processed across all our acquirers yesterday.',
  'What is our blended effective interchange rate year-to-date?',
  'List all wire transfers over $1M processed today.',
  'What is our total liquidity available today?',
  'Show me this week\'s net cash position by region.',
  'Match yesterday\'s bank balances against our ERP ledger balances and list all discrepancies above $10K.',
  'List all FX exposures by currency pair for our international subsidiaries and show the net position vs hedged amount.',
  'Generate a liquidity coverage report showing total available liquidity, committed facilities, and cash on hand by currency.',
  'Give me a one-page CFO briefing on treasury health: liquidity, debt, FX, interest rate exposure, and key risks.',
  'Reconcile yesterday\'s gross card sales against acquirer net settlements, breaking out variance by interchange, assessments, and fees.',
  'Show me an 8-quarter trend of payment KPIs with policy thresholds overlaid.',
  'Build a 4-week and 3-month cash forecast factoring in seasonality, and flag any week below our $200M liquidity threshold.',
  'Run a stress test on our liquidity assuming a 20% drop in daily receipts for 30 days.',
  'Detect vendors with anomalous payment patterns deviating more than 3 standard deviations from their typical payment size.',
  'Build a hedge effectiveness analysis for our FX forward portfolio, quantifying under- or over-hedged positions by currency and tenor.',
];

// Backward-compat alias: all tones share the same prompts.
export const GHOST_PROMPTS_BY_TONE = {
  analyst:   GHOST_PROMPTS,
  manager:   GHOST_PROMPTS,
  director:  GHOST_PROMPTS,
  executive: GHOST_PROMPTS,
} as const;

// ─── Helpers ──────────────────────────────────────────────────────────────────

export function pickRandom<T>(arr: T[], n: number): T[] {
  const shuffled = [...arr].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, n);
}

/** Pick 4 suggestions: 1 simple + 2 complex + 1 advanced, fully randomised. */
export function pickSuggestions(_recentTitles: string[] = []): Suggestion[] {
  const [simple]             = pickRandom(TREASURY_SIMPLE, 1);
  const [complex1, complex2] = pickRandom(TREASURY_COMPLEX, 2);
  const [advanced]           = pickRandom(TREASURY_ADVANCED, 1);
  return [simple, complex1, complex2, advanced].sort(() => Math.random() - 0.5);
}

// Legacy flat exports — kept so any existing direct imports still compile.
export const SIMPLE   = TREASURY_SIMPLE;
export const COMPLEX  = TREASURY_COMPLEX;
export const ADVANCED = TREASURY_ADVANCED;
