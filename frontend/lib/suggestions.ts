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
  Zap,
  Target,
  Network,
  Landmark,
} from 'lucide-react';
import type { ResponseTone } from '@/lib/store/preferences';

export interface Suggestion {
  icon: LucideIcon;
  label: string;
  prompt: string;
}

// ─── Analyst ──────────────────────────────────────────────────────────────────
// Source: Analyst persona.txt — all questions included verbatim.

const ANALYST_SIMPLE: Suggestion[] = [
  { icon: CreditCard,    label: 'Card volume by acquirer',     prompt: 'Show me total card payment volume processed across all our acquirers yesterday.' },
  { icon: Banknote,      label: 'Chase settlement today',      prompt: 'What was the total settlement amount received from Chase Paymentech into our concentration account today?' },
  { icon: Layers,        label: 'Processors last week',        prompt: 'List all payment processors we used last week with total volume by processor.' },
  { icon: PieChart,      label: 'Card sales by network',       prompt: 'Show me the breakdown of yesterday\'s card sales by network — Visa, Mastercard, Amex, Discover, and Costco-branded.' },
  { icon: ArrowLeftRight,label: 'ACH debit volume',            prompt: 'What is the total ACH debit volume initiated for vendor payments this week?' },
  { icon: DollarSign,    label: 'Large wires yesterday',       prompt: 'Display all wire payments over USD 5 million sent through our payment hub yesterday.' },
  { icon: CreditCard,    label: 'Membership fee collections',  prompt: 'Show me total membership fee collections processed yesterday by channel.' },
  { icon: AlertTriangle, label: 'Bank file rejections',        prompt: 'List all payment file rejections from our banks in the last 24 hours with reason codes.' },
  { icon: Clock,         label: 'Pending settlements',         prompt: 'What is the total amount of pending settlements in our merchant acquirer holding accounts as of today?' },
  { icon: ShieldAlert,   label: 'Chargebacks last week',       prompt: 'Show me total chargebacks initiated against our card receivables last week.' },
];

const ANALYST_COMPLEX: Suggestion[] = [
  { icon: Scale,         label: 'POS vs acquirer recon',       prompt: 'Reconcile yesterday\'s gross card sales reported by our point-of-sale system against the net settlements deposited by each acquirer, and break out the variance by interchange, assessments, processor fees, and chargebacks.' },
  { icon: Clock,         label: 'Settlement SLA by acquirer',  prompt: 'Show me settlement timing by acquirer and card network for the past 30 days — authorization time, batch close time, processor settlement, and bank deposit time — and flag any settlements that arrived later than the contracted SLA.' },
  { icon: BarChart3,     label: 'Auth rates by acquirer',      prompt: 'Compare card payment authorization rates across all acquirers for the past 60 days by card network, transaction size band, and warehouse region, and identify any acquirer whose decline rate is more than 100 basis points worse than peers.' },
  { icon: AlertTriangle, label: 'ACH returns by reason code',  prompt: 'Pull all ACH returns from the past 60 days, categorize by return reason code, and show me the dollar impact and trend by originating business unit.' },
  { icon: Network,       label: 'Payment hub throughput',      prompt: 'Generate a daily payment hub throughput report for the last 30 days showing volume and value by payment type — wire, ACH, RTP, FedNow, check, virtual card — along with success, rejection, and repair rates.' },
  { icon: Percent,       label: 'Acquirer fee breakdown',      prompt: 'Show me total payment processing fees paid to each acquirer for the past quarter as a percentage of processed amount, split by interchange, network assessments, and processor margin.' },
  { icon: Globe,         label: 'Cross-border cost by country', prompt: 'Compare cross-border card transaction costs across our acquirers for the past 6 months by issuing country and currency, and highlight where DCC or local acquiring would have reduced cost.' },
  { icon: Search,        label: 'Duplicate payment exceptions', prompt: 'List all duplicate payment exceptions flagged by our payment hub in the last 90 days, and show resolution status, dollar amount, and recovery rate.' },
  { icon: ShieldAlert,   label: 'Chargeback ratios vs Visa threshold', prompt: 'Show me chargeback ratios by warehouse, acquirer, and reason code for the last 6 months and flag any combination that exceeds the 1% Visa monitoring threshold.' },
];

