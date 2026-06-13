'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Download, ClipboardCopy, RotateCcw,
  TrendingUp, Layers,
  BarChart2, BarChart3, PieChart,
  Table2, ScatterChart,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { useTheme } from 'next-themes';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { CHART_PALETTE } from '@/components/charts/theme';

// ─── Chart type switcher metadata ─────────────────────────────────────────────

const CHART_ICONS: Record<string, React.ElementType> = {
  line:         TrendingUp,
  multi_line:   TrendingUp,
  stacked_area: Layers,
  bar:          BarChart2,
  stacked_bar:  BarChart3,
  grouped_bar:  BarChart3,
  pie:          PieChart,
  donut:        PieChart,
  scatter:      ScatterChart,
  waterfall:    BarChart2,
  dual_axis:    BarChart2,
  kpi_card:     BarChart2,
};

const CHART_LABELS: Record<string, string> = {
  line: 'Line', multi_line: 'Multi-line',
  stacked_area: 'Stacked area', bar: 'Bar',
  stacked_bar: 'Stacked bar', grouped_bar: 'Grouped bar',
  pie: 'Pie', donut: 'Donut', scatter: 'Scatter',
  waterfall: 'Waterfall', dual_axis: 'Dual axis', kpi_card: 'KPI',
};

// ─── Types ─────────────────────────────────────────────────────────────────────

type VegaView = {
  toCanvas: (scaleFactor?: number) => Promise<HTMLCanvasElement>;
  finalize: () => void;
  height: (h: number) => VegaView;
  run: () => void;
};

interface MessageVisualizationProps {
  columns?: string[];
  rows?: unknown[][];
  chartSpec?: Record<string, unknown>;
  primaryChartType?: string;
  alternativeChartSpecs?: { chart_type: string; spec: Record<string, unknown> }[];
  conversationId?: string;
}

// ─── Theme ─────────────────────────────────────────────────────────────────────

function getVegaThemeConfig(isDark: boolean): Record<string, unknown> {
  const labelColor = isDark ? '#9ca3af' : '#374151';
  const gridColor  = isDark ? '#374151' : '#e5e7eb';
  const font       = "'Alliance No.1', sans-serif";

  return {
    range:  { category: [...CHART_PALETTE] },
    mark:   { tooltip: true },
    view:   { stroke: 'transparent' },
    axis: {
      labelColor, titleColor: labelColor,
      grid: false, gridOpacity: 0,
      domainColor: 'transparent', tickColor: 'transparent',
      labelFont: font, titleFont: font,
      labelFontSize: 11, titleFontSize: 11,
    },
    legend: { labelColor, titleColor: labelColor, labelFont: font, titleFont: font, labelFontSize: 11 },
    title:  { color: labelColor, font, fontSize: 13, fontWeight: 500 },
    axisY:    { grid: false, gridOpacity: 0 },
    axisLeft: { grid: false, gridOpacity: 0 },
    bar:    { strokeWidth: 0 },
    arc:    { strokeWidth: 0 },
    area:   { strokeWidth: 0 },
    point:  { strokeWidth: 0 },
    line:   { strokeWidth: 2 },
  };
}

// ─── Old recharts format → Vega-Lite ──────────────────────────────────────────

function oldFormatToVegaLite(raw: Record<string, unknown>): Record<string, unknown> | null {
  const type = (raw.type as string)?.toLowerCase();
  if (!type || type === 'table') return null;

  const data = Array.isArray(raw.data) ? (raw.data as Record<string, unknown>[]) : [];
  if (!data.length) return null;

  const keys = Object.keys(data[0]);
  const base: Record<string, unknown> = {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    width: 'container',
    height: 350,
    background: 'transparent',
    title: raw.title || '',
    data: { values: data },
  };

  if (type === 'pie') {
    const nameKey  = (raw.name_key  || keys[0]) as string;
    const valueKey = (raw.value_key || keys[1] || keys[0]) as string;
    return { ...base, mark: { type: 'arc', innerRadius: 50 },
      encoding: {
        theta: { field: valueKey, type: 'quantitative' },
        color: { field: nameKey,  type: 'nominal' },
      },
    };
  }

  if (type === 'scatter') {
    const xKey = (raw.x_key || keys[0]) as string;
    const yKey = (raw.y_key || keys[1] || keys[0]) as string;
    return { ...base, mark: 'point',
      encoding: {
        x: { field: xKey, type: 'quantitative', axis: { title: raw.x_label || xKey } },
        y: { field: yKey, type: 'quantitative', axis: { title: raw.y_label || yKey } },
      },
    };
  }

  const xKey   = (raw.x_key || keys[0]) as string;
  const yKeys  = Array.isArray(raw.y_keys) ? (raw.y_keys as string[]) : [(raw.y_key || keys[1] || keys[0]) as string];
  const mark   = type === 'line' ? 'line' : type === 'area' ? 'area' : 'bar';
  const xType  = (type === 'line' || type === 'area') ? 'temporal' : 'nominal';

  if (yKeys.length === 1) {
    return { ...base, mark,
      encoding: {
        x: { field: xKey,    type: xType,         axis: { title: raw.x_label || xKey } },
        y: { field: yKeys[0], type: 'quantitative', axis: { title: raw.y_label || yKeys[0] } },
      },
    };
  }

  return { ...base,
    transform: [{ fold: yKeys, as: ['series', 'value'] }],
    mark,
    encoding: {
      x:     { field: xKey,     type: xType,         axis: { title: raw.x_label || xKey } },
      y:     { field: 'value',  type: 'quantitative', axis: { title: raw.y_label || 'Value' } },
      color: { field: 'series', type: 'nominal' },
    },
  };
}

