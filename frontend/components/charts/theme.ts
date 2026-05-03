/**
 * Quest chart theme.
 *
 * 6-color brand-aligned categorical palette + shared axis/grid/font props.
 * Mirrors --chart-1..6 in app/globals.css; the JS HEX literals exist
 * because Recharts renders SVG that gets exported to canvas/PNG, and
 * canvas APIs cannot resolve CSS variables.
 *
 * Order is chosen so 1–3 series stay on-brand; 4–6 add warmth/contrast.
 * If a chart has more than 6 series, the palette wraps — but charts with
 * 7+ series are usually a data-viz problem, not a palette problem.
 */

export const CHART_PALETTE = [
  '#1B76B8', // brand blue
  '#F9A619', // amber
  '#4f739f', // muted slate
  '#14b8a6', // teal
  '#a78bfa', // violet
  '#f43f5e', // rose
] as const;

export type ChartColor = (typeof CHART_PALETTE)[number];

export function chartColor(index: number): string {
  return CHART_PALETTE[index % CHART_PALETTE.length];
}

/**
 * Font for SVG <text> elements inside Recharts.
 * Kept as a literal string (not `var(--font-sans)`) so the canvas-based
 * PNG export in message-visualization.tsx can render it without CSS
 * variable resolution.
 */
export const CHART_FONT = "'Geist', system-ui, sans-serif";

export const CHART_TICK_FONT_SIZE = 11;

export const CHART_TICK_PROPS = {
  fontSize: CHART_TICK_FONT_SIZE,
  fontFamily: CHART_FONT,
} as const;

export const CHART_GRID_PROPS = {
  strokeDasharray: '3 3',
  vertical: false,
  stroke: 'var(--border)',
  strokeOpacity: 0.5,
} as const;

export const CHART_GRID_PROPS_BOTH = {
  ...CHART_GRID_PROPS,
  vertical: true,
} as const;

export const CHART_TOOLTIP_CURSOR = {
  fill: 'var(--border)',
  fillOpacity: 0.3,
} as const;

export const CHART_ANIMATION = {
  isAnimationActive: true,
  animationDuration: 600,
  animationEasing: 'ease-out',
} as const;

export const CHART_ANIMATION_LONG = {
  ...CHART_ANIMATION,
  animationDuration: 800,
} as const;