const ANALYST_ADVANCED: Suggestion[] = [
  { icon: LineChart,     label: 'Settlement cash forecast',    prompt: 'Build a daily expected settlement forecast for the next 14 days using historical authorization-to-settlement patterns by acquirer, card network, and weekday seasonality, and show me the forecasted versus minimum cash needed in each concentration account.' },
  { icon: Activity,      label: 'Settlement anomaly detection', prompt: 'Detect anomalies in payment processor settlement amounts over the last 12 months using statistical thresholds against the rolling 90-day mean by acquirer and channel, and correlate any flagged anomalies with known events like network outages, fee schedule changes, or holiday volume spikes.' },
  { icon: GitBranch,     label: 'Least-cost routing analysis', prompt: 'Run a least-cost routing analysis on the last 90 days of card transactions across our acquirers focused on interchange optimization, debit network routing, and acquirer pricing. Quantify the savings opportunity if every transaction had been routed optimally.' },
  { icon: ArrowLeftRight,label: 'ACH & wire rejection fixes',  prompt: 'Analyze ACH and wire rejections over the past 12 months and classify them into bank validation issues, file format errors, beneficiary data problems, and OFAC holds, and recommend the top 5 process fixes ranked by dollar impact.' },
  { icon: Zap,           label: 'ACH to RTP migration impact', prompt: 'Forecast the cash flow timing impact of migrating 30% of our ACH disbursement volume to RTP / FedNow. Include the loss of float, fee differences, fraud loss differential, and reconciliation efficiency, and show the net P&L and cash impact.' },
  { icon: ShieldAlert,   label: 'Chargeback fraud clusters',   prompt: 'Analyze chargeback patterns over the past 24 months using transaction-level data to identify clusters of friendly fraud, true fraud, and merchant error, and recommend representment strategies with expected win rates by reason code.' },
  { icon: Target,        label: 'STP scorecard',               prompt: 'Build a payment hub straight-through-processing scorecard that shows STP rate by payment type, country, and originating system over the past 12 months. Identify the top 10 exception drivers, and quantify the cost of each repair touch.' },
  { icon: AlertTriangle, label: 'Acquirer outage stress test', prompt: 'Stress-test our acquirer concentration risk by simulating a 72-hour outage at our largest card processor, model the operational impact on settlement timing, the cash buffer required, and the volume our backup acquirer would need to absorb, and identify gaps.' },
  { icon: Shield,        label: 'CNP fraud attribution model', prompt: 'Build a fraud loss attribution model on our card-not-present membership renewal channel over the past 18 months that separates losses by issuing BIN, geography, device fingerprint, and authentication method (3DS vs no 3DS), and recommend rule changes that reduce loss without increasing false-positive declines.' },
];

// ─── Manager ──────────────────────────────────────────────────────────────────
// Source: manager persona.txt — all questions included verbatim.

const MANAGER_SIMPLE: Suggestion[] = [
  { icon: BarChart3,     label: 'Total volume this month',     prompt: 'What is our total payment volume processed this month across all channels?' },
  { icon: DollarSign,    label: 'Cost YTD vs budget',          prompt: 'Show me total payment processing cost year-to-date versus budget.' },
  { icon: Layers,        label: 'Top 10 vendors by volume',    prompt: 'List our top 10 vendors by outbound payment volume this quarter.' },
  { icon: Clock,         label: 'Average days-to-pay',         prompt: 'What is our current average days-to-pay across all suppliers?' },
  { icon: CreditCard,    label: 'Card receivables in transit', prompt: 'Show me total card receivables in transit between authorization and bank deposit as of today.' },
  { icon: PieChart,      label: 'Outbound payments by method', prompt: 'Display the breakdown of outbound payments by method — ACH, wire, RTP, virtual card, check — for the current month.' },
  { icon: Percent,       label: 'Interchange last quarter',    prompt: 'What was total interchange paid to card networks last quarter?' },
  { icon: ShieldAlert,   label: 'Fraud losses YTD by channel', prompt: 'Show me total payment fraud losses booked this year-to-date by channel.' },
  { icon: FileText,      label: 'Contracts up for renewal',    prompt: 'List all payment processor contracts up for renewal in the next 12 months.' },
  { icon: Scale,         label: 'Applied vs unapplied cash',   prompt: 'Show me total cash applied versus unapplied receipts as of month-end.' },
];