// ─── Spec normalization ────────────────────────────────────────────────────────

type NormalizedSpec = Record<string, unknown> | 'kpi_card' | 'table' | null;

function normalizeSpec(
  raw: Record<string, unknown>,
  columns?: string[],
  rows?: unknown[][],
): NormalizedSpec {
  if (raw.type === 'kpi_card') return 'kpi_card';
  if (raw.type === 'table')    return 'table';
  if (raw.values && Array.isArray(raw.values)) return 'kpi_card';
  if (raw.$schema) {
    // Layered specs (dual_axis) have no top-level mark — they use raw.layer instead.
    // Plain specs without either mark or layer are broken and would throw a 'marktype' error.
    if (!raw.mark && !Array.isArray(raw.layer)) return null;
    return raw;
  }

  if (raw.data && Array.isArray(raw.data)) return oldFormatToVegaLite(raw);

  if (!columns || !rows || !rows.length) return null;
  const data = rows.map((row) => {
    const obj: Record<string, unknown> = {};
    columns.forEach((col, i) => { obj[col] = (row as unknown[])[i]; });
    return obj;
  });
  return oldFormatToVegaLite({ ...raw, data });
}

// ─── Spec enrichment ──────────────────────────────────────────────────────────

function toTitleCase(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function humanizeEncoding(spec: Record<string, unknown>): Record<string, unknown> {
  // Recurse into layered specs (dual-axis, multi-line) — each layer has its own encoding
  let result = spec;
  if (Array.isArray(spec.layer)) {
    const newLayers = (spec.layer as Record<string, unknown>[]).map(humanizeEncoding);
    const layerChanged = newLayers.some((l, i) => l !== (spec.layer as Record<string, unknown>[])[i]);
    if (layerChanged) result = { ...result, layer: newLayers };
  }

  const enc = result.encoding as Record<string, unknown> | undefined;
  if (!enc) return result;

  const newEnc: Record<string, unknown> = {};
  let changed = false;

  for (const [channel, ch] of Object.entries(enc)) {
    if (!ch || typeof ch !== 'object' || typeof (ch as Record<string, unknown>).field !== 'string') {
      newEnc[channel] = ch; continue;
    }
    const def   = ch as Record<string, unknown>;
    const label = toTitleCase(def.field as string);
    const base  = def.title == null ? { ...def, title: label } : def;
    if (def.title == null) changed = true;

    if (['color', 'size', 'shape'].includes(channel)) {
      const raw = def.legend;
      if (raw === false || raw === null) { newEnc[channel] = base; continue; }
      newEnc[channel] = { ...base, legend: { title: label, ...((raw as Record<string, unknown>) ?? {}) } };
      changed = true;
    } else if (['x', 'y'].includes(channel)) {
      const raw = def.axis;
      if (raw === false || raw === null) { newEnc[channel] = base; continue; }
      newEnc[channel] = { ...base, axis: { title: label, ...((raw as Record<string, unknown>) ?? {}), grid: false } };
      changed = true;
    } else {
      newEnc[channel] = base;
    }
  }

  // Humanize explicit tooltip array entries.
  // Also re-humanize titles that look like raw identifiers (snake_case or SCREAMING_CASE)
  // — the backend may have set title to the raw column name.
  const tooltipEnc = enc.tooltip;
  if (Array.isArray(tooltipEnc)) {
    newEnc.tooltip = tooltipEnc.map((item: unknown) => {
      if (!item || typeof item !== 'object') return item;
      const t = item as Record<string, unknown>;
      if (typeof t.field !== 'string') return t;
      const existingTitle = t.title != null ? String(t.title) : null;
      const looksRaw = existingTitle === null
        || existingTitle.includes('_')
        || /^[A-Z0-9]+$/.test(existingTitle);
      if (!looksRaw) return t;
      return { ...t, title: toTitleCase(t.field) };
    });
    changed = true;
  } else if (tooltipEnc !== undefined) {
    newEnc.tooltip = tooltipEnc;
  }

  return changed ? { ...result, encoding: newEnc } : result;
}

// Convert mark-level tooltip:true into an explicit encoding.tooltip array so Vega never falls
// back to the raw "field (timeUnit)" key format. Recurses into layers.
function normalizeTooltipEncoding(spec: Record<string, unknown>): Record<string, unknown> {
  if (Array.isArray(spec.layer)) {
    const newLayers = (spec.layer as Record<string, unknown>[]).map(normalizeTooltipEncoding);
    const layerChanged = newLayers.some((l, i) => l !== (spec.layer as Record<string, unknown>[])[i]);
    return layerChanged ? { ...spec, layer: newLayers } : spec;
  }

  const enc = spec.encoding as Record<string, unknown> | undefined;
  if (!enc || Array.isArray(enc.tooltip)) return spec; // explicit array already handled

  const items: Record<string, unknown>[] = [];
  for (const ch of ['x', 'color', 'y', 'y2', 'size', 'shape', 'detail'] as const) {
    const def = enc[ch];
    if (!def || typeof def !== 'object') continue;
    const d = def as Record<string, unknown>;
    if (typeof d.field !== 'string') continue;
    const item: Record<string, unknown> = {
      field: d.field,
      title: (d.title as string) || toTitleCase(d.field),
    };
    if (d.type)       item.type       = d.type;
    if (d.timeUnit)   item.timeUnit   = d.timeUnit;
    if (d.format)     item.format     = d.format;
    if (d.formatType) item.formatType = d.formatType;
    items.push(item);
  }

  if (!items.length) return spec;
  return { ...spec, encoding: { ...enc, tooltip: items } };
}

// ─── Universal K/M/B/T number formatting ──────────────────────────────────────
// Applies smartNum (registered below in VegaVisualization) to every quantitative
// axis and tooltip so large numbers always render as 50K / 1.2M / 6.5B / 1T.
// Runs after normalizeTooltipEncoding so the explicit tooltip array already exists.

function applySmartNumberFormatting(spec: Record<string, unknown>): Record<string, unknown> {
  if (Array.isArray(spec.layer)) {
    const newLayers = (spec.layer as Record<string, unknown>[]).map(applySmartNumberFormatting);
    const layerChanged = newLayers.some((l, i) => l !== (spec.layer as Record<string, unknown>[])[i]);
    if (layerChanged) spec = { ...spec, layer: newLayers };
  }

  const enc = spec.encoding as Record<string, unknown> | undefined;
  if (!enc) return spec;

  let changed = false;
  const newEnc = { ...enc };

  for (const channel of ['x', 'y'] as const) {
    const def = enc[channel] as Record<string, unknown> | undefined;
    if (!def || typeof def !== 'object') continue;
    if (def.type !== 'quantitative') continue;
    if (def.axis === false || def.axis === null) continue;

    const axis = (def.axis as Record<string, unknown>) ?? {};
    // Strip old format/labelExpr so they don't conflict; preserve everything else
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { format: _f, labelExpr: _l, ...restAxis } = axis;
    newEnc[channel] = { ...def, axis: { ...restAxis, labelExpr: "smartNum(datum.value, '')" } };
    changed = true;
  }

  const tooltipEnc = enc.tooltip;
  if (Array.isArray(tooltipEnc)) {
    const newTooltip = (tooltipEnc as Record<string, unknown>[]).map(item => {
      if (!item || typeof item !== 'object') return item;
      const t = item as Record<string, unknown>;
      if (t.type !== 'quantitative') return t;
      if (t.formatType) return t; // already handled
      return { ...t, formatType: 'smartNum', format: '' };
    });
    if (newTooltip.some((t, i) => t !== (tooltipEnc as unknown[])[i])) {
      newEnc.tooltip = newTooltip;
      changed = true;
    }
  }

  return changed ? { ...spec, encoding: newEnc } : spec;
}

const ARC_MARKS = new Set(['arc']);

function addZoomParams(spec: Record<string, unknown>): Record<string, unknown> {
  const mark     = spec.mark;
  const markType = typeof mark === 'string' ? mark : (mark as Record<string, unknown>)?.type;
  if (ARC_MARKS.has(markType as string)) return spec;
  const existing = (spec.params as unknown[]) || [];
  if (existing.some((p) => (p as Record<string, unknown>).name === 'grid')) return spec;
  // bind:'scales' only works on continuous (quantitative/temporal) axes.
  // For nominal/ordinal x-axis (bar, grouped_bar, etc.) it triggers a Vega warning and does nothing.
  const enc  = spec.encoding as Record<string, unknown> | undefined;
  const xDef = enc?.x as Record<string, unknown> | undefined;
  const xType = xDef?.type as string | undefined;
  const isContinuous = xType === 'quantitative' || xType === 'temporal';
  if (!isContinuous) return spec;
  return { ...spec, params: [...existing, { name: 'grid', select: 'interval', bind: 'scales' }] };
}

interface SliderInfo {
  applicable: boolean;
  xField: string | null;
  uniqueXValues: unknown[];
}

function getSliderInfo(spec: Record<string, unknown>): SliderInfo {
  const mark     = spec.mark;
  const markType = typeof mark === 'string' ? mark : (mark as Record<string, unknown>)?.type;
  if (ARC_MARKS.has(markType as string)) return { applicable: false, xField: null, uniqueXValues: [] };

  const enc    = spec.encoding as Record<string, unknown> | undefined;
  const xField = ((enc?.x as Record<string, unknown>)?.field as string) || null;
  if (!xField) return { applicable: false, xField: null, uniqueXValues: [] };

  const values = (spec.data as Record<string, unknown>)?.values;
  if (!Array.isArray(values) || values.length < 9) return { applicable: false, xField: null, uniqueXValues: [] };

  const seen = new Set<unknown>();
  const uniqueXValues: unknown[] = [];
  for (const row of values as Record<string, unknown>[]) {
    const v = row[xField];
    if (!seen.has(v)) { seen.add(v); uniqueXValues.push(v); }
  }

  return { applicable: uniqueXValues.length > 8, xField, uniqueXValues };
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────

function fmtKpi(value: unknown, valueFormat?: string): string {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (!isNaN(n)) {
    if (Math.abs(n) >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
    if (Math.abs(n) >= 1_000_000)     return `${(n / 1_000_000).toFixed(2)}M`;
    if (Math.abs(n) >= 1_000)         return `${(n / 1_000).toFixed(1)}K`;
    if (valueFormat?.endsWith('%'))    return `${(n * 100).toFixed(1)}%`;
    if (n % 1 === 0)                   return n.toLocaleString();
    if (Math.abs(n) < 0.1)            return n.toFixed(4);
    return n.toFixed(2);
  }
  return String(value);
}

function KpiCard({ values, valueFormat }: { values: Record<string, unknown>[]; valueFormat?: string }) {
  const entries = values
    .flatMap((row) => Object.entries(row))
    .filter(([, v]) => v != null && v !== '' && !Number.isNaN(v as number));
  if (!entries.length) return null;
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 py-2">
      {entries.map(([key, value], i) => (
        <div key={i} className="rounded-lg border border-border p-4 bg-muted/30">
          <p className="text-xs text-muted-foreground uppercase tracking-wide truncate">
            {key.replace(/_/g, ' ')}
          </p>
          <p className="text-2xl font-semibold text-foreground mt-1 tabular-nums">
            {fmtKpi(value, valueFormat)}
          </p>
        </div>
      ))}
    </div>
  );
}

// ─── Range Slider ─────────────────────────────────────────────────────────────

function RangeSlider({ total, startLabel, endLabel, onCommit }: {
  total: number;
  startLabel: string;
  endLabel: string;
  onCommit: (start: number, end: number) => void;
}) {
  const max      = total - 1;
  const [left,  setLeft]  = useState(0);
  const [right, setRight] = useState(max);
  const leftRef  = useRef(0);
  const rightRef = useRef(max);

  const leftPct  = (left  / max) * 100;
  const rightPct = (right / max) * 100;

  const thumbCls = 'absolute inset-0 w-full appearance-none bg-transparent pointer-events-none '
    + '[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-4 '
    + '[&::-webkit-slider-thumb]:rounded-sm [&::-webkit-slider-thumb]:bg-muted-foreground/80 '
    + '[&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-border '
    + '[&::-webkit-slider-thumb]:cursor-ew-resize [&::-webkit-slider-thumb]:pointer-events-auto '
    + '[&::-moz-range-thumb]:w-2.5 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-sm '
    + '[&::-moz-range-thumb]:bg-muted-foreground/80 [&::-moz-range-thumb]:border '
    + '[&::-moz-range-thumb]:border-border [&::-moz-range-thumb]:cursor-ew-resize '
    + '[&::-moz-range-thumb]:pointer-events-auto';

  return (
    <div className="mt-3 px-2">
      <div className="relative h-5">
        <div className="absolute top-1/2 -translate-y-1/2 left-0 right-0 h-1 rounded-full bg-border/40" />
        <div
          className="absolute top-1/2 -translate-y-1/2 h-1 rounded-full bg-muted-foreground/50"
          style={{ left: `${leftPct}%`, right: `${100 - rightPct}%` }}
        />
        <input type="range" min={0} max={max} value={left}
          onChange={(e) => {
            const v = Math.min(+e.target.value, right - 1);
            setLeft(v); leftRef.current = v;
          }}
          onMouseUp={() => onCommit(leftRef.current, rightRef.current)}
          onTouchEnd={() => onCommit(leftRef.current, rightRef.current)}
          className={thumbCls}
        />
        <input type="range" min={0} max={max} value={right}
          onChange={(e) => {
            const v = Math.max(+e.target.value, left + 1);
            setRight(v); rightRef.current = v;
          }}
          onMouseUp={() => onCommit(leftRef.current, rightRef.current)}
          onTouchEnd={() => onCommit(leftRef.current, rightRef.current)}
          className={thumbCls}
        />
      </div>
      <div className="flex justify-between text-[9px] text-muted-foreground/50 mt-0.5 px-0.5">
        <span>{startLabel}</span>
        <span>{endLabel}</span>
      </div>
    </div>
  );
}

// ─── Vega Visualization wrapper ────────────────────────────────────────────────

type ChartActions = { copy: () => Promise<void>; download: () => Promise<void>; reset: () => void };

function VegaVisualization({ rawSpec, conversationId, onActionsReady }: {
  rawSpec: Record<string, unknown>;
  conversationId?: string;
  onActionsReady?: (actions: ChartActions) => void;
}) {
  const { resolvedTheme } = useTheme();
  const isDark       = resolvedTheme === 'dark';
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef      = useRef<VegaView | null>(null);
  const heightRef    = useRef<number | null>(null);
  const [dataSlice, setDataSlice] = useState<Record<string, unknown>[] | null>(null);
  const [containerWidth, setContainerWidth] = useState<number | null>(null);
  const [resetCounter, setResetCounter] = useState(0);

  const handleReset = useCallback(() => {
    heightRef.current = null;
    setResetCounter(c => c + 1);
  }, []);

  // Track container width so the embed effect re-runs (and Vega re-measures width:'container')
  // whenever the viewport changes. Only update state when the width actually changes to avoid
  // spurious re-embeds on vertical-only resizes.
  useEffect(() => {
    const measure = () => {
      const outer = containerRef.current?.parentElement;
      if (!outer) return;
      const w = outer.getBoundingClientRect().width;
      if (w > 0) setContainerWidth(prev => (prev !== w ? w : prev));
    };
    measure();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onResize = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(measure, 150);
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      if (timer) clearTimeout(timer);
    };
  }, []);

  useEffect(() => { setDataSlice(null); heightRef.current = null; }, [rawSpec]);

  const allData = useMemo(() => {
    return (rawSpec.data as Record<string, unknown>)?.values as Record<string, unknown>[] | undefined;
  }, [rawSpec]);

  const sliderInfo = useMemo(() => getSliderInfo(rawSpec), [rawSpec]);

  const embedSpec = useMemo(() => {
    const theme = getVegaThemeConfig(isDark);
    const spec  = applySmartNumberFormatting(normalizeTooltipEncoding(addZoomParams(humanizeEncoding({ ...rawSpec, config: theme }))));
    return dataSlice ? { ...spec, data: { values: dataSlice } } : spec;
  }, [rawSpec, isDark, dataSlice]);

  const handleDownload = useCallback(async () => {
    const view = viewRef.current;
    if (!view) return;
    try {
      const canvas = await view.toCanvas(2);
      const a = document.createElement('a');
      a.download = `chart-${(conversationId ?? '').slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.png`;
      a.href = canvas.toDataURL('image/png');
      a.click();
      toast.success('Chart downloaded');
    } catch {
      toast.error('Failed to export chart');
    }
  }, [conversationId]);

  const handleCopy = useCallback(async () => {
    const view = viewRef.current;
    if (!view) return;
    try {
      const canvas = await view.toCanvas(2);
      await new Promise<void>((resolve, reject) =>
        canvas.toBlob(async (blob) => {
          if (!blob) { reject(new Error('no blob')); return; }
          await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
          resolve();
        }, 'image/png')
      );
      toast.success('Chart copied');
    } catch {
      toast.error('Failed to copy chart');
    }
  }, []);

  // Expose copy/download/reset to parent toolbar as soon as handlers are stable
  useEffect(() => {
    onActionsReady?.({ copy: handleCopy, download: handleDownload, reset: handleReset });
  }, [onActionsReady, handleCopy, handleDownload, handleReset]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let mounted  = true;
    let localView: VegaView | null = null;

    Promise.all([import('vega-embed'), import('vega')]).then(([{ default: vegaEmbed }, vega]) => {
      if (!mounted || !containerRef.current) return;
      // Register smartNum once — idempotent, same function every call.
      // Used by tooltip formatType:"smartNum" to render €/£/¥/$ large numbers
      // as "€50.7T", "£5B", "$1.2M" etc. (D3 format strings only support "$").
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (vega as any).expressionFunction('smartNum', (value: unknown, fmt: unknown) => {
        if (typeof value !== 'number' || !isFinite(value)) return String(value ?? '');
        const sym = typeof fmt === 'string' ? fmt : '';
        const abs = Math.abs(value);
        const fmt2 = (n: number) => n.toFixed(2).replace(/\.?0+$/, '');
        if (abs >= 1e12) return sym + fmt2(value / 1e12) + 'T';
        if (abs >= 1e9)  return sym + fmt2(value / 1e9)  + 'B';
        if (abs >= 1e6)  return sym + fmt2(value / 1e6)  + 'M';
        if (abs >= 1e3)  return sym + fmt2(value / 1e3)  + 'K';
        return sym + value.toLocaleString('en-US', { maximumFractionDigits: abs < 1 ? 4 : 2 });
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      vegaEmbed(containerRef.current, embedSpec as any, { renderer: 'canvas', actions: false })
        .then((result) => {
          if (!mounted) { result.view.finalize(); return; }
          localView = result.view as unknown as VegaView;
          viewRef.current = localView;
          if (heightRef.current != null) localView.height(heightRef.current).run();
        })
        .catch((err) => { if (!mounted) return; console.error('vega-embed:', err); });
    });

    return () => {
      mounted = false;
      if (localView) { localView.finalize(); viewRef.current = null; }
    };
  }, [embedSpec, containerWidth, resetCounter]);

  const startLabel = sliderInfo.uniqueXValues.length
    ? String(sliderInfo.uniqueXValues[0] ?? '') : '';
  const endLabel = sliderInfo.uniqueXValues.length
    ? String(sliderInfo.uniqueXValues[sliderInfo.uniqueXValues.length - 1] ?? '') : '';

  const handleSliderCommit = useCallback((start: number, end: number) => {
    if (!allData || !sliderInfo.xField) return;
    const selected = new Set(sliderInfo.uniqueXValues.slice(start, end + 1));
    const field    = sliderInfo.xField;
    setDataSlice(allData.filter(row => selected.has(row[field])));
  }, [allData, sliderInfo]);

  return (
    <div data-chart-conv-id={conversationId}>
      <div ref={containerRef} className="w-full" />
      {sliderInfo.applicable && allData && (
        <RangeSlider
          key={sliderInfo.uniqueXValues.length}
          total={sliderInfo.uniqueXValues.length}
          startLabel={startLabel}
          endLabel={endLabel}
          onCommit={handleSliderCommit}
        />
      )}
    </div>
  );
}

// ─── Inline table renderer ────────────────────────────────────────────────────

function InlineTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th key={i} className="text-left px-3 py-2 text-muted-foreground font-medium border-b border-border whitespace-nowrap">
                {toTitleCase(col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b border-border/40 hover:bg-muted/20">
              {(row as unknown[]).map((cell, ci) => (
                <td key={ci} className="px-3 py-2 text-foreground whitespace-nowrap">
                  {cell == null ? '—' : String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Build alternative spec from primary spec + type string ──────────────────

function buildSpecForType(
  primary: Record<string, unknown>,
  altType: string,
): Record<string, unknown> | null {
  const data = primary.data as Record<string, unknown> | undefined;
  if (!data?.values) return null;

  const enc      = primary.encoding as Record<string, unknown> | undefined;
  if (!enc) return null;

  const xEnc     = enc.x      as Record<string, unknown> | undefined;
  const yEnc     = enc.y      as Record<string, unknown> | undefined;
  const colEnc   = enc.color  as Record<string, unknown> | undefined;
  const thetaEnc = enc.theta  as Record<string, unknown> | undefined;

  // Determine if primary is arc-based (pie/donut) or cartesian
  const isArc = !!thetaEnc;

  // Extract canonical field names from whatever encoding the primary uses
  const xField   = xEnc?.field as string | undefined;
  const yField   = yEnc?.field as string | undefined;
  const catField = (colEnc?.field ?? xEnc?.field) as string | undefined;
  const valField = isArc ? (thetaEnc?.field as string | undefined) : yField;
  const arcCat   = isArc ? (colEnc?.field as string | undefined) : catField;

  // Canonical encodings for cartesian output — strip internal-only fields
  const outX: Record<string, unknown> = xEnc
    ? { ...xEnc }
    : { field: arcCat ?? xField, type: 'nominal' };
  const outY: Record<string, unknown> = {
    field: valField ?? yField,
    type: 'quantitative',
    ...(yEnc?.axis ? { axis: yEnc.axis } : {}),
    ...(yEnc?.title ? { title: yEnc.title } : {}),
  };
  // Never forward stack/xOffset into the base — each case applies it explicitly
  delete outX.xOffset;
  delete (outY as Record<string, unknown>).stack;

  const base: Record<string, unknown> = {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    width: 'container', height: 350, background: 'transparent',
    title: primary.title ?? '',
    data,
  };

  switch (altType) {
    // ── Simple bar — keep color encoding so each category stays distinguishable
    case 'bar':
      return {
        ...base, mark: 'bar',
        encoding: {
          x: { ...outX, sort: '-y' },
          y: outY,
          ...(colEnc ? { color: colEnc } : {}),
        },
      };

    // ── Grouped bar ───────────────────────────────────────────────────────────
    case 'grouped_bar': {
      const gcol = colEnc ?? (catField ? { field: catField, type: 'nominal' } : undefined);
      return {
        ...base, mark: 'bar',
        encoding: { x: outX, y: outY, ...(gcol ? { color: gcol, xOffset: gcol } : {}) },
      };
    }

    // ── Stacked bar ───────────────────────────────────────────────────────────
    case 'stacked_bar': {
      const scol = colEnc ?? (catField ? { field: catField, type: 'nominal' } : undefined);
      return {
        ...base, mark: 'bar',
        encoding: { x: outX, y: { ...outY, stack: 'zero' }, ...(scol ? { color: scol } : {}) },
      };
    }

    // ── Waterfall (running-sum bar) ───────────────────────────────────────────
    case 'waterfall': {
      const wX = xField ?? (arcCat as string | undefined);
      const wY = valField;
      if (!wX || !wY) return null;
      return {
        ...base,
        transform: [
          { sort: [{ field: wX }], window: [{ op: 'sum', field: wY, as: '_wsum' }] },
          { calculate: `datum._wsum - datum['${wY}']`, as: '_wlead' },
        ],
        mark: { type: 'bar' },
        encoding: {
          x: { field: wX, type: 'nominal', axis: { title: (outX.axis as Record<string, unknown>)?.title ?? wX } },
          y: { field: '_wlead', type: 'quantitative', title: '' },
          y2: { field: '_wsum' },
        },
      };
    }

    // ── Line ─────────────────────────────────────────────────────────────────
    case 'line':
      return {
        ...base, mark: altType,
        encoding: { x: outX, y: outY, ...(colEnc ? { color: colEnc } : {}) },
      };
    case 'area':
      return {
        ...base, mark: 'line',
        encoding: { x: outX, y: outY, ...(colEnc ? { color: colEnc } : {}) },
      };

    // ── Multi-line ────────────────────────────────────────────────────────────
    case 'multi_line': {
      const mlCol = colEnc ?? (catField ? { field: catField, type: 'nominal' } : undefined);
      return {
        ...base, mark: 'line',
        encoding: { x: outX, y: outY, ...(mlCol ? { color: mlCol } : {}) },
      };
    }

    // ── Stacked area ──────────────────────────────────────────────────────────
    case 'stacked_area': {
      const saCol = colEnc ?? (catField ? { field: catField, type: 'nominal' } : undefined);
      return {
        ...base, mark: 'area',
        encoding: { x: outX, y: { ...outY, stack: 'zero' }, ...(saCol ? { color: saCol } : {}) },
      };
    }

    // ── Pie / Donut ───────────────────────────────────────────────────────────
    case 'pie':
    case 'donut': {
      const pCat = arcCat ?? catField ?? xField;
      const pVal = valField ?? yField;
      if (!pCat || !pVal) return null;
      return {
        ...base,
        mark: { type: 'arc', innerRadius: altType === 'donut' ? 50 : 0 },
        encoding: {
          theta: { field: pVal, type: 'quantitative' },
          color: { field: pCat, type: 'nominal' },
        },
      };
    }

    // ── Scatter ───────────────────────────────────────────────────────────────
    case 'scatter':
      return {
        ...base, mark: 'point',
        encoding: { x: { ...outX, type: 'quantitative' }, y: outY, ...(colEnc ? { color: colEnc } : {}) },
      };

    default:
      return null;
  }
}

// ─── Chart Type Switcher ──────────────────────────────────────────────────────

function ChartTypeSwitcher({ types, activeType, onSelect }: {
  types: string[];
  activeType: string;
  onSelect: (type: string) => void;
}) {
  if (types.length < 2) return null;
  return (
    <div className="flex gap-1 flex-wrap">
      {types.map((t) => {
        const Icon = CHART_ICONS[t] ?? BarChart2;
        const label = CHART_LABELS[t] ?? t;
        const isActive = t === activeType;
        return (
          <button
            key={t}
            onClick={() => onSelect(t)}
            className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs transition-colors border ${
              isActive
                ? 'bg-foreground/10 border-border text-foreground'
                : 'bg-transparent border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/30'
            }`}
          >
            <Icon className="w-3 h-3" />
            <span>{label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Chart Toolbar (switcher + copy/download) ─────────────────────────────────

function ChartToolbar({ types, activeType, onSelect, actions }: {
  types: string[];
  activeType: string;
  onSelect: (t: string) => void;
  actions: ChartActions | null;
}) {
  const hasActions = !!actions;
  const hasSwitcher = types.length > 1;
  if (!hasSwitcher && !hasActions) return null;
  return (
    <div className="flex items-center justify-between gap-2 mb-2 pb-2 border-b border-border/40">
      <div className="flex-1">
        {hasSwitcher && (
          <ChartTypeSwitcher types={types} activeType={activeType} onSelect={onSelect} />
        )}
      </div>
      {hasActions && (
        <div className="flex gap-1 shrink-0">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => actions.reset()}
                className="p-1.5 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors"
              >
                <RotateCcw className="w-3.5 h-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Reset chart</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => actions.copy()}
                className="p-1.5 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors"
              >
                <ClipboardCopy className="w-3.5 h-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Copy chart</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => actions.download()}
                className="p-1.5 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors"
              >
                <Download className="w-3.5 h-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Download chart</TooltipContent>
          </Tooltip>
        </div>
      )}
    </div>
  );
}

// ─── Detect primary chart type from Vega-Lite spec encoding ──────────────────
// Used when chart_type is not stored in metadata (historical messages).

function detectPrimaryType(
  spec: Record<string, unknown> | undefined,
  explicitType: string | undefined,
): string {
  if (explicitType) return explicitType;
  if (!spec) return '';
  const mark = typeof spec.mark === 'string'
    ? spec.mark
    : (spec.mark as Record<string, unknown> | undefined)?.type as string | undefined;
  const enc = spec.encoding as Record<string, unknown> | undefined;
  if (!mark) return '';
  if (mark === 'arc') return 'donut';
  if (mark === 'bar') {
    if (enc?.xOffset) return 'grouped_bar';
    if ((enc?.y as Record<string, unknown> | undefined)?.stack === 'zero') return 'stacked_bar';
    return 'bar';
  }
  if (mark === 'point') return 'scatter';
  if (mark === 'area') return enc?.color ? 'stacked_area' : 'line';
  if (mark === 'line') return enc?.color ? 'multi_line' : 'line';
  return mark;
}

// ─── Main Export ───────────────────────────────────────────────────────────────

export function MessageVisualization({
  columns, rows, chartSpec,
  primaryChartType, alternativeChartSpecs,
  conversationId,
}: MessageVisualizationProps) {
  // Single source of truth for primary type — explicit prop OR derived from spec encoding
  const primaryType = useMemo(
    () => detectPrimaryType(chartSpec, primaryChartType),
    [chartSpec, primaryChartType],
  );

  type AltSpec = { chart_type: string; spec: Record<string, unknown> };
  const allTypes = useMemo<AltSpec[]>(() => {
    const _primarySpec = (() => {
      if (!chartSpec) return {};
      const m = typeof chartSpec.mark === 'string' ? chartSpec.mark
              : (chartSpec.mark as Record<string, unknown>)?.type;
      if (m === 'area') return { ...chartSpec, mark: { type: 'line', point: true } };
      return chartSpec;
    })();
    const primary: AltSpec[] = primaryType ? [{ chart_type: primaryType, spec: _primarySpec }] : [];
    const seen = new Set(primaryType ? [primaryType] : []);
    const alts = alternativeChartSpecs as AltSpec[] | string[] | undefined;
    for (const a of alts ?? []) {
      // Support both new full-spec format and legacy type-name strings
      const type = typeof a === 'string' ? a : a.chart_type;
      if (type === 'area') continue;
      const spec = typeof a === 'string' ? (chartSpec ? buildSpecForType(chartSpec, a) ?? {} : {}) : a.spec;
      if (type && !seen.has(type)) {
        seen.add(type);
        primary.push({ chart_type: type, spec });
      }
    }
    return primary;
  }, [primaryType, chartSpec, alternativeChartSpecs]);

  // Initialise to the detected primary so the correct button is highlighted on load
  const [activeType, setActiveType] = useState<string>(() => primaryType);
  const [chartActions, setChartActions] = useState<ChartActions | null>(null);

  const activeSpec = useMemo<Record<string, unknown> | null>(() => {
    const found = allTypes.find(t => t.chart_type === activeType);
    if (found?.spec && Object.keys(found.spec).length > 0) return found.spec;
    return chartSpec ?? null;
  }, [activeType, allTypes, chartSpec]);

  if (!activeSpec) return null;

  const isTableType = activeType === 'table';
  const currentNormalized = isTableType ? 'table' : normalizeSpec(activeSpec, columns, rows);
  if (!currentNormalized) return null;

  if (currentNormalized === 'kpi_card') {
    const rawValues = (activeSpec.values as Record<string, unknown>[] | undefined) ?? [];
    // Filter rows where every value is null/undefined/NaN/empty — nothing to display
    const values = rawValues.filter(row =>
      Object.values(row).some(v => v != null && v !== '' && !Number.isNaN(v as number))
    );
    if (!values.length) return null;
    return (
      <div className="mt-3 rounded-xl border border-border px-4 pt-3 pb-2 bg-sidebar">
        {allTypes.length > 1 && (
          <div className="mb-3 pb-2 border-b border-border/40">
            <ChartTypeSwitcher types={allTypes.map(t => t.chart_type)} activeType={activeType} onSelect={setActiveType} />
          </div>
        )}
        {activeSpec.title ? <p className="text-sm font-medium text-foreground mb-3">{String(activeSpec.title)}</p> : null}
        <KpiCard values={values} valueFormat={activeSpec.value_format as string | undefined} />
      </div>
    );
  }

  if (currentNormalized === 'table') {
    if (!columns?.length || !rows?.length) return null;
    return (
      <div className="mt-3 rounded-xl border border-border overflow-hidden bg-sidebar">
        {allTypes.length > 1 && (
          <div className="px-4 pt-3 pb-2 border-b border-border/40">
            <ChartTypeSwitcher types={allTypes.map(t => t.chart_type)} activeType={activeType} onSelect={setActiveType} />
          </div>
        )}
        <InlineTable columns={columns} rows={rows} />
      </div>
    );
  }

  return (
    <div className="mt-3">
      <div className="rounded-xl border border-border pt-3 px-4 pb-2 bg-sidebar">
        <ChartToolbar
          types={allTypes.map(t => t.chart_type)}
          activeType={activeType}
          onSelect={setActiveType}
          actions={chartActions}
        />
        <VegaVisualization
          rawSpec={currentNormalized}
          conversationId={conversationId}
          onActionsReady={setChartActions}
        />
      </div>
    </div>
  );
}

// Legacy export — used by PDF export utilities to grab the rendered chart canvas.
export async function exportChartAsCanvas(container: HTMLElement, _title?: string): Promise<HTMLCanvasElement> {
  const src = container.querySelector('canvas');
  if (!src) throw new Error('No chart canvas found');
  const dst = document.createElement('canvas');
  dst.width  = src.width;
  dst.height = src.height;
  dst.getContext('2d')!.drawImage(src, 0, 0);
  return dst;
}
