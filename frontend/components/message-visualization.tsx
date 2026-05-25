'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, ClipboardCopy } from 'lucide-react';
import { toast } from '@/lib/toast';
import { useTheme } from 'next-themes';
import { CHART_PALETTE } from '@/components/charts/theme';

// ─── Types ─────────────────────────────────────────────────────────────────────

type VegaView = {
  toCanvas: (scaleFactor?: number) => Promise<HTMLCanvasElement>;
  finalize: () => void;
};

interface MessageVisualizationProps {
  columns?: string[];
  rows?: unknown[][];
  chartSpec?: Record<string, unknown>;
  conversationId?: string;
}

// ─── Theme ─────────────────────────────────────────────────────────────────────

function getVegaThemeConfig(isDark: boolean): Record<string, unknown> {
  const labelColor = isDark ? '#9ca3af' : '#374151';
  const gridColor  = isDark ? '#374151' : '#e5e7eb';
  const font       = 'Geist, system-ui, sans-serif';

  return {
    range:  { category: [...CHART_PALETTE] },
    axis: {
      labelColor, titleColor: labelColor,
      gridColor, gridOpacity: 0.6,
      domainColor: 'transparent', tickColor: 'transparent',
      labelFont: font, titleFont: font,
      labelFontSize: 11, titleFontSize: 11,
    },
    legend: { labelColor, titleColor: labelColor, labelFont: font, titleFont: font, labelFontSize: 11 },
    title:  { color: labelColor, font, fontSize: 13, fontWeight: 500 },
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
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
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
  if (raw.$schema)             return raw;
  if (raw.values && Array.isArray(raw.values)) return 'kpi_card';

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

const ARC_MARKS = new Set(['arc']);

function addZoomParams(spec: Record<string, unknown>): Record<string, unknown> {
  const mark     = spec.mark;
  const markType = typeof mark === 'string' ? mark : (mark as Record<string, unknown>)?.type;
  if (ARC_MARKS.has(markType as string)) return spec;
  const existing = (spec.params as unknown[]) || [];
  if (existing.some((p) => (p as Record<string, unknown>).name === 'grid')) return spec;
  return { ...spec, params: [...existing, { name: 'grid', select: 'interval', bind: 'scales' }] };
}

function isTimeSeries(spec: Record<string, unknown>): boolean {
  const values = (spec.data as Record<string, unknown>)?.values;
  if (!Array.isArray(values) || values.length < 15) return false;
  const mark     = spec.mark;
  const markType = typeof mark === 'string' ? mark : (mark as Record<string, unknown>)?.type;
  return markType === 'line' || markType === 'area';
}

function getXField(spec: Record<string, unknown>): string | null {
  const enc = spec.encoding as Record<string, unknown> | undefined;
  return ((enc?.x as Record<string, unknown>)?.field as string) || null;
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────

function fmtKpi(value: unknown): string {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (!isNaN(n)) {
    if (Math.abs(n) >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
    if (Math.abs(n) >= 1_000_000)     return `${(n / 1_000_000).toFixed(2)}M`;
    if (Math.abs(n) >= 1_000)         return `${(n / 1_000).toFixed(1)}K`;
    return n % 1 === 0 ? n.toLocaleString() : n.toFixed(2);
  }
  return String(value);
}

function KpiCard({ values }: { values: Record<string, unknown>[] }) {
  const entries = values.flatMap((row) => Object.entries(row));
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 py-2">
      {entries.map(([key, value], i) => (
        <div key={i} className="rounded-lg border border-border p-4 bg-muted/30">
          <p className="text-xs text-muted-foreground uppercase tracking-wide truncate">
            {key.replace(/_/g, ' ')}
          </p>
          <p className="text-2xl font-semibold text-foreground mt-1 tabular-nums">
            {fmtKpi(value)}
          </p>
        </div>
      ))}
    </div>
  );
}

// ─── Vega Embed ───────────────────────────────────────────────────────────────

function VegaChart({ spec, viewRef }: {
  spec: Record<string, unknown>;
  viewRef: React.MutableRefObject<VegaView | null>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let mounted  = true;
    let localView: VegaView | null = null;

    import('vega-embed').then(({ default: vegaEmbed }) => {
      if (!mounted || !containerRef.current) return;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      vegaEmbed(containerRef.current, spec as any, { renderer: 'canvas', actions: false })
        .then((result) => {
          if (!mounted) { result.view.finalize(); return; }
          localView = result.view as unknown as VegaView;
          viewRef.current = localView;
        })
        .catch((err) => { if (!mounted) return; console.error('vega-embed:', err); });
    });

    return () => {
      mounted = false;
      if (localView) { localView.finalize(); viewRef.current = null; }
    };
  }, [spec]); // spec is a stable useMemo reference — re-embeds only when spec changes

  return <div ref={containerRef} className="w-full" />;
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

function VegaVisualization({ rawSpec, conversationId }: {
  rawSpec: Record<string, unknown>;
  conversationId?: string;
}) {
  const { resolvedTheme } = useTheme();
  const isDark   = resolvedTheme === 'dark';
  const viewRef  = useRef<VegaView | null>(null);
  const [dataSlice, setDataSlice] = useState<Record<string, unknown>[] | null>(null);

  useEffect(() => { setDataSlice(null); }, [rawSpec]);

  const allData = useMemo(() => {
    return (rawSpec.data as Record<string, unknown>)?.values as Record<string, unknown>[] | undefined;
  }, [rawSpec]);

  const baseSpec = useMemo(() => {
    const theme = getVegaThemeConfig(isDark);
    return addZoomParams({ ...rawSpec, config: theme });
  }, [rawSpec, isDark]);

  const displaySpec = useMemo(() => {
    if (!dataSlice) return baseSpec;
    return { ...baseSpec, data: { values: dataSlice } };
  }, [baseSpec, dataSlice]);

  const showSlider = isTimeSeries(rawSpec);
  const xField    = showSlider ? getXField(rawSpec) : null;
  const startLabel = xField && allData ? String(allData[0]?.[xField] ?? '')                : '';
  const endLabel   = xField && allData ? String(allData[allData.length - 1]?.[xField] ?? '') : '';

  const handleSliderCommit = useCallback((start: number, end: number) => {
    if (!allData) return;
    setDataSlice(allData.slice(start, end + 1));
  }, [allData]);

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

  return (
    <div className="group relative" data-chart-conv-id={conversationId}>
      <div className="absolute top-2 right-2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity z-10">
        <button
          onClick={handleCopy}
          className="p-1.5 rounded-md bg-background/80 backdrop-blur-sm border border-border text-muted-foreground hover:text-foreground transition-colors"
        >
          <ClipboardCopy className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleDownload}
          className="p-1.5 rounded-md bg-background/80 backdrop-blur-sm border border-border text-muted-foreground hover:text-foreground transition-colors"
        >
          <Download className="w-3.5 h-3.5" />
        </button>
      </div>

      <VegaChart spec={displaySpec} viewRef={viewRef} />

      {showSlider && allData && (
        <RangeSlider
          key={allData.length}
          total={allData.length}
          startLabel={startLabel}
          endLabel={endLabel}
          onCommit={handleSliderCommit}
        />
      )}
    </div>
  );
}

// ─── Main Export ───────────────────────────────────────────────────────────────

export function MessageVisualization({ columns, rows, chartSpec, conversationId }: MessageVisualizationProps) {
  if (!chartSpec) return null;

  const specResult = normalizeSpec(chartSpec, columns, rows);
  if (!specResult) return null;

  if (specResult === 'table') return null;

  if (specResult === 'kpi_card') {
    const values = (chartSpec.values as Record<string, unknown>[] | undefined) ?? [];
    if (!values.length) return null;
    return (
      <div className="mt-3 rounded-xl border border-border px-4 pt-3 pb-2 bg-sidebar">
        {chartSpec.title ? <p className="text-sm font-medium text-foreground mb-3">{String(chartSpec.title)}</p> : null}
        <KpiCard values={values} />
      </div>
    );
  }

  return (
    <div className="mt-3">
      <div className="rounded-xl border border-border pt-4 px-4 pb-2 bg-sidebar">
        <VegaVisualization rawSpec={specResult} conversationId={conversationId} />
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