const MANAGER_COMPLEX: Suggestion[] = [
  { icon: TrendingUp,    label: 'Cost per transaction trend',  prompt: 'Compare our blended cost-per-transaction across acquirers for the past 4 quarters, normalized for ticket size and card mix, and explain variances driven by interchange, network fees, processor margin, and incentive tier achievements.' },
  { icon: BarChart3,     label: '12-month acquirer scorecard', prompt: 'Pull our acquirer scorecard for the last 12 months covering authorization rate, settlement timeliness, fee competitiveness, dispute win rate, technical uptime, and innovation roadmap progress.' },
  { icon: LineChart,     label: 'Card mix interchange impact', prompt: 'Show me month-over-month trends in card mix — credit, debit, prepaid, commercial, international — and quantify the interchange impact of each shift over the past 18 months.' },
  { icon: AlertTriangle, label: 'Hub exceptions report',       prompt: 'Generate an exceptions and repairs report for our payment hub for the past quarter showing volume by exception type, average resolution time, and manpower effort to fix.' },
  { icon: Target,        label: 'Supplier on-time rates',      prompt: 'Compare on-time payment rates to suppliers across business units for the past 6 months, broken down by payment method, and highlight units operating outside our 95% on-time policy.' },
  { icon: RefreshCw,     label: 'Chargeback to settlement recon', prompt: 'Pull all payment-related chargebacks, refunds, and merchandise returns for the past 4 quarters and reconcile to the corresponding card settlement activity to validate net receivable position.' },
  { icon: Globe,         label: 'Cross-border cost by corridor', prompt: 'Show me the breakdown of cross-border payment volume by corridor, currency, and method for the past 12 months, with end-to-end cost including FX spread, lifting fees, and correspondent charges.' },
  { icon: Wallet,        label: 'Virtual card rebate vs target', prompt: 'Analyze our supplier rebate earnings from the virtual card and commercial card programs over the last 8 quarters by spend category, and compare actual rebate against contracted thresholds.' },
  { icon: ShieldAlert,   label: 'Operational losses trend',    prompt: 'Pull all payment-related operational losses, fraud losses, and write-offs over the past 24 months, categorize by root cause, and show the trend versus our internal loss tolerance.' },
];

const MANAGER_ADVANCED: Suggestion[] = [
  { icon: Calculator,    label: 'Payments cost allocation',    prompt: 'Build a payments cost allocation model across acquirers, networks, banks, the payment hub, fraud tools, and labor and recommend a target cost stack with quantified savings and a 12-month implementation plan.' },
  { icon: ArrowUpDown,   label: 'PIN debit network shift',     prompt: 'Run a what-if analysis on shifting 20% of our PIN debit volume from one debit network to another under current Reg II rates. Model the interchange savings, network fee differential, contractual minimums, and required certification effort, and recommend whether to proceed.' },
  { icon: RefreshCw,     label: 'Supplier payment timing',     prompt: 'Optimize our supplier payment timing across the top 100 vendors using a model that maximizes early-payment-discount capture, virtual-card rebate, and DPO and quantify the working-capital and P&L impact.' },
  { icon: Network,       label: 'Acquirer RFP scoring model',  prompt: 'Build a request-for-proposal evaluation model that compares our current acquirer panel against three alternative providers across pricing, technology, fraud capabilities, settlement timing, geographic coverage, and stability, and produce a weighted-score recommendation.' },
  { icon: TrendingUp,    label: 'Pay-by-bank 3-year forecast', prompt: 'Forecast the impact of implementing pay-by-bank or open-banking checkout on our member-facing channels over a 3-year horizon and compare against incremental investment.' },
  { icon: Zap,           label: 'Wire-to-RTP migration value', prompt: 'Quantify the value of moving 50% of our domestic vendor wire volume to RTP, including float impact, fee savings, working-capital effects, and fraud-loss differential, and identify the technology and process changes required.' },
];

// ─── Director ─────────────────────────────────────────────────────────────────
// Source: director persona.txt — all questions included verbatim.

