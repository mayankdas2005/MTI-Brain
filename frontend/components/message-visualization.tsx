'use client';

import { useMemo, useRef, useCallback, useState } from 'react';
import { Download, ClipboardCopy, RotateCcw } from 'lucide-react';
import { toast } from '@/lib/toast';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter, AreaChart, Area,
  XAxis, YAxis, CartesianGrid,
} from 'recharts';
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from '@/components/ui/chart';

// ─── Chart Spec Types ───

interface ChartSpecBase {
  type: 'bar' | 'line' | 'pie' | 'scatter' | 'area' | 'table';
  title?: string;
}

interface BarLineAreaSpec extends ChartSpecBase {
  type: 'bar' | 'line' | 'area';
  x_key: string;
  x_label?: string;
  y_keys: string[];
  y_label?: string;
  sort?: 'asc' | 'desc';
  data: Record<string, unknown>[];
}

interface PieSpec extends ChartSpecBase {
  type: 'pie';
  name_key: string;
  value_key: string;
  data: Record<string, unknown>[];
}

interface ScatterSpec extends ChartSpecBase {
  type: 'scatter';
  x_key: string;
  x_label?: string;
  y_key: string;
  y_label?: string;
  data: Record<string, unknown>[];
}

interface TableSpec extends ChartSpecBase {
  type: 'table';
}

type ChartSpec = BarLineAreaSpec | PieSpec | ScatterSpec | TableSpec;

interface MessageVisualizationProps {
  columns?: string[];
  rows?: unknown[][];
  chartSpec?: Record<string, unknown>;
}

// ─── Font family for SVG text — explicit so exports/copies keep the same font ───

const CHART_FONT = "'Geist', system-ui, sans-serif";

// ─── Color palette — vibrant, accessible, distinct on light & dark ───

const CHART_COLORS = [
  '#3B82F6', // blue
  '#F97316', // orange
  '#10B981', // emerald
  '#8B5CF6', // violet
  '#EC4899', // pink
  '#14B8A6', // teal
  '#F59E0B', // amber
  '#6366F1', // indigo
  '#EF4444', // red
  '#06B6D4', // cyan
  '#84CC16', // lime
  '#D946EF', // fuchsia
];

// ─── Normalize spec (backward compat with old x_col/y_col format) ───

function normalizeSpec(
  raw: Record<string, unknown>,
  columns?: string[],
  rows?: unknown[][],
): ChartSpec | null {
  const type = (raw.type as string)?.toLowerCase();
  if (!type) return null;

  // New format: spec already has embedded `data`
  if (raw.data && Array.isArray(raw.data)) {
    return raw as unknown as ChartSpec;
  }

  // Old format: { type, x_col, y_col, title, sort } — derive data from rows/columns
  if (!columns || !rows || rows.length === 0) return null;

  const data = rows.map((row) => {
    const obj: Record<string, unknown> = {};
    columns.forEach((col, i) => { obj[col] = row[i]; });
    return obj;
  });

  const title = raw.title as string | undefined;
  const xCol = (raw.x_col || raw.x_key || columns[0]) as string;
  const sort = raw.sort as 'asc' | 'desc' | undefined;

  if (type === 'pie') {
    const valueCol = (raw.y_col || raw.value_key || columns[1]) as string;
    return {
      type: 'pie',
      title,
      name_key: (raw.name_key || xCol) as string,
      value_key: valueCol,
      data,
    };
  }

  if (type === 'scatter') {
    const yCol = (raw.y_col || raw.y_key || columns[1]) as string;
    return {
      type: 'scatter',
      title,
      x_key: xCol,
      x_label: raw.x_label as string | undefined,
      y_key: yCol,
      y_label: raw.y_label as string | undefined,
      data,
    };
  }

  if (type === 'table') {
    return { type: 'table', title };
  }

  // bar, line, area
  const chartType = (['bar', 'line', 'area'].includes(type) ? type : 'bar') as 'bar' | 'line' | 'area';
  const yCol = (raw.y_col || columns[1]) as string;
  const yKeys = raw.y_keys
    ? (raw.y_keys as string[])
    : [yCol];

  return {
    type: chartType,
    title,
    x_key: xCol,
    x_label: raw.x_label as string | undefined,
    y_keys: yKeys,
    y_label: raw.y_label as string | undefined,
    sort,
    data,
  };
}