const DIRECTOR_SIMPLE: Suggestion[] = [
  { icon: DollarSign,    label: 'Annual processing spend',     prompt: 'What is our total annual payments processing spend across all acquirers and processors?' },
  { icon: BarChart3,     label: 'Top 5 payment partners',      prompt: 'Show me our top 5 payment partners ranked by volume and by spend.' },
  { icon: Percent,       label: 'Blended interchange rate',    prompt: 'What is our blended effective interchange rate year-to-date?' },
  { icon: DollarSign,    label: 'Processing cost % of revenue', prompt: 'Display our payment processing cost as a percentage of card-payment revenue for the trailing 12 months.' },
  { icon: ShieldAlert,   label: 'Fraud loss rate this year',   prompt: 'Show me total fraud loss as a percentage of payment volume for the current year.' },
  { icon: Activity,      label: 'Global STP rate',             prompt: 'What is our straight-through-processing rate across the global payment hub?' },
  { icon: Globe,         label: 'Volume by region & method',   prompt: 'Show me total payment volume by region and by payment method for the trailing 12 months.' },
  { icon: CreditCard,    label: 'Acquirer concentration',      prompt: 'Display our acquirer concentration and share of total card volume by top 3 acquirers.' },
  { icon: Wallet,        label: 'Annual rebate income',        prompt: 'Show me total annual rebate income from virtual card and commercial card programs.' },
  { icon: Clock,         label: 'DPO vs industry benchmark',   prompt: 'What is our average days-payable-outstanding versus the industry benchmark?' },
];

const DIRECTOR_COMPLEX: Suggestion[] = [
  { icon: TrendingUp,    label: '8-quarter KPI trend',         prompt: 'Show me an 8-quarter trend of payment KPIs — cost as percent of volume, authorization rate, STP rate, fraud loss basis points, chargeback ratio, and supplier on-time rate — with policy thresholds overlaid.' },
  { icon: AlertTriangle, label: 'Material payment incidents',  prompt: 'Pull all material payment incidents over the past 12 months — outages, settlement delays, fraud events, sanctions hits, large reconciliation breaks — with root cause, financial impact, and remediation status.' },
  { icon: LineChart,     label: '5-year cost evolution',       prompt: 'Show me how our payments cost evolved over the past 5 years versus card mix shift, ticket-size changes, channel mix shift (in-warehouse vs e-commerce vs membership), and contract renewals.' },
  { icon: BarChart3,     label: 'Quarterly exec scorecard',    prompt: 'Generate a quarterly payments scorecard for the executive committee covering volume, cost, fraud, resilience, supplier experience, and member experience, with traffic-light status against targets.' },
];

const DIRECTOR_ADVANCED: Suggestion[] = [
  { icon: GitBranch,     label: '5-year payments roadmap',     prompt: 'Build a 5-year payments strategy roadmap aligned with our member growth, e-commerce expansion, and international plans that minimizes total cost of payments while maintaining resilience, fraud control, and member experience, with quantified investment, savings, and risk reduction.' },
  { icon: Building2,     label: 'Operating model design',      prompt: 'Develop an enterprise payments operating-model recommendation comparing centralized payment factory, regional hubs, and embedded business-unit models across cost, control, agility, and resilience, with a 3-year transition plan and business case.' },
  { icon: Landmark,      label: 'Closed-loop network value',   prompt: 'Quantify the strategic and financial value of building a Costco-branded closed-loop payment network for member transactions, including interchange savings, member loyalty data, fraud control, and capital and operational cost over a 10-year horizon.' },
  { icon: Shield,        label: 'Resilience stress test',      prompt: 'Run a comprehensive payments resilience stress test against catastrophic scenarios — multi-day primary acquirer outage, ransomware on the payment hub, Fed wire disruption, simultaneous fraud attack across channels — and quantify worst-case financial and customer impact with prioritized investments.' },
  { icon: Activity,      label: 'AI & automation roadmap',     prompt: 'Develop a comprehensive AI-and-automation roadmap for payment operations covering exception handling, fraud detection, sanctions screening, cash application, and dispute management, with quantified labor savings, error-rate reduction, and risk-mitigation impact.' },
  { icon: Network,       label: 'Unified fraud & disputes model', prompt: 'Build an integrated fraud-and-disputes operating model that consolidates the current siloed processes across e-commerce, in-warehouse, member services, and supplier payments, and quantify loss reduction, dispute win-rate improvement, and operational savings.' },
];

// ─── Executive ───────────────────────────────────────────────────────────────
// Source: executive persona.txt — all questions included verbatim.