function buildChartConfig(fields: string[]): ChartConfig {
  const config: ChartConfig = {};
  fields.forEach((field, i) => {
    config[field] = {
      label: field.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
      color: CHART_COLORS[i % CHART_COLORS.length],
    };
  });
  return config;
}

// ─── Chart Renderers ───

function BarLineAreaChart({ spec }: { spec: BarLineAreaSpec }) {
  const config = buildChartConfig([spec.x_key, ...spec.y_keys]);
  const ChartComponent = spec.type === 'line' ? LineChart : spec.type === 'area' ? AreaChart : BarChart;

  const [dataSlice, setDataSlice] = useState(spec.data);
  const [sliderKey, setSliderKey] = useState(0);
  const isZoomed = dataSlice.length < spec.data.length;
  const showSlider = (spec.type === 'line' || spec.type === 'area') && spec.data.length >= 15;

  return (
    <div>
      <div className="relative">
        {isZoomed && (
          <button
            onClick={() => { setDataSlice(spec.data); setSliderKey((k) => k + 1); }}
            className="absolute top-1 right-1 z-10 flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-medium bg-background/90 backdrop-blur-sm border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <RotateCcw className="w-3 h-3" />
            Reset
          </button>
        )}
        <ChartContainer config={config} className="h-[340px] w-full">
          <ChartComponent
            data={dataSlice}
            margin={{ top: 10, right: 24, bottom: 14, left: spec.y_label ? 20 : 4 }}
          >
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" strokeOpacity={0.5} />
            <XAxis
              dataKey={spec.x_key}
              tick={{ fontSize: 11, fontFamily: CHART_FONT, dy: 6 }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              tick={{ fontSize: 11, fontFamily: CHART_FONT }}
              tickLine={false}
              axisLine={false}
              width={68}
              tickFormatter={(v: number) => {
                if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
                if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
                return String(v);
              }}
              domain={spec.type === 'bar' ? [0, 'auto'] : ['auto', 'auto']}
              label={spec.y_label ? {
                value: spec.y_label,
                angle: -90,
                position: 'insideLeft',
                dx: -16,
                style: { fontSize: 11, fontFamily: CHART_FONT, fill: 'var(--muted-foreground)', textAnchor: 'middle' },
              } : undefined}
            />
            <ChartTooltip content={<ChartTooltipContent />} cursor={{ fill: 'var(--border)', fillOpacity: 0.3 }} />
            {spec.y_keys.map((field, i) =>
              spec.type === 'line' ? (
                <Line
                  key={field}
                  type="monotone"
                  dataKey={field}
                  stroke={CHART_COLORS[i % CHART_COLORS.length]}
                  strokeWidth={2.5}
                  dot={{ r: 3.5, fill: CHART_COLORS[i % CHART_COLORS.length], strokeWidth: 0 }}
                  activeDot={{ r: 6, strokeWidth: 2, stroke: '#fff' }}
                  isAnimationActive
                  animationDuration={800}
                  animationEasing="ease-out"
                />
              ) : spec.type === 'area' ? (
                <Area
                  key={field}
                  type="monotone"
                  dataKey={field}
                  stroke={CHART_COLORS[i % CHART_COLORS.length]}
                  fill={CHART_COLORS[i % CHART_COLORS.length]}
                  fillOpacity={0.15}
                  strokeWidth={2.5}
                  isAnimationActive
                  animationDuration={800}
                  animationEasing="ease-out"
                />
              ) : (
                <Bar
                  key={field}
                  dataKey={field}
                  fill={CHART_COLORS[i % CHART_COLORS.length]}
                  radius={[6, 6, 0, 0]}
                  isAnimationActive
                  animationDuration={600}
                  animationEasing="ease-out"
                />
              )
            )}
          </ChartComponent>
        </ChartContainer>
      </div>
      {/* Below the chart: x-label, then legend, then slider — all HTML, zero overlap */}
      {spec.x_label && (
        <p data-x-label className="text-center text-[11px] text-muted-foreground mt-1">{spec.x_label}</p>
      )}
      <div className="flex items-center justify-center gap-4 mt-1">
        {spec.y_keys.map((field, i) => (
          <div key={field} data-legend-item className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <div className="w-2 h-2 rounded-[2px]" style={{ backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }} />
            {field.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
          </div>
        ))}
      </div>
      {showSlider && (
        <RangeSlider
          key={sliderKey}
          total={spec.data.length}
          startLabel={String(spec.data[0]?.[spec.x_key] ?? '')}
          endLabel={String(spec.data[spec.data.length - 1]?.[spec.x_key] ?? '')}
          onChange={(start, end) => {
            setDataSlice(spec.data.slice(start, end + 1));
          }}
        />
      )}
    </div>
  );
}

/** Dual-handle range slider */
function RangeSlider({ total, startLabel, endLabel, onChange }: {
  total: number;
  startLabel: string;
  endLabel: string;
  onChange: (start: number, end: number) => void;
}) {
  const max = total - 1;
  const [left, setLeft] = useState(0);
  const [right, setRight] = useState(max);

  const leftPct = (left / max) * 100;
  const rightPct = (right / max) * 100;

  return (
    <div className="mt-3 px-2">
      <div className="relative h-5">
        {/* Track */}
        <div className="absolute top-1/2 -translate-y-1/2 left-0 right-0 h-1 rounded-full bg-border/40" />
        {/* Selected range */}
        <div
          className="absolute top-1/2 -translate-y-1/2 h-1 rounded-full bg-muted-foreground/50"
          style={{ left: `${leftPct}%`, right: `${100 - rightPct}%` }}
        />
        {/* Left thumb */}
        <input
          type="range" min={0} max={max} value={left}
          onChange={(e) => {
            const v = Math.min(+e.target.value, right - 1);
            setLeft(v);
            onChange(v, right);
          }}
          className="absolute inset-0 w-full appearance-none bg-transparent pointer-events-none [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-sm [&::-webkit-slider-thumb]:bg-muted-foreground/80 [&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-border [&::-webkit-slider-thumb]:cursor-ew-resize [&::-webkit-slider-thumb]:pointer-events-auto [&::-moz-range-thumb]:w-2.5 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-sm [&::-moz-range-thumb]:bg-muted-foreground/80 [&::-moz-range-thumb]:border [&::-moz-range-thumb]:border-border [&::-moz-range-thumb]:cursor-ew-resize [&::-moz-range-thumb]:pointer-events-auto"
        />
        {/* Right thumb */}
        <input
          type="range" min={0} max={max} value={right}
          onChange={(e) => {
            const v = Math.max(+e.target.value, left + 1);
            setRight(v);
            onChange(left, v);
          }}
          className="absolute inset-0 w-full appearance-none bg-transparent pointer-events-none [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-sm [&::-webkit-slider-thumb]:bg-muted-foreground/80 [&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-border [&::-webkit-slider-thumb]:cursor-ew-resize [&::-webkit-slider-thumb]:pointer-events-auto [&::-moz-range-thumb]:w-2.5 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-sm [&::-moz-range-thumb]:bg-muted-foreground/80 [&::-moz-range-thumb]:border [&::-moz-range-thumb]:border-border [&::-moz-range-thumb]:cursor-ew-resize [&::-moz-range-thumb]:pointer-events-auto"
        />
      </div>
      <div className="flex justify-between text-[9px] text-muted-foreground/50 mt-0.5 px-0.5">
        <span>{startLabel}</span>
        <span>{endLabel}</span>
      </div>
    </div>
  );
}

function PieChartView({ spec }: { spec: PieSpec }) {
  // Build config from actual data values so legend shows slice names, not field names
  const config: ChartConfig = {};
  spec.data.forEach((item, i) => {
    const name = String(item[spec.name_key] || `Slice ${i + 1}`);
    config[name] = {
      label: name,
      color: CHART_COLORS[i % CHART_COLORS.length],
    };
  });

  return (
    <ChartContainer config={config} className="h-[340px] w-full">
      <PieChart>
        <ChartTooltip content={<ChartTooltipContent hideLabel />} />
        <Pie
          data={spec.data}
          dataKey={spec.value_key}
          nameKey={spec.name_key}
          cx="50%"
          cy="48%"
          outerRadius={105}
          innerRadius={45}
          paddingAngle={3}
          cornerRadius={4}
          label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
          labelLine={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }}
          isAnimationActive
          animationDuration={800}
          animationBegin={100}
          animationEasing="ease-out"
        >
          {spec.data.map((_, i) => (
            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} stroke="none" />
          ))}
        </Pie>
        <ChartLegend
          content={<ChartLegendContent nameKey={spec.name_key} />}
          verticalAlign="bottom"
          wrapperStyle={{ paddingTop: 0 }}
        />
      </PieChart>
    </ChartContainer>
  );
}