const EXECUTIVE_SIMPLE: Suggestion[] = [
  { icon: Percent,       label: 'Cost of payments % revenue',  prompt: 'What is our total cost of payments as a percentage of revenue?' },
  { icon: DollarSign,    label: 'Processing spend last year',  prompt: 'How much did we spend on payment processing last year?' },
  { icon: ShieldAlert,   label: 'Fraud rate vs benchmark',     prompt: 'What is our payments fraud loss rate versus industry benchmark?' },
  { icon: PieChart,      label: 'Top 3 partner share',         prompt: 'Show me our top 3 payment partners and the percentage of volume each handles.' },
  { icon: Banknote,      label: 'Interchange spend last year', prompt: 'What was our total interchange spend last year?' },
  { icon: Wallet,        label: 'Commercial card rebates',     prompt: 'How much rebate income did we earn from commercial-card programs last year?' },
  { icon: Activity,      label: 'STP rate',                    prompt: 'What is our payment-hub straight-through-processing rate?' },
  { icon: Globe,         label: 'E-com vs in-warehouse share', prompt: 'What is our share of e-commerce versus in-warehouse payment volume?' },
  { icon: ShieldAlert,   label: 'Member fraud absorbed',       prompt: 'How much member-facing fraud did we absorb last year?' },
  { icon: BarChart3,     label: 'One-page payments dashboard', prompt: 'Show me a one-page payments dashboard covering cost, fraud, resilience, member experience, and supplier experience, benchmarked against best-in-class peers.' },
];

const EXECUTIVE_COMPLEX: Suggestion[] = [
  { icon: TrendingUp,    label: 'Peer benchmarking 5 years',   prompt: 'Compare our payments economics against our top retail peers over the past 5 years — cost as percent of revenue, fraud loss rate, and acquirer concentration — and explain the strategic drivers.' },
  { icon: LineChart,     label: 'Decade cost trajectory',      prompt: 'Show me the trajectory of our payments cost as a percentage of revenue over the last decade, decomposed into mix, rate, and operating-leverage effects.' },
  { icon: Scale,         label: 'Technology investment vs peers', prompt: 'Compare our investment in payments technology and operations against peer benchmarks for the last 3 years and assess whether we are over-, under-, or appropriately invested.' },
  { icon: FileText,      label: 'Audit committee summary',     prompt: 'Summarize all material payment incidents and regulatory matters over the past 24 months in a format suitable for the audit committee.' },
];

const EXECUTIVE_ADVANCED: Suggestion[] = [
  { icon: LineChart,     label: '10-year payments vision',     prompt: 'Build a 10-year strategic vision for Costco\'s payments capability that aligns with the company\'s growth strategy, membership model, and international expansion, and quantify the value-creation potential across cost, revenue, and member experience.' },
  { icon: GitBranch,     label: 'Build vs buy vs partner',     prompt: 'Develop a board-level point of view on whether to acquire, partner with, or build payment-technology capabilities — fraud platform, payment hub, member wallet, alternative-payment rails — supported by build-versus-buy economics and strategic-control considerations.' },
  { icon: Globe,         label: 'Payments data monetization',  prompt: 'Quantify the long-term strategic value of payments data — member behavior, supplier flows, real-time signals — and recommend a monetization-and-utilization framework that respects member trust and regulatory boundaries.' },
  { icon: Shield,        label: 'Enterprise risk & resilience', prompt: 'Build an integrated risk-and-resilience strategy for payments at the enterprise level covering fraud, cyber, third-party, settlement, and concentration risks, with a quantified roadmap of investments versus residual-risk reduction.' },
  { icon: Globe,         label: 'Geopolitical scenario analysis', prompt: 'Develop a comprehensive geopolitical and regulatory scenario analysis for our global payments operations covering US-China trade dynamics, EU payment regulations, sanctions regimes, and emerging-market local-rail mandates, with strategic responses for each.' },
  { icon: Building2,     label: 'Supplier finance platform',   prompt: 'Build a strategic case for embedding Costco as a payments-and-financial-services platform for our supplier ecosystem with instant payouts, supply-chain finance, FX, and treasury services, and quantify revenue, member-cost benefit, and capital intensity over a 10-year horizon.' },
];

// ─── Persona index ────────────────────────────────────────────────────────────