function ScatterChartView({ spec }: { spec: ScatterSpec }) {
  const config = buildChartConfig([spec.x_key, spec.y_key]);

  return (
    <ChartContainer config={config} className="h-[340px] w-full">
      <ScatterChart margin={{ top: 10, right: 24, bottom: spec.x_label ? 24 : 10, left: spec.y_label ? 24 : 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.5} />
        <XAxis
          dataKey={spec.x_key}
          name={spec.x_label || spec.x_key}
          tick={{ fontSize: 11, fontFamily: CHART_FONT }}
          tickLine={false}
          axisLine={false}
          label={spec.x_label ? { value: spec.x_label, position: 'insideBottom', offset: -6, fontSize: 11, fontFamily: CHART_FONT } : undefined}
        />
        <YAxis
          dataKey={spec.y_key}
          name={spec.y_label || spec.y_key}
          tick={{ fontSize: 11, fontFamily: CHART_FONT }}
          tickLine={false}
          axisLine={false}
          width={48}
          label={spec.y_label ? { value: spec.y_label, angle: -90, position: 'insideLeft', offset: 4, fontSize: 11, fontFamily: CHART_FONT } : undefined}
        />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Scatter data={spec.data} fill={CHART_COLORS[0]} isAnimationActive animationDuration={600} animationEasing="ease-out">
          {spec.data.map((_, i) => (
            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} stroke="#fff" strokeWidth={1} />
          ))}
        </Scatter>
      </ScatterChart>
    </ChartContainer>
  );
}