const POOLS: Record<ResponseTone, { simple: Suggestion[]; complex: Suggestion[]; advanced: Suggestion[] }> = {
  analyst:   { simple: ANALYST_SIMPLE,   complex: ANALYST_COMPLEX,   advanced: ANALYST_ADVANCED },
  manager:   { simple: MANAGER_SIMPLE,   complex: MANAGER_COMPLEX,   advanced: MANAGER_ADVANCED },
  director:  { simple: DIRECTOR_SIMPLE,  complex: DIRECTOR_COMPLEX,  advanced: DIRECTOR_ADVANCED },
  executive: { simple: EXECUTIVE_SIMPLE, complex: EXECUTIVE_COMPLEX, advanced: EXECUTIVE_ADVANCED },
};

// ─── Ghost prompts per persona ────────────────────────────────────────────────

export const GHOST_PROMPTS_BY_TONE: Record<ResponseTone, string[]> = {
  analyst: [
    'Show me total card payment volume across all acquirers yesterday.',
    'What were ACH returns last month by return reason code?',
    'Compare authorization rates across acquirers by card network.',
    'Show me settlement SLA breaches for the past 30 days.',
    'List duplicate payment exceptions flagged in the last 90 days.',
    'Reconcile POS card sales against acquirer net settlements.',
    'Detect anomalies in processor settlement amounts this year.',
    'Run a least-cost routing analysis on recent card transactions.',
    'Show me chargeback ratios by warehouse and acquirer.',
    'What were card-not-present fraud losses on membership renewals?',
  ],
  manager: [
    'What is our total payment volume this month across all channels?',
    'Show me payment processing cost year-to-date versus budget.',
    'List our top 10 vendors by outbound payment volume this quarter.',
    'What is our current average days-to-pay across all suppliers?',
    'Compare acquirer scorecard over the last 12 months.',
    'Show me on-time supplier payment rates by business unit.',
    'What is the interchange impact of our card mix shift over 18 months?',
    'Analyze virtual card rebate earnings versus contracted thresholds.',
    'Show me cross-border payment costs by corridor this year.',
    'What is the P&L impact of shifting wire volume to RTP?',
  ],
  director: [
    'What is our total annual payments processing spend?',
    'Show me our blended effective interchange rate year-to-date.',
    'What is our straight-through-processing rate globally?',
    'Show me an 8-quarter trend of key payment KPIs.',
    'Pull all material payment incidents over the past 12 months.',
    'How has our payments cost evolved over the past 5 years?',
    'Show me our acquirer concentration by share of card volume.',
    'Generate a quarterly payments scorecard for the executive committee.',
    'What is the business case for a Costco closed-loop payment network?',
    'Build a 5-year payments strategy roadmap.',
  ],
  executive: [
    'What is our total cost of payments as a percentage of revenue?',
    'How much did we spend on payment processing last year?',
    'What is our fraud loss rate versus industry benchmark?',
    'Show me a one-page payments dashboard.',
    'Compare our payments economics against top retail peers over 5 years.',
    'What is the decade trajectory of our payments cost as percent of revenue?',
    'How much rebate income from commercial card programs last year?',
    'Summarize payment incidents for the audit committee.',
    'Should we build, buy, or partner for payment technology capabilities?',
    'What is the strategic value of a Costco payments network?',
  ],
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

export function pickRandom<T>(arr: T[], n: number): T[] {
  const shuffled = [...arr].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, n);
}

/** Pick 4 suggestions: 1 simple + 2 complex + 1 advanced, fully randomised.
 *  Each call returns a different combination so the page feels fresh on
 *  every visit. Deduplication ensures no two chips show the same prompt. */
export function pickSuggestions(
  _recentTitles: string[] = [],
  tone: ResponseTone = 'executive',
): Suggestion[] {
  const pool = POOLS[tone] ?? POOLS.executive;
  const [simple]          = pickRandom(pool.simple, 1);
  const [complex1, complex2] = pickRandom(pool.complex, 2);
  const [advanced]        = pickRandom(pool.advanced, 1);
  return [simple, complex1, complex2, advanced].sort(() => Math.random() - 0.5);
}

// Legacy flat exports — kept so any existing direct imports still compile.
export const SIMPLE   = EXECUTIVE_SIMPLE;
export const COMPLEX  = EXECUTIVE_COMPLEX;
export const ADVANCED = EXECUTIVE_ADVANCED;
export const GHOST_PROMPTS = GHOST_PROMPTS_BY_TONE.executive;