// ─── Main Component ───

/** Inline all computed styles onto every element in the SVG so it renders standalone. */
function inlineStyles(source: Element, target: Element) {
  const computed = getComputedStyle(source);
  const props = ['fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-opacity',
    'opacity', 'font-size', 'font-family', 'font-weight', 'text-anchor',
    'dominant-baseline', 'display', 'visibility', 'color', 'letter-spacing'];
  for (const prop of props) {
    const val = computed.getPropertyValue(prop);
    if (val && val !== 'none' && val !== 'normal' && val !== '0px') {
      (target as HTMLElement).style.setProperty(prop, val);
    }
  }
  for (let i = 0; i < source.children.length; i++) {
    if (target.children[i]) {
      inlineStyles(source.children[i], target.children[i]);
    }
  }
}

async function exportChartAsCanvas(container: HTMLDivElement, title?: string): Promise<HTMLCanvasElement> {
  // Select the Recharts SVG, not the lucide icon SVGs in the action buttons
  const svgEl = container.querySelector('.recharts-wrapper svg') || container.querySelector('svg:last-of-type');
  if (!svgEl) throw new Error('No SVG found');

  const clone = svgEl.cloneNode(true) as SVGSVGElement;
  const rect = svgEl.getBoundingClientRect();
  clone.setAttribute('width', String(rect.width));
  clone.setAttribute('height', String(rect.height));
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

  // Inline all computed styles (resolves CSS variables)
  inlineStyles(svgEl, clone);

  // Use Blob URL instead of data URL — more reliable with special characters
  const svgData = new XMLSerializer().serializeToString(clone);
  const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);

  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('SVG image load failed'));
    image.src = url;
  });

  URL.revokeObjectURL(url);

  // Extract legend items from HTML legend (outside SVG) or Recharts legend (inside SVG)
  const legendItems: { color: string; label: string }[] = [];
  // HTML legend: <div> with color dots + text
  container.querySelectorAll('[data-legend-item]').forEach((item) => {
    const dot = item.querySelector('div') as HTMLElement | null;
    const text = item.textContent?.trim() || '';
    const color = dot?.style.backgroundColor || '#666';
    if (text) legendItems.push({ color, label: text });
  });
  // Fallback: Recharts built-in legend (for pie charts)
  if (legendItems.length === 0) {
    const legendWrapper = container.querySelector('.recharts-legend-wrapper');
    if (legendWrapper) {
      legendWrapper.querySelectorAll(':scope > div > div').forEach((item) => {
        const colorDot = item.querySelector('div[class*="rounded"]') as HTMLElement | null;
        const text = item.textContent?.trim() || '';
        const color = colorDot?.style.backgroundColor || '#666';
        if (text) legendItems.push({ color, label: text });
      });
    }
  }

  // Extract x-axis label from HTML
  const xLabelEl = container.querySelector('[data-x-label]');
  const xLabel = xLabelEl?.textContent?.trim() || '';

  const padding = 32;
  const titleHeight = title ? 36 : 0;
  const xLabelHeight = xLabel ? 20 : 0;
  const legendHeight = legendItems.length > 0 ? 28 : 0;
  const scale = 2;
  const totalHeight = rect.height + padding * 2 + titleHeight + xLabelHeight + legendHeight;
  const canvas = document.createElement('canvas');
  canvas.width = (rect.width + padding * 2) * scale;
  canvas.height = totalHeight * scale;
  const ctx = canvas.getContext('2d')!;
  ctx.scale(scale, scale);

  const bgColor = getComputedStyle(container).backgroundColor || '#ffffff';
  ctx.fillStyle = bgColor;
  ctx.fillRect(0, 0, canvas.width / scale, canvas.height / scale);

  if (title) {
    const textColor = getComputedStyle(container).color || '#000';
    ctx.fillStyle = textColor;
    ctx.font = `500 14px ${CHART_FONT}`;
    ctx.fillText(title, padding, padding + 16);
  }

  ctx.drawImage(img, padding, padding + titleHeight, rect.width, rect.height);

  let cursorY = padding + titleHeight + rect.height;

  // Draw x-axis label
  if (xLabel) {
    cursorY += 14;
    ctx.font = `11px ${CHART_FONT}`;
    ctx.fillStyle = getComputedStyle(container).color || '#888';
    ctx.textAlign = 'center';
    ctx.fillText(xLabel, (rect.width + padding * 2) / 2, cursorY);
    ctx.textAlign = 'start';
    cursorY += 6;
  }

  // Draw legend below x-label
  if (legendItems.length > 0) {
    cursorY += 16;
    ctx.font = `12px ${CHART_FONT}`;
    const totalWidth = legendItems.reduce((w, item) =>
      w + 10 + 6 + ctx.measureText(item.label).width + 16, 0);
    let legendX = (rect.width + padding * 2 - totalWidth) / 2;

    for (const item of legendItems) {
      ctx.fillStyle = item.color;
      ctx.fillRect(legendX, cursorY - 4, 10, 10);
      legendX += 16;
      ctx.fillStyle = getComputedStyle(container).color || '#666';
      ctx.fillText(item.label, legendX, cursorY + 5);
      legendX += ctx.measureText(item.label).width + 16;
    }
  }

  return canvas;
}

export function MessageVisualization({
  columns,
  rows,
  chartSpec,
}: MessageVisualizationProps) {
  const chartRef = useRef<HTMLDivElement>(null);

  const spec = useMemo(() => {
    if (!chartSpec) return null;
    return normalizeSpec(chartSpec, columns, rows);
  }, [chartSpec, columns, rows]);

  const handleDownload = useCallback(async () => {
    if (!chartRef.current) return;
    try {
      const canvas = await exportChartAsCanvas(chartRef.current, spec?.title);
      const a = document.createElement('a');
      a.download = `chart-${Date.now()}.png`;
      a.href = canvas.toDataURL('image/png');
      a.click();
      toast.success('Chart downloaded');
    } catch {
      toast.error('Failed to export chart');
    }
  }, [spec?.title]);

  const handleCopy = useCallback(async () => {
    if (!chartRef.current) return;
    try {
      const canvas = await exportChartAsCanvas(chartRef.current, spec?.title);
      canvas.toBlob(async (blob) => {
        if (!blob) return;
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        toast.success('Chart copied to clipboard');
      }, 'image/png');
    } catch {
      toast.error('Failed to copy chart');
    }
  }, [spec?.title]);

  if (!spec || spec.type === 'table') return null;

  return (
    <div className="mt-3">
      <div ref={chartRef} className="group relative rounded-xl border border-border pt-4 px-4 pb-2 bg-sidebar">
        {/* Chart action buttons */}
        <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity z-10">
          <button
            onClick={handleCopy}
            className="p-1.5 rounded-md bg-background/80 backdrop-blur-sm border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            title="Copy chart to clipboard"
          >
            <ClipboardCopy className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={handleDownload}
            className="p-1.5 rounded-md bg-background/80 backdrop-blur-sm border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            title="Download chart as PNG"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
        </div>

        {spec.title && (
          <p className="text-sm font-medium text-foreground mb-3">{spec.title}</p>
        )}
        {(spec.type === 'bar' || spec.type === 'line' || spec.type === 'area') && (
          <BarLineAreaChart spec={spec as BarLineAreaSpec} />
        )}
        {spec.type === 'pie' && <PieChartView spec={spec as PieSpec} />}
        {spec.type === 'scatter' && <ScatterChartView spec={spec as ScatterSpec} />}
      </div>
    </div>
  );
}
